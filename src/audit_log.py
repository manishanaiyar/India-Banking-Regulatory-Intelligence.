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

CHANGES from the original version (see conversation notes):
1. reviewer_id is now a REQUIRED, structured field on review_decision_log
   - separate from the free-text reviewer_note. The original version only
   had reviewer_note, and main.py was passing the reviewer's identity
   into that free-text field (`reviewer_note=decision.reviewer`) - which
   meant "who approved this" and "any remarks about the decision" were
   conflated into one unstructured column. A real accountable audit
   trail needs the actor identity as its own field, queryable and
   NOT-NULL, even before real auth exists (reviewer_id currently comes
   from the same demo_reviewer default main.py already used - this
   doesn't add auth, it just gives the identity its own honest column so
   wiring up real auth later is a column-population change, not a
   schema change). ACTION NEEDED: main.py's call to log_review_decision()
   must change `reviewer_note=decision.reviewer` to
   `reviewer_id=decision.reviewer` - see PATCH note at the bottom of
   this docstring.

2. query_log_sections is a NEW normalized linking table (query_log_id,
   section_id) replacing the old approach of only storing
   retrieved_section_ids as a JSON blob inside query_log. The JSON blob
   is NOT queryable by a specific section_id without loading and
   parsing every row - it could not answer "which queries cited section
   X" or "was section X reviewed after being cited in a query" without
   a full-table scan and manual JSON parsing in Python. The normalized
   table makes both of those a plain SQL JOIN - see
   get_queries_citing_section() and get_review_history_for_section()
   below. export_query_log() still returns retrieved_section_ids as a
   list per row (reconstructed via JOIN) so any existing caller of that
   function sees the same shape as before.

3. Immutability is now DB-ENFORCED, not just a design intention. SQLite
   triggers on all three tables raise on any UPDATE or DELETE, so a bug
   (or a person) can't silently alter or remove an audit record - they
   get a SQLite error instead. This does NOT protect against someone
   deleting the .db file itself or editing it outside SQLite's own
   integrity checks - true tamper-evidence (e.g. hash-chaining each row
   to the previous one) is a further step, not implemented here; noted
   as a possible future enhancement rather than built now, to avoid
   over-engineering an MVP that's still on a free-tier ephemeral disk
   (see point 4).

4. NOTE ON RENDER FREE TIER (unchanged from original, restated): SQLite
   writes to a local file, which does NOT persist across a Render
   free-tier cold restart (ephemeral filesystem), and does NOT share
   state across multiple instances if this service is ever scaled
   horizontally - each instance would see its own empty/independent
   audit_log.db. Fine for a single-instance demo; before calling this
   "production audit trail" for real compliance use, point DB_PATH at a
   persistent volume or move to a hosted DB (Postgres, etc.) that
   supports multiple concurrent writers.

PATCH NEEDED IN main.py (one-line change, this file cannot make it for
you since it doesn't touch main.py): in approve_review_item(), change

    audit_log.log_review_decision(
        item_type="section", item_reference=decision.section_id, law_code=law,
        decision=entry["status"], reviewer_note=decision.reviewer,
    )

to

    audit_log.log_review_decision(
        item_type="section", item_reference=decision.section_id, law_code=law,
        decision=entry["status"], reviewer_id=decision.reviewer,
    )
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

CREATE INDEX IF NOT EXISTS idx_query_log_timestamp ON query_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_query_log_law ON query_log(law_code);
CREATE INDEX IF NOT EXISTS idx_query_log_sections_section ON query_log_sections(section_id);
CREATE INDEX IF NOT EXISTS idx_review_timestamp ON review_decision_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_review_item_reference ON review_decision_log(item_reference);

-- Immutability: an audit trail that can be silently edited or deleted
-- isn't an audit trail. These triggers make UPDATE/DELETE fail with a
-- SQLite error on all three tables, at the database level, rather than
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
    reviewer_note is optional free-text remarks about the decision -
    these are now two separate fields; see this module's docstring for
    why they were merged before and why that was a problem."""
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


def get_queries_citing_section(section_id: str, limit: int = 100) -> list[dict]:
    """NEW: every query that cited a given section - the other half of
    the lineage that a JSON blob couldn't answer without a full scan."""
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
    """NEW: full decision history for one section (item_reference) -
    a section can in principle be revisited (approved, later
    re-flagged, reviewed again), and this returns that whole history in
    order, oldest first."""
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

    print("Queries citing S33:", get_queries_citing_section("S33"))
    print("Review history for S33:", get_review_history_for_section("S33"))

    # Immutability check - this should raise sqlite3.IntegrityError / OperationalError
    try:
        with _connect() as conn:
            conn.execute("UPDATE query_log SET answer_text = 'tampered' WHERE id = ?", (query_id,))
        print("IMMUTABILITY CHECK FAILED - update should have been blocked!")
    except sqlite3.Error as exc:
        print(f"Immutability check passed - update correctly blocked: {exc}")
