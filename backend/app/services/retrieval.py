"""Structured scheme retrieval.

    query -> understand_query() -> exact scheme / entity / applicability signals -> ranked schemes

Ranking tiers (higher tiers dominate through much larger weights):
  1. exact scheme match (name or alias)         1000   (+ 900 for the replacement of a superseded scheme)
  2. entity match: state / crop / beneficiary / activity / domain / farmer type / gender  (tens of points each)
  3. applicability: central schemes apply in every state (a state in the query never means "only state schemes")
  4. textual relevance: IDF-weighted whole-token overlap over scheme fields (capped, cannot outrank 1-3)
  5. fallback: weak text relevance alone qualifies a scheme only above a minimum threshold

There is no substring matching anywhere: tokens are normalised, stemmed and compared whole.
Inactive (superseded/repealed) schemes are excluded unless the question names them (or asks about
history), in which case their current replacement is returned with them.
"""
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from app.services.intent_detection import stem, tokenize
from app.services.query_understanding import QueryUnderstanding, understand_query
from app.services.scheme_query import follow_replacement

FARM_OCCUPATIONS = {"farmer", "fisherman", "livestock_farmer", "dairy_farmer", "poultry_farmer"}
CORE_FARM_DOMAINS = {"income_support", "crop_insurance", "agri_credit", "farmer_pension"}
INFRA_DOMAINS = {"agri_infrastructure", "post_harvest_infrastructure"}
INTENT_FIELD = {
    "document_requirements": "documents", "eligibility_check": "eligibility", "benefits_query": "benefits",
    "application_process": "application", "helpline_query": "contacts",
}

# weights
W_EXACT, W_REPLACEMENT = 1000.0, 900.0
W_STATE_SPECIFIC_Q, W_STATE_SPECIFIC_P = 60.0, 30.0
W_CENTRAL_Q, W_CENTRAL_P, W_CENTRAL_NONE = 20.0, 10.0, 5.0
W_CROP_SPECIFIC, W_CROP_CATEGORY, W_CROP_NOTIFIED, W_CROP_ALL = 60.0, 45.0, 12.0, 6.0
W_CROP_PROFILE = 20.0
W_BENEFICIARY_Q, W_BENEFICIARY_P = 40.0, 10.0
W_FARMER_TYPE, W_FARMER_TYPE_INFERRED, W_FARMER_TYPE_MISSING = 15.0, 8.0, -20.0
W_GENDER = 25.0
W_ACTIVITY, W_DOMAIN = 35.0, 30.0
W_INTENT_FIELD, W_CORE_PRIOR = 10.0, 8.0
W_LAND_EXCEEDS, W_PROFILE_STATE_MISMATCH, W_INACTIVE = -40.0, -20.0, -50.0
LEX_CAP, LEX_QUALIFY = 30.0, 6.0

_STOP = set("""a an the this that these those it its is are am was were be been being do does did have has had
i me my we our you your he she they them their what which who whom whose how when where why can could will would
shall should may might must about for of in on to from with without and or but not no so than then there here
any all some also as at by into over under up out off per get gets give gives given want wants need needs know
tell please help provide provides provided available avail apply applying application applications scheme schemes
yojana yojna document documents doc docs required require requirement requirements eligible eligibility benefit
benefits much many one two three kind types type information details detail like such other others more most
ka ki ke hai hain kya kaise""".split())


def _stems(text: str) -> Set[str]:
    return {stem(t) for t in tokenize(text) if t not in _STOP and stem(t) not in _STOP and len(t) > 1}


# ---------------------------------------------------------------------- scheme text fields
def _field_texts(s: Dict[str, Any]) -> Dict[str, str]:
    el = s.get("eligibility_detail") or {}
    ben = s.get("benefits_detail") or {}
    app = s.get("application") or {}
    el_text = " ".join((el.get("criteria") or []) + (el.get("exclusions") or [])) if isinstance(el, dict) else str(s.get("eligibility", ""))
    ben_text = ben.get("summary", "") if isinstance(ben, dict) else str(s.get("benefits", ""))
    return {
        "name": s.get("name", ""),
        "aliases": " ".join(s.get("aliases") or []),
        "topics": " ".join((s.get("domains") or []) + (s.get("activities") or [])).replace("_", " "),
        "summary": s.get("summary") or s.get("description", ""),
        "beneficiary": (s.get("beneficiary") or {}).get("description", "") or s.get("target_group", ""),
        "eligibility": el_text or str(s.get("eligibility", "")),
        "benefits": ben_text or str(s.get("benefits", "")),
        "documents": " ".join(s.get("documents") or []),
        "application": " ".join(app.get("steps") or []),
    }


