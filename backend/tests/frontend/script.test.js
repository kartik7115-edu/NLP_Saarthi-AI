// Frontend wiring tests against the REAL served script.js (request language, displayed field, TTS input, cache).
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { createEnv, load, DIST, VOICES } = require("./harness");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const EN = "The current Saarthi.AI dataset lists Aadhaar and land records for PM-KISAN. You get Rs. 6,000 per year.";
const HI = "वर्तमान Saarthi.AI डेटासेट में PM-KISAN के लिए आधार और भूमि अभिलेख सूचीबद्ध हैं। आपको प्रति वर्ष 6,000 रुपये मिलते हैं।";
const TA = "தற்போதைய தரவுத்தொகுப்பில் PM-KISAN க்கான ஆதார் மற்றும் நில ஆவணங்கள் உள்ளன. நீங்கள் ஆண்டுக்கு 6,000 ரூபாய் பெறுவீர்கள்.";
const LANGS = ["en", "hi", "bn", "te", "mr", "ta", "gu", "kn", "ml", "pa", "or"];

function boot(opts = {}) {
  const env = createEnv(opts);
  load(env, "voice.js");
  load(env, "script.js");
  env.win.SaarthiTTS.config.voiceWaitMs = 150;
  return env;
}
const ok = (data) => async () => ({ ok: true, json: async () => data });
const chatHtml = (env) => env.get("chat-box").innerHTML;

test("every language selection reaches the backend unchanged; 'auto' stays 'auto' for server-side detection", async () => {
  for (const lang of [...LANGS, "auto"]) {
    const env = boot({ selectedLang: lang, fetchImpl: ok({ answer: lang === "hi" ? HI : EN, response_language: lang === "auto" ? "en" : lang, recommended_schemes: [] }) });
    await env.ctx.processQuery("What documents are required for PM-KISAN?");
    const call = env.fetchCalls.find((c) => String(c.url).endsWith("/query"));
    assert.ok(call, `no /query call for ${lang}`);
    assert.equal(JSON.parse(call.opts.body).language, lang, `language sent for selection "${lang}"`);
  }
});

test("changing the selector changes the language of the NEXT request", async () => {
  const env = boot({ selectedLang: "hi", fetchImpl: ok({ answer: HI, response_language: "hi", recommended_schemes: [] }) });
  await env.ctx.processQuery("first question about schemes");
  env.get("language-select").value = "ta";
  await env.ctx.processQuery("second question about schemes");
  const langs = env.fetchCalls.filter((c) => String(c.url).endsWith("/query")).map((c) => JSON.parse(c.opts.body).language);
  assert.deepEqual(langs, ["hi", "ta"]);
});

test("the displayed text is the `answer` field, not the romanized one", () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.swara] });
  env.ctx.displayResponse({ answer: HI, response_language: "hi", answer_romanized: "Vartaman dataset mein PM-KISAN", recommended_schemes: [] }, false);
  assert.ok(chatHtml(env).includes("वर्तमान"), "answer text is rendered");
  assert.ok(!chatHtml(env).includes("Vartaman"), "romanized text is not rendered");
});

test("TTS receives the localized answer, with the matching language code and voice", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  env.ctx.displayResponse({ answer: HI, response_language: "hi", answer_romanized: "Vartaman dataset", recommended_schemes: [] }, false);
  await sleep(40);
  assert.ok(env.spoken.length >= 1);
  assert.ok(env.spoken[0].text.includes("आधार") || env.spoken[0].text.includes("PM-KISAN"));
  assert.equal(env.spoken[0].lang, "hi-IN");
  assert.equal(env.spoken[0].voice.name, VOICES.swara.name);
});

test("a cached/offline response is spoken in its own language, not the selector's", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.swara, VOICES.tamil] });
  env.ctx.displayResponse({ answer: TA, response_language: "ta", recommended_schemes: [] }, true);
  await sleep(40);
  assert.equal(env.spoken[0].lang, "ta-IN");
  assert.equal(env.spoken[0].voice.name, VOICES.tamil.name);
});

