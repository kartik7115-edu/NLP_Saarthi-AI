// Frontend TTS tests. Run:  node --test tests/frontend      (Node >= 18, no npm dependencies)
// They load the REAL served voice.js (and script.js) into a vm context with stubbed browser APIs.
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("vm");
const { createEnv, load, VOICES } = require("./harness");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const EN = "The current Saarthi.AI dataset lists Aadhaar and land records for PM-KISAN. You get Rs. 6,000 per year.";
// One native-script sample per language (content is irrelevant; the script is what the frontend checks)
const SAMPLE = {
  hi: "वर्तमान डेटासेट में PM-KISAN के लिए आधार और भूमि अभिलेख सूचीबद्ध हैं। आपको प्रति वर्ष 6,000 रुपये मिलते हैं।",
  mr: "सध्याच्या डेटासेटमध्ये PM-KISAN साठी आधार आणि जमिनीची कागदपत्रे नमूद आहेत. तुम्हाला दरवर्षी 6,000 रुपये मिळतात.",
  bn: "বর্তমান ডেটাসেটে PM-KISAN এর জন্য আধার এবং জমির নথি তালিকাভুক্ত আছে। আপনি বছরে 6,000 টাকা পান।",
  te: "ప్రస్తుత డేటాసెట్‌లో PM-KISAN కోసం ఆధార్ మరియు భూమి రికార్డులు ఉన్నాయి. మీరు సంవత్సరానికి 6,000 రూపాయలు పొందుతారు.",
  ta: "தற்போதைய தரவுத்தொகுப்பில் PM-KISAN க்கான ஆதார் மற்றும் நில ஆவணங்கள் உள்ளன. நீங்கள் ஆண்டுக்கு 6,000 ரூபாய் பெறுவீர்கள்.",
  gu: "હાલના ડેટાસેટમાં PM-KISAN માટે આધાર અને જમીનના રેકોર્ડ નોંધાયેલા છે. તમને દર વર્ષે 6,000 રૂપિયા મળે છે.",
  kn: "ಪ್ರಸ್ತುತ ಡೇಟಾಸೆಟ್‌ನಲ್ಲಿ PM-KISAN ಗಾಗಿ ಆಧಾರ್ ಮತ್ತು ಭೂ ದಾಖಲೆಗಳು ಪಟ್ಟಿಮಾಡಲಾಗಿದೆ. ನೀವು ವರ್ಷಕ್ಕೆ 6,000 ರೂಪಾಯಿ ಪಡೆಯುತ್ತೀರಿ.",
  ml: "നിലവിലെ ഡാറ്റാസെറ്റിൽ PM-KISAN നായുള്ള ആധാറും ഭൂരേഖകളും പട്ടികപ്പെടുത്തിയിട്ടുണ്ട്. നിങ്ങൾക്ക് പ്രതിവർഷം 6,000 രൂപ ലഭിക്കും.",
  pa: "ਮੌਜੂਦਾ ਡੇਟਾਸੈੱਟ ਵਿੱਚ PM-KISAN ਲਈ ਆਧਾਰ ਅਤੇ ਜ਼ਮੀਨ ਦੇ ਰਿਕਾਰਡ ਦਰਜ ਹਨ। ਤੁਹਾਨੂੰ ਹਰ ਸਾਲ 6,000 ਰੁਪਏ ਮਿਲਦੇ ਹਨ।",
  or: "ବର୍ତ୍ତମାନର ଡାଟାସେଟରେ PM-KISAN ପାଇଁ ଆଧାର ଏବଂ ଜମି ରେକର୍ଡ ତାଲିକାଭୁକ୍ତ ଅଛି। ଆପଣ ବର୍ଷକୁ 6,000 ଟଙ୍କା ପାଆନ୍ତି।",
};
const BCP = { en: "en-IN", hi: "hi-IN", bn: "bn-IN", te: "te-IN", mr: "mr-IN", ta: "ta-IN", gu: "gu-IN", kn: "kn-IN", ml: "ml-IN", pa: "pa-IN", or: "or-IN" };
const ALL_VOICES = Object.keys(BCP).map((l) => ({ name: `Test ${l} voice`, lang: BCP[l], localService: true }));

