"""Lightweight validator for the canonical schemes.json (schema v2).

Usage (from backend/):  python scripts/validate_schemes.py [path/to/schemes.json]
Exit code 0 = no errors (warnings allowed), 1 = errors found.
"""
import json
import os
import re
import sys
from datetime import date
from urllib.parse import urlparse

DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "data", "schemes.json")

STATUS = {"active", "superseded", "repealed"}
LEVEL = {"central", "state"}
CONFIDENCE = {"verified", "needs_review"}
SOURCE_TYPES = {"official", "press_release", "gazette", "secondary", "unverified"}
TRUSTED_SOURCE_TYPES = {"official", "press_release", "gazette"}
MODES = {"online", "offline", "csc", "bank", "panchayat", "agriculture_department", "insurance_company"}
FARMER_TYPES = {"landowner", "tenant", "sharecropper", "small", "marginal", "large", "women", "young", "landless"}
GENDERS = {"any", "women", "men", "transgender"}
ACTIVITIES = {"crop_cultivation", "irrigation", "horticulture", "fisheries", "dairy", "livestock", "poultry",
              "farm_mechanisation", "storage", "marketing", "food_processing", "natural_farming",
              "solar_agriculture", "farmer_producer_organization"}
FARM_OCCUPATIONS = {"farmer", "fisherman", "livestock_farmer", "dairy_farmer", "poultry_farmer"}
STATES = {
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa", "Gujarat", "Haryana",
    "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala", "Madhya Pradesh", "Maharashtra", "Manipur",
    "Meghalaya", "Mizoram", "Nagaland", "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana",
    "Tripura", "Uttar Pradesh", "Uttarakhand", "West Bengal", "Andaman and Nicobar Islands", "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu", "Delhi", "Jammu and Kashmir", "Ladakh", "Lakshadweep", "Puducherry",
}
ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
TOKEN_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
PUNCT = re.compile(r"[\s\-_.,;:?!()\[\]{}\"'/\\|।–—]+")
REQUIRED = ["id", "name", "aliases", "status", "superseded_by", "level", "states", "domains", "summary", "beneficiary",
            "crops", "activities", "eligibility", "benefits", "documents", "application", "contacts", "sources",
            "last_verified", "confidence"]


def norm(text):
    return PUNCT.sub(" ", str(text).lower()).strip()


def valid_url(u):
    p = urlparse(u)
    return p.scheme in ("http", "https") and bool(p.netloc) and " " not in u


def valid_date(s):
    try:
        date.fromisoformat(s)
        return True
    except (TypeError, ValueError):
        return False


