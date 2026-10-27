// Stage 5 repair - TTS and answer-language display, against the REAL served frontend (backend/frontend_dist).
// Run: node --test tests/frontend        (Node >= 18, no npm dependencies)
// What these tests can and cannot prove: they drive the real voice.js / script.js with a stubbed speechSynthesis, so they
// verify the language mapping, voice choice, waiting for voices, notices and replay logic. Whether a given browser HAS a
// Hindi or Marathi voice, and how it sounds, can only be checked manually in that browser (see the report).
const test = require("node:test");
const assert = require("node:assert/strict");
const vm = require("vm");
const { createEnv, load, VOICES } = require("./harness");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HI = "अपनी पात्रता जाँचने के लिए, अपनी जानकारी को नीचे दी गई हर योजना की शर्तों से मिलाएँ। PM-KISAN: आयकर देने वाले पात्र नहीं हैं।";
const MR = "तुमची पात्रता तपासण्यासाठी, तुमची माहिती खालील प्रत्येक योजनेच्या अटींशी जुळवून पाहा. PM-KISAN: आयकर भरणारे पात्र नाहीत.";
const EN = "To check your eligibility, compare your details with the criteria of each scheme below.";

function boot(opts = {}) {
  const env = createEnv(opts);
  load(env, "voice.js");
  load(env, "script.js");
  env.win.SaarthiTTS.config.voiceWaitMs = 60;
  return env;
}
const I18N = (env, lang, key) => vm.runInContext(`I18N.${lang}.${key}`, env.ctx);
const chat = (env) => env.get("chat-box").innerHTML;
const last = (env) => env.spoken[env.spoken.length - 1];

test("English, Hindi and Marathi answers each get their own language code and voice", async () => {
  const voices = [VOICES.zira, VOICES.neerja, VOICES.swara, VOICES.aarohi];
  for (const [text, lang, bcp, voice] of [[EN, "en", "en-IN", VOICES.neerja], [HI, "hi", "hi-IN", VOICES.swara], [MR, "mr", "mr-IN", VOICES.aarohi]]) {
    const env = boot({ selectedLang: lang, voices });
    const res = await env.win.SaarthiTTS.speakResponse({ answer: text, response_language: lang }, lang);
    assert.equal(res.spoken, true, lang);
    assert.ok(env.spoken.every((u) => u.lang === bcp && u.voice && u.voice.name === voice.name), lang);
  }
});

test("Marathi text WITHOUT a language label is recognised as Marathi (not Hindi by script)", async () => {
  const env = boot({ selectedLang: "auto", voices: [VOICES.swara, VOICES.aarohi] });
  assert.equal(env.win.SaarthiTTS.guessDevanagariLanguage(MR), "mr");
  assert.equal(env.win.SaarthiTTS.guessDevanagariLanguage(HI), "hi");
  await env.win.SaarthiTTS.speakResponse({ answer: MR }, "auto");
  assert.equal(last(env).lang, "mr-IN");
  assert.equal(last(env).voice.name, VOICES.aarohi.name);
});

test("Marathi with only a Hindi voice installed: not read, user told in Marathi (no silent substitute voice)", async () => {
  const env = boot({ selectedLang: "mr", voices: [VOICES.neerja, VOICES.swara] });
  const res = await env.win.SaarthiTTS.speakResponse({ answer: MR, response_language: "mr" }, "mr");
  assert.equal(res.reason, "no_voice");
  assert.equal(env.spoken.length, 0);
  assert.ok(env.statusMessages.includes(I18N(env, "mr", "noVoice")));
  assert.ok(chat(env).includes(I18N(env, "mr", "noVoice")), "a notice is added next to the answer, not only in the status line");
});

test("voices that load only after the first wait are still used (second wait), not reported as missing", async () => {
  const env = boot({ selectedLang: "hi", voices: [] });
  const pending = env.win.SaarthiTTS.speakResponse({ answer: HI, response_language: "hi" }, "hi");
  await sleep(90);                                    // first wait (60 ms) has expired
  env.setVoices([VOICES.neerja, VOICES.swara]);
  const res = await pending;
  assert.equal(res.spoken, true);
  assert.equal(last(env).voice.name, VOICES.swara.name);
});

