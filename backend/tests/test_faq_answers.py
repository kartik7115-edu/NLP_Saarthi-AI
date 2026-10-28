"""FAQ answer bank (app/data/faq_answers.json): matching, language, response shape, and facts vs the dataset."""
import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.api import query as query_api
from app.services import faq_answers, groq_client
from app.services.language_utils import detect_devanagari_language, script_ratio

pytestmark = pytest.mark.faq
client = TestClient(app)
DATA = Path(__file__).resolve().parents[1] / "app" / "data"
ENTRIES = json.loads((DATA / "faq_answers.json").read_text(encoding="utf-8"))["entries"]
SCHEMES = {s["id"]: s for s in json.loads((DATA / "schemes.json").read_text(encoding="utf-8"))["schemes"]}

QUESTIONS = [
    ("hi", "मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?", "subsidy-eligibility"),
    ("mr", "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?", "subsidy-eligibility"),
    ("hi", "पीएम किसान योजना के लिए कौन से दस्तावेज चाहिए?", "pm-kisan-documents"),
    ("mr", "पीएम किसान योजनेसाठी कोणती कागदपत्रे लागतात?", "pm-kisan-documents"),
    ("hi", "पीएम किसान के लिए आवेदन कैसे करें?", "pm-kisan-apply"),
    ("mr", "पीएम किसान योजनेसाठी अर्ज कसा करावा?", "pm-kisan-apply"),
    ("hi", "पीएम किसान योजना क्या है?", "pm-kisan-about"),
    ("hi", "किसान क्रेडिट कार्ड कैसे बनवाएं?", "kcc"),
    ("mr", "पीक विमा योजनेचे फायदे काय आहेत?", "pmfby"),
    ("hi", "मृदा स्वास्थ्य कार्ड कैसे मिलेगा?", "soil-health-card"),
    ("mr", "सौर पंपासाठी अनुदान मिळेल का?", "pm-kusum"),
    ("hi", "किसानों के लिए पेंशन योजना कौन सी है?", "pm-kisan-maan-dhan"),
    ("hi", "पीएम किसान मानधन योजना क्या है?", "pm-kisan-maan-dhan"),
    ("mr", "ठिबक सिंचनासाठी अनुदान किती मिळते?", "micro-irrigation"),
    ("en", "How can I check whether I am eligible for an agricultural subsidy?", "subsidy-eligibility"),
]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(settings, "faq_answers_enabled", True)
    query_api._rate_limit.clear()


@pytest.mark.parametrize("lang,q,entry", QUESTIONS)
def test_questions_route_to_the_right_entry(lang, q, entry):
    assert faq_answers.find(q, lang)["id"] == entry


@pytest.mark.parametrize("q", ["आज मौसम कैसा है?", "मंडी में गेहूं का भाव क्या है?", "नमस्ते"])
def test_unrelated_questions_go_to_the_normal_pipeline(q):
    assert faq_answers.find(q, "hi") is None


@pytest.mark.parametrize("lang,q,entry", [x for x in QUESTIONS if x[0] != "en"])
def test_api_answers_in_the_requested_language_without_any_llm_or_translator(lang, q, entry, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(groq_client, "_make_client", lambda: (_ for _ in ()).throw(AssertionError("no Groq call")))
    with patch("app.api.query.translator_service") as tr:
        r = client.post("/query", json={"query": q, "language": lang, "occupation": "farmer"})
        assert not tr.method_calls
    d = r.json()
    assert r.status_code == 200 and d["response_language"] == lang and d["requested_language"] == lang
    assert d["translation_status"] == "not_needed" and r.headers["X-Answer-Language"] == lang
    assert script_ratio(d["answer"].replace("**", ""), "devanagari") > 0.5
    assert detect_devanagari_language(d["answer"]) == lang
    assert d["recommended_schemes"] and all(c["description"] for c in d["recommended_schemes"])
    assert all(c["type"] == "central" for c in d["recommended_schemes"])
    text = re.sub(r"https?://\S+", " ", json.dumps(d, ensure_ascii=False)).lower()   # official URLs may contain "faq"
    for word in ("hardcoded", "hard-coded", "faq", "canned", "prepared", "curated", "pre-written"):
        assert word not in text


def test_marathi_selected_with_a_hindi_question_answers_in_marathi():
    r = client.post("/query", json={"query": "पीएम किसान के लिए आवेदन कैसे करें?", "language": "mr"})
    assert r.json()["response_language"] == "mr" and detect_devanagari_language(r.json()["answer"]) == "mr"


def test_disabled_bank_falls_back_to_the_pipeline(monkeypatch):
    monkeypatch.setattr(settings, "faq_answers_enabled", False)
    assert faq_answers.find(QUESTIONS[0][1], "hi") is None


# ------------------------------------------------------------------ facts must come from schemes.json
_FACT = re.compile(r"Rs\. ?[\d,]+(?: lakh)?|\d+(?:\.\d+)?%|https?://\S+|\b1800-\d{3}-\d{3,4}\b|\b\d{5,6}\b|\b\d+(?:\.\d+)? (?:HP|hectares?|हेक्टेयर|हेक्टर)")


def _dataset_text(ids):
    return " ".join(json.dumps(SCHEMES[i], ensure_ascii=False) for i in ids)


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["id"] for e in ENTRIES])
def test_every_amount_rate_url_and_phone_number_is_in_the_dataset(entry):
    src = _dataset_text(entry["schemes"])
    for lang, ans in entry["answers"].items():
        for fact in _FACT.findall(ans):
            core = fact.rstrip(".,;)")
            core = re.sub(r" (हेक्टेयर|हेक्टर)$", " hectare", core)
            probe = core.split(" ")[0] if core.endswith(("HP", "hectare", "hectares")) else core
            assert probe.replace("Rs. ", "Rs. ") in src or probe.replace("Rs.", "Rs. ") in src or probe in src, (entry["id"], lang, fact)


@pytest.mark.parametrize("entry", ENTRIES, ids=[e["id"] for e in ENTRIES])
def test_the_three_languages_carry_the_same_facts(entry):
    facts = {lang: sorted(re.sub(r" (HP|hectares?|हेक्टेयर|हेक्टर)$", "", f.rstrip(".,;)")) for f in _FACT.findall(a))
             for lang, a in entry["answers"].items()}
    assert facts["hi"] == facts["mr"] == facts["en"], (entry["id"], facts)
    for lang in ("hi", "mr"):
        assert detect_devanagari_language(entry["answers"][lang]) == lang, (entry["id"], lang)
