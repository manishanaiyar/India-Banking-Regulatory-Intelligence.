"""
kyc_pmla_ingest.py
-------------------
RBI/KYC pillar: RBI Master Direction on KYC and PMLA, 2002.

Now delegates to llm_ingest.py (LLM-based extraction for KYC, header-split
for PMLA), replacing the earlier regex-based approach - the RBI KYC PDF's
layout is irregular enough that a fixed regex needed constant babysitting,
and your tested notebook's LLM extraction handled it more reliably.

Kept as a separate module (rather than deleting it and pointing main.py at
llm_ingest.py directly) so ingest_kyc() / ingest_pmla() stay at the same
import path main.py already uses:
    from src.kyc_pmla_ingest import ingest_kyc as _ingest_fn
    from src.kyc_pmla_ingest import ingest_pmla as _ingest_fn
No changes needed in main.py for this swap.

The old regex-based CATEGORY_KEYWORDS/build_tagged_sections_for_law
approach still lives in ingest_common.py if you ever want to fall back to
it (e.g. if Groq's free tier becomes unavailable) - nothing here deletes
it, this file just no longer calls it.
"""

from __future__ import annotations

from .llm_ingest import IngestError, ingest_kyc, ingest_pmla

__all__ = ["IngestError", "ingest_kyc", "ingest_pmla"]
