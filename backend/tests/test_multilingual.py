"""Stage 5: multilingual answers.

Backend flow under test:  question -> (translate to English for retrieval) -> grounded English answer (Groq or offline)
-> protected translation into the selected language -> response with an honest response_language.

No network is used: translation providers are faked (translation_fakes.py). Live translation and live Groq are NOT
exercised here; see the report for how to check them on a machine with network access.
"""
import logging
import os
import sys
from unittest.mock import PropertyMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import groq_client, rag_engine as rag_module  # noqa: E402
from app.services.answer_localizer import localize_answer, protect_spans, restore_spans  # noqa: E402
from app.services.language_utils import (  # noqa: E402
    LANGUAGE_NAMES, SUPPORTED_LANGUAGES, is_text_in_language, normalize_language, script_ratio, source_language_for_query,
)
from app.services.rag_engine import rag_engine  # noqa: E402
from app.services.translator import TranslationError, TranslatorService, chunk_text  # noqa: E402
from translation_fakes import NATIVE_WORD, FakeGroqClient, FakeTranslator, fake_translate  # noqa: E402

client = TestClient(app)
NON_ENGLISH = [l for l in SUPPORTED_LANGUAGES if l != "en"]
QUERY = "What documents are required for PM-KISAN?"
FACT_ANSWER = ("The current Saarthi.AI dataset lists Aadhaar and land records for PM-KISAN. You get Rs. 6,000 per year in three "
               "instalments of Rs. 2,000. The helpline is 155261. See https://pmkisan.gov.in/ for details. "
               "Eligibility changes apply from 31.03.2026.")


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    if not rag_engine._all_schemes:
        rag_engine.load_documents()


@pytest.fixture(autouse=True)
def _clean_cache():
    rag_module._answer_cache.clear()
    yield
    rag_module._answer_cache.clear()


@pytest.fixture
def run(monkeypatch):
    """ask(...) -> (response, translator, groq_client). Everything external is faked."""
    def ask(query=QUERY, language="en", reply=FACT_ANSWER, translator=None, groq=True, groq_exc=None, payload_extra=None,
            omit_language=False, keep_cache=False):
        from app.api.query import _rate_limit
        _rate_limit.clear()
        if not keep_cache:
            rag_module._answer_cache.clear()
        translator = translator or FakeTranslator()
        fake_groq = FakeGroqClient(reply=reply, exc=groq_exc)
        monkeypatch.setattr(settings, "offline_only", False)
        monkeypatch.setattr(settings, "groq_api_key", "gsk_fake_key_for_tests" if groq else None)
        monkeypatch.setattr(groq_client, "_make_client", lambda: fake_groq)
        payload = {"query": query, **({} if omit_language else {"language": language}), **(payload_extra or {})}
        with patch("app.api.query.brightdata_search", return_value=[]), patch("app.api.query.search_document_links", return_value=[]), \
             patch("app.api.query.enrich_query_context", return_value=""), patch("app.api.query.get_relevant_gov_links", return_value=[]), \
             patch("app.api.query.translator_service", translator):
            return client.post("/query", json=payload), translator, fake_groq
    return ask


# ====================================================================== language codes and script checks
@pytest.mark.parametrize("code,expected", [
    (None, "auto"), ("", "auto"), ("  ", "auto"), ("auto", "auto"), ("AUTO", "auto"), ("hi", "hi"), ("HI", "hi"), ("hi-IN", "hi"),
    ("hi_IN", "hi"), ("mr", "mr"), ("or", "or"), ("pa-Guru-IN", "pa"), ("xyz", None), ("fr", None), ("ur", None), ("123", None),
])
def test_normalize_language(code, expected):
    assert normalize_language(code) == expected


def test_every_supported_language_has_a_name_and_a_script_check():
    assert set(LANGUAGE_NAMES) == set(SUPPORTED_LANGUAGES) and len(SUPPORTED_LANGUAGES) == 11
    for lang in NON_ENGLISH:
        text = fake_translate("The current dataset lists documents for this scheme.", lang)
        assert is_text_in_language(text, lang), lang
        assert not is_text_in_language("The current dataset lists documents for this scheme.", lang), lang


