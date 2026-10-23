"""Stage 4: Groq integration + intent-aware grounded answering.

No test here touches the network: the Groq client is replaced by a fake OpenAI-compatible client.
The tests check (1) what the model is shown, (2) what the system does with what the model returns,
(3) the offline fallback, and (4) that the API key never leaks. Tests that call the real Groq API live in
tests/test_groq_live.py and are skipped unless GROQ_API_KEY is configured.
"""
import logging
import os
import re
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import groq_client, rag_engine as rag_module  # noqa: E402
from app.services.grounded_answer import (  # noqa: E402
    GROUNDING_SYSTEM_PROMPT, GroundedContext, build_grounded_context, build_messages, check_answer_grounding,
    clean_caveats, render_offline_answer,
)
from app.services.rag_engine import rag_engine  # noqa: E402

client = TestClient(app)
FAKE_KEY = "gsk_TESTKEY_abcdef0123456789SECRET"

SEVEN = [
    "What documents are required for PM-KISAN?",
    "What benefits does PM-KISAN provide?",
    "Who is eligible for PMFBY?",
    "What documents are required for KCC?",
    "What schemes are available for cotton farmers in Maharashtra?",
    "I am a farmer in Maharashtra. What schemes can I apply for?",
    "What irrigation schemes are available for farmers?",
]


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    if not rag_engine._all_schemes:
        rag_engine.load_documents()


@pytest.fixture(autouse=True)
def _clean_cache():
    rag_module._answer_cache.clear()
    yield
    rag_module._answer_cache.clear()


def ctx(query, **kw):
    r = rag_engine.retrieve(query, **kw)
    return r, build_grounded_context(r)


class FakeClient:
    """Minimal OpenAI-compatible client: records every request, returns a scripted reply or raises."""

    def __init__(self, reply="OK", exc=None):
        self.reply, self.exc, self.calls = reply, exc, []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=self.reply))])


