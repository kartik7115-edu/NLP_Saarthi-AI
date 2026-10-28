"""Translate a grounded English answer into the user's language WITHOUT changing the facts in it.

Machine translation is not allowed to touch:
  - official scheme names and their aliases (taken from the retrieved records), acronyms such as PM-KISAN / PMFBY
  - URLs and e-mail addresses
  - numbers: rupee amounts, percentages, dates, helpline numbers, hectares, years ...

Those spans are replaced by opaque tokens (QZ<n>QZ) before translation and restored afterwards. The result is
only accepted if every token came back exactly once and the rest of the text is really in the target script.
Otherwise the English answer is returned with language="en" and status="failed": a failed translation is
reported explicitly and is never labelled as a successful localized answer.
"""
import re
from dataclasses import dataclass
from dataclasses import field
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

from app.services.language_utils import (
    MIN_NATIVE_RATIO, is_text_in_language, letter_counts, script_ratio, LANGUAGE_SCRIPT, wrong_devanagari_language,
)

TOKEN_FMT = "QZ{}QZ"
# Providers sometimes write the placeholder digits in Devanagari (QZ३QZ) or with spaces: both still count.
_TOKEN_RE = re.compile(r"QZ\s*([0-9\u0966-\u096F]+)\s*QZ", re.I)
_DEV_DIGITS = {ord(c): str(i) for i, c in enumerate("\u0966\u0967\u0968\u0969\u096a\u096b\u096c\u096d\u096e\u096f")}


def _token_index(m) -> int:
    return int(m.group(1).translate(_DEV_DIGITS))

_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]\"']+"
    r"|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:gov\.in|nic\.in|gov|nic|org|com|net|co\.in|in)\b(?:/[^\s<>()\[\]\"']*)?", re.I)
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
# optional currency prefix, digits with , . - / separators, optional percent sign  (6,000 | Rs. 2,000 | 31.03.2026 | 1800-115-526 | 7.5 | 3%)
_NUMBER_RE = re.compile(r"(?:(?:Rs\.?|INR|\u20b9)\s?)?\d+(?:[,.\-/]\d+)*(?:\s?%)?")
# acronyms and brand-like tokens: PMFBY, KCC, PM-KISAN, e-NAM, eNAM, MIDH, CGTMSE, PACS ...
_ACRONYM_RE = re.compile(r"\b(?:[A-Z]{2,}[A-Za-z0-9]*(?:-[A-Za-z0-9]+)*|e-?NAM)\b")
_LIST_MARKER_RE = re.compile(r"^\s*\d+[.)]\s", re.M)


@dataclass
class LocalizationResult:
    text: str                    # the answer to show
    language: str                # language `text` is actually written in
    status: str                  # "not_needed" | "translated" | "failed"
    reason: Optional[str] = None  # short safe failure code when status == "failed"
    provider: Optional[str] = None                       # which provider produced an accepted translation
    attempts: List[str] = field(default_factory=list)    # "<provider>:<reason>" for every rejected attempt


def _latin_terms(terms: Iterable[str]) -> List[str]:
    out, seen = [], set()
    for t in terms or []:
        t = (t or "").strip()
        if len(t) >= 3 and t.lower() not in seen and sum(letter_counts(t).values()) == letter_counts(t).get("latin", 0):
            seen.add(t.lower())
            out.append(t)
    out.sort(key=len, reverse=True)
    return out


