import json
import logging
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from app.services.scheme_loader import load_schemes
from app.services.retrieval import RetrievalResult, retrieve as _structured_retrieve
from app.services.offline_answer_engine import generate_offline_answer
from app.services import groq_client
from app.services.grounded_answer import (
    GroundedContext, build_grounded_context, build_messages, check_answer_grounding, render_offline_answer,
)
from app.services.answer_localizer import native_answer_problem
from app.config import settings

logger = logging.getLogger(__name__)

_answer_cache = {}
_ANSWER_CACHE_MAX = 500
_ANSWER_CACHE_TTL = 3600          # legacy name; the effective TTL is settings.answer_cache_ttl_seconds
_cache_lock = threading.Lock()
_cache_loaded_from = None
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _cache_path():
    p = (settings.answer_cache_file or "").strip()
    if not p:
        return None
    return p if os.path.isabs(p) else os.path.join(_BACKEND_DIR, p)


def _cache_ttl() -> float:
    return float(settings.answer_cache_ttl_seconds or _ANSWER_CACHE_TTL)


def _cache_get(key):
    path = _cache_path()
    global _cache_loaded_from
    if path and _cache_loaded_from != path:
        _cache_loaded_from = path
        try:
            with open(path, encoding="utf-8") as f:
                for k, v in (json.load(f) or {}).items():
                    _answer_cache.setdefault(k, v)
            logger.info("Loaded %d saved answers from %s", len(_answer_cache), path)
        except FileNotFoundError:
            pass
        except Exception as e:                                    # a corrupt cache file must never break answering
            logger.warning("Ignoring unreadable answer cache %s: %s", path, type(e).__name__)
    entry = _answer_cache.get(key)
    if entry and (time.time() - entry["ts"]) < _cache_ttl():
        return entry
    return None


def _cache_put(key, answer, language="en"):
    with _cache_lock:
        if len(_answer_cache) >= _ANSWER_CACHE_MAX:
            del _answer_cache[min(_answer_cache, key=lambda k: _answer_cache[k]["ts"])]
        _answer_cache[key] = {"answer": answer, "ts": time.time(), "language": language}
        path = _cache_path()
        if not path:
            return
        try:
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(_answer_cache, f, ensure_ascii=False)
            os.replace(tmp, path)
        except Exception as e:
            logger.warning("Could not save the answer cache: %s", type(e).__name__)

LANG_NAMES = {
    "hi": "Hindi", "en": "English", "bn": "Bengali", "te": "Telugu",
    "mr": "Marathi", "ta": "Tamil", "gu": "Gujarati", "kn": "Kannada",
    "ml": "Malayalam", "pa": "Punjabi", "or": "Odia",
}


def _build_azure_client():
    """DEPRECATED - no longer used by the answer path (Groq replaced Azure OpenAI).

    Kept only because existing tests patch this name; safe to delete together with those patches.
    """
    if settings.offline_only:
        return None
    if not settings.azure_openai_api_key or not settings.azure_openai_endpoint or not settings.azure_openai_deployment:
        return None
    try:
        from openai import AzureOpenAI
        client = AzureOpenAI(
            azure_endpoint=settings.azure_openai_endpoint,
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
        )
        return client
    except Exception as e:
        logger.warning(f"Could not initialise Azure OpenAI client: {e}")
        return None


@dataclass
class AnswerResult:
    """An answer plus how it was produced, so callers never mistake an offline answer for a Groq answer."""
    answer: str
    source: str                      # "groq" | "offline"
    model: Optional[str] = None
    fallback_reason: Optional[str] = None
    cached: bool = False
    language: str = "en"             # language `answer` is actually written in (Groq answers are always English)


