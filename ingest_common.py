"""
ingest_common.py
-----------------
Shared fetch/parse/tag pipeline for the three new laws (KYC/AML, PMLA,
RBI Cyber). Produces section dicts shaped to satisfy BOTH consumers of a
section dict in your real codebase:

  1. main.py's run_ingestion()-style loop:
         needs_review = section["sensitive"] or section["confidence"] < threshold
     -> needs: id, sensitive (bool), confidence (float)

  2. dpdp_stores.py's KnowledgeStore.commit_section(), which does:
         entities = section["entities"]
         for label, items in (("Obligation", entities["obligations"]),
                               ("Right", entities["rights"]),
                               ("Penalty", entities["penalties"]),
                               ("Definition", entities["definitions"])):
             ...
     -> needs: entities to be a DICT with exactly these four keys, each a
        list of strings. This was verified against your real dpdp_stores.py
        - an earlier version of this file passed entities=[] (an empty
        LIST), which would have raised `TypeError: list indices must be
        integers` the first time a new-law section was auto-approved and
        committed. Fixed here.

Category-naming note: CATEGORY_KEYWORDS below uses the SAME plural
vocabulary as dpdp_config.py's SENSITIVE_CATEGORIES = ("obligations",
"penalties") and as banking_config.py's per-law sensitive_categories
tuples ("penalties", not "penalty"). An earlier version of this file used
singular category names ("obligation", "penalty") internally, which meant
`category in sensitive_categories` could never match "penalties" against
"penalty" - penalty clauses would have silently skipped human review.
Fixed here by using plural category keys throughout.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import requests

logger = logging.getLogger("ingest_common")

REQUEST_TIMEOUT_SECONDS = 30
USER_AGENT = "banking-regulatory-intelligence-ingest/1.0 (+internal compliance tool)"

# Plural throughout - matches dpdp_config.SENSITIVE_CATEGORIES convention
# and banking_config.py's per-law sensitive_categories tuples exactly.
CATEGORY_KEYWORDS = {
    "obligations": [
        "shall", "must", "is required to", "responsible for", "duty to",
        "obligated to", "every reporting entity shall",
    ],
    "cdd_obligations": [
        "customer due diligence", "cdd", "identify the customer", "verify the identity",
    ],
    "beneficial_owner": [
        "beneficial owner", "ultimate beneficial owner", "controlling ownership interest",
    ],
    "reporting_duties": [
        "suspicious transaction report", "str", "ctr", "report to fiu",
        "financial intelligence unit",
    ],
    "incident_reporting": [
        "incident reporting", "report the incident", "cert-in", "within 6 hours",
        "within six hours",
    ],
    "rights": [
        "right to", "entitled to", "may request",
    ],
    "penalties": [
        "penalty", "fine", "imprisonment", "punishable", "shall be liable",
        "contravention", "confiscation", "prosecution",
    ],
    "definitions": [
        "means", "defined as", "for the purposes of this", "\"means\"",
    ],
}

# Which Neo4j label bucket (matching dpdp_stores.commit_section()'s four
# hardcoded labels: Obligation/Right/Penalty/Definition) each category
# feeds into. Categories with no entry here (cdd_obligations,
# beneficial_owner, reporting_duties, incident_reporting) still drive the
# `sensitive` flag but don't create a graph node under any of the four
# existing labels - extend dpdp_stores.py's label list if you want
# first-class graph nodes for these KYC/PMLA/Cyber-specific categories.
GRAPH_BUCKET_FOR_CATEGORY = {
    "obligations": "obligations",
    "cdd_obligations": "obligations",
    "rights": "rights",
    "penalties": "penalties",
    "definitions": "definitions",
}


class IngestError(Exception):
    """Raised when a source document can't be fetched or parsed."""


def fetch_pdf_bytes(url: str) -> bytes:
    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise IngestError(f"Failed to fetch source document from {url}: {exc}") from exc

    content_type = resp.headers.get("Content-Type", "")
    if "pdf" not in content_type.lower() and not resp.content.startswith(b"%PDF"):
        raise IngestError(
            f"URL {url} did not return a PDF (Content-Type: {content_type}). "
            "The document may have moved, or the site blocked the request - "
            "verify pdf_url in banking_config.py and see INTEGRATION_GUIDE.md "
            "for the RBI 403-Forbidden note."
        )
    return resp.content


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Uses pypdf. Your real dpdp_ingest.py also uses pypdf (see its
    extract_text()), so this is consistent with your existing dependency,
    not a new one."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise IngestError(
            "pypdf is not installed. Add `pypdf` to requirements.txt "
            "(your existing dpdp_ingest.py already depends on it)."
        ) from exc

    import io
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to extract text from page %d: %s", i, exc)
    return "\n".join(pages)


def _tag_category_and_confidence(text: str) -> tuple[str | None, float]:
    text_lower = text.lower()
    scores = {
        cat: sum(len(re.findall(re.escape(kw), text_lower)) for kw in kws)
        for cat, kws in CATEGORY_KEYWORDS.items()
    }
    total = sum(scores.values())
    if total == 0:
        return None, 0.0
    best_cat = max(scores, key=scores.get)
    best_score = scores[best_cat]
    raw_confidence = best_score / total
    sparsity_penalty = min(total / 3, 1.0)
    return best_cat, round(raw_confidence * sparsity_penalty, 3)


def _build_entities(category: str | None, title: str) -> dict:
    """Always returns the full 4-key dict shape dpdp_stores.commit_section()
    requires, regardless of which (or whether any) category matched."""
    entities = {"obligations": [], "rights": [], "penalties": [], "definitions": []}
    bucket = GRAPH_BUCKET_FOR_CATEGORY.get(category)
    if bucket:
        entities[bucket].append(title)
    return entities


def build_tagged_sections_for_law(
    law_code: str,
    pdf_url: str,
    section_pattern: str,
    sensitive_categories: tuple[str, ...],
    chapter_label: str,
) -> list[dict]:
    """Fetch, parse, and tag sections for one non-DPDP law. Output shape
    is compatible with BOTH main.py's needs_review check AND
    dpdp_stores.KnowledgeStore.commit_section() - see this module's
    docstring for exactly which fields are load-bearing and why.
    """
    pdf_bytes = fetch_pdf_bytes(pdf_url)
    full_text = extract_text_from_pdf(pdf_bytes)

    matches = list(re.finditer(section_pattern, full_text, re.MULTILINE))
    if not matches:
        raise IngestError(
            f"section_pattern matched zero sections for law={law_code}. "
            "The document structure may have changed, or the regex needs "
            "adjustment - refusing to proceed rather than silently "
            "returning an empty/garbage parse."
        )

    fetched_at = datetime.now(timezone.utc).isoformat()
    sections: list[dict] = []
    for i, match in enumerate(matches):
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()
        if not body:
            continue

        section_number = match.group(1)
        lines = body.split("\n", 1)
        title = lines[0].strip()[:200] or f"Clause {section_number}"
        category, confidence = _tag_category_and_confidence(body)
        sensitive = category is not None and category in sensitive_categories

        sections.append({
            "id": f"{law_code}:{section_number}",
            "title": title,
            "chapter": chapter_label,
            "raw_text": body,
            "source_url": pdf_url,
            "entities": _build_entities(category, title),
            "sensitive": sensitive,
            "confidence": confidence,
            "law_code": law_code,
            "category": category,
            "fetched_at": fetched_at,
        })

    logger.info("Parsed %d sections for law=%s", len(sections), law_code)
    return sections
