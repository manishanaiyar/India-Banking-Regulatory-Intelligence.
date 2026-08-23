"""
kyc_pmla_ingest.py
-------------------
RBI/KYC pillar: RBI Master Direction on KYC (incl. CDD, Beneficial Owner)
and PMLA, 2002 + Rules. Uses ingest_common.build_tagged_sections_for_law()
so output is shaped exactly like your existing DPDP sections.

VERIFY THESE PATTERNS before production ingest - see ingest_common.py's
module docstring. Open the actual current PDFs and check how they number
clauses; adjust the regex if it doesn't match.
"""

from __future__ import annotations

import logging

from banking_config import LAWS
from ingest_common import IngestError, build_tagged_sections_for_law

logger = logging.getLogger("kyc_pmla_ingest")

KYC_SECTION_PATTERN = r"^(\d{1,2}\.\d{1,3})\s+"           # e.g. "4.3 "
PMLA_SECTION_PATTERN = r"^Section\s+(\d{1,3}[A-Z]?)\.\s+"  # e.g. "Section 12A. "


def ingest_kyc() -> list[dict]:
    cfg = LAWS["kyc_aml"]
    return build_tagged_sections_for_law(
        law_code="kyc_aml",
        pdf_url=cfg["pdf_url"],
        section_pattern=KYC_SECTION_PATTERN,
        sensitive_categories=cfg["sensitive_categories"],
        chapter_label=cfg["label"],
    )


def ingest_pmla() -> list[dict]:
    cfg = LAWS["pmla"]
    return build_tagged_sections_for_law(
        law_code="pmla",
        pdf_url=cfg["pdf_url"],
        section_pattern=PMLA_SECTION_PATTERN,
        sensitive_categories=cfg["sensitive_categories"],
        chapter_label=cfg["label"],
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    for name, fn in (("KYC", ingest_kyc), ("PMLA", ingest_pmla)):
        try:
            sections = fn()
            print(f"{name}: parsed {len(sections)} sections")
        except IngestError as exc:
            print(f"{name} ingest failed: {exc}")
