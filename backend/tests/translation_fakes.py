"""A faithful fake of translator_service for tests (no network).

A *working* fake produces text in the target script and keeps the QZ<n>QZ protection tokens intact, like a good
translation provider. Failure modes reproduce the real ways translation goes wrong:
  raise      every provider fails                   -> TranslationError("all_providers_failed")
  echo       provider returns the input unchanged   -> TranslationError("provider_returned_input_unchanged")
  drop_token provider loses a protected token       -> localizer reports protected_term_lost
  garbage    provider returns a non-string          -> localizer reports invalid_provider_result
  latin      provider returns a Latin-script answer -> localizer reports result_not_in_target_language
"""
import re

from app.services.translator import TranslationError

NATIVE_WORD = {
    "hi": "\u091c\u093e\u0928\u0915\u093e\u0930\u0940", "bn": "\u09a4\u09a5\u09cd\u09af", "te": "\u0c38\u0c2e\u0c3e\u0c1a\u0c3e\u0c30\u0c02",
    "mr": "\u092e\u093e\u0939\u093f\u0924\u0940", "ta": "\u0ba4\u0b95\u0bb5\u0bb2\u0bcd", "gu": "\u0aae\u0abe\u0ab9\u0abf\u0aa4\u0ac0",
    "kn": "\u0cae\u0cbe\u0cb9\u0cbf\u0ca4\u0cbf", "ml": "\u0d35\u0d3f\u0d35\u0d30\u0d02", "pa": "\u0a1c\u0a3e\u0a23\u0a15\u0a3e\u0a30\u0a40",
    "or": "\u0b38\u0b42\u0b1a\u0b28\u0b3e",
}
_TOKEN_SPLIT = re.compile(r"(QZ\s*\d+\s*QZ)", re.I)
_LATIN_WORD = re.compile(r"[A-Za-z]+(?:['\u2019][A-Za-z]+)*")


def fake_translate(text, target):
    """English -> `target` the way a good provider would: words become native, protected tokens untouched."""
    if target == "en":
        return text
    word = NATIVE_WORD[target]
    return "".join(part if _TOKEN_SPLIT.fullmatch(part) else _LATIN_WORD.sub(word, part) for part in _TOKEN_SPLIT.split(text))


class FakeTranslator:
    def __init__(self, mode=None, detect="en", romanize=None):
        self.mode, self.detect, self.romanize = mode, detect, romanize
        self.calls = []                      # (kind, text, source, target)

    # -- API used by app.api.query
    def detect_language(self, text):
        self.calls.append(("detect", text, None, None))
        return self.detect(text) if callable(self.detect) else self.detect

    def translate_text_strict(self, text, source="auto", target="en"):
        self.calls.append(("strict", text, source, target))
        if self.mode == "raise":
            raise TranslationError("all_providers_failed", "fake")
        if self.mode == "echo":
            raise TranslationError("provider_returned_input_unchanged")
        if self.mode == "garbage":
            return object()
        if self.mode == "latin":
            return text
        out = fake_translate(text, target)
        if self.mode == "drop_token":
            out = _TOKEN_SPLIT.sub("", out, count=1)
        return out

    def translate_text(self, text, source="auto", target="en"):
        """Legacy non-raising API (question translation for retrieval)."""
        self.calls.append(("legacy", text, source, target))
        if not text or source == target:
            return text
        if target == "en":
            return getattr(self, "english_for", {}).get(text, text)
        return fake_translate(text, target) if self.mode not in ("raise", "echo") else text

    def transliterate_text(self, text, language):
        return self.romanize

    def kinds(self, kind):
        return [c for c in self.calls if c[0] == kind]


class FakeGroqClient:
    """OpenAI-compatible fake used by the multilingual API tests."""

    def __init__(self, reply="OK", exc=None):
        import types
        self.reply, self.exc, self.calls = reply, exc, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        import types
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])
