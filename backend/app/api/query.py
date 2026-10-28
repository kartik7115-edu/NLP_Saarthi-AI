import asyncio
import logging
import re
import time
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Request, Response
from app.schemas import QueryRequest, QueryResponse
from app.services.rag_engine import rag_engine
from app.services.intent_detection import detect_intent, stem, tokenize
from app.config import settings
from app.services import llm_translator
from app.services.answer_localizer import localize_with_fallbacks, LocalizationResult
from app.services.language_utils import (
    LANGUAGE_SCRIPT, detect_devanagari_language, dominant_script, is_text_in_language, normalize_language,
    source_language_for_query,
)
from app.services.multilingual_query import english_keywords
from app.services.recommendation_engine import recommend_schemes
from app.services.translator import translator_service
from app.services.brightdata_service import search_schemes as brightdata_search
from app.services.brightdata_service import search_document_links
from app.services.help_center_service import get_help_centers
from app.services.gov_data_service import enrich_query_context, get_relevant_gov_links

logger = logging.getLogger(__name__)

router = APIRouter()

DEVANAGARI_RANGE = range(0x0900, 0x097F + 1)
SCRIPT_RANGES = {
    "hi": range(0x0900, 0x097F + 1),
    "mr": range(0x0900, 0x097F + 1),
    "bn": range(0x0980, 0x09FF + 1),
    "te": range(0x0C00, 0x0C7F + 1),
    "ta": range(0x0B80, 0x0BFF + 1),
    "gu": range(0x0A80, 0x0AFF + 1),
    "kn": range(0x0C80, 0x0CFF + 1),
    "ml": range(0x0D00, 0x0D7F + 1),
    "pa": range(0x0A00, 0x0A7F + 1),
    "or": range(0x0B00, 0x0B7F + 1),
    "ur": range(0x0600, 0x06FF + 1),
    "as": range(0x0980, 0x09FF + 1),
}

def _text_likely_in_language(text: str, lang: str) -> bool:
    """Is `text` written in `lang`? Share-of-letters check (see language_utils), not an absolute character count."""
    return is_text_in_language(text, lang)

def _is_safe_url(url: str) -> bool:
    if not url:
        return False
    url_stripped = url.strip().lower()
    return url_stripped.startswith("http://") or url_stripped.startswith("https://")

_rate_limit: dict = defaultdict(list)
RATE_LIMIT_WINDOW = 60
RATE_LIMIT_MAX = 20

def _check_rate_limit(client_ip: str):
    now = time.time()
    _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if now - t < RATE_LIMIT_WINDOW]
    if len(_rate_limit[client_ip]) >= RATE_LIMIT_MAX:
        raise HTTPException(status_code=429, detail="Too many requests. Please wait a moment.")
    _rate_limit[client_ip].append(now)


async def _run_brightdata_search(english_query):
    try:
        result = await asyncio.to_thread(brightdata_search, english_query)
        if result:
            logger.info(f"Bright Data returned {len(result)} snippet(s)")
        return result or []
    except Exception as e:
        logger.warning(f"Bright Data search failed (non-fatal): {e}")
        return []


async def _run_gov_enrichment(english_query, crop):
    try:
        return await asyncio.to_thread(enrich_query_context, english_query, crop=crop)
    except Exception as e:
        logger.warning(f"Gov data enrichment failed (non-fatal): {e}")
        return ""


async def _run_rag_search(english_query, user_state, occupation, profile=None):
    return await asyncio.to_thread(
        rag_engine.retrieve,
        english_query,
        user_state=user_state,
        occupation=occupation,
        profile=profile,
    )


_REC_STOP = {"what", "which", "who", "how", "are", "the", "for", "and", "can", "does", "with", "this", "that",
             "scheme", "schemes", "available", "required", "documents", "document", "apply", "get", "tell", "about"}


def _rec_relevance(rec, query_stems, retrieved_names):
    """Whole-token relevance of a recommendation to the question (no substring matching)."""
    if rec["name"] in retrieved_names:
        return 100
    words = {stem(t) for t in tokenize(rec["name"] + " " + rec.get("description", ""))}
    return sum(1 for w in query_stems if w in words)


