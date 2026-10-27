"""LIVE multilingual end-to-end tests: real Groq (GROQ_MODEL from .env) + real translation providers.

OPT-IN, like tests/test_groq_live.py: skipped unless RUN_LIVE_GROQ_TESTS=1, GROQ_API_KEY is configured and OFFLINE_ONLY
is not true. Run from backend/:
    RUN_LIVE_GROQ_TESTS=1 python -m pytest tests/test_multilingual_live.py -v -s
Each test prints the answer and the language headers so a human can judge how natural the Hindi / Marathi is.
A pass means: the answer came from Groq, is in the requested language (script + Hindi/Marathi check), keeps the scheme
names, and the metadata matches. It does NOT certify translation quality - read the printed answers.
"""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services.language_utils import detect_devanagari_language, script_ratio  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_GROQ_TESTS") != "1" or not (settings.groq_api_key or "").strip() or settings.offline_only,
                       reason="live tests are opt-in: set RUN_LIVE_GROQ_TESTS=1 with GROQ_API_KEY configured"),
]

CASES = [
    ("en", "How can I check whether I am eligible for an agricultural subsidy?"),
    ("hi", "मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?"),
    ("mr", "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?"),
]


@pytest.mark.parametrize("lang,question", CASES, ids=[c[0] for c in CASES])
def test_live_answer_language(lang, question):
    from app.api.query import _rate_limit
    _rate_limit.clear()
    r = TestClient(app).post("/query", json={"query": question, "language": lang, "occupation": "farmer"})
    d = r.json()
    print(f"\n--- {lang} | source={r.headers.get('X-Answer-Source')} query_translation={r.headers.get('X-Query-Translation')} "
          f"translation={r.headers.get('X-Translation-Status')} provider={r.headers.get('X-Translation-Provider')} "
          f"attempts={r.headers.get('X-Translation-Attempts')}\n{d['answer']}\ncards={[c['name'] for c in d['recommended_schemes']]}")
    assert r.status_code == 200
    assert r.headers.get("X-Answer-Source") == "groq", "Groq was not used (see X-Answer-Source / server log)"
    assert d["intent"] == "eligibility_check" and d["recommended_schemes"]
    assert all(c["type"] != "state" for c in d["recommended_schemes"])          # state unknown: no state scheme as a match
    assert d["response_language"] == lang == r.headers["X-Answer-Language"]
    if lang != "en":
        assert d["translation_status"] == "translated"
        assert script_ratio(d["answer"], "devanagari") > 0.5
        assert detect_devanagari_language(d["answer"]) in (lang, None)