_FIELD_WEIGHT = {"name": 3.0, "aliases": 3.0, "topics": 2.5, "summary": 1.5, "beneficiary": 1.5,
                 "eligibility": 1.0, "benefits": 1.0, "documents": 1.0, "application": 0.8}


def _focus(s: Dict[str, Any], field_name: Optional[str]) -> Optional[Dict[str, Any]]:
    """The scheme data the question is about (the 'intent field')."""
    if not field_name:
        return None
    if field_name == "documents":
        items = [f"{d.get('name')}" + (f" ({d['notes']})" if d.get("notes") else "") for d in (s.get("documents_detail") or [])]
    elif field_name == "eligibility":
        el = s.get("eligibility_detail") or {}
        items = list(el.get("criteria") or []) + ["Not eligible: " + x for x in (el.get("exclusions") or [])]
        if not items and s.get("eligibility"):
            items = [s["eligibility"]]
    elif field_name == "benefits":
        ben = s.get("benefits_detail") or {}
        items = [ben["summary"]] if ben.get("summary") else ([s["benefits"]] if s.get("benefits") else [])
    elif field_name == "application":
        app = s.get("application") or {}
        items = list(app.get("steps") or [])
        if app.get("portal_url"):
            items.append("Portal: " + app["portal_url"])
    elif field_name == "contacts":
        items = [f"{h.get('number')} ({h.get('label')})" if h.get("label") else str(h.get("number"))
                 for h in ((s.get("contacts") or {}).get("helplines") or []) if h.get("number")]
        if not items and s.get("helpline"):
            items = [s["helpline"]]
    else:
        items = []
    return {"field": field_name, "available": bool(items), "items": items}


# ---------------------------------------------------------------------- result objects
@dataclass
class ScoredScheme:
    scheme: Dict[str, Any]
    score: float
    tier: str
    signals: Dict[str, Any]
    selected: bool = False
    reason: Optional[str] = None
    focus: Optional[Dict[str, Any]] = None

    @property
    def id(self) -> str:
        return self.scheme.get("id") or self.scheme.get("name")


@dataclass
class RetrievalResult:
    understanding: QueryUnderstanding
    results: List[ScoredScheme] = field(default_factory=list)       # selected, ranked
    candidates: List[ScoredScheme] = field(default_factory=list)    # every scored scheme (debug)
    notes: List[str] = field(default_factory=list)
    coverage: Dict[str, Any] = field(default_factory=dict)

    @property
    def schemes(self) -> List[Dict[str, Any]]:
        return [r.scheme for r in self.results]

    def debug_rows(self, n: int = 5, include_unselected: bool = True) -> List[Dict[str, Any]]:
        pool = self.candidates if include_unselected else self.results
        return [{"id": c.id, "name": c.scheme.get("name"), "score": round(c.score, 1), "tier": c.tier,
                 "selected": c.selected, "reason": c.reason, "signals": c.signals} for c in pool[:n]]


# ---------------------------------------------------------------------- lexical scoring
class _Corpus:
    def __init__(self, schemes: List[Dict[str, Any]]):
        self.fields = {}
        self.df: Dict[str, int] = {}
        for s in schemes:
            ft = {k: _stems(v) for k, v in _field_texts(s).items()}
            self.fields[id(s)] = ft
            for tok in set().union(*ft.values()):
                self.df[tok] = self.df.get(tok, 0) + 1
        self.n = max(1, len(schemes))

    def idf(self, tok: str) -> float:
        return math.log(1.0 + self.n / (self.df.get(tok, 0) + 0.5))

    def score(self, s: Dict[str, Any], query_stems: Set[str], intent_field: Optional[str]) -> float:
        ft = self.fields[id(s)]
        total, matched = 0.0, []
        for tok in query_stems:
            best = 0.0
            for fname, toks in ft.items():
                if tok in toks:
                    w = _FIELD_WEIGHT[fname]
                    if intent_field and fname == intent_field:
                        w *= 2
                    best = max(best, w)
            if best:
                total += self.idf(tok) * best
                matched.append(tok)
        raw = total * 2.0
        return LEX_CAP * (1.0 - math.exp(-raw / LEX_CAP)), matched   # smooth saturation: stays strictly increasing


