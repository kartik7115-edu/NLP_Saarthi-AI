# Saarthi.AI Stage 5: multilingual repair report

Scope: Hindi questions got English answers (A), Marathi questions failed retrieval (B), and text-to-speech did not
reliably match the answer language (C). The architecture is unchanged and so is the Groq configuration
(`GROQ_MODEL=openai/gpt-oss-120b`, read from `.env`). No dependencies were added and no dataset records were added or edited.

## 1. Execution path (as actually run)

| Stage | File(s) actually used |
|---|---|
| Language selector | `backend/frontend_dist/index.html` + `script.js` (`getRequestLang()`), or `frontend/index.html` + `script.js` (`<html lang>`), depending on the start command (see below) |
| Request / schema | `backend/app/api/query.py` → `app/schemas.py` (`QueryRequest.language`) |
| Code normalisation | `language_utils.normalize_language` (`hi-IN`, `hi_IN`, `HI` → `hi`; `mr-IN` → `mr`) |
| Detection (`auto`) | `translator.detect_language` (langdetect) → `query._resolve_auto_language` |
| Question → English | `translator.translate_text` (Azure if `AZURE_TRANSLATOR_KEY`, else the free Google endpoint via deep_translator) |
| Understanding / intent | `query_understanding.understand_query`, `intent_detection` |
| Retrieval / ranking | `rag_engine.retrieve` → `retrieval.retrieve` over `app/data/schemes.json` (72 records; 19 are state-level) |
| Context | `grounded_answer.build_grounded_context` |
| Generation | `rag_engine.generate_answer_with_meta` → `groq_client.chat` (always in English) → grounding guard, with an offline fallback |
| Localization | `answer_localizer` (protected tokens + validation) |
| Response | `QueryResponse` + `X-*` headers |
| Rendering / TTS | `script.js displayResponse` → `voice.js SaarthiTTS.speakResponse` |
| Offline | `sw.js`, `script.js generateOfflineResponse`, `offline_answer_engine` / `render_offline_answer` |

**Which frontend is served?** `backend/app/main.py` picks the first existing directory from `SAARTHI_FRONTEND_DIR`,
`backend/frontend_dist` and `frontend/`:
* `cd backend && python -m uvicorn app.main:app` (the `.replit` deployment and the Dockerfile) serves **`backend/frontend_dist`**.
* `python main.py` from the project root sets `SAARTHI_FRONTEND_DIR` and serves **`frontend/`**.

The two copies have different `index.html`, `style.css` and `script.js` (`script.js` is 1391 lines in one and 766 in the
other). `voice.js` and `sw.js` are identical in both. Both copies were fixed, and serving was confirmed by starting each
entry point and fetching `/voice.js`, `/sw.js` and `/script.js`.

## 2. Root causes (reproduced in this environment)

Reproduction: the real pipeline with the real translator. Its providers were unreachable here, which is the same
situation as the free Google endpoint being blocked or rate-limited. Before the fix:

| Request | Retrieval | Answer | Metadata |
|---|---|---|---|
| EN eligibility question | 5 central farm schemes | English | `en`, `not_needed` |
| HI question, `language=hi` | KCC, PM-KISAN, Maan Dhan, PMFBY **+ Annadata Sukhibhava (Andhra Pradesh)** | **English** | `en`, `failed: all_providers_failed` |
| MR question, `language=mr` | **nothing** ("I couldn't find a specific scheme…") | **English** | `en`, `failed` |
| MR recommendation cards | **Annadata Sukhibhava (AP), CM-KISAN (Odisha)**, KCC | | |

**A. Hindi → English answer.** Retrieval worked. The answer was always generated in English, and the only way to
localize it was the machine-translation provider. No Azure key is configured, so that provider was the free,
unofficial Google endpoint used by deep_translator. When that endpoint fails, the localizer correctly refuses to label
English as Hindi and returns English with `status=failed`. The endpoint can fail through a network error, a rate limit,
a block, or a protection token it mangles (answers carry around 50 `QZnQZ` placeholders). Groq was configured and
working the whole time but was never used for localization. In the `frontend/` copy the English fallback was also
shown with **no notice**. *Not established:* which Google failure mode the original tester hit. All of them lead to
the same outcome.

