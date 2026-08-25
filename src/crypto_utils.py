"""
crypto_utils.py
----------------
Real, working implementations of the three controls the Policy Engine can
recommend (see policy_engine.py / banking_config.POLICY_MAP): masking,
encryption, tokenisation. Before this file existed, /evaluate only ever
told a caller WHAT to do ("apply masking + encryption") - nothing in the
codebase actually DID it. This is that missing enforcement layer.

Design notes:
  - Masking is pattern-based and irreversible by design (it's meant for
    display/logs, not for round-tripping back to the original value).
  - Encryption is symmetric (Fernet - AES128-CBC + HMAC, from the
    `cryptography` package) and IS reversible with the same key. The key
    must come from POLICY_ENCRYPTION_KEY in the environment for any real
    deployment; a random key is generated at import time as a dev-only
    fallback (encrypted values won't survive a process restart in that
    case, but nothing crashes for local/demo use).
  - Tokenisation replaces a value with a random surrogate and keeps the
    mapping in an in-memory vault (TokenVault). This mirrors how a real
    tokenisation vault behaves (surrogate has no mathematical relationship
    to the original, unlike encryption) but is NOT persistent - a
    production deployment must back this vault with a real database, not
    a Python dict (see TokenVault's docstring).

Requires the `cryptography` package - add to requirements.txt:
    cryptography>=42.0.0
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from typing import Optional

logger = logging.getLogger("dpdp.crypto")

# ---------------------------------------------------------------------------
# Masking - regex patterns for the data types this project's
# DATA_CLASSIFICATION_RULES cares about (banking_config.py). Each pattern
# keeps a small amount visible (last 4 digits, first char of an email
# local-part) so masked output is still useful for debugging/audit
# without exposing the full value - the standard "partial mask" pattern
# used across banking/fintech systems.
# ---------------------------------------------------------------------------
_PATTERNS = {
    # 16-digit card number, grouped in 4s - checked before "aadhaar" so a
    # card number's first 12 digits don't get mistaken for an Aadhaar hit.
    "card": (
        re.compile(r"\b(\d{4})[\s-]?\d{4}[\s-]?\d{4}[\s-]?(\d{4})\b"),
        lambda m: f"{m.group(1)}-XXXX-XXXX-{m.group(2)}",
    ),
    "aadhaar": (
        re.compile(r"\b(\d{4})[\s-]?(\d{4})[\s-]?(\d{4})\b"),
        lambda m: f"XXXX-XXXX-{m.group(3)}",
    ),
    "pan": (
        re.compile(r"\b([A-Z]{5}\d{4}[A-Z])\b"),
        lambda m: f"{m.group(1)[:2]}XXXXXXX{m.group(1)[-1]}",
    ),
    "email": (
        re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*(@[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"),
        lambda m: f"{m.group(1)}***{m.group(2)}",
    ),
    "phone": (
        re.compile(r"\b(\+?91[-\s]?)?([6-9]\d{4})\d{5}\b"),
        lambda m: f"{m.group(1) or ''}{m.group(2)}XXXXX",
    ),
}

# Applied in this exact order (card before aadhaar - see comment above).
_PATTERN_ORDER = ["card", "aadhaar", "pan", "email", "phone"]


def mask_text(text: str) -> str:
    """Applies every known pattern in a fixed order (see _PATTERN_ORDER)."""
    masked = text
    for name in _PATTERN_ORDER:
        pattern, repl = _PATTERNS[name]
        masked = pattern.sub(repl, masked)
    return masked


# ---------------------------------------------------------------------------
# Encryption - Fernet (symmetric, authenticated). Reversible with the key.
# ---------------------------------------------------------------------------
def _load_or_generate_key() -> bytes:
    from cryptography.fernet import Fernet

    env_key = os.environ.get("POLICY_ENCRYPTION_KEY")
    if env_key:
        return env_key.encode()
    generated = Fernet.generate_key()
    logger.warning(
        "POLICY_ENCRYPTION_KEY is not set - using a randomly generated, "
        "in-memory-only key. Encrypted values will NOT be decryptable "
        "after a process restart. Set POLICY_ENCRYPTION_KEY in the "
        "environment before any real deployment. Generate one with: "
        "python -c \"from cryptography.fernet import Fernet; "
        "print(Fernet.generate_key().decode())\""
    )
    return generated


_FERNET_KEY = _load_or_generate_key()


def encrypt_value(value: str) -> str:
    from cryptography.fernet import Fernet

    f = Fernet(_FERNET_KEY)
    return f.encrypt(value.encode()).decode()


def decrypt_value(token: str) -> str:
    from cryptography.fernet import Fernet, InvalidToken

    f = Fernet(_FERNET_KEY)
    try:
        return f.decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ValueError("Could not decrypt - wrong key or corrupted token.") from exc


# ---------------------------------------------------------------------------
# Tokenisation - random surrogate + reversible in-memory vault.
# ---------------------------------------------------------------------------
class TokenVault:
    """In-memory token <-> value store.

    PRODUCTION NOTE: a real tokenisation vault must be a durable, access-
    controlled store (its own encrypted database, ideally on a separate
    network segment from the application it serves) - never application
    memory. This class is deliberately shaped like a real vault client
    (tokenise/detokenise) so swapping the in-memory dict for a real
    backend later is a one-file change, not a rewrite of every caller.
    """

    def __init__(self) -> None:
        self._forward: dict[str, str] = {}   # token -> value
        self._reverse: dict[str, str] = {}    # value -> token (dedupe)

    def tokenise(self, value: str) -> str:
        if value in self._reverse:
            return self._reverse[value]
        token = f"tok_{uuid.uuid4().hex[:16]}"
        self._forward[token] = value
        self._reverse[value] = token
        return token

    def detokenise(self, token: str) -> Optional[str]:
        return self._forward.get(token)


_VAULT = TokenVault()


def tokenise_value(value: str) -> str:
    return _VAULT.tokenise(value)


def detokenise_value(token: str) -> Optional[str]:
    return _VAULT.detokenise(token)


# ---------------------------------------------------------------------------
# Single entry point the Policy Engine calls once it knows which controls
# a piece of text/data needs.
# ---------------------------------------------------------------------------
def apply_controls(text: str, controls: list[str]) -> dict:
    """Applies each recommended control and returns every requested
    transformation, so a caller (or an auditor) can see exactly what each
    control does to the same input side by side. Controls are independent
    of each other - encrypting doesn't consume the masked or tokenised
    output; all three run against the original `text`."""
    result: dict[str, str] = {}
    if "masking" in controls:
        result["masked"] = mask_text(text)
    if "encryption" in controls:
        result["encrypted"] = encrypt_value(text)
    if "tokenisation" in controls:
        result["tokenised"] = tokenise_value(text)
    return result
