"""
Central configuration for the DPDP Act GraphRAG service - Render/Groq/AuraDB
build. Same Act-parsing data as the Colab/Codespaces version; the retrieval
and LLM backends are swapped out (TF-IDF instead of embeddings+Qdrant, Groq's
hosted API instead of local Ollama) to fit a 512MB free-tier RAM budget.
"""

import os

# ---------------------------------------------------------------------------
# Source data
# ---------------------------------------------------------------------------
DPDP_PDF_URL = (
    "https://www.meity.gov.in/static/uploads/2024/06/"
    "2bf1f0e9f04e6fb4f8fef35e82c42aa5.pdf"
)
LOCAL_PDF_PATH = "dpdp_act_2023.pdf"
MAX_SECTIONS = 44

# ---------------------------------------------------------------------------
# Neo4j AuraDB Free (remote - all env vars, set these in Render's dashboard,
# never commit real values). AuraDB gives you the URI/user/password once,
# at instance creation - save them immediately, they are not shown again.
# ---------------------------------------------------------------------------
NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")

# ---------------------------------------------------------------------------
# Groq (hosted, OpenAI-compatible, free tier - no card). Llama 3.3 70B is
# used by default for answer quality; swap to llama-3.1-8b-instant in the
# Render env vars if you want faster/cheaper responses and 70B's quality
# isn't needed for a legal-reference assistant.
# ---------------------------------------------------------------------------
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MAX_TOKENS = 300
GROQ_TIMEOUT_SECONDS = 30

# ---------------------------------------------------------------------------
# CORS - the frontend lives on a different domain (Vercel) than the backend
# (Render), so this must be set explicitly. "*" is fine for a public demo
# with no accounts/sensitive data; tighten to your exact Vercel URL once
# you know it (see DEPLOYMENT.md).
# ---------------------------------------------------------------------------
ALLOWED_ORIGIN = os.environ.get("ALLOWED_ORIGIN", "*")

# ---------------------------------------------------------------------------
# Human-in-the-loop thresholds
# ---------------------------------------------------------------------------
CONFIDENCE_THRESHOLD = 0.85
SENSITIVE_CATEGORIES = ("obligations", "penalties")
HIGH_RISK_QUERY_KEYWORDS = (
    "penalty", "fine", "punish", "breach", "obligation", "must",
    "cross-border", "cross border", "transfer outside",
)

# ---------------------------------------------------------------------------
# Retrieval / groundedness (TF-IDF cosine similarity, not embeddings)
# ---------------------------------------------------------------------------
RETRIEVAL_TOP_K = 5
CONTEXT_CHAR_LIMIT = 800  # per-section chars included in the LLM prompt

# TF-IDF cosine similarity scores run on a DIFFERENT scale than the
# sentence-transformer embeddings used in the Colab/Codespaces version -
# thresholds tuned for embeddings (e.g. 0.30) would over-refuse here.
# TF-IDF is purely lexical (keyword overlap), so it's naturally stricter
# about wording than a semantic embedding would be: a question that shares
# almost no vocabulary with the relevant section (e.g. a heavily paraphrased
# question) may score lower than it would have with embeddings, even when
# genuinely in scope. That's the real trade-off of trading RAM for a
# lighter-weight retrieval method - test with your own realistic phrasings
# and adjust these two numbers if you see too many false "not found" results.
SIMILARITY_THRESHOLD = 0.12
HARD_CUTOFF = 0.04

RATE_LIMIT_REQUESTS_PER_MINUTE = 20
PDF_FETCH_RETRIES = 3
PDF_FETCH_BACKOFF_SECONDS = 3

CHAPTER_MAP = [
    ("I", "PRELIMINARY", 1, 3),
    ("II", "OBLIGATIONS OF DATA FIDUCIARY", 4, 10),
    ("III", "RIGHTS AND DUTIES OF DATA PRINCIPAL", 11, 15),
    ("IV", "SPECIAL PROVISIONS", 16, 17),
    ("V", "DATA PROTECTION BOARD OF INDIA", 18, 26),
    ("VI", "POWERS, FUNCTIONS AND PROCEDURE TO BE FOLLOWED BY BOARD", 27, 28),
    ("VII", "APPEAL AND ALTERNATE DISPUTE RESOLUTION", 29, 32),
    ("VIII", "PENALTIES AND ADJUDICATION", 33, 34),
    ("IX", "MISCELLANEOUS", 35, 44),
]

SECTION_TITLES = {
    1: "Short title and commencement", 2: "Definitions", 3: "Application of Act",
    4: "Grounds for processing personal data", 5: "Notice", 6: "Consent",
    7: "Certain legitimate uses", 8: "General obligations of Data Fiduciary",
    9: "Processing of personal data of children",
    10: "Additional obligations of Significant Data Fiduciary",
    11: "Right to access information about personal data",
    12: "Right to correction and erasure of personal data",
    13: "Right of grievance redressal", 14: "Right to nominate",
    15: "Duties of Data Principal", 16: "Processing of personal data outside India",
    17: "Exemptions", 18: "Establishment of Board",
    19: "Composition and qualifications for appointment of Chairperson and Members",
    20: "Salary, allowances payable to and term of office",
    21: "Disqualifications for appointment and continuation as Chairperson and Members",
    22: "Resignation by Members and filling of vacancy", 23: "Proceedings of Board",
    24: "Officers and employees of Board", 25: "Members and officers to be public servants",
    26: "Powers of Chairperson", 27: "Powers and functions of Board",
    28: "Procedure to be followed by Board", 29: "Appeal to Appellate Tribunal",
    30: "Orders passed by Appellate Tribunal to be executable as decree",
    31: "Alternate dispute resolution", 32: "Voluntary undertaking", 33: "Penalties",
    34: "Crediting sums realised by way of penalties to Consolidated Fund of India",
    35: "Protection of action taken in good faith", 36: "Power to call for information",
    37: "Power of Central Government to issue directions", 38: "Consistency with other laws",
    39: "Bar of jurisdiction", 40: "Power to make rules",
    41: "Laying of rules and certain notifications", 42: "Power to amend Schedule",
    43: "Power to remove difficulties", 44: "Amendments to certain Acts",
}


def chapter_for_section(section_number: int) -> str:
    for roman, title, start, end in CHAPTER_MAP:
        if start <= section_number <= end:
            return f"Chapter {roman} - {title}"
    return "Unknown"