def test_heuristic_false_positive_english_with_localized_headings_is_not_hindi():
    mixed = ("**आवश्यक दस्तावेज़:**\n- Aadhaar [mandatory]\n- Bank account details\n**पात्रता:**\n"
             "- Landholding farmer families owning cultivable land.\n**लाभ:**\n- Rs. 6,000 per year in three instalments.")
    assert sum(1 for c in mixed if 0x0900 <= ord(c) <= 0x097F) > 20          # the OLD rule ("> 20 native chars") accepted this
    assert not is_text_in_language(mixed, "hi")
    from app.api.query import _text_likely_in_language
    assert _text_likely_in_language(mixed, "hi") is False


def test_hindi_with_latin_scheme_names_is_still_hindi():
    text = "PM-KISAN के लिए आधार और भूमि अभिलेख आवश्यक हैं। आपको प्रति वर्ष Rs. 6,000 मिलते हैं।"
    assert is_text_in_language(text, "hi") and is_text_in_language(text, "mr")        # Devanagari is shared by hi and mr
    assert not is_text_in_language(text, "ta") and script_ratio(text, "devanagari") > 0.6


@pytest.mark.parametrize("query,selected,expected", [
    ("What documents are required for PM-KISAN?", "hi", "en"),        # English question, Hindi selected: not "from Hindi"
    ("PM-KISAN ke bare mein batao", "hi", "en"),                      # romanized Hindi: Latin script
    ("पीएम किसान के दस्तावेज़", "hi", "hi"),
    ("पीएम किसान के दस्तावेज़", "en", "auto"),                         # Hindi typed while English is selected
    ("பிஎம் கிசான் ஆவணங்கள்", "hi", "auto"),                           # script differs from the selected language
    ("", "hi", "en"),
])
def test_query_source_language(query, selected, expected):
    assert source_language_for_query(query, selected) == expected


# ====================================================================== translator service: failures are explicit
@pytest.fixture
def svc():
    return TranslatorService()


def test_strict_translation_raises_when_every_provider_fails(svc):
    with patch("app.services.translator.GoogleTranslator", side_effect=ConnectionError("blocked")):
        with pytest.raises(TranslationError) as e:
            svc.translate_text_strict("The scheme gives money to farmers each year.", "en", "hi")
    assert e.value.code == "all_providers_failed" and "ConnectionError" in str(e.value)


def test_legacy_translate_text_still_returns_the_input_on_failure(svc):
    with patch("app.services.translator.GoogleTranslator", side_effect=ConnectionError("blocked")):
        assert svc.translate_text("Hello farmers, this is a test.", "en", "hi") == "Hello farmers, this is a test."
    assert svc.translate_text(None, "en", "hi") is None and svc.translate_text("x", "en", "en") == "x"


def test_strict_translation_detects_a_provider_that_echoes_its_input(svc):
    class Echo:
        def __init__(self, source, target): pass
        def translate(self, t): return t
    with patch("app.services.translator.GoogleTranslator", Echo):
        with pytest.raises(TranslationError) as e:
            svc.translate_text_strict("The scheme gives money to farmers each year.", "en", "mr")
        assert e.value.code == "provider_returned_input_unchanged"
        assert svc.translate_text_strict("pm kisan ke bare me batao", "auto", "en") == "pm kisan ke bare me batao"   # Latin -> en: legitimate


def test_strict_translation_rejects_none_and_empty_provider_results(svc):
    class Nothing:
        def __init__(self, source, target): pass
        def translate(self, t): return None
    with patch("app.services.translator.GoogleTranslator", Nothing):
        with pytest.raises(TranslationError):
            svc.translate_text_strict("The scheme gives money to farmers each year.", "en", "ta")


def test_strict_translation_input_and_target_validation(svc):
    for bad in (None, "", "   ", 5):
        with pytest.raises(TranslationError):
            svc.translate_text_strict(bad, "en", "hi")
    with pytest.raises(TranslationError) as e:
        svc.translate_text_strict("Some text for translation.", "en", "fr")
    assert e.value.code == "unsupported_target"
    assert svc.translate_text_strict("Same language text.", "hi", "hi") == "Same language text."


