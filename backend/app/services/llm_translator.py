"""Translation with the configured Groq model - the fallback when the machine-translation providers fail.

Before this module, the ONLY way to put an answer into Hindi or Marathi was the translator service (Azure when a key
is configured, otherwise the free, unofficial Google endpoint used by deep_translator). Whenever that endpoint failed
(rate limit, block, network error, or a mangled protection token) the user got the English answer. Groq was already
configured and working, but it was never asked.

`translate_with_groq(text, source, target)` has the same contract as translator_service.translate_text_strict:
it returns the translation or RAISES TranslationError. The answer localizer validates whatever comes back exactly as it
validates a machine translation (protected tokens, target script, Hindi-vs-Marathi check), so an LLM translation is
never accepted on trust.
"""
import logging
import re

from app.services import groq_client
from app.services.language_utils import LANGUAGE_NAMES, normalize_language
from app.services.translator import TranslationError

logger = logging.getLogger(__name__)

_SYSTEM = (
    "You are a professional translator for an Indian government-scheme helpline used by farmers. "
    "Translate the user's text from {src} into {tgt}. Rules:\n"
    "1. Output ONLY the translation - no preface, notes, quotes or explanations.\n"
    "2. Write natural, simple, everyday {tgt} that a farmer would understand{script}.\n"
    "3. Tokens of the form QZ<number>QZ (for example QZ0QZ, QZ12QZ) are placeholders for scheme names, amounts, dates, "
    "URLs and phone numbers. Copy every placeholder exactly once, unchanged, in the right place. Never translate, "
    "transliterate, renumber, merge or drop a placeholder.\n"
    "4. Translate everything else, including headings, bullet points, notes and questions. Keep the line breaks, "
    "bullet markers ('-', '1.') and **bold** markers.\n"
    "5. Do not add, remove, soften or strengthen any information. Do not answer the text; only translate it."
)
_SCRIPT_HINT = {"hi": " in Devanagari script", "mr": " in Devanagari script (Marathi, not Hindi)"}
_PLACEHOLDER = re.compile(r"QZ\d+QZ")
_PREFACE = re.compile(r"^\s*(?:here is|here's|translation|अनुवाद|भाषांतर)[^\n]{0,40}:\s*\n", re.I)


def _model_options() -> dict:
    # Reasoning models on Groq (openai/gpt-oss-*) count reasoning tokens against max_tokens; translation needs little.
    return {"reasoning_effort": "low"} if groq_client.model_name().startswith("openai/gpt-oss") else {}


def is_available() -> bool:
    return groq_client.is_configured()


def translate_with_groq(text: str, source: str = "en", target: str = "hi") -> str:
    if not isinstance(text, str) or not text.strip():
        raise TranslationError("empty_input")
    src, tgt = normalize_language(source), normalize_language(target)
    if tgt in (None, "auto"):
        raise TranslationError("unsupported_target", str(target))
    if not groq_client.is_configured():
        raise TranslationError("llm_not_configured")
    src_name = LANGUAGE_NAMES.get(src, "the source language") if src not in (None, "auto") else "the source language"
    system = _SYSTEM.format(src=src_name, tgt=LANGUAGE_NAMES[tgt], script=_SCRIPT_HINT.get(tgt, ""))
    n_tokens = len(set(_PLACEHOLDER.findall(text)))
    if n_tokens:
        # telling the model exactly which placeholders exist makes it far less likely to drop or merge one
        system += (f"\n6. This text contains exactly {n_tokens} placeholders (QZ0QZ to QZ{n_tokens - 1}QZ). "
                   f"Your output must contain each of them exactly once.")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": text}]
    # Room for the whole translation (Devanagari needs more tokens than English) plus a little reasoning, but no
    # more: Groq's per-minute token limit can count the requested max_tokens, so an oversized budget invites HTTP 429.
    budget = min(4096, 500 + len(text))
    try:
        out = groq_client.chat(messages, temperature=0.0, max_tokens=budget, extra_body=_model_options() or None,
                               require_complete=True)
    except groq_client.GroqError as e:
        logger.warning("Groq translation %s->%s failed: %s", src, tgt, groq_client.redact(e))
        code = {"empty_response": "llm_empty_result", "truncated": "llm_truncated"}.get(str(e), "llm_failed")
        if str(e).startswith(("RateLimitError", "rate_limited")):
            code = "llm_rate_limited"
        raise TranslationError(code, groq_client.redact(e)) from None
    out = _PREFACE.sub("", out.strip()).strip()
    if len(out) >= 2 and out[0] == out[-1] and out[0] in "\"'`":
        out = out[1:-1].strip()
    if not out:
        raise TranslationError("llm_empty_result")
    return out