**B. Marathi retrieval failure.** When the question translation failed, `translate_text` silently returned the
Marathi input, and retrieval ran on Marathi tokens. Matching is on whole tokens: `कृषी` (Marathi spelling) did not
match the Hindi `कृषि` in `FARM_WORDS`, and the agglutinated `अनुदानासाठी` (अनुदान + साठी) matched nothing. With no
signal at all, nothing qualified, which produced "no information" plus an English answer. Two further defects
compounded this:
* **State schemes without a state.** When retrieval was empty, `query.py` fell back to the legacy `recommend_schemes()`
  profile list, which scored state schemes from every state, such as Andhra Pradesh and Odisha. Separately, `retrieval.py`
  placed state schemes in tier "2_entity_match" whenever *no* state was known (`... or not u.state`). That is how the
  Hindi answer listed Annadata Sukhibhava (AP).
* A Marathi question typed with Hindi selected (or the reverse) was translated "from" the selected language,
  because the script is shared and the code only checked the script.

**C. TTS mismatch.** `voice.js` already had strict voice matching (Stage 5), so the main causes were:
1. The displayed answer was English (A), so English speech was the honest result.
2. **Service-worker staleness.** `sw.js` served `/script.js` and `/voice.js` cache-first (stale-while-revalidate),
   so the first load after an update ran the *previous* scripts. An already-fixed speech bug could therefore keep
   appearing until a second reload or a cache-name bump.
3. **Silent autoplay refusal.** The answer is spoken automatically after an async fetch. Chrome-family browsers then
   often reject `speechSynthesis.speak` with `not-allowed` because there is no recent user gesture. The handler only
   logged a warning, so nothing was spoken, nothing was said, and there was no way to replay.
4. An empty voice list after 1.5 s (slow Android voice loading) was reported as "no voice for this language".
5. Unlabelled Devanagari was always treated as Hindi, and offline answers built in the browser (localized labels but
   English dataset text) were labelled with the selected language.

## 3. Changes

| File | Change |
|---|---|
| `backend/app/services/language_utils.py` | `detect_devanagari_language()` tells Hindi from Marathi using language-specific function words and inflections plus seeded langdetect, so the shared script alone is never relied on. `wrong_devanagari_language()`. `source_language_for_query()` uses it for Devanagari questions. |
| `backend/app/services/multilingual_query.py` (new) | Offline Hindi/Marathi → English **retrieval keywords** with prefix matching for inflections (`अनुदानासाठी`→subsidy, `कृषी`→agriculture, `महाराष्ट्रात`→Maharashtra). It expands the query only and adds no facts. |
| `backend/app/services/llm_translator.py` (new) | `translate_with_groq()` uses the configured Groq model as a translation provider. It raises on failure, uses a placeholder-preserving prompt, and sends `reasoning_effort: low` (gpt-oss only) with a large `max_tokens`. |
| `backend/app/services/groq_client.py` | `chat()` accepts an optional `extra_body`. Existing calls are unchanged. |
| `backend/app/services/answer_localizer.py` | Rejects a translation that is in the wrong one of Hindi/Marathi (`result_in_wrong_language`). New `localize_with_fallbacks()` tries providers in order and records `provider` and `attempts`. The fallback policy is unchanged: English, labelled `en`, `status=failed`. |
| `backend/app/api/query.py` | Question translation: machine → Groq → keywords, with the original words kept so Devanagari aliases still resolve. Retrieval is retried with the keywords if a translation retrieved nothing. Answer localization: `machine,groq` chain. Cards are localized in one request, all or nothing. Profile recommendations drop state schemes unless the user's state is known and matches. `auto` mode no longer turns `ne`/unknown into English for Devanagari. New headers: `X-Query-Translation`, `X-Translation-Provider`, `X-Translation-Attempts`, `X-Cards-Translation`. |
| `backend/app/services/retrieval.py` | State schemes are never matches when the state is unknown (`state_unknown`) or belongs to another profile state (`state_mismatch_profile`). Up to 3 relevant ones are kept as `state_examples`. A note labels them "EXAMPLES ONLY … only in <state>" and asks for the state. |
| `backend/app/services/grounded_answer.py` | Grounding rule 14: state schemes appear only as labelled examples, the model asks for the state, and the state is never inferred from the question's language. |
| `backend/app/schemas.py` | Optional, backward-compatible response fields `requested_language` and `translation_status`. `response_language` still means "the language `answer` is in". |
| `backend/app/config.py`, `.env.example` | New optional `TRANSLATION_PROVIDER_ORDER` (default `machine,groq`). The Groq model and config are untouched. |
| `voice.js` (both copies, identical) | Hindi/Marathi guess for unlabelled Devanagari. `voices_not_loaded` is distinct from `no_voice`, with a second wait. `not-allowed` / `language-unavailable` errors are reported, never silent. `onSaarthiSpeechNotice` hook. `scriptShare` export. |
| `script.js` (both copies) | A **Listen** button under every answer replays *that* answer (the tap satisfies the autoplay rule). Speech problems are shown as an inline notice. The "not translated" notice uses the backend `translation_status`; it is new in `frontend/`, where it was missing. Browser-generated offline answers carry an honest `response_language`. New UI strings in all 11 languages. |
| `sw.js` (both copies) | Cache `saarthi-v4`. **Network-first** for `/`, `/index.html`, `/script.js`, `/voice.js`, `/style.css`, `/manifest.json` and `/sw.js`, with the cache used only when offline. |

