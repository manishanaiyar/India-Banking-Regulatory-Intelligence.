"""
audit_log.py
------------
Audit & Monitoring layer from the architecture diagram. SQLite (stdlib,
zero new dependency, zero signup) - appropriate given this project's
free-tier/no-signup constraints.

Call log_query() once per /ask call (after retrieval, whether or not an
answer was actually generated), log_review_decision() from
/approve-review-item, and log_ingestion_classification() once per section
during ingestion - so every classification, review, and query decision
has a permanent, exportable record.

CHANGES from the previous version:

1. reviewer_id is a REQUIRED, structured field on review_decision_log -
   separate from the free-text reviewer_note. main.py must call this with
   reviewer_id=..., not reviewer_note=... (that was a live bug - see
   PATCH note below; main.py in this delivery already has it fixed).

2. query_log_sections is a normalized linking table (query_log_id,
   section_id) instead of a JSON blob, so "which queries cited section X"
   is a plain SQL JOIN (get_queries_citing_section()) instead of a full-
   table scan with manual JSON parsing.

3. Immutability is DB-ENFORCED via SQLite triggers on all tables (UPDATE/
   DELETE raise). This does not protect against someone deleting the .db
   file itself - true tamper-evidence (hash-chaining) is a further step,
   not implemented here, to avoid over-engineering an MVP still on a
   free-tier ephemeral disk (see point 4).

4. NOTE ON RENDER FREE TIER: SQLite writes to a local file, which does
   NOT persist across a Render free-tier cold restart (ephemeral
   filesystem), and does NOT share state across multiple instances if
   this service is ever scaled horizontally. Fine for a single-instance
   demo; point DB_PATH at a persistent volume or move to Postgres before
   calling this a real compliance audit trail.

5. NEW - ingestion_policy_log table + log_ingestion_classification() /
   export_ingestion_log(). Previously the Policy Engine's classification
   of user QUERIES was audited (via query_log.data_classes /
   required_controls) but classification of the actual ingested
   regulatory TEXT (every DPDP/KYC/PMLA/Cyber section) was not recorded
   anywhere - dpdp_ingest.py / llm_ingest.py compute data_classes /
   required_controls per section now, but nothing logged them. main.py's
   ingestion loops now call this once per section.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "audit_log.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    law_code TEXT NOT NULL,
    query_text TEXT NOT NULL,
    answer_text TEXT,
    was_high_risk INTEGER NOT NULL,
    required_human_review INTEGER NOT NULL,
    data_classes TEXT,
    required_controls TEXT
);

CREATE TABLE IF NOT EXISTS query_log_sections (
    query_log_id INTEGER NOT NULL REFERENCES query_log(id),
    section_id TEXT NOT NULL,
    PRIMARY KEY (query_log_id, section_id)
);

CREATE TABLE IF NOT EXISTS review_decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_reference TEXT NOT NULL,
    law_code TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer_id TEXT NOT NULL,
    reviewer_note TEXT
);

CREATE TABLE IF NOT EXISTS ingestion_policy_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    law_code TEXT NOT NULL,
    section_id TEXT NOT NULL,
    data_classes TEXT,
    required_controls TEXT
);

CREATE INDEX IF NOT EXISTS idx_query_log_timestamp ON query_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_query_log_law ON query_log(law_code);
CREATE INDEX IF NOT EXISTS idx_query_log_sections_section ON query_log_sections(section_id);
CREATE INDEX IF NOT EXISTS idx_review_timestamp ON review_decision_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_review_item_reference ON review_decision_log(item_reference);
CREATE INDEX IF NOT EXISTS idx_ingestion_policy_law ON ingestion_policy_log(law_code);
CREATE INDEX IF NOT EXISTS idx_ingestion_policy_section ON ingestion_policy_log(section_id);

-- Immutability: an audit trail that can be silently edited or deleted
-- isn't an audit trail. These triggers make UPDATE/DELETE fail with a
-- SQLite error on every table, at the database level, rather than
-- relying on every caller to simply never do it.
CREATE TRIGGER IF NOT EXISTS trg_query_log_no_update
BEFORE UPDATE ON query_log
BEGIN SELECT RAISE(ABORT, 'query_log is append-only: audit records cannot be modified'); END;

CREATE TRIGGER IF NOT EXISTS trg_query_log_no_delete
BEFORE DELETE ON query_log
BEGIN SELECT RAISE(ABORT, 'query_log is append-only: audit records cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_query_log_sections_no_update
BEFORE UPDATE ON query_log_sections
BEGIN SELECT RAISE(ABORT, 'query_log_sections is append-only: audit records cannot be modified'); END;

CREATE TRIGGER IF NOT EXISTS trg_query_log_sections_no_delete
BEFORE DELETE ON query_log_sections
BEGIN SELECT RAISE(ABORT, 'query_log_sections is append-only: audit records cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_review_decision_log_no_update
BEFORE UPDATE ON review_decision_log
BEGIN SELECT RAISE(ABORT, 'review_decision_log is append-only: audit records cannot be modified'); END;

CREATE TRIGGER IF NOT EXISTS trg_review_decision_log_no_delete
BEFORE DELETE ON review_decision_log
BEGIN SELECT RAISE(ABORT, 'review_decision_log is append-only: audit records cannot be deleted'); END;

CREATE TRIGGER IF NOT EXISTS trg_ingestion_policy_log_no_update
BEFORE UPDATE ON ingestion_policy_log
BEGIN SELECT RAISE(ABORT, 'ingestion_policy_log is append-only: audit records cannot be modified'); END;

CREATE TRIGGER IF NOT EXISTS trg_ingestion_policy_log_no_delete
BEFORE DELETE ON ingestion_policy_log
BEGIN SELECT RAISE(ABORT, 'ingestion_policy_log is append-only: audit records cannot be deleted'); END;
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


@dataclass
class QueryLogEntry:
    law_code: str
    query_text: str
    retrieved_section_ids: list[str]
    answer_text: str | None
    was_high_risk: bool
    required_human_review: bool
    data_classes: list[str] | None = None
    required_controls: list[str] | None = None


def log_query(entry: QueryLogEntry) -> int:
    """Inserts one query_log row, plus one query_log_sections row per
    cited section id - this is what makes get_queries_citing_section()
    and the retrieved_section_ids reconstruction in export_query_log()
    possible without JSON parsing."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO query_log (
                timestamp, law_code, query_text,
                answer_text, was_high_risk, required_human_review,
                data_classes, required_controls
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                entry.law_code,
                entry.query_text,
                entry.answer_text,
                int(entry.was_high_risk),
                int(entry.required_human_review),
                json.dumps(entry.data_classes or []),
                json.dumps(entry.required_controls or []),
            ),
        )
        query_log_id = cursor.lastrowid
        for section_id in entry.retrieved_section_ids:
            conn.execute(
                "INSERT INTO query_log_sections (query_log_id, section_id) VALUES (?, ?)",
                (query_log_id, section_id),
            )
        return query_log_id


