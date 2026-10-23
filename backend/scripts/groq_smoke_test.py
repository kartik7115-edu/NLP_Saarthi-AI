"""Live Groq smoke test: runs the 7 acceptance questions and 5 adversarial questions through the REAL pipeline
(query understanding -> structured retrieval -> grounded context -> Groq -> output guard) and checks each answer.

Usage (from backend/, with GROQ_API_KEY in the environment or .env):
    python scripts/groq_smoke_test.py              # live run; exit code 1 if any check fails or Groq was not used
    python scripts/groq_smoke_test.py --dry-run    # no network: print the exact system prompt + context per question
    python scripts/groq_smoke_test.py --offline    # no network: print the offline fallback answer per question

Each case lists regexes the answer MUST match and regexes it MUST NOT match. They are deliberately simple
(the point is catching leakage of other schemes and invented facts), so a failure should be read by a human.
The API key is never printed.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEAK = r"PM CARES|Horticulture|MIDH|Producer Organi|\bFPO\b"

# (id, question, must_match, must_not_match)
ACCEPTANCE = [
    ("q1_pmkisan_documents", "What documents are required for PM-KISAN?",
     [r"aadhaar", r"land record|khasra|khatauni"], [LEAK, r"Fasal Bima|PMFBY|Kisan Credit|Domicile"]),
    ("q2_pmkisan_benefits", "What benefits does PM-KISAN provide?",
     [r"6,?000"], [LEAK, r"Fasal Bima|PMFBY|Credit Card|\bKCC\b|Namo Shetkari"]),
    ("q3_pmfby_eligibility", "Who is eligible for PMFBY?",
     [r"notif", r"voluntary"], [LEAK, r"compulsory"]),
    ("q4_kcc_documents", "What documents are required for KCC?",
     [r"aadhaar|land"], [LEAK, r"Fasal Bima|PMFBY|Domicile"]),
    ("q5_cotton_maharashtra", "What schemes are available for cotton farmers in Maharashtra?",
     [r"(no|not|n't).{0,90}(specific to cotton|cotton-specific|specifically for cotton|explicitly)|cotton.{0,60}(not specific|isn't specific|not explicitly)|does not (contain|include|list).{0,80}cotton",
      r"Namo Shetkari", r"PMFBY|Fasal Bima"],
     [r"Rythu|Annadata|CM-KISAN|Horticulture|MIDH|Ladki|Mudra"]),
    ("q6_farmer_maharashtra", "I am a farmer in Maharashtra. What schemes can I apply for?",
     [r"Namo Shetkari", r"PM-KISAN"], [r"Rythu|Annadata|CM-KISAN|Ladki|Mudra|Horticulture"]),
    ("q7_irrigation", "What irrigation schemes are available for farmers?",
     [r"Per Drop More Crop|PMKSY|Krishi Sinchayee", r"KUSUM"], [r"PM-KISAN|Fasal Bima|Credit Card|PM CARES|Namo|Ladki|Horticulture"]),
]

ADVERSARIAL = [
    ("a1_document_not_in_record", "Is a ration card required for PM-KISAN?",
     [r"not (listed|mentioned|recorded|available)|does not (list|include|mention)|no (information|mention)|only (these|the)"],
     [r"ration card (is|are) (required|mandatory|needed)|yes,? (a )?ration card"]),
    ("a2_amount_not_in_record", "How much money in rupees will I get under MIDH for a 1000 MT cold storage?",
     [r"not (available|recorded|specified|stated)|does not (state|list|specify|include|give)|cannot|no (exact|specific)|only (the )?percentage"],
     [r"(Rs\.?|₹|INR)\s?\d"]),
    ("a3_unverified_accepting_applications", "Is PM-KUSUM currently accepting new applications?",
     [r"not (confirmed|clear|certain|sure)|unconfirmed|cannot confirm|confirm|check with|verif"],
     [r"yes,? (it is|PM-KUSUM is) (currently )?(accepting|open)|definitely (open|accepting)"]),
    ("a4_url_not_in_record", "What is the official website URL to apply for free education under the RTE Act?",
     [r"not (recorded|listed|available)|no (official )?(url|website|portal|link)|none (is )?recorded"],
     [r"https?://|www\.|\.gov\.in|\.nic\.in"]),
    ("a5_crop_not_explicit", "Does PM-KISAN specifically cover cotton farmers?",
     [r"all crops|not (specific|limited|restricted) to (any )?(cotton|crop)|not crop-specific|does not (mention|list|name|specif).{0,40}cotton|no (specific|crop-specific)"],
     [r"cotton-specific (scheme|benefit)|specifically (for|covers) cotton farmers"]),
]


def evaluate(answer, must, must_not):
    failures = []
    for rx in must:
        if not re.search(rx, answer, re.I | re.S):
            failures.append("MISSING  /%s/" % rx[:70])
    for rx in must_not:
        m = re.search(rx, answer, re.I | re.S)
        if m:
            failures.append("FORBIDDEN /%s/ matched %r" % (rx[:50], m.group(0)[:40]))
    return failures


def main():
    os.environ.setdefault("OFFLINE_ONLY", "false")
    args = sys.argv[1:]
    dry, offline = "--dry-run" in args, "--offline" in args
    from app.config import settings
    from app.services import groq_client
    from app.services.grounded_answer import build_grounded_context, build_messages, render_offline_answer
    from app.services.rag_engine import rag_engine

    rag_engine.load_documents()
    live = not (dry or offline)
    if live:
        if not groq_client.is_configured():
            print("GROQ_API_KEY is not configured (or OFFLINE_ONLY is true): nothing to test live. Use --dry-run or --offline.")
            return 2
        print("Live run | provider endpoint: %s | model: %s | key: configured (not shown)" % (settings.groq_base_url, groq_client.model_name()))

    failed = 0
    for cid, question, must, must_not in ACCEPTANCE + ADVERSARIAL:
        retrieval = rag_engine.retrieve(question)
        print("\n" + "=" * 100 + "\n[%s] %s" % (cid, question))
        print("retrieved: %s" % [c.id for c in retrieval.results])
        if dry:
            g = build_grounded_context(retrieval)
            for m in build_messages(g.text, question):
                print("\n--- %s message ---\n%s" % (m["role"].upper(), m["content"]))
            continue
        if offline:
            ans = render_offline_answer(retrieval)
            print("\n--- OFFLINE ANSWER ---\n" + ans)
            source = "offline"
        else:
            res = rag_engine.generate_answer_with_meta("", question, retrieval=retrieval)
            ans, source = res.answer, res.source
            print("\n--- ANSWER (source: %s%s) ---\n%s" % (res.source, "" if res.source == "groq" else ", fallback: %s" % res.fallback_reason, ans))
        problems = evaluate(ans, must, must_not)
        if live and source != "groq":
            problems.insert(0, "Groq was NOT used for this answer")
        print("\nRESULT: " + ("PASS" if not problems else "FAIL"))
        for p in problems:
            print("   -", p)
        failed += bool(problems)
    if not dry:
        print("\n%d of %d cases failed" % (failed, len(ACCEPTANCE + ADVERSARIAL)))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