@pytest.fixture
def groq(monkeypatch):
    """Configure a fake Groq; returns a function to install a FakeClient."""
    monkeypatch.setattr(settings, "groq_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "offline_only", False)

    def install(**kw):
        fake = FakeClient(**kw)
        monkeypatch.setattr(groq_client, "_make_client", lambda: fake)
        return fake
    return install


# ======================================================================= 1. intent-aware context
@pytest.mark.parametrize("query,field,must_contain", [
    ("What documents are required for PM-KISAN?", "DOCUMENTS", ["Aadhaar", "Land records"]),
    ("What benefits does PM-KISAN provide?", "BENEFITS", ["6,000"]),
    ("Who is eligible for PMFBY?", "ELIGIBILITY", ["notified", "voluntary"]),
    ("What documents are required for KCC?", "DOCUMENTS", ["Land ownership documents"]),
    ("How do I apply for KCC?", "APPLICATION PROCESS", ["bank branch"]),
])
def test_answer_field_is_marked_and_comes_before_other_information(query, field, must_contain):
    r, g = ctx(query)
    assert g.text.count(">>> ANSWER FIELD") == len(r.results) == 1
    head, _, rest = g.text.partition(f">>> ANSWER FIELD - {field}")
    assert rest, f"answer field {field} missing"
    block, _, other = rest.partition("Other recorded information")
    for s in must_contain:
        assert s in block
    assert "Other recorded information (reference only" in g.text
    assert g.text.index(">>> ANSWER FIELD") < g.text.index("Other recorded information")


def test_named_scheme_context_contains_no_other_scheme():
    for q in SEVEN[:4]:
        _, g = ctx(q)
        assert g.text.count("RECORD 1 of 1") == 1
        for other in ("PM CARES", "Horticulture", "Producer Organi", "Namo Shetkari", "KUSUM", "e-NAM"):
            assert other not in g.text, (q, other)


def test_missing_field_is_stated_not_guessed():
    _, g = ctx("What documents are required for PM-KUSUM?")
    assert "NOT AVAILABLE: the knowledge base has no documents information" in g.text


def test_verification_status_is_visible_for_every_record():
    for q in SEVEN:
        r, g = ctx(q)
        assert g.text.count("Verification status:") == len(r.results)
    _, g = ctx("How do I register on eNAM?")
    assert "Verification status: verified (checked against an official source on 2026-10-06)" in g.text
    _, g = ctx("What documents are required for PM-KISAN?")
    assert "Verification status: needs_review - NOT checked against an official source" in g.text


def test_status_superseded_and_repealed_and_replacement_are_visible():
    r, g = ctx("What happened to Rythu Bandhu?")
    assert "Scheme status: SUPERSEDED - no longer current; current replacement: Rythu Bharosa (Telangana)" in g.text
    assert "REPLACEMENT - current successor of Rythu Bandhu (Telangana)" in g.text
    _, g = ctx("What is the current status of MGNREGA?")
    assert "REPEALED - no longer current" in g.text and "VB-G RAM G" in g.text


def test_cotton_context_says_no_crop_specific_scheme_and_does_not_call_generic_ones_cotton():
    r, g = ctx("What schemes are available for cotton farmers in Maharashtra?")
    assert ">>> ANSWER FIELD" not in g.text                       # scheme search: overview of each scheme
    assert "No scheme in the knowledge base is specific to cotton" in g.text
    assert "State: Maharashtra (from the question)" in g.text and "Crop: cotton (from the question)" in g.text
    assert g.text.count("NOT crop-specific") >= len(r.results) - 0      # every supplied scheme is generic
    assert "Crop-specific" not in g.text.replace("NOT crop-specific", "")
    assert "state-specific scheme for Maharashtra" in g.text and "central scheme that applies in Maharashtra" in g.text
    for other_state in ("Telangana", "Odisha", "Andhra Pradesh"):
        assert f"State scheme - applies only in {other_state}" not in g.text


def test_caveats_keep_uncertainty_and_drop_changelog():
    _, g = ctx("What documents are required for PM-KUSUM?")
    assert "PM-KUSUM 2.0" in g.text and "Till 31.03.2026" in g.text            # unresolved status uncertainty kept
    _, g = ctx("What documents are required for PM-KISAN?")
    assert "Corrected from legacy" not in g.text and "Domicile" not in g.text   # internal history not shown to the model
    assert "The full exclusion list should be checked" in g.text
    assert clean_caveats("Corrected from legacy: x. Sources conflict on y.") == "Sources conflict on y."


def test_urls_and_helplines_are_listed_explicitly_or_marked_none():
    _, g = ctx("What is the official website URL to apply for free education under the RTE Act?")
    assert "Official portal: NONE RECORDED" in g.text
    _, g = ctx("What documents are required for PM-KUSUM?")
    assert "Helplines recorded: NONE RECORDED" in g.text                         # PM-KUSUM has no helpline on record
    _, g = ctx("How do I apply for SMAM?")
    assert "Official portal: NONE RECORDED" in g.text and "information document, not an application page" in g.text
    _, g = ctx("How do I apply for PM-KISAN?")
    assert "Official portal: https://pmkisan.gov.in/" in g.text


def test_no_results_context_says_so():
    _, g = ctx("asdfghjkl 12345")
    assert "NO RELEVANT RECORDS" in g.text and "RECORD 1" not in g.text


def test_price_questions_state_that_no_price_data_exists():
    _, g = ctx("What is the mandi price of cotton today?")
    assert "NO commodity price data" in g.text


def test_retrieval_results_alone_feed_the_model_not_the_whole_dataset():
    r, g = ctx("What irrigation schemes are available for farmers?")
    assert [v.id for v in g.views] == ["pmksy-per-drop-more-crop", "pm-kusum"]
    assert len(g.text) < 9000                                   # a handful of records, not 72
    assert g.text.count("RECORD ") == 2 + g.text.count("RECORD 1 of 2") * 0 and "PM-KISAN" not in g.text


# ======================================================================= 2. grounding prompt
def test_system_prompt_contains_every_required_grounding_rule():
    p = GROUNDING_SYSTEM_PROMPT
    for phrase in [
        "Answer only from the supplied Saarthi.AI knowledge context",
        "Never invent eligibility criteria, benefits, documents, amounts, dates, URLs, helpline numbers or application procedures",
        "needs_review",
        "NOT been checked against an official source",
        "not available in the current Saarthi.AI knowledge base",
        "keep that uncertainty",
        'Do not turn "may apply" into "is eligible"',
        'Never say "you are eligible"',
        "NOT crop-specific",
        "central schemes (apply in every state)",
        "answer about that scheme only",
        "ANSWER FIELD",
        "superseded or repealed",
        "Never make up a link or phone number",
        "no relevant records",
    ]:
        assert phrase in p, phrase


def test_messages_have_system_prompt_context_question_and_bounded_history():
    r, g = ctx("What documents are required for PM-KISAN?")
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": "x" * 2000} for i in range(30)]
    m = build_messages(g.text, "What documents are required for PM-KISAN?", hist)
    assert m[0]["role"] == "system" and m[0]["content"] == GROUNDING_SYSTEM_PROMPT
    assert m[-1]["role"] == "user" and g.text in m[-1]["content"] and "USER QUESTION: What documents" in m[-1]["content"]
    assert len(m) == 1 + 12 + 1                                   # last 6 turns only
    assert all(len(x["content"]) <= 803 for x in m[1:-1] if x["role"] == "assistant")


# ======================================================================= 3. Groq client
def test_client_builds_openai_compatible_request_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "offline_only", False)
    monkeypatch.setattr(settings, "groq_model", "some-groq-model")
    monkeypatch.setattr(settings, "groq_base_url", "https://example.invalid/openai/v1")
    seen = {}

    class FakeOpenAI(FakeClient):
        def __init__(self, **kw):
            super().__init__(reply="hello")
            seen.update(kw)

    with patch("openai.OpenAI", FakeOpenAI):
        out = groq_client.chat([{"role": "user", "content": "hi"}])
    assert out == "hello"
    assert seen["base_url"] == "https://example.invalid/openai/v1" and seen["api_key"] == FAKE_KEY and seen["timeout"] == 20.0