function boot(opts = {}) {
  const env = createEnv(opts);
  load(env, "voice.js");
  load(env, "script.js");
  env.win.SaarthiTTS.config.voiceWaitMs = 150;      // keep the "no voices ever arrive" tests fast
  return env;
}
const last = (env) => env.spoken[env.spoken.length - 1];

test("only the app's 11 answer languages are speech languages; auto/unknown/missing are never mapped to Hindi", () => {
  const { resolveSpeechLang } = boot().win.SaarthiTTS;
  for (const [short, bcp] of Object.entries(BCP)) assert.deepEqual({ ...resolveSpeechLang(short) }, { short, bcp });
  assert.equal(resolveSpeechLang("hi_IN").short, "hi");
  assert.equal(resolveSpeechLang("HI-in").short, "hi");
  for (const bad of ["auto", "", null, undefined, "xx", "fr", "ur", "as"]) assert.equal(resolveSpeechLang(bad), null, String(bad));
});

for (const lang of Object.keys(SAMPLE)) {
  test(`${lang}: the displayed answer is spoken with utterance.lang=${BCP[lang]} and a ${lang} voice`, async () => {
    const env = boot({ selectedLang: lang, voices: [VOICES.zira, ...ALL_VOICES.slice().reverse()] });
    const result = await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE[lang], response_language: lang }, lang);
    assert.equal(result.spoken, true);
    assert.equal(last(env).lang, BCP[lang]);
    assert.ok(last(env).voice && last(env).voice.lang.toLowerCase().startsWith(lang), "voice must speak the language");
    assert.ok(env.spoken.every((u) => u.lang === BCP[lang] && u.voice === last(env).voice), "every chunk uses the same language and voice");
    assert.ok(last(env).text.includes("PM-KISAN") || env.spoken.some((u) => u.text.includes("PM-KISAN")), "speaks the answer text");
  });
}

test("English answer uses the English (India) voice; Hindi uses Swara; Marathi uses Aarohi", async () => {
  const voices = [VOICES.zira, VOICES.neerja, VOICES.swara, VOICES.aarohi];
  let env = boot({ voices }); await env.win.SaarthiTTS.speakResponse({ answer: EN, response_language: "en" }, "en");
  assert.equal(last(env).voice.name, VOICES.neerja.name);
  env = boot({ voices }); await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi, response_language: "hi" }, "hi");
  assert.equal(last(env).voice.name, VOICES.swara.name);
  env = boot({ voices }); await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  assert.equal(last(env).voice.name, VOICES.aarohi.name);
});

test("the romanized transliteration is never what gets spoken", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  env.ctx.displayResponse({ answer: SAMPLE.hi, response_language: "hi", answer_romanized: "Vartaman dataset mein PM-KISAN ke liye aadhaar", recommended_schemes: [] }, false);
  await sleep(30);
  assert.equal(last(env).lang, "hi-IN");
  assert.equal(last(env).voice.name, VOICES.swara.name);
  assert.ok(env.spoken.every((u) => !u.text.includes("Vartaman")));
  assert.ok(/[\u0900-\u097F]/.test(env.spoken[0].text));
});

test("voices that arrive late (getVoices() empty at first, then voiceschanged) are used", async () => {
  const env = boot({ selectedLang: "mr", voices: [] });
  const pending = env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  await sleep(40);
  assert.equal(env.spoken.length, 0, "does not speak before a voice exists");
  env.setVoices([VOICES.neerja, VOICES.aarohi]);
  const result = await pending;
  assert.equal(result.spoken, true);
  assert.equal(last(env).voice.name, VOICES.aarohi.name);
  assert.equal(last(env).lang, "mr-IN");
});

