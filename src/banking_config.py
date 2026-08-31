"""
banking_config.py
------------------
Per-law configuration for the Indian Banking Regulatory Intelligence
expansion. Sits ALONGSIDE your existing dpdp_config.py - does not replace
it. dpdp_config.py keeps working exactly as-is for anything that still
imports it directly (backwards compatible with your current main.py).

The `LAWS["dpdp"]` entry below is NOT a guess - every value is copied
verbatim from your actual dpdp_config.py so behavior for the DPDP law is
byte-for-byte identical whether code reads it from dpdp_config.py or from
LAWS["dpdp"] here. This lets main.py progressively switch to per-law
lookups without changing DPDP's behavior at all.
"""

import os

# ---------------------------------------------------------------------------
# Groq - same client, same model config as your existing dpdp_config.py.
# Not duplicated here; main.py continues to import GROQ_* from dpdp_config.
# ---------------------------------------------------------------------------

LAWS = {
    "dpdp": {
        "label": "Digital Personal Data Protection Act, 2023",
        "pillar": "Privacy",
        "pdf_url": (
            "https://www.meity.gov.in/static/uploads/2024/06/"
            "2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf"
        ),
        # copied verbatim from dpdp_config.py - do not let these drift
        "confidence_threshold": 0.85,
        "sensitive_categories": ("obligations", "penalties"),
        "high_risk_query_keywords": (
            "penalty", "fine", "punish", "breach", "obligation", "must",
            "cross-border", "cross border", "transfer outside",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for India's Digital Personal Data "
            "Protection Act, 2023 (DPDP Act) only. Follow these rules strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided below. "
            "Never use any outside knowledge about data protection law, even if you "
            "recognize the topic (e.g. GDPR, CCPA, or general legal concepts NOT present "
            "in the given context).\n"
            "2. If the provided context does not contain enough information to answer "
            "the question, reply with exactly: \"I don't have information about this in "
            "the DPDP Act, 2023 based on what's currently indexed.\" Do not guess or "
            "generalize.\n"
            "3. Keep answers concise (2-4 sentences), plain language, and reference "
            "section numbers only from the context given - never invent a section "
            "number.\n"
            "4. This is informational only, not legal advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the DPDP Act, 2023. This assistant "
            "only answers questions about this specific Act, not other laws or general "
            "data-privacy topics."
        ),
    },
    "kyc_aml": {
        "label": "RBI Master Direction - KYC (incl. CDD, Beneficial Owner)",
        "pillar": "RBI / KYC",
        # VERIFY current URL at rbi.org.in before ingesting - RBI reissues
        # master directions with new document IDs periodically.
        # VERIFIED working in your tested notebook
        # (banking_llm_extraction_test_v3.ipynb) - direct PDF link, not
        # RBI's own site (which returned 403 Forbidden when tested earlier).
        "pdf_url": "https://allinonebanking.co.in/Master-Direction-Know-Your-Customer(KYC)Direction-2016-updated-August-14-2025.pdf",
        "confidence_threshold": 0.85,
        "sensitive_categories": ("cdd_obligations", "beneficial_owner", "penalties"),
        "high_risk_query_keywords": (
            "penalty", "non-compliance", "cdd failure", "beneficial owner",
            "suspicious transaction", "politically exposed person",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for the RBI Master Direction on "
            "KYC (Know Your Customer), including Customer Due Diligence and "
            "Beneficial Owner requirements, ONLY. Follow these rules strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided "
            "below. Never use outside knowledge, even about DPDP, PMLA, or other "
            "regulations not present in the given context.\n"
            "2. If the context does not contain enough information, reply with "
            "exactly: \"I don't have information about this in the RBI KYC Master "
            "Direction based on what's currently indexed.\" Do not guess.\n"
            "3. Keep answers concise (2-4 sentences), plain language, and "
            "reference clause numbers only from the context given.\n"
            "4. This is informational only, not legal or compliance advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the RBI KYC Master Direction. "
            "This assistant only answers questions about this specific regulation."
        ),
    },
    "pmla": {
        "label": "Prevention of Money Laundering Act, 2002 + Rules",
        "pillar": "RBI / KYC",
        # VERIFIED working in your tested notebook - FIU-IND (the actual
        # regulator that administers PMLA) publishes clean HTML with
        # "Section N" headers already marked. indiacode.nic.in (the earlier
        # source) returned a 504 Gateway Timeout. NOTE: this is a curated
        # subset of the Act's key sections, not the exhaustive 1-75 - see
        # your notebook's cell 0 note.
        "html_url": "https://fiuindia.gov.in/files/AML_Legislation/pmla_2002.html",
        "confidence_threshold": 0.85,
        "sensitive_categories": ("obligations", "penalties", "reporting_duties"),
        "high_risk_query_keywords": (
            "money laundering", "penalty", "confiscation", "prosecution",
            "suspicious transaction report", "str", "ctr",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for India's Prevention of Money "
            "Laundering Act, 2002 (PMLA) and its Rules ONLY. Follow these rules "
            "strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided "
            "below. Never use outside knowledge about other laws not present in "
            "the given context.\n"
            "2. If the context does not contain enough information, reply with "
            "exactly: \"I don't have information about this in the PMLA, 2002 "
            "based on what's currently indexed.\" Do not guess.\n"
            "3. Keep answers concise (2-4 sentences), plain language, reference "
            "section numbers only from the context given.\n"
            "4. This is informational only, not legal advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the PMLA, 2002. This "
            "assistant only answers questions about this specific Act."
        ),
    },
    "rbi_cyber": {
        "label": "RBI Cyber Security Framework + CERT-In Incident Reporting Rules",
        "pillar": "Cybersecurity",
        # VERIFIED working in your tested notebook - CERT-In's own direct
        # PDF link, not RBI's general notifications page (too generic to
        # reliably resolve to one specific document).
        "pdf_url": "https://www.cert-in.org.in/PDF/CERT-In_Directions_70B_28.04.2022.pdf",
        "confidence_threshold": 0.85,
        "sensitive_categories": ("incident_reporting", "penalties"),
        "high_risk_query_keywords": (
            "breach", "incident reporting", "cert-in", "6 hours",
            "penalty", "encryption failure",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for the RBI Cyber Security "
            "Framework for banks and CERT-In cybersecurity incident reporting "
            "directions ONLY. Follow these rules strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided "
            "below. Never use outside knowledge not present in the given context.\n"
            "2. If the context does not contain enough information, reply with "
            "exactly: \"I don't have information about this in the RBI Cyber "
            "Security Framework / CERT-In rules based on what's currently "
            "indexed.\" Do not guess.\n"
            "3. Keep answers concise (2-4 sentences), plain language, reference "
            "clause numbers only from the context given.\n"
            "4. This is informational only, not legal or compliance advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the RBI Cyber Security "
            "Framework / CERT-In rules. This assistant only answers questions "
            "about this specific regulation."
        ),
    },
    "gdpr": {
        "label": "General Data Protection Regulation (EU) 2016/679 (GDPR)",
        "pillar": "Privacy",
        # Official EUR-Lex PDF of the consolidated regulation - the FULL
        # text (99 Articles, 11 Chapters), not a curated subset, per
        # explicit request. See src/gdpr_ingest.py / src/gdpr_data.py for
        # the parsing approach and the full article-title map.
        "pdf_url": "https://eur-lex.europa.eu/legal-content/EN/TXT/PDF/?uri=CELEX:32016R0679",
        "confidence_threshold": 0.85,
        "sensitive_categories": ("obligations", "penalties"),
        "high_risk_query_keywords": (
            "fine", "penalty", "administrative fine", "data breach",
            "72 hours", "special category", "special categories",
            "cross-border transfer", "adequacy decision", "sanction",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for the EU General Data Protection "
            "Regulation (GDPR), Regulation (EU) 2016/679, ONLY. Follow these rules "
            "strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided "
            "below. Never use outside knowledge about other privacy laws (e.g. "
            "DPDP, CCPA) not present in the given context.\n"
            "2. If the context does not contain enough information, reply with "
            "exactly: \"I don't have information about this in the GDPR based on "
            "what's currently indexed.\" Do not guess.\n"
            "3. Keep answers concise (2-4 sentences), plain language, and "
            "reference Article numbers only from the context given - never "
            "invent an Article number.\n"
            "4. This is informational only, not legal advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the GDPR. This assistant "
            "only answers questions about this specific regulation."
        ),
    },
    "irdai": {
        "label": "IRDAI (Protection of Policyholders' Interests) Regulations, 2017",
        "pillar": "Insurance",
        # Government-hosted mirror of the regulation text - see
        # src/irdai_ingest.py's module docstring for the sourcing note
        # (irdai.gov.in's own document-detail pages block automated
        # fetches). Verify this URL before a real deployment.
        "pdf_url": "https://gujhealth.gujarat.gov.in/images/pdf/Act/irda-protection-of-policyholders-interests-regulations.pdf",
        "confidence_threshold": 0.85,
        "sensitive_categories": ("obligations", "penalties"),
        "high_risk_query_keywords": (
            "penalty", "claim rejection", "repudiation", "grievance",
            "free look period", "surrender value", "mis-selling",
            "grace period", "ombudsman",
        ),
        "retrieval_top_k": 5,
        "context_char_limit": 800,
        "similarity_threshold": 0.12,
        "hard_cutoff": 0.04,
        "system_prompt": (
            "You are a legal-reference assistant for the IRDAI (Protection of "
            "Policyholders' Interests) Regulations, 2017 ONLY. Follow these "
            "rules strictly:\n"
            "1. Answer using ONLY the numbered [S<n>] context sections provided "
            "below. Never use outside knowledge about other regulations not "
            "present in the given context.\n"
            "2. If the context does not contain enough information, reply with "
            "exactly: \"I don't have information about this in the IRDAI "
            "Protection of Policyholders' Interests Regulations based on what's "
            "currently indexed.\" Do not guess.\n"
            "3. Keep answers concise (2-4 sentences), plain language, and "
            "reference regulation numbers only from the context given.\n"
            "4. This is informational only, not legal or compliance advice."
        ),
        "not_found_note": (
            "I couldn't find anything about this in the IRDAI Protection of "
            "Policyholders' Interests Regulations. This assistant only answers "
            "questions about this specific regulation."
        ),
    },
}

