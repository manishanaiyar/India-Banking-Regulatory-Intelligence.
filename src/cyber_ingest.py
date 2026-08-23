"""
cyber_ingest.py
----------------
Cybersecurity pillar: RBI Cyber Security Framework + CERT-In incident
reporting directions. Same pattern as kyc_pmla_ingest.py - see that
file's and ingest_common.py's docstrings for the verification warning.
"""

from __future__ import annotations

import logging

from src.banking_config import LAWS
from src.ingest_common import IngestError, build_tagged_sections_for_law

logger = logging.getLogger("cyber_ingest")

CYBER_SECTION_PATTERN = r"^(\d{1,2}\.\d{1,3})\s+"  # e.g. "3.2 " - VERIFY against actual doc


def ingest_cyber() -> list[dict]:
    cfg = LAWS["rbi_cyber"]
    return build_tagged_sections_for_law(
        law_code="rbi_cyber",
        pdf_url=cfg["pdf_url"],
        section_pattern=CYBER_SECTION_PATTERN,
        sensitive_categories=cfg["sensitive_categories"],
        chapter_label=cfg["label"],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        sections = ingest_cyber()
        print(f"Cyber: parsed {len(sections)} sections")
    except IngestError as exc:
        print(f"Cyber ingest failed: {exc}")
