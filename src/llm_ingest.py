"""
llm_ingest.py
-------------
LLM-based section extraction, adapted from your tested
banking_llm_extraction_test_v3.ipynb notebook. Replaces the earlier
regex-based approach in ingest_common.py for KYC and Cyber (both still
irregularly-formatted PDFs where a fixed regex is fragile); PMLA uses a
faster, LLM-free header-split since fiuindia.gov.in already marks
"Section N" boundaries in clean HTML.

Two real fixes carried over from your notebook, preserved here exactly:
  1. chunk_text() hard-splits an oversized single "paragraph" by character
     count as a last resort - the KYC PDF's extracted text has no blank
     lines, so naive paragraph-splitting alone silently produced one giant
     46k-char chunk that the LLM would have choked on or truncated badly.
  2. PMLA source is fiuindia.gov.in (FIU-IND, the actual regulator), not
     indiacode.nic.in - the latter 504'd. Note this is a curated subset of
     the Act's key sections, not the exhaustive 1-75, per your notebook.

Changes made when integrating into the project (all deliberate, listed so
nothing here is a silent surprise):
  - GROQ_API_KEY / GROQ_MODEL now come from dpdp_config.py (the same
    values your existing /ask endpoint already uses via groq_client.py),
    not an interactive getpass() prompt - this runs on a server, not in a
    notebook.
  - Category vocabulary changed from the notebook's capitalized-singular
    ("Obligation", "Penalty") to this project's lowercase-plural
    ("obligations", "penalties") - matching dpdp_config.SENSITIVE_CATEGORIES
    and banking_config.LAWS[...]["sensitive_categories"] exactly. Using the
    notebook's original vocabulary here would have silently broken the
    sensitive-flagging check the same way the singular/plural mismatch did
    in ingest_common.py before that was fixed - see INTEGRATION_GUIDE.md.
  - Function names (ingest_kyc, ingest_pmla, ingest_cyber) match what
    main.py already imports from kyc_pmla_ingest.py / cyber_ingest.py -
    see those two files, now thin wrappers around this module. main.py
    itself needs ZERO changes for this integration.

IMPORTANT - NOT RUNTIME-TESTED END TO END: this sandbox has no network
access, so the actual fetch/LLM calls could not be executed here (same
limitation noted for the RBI 403 test earlier). What WAS tested: the pure
logic (chunk_text, strip_html_tags, split_pmla_sections, category-mapping,
and the output shape's compatibility with real dpdp_stores.commit_section())
- see test_llm_ingest.py. Run ingest_kyc()/ingest_pmla()/ingest_cyber()
yourself once, exactly as you did in the notebook, before wiring this into
a live human-review workflow.

OPERATIONAL WARNING - this is SLOW, do not call synchronously from a web
request without reading this: SLEEP_BETWEEN_CALLS_SECONDS = 65 (Groq free
tier rate limiting) means a ~47k-char document chunked at 9000 chars/chunk
(~6 chunks) takes roughly 5-6 minutes end to end. Render's default request
timeout is well under that. See INTEGRATION_GUIDE.md for the recommended
pattern (run offline, cache the JSON, load it at deploy time) rather than
calling this live from POST /ingest/{law_code}.
"""

from __future__ import annotations

import io
import json
import logging
import re
import time

import requests

from . import dpdp_config as cfg
from .banking_config import LAWS

logger = logging.getLogger("llm_ingest")

USER_AGENT = "banking-regulatory-intelligence-ingest/1.0 (+internal compliance tool)"

# Lowercase-plural, matching dpdp_config.SENSITIVE_CATEGORIES and
# banking_config.py's per-law sensitive_categories tuples - see this
# module's docstring for why this must stay consistent with those.
SENSITIVE_CATEGORIES = {"obligations", "penalties"}
CONFIDENCE_THRESHOLD = 0.85
SLEEP_BETWEEN_CALLS_SECONDS = 65

