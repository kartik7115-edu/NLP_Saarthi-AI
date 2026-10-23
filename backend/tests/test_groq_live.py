"""LIVE Groq tests: the 7 acceptance questions and 5 adversarial questions against the real API.

OPT-IN so a plain `pytest` never spends API calls: skipped unless RUN_LIVE_GROQ_TESTS=1, GROQ_API_KEY is
configured (environment or .env) and OFFLINE_ONLY is not true. Run with:
    RUN_LIVE_GROQ_TESTS=1 pytest tests/test_groq_live.py -v
They are marked `live` so tests/conftest.py does NOT null the key for them. Expect occasional wording
failures from a non-deterministic model; each failure message shows the regex and the answer.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.config import settings  # noqa: E402
from app.services.rag_engine import rag_engine  # noqa: E402
import groq_smoke_test as smoke  # noqa: E402

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_GROQ_TESTS") != "1" or not (settings.groq_api_key or "").strip() or settings.offline_only,
                       reason="live Groq tests are opt-in: set RUN_LIVE_GROQ_TESTS=1 with GROQ_API_KEY configured"),
]


@pytest.mark.parametrize("case", smoke.ACCEPTANCE + smoke.ADVERSARIAL, ids=lambda c: c[0])
def test_live_groq_answer_is_grounded(case):
    cid, question, must, must_not = case
    if not rag_engine._all_schemes:
        rag_engine.load_documents()
    res = rag_engine.generate_answer_with_meta("", question, retrieval=rag_engine.retrieve(question))
    assert res.source == "groq", f"Groq was not used: {res.fallback_reason}"
    failures = smoke.evaluate(res.answer, must, must_not)
    assert not failures, f"{failures}\n--- answer ---\n{res.answer}"
