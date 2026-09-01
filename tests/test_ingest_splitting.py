"""
Regression tests for the "sequential-anchor" section/article/regulation
splitting logic shared across dpdp_ingest.py, gdpr_ingest.py, and
irdai_ingest.py. Each of these parses a legal document into numbered
sections using a regex search that starts from wherever the previous
number was found - the whole point being that this avoids false matches
on inline references to a section/article number that appear BEFORE the
real numbered heading (e.g. GDPR's recitals reference "Article 16" long
before Article 16's actual heading; IRDAI's regulations contain their own
internal numbered sub-clauses that could be mistaken for the next
regulation's heading). These tests exist because that exact failure mode
was found and fixed during development - this locks the fix in.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.gdpr_ingest import split_into_articles
from src.irdai_ingest import split_irdai_regulations


def test_gdpr_split_ignores_inline_article_references_in_recitals():
    text = """
Having regard to the Treaty, and in particular Article 16 thereof,
(12) Article 16(2) TFEU mandates the rules. See also Articles 12 to 15,
and Article 8(1) of the Charter.

Article 1
Subject-matter and objectives
This Regulation lays down rules relating to the protection of natural persons.

Article 2
Material scope
This Regulation applies to the processing of personal data.
"""
    articles = split_into_articles(text, max_article=2)
    assert [a["number"] for a in articles] == [1, 2]
    assert "Subject-matter" in articles[0]["raw_text"]
    # The inline recital reference to "Article 16" must not have been
    # picked up as a section boundary, and must not appear inside Article 1's body.
    assert "thereof" not in articles[0]["raw_text"]


def test_gdpr_split_ignores_forward_references_within_an_articles_own_body():
    text = """
Article 5
Principles relating to processing of personal data
Personal data shall be processed as set out in Article 6 and further detailed elsewhere.

Article 6
Lawfulness of processing
Processing shall be lawful only if consent has been given.
"""
    articles = split_into_articles(text, max_article=6)
    assert [a["number"] for a in articles] == [5, 6]
    assert "Lawfulness" in articles[1]["title"]


def test_irdai_split_ignores_internal_numbered_subclauses():
    """Regulation 14's own body contains plain (non-caps) numbered
    sub-clauses like '2. (i) A death claim...' - these must NOT be
    mistaken for the next regulation's heading, which requires an
    ALL-CAPS title."""
    text = """
14. CLAIMS PROCEDURE IN RESPECT OF A LIFE INSURANCE POLICY

1. A life insurer, upon receiving a death claim, shall process the claim without delay.

2. (i) A death claim under a life insurance policy shall be paid or be rejected or
repudiated giving all the relevant reasons, within 30 days from the date of receipt
of all relevant papers.

20. TRANSITORY PROVISIONS

The insurers shall revise all the policy document formats.
"""
    parts = split_irdai_regulations(text, max_regulation=20)
    numbers = [p["number"] for p in parts if not p["is_annexure"]]
    assert numbers == [14, 20]
    assert "death claim" in parts[0]["body"]
    assert "TRANSITORY" in parts[1]["title"]


def test_irdai_split_captures_annexure_as_bonus_item():
    text = """
20. TRANSITORY PROVISIONS

The insurers shall revise all the policy document formats.

Annexure – I
Grievance Redressal Procedure

A complainant who wishes to make a complaint shall approach the grievance officer.
"""
    parts = split_irdai_regulations(text, max_regulation=20)
    assert any(p["is_annexure"] for p in parts)
    annexure = next(p for p in parts if p["is_annexure"])
    assert "Grievance" in annexure["body"]


def test_irdai_split_returns_empty_list_on_unrecognized_layout():
    parts = split_irdai_regulations("This document has no numbered regulations at all.", max_regulation=20)
    assert parts == []