def log_review_decision(
    item_type: str,
    item_reference: str,
    law_code: str,
    decision: str,
    reviewer_id: str,
    reviewer_note: str | None = None,
) -> int:
    """reviewer_id is who made the decision (structured, required).
    reviewer_note is optional free-text remarks - kept as two separate
    fields so "who decided" is always queryable independent of remarks."""
    if decision not in ("approve", "reject", "approved", "rejected"):
        raise ValueError("decision must be an approve/reject value")
    if not reviewer_id:
        raise ValueError("reviewer_id is required - an audit trail entry must name an actor")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_decision_log (
                timestamp, item_type, item_reference, law_code,
                decision, reviewer_id, reviewer_note
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), item_type, item_reference,
             law_code, decision, reviewer_id, reviewer_note),
        )
        return cursor.lastrowid


def log_ingestion_classification(
    law_code: str,
    section_id: str,
    data_classes: list[str] | None,
    required_controls: list[str] | None,
) -> int:
    """NEW: one row per ingested section, recording what the Policy
    Engine classified it as at ingestion time - the "DATA CLASSIFICATION
    -> POLICY ENGINE -> AUDIT & MONITORING" path from the architecture
    diagram, for the document text itself (not just live queries)."""
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO ingestion_policy_log (
                timestamp, law_code, section_id, data_classes, required_controls
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                law_code, section_id,
                json.dumps(data_classes or []),
                json.dumps(required_controls or []),
            ),
        )
        return cursor.lastrowid


