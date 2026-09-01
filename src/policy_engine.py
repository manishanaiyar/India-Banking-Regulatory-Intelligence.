"""
policy_engine.py
-----------------
Rule-based Data Classification + Policy Engine (see architecture diagram).
Deterministic keyword matching for classification - no LLM call, so it can
never hallucinate a classification. Every result includes the matched
keywords and a rationale string, so a human reviewer can audit exactly why
a control was recommended.

CHANGED: this used to be advisory-only - it told you WHAT controls a piece
of text needed, but nothing ever applied them. It now optionally calls
crypto_utils.apply_controls() to actually mask/encrypt/tokenise the text,
closing the "Policy Engine" box on the architecture diagram end to end
instead of leaving it as a recommendation-only stub. Pass protect=True to
evaluate() to get real transformed output back (see /protect in main.py).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from src.banking_config import DATA_CLASSIFICATION_RULES, POLICY_MAP
from src import crypto_utils

# Word-boundary matching, not a plain substring check - a naive `"name" in
# text_lower` incorrectly matches inside unrelated words (e.g. "username",
# "surname", "codename"). \b doesn't work perfectly for multi-word phrases
# with punctuation, but for this keyword list (single words and simple
# phrases) it correctly rejects mid-word matches while still matching the
# phrase surrounded by spaces/punctuation/text boundaries.
_KEYWORD_PATTERN_CACHE: dict[str, "re.Pattern[str]"] = {}


def _keyword_pattern(keyword: str) -> "re.Pattern[str]":
    if keyword not in _KEYWORD_PATTERN_CACHE:
        _KEYWORD_PATTERN_CACHE[keyword] = re.compile(r"\b" + re.escape(keyword) + r"\b")
    return _KEYWORD_PATTERN_CACHE[keyword]


# Maps a crypto_utils regex pattern name to the data class it should count
# as evidence for, so classify_text() never disagrees with what
# crypto_utils.mask_text() actually finds and masks in the same text. This
# is what closes the gap where a real card number ("...ending in 4111 2222
# 3333 4444") got silently masked by crypto_utils but wasn't reported under
# transaction_data because the text said "corporate card", not the literal
# keyword phrase "card number".
_PATTERN_TO_CLASS = {
    "card": "transaction_data",
    "aadhaar": "customer_pii",
    "pan": "customer_pii",
    "email": "customer_pii",
    "phone": "customer_pii",
    "phone_intl": "customer_pii",
}


@dataclass
class PolicyResult:
    data_classes: list[str]
    required_controls: list[str]
    rationale: dict[str, str]
    matched_keywords: dict[str, list[str]] = field(default_factory=dict)
    protected: dict[str, str] = field(default_factory=dict)


def classify_text(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Returns (matched_classes, matched_keywords). A text can match more
    than one class - e.g. a KYC form mentioning both Aadhaar (customer_pii)
    and account balance (financial_data).

    Two detection passes, merged:
      1. Keyword list (DATA_CLASSIFICATION_RULES), word-boundary matched.
      2. crypto_utils' own regex patterns (card/aadhaar/pan/email/phone) -
         this is what catches an actual card/Aadhaar/etc. NUMBER even when
         the surrounding text doesn't use the exact keyword phrase, and
         guarantees classification never misses something the masking
         step downstream would have caught anyway."""
    text_lower = text.lower()
    matched_classes: list[str] = []
    matched_keywords: dict[str, list[str]] = {}

    for data_class, keywords in DATA_CLASSIFICATION_RULES.items():
        hits = [kw for kw in keywords if _keyword_pattern(kw).search(text_lower)]
        if hits:
            matched_classes.append(data_class)
            matched_keywords[data_class] = hits

    for pattern_name, matches in crypto_utils.detect_patterns(text).items():
        data_class = _PATTERN_TO_CLASS.get(pattern_name)
        if data_class is None:
            continue
        if data_class not in matched_classes:
            matched_classes.append(data_class)
            matched_keywords[data_class] = []
        label = f"[detected {pattern_name} pattern]"
        if label not in matched_keywords[data_class]:
            matched_keywords[data_class].append(label)

    return matched_classes, matched_keywords


def get_policy(data_classes: list[str]) -> PolicyResult:
    """Union of required controls across all given classes, with per-class
    rationale. Unknown class strings are silently skipped (not raised) so
    a caller passing a partially-invalid list still gets a best-effort
    result - validate inputs at the API boundary instead (main.py does
    this via the Pydantic request models)."""
    controls: list[str] = []
    rationale: dict[str, str] = {}
    for data_class in data_classes:
        policy = POLICY_MAP.get(data_class)
        if not policy:
            continue
        rationale[data_class] = policy["rationale"]
        for control in policy["controls"]:
            if control not in controls:
                controls.append(control)
    return PolicyResult(data_classes=data_classes, required_controls=controls, rationale=rationale)


def evaluate(text: str, protect: bool = False) -> PolicyResult:
    """Convenience: classify + resolve policy in one call.

    protect=True additionally runs the recommended controls against
    `text` via crypto_utils.apply_controls() and populates
    PolicyResult.protected. Defaults to False because most callers (e.g.
    /ask on every query, or ingestion tagging every section) only need
    the classification for audit purposes, not an actual transformed
    value - so we don't pay masking/encryption cost on every call unless
    the caller specifically wants the protected output (see /protect)."""
    classes, keywords = classify_text(text)
    result = get_policy(classes)
    result.matched_keywords = keywords
    if protect and result.required_controls:
        result.protected = crypto_utils.apply_controls(text, result.required_controls)
    return result


if __name__ == "__main__":
    samples = [
        "The customer's Aadhaar number 1234 5678 9012 and phone number 9876543210 must be verified during onboarding.",
        "The transaction amount and UTR number shall be recorded for audit.",
        "Biometric fingerprint data collected shall not be stored beyond the session.",
        "The quarterly board meeting was rescheduled.",
    ]
    for sample in samples:
        result = evaluate(sample, protect=True)
        print(f"\nText: {sample}")
        print(f"  Classes: {result.data_classes}")
        print(f"  Controls: {result.required_controls}")
        print(f"  Protected: {result.protected}")
