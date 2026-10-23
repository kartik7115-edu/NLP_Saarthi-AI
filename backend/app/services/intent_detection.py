"""Intent detection (token / phrase based, no substring matching).

Public API is unchanged: detect_intent(query) -> one of
price_query, helpline_query, document_requirements, application_process,
eligibility_check, benefits_query, scheme_search, general_information.

Changes from the original keyword matcher:
- cues match whole tokens (or token sequences), never substrings ("call" no longer matches "called",
  "plan" no longer matches "explain");
- every intent is scored (strong cue = 3, weak cue = 1) instead of "first keyword wins";
- a listing question ("What schemes ... can I apply for?") is a scheme search, not an application question;
- an optional has_named_scheme flag lets callers say a specific scheme was already identified.
Non-Latin keywords match as token prefixes so inflected Hindi/Tamil forms still work.
"""
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

_SEP = re.compile(r"[\s\-_.,;:?!()\[\]{}\"'/\\|।–—]+")


def tokenize(text: str) -> List[str]:
    text = unicodedata.normalize("NFC", str(text or "")).lower()
    return [t for t in _SEP.sub(" ", text).split(" ") if t]


def stem(token: str) -> str:
    """Tiny English plural stemmer; non-ASCII tokens are returned unchanged."""
    if not token.isascii():
        return token
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _cue(text: str) -> Tuple[Tuple[str, ...], bool]:
    toks = tokenize(text)
    ascii_cue = all(t.isascii() for t in toks)
    return tuple(stem(t) if ascii_cue else t for t in toks), ascii_cue


# (strong cues, weak cues) per intent. Strong = 3 points, weak = 1 point.
_CUES: Dict[str, Tuple[List[str], List[str]]] = {
    "price_query": (
        ["market price", "crop price", "mandi price", "mandi rate", "price today", "rate today", "selling price",
         "bhav", "daam", "aaj ka bhav", "aaj ka daam", "bazaar", "बाजार भाव", "मंडी भाव"],
        ["mandi", "मंडी", "price"]),
    "helpline_query": (
        ["helpline", "toll free", "phone number", "contact number", "helpdesk", "grievance", "complaint",
         "shikayat", "customer care", "call center", "call centre"],
        ["call", "phone", "contact", "number"]),
    "document_requirements": (
        ["document", "doc", "paperwork", "paper", "kagaz", "kaagaz", "dastavez", "दस्तावेज", "ஆவண"],
        ["required", "requirement", "what do i need", "need to submit", "need to bring"]),
    "application_process": (
        ["how to apply", "how do i apply", "how can i apply", "apply online", "apply for", "register",
         "registration", "enroll", "enrol", "enrolment", "enrollment", "sign up", "application process",
         "application form", "avedan", "aavedan", "आवेदन", "kaise apply", "apply kaise"],
        ["apply", "application", "applying"]),
    "eligibility_check": (
        ["eligible", "eligibility", "who can", "qualify", "patrata", "पात्र", "yogya", "தகுதி", "koun", "arugu"],
        ["criteria", "who gets"]),
    "benefits_query": (
        ["benefit", "what will i get", "what do i get", "what i get", "kya milega", "fayde", "labh", "लाभ",
         "நன்மை", "how much money", "how much subsidy", "how much amount", "how much does", "how much will",
         "how much is", "amount", "installment", "instalment", "subsidy"],
        ["how much", "money", "assistance", "provide", "give", "pay"]),
    "scheme_search": (
        [],
        ["scheme", "yojana", "yojna", "plan", "thittam", "योजना", "திட்டம்", "programme", "program"]),
}
# tie-break order (same precedence the original matcher used)
_ORDER = ["price_query", "helpline_query", "document_requirements", "application_process",
          "eligibility_check", "benefits_query", "scheme_search"]

_COMPILED = {
    intent: ([_cue(c) for c in strong], [_cue(c) for c in weak])
    for intent, (strong, weak) in _CUES.items()
}

_LISTING_WORDS = {"which", "any", "list", "recommend", "suggest"}


def _contains(tokens: List[str], stems: List[str], cue: Tuple[Tuple[str, ...], bool]) -> bool:
    seq, ascii_cue = cue
    if not seq:
        return False
    n = len(seq)
    for i in range(len(tokens) - n + 1):
        ok = True
        for j in range(n):
            if ascii_cue:
                if stems[i + j] != seq[j]:
                    ok = False
                    break
            elif not tokens[i + j].startswith(seq[j]):
                ok = False
                break
        if ok:
            return True
    return False


def is_listing_question(tokens: List[str]) -> bool:
    """'What schemes ...', 'which scheme ...', 'schemes available ...' - asks for a list of schemes."""
    if "schemes" in tokens or "yojanas" in tokens:
        return True
    return bool(("scheme" in tokens or "yojana" in tokens) and set(tokens) & _LISTING_WORDS)


def score_intents(query: str, has_named_scheme: bool = False) -> Dict[str, int]:
    tokens = tokenize(query)
    stems = [stem(t) for t in tokens]
    scores: Dict[str, int] = {}
    for intent in _ORDER:
        strong, weak = _COMPILED[intent]
        s = sum(3 for c in strong if _contains(tokens, stems, c)) + sum(1 for c in weak if _contains(tokens, stems, c))
        if s:
            scores[intent] = s
    if not has_named_scheme and is_listing_question(tokens):
        scores["scheme_search"] = scores.get("scheme_search", 0) + 4
    return scores


def detect_intent(query: str, has_named_scheme: bool = False) -> str:
    scores = score_intents(query, has_named_scheme)
    if not scores:
        return "general_information"
    best = max(scores.values())
    for intent in _ORDER:
        if scores.get(intent) == best:
            return intent
    return "general_information"
