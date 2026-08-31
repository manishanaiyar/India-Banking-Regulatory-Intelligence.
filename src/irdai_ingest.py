"""
irdai_ingest.py
----------------
Insurance pillar: IRDAI (Insurance Regulatory and Development Authority
of India) (Protection of Policyholders' Interests) Regulations, 2017 -
the core policyholder-protection regulation covering disclosure, claims
settlement timelines, grievance redressal, and insurer obligations.

Modeled on llm_ingest.py's PMLA path (HTML header-split, no LLM call
needed): the source page's 20 numbered regulations are consistently
bolded ("**N. TITLE IN CAPS**"), the same shape PMLA's "Section N -"
headers have, so a fast deterministic regex split is both possible and
preferable to an LLM call here too - no Groq rate-limit wait, ingests
in seconds.

SOURCE NOTE: irdai.gov.in's own document-detail pages block automated
fetches, and a government-mirror PDF that was tried first turned out to
be unreachable in live testing (connection error). `html_url` below
points at a stable, short, working HTML mirror of the full regulation
text (all 20 Regulations + Annexure-I Grievance Redressal Procedure)
instead - verified reachable before this was wired up. If irdai.gov.in
ever publishes a stable direct PDF link, prefer that over a mirror.

HEADER-DETECTION NOTE: after strip_html_tags() (imported from
llm_ingest.py) replaces every HTML tag with a newline, each bolded
regulation header like "<strong>9. MATTERS TO BE STATED...</strong>"
becomes its own line: "\\n9. MATTERS TO BE STATED...\\n". Ordinary body
text that also starts with "N." (e.g. regulation 9's own internal
"1. A life insurance policy shall clearly state:") is NOT all-caps, so
requiring the captured title to be ALL-CAPS is what distinguishes a real
regulation header from an internal numbered sub-clause - see
REGULATION_HEADER_PATTERN.
"""

from __future__ import annotations

import logging
import re
import time

from .ingest_common import IngestError
from .llm_ingest import fetch_html_text, strip_html_tags
from .banking_config import LAWS
from .policy_engine import evaluate as evaluate_policy

logger = logging.getLogger("irdai_ingest")

MAX_REGULATION = 20

# See module docstring's "HEADER-DETECTION NOTE" for why the ALL-CAPS
# requirement matters here.
_HEADER_RE_CACHE: dict[int, "re.Pattern[str]"] = {}


def _regulation_header_pattern(number: int) -> "re.Pattern[str]":
    if number not in _HEADER_RE_CACHE:
        _HEADER_RE_CACHE[number] = re.compile(
            rf"(?:^|\n)\s*{number}\.\s+([A-Z][A-Z0-9 ,\-/()']{{2,90}}?):?\s*\n", re.MULTILINE
        )
    return _HEADER_RE_CACHE[number]


ANNEXURE_HEADER_PATTERN = re.compile(r"(?:^|\n)\s*Annexure\s*[-\u2013]\s*I\s*\n", re.MULTILINE)

# Same lowercase-plural vocabulary as dpdp_config.SENSITIVE_CATEGORIES and
# banking_config.py's per-law sensitive_categories tuples.
SENSITIVE_CATEGORIES = {"obligations", "penalties"}


def split_irdai_regulations(full_text: str, max_regulation: int = MAX_REGULATION) -> list[dict]:
    """Sequential-anchor split (same technique as gdpr_ingest.split_into_articles()
    and dpdp_ingest.split_into_sections()) - only searches for Regulation N
    after the position where Regulation N-1 was found, which combined with
    the ALL-CAPS title requirement avoids false matches on the many plain
    "N. ..." numbered sub-clauses inside each regulation's own body text."""
    positions: dict[int, tuple[int, int, str]] = {}  # number -> (body_start, header_end, title)
    cursor = 0
    for n in range(1, max_regulation + 1):
        match = _regulation_header_pattern(n).search(full_text, cursor)
        if not match:
            continue
        positions[n] = (match.start(), match.end(), match.group(1).strip())
        cursor = match.end()

    # Bonus: Annexure-I (Grievance Redressal Procedure) - real, useful
    # content that follows Regulation 20, included as a 21st item.
    annexure_match = ANNEXURE_HEADER_PATTERN.search(full_text, cursor)

    found_numbers = sorted(positions.keys())
    parsed = []
    for i, n in enumerate(found_numbers):
        start, _header_end, title = positions[n]
        end = positions[found_numbers[i + 1]][0] if i + 1 < len(found_numbers) else (
            annexure_match.start() if annexure_match else len(full_text)
        )
        body = full_text[start:end].strip()
        parsed.append({"number": n, "title": title, "body": body, "is_annexure": False})

    if annexure_match:
        annexure_body = full_text[annexure_match.start():].strip()
        parsed.append({
            "number": max_regulation + 1, "title": "Annexure-I: Grievance Redressal Procedure",
            "body": annexure_body, "is_annexure": True,
        })

    return parsed


def _classify_irdai_regulation(body: str) -> str:
    """Simple keyword rule, same spirit as llm_ingest._classify_pmla_section()
    - this source doesn't need an LLM for category tagging either.
    Extended with insurance-specific "rights" vocabulary (policyholder
    entitlements) that PMLA's classifier didn't need."""
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
    html = fetch_html_text(law_cfg["html_url"])
    full_text = strip_html_tags(html)
    logger.info("irdai: fetched + stripped %d chars", len(full_text))

    parts = split_irdai_regulations(full_text)
    if not parts:
        raise IngestError(
            "split_irdai_regulations() found zero numbered regulation headers - the source "
            "page's layout may have changed. Refusing to proceed with an empty parse."
        )

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sections = []
    for part in parts:
        category = _classify_irdai_regulation(part["body"])
        policy_result = evaluate_policy(part["body"])
        sensitive = category in SENSITIVE_CATEGORIES or "sensitive_data" in policy_result.data_classes
        id_suffix = "annexure1" if part["is_annexure"] else str(part["number"])
        sections.append({
            "id": f"irdai:{id_suffix}",
            "title": part["title"],
            "chapter": law_cfg["label"],
            "raw_text": part["body"],
            "source_url": law_cfg["html_url"],
            "entities": _build_entities(category, part["title"]),
            "sensitive": sensitive,
            "confidence": 0.9,
            "law_code": "irdai",
            "category": category,
            "fetched_at": fetched_at,
            "data_classes": policy_result.data_classes,
            "required_controls": policy_result.required_controls,
            "policy_rationale": policy_result.rationale,
        })

    if len(parts) < MAX_REGULATION:
        missing = sorted(set(range(1, MAX_REGULATION + 1)) - {p["number"] for p in parts if not p["is_annexure"]})
        logger.warning(
            "Only parsed %d/%d IRDAI regulations - missing numbers: %s. "
            "The source page's layout may have changed; check split_irdai_regulations().",
            len(parts), MAX_REGULATION, missing,
        )
    else:
        logger.info("irdai: %d regulations (incl. Annexure-I) via header split", len(sections))
    return sections
