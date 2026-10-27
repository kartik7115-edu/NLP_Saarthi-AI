"""English retrieval keywords for Hindi / Marathi questions.

Retrieval matches English tokens against the English scheme dataset. A Hindi or Marathi question therefore has to be
turned into English first. Machine translation does that when it works; when it fails (the free Google endpoint is
rate-limited, blocked or down, and no other provider is configured) the question used to reach retrieval untranslated
and a Marathi question such as "मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?" matched nothing at all.

`english_keywords()` is the deterministic, offline safety net: it maps common Hindi and Marathi scheme vocabulary
(including inflected / agglutinated forms such as अनुदानासाठी = अनुदान + साठी) to the English words the retrieval and
intent detector already understand. It never adds facts: it only expands the QUERY. The original question is kept
unchanged for the answer.
"""
import re
from typing import List, Tuple

from app.services.query_understanding import STATE_ALIASES

# (Devanagari stems, English keywords). A question word matches a stem when it equals it or starts with it, so
# Marathi/Hindi case endings are covered (शेतकऱ्यांसाठी, किसानों, महाराष्ट्रात). Stems are at least 3 characters to
# avoid accidental prefix hits. More specific entries are listed before the general ones they overlap with.
_GLOSSARY: List[Tuple[Tuple[str, ...], str]] = [
    (("लाभार्थी", "लाभार्थ्यां"), "beneficiary"),
    (("कृषि", "कृषी", "खेती", "शेती", "कृषक"), "agriculture"),
    (("किसान", "शेतकरी", "शेतकऱ्"), "farmer"),
    (("सब्सिडी", "अनुदान", "सबसिडी"), "subsidy"),
    (("पात्र", "योग्यता"), "eligible"),
    (("दस्तावेज", "कागजात", "कागदपत्र", "कागज़ात"), "documents"),
    (("आवेदन", "अर्ज"), "apply"),
    (("पंजीकरण", "पंजीयन", "नोंदणी", "रजिस्ट्रेशन"), "registration"),
    (("फसल बीमा", "पीक विमा"), "crop insurance"),
    (("बीमा", "विमा"), "insurance"),
    (("फसल", "पीक"), "crop"),
    (("ऋण", "कर्ज", "लोन"), "loan"),
    (("पेंशन", "निवृत्तिवेतन", "निवृत्तीवेतन"), "pension"),
    (("सिंचाई", "सिंचन", "ठिबक", "ड्रिप"), "irrigation"),
    (("सौर", "सोलर"), "solar"),
    (("पंप",), "pump"),
    (("ट्रैक्टर", "ट्रॅक्टर"), "tractor"),
    (("मिट्टी", "माती", "मृदा"), "soil"),
    (("भंडारण", "गोदाम", "कोल्ड स्टोरेज", "शीतगृह"), "storage"),
    (("मंडी", "बाजार"), "market"),
    (("लाभ", "फायदा", "फायदे", "फ़ायदा"), "benefits"),
    (("हेल्पलाइन", "हेल्पलाईन", "टोल फ्री"), "helpline"),
    (("महिला", "स्त्रिया"), "women"),
    (("डेयरी", "डेअरी", "दुग्ध", "दूध"), "dairy"),
    (("पशुपालन", "पशुधन", "पशु", "जनावर"), "livestock"),
    (("मछली", "मत्स्य", "मासेमारी", "मच्छीमार"), "fisheries"),
    (("बागवानी", "फलोत्पादन", "फळबाग"), "horticulture"),
    (("जैविक", "सेंद्रिय"), "organic"),
    (("ब्याज", "व्याज"), "interest"),
    (("छोटे", "लघु", "अल्पभूधारक"), "small"),
    (("सीमांत",), "marginal"),
    (("बटाईदार", "किरायेदार", "खंडकरी"), "tenant"),
    (("योजना", "योजने"), "scheme"),
    # crops (Marathi names; the Hindi ones are already in query_understanding.CROPS)
    (("कापूस", "कपास"), "cotton"),
    (("गहू", "गेहूं", "गेहूँ"), "wheat"),
    (("भात", "धान"), "paddy"),
    (("ऊस", "गन्ना"), "sugarcane"),
    (("सोयाबीन",), "soybean"),
    (("हरभरा", "तूर", "दाल", "डाळ"), "pulses"),
]

_DEV_TOKEN = re.compile(r"[ऀ-ॿ]+")


def _entries():
    states = [((alias,), state) for alias, state in STATE_ALIASES.items() if re.search(r"[ऀ-ॿ]", alias)]
    entries = states + _GLOSSARY
    # longest stems first so "फसल बीमा" wins over "फसल"
    flat = [(stem, eng) for stems, eng in entries for stem in stems]
    flat.sort(key=lambda e: -len(e[0]))
    return flat


_FLAT = _entries()


def english_keywords(text: str) -> str:
    """English keywords for the Hindi/Marathi words of `text` ('' when nothing is recognised). Order follows the text."""
    tokens = _DEV_TOKEN.findall(text or "")
    if not tokens:
        return ""
    out: List[str] = []
    used = [False] * len(tokens)
    for stem, eng in _FLAT:
        parts = stem.split(" ")
        n = len(parts)
        for i in range(len(tokens) - n + 1):
            if any(used[i:i + n]):
                continue
            ok = all(tokens[i + j] == parts[j] for j in range(n - 1)) and (
                tokens[i + n - 1] == parts[-1] or (len(parts[-1]) >= 3 and tokens[i + n - 1].startswith(parts[-1])))
            if ok:
                for j in range(i, i + n):
                    used[j] = True
                out.append((i, eng))
    out.sort()
    seen, words = set(), []
    for _, eng in out:
        if eng not in seen:
            seen.add(eng)
            words.append(eng)
    return " ".join(words)