test("voices are also found by polling when the browser never fires voiceschanged", async () => {
  const env = boot({ selectedLang: "hi", voices: [] });
  const pending = env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi, response_language: "hi" }, "hi");
  await sleep(30);
  env.synthesis.getVoices = () => [VOICES.swara];            // list appears, no event
  assert.equal((await pending).spoken, true);
  assert.equal(last(env).voice.name, VOICES.swara.name);
});

test("missing voice: nothing is spoken, the user is told, and English speech is NOT substituted", async () => {
  const env = boot({ selectedLang: "mr", voices: [VOICES.neerja, VOICES.zira] });
  const result = await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  assert.deepEqual({ spoken: result.spoken, reason: result.reason, lang: result.lang }, { spoken: false, reason: "no_voice", lang: "mr" });
  assert.equal(env.spoken.length, 0);
  assert.equal(env.statusMessages.length, 1);
  assert.equal(env.statusMessages[0], vm.runInContext("I18N.mr.noVoice", env.ctx), "the notice is shown in the selected language (Marathi)");
  assert.equal(env.get("status-indicator").innerText, env.statusMessages[0], "the user actually sees the message");
});

test("no voices at all and a non-English answer: explicit skip after the wait, never a silent default-voice read", async () => {
  const env = boot({ selectedLang: "ta", voices: [] });
  const result = await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.ta, response_language: "ta" }, "ta");
  // An empty voice list (after waiting twice) only proves the voices did not load, not that no Tamil voice exists:
  // the user is told the voices have not loaded and can retry with "Listen" - nothing is read by a default voice.
  assert.equal(result.reason, "voices_not_loaded");
  assert.equal(env.spoken.length, 0);
  assert.ok(env.statusMessages.some((m) => m && /voices|Listen/i.test(m) || m === vm.runInContext("I18N.ta.voicesLoading", env.ctx)));
});

test("English with no installed English voice may use the browser default (it is English)", async () => {
  const env = boot({ selectedLang: "en", voices: [VOICES.swara] });
  const result = await env.win.SaarthiTTS.speakResponse({ answer: EN, response_language: "en" }, "en");
  assert.equal(result.spoken, true);
  assert.equal(last(env).lang, "en-IN");
  assert.equal(last(env).voice, null);
});

test("Hindi and Marathi share a script but not a voice: a Hindi voice does not read Marathi", async () => {
  const env = boot({ voices: [VOICES.swara] });
  const result = await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  assert.equal(result.reason, "no_voice");
  assert.equal(env.spoken.length, 0);
});

test("English text labelled with another language (failed translation) is spoken as English, not by a Hindi voice", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  const result = await env.win.SaarthiTTS.speakResponse({ answer: EN, response_language: "hi" }, "hi");
  assert.equal(result.spoken, true);
  assert.equal(result.mismatch, true);
  assert.equal(result.lang, "en");
  assert.equal(last(env).lang, "en-IN");
  assert.equal(last(env).voice.name, VOICES.neerja.name);
});

test("an unresolved selector value ('auto') is inferred from the text's script, never forced to Hindi", async () => {
  let env = boot({ selectedLang: "auto", voices: [VOICES.neerja, VOICES.swara] });
  await env.win.speakText("Hello, this is an English sentence for testing the speaker.", "auto");
  assert.equal(last(env).lang, "en-IN");
  env = boot({ selectedLang: "auto", voices: [VOICES.neerja, VOICES.swara] });
  await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi }, "auto");           // no response_language at all
  assert.equal(last(env).lang, "hi-IN");
  const plan = env.win.SaarthiTTS.planSpeech("", "hi", [VOICES.swara]);
  assert.equal(plan.action, "skip");
});

