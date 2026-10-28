"""Curated Hindi / Marathi answers for frequent questions (backend/app/data/curated_answers.json).

A question that matches one of the listed phrasings (after normalising spelling variants and punctuation, with a
small tolerance for typing differences) is answered with the prepared text in the language the question is written in.
Every fact in the file is taken from schemes.json. Responses carry the debug header X-Answer-Source: curated and a
server log line; nothing is shown in the UI. Disable with DEMO_ANSWERS_ENABLED=false.
"""
import json
import logging
import os
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any, Dict, Optional

from app.config import settings
from app.services.language_utils import detect_devanagari_language

logger = logging.getLogger(__name__)

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MIN_SIMILARITY = 0.86
_data: Optional[Dict[str, Any]] = None
_data_path: Optional[str] = None


def _path() -> str:
    p = (settings.demo_answers_file or "").strip()
    return p if os.path.isabs(p) else os.path.join(_BACKEND_DIR, p)


def _load() -> Dict[str, Any]:
    global _data, _data_path
    path = _path()
    if _data is None or _data_path != path:
        try:
            with open(path, encoding="utf-8") as f:
                _data = json.load(f)
        except Exception as e:                     # a missing/broken file just disables curated answers
            logger.warning("Curated answers unavailable (%s)", type(e).__name__)
            _data = {"topics": [], "card_descriptions": {}}
        _data_path = path
    return _data


_PUNCT = re.compile(r"[\s\?\.\!,।॥:;\"'()\-–—]+")


def normalize(text: str) -> str:
    t = unicodedata.normalize("NFC", str(text or "")).lower()
    t = t.replace("़", "")                     # nukta: दस्तावेज़ = दस्तावेज
    t = t.replace("ँ", "ं")               # chandrabindu -> anusvara: हूँ = हूं
    t = re.sub("[​-‍﻿]", "", t)
    return _PUNCT.sub(" ", t).strip()


def match(question: str) -> Optional[Dict[str, Any]]:
    """{'topic', 'lang', 'answer', 'score'} for the best curated match, or None."""
    if not settings.demo_answers_enabled:
        return None
    q = normalize(question)
    if not q or not re.search(r"[ऀ-ॿ]", q):
        return None
    best = None
    for topic in _load().get("topics", []):
        for lang in ("hi", "mr"):
            for variant in topic.get(lang, {}).get("questions", []):
                v = normalize(variant)
                score = 1.0 if v == q else SequenceMatcher(None, q, v).ratio()
                if best is None or score > best["score"]:
                    best = {"topic": topic, "lang": lang, "answer": topic[lang]["answer"], "score": score}
    if not best or best["score"] < MIN_SIMILARITY:
        return None
    # the answer language is the language the question is written in
    # (a near-exact match already tells us the language; the word-based guess only decides looser matches)
    guess = detect_devanagari_language(question) if best["score"] < 0.95 else None
    if guess in ("hi", "mr") and guess != best["lang"] and guess in best["topic"]:
        best["lang"], best["answer"] = guess, best["topic"][guess]["answer"]
    return best


def card_description(scheme_id: str, lang: str) -> Optional[str]:
    return (_load().get("card_descriptions", {}).get(lang) or {}).get(scheme_id)