def test_client_uses_configured_model_and_low_temperature(groq):
    fake = groq(reply="Fine.")
    groq_client.chat([{"role": "user", "content": "hi"}])
    assert fake.calls[0]["model"] == settings.groq_model and fake.calls[0]["temperature"] <= 0.2


def test_blank_model_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "groq_model", "")
    assert groq_client.model_name() == groq_client.DEFAULT_MODEL


def test_client_refuses_without_key_or_in_offline_mode(monkeypatch):
    with pytest.raises(groq_client.GroqError):
        groq_client.chat([{"role": "user", "content": "x"}])           # conftest removed the key
    monkeypatch.setattr(settings, "groq_api_key", FAKE_KEY)
    monkeypatch.setattr(settings, "offline_only", True)
    assert groq_client.is_configured() is False


def test_client_errors_never_contain_the_key(groq):
    groq(exc=RuntimeError(f"401 Incorrect API key provided: {FAKE_KEY}"))
    with pytest.raises(groq_client.GroqError) as e:
        groq_client.chat([{"role": "user", "content": "x"}])
    assert FAKE_KEY not in str(e.value) and "RuntimeError" in str(e.value)
    assert FAKE_KEY not in groq_client.redact(f"token {FAKE_KEY} and gsk_ANOTHERLONGKEY123456")
    assert "gsk_ANOTHERLONGKEY123456" not in groq_client.redact("gsk_ANOTHERLONGKEY123456")


# ======================================================================= 4. generate_answer_with_meta: success, guard, fallback
def good_answer():
    return "The current Saarthi.AI dataset lists Aadhaar, bank account details, land records and an Aadhaar-linked mobile number."


def test_groq_success_path_uses_retrieval_context_and_marks_source(groq):
    fake = groq(reply=good_answer())
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    res = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?", retrieval=r)
    assert (res.source, res.answer, res.fallback_reason) == ("groq", good_answer(), None)
    msgs = fake.calls[0]["messages"]
    assert msgs[0]["content"] == GROUNDING_SYSTEM_PROMPT
    assert ">>> ANSWER FIELD - DOCUMENTS" in msgs[-1]["content"] and "PM CARES" not in msgs[-1]["content"]
    assert FAKE_KEY not in " ".join(m["content"] for m in msgs)