**Existing tests whose expectation changed** (the behaviour changed on purpose; none were deleted):
* `test_multilingual.py`: a Hindi question with English selected is now translated from `hi` (identified) instead of
  `auto`. Two cases were added: Marathi typed with Hindi selected → `mr`, and a too-short text → `auto`.
* `frontend/voice.test.js`: "no voices at all" now reports `voices_not_loaded`, and the test also asserts the user is told.
* `frontend/script.test.js`, `test_frontend_js.py`: service-worker version v3 → v4, plus network-first assertions. The
  pytest wrapper now also runs the two new Node suites (minimum passes raised from 45 to 62).

## 4. Test results (run in this environment)

| Test | Result | Evidence / notes |
|---|---|---|
| English retrieval | **Pass** | Same top-5 ranking as before; no state schemes; `test_A_*` |
| Hindi retrieval | **Pass** | Intent `eligibility_check`; PM-KISAN/KCC retrieved with a translated question *and* with no translation (keywords); `test_B_*` |
| Hindi answer language | **Pass (mocked providers)** | Google down → Groq fallback: Devanagari share > 0.7, detected `hi`, facts kept, `response_language=hi`, `translation_status=translated`, `X-Translation-Provider: groq` |
| Marathi retrieval | **Pass** | The raw Marathi question returned 0 records before; now PM-KISAN + KCC; no "No relevant scheme" note; `test_C_*` |
| Marathi answer language | **Pass (mocked providers)** | Detected `mr`; a *Hindi* translation for a Marathi request is rejected (`result_in_wrong_language`) |
| Missing state | **Pass** | No state scheme selected or shown as a card for EN/HI/MR; examples labelled with their state; state requested; Marathi ≠ Maharashtra; `test_D_*` |
| Translation failure handling | **Pass** | echo / dropped token / empty / Groq exception / machine down: English labelled `en`, `failed`, `X-Translation-Attempts` lists each provider, log has no key; `test_E_*` |
| Information integrity | **Pass** | Scheme names, `Rs. 6,000`, `2%`, URL, `31.03.2026` and `155261` survive; a translation that alters any of them is rejected; `test_F_*` |
| Text-to-speech logic | **Pass (62/62 Node tests)** | EN/HI/MR voice selection, late voices, Marathi without a Marathi voice, autoplay block + Listen replay, newest answer wins, both frontend copies. Real speech output: **not tested** (needs a browser) |
| Existing regression tests | **Pass** | `pytest`: 475 passed, 15 skipped (live, opt-in). Baseline before changes: 430 passed, 12 skipped. Retrieval eval metrics identical to baseline |
| Groq integration | **Not run live** | The sandbox cannot reach `api.groq.com` (`APIConnectionError`; proxy 403). `GROQ_MODEL=openai/gpt-oss-120b` confirmed loaded. Mocked Groq tests pass. Live suite: `RUN_LIVE_GROQ_TESTS=1 python -m pytest tests/test_multilingual_live.py -v -s` |

The new tests were also run against the **unfixed** baseline: 7 of the 10 new frontend repair tests fail there. The
backend repair tests cannot import there, because the new modules do not exist. The baseline's behavioural failures are
the reproduction table in section 2.

