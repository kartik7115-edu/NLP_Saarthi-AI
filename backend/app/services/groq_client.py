"""Small, isolated client for the Groq API (OpenAI-compatible endpoint).

This module is the only place that talks to the LLM provider. It
- reads GROQ_API_KEY / GROQ_MODEL / GROQ_BASE_URL / GROQ_TIMEOUT_SECONDS from settings (environment / .env),
- never logs, returns or raises the API key (errors are reduced to a class name and a redacted message),
- raises GroqError on any failure so the caller can fall back to the offline answer engine.
"""
import logging
import re
import threading
import time
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

_KEY_LIKE = re.compile(r"gsk_[A-Za-z0-9_\-]{6,}")

# Models such as openai/gpt-oss-* write "1800\u2011180\u20111551" (non-breaking hyphens) and narrow no-break spaces.
# Downstream code (grounding guard, protected-span masking) expects ASCII hyphens/spaces: a phone number written with
# U+2011 was split into "1800", "180", "1551" and the whole (correct) answer was rejected as ungrounded.
_DASHES = re.compile("[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]")
_SPACES = re.compile("[\u00A0\u2007\u202F]")
_ZERO_WIDTH = re.compile("[\u200B\u2060\uFEFF]")

# After an HTTP 429 every further call in the next few seconds fails too and only burns the quota further;
# callers fall back immediately instead. The wait honours Retry-After when Groq sends it.
_RATE_LIMIT_COOLDOWN_DEFAULT = 20.0
_cooldown_lock = threading.Lock()
_cooldown_until = 0.0


def normalize_output(text: str) -> str:
    """Map typographic dashes / no-break spaces to ASCII so numbers, dates and phone numbers stay one token."""
    text = _ZERO_WIDTH.sub("", text)
    text = _DASHES.sub("-", text)
    return _SPACES.sub(" ", text)


def cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def _start_cooldown(exc: Exception) -> float:
    global _cooldown_until
    wait = _RATE_LIMIT_COOLDOWN_DEFAULT
    try:
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        ra = headers.get("retry-after")
        if ra:
            wait = min(120.0, max(1.0, float(ra)))
    except (TypeError, ValueError):
        pass
    with _cooldown_lock:
        _cooldown_until = max(_cooldown_until, time.monotonic() + wait)
    return wait


class GroqError(Exception):
    """Any failure while calling Groq (not configured, network, auth, rate limit, empty answer ...)."""


def model_name() -> str:
    return (settings.groq_model or "").strip() or DEFAULT_MODEL


def is_configured() -> bool:
    return bool((settings.groq_api_key or "").strip()) and not settings.offline_only


def redact(text: Any) -> str:
    """Remove the configured key (and anything shaped like a Groq key) from a string."""
    s = str(text)
    key = (settings.groq_api_key or "").strip()
    if key:
        s = s.replace(key, "***")
    return _KEY_LIKE.sub("***", s)


def _make_client():
    from openai import OpenAI  # the OpenAI SDK speaks Groq's OpenAI-compatible protocol
    return OpenAI(
        api_key=settings.groq_api_key.strip(),
        base_url=(settings.groq_base_url or "").strip() or DEFAULT_BASE_URL,
        timeout=float(settings.groq_timeout_seconds or 20.0),
        max_retries=1,
    )


def chat(messages: List[Dict[str, str]], *, temperature: float = 0.1, max_tokens: int = 700,
         model: Optional[str] = None, extra_body: Optional[Dict[str, Any]] = None,
         require_complete: bool = False) -> str:
    """Send a chat completion request and return the answer text. Raises GroqError on any failure.

    extra_body: optional provider-specific request fields (only sent when given, so existing calls are unchanged).
    require_complete: raise GroqError("truncated") when the model stopped at max_tokens (used for translations,
    where a cut-off text silently loses content).
    """
    if not is_configured():
        raise GroqError("groq_not_configured")
    remaining = cooldown_remaining()
    if remaining > 0:
        raise GroqError(f"rate_limited (cooling down {remaining:.0f}s)")
    try:
        client = _make_client()
        kwargs = dict(model=model or model_name(), messages=messages, temperature=temperature, max_tokens=max_tokens)
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = (choice.message.content or "").strip()
        finish = getattr(choice, "finish_reason", None)
    except Exception as e:  # noqa: BLE001 - every provider/network error means "use the fallback"
        status = getattr(e, "status_code", None)
        detail = f"{type(e).__name__}" + (f" (HTTP {status})" if status else "")
        if status == 429:
            detail += f"; pausing Groq calls for {_start_cooldown(e):.0f}s"
        logger.warning("Groq call failed: %s", detail)
        raise GroqError(detail) from None     # `from None`: do not chain the original exception (may echo request details)
    if require_complete and finish == "length":
        raise GroqError("truncated")
    if not text:
        raise GroqError("empty_response")
    return normalize_output(text)
