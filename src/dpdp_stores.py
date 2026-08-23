"""
Thin, typed wrappers around Neo4j AuraDB and the pure-Python TF-IDF index,
plus the in-memory human-review queue, answer cache, and rate limiter.

This is the Render/AuraDB adaptation of the original VectorGraphStore: no
sentence-transformers, no Qdrant - both replaced to fit a 512MB free-tier
RAM budget (see tfidf_search.py for why, and dpdp_config.py for the
threshold trade-offs that come with it). ReviewQueue, AnswerCache, and
RateLimiter are unchanged from the local version - they never depended on
the embedding/vector stack in the first place.
"""

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from neo4j import GraphDatabase

from src.dpdp_config import NEO4J_PASSWORD, NEO4J_URI, NEO4J_USER
from src.tfidf_search import TFIDFIndex

logger = logging.getLogger("dpdp.stores")


class KnowledgeStore:
    """Owns the Neo4j AuraDB driver and the TF-IDF search index."""

    def __init__(self) -> None:
        self.neo4j = (
            GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
            if NEO4J_URI
            else None
        )
        self.tfidf = TFIDFIndex()
        self._committed: dict[str, dict] = {}
        self._lock = threading.Lock()

    def ping_neo4j(self) -> bool:
        if self.neo4j is None:
            return False
        try:
            self.neo4j.verify_connectivity()
            return True
        except Exception:
            return False

    def commit_section(self, section: dict) -> None:
        """Write one approved section into Neo4j (graph) and refit the
        TF-IDF index over all committed sections' text (cheap at <=44
        sections - a full refit, not an incremental update)."""
        with self._lock:
            self._committed[section["id"]] = section
            self.tfidf.fit({sid: s["raw_text"] for sid, s in self._committed.items()})

        if self.neo4j is not None:
            with self.neo4j.session() as session:
                session.run(
                    "MERGE (s:Section {id: $id}) SET s.title = $title, s.source_url = $url",
                    id=section["id"], title=section["title"], url=section["source_url"],
                )
                entities = section["entities"]
                for label, items in (
                    ("Obligation", entities["obligations"]), ("Right", entities["rights"]),
                    ("Penalty", entities["penalties"]), ("Definition", entities["definitions"]),
                ):
                    for name in items:
                        node_id = f"{label.lower()}::{name}"[:200]
                        session.run(
                            f"MERGE (e:`{label}` {{id: $id}}) SET e.name = $name",
                            id=node_id, name=name,
                        )
                        session.run(
                            "MATCH (s:Section {id: $sid}), (e {id: $eid}) MERGE (s)-[:MENTIONS]->(e)",
                            sid=section["id"], eid=node_id,
                        )
        logger.info("Committed %s to Neo4j AuraDB + TF-IDF index", section["id"])

    def search(self, query: str, top_k: int, score_threshold: float = 0.0) -> list[tuple[str, float]]:
        with self._lock:
            results = self.tfidf.search(query, top_k)
        return [(doc_id, score) for doc_id, score in results if score >= score_threshold]

    def get_section_meta(self, section_id: str) -> Optional[dict]:
        with self._lock:
            return self._committed.get(section_id)

    def graph_context(self, section_id: str) -> list[dict]:
        if self.neo4j is None:
            return []
        cypher = """
        MATCH (s:Section {id: $sid})-[:MENTIONS]->(related)
        RETURN labels(related)[0] AS type, related.name AS name
        """
        with self.neo4j.session() as session:
            return session.run(cypher, sid=section_id).data()

    def indexed_count(self) -> int:
        with self._lock:
            return len(self._committed)


class ReviewQueue:
    """Thread-safe in-memory human-in-the-loop queue. One entry per section."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}
        self._sections: dict[str, dict] = {}  # full section payload, for approve-time commit

    def register(self, section: dict, needs_review: bool) -> None:
        with self._lock:
            self._sections[section["id"]] = section
            self._entries[section["id"]] = {
                "section_id": section["id"],
                "title": section["title"],
                "chapter": section["chapter"],
                "confidence": section["confidence"],
                "sensitive": section["sensitive"],
                "status": "pending_review" if needs_review else "auto_approved",
                "reviewed_by": None,
                "reviewed_at": None,
            }

    def pending(self) -> list[dict]:
        with self._lock:
            return [e for e in self._entries.values() if e["status"] == "pending_review"]

    def all_entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries.values())

    def get_section(self, section_id: str) -> Optional[dict]:
        with self._lock:
            return self._sections.get(section_id)

    def decide(self, section_id: str, decision: str, reviewer: str) -> dict:
        with self._lock:
            if section_id not in self._entries:
                raise KeyError(section_id)
            entry = self._entries[section_id]
            entry["status"] = "approved" if decision == "approve" else "rejected"
            entry["reviewed_by"] = reviewer
            entry["reviewed_at"] = datetime.now(timezone.utc).isoformat()
            return dict(entry)

    def stats(self) -> dict:
        with self._lock:
            values = list(self._entries.values())
        counts = {"total": len(values)}
        for status in ("auto_approved", "pending_review", "approved", "rejected"):
            counts[status] = sum(1 for e in values if e["status"] == status)
        return counts


class AnswerCache:
    """Simple exact-match, size-capped cache so repeat questions skip
    retrieval + generation entirely."""

    def __init__(self, max_entries: int = 200) -> None:
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._max_entries = max_entries

    @staticmethod
    def _key(query: str) -> str:
        return query.strip().lower()

    def get(self, query: str) -> Optional[dict]:
        with self._lock:
            return self._data.get(self._key(query))

    def set(self, query: str, value: dict) -> None:
        with self._lock:
            if len(self._data) >= self._max_entries:
                self._data.pop(next(iter(self._data)))
            self._data[self._key(query)] = value


class RateLimiter:
    """Fixed-window per-client throttle, entirely in-memory - no Redis
    needed for a single free-tier instance."""

    def __init__(self, limit_per_minute: int) -> None:
        self._lock = threading.Lock()
        self._limit = limit_per_minute
        self._windows: dict[str, tuple[int, float]] = {}

    def allow(self, client_key: str) -> bool:
        import time
        now = time.time()
        with self._lock:
            count, window_start = self._windows.get(client_key, (0, now))
            if now - window_start >= 60:
                count, window_start = 0, now
            count += 1
            self._windows[client_key] = (count, window_start)
            return count <= self._limit