def test_azure_failure_falls_back_to_google_and_is_not_an_error(svc):
    class Good:
        def __init__(self, source, target): pass
        def translate(self, t): return "अनुवादित पाठ"
    with patch.object(TranslatorService, "azure_available", new_callable=PropertyMock, return_value=True), \
         patch.object(TranslatorService, "_azure_translate", side_effect=RuntimeError("401 key=SECRET")), \
         patch("app.services.translator.GoogleTranslator", Good):
        assert svc.translate_text_strict("Please translate this text.", "en", "hi") == "अनुवादित पाठ"


def test_azure_error_text_never_reaches_the_raised_error(svc):
    with patch.object(TranslatorService, "azure_available", new_callable=PropertyMock, return_value=True), \
         patch.object(TranslatorService, "_azure_translate", side_effect=RuntimeError("key=SECRET-AZURE-KEY")), \
         patch("app.services.translator.GoogleTranslator", side_effect=ConnectionError("down")):
        with pytest.raises(TranslationError) as e:
            svc.translate_text_strict("Please translate this text.", "en", "hi")
    assert "SECRET" not in str(e.value) and "azure:RuntimeError" in str(e.value)


def test_long_text_is_chunked_below_the_provider_limit_and_rebuilt(svc):
    text = "Heading line\n\n" + ("This sentence describes one eligibility condition. " * 300) + "\nLast line"
    seen = []

    class Recorder:
        def __init__(self, source, target): pass
        def translate(self, t):
            seen.append(t)
            return "अनुवाद " + t
    with patch("app.services.translator.GoogleTranslator", Recorder):
        out = svc.translate_text_strict(text, "en", "hi")
    assert len(seen) >= 3 and all(len(s) <= 4000 for s in seen)
    assert out.count("अनुवाद") == len(seen) and out.count("\n") >= text.count("\n")
    assert "".join(c + s for c, s in chunk_text(text)).split() == text.split()


# ====================================================================== localizer: facts survive, failures are explicit
TERMS = ["PM-KISAN", "Pradhan Mantri Fasal Bima Yojana (PMFBY)", "PMFBY", "Saarthi.AI"]


def good_translate(text, source, target):
    return fake_translate(text, target)


def test_facts_are_masked_before_translation_and_restored_exactly():
    seen = {}

    def spy(text, source, target):
        seen["masked"] = text
        return fake_translate(text, target)
    res = localize_answer(FACT_ANSWER, "hi", TERMS, spy)
    assert res.status == "translated" and res.language == "hi"
    for fact in ("PM-KISAN", "Rs. 6,000", "Rs. 2,000", "155261", "https://pmkisan.gov.in/", "31.03.2026", "Saarthi.AI"):
        assert fact in res.text, fact                                         # exactly as in the grounded English answer
        assert fact not in seen["masked"], fact                               # the translator never saw (so cannot alter) them
    assert is_text_in_language(res.text, "hi") and "dataset lists" not in res.text


def test_official_scheme_names_and_acronyms_are_preserved():
    en = "Pradhan Mantri Fasal Bima Yojana (PMFBY) covers notified crops. Farmers pay 2% for Kharif and 1.5% for Rabi."
    res = localize_answer(en, "ta", TERMS, good_translate)
    assert res.status == "translated"
    for fact in ("Pradhan Mantri Fasal Bima Yojana (PMFBY)", "2%", "1.5%"):
        assert fact in res.text


def test_list_numbering_is_layout_not_a_protected_fact():
    masked, originals = protect_spans("1. First step\n2) Second step costs Rs. 500", [])
    assert masked.startswith("1. First step\n2) Second step") and originals == ["Rs. 500"]


def test_caveats_survive_with_their_numbers():
    en = "This is marked as needing verification. The period was listed as Till 31.03.2026; PM-KUSUM 2.0 is not yet finalised."
    res = localize_answer(en, "mr", ["PM-KUSUM"], good_translate)
    assert res.status == "translated" and "31.03.2026" in res.text and "PM-KUSUM" in res.text and "2.0" in res.text


