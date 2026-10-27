const micBtn = document.getElementById("mic-btn");
const languageSelect = document.getElementById("language-select");

const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
let recognition = null;
let isListening = false;
let isSpeaking = false;
let currentUtterance = null;
let speechQueue = [];

const INDIAN_LANG_MAP = {
    "hi": "hi-IN",
    "en": "en-IN",
    "mr": "mr-IN",
    "bn": "bn-IN",
    "te": "te-IN",
    "ta": "ta-IN",
    "kn": "kn-IN",
    "gu": "gu-IN",
    "pa": "pa-IN",
    "or": "or-IN",
    "ml": "ml-IN",
    "as": "as-IN",
    "ur": "ur-IN",
};

const FEMALE_VOICE_NAMES = [
    "heera", "swara", "kalpana", "shruti",
    "zira", "hazel", "susan", "samantha", "karen",
    "monika", "amala", "aditi", "raveena", "priya",
    "neerja", "lekha", "meera", "female",
    "google हिन्दी", "google hindi",
    "google বাংলা", "google తెలుగు", "google தமிழ்",
    "google ગુજરાતી", "google ಕನ್ನಡ", "google മലയാളം",
];

const MALE_VOICE_NAMES = [
    "ravi", "hemant", "male", "madhur", "prabhat",
];

function getRecognitionLang() {
    const langCode = languageSelect.value;
    if (langCode && INDIAN_LANG_MAP[langCode]) {
        return INDIAN_LANG_MAP[langCode];
    }
    const nav = (navigator.language || "").toLowerCase();
    for (const [code, bcp] of Object.entries(INDIAN_LANG_MAP)) {
        if (nav.startsWith(code)) return bcp;
    }
    return "hi-IN";
}

// ---------------------------------------------------------------------------------------------------------------
// Text-to-speech: language and voice selection.
//
// Rules (see tests/frontend/voice.test.js):
//  * The language spoken is the language of the text that is DISPLAYED: the backend's response_language, checked
//    against the script of the text. Speech is never started with a voice for a different language.
//  * Only the app's answer languages are speech languages. "auto", unknown or missing codes are NOT mapped to Hindi.
//  * A voice must match the language's primary subtag (hi-IN, hi_IN -> hi). If none is installed the answer is not
//    read aloud and the user is told, instead of letting the browser's default (English) voice read it.
//  * Voices load asynchronously (getVoices() is [] on first call in Chrome/Edge): we wait for them.
// ---------------------------------------------------------------------------------------------------------------
const TTS_LANGS = ["en", "hi", "bn", "te", "mr", "ta", "gu", "kn", "ml", "pa", "or"];
const TTS_CONFIG = { voiceWaitMs: 1500, nativeMinShare: 0.3 };
const TTS_SCRIPT_RANGES = {
    latin: [0x41, 0x24F], devanagari: [0x900, 0x97F], bengali: [0x980, 0x9FF], gurmukhi: [0xA00, 0xA7F],
    gujarati: [0xA80, 0xAFF], odia: [0xB00, 0xB7F], tamil: [0xB80, 0xBFF], telugu: [0xC00, 0xC7F],
    kannada: [0xC80, 0xCFF], malayalam: [0xD00, 0xD7F],
};
const TTS_LANG_SCRIPT = {
    en: "latin", hi: "devanagari", mr: "devanagari", bn: "bengali", pa: "gurmukhi", gu: "gujarati",
    or: "odia", ta: "tamil", te: "telugu", kn: "kannada", ml: "malayalam",
};
let speechRequestId = 0;

function resolveSpeechLang(code) {
    if (!code) return null;
    const short = String(code).trim().toLowerCase().replace(/_/g, "-").split("-")[0];
    return TTS_LANGS.includes(short) ? { short: short, bcp: INDIAN_LANG_MAP[short] } : null;
}

function ttsLetterCounts(text) {
    const counts = {};
    for (const ch of String(text || "")) {
        if (!/[\p{L}\p{M}]/u.test(ch)) continue;
        const cp = ch.codePointAt(0);
        let script = "other";
        for (const [name, [lo, hi]] of Object.entries(TTS_SCRIPT_RANGES)) {
            if (cp >= lo && cp <= hi) { script = name; break; }
        }
        counts[script] = (counts[script] || 0) + 1;
    }
    return counts;
}

