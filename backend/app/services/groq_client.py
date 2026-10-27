"""Small, isolated client for the Groq API (OpenAI-compatible endpoint).

This module is the only place that talks to the LLM provider. It
- reads GROQ_API_KEY / GROQ_MODEL / GROQ_BASE_URL / GROQ_TIMEOUT_SECONDS from settings (environment / .env),
- never logs, returns or raises the API key (errors are reduced to a class name and a redacted message),
- raises GroqError on any failure so the caller can fall back to the offline answer engine.
"""
import logging
import re
from typing import Any, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "llama-3.3-70b-versatile"
DEFAULT_BASE_URL = "https://api.groq.com/openai/v1"

_KEY_LIKE = re.compile(r"gsk_[A-Za-z0-9_\-]{6,}")


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
         model: Optional[str] = None, extra_body: Optional[Dict[str, Any]] = None) -> str:
    """Send a chat completion request and return the answer text. Raises GroqError on any failure.

    extra_body: optional provider-specific request fields (only sent when given, so existing calls are unchanged).
    """
    if not is_configured():
        raise GroqError("groq_not_configured")
    try:
        client = _make_client()
        kwargs = dict(model=model or model_name(), messages=messages, temperature=temperature, max_tokens=max_tokens)
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = client.chat.completions.create(**kwargs)
        text = (response.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001 - every provider/network error means "use the fallback"
        status = getattr(e, "status_code", None)
        detail = f"{type(e).__name__}" + (f" (HTTP {status})" if status else "")
        logger.warning("Groq call failed: %s", detail)
        raise GroqError(detail) from None     # `from None`: do not chain the original exception (may echo request details)
    if not text:
        raise GroqError("empty_response")
    return text
