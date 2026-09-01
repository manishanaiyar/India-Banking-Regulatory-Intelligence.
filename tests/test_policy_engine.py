"""
Tests for src/policy_engine.py - deterministic keyword classification and
control resolution. No LLM involved, so results must be exactly
reproducible for the same input every time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import policy_engine
from src import crypto_utils


def test_classify_detects_customer_pii():
    classes, keywords = policy_engine.classify_text(
        "The customer's Aadhaar number and phone number must be verified during onboarding."
    )
    assert "customer_pii" in classes
    assert "aadhaar" in keywords["customer_pii"]


def test_classify_detects_multiple_classes_in_one_text():
    classes, _ = policy_engine.classify_text(
        "The customer's Aadhaar number was linked to the transaction amount and UTR number for audit."
    )
    assert "customer_pii" in classes
    assert len(classes) >= 2


def test_classify_returns_empty_for_non_sensitive_text():
    classes, keywords = policy_engine.classify_text("The quarterly board meeting was rescheduled.")
    assert classes == []
    assert keywords == {}


def test_evaluate_is_deterministic():
    text = "Biometric fingerprint data collected shall not be stored beyond the session."
    result_a = policy_engine.evaluate(text)
    result_b = policy_engine.evaluate(text)
    assert result_a.data_classes == result_b.data_classes
    assert result_a.required_controls == result_b.required_controls


def test_evaluate_without_protect_leaves_protected_empty():
    result = policy_engine.evaluate("Aadhaar number 1234 5678 9012", protect=False)
    assert result.protected == {}


def test_evaluate_with_protect_populates_protected_output():
    result = policy_engine.evaluate("Aadhaar number 1234 5678 9012", protect=True)
    assert result.data_classes
    assert result.protected
    assert "1234 5678 9012" not in result.protected.get("masked", "")


def test_classify_does_not_false_match_keyword_inside_unrelated_word():
    """Regression test: 'name' is a keyword, but 'username' must not
    trigger it - found via live testing where a support-ticket-style
    text with 'My username is...' incorrectly showed 'name' as a
    matched keyword due to a naive substring check."""
    classes, keywords = policy_engine.classify_text(
        "My username is alex.vance@example.com and my employee ID is IC-2026-994."
    )
    all_keywords = [kw for kws in keywords.values() for kw in kws]
    assert "name" not in all_keywords


def test_classify_detects_card_number_even_without_the_literal_keyword_phrase():
    """Regression test: the keyword list only has the literal phrase
    'card number', so text saying 'corporate card ending in 4111 2222
    3333 4444' didn't match transaction_data by keyword alone - even
    though crypto_utils.mask_text() was already finding and masking that
    exact card number via regex. classify_text() must not disagree with
    what masking actually catches."""
    classes, keywords = policy_engine.classify_text(
        "I noticed a charge on our corporate card ending in 4111 2222 3333 4444."
    )
    assert "transaction_data" in classes


def test_classify_does_not_double_count_card_digits_as_aadhaar_too():
    """Regression test: a 16-digit card number contains a 12-digit
    substring that coincidentally matches the generic Aadhaar shape, so
    checking each crypto_utils pattern independently against the same
    text double-reported one card number as both transaction_data (card)
    and customer_pii (aadhaar). detect_patterns()'s sequential elimination
    (same order as mask_text()) must prevent this."""
    classes, keywords = policy_engine.classify_text(
        "Card number: 4111 2222 3333 4444"
    )
    assert "[detected aadhaar pattern]" not in keywords.get("customer_pii", [])


def test_mask_international_phone_number():
    """Regression test: a US-format number like '+1-555-019-2831' passed
    through mask_text() completely unmasked, because the only phone
    pattern was India-specific ([6-9]xxxxxxxxx). Full international
    coverage isn't realistic with regex alone, but an explicit
    '+<country code>-...' format should be caught."""
    masked = crypto_utils.mask_text("Call me at +1-555-019-2831 anytime.")
    assert "555-019-2831" not in masked
    assert "+1" in masked