// Hindi and Marathi share Devanagari, so the script alone cannot say which one a text is in. Frequent words that
// occur in only one of the two languages decide (mirrors backend language_utils.detect_devanagari_language).
const TTS_HI_MARKERS = new Set("है हैं था थे थी के में और लिए नहीं यह वह से को पर हूँ हूं आप आपको आपके सकते सकता सकती चाहिए जाता जाती जाते गया गई भी कौन क्या कैसे अगर लेकिन".split(" "));
const TTS_MR_MARKERS = new Set("आहे आहेत आणि नाही मध्ये साठी तुम्ही तुमच्या तुम्हाला तुमचे तुमची आपण करा करू शकता शकतो शकते किंवा असे असेल होते केले जाते मिळते मिळेल मिळतात पण कसे काय कोणत्या कोणते".split(" "));
const TTS_MR_SUFFIXES = ["च्या", "साठी", "मध्ये", "ांना", "ांची", "ांचे", "ाचा", "ाची", "ाचे", "ाला"];

function guessDevanagariLanguage(text) {
    let hi = 0, mr = 0;
    for (const w of String(text || "").match(/[\u0900-\u097F]+/g) || []) {
        if (TTS_HI_MARKERS.has(w) || (w.length > 3 && w.endsWith("ों"))) hi++;
        if (TTS_MR_MARKERS.has(w) || (w.length > 4 && TTS_MR_SUFFIXES.some(sfx => w.endsWith(sfx)))) mr++;
    }
    if (mr >= 2 && mr >= 2 * Math.max(hi, 1)) return "mr";
    if (hi > mr) return "hi";
    if (mr > hi) return "mr";
    return null;
}

// Share of the letters of `text` written in the script of `lang` (0..1).
function scriptShare(text, lang) {
    const resolved = resolveSpeechLang(lang);
    if (!resolved) return 0;
    const counts = ttsLetterCounts(text);
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    return total ? (counts[TTS_LANG_SCRIPT[resolved.short]] || 0) / total : 0;
}

// What language is `text` really in, given the language it is labelled with?
function effectiveAnswerLanguage(text, claimedLang) {
    const counts = ttsLetterCounts(text);
    const total = Object.values(counts).reduce((a, b) => a + b, 0);
    const claimed = resolveSpeechLang(claimedLang);
    if (!total) return { lang: claimed ? claimed.short : null, mismatch: false, claimed: claimed ? claimed.short : null };
    const dominant = Object.keys(counts).reduce((a, b) => (counts[a] >= counts[b] ? a : b));
    let fromScript = Object.keys(TTS_LANG_SCRIPT).find(l => TTS_LANG_SCRIPT[l] === dominant) || null;
    if (dominant === "devanagari") fromScript = guessDevanagariLanguage(text) || "hi";   // never "Marathi = Hindi" by script
    if (!claimed) return { lang: fromScript, mismatch: false, claimed: null };
    if (claimed.short === "en") return { lang: "en", mismatch: false, claimed: "en" };
    const share = (counts[TTS_LANG_SCRIPT[claimed.short]] || 0) / total;
    if (share >= TTS_CONFIG.nativeMinShare) return { lang: claimed.short, mismatch: false, claimed: claimed.short };
    // Labelled as a non-English language but not written in its script: e.g. an English answer whose translation failed.
    return { lang: fromScript, mismatch: true, claimed: claimed.short };
}

function voicePrimary(voice) {
    return String((voice && voice.lang) || "").toLowerCase().replace(/_/g, "-").split("-")[0];
}

