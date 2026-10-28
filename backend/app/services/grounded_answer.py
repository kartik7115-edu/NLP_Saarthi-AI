"""Grounded answering on top of structured retrieval.

    RetrievalResult --> record views --> (a) intent-aware context for the LLM
                                         (b) deterministic offline answer (same views, same facts)
    LLM answer     --> output guard (URLs / phone numbers must come from the records)

The LLM never searches the dataset: it only sees the records retrieval selected, with the field that
answers the question marked first. The offline renderer is built from the same record views, so the
online and offline answers cannot disagree about a fact.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from app.services.retrieval import INTENT_FIELD, RetrievalResult, ScoredScheme
from app.services.scheme_query import follow_replacement

# ======================================================================= prompt
GROUNDING_SYSTEM_PROMPT = """You are Saarthi.AI, an assistant that explains Indian government schemes to farmers and citizens.

You receive a SAARTHI.AI KNOWLEDGE CONTEXT and a user question. The context is the ONLY source of facts you may use.

GROUNDING RULES - follow every one of them
1. Answer only from the supplied Saarthi.AI knowledge context. Use no outside knowledge about schemes, even if you believe you know the answer.
2. Never invent eligibility criteria, benefits, documents, amounts, dates, URLs, helpline numbers or application procedures. Quote figures, links and phone numbers only if they appear in the context.
3. If the information needed is missing from the context, say plainly that it is not available in the current Saarthi.AI knowledge base (you may suggest checking the official portal or the nearest CSC / agriculture office). Do not guess and do not fill gaps with general knowledge.
4. Verification status: a record marked "needs_review" has NOT been checked against an official source. Phrase its facts as "The current Saarthi.AI dataset lists ..." or "This is currently marked as needing verification" - never as confirmed or official. For a record marked "verified" state its facts plainly without extra disclaimers. Say the verification caveat once; do not repeat it after every sentence.
5. If a record carries a caveat or conflict (sources disagree, a scheme period ended, rates differ, availability unclear), keep that uncertainty in your answer whenever it is relevant to the question. Do not resolve a conflict yourself. Do not mention internal dataset history (such as "corrected from legacy").
6. Do not turn "may apply" into "is eligible". You cannot decide whether a particular person qualifies: describe the criteria and tell them to confirm with the scheme authority. Never say "you are eligible".
7. Crop coverage: do not call a scheme specific to a crop (for example cotton) unless the record's crop coverage says it is crop-specific. "All crops" and "only crops notified for the season" are NOT crop-specific. If the context says no crop-specific scheme was found, say that first.
8. Distinguish central schemes (apply in every state) from state schemes (apply only in the listed state). A state mentioned in the question does not mean "only state schemes": central schemes that apply there are relevant.
9. If the user named a scheme, answer about that scheme only. Do not describe or recommend other schemes.
10. If the context marks an ANSWER FIELD, answer from that field first and directly. Bring in other fields only when they are needed to answer correctly.
11. A scheme whose status is superseded or repealed is no longer current: say so and name the replacement given in the context.
12. Give only URLs listed as "Official portal" or "Official source" in the context, and only helpline numbers listed under "Helplines recorded". Web addresses that merely appear inside a caveat are unconfirmed - do not give them. If none is listed, say none is recorded. Never make up a link or phone number.
13. If the context contains no relevant records, say the knowledge base has no relevant scheme for this question and do not name any scheme.
14. If the user's state is not known, do not present any state scheme as one the user can get. State schemes named in the retrieval notes as EXAMPLES may be mentioned only as examples, each with the state it applies in, and you must ask the user which state they live in. Never guess the user's state (not from the language of the question either).