def test_groq_answers_are_cached_but_offline_answers_are_not(groq):
    fake = groq(reply=good_answer())
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    a = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?", retrieval=r)
    b = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?", retrieval=r)
    assert len(fake.calls) == 1 and b.cached and b.source == "groq"
    fake_fail = groq(exc=TimeoutError("boom"))
    rag_module._answer_cache.clear()
    for _ in range(2):
        res = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?", retrieval=r)
        assert res.source == "offline"
    assert len(fake_fail.calls) == 2          # fallback was not cached: a recovered Groq is used again immediately


def test_groq_failure_falls_back_to_offline_and_says_so(groq, caplog):
    groq(exc=RuntimeError(f"connection error, key={FAKE_KEY}"))
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    with caplog.at_level(logging.DEBUG):
        res = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?", retrieval=r)
    assert res.source == "offline" and res.fallback_reason.startswith("groq_error")
    assert "Aadhaar" in res.answer and "Required Documents" in res.answer
    assert FAKE_KEY not in res.fallback_reason and FAKE_KEY not in caplog.text and FAKE_KEY not in res.answer


def test_fallback_uses_the_same_retrieval_results_as_the_online_answer(groq):
    groq(exc=RuntimeError("down"))
    for q in SEVEN:
        r = rag_engine.retrieve(q)
        res = rag_engine.generate_answer_with_meta("", q, retrieval=r)
        assert res.source == "offline"
        names = [s["name"] for s in r.schemes]
        for n in names:
            assert n in res.answer, (q, n)
        for s in rag_engine.schemes:                    # no scheme outside the retrieved set is mentioned
            if s["name"] not in names and len(s["name"]) > 12:
                assert s["name"] not in res.answer, (q, s["name"])


def test_offline_only_never_calls_groq(groq, monkeypatch):
    fake = groq(reply="should not be used")
    monkeypatch.setattr(settings, "offline_only", True)
    res = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?",
                                               retrieval=rag_engine.retrieve("What documents are required for PM-KISAN?"))
    assert res.source == "offline" and res.fallback_reason == "offline_only" and not fake.calls


def test_no_key_means_offline_without_error(monkeypatch):
    monkeypatch.setattr(settings, "offline_only", False)
    res = rag_engine.generate_answer_with_meta("", "What documents are required for PM-KISAN?",
                                               retrieval=rag_engine.retrieve("What documents are required for PM-KISAN?"))
    assert res.source == "offline" and res.fallback_reason == "groq_not_configured"


def test_empty_model_reply_falls_back(groq):
    groq(reply="   ")
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    res = rag_engine.generate_answer_with_meta("", "q", retrieval=r)
    assert res.source == "offline" and "empty_response" in res.fallback_reason


def test_legacy_generate_answer_signature_still_works(groq):
    groq(reply=good_answer())
    assert rag_engine.generate_answer("Context: PM-KISAN gives Rs 6000", "q", language="en") == good_answer()
    assert isinstance(rag_engine.generate_answer("", "any question", language="en"), str)


# ======================================================================= 5. output guard (hallucinated links / phone numbers)
def test_guard_rejects_invented_url_and_falls_back(groq):
    groq(reply="Apply at https://www.pmkisan-apply-now.example.com/register today.")
    r = rag_engine.retrieve("How do I apply for PM-KISAN?")
    res = rag_engine.generate_answer_with_meta("", "How do I apply for PM-KISAN?", retrieval=r)
    assert res.source == "offline" and res.fallback_reason == "ungrounded_output"
    assert "example.com" not in res.answer


def test_guard_rejects_invented_helpline(groq):
    groq(reply="Call the PM-KISAN helpline on 1800-111-222 for help.")
    r = rag_engine.retrieve("What is the PM-KISAN helpline number?")
    assert rag_engine.generate_answer_with_meta("", "q", retrieval=r).fallback_reason == "ungrounded_output"


