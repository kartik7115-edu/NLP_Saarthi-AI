"""Retrieval preparation over canonical scheme records (schema v2).

Pure functions, no I/O and no LLM:
  resolve_schemes()   - deterministic scheme resolution from a query via aliases
  filter_schemes()    - structured filtering (state/ALL, crop, farmer type, gender, ...)
  follow_replacement()- redirect superseded/repealed schemes to their current replacement

These are the building blocks for the planned pipeline
  query -> entities -> scheme resolution -> structured filter -> small context -> LLM.
They are not yet wired into rag_engine.search_similar.
"""
import re
from typing import Any, Dict, Iterable, List, Optional

_PUNCT = re.compile(r"[\s\-_.,;:?!()\[\]{}\"'/\\|।–—]+")

FARM_OCCUPATIONS = {"farmer", "fisherman", "livestock_farmer", "dairy_farmer", "poultry_farmer"}


def normalize(text: str) -> str:
    return _PUNCT.sub(" ", str(text).lower()).strip()


def _tokens(text: str) -> List[str]:
    n = normalize(text)
    return n.split(" ") if n else []


def build_alias_index(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Alias table sorted longest-first so 'PM Kisan Maan Dhan' wins over 'PM Kisan'."""
    entries = []
    for r in records:
        names = [r.get("name", "")] + list(r.get("aliases", []))
        seen = set()
        for n in names:
            toks = _tokens(n)
            key = " ".join(toks)
            if toks and key not in seen:
                seen.add(key)
                entries.append({"id": r["id"], "tokens": toks, "text": key})
    entries.sort(key=lambda e: (-len(e["tokens"]), -len(e["text"])))
    return entries


def resolve_schemes(query: str, records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return schemes explicitly named in the query, in order of appearance.

    Matching is on whole-token sequences (no substring matching), longest alias first,
    and a token span can only be claimed once. Each result is
    {"id", "matched", "record"}. Returns [] if no scheme is named.
    """
    by_id = {r["id"]: r for r in records}
    q = _tokens(query)
    index = build_alias_index(records)
    claimed = [False] * len(q)
    found = []
    for e in index:
        t = e["tokens"]
        n = len(t)
        for i in range(len(q) - n + 1):
            if q[i:i + n] == t and not any(claimed[i:i + n]):
                for j in range(i, i + n):
                    claimed[j] = True
                found.append((i, e["id"], " ".join(t)))
    found.sort()
    out, seen = [], set()
    for _, sid, matched in found:
        if sid not in seen:
            seen.add(sid)
            out.append({"id": sid, "matched": matched, "record": by_id[sid]})
    return out


def follow_replacement(record: Dict[str, Any], records: List[Dict[str, Any]], max_hops: int = 5) -> Dict[str, Any]:
    """Follow superseded_by links to the current record (returns the record itself if active)."""
    by_id = {r["id"]: r for r in records}
    cur = record
    for _ in range(max_hops):
        nxt = cur.get("superseded_by")
        if cur.get("status", "active") == "active" or not nxt or nxt not in by_id:
            break
        cur = by_id[nxt]
    return cur


def _state_ok(r: Dict[str, Any], state: Optional[str]) -> bool:
    if not state:
        return True
    states = [s.lower() for s in r.get("states", [])]
    return "all" in states or state.lower() in states


def _crop_ok(r: Dict[str, Any], crop: Optional[str]) -> bool:
    if not crop:
        return True
    crops = [c.lower() for c in r.get("crops", [])]
    # NOTIFIED_CROPS (e.g. crop insurance) may cover the crop depending on the state/season notification.
    return "all" in crops or "notified_crops" in crops or crop.lower() in crops


def filter_schemes(
    records: List[Dict[str, Any]],
    state: Optional[str] = None,
    crop: Optional[str] = None,
    farmer_type: Optional[str] = None,
    gender: Optional[str] = None,
    occupation: Optional[str] = None,
    activity: Optional[str] = None,
    domain: Optional[str] = None,
    include_inactive: bool = False,
    land_ha: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Structured filter on canonical records. Central ('ALL') schemes always pass the state test.

    occupation='farmer' keeps only schemes whose beneficiary occupations include a farm occupation,
    so generic non-farm schemes (e.g. women cash-transfer schemes) never match farmer queries
    just because of a shared word or gender.
    """
    out = []
    for r in records:
        if not include_inactive and r.get("status", "active") != "active":
            continue
        b = r.get("beneficiary", {})
        if not _state_ok(r, state) or not _crop_ok(r, crop):
            continue
        if occupation:
            occ = set(b.get("occupations", []))
            want = FARM_OCCUPATIONS if occupation in ("farmer", "farm") else {occupation}
            if not (occ & want):
                continue
        if farmer_type and farmer_type not in b.get("farmer_types", []):
            continue
        if gender:
            g = b.get("genders", ["any"])
            if "any" not in g and gender not in g:
                continue
        if activity and activity not in r.get("activities", []):
            continue
        if domain and domain not in r.get("domains", []):
            continue
        if land_ha is not None:
            mx = b.get("land_max_ha")
            if mx is not None and land_ha > mx:
                continue
        out.append(r)
    return out


def rank_by_specificity(
    records: List[Dict[str, Any]],
    state: Optional[str] = None,
    crop: Optional[str] = None,
    farmer_type: Optional[str] = None,
    gender: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Order filtered schemes so the most specific ones come first (stable otherwise).

    A state-specific scheme outranks a national one; a scheme that names the crop outranks
    ALL/NOTIFIED_CROPS; a scheme explicitly for the requested gender/farmer type outranks 'any'.
    """
    def score(r):
        s = 0
        if state and "ALL" not in r.get("states", []) and state.lower() in [x.lower() for x in r.get("states", [])]:
            s += 3
        if crop and crop.lower() in [c.lower() for c in r.get("crops", [])]:
            s += 3
        b = r.get("beneficiary", {})
        if gender and gender in b.get("genders", []) and "any" not in b.get("genders", []):
            s += 2
        if farmer_type and farmer_type in b.get("farmer_types", []):
            s += 1
        return -s

    return sorted(records, key=score)
