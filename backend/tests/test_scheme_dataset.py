"""Dataset, compatibility-adapter and golden-query tests for schemes.json (schema v2).

Golden queries 1-10 are exercised at the data layer (alias resolution, structured filters,
supersession). Wiring into rag_engine/the LLM prompt is a separate step.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.services.scheme_loader import load_canonical, load_schemes  # noqa: E402
from app.services.scheme_query import (  # noqa: E402
    filter_schemes, follow_replacement, rank_by_specificity, resolve_schemes,
)
import validate_schemes  # noqa: E402


@pytest.fixture(scope="module")
def recs():
    return load_canonical(include_inactive=True)


@pytest.fixture(scope="module")
def by_id(recs):
    return {r["id"]: r for r in recs}


def ids(rs):
    return {r["id"] for r in rs}


def resolved(query, recs):
    return [m["id"] for m in resolve_schemes(query, recs)]


# ------------------------------------------------------------------ validator
def test_dataset_passes_validator():
    errors, _ = validate_schemes.validate()
    assert errors == []


def test_nothing_marked_verified_without_official_source(recs):
    for r in recs:
        if r["confidence"] == "verified":
            assert r["last_verified"]
            assert any(s["type"] in ("official", "press_release", "gazette") for s in r["sources"])


def test_central_schemes_use_explicit_all(recs):
    for r in recs:
        if r["level"] == "central":
            assert r["states"] == ["ALL"], r["id"]
        else:
            assert "ALL" not in r["states"] and r["states"], r["id"]


def test_no_placeholder_urls(recs):
    for r in recs:
        url = r["application"]["portal_url"]
        assert url == "" or url.startswith("http"), r["id"]
        assert "varies" not in url.lower()


# ------------------------------------------------------------ compat adapter
def test_adapter_exposes_legacy_fields():
    for s in load_schemes(include_inactive=True):
        assert s["type"] in ("central", "state")
        assert isinstance(s["eligibility"], str) and s["eligibility"]
        assert isinstance(s["benefits"], str) and len(s["benefits"]) > 10
        assert isinstance(s["documents"], list) and all(isinstance(d, str) for d in s["documents"])
        assert isinstance(s["documents_links"], list)
        assert isinstance(s["target_group"], str)
        assert "application_url" in s and "helpline" in s and "description" in s
        if s["type"] == "state":
            assert s["state"]
        else:
            assert s["state"] is None  # legacy: central schemes had no state
        if s["application_url"].startswith("http"):
            assert s["application_url"] in s["documents_links"]


def test_canonical_objects_kept_under_detail_keys():
    s = next(x for x in load_schemes() if x["id"] == "pm-kisan")
    assert isinstance(s["eligibility_detail"], dict) and isinstance(s["documents_detail"], list)
    assert isinstance(s["benefits_detail"], dict)


def test_inactive_hidden_by_default_but_loadable():
    default = {s["id"] for s in load_schemes()}
    everything = {s["id"] for s in load_schemes(include_inactive=True)}
    assert {"rythu-bandhu", "kalia-yojana", "mgnrega"} <= everything
    assert not ({"rythu-bandhu", "kalia-yojana", "mgnrega"} & default)


def test_state_filter_includes_central_and_all():
    s = load_schemes(state="Maharashtra")
    assert any(x["id"] == "pm-kisan" for x in s)          # central applies in every state
    assert any(x["id"] == "namo-shetkari-mahasanman-nidhi" for x in s)
    assert not any(x["id"] == "rythu-bharosa" for x in s)  # Telangana only


# --------------------------------------------------------- cleaning / collisions
def test_ladki_bahin_is_a_single_record(recs):
    assert len([r for r in recs if "ladki bahin" in r["name"].lower()]) == 1


def test_ujjwala_and_ujjawala_stay_distinct(recs, by_id):
    assert "ujjwala-yojana-pmuy" in by_id and "ujjawala-anti-trafficking" in by_id
    assert resolved("How do I get an Ujjwala LPG connection?", recs) == ["ujjwala-yojana-pmuy"]
    assert resolved("Tell me about the Ujjawala anti trafficking scheme", recs) == ["ujjawala-anti-trafficking"]


def test_maan_dhan_does_not_resolve_to_pm_kisan(recs):
    assert resolved("What is the PM Kisan Maan Dhan pension?", recs) == ["pm-kisan-maan-dhan"]


def test_known_wrong_legacy_values_removed(by_id):
    pmfby = by_id["pmfby"]
    text = " ".join(pmfby["eligibility"]["criteria"]).lower()
    assert "voluntary" in text and "compulsory" not in text
    nums = [h["number"] for h in pmfby["contacts"]["helplines"]]
    assert nums == ["14447"]
    kcc = by_id["kisan-credit-card"]
    assert "pmkisan.gov.in" not in kcc["application"]["portal_url"]
    types = {i["type"] for i in kcc["benefits"]["items"]}
    assert {"loan_limit", "interest_rate", "interest_subvention", "prompt_repayment_incentive"} <= types
    pk_docs = " ".join(d["name"].lower() for d in by_id["pm-kisan"]["documents"])
    assert "domicile" not in pk_docs and "aadhaar" in pk_docs and "land" in pk_docs


# ------------------------------------------------------------ golden queries
def test_g01_documents_for_pm_kisan(recs, by_id):
    assert resolved("What documents are required for PM-KISAN?", recs) == ["pm-kisan"]
    names = [d["name"] for d in by_id["pm-kisan"]["documents"]]
    assert any("Aadhaar" in n for n in names) and any("Land" in n for n in names)


def test_g02_benefits_of_pm_kisan(recs, by_id):
    assert resolved("What are the benefits of PM-KISAN?", recs) == ["pm-kisan"]
    assert any(i["amount"] == 6000 and i["unit"] == "INR/year" for i in by_id["pm-kisan"]["benefits"]["items"])


def test_g03_eligibility_of_pmfby(recs, by_id):
    assert resolved("Who is eligible for PMFBY?", recs) == ["pmfby"]
    assert by_id["pmfby"]["crops"] == ["NOTIFIED_CROPS"]
    assert "notification" in " ".join(by_id["pmfby"]["eligibility"]["criteria"]).lower()


def test_g04_cotton_farmer_in_maharashtra(recs):
    out = filter_schemes(recs, state="Maharashtra", crop="cotton", occupation="farmer")
    got = ids(out)
    assert {"pm-kisan", "pmfby", "kisan-credit-card", "namo-shetkari-mahasanman-nidhi"} <= got
    assert not (got & {"mukhyamantri-majhi-ladki-bahin-yojana", "rythu-bharosa", "annadata-sukhibhava",
                       "cm-kisan-odisha", "mudra-loan-pmmy", "mgnrega", "rythu-bandhu"})
    assert rank_by_specificity(out, state="Maharashtra", crop="cotton")[0]["id"] == "namo-shetkari-mahasanman-nidhi"


def test_g05_small_farmer_in_andhra_pradesh(recs):
    out = filter_schemes(recs, state="Andhra Pradesh", farmer_type="small", domain="income_support")
    got = ids(out)
    assert {"pm-kisan", "annadata-sukhibhava"} <= got
    assert not (got & {"namo-shetkari-mahasanman-nidhi", "cm-kisan-odisha", "rythu-bharosa", "ysr-cheyutha"})


def test_g06_women_farmers_do_not_return_generic_women_schemes(recs):
    out = filter_schemes(recs, occupation="farmer", gender="women")
    got = ids(out)
    assert got  # general farmer schemes are open to women farmers
    assert not (got & {"mukhyamantri-majhi-ladki-bahin-yojana", "ladli-behna-yojana-mp", "ysr-cheyutha",
                       "mahtari-vandana-yojana", "beti-bachao-beti-padhao"})
    for r in out:
        assert {"farmer"} & set(r["beneficiary"]["occupations"])
    # Known gap: no scheme explicitly for women farmers yet (Stage 2 coverage).
    assert not [r for r in out if "any" not in r["beneficiary"]["genders"]]


def test_g07_what_is_kcc(recs, by_id):
    assert resolved("What is KCC and who can use it?", recs) == ["kisan-credit-card"]
    assert "tenant" in " ".join(by_id["kisan-credit-card"]["eligibility"]["criteria"]).lower()


def test_g08_what_happened_to_rythu_bandhu(recs):
    m = resolve_schemes("What happened to Rythu Bandhu?", recs)
    assert [x["id"] for x in m] == ["rythu-bandhu"]
    assert m[0]["record"]["status"] == "superseded"
    assert follow_replacement(m[0]["record"], recs)["id"] == "rythu-bharosa"


def test_g09_replacement_for_kalia(recs):
    m = resolve_schemes("What is the current replacement for KALIA?", recs)
    assert [x["id"] for x in m] == ["kalia-yojana"]
    assert follow_replacement(m[0]["record"], recs)["id"] == "cm-kisan-odisha"


def test_g10_status_of_mgnrega(recs):
    m = resolve_schemes("What is the current status of MGNREGA?", recs)
    assert [x["id"] for x in m] == ["mgnrega"]
    assert m[0]["record"]["status"] == "repealed"
    assert follow_replacement(m[0]["record"], recs)["id"] == "vb-g-ram-g"


# ----------------------------------------------------------------- misc rules
def test_lexical_overlap_does_not_resolve_unrelated_schemes(recs):
    # stopwords like "are"/"for" must never resolve to PM CARES etc.
    assert resolved("What documents are required for a loan?", recs) == []


def test_farm_word_does_not_pull_non_farm_schemes(recs):
    out = filter_schemes(recs, occupation="farmer")
    assert "mudra-loan-pmmy" not in ids(out)  # Mudra is non-farm even though its text mentions 'farm'


def test_land_size_filter_uses_land_max(recs):
    assert "pm-kisan-maan-dhan" in ids(filter_schemes(recs, occupation="farmer", land_ha=1.5))
    assert "pm-kisan-maan-dhan" not in ids(filter_schemes(recs, occupation="farmer", land_ha=5))
