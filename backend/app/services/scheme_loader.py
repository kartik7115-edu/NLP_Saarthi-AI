import json
import os
from typing import Any, Dict, List, Optional

from app.config import settings

# ---------------------------------------------------------------------------
# Canonical scheme records (schema v2) are adapted into the flat "legacy" shape
# that the rest of the backend, the API and the frontend already expect.
#
# Legacy keys derived here:  type, scope, state, description, eligibility (str),
# benefits (str), documents (list[str]), target_group, helpline, application_url,
# documents_links.  Canonical objects whose key names collide with legacy string
# keys are exposed as eligibility_detail / benefits_detail / documents_detail.
# All other canonical fields (id, aliases, status, level, states, domains, ...)
# are passed through unchanged.
# ---------------------------------------------------------------------------


def _is_canonical(s: Dict[str, Any]) -> bool:
    return "level" in s and "summary" in s


def _eligibility_text(el: Dict[str, Any]) -> str:
    parts = [c.strip() for c in el.get("criteria", []) if c and c.strip()]
    text = " ".join(parts)
    excl = [e.strip() for e in el.get("exclusions", []) if e and e.strip()]
    if excl:
        text = (text + " " if text else "") + "Not eligible: " + "; ".join(excl) + "."
    return text


def _helpline_text(contacts: Dict[str, Any]) -> str:
    nums = []
    for h in (contacts or {}).get("helplines", []):
        n = (h.get("number") or "").strip()
        if not n:
            continue
        label = (h.get("label") or "").strip()
        nums.append("%s (%s)" % (n, label) if label else n)
    return " / ".join(nums)


def _adapt_canonical(rec: Dict[str, Any]) -> Dict[str, Any]:
    s = dict(rec)  # shallow copy; canonical sub-objects are not mutated
    level = rec.get("level", "central")
    states = rec.get("states") or []
    real_states = [x for x in states if x != "ALL"]
    app = rec.get("application") or {}
    url = app.get("portal_url") or ""
    links: List[str] = []
    if url.startswith("http"):
        links.append(url)
    for src in rec.get("sources", []):
        u = src.get("url") or ""
        if src.get("type") in ("official", "press_release", "gazette") and u.startswith("http") and u not in links:
            links.append(u)

    s["eligibility_detail"] = rec.get("eligibility")
    s["benefits_detail"] = rec.get("benefits")
    s["documents_detail"] = rec.get("documents")

    s["type"] = "central" if level == "central" else "state"
    s["scope"] = s["type"]
    s["state"] = real_states[0] if real_states else None
    s["description"] = rec.get("summary", "")
    s["eligibility"] = _eligibility_text(rec.get("eligibility") or {})
    s["benefits"] = (rec.get("benefits") or {}).get("summary", "")
    s["documents"] = [d.get("name", "") for d in rec.get("documents", []) if d.get("name")]
    s["target_group"] = (rec.get("beneficiary") or {}).get("description", "")
    s["helpline"] = _helpline_text(rec.get("contacts") or {})
    s["application_url"] = url
    s["documents_links"] = links
    return s


def _normalize_legacy(s: Dict[str, Any]) -> Dict[str, Any]:
    """Original v1 normalisation, kept so an old-format file still loads."""
    if "scope" in s and "type" not in s:
        s["type"] = s["scope"]
    if "states" in s and "state" not in s:
        states = s.get("states") or []
        s["state"] = states[0] if states else None
    if "application_url" in s:
        links = list(s.get("documents_links") or [])
        url = s["application_url"]
        if url and url.startswith("http") and url not in links:
            links.insert(0, url)
        s["documents_links"] = links
    return s


def _normalize_scheme(s: Dict[str, Any]) -> Dict[str, Any]:
    return _adapt_canonical(s) if _is_canonical(s) else _normalize_legacy(s)


def _applies_to_state(s: Dict[str, Any], state: str) -> bool:
    """Central/ALL schemes apply everywhere; state schemes apply if the state is listed."""
    if s.get("type") == "central":
        return True
    state_l = state.lower()
    listed = [x.lower() for x in (s.get("states") or []) if x]
    if "all" in listed or state_l in listed:
        return True
    return bool(s.get("state") and s["state"].lower() == state_l)


def load_canonical(include_inactive: bool = True) -> List[Dict[str, Any]]:
    """Raw canonical records (no legacy adaptation). Used by alias resolution and structured filtering."""
    filepath = settings.data_path
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    recs = [r for r in data.get("schemes", []) if _is_canonical(r)]
    if not include_inactive:
        recs = [r for r in recs if r.get("status", "active") == "active"]
    return recs


def load_schemes(
    scheme_type: Optional[str] = None,
    state: Optional[str] = None,
    include_inactive: bool = False,
) -> List[Dict[str, Any]]:
    """Load schemes in the legacy-compatible shape.

    Superseded/repealed schemes are hidden by default so that listings and
    recommendations only show current schemes; pass include_inactive=True to
    answer questions about old scheme names.
    """
    filepath = settings.data_path
    if not os.path.exists(filepath):
        return []

    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
        schemes = [_normalize_scheme(s) for s in data.get("schemes", [])]

    if not include_inactive:
        schemes = [s for s in schemes if s.get("status", "active") == "active"]

    if scheme_type:
        schemes = [s for s in schemes if s.get("type", "").lower() == scheme_type.lower()]

    if state:
        schemes = [s for s in schemes if _applies_to_state(s, state)]

    return schemes