STYLE
- Start with a one-sentence direct answer, then add short bullet points if useful. Plain, simple English (the answer may be translated afterwards).
- Keep scheme names, rupee amounts, percentages and numbers exactly as in the context. No emojis. Be concise.
- Ask a follow-up question only if a missing detail (for example the state or crop) would change the answer, and ask at most one.
- Earlier chat turns are conversational context only; they are not a source of facts."""

MAX_HISTORY_TURNS = 6

INTENT_DESCRIPTION = {
    "document_requirements": "the user asks which DOCUMENTS are required",
    "eligibility_check": "the user asks about ELIGIBILITY (who can get the scheme)",
    "benefits_query": "the user asks about BENEFITS (what they get / how much)",
    "application_process": "the user asks HOW TO APPLY (application process)",
    "helpline_query": "the user asks for a HELPLINE / contact number",
    "scheme_search": "the user wants to know WHICH SCHEMES are available / could apply to them",
    "price_query": "the user asks about market PRICES",
    "general_information": "a general question about schemes",
}
FIELD_TITLE = {"documents": "DOCUMENTS", "eligibility": "ELIGIBILITY", "benefits": "BENEFITS",
               "application": "APPLICATION PROCESS", "contacts": "HELPLINES / CONTACTS"}
FIELD_LABEL_KEY = {"documents": "documents_heading", "eligibility": "eligibility_heading", "benefits": "benefits_heading",
                   "application": "how_to_apply", "contacts": "helpline_heading"}
TRUSTED_SOURCE_TYPES = ("official", "press_release", "gazette")


# ======================================================================= small helpers
_SENT_END = re.compile(r"\.(?=\s+[A-Z])")      # a period followed by a capital letter: not "Rs. 3 lakh" or "e.g. x"


def _clip(text: str, n: int) -> str:
    """Shorten to about n characters, cutting at a sentence end when possible, otherwise with an ellipsis."""
    text = " ".join(str(text or "").split())
    if len(text) <= n:
        return text
    ends = [m.end() for m in _SENT_END.finditer(text[:n])]
    if ends and ends[-1] > n * 0.5:
        return text[:ends[-1]]
    return text[:n].rstrip(" ,;:") + "..."


def _fmt_amount(a: Any) -> str:
    if isinstance(a, bool) or a is None:
        return ""
    if isinstance(a, int):
        return f"{a:,}"
    return str(a)


# ----------------------------------------------------------------------- field extraction (adapter dict -> lines)
def documents_lines(s: Dict[str, Any]) -> List[str]:
    det = s.get("documents_detail")
    if det:
        out = []
        for d in det:
            m = d.get("mandatory")
            tag = "mandatory" if m is True else ("conditional/optional" if m is False else "requirement level not specified")
            line = f"{d.get('name')} [{tag}]"
            if d.get("notes"):
                line += f" - {d['notes']}"
            out.append(line)
        return out
    return [str(d) for d in (s.get("documents") or [])]


def eligibility_lines(s: Dict[str, Any]) -> List[str]:
    el = s.get("eligibility_detail")
    if isinstance(el, dict):
        out = list(el.get("criteria") or [])
        out += ["NOT ELIGIBLE / EXCLUDED: " + x for x in (el.get("exclusions") or [])]
        return out
    return [s["eligibility"]] if s.get("eligibility") else []


def benefits_lines(s: Dict[str, Any]) -> List[str]:
    ben = s.get("benefits_detail")
    if isinstance(ben, dict):
        out = [ben["summary"]] if ben.get("summary") else []
        for it in ben.get("items") or []:
            amt, unit, note = _fmt_amount(it.get("amount")), it.get("unit") or "", it.get("note") or ""
            core = " ".join(x for x in (it.get("type", "").replace("_", " "), amt, unit) if x)
            out.append("Item: " + core + (f" - {note}" if note else ""))
        return out
    return [s["benefits"]] if s.get("benefits") else []


def application_lines(s: Dict[str, Any]) -> List[str]:
    app = s.get("application")
    out: List[str] = []
    if isinstance(app, dict):
        if app.get("modes"):
            out.append("Application modes: " + ", ".join(app["modes"]))
        out += [f"Step {i}: {st}" for i, st in enumerate(app.get("steps") or [], 1)]
        if app.get("portal_url"):
            out.append("Official portal: " + app["portal_url"])
    elif s.get("application_url"):
        out.append("Official portal: " + s["application_url"])
    return out


def contacts_lines(s: Dict[str, Any]) -> List[str]:
    hl = (s.get("contacts") or {}).get("helplines") if isinstance(s.get("contacts"), dict) else None
    out = []
    for h in hl or []:
        if h.get("number"):
            out.append(f"{h['number']}" + (f" - {h['label']}" if h.get("label") else ""))
    if not out and s.get("helpline"):
        out = [str(s["helpline"])]
    return out


_FIELD_FN = {"documents": documents_lines, "eligibility": eligibility_lines, "benefits": benefits_lines,
             "application": application_lines, "contacts": contacts_lines}


# Sentences that only describe how a record was migrated/corrected are internal changelog, not uncertainty.
_HISTORY_SENTENCE = re.compile(r"^(?:Corrected from legacy|Migrated from|Merged from|Carried over from the legacy|Superseded\b|Repealed\b)", re.I)
_SENTENCE_SPLIT = re.compile(r"(?<=[a-z0-9\)])\.\s+(?=[A-Z])")


def clean_caveats(notes: str) -> str:
    """Keep the uncertainty/conflict/caveat sentences of a record's notes; drop pure changelog sentences."""
    kept = [s.strip() for s in _SENTENCE_SPLIT.split((notes or "").strip()) if s.strip() and not _HISTORY_SENTENCE.match(s.strip())]
    return " ".join(s if s.endswith(".") else s + "." for s in kept)