def protect_spans(text: str, terms: Iterable[str] = ()) -> Tuple[str, List[str]]:
    """Replace protected spans with QZ<n>QZ tokens. Returns (masked_text, originals_in_token_order)."""
    spans: List[Tuple[int, int]] = []

    def overlaps(a: int, b: int) -> bool:
        return any(not (b <= s or a >= e) for s, e in spans)

    def take(regex: "re.Pattern", skip_list_markers: bool = False) -> None:
        markers = [(m.start(), m.end() - 1) for m in _LIST_MARKER_RE.finditer(text)] if skip_list_markers else []
        for m in regex.finditer(text):
            a, b = m.start(), m.end()
            if a == b or overlaps(a, b):
                continue
            if any(s <= a < e for s, e in markers):          # "1. " / "2) " at the start of a line is layout, not a fact
                continue
            spans.append((a, b))

    take(_EMAIL_RE)                                            # 1. e-mail addresses (they contain a domain, so before URLs)
    take(_URL_RE)                                              # 2. URLs, before names, so a scheme alias inside a URL cannot split it
    for term in _latin_terms(terms):                          # 3. official names / aliases of the retrieved schemes
        take(re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", re.I))
    take(_NUMBER_RE, skip_list_markers=True)                   # 4. amounts, dates, percentages, phone numbers
    take(_ACRONYM_RE)                                          # 5. acronyms
    spans.sort()
    originals: List[str] = []
    out, pos = [], 0
    for a, b in spans:
        out.append(text[pos:a])
        out.append(TOKEN_FMT.format(len(originals)))
        originals.append(text[a:b])
        pos = b
    out.append(text[pos:])
    return "".join(out), originals


def restore_spans(translated: str, originals: List[str]) -> Optional[str]:
    """Put the original spans back. Returns None unless every token appears exactly once."""
    found = [_token_index(m) for m in _TOKEN_RE.finditer(translated)]
    if sorted(found) != list(range(len(originals))):
        return None
    return _TOKEN_RE.sub(lambda m: originals[_token_index(m)], translated)


def localize_answer(english_text: str, target_lang: str, terms: Iterable[str],
                    translate_fn: Callable[[str, str, str], str]) -> LocalizationResult:
    """translate_fn(text, source, target) must RAISE on failure (translator_service.translate_text_strict)."""
    if not english_text or target_lang == "en":
        return LocalizationResult(english_text or "", "en", "not_needed")
    masked, originals = protect_spans(english_text, terms)
    try:
        translated = translate_fn(masked, "en", target_lang)
    except Exception as e:                                     # TranslationError or anything a provider/mocks raise
        return LocalizationResult(english_text, "en", "failed", getattr(e, "code", type(e).__name__))
    if not isinstance(translated, str) or not translated.strip():
        return LocalizationResult(english_text, "en", "failed", "invalid_provider_result")
    restored = restore_spans(translated, originals)
    if restored is None:
        return LocalizationResult(english_text, "en", "failed", "protected_term_lost")
    # Judge the language on the text without the protected (Latin) spans, so names and numbers cannot skew it.
    prose = _TOKEN_RE.sub(" ", translated)
    counts = letter_counts(prose)
    if sum(counts.values()) >= 8 and script_ratio(prose, LANGUAGE_SCRIPT[target_lang]) < MIN_NATIVE_RATIO:
        return LocalizationResult(english_text, "en", "failed", "result_not_in_target_language")
    # Hindi and Marathi share Devanagari: the script check above cannot tell them apart, so check the words too.
    if wrong_devanagari_language(prose, target_lang):
        return LocalizationResult(english_text, "en", "failed", "result_in_wrong_language")
    if restored.strip() == english_text.strip():
        return LocalizationResult(english_text, "en", "failed", "provider_returned_input_unchanged")
    return LocalizationResult(restored, target_lang, "translated")


def native_answer_problem(text: str, lang: str, terms: Iterable[str] = ()) -> Optional[str]:
    """None when `text` really is written in `lang` (scheme names, URLs and numbers may stay Latin), else a reason."""
    if not text or not text.strip():
        return "empty"
    if lang == "en" or lang not in LANGUAGE_SCRIPT:
        return None
    masked, _ = protect_spans(text, terms)
    prose = _TOKEN_RE.sub(" ", masked)
    if sum(letter_counts(prose).values()) < 8 or script_ratio(prose, LANGUAGE_SCRIPT[lang]) < MIN_NATIVE_RATIO:
        return "not_in_target_language"
    if wrong_devanagari_language(prose, lang):
        return "wrong_language"
    return None


TranslateFn = Callable[[str, str, str], str]


def localize_with_fallbacks(english_text: str, target_lang: str, terms: Iterable[str],
                            providers: Sequence[Tuple[str, TranslateFn]]) -> LocalizationResult:
    """Try each (name, translate_fn) in order; the first translation that passes every check in localize_answer wins.

    Fallback policy when every provider fails: the ENGLISH text is returned with language="en", status="failed",
    reason = the last failure and `attempts` listing every provider's failure. It is never labelled as localized.
    """
    terms = list(terms or [])
    if not english_text or target_lang == "en":
        return LocalizationResult(english_text or "", "en", "not_needed")
    attempts: List[str] = []
    last: Optional[LocalizationResult] = None
    for name, fn in providers:
        result = localize_answer(english_text, target_lang, terms, fn)
        if result.status == "translated":
            result.provider, result.attempts = name, attempts
            return result
        attempts.append(f"{name}:{result.reason}")
        last = result
    if last is None:
        return LocalizationResult(english_text, "en", "failed", "no_translation_provider", attempts=attempts)
    last.attempts = attempts
    return last