# Maps the LLM's output category (capitalized singular, per the extraction
# prompt below - deliberately NOT changed from your tested prompt, since
# prompt wording changes are exactly the kind of thing that should be
# re-tested before trusting new output) to this project's internal
# lowercase-plural vocabulary.
_CATEGORY_TO_PROJECT_VOCAB = {
    "Obligation": "obligations",
    "Right": "rights",
    "Penalty": "penalties",
    "Definition": "definitions",
}


class IngestError(Exception):
    """Raised when a source document can't be fetched, parsed, or when the
    LLM returns zero sections across every chunk."""


# ---------------------------------------------------------------------------
# Fetch helpers - unchanged from your notebook (already handle retries and
# validate content-type, same defensive pattern as ingest_common.py).
# ---------------------------------------------------------------------------
def fetch_pdf_bytes(url: str, timeout: int = 90, retries: int = 2) -> bytes:
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "pdf" not in content_type.lower() and not resp.content.startswith(b"%PDF"):
                raise IngestError(f"URL {url} did not return a PDF (Content-Type: {content_type}).")
            return resp.content
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("Fetch attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(5)
    raise IngestError(f"Failed to fetch source document from {url} after {retries} attempts: {last_exc}")


def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to extract text from page %d: %s", i, exc)
    return "\n".join(pages)


def fetch_html_text(url: str, timeout: int = 60, retries: int = 2) -> str:
    """For PMLA: fiuindia.gov.in serves clean HTML, not a PDF."""
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as exc:
            last_exc = exc
            logger.warning("HTML fetch attempt %d/%d failed for %s: %s", attempt, retries, url, exc)
            if attempt < retries:
                time.sleep(5)
    raise IngestError(f"Failed to fetch {url} after {retries} attempts: {last_exc}")


# ---------------------------------------------------------------------------
# Chunking - identical to your fixed v3 notebook version, including the
# embedded self-test that runs at import time so a chunking regression is
# caught immediately rather than mid-ingest.
# ---------------------------------------------------------------------------
def chunk_text(text: str, target_chars: int = 9000) -> list[str]:
    paragraphs = re.split(r"\n\s*\n", text)
    if len(paragraphs) <= 1 and len(text) > target_chars:
        paragraphs = text.split("\n")

    chunks, current = [], []
    current_len = 0
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(para) > target_chars:
            for i in range(0, len(para), target_chars):
                piece = para[i:i + target_chars]
                if current:
                    chunks.append("\n\n".join(current))
                    current, current_len = [], 0
                chunks.append(piece)
            continue
        if current_len + len(para) > target_chars and current:
            chunks.append("\n\n".join(current))
            current, current_len = [], 0
        current.append(para)
        current_len += len(para)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _chunk_text_self_test() -> None:
    test = chunk_text("x" * 50000, target_chars=9000)
    assert all(len(c) <= 9000 for c in test), "chunk_text produced an oversized chunk!"
    assert sum(len(c) for c in test) == 50000, "chunk_text lost characters!"


_chunk_text_self_test()


# ---------------------------------------------------------------------------
# LLM extraction agent - system prompt UNCHANGED from your tested notebook
# version (deliberately - prompt wording is exactly the part you already
# validated produces good extractions; changing wording here without
# re-testing would undermine that).
# ---------------------------------------------------------------------------
EXTRACTION_SYSTEM_PROMPT = """You are a precise regulatory-document parsing agent.

You will be given ONE CHUNK of a larger regulatory document (not necessarily the whole
document). Identify every distinct numbered section, clause, sub-clause, definition, and
list item visible IN THIS CHUNK — including items nested inside a "Definitions" clause and
items inside an Annexure — and output them as a JSON array. Do not skip nested items just
because they sit inside a larger clause: each nested item is its own array entry, separate
from its parent. If a section is visibly cut off at the start or end of this chunk (because
it continues in a neighboring chunk), still extract the visible portion — do not discard it.

For each item, extract these exact fields:
- "section_number": the identifier exactly as it appears in the source (e.g. "12", "5A",
  "(ii)", "Annexure-I-iii"). Preserve letter suffixes and roman numerals exactly.
- "title": a short descriptive title, under 12 words, in your own words.
- "full_text": the COMPLETE text of that section/clause/item, copied VERBATIM from the
  source. Do not paraphrase, summarize, or shorten it.
- "category": exactly one of "Obligation", "Right", "Penalty", "Definition" — pick the single
  best fit. Use "Definition" as the default for descriptive, procedural, or definitional text
  that isn't clearly an obligation, right, or penalty.
- "confidence": your own confidence in this category assignment, a number from 0.0 to 1.0.
- "rationale": one short sentence explaining the category choice.

Rules:
- Never merge two distinct sections/items into one entry.
- Never skip a section/item because it seems minor.
- Preserve section numbering exactly as written — do not renumber or normalize it.
- Output ONLY a single JSON object of the shape {"sections": [...]}. No prose, no markdown
  code fences, no explanation before or after the JSON. If this chunk has no extractable
  sections, output {"sections": []}.
"""


def _call_groq_json(chapter_label: str, chunk: str, retries: int = 3) -> dict:
    if not cfg.GROQ_API_KEY:
        raise IngestError(
            "GROQ_API_KEY is not set (checked dpdp_config.GROQ_API_KEY - same "
            "env var your /ask endpoint already uses). Set it in Render's "
            "Environment tab before running LLM-based ingestion."
        )
    for attempt in range(1, retries + 1):
        resp = requests.post(
            cfg.GROQ_API_URL,
            headers={"Authorization": f"Bearer {cfg.GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": cfg.GROQ_MODEL,
                "max_tokens": 3000,
                "reasoning_effort": "low",
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": f"Document: {chapter_label}\n\n{chunk}"},
                ],
            },
            timeout=120,
        )
        if resp.status_code in (429, 413):
            wait = 20 * attempt
            logger.warning("Rate limited (attempt %d/%d) - sleeping %ds: %s",
                            attempt, retries, wait, resp.text[:200])
            time.sleep(wait)
            continue
        if resp.status_code != 200:
            raise IngestError(f"Groq API error {resp.status_code}: {resp.text[:500]}")

        raw = resp.json()["choices"][0]["message"]["content"]
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            cleaned = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
            return json.loads(cleaned)
    raise IngestError(f"Groq API still rate-limited after {retries} attempts.")