test("browser autoplay refusal ('not-allowed') is reported, and the Listen button replays that answer", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  let blocked = true;
  env.synthesis.speak = (u) => {
    env.spoken.push(u);
    setTimeout(() => (blocked ? u.onerror && u.onerror({ error: "not-allowed" }) : (u.onstart && u.onstart(), u.onend && u.onend())), 0);
  };
  env.ctx.displayResponse({ answer: HI, response_language: "hi", recommended_schemes: [] }, false);
  await sleep(30);
  assert.ok(env.statusMessages.includes(I18N(env, "hi", "ttsBlocked")), "user is told speech was blocked");
  assert.ok(chat(env).includes(I18N(env, "hi", "ttsBlocked")));
  const id = (chat(env).match(/data-answer-id="(ans\d+)"/) || [])[1];
  assert.ok(id, "every answer has a Listen button");
  assert.ok(chat(env).includes(I18N(env, "hi", "listen")), "button label is in the selected language");
  blocked = false;                                    // a tap is a user gesture: the browser now allows speech
  const before = env.spoken.length;
  const res = await env.win.speakAnswerById(id);
  assert.equal(res.spoken, true);
  assert.ok(env.spoken.length > before && env.spoken.slice(before).every((u) => u.lang === "hi-IN" && u.voice.name === VOICES.swara.name));
  assert.ok(env.spoken.slice(before).some((u) => u.text.includes("पात्रता")));
});

test("the Listen button of an OLDER answer speaks that answer, and a new answer replaces the speech", async () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.neerja, VOICES.swara] });
  env.ctx.displayResponse({ answer: HI, response_language: "hi", recommended_schemes: [] }, false);
  await sleep(10);
  env.ctx.displayResponse({ answer: "दूसरा उत्तर: किसान क्रेडिट कार्ड के लिए बैंक में आवेदन करें।", response_language: "hi", recommended_schemes: [] }, false);
  await sleep(10);
  assert.ok(last(env).text.includes("दूसरा उत्तर"), "the newest answer is the one being spoken");
  const ids = [...chat(env).matchAll(/data-answer-id="(ans\d+)"/g)].map((m) => m[1]);
  assert.equal(ids.length, 2);
  await env.win.speakAnswerById(ids[0]);
  assert.ok(last(env).text.includes("पात्रता") || env.spoken.slice(-3).some((u) => u.text.includes("पात्रता")));
});

test("a failed translation (backend translation_status) is announced even though the text is English", () => {
  const env = boot({ selectedLang: "mr", voices: [VOICES.neerja] });
  env.ctx.displayResponse({ answer: EN, response_language: "en", requested_language: "mr", translation_status: "failed", recommended_schemes: [] }, false);
  assert.ok(chat(env).includes(I18N(env, "mr", "notTranslated")));
});

test("a successful Hindi answer shows no 'not translated' notice", () => {
  const env = boot({ selectedLang: "hi", voices: [VOICES.swara] });
  env.ctx.displayResponse({ answer: HI, response_language: "hi", translation_status: "translated", recommended_schemes: [] }, false);
  assert.ok(!chat(env).includes(I18N(env, "hi", "notTranslated")));
});

test("an answer built in the browser while offline is labelled with the language it is mostly written in", () => {
  const env = boot({ selectedLang: "hi" });
  const data = env.ctx.generateOfflineResponse("pm kisan", [{ name: "PM-KISAN", type: "central", description: "Income support of Rs. 6,000 per year to landholding farmer families, paid in three instalments.", benefits: "Rs. 6,000 per year in three equal instalments", eligibility: "All landholding farmer families with cultivable land" }]);
  assert.equal(data.response_language, "en", "mostly-English text with Hindi labels is not labelled Hindi");
  const none = env.ctx.generateOfflineResponse("xyz", []);
  assert.equal(none.response_language, "hi", "the no-match text is written in Hindi, so it is labelled Hindi");
});

test("every UI language has the new speech strings, not English copies", () => {
  const env = boot();
  for (const lang of ["hi", "bn", "te", "mr", "ta", "gu", "kn", "ml", "pa", "or"]) {
    for (const key of ["listen", "ttsBlocked", "voicesLoading", "ttsFailed"]) {
      const v = I18N(env, lang, key);
      assert.ok(v && v !== I18N(env, "en", key), `${lang}.${key}`);
    }
  }
});
