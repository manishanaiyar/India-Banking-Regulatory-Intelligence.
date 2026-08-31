"""
gdpr_ingest.py
--------------
GDPR pillar (added alongside Privacy/RBI-KYC/Cybersecurity): full text of
the EU General Data Protection Regulation (Regulation (EU) 2016/679), 99
Articles across 11 Chapters - the complete regulation, not a curated
subset, per the user's request.

Deliberately modeled on dpdp_ingest.py rather than the LLM-based
llm_ingest.py path: GDPR's official EUR-Lex PDF has clean, regularly
formatted "Article N" headers (same shape as the DPDP Act's "N." section
headers), so a fast, deterministic regex split is both possible and more
reliable than an LLM call - no Groq rate-limit wait, no verbatim-overlap
risk, ingests in seconds instead of minutes.

IMPORTANT regex-safety note: GDPR's 173 recitals contain many INLINE
mentions of article numbers ("Article 16 thereof", "Articles 12 to 15",
"Article 8(1) of the Charter", etc.) that appear BEFORE the actual
numbered Article headers in the document. A naive `re.finditer()` over
the whole document would match these inline mentions as false section
boundaries. This is exactly why split_into_sections() below uses
SEQUENTIAL-ANCHOR matching (search for Article N only starting from where
Article N-1 was found, exactly like dpdp_ingest.split_into_sections()) -
by the time we search for Article N, the cursor has already moved past
every recital and every earlier article's body text, so earlier inline
references are structurally unreachable. Combined with anchoring each
match to the start of a line (inline references are never the first
token on a line in the extracted text), this reproduces dpdp_ingest.py's
proven approach rather than inventing a new one.
"""

import logging
import os
import re
import time

import requests

from src.gdpr_data import ARTICLE_TITLES, MAX_ARTICLE, chapter_for_article
from src.banking_config import LAWS
from src.policy_engine import evaluate as evaluate_policy

logger = logging.getLogger("gdpr.ingest")

LOCAL_PDF_PATH = "gdpr_regulation_2016_679.pdf"

_ARTICLE_START_RE_CACHE: dict[int, "re.Pattern[str]"] = {}


def _article_start_pattern(number: int) -> "re.Pattern[str]":
    """Anchored to the start of a line - see module docstring on why this
    matters for avoiding false matches on inline recital references."""
    if number not in _ARTICLE_START_RE_CACHE:
        _ARTICLE_START_RE_CACHE[number] = re.compile(rf"(?:^|\n)Article\s+{number}\b")
    return _ARTICLE_START_RE_CACHE[number]


