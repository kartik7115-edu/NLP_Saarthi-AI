"""Curated Hindi/Marathi answers (app/data/curated_answers.json): matching, response shape, and fact integrity."""
import json
import os
import re
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.api import query as query_api  # noqa: E402
from app.services import curated_answers, groq_client  # noqa: E402
from app.services.language_utils import detect_devanagari_language, script_ratio  # noqa: E402

client = TestClient(app)
ROOT = os.path.join(os.path.dirname(__file__), "..", "app", "data")
DATA = json.load(open(os.path.join(ROOT, "curated_answers.json"), encoding="utf-8"))
SCHEMES = {s["id"]: s for s in json.load(open(os.path.join(ROOT, "schemes.json"), encoding="utf-8"))["schemes"]}


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(settings, "demo_answers_enabled", True)
    query_api._rate_limit.clear()


def post(q, lang):
    def boom(**kw):
        raise AssertionError("Groq must not be called for a curated answer")
    with patch.object(groq_client, "chat", side_effect=boom), patch("app.api.query.brightdata_search", return_value=[]):
        return client.post("/query", json={"query": q, "language": lang, "occupation": "farmer"})


def test_there_are_ten_topics_each_in_hindi_and_marathi():
    assert len(DATA["topics"]) == 10
    for t in DATA["topics"]:
        assert detect_devanagari_language(t["hi"]["answer"]) == "hi", t["id"]
        assert detect_devanagari_language(t["mr"]["answer"]) == "mr", t["id"]
        assert all(sid in SCHEMES for sid in t["schemes"]), t["id"]


@pytest.mark.parametrize("topic", DATA["topics"], ids=lambda t: t["id"])
def test_every_number_and_url_in_the_answers_comes_from_the_scheme_records(topic):
    record_text = json.dumps([SCHEMES[s] for s in topic["schemes"]], ensure_ascii=False)
    record_digits = re.sub(r"[,\s]", "", record_text)
    for lang in ("hi", "mr"):
        ans = topic[lang]["answer"]
        for url in re.findall(r"https?://[^\s)]+", ans):
            assert url.rstrip("/.") in record_text, (topic["id"], lang, url)
        for num in re.findall(r"\d[\d,.\-]*\d|\d", re.sub(r"https?://\S+", "", ans)):
            assert re.sub(r",", "", num).strip(".-") in record_digits, (topic["id"], lang, num)
        for card in topic["schemes"]:
            desc = DATA["card_descriptions"][lang].get(card, "")
            for num in re.findall(r"\d[\d,.]*\d|\d", desc):
                assert re.sub(r",", "", num) in re.sub(r"[,\s]", "", json.dumps(SCHEMES[card])), (card, lang, num)


@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_the_demo_question_is_answered_in_its_language_without_groq(lang):
    q = DATA["topics"][0][lang]["questions"][0]
    r = post(q, lang)
    d = r.json()
    assert r.status_code == 200 and d["response_language"] == lang and d["intent"] == "eligibility_check"
    assert detect_devanagari_language(d["answer"]) == lang and script_ratio(d["answer"], "devanagari") > 0.7
    assert r.headers["X-Answer-Source"] == "curated"
    cards = d["recommended_schemes"]
    assert cards and all(c["type"] == "central" for c in cards)                    # no state scheme presented as a match
    assert all(script_ratio(c["description"], "devanagari") > 0.5 for c in cards)   # cards localized too
    assert d["doc_links"]
    body = json.dumps(d, ensure_ascii=False).lower()
    assert "curated" not in body and "hardcoded" not in body and "demo" not in body


def test_spelling_and_punctuation_variants_still_match():
    for q in ["मैं कैसे पता कर सकता हूं कि मैं कृषि सब्सिडी के लिए पात्र हूं या नहीं", "पीएम किसान योजना मे कितने पैसे मिलते है",
              "किसान क्रेडिट कार्ड कैसे बनवाये?"]:
        assert curated_answers.match(q), q


def test_a_marathi_question_gets_marathi_even_when_hindi_is_selected():
    r = post(DATA["topics"][1]["mr"]["questions"][0], "hi")
    assert r.json()["response_language"] == "mr" and detect_devanagari_language(r.json()["answer"]) == "mr"


@pytest.mark.parametrize("q", ["आज मौसम कैसा है?", "धान की MSP क्या है?", "What documents are required for PM-KISAN?",
                               "राशन कार्ड कैसे बनवाएं?"])
def test_other_questions_go_through_the_normal_pipeline(q):
    assert curated_answers.match(q) is None


def test_switch_off(monkeypatch):
    monkeypatch.setattr(settings, "demo_answers_enabled", False)
    assert curated_answers.match(DATA["topics"][0]["hi"]["questions"][0]) is None