# ---------------------------------------------------------------------- main entry point
def retrieve(query: str, schemes: List[Dict[str, Any]], profile: Optional[Dict[str, Any]] = None,
             top_k: int = 5) -> RetrievalResult:
    """schemes: scheme dicts (adapter output or canonical) INCLUDING superseded/repealed ones."""
    u = understand_query(query, schemes, profile)
    by_id = {s.get("id"): s for s in schemes if s.get("id")}
    active = [s for s in schemes if s.get("status", "active") == "active"]
    corpus = _Corpus(active + [s for s in schemes if s.get("status", "active") != "active"])
    exact_ids = [m["id"] for m in u.schemes]
    exact_rank = {sid: i for i, sid in enumerate(exact_ids)}
    intent_field = INTENT_FIELD.get(u.intent)

    q_stems = {stem(t) for i, t in enumerate(u.tokens) if i not in u.consumed and t not in _STOP and stem(t) not in _STOP and len(t) > 1}
    farm_query = bool(set(u.occupations) & FARM_OCCUPATIONS) and u.occupation_source == "query"
    topic_acts, topic_doms = set(u.activities), set(u.domains)

    cands: List[ScoredScheme] = []
    for s in schemes:
        sid = s.get("id")
        status = s.get("status", "active")
        b = s.get("beneficiary") or {}
        sig: Dict[str, Any] = {"exact_scheme_match": False, "intent_field_match": None, "beneficiary_match": False,
                               "state_match": "none", "crop_match": "none", "activity_matches": [], "domain_matches": [],
                               "farmer_type_matches": [], "gender_match": False, "lexical_score": 0.0, "lexical_terms": []}
        reason: Optional[str] = None
        qualifiers: List[str] = []
        score = 0.0
        tier = "5_generic_fallback"

        # ---- tier 1
        if sid in exact_rank:
            sig["exact_scheme_match"] = True
            sig["matched_text"] = u.schemes[exact_rank[sid]]["matched"]
            score += W_EXACT - 5 * exact_rank[sid]
            tier = "1_exact_scheme"
            qualifiers.append("exact_scheme")
        elif status != "active":
            if u.historical:
                score += W_INACTIVE
            else:
                reason = f"{status}_scheme"

        farm_targeted = bool(set(b.get("occupations") or []) & FARM_OCCUPATIONS)

        # ---- hard applicability exclusions (never applied to an exact scheme match)
        states = [x for x in (s.get("states") or ([s["state"]] if s.get("state") else [])) if x]
        is_central = (s.get("level") or s.get("type")) == "central" or "ALL" in states
        if not sig["exact_scheme_match"] and reason is None:
            if u.state and u.state_source == "query" and not is_central and u.state not in states:
                reason = "state_mismatch"
            sch_occ = set(b.get("occupations") or [])
            if reason is None and farm_query and sch_occ and not (sch_occ & FARM_OCCUPATIONS):
                reason = "occupation_mismatch"      # e.g. street-vendor or small-business schemes for a farmer question
            crops = [c.lower() for c in (s.get("crops") or [])]
            if reason is None and u.crop and u.crop_source == "query":
                if not crops:
                    reason = "crop_not_applicable"
                elif not ({"all", "notified_crops", u.crop.lower()} & set(crops)) and not (u.crop_category and u.crop_category in crops):
                    reason = "crop_mismatch"

        # ---- tier 2/3 signals
        crops = [c.lower() for c in (s.get("crops") or [])]
        if u.state:
            q = u.state_source == "query"
            if not is_central and u.state in states:
                score += W_STATE_SPECIFIC_Q if q else W_STATE_SPECIFIC_P
                sig["state_match"] = "specific" if q else "specific_profile"
            elif is_central:
                score += W_CENTRAL_Q if q else W_CENTRAL_P
                sig["state_match"] = "applicable_central"
            elif not q:
                score += W_PROFILE_STATE_MISMATCH
                sig["state_match"] = "other_state_profile"
            else:
                sig["state_match"] = "other_state"
        elif is_central:
            score += W_CENTRAL_NONE
            sig["state_match"] = "central_no_state"

        if u.crop:
            if u.crop.lower() in crops:
                score += W_CROP_SPECIFIC if u.crop_source == "query" else W_CROP_PROFILE
                sig["crop_match"] = "specific"
                qualifiers.append("crop")
            elif u.crop_category and u.crop_category in crops:
                score += W_CROP_CATEGORY if u.crop_source == "query" else W_CROP_PROFILE
                sig["crop_match"] = "category"
                qualifiers.append("crop")
            elif "notified_crops" in crops:
                score += W_CROP_NOTIFIED
                sig["crop_match"] = "notified_crops"
            elif "all" in crops:
                score += W_CROP_ALL
                sig["crop_match"] = "all_crops"

        q_occ = set(u.occupations)
        if q_occ and (q_occ & set(b.get("occupations") or [])):
            sig["beneficiary_match"] = True
            score += W_BENEFICIARY_Q if u.occupation_source == "query" else W_BENEFICIARY_P
            if u.occupation_source == "query":
                qualifiers.append("beneficiary")

        for ft in u.farmer_types:
            if ft in (b.get("farmer_types") or []):
                score += W_FARMER_TYPE
                sig["farmer_type_matches"].append(ft)
            elif ft in ("tenant", "sharecropper", "landless") and (b.get("farmer_types") or []) and farm_targeted:
                score += W_FARMER_TYPE_MISSING
                sig.setdefault("farmer_type_not_listed", []).append(ft)
        for ft in u.farmer_types_inferred:
            if ft in (b.get("farmer_types") or []):
                score += W_FARMER_TYPE_INFERRED
                sig["farmer_type_matches"].append(ft + " (inferred from land size)")

        if u.gender:
            g = b.get("genders") or ["any"]
            specific = (u.gender in g and "any" not in g) or u.gender in (b.get("farmer_types") or [])
            if specific:
                score += W_GENDER
                sig["gender_match"] = True
                if not farm_query or farm_targeted:
                    qualifiers.append("gender")

        a_hits = sorted(topic_acts & set(s.get("activities") or []))
        d_hits = sorted(topic_doms & set(s.get("domains") or []))
        sig["activity_matches"], sig["domain_matches"] = a_hits, d_hits
        if a_hits or d_hits:
            score += min(70.0, W_ACTIVITY * len(a_hits)) + min(60.0, W_DOMAIN * len(d_hits))
            qualifiers.append("topic")
        elif (topic_acts or topic_doms) and reason is None and not sig["exact_scheme_match"]:
            reason = "topic_mismatch"

        if u.land_ha is not None and b.get("land_max_ha") is not None and u.land_ha > b["land_max_ha"]:
            score += W_LAND_EXCEEDS
            sig["land_size_exceeds_cap"] = True

        # Generic farmer questions ("what schemes can I apply for?") prefer the core farm-support programmes
        # (income, insurance, credit, pension) over infrastructure/market/technology programmes.
        s_domains = set(s.get("domains") or [])
        if (not u.has_topic and (farm_query or u.occupation_source == "profile")
                and (s_domains & CORE_FARM_DOMAINS) and not (s_domains & INFRA_DOMAINS)):
            score += W_CORE_PRIOR
            sig["core_farm_prior"] = True

        # ---- tier 4
        lex, matched = corpus.score(s, q_stems, intent_field)
        sig["lexical_score"], sig["lexical_terms"] = round(lex, 2), sorted(matched)
        score += lex
        if lex >= LEX_QUALIFY:
            qualifiers.append("lexical")

        if intent_field:
            f = _focus(s, intent_field)
            if f and f["available"]:
                sig["intent_field_match"] = intent_field
                if not sig["exact_scheme_match"]:
                    score += W_INTENT_FIELD

        entity_hit = any(q in qualifiers for q in ("crop", "beneficiary", "topic", "gender"))
        if sig["exact_scheme_match"]:
            tier = "1_exact_scheme"
        elif entity_hit and (sig["state_match"] == "specific" or not u.state):
            tier = "2_entity_match"          # state-specific, or no state in play
        elif entity_hit:
            tier = "3_applicable_central"    # entity match that applies through central applicability
        elif "lexical" in qualifiers:
            tier = "4_textual_relevance"
        if reason is None and not qualifiers:
            reason = "no_relevance_signal"

        sig["qualifiers"] = qualifiers
        cands.append(ScoredScheme(scheme=s, score=score, tier=tier, signals=sig, reason=reason,
                                  focus=_focus(s, intent_field) if intent_field else None))

    # ---- replacement of superseded/repealed exact matches
    cand_by_id = {c.id: c for c in cands}
    for sid in exact_ids:
        rec = by_id.get(sid)
        if rec and rec.get("status", "active") != "active":
            nxt = follow_replacement(rec, schemes)
            c = cand_by_id.get(nxt.get("id"))
            if c and nxt.get("id") != sid and not c.signals["exact_scheme_match"]:
                c.score = max(c.score, W_REPLACEMENT)
                c.tier, c.reason = "1b_replacement", None
                c.signals["replacement_for"] = sid
                c.signals["qualifiers"].append("replacement")

    cands.sort(key=lambda c: (-c.score, -c.signals["lexical_score"], c.id))

    # ---- selection
    has_exact = bool(exact_ids)
    selected: List[ScoredScheme] = []
    for c in cands:
        if has_exact:
            keep = c.signals["exact_scheme_match"] or c.signals.get("replacement_for")
            if not keep and c.reason is None:
                c.reason = "exact_scheme_query"
        else:
            keep = c.reason is None
        if keep and len(selected) < top_k:
            c.selected = True
            selected.append(c)
        elif keep:
            c.reason = "below_top_k"

    result = RetrievalResult(understanding=u, results=selected, candidates=cands)
    _annotate(result)
    return result


