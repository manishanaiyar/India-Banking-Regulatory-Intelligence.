"""
irdai_ingest.py
----------------
Insurance pillar: IRDAI (Insurance Regulatory and Development Authority
of India) (Protection of Policyholders' Interests) Regulations, 2017 -
the core policyholder-protection regulation covering disclosure, claims
settlement timelines, grievance redressal, and insurer obligations.

Modeled on llm_ingest.py's PMLA path (header-split, no LLM call needed):
IRDAI's regulation text uses regular "Regulation N." headers, the same
shape PMLA's "Section N -" headers have, so a fast deterministic regex
split is both possible and preferable to an LLM call here too - no
Groq rate-limit wait, ingests in seconds.

SOURCE NOTE (same caveat banking_config.py already carries for the other
laws' URLs): irdai.gov.in's own document-detail pages are not stable
direct-download links and blocked automated fetches when checked, so
`irdai_pdf_url` below points at a government-hosted mirror of the same
regulation text (Gujarat Health & Family Welfare Dept., which republishes
it verbatim for insurer compliance reference). Verify this URL still
resolves before a real deployment, and prefer irdai.gov.in's own
Legal-Framework > Regulations page if a stable direct PDF link becomes
available there.
"""

from __future__ import annotations

import logging
import re
import time

from .ingest_common import IngestError, extract_text_from_pdf, fetch_pdf_bytes
from .banking_config import LAWS
from .policy_engine import evaluate as evaluate_policy

logger = logging.getLogger("irdai_ingest")

REGULATION_HEADER_PATTERN = re.compile(r"(?:^|\n)Regulation\s+(\d{1,2}[A-Z]?)\s*[.\-]", re.MULTILINE)

# Same lowercase-plural vocabulary as dpdp_config.SENSITIVE_CATEGORIES and
# banking_config.py's per-law sensitive_categories tuples.
SENSITIVE_CATEGORIES = {"obligations", "penalties"}


def split_irdai_regulations(full_text: str) -> list[tuple[str, str, str]]:
    """Returns [(regulation_number, title, body), ...]. Same header-split
    shape as llm_ingest.split_pmla_sections()."""
    matches = list(REGULATION_HEADER_PATTERN.finditer(full_text))
    parts = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()
        if not body:
            continue
        regulation_number = match.group(1)
        title = body.split("\n", 1)[0].strip()[:200]
        parts.append((regulation_number, title, body))
    return parts


def _classify_irdai_regulation(body: str) -> str:
    """Simple keyword rule, same spirit as llm_ingest._classify_pmla_section()
    - IRDAI's regulation text doesn't need an LLM for category tagging
    either. Extended with insurance-specific "rights" vocabulary
    (policyholder entitlements) that PMLA's classifier didn't need."""
    body_lower = body.lower()
    if "penalty" in body_lower or "fine" in body_lower or "punishable" in body_lower or "contravention" in body_lower:
        return "penalties"
    if "entitled to" in body_lower or "right to" in body_lower or "policyholder shall have" in body_lower:
        return "rights"
    if "shall" in body_lower or "must" in body_lower or "every insurer" in body_lower:
        return "obligations"
    return "definitions"


def _build_entities(category: str | None, title: str) -> dict:
    entities = {"obligations": [], "rights": [], "penalties": [], "definitions": []}
    if category in entities:
        entities[category].append(title)
    return entities


def ingest_irdai() -> list[dict]:
    """Public entry point - same import shape main.py already uses for
    the other laws: `from src.irdai_ingest import ingest_irdai as _ingest_fn`."""
    law_cfg = LAWS["irdai"]
    pdf_bytes = fetch_pdf_bytes(law_cfg["pdf_url"])
    full_text = extract_text_from_pdf(pdf_bytes)
    logger.info("irdai: fetched + extracted %d chars", len(full_text))

    parts = split_irdai_regulations(full_text)
    if not parts:
        raise IngestError(
            "split_irdai_regulations() found zero 'Regulation N.' headers - the source "
            "document's layout may have changed. Refusing to proceed with an empty parse."
        )

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sections = []
    for regulation_number, title, body in parts:
        category = _classify_irdai_regulation(body)
        policy_result = evaluate_policy(body)
        sensitive = category in SENSITIVE_CATEGORIES or "sensitive_data" in policy_result.data_classes
        sections.append({
            "id": f"irdai:{regulation_number}",
            "title": title,
            "chapter": law_cfg["label"],
            "raw_text": body,
            "source_url": law_cfg["pdf_url"],
            "entities": _build_entities(category, title),
            "sensitive": sensitive,
            "confidence": 0.9,
            "law_code": "irdai",
            "category": category,
            "fetched_at": fetched_at,
            "data_classes": policy_result.data_classes,
            "required_controls": policy_result.required_controls,
            "policy_rationale": policy_result.rationale,
        })
    logger.info("irdai: %d regulations via header split", len(sections))
    return sections
