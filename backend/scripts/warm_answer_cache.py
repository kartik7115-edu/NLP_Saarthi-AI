"""Ask the demo questions ONCE, slowly, against the running server, so their validated answers are saved on disk.

Why: Groq's free tier allows only a few full answers per minute and the free Google endpoint blocks bursts. Answers
that Groq produced and that passed the grounding + language checks are saved in backend/app/data/answer_cache.json
(see settings.answer_cache_file). During the demo the same questions are then answered from that file - instantly and
without calling Groq or Google. Nothing is invented: only real, validated Groq answers are saved.

Usage (server already running, from backend/):
    python scripts/warm_answer_cache.py                       # the built-in demo questions (en, hi, mr)
    python scripts/warm_answer_cache.py --url http://127.0.0.1:8001 --gap 40
    python scripts/warm_answer_cache.py --lang hi "पीएम किसान के लिए कौन से दस्तावेज चाहिए?"
Keep the farmer profile in the app EMPTY during the demo (the profile is part of the saved answer's key).
"""
import argparse
import sys
import time

import requests

DEMO = [
    ("hi", "मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?"),
    ("mr", "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?"),
    ("en", "How can I check whether I am eligible for an agricultural subsidy?"),
    ("hi", "पीएम किसान योजना के लिए कौन से दस्तावेज चाहिए?"),
    ("hi", "किसान क्रेडिट कार्ड के लिए आवेदन कैसे करें?"),
    ("hi", "फसल बीमा योजना के क्या लाभ हैं?"),
]


def ask(url, lang, q, timeout=90):
    r = requests.post(url.rstrip("/") + "/query/", json={"query": q, "language": lang, "occupation": "farmer"}, timeout=timeout)
    r.raise_for_status()
    d = r.json()
    ok_lang = d.get("response_language") == lang
    return r.headers.get("X-Answer-Source", "?"), r.headers.get("X-Translation-Provider", "-"), ok_lang, d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("questions", nargs="*")
    ap.add_argument("--lang", default="hi")
    ap.add_argument("--url", default="http://127.0.0.1:8001")
    ap.add_argument("--gap", type=float, default=40.0, help="seconds between questions (Groq per-minute limit)")
    ap.add_argument("--tries", type=int, default=3)
    a = ap.parse_args()
    todo = [(a.lang, q) for q in a.questions] or DEMO
    failed = 0
    for i, (lang, q) in enumerate(todo):
        for attempt in range(1, a.tries + 1):
            try:
                source, provider, ok_lang, d = ask(a.url, lang, q)
            except Exception as e:
                source, provider, ok_lang, d = f"error: {type(e).__name__}", "-", False, {}
            good = source == "groq" and (ok_lang or lang == "en")
            print(f"[{lang}] try {attempt}: source={source} provider={provider} language_ok={ok_lang} | {q[:60]}")
            if good:
                print("      saved. First line:", (d.get("answer") or "").strip().splitlines()[0][:120])
                break
            if attempt < a.tries:
                print(f"      not a validated Groq answer yet - waiting {a.gap:.0f}s (rate limit) and retrying")
                time.sleep(a.gap)
        else:
            failed += 1
        if i < len(todo) - 1:
            time.sleep(a.gap)
    print("done:", len(todo) - failed, "saved,", failed, "not saved (see the server log lines 'query lang=...')")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
