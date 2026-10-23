"""Stage 2 (batch 1) data-layer tests: central farm schemes.

Covers alias matching, state applicability, beneficiary/crop/activity filtering, superseded handling
and verified-vs-needs_review integrity. Retrieval wiring into rag_engine is a later stage.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from app.services.scheme_loader import load_canonical, load_schemes  # noqa: E402
from app.services.scheme_query import filter_schemes, follow_replacement, resolve_schemes  # noqa: E402
import validate_schemes  # noqa: E402

BATCH1 = {
    "agriculture-infrastructure-fund", "e-nam", "soil-health-card", "pm-kusum",
    "pmksy-per-drop-more-crop", "smam-farm-mechanisation", "midh-horticulture", "fpo-10000-scheme",
}
VERIFIED_IN_BATCH1 = {"e-nam"}


@pytest.fixture(scope="module")
def recs():
    return load_canonical(include_inactive=True)


@pytest.fixture(scope="module")
def by_id(recs):
    return {r["id"]: r for r in recs}


def ids(rs):
    return {r["id"] for r in rs}


def resolved(q, recs):
    return [m["id"] for m in resolve_schemes(q, recs)]


def test_dataset_still_valid():
    errors, _ = validate_schemes.validate()
    assert errors == []


def test_batch1_present(by_id):
    assert BATCH1 <= set(by_id)


# --------------------------------------------------------------- alias matching
@pytest.mark.parametrize("query,expected", [
    ("What is the interest subvention under AIF?", "agriculture-infrastructure-fund"),
    ("Tell me about the Agri Infra Fund", "agriculture-infrastructure-fund"),
    ("How do I register on eNAM?", "e-nam"),
    ("e-NAM mandi registration for farmers", "e-nam"),
    ("What is the Soil Health Card scheme?", "soil-health-card"),
    ("मृदा स्वास्थ्य कार्ड कैसे बनवाएं", "soil-health-card"),
    ("Is PM-KUSUM still open for solar pumps?", "pm-kusum"),
    ("PM Kusum subsidy for farmers", "pm-kusum"),
    ("How much subsidy is there under PMKSY?", "pmksy-per-drop-more-crop"),
    ("Per Drop More Crop drip irrigation", "pmksy-per-drop-more-crop"),
    ("What is SMAM for tractors?", "smam-farm-mechanisation"),
    ("MIDH cold storage subsidy", "midh-horticulture"),
    ("How to join the 10,000 FPO scheme?", "fpo-10000-scheme"),
])
def test_exact_alias_matching(query, expected, recs):
    assert resolved(query, recs) == [expected]


def test_pm_kusum_and_pm_kisan_stay_distinct(recs):
    assert resolved("PM Kusum", recs) == ["pm-kusum"]
    assert resolved("PM Kisan", recs) == ["pm-kisan"]
    assert resolved("PM-KISAN and PM-KUSUM", recs) == ["pm-kisan", "pm-kusum"]


def test_no_alias_collisions_with_stage1(recs):
    from validate_schemes import norm
    seen = {}
    for r in recs:
        for a in [r["name"]] + r["aliases"]:
            n = norm(a)
            assert seen.setdefault(n, r["id"]) == r["id"], "%r collides" % a


def test_unrelated_queries_do_not_resolve_to_new_schemes(recs):
    assert resolved("What documents are required for a loan?", recs) == []
    assert resolved("market price of onion today", recs) == []


# --------------------------------------------------------------- state applicability
def test_batch1_is_central_with_explicit_all(by_id):
    for i in BATCH1:
        r = by_id[i]
        assert r["level"] == "central" and r["states"] == ["ALL"], i


@pytest.mark.parametrize("state", ["Maharashtra", "Andhra Pradesh", "Telangana", "Punjab"])
def test_central_batch1_applies_in_every_state(state, recs):
    got = ids(filter_schemes(recs, state=state, occupation="farmer"))
    assert BATCH1 <= got


def test_legacy_state_field_is_none_for_batch1():
    for s in load_schemes():
        if s["id"] in BATCH1:
            assert s["type"] == "central" and s["state"] is None


def test_state_filter_in_loader_includes_batch1():
    assert BATCH1 <= {s["id"] for s in load_schemes(state="Maharashtra")}


# --------------------------------------------------------------- beneficiary filtering
def test_farmer_filter_excludes_non_farm_schemes(recs):
    got = ids(filter_schemes(recs, occupation="farmer"))
    assert BATCH1 <= got
    assert not (got & {"mudra-loan-pmmy", "ladli-behna-yojana-mp", "pm-svanidhi"})


def test_small_farmer_filter(recs):
    got = ids(filter_schemes(recs, occupation="farmer", farmer_type="small"))
    assert {"smam-farm-mechanisation", "pmksy-per-drop-more-crop", "agriculture-infrastructure-fund", "pm-kusum"} <= got


def test_women_farmer_type_matches_only_schemes_that_name_women(recs):
    got = ids(filter_schemes(recs, farmer_type="women"))
    assert "smam-farm-mechanisation" in got
    assert not (got & {"mukhyamantri-majhi-ladki-bahin-yojana", "pm-kisan", "e-nam"})


def test_women_farmers_do_not_pull_generic_women_schemes(recs):
    got = ids(filter_schemes(recs, occupation="farmer", gender="women"))
    assert "smam-farm-mechanisation" in got  # open to women farmers (genders: any)
    assert not (got & {"mukhyamantri-majhi-ladki-bahin-yojana", "ysr-cheyutha", "mahtari-vandana-yojana"})


def test_land_size_filter_does_not_exclude_batch1(recs):
    # None of the batch-1 schemes has a land cap, so a large holding must not filter them out.
    got = ids(filter_schemes(recs, occupation="farmer", land_ha=50))
    assert BATCH1 <= got


# --------------------------------------------------------------- crop / activity filtering
def test_cotton_farmer_gets_crop_agnostic_schemes_but_not_horticulture(recs):
    got = ids(filter_schemes(recs, state="Maharashtra", crop="cotton", occupation="farmer"))
    assert (BATCH1 - {"midh-horticulture"}) <= got
    assert "midh-horticulture" not in got


def test_horticulture_crop_filter_includes_midh(recs):
    got = ids(filter_schemes(recs, crop="horticultural_crops", occupation="farmer"))
    assert "midh-horticulture" in got


def test_notified_crops_schemes_stay_for_any_crop(recs, by_id):
    assert by_id["e-nam"]["crops"] == ["NOTIFIED_CROPS"]
    assert "e-nam" in ids(filter_schemes(recs, crop="cotton", occupation="farmer"))


@pytest.mark.parametrize("activity,expected,unexpected", [
    ("irrigation", {"pmksy-per-drop-more-crop", "pm-kusum"}, {"smam-farm-mechanisation", "e-nam"}),
    ("solar_agriculture", {"pm-kusum"}, {"pmksy-per-drop-more-crop"}),
    ("farm_mechanisation", {"smam-farm-mechanisation"}, {"pm-kusum", "midh-horticulture"}),
    ("storage", {"agriculture-infrastructure-fund", "midh-horticulture"}, {"e-nam"}),
    ("marketing", {"e-nam", "agriculture-infrastructure-fund"}, {"pm-kusum"}),
    ("horticulture", {"midh-horticulture"}, {"smam-farm-mechanisation"}),
    ("farmer_producer_organization", {"fpo-10000-scheme", "agriculture-infrastructure-fund", "e-nam"}, {"pm-kusum"}),
])
def test_activity_filtering(activity, expected, unexpected, recs):
    got = ids(filter_schemes(recs, activity=activity))
    assert expected <= got
    assert not (unexpected & got)


# --------------------------------------------------------------- superseded handling
def test_batch1_has_no_superseded_records_and_stage1_redirects_still_work(recs, by_id):
    for i in BATCH1:
        assert by_id[i]["status"] == "active" and by_id[i]["superseded_by"] is None
        assert follow_replacement(by_id[i], recs)["id"] == i
    assert follow_replacement(by_id["rythu-bandhu"], recs)["id"] == "rythu-bharosa"
    assert follow_replacement(by_id["mgnrega"], recs)["id"] == "vb-g-ram-g"


def test_merged_schemes_document_their_new_parent(by_id):
    # Soil Health Card and PDMC are now components of RKVY: that must be stated, not hidden.
    assert "RKVY" in by_id["soil-health-card"]["name"] + by_id["soil-health-card"]["summary"]
    assert "RKVY" in by_id["pmksy-per-drop-more-crop"]["summary"]


def test_inactive_records_stay_out_of_default_listing_and_batch1_stays_in():
    default = {s["id"] for s in load_schemes()}
    assert BATCH1 <= default
    assert not ({"rythu-bandhu", "kalia-yojana", "mgnrega"} & default)


# --------------------------------------------------------------- verified vs needs_review
def test_only_e_nam_is_verified_in_batch1(by_id):
    for i in BATCH1:
        want = "verified" if i in VERIFIED_IN_BATCH1 else "needs_review"
        assert by_id[i]["confidence"] == want, i


def test_verified_records_have_date_and_official_source(recs):
    for r in recs:
        if r["confidence"] == "verified":
            assert r["last_verified"]
            assert any(s["type"] in ("official", "press_release", "gazette") and s["url"] for s in r["sources"]), r["id"]


def test_needs_review_batch1_records_explain_why(by_id):
    for i in BATCH1 - VERIFIED_IN_BATCH1:
        r = by_id[i]
        assert r["last_verified"] is None, i
        assert r["notes"].startswith("Needs review"), i
        assert len(r["notes"]) > 150, i


def test_known_conflicts_are_documented_not_resolved_silently(by_id):
    assert "every two years" in by_id["soil-health-card"]["notes"] and "3 years" in by_id["soil-health-card"]["notes"]
    assert "31.03.2026" in " ".join(by_id["pm-kusum"]["eligibility"]["criteria"]) and "PM-KUSUM 2.0" in by_id["pm-kusum"]["notes"]
    assert "women" in by_id["smam-farm-mechanisation"]["notes"].lower() and "Conflict" in by_id["smam-farm-mechanisation"]["notes"]
    assert "Conflict" in by_id["midh-horticulture"]["notes"]
    assert "FY 2025-26" in " ".join(by_id["agriculture-infrastructure-fund"]["eligibility"]["criteria"])
    assert "completed" in " ".join(by_id["fpo-10000-scheme"]["eligibility"]["criteria"]).lower()


def test_verified_e_nam_content_matches_official_pages(by_id):
    e = by_id["e-nam"]
    assert any(h["number"] == "1800 270 0224" for h in e["contacts"]["helplines"])
    docs = " ".join(d["name"].lower() for d in e["documents"])
    assert "passbook" in docs and "identity proof" in docs
    assert not any("aadhaar" in d["name"].lower() for d in e["documents"])  # official page does not require Aadhaar
    assert any(i["type"] == "fee" and i["amount"] == 0 for i in e["benefits"]["items"])
    assert all(s["url"].startswith("https://enam.gov.in/") for s in e["sources"])


def test_no_unsourced_application_urls(by_id):
    # URLs that could not be confirmed must stay empty rather than guessed.
    for i in ("smam-farm-mechanisation", "midh-horticulture", "fpo-10000-scheme"):
        assert by_id[i]["application"]["portal_url"] == ""


# --------------------------------------------------------------- key figures
def test_key_figures_recorded_as_found(by_id):
    def items(i, t):
        return [x for x in by_id[i]["benefits"]["items"] if x["type"] == t]
    aif = by_id["agriculture-infrastructure-fund"]
    assert items("agriculture-infrastructure-fund", "interest_subvention")[0]["amount"] == 3
    assert items("agriculture-infrastructure-fund", "credit_guarantee")[0]["amount"] == 20000000
    assert {x["amount"] for x in items("pm-kusum", "central_subsidy")} == {30, 50}
    assert {x["amount"] for x in items("pmksy-per-drop-more-crop", "subsidy")} == {55, 45}
    assert {x["amount"] for x in items("smam-farm-mechanisation", "subsidy")} == {40, 50}
    assert {x["amount"] for x in items("midh-horticulture", "credit_linked_subsidy")} == {35, 50}
    assert items("soil-health-card", "central_support_per_sample")[0]["amount"] == 190
    assert items("fpo-10000-scheme", "equity_grant_limit")[0]["amount"] == 1500000
    assert aif["states"] == ["ALL"]
