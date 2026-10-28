"""Stage 5 repair: the three reported multilingual failures, reproduced and guarded.

  A  English retrieval and answer stay as they were.
  B  Hindi question  "मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?"  -> relevant records, Hindi answer.
  C  Marathi question "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?"           -> relevant records, Marathi answer.
  D  Missing state: no state scheme is presented as a match; examples are labelled and the state is asked for.
  E  Localization failure: predictable, honest metadata, never English labelled as Hindi/Marathi.
  F  Information integrity: names, amounts, percentages, URLs, dates survive localization.

What is real and what is faked (no network is available to the test run):
  * REAL: query understanding, intent detection, retrieval over the real schemes.json, grounded context, grounding guard,
    protection tokens, all validation (script share, Hindi-vs-Marathi check), API response construction.
  * FAKED: the machine-translation provider (FakeTranslator) and the Groq HTTP client. The Groq fake returns a fixed
    English answer for generation and a hand-written, natural Hindi / Marathi rendering for translation requests, built
    by phrase substitution on the exact masked text the pipeline sends - so the placeholders it must preserve are the
    real ones. Live Groq / live Google are exercised only by tests/test_multilingual_live.py (opt-in).
"""
import logging
import os
import re
import sys
import types
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.api import query as query_api  # noqa: E402
from app.services import groq_client, rag_engine as rag_module  # noqa: E402
from app.services.answer_localizer import localize_with_fallbacks, protect_spans  # noqa: E402
from app.services.grounded_answer import GROUNDING_SYSTEM_PROMPT, build_grounded_context  # noqa: E402
from app.services.language_utils import (  # noqa: E402
    detect_devanagari_language, is_text_in_language, script_ratio, source_language_for_query, wrong_devanagari_language,
)
from app.services.llm_translator import translate_with_groq  # noqa: E402
from app.services.multilingual_query import english_keywords  # noqa: E402
from app.services.rag_engine import rag_engine  # noqa: E402
from app.services.translator import TranslationError  # noqa: E402
from translation_fakes import FakeTranslator  # noqa: E402

client = TestClient(app)

Q_EN = "How can I check whether I am eligible for an agricultural subsidy?"
Q_HI = "मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?"
Q_MR = "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?"
# What a machine translator typically returns for them (Google renders अनुदान as "grant" as often as "subsidy")
EN_FOR = {Q_HI: "How can I find out whether I am eligible for agricultural subsidy or not?",
          Q_MR: "How can I check whether I am eligible for agricultural grant or not?"}
STATE_LEVEL = lambda schemes: [s["id"] for s in schemes if s.get("level") == "state"]

# ----------------------------------------------------------------------------------------------- realistic Groq fake
ANSWER_EN = ("To check your eligibility, compare your details with the criteria of each scheme below.\n"
             "- **PM-KISAN**: landholding farmer families can apply; income-tax payers are not eligible. Benefit: Rs. 6,000 per year.\n"
             "- **Kisan Credit Card (KCC)**: owner cultivators, tenant farmers and sharecroppers can apply.\n"
             "Your state is not known. Which state do you live in?")
PHRASES = {
    "hi": [
        ("To check your eligibility, compare your details with the criteria of each scheme below.",
         "अपनी पात्रता जाँचने के लिए, अपनी जानकारी को नीचे दी गई हर योजना की शर्तों से मिलाएँ।"),
        (": landholding farmer families can apply; income-tax payers are not eligible. Benefit: ",
         ": ज़मीन वाले किसान परिवार आवेदन कर सकते हैं; आयकर देने वाले पात्र नहीं हैं। लाभ: "),
        (" per year.", " प्रति वर्ष।"),
        (": owner cultivators, tenant farmers and sharecroppers can apply.",
         ": अपनी ज़मीन पर खेती करने वाले, किरायेदार किसान और बटाईदार आवेदन कर सकते हैं।"),
        ("Your state is not known. Which state do you live in?", "आपका राज्य पता नहीं है। आप किस राज्य में रहते हैं?"),
    ],
    "mr": [
        ("To check your eligibility, compare your details with the criteria of each scheme below.",
         "तुमची पात्रता तपासण्यासाठी, तुमची माहिती खालील प्रत्येक योजनेच्या अटींशी जुळवून पाहा."),
        (": landholding farmer families can apply; income-tax payers are not eligible. Benefit: ",
         ": जमीनधारक शेतकरी कुटुंबे अर्ज करू शकतात; आयकर भरणारे पात्र नाहीत. लाभ: "),
        (" per year.", " दरवर्षी."),
        (": owner cultivators, tenant farmers and sharecroppers can apply.",
         ": स्वतःची जमीन कसणारे, खंडाने शेती करणारे शेतकरी आणि वाटेकरी अर्ज करू शकतात."),
        ("Your state is not known. Which state do you live in?", "तुमचे राज्य माहीत नाही. तुम्ही कोणत्या राज्यात राहता?"),
    ],
}
_TOKEN = re.compile(r"QZ\d+QZ|SAARTHICARD\d+")