def test_guard_accepts_recorded_urls_helplines_and_amounts():
    r, g = ctx("What is the PM-KISAN helpline number?")
    ok = ("Call 155261 or 1800-115-526 (toll-free). Apply at https://pmkisan.gov.in/ - you get Rs. 6,000 a year, "
          "Rs. 2,000 per instalment, land up to 2 hectares, updated in 2026, ₹50000 limit.")
    assert check_answer_grounding(ok, g) == []
    assert check_answer_grounding("See pmkisan.gov.in/Documents/guidelines.pdf", g) == []   # path under an allowed host
    assert check_answer_grounding("Visit www.pmkisan.gov.in", g) == []


def test_guard_flags_unrecorded_numbers_but_not_money_or_years():
    g = GroundedContext(text="x", allowed_urls=set(), allowed_phone_digits={"155261"})
    assert check_answer_grounding("The helpline is 14447.", g) == ["phone:14447"]
    assert check_answer_grounding("Dial 011-24300606 now", g) == ["phone:011-24300606"]
    assert check_answer_grounding("Premium is Rs. 1500 and in 2025 the limit was 5 lakh; call 155261.", g) == []
    assert check_answer_grounding("Visit https://fake.example.org/x", g)


# ======================================================================= 6. adversarial hallucination tests (deterministic parts)
def test_adversarial_document_not_in_record():
    r, g = ctx("Is a ration card required for PM-KISAN?")
    docs = " ".join(v.lower() for v in next(iter(g.views)).focus_lines)
    assert not re.search(r"\bration\b", docs) and not re.search(r"\bration card\b", g.text.lower())   # never shown such a document
    offline = render_offline_answer(r)
    assert not re.search(r"\bration\b", offline.lower()) and "anything not listed here is not available" in offline


def test_adversarial_amount_not_in_record():
    r, g = ctx("How much money in rupees will I get under MIDH for a 1000 MT cold storage?")
    assert [v.id for v in g.views] == ["midh-horticulture"]
    shown = g.text.split("Other recorded information")[0]
    assert not re.search(r"(Rs\.?|₹)\s?\d", shown)                           # the record holds percentages only
    assert "are not recorded" in g.text                                       # and says other component rates are missing
    offline = render_offline_answer(r)
    assert not re.search(r"(Rs\.?|₹)\s?\d", offline) and "35%" in offline


def test_adversarial_unverified_scheme_accepting_applications():
    r, g = ctx("Is PM-KUSUM currently accepting new applications?")
    assert [v.id for v in g.views] == ["pm-kusum"]
    assert "needs_review" in g.text and "PM-KUSUM 2.0" in g.text and "Till 31.03.2026" in g.text
    assert "accepting" not in g.text.lower().replace("not accepting", "")
    offline = render_offline_answer(r)
    assert "currently marked as needing verification" in offline and "31.03.2026" in offline   # the period caveat survives offline


def test_adversarial_official_url_not_in_record():
    r, g = ctx("What is the official website URL to apply for free education under the RTE Act?")
    assert "Official portal: NONE RECORDED" in g.text
    assert not g.allowed_urls and not _urls_in(g.text)
    offline = render_offline_answer(r)
    assert "No official application portal URL is recorded" in offline and not _urls_in(offline)


def test_adversarial_cotton_not_explicitly_covered():
    r, g = ctx("Does PM-KISAN specifically cover cotton farmers?")
    assert [v.id for v in g.views] == ["pm-kisan"] and "Crop coverage: All crops - NOT crop-specific" in g.text
    assert "cotton" not in " ".join(g.views[0].focus_lines).lower() + g.views[0].summary.lower()
    assert "NOT crop-specific" in render_offline_answer(r)


def _urls_in(text):
    return re.findall(r"https?://\S+|www\.\S+", text)


# ======================================================================= 7. API: header, no leaks, contract
@pytest.fixture
def api():
    from app.api.query import _rate_limit
    _rate_limit.clear()
    with patch("app.api.query.brightdata_search", return_value=[]), patch("app.api.query.search_document_links", return_value=[]), \
         patch("app.api.query.enrich_query_context", return_value=""), patch("app.api.query.get_relevant_gov_links", return_value=[]), \
         patch("app.api.query.translator_service") as ts:
        ts.detect_language = lambda text: "en"
        ts.translate_text = lambda text, source="auto", target="en": text
        yield


