"""
Tests for src/policy_engine.py - deterministic keyword classification and
control resolution. No LLM involved, so results must be exactly
reproducible for the same input every time.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import policy_engine


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


def test_get_policy_skips_unknown_class_without_raising():
    result = policy_engine.get_policy(["not_a_real_class"])
    assert result.required_controls == []
    assert result.rationale == {}