async def _run_doc_link_search(english_query, primary_scheme_name):
    try:
        return await asyncio.to_thread(search_document_links, english_query, primary_scheme_name)
    except Exception as e:
        logger.warning(f"Bright Data doc link search failed (non-fatal): {e}")
        return []


async def _run_gov_links(english_query):
    try:
        return await asyncio.to_thread(get_relevant_gov_links, english_query)
    except Exception as e:
        logger.warning(f"Gov links failed (non-fatal): {e}")
        return []


async def _run_translate(text, source, target):
    try:
        return await asyncio.to_thread(translator_service.translate_text, text, source, target)
    except Exception as e:
        logger.warning(f"Translation failed (non-fatal): {e}")
        return text


async def _run_transliterate(text, language):
    try:
        return await asyncio.to_thread(translator_service.transliterate_text, text, language)
    except Exception as e:
        logger.warning(f"Transliteration failed (non-fatal): {e}")
        return None


def _translation_providers():
    """Ordered (name, translate_fn) list for localizing answers. Every fn RAISES on failure.

    "machine" = translator_service (Azure Translator when a key is configured, otherwise the free Google endpoint);
    "groq"    = the configured Groq model (only when Groq is configured). Order: settings.translation_provider_order.
    """
    out = []
    for name in [p.strip().lower() for p in (settings.translation_provider_order or "machine,groq").split(",") if p.strip()]:
        if name == "machine":
            out.append(("machine", translator_service.translate_text_strict))
        elif name == "groq" and llm_translator.is_available():
            out.append(("groq", llm_translator.translate_with_groq))
    return out


def _usable_english(candidate, original: str) -> bool:
    """A question translation is usable for retrieval only if it really is (mostly Latin-script) text, not the input echoed."""
    return (isinstance(candidate, str) and bool(candidate.strip()) and candidate.strip() != original.strip()
            and is_text_in_language(candidate, "en"))


async def _run_llm_translate(text, source, target):
    if not llm_translator.is_available():
        return None
    try:
        return await asyncio.to_thread(llm_translator.translate_with_groq, text, source, target)
    except Exception as e:
        logger.warning("Groq question translation failed (non-fatal): %s", getattr(e, "code", type(e).__name__))
        return None


def _resolve_auto_language(detected, query: str):
    """Answer language for language='auto': the detector's result, made safe for Devanagari and unsupported codes."""
    lang = normalize_language(detected) if isinstance(detected, str) else None
    script = dominant_script(query)
    if script == "devanagari" and lang in (None, "auto", "hi", "mr"):
        # langdetect can return 'ne'/'sa' for Devanagari; Hindi and Marathi are told apart by their words
        lang = detect_devanagari_language(query) or (lang if lang in ("hi", "mr") else "hi")
    if lang in (None, "auto") and script not in (None, "latin"):
        candidates = [l for l, sc in LANGUAGE_SCRIPT.items() if sc == script]
        if len(candidates) == 1:
            lang = candidates[0]
    return lang if lang not in (None, "auto") else "en"


_CARD_MARK = "SAARTHICARD{}"
_CARD_SPLIT = re.compile(r"SAARTHICARD(\d+)")


def _localize_cards(descriptions, target_lang, names, providers):
    """Translate every card description in ONE request per provider (all or nothing). Returns (texts|None, result)."""
    marks = [_CARD_MARK.format(i) for i in range(len(descriptions))]
    combined = "\n".join(f"{m} {d}" for m, d in zip(marks, descriptions))
    res = localize_with_fallbacks(combined, target_lang, list(names) + marks, providers)
    if res.status != "translated":
        return None, res
    pieces = _CARD_SPLIT.split(res.text)
    out = {}
    for i in range(1, len(pieces) - 1, 2):
        out[int(pieces[i])] = pieces[i + 1].strip()
    if sorted(out) != list(range(len(descriptions))) or not all(out.values()):
        return None, LocalizationResult(combined, "en", "failed", "card_split_failed", attempts=res.attempts)
    return [out[i] for i in range(len(descriptions))], res


