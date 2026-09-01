"""
Regression + integration tests for main.py's request-handling logic:
the approve-review-item crash fix, ingestion audit logging, and the new
retrieval/generation observability and anti-hallucination pieces.

Uses FastAPI's TestClient (in-process, no real network/Neo4j/Groq needed
for these specific paths) so this suite runs anywhere, including CI,
without live credentials.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # Isolate the audit DB per test run so tests don't interfere with each
    # other or leave state behind in the repo.
    monkeypatch.chdir(tmp_path)
    import main
    with TestClient(main.app) as c:
        yield c, main


def _register_fake_section(main, law="dpdp", section_id="S_TEST"):
    fake_section = {
        "id": section_id, "title": "Test Section", "chapter": "Chapter Test",
        "raw_text": "Some test text about consent and processing of personal data.",
        "source_url": "http://example.com",
        "entities": {"obligations": [], "rights": [], "penalties": [], "definitions": []},
        "confidence": 0.5, "sensitive": True,
        "data_classes": ["customer_pii"], "required_controls": ["masking"],
    }
    store = main._get_store(law)
    queue = main._get_review_queue(law)
    queue.register(fake_section, needs_review=True)
    return fake_section


def test_approve_review_item_does_not_crash(client):
    """Regression test for the bug where log_review_decision() was called
    without the required reviewer_id argument, raising TypeError on every
    single approve/reject call."""
    c, main = client
    _register_fake_section(main)
    resp = c.post("/approve-review-item?law=dpdp", json={
        "section_id": "S_TEST", "decision": "approve", "reviewer": "test_reviewer",
    })
    assert resp.status_code == 200


def test_approve_review_item_logs_reviewer_id_correctly(client):
    c, main = client
    _register_fake_section(main)
    c.post("/approve-review-item?law=dpdp", json={
        "section_id": "S_TEST", "decision": "approve", "reviewer": "alice",
    })
    logs = c.get("/audit-log/reviews").json()
    match = next(row for row in logs if row["item_reference"] == "S_TEST")
    assert match["reviewer_id"] == "alice"


def test_approve_review_item_missing_section_returns_404_not_500(client):
    c, _main = client
    resp = c.post("/approve-review-item?law=dpdp", json={
        "section_id": "does_not_exist", "decision": "approve", "reviewer": "alice",
    })
    assert resp.status_code == 404


def test_ingestion_classification_log_route_exists_and_is_populated(client):
    """Regression test: log_ingestion_classification() existed but was
    never called anywhere, and /audit-log/ingestion didn't even exist as
    a route - so this endpoint always 404'd or returned nothing."""
    c, main = client
    section = _register_fake_section(main)
    main._get_store("dpdp").commit_section(section)
    from src import audit_log
    audit_log.log_ingestion_classification(
        law_code="dpdp", section_id=section["id"],
        data_classes=section["data_classes"], required_controls=section["required_controls"],
    )
    resp = c.get("/audit-log/ingestion?law=dpdp")
    assert resp.status_code == 200
    rows = resp.json()
    assert any(row["section_id"] == "S_TEST" for row in rows)


def test_retrieve_includes_timing_observability(client):
    c, main = client
    section = _register_fake_section(main)
    main._get_store("dpdp").commit_section(section)
    context_str, citations, citation_meta, top_score, timings = main._retrieve(
        "consent for processing personal data", "dpdp"
    )
    assert citations == ["S_TEST"]
    assert "retrieval_ms" in timings
    assert "graph_ms" in timings
    assert timings["retrieval_ms"] >= 0


def test_citation_faithfulness_passes_for_grounded_answer():
    from main import _check_citation_faithfulness
    assert _check_citation_faithfulness("See [S1] for details.", ["S1", "S2"]) == []


def test_citation_faithfulness_flags_hallucinated_citation():
    from main import _check_citation_faithfulness
    result = _check_citation_faithfulness("See [S1] and also [S99].", ["S1", "S2"])
    assert result == ["S99"]


def test_smart_context_window_falls_back_to_prefix_when_text_fits():
    from main import _best_context_window
    text = "short text"
    assert _best_context_window(text, {"short"}, char_limit=100) == text


def test_smart_context_window_falls_back_when_no_query_terms():
    from main import _best_context_window
    text = "x" * 500
    assert _best_context_window(text, set(), char_limit=100) == text[:100]
