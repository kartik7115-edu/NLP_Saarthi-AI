"""Reviewed answers for frequent farmer questions (app/data/faq_answers.json), in Hindi, Marathi and English.

A question that matches an entry is answered from it directly in the requested language - no LLM or translation
service is involved, so these answers are fast and always available. Every fact in the file comes from
app/data/schemes.json. Matching: after normalisation (case, punctuation, hyphens, nukta, chandrabindu), the question
must contain one phrase from EVERY group in the entry's "match" list and none of its "exclude" phrases. Specific
entries win over entries marked "generic"; among equals the one listed first wins.

Settings: FAQ_ANSWERS_ENABLED (default true), FAQ_ANSWER_LANGUAGES (default "hi,mr,en").
"""
import json
import os
import re
import unicodedata
from typing import Any, Dict, List, Optional

from app.config import settings

_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "faq_answers.json")
_entries: Optional[List[Dict[str, Any]]] = None
_PUNCT = re.compile(r"[\s\-_.,;:?!()\[\]{}\"'/\\|।॥–—]+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", str(text or "")).lower()
    t = t.replace("़", "").replace("ँ", "ं")        # nukta (ज़ -> ज), chandrabindu -> anusvara (हूँ -> हूं)
    return " " + _PUNCT.sub(" ", t).strip() + " "


def _load() -> List[Dict[str, Any]]:
    global _entries
    if _entries is None:
        try:
            with open(_PATH, encoding="utf-8") as f:
                data = json.load(f)
            entries = data.get("entries", [])
            for e in entries:
                e["_match"] = [[normalize(p).strip() for p in group] for group in e.get("match", [])]
                e["_exclude"] = [normalize(p).strip() for p in e.get("exclude", [])]
            _entries = entries
        except FileNotFoundError:
            _entries = []
    return _entries


def _languages() -> set:
    return {x.strip() for x in (settings.faq_answer_languages or "").split(",") if x.strip()}


def find(question: str, language: str) -> Optional[Dict[str, Any]]:
    """The best matching entry that has an answer in `language`, or None."""
    if not settings.faq_answers_enabled or language not in _languages():
        return None
    q = normalize(question)
    best, best_key = None, None
    for i, e in enumerate(_load()):
        if language not in (e.get("answers") or {}):
            continue
        if any(x and x in q for x in e["_exclude"]):
            continue
        if not e["_match"] or not all(any(p and p in q for p in group) for group in e["_match"]):
            continue
        key = (0 if e.get("generic") else 1, len(e["_match"]), -i)
        if best_key is None or key > best_key:
            best, best_key = e, key
    return best


def _strip_md(line: str) -> str:
    line = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", line)        # list marker only - never digits that are content
    return re.sub(r"\*\*(.+?)\*\*", r"\1", line).strip()


def card_description(entry: Dict[str, Any], language: str, scheme: Dict[str, Any]) -> str:
    """Short description for a scheme card, in `language`: the answer line about that scheme, else the first line."""
    answer = entry["answers"][language]
    names = [scheme.get("name", "")] + list(scheme.get("aliases") or [])
    lines = [l for l in answer.split("\n") if l.strip()]
    for l in lines:
        if any(n and f"**{n}" in l for n in names) and len(lines) > 1 and l != lines[0]:
            # "- **PM-KISAN**: text" -> "text" (the card already shows the scheme name)
            return _strip_md(re.sub(r"^\s*[-•]?\s*\*\*.+?\*\*(?:\s*\([^)]*\))?\s*:\s*", "", l)) or _strip_md(l)
    first = _strip_md(lines[0]) if lines else ""
    return first
