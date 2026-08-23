"""
policy_engine.py
-----------------
Rule-based Data Classification + Policy Engine (see architecture diagram).
Deterministic keyword matching - no LLM call, so it can never hallucinate
a classification. Every result includes the matched keywords and a
rationale string, so a human reviewer can audit exactly why a control was
recommended.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.banking_config import DATA_CLASSIFICATION_RULES, POLICY_MAP


@dataclass
class PolicyResult:
    data_classes: list[str]
    required_controls: list[str]
    rationale: dict[str, str]
    matched_keywords: dict[str, list[str]] = field(default_factory=dict)


def classify_text(text: str) -> tuple[list[str], dict[str, list[str]]]:
    """Returns (matched_classes, matched_keywords). A text can match more
    than one class - e.g. a KYC form mentioning both Aadhaar (customer_pii)
    and account balance (financial_data)."""
    text_lower = text.lower()
    matched_classes: list[str] = []
    matched_keywords: dict[str, list[str]] = {}

    for data_class, keywords in DATA_CLASSIFICATION_RULES.items():
        hits = [kw for kw in keywords if kw in text_lower]
        if hits:
            matched_classes.append(data_class)
            matched_keywords[data_class] = hits

    return matched_classes, matched_keywords


def get_policy(data_classes: list[str]) -> PolicyResult:
    """Union of required controls across all given classes, with per-class
    rationale. Unknown class strings are silently skipped (not raised) so
    a caller passing a partially-invalid list still gets a best-effort
    result - validate inputs at the API boundary instead (banking_routes.py
    does this)."""
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


def evaluate(text: str) -> PolicyResult:
    """Convenience: classify + resolve policy in one call."""
    classes, keywords = classify_text(text)
    result = get_policy(classes)
    result.matched_keywords = keywords
    return result


if __name__ == "__main__":
    samples = [
        "The customer's Aadhaar number and phone number must be verified during onboarding.",
        "The transaction amount and UTR number shall be recorded for audit.",
        "Biometric fingerprint data collected shall not be stored beyond the session.",
        "The quarterly board meeting was rescheduled.",
    ]
    for sample in samples:
        result = evaluate(sample)
        print(f"\nText: {sample}")
        print(f"  Classes: {result.data_classes}")
        print(f"  Controls: {result.required_controls}")
