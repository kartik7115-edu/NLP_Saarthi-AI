"""Language codes and script-based language checks shared by the query pipeline.

Supported answer languages: en hi bn te mr ta gu kn ml pa or.

`is_text_in_language` replaces the old absolute-count heuristic (">20 native characters"), which called an
English answer with a few localized headings "already Hindi". It measures the *share* of letters that belong to
the target script, ignoring protected Latin tokens (scheme names, URLs, amounts) that legitimately stay Latin.
"""
import re
import unicodedata
from typing import Optional

SUPPORTED_LANGUAGES = ("en", "hi", "bn", "te", "mr", "ta", "gu", "kn", "ml", "pa", "or")

LANGUAGE_NAMES = {
    "en": "English", "hi": "Hindi", "bn": "Bengali", "te": "Telugu", "mr": "Marathi", "ta": "Tamil",
    "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia",
}

# (first, last) code point of each language's script block
SCRIPT_BLOCKS = {
    "latin": (0x0041, 0x024F),
    "devanagari": (0x0900, 0x097F),
    "bengali": (0x0980, 0x09FF),
    "gurmukhi": (0x0A00, 0x0A7F),
    "gujarati": (0x0A80, 0x0AFF),
    "odia": (0x0B00, 0x0B7F),
    "tamil": (0x0B80, 0x0BFF),
    "telugu": (0x0C00, 0x0C7F),
    "kannada": (0x0C80, 0x0CFF),
    "malayalam": (0x0D00, 0x0D7F),
}
LANGUAGE_SCRIPT = {
    "en": "latin", "hi": "devanagari", "mr": "devanagari", "bn": "bengali", "pa": "gurmukhi", "gu": "gujarati",
    "or": "odia", "ta": "tamil", "te": "telugu", "kn": "kannada", "ml": "malayalam",
}

# Share of letters (protected Latin tokens excluded by the caller) that must be in the target script.
MIN_NATIVE_RATIO = 0.45
MIN_LATIN_RATIO_FOR_ENGLISH = 0.7


def normalize_language(code) -> Optional[str]:
    """'auto' for missing/empty/auto, a supported two-letter code for known languages, None for anything else.

    Accepts 'HI', 'hi-IN', 'hi_IN'. Unsupported codes (for example 'xyz' or 'fr') return None so the caller
    can decide explicitly; they are never silently treated as a supported language.
    """
    if code is None:
        return "auto"
    s = str(code).strip().lower().replace("_", "-")
    if s in ("", "auto"):
        return "auto"
    primary = s.split("-")[0]
    return primary if primary in SUPPORTED_LANGUAGES else None


def _is_letter(ch: str) -> bool:
    # Indic vowel signs (matras) are category Mc/Mn, not alphabetic, but they belong to the word.
    return ch.isalpha() or unicodedata.category(ch) in ("Mn", "Mc")


def _script_of(ch: str) -> Optional[str]:
    cp = ord(ch)
    for name, (lo, hi) in SCRIPT_BLOCKS.items():
        if lo <= cp <= hi:
            return name
    return None


def letter_counts(text: str) -> dict:
    counts: dict = {}
    for ch in text or "":
        if not _is_letter(ch):
            continue
        counts[_script_of(ch) or "other"] = counts.get(_script_of(ch) or "other", 0) + 1
    return counts


def dominant_script(text: str) -> Optional[str]:
    """The script holding most letters of `text` (None when there are no letters)."""
    counts = letter_counts(text)
    return max(counts, key=counts.get) if counts else None


def script_ratio(text: str, script: str) -> float:
    counts = letter_counts(text)
    total = sum(counts.values())
    return counts.get(script, 0) / total if total else 0.0


def is_text_in_language(text: str, lang: str) -> bool:
    """True when `text` is plausibly written in `lang` (script-share check; Hindi and Marathi share a script)."""
    if not text or not text.strip():
        return False
    script = LANGUAGE_SCRIPT.get(lang)
    if script is None:
        return False
    counts = letter_counts(text)
    if sum(counts.values()) == 0:
        return lang == "en"
    ratio = script_ratio(text, script)
    return ratio >= (MIN_LATIN_RATIO_FOR_ENGLISH if lang == "en" else MIN_NATIVE_RATIO)


def source_language_for_query(query: str, answer_lang: str) -> str:
    """Language to translate the user's QUESTION from, to make it English for retrieval.

    The selected answer language says nothing about what the user typed: an English question with Hindi selected
    must not be translated "from Hindi". Latin script -> treated as English; a script matching the selected
    language -> that language; any other script -> 'auto' (let the provider detect it).
    """
    script = dominant_script(query)
    if script is None or script == "latin":
        return "en"
    if LANGUAGE_SCRIPT.get(answer_lang) == script:
        return answer_lang
    return "auto"
