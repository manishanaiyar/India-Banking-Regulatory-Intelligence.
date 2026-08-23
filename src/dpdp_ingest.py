"""
Ingestion pipeline: fetch the official PDF -> split into numbered sections ->
tag each section with rule-based categories -> score a confidence heuristic.

Pure functions with no side effects on the stores, so they're easy to unit
test in isolation (see the sanity-check cell in the notebook) and easy to
swap out later for a real LLM extraction agent (see README "grows into").
"""

import logging
import os
import re
import time

import requests

from src.dpdp_config import (
    CHAPTER_MAP, DPDP_PDF_URL, LOCAL_PDF_PATH, SECTION_TITLES,
    SENSITIVE_CATEGORIES, chapter_for_section,
)

logger = logging.getLogger("dpdp.ingest")

_SECTION_START_RE_CACHE: dict[int, "re.Pattern[str]"] = {}


def _section_start_pattern(number: int) -> "re.Pattern[str]":
    if number not in _SECTION_START_RE_CACHE:
        _SECTION_START_RE_CACHE[number] = re.compile(rf"(?:^|\n){number}\.\s")
    return _SECTION_START_RE_CACHE[number]


def fetch_act_pdf(
    dest_path: str = LOCAL_PDF_PATH,
    url: str = DPDP_PDF_URL,
    max_retries: int = 3,
    backoff_seconds: float = 3.0,
) -> str:
    """Download the official Gazette PDF once and cache it on disk.

    Retries with backoff (government sites occasionally 5xx or time out),
    and validates the response actually starts with a PDF magic number -
    a common silent-failure mode is the server returning a 200 OK HTML
    error/maintenance page instead of the real file, which would otherwise
    crash confusingly deep inside pypdf instead of here with a clear cause."""
    if os.path.exists(dest_path):
        with open(dest_path, "rb") as f:
            if f.read(4) == b"%PDF":
                logger.info("Using cached, validated PDF at %s", dest_path)
                return dest_path
        logger.warning("Cached file at %s is not a valid PDF - re-fetching", dest_path)
        os.remove(dest_path)

    last_error: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            logger.info("Fetching DPDP Act PDF from %s (attempt %d/%d)", url, attempt, max_retries)
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
            response.raise_for_status()
            if not response.content.startswith(b"%PDF"):
                raise ValueError(
                    "Response did not look like a PDF (got a webpage instead - the source "
                    "URL may be temporarily down or redirecting to an error page)."
                )
            with open(dest_path, "wb") as f:
                f.write(response.content)
            logger.info("Saved %d bytes to %s", len(response.content), dest_path)
            return dest_path
        except Exception as exc:
            last_error = exc
            logger.warning("PDF fetch attempt %d failed: %s", attempt, exc)
            if attempt < max_retries:
                time.sleep(backoff_seconds * attempt)

    raise RuntimeError(
        f"Could not fetch the DPDP Act PDF from {url} after {max_retries} attempts: {last_error}"
    )


def extract_text(pdf_path: str) -> str:
    from pypdf import PdfReader

    reader = PdfReader(pdf_path)
    return "\n".join((page.extract_text() or "") for page in reader.pages)


def split_into_sections(text: str, max_section: int = 44) -> list[dict]:
    """Split the raw Act text into numbered sections using sequential-anchor
    matching (each section number is searched for only after the previous
    one), which avoids false positives from numbers inside body text."""
    positions: dict[int, int] = {}
    cursor = 0
    for n in range(1, max_section + 1):
        match = _section_start_pattern(n).search(text, cursor)
        if not match:
            continue
        positions[n] = match.start()
        cursor = match.start() + 1

    found_numbers = sorted(positions.keys())
    parsed = []
    for i, n in enumerate(found_numbers):
        start = positions[n]
        end = positions[found_numbers[i + 1]] if i + 1 < len(found_numbers) else start + 4000
        body = text[start:end].strip()
        parsed.append({
            "id": f"S{n}",
            "number": n,
            "chapter": chapter_for_section(n),
            "title": SECTION_TITLES.get(n, f"Section {n}"),
            "raw_text": body,
            "source_url": DPDP_PDF_URL,
        })
    return parsed


_DEFINITION_RE = re.compile(r'["\u201c]([^"\u201d]{2,60})["\u201d]\s+means')


def tag_entities(section: dict) -> dict:
    title_lower = section["title"].lower()
    chapter_upper = section["chapter"].upper()
    entities = {"obligations": [], "rights": [], "penalties": [], "definitions": []}

    if "right" in title_lower:
        entities["rights"].append(section["title"])
    if "obligation" in title_lower or "duties" in title_lower or "OBLIGATIONS" in chapter_upper:
        entities["obligations"].append(section["title"])
    if "penalt" in title_lower or "PENALTIES" in chapter_upper:
        entities["penalties"].append(section["title"])
    if section["number"] == 2:
        entities["definitions"] = _DEFINITION_RE.findall(section["raw_text"])[:10]

    return entities


def estimate_confidence(section: dict) -> float:
    length = len(section["raw_text"])
    if length < 40:
        return 0.4
    score = 0.75 + min(length, 1200) / 1200 * 0.2
    return round(min(score, 0.97), 2)


def is_sensitive(entities: dict) -> bool:
    return any(len(entities.get(cat, [])) > 0 for cat in SENSITIVE_CATEGORIES)


def build_tagged_sections(pdf_path: str, max_section: int = 44) -> list[dict]:
    """End-to-end: parse -> tag -> score. Returns sections ready for the
    review gate; does not touch Qdrant/Neo4j (see dpdp_stores.py)."""
    raw_text = extract_text(pdf_path)
    sections = split_into_sections(raw_text, max_section)
    for section in sections:
        section["entities"] = tag_entities(section)
        section["confidence"] = estimate_confidence(section)
        section["sensitive"] = is_sensitive(section["entities"])
    if len(sections) < max_section:
        missing = sorted(set(range(1, max_section + 1)) - {s["number"] for s in sections})
        logger.warning(
            "Only parsed %d/%d sections - missing section numbers: %s. "
            "The source PDF's layout may have changed; check split_into_sections().",
            len(sections), max_section, missing,
        )
    else:
        logger.info("Parsed and tagged %d/%d sections", len(sections), max_section)
    return sections
