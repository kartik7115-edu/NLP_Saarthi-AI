"""Stage 3: structured retrieval and query understanding.

Covers exact-scheme queries, entity queries, negative/relevance tests, state applicability,
superseded/repealed handling, the exposed ranking signals, intent detection, the /query endpoint
and regression guards on the evaluation metrics (scripts/eval_retrieval.py).
"""
import os
import sys
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.main import app  # noqa: E402
from app.services.intent_detection import detect_intent  # noqa: E402
from app.services.query_understanding import understand_query  # noqa: E402
from app.services.rag_engine import rag_engine  # noqa: E402
from app.services.scheme_loader import load_schemes  # noqa: E402

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def _loaded():
    if not rag_engine._all_schemes:
        rag_engine.load_documents()


def ids(query, **kw):
    return [c.id for c in rag_engine.retrieve(query, **kw).results]


def top(query, **kw):
    return rag_engine.retrieve(query, **kw).results[0]


# =============================================================== exact scheme queries
@pytest.mark.parametrize("query,expected,field", [
    ("What documents are required for PM-KISAN?", "pm-kisan", "documents"),
    ("What benefits does PM-KISAN provide?", "pm-kisan", "benefits"),
    ("Who is eligible for PMFBY?", "pmfby", "eligibility"),
    ("How do I apply for KCC?", "kisan-credit-card", "application"),
    ("What documents are required for KCC?", "kisan-credit-card", "documents"),
])
def test_exact_scheme_is_retrieved_first_and_alone(query, expected, field):
    r = rag_engine.retrieve(query)
    assert [c.id for c in r.results] == [expected]
    t = r.results[0]
    assert t.tier == "1_exact_scheme" and t.signals["exact_scheme_match"] is True
    assert t.signals["intent_field_match"] == field
    assert t.focus["field"] == field and t.focus["available"] is True and t.focus["items"]


@pytest.mark.parametrize("query", [
    "PM Kisan", "PM-KISAN", "pm kisan scheme details", "PM Kisan Samman Nidhi",
    "Pradhan Mantri Kisan Samman Nidhi", "PMKISAN", "प्रधानमंत्री किसान सम्मान निधि", "पीएम किसान",
])
def test_alias_variants_resolve_to_the_same_scheme(query):
    assert ids(query) == ["pm-kisan"]


def test_longest_alias_wins_over_shorter_prefix():
    assert ids("What is the PM Kisan Maan Dhan pension?") == ["pm-kisan-maan-dhan"]


def test_multiple_named_schemes_are_all_returned_in_order_of_mention():
    assert ids("Compare PM-KISAN and PMFBY") == ["pm-kisan", "pmfby"]


def test_focus_field_reports_missing_information_instead_of_guessing():
    r = rag_engine.retrieve("What documents are required for PM-KUSUM?")
    assert [c.id for c in r.results] == ["pm-kusum"]
    assert r.results[0].focus["available"] is False
    assert any("no documents information" in n for n in r.notes)


# =============================================================== negative / relevance tests
def test_pm_kisan_query_never_ranks_pm_cares_above_pm_kisan():
    r = rag_engine.retrieve("What documents are required for PM-KISAN?")
    assert r.results[0].id == "pm-kisan"
    by_id = {c.id: c for c in r.candidates}
    assert by_id["pm-cares-for-children"].score < by_id["pm-kisan"].score
    assert "pm-cares-for-children" not in [c.id for c in r.results]


def test_generic_words_do_not_retrieve_schemes():
    # "are", "for", "what", "documents", "required" are stopwords/intent words, not scheme evidence.
    for q in ["What documents are required?", "are for what", "hello how are you", "asdfghjkl 12345", ""]:
        assert ids(q) == [], q


def test_no_substring_matching():
    # 'are' is a substring of 'cares', 'for' of 'FPO scheme'...: neither may produce a match.
    r = rag_engine.retrieve("what are the documents for farmers")
    assert "pm-cares-for-children" not in [c.id for c in r.results]
    assert "pm-cares-for-children" not in [c.id for c in r.candidates if c.signals["qualifiers"]]