# ----------------------------------------------------------------------- record views
@dataclass
class RecordView:
    id: str
    name: str
    role: str
    level_badge: str
    applicability: str
    crop_coverage: str
    status: str
    status_line: str
    confidence: str
    verification_line: str
    why: str
    focus_field: Optional[str]
    focus_lines: List[str]
    overview: Dict[str, str]
    caveats: str
    portal_url: str                            # application portal ('' when none is recorded)
    official_urls: List[Tuple[str, str]]       # (label, url): portal first, then official source documents
    helplines: List[str]
    summary: str
    replacement_name: Optional[str] = None


def _crop_coverage(s: Dict[str, Any]) -> str:
    crops = [c for c in (s.get("crops") or [])]
    low = [c.lower() for c in crops]
    if not crops:
        return "Not a crop-specific scheme (no crop information recorded)"
    if "all" in low:
        return "All crops - NOT crop-specific"
    if "notified_crops" in low:
        return ("Only crops notified for the season/mandi - NOT crop-specific; whether a particular crop is covered "
                "depends on that notification")
    if low == ["horticultural_crops"]:
        return "Horticultural crops only"
    return "Crop-specific: " + ", ".join(crops)


def _applicability(s: Dict[str, Any], state: Optional[str]) -> Tuple[str, str]:
    central = (s.get("level") or s.get("type")) == "central" or "ALL" in (s.get("states") or [])
    if central:
        badge = "Central - all states"
        text = "Central scheme - applies in all states" + (f", so it applies in {state}" if state else "")
    else:
        sts = [x for x in (s.get("states") or []) if x != "ALL"] or ([s["state"]] if s.get("state") else [])
        badge = "State - " + ", ".join(sts)
        text = "State scheme - applies only in " + ", ".join(sts)
        if state and state not in sts:
            text += f" (NOT applicable in {state})"
    return badge, text


def _why(c: ScoredScheme, u) -> str:
    sig, bits = c.signals, []
    if sig["exact_scheme_match"]:
        bits.append("named by the user")
    if sig.get("replacement_for"):
        bits.append("current replacement of the scheme the user asked about")
    sm = sig["state_match"]
    if sm == "specific" and u.state:
        bits.append(f"state-specific scheme for {u.state}")
    elif sm == "applicable_central" and u.state:
        bits.append(f"central scheme that applies in {u.state}")
    cm = sig["crop_match"]
    if cm == "specific":
        bits.append(f"lists {u.crop} explicitly")
    elif cm == "category":
        bits.append("covers this crop's category")
    elif cm == "notified_crops":
        bits.append("covers crops notified for the season (not crop-specific)")
    elif cm == "all_crops":
        bits.append("applies to all crops (not crop-specific)")
    if sig["beneficiary_match"]:
        bits.append("meant for " + (u.beneficiary or "the user's group").replace("_", " ") + "s")
    topics = sorted(set(sig["activity_matches"] + sig["domain_matches"]))
    if topics:
        bits.append("matches topic: " + ", ".join(t.replace("_", " ") for t in topics))
    if sig["gender_match"]:
        bits.append("names women explicitly")
    return "; ".join(bits) if bits else "textually related to the question"