def phrase_translate(masked, lang):
    out = masked
    for en, native in PHRASES[lang]:
        out = out.replace(en, native)
    return out


class RealisticGroq:
    """OpenAI-compatible fake: English answers for generation, natural Hindi/Marathi for translation requests."""

    def __init__(self, answer=ANSWER_EN, translate=None, generation_exc=None):
        self.answer, self.translate, self.generation_exc = answer, translate, generation_exc
        self.calls = []
        self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

    def kind(self, kw):
        return "translate" if kw["messages"][0]["content"].startswith("You are a professional translator") else "answer"

    def _create(self, **kw):
        self.calls.append(kw)
        system, user = kw["messages"][0]["content"], kw["messages"][-1]["content"]
        if self.kind(kw) == "answer":
            if self.generation_exc:
                raise self.generation_exc
            text = self.answer
        else:
            target = "en" if "into English" in system else ("mr" if "into Marathi" in system else "hi")
            if self.translate is not None:
                text = self.translate(user, target)
            elif target == "en":
                text = EN_FOR.get(user, user)
            else:
                text = phrase_translate(user, target)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=types.SimpleNamespace(content=text))])

    def translations(self):
        return [c for c in self.calls if self.kind(c) == "translate"]


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    if not rag_engine._all_schemes:
        rag_engine.load_documents()


@pytest.fixture
def ask(monkeypatch):
    def _ask(query, language, translator=None, groq=None, groq_configured=True, payload_extra=None):
        query_api._rate_limit.clear()
        rag_module._answer_cache.clear()
        translator = translator or FakeTranslator()
        groq = groq or RealisticGroq()
        monkeypatch.setattr(settings, "offline_only", False)
        monkeypatch.setattr(settings, "groq_api_key", "gsk_fake_key_for_tests" if groq_configured else None)
        monkeypatch.setattr(groq_client, "_make_client", lambda: groq)
        payload = {"query": query, "language": language, "occupation": "farmer", **(payload_extra or {})}
        with patch("app.api.query.brightdata_search", return_value=[]), patch("app.api.query.search_document_links", return_value=[]), \
             patch("app.api.query.enrich_query_context", return_value=""), patch("app.api.query.get_relevant_gov_links", return_value=[]), \
             patch("app.api.query.translator_service", translator):
            return client.post("/query", json=payload), translator, groq
    return _ask


def machine(mode=None):
    t = FakeTranslator(mode=mode)
    t.english_for = dict(EN_FOR) if mode is None else {}
    return t


def answer_context(groq):
    return [c for c in groq.calls if groq.kind(c) == "answer"][0]["messages"][-1]["content"]


# ======================================================================================== language identification
def test_hindi_and_marathi_questions_are_told_apart_although_they_share_a_script():
    assert detect_devanagari_language(Q_HI) == "hi" and detect_devanagari_language(Q_MR) == "mr"
    assert source_language_for_query(Q_MR, "hi") == "mr" and source_language_for_query(Q_HI, "mr") == "hi"
    hi_answer = phrase_translate(ANSWER_EN, "hi")
    mr_answer = phrase_translate(ANSWER_EN, "mr")
    assert wrong_devanagari_language(hi_answer, "mr") and wrong_devanagari_language(mr_answer, "hi")
    assert not wrong_devanagari_language(hi_answer, "hi") and not wrong_devanagari_language(mr_answer, "mr")