def test_punctuation_and_hyphen_variants():
    for q in ["PM-KISAN?", "pm_kisan!!", "(PM Kisan)", "PM-Kisan,"]:
        assert ids(q) == ["pm-kisan"], q


def test_non_farm_schemes_are_not_returned_for_farmer_questions():
    got = set(ids("I am a farmer in Maharashtra. What schemes can I apply for?", top_k=20))
    assert not got & {"mukhyamantri-majhi-ladki-bahin-yojana", "mudra-loan-pmmy", "pm-svanidhi", "pm-cares-for-children",
                      "agnipath", "post-matric-scholarship-sc"}


def test_street_vendor_and_business_loans_not_returned_for_dairy_loans():
    got = set(ids("Are there loans for dairy farmers?", top_k=20))
    assert "kisan-credit-card" in got
    assert not got & {"pm-svanidhi", "mudra-loan-pmmy"}


def test_women_farmer_query_does_not_return_generic_women_schemes():
    got = set(ids("Are there schemes for women farmers?", top_k=20))
    assert "smam-farm-mechanisation" in got           # the only farm scheme that names women
    assert not got & {"mukhyamantri-majhi-ladki-bahin-yojana", "ladli-behna-yojana-mp", "ysr-cheyutha", "mahtari-vandana-yojana"}


def test_explicit_topic_removes_unrelated_farm_schemes():
    got = ids("What irrigation schemes are available for farmers?", top_k=20)
    assert got == ["pmksy-per-drop-more-crop", "pm-kusum"]


# =============================================================== entity queries
def test_cotton_farmers_in_maharashtra():
    r = rag_engine.retrieve("What schemes are available for cotton farmers in Maharashtra?")
    u = r.understanding
    assert (u.intent, u.state, u.crop, u.beneficiary) == ("scheme_search", "Maharashtra", "cotton", "farmer")
    got = [c.id for c in r.results]
    assert got[0] == "namo-shetkari-mahasanman-nidhi"                  # Maharashtra + farmer first
    assert {"pm-kisan", "pmfby", "kisan-credit-card"} <= set(got)      # central schemes stay eligible
    assert not set(got) & {"rythu-bharosa", "rythu-bima", "annadata-sukhibhava", "cm-kisan-odisha",
                           "midh-horticulture", "mukhyamantri-majhi-ladki-bahin-yojana"}
    # crop-specific wording: no cotton scheme exists, and the result says so instead of pretending
    assert r.coverage["crop_specific_match"] is False and r.coverage["state_specific_match"] is True
    assert any("specific to cotton" in n for n in r.notes)
    # a scheme that lists cotton-relevant notified crops outranks all-crop central schemes
    order = [c.id for c in r.results]
    assert order.index("pmfby") < order.index("pm-kisan")


def test_farmer_in_maharashtra_ranks_state_scheme_then_central_farm_support():
    r = rag_engine.retrieve("I am a farmer in Maharashtra. What schemes can I apply for?")
    got = [c.id for c in r.results]
    assert got[0] == "namo-shetkari-mahasanman-nidhi"
    assert set(got[1:]) <= {"pm-kisan", "pmfby", "kisan-credit-card", "pm-kisan-maan-dhan"}
    assert r.results[0].tier == "2_entity_match" and all(c.tier == "3_applicable_central" for c in r.results[1:])


def test_acres_and_crop_imply_a_farmer_without_the_word_farmer():
    r = rag_engine.retrieve("I have 2 acres of cotton in Maharashtra. What schemes can I apply for?")
    u = r.understanding
    assert u.intent == "scheme_search" and u.state == "Maharashtra" and u.crop == "cotton"
    assert u.beneficiary == "farmer" and u.occupation_inferred_from and abs(u.land_ha - 0.809) < 0.01
    assert u.farmer_types_inferred == ["marginal"]                      # < 1 hectare
    assert r.results and r.results[0].id == "namo-shetkari-mahasanman-nidhi"


