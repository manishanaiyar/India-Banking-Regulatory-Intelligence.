"""
Tests for src/crypto_utils.py - the real masking/encryption/tokenisation
implementations behind the Policy Engine's recommended controls.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import crypto_utils


def test_mask_aadhaar_keeps_last_four_digits_only():
    masked = crypto_utils.mask_text("Aadhaar: 1234 5678 9012")
    assert "1234" not in masked
    assert "5678" not in masked
    assert "9012" in masked


def test_mask_card_number_keeps_first_and_last_four():
    masked = crypto_utils.mask_text("Card: 4111 1111 1111 1234")
    assert masked.count("4111") == 1  # first group preserved once
    assert "1234" in masked
    assert "1111-1111" not in masked


def test_mask_email_keeps_first_char_and_domain():
    masked = crypto_utils.mask_text("Contact: jdoe@example.com")
    assert "jdoe" not in masked
    assert masked.count("j") >= 1
    assert "@example.com" in masked


def test_mask_phone_keeps_prefix_only():
    masked = crypto_utils.mask_text("Phone: 9876543210")
    assert "9876543210" not in masked
    assert "98765" in masked


def test_mask_text_with_no_sensitive_data_is_unchanged():
    text = "The quarterly board meeting was rescheduled."
    assert crypto_utils.mask_text(text) == text


def test_encrypt_decrypt_roundtrip():
    original = "sensitive value 1234"
    token = crypto_utils.encrypt_value(original)
    assert token != original
    assert crypto_utils.decrypt_value(token) == original


def test_decrypt_invalid_token_raises_value_error():
    import pytest
    with pytest.raises(ValueError):
        crypto_utils.decrypt_value("not-a-real-fernet-token")


def test_tokenise_detokenise_roundtrip():
    original = "1234567890123456"
    token = crypto_utils.tokenise_value(original)
    assert token.startswith("tok_")
    assert crypto_utils.detokenise_value(token) == original


def test_tokenise_same_value_twice_returns_same_token():
    """The vault dedupes by value so the same PII always maps to the same
    surrogate - required for referential consistency across records."""
    t1 = crypto_utils.tokenise_value("repeat-value")
    t2 = crypto_utils.tokenise_value("repeat-value")
    assert t1 == t2


def test_apply_controls_runs_only_requested_controls():
    result = crypto_utils.apply_controls("Aadhaar 1234 5678 9012", ["masking"])
    assert "masked" in result
    assert "encrypted" not in result
    assert "tokenised" not in result


def test_apply_controls_runs_all_three_independently_against_original_text():
    text = "Aadhaar 1234 5678 9012"
    result = crypto_utils.apply_controls(text, ["masking", "encryption", "tokenisation"])
    assert set(result.keys()) == {"masked", "encrypted", "tokenised"}
    # Encryption/tokenisation both run against the ORIGINAL text, not the
    # masked output - decrypting must recover the full original value.
    assert crypto_utils.decrypt_value(result["encrypted"]) == text