@pytest.mark.parametrize("mode,reason", [
    ("raise", "all_providers_failed"), ("echo", "provider_returned_input_unchanged"), ("drop_token", "protected_term_lost"),
    ("garbage", "invalid_provider_result"), ("latin", "result_not_in_target_language"),
])
def test_every_failure_mode_returns_english_labelled_english(mode, reason):
    fake = FakeTranslator(mode=mode)
    res = localize_answer(FACT_ANSWER, "hi", TERMS, fake.translate_text_strict)
    assert (res.status, res.language, res.reason, res.text) == ("failed", "en", reason, FACT_ANSWER)


def test_result_in_the_wrong_script_is_rejected():
    mostly_latin = lambda text, s, t: "QZ0QZ lists documents " + NATIVE_WORD["hi"] + " for the farmer in the dataset today"
    res = localize_answer("Saarthi.AI lists documents for the farmer in the dataset today", "hi", ["Saarthi.AI"], mostly_latin)
    assert (res.status, res.reason, res.language) == ("failed", "result_not_in_target_language", "en")


def test_translator_that_spaces_or_lowercases_tokens_is_tolerated():
    def sloppy(text, source, target):
        return fake_translate(text, target).replace("QZ3QZ", "qz 3 qz")
    res = localize_answer(FACT_ANSWER, "hi", TERMS, sloppy)
    assert res.status == "translated"
    for fact in ("PM-KISAN", "Rs. 6,000", "Rs. 2,000", "155261", "https://pmkisan.gov.in/", "31.03.2026"):
        assert fact in res.text, fact
    assert restore_spans("QZ 0 QZ और qz1qz", ["A", "B"]) == "A और B"


def test_english_target_is_a_no_op():
    res = localize_answer(FACT_ANSWER, "en", TERMS, lambda *a: (_ for _ in ()).throw(AssertionError("must not translate")))
    assert (res.status, res.language, res.text) == ("not_needed", "en", FACT_ANSWER)


# ====================================================================== /query: English, Hindi, Marathi and all languages
def test_english_response_is_returned_untranslated(run):
    r, t, g = run(language="en")
    d = r.json()
    assert r.status_code == 200 and d["answer"] == FACT_ANSWER and d["response_language"] == "en"
    assert r.headers["X-Translation-Status"] == "not_needed" and r.headers["X-Answer-Language"] == "en"
    assert not t.kinds("strict") and r.headers["X-Answer-Source"] == "groq"


@pytest.mark.parametrize("lang", ["hi", "mr"])
def test_hindi_and_marathi_responses_are_localized_with_facts_intact(run, lang):
    r, t, g = run(language=lang)
    d = r.json()
    assert r.status_code == 200 and d["response_language"] == lang
    assert r.headers["X-Translation-Status"] == "translated" and r.headers["X-Requested-Language"] == lang
    assert is_text_in_language(d["answer"], lang) and NATIVE_WORD[lang] in d["answer"]
    for fact in ("PM-KISAN", "Rs. 6,000", "155261", "https://pmkisan.gov.in/", "31.03.2026"):
        assert fact in d["answer"]
    assert [c for c in t.kinds("strict")][0][2:] == ("en", lang)        # translated FROM English TO the selected language


@pytest.mark.parametrize("lang", NON_ENGLISH)
def test_every_supported_language_gets_its_own_script_and_accurate_metadata(run, lang):
    r, _, _ = run(language=lang)
    d = r.json()
    assert d["response_language"] == lang == r.headers["X-Answer-Language"]
    assert is_text_in_language(d["answer"], lang) and NATIVE_WORD[lang] in d["answer"]
    others = [NATIVE_WORD[o] for o in NON_ENGLISH if NATIVE_WORD[o] != NATIVE_WORD[lang]]
    assert not any(w in d["answer"] for w in others)                     # no other language leaked in


def test_romanized_field_only_for_a_successful_translation(run):
    r, _, _ = run(language="hi", translator=FakeTranslator(romanize="Vartaman dataset..."))
    assert r.json()["answer_romanized"] == "Vartaman dataset..."
    r, _, _ = run(language="hi", translator=FakeTranslator(mode="raise", romanize="should not appear"))
    assert r.json()["answer_romanized"] is None
    r, _, _ = run(language="hi", translator=FakeTranslator(romanize=12345))      # non-string provider output is dropped, not a 500
    assert r.status_code == 200 and r.json()["answer_romanized"] is None