def _portal(s: Dict[str, Any]) -> str:
    app = s.get("application")
    portal = (app.get("portal_url") if isinstance(app, dict) else None) or s.get("application_url") or ""
    return portal if portal.startswith("http") else ""


def _urls(s: Dict[str, Any]) -> List[Tuple[str, str]]:
    out, seen = [], set()
    app = s.get("application")
    portal = (app.get("portal_url") if isinstance(app, dict) else None) or s.get("application_url") or ""
    if portal.startswith("http"):
        out.append(("Official portal", portal))
        seen.add(portal)
    for src in s.get("sources") or []:
        u = src.get("url") or ""
        if src.get("type") in TRUSTED_SOURCE_TYPES and u.startswith("http") and u not in seen:
            out.append(("Official source (information document, not an application page): " + _clip(src.get("title", ""), 90), u))
            seen.add(u)
    return out


def build_record_views(retrieval: RetrievalResult) -> List[RecordView]:
    u = retrieval.understanding
    all_schemes = [c.scheme for c in retrieval.candidates]
    by_id = {x.get("id"): x for x in all_schemes}
    focus_field = INTENT_FIELD.get(u.intent)
    views: List[RecordView] = []
    for c in retrieval.results:
        s = c.scheme
        status = s.get("status", "active")
        badge, applic = _applicability(s, u.state)
        rep_name = None
        if status != "active":
            rep = follow_replacement(s, all_schemes)
            if rep.get("id") != s.get("id"):
                rep_name = rep.get("name")
        status_line = status if status == "active" else (
            f"{status.upper()} - no longer current" + (f"; current replacement: {rep_name}" if rep_name else ""))
        conf = s.get("confidence") or "needs_review"
        if conf == "verified":
            ver = "verified" + (f" (checked against an official source on {s['last_verified']})" if s.get("last_verified") else "")
        else:
            ver = "needs_review - NOT checked against an official source; say 'the current Saarthi.AI dataset lists ...'"
        if c.signals["exact_scheme_match"]:
            role = "PRIMARY - the scheme the user named"
        elif c.signals.get("replacement_for"):
            old = by_id.get(c.signals["replacement_for"], {}).get("name", "the old scheme")
            role = f"REPLACEMENT - current successor of {old}"
        else:
            role = "RETRIEVED - matched the user's question"

        focus_lines = _FIELD_FN[focus_field](s) if focus_field else []
        overview: Dict[str, str] = {}
        if focus_field != "documents":
            d = documents_lines(s)
            if d:
                overview["Documents"] = "; ".join(_clip(x, 90) for x in d[:8])
        if focus_field != "eligibility":
            e = eligibility_lines(s)
            if e:
                overview["Eligibility"] = " | ".join(_clip(x, 230) for x in e[:3])
        if focus_field != "benefits":
            b = benefits_lines(s)
            if b:
                overview["Benefits"] = _clip(b[0], 380)
        if focus_field != "application":
            a = application_lines(s)
            if a:
                overview["Application"] = " | ".join(_clip(x, 160) for x in a[:3])
        views.append(RecordView(
            id=s.get("id") or s.get("name"), name=s.get("name", ""), role=role, level_badge=badge,
            applicability=applic, crop_coverage=_crop_coverage(s), status=status, status_line=status_line,
            confidence=conf, verification_line=ver, why=_why(c, u), focus_field=focus_field, focus_lines=focus_lines,
            overview=overview, caveats=clean_caveats(s.get("notes") or ""), portal_url=_portal(s), official_urls=_urls(s),
            helplines=contacts_lines(s), summary=s.get("summary") or s.get("description", ""), replacement_name=rep_name,
        ))
    return views