def test_land_size_above_a_scheme_cap_is_penalised_not_hidden():
    small = {c.id: c for c in rag_engine.retrieve("I have 1 acre of wheat. Which schemes help me?", top_k=20).candidates}
    big = {c.id: c for c in rag_engine.retrieve("I have 10 acres of wheat. Which schemes help me?", top_k=20).candidates}
    assert big["pm-kisan-maan-dhan"].signals.get("land_size_exceeds_cap") is True
    assert big["pm-kisan-maan-dhan"].score < small["pm-kisan-maan-dhan"].score


def test_dairy_and_livestock_queries():
    assert ids("Are there loans for dairy farmers?")[0] == "kisan-credit-card"
    assert ids("Which schemes help livestock owners with credit?")[0] == "kisan-credit-card"
    assert ids("Which schemes support fishermen with credit?")[0] == "kisan-credit-card"


def test_irrigation_and_other_topic_queries():
    assert ids("What irrigation schemes are available for farmers?")[0] == "pmksy-per-drop-more-crop"
    assert ids("Is there a subsidy for solar pumps for farmers?")[0] == "pm-kusum"
    assert ids("subsidy for buying a tractor")[0] == "smam-farm-mechanisation"
    assert ids("Which scheme helps build a cold storage?")[:2] == ["midh-horticulture", "agriculture-infrastructure-fund"]
    assert ids("Is there crop insurance for farmers?")[0] == "pmfby"
    assert ids("Which scheme supports farmer producer organisations?")[0] == "fpo-10000-scheme"


def test_horticulture_crop_matches_horticulture_scheme_but_cotton_does_not():
    assert "midh-horticulture" in ids("Which schemes help mango farmers?", top_k=20)
    assert "midh-horticulture" not in ids("Which schemes help cotton farmers?", top_k=20)


def test_tenant_farmer_penalises_schemes_that_do_not_list_tenants():
    r = rag_engine.retrieve("Which schemes help tenant farmers?", top_k=20)
    order = [c.id for c in r.results]
    assert order.index("pmfby") < order.index("pm-kisan")             # PMFBY lists tenants, PM-KISAN does not
    pk = next(c for c in r.candidates if c.id == "pm-kisan")
    assert pk.signals["farmer_type_not_listed"] == ["tenant"]


# =============================================================== state applicability
def test_state_in_query_never_means_only_state_schemes():
    r = rag_engine.retrieve("What schemes are available for farmers in Maharashtra?")
    levels = {c.scheme["type"] for c in r.results}
    assert levels == {"central", "state"}


def test_state_specific_ranks_above_generic_when_applicable():
    got = [c.id for c in rag_engine.retrieve("farmer income support in Telangana").results]
    assert got[0] in ("rythu-bharosa",) and "pm-kisan" in got and got.index("rythu-bharosa") < got.index("pm-kisan")


def test_other_states_schemes_are_excluded_when_the_query_names_a_state():
    cands = {c.id: c for c in rag_engine.retrieve("farmer schemes in Maharashtra", top_k=50).candidates}
    for sid in ("rythu-bharosa", "annadata-sukhibhava", "cm-kisan-odisha"):
        assert cands[sid].reason == "state_mismatch" and not cands[sid].selected


def test_profile_state_is_a_soft_fallback_and_the_query_state_wins():
    assert "rythu-bharosa" in ids("farmer scheme", user_state="Telangana")
    assert ids("farmer scheme", user_state="Telangana")[:2] == ["rythu-bharosa", "rythu-bima"]
    got = ids("farmer scheme in Maharashtra", user_state="Telangana")
    assert got[0] == "namo-shetkari-mahasanman-nidhi" and "rythu-bharosa" not in got


def test_named_state_scheme_for_a_different_state_carries_a_note():
    r = rag_engine.retrieve("Rythu Bharosa farmer scheme in Maharashtra")
    assert [c.id for c in r.results] == ["rythu-bharosa"]
    assert any("applies only in Telangana" in n for n in r.notes)


# =============================================================== superseded / repealed
@pytest.mark.parametrize("query,first,replacement", [
    ("What happened to Rythu Bandhu?", "rythu-bandhu", "rythu-bharosa"),
    ("What is the current replacement for KALIA?", "kalia-yojana", "cm-kisan-odisha"),
    ("What is the current status of MGNREGA?", "mgnrega", "vb-g-ram-g"),
])
def test_historical_queries_return_old_scheme_and_its_replacement(query, first, replacement):
    r = rag_engine.retrieve(query)
    assert [c.id for c in r.results] == [first, replacement]
    assert r.results[1].tier == "1b_replacement" and r.results[1].signals["replacement_for"] == first
    assert any("current replacement" in n for n in r.notes)