function pickVoice(voices, langCode) {
    const resolved = resolveSpeechLang(langCode);
    if (!resolved || !voices || !voices.length) return null;
    const targetLang = resolved.bcp.toLowerCase();
    let bestVoice = null;
    let bestScore = -1;

    for (const voice of voices) {
        if (voicePrimary(voice) !== resolved.short) continue;      // strict: the voice must speak this language

        let score = 0;
        const nameLower = String(voice.name || "").toLowerCase();
        if (String(voice.lang || "").toLowerCase().replace(/_/g, "-") === targetLang) score += 2;

        const isFemale = FEMALE_VOICE_NAMES.some(fn => nameLower.includes(fn));
        const isMale = MALE_VOICE_NAMES.some(mn => nameLower.includes(mn));
        if (isFemale && !isMale) score += 10;
        if (isMale && !isFemale) score -= 5;

        const premiumKeywords = ["neural", "natural", "premium", "wavenet", "enhanced"];
        for (const kw of premiumKeywords) {
            if (nameLower.includes(kw)) { score += 6; break; }
        }
        const providerKeywords = ["google", "microsoft"];
        for (const kw of providerKeywords) {
            if (nameLower.includes(kw)) { score += 4; break; }
        }
        if (!voice.localService) score += 2;

        if (score > bestScore) {
            bestScore = score;
            bestVoice = voice;
        }
    }
    return bestVoice;
}

function getVoicesNow() {
    return window.speechSynthesis ? (window.speechSynthesis.getVoices() || []) : [];
}

// Kept for compatibility: best installed voice for a language right now (null when none).
function selectBestVoice(langCode) {
    return pickVoice(getVoicesNow(), langCode);
}

// Resolves with the voice list as soon as the browser has one (voiceschanged event or polling), or [] after a timeout.
function loadVoices(timeoutMs) {
    const synth = window.speechSynthesis;
    if (!synth) return Promise.resolve([]);
    if ((synth.getVoices() || []).length) return Promise.resolve(synth.getVoices());
    const wait = typeof timeoutMs === "number" ? timeoutMs : TTS_CONFIG.voiceWaitMs;
    return new Promise(resolve => {
        let finished = false;
        let poll = null;
        let timer = null;
        const finish = () => {
            if (finished) return;
            finished = true;
            clearInterval(poll);
            clearTimeout(timer);
            if (synth.removeEventListener) synth.removeEventListener("voiceschanged", check);
            resolve(synth.getVoices() || []);
        };
        const check = () => { if ((synth.getVoices() || []).length) finish(); };
        if (synth.addEventListener) synth.addEventListener("voiceschanged", check);
        poll = setInterval(check, 100);                 // some browsers never fire voiceschanged
        timer = setTimeout(finish, wait);
    });
}

// Decide what to do with `text` (labelled `claimedLang`) given the installed `voices`. Pure: no speaking.
function planSpeech(text, claimedLang, voices) {
    const clean = cleanTextForSpeech(text || "");
    if (!clean || clean.length < 2) return { action: "skip", reason: "empty" };
    const eff = effectiveAnswerLanguage(clean, claimedLang);
    const resolved = eff.lang ? resolveSpeechLang(eff.lang) : null;
    if (!resolved) return { action: "skip", reason: "unsupported_language", claimed: eff.claimed };
    const voice = pickVoice(voices, resolved.short);
    if (!voice && resolved.short !== "en") {
        // An empty voice list means the browser has not loaded its voices yet - that is not "no voice for this language".
        const reason = (voices && voices.length) ? "no_voice" : "voices_not_loaded";
        return { action: "skip", reason: reason, lang: resolved.short, bcp: resolved.bcp, mismatch: eff.mismatch };
    }
    return {
        action: "speak", lang: resolved.short, bcp: resolved.bcp, voice: voice, text: clean,
        mismatch: eff.mismatch, reason: voice ? null : "default_voice_for_english",
    };
}

function ttsMessage(key, fallback) {
    try { if (typeof t === "function") { const m = t(key); if (m && m !== key) return m; } } catch (e) { /* use fallback */ }
    return fallback;
}

const TTS_NOTICES = {
    no_voice: ["noVoice", "No voice for this language is available in this browser, so the answer was not read aloud. Please read it on screen."],
    voices_not_loaded: ["voicesLoading", "This browser has not loaded its voices yet. Tap \u201cListen\u201d to try again, or read the answer on screen."],
    blocked: ["ttsBlocked", "The browser did not allow the answer to be read aloud automatically. Tap \u201cListen\u201d to hear it, or read it on screen."],
    failed: ["ttsFailed", "The answer could not be read aloud in this browser. Please read it on screen."],
};
let lastSpeechNotice = null;          // {reason, message, lang} of the last speech problem (null after a successful start)