def _annotate(r: RetrievalResult) -> None:
    """Coverage flags and plain-language notes so callers can say what the data does NOT cover."""
    u = r.understanding
    sel = r.results
    exact = [c for c in sel if c.signals["exact_scheme_match"]]
    r.coverage = {
        "exact_scheme": bool(exact),
        "state_specific_match": any(c.signals["state_match"].startswith("specific") for c in sel),
        "crop_specific_match": any(c.signals["crop_match"] in ("specific", "category") for c in sel),
        "topic_match": any(c.signals["activity_matches"] or c.signals["domain_matches"] for c in sel),
        "empty": not sel,
    }
    n = r.notes
    for c in exact:
        st = c.scheme.get("status", "active")
        if st != "active":
            rep = follow_replacement(c.scheme, [x.scheme for x in r.candidates])
            n.append(f"{c.scheme['name']} is {st}; current replacement: {rep['name']}." if rep.get("id") != c.scheme.get("id")
                     else f"{c.scheme['name']} is {st}.")
        sch_states = [x for x in (c.scheme.get("states") or []) if x != "ALL"]
        if sch_states and u.state and u.state_source == "query" and u.state not in sch_states and (c.scheme.get("level") == "state"):
            n.append(f"{c.scheme['name']} applies only in {', '.join(sch_states)}, not in {u.state}.")
        if c.focus and not c.focus["available"]:
            n.append(f"The knowledge base has no {c.focus['field']} information for {c.scheme['name']}.")
    if not exact:
        if u.crop and u.crop_source == "query" and sel and not r.coverage["crop_specific_match"]:
            n.append(f"No scheme in the knowledge base is specific to {u.crop}; the schemes shown apply to all crops "
                     f"(or to notified crops) and may still cover {u.crop} farmers.")
        if u.state and u.state_source == "query" and sel and not r.coverage["state_specific_match"]:
            n.append(f"No {u.state}-specific scheme was found for this question; the schemes shown are central schemes that apply in {u.state}.")
        if not sel:
            n.append("No relevant scheme was found in the knowledge base for this question.")
