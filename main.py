"""
DPDP Act GraphRAG service - Render/Groq/AuraDB build.

Startup: fetch the official PDF, parse into sections, tag with rule-based
categories (fast, no LLM), gate through human review, write approved
sections into the TF-IDF index + Neo4j AuraDB. Groq's hosted API is only
called inside /ask, once per question, and streamed back token-by-token.

The frontend is a SEPARATE deployment (Vercel) - this process serves only
the JSON/NDJSON API, no static files.
"""

import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import dpdp_config as cfg
from dpdp_ingest import build_tagged_sections, fetch_act_pdf
from dpdp_stores import AnswerCache, KnowledgeStore, RateLimiter, ReviewQueue
from groq_client import GroqError, stream_chat

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("dpdp.main")

store = KnowledgeStore()
review_queue = ReviewQueue()
answer_cache = AnswerCache()
rate_limiter = RateLimiter(cfg.RATE_LIMIT_REQUESTS_PER_MINUTE)

_ingestion_done = False
_ingestion_error: Optional[str] = None


def run_ingestion() -> None:
    """Idempotent: safe to call from startup and defensively from /ask.
    Render's free tier restarts the whole process on wake-from-sleep, so
    this reruns on every cold start - Neo4j writes are idempotent (MERGE),
    but the in-memory review queue does NOT remember previous human
    approvals across a restart (see DEPLOYMENT.md limitations section):
    sensitive/low-confidence sections will need re-approving after a
    cold start, same as they did the first time."""
    global _ingestion_done, _ingestion_error
    if _ingestion_done:
        return

    try:
        pdf_path = fetch_act_pdf(
            max_retries=cfg.PDF_FETCH_RETRIES, backoff_seconds=cfg.PDF_FETCH_BACKOFF_SECONDS,
        )
        sections = build_tagged_sections(pdf_path, cfg.MAX_SECTIONS)

        auto_approved = 0
        for section in sections:
            needs_review = section["sensitive"] or section["confidence"] < cfg.CONFIDENCE_THRESHOLD
            review_queue.register(section, needs_review)
            if not needs_review:
                store.commit_section(section)
                auto_approved += 1

        logger.info(
            "Ingestion complete: %d/%d auto-approved and indexed, %d pending human review.",
            auto_approved, len(sections), len(sections) - auto_approved,
        )
        _ingestion_error = None
        _ingestion_done = True
    except Exception as exc:
        logger.exception("Ingestion failed")
        _ingestion_error = str(exc)
        # _ingestion_done stays False so the next call (health poll or /ask) retries


@asynccontextmanager
async def lifespan(_: FastAPI):
    run_ingestion()
    yield


app = FastAPI(title="DPDP Act GraphRAG API (Render build)", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[cfg.ALLOWED_ORIGIN] if cfg.ALLOWED_ORIGIN != "*" else ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def root():
    return {
        "service": "DPDP Act GraphRAG API",
        "docs": "/docs",
        "health": "/health",
        "note": "This is the backend API only. The chat UI is a separate frontend deployment.",
    }


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)

    def clean_query(self) -> str:
        cleaned = self.query.strip()
        if not cleaned:
            raise HTTPException(status_code=400, detail="Query cannot be empty or whitespace-only.")
        return cleaned


class ReviewDecision(BaseModel):
    section_id: str
    decision: str
    reviewer: str = "demo_reviewer"


# ---------------------------------------------------------------------------
# Health / observability
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {
        "status": "ok",
        "ingested": _ingestion_done,
        "ingestion_error": _ingestion_error,
        "neo4j_connected": store.ping_neo4j(),
        "sections_indexed": store.indexed_count() if _ingestion_done else 0,
    }


@app.get("/stats")
def stats():
    counts = review_queue.stats()
    return {
        "sections_total": counts["total"],
        "auto_approved": counts["auto_approved"],
        "pending_review": counts["pending_review"],
        "human_approved": counts["approved"],
        "rejected": counts["rejected"],
        "indexed": store.indexed_count() if _ingestion_done else 0,
    }


# ---------------------------------------------------------------------------
# Human-in-the-loop review
# ---------------------------------------------------------------------------
@app.get("/pending-review")
def pending_review():
    return review_queue.pending()


@app.get("/section/{section_id}")
def get_section(section_id: str):
    section = review_queue.get_section(section_id)
    if not section:
        raise HTTPException(status_code=404, detail=f"No such section {section_id}")
    return {
        "id": section["id"], "title": section["title"], "chapter": section["chapter"],
        "text": section["raw_text"], "source_url": section["source_url"],
        "entities": section["entities"],
    }


@app.post("/approve-review-item")
def approve_review_item(decision: ReviewDecision):
    if decision.decision not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="decision must be 'approve' or 'reject'")
    try:
        entry = review_queue.decide(decision.section_id, decision.decision, decision.reviewer)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"No such section {decision.section_id}")

    if decision.decision == "approve":
        store.commit_section(review_queue.get_section(decision.section_id))

    return {"section_id": decision.section_id, "new_status": entry["status"]}


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------
def query_is_high_risk(query: str) -> bool:
    lowered = query.lower()
    return any(kw in lowered for kw in cfg.HIGH_RISK_QUERY_KEYWORDS)