@pytest.mark.parametrize("query", [
    "farmer income support in Telangana", "farmer schemes in Odisha", "rural employment guarantee scheme",
    "farmer scheme", "schemes for farmers",
])
def test_normal_queries_never_return_inactive_schemes(query):
    got = set(ids(query, top_k=50))
    assert not got & {"rythu-bandhu", "kalia-yojana", "mgnrega"}


def test_search_similar_keeps_old_contract_and_history_still_works():
    res = rag_engine.search_similar("Rythu Bandhu farmer Telangana", user_state="Telangana")
    assert res and all("name" in s and s["type"] in ("central", "state") for s in res)
    assert [s["id"] for s in res][:2] == ["rythu-bandhu", "rythu-bharosa"]


# =============================================================== exposed signals
def test_ranking_signals_are_exposed_for_debugging():
    r = rag_engine.retrieve("What schemes are available for cotton farmers in Maharashtra?")
    namo = r.results[0].signals
    assert namo["state_match"] == "specific" and namo["beneficiary_match"] is True and namo["crop_match"] == "all_crops"
    assert isinstance(r.results[0].score, float)
    pmfby = next(c for c in r.results if c.id == "pmfby").signals
    assert pmfby["state_match"] == "applicable_central" and pmfby["crop_match"] == "notified_crops"
    rows = r.debug_rows(3)
    assert rows[0]["id"] == "namo-shetkari-mahasanman-nidhi" and {"score", "tier", "signals", "selected"} <= set(rows[0])


def test_candidates_expose_why_something_was_not_returned():
    r = rag_engine.retrieve("What irrigation schemes are available for farmers?", top_k=50)
    pk = next(c for c in r.candidates if c.id == "pm-kisan")
    assert pk.selected is False and pk.reason == "topic_mismatch"


# =============================================================== query understanding / intent
@pytest.mark.parametrize("query,intent", [
    ("What documents are required for PM-KISAN?", "document_requirements"),
    ("Who is eligible for PM-KISAN?", "eligibility_check"),
    ("How do I apply for PM-KISAN?", "application_process"),
    ("How much money does PM-KISAN provide?", "benefits_query"),
    ("What benefits does PM-KISAN provide?", "benefits_query"),
    ("How do I apply for PMFBY?", "application_process"),
    ("What schemes are available for cotton farmers in Maharashtra?", "scheme_search"),
    ("I have 2 acres of cotton in Maharashtra. What schemes can I apply for?", "scheme_search"),
    ("What irrigation schemes are available for farmers?", "scheme_search"),
    ("What is the mandi price of cotton today?", "price_query"),
    ("What is the PM-KISAN helpline number?", "helpline_query"),
])
def test_intent_classification(query, intent):
    named = bool(understand_query(query, load_schemes(include_inactive=True)).schemes)
    assert detect_intent(query, has_named_scheme=named) == intent
    assert rag_engine.retrieve(query).understanding.intent == intent


def test_intent_no_longer_uses_substrings():
    assert detect_intent("What schemes are called Yojana for farmers?") != "helpline_query"   # 'call' in 'called'
    assert detect_intent("Can you explain PM-KISAN to me?") == "general_information"          # 'plan' in 'explain'
    assert detect_intent("What is the number of installments in PM-KISAN?") == "benefits_query"


def test_entities_are_read_from_the_question_not_the_profile():
    recs = load_schemes(include_inactive=True)
    u = understand_query("What schemes are available for cotton farmers in Maharashtra?", recs, profile=None)
    assert (u.state, u.state_source, u.crop, u.crop_source, u.beneficiary) == ("Maharashtra", "query", "cotton", "query", "farmer")
    u = understand_query("What is the best scheme for women farmers in Punjab?", recs)
    assert (u.state, u.gender, u.beneficiary) == ("Punjab", "women", "farmer") and "women" in u.farmer_types
    u = understand_query("Which schemes help small and marginal farmers?", recs)
    assert u.farmer_types == ["small", "marginal"]
    u = understand_query("What schemes are there for me", recs, profile={"state": "Telangana", "crop": "cotton", "occupation": "farmer"})
    assert (u.state, u.state_source, u.crop_source, u.occupation_source) == ("Telangana", "profile", "profile", "profile")