test("English answer under a non-English selection: visible notice in the user's language, spoken as English", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  env.ctx.displayResponse({ answer: EN, response_language: "en", recommended_schemes: [] }, false);
  await sleep(40);
  assert.ok(chatHtml(env).includes("अंग्रेज़ी में दिखाया गया है"), "the notice is shown in Hindi");
  assert.equal(env.spoken[0].lang, "en-IN");
  assert.equal(env.spoken[0].voice.name, VOICES.neerja.name);
});

test("no notice when the answer is in the selected language", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.swara] });
  env.ctx.displayResponse({ answer: HI, response_language: "hi", recommended_schemes: [] }, false);
  await sleep(20);
  assert.ok(!chatHtml(env).includes("अंग्रेज़ी में दिखाया गया है"));
  const auto = boot({ selectedLang: "auto", voices: [VOICES.neerja] });
  auto.ctx.displayResponse({ answer: EN, response_language: "en", recommended_schemes: [] }, false);
  await sleep(20);
  assert.ok(!chatHtml(auto).includes("could not be translated"));
});

test("missing voice is reported to the user and nothing is spoken", async () => {
  const env = boot({ selectedLang: "ta", voices: [VOICES.neerja] });
  env.ctx.displayResponse({ answer: TA, response_language: "ta", recommended_schemes: [] }, false);
  await sleep(400);
  assert.equal(env.spoken.length, 0);
  assert.equal(env.statusMessages.filter((m) => m && m.length > 10).length, 1);
});

// ------------------------------------------------------------------------------ cache isolation
const QUESTION = "what documents are required for pm kisan";

test("cache: an answer cached for Hindi is never returned for Tamil (exact key or fuzzy match)", () => {
  const env = boot({ selectedLang: "ta" });
  env.ctx.cacheResponse("saarthi_hi", { answer: HI, response_language: "hi" }, QUESTION, "hi");
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "ta"), null);
  assert.equal(env.ctx.getCachedResponse("saarthi_hi", "ta"), null);
  assert.ok(env.ctx.findBestCachedAnswer(QUESTION, "hi"), "still found for the language it belongs to");
  assert.equal(env.ctx.getCachedResponse("saarthi_hi", "hi").response_language, "hi");
});

test("cache: each language keeps its own answer for the same question", () => {
  const env = boot();
  env.ctx.cacheResponse("k_hi", { answer: HI, response_language: "hi" }, QUESTION, "hi");
  env.ctx.cacheResponse("k_ta", { answer: TA, response_language: "ta" }, QUESTION, "ta");
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "hi").answer, HI);
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "ta").answer, TA);
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "mr"), null);
});

test("cache: an entry is matched on the language it was cached FOR, not only on what its text looks like", () => {
  // Hindi and Marathi share a script, so the text check alone cannot tell them apart: the stored language must decide.
  const env = boot();
  // planted directly in storage (cacheResponse itself would refuse this combination)
  env.localStorage.setItem("k_mr", JSON.stringify({ data: { answer: HI, response_language: "hi" }, timestamp: Date.now(), query: QUESTION, language: "mr" }));
  env.ctx.saveCacheMeta([{ key: "k_mr", timestamp: Date.now(), lastAccessed: Date.now() }]);
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "hi"), null, "cached for mr, so not served for hi");
  assert.equal(env.ctx.getCachedResponse("k_mr", "hi"), null);
});

