// The SECOND frontend copy: <project>/frontend, served when the app is started with `python main.py` from the project
// root (main.py sets SAARTHI_FRONTEND_DIR to it). `cd backend && uvicorn app.main:app` serves backend/frontend_dist.
// Both copies must behave the same for language and speech, so the key checks run against this copy too.
// node --test runs every file in its own process, so pointing the harness at the other directory here is safe.
const path = require("path");
process.env.SAARTHI_FRONTEND_DIR = path.resolve(__dirname, "..", "..", "..", "frontend");
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const vm = require("vm");
const { createEnv, load, VOICES, DIST } = require("./harness");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const HI = "अपनी पात्रता जाँचने के लिए, अपनी जानकारी को नीचे दी गई हर योजना की शर्तों से मिलाएँ। PM-KISAN: आयकर देने वाले पात्र नहीं हैं।";
const MR = "तुमची पात्रता तपासण्यासाठी, तुमची माहिती खालील प्रत्येक योजनेच्या अटींशी जुळवून पाहा. PM-KISAN: आयकर भरणारे पात्र नाहीत.";
const EN = "To check your eligibility, compare your details with the criteria of each scheme below.";

function boot(lang, voices) {
  const env = createEnv({ voices });
  env.sandbox.document.documentElement.lang = lang;      // this copy keeps the selected language on <html lang>
  load(env, "voice.js");
  load(env, "script.js");
  env.win.SaarthiTTS.config.voiceWaitMs = 60;
  return env;
}
// this copy appends a wrapper element per message instead of writing chat-box.innerHTML
const chat = (env) => env.get("chat-box").innerHTML + env.get("chat-box").children.map((c) => c.innerHTML).join("");
const I18N = (env, lang, key) => vm.runInContext(`I18N.${lang}.${key}`, env.ctx);

test("the harness really loaded the root frontend/ copy", () => {
  assert.ok(DIST.endsWith(path.join("frontend")) && !DIST.endsWith("frontend_dist"));
  assert.equal(fs.readFileSync(path.join(DIST, "voice.js"), "utf8"),
               fs.readFileSync(path.join(__dirname, "..", "..", "frontend_dist", "voice.js"), "utf8"), "voice.js copies identical");
});

test("root copy: Hindi and Marathi answers are spoken with hi-IN / mr-IN voices", async () => {
  for (const [text, lang, bcp, voice] of [[HI, "hi", "hi-IN", VOICES.swara], [MR, "mr", "mr-IN", VOICES.aarohi]]) {
    const env = boot(lang, [VOICES.neerja, VOICES.swara, VOICES.aarohi]);
    env.ctx.displayResponse({ answer: text, response_language: lang, recommended_schemes: [] }, false);
    await sleep(20);
    assert.ok(env.spoken.length && env.spoken.every((u) => u.lang === bcp && u.voice.name === voice.name), lang);
  }
});

test("root copy: an English fallback for a Hindi request is announced (it used to be shown silently)", () => {
  const env = boot("hi", [VOICES.neerja]);
  env.ctx.displayResponse({ answer: EN, response_language: "en", translation_status: "failed", recommended_schemes: [] }, false);
  assert.ok(chat(env).includes(I18N(env, "hi", "notTranslated")));
});

test("root copy: Listen button replays the answer after the browser blocked autoplay", async () => {
  const env = boot("mr", [VOICES.neerja, VOICES.aarohi]);
  let blocked = true;
  env.synthesis.speak = (u) => { env.spoken.push(u); setTimeout(() => (blocked ? u.onerror({ error: "not-allowed" }) : u.onend && u.onend()), 0); };
  env.ctx.displayResponse({ answer: MR, response_language: "mr", recommended_schemes: [] }, false);
  await sleep(30);
  assert.ok(chat(env).includes(I18N(env, "mr", "ttsBlocked")));
  const id = (chat(env).match(/data-answer-id="(ans\d+)"/) || [])[1];
  assert.ok(id);
  blocked = false;
  const res = await env.win.speakAnswerById(id);
  assert.equal(res.spoken, true);
  assert.equal(env.spoken[env.spoken.length - 1].lang, "mr-IN");
});

test("root copy: the offline answer generated in the browser is labelled English (its text is English)", () => {
  const env = boot("hi", []);
  assert.equal(env.ctx.generateOfflineResponse("x", []).response_language, "en");
});