// Tell the user why the answer is NOT being read aloud (status line + an optional page hook for an inline notice).
function notifySpeechProblem(reason, lang) {
    const entry = TTS_NOTICES[reason] || TTS_NOTICES.failed;
    const message = ttsMessage(entry[0], entry[1]);
    lastSpeechNotice = { reason: reason, message: message, lang: lang || null };
    if (typeof window.onSaarthiSpeechNotice === "function") {
        try { window.onSaarthiSpeechNotice(lastSpeechNotice); } catch (e) { /* page hook must not break speech */ }
    }
    if (window.showStatus) {
        window.showStatus(message);
        setTimeout(() => { if (window.showStatus) window.showStatus(""); }, 7000);
    }
}

function notifyNoVoice() { notifySpeechProblem("no_voice"); }      // kept for compatibility

function startSpeaking(plan, requestId) {
    const chunks = splitTextIntoChunks(plan.text, 180);
    speechQueue = chunks.map(chunk => ({ text: chunk, bcp: plan.bcp, voice: plan.voice, lang: plan.lang, requestId: requestId }));
    speakNextChunk();
}

// Speak the answer exactly as displayed, in the language it is written in, with a matching voice.
// Returns {spoken:true,...} when speech was STARTED; a browser that later refuses it (autoplay policy) is reported
// through notifySpeechProblem("blocked") from the utterance's error handler.
async function speakResponse(data, selectedLang) {
    if (!window.speechSynthesis) {
        console.warn("Speech Synthesis API not supported.");
        return { spoken: false, reason: "speech_unsupported" };
    }
    const myRequest = ++speechRequestId;
    stopSpeaking();
    const claimed = (data && data.response_language) || selectedLang;
    let voices = await loadVoices();
    if (myRequest !== speechRequestId) return { spoken: false, reason: "superseded" };   // language/answer changed while waiting
    let plan = planSpeech(data && data.answer, claimed, voices);
    if (plan.action === "skip" && plan.reason === "voices_not_loaded") {
        voices = await loadVoices(TTS_CONFIG.voiceWaitMs * 2);       // slow devices (Android) load voices late: wait once more
        if (myRequest !== speechRequestId) return { spoken: false, reason: "superseded" };
        plan = planSpeech(data && data.answer, claimed, voices);
    }
    if (plan.action === "skip") {
        if (plan.reason === "no_voice" || plan.reason === "voices_not_loaded") notifySpeechProblem(plan.reason, plan.lang);
        return { spoken: false, reason: plan.reason, lang: plan.lang || null };
    }
    lastSpeechNotice = null;
    startSpeaking(plan, myRequest);
    return { spoken: true, lang: plan.lang, bcp: plan.bcp, voice: plan.voice ? plan.voice.name : null, mismatch: plan.mismatch };
}

function onSelectedLanguageChanged() {
    speechRequestId++;                 // cancels a speak that is still waiting for voices
    stopSpeaking();                    // never keep reading an answer in the previous language
    getVoicesNow();                    // voices are re-read at the next speak, so the new language gets its own voice
}
if (languageSelect) languageSelect.addEventListener("change", onSelectedLanguageChanged);

function stopSpeaking() {
    if (window.speechSynthesis) {
        window.speechSynthesis.cancel();
    }
    speechQueue = [];
    isSpeaking = false;
    currentUtterance = null;
    updateVoiceOrb();
    hideStopButton();
}

function showStopButton() {
    const btn = document.getElementById("stop-speech-btn");
    if (btn) {
        btn.classList.remove("hidden");
        btn.classList.add("flex");
    }
}

function hideStopButton() {
    const btn = document.getElementById("stop-speech-btn");
    if (btn) {
        btn.classList.add("hidden");
        btn.classList.remove("flex");
    }
}