# ====================================================================== non-English questions
def test_hindi_question_is_translated_for_retrieval_and_the_answer_translated_back(run):
    t = FakeTranslator(detect="hi")
    t.english_for = {"पीएम किसान के लिए कौन से दस्तावेज चाहिए": QUERY}
    r, t, g = run(query="पीएम किसान के लिए कौन से दस्तावेज चाहिए", language="hi", translator=t)
    d = r.json()
    assert r.status_code == 200 and d["intent"] == "document_requirements"
    assert t.kinds("legacy")[0][1:] == ("पीएम किसान के लिए कौन से दस्तावेज चाहिए", "hi", "en")
    assert QUERY in g.calls[0]["messages"][-1]["content"]                    # Groq saw the English question + PM-KISAN records
    assert ">>> ANSWER FIELD - DOCUMENTS" in g.calls[0]["messages"][-1]["content"]
    assert d["response_language"] == "hi" and is_text_in_language(d["answer"], "hi")


def test_english_question_with_hindi_selected_is_not_translated_as_hindi(run):
    r, t, _ = run(query=QUERY, language="hi")
    assert not t.kinds("legacy") and r.json()["response_language"] == "hi"


def test_hindi_question_with_english_selected_is_translated_for_retrieval_but_answered_in_english(run):
    t = FakeTranslator(detect="en")
    t.english_for = {"पीएम किसान के लिए कौन से दस्तावेज चाहिए": QUERY}
    r, t, _ = run(query="पीएम किसान के लिए कौन से दस्तावेज चाहिए", language="en", translator=t)
    assert t.kinds("legacy")[0][2:] == ("auto", "en") and r.json()["response_language"] == "en" and not t.kinds("strict")
    assert r.json()["intent"] == "document_requirements"


def test_question_in_another_script_than_the_selected_language_uses_auto_source(run):
    r, t, _ = run(query="பிஎம் கிசான் ஆவணங்கள்", language="hi")
    assert t.kinds("legacy")[0][2] == "auto" and r.json()["response_language"] == "hi"


# ====================================================================== incorrect or missing language codes
def test_missing_language_field_triggers_detection(run):
    r, t, _ = run(omit_language=True, translator=FakeTranslator(detect="hi"))
    assert r.status_code == 200 and t.kinds("detect") and r.json()["response_language"] == "hi"


def test_auto_and_empty_language_detect_from_the_question(run):
    for lang in ("auto", "", "  "):
        r, t, _ = run(language=lang, translator=FakeTranslator(detect="mr"))
        assert r.status_code == 200 and r.json()["response_language"] == "mr", repr(lang)


def test_unsupported_language_code_is_reported_not_silently_accepted(run):
    r, t, _ = run(language="xyz")
    assert r.status_code == 200 and r.headers["X-Language-Note"] == "unsupported_language_code"
    assert r.json()["response_language"] == "en" and r.headers["X-Requested-Language"] == "en"


def test_detected_unsupported_language_falls_back_to_english(run):
    r, _, _ = run(language="auto", translator=FakeTranslator(detect="fr"))
    assert r.status_code == 200 and r.json()["response_language"] == "en"
    r, _, _ = run(language="auto", translator=FakeTranslator(detect=None))
    assert r.status_code == 200 and r.json()["response_language"] == "en"


@pytest.mark.parametrize("code", ["HI", "hi-IN", "hi_IN", " hi "])
def test_language_code_variants_are_normalized(run, code):
    r, _, _ = run(language=code)
    assert r.json()["response_language"] == "hi"


# ====================================================================== translation failures are explicit
@pytest.mark.parametrize("mode", ["raise", "echo", "drop_token", "garbage", "latin"])
def test_translation_failure_returns_english_and_says_so(run, mode, caplog):
    with caplog.at_level(logging.WARNING):
        r, _, _ = run(language="hi", translator=FakeTranslator(mode=mode))
    d = r.json()
    assert r.status_code == 200 and d["answer"] == FACT_ANSWER
    assert d["response_language"] == "en"                                    # NOT "hi": the text is English
    assert r.headers["X-Requested-Language"] == "hi" and r.headers["X-Answer-Language"] == "en"
    assert r.headers["X-Translation-Status"] == "failed" and r.headers["X-Translation-Error"]
    assert "could not be translated to hi" in caplog.text
    assert d["answer_romanized"] is None