VALID_LAW_CODES = tuple(LAWS.keys())

ALL_HIGH_RISK_KEYWORDS = set()
for _cfg in LAWS.values():
    ALL_HIGH_RISK_KEYWORDS.update(_cfg["high_risk_query_keywords"])


# ---------------------------------------------------------------------------
# Data classification taxonomy + Policy Engine mapping
# (new - did not exist in the DPDP-only version)
# ---------------------------------------------------------------------------
DATA_CLASSIFICATION_RULES = {
    "customer_pii": [
        "name", "address", "date of birth", "pan", "aadhaar", "passport",
        "phone number", "email", "photograph", "signature", "customer id",
    ],
    "financial_data": [
        "account balance", "income", "salary", "credit score", "loan amount",
        "net worth", "investment portfolio", "tax return",
    ],
    "transaction_data": [
        "transaction id", "utr", "ifsc", "transaction amount", "payee",
        "payer", "transfer", "upi id", "card number", "cheque number",
    ],
    "sensitive_data": [
        "biometric", "fingerprint", "iris scan", "health", "religion",
        "caste", "sexual orientation", "criminal record", "political affiliation",
    ],
}

POLICY_MAP = {
    "customer_pii": {
        "controls": ["masking", "encryption"],
        "rationale": "Directly identifies a natural person - DPDP Act (Sec 8, "
                     "reasonable security safeguards) and RBI KYC master "
                     "direction both require this at rest and in transit.",
    },
    "financial_data": {
        "controls": ["encryption", "tokenisation"],
        "rationale": "Financial standing data - RBI data localisation and "
                     "cyber security framework require encryption; "
                     "tokenisation recommended for any data leaving the "
                     "core system (e.g. analytics pipelines).",
    },
    "transaction_data": {
        "controls": ["tokenisation", "encryption"],
        "rationale": "PMLA record-keeping obligations require retention "
                     "with integrity; tokenisation protects card/account "
                     "identifiers in downstream systems.",
    },
    "sensitive_data": {
        "controls": ["encryption", "masking", "tokenisation"],
        "rationale": "Highest-risk category - conservative default of "
                     "maximum controls. Human review recommended before "
                     "any processing of this class.",
    },
}