def fetch_gdpr_pdf(
    dest_path: str = LOCAL_PDF_PATH,
    url: str | None = None,
    max_retries: int = 3,
    backoff_seconds: float = 3.0,
) -> str:
    """Same fetch/cache/validate shape as dpdp_ingest.fetch_act_pdf() -
    cache on disk, retry with backoff, and reject a 200-OK HTML error page
    masquerading as the PDF."""
    url = url or LAWS["gdpr"]["pdf_url"]
    if os.path.exists(dest_path):
        with open(dest_path, "rb") as f:
            if f.read(4) == b"%PDF":
                logger.info("Using cached, validated GDPR PDF at %s", dest_path)
                return dest_path
        logger.warning("Cached file at %s is not a valid PDF - re-fetching", dest_path)
        os.remove(dest_path)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching GDPR PDF from %s (attempt %d/%d)", url, attempt, max_retries)
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError(
                    "Response did not look like a PDF (got a webpage instead - eur-lex.europa.eu "
                    "may be temporarily down, or the CELEX URL may need updating)."
                )
            with open(dest_path, "wb") as f:
                f.write(response.content)
            logger.info("Saved %d bytes to %s", len(response.content), dest_path)
            return dest_path
        except Exception as exc:
            last_error = exc
            logger.warning("GDPR PDF fetch attempt %d failed: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)

    raise RuntimeError(f"Could not fetch the GDPR PDF from {url} after {max_retries} attempts: {last_error}")


def extract_text(pdf_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def split_into_articles(text: str, max_article: int = MAX_ARTICLE) -> list[dict]:
    """Sequential-anchor split - see module docstring. Only searches for
    Article N after the position where Article N-1 was found, which is
    what makes this safe against the many inline "Article N" references
    inside the 173 recitals that precede the actual numbered articles."""
    positions: dict[int, int] = {}
    cursor = 0
    for n in range(1, max_article + 1):
        match = _article_start_pattern(n).search(text, cursor)
        if not match:
            continue
        positions[n] = match.start()
        cursor = match.start() + 1

    found_numbers = sorted(positions.keys())
    parsed = []
    for i, n in enumerate(found_numbers):
        start = positions[n]
        end = positions[found_numbers[i + 1]] if i + 1 < len(found_numbers) else start + 6000
        body = text[start:end].strip()
        parsed.append({
            "id": f"gdpr:{n}",
            "number": n,
            "chapter": chapter_for_article(n),
            "title": ARTICLE_TITLES.get(n, f"Article {n}"),
            "raw_text": body,
            "source_url": LAWS["gdpr"]["pdf_url"],
            "law_code": "gdpr",
        })
    return parsed


def tag_entities(article: dict) -> dict:
    """Keyword/chapter-based tagging, same shape as dpdp_ingest.tag_entities()
    so dpdp_stores.commit_section() (which expects exactly these four keys)
    works unmodified for GDPR sections too."""
    title_lower = article["title"].lower()
    chapter_upper = article["chapter"].upper()
    entities = {"obligations": [], "rights": [], "penalties": [], "definitions": []}

    if "right" in title_lower or "RIGHTS OF THE DATA SUBJECT" in chapter_upper:
        entities["rights"].append(article["title"])
    if "CONTROLLER AND PROCESSOR" in chapter_upper or "obligation" in title_lower:
        entities["obligations"].append(article["title"])
    if "penalt" in title_lower or "REMEDIES, LIABILITY AND PENALTIES" in chapter_upper:
        entities["penalties"].append(article["title"])
    if article["number"] == 4:
        # Article 4 ("Definitions") is GDPR's single definitions article,
        # same role as DPDP Act's Section 2.
        entities["definitions"].append(article["title"])

    return entities


def estimate_confidence(article: dict) -> float:
    """Same length-based heuristic as dpdp_ingest.estimate_confidence() -
    a very short extracted body is more likely to be a mis-split boundary
    than a genuinely short article."""
    length = len(article["raw_text"])
    if length < 40:
        return 0.4
    score = 0.75 + min(length, 1200) / 1200 * 0.2
    return round(min(score, 0.97), 2)


# GDPR's own sensitive categories, mirroring dpdp_config.SENSITIVE_CATEGORIES
# and banking_config.py's per-law tuples - kept in this module (not
# gdpr_data.py) since it's ingestion-time policy, not static article data.
SENSITIVE_CATEGORIES = ("obligations", "penalties")


def is_sensitive(entities: dict) -> bool:
    return any(len(entities.get(cat, [])) > 0 for cat in SENSITIVE_CATEGORIES)


def build_tagged_articles(pdf_path: str, max_article: int = MAX_ARTICLE) -> list[dict]:
    """End-to-end: parse -> tag -> score -> classify. Same shape as
    dpdp_ingest.build_tagged_sections() - every article also carries
    data_classes / required_controls / policy_rationale from the Policy
    Engine, and a "sensitive_data" match force-flags it for human review
    regardless of the keyword-based is_sensitive() check."""
    raw_text = extract_text(pdf_path)
    articles = split_into_articles(raw_text, max_article)
    for article in articles:
        article["entities"] = tag_entities(article)
        article["confidence"] = estimate_confidence(article)
        article["sensitive"] = is_sensitive(article["entities"])

        policy_result = evaluate_policy(article["raw_text"])
        article["data_classes"] = policy_result.data_classes
        article["required_controls"] = policy_result.required_controls
        article["policy_rationale"] = policy_result.rationale
        if "sensitive_data" in policy_result.data_classes:
            article["sensitive"] = True

    if len(articles) < max_article:
        missing = sorted(set(range(1, max_article + 1)) - {a["number"] for a in articles})
        logger.warning(
            "Only parsed %d/%d GDPR articles - missing article numbers: %s. "
            "The EUR-Lex PDF layout may have changed; check split_into_articles().",
            len(articles), max_article, missing,
        )
    else:
        logger.info("Parsed, tagged, and classified %d/%d GDPR articles", len(articles), max_article)
    return articles


def ingest_gdpr() -> list[dict]:
    """Public entry point - same import shape main.py already uses for
    the other laws: `from src.gdpr_ingest import ingest_gdpr as _ingest_fn`."""
    pdf_path = fetch_gdpr_pdf()
    return build_tagged_articles(pdf_path, MAX_ARTICLE)
