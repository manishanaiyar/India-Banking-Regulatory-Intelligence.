"""
cyber_ingest.py
----------------
Cybersecurity pillar: RBI Cyber Security Framework + CERT-In incident
reporting directions.

Now delegates to llm_ingest.py's LLM-based extraction (same reasoning as
kyc_pmla_ingest.py - see that file's docstring). Kept as a separate module
so ingest_cyber() stays at the same import path main.py already uses:
    from src.cyber_ingest import ingest_cyber as _ingest_fn
No changes needed in main.py for this swap.
"""

from __future__ import annotations

from .llm_ingest import IngestError, ingest_cyber

__all__ = ["IngestError", "ingest_cyber"]