## 5. Manual verification

1. `pip install -r requirements.txt`, and check that `.env` has `GROQ_API_KEY` and `GROQ_MODEL=openai/gpt-oss-120b`.
2. Start the app with **one** of these:
   * `cd backend && python -m uvicorn app.main:app --port 8000` (serves `backend/frontend_dist`, the deployed UI), or
   * `PORT=8000 python main.py` from the project root (serves `frontend/`).
   (`python server.py`, named in `.replit` and the README, does not exist in this project.)
3. Open `http://localhost:8000`. If the app was opened before, reload once; DevTools → Application → Service Workers
   should show `sw.js` with cache `saarthi-v4`.
4. Select **हिंदी** and send: `मैं कैसे पता कर सकता हूँ कि मैं कृषि सब्सिडी के लिए पात्र हूँ या नहीं?`
   * Expect a Devanagari Hindi answer that explains the eligibility criteria and asks for your state, with central
     scheme cards only and **no** amber "could not be translated" notice.
   * DevTools → Network → `query` should show `X-Answer-Source: groq`, `X-Translation-Status: translated`,
     `X-Answer-Language: hi`, and in the JSON `"response_language": "hi"`.
5. Speech: the answer plays automatically if the browser allows it. Otherwise an inline notice appears; press **सुनें**.
   To see which voices you have, run `speechSynthesis.getVoices().filter(v => /^(hi|mr|en)/.test(v.lang)).map(v => v.name + ' ' + v.lang)`
   in the console. Hindi must be spoken by a `hi-*` voice. If none is listed, the app says so and does not read the answer.
6. Select **मराठी** and send: `मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?`
   Expect a Marathi answer (for example, "तुम्ही कोणत्या राज्यात राहता?") and `X-Answer-Language: mr`. Press **ऐका**. Without an
   `mr-*` voice you must see the Marathi "no voice" notice, never an English or Hindi voice reading it.
7. Select **English** and ask `How can I check whether I am eligible for an agricultural subsidy?`. Expect an English
   answer, no notice, and English speech.
8. Optional, the failure path: set `TRANSLATION_PROVIDER_ORDER=machine` and block network to translate.google.com.
   A Hindi request must return English with the amber notice and `X-Translation-Status: failed`.

API-only check:
`curl -s -D - http://localhost:8000/query/ -H 'content-type: application/json' -d '{"query":"मी कृषी अनुदानासाठी पात्र आहे की नाही हे कसे तपासू शकतो?","language":"mr-IN","occupation":"farmer"}'`

## 6. Remaining limitations

* **Live Groq and live Google translation were not exercised** (blocked in this sandbox). The quality of the Groq
  Hindi/Marathi translation and whether gpt-oss-120b preserves every placeholder must be confirmed with the live suite.
  The validation will reject bad output, but rejected output means an English answer.
* Extra Groq calls per non-English request when Google fails: question, answer and cards, i.e. up to 3 more. On a
  rate-limited Groq tier this can push requests onto the English fallback. `TRANSLATION_PROVIDER_ORDER=groq,machine`
  makes Groq primary if Google is unreliable for you.
* The keyword safety net covers common scheme vocabulary only. With every translator down, unusual wording can still miss.
* Hindi/Marathi detection is heuristic. Very short texts are undecidable and are then accepted, not rejected.
* When Groq *and* translation are down, the offline answer body is English (dataset text), and it is labelled and announced as English.
* Voice availability depends on the device. Many desktop Chrome/Windows setups have Hindi but **no Marathi voice**;
  Edge's online voices and Android Google TTS (with Marathi data) usually do. Check with step 5; do not assume.
* The first automatic playback can still be blocked by the browser, so **Listen** is the reliable path.
* The two frontends (`frontend/`, `backend/frontend_dist/`) remain two different UIs. The `.replit` build step
  `cp -r frontend backend/frontend_dist` copies *into* the existing directory (`backend/frontend_dist/frontend/`), so
  it does not replace the served copy. These were left unchanged because changing them would swap the visible UI.
* The uploaded archive contains a real `GROQ_API_KEY` in `.env`. It was not printed or copied into any output. Rotate it
  if the archive has been shared.
