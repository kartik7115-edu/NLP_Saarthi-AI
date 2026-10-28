import logging
import re
import time
import uuid
import requests
from deep_translator import GoogleTranslator
from langdetect import detect
from app.config import settings
from app.services.language_utils import is_text_in_language, letter_counts, normalize_language

logger = logging.getLogger(__name__)

TRANSLITERATION_SCRIPTS = {
    "hi": ("Deva", "Latn"),
    "mr": ("Deva", "Latn"),
    "bn": ("Beng", "Latn"),
    "te": ("Telu", "Latn"),
    "ta": ("Taml", "Latn"),
    "gu": ("Gujr", "Latn"),
    "kn": ("Knda", "Latn"),
    "ml": ("Mlym", "Latn"),
    "pa": ("Guru", "Latn"),
    "or": ("Orya", "Latn"),
    "ur": ("Arab", "Latn"),
    "as": ("Beng", "Latn"),
}


class TranslationError(Exception):
    """Translation could not be completed reliably.

    `code` is a short, safe reason (no keys, no provider payloads) that callers can log or expose in a header.
    """

    def __init__(self, code: str, detail: str = ""):
        super().__init__(code + (f": {detail}" if detail else ""))
        self.code = code


GOOGLE_RATE_LIMIT_COOLDOWN_SECONDS = 120   # after HTTP 429 from the free Google endpoint
MAX_CHUNK_CHARS = 4000   # the free Google endpoint rejects >5000 characters per request


def _split_long_line(line: str, limit: int) -> list:
    """Break one over-long line at sentence ends (then spaces, then hard) into pieces of at most `limit` chars."""
    pieces, buf = [], ""
    for sentence in re.split(r"(?<=[.!?\u0964])\s+", line):
        while len(sentence) > limit:
            cut = sentence.rfind(" ", 0, limit)
            cut = cut if cut > 0 else limit
            if buf:
                pieces.append(buf)
                buf = ""
            pieces.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if buf and len(buf) + 1 + len(sentence) > limit:
            pieces.append(buf)
            buf = ""
        buf = (buf + " " + sentence).strip() if buf else sentence
    if buf:
        pieces.append(buf)
    return pieces


def chunk_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list:
    """Split text into [(chunk, separator_that_followed_it)] so that "".join(c + s) rebuilds the original layout."""
    chunks = []
    lines = text.split("\n")
    buf, buf_len = [], 0
    for i, line in enumerate(lines):
        last = i == len(lines) - 1
        if len(line) > limit:
            if buf:
                chunks.append(("\n".join(buf), "\n"))
                buf, buf_len = [], 0
            pieces = _split_long_line(line, limit)
            for j, piece in enumerate(pieces):
                chunks.append((piece, "\n" if (j == len(pieces) - 1 and not last) else " "))
            continue
        if buf and buf_len + 1 + len(line) > limit:
            chunks.append(("\n".join(buf), "\n"))
            buf, buf_len = [], 0
        buf.append(line)
        buf_len += len(line) + 1
    if buf:
        chunks.append(("\n".join(buf), ""))
    elif chunks:
        chunks[-1] = (chunks[-1][0], "")
    return chunks