@pytest.mark.parametrize("detected,query,expected", [
    ("hi", Q_HI, "hi"), ("mr", Q_MR, "mr"), ("ne", Q_MR, "mr"), ("ne", Q_HI, "hi"),   # langdetect may say Nepali
    (None, Q_MR, "mr"), ("en", Q_EN, "en"), ("xx", Q_EN, "en"),
])
def test_auto_language_never_answers_a_devanagari_question_in_english_by_accident(detected, query, expected):
    assert query_api._resolve_auto_language(detected, query) == expected


@pytest.mark.parametrize("q,expected", [(Q_HI, {"agriculture", "subsidy", "eligible"}), (Q_MR, {"agriculture", "subsidy", "eligible"}),
                                        ("महाराष्ट्रातील कापूस शेतकऱ्यांसाठी कोणत्या योजना आहेत?", {"Maharashtra", "cotton", "farmer", "scheme"}),
                                        ("पीक विमा योजनेसाठी अर्ज कसा करावा?", {"crop", "insurance", "scheme", "apply"})])
def test_hindi_and_marathi_scheme_vocabulary_becomes_english_retrieval_keywords(q, expected):
    assert expected <= set(english_keywords(q).split())


# ======================================================================================== A: English
def test_A_english_retrieval_is_unchanged_and_relevant():
    r = rag_engine.retrieve(Q_EN, occupation="farmer")
    assert r.understanding.intent == "eligibility_check" and r.results
    ids = [c.id for c in r.results]
    assert ids[0] == "agriculture-infrastructure-fund" and "kisan-credit-card" in ids   # same ranking as before the repair
    assert not STATE_LEVEL(r.schemes)


def test_A_english_api_answer_is_english(ask):
    r, t, g = ask(Q_EN, "en")
    d = r.json()
    assert r.status_code == 200 and d["intent"] == "eligibility_check"
    assert d["answer"] == ANSWER_EN and d["response_language"] == "en" and d["requested_language"] == "en"
    assert d["translation_status"] == "not_needed" and r.headers["X-Translation-Status"] == "not_needed"
    assert not t.kinds("strict") and not g.translations()                              # nothing was translated
    assert d["recommended_schemes"] and "RECORD 1 of" in answer_context(g)


# ======================================================================================== B: Hindi
def test_B_hindi_intent_and_retrieval_with_and_without_a_translation():
    for q in (EN_FOR[Q_HI], Q_HI + " " + english_keywords(Q_HI)):         # translated / translation failed
        r = rag_engine.retrieve(q, occupation="farmer")
        assert r.understanding.intent == "eligibility_check", q
        assert {"pm-kisan", "kisan-credit-card"} & {c.id for c in r.results}, q
        assert not STATE_LEVEL(r.schemes)


def test_B_hindi_answer_is_natural_hindi_when_the_machine_translator_works(ask):
    r, t, g = ask(Q_HI, "hi", translator=machine())
    d = r.json()
    assert r.status_code == 200 and d["intent"] == "eligibility_check"
    assert r.headers["X-Query-Translation"] == "machine" and EN_FOR[Q_HI] in answer_context(g)
    assert d["response_language"] == "hi" == r.headers["X-Answer-Language"] and d["translation_status"] == "translated"
    assert r.headers["X-Translation-Provider"] == "machine"


def test_B_hindi_answer_falls_back_to_groq_translation_when_google_fails(ask):
    """The reported failure: Google unavailable -> English answer. Now Groq translates, and the result is validated."""
    r, t, g = ask(Q_HI, "hi", translator=machine("raise"))
    d = r.json()
    assert r.status_code == 200 and d["intent"] == "eligibility_check"
    assert r.headers["X-Query-Translation"] == "groq"                         # question translated by Groq for retrieval
    assert r.headers["X-Translation-Provider"] == "groq" and r.headers["X-Translation-Attempts"] == "machine:all_providers_failed"
    assert d["response_language"] == "hi" and d["translation_status"] == "translated"
    ans = d["answer"]
    assert script_ratio(ans, "devanagari") > 0.7 and detect_devanagari_language(ans) == "hi"
    assert "पात्रता" in ans and "आप किस राज्य में रहते हैं?" in ans            # explains eligibility, asks for the state
    for fact in ("PM-KISAN", "Kisan Credit Card (KCC)", "Rs. 6,000"):
        assert fact in ans