test("voice selection is refreshed when the language changes, and the old speech is stopped", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara, VOICES.aarohi] });
  let cancels = 0;
  env.synthesis.cancel = () => { cancels++; };
  await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi, response_language: "hi" }, "hi");
  assert.equal(last(env).voice.name, VOICES.swara.name);
  const before = cancels;
  env.get("language-select").value = "mr";
  env.get("language-select")._fire("change");
  assert.ok(cancels > before, "changing the language cancels the speech in progress");
  await env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  assert.equal(last(env).voice.name, VOICES.aarohi.name);
  assert.equal(last(env).lang, "mr-IN");
});

test("changing the language while voices are still loading cancels the pending speech", async () => {
  const env = boot({ selectedLang: "hi", voices: [] });
  const pending = env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi, response_language: "hi" }, "hi");
  await sleep(20);
  env.get("language-select")._fire("change");
  env.setVoices([VOICES.swara]);
  assert.equal((await pending).reason, "superseded");
  assert.equal(env.spoken.length, 0);
});

test("a newer answer supersedes an older one that is still waiting for voices", async () => {
  const env = boot({ voices: [] });
  const first = env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.hi, response_language: "hi" }, "hi");
  await sleep(10);
  const second = env.win.SaarthiTTS.speakResponse({ answer: SAMPLE.mr, response_language: "mr" }, "mr");
  env.setVoices([VOICES.swara, VOICES.aarohi]);
  assert.equal((await first).reason, "superseded");
  assert.equal((await second).spoken, true);
  assert.equal(env.spoken.length > 0 && env.spoken.every((u) => u.lang === "mr-IN"), true);
});

test("effectiveAnswerLanguage: label vs. script", () => {
  const { effectiveAnswerLanguage: eff } = boot().win.SaarthiTTS;
  assert.deepEqual({ ...eff(SAMPLE.hi, "hi") }, { lang: "hi", mismatch: false, claimed: "hi" });
  assert.equal(eff(SAMPLE.mr, "mr").lang, "mr");
  assert.equal(eff(EN, "en").mismatch, false);
  assert.deepEqual({ lang: eff(EN, "hi").lang, mismatch: eff(EN, "hi").mismatch }, { lang: "en", mismatch: true });
  assert.equal(eff(SAMPLE.ta, "hi").lang, "ta");                              // mislabelled script is inferred
  assert.equal(eff("**आवश्यक दस्तावेज़:** Aadhaar [mandatory] Bank account details land records", "hi").mismatch, true);   // headings-only "Hindi"
  assert.equal(eff("PM-KISAN के लिए आधार और भूमि अभिलेख आवश्यक हैं।", "hi").mismatch, false);                       // Latin names inside real Hindi
});

test("speech text cleaning keeps hyphenated names, phone numbers and dates, and drops bullets/markdown", () => {
  const { cleanTextForSpeech: clean } = boot().win.SaarthiTTS;
  const out = clean("**Helpline:** call 1800-115-526 for PM-KISAN. Date 31-03-2026.\n- item one\n• item two\n  - item three");
  assert.ok(out.includes("1800-115-526") && out.includes("PM-KISAN") && out.includes("31-03-2026"), out);
  assert.ok(!out.includes("**") && !/(^|\. )[-•]/.test(out));
  assert.ok(out.includes("item one") && out.includes("item two") && out.includes("item three"));
});

test("a long answer is split into chunks that all keep the language and voice", async () => {
  const env = boot({ voices: [VOICES.swara] });
  const long = Array(40).fill(SAMPLE.hi).join(" ");
  await env.win.SaarthiTTS.speakResponse({ answer: long, response_language: "hi" }, "hi");
  await sleep(60);
  assert.ok(env.spoken.length > 3);
  assert.ok(env.spoken.every((u) => u.lang === "hi-IN" && u.voice.name === VOICES.swara.name));
});

test("browsers without speech synthesis are handled", async () => {
  const env = boot({ withSpeech: false });
  assert.deepEqual({ ...(await env.win.SaarthiTTS.speakResponse({ answer: EN, response_language: "en" }, "en")) }, { spoken: false, reason: "speech_unsupported" });
});
