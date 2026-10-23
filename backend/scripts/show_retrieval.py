"""Print the understanding and the top-N ranked schemes (scores + signals) for one or more queries.

Usage (from backend/):
    python scripts/show_retrieval.py "What documents are required for PM-KISAN?" ["another query" ...]
    python scripts/show_retrieval.py --state Telangana "farmer scheme"       # profile fallback state
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OFFLINE_ONLY", "true")

from app.services.rag_engine import rag_engine  # noqa: E402

DEFAULT_QUERIES = [
    "What documents are required for PM-KISAN?",
    "What benefits does PM-KISAN provide?",
    "Who is eligible for PMFBY?",
    "What documents are required for KCC?",
    "What schemes are available for cotton farmers in Maharashtra?",
    "I am a farmer in Maharashtra. What schemes can I apply for?",
    "What irrigation schemes are available for farmers?",
]


def show(query, state=None, n=5):
    r = rag_engine.retrieve(query, top_k=n, user_state=state)
    u = r.understanding
    print("=" * 100)
    print("QUERY:", query)
    print("UNDERSTANDING: intent=%s | scheme=%s | state=%s | crop=%s | beneficiary=%s | gender=%s | topics=%s"
          % (u.intent, [s["id"] for s in u.schemes] or None, u.state, u.crop, u.beneficiary, u.gender,
             sorted(set(u.activities + u.domains)) or None))
    print("%-3s %-8s %-22s %-34s %s" % ("#", "score", "tier", "scheme", "signals"))
    shown = 0
    rest_no_signal = 0
    for c in r.candidates:
        informative = c.selected or c.signals["qualifiers"] or c.signals["lexical_score"] > 0 or c.reason in (
            "topic_mismatch", "state_mismatch", "crop_mismatch", "below_top_k")
        if not informative:
            rest_no_signal += 1
            continue
        if shown >= n:
            continue
        s = c.signals
        sig = []
        if s["exact_scheme_match"]:
            sig.append("exact_scheme_match=True(%s)" % s.get("matched_text"))
        if s.get("replacement_for"):
            sig.append("replacement_for=%s" % s["replacement_for"])
        if s["intent_field_match"]:
            sig.append("intent_field=%s" % s["intent_field_match"])
        if s["beneficiary_match"]:
            sig.append("beneficiary=match")
        sig.append("state=%s" % s["state_match"])
        if s["crop_match"] != "none":
            sig.append("crop=%s" % s["crop_match"])
        topics = sorted(set(s["activity_matches"] + s["domain_matches"]))
        if topics:
            sig.append("topic=%s" % topics)
        if s["gender_match"]:
            sig.append("gender=match")
        sig.append("lexical=%.1f" % s["lexical_score"])
        flag = "*" if c.selected else " "
        why = "" if c.selected else "  [NOT RETURNED: %s]" % c.reason
        print("%s%-2d %-8.1f %-22s %-34s %s%s" % (flag, shown + 1, c.score, c.tier, c.id, ", ".join(sig), why))
        shown += 1
    print("   (%d other schemes had no relevance signal and are not shown; they score only the baseline applicability points)" % rest_no_signal)
    if r.notes:
        for note in r.notes:
            print("NOTE:", note)
    print("RETURNED %d scheme(s): %s" % (len(r.results), [c.id for c in r.results]))
    if r.results and r.results[0].focus:
        f = r.results[0].focus
        print("FOCUS FIELD of top result: %s available=%s items=%s" % (f["field"], f["available"], [i[:60] for i in f["items"][:4]]))


if __name__ == "__main__":
    args = sys.argv[1:]
    state = None
    if args and args[0] == "--state":
        state, args = args[1], args[2:]
    for q in (args or DEFAULT_QUERIES):
        show(q, state)