def test_B_hindi_recommendation_cards_are_localized_too(ask):
    def translate(user, target):
        if target == "en":
            return EN_FOR.get(user, user)
        if "To check your eligibility" in user:                       # the answer
            return phrase_translate(user, target)
        # the cards arrive as ONE request, one "<card marker token> <description>" line per card: keep every token
        lines = []
        for line in user.split("\n"):
            toks = _TOKEN.findall(line)
            lines.append(toks[0] + " यह योजना किसानों के लिए है और इसकी शर्तें योजना के नियमों में दी गई हैं। " + " ".join(toks[1:]))
        return "\n".join(lines)
    r, _, _ = ask(Q_HI, "hi", translator=machine("raise"), groq=RealisticGroq(translate=translate))
    d = r.json()
    assert r.headers["X-Cards-Translation"] == "translated"
    cards = d["recommended_schemes"]
    assert cards and all(is_text_in_language(c["description"], "hi") for c in cards)
    assert all(c["name"].isascii() for c in cards)                                 # official names never translated


# ======================================================================================== C: Marathi
def test_C_marathi_question_without_any_translation_still_retrieves_records():
    """The reported failure: the untranslated Marathi question (कृषी, अनुदानासाठी) matched nothing."""
    r_old_style = rag_engine.retrieve(Q_MR)                                          # raw question, as before the repair
    assert not r_old_style.results                                                   # (documents why the keywords are needed)
    r = rag_engine.retrieve(Q_MR + " " + english_keywords(Q_MR), occupation="farmer")
    assert r.understanding.intent == "eligibility_check"
    assert {"pm-kisan", "kisan-credit-card"} <= {c.id for c in r.results}
    assert not any("No relevant scheme" in n for n in r.notes)


def test_C_marathi_answer_is_marathi_and_not_no_information(ask):
    r, t, g = ask(Q_MR, "mr", translator=machine("raise"))
    d = r.json()
    assert r.status_code == 200 and d["intent"] == "eligibility_check"
    ctx = answer_context(g)
    assert "RECORD 1 of" in ctx and "NO RELEVANT RECORDS" not in ctx                 # Groq got real scheme records
    assert d["response_language"] == "mr" == r.headers["X-Answer-Language"] and d["translation_status"] == "translated"
    ans = d["answer"]
    assert detect_devanagari_language(ans) == "mr" and "तुम्ही कोणत्या राज्यात राहता?" in ans
    assert "PM-KISAN" in ans and "Rs. 6,000" in ans


def test_C_marathi_with_every_translator_down_still_retrieves_and_says_english_honestly(ask):
    r, t, g = ask(Q_MR, "mr", translator=machine("raise"), groq_configured=False)       # offline answer, no translation at all
    d = r.json()
    assert r.status_code == 200 and r.headers["X-Query-Translation"] == "failed"
    assert d["recommended_schemes"] and "No relevant scheme was found" not in d["answer"]
    assert any(c["name"] == "PM-KISAN" for c in d["recommended_schemes"])
    assert d["response_language"] == "en" and d["translation_status"] == "failed" and d["requested_language"] == "mr"


def test_C_a_hindi_translation_is_rejected_for_a_marathi_request(ask):
    r, _, _ = ask(Q_MR, "mr", translator=machine("raise"),
                  groq=RealisticGroq(translate=lambda u, tgt: EN_FOR.get(u, u) if tgt == "en" else phrase_translate(u, "hi")))
    d = r.json()
    assert d["response_language"] == "en" and d["translation_status"] == "failed"
    assert r.headers["X-Translation-Attempts"] == "machine:all_providers_failed,groq:result_in_wrong_language"


# ======================================================================================== D: missing state
@pytest.mark.parametrize("q", [Q_EN, EN_FOR[Q_HI], EN_FOR[Q_MR], Q_MR + " " + english_keywords(Q_MR)])
def test_D_unknown_state_never_yields_state_schemes_as_matches(q):
    r = rag_engine.retrieve(q, occupation="farmer")
    assert r.understanding.state is None and r.results and not STATE_LEVEL(r.schemes)
    note = next(n for n in r.notes if n.startswith("State not provided"))
    assert "EXAMPLES ONLY" in note and "share your state" in note
    for c in r.state_examples:                                                        # examples carry their state
        assert c.scheme["level"] == "state" and f"only in {c.scheme['states'][0]}" in note