# ======================================================================= context for the LLM
@dataclass
class GroundedContext:
    text: str
    allowed_urls: Set[str] = field(default_factory=set)
    allowed_phone_digits: Set[str] = field(default_factory=set)
    views: List[RecordView] = field(default_factory=list)

    @classmethod
    def from_text(cls, text: str) -> "GroundedContext":
        """Context built elsewhere (legacy callers): every URL / number in the text counts as allowed."""
        return cls(text=text, allowed_urls={_norm_url(u) for u in _find_urls(text)},
                   allowed_phone_digits={re.sub(r"\D", "", m) for m in re.findall(r"\d[\d\s\-]{2,}\d", text)})


def build_grounded_context(retrieval: RetrievalResult, farmer_profile: Optional[Dict[str, Any]] = None) -> GroundedContext:
    u = retrieval.understanding
    views = build_record_views(retrieval)
    L: List[str] = ["SAARTHI.AI KNOWLEDGE CONTEXT",
                    "(The only source of facts you may use. Anything not written here is unknown to you.)", "",
                    "QUESTION ANALYSIS"]
    L.append(f"- Question type: {u.intent} - {INTENT_DESCRIPTION.get(u.intent, '')}")
    L.append("- Scheme(s) named by the user: " + (", ".join(s["name"] for s in u.schemes) if u.schemes else "none"))
    src = lambda s_: "from the question" if s_ == "query" else "from the user's saved profile, not the question"
    if u.state:
        L.append(f"- State: {u.state} ({src(u.state_source)})")
    if u.crop:
        L.append(f"- Crop: {u.crop} ({src(u.crop_source)})")
    if u.beneficiary:
        L.append(f"- User group: {', '.join(u.occupations)}" + (f"; farmer type: {', '.join(u.farmer_types)}" if u.farmer_types else ""))
    if u.gender:
        L.append(f"- Gender focus: {u.gender}")
    if u.land_ha is not None:
        L.append(f"- Land size mentioned: about {u.land_ha} hectares")
    if farmer_profile:
        L.append("- Self-reported profile (unverified, low priority): " + ", ".join(f"{k}={v}" for k, v in farmer_profile.items()))
    if retrieval.notes:
        L.append("- Retrieval notes (coverage facts - respect them in the answer):")
        L += [f"    * {n}" for n in retrieval.notes]
    if u.intent == "price_query":
        L.append("- The knowledge base holds NO commodity price data; schemes below (if any) are not price sources.")
    L.append(f"- Records supplied: {len(views)} (most relevant first)")
    L.append("")

    if not views:
        L.append("NO RELEVANT RECORDS were found in the knowledge base for this question.")
    for i, v in enumerate(views, 1):
        L.append(f"RECORD {i} of {len(views)} - {v.name}  [id: {v.id}]")
        L.append(f"Role: {v.role}")
        L.append(f"Applies to: {v.applicability}")
        L.append(f"Crop coverage: {v.crop_coverage}")
        L.append(f"Scheme status: {v.status_line}")
        L.append(f"Verification status: {v.verification_line}")
        L.append(f"Why it was retrieved: {v.why}")
        if v.focus_field:
            L.append(f">>> ANSWER FIELD - {FIELD_TITLE[v.focus_field]} (this answers the user's question):")
            if v.focus_lines:
                L += [f"    - {x}" for x in v.focus_lines]
            else:
                L.append(f"    NOT AVAILABLE: the knowledge base has no {v.focus_field} information for this scheme.")
            L.append("Other recorded information (reference only; do not volunteer it unless needed):")
        else:
            L.append("Recorded information:")
        L.append(f"    Summary: {_clip(v.summary, 400)}")
        for k, val in v.overview.items():
            L.append(f"    {k}: {val}")
        L.append("    Helplines recorded: " + ("; ".join(v.helplines) if v.helplines else "NONE RECORDED"))
        L.append("    Official portal: " + (v.portal_url or "NONE RECORDED"))
        L += [f"    {lab}: {url}" for lab, url in v.official_urls if url != v.portal_url]
        if v.caveats:
            L.append("    Caveats and uncertainties recorded for this record (keep relevant ones; never mention dataset history): " + v.caveats)
        L.append("")
    L.append("END OF CONTEXT")
    allowed_urls = {_norm_url(url) for v in views for _, url in v.official_urls}
    digits = {re.sub(r"\D", "", n) for v in views for h in v.helplines for n in re.findall(r"\d[\d\s\-]{2,}\d|\d{3,}", h)}
    return GroundedContext(text="\n".join(L), allowed_urls=allowed_urls, allowed_phone_digits=digits, views=views)