def _build_entities(project_category: str | None, title: str) -> dict:
    entities = {"obligations": [], "rights": [], "penalties": [], "definitions": []}
    if project_category in entities:
        entities[project_category].append(title)
    return entities


def llm_extract_sections(law_code: str, full_text: str, chapter_label: str, source_url: str) -> list[dict]:
    """Chunk the document, call the LLM per chunk, and return sections in
    the project's standard shape (same as ingest_common.py's output -
    verified compatible with dpdp_stores.KnowledgeStore.commit_section()
    and main.py's needs_review check)."""
    chunks = chunk_text(full_text, target_chars=9000)
    logger.info("law=%s split into %d chunk(s)", law_code, len(chunks))

    all_items = []
    for i, chunk in enumerate(chunks):
        logger.info("law=%s chunk %d/%d (%d chars)", law_code, i + 1, len(chunks), len(chunk))
        parsed = _call_groq_json(chapter_label, chunk)
        items = parsed.get("sections", [])
        logger.info("law=%s chunk %d/%d -> %d sections", law_code, i + 1, len(chunks), len(items))
        all_items.extend(items)
        if i < len(chunks) - 1:
            time.sleep(SLEEP_BETWEEN_CALLS_SECONDS)

    if not all_items:
        raise IngestError(f"LLM returned zero sections across all chunks for law={law_code}.")

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sections = []
    for item in all_items:
        llm_category = item.get("category", "Definition")
        project_category = _CATEGORY_TO_PROJECT_VOCAB.get(llm_category, "definitions")
        confidence = float(item.get("confidence", 0.0))
        sensitive = project_category in SENSITIVE_CATEGORIES or confidence < CONFIDENCE_THRESHOLD
        title = item.get("title", "")[:200]
        section_number = item.get("section_number", "?")
        sections.append({
            "id": f"{law_code}:{section_number}",
            "title": title,
            "chapter": chapter_label,
            "raw_text": item.get("full_text", ""),
            "source_url": source_url,
            "entities": _build_entities(project_category, title),
            "sensitive": sensitive,
            "confidence": confidence,
            "law_code": law_code,
            "category": project_category,
            "rationale": item.get("rationale", ""),
            "fetched_at": fetched_at,
        })
    logger.info("LLM extracted %d total sections for law=%s", len(sections), law_code)
    return sections