def test_D_state_from_the_profile_allows_that_state_only():
    got = {c.id for c in rag_engine.retrieve(Q_EN, user_state="Maharashtra", occupation="farmer", top_k=20).results}
    assert "namo-shetkari-mahasanman-nidhi" in got
    assert not got & {"rythu-bharosa", "rythu-bima", "annadata-sukhibhava", "cm-kisan-odisha"}


def test_D_language_is_never_used_as_the_state():
    r = rag_engine.retrieve(Q_MR + " " + english_keywords(Q_MR), occupation="farmer")   # Marathi does not mean Maharashtra
    assert r.understanding.state is None and "namo-shetkari-mahasanman-nidhi" not in {c.id for c in r.results}


def test_D_api_recommendations_and_llm_context_for_an_unknown_state(ask):
    for q, lang in ((Q_EN, "en"), (Q_HI, "hi"), (Q_MR, "mr")):
        r, _, g = ask(q, lang, translator=machine())
        cards = r.json()["recommended_schemes"]
        assert cards and all(c["type"] != "state" and not c["state"] for c in cards), (lang, cards)
        ctx = answer_context(g)
        assert "State not provided" in ctx and "EXAMPLES ONLY" in ctx
    assert "14. If the user's state is not known" in GROUNDING_SYSTEM_PROMPT


def test_D_offline_answer_labels_examples_and_asks_for_the_state(ask):
    r, _, _ = ask(Q_EN, "en", groq_configured=False)
    ans = r.json()["answer"]
    assert "State not provided" in ans and "EXAMPLES ONLY" in ans and "share your state" in ans


# ======================================================================================== E: localization failure
@pytest.mark.parametrize("groq_translate,reason", [
    (lambda u, t: u, "groq:result_not_in_target_language"),                       # Groq echoes English
    (lambda u, t: re.sub(r"QZ0QZ", "", phrase_translate(u, t)), "groq:protected_term_lost"),   # drops PM-KISAN
    (lambda u, t: "", "groq:llm_empty_result"),                                    # empty
])
def test_E_every_provider_failing_returns_english_labelled_english(ask, caplog, groq_translate, reason):
    tr = lambda u, t: EN_FOR.get(u, u) if t == "en" else groq_translate(u, t)
    with caplog.at_level(logging.WARNING):
        r, _, _ = ask(Q_HI, "hi", translator=machine("raise"), groq=RealisticGroq(translate=tr))
    d = r.json()
    assert r.status_code == 200 and d["answer"] == ANSWER_EN
    assert d["response_language"] == "en" == r.headers["X-Answer-Language"]          # NOT "hi"
    assert d["requested_language"] == "hi" and d["translation_status"] == "failed" == r.headers["X-Translation-Status"]
    assert r.headers["X-Translation-Attempts"] == "machine:all_providers_failed," + reason
    assert "could not be translated to hi" in caplog.text and "gsk_" not in caplog.text   # diagnosable, no secret


def test_E_groq_errors_during_translation_are_handled(ask):
    class Boom(RealisticGroq):
        def _create(self, **kw):
            if self.kind(kw) == "translate":
                self.calls.append(kw)
                raise ConnectionError("network down")
            return super()._create(**kw)
    r, _, _ = ask(Q_HI, "hi", translator=machine("raise"), groq=Boom())
    d = r.json()
    assert r.status_code == 200 and d["response_language"] == "en" and d["translation_status"] == "failed"
    assert "groq:llm_failed" in r.headers["X-Translation-Attempts"]