def export_query_log(law_code: str | None = None, limit: int = 1000) -> list[dict]:
    with _connect() as conn:
        if law_code:
            rows = conn.execute(
                "SELECT * FROM query_log WHERE law_code = ? ORDER BY id DESC LIMIT ?",
                (law_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM query_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        results = []
        for row in rows:
            d = dict(row)
            section_rows = conn.execute(
                "SELECT section_id FROM query_log_sections WHERE query_log_id = ?", (d["id"],)
            ).fetchall()
            d["retrieved_section_ids"] = [r["section_id"] for r in section_rows]
            results.append(d)
        return results


def export_review_log(limit: int = 1000) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM review_decision_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


def export_ingestion_log(law_code: str | None = None, limit: int = 1000) -> list[dict]:
    """NEW: paired with log_ingestion_classification() - lets /audit-log/
    ingestion show every section's classification at ingestion time."""
    with _connect() as conn:
        if law_code:
            rows = conn.execute(
                "SELECT * FROM ingestion_policy_log WHERE law_code = ? ORDER BY id DESC LIMIT ?",
                (law_code, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ingestion_policy_log ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]


def get_queries_citing_section(section_id: str, limit: int = 100) -> list[dict]:
    """Every query that cited a given section."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT q.* FROM query_log q
            JOIN query_log_sections qs ON qs.query_log_id = q.id
            WHERE qs.section_id = ?
            ORDER BY q.id DESC LIMIT ?
            """,
            (section_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]


def get_review_history_for_section(section_id: str) -> list[dict]:
    """Full decision history for one section (item_reference), oldest
    first - a section can in principle be revisited (approved, later
    re-flagged, reviewed again)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM review_decision_log WHERE item_reference = ? ORDER BY id ASC",
            (section_id,),
        ).fetchall()
        return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
    query_id = log_query(QueryLogEntry(
        law_code="dpdp",
        query_text="What is the penalty for a data breach?",
        retrieved_section_ids=["S33", "S34"],
        answer_text=None,
        was_high_risk=True,
        required_human_review=True,
    ))
    print(f"Logged query, id={query_id}")

    review_id = log_review_decision(
        item_type="section", item_reference="S33", law_code="dpdp",
        decision="approve", reviewer_id="demo_reviewer",
        reviewer_note="Verified against source PDF page 12",
    )
    print(f"Logged review decision, id={review_id}")

    ingestion_id = log_ingestion_classification(
        law_code="dpdp", section_id="S33",
        data_classes=["sensitive_data"], required_controls=["masking", "encryption", "tokenisation"],
    )
    print(f"Logged ingestion classification, id={ingestion_id}")

    print("Queries citing S33:", get_queries_citing_section("S33"))
    print("Review history for S33:", get_review_history_for_section("S33"))

    # Immutability check - this should raise sqlite3.IntegrityError / OperationalError
    try:
        with _connect() as conn:
            conn.execute("UPDATE query_log SET answer_text = 'tampered' WHERE id = ?", (query_id,))
        print("IMMUTABILITY CHECK FAILED - update should have been blocked!")
    except sqlite3.Error as exc:
        print(f"Immutability check passed - update correctly blocked: {exc}")