# ---------------------------------------------------------------------------
# PMLA-specific: header-split on fiuindia.gov.in's clean HTML. No LLM call
# needed for section boundaries - the "Section N" headers already do that
# reliably, per your notebook's finding.
# ---------------------------------------------------------------------------
def strip_html_tags(html: str) -> str:
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


PMLA_SECTION_HEADER_PATTERN = re.compile(r"Section\s+(\d{1,3}[A-Z]{0,2})\s*[-.]", re.IGNORECASE)


def split_pmla_sections(full_text: str) -> list[tuple[str, str, str]]:
    matches = list(PMLA_SECTION_HEADER_PATTERN.finditer(full_text))
    parts = []
    for idx, match in enumerate(matches):
        start = match.start()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(full_text)
        body = full_text[start:end].strip()
        if not body:
            continue
        section_number = match.group(1)
        title = body.split("\n", 1)[0].strip()[:200]
        parts.append((section_number, title, body))
    return parts


def _classify_pmla_section(body: str) -> str:
    """Simple keyword rule, same as your notebook - PMLA's clean HTML
    doesn't need the LLM for boundaries, and this per-section classification
    was already fast/accurate enough without one either."""
    body_lower = body.lower()
    if "penalty" in body_lower or "fine" in body_lower or "punish" in body_lower:
        return "penalties"
    if "shall" in body_lower or "must" in body_lower:
        return "obligations"
    return "definitions"


# ---------------------------------------------------------------------------
# Public entry points - SAME NAMES as kyc_pmla_ingest.py / cyber_ingest.py
# already export, so those two files become thin wrappers (see their
# updated contents) and main.py needs no changes at all.
# ---------------------------------------------------------------------------
def ingest_kyc() -> list[dict]:
    law_cfg = LAWS["kyc_aml"]
    pdf_bytes = fetch_pdf_bytes(law_cfg["pdf_url"])
    full_text = extract_text_from_pdf(pdf_bytes)
    logger.info("kyc_aml: fetched + extracted %d chars", len(full_text))
    return llm_extract_sections("kyc_aml", full_text, law_cfg["label"], law_cfg["pdf_url"])


def ingest_pmla() -> list[dict]:
    law_cfg = LAWS["pmla"]
    html = fetch_html_text(law_cfg["html_url"])
    full_text = strip_html_tags(html)
    logger.info("pmla: fetched + stripped %d chars", len(full_text))
    parts = split_pmla_sections(full_text)
    if not parts:
        raise IngestError(
            "split_pmla_sections() found zero 'Section N' headers - fiuindia.gov.in's "
            "page structure may have changed. Refusing to proceed with an empty parse."
        )

    fetched_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    sections = []
    for section_number, title, body in parts:
        category = _classify_pmla_section(body)
        sensitive = category in SENSITIVE_CATEGORIES
        sections.append({
            "id": f"pmla:{section_number}",
            "title": title,
            "chapter": law_cfg["label"],
            "raw_text": body,
            "source_url": law_cfg["html_url"],
            "entities": _build_entities(category, title),
            "sensitive": sensitive,
            "confidence": 0.9,
            "law_code": "pmla",
            "category": category,
            "fetched_at": fetched_at,
        })
    logger.info("pmla: %d sections via header split", len(sections))
    return sections


def ingest_cyber() -> list[dict]:
    law_cfg = LAWS["rbi_cyber"]
    pdf_bytes = fetch_pdf_bytes(law_cfg["pdf_url"])
    full_text = extract_text_from_pdf(pdf_bytes)
    logger.info("rbi_cyber: fetched + extracted %d chars", len(full_text))
    return llm_extract_sections("rbi_cyber", full_text, law_cfg["label"], law_cfg["pdf_url"])