def test_failed_translation_keeps_english_recommendation_cards(run):
    r, _, _ = run(language="hi", translator=FakeTranslator(mode="raise"))
    cards = r.json()["recommended_schemes"]
    assert cards and all(c["description"].isascii() for c in cards)


# ====================================================================== Groq failure and offline fallback
def test_groq_failure_falls_back_to_offline_and_is_still_localized(run):
    r, t, g = run(language="hi", groq_exc=ConnectionError("down"))
    d = r.json()
    assert r.headers["X-Answer-Source"] == "offline" and r.status_code == 200
    assert d["response_language"] == "hi" and is_text_in_language(d["answer"], "hi")
    assert "PM-KISAN" in d["answer"]                                         # the record's official scheme name survives
    assert r.headers["X-Translation-Status"] == "translated"


def test_offline_fallback_hindi_is_fully_localized_not_english_with_hindi_headings(run):
    r, _, _ = run(language="hi", groq=False)
    d = r.json()
    assert r.headers["X-Answer-Source"] == "offline"
    assert d["response_language"] == "hi" and script_ratio(d["answer"].replace("**", ""), "devanagari") > 0.45


def test_offline_fallback_with_translation_down_is_english_and_labelled_english(run):
    r, _, _ = run(language="hi", groq=False, translator=FakeTranslator(mode="raise"))
    d = r.json()
    assert r.headers["X-Answer-Source"] == "offline" and d["response_language"] == "en"
    assert "Required Documents" in d["answer"] and r.headers["X-Translation-Status"] == "failed"


# ====================================================================== caching never crosses languages
def test_groq_answers_are_cached_in_english_and_localized_per_request(run):
    r1, t1, g1 = run(language="hi")
    r2, t2, g2 = run(language="mr", keep_cache=True)
    r3, t3, g3 = run(language="en", keep_cache=True)
    assert len(g1.calls) == 1 and len(g2.calls) == 0 and len(g3.calls) == 0         # one model call, then English cache hits
    a1, a2, a3 = (r.json()["answer"] for r in (r1, r2, r3))
    assert NATIVE_WORD["hi"] in a1 and NATIVE_WORD["mr"] in a2 and NATIVE_WORD["hi"] not in a2
    assert a3 == FACT_ANSWER and r3.json()["response_language"] == "en"
    assert (r1.json()["response_language"], r2.json()["response_language"]) == ("hi", "mr")


def test_cache_key_includes_the_language():
    k = lambda lang: rag_engine._groq_cache_key("m", "ctx", "q", {}, "", lang)
    assert k("en") != k("hi") and k("hi") != k("mr") and k("hi") == k("hi")


# ====================================================================== recommendation cards
def test_cards_keep_official_names_and_localize_descriptions(run):
    r, _, _ = run(language="hi")
    cards = r.json()["recommended_schemes"]
    assert cards[0]["name"] == "PM-KISAN"                                    # never machine-translated
    assert is_text_in_language(cards[0]["description"], "hi")


def test_a_failing_card_translation_does_not_break_the_answer_or_the_other_fields(run):
    class AnswerOnly(FakeTranslator):
        def translate_text_strict(self, text, source="auto", target="en"):
            first = not self.kinds("strict")
            out = super().translate_text_strict(text, source, target)
            if not first:                      # the answer is translated fine; every card description then fails
                raise TranslationError("all_providers_failed", "card")
            return out
    r, t, _ = run(language="hi", translator=AnswerOnly())
    d = r.json()
    assert r.status_code == 200 and d["response_language"] == "hi" and is_text_in_language(d["answer"], "hi")
    assert d["recommended_schemes"][0]["name"] == "PM-KISAN"
    assert all(c["description"].isascii() for c in d["recommended_schemes"])        # English cards, clearly not "translated"