def test_E_provider_order_is_configurable_and_groq_is_skipped_when_not_configured(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert [n for n, _ in query_api._translation_providers()] == ["machine"]
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(settings, "translation_provider_order", "groq,machine")
    assert [n for n, _ in query_api._translation_providers()] == ["groq", "machine"]


def test_E_translate_with_groq_raises_instead_of_returning_input(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    with pytest.raises(TranslationError):
        translate_with_groq("Hello farmers", "en", "hi")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    with pytest.raises(TranslationError):
        translate_with_groq("Hello", "en", "xx")


def test_E_reasoning_model_gets_a_low_reasoning_budget_and_a_large_token_budget(monkeypatch):
    fake = RealisticGroq()
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(settings, "groq_model", "openai/gpt-oss-120b")
    monkeypatch.setattr(groq_client, "_make_client", lambda: fake)
    masked, _ = protect_spans(ANSWER_EN, ["PM-KISAN", "Kisan Credit Card (KCC)"])
    out = translate_with_groq(masked, "en", "hi")
    call = fake.calls[-1]
    assert call["model"] == "openai/gpt-oss-120b" and call["extra_body"] == {"reasoning_effort": "low"}
    assert 500 + len(masked) - 1 <= call["max_tokens"] <= 4096 and call["temperature"] == 0.0
    assert "exactly" in call["messages"][0]["content"] and "placeholders (QZ0QZ to QZ" in call["messages"][0]["content"]
    assert "QZ0QZ" in out


# ======================================================================================== F: information integrity
FACTS_EN = ("Under PM-KISAN you get Rs. 6,000 per year. PMFBY premium is 2% for Kharif crops. "
            "Apply at https://pmkisan.gov.in/ before 31.03.2026. Helpline: 155261.")
FACTS = ["PM-KISAN", "Rs. 6,000", "PMFBY", "2%", "https://pmkisan.gov.in/", "31.03.2026", "155261"]
HI_FACTS = [("Under ", ""), (" you get ", " के तहत आपको "), (" per year. ", " प्रति वर्ष मिलते हैं। "),
            (" premium is ", " का प्रीमियम "), (" for Kharif crops. ", " खरीफ फसलों के लिए है। "), ("Apply at ", ""),
            (" before ", " पर आवेदन करें, अंतिम तिथि "), (". Helpline: ", "। हेल्पलाइन: "), (".", "।")]


def _hi_facts(masked, *_):
    out = masked
    for en, hi in HI_FACTS:
        out = out.replace(en, hi)
    return out


def test_F_facts_survive_localization_exactly():
    res = localize_with_fallbacks(FACTS_EN, "hi", ["PM-KISAN", "Pradhan Mantri Fasal Bima Yojana (PMFBY)"],
                                  [("groq", _hi_facts)])
    assert res.status == "translated" and res.language == "hi" and res.provider == "groq"
    for fact in FACTS:
        assert fact in res.text, fact
    assert detect_devanagari_language(res.text) == "hi"


@pytest.mark.parametrize("corrupt", [
    lambda t: t.replace("QZ1QZ", "6000"),         # amount rewritten
    lambda t: t.replace("QZ3QZ", "QZ3QZ QZ3QZ"),  # percentage duplicated
    lambda t: t.replace("QZ4QZ", "pmkisan.gov.in"),  # URL rewritten
])
def test_F_a_translation_that_changes_a_fact_is_rejected(corrupt):
    res = localize_with_fallbacks(FACTS_EN, "hi", ["PM-KISAN"], [("groq", lambda m, *_: corrupt(_hi_facts(m)))])
    assert res.status == "failed" and res.language == "en" and res.text == FACTS_EN
    assert res.reason == "protected_term_lost"


def test_F_eligibility_conditions_reach_the_answer_context_from_the_dataset():
    r = rag_engine.retrieve(EN_FOR[Q_HI], occupation="farmer")
    ctx = build_grounded_context(r).text
    assert ">>> ANSWER FIELD - ELIGIBILITY" in ctx
    assert "Income-tax payers" in ctx or "income-tax payers" in ctx.lower()       # PM-KISAN exclusion, from schemes.json


# ======================================================================================== findings from the first live run
# (Windows, real Groq gpt-oss-120b + real Google): correct answers rejected, 429 storms, failed SW install.
def test_live_groq_answer_with_non_breaking_hyphens_in_a_recorded_helpline_is_accepted(ask):
    """Log: 'Groq answer rejected by the grounding guard (phone:1800, phone:180, phone:1551)'. The model wrote the
    dataset's Kisan Call Centre number 1800-180-1551 with U+2011 hyphens; the guard split it and discarded the answer."""
    reply = ("Kisan Credit Card (KCC) is for owner cultivators, tenant farmers and sharecroppers. "
             "For help call the Kisan Call Centre at 1800‑180‑1551.")
    r, _, g = ask("What is the Kisan Credit Card helpline number?", "en", groq=RealisticGroq(answer=reply))
    assert r.headers["X-Answer-Source"] == "groq", "a grounded answer must not be replaced by the offline answer"
    assert "1800-180-1551" in r.json()["answer"] and "‑" not in r.json()["answer"]


def test_an_invented_number_is_still_rejected_after_normalisation(ask):
    reply = "Kisan Credit Card (KCC): call the helpline 1800‑999‑0000 for details."
    r, _, _ = ask("What is the Kisan Credit Card helpline number?", "en", groq=RealisticGroq(answer=reply))
    assert r.headers["X-Answer-Source"] == "offline"                     # guard still works


def test_after_a_429_groq_is_paused_instead_of_hammered(monkeypatch):
    class RateLimited(Exception):
        status_code = 429
        response = types.SimpleNamespace(headers={"retry-after": "7"})
    calls = []

    def create(**kw):
        calls.append(kw)
        raise RateLimited()
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=create)))
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(groq_client, "_make_client", lambda: fake)
    monkeypatch.setattr(groq_client, "_cooldown_until", 0.0)
    with pytest.raises(groq_client.GroqError, match="429"):
        groq_client.chat([{"role": "user", "content": "x"}])
    assert 6 < groq_client.cooldown_remaining() <= 7                       # Retry-After honoured
    with pytest.raises(groq_client.GroqError, match="rate_limited"):
        groq_client.chat([{"role": "user", "content": "y"}])
    assert len(calls) == 1                                                 # the second call never reached Groq
    with pytest.raises(TranslationError) as e:
        translate_with_groq("Hello farmers, here is the answer.", "en", "hi")
    assert e.value.code == "llm_rate_limited"
    monkeypatch.setattr(groq_client, "_cooldown_until", 0.0)


