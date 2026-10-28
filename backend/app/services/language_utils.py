"""Language codes and script-based language checks shared by the query pipeline.

Supported answer languages: en hi bn te mr ta gu kn ml pa or.

`is_text_in_language` replaces the old absolute-count heuristic (">20 native characters"), which called an
English answer with a few localized headings "already Hindi". It measures the *share* of letters that belong to
the target script, ignoring protected Latin tokens (scheme names, URLs, amounts) that legitimately stay Latin.
"""
import re
import unicodedata
from typing import Optional, Tuple

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

    Devanagari is shared by Hindi and Marathi, so the script alone cannot say which one was typed: a Marathi question
    with Hindi selected (or the reverse) is identified lexically and translated from the language it is really in.
    """
    script = dominant_script(query)
    if script is None or script == "latin":
        return "en"
    if script == "devanagari":
        guess = detect_devanagari_language(query)
        if guess:
            return guess
    if LANGUAGE_SCRIPT.get(answer_lang) == script:
        return answer_lang
    return "auto"


# ----------------------------------------------------------------------------- Hindi vs Marathi
# Hindi and Marathi share the Devanagari script, so a script check can never tell them apart. These are frequent
# function words / inflections that occur in only one of the two languages (words common to both are left out).
HI_MARKERS = frozenset("""है हैं था थे थी के में और लिए नहीं यह वह से को पर हूँ हूं आप आपको आपके सकते सकता सकती
चाहिए जाता जाती जाते गया गई भी कौन क्या कैसे अगर लेकिन तक साथ द्वारा वाले वाली किया करें करना होगा होती होता""".split())
MR_MARKERS = frozenset("""आहे आहेत आणि नाही मध्ये साठी तुम्ही तुमच्या तुम्हाला तुमचे तुमची आपण करा करू शकता शकतो
शकते किंवा असे असेल असतो असते होते केले जाते मिळते मिळेल मिळतात पण कसे काय कोणत्या कोणते द्वारे आहोत नसेल""".split())
HI_SUFFIXES = ("ों",)                                         # oblique plural: किसानों, योजनाओं
MR_SUFFIXES = ("च्या", "साठी", "मध्ये", "ांना", "ांची", "ांचे", "ाचा", "ाची", "ाचे", "ाला")   # शेतकऱ्यांसाठी, योजनेच्या
_DEV_WORD = re.compile(r"[ऀ-ॿ]+")

try:                                                           # statistical n-gram detector already used by translator.py
    from langdetect import DetectorFactory as _DetectorFactory, detect_langs as _detect_langs
    _DetectorFactory.seed = 0                                  # langdetect is random unless seeded
except Exception:                                              # pragma: no cover - langdetect is a hard dependency
    _detect_langs = None


def devanagari_marker_counts(text: str) -> Tuple[int, int]:
    """(hindi_hits, marathi_hits) for the Devanagari words of `text`."""
    hi = mr = 0
    for w in _DEV_WORD.findall(text or ""):
        if w in HI_MARKERS or (len(w) > 3 and w.endswith(HI_SUFFIXES)):
            hi += 1
        if w in MR_MARKERS or (len(w) > 4 and w.endswith(MR_SUFFIXES)):
            mr += 1
    return hi, mr


def detect_devanagari_language(text: str) -> Optional[str]:
    """'hi' or 'mr' for Devanagari text, None when the text does not say clearly enough.

    1. language-specific function words / inflections (decisive when one side clearly dominates);
    2. otherwise the langdetect n-gram model, only with enough distinct words and a confident result.
    """
    words = _DEV_WORD.findall(text or "")
    if len(words) < 2:
        return None
    hi, mr = devanagari_marker_counts(text)
    if hi >= 2 and hi >= 2 * max(mr, 1):
        return "hi"
    if mr >= 2 and mr >= 2 * max(hi, 1):
        return "mr"
    # one language-specific word and none of the other language: trust it over the statistical model, which is
    # unreliable on short questions (it called "पीएम किसान योजनेत किती पैसे मिळतात?" Hindi)
    if mr >= 1 and hi == 0:
        return "mr"
    if hi >= 1 and mr == 0:
        return "hi"
    if _detect_langs is not None and len(set(words)) >= 4:
        try:
            probs = {p.lang: p.prob for p in _detect_langs(" ".join(words))}
        except Exception:                                      # langdetect raises on text it cannot handle
            probs = {}
        best = max(("hi", "mr"), key=lambda l: probs.get(l, 0.0))
        if probs.get(best, 0.0) >= 0.8:
            return best
    if hi > mr:
        return "hi"
    if mr > hi:
        return "mr"
    return None


def wrong_devanagari_language(text: str, lang: str) -> bool:
    """True when `text` is supposed to be Hindi/Marathi but is clearly the OTHER of the two languages."""
    if lang not in ("hi", "mr"):
        return False
    guess = detect_devanagari_language(text)
    return guess is not None and guess != lang