_LANG_NAMES = {"hi": "Hindi", "mr": "Marathi", "bn": "Bengali", "te": "Telugu", "ta": "Tamil", "gu": "Gujarati",
               "kn": "Kannada", "ml": "Malayalam", "pa": "Punjabi", "or": "Odia"}
_SCRIPT_NOTE = {"hi": "Devanagari script", "mr": "Devanagari script - Marathi, NOT Hindi"}
_ENGLISH_STYLE = "Plain, simple English (the answer may be translated afterwards)."


def system_prompt(answer_language: str = "en") -> str:
    """The grounding prompt; for another answer language the answer is written directly in that language."""
    if not answer_language or answer_language == "en" or answer_language not in _LANG_NAMES:
        return GROUNDING_SYSTEM_PROMPT
    name = _LANG_NAMES[answer_language]
    script = _SCRIPT_NOTE.get(answer_language, f"the {name} script")
    return (GROUNDING_SYSTEM_PROMPT.replace(_ENGLISH_STYLE, f"Natural, simple, everyday {name} that a farmer understands.")
            + f"""

ANSWER LANGUAGE - {name.upper()}
- Write the ENTIRE answer in {name} ({script}): the direct answer, bullet points, notes, caveats and any follow-up question.
- The context is in English: translate its meaning faithfully; do not add, drop, soften or strengthen any fact.
- Copy these exactly as they appear in the context, in Latin letters and ASCII digits: official scheme names and
  acronyms (for example PM-KISAN, PMFBY, KCC), URLs, phone numbers, rupee amounts, percentages and dates.""")


def build_messages(context_text: str, question: str, conversation_history: Optional[list] = None,
                   answer_language: str = "en", original_question: Optional[str] = None) -> List[Dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt(answer_language)}]
    for msg in (conversation_history or [])[-MAX_HISTORY_TURNS * 2:]:
        role, content = msg.get("role", "user"), msg.get("content", "")
        if role in ("user", "assistant") and content:
            if role == "assistant" and len(content) > 800:
                content = content[:800] + "..."
            messages.append({"role": role, "content": content})
    asked = f"\n(The user asked in their own language: {original_question})" if original_question and original_question != question else ""
    tail = "Answer using only the knowledge context above and follow the grounding rules."
    if answer_language and answer_language != "en" and answer_language in _LANG_NAMES:
        tail += f" Write the answer in {_LANG_NAMES[answer_language]}."
    messages.append({"role": "user", "content": f"{context_text}\n\nUSER QUESTION: {question}{asked}\n\n{tail}"})
    return messages


# ======================================================================= output guard
_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>()\[\]\"']+"
    r"|\b[a-z0-9][a-z0-9\-]*(?:\.[a-z0-9\-]+)*\.(?:gov\.in|nic\.in|gov|nic|org|com|net|co\.in|in)\b(?:/[^\s<>()\[\]\"']*)?", re.I)
_CUE_RE = re.compile(r"helpline|toll[\s-]?free|call|phone|contact|dial|number"
                     r"|हेल्पलाइन|हेल्पलाईन|टोल|कॉल|फोन|फ़ोन|संपर्क|नंबर|क्रमांक|डायल", re.I)
_NUM_RE = re.compile(r"(?<![\d,.])(\d[\d\s\-]{1,14}\d)(?![\d,])")
_MONEY_BEFORE = re.compile(r"(?:rs\.?|inr|₹)\s*$", re.I)
_UNIT_AFTER = re.compile(r"^\s*(?:%|per\b|hectare|acre|days?\b|lakh|crore|hp\b|mt\b|kw|mw|years?\b|months?\b|times?\b)", re.I)


def _find_urls(text: str) -> List[str]:
    return [m.group(0) for m in _URL_RE.finditer(text)]


def _norm_url(u: str) -> str:
    u = u.strip().rstrip(".,;:!?)]}\"'").lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    return u.rstrip("/")