def test_a_truncated_groq_translation_is_reported_not_accepted(monkeypatch):
    resp = types.SimpleNamespace(choices=[types.SimpleNamespace(finish_reason="length",
                                                                message=types.SimpleNamespace(content="अधूरा अनुवाद QZ0QZ"))])
    fake = types.SimpleNamespace(chat=types.SimpleNamespace(completions=types.SimpleNamespace(create=lambda **k: resp)))
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(groq_client, "_make_client", lambda: fake)
    with pytest.raises(TranslationError) as e:
        translate_with_groq("QZ0QZ gives Rs. 6,000.", "en", "hi")
    assert e.value.code == "llm_truncated"


def test_placeholders_written_with_devanagari_digits_are_restored():
    masked, originals = protect_spans(FACTS_EN, ["PM-KISAN"])
    dev = str.maketrans("0123456789", "०१२३४५६७८९")
    res = localize_with_fallbacks(FACTS_EN, "hi", ["PM-KISAN"],
                                  [("groq", lambda m, *_: re.sub(r"QZ(\d+)QZ", lambda x: "QZ" + x.group(1).translate(dev) + "QZ", _hi_facts(m)))])
    assert res.status == "translated" and all(f in res.text for f in FACTS)


def test_background_precache_requests_never_call_groq(ask, monkeypatch):
    query_api._rate_limit.clear()
    groq = RealisticGroq()
    monkeypatch.setattr(settings, "groq_api_key", "gsk_fake")
    monkeypatch.setattr(groq_client, "_make_client", lambda: groq)
    with patch("app.api.query.brightdata_search", return_value=[]), patch("app.api.query.search_document_links", return_value=[]), \
         patch("app.api.query.enrich_query_context", return_value=""), patch("app.api.query.get_relevant_gov_links", return_value=[]), \
         patch("app.api.query.translator_service", machine("raise")):
        r = client.post("/query", json={"query": Q_HI, "language": "hi"}, headers={"X-Saarthi-Precache": "1"})
    assert r.status_code == 200 and r.headers["X-Answer-Source"] == "offline" and not groq.calls
    assert r.json()["response_language"] == "en" and r.json()["translation_status"] == "failed"   # honest, no Groq spent


def test_query_without_trailing_slash_is_served_directly_and_index_html_exists():
    query_api._rate_limit.clear()
    r = client.post("/query", json={"query": "PM-KISAN documents", "language": "en"}, follow_redirects=False)
    assert r.status_code == 200
    assert client.get("/index.html").status_code == 200                  # sw.js pre-caches it; 404 broke the SW install