def _ndjson(obj: dict) -> str:
    return json.dumps(obj) + "\n"


SYSTEM_PROMPT = (
    "You are a legal-reference assistant for India's Digital Personal Data Protection "
    "Act, 2023 (DPDP Act) only. Follow these rules strictly:\n"
    "1. Answer using ONLY the numbered [S<n>] context sections provided below. Never use "
    "any outside knowledge about data protection law, even if you recognize the topic "
    "(e.g. GDPR, CCPA, or general legal concepts NOT present in the given context).\n"
    "2. If the provided context does not contain enough information to answer the "
    "question, reply with exactly: \"I don't have information about this in the DPDP "
    "Act, 2023 based on what's currently indexed.\" Do not guess or generalize.\n"
    "3. Keep answers concise (2-4 sentences), plain language, and reference section "
    "numbers only from the context given - never invent a section number.\n"
    "4. This is informational only, not legal advice."
)


def _retrieve(query: str) -> tuple[str, list[str], list[dict], float]:
    """TF-IDF search + graph enrichment. Returns (context_str, citation_ids,
    citation_meta, top_score). Results below cfg.HARD_CUTOFF are filtered
    out server-side by KnowledgeStore.search - this is what stops an
    unrelated question like "What is GDPR?" from being handed any context
    at all, so the model never gets a chance to answer from outside
    knowledge instead of admitting the topic isn't covered."""
    results = store.search(query, cfg.RETRIEVAL_TOP_K, score_threshold=cfg.HARD_CUTOFF)

    context_str = ""
    citations: list[str] = []
    citation_meta: list[dict] = []
    top_score = 0.0
    for section_id, score in results:
        meta = store.get_section_meta(section_id)
        if meta is None:
            continue
        top_score = max(top_score, score)
        citations.append(section_id)
        citation_meta.append({
            "id": section_id, "title": meta["title"], "chapter": meta.get("chapter", ""),
            "score": round(score, 3),
        })
        graph_rows = store.graph_context(section_id)
        graph_info = "\n".join(f"  - {row['type']}: {row['name']}" for row in graph_rows)
        context_str += f"\n[{section_id}] {meta['raw_text'][:cfg.CONTEXT_CHAR_LIMIT]}\n{graph_info}\n"

    logger.info(
        "Retrieval for %r: %d results above HARD_CUTOFF=%.3f, top_score=%.3f",
        query, len(results), cfg.HARD_CUTOFF, top_score,
    )
    return context_str, citations, citation_meta, top_score


def _stream_answer(query: str):
    """Generator yielding NDJSON lines: token chunks, then a final summary line."""
    run_ingestion()

    if _ingestion_error:
        yield _ndjson({
            "type": "done", "status": "no_answer",
            "note": f"The Act text could not be loaded ({_ingestion_error}). "
                    f"This will retry automatically on the next question.",
            "citations": [], "citation_meta": [],
        })
        return

    cached = answer_cache.get(query)
    if cached:
        yield _ndjson({"type": "token", "text": cached["answer"]})
        yield _ndjson({
            "type": "done", "status": "answered",
            "citations": cached["citations"], "citation_meta": cached["citation_meta"], "cached": True,
        })
        return

    context_str, citations, citation_meta, top_score = _retrieve(query)

    if not citations:
        if store.indexed_count() == 0:
            note = "Still finishing ingestion of the Act text - please try again in a moment."
        else:
            note = (
                "I couldn't find anything about this in the DPDP Act, 2023. This assistant "
                "only answers questions about this specific Act, not other laws or general "
                "data-privacy topics."
            )
        yield _ndjson({
            "type": "done", "status": "no_answer", "note": note,
            "citations": [], "citation_meta": [],
        })
        return

    if query_is_high_risk(query):
        yield _ndjson({
            "type": "done", "status": "pending_review",
            "note": "High-risk question (penalty/obligation/cross-border). Held for human "
                    "review instead of an auto-generated answer.",
            "citations": citations, "citation_meta": citation_meta,
        })
        return

    low_confidence = top_score < cfg.SIMILARITY_THRESHOLD
    user_prompt = f"Context from the DPDP Act, 2023:\n{context_str}\n\nQuestion: {query}"

    full_answer = ""
    try:
        for piece in stream_chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]):
            full_answer += piece
            yield _ndjson({"type": "token", "text": piece})
    except GroqError as exc:
        logger.exception("Groq generation failed")
        yield _ndjson({"type": "error", "detail": str(exc)})
        return

    if low_confidence:
        disclaimer = "\n\n(Low-confidence match - please verify against the cited section text.)"
        full_answer += disclaimer
        yield _ndjson({"type": "token", "text": disclaimer})

    answer_cache.set(query, {
        "answer": full_answer, "citations": citations, "citation_meta": citation_meta,
    })
    yield _ndjson({
        "type": "done", "status": "answered",
        "citations": citations, "citation_meta": citation_meta, "cached": False,
    })


@app.post("/ask")
def ask_dpdp(request: QueryRequest, http_request: Request):
    client_key = http_request.client.host if http_request.client else "unknown"
    if not rate_limiter.allow(client_key):
        raise HTTPException(status_code=429, detail="Too many requests - please slow down.")
    query = request.clean_query()
    return StreamingResponse(_stream_answer(query), media_type="application/x-ndjson")