def _state_applicable(rec, state) -> bool:
    """Central schemes always; a state scheme only when the user's state is known and is the scheme's state."""
    rec_state = (rec.get("state") or "").strip()
    if rec.get("type") != "state" and not rec_state:
        return True
    return bool(state) and rec_state.lower() == str(state).strip().lower()


@router.post("", response_model=QueryResponse, include_in_schema=False)   # "/query" without the slash: no 307 round trip
@router.post("/", response_model=QueryResponse)
async def handle_query(request: QueryRequest, raw_request: Request, response: Response):
    client_ip = raw_request.client.host if raw_request.client else "unknown"
    _check_rate_limit(client_ip)
    original_query = request.query
    # Background pre-caching by the frontend (popular questions for offline use) must not spend the Groq quota the
    # user's own questions need: it gets the grounded offline answer and machine translation only.
    background = raw_request.headers.get("x-saarthi-precache") == "1"
    if background:
        response.headers["X-Saarthi-Precache"] = "1"

    # --- languages: the language the ANSWER must be in (selected) is separate from the language the QUESTION is in
    language_note = None
    answer_lang = normalize_language(request.language)
    if answer_lang is None:                       # unsupported code such as "xyz": never treated as a supported language
        language_note = "unsupported_language_code"
        answer_lang = "auto"
    if answer_lang == "auto":
        detected = await asyncio.to_thread(translator_service.detect_language, original_query)
        answer_lang = _resolve_auto_language(detected, original_query)
    query_lang = source_language_for_query(original_query, answer_lang)

    # --- the QUESTION is made English for retrieval; the original question is kept for the answer language.
    english_query = original_query
    retrieval_query = original_query
    query_translation = "not_needed"
    keywords = ""
    if query_lang != "en":
        keywords = english_keywords(original_query)
        translated_query = await _run_translate(original_query, query_lang, "en")
        if _usable_english(translated_query, original_query):
            english_query, query_translation = translated_query, "machine"
        else:
            translated_query = None if background else await _run_llm_translate(original_query, query_lang, "en")
            if _usable_english(translated_query, original_query):
                english_query, query_translation = translated_query, "groq"
            else:
                query_translation = "failed"
        if query_translation == "failed":
            # No translation: keep the original words (Devanagari scheme aliases still resolve) plus English keywords,
            # so the question is not reported as "no information" merely because it is not in English.
            retrieval_query = (original_query + " " + keywords).strip()
            logger.warning("Question could not be translated from %s; retrieving with %d English keyword(s)",
                           query_lang, len(keywords.split()))
        else:
            retrieval_query = english_query
    response.headers["X-Query-Translation"] = query_translation

    farmer_profile = {
        "state": request.state or request.location,
        "occupation": request.occupation,
        "income": request.income,
        "crop": request.crop,
        "land_size": request.land_size,
    }
    farmer_profile = {k: v for k, v in farmer_profile.items() if v}

    retrieval, web_snippets, gov_context = await asyncio.gather(
        _run_rag_search(
            retrieval_query,
            user_state=request.state or request.location,
            occupation=request.occupation,
            profile=farmer_profile if farmer_profile else None,
        ),
        _run_brightdata_search(english_query),
        _run_gov_enrichment(english_query, crop=request.crop),
    )
    if not retrieval.schemes and keywords and query_translation != "failed":
        # the translation was worded differently from the dataset (e.g. "grant" for अनुदान): retry with the keywords
        retry = await _run_rag_search(english_query + " " + keywords, user_state=request.state or request.location,
                                      occupation=request.occupation, profile=farmer_profile if farmer_profile else None)
        if retry.schemes:
            retrieval = retry
    top_schemes = retrieval.schemes
    intent = retrieval.understanding.intent
    known_state = retrieval.understanding.state
    logger.debug("Retrieval: %s", retrieval.debug_rows(5))

    context = rag_engine._build_rich_context(
        schemes=top_schemes,
        web_snippets=web_snippets,
        farmer_profile=farmer_profile if farmer_profile else None,
        notes=retrieval.notes,
    )
    if gov_context:
        context = context + "\n\n" + gov_context if context else gov_context

    conv_history = None
    if request.conversation_history:
        conv_history = [{"role": m.role, "content": m.content} for m in request.conversation_history]

    answer_result = await asyncio.to_thread(
        rag_engine.generate_answer_with_meta,
        context,
        english_query,
        farmer_profile=farmer_profile if farmer_profile else None,
        language="en",            # generate in English; the answer is localized below (grounding is checked in English)
        matched_schemes=top_schemes,
        conversation_history=conv_history,
        retrieval=retrieval,
        allow_llm=not background,
        answer_language=answer_lang,
        original_question=original_query if english_query != original_query else None,
    )
    answer = answer_result.answer
    # The response body schema is unchanged; how the answer was produced is exposed as a header so an
    # offline fallback is never mistaken for a Groq answer.
    response.headers["X-Answer-Source"] = answer_result.source

    # Official scheme names and aliases of the retrieved records must survive translation untouched.
    protected_terms = ["Saarthi.AI"]
    for s in top_schemes + [c.scheme for c in getattr(retrieval, "state_examples", [])]:
        protected_terms.append(s.get("name", ""))
        protected_terms.extend(s.get("aliases") or [])

    providers = [p for p in _translation_providers() if not (background and p[0] == "groq")]
    localization = LocalizationResult(answer, answer_result.language, "not_needed")
    if answer and answer_result.language == answer_lang != "en":
        # Groq wrote the answer directly in the requested language (validated in rag_engine): nothing to translate
        localization = LocalizationResult(answer, answer_lang, "translated", provider="groq_direct")
    elif answer and answer_result.language != answer_lang:
        localization = await asyncio.to_thread(localize_with_fallbacks, answer, answer_lang, protected_terms, providers)
        if localization.status == "failed":
            # Fallback policy: the English answer, labelled "en", status "failed" (the frontend tells the user).
            logger.warning("Answer could not be translated to %s (%s); returning the English answer labelled 'en'",
                           answer_lang, ", ".join(localization.attempts) or localization.reason)
    final_answer = localization.text
    response_language = localization.language          # always the language `final_answer` is really in

    response.headers["X-Requested-Language"] = answer_lang
    response.headers["X-Answer-Language"] = response_language
    response.headers["X-Translation-Status"] = localization.status
    if localization.provider:
        response.headers["X-Translation-Provider"] = localization.provider
    if localization.reason:
        response.headers["X-Translation-Error"] = localization.reason
    if localization.attempts:
        response.headers["X-Translation-Attempts"] = ",".join(localization.attempts)
    if language_note:
        response.headers["X-Language-Note"] = language_note

    answer_romanized = None
    if localization.status == "translated":
        romanized = await _run_transliterate(final_answer, answer_lang)
        answer_romanized = romanized if isinstance(romanized, str) and romanized.strip() else None

    include_schemes = intent in ("scheme_search", "eligibility_check", "benefits_query",
                                    "document_requirements", "application_process", "general_information")
    include_centers = intent in ("helpline_query", "application_process", "general_information")
    include_doc_links = intent not in ("helpline_query",)

    final_recommendations = []
    doc_links_task = None
    gov_links_task = None

    if include_doc_links:
        gov_links_task = _run_gov_links(english_query)
        if top_schemes:
            primary_scheme = top_schemes[0]
            doc_links_task = _run_doc_link_search(english_query, primary_scheme["name"])

    if include_schemes:
        # Profile-based suggestions never include another state's schemes, nor any state scheme when the state is unknown.
        raw_recommendations = [r for r in recommend_schemes(request.model_dump()) if _state_applicable(r, known_state)]
        query_stems = {stem(w) for w in tokenize(retrieval_query) if w not in _REC_STOP and stem(w) not in _REC_STOP and len(w) > 3}
        retrieved_names = {s["name"] for s in top_schemes}
        scored_recs = []
        for rec in raw_recommendations:
            scored_recs.append((_rec_relevance(rec, query_stems, retrieved_names), rec))
        scored_recs.sort(key=lambda x: x[0], reverse=True)
        # Lead with what retrieval found for this question, then profile recommendations that are
        # relevant by whole-token overlap. The profile-only fallback is kept for when nothing was retrieved.
        retrieved_recs = [{"name": s["name"], "description": s.get("description", ""), "type": s.get("type"),
                           "state": s.get("state"), "documents_links": s.get("documents_links")} for s in top_schemes]
        filtered_recs = (retrieved_recs + [r for score, r in scored_recs if score > 0 and r["name"] not in retrieved_names])[:5]
        if not filtered_recs:
            filtered_recs = [r for _, r in scored_recs[:3]]

        if answer_lang != "en" and localization.status == "translated" and filtered_recs:
            descs = [rec["description"] or "" for rec in filtered_recs]
            names = [rec["name"] for rec in filtered_recs] + protected_terms
            localized, card_result = (None, None)
            card_providers = [p for p in providers if p[0] != "groq" or settings.translate_cards_with_llm]
            if all(d.strip() for d in descs) and card_providers:
                localized, card_result = await asyncio.to_thread(_localize_cards, descs, answer_lang, names, card_providers)
            response.headers["X-Cards-Translation"] = "translated" if localized else "failed"
            if not localized:
                logger.warning("Recommendation cards could not be translated to %s (%s); keeping English descriptions",
                               answer_lang, ", ".join(card_result.attempts) if card_result else "empty_description")
            descriptions = localized or descs
            for rec, description in zip(filtered_recs, descriptions):
                final_recommendations.append({
                    "name": rec["name"],                     # official scheme names are never machine-translated
                    "description": description,
                    "type": rec.get("type"),
                    "state": rec.get("state"),
                    "documents_links": rec.get("documents_links")
                })
        else:
            for rec in filtered_recs:
                final_recommendations.append({
                    "name": rec["name"],
                    "description": rec["description"],
                    "type": rec.get("type"),
                    "state": rec.get("state"),
                    "documents_links": rec.get("documents_links")
                })

    doc_links = []
    if include_doc_links:
        seen_urls = set()
        for scheme in top_schemes:
            for link in scheme.get("documents_links", []) or []:
                if link not in seen_urls and _is_safe_url(link):
                    seen_urls.add(link)
                    doc_links.append({"title": scheme["name"], "url": link})

        if doc_links_task:
            enriched_links = await doc_links_task
            for el in enriched_links:
                if el["url"] not in seen_urls and _is_safe_url(el["url"]):
                    seen_urls.add(el["url"])
                    doc_links.append({"title": el["title"], "url": el["url"]})

        if gov_links_task:
            gov_links = await gov_links_task
            for gl in gov_links:
                if gl["url"] not in seen_urls and _is_safe_url(gl["url"]):
                    seen_urls.add(gl["url"])
                    doc_links.append({"title": gl["title"], "url": gl["url"]})

    nearest_centers = []
    if include_centers:
        user_state = request.state or request.location
        centers = get_help_centers(state=user_state)
        for c in centers:
            nearest_centers.append({
                "name": c["name"],
                "type": c["type"],
                "phone": c["phone"],
                "address": c["address"],
                "district": c["district"],
                "maps_url": c.get("maps_url"),
            })

    logger.info("query lang=%s source=%s translation=%s provider=%s attempts=%s records=%d",
                answer_lang, answer_result.source + (" (saved)" if answer_result.cached else ""), localization.status,
                localization.provider or "-", ",".join(localization.attempts) or "-", len(top_schemes))
    return QueryResponse(
        intent=intent,
        answer=final_answer,
        answer_romanized=answer_romanized,
        recommended_schemes=final_recommendations,
        response_language=response_language,
        doc_links=doc_links,
        nearest_centers=nearest_centers,
        requested_language=answer_lang,
        translation_status=localization.status,
    )