def check_answer_grounding(answer: str, gctx: GroundedContext) -> List[str]:
    """Hard grounding violations: a URL or phone number in the answer that the records do not contain."""
    problems: List[str] = []
    for raw in _find_urls(answer):
        n = _norm_url(raw)
        if not any(n == a or n.startswith(a + "/") or a.startswith(n + "/") or n.startswith(a) for a in gctx.allowed_urls):
            problems.append("url:" + raw)
    for line in re.split(r"(?<=[.!?])\s+(?=[A-Z])|\n", answer):          # a period before a digit ("Rs. 1500") is not a sentence end
        is_cue = bool(_CUE_RE.search(line))
        for m in _NUM_RE.finditer(line):
            digits = re.sub(r"\D", "", m.group(1))
            if digits in gctx.allowed_phone_digits:
                continue
            before, after = line[:m.start()], line[m.end():]
            if _MONEY_BEFORE.search(before) or _UNIT_AFTER.search(after):
                continue
            tollfree_or_std = digits.startswith("1800") or (digits.startswith("0") and len(digits) >= 9)
            if tollfree_or_std or (is_cue and 3 <= len(digits) <= 12 and not (1990 <= int(digits) <= 2100 if len(digits) == 4 else False)):
                problems.append("phone:" + m.group(1).strip())
    return problems


# ======================================================================= offline answer (same views, same facts)
def render_offline_answer(retrieval: RetrievalResult, language: str = "en") -> str:
    """Deterministic, intent-aware answer built from the same record views the LLM would see."""
    from app.services.offline_answer_engine import _get_labels
    labels = _get_labels(language)
    u = retrieval.understanding
    views = build_record_views(retrieval)
    out: List[str] = []

    if u.intent == "price_query":
        return ("Saarthi.AI does not hold market price data, so I cannot give current prices. "
                "Please check your local mandi or the official market portals.\n\n" + labels["footer"])
    if not views:
        return f"{labels['no_match']}\n\n{labels['footer']}"

    for n in retrieval.notes:
        out.append(f"Note: {n}")
    if retrieval.notes:
        out.append("")

    ff = INTENT_FIELD.get(u.intent)
    if ff:
        heading = labels[FIELD_LABEL_KEY[ff]]
        for v in views:
            out.append(f"**{v.name}** [{v.level_badge}]")
            if v.status != "active":
                out.append(f"Status: {v.status_line}")
            out.append(f"**{heading}:**")
            if v.focus_lines:
                out += [f"- {x}" for x in v.focus_lines]
            else:
                out.append(f"- Not available: the current Saarthi.AI knowledge base has no {ff} information for this scheme.")
            if ff == "application" and not v.portal_url:
                out.append("- No official application portal URL is recorded for this scheme.")
            if ff == "contacts" and not v.helplines:
                out.append("- No helpline number is recorded for this scheme.")
            out.append(f"Only the {ff} information shown above is recorded in the current Saarthi.AI knowledge base; "
                       "anything not listed here is not available in it.")
            if v.caveats and v.confidence != "verified":
                out.append(f"Caveat recorded in the dataset: {_clip(v.caveats, 900)}")
            out.append("")
    else:
        out.append(f"{len(views)} scheme(s) in the Saarthi.AI knowledge base match your question:")
        for i, v in enumerate(views, 1):
            out.append(f"\n{i}. **{v.name}** [{v.level_badge}]")
            if v.status != "active":
                out.append(f"   Status: {v.status_line}")
            out.append(f"   {_clip(v.summary, 220)}")
            out.append(f"   Crop coverage: {v.crop_coverage}")
            if v.overview.get("Benefits"):
                out.append(f"   Benefits: {_clip(v.overview['Benefits'], 200)}")
            if v.caveats and v.confidence != "verified":
                out.append(f"   Caveat recorded in the dataset: {_clip(v.caveats, 300)}")
        out.append("")
    if any(v.confidence != "verified" for v in views):
        out.append("This information comes from the Saarthi.AI dataset and is currently marked as needing verification. "
                   "Please confirm the details with the official portal or your nearest CSC / agriculture office.")
    return "\n".join(out).strip()