def validate(path=DEFAULT_PATH):
    errors, warnings = [], []
    err = lambda r, m: errors.append("%s: %s" % (r, m))
    warn = lambda r, m: warnings.append("%s: %s" % (r, m))

    data = json.load(open(path, encoding="utf-8"))
    recs = data.get("schemes")
    if not isinstance(recs, list) or not recs:
        return ["schemes list missing or empty"], []

    ids = [r.get("id") for r in recs]
    id_set = set(ids)
    seen_ids, names, alias_owner = set(), {}, {}

    for r in recs:
        rid = r.get("id") or "<missing id>"
        for k in REQUIRED:
            if k not in r:
                err(rid, "missing required field '%s'" % k)
        if rid in seen_ids:
            err(rid, "duplicate id")
        seen_ids.add(rid)
        if r.get("id") and not ID_RE.match(r["id"]):
            err(rid, "id must be lowercase-hyphen slug")
        if not str(r.get("name", "")).strip():
            err(rid, "empty name")
        nn = norm(r.get("name", ""))
        if nn in names and names[nn] != rid:
            err(rid, "duplicate canonical name with '%s'" % names[nn])
        names[nn] = rid

        # status / supersession
        st = r.get("status")
        if st not in STATUS:
            err(rid, "invalid status %r" % st)
        sb = r.get("superseded_by")
        if st in ("superseded", "repealed"):
            if not sb or sb not in id_set:
                err(rid, "superseded_by must reference an existing id (got %r)" % sb)
            elif sb == rid:
                err(rid, "superseded_by points to itself")
        elif sb is not None:
            err(rid, "active record must have superseded_by = null")

        # geography
        lv, states = r.get("level"), r.get("states")
        if lv not in LEVEL:
            err(rid, "invalid level %r" % lv)
        if not isinstance(states, list) or not states:
            err(rid, "states must be a non-empty list (use ['ALL'] for national schemes)")
        else:
            if lv == "central" and states != ["ALL"]:
                err(rid, "central scheme must have states == ['ALL']")
            if lv == "state":
                if "ALL" in states:
                    err(rid, "state scheme must not contain 'ALL'")
                for s in states:
                    if s not in STATES:
                        err(rid, "unknown state %r" % s)

        # aliases
        al = r.get("aliases")
        if not isinstance(al, list):
            err(rid, "aliases must be a list")
            al = []
        if not al:
            warn(rid, "no aliases")
        for a in al:
            na = norm(a)
            if not na:
                err(rid, "empty alias")
            elif na in alias_owner and alias_owner[na] != rid:
                err(rid, "alias %r collides with record '%s'" % (a, alias_owner[na]))
            else:
                alias_owner[na] = rid

        # beneficiary / crops / activities
        b = r.get("beneficiary") or {}
        occ = b.get("occupations", [])
        for ft in b.get("farmer_types", []):
            if ft not in FARMER_TYPES:
                err(rid, "invalid farmer_type %r" % ft)
        for g in b.get("genders", []):
            if g not in GENDERS:
                err(rid, "invalid gender %r" % g)
        if not b.get("genders"):
            err(rid, "beneficiary.genders must not be empty (use ['any'])")
        for o in occ:
            if not TOKEN_RE.match(o):
                err(rid, "occupation %r must be snake_case" % o)
        for c in r.get("crops", []):
            if c not in ("ALL", "NOTIFIED_CROPS") and not TOKEN_RE.match(c):
                err(rid, "crop %r must be ALL, NOTIFIED_CROPS or a lowercase crop name" % c)
        for a in r.get("activities", []):
            if a not in ACTIVITIES:
                err(rid, "invalid activity %r" % a)
        for d in r.get("domains", []):
            if not TOKEN_RE.match(d):
                err(rid, "domain %r must be snake_case" % d)
        if not r.get("domains"):
            err(rid, "domains must not be empty")

        # farmer schemes need farmer metadata
        if FARM_OCCUPATIONS & set(occ):
            if not r.get("crops"):
                err(rid, "farmer scheme must declare crops (ALL, NOTIFIED_CROPS or names)")
            if not b.get("farmer_types") and "landless" not in b.get("farmer_types", []):
                warn(rid, "farmer scheme has no farmer_types")
            if not r.get("application", {}).get("steps") and st == "active":
                warn(rid, "active farmer scheme has no application steps")
            if not r.get("documents") and st == "active":
                warn(rid, "active farmer scheme has no documents listed")
        elif r.get("crops"):
            warn(rid, "has crops but no farm occupation")

        # content
        el = r.get("eligibility") or {}
        if not (el.get("criteria") or el.get("exclusions")):
            err(rid, "eligibility must have criteria")
        ben = r.get("benefits") or {}
        if len(str(ben.get("summary", ""))) < 10:
            err(rid, "benefits.summary too short")
        for d in r.get("documents", []):
            if not isinstance(d, dict) or not d.get("name"):
                err(rid, "document entries need a name")
        # application
        app = r.get("application") or {}
        for m in app.get("modes", []):
            if m not in MODES:
                err(rid, "invalid application mode %r" % m)
        pu = app.get("portal_url", "")
        if pu and not valid_url(pu):
            err(rid, "invalid portal_url %r" % pu)
        if not pu and st == "active":
            warn(rid, "no portal_url (needs_review expected)")
        # sources
        for s in r.get("sources", []):
            if s.get("type") not in SOURCE_TYPES:
                err(rid, "invalid source type %r" % s.get("type"))
            if s.get("url") and not valid_url(s["url"]):
                err(rid, "invalid source url %r" % s.get("url"))
        # verification
        conf, lvd = r.get("confidence"), r.get("last_verified")
        if conf not in CONFIDENCE:
            err(rid, "invalid confidence %r" % conf)
        if lvd is not None and not valid_date(lvd):
            err(rid, "last_verified must be YYYY-MM-DD or null (got %r)" % lvd)
        if conf == "verified":
            if not lvd:
                err(rid, "verified record needs last_verified")
            if not any(s.get("type") in TRUSTED_SOURCE_TYPES and s.get("url") for s in r.get("sources", [])):
                err(rid, "verified record needs an official/press_release/gazette source with a URL")
        if conf == "needs_review" and lvd:
            warn(rid, "needs_review but has last_verified date")

    n_review = sum(1 for r in recs if r.get("confidence") == "needs_review")
    warnings.append("SUMMARY: %d records, %d needs_review, %d verified" % (len(recs), n_review, len(recs) - n_review))
    return errors, warnings


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PATH
    errors, warnings = validate(path)
    for w in warnings:
        print("WARN ", w)
    for e in errors:
        print("ERROR", e)
    print("%d error(s), %d warning(s)" % (len(errors), len(warnings)))
    sys.exit(1 if errors else 0)


if __name__ == "__main__":
    main()