class TranslatorService:
    def __init__(self):
        self._azure_available = None

    @property
    def azure_available(self) -> bool:
        if self._azure_available is None:
            self._azure_available = bool(
                settings.azure_translator_key
                and not settings.offline_only
            )
        return self._azure_available

    def detect_language(self, text: str) -> str:
        if self.azure_available:
            try:
                return self._azure_detect(text)
            except Exception as e:
                logger.warning(f"Azure language detection failed, falling back: {e}")
        try:
            lang_code = detect(text)
            return lang_code
        except Exception as e:
            logger.error(f"Language detection failed: {e}")
            return "en"

    def _translate_chunk(self, chunk: str, source: str, target: str) -> str:
        """Translate one chunk with Azure, then Google. Raises TranslationError when no provider produced text."""
        failures = []
        if self.azure_available:
            try:
                out = self._azure_translate(chunk, source, target)
                if isinstance(out, str) and out.strip():
                    return out
                failures.append("azure:empty_result")
            except Exception as e:
                logger.warning(f"Azure translation failed, falling back: {e}")
                failures.append(f"azure:{type(e).__name__}")
        if self._google_cooldown_remaining() > 0:
            # Google answered "too many requests" recently: asking again only adds latency and extends the block
            failures.append("google:cooling_down")
            raise TranslationError("all_providers_failed", ", ".join(failures))
        try:
            out = GoogleTranslator(source=source, target=target).translate(chunk)
            if isinstance(out, str) and out.strip():
                return out
            failures.append("google:empty_result")
        except Exception as e:
            if type(e).__name__ == "TooManyRequests":
                self._google_blocked_until = time.monotonic() + GOOGLE_RATE_LIMIT_COOLDOWN_SECONDS
                logger.warning("Google Translate rate limit reached; not using it for %ds (other providers continue)",
                               GOOGLE_RATE_LIMIT_COOLDOWN_SECONDS)
            else:
                logger.error(f"Translation failed from {source} to {target}: {e}")
            failures.append(f"google:{type(e).__name__}")
        raise TranslationError("all_providers_failed", ", ".join(failures))

    def _google_cooldown_remaining(self) -> float:
        return max(0.0, getattr(self, "_google_blocked_until", 0.0) - time.monotonic())

    def translate_text_strict(self, text: str, source: str = "auto", target: str = "en") -> str:
        """Translate or raise TranslationError. Never returns the input as if it had been translated.

        Failure modes made explicit: unsupported target, every provider failing, an empty result, and a provider
        that echoes the untranslated input back.
        """
        if not isinstance(text, str) or not text.strip():
            raise TranslationError("empty_input")
        src, tgt = normalize_language(source) or "auto", normalize_language(target)
        if tgt in (None, "auto"):
            raise TranslationError("unsupported_target", str(target))
        if src == tgt:
            return text
        parts = [self._translate_chunk(chunk, src, tgt) + sep for chunk, sep in chunk_text(text)]
        result = "".join(parts)
        if not result.strip():
            raise TranslationError("empty_result")
        if (result.strip() == text.strip() and not is_text_in_language(text, tgt)
                and sum(letter_counts(text).values()) >= 12):
            raise TranslationError("provider_returned_input_unchanged")
        return result

    def translate_text(self, text: str, source: str = "auto", target: str = "en") -> str:
        """Legacy, non-raising API: returns the input when translation is impossible.

        Use translate_text_strict when the caller must know whether translation really happened
        (for example answers that are labelled with a response language).
        """
        if not text or source == target:
            return text
        try:
            return self.translate_text_strict(text, source, target)
        except TranslationError as e:
            logger.error(f"Translation failed from {source} to {target}: {e}")
            return text

    def transliterate_text(self, text: str, language: str) -> str | None:
        if not text or language == "en":
            return None

        if not self.azure_available:
            logger.debug("Azure Translator not configured: no romanized transliteration")   # optional feature, not an error
            return None

        script_info = TRANSLITERATION_SCRIPTS.get(language)
        if not script_info:
            logger.warning(f"No transliteration script mapping for language: {language}")
            return None

        from_script, to_script = script_info

        try:
            result = self._azure_transliterate(text, language, from_script, to_script)
            if result and result != text:
                return result
            return None
        except Exception as e:
            logger.warning(f"Transliteration failed for {language}: {e}")
            return None

    def _azure_detect(self, text: str) -> str:
        url = f"{settings.azure_translator_endpoint}/detect?api-version=3.0"
        headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
            "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
            "Content-Type": "application/json",
        }
        body = [{"Text": text}]
        resp = requests.post(url, headers=headers, json=body, timeout=5)
        resp.raise_for_status()
        result = resp.json()
        return result[0]["language"]

    def _azure_translate(self, text: str, source: str, target: str) -> str:
        params = {"api-version": "3.0", "to": target}
        if source and source != "auto":
            params["from"] = source

        url = f"{settings.azure_translator_endpoint}/translate"
        headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
            "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        body = [{"Text": text}]
        resp = requests.post(url, headers=headers, json=body, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        return result[0]["translations"][0]["text"]

    def _azure_transliterate(self, text: str, language: str, from_script: str, to_script: str) -> str:
        url = f"{settings.azure_translator_endpoint}/transliterate"
        params = {
            "api-version": "3.0",
            "language": language,
            "fromScript": from_script,
            "toScript": to_script,
        }
        headers = {
            "Ocp-Apim-Subscription-Key": settings.azure_translator_key,
            "Ocp-Apim-Subscription-Region": settings.azure_translator_region,
            "Content-Type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        body = [{"Text": text}]
        resp = requests.post(url, headers=headers, json=body, params=params, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        return result[0]["text"]


translator_service = TranslatorService()