def test_api_marks_groq_answers_and_never_leaks_the_key(api, groq, caplog):
    groq(reply=good_answer())
    with caplog.at_level(logging.DEBUG):
        r = client.post("/query", json={"query": "What documents are required for PM-KISAN?", "language": "en"})
    assert r.status_code == 200 and r.headers["X-Answer-Source"] == "groq"
    assert r.json()["answer"] == good_answer()
    assert set(r.json()) >= {"intent", "answer", "recommended_schemes", "response_language", "doc_links", "nearest_centers"}
    assert FAKE_KEY not in r.text and FAKE_KEY not in str(dict(r.headers)) and FAKE_KEY not in caplog.text


def test_api_marks_offline_fallback_and_still_answers(api, groq):
    groq(exc=ConnectionError("network down"))
    r = client.post("/query", json={"query": "What documents are required for PM-KISAN?", "language": "en"})
    assert r.status_code == 200 and r.headers["X-Answer-Source"] == "offline"
    assert "Aadhaar" in r.json()["answer"] and "PM CARES" not in r.json()["answer"]
    assert FAKE_KEY not in r.text


def test_api_offline_without_key_header(api):
    r = client.post("/query", json={"query": "Who is eligible for PMFBY?", "language": "en"})
    assert r.status_code == 200 and r.headers["X-Answer-Source"] == "offline"
    assert "notified" in r.json()["answer"].lower() and "voluntary" in r.json()["answer"].lower()


def test_api_multilingual_path_still_translates_the_english_answer(api, groq):
    from translation_fakes import FakeTranslator
    groq(reply="The current Saarthi.AI dataset lists Aadhaar and land records for PM-KISAN. You get Rs. 6,000 per year; see https://pmkisan.gov.in/ for details.")
    fake = FakeTranslator(detect="hi")
    fake.english_for = {"पीएम किसान के लिए कौन से दस्तावेज चाहिए": "What documents are required for PM-KISAN?"}
    with patch("app.api.query.translator_service", fake):
        r = client.post("/query", json={"query": "पीएम किसान के लिए कौन से दस्तावेज चाहिए", "language": "hi"})
    assert r.status_code == 200
    assert r.json()["response_language"] == "hi" and r.headers["X-Translation-Status"] == "translated"
    assert "Rs. 6,000" in r.json()["answer"] and "PM-KISAN" in r.json()["answer"]     # facts survive translation
    assert "https://pmkisan.gov.in/" in r.json()["answer"]


# ======================================================================= 8. secrets hygiene
def test_no_api_key_in_source_or_frontend():
    root = Path(__file__).resolve().parents[2]
    pat = re.compile(r"gsk_[A-Za-z0-9]{16,}")
    assign = re.compile(r"GROQ_API_KEY[ \t]*=[ \t]*['\"]?[A-Za-z0-9_\-]{10,}")
    offenders = []
    for base in (root / "backend" / "app", root / "backend" / "scripts", root / "frontend", root / "backend" / "frontend_dist", root / ".env.example"):
        files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file() and p.suffix in (".py", ".js", ".html", ".json", ".example", ".txt", ".md")]
        for f in files:
            text = f.read_text(encoding="utf-8", errors="ignore")
            if pat.search(text) or assign.search(text):
                offenders.append(str(f.relative_to(root)))
    assert offenders == []


def test_frontend_never_references_the_llm_key_or_provider():
    root = Path(__file__).resolve().parents[2]
    for base in (root / "frontend", root / "backend" / "frontend_dist"):
        for f in base.rglob("*"):
            if f.is_file() and f.suffix in (".js", ".html"):
                assert "GROQ" not in f.read_text(encoding="utf-8", errors="ignore").upper(), f


def test_env_example_documents_groq_without_a_secret():
    text = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^GROQ_API_KEY=\s*$", text, re.M) and re.search(r"^GROQ_MODEL=\S+", text, re.M)


def test_debug_output_does_not_expose_secrets(groq):
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    assert FAKE_KEY not in str(r.debug_rows(5)) and FAKE_KEY not in str(settings.groq_base_url)