function updateVoiceOrb() {
    const orb = document.getElementById("mic-btn");
    if (!orb) return;

    orb.classList.remove("listening", "speaking");

    const micIcon = orb.querySelector(".material-symbols-outlined");

    if (isListening) {
        orb.classList.add("listening");
        if (micIcon) micIcon.textContent = "hearing";
    } else if (isSpeaking) {
        orb.classList.add("speaking");
        if (micIcon) micIcon.textContent = "volume_up";
    } else {
        if (micIcon) micIcon.textContent = "mic";
    }
}

if (SpeechRecognition) {
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = true;
    recognition.maxAlternatives = 1;

    recognition.onstart = function () {
        isListening = true;
        updateVoiceOrb();
        if (window.showStatus) window.showStatus(window.t ? window.t("listening") : "Listening...");
    };

    recognition.onresult = function (event) {
        let finalTranscript = "";
        let interimTranscript = "";

        for (let i = 0; i < event.results.length; i++) {
            if (event.results[i].isFinal) {
                finalTranscript += event.results[i][0].transcript;
            } else {
                interimTranscript += event.results[i][0].transcript;
            }
        }

        const textInput = document.getElementById("text-input");

        if (finalTranscript) {
            textInput.value = finalTranscript;
            if (window.showStatus) window.showStatus(window.t ? window.t("understood") : "Understood!");
            setTimeout(sendTextMessage, 400);
        } else if (interimTranscript) {
            textInput.value = interimTranscript;
        }
    };

    recognition.onerror = function (event) {
        console.error("Speech recognition error", event.error);

        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
            if (window.showStatus) window.showStatus("Microphone access denied. Please allow microphone in browser settings.");
        } else if (event.error === "no-speech") {
            if (window.showStatus) window.showStatus("No speech detected. Try again.");
        } else if (event.error === "network") {
            if (window.showStatus) window.showStatus("Network error. Check your connection.");
        } else {
            if (window.showStatus) window.showStatus(window.t ? window.t("hearError") : "Sorry, I couldn't hear you.");
        }
        stopListening();
    };

    recognition.onend = function () {
        stopListening();
    };

    micBtn.addEventListener("click", () => {
        if (isSpeaking) {
            stopSpeaking();
            return;
        }

        if (isListening) {
            recognition.stop();
        } else {
            recognition.lang = getRecognitionLang();
            try {
                recognition.start();
            } catch (e) {
                console.error("Could not start recognition", e);
                if (e.message && e.message.includes("already started")) {
                    recognition.stop();
                    setTimeout(() => {
                        recognition.lang = getRecognitionLang();
                        recognition.start();
                    }, 300);
                }
            }
        }
    });
} else {
    micBtn.disabled = true;
    micBtn.classList.add("mic-disabled");
    micBtn.innerHTML = '<span class="material-symbols-outlined text-2xl">mic_off</span>';

    const hint = document.getElementById("mic-unsupported-hint");
    if (hint) {
        hint.classList.remove("hidden");
        hint.textContent = "Voice not supported on this browser — please type your message.";
    }

    const setMicLabel = () => {
        const msg = window.t ? window.t("micUnsupported") : "Voice not supported on this browser";
        micBtn.title = msg;
        micBtn.setAttribute("aria-label", msg);
        if (hint) hint.textContent = msg;
    };
    document.addEventListener("DOMContentLoaded", setMicLabel);
}

function stopListening() {
    isListening = false;
    updateVoiceOrb();
    if (window.showStatus) window.showStatus("");
}