class RAGEngine:
    def __init__(self):
        self.schemes = []          # active schemes (legacy-compatible dicts)
        self._all_schemes = []     # active + superseded/repealed, so old scheme names still resolve
        self._azure_client = None

    def load_documents(self):
        print("Loading schemes for RAG Engine...")
        self.schemes = load_schemes()
        self._all_schemes = load_schemes(include_inactive=True)
        return self.schemes

    def create_embeddings(self, texts):
        return []

    def build_vector_index(self):
        if not self.schemes:
            self.load_documents()
        print("Vector index build skipped (keyword-search mode).")

    def retrieve(self, query: str, top_k: int = 5, user_state: Optional[str] = None, occupation: Optional[str] = None,
                 profile: Optional[Dict[str, Any]] = None) -> RetrievalResult:
        """Structured retrieval with the understanding, per-scheme signals and coverage notes.

        Entities found in the question itself (state, crop, beneficiary, scheme ...) always win;
        user_state / occupation / profile are lower-priority fallbacks.
        """
        if not self._all_schemes:
            self.load_documents()
        prof = dict(profile or {})
        if user_state and not prof.get("state"):
            prof["state"] = user_state
        if occupation and not prof.get("occupation"):
            prof["occupation"] = occupation
        return _structured_retrieve(query, self._all_schemes, profile=prof, top_k=top_k)

    def search_similar(self, query: str, top_k: int = 5, user_state: Optional[str] = None, occupation: Optional[str] = None,
                       profile: Optional[Dict[str, Any]] = None):
        """Backwards-compatible wrapper: returns the ranked scheme dicts (legacy shape)."""
        return self.retrieve(query, top_k=top_k, user_state=user_state, occupation=occupation, profile=profile).schemes

    def _build_rich_context(self, schemes: list, web_snippets: list = None, farmer_profile: dict = None, notes: list = None) -> str:
        parts = []

        if farmer_profile:
            profile_parts = []
            if farmer_profile.get("state"):
                profile_parts.append(f"State: {farmer_profile['state']}")
            if farmer_profile.get("crop"):
                profile_parts.append(f"Primary Crop: {farmer_profile['crop']}")
            if farmer_profile.get("land_size"):
                profile_parts.append(f"Land Size: {farmer_profile['land_size']} acres")
            if farmer_profile.get("income"):
                profile_parts.append(f"Annual Income: ₹{farmer_profile['income']}")
            if farmer_profile.get("occupation"):
                profile_parts.append(f"Occupation: {farmer_profile['occupation']}")
            if profile_parts:
                parts.append("=== FARMER PROFILE ===\n" + "\n".join(profile_parts))

        if schemes:
            scheme_texts = []
            for s in schemes:
                lines = [f"Scheme: {s['name']}"]
                if s.get('type'):
                    lines.append(f"  Type: {s['type'].title()} scheme")
                if s.get('state'):
                    lines.append(f"  State: {s['state']}")
                if s.get('description'):
                    lines.append(f"  Description: {s['description']}")
                if s.get('eligibility'):
                    lines.append(f"  Eligibility: {s['eligibility']}")
                if s.get('benefits'):
                    lines.append(f"  Benefits: {s['benefits']}")
                if s.get('target_group'):
                    lines.append(f"  Target Group: {s['target_group']}")
                if s.get('documents'):
                    lines.append(f"  Documents Required: {', '.join(s['documents'][:6])}")
                if s.get('helpline'):
                    lines.append(f"  Helpline: {s['helpline']}")
                if s.get('application_url') and s['application_url'].startswith('http'):
                    lines.append(f"  Apply: {s['application_url']}")
                if s.get('documents_links'):
                    lines.append(f"  Official Links: {', '.join(s['documents_links'][:3])}")
                scheme_texts.append("\n".join(lines))
            parts.append("=== GOVERNMENT SCHEMES ===\n" + "\n\n".join(scheme_texts))

        if notes:
            parts.append("=== RETRIEVAL NOTES ===\n" + "\n".join(f"- {n}" for n in notes))

        if web_snippets:
            parts.append("=== WEB SEARCH RESULTS ===\n" + "\n".join(f"- {s}" for s in web_snippets))

        return "\n\n".join(parts)

    def _cache_key(self, context: str, question: str, profile: dict = None, history_summary: str = "") -> str:
        raw = f"{question}|{context[:500]}|{str(sorted((profile or {}).items()))}|{history_summary}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _groq_cache_key(self, model: str, context: str, question: str, profile: dict, history_summary: str,
                        language: str = "en") -> str:
        raw = f"{model}|{language}|{context}|{question}|{sorted((profile or {}).items())}|{history_summary}"
        return hashlib.md5(raw.encode()).hexdigest()

    def _offline_answer(self, question: str, farmer_profile: dict, language: str, matched_schemes: list,
                        retrieval: Optional[RetrievalResult]) -> str:
        if retrieval is not None:
            # Same record views the LLM would have seen -> offline and online answers cannot contradict each other.
            return render_offline_answer(retrieval, language)
        return generate_offline_answer(matched_schemes or [], question, farmer_profile, language)

    def generate_answer_with_meta(self, context: str, question: str, farmer_profile: dict = None, language: str = "en",
                                  matched_schemes: list = None, conversation_history: list = None,
                                  retrieval: Optional[RetrievalResult] = None, allow_llm: bool = True,
                                  answer_language: str = "en", original_question: Optional[str] = None) -> AnswerResult:
        """Grounded answer generation: Groq over retrieved records, with an offline fallback.

        retrieval: the structured retrieval result. When given, the LLM context is built from it (intent-aware,
        verification-aware); `context` is then ignored. The Groq answer is always generated in English; the
        caller translates it when the user's language is different. The returned AnswerResult.language says which
        language the text really is in; `language` only selects the labels of the offline renderer.
        """
        grounded: Optional[GroundedContext] = build_grounded_context(retrieval, farmer_profile) if retrieval is not None else None
        ctx_text = grounded.text if grounded else context
        if not ctx_text:
            return AnswerResult(self._offline_answer(question, farmer_profile, language, matched_schemes, retrieval),
                                "offline", fallback_reason="empty_context", language=language)

        reason = None
        if not allow_llm:
            reason = "llm_not_requested"          # e.g. background pre-caching: keep the Groq quota for real questions
        elif settings.offline_only:
            reason = "offline_only"
        elif not groq_client.is_configured():
            reason = "groq_not_configured"
        else:
            model = groq_client.model_name()
            # Not part of the cache key: the answer is determined by the question and the retrieved records, so the same
            # question gets its saved answer whether it is the first message of a chat or a later one.
            history_summary = ""
            gctx = grounded or GroundedContext.from_text(ctx_text)
            if answer_language and answer_language != "en" and (settings.answer_generation_mode or "").lower() == "direct":
                direct = self._direct_answer(model, ctx_text, question, farmer_profile, history_summary, conversation_history,
                                             answer_language, original_question, gctx)
                if isinstance(direct, AnswerResult):
                    return direct
                if direct == "rate_limited":            # a second call now would fail too: go straight to the fallback
                    return AnswerResult(self._offline_answer(question, farmer_profile, language, matched_schemes, retrieval),
                                        "offline", fallback_reason="groq_rate_limited", language=language)
            key = self._groq_cache_key(model, ctx_text, question, farmer_profile, history_summary, "en")
            cached = _cache_get(key)
            if cached:
                logger.info("Returning cached Groq answer")
                return AnswerResult(cached["answer"], "groq", model, cached=True)
            try:
                messages = build_messages(ctx_text, question, conversation_history)
                answer = groq_client.chat(messages)
                problems = check_answer_grounding(answer, gctx)
                if problems:
                    reason = "ungrounded_output"
                    logger.warning("Groq answer rejected by the grounding guard (%d issue(s): %s); using offline answer",
                                   len(problems), ", ".join(problems[:3]))
                else:
                    _cache_put(key, answer, "en")
                    return AnswerResult(answer, "groq", model)
            except groq_client.GroqError as e:
                reason = f"groq_error: {groq_client.redact(e)}"
                logger.warning("Groq unavailable (%s), using offline answer engine", reason)

        # Fallback. Deliberately NOT cached: a recovered Groq must not be hidden behind a cached offline answer.
        return AnswerResult(self._offline_answer(question, farmer_profile, language, matched_schemes, retrieval),
                            "offline", fallback_reason=reason, language=language)

    def _direct_answer(self, model, ctx_text, question, farmer_profile, history_summary, conversation_history,
                       answer_language, original_question, gctx):
        """One Groq call that answers directly in `answer_language`. Returns an AnswerResult when the answer passes the
        grounding guard AND is really in that language; otherwise a short reason string (the caller falls back)."""
        key = self._groq_cache_key(model, ctx_text, question, farmer_profile, history_summary, answer_language)
        cached = _cache_get(key)
        if cached:
            return AnswerResult(cached["answer"], "groq", model, cached=True, language=answer_language)
        try:
            messages = build_messages(ctx_text, question, conversation_history, answer_language, original_question)
            # gpt-oss counts its hidden reasoning against max_tokens: low effort + a generous budget keeps a long
            # Devanagari answer from being cut off (a cut-off answer is rejected and costs a second call).
            opts = {"reasoning_effort": "low"} if model.startswith("openai/gpt-oss") else None
            answer = groq_client.chat(messages, max_tokens=2500, require_complete=True, extra_body=opts)
        except groq_client.GroqError as e:
            msg = groq_client.redact(e)
            logger.warning("Groq direct %s answer failed (%s)", answer_language, msg)
            return "rate_limited" if ("429" in msg or msg.startswith("rate_limited")) else "error"
        problems = check_answer_grounding(answer, gctx)
        terms = ["Saarthi.AI"] + [v.name for v in gctx.views]
        lang_problem = native_answer_problem(answer, answer_language, terms)
        if problems or lang_problem:
            logger.warning("Groq direct %s answer rejected (%s); falling back to English + translation", answer_language,
                           ", ".join(problems[:3]) or lang_problem)
            return "rejected"
        _cache_put(key, answer, answer_language)
        return AnswerResult(answer, "groq", model, language=answer_language)

    def generate_answer(self, context: str, question: str, farmer_profile: dict = None, language: str = "en",
                        matched_schemes: list = None, conversation_history: list = None,
                        retrieval: Optional[RetrievalResult] = None) -> str:
        """Backwards-compatible wrapper returning only the answer text."""
        return self.generate_answer_with_meta(context, question, farmer_profile, language, matched_schemes,
                                              conversation_history, retrieval).answer


rag_engine = RAGEngine()