test("cache: English text labelled with another language is neither stored nor served", () => {
  const env = boot();
  env.ctx.cacheResponse("k_bad", { answer: EN, response_language: "hi" }, QUESTION, "hi");       // translation had failed
  assert.equal(env.localStorage.getItem("k_bad"), null, "not stored");
  env.ctx.cacheResponse("k_bad2", { answer: EN, response_language: "en" }, QUESTION, "hi");       // honest label, still not Hindi
  assert.equal(env.localStorage.getItem("k_bad2"), null, "an English answer is not a Hindi cache entry");
  // an entry written by an older build (before this check) must not be served either
  env.localStorage.setItem("k_old", JSON.stringify({ data: { answer: EN, response_language: "hi" }, timestamp: Date.now(), query: QUESTION, language: "hi" }));
  env.ctx.saveCacheMeta([{ key: "k_old", timestamp: Date.now(), lastAccessed: Date.now() }]);
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "hi"), null);
  assert.equal(env.ctx.getCachedResponse("k_old", "hi"), null);
});

test("cache: English answers are cached for English", () => {
  const env = boot();
  env.ctx.cacheResponse("k_en", { answer: EN, response_language: "en" }, QUESTION, "en");
  assert.equal(env.ctx.findBestCachedAnswer(QUESTION, "en").answer, EN);
});

test("offline: the exact-key cache is language-checked through processQuery", async () => {
  const env = boot({ selectedLang: "ta" });
  env.sandbox.navigator.onLine = false;
  env.ctx.cacheResponse("saarthi_x", { answer: HI, response_language: "hi" }, QUESTION, "hi");
  await env.ctx.processQuery(QUESTION);
  assert.ok(!chatHtml(env).includes("वर्तमान"), "the Hindi cached answer must not be shown to a Tamil user");
});

// ------------------------------------------------------------------------------ i18n, files served, sync
test("every language has the two new notice strings, translated (not English)", () => {
  const env = boot();
  const I18N = vm.runInContext("I18N", env.ctx);
  assert.deepEqual(Object.keys(I18N).sort(), [...LANGS].sort());
  for (const lang of LANGS) {
    assert.ok(I18N[lang].noVoice && I18N[lang].notTranslated, `${lang} missing strings`);
    if (lang !== "en") {
      assert.notEqual(I18N[lang].noVoice, I18N.en.noVoice, `${lang} noVoice is still English`);
      assert.notEqual(I18N[lang].notTranslated, I18N.en.notTranslated, `${lang} notTranslated is still English`);
    }
  }
});

test("script.js no longer prefers answer_romanized for speech and uses speakResponse", () => {
  const src = fs.readFileSync(path.join(DIST, "script.js"), "utf8");
  assert.ok(!/answer_romanized\s*\|\|/.test(src) && !/data\.answer_romanized\s*\?\s*"en"/.test(src));
  assert.ok(src.includes("SaarthiTTS.speakResponse(data, getLang())"));
  assert.ok(src.includes("function getRequestLang()") && src.includes("language: getRequestLang()"));
});

test("service worker cache version was bumped and still precaches script.js and voice.js", () => {
  const sw = fs.readFileSync(path.join(DIST, "sw.js"), "utf8");
  assert.match(sw, /CACHE_NAME = 'saarthi-v3'/);
  assert.ok(sw.includes("'/script.js'") && sw.includes("'/voice.js'"));
});

test("index.html loads voice.js and script.js", () => {
  const html = fs.readFileSync(path.join(DIST, "index.html"), "utf8");
  assert.ok(/src="\/?voice\.js/.test(html) && /src="\/?script\.js/.test(html));
});

test("frontend/ (stale copy) is kept in sync where it overlaps: voice.js and sw.js identical", () => {
  const stale = path.join(DIST, "..", "..", "frontend");
  if (!fs.existsSync(stale)) return;
  for (const f of ["voice.js", "sw.js"]) {
    assert.equal(fs.readFileSync(path.join(stale, f), "utf8"), fs.readFileSync(path.join(DIST, f), "utf8"), f);
  }
  const staleScript = fs.readFileSync(path.join(stale, "script.js"), "utf8");
  assert.ok(staleScript.includes("SaarthiTTS.speakResponse") && !/data\.answer_romanized\s*\|\|/.test(staleScript));
});