def test_state_gazetteer_uses_whole_phrases():
    recs = load_schemes(include_inactive=True)
    assert understand_query("farmers in Andhra Pradesh", recs).state == "Andhra Pradesh"
    assert understand_query("farmers in महाराष्ट्र", recs).state == "Maharashtra"
    assert understand_query("farmers in Orissa", recs).state == "Odisha"
    assert understand_query("Viksit Gram Panchayat plans", recs).crop is None     # 'gram' must not be read as a crop


# =============================================================== /query endpoint (end to end)
@pytest.fixture
def api():
    from app.api.query import _rate_limit
    _rate_limit.clear()
    with patch("app.api.query.brightdata_search", return_value=[]), patch("app.api.query.search_document_links", return_value=[]), \
         patch("app.api.query.enrich_query_context", return_value=""), patch("app.api.query.get_relevant_gov_links", return_value=[]), \
         patch("app.api.query.translator_service") as ts, patch("app.services.rag_engine._build_azure_client", return_value=None):
        ts.detect_language = lambda text: "en"
        ts.translate_text = lambda text, source="auto", target="en": text
        yield


@pytest.mark.parametrize("query,intent", [
    ("What documents are required for PM-KISAN?", "document_requirements"),
    ("Who is eligible for PM-KISAN?", "eligibility_check"),
    ("How do I apply for PMFBY?", "application_process"),
    ("What schemes are available for cotton farmers in Maharashtra?", "scheme_search"),
])
def test_query_endpoint_uses_structured_retrieval(api, query, intent):
    r = client.post("/query", json={"query": query, "language": "en"})
    assert r.status_code == 200
    body = r.json()
    assert body["intent"] == intent
    assert set(body) >= {"intent", "answer", "recommended_schemes", "response_language", "doc_links", "nearest_centers"}


def test_query_endpoint_answer_is_about_the_named_scheme_only(api):
    body = client.post("/query", json={"query": "What documents are required for PM-KISAN?", "language": "en"}).json()
    assert "PM-KISAN" in body["answer"]
    for noise in ("PM CARES", "Fasal Bima", "Kisan Credit Card", "Horticulture", "Producer Organi"):
        assert noise not in body["answer"], noise
    assert body["recommended_schemes"][0]["name"] == "PM-KISAN"           # recommendations panel no longer substring-scored


def test_query_endpoint_profile_is_optional_for_entities(api):
    with_profile = client.post("/query", json={"query": "What schemes are available for cotton farmers in Maharashtra?", "language": "en",
                                               "state": "Telangana", "crop": "wheat", "occupation": "farmer"}).json()
    assert "Namo Shetkari" in with_profile["answer"] and "Rythu" not in with_profile["answer"]   # query state beats profile state


# =============================================================== metric regression guards
def test_evaluation_metrics_do_not_regress():
    import eval_retrieval as ev
    rows = ev.run_eval()
    exact = ev.aggregate(rows, "new", {"exact", "exact-hindi"})
    hist = ev.aggregate(rows, "new", {"historical"})
    lst = ev.aggregate(rows, "new", {"list"})
    allq = ev.aggregate(rows, "new", {"exact", "exact-hindi", "historical", "list"})
    assert exact["hit@1"] == 1.0 and exact["mrr"] == 1.0 and exact["p@5"] == 1.0
    assert hist["r@5"] == 1.0 and hist["hit@1"] == 1.0
    assert lst["hit@1"] >= 0.9 and lst["r@5"] >= 0.9 and lst["p@5"] >= 0.7
    old = ev.aggregate(rows, "old", {"exact", "historical", "list"})
    assert allq["mrr"] > old["mrr"] and allq["p@5"] > old["p@5"] + 0.4   # large, not marginal, improvement over the old code
