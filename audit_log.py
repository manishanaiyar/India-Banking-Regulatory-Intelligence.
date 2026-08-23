"""
audit_log.py
------------
Audit & Monitoring layer from the architecture diagram. SQLite (stdlib,
zero new dependency, zero signup) - appropriate given this project's
free-tier/no-signup constraints.

Call log_query() once per /ask call (after retrieval, whether or not an
answer was actually generated) and log_review_decision() from
/approve-review-item, so every Checkpoint-1 and Checkpoint-2 decision has
a permanent, exportable record.

NOTE ON RENDER FREE TIER: SQLite writes to a local file, which does NOT
persist across a Render free-tier cold restart (ephemeral filesystem) any
more than your in-memory review queue does today (see main.py's
run_ingestion() docstring on this exact limitation). This is the same
trade-off you already accepted for the review queue - fine for a demo,
worth noting if this becomes a real compliance requirement, in which case
point DB_PATH at a persistent volume or swap to a hosted DB.
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
    retrieved_section_ids TEXT NOT NULL,
    answer_text TEXT,
    was_high_risk INTEGER NOT NULL,
    required_human_review INTEGER NOT NULL,
    data_classes TEXT,
    required_controls TEXT
);

CREATE TABLE IF NOT EXISTS review_decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    item_type TEXT NOT NULL,
    item_reference TEXT NOT NULL,
    law_code TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer_note TEXT
);

CREATE INDEX IF NOT EXISTS idx_query_log_timestamp ON query_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_query_log_law ON query_log(law_code);
CREATE INDEX IF NOT EXISTS idx_review_timestamp ON review_decision_log(timestamp);
"""


@contextmanager
def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO query_log (
                timestamp, law_code, query_text, retrieved_section_ids,
                answer_text, was_high_risk, required_human_review,
                data_classes, required_controls
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                entry.law_code,
                entry.query_text,
                json.dumps(entry.retrieved_section_ids),
                entry.answer_text,
                int(entry.was_high_risk),
                int(entry.required_human_review),
                json.dumps(entry.data_classes or []),
                json.dumps(entry.required_controls or []),
            ),
        )
        return cursor.lastrowid


def log_review_decision(item_type: str, item_reference: str, law_code: str,
                         decision: str, reviewer_note: str | None = None) -> int:
    if decision not in ("approve", "reject", "approved", "rejected"):
        raise ValueError("decision must be an approve/reject value")
    with _connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO review_decision_log (
                timestamp, item_type, item_reference, law_code, decision, reviewer_note
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (datetime.now(timezone.utc).isoformat(), item_type, item_reference,
             law_code, decision, reviewer_note),
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
        return [dict(row) for row in rows]


def export_review_log(limit: int = 1000) -> list[dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM review_decision_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(row) for row in rows]


if __name__ == "__main__":
    init_db()
    row_id = log_query(QueryLogEntry(
        law_code="dpdp",
        query_text="What is the penalty for a data breach?",
        retrieved_section_ids=["33", "34"],
        answer_text=None,
        was_high_risk=True,
        required_human_review=True,
    ))
    print(f"Logged query, id={row_id}")
    print(export_query_log(limit=5))