function cleanTextForSpeech(text) {
    return text
        .replace(/\*\*(.+?)\*\*/g, "$1")
        .replace(/\*(.+?)\*/g, "$1")
        .replace(/#{1,3}\s*/g, "")
        .replace(/[_~`]/g, "")
        .replace(/https?:\/\/\S+/g, "")
        .replace(/[─━═▬]{2,}/g, "")
        .replace(/[💰✅📄📞🔗📋📍🛡️📝🌐]/g, "")
        .replace(/\d+\)\s*/g, "")
        .replace(/^[ \t]*[-•][ \t]+/gm, "")
        .replace(/•\s*/g, "")
        .replace(/\(\s*\)/g, "")
        .replace(/\n{2,}/g, ". ")
        .replace(/\n/g, ". ")
        .replace(/\.\s*\./g, ".")
        .replace(/\s{2,}/g, " ")
        .replace(/,\s*,/g, ",")
        .trim();
}

function splitAtBoundary(text, maxLen, separatorPattern) {
    if (text.length <= maxLen) return [text];

    const parts = text.split(separatorPattern).filter(p => p.length > 0);
    const chunks = [];
    let current = "";

    for (const part of parts) {
        if (current.length + part.length + 1 > maxLen && current.length > 0) {
            chunks.push(current.trim());
            current = part;
        } else {
            current += (current ? " " : "") + part;
        }
    }
    if (current.trim()) chunks.push(current.trim());
    return chunks;
}

function splitTextIntoChunks(text, maxLen) {
    if (text.length <= maxLen) return [text];

    let chunks = splitAtBoundary(text, maxLen, /[।.!?]+\s*/);

    const result = [];
    for (const chunk of chunks) {
        if (chunk.length <= maxLen) {
            result.push(chunk);
        } else {
            const commaChunks = splitAtBoundary(chunk, maxLen, /[,;:]\s*/);
            for (const cc of commaChunks) {
                if (cc.length <= maxLen) {
                    result.push(cc);
                } else {
                    const words = cc.split(/\s+/);
                    let line = "";
                    for (const word of words) {
                        if (line.length + word.length + 1 > maxLen && line.length > 0) {
                            result.push(line.trim());
                            line = word;
                        } else {
                            line += (line ? " " : "") + word;
                        }
                    }
                    if (line.trim()) result.push(line.trim());
                }
            }
        }
    }

    return result;
}

function speakNextChunk() {
    if (speechQueue.length === 0) {
        isSpeaking = false;
        currentUtterance = null;
        updateVoiceOrb();
        hideStopButton();
        return;
    }

    const { text, bcp, voice, lang, requestId } = speechQueue.shift();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = bcp;

    if (voice) utterance.voice = voice;

    utterance.rate = 0.92;
    utterance.pitch = 1.12;
    utterance.volume = 1.0;

    utterance.onstart = function () {
        isSpeaking = true;
        updateVoiceOrb();
        showStopButton();
    };

    utterance.onend = function () {
        speakNextChunk();
    };

    utterance.onerror = function (e) {
        const err = e && e.error;
        if (err !== "interrupted" && err !== "canceled") {
            console.warn("TTS error:", err);
            // Never fail silently: autoplay refusal ("not-allowed": Chrome needs a recent tap) is the common case.
            if (requestId === undefined || requestId === speechRequestId) {
                notifySpeechProblem(err === "not-allowed" ? "blocked"
                    : (err === "language-unavailable" || err === "voice-unavailable") ? "no_voice" : "failed", lang);
            }
        }
        speechQueue = [];
        isSpeaking = false;
        currentUtterance = null;
        updateVoiceOrb();
        hideStopButton();
    };

    currentUtterance = utterance;
    window.speechSynthesis.speak(utterance);
}

// Compatibility wrapper: speak `text` labelled with `responseLang`. "auto"/unknown labels are resolved from the text's
// script; they are never mapped to Hindi.
window.speakText = function (text, responseLang = null) {
    return speakResponse({ answer: text, response_language: responseLang }, languageSelect ? languageSelect.value : null);
};

window.SaarthiTTS = {
    TTS_LANGS: TTS_LANGS, config: TTS_CONFIG,
    resolveSpeechLang: resolveSpeechLang, effectiveAnswerLanguage: effectiveAnswerLanguage, pickVoice: pickVoice,
    loadVoices: loadVoices, planSpeech: planSpeech, speakResponse: speakResponse, cleanTextForSpeech: cleanTextForSpeech,
    guessDevanagariLanguage: guessDevanagariLanguage, scriptShare: scriptShare,
    lastNotice: () => lastSpeechNotice,
};

window.stopSpeaking = stopSpeaking;
window.isSpeaking = () => isSpeaking;

const textInput = document.getElementById("text-input");
if (textInput) {
    textInput.addEventListener("input", () => {
        if (isSpeaking) stopSpeaking();
    });
}
