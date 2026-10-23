"""Retrieval evaluation: new structured retrieval vs the original word-overlap retrieval (baseline).

Usage (from backend/):  python scripts/eval_retrieval.py [--verbose]

HOW THE METRICS ARE CALCULATED
- Gold set: ~30 hand-written questions, each with a hand-labelled set of relevant scheme ids. The labels are
  my reading of each scheme's eligibility/benefits in schemes.json (not an independent human annotation) and
  the set is small, so treat the numbers as a regression guard, not a benchmark.
- Retrieved list: the schemes a retriever returns (at most K=5), in rank order.
- Hit@1        : 1 if the top result is relevant.
- MRR          : mean of 1/rank of the first relevant result (0 if none returned).
- Precision@5  : relevant results / results returned (up to 5). A retriever that returns fewer, correct results
                 is not penalised for returning fewer.
- Recall@5     : relevant results found in the top 5 / min(5, number of relevant schemes). Capped at 5 because a
                 list query with 12 relevant schemes cannot score above 5/12 in a top-5 list.
- "exact" queries name one scheme (or a superseded scheme + its replacement); MRR is reported for these as asked.
- "list" queries ask for schemes by state/crop/topic/beneficiary; MRR is still shown but Precision@5/Recall@5
  are the meaningful numbers.
The baseline is a verbatim copy of the previous rag_engine.search_similar scoring (substring matching,
whitespace tokenisation, +0.5 for every central scheme) run on the same adapted dataset.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("OFFLINE_ONLY", "true")

from app.services.rag_engine import rag_engine  # noqa: E402
from app.services.scheme_loader import load_schemes  # noqa: E402

K = 5
FARM_CENTRAL = {"pm-kisan", "pmfby", "kisan-credit-card", "pm-kisan-maan-dhan", "agriculture-infrastructure-fund",
                "e-nam", "soil-health-card", "pm-kusum", "pmksy-per-drop-more-crop", "smam-farm-mechanisation",
                "fpo-10000-scheme"}   # all-crop central farm schemes (MIDH is horticulture-only, so not for cotton)

# (category, query, relevant ids, optional profile state, run baseline?)
GOLD = [
    ("exact", "What documents are required for PM-KISAN?", {"pm-kisan"}),
    ("exact", "What benefits does PM-KISAN provide?", {"pm-kisan"}),
    ("exact", "How much money does PM-KISAN provide?", {"pm-kisan"}),
    ("exact", "Who is eligible for PMFBY?", {"pmfby"}),
    ("exact", "What documents are required for KCC?", {"kisan-credit-card"}),
    ("exact", "How do I apply for KCC?", {"kisan-credit-card"}),
    ("exact", "How do I apply for PMFBY?", {"pmfby"}),
    ("exact", "PM Kisan Samman Nidhi eligibility", {"pm-kisan"}),
    ("exact", "What is the PMFBY crop insurance helpline number?", {"pmfby"}),
    ("exact", "How do I register on eNAM?", {"e-nam"}),
    ("exact", "What is the interest subvention under AIF?", {"agriculture-infrastructure-fund"}),
    ("exact", "Tell me about the PM-KUSUM solar pump subsidy", {"pm-kusum"}),
    ("exact", "What is the Soil Health Card scheme?", {"soil-health-card"}),
    ("exact", "What documents are needed for Namo Shetkari?", {"namo-shetkari-mahasanman-nidhi"}),
    ("exact", "Compare PM-KISAN and PMFBY", {"pm-kisan", "pmfby"}),
    ("exact-hindi", "प्रधानमंत्री किसान सम्मान निधि के लिए आवेदन कैसे करें", {"pm-kisan"}),
    ("historical", "What happened to Rythu Bandhu?", {"rythu-bandhu", "rythu-bharosa"}),
    ("historical", "What is the current replacement for KALIA?", {"kalia-yojana", "cm-kisan-odisha"}),
    ("historical", "What is the current status of MGNREGA?", {"mgnrega", "vb-g-ram-g"}),
    ("list", "What schemes are available for cotton farmers in Maharashtra?", FARM_CENTRAL | {"namo-shetkari-mahasanman-nidhi"}),
    ("list", "I am a farmer in Maharashtra. What schemes can I apply for?", FARM_CENTRAL | {"namo-shetkari-mahasanman-nidhi", "midh-horticulture"}),
    ("list", "What irrigation schemes are available for farmers?", {"pmksy-per-drop-more-crop", "pm-kusum"}),
    ("list", "Is there a subsidy for solar pumps for farmers?", {"pm-kusum"}),
    ("list", "Is there a subsidy for buying a tractor or farm machinery?", {"smam-farm-mechanisation"}),
    ("list", "Which scheme helps build a cold storage?", {"midh-horticulture", "agriculture-infrastructure-fund"}),
    ("list", "Are there loans for dairy farmers?", {"kisan-credit-card"}),
    ("list", "Which schemes support fishermen with credit?", {"kisan-credit-card"}),
    ("list", "Which scheme supports farmer producer organisations?", {"fpo-10000-scheme", "agriculture-infrastructure-fund"}),
    ("list", "I am a small farmer in Andhra Pradesh. What financial assistance can I get?", {"annadata-sukhibhava", "pm-kisan"}),
    ("list", "Is there crop insurance for farmers?", {"pmfby"}),
    ("list", "Is there a pension scheme for small and marginal farmers?", {"pm-kisan-maan-dhan"}),
]


# ------------------------------------------------------------------ baseline (previous search_similar, verbatim logic)
def legacy_search(query, schemes, top_k=K, user_state=None, occupation=None):
    query_lower = query.lower()
    query_words = set(query_lower.split())
    occupation_lower = (occupation or "").lower()
    scored = []
    for scheme in schemes:
        name_lower = scheme["name"].lower()
        desc_lower = scheme.get("description", "").lower()
        target_lower = scheme.get("target_group", "").lower()
        elig_lower = scheme.get("eligibility", "").lower()
        benefits_lower = scheme.get("benefits", "").lower()
        state_lower = (scheme.get("state") or "").lower()
        score = 0
        for word in query_words:
            if len(word) < 3:
                continue
            if word in name_lower:
                score += 3
            elif word in target_lower:
                score += 2
            elif word in desc_lower or word in elig_lower or word in benefits_lower:
                score += 1
        if user_state and state_lower and user_state.lower() in state_lower:
            score += 2
        if scheme.get("type") == "central":
            score += 0.5
        if occupation_lower:
            if occupation_lower in target_lower or occupation_lower in desc_lower:
                score += 2
        if score > 0:
            scored.append((score, scheme))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s["id"] for _, s in scored[:top_k]]


# ------------------------------------------------------------------ metrics
def metrics(returned, relevant):
    returned = returned[:K]
    hits = [r in relevant for r in returned]
    rr = next((1.0 / (i + 1) for i, h in enumerate(hits) if h), 0.0)
    return {
        "hit@1": 1.0 if hits and hits[0] else 0.0,
        "rr": rr,
        "p@5": (sum(hits) / len(returned)) if returned else 0.0,
        "r@5": sum(hits) / min(K, len(relevant)),
        "n": len(returned),
    }


def run_eval(verbose=False):
    if not rag_engine._all_schemes:
        rag_engine.load_documents()
    active = load_schemes()
    rows = []
    for cat, query, relevant in GOLD:
        new = [s["id"] for s in rag_engine.retrieve(query, top_k=K).schemes]
        old = None if cat == "exact-hindi" else legacy_search(query, active)
        rows.append({"cat": cat, "query": query, "relevant": relevant, "new": new, "old": old,
                     "m_new": metrics(new, relevant), "m_old": metrics(old, relevant) if old is not None else None})
    return rows


def aggregate(rows, which, cats):
    sel = [r for r in rows if r["cat"] in cats and r["m_" + which] is not None]
    if not sel:
        return None
    avg = lambda key: sum(r["m_" + which][key] for r in sel) / len(sel)
    return {"n": len(sel), "hit@1": avg("hit@1"), "mrr": avg("rr"), "p@5": avg("p@5"), "r@5": avg("r@5")}


def report(rows, verbose=False):
    groups = [("exact scheme queries (English)", {"exact"}), ("exact scheme queries incl. Hindi alias (new only)", {"exact", "exact-hindi"}),
              ("historical (superseded + replacement)", {"historical"}), ("list / entity queries", {"list"}),
              ("ALL queries", {"exact", "exact-hindi", "historical", "list"})]
    print("%-52s %3s | %-34s | %-34s" % ("", "n", "NEW  Hit@1  MRR   P@5   R@5", "OLD  Hit@1  MRR   P@5   R@5"))
    for label, cats in groups:
        a, b = aggregate(rows, "new", cats), aggregate(rows, "old", cats)
        fmt = lambda m: "      %4.2f  %4.2f  %4.2f  %4.2f" % (m["hit@1"], m["mrr"], m["p@5"], m["r@5"]) if m else "      n/a"
        print("%-52s %3d |%-34s |%-34s" % (label, a["n"], fmt(a), fmt(b)))
    if verbose:
        print()
        for r in rows:
            print("[%s] %s\n   relevant=%s\n   NEW=%s\n   OLD=%s" % (r["cat"], r["query"], sorted(r["relevant"]), r["new"], r["old"]))


if __name__ == "__main__":
    report(run_eval(), verbose="--verbose" in sys.argv)
