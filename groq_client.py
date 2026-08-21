"""
Minimal streaming client for Groq's hosted, OpenAI-compatible chat API.

Deliberately not using the `groq` or `openai` SDK packages - a raw
`requests` call to the documented endpoint keeps the dependency list (and
therefore the RAM/build-time footprint) smaller, and Groq's SSE format is
simple enough that a small hand-rolled parser is genuinely less risk than
pulling in a whole client library for one endpoint.
"""

import json
import logging
from typing import Iterator

import requests

import dpdp_config as cfg

logger = logging.getLogger("dpdp.groq")


class GroqError(Exception):
    pass


def stream_chat(messages: list[dict]) -> Iterator[str]:
    """Yields response text incrementally. Raises GroqError with a clear,
    user-relevant message on any failure (missing key, bad model name,
    rate limit, network error) instead of letting a raw exception surface."""
    if not cfg.GROQ_API_KEY:
        raise GroqError(
            "GROQ_API_KEY is not set. Add it in Render's Environment tab - "
            "get a free key at console.groq.com/keys."
        )

    payload = {
        "model": cfg.GROQ_MODEL,
        "messages": messages,
        "max_tokens": cfg.GROQ_MAX_TOKENS,
        "stream": True,
        "reasoning_effort": "low"
    }
    headers = {
        "Authorization": f"Bearer {cfg.GROQ_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            cfg.GROQ_API_URL, headers=headers, json=payload,
            stream=True, timeout=cfg.GROQ_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise GroqError(f"Could not reach Groq's API: {exc}") from exc

    if response.status_code != 200:
        body = response.text[:500]
        raise GroqError(f"Groq API returned {response.status_code}: {body}")

    for raw_line in response.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data: "):
            continue
        data = raw_line[len("data: "):]
        if data.strip() == "[DONE]":
            return
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            logger.warning("Skipping malformed SSE chunk from Groq: %r", data[:200])
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta", {})
        piece = delta.get("content")
        if piece:
            yield piece
