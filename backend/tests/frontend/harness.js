// Loads the REAL served frontend files (backend/frontend_dist/voice.js and script.js) into a Node `vm`
// context with minimal browser stubs. No jsdom / npm dependencies: run with `node --test tests/frontend`.
const fs = require("fs");
const path = require("path");
const vm = require("vm");

// SAARTHI_FRONTEND_DIR lets the same tests be pointed at another build (for example the pre-fix files, to prove the tests catch the bugs)
const DIST = process.env.SAARTHI_FRONTEND_DIR || path.resolve(__dirname, "..", "..", "frontend_dist");

function makeElement(overrides = {}) {
  const listeners = {};
  const el = {
    value: "", textContent: "", innerHTML: "", disabled: false, title: "", dataset: {}, style: {}, children: [],
    classList: { _s: new Set(), add(c) { this._s.add(c); }, remove(c) { this._s.delete(c); }, contains(c) { return this._s.has(c); }, toggle() {} },
    addEventListener(type, fn) { (listeners[type] = listeners[type] || []).push(fn); },
    removeEventListener() {}, setAttribute() {}, getAttribute() { return null; }, removeAttribute() {},
    appendChild(c) { el.children.push(c); return c; }, insertAdjacentHTML(_p, html) { el.innerHTML += html; },
    querySelector() { return makeElement(); }, querySelectorAll() { return []; }, closest() { return null; },
    focus() {}, blur() {}, click() {}, scrollIntoView() {}, remove() {}, scrollTo() {},
    _listeners: listeners, _fire(type, ev = {}) { (listeners[type] || []).forEach(f => f(ev)); },
    ...overrides,
  };
  return el;
}

class FakeUtterance {
  constructor(text) { this.text = text; this.lang = ""; this.voice = null; this.rate = 1; this.pitch = 1; this.volume = 1; }
}

// voices: array of {name, lang, localService}
function createEnv({ voices = [], voicesArriveLater = null, selectedLang = "en", navigatorLang = "en-US", withSpeech = true, fetchImpl = null, store = {} } = {}) {
  const elements = {};
  const get = (id) => (elements[id] = elements[id] || makeElement());
  get("language-select").value = selectedLang;
  const spoken = [];            // every utterance handed to speechSynthesis.speak
  const statusMessages = [];
  const fetchCalls = [];
  let currentVoices = voices.slice();

  const synthesis = {
    speaking: false,
    getVoices() { return currentVoices; },
    speak(u) { spoken.push(u); if (u.onstart) u.onstart(); setTimeout(() => u.onend && u.onend(), 0); },
    cancel() {}, pause() {}, resume() {},
    onvoiceschanged: null,
    _listeners: {},
    addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); },
    removeEventListener() {},
    _setVoices(v) { currentVoices = v; (this._listeners.voiceschanged || []).forEach(f => f()); if (typeof this.onvoiceschanged === "function") this.onvoiceschanged(); },
  };

  const localStorage = {
    _d: { ...store },
    getItem(k) { return k in this._d ? this._d[k] : null; },
    setItem(k, v) { this._d[k] = String(v); },
    removeItem(k) { delete this._d[k]; },
    key(i) { return Object.keys(this._d)[i]; },
    get length() { return Object.keys(this._d).length; },
  };

  const documentStub = {
    getElementById: get, querySelector: () => makeElement(), querySelectorAll: () => [],
    createElement: () => makeElement(), addEventListener() {}, body: makeElement(), documentElement: makeElement(),
    readyState: "complete",
  };
  const win = {
    speechSynthesis: withSpeech ? synthesis : undefined,
    addEventListener() {}, removeEventListener() {}, location: { origin: "http://localhost:8000", protocol: "http:", href: "http://localhost:8000/", hostname: "localhost", port: "8000" },
    matchMedia: () => ({ matches: false, addEventListener() {} }), scrollTo() {}, setTimeout, clearTimeout,
    showStatus: (m) => statusMessages.push(m),
  };
  const sandbox = {
    window: win, document: documentStub, navigator: { language: navigatorLang, onLine: true, serviceWorker: undefined },
    localStorage, sessionStorage: { getItem() { return null; }, setItem() {}, removeItem() {} },
    SpeechSynthesisUtterance: FakeUtterance, SpeechRecognition: undefined,
    console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval() {}, requestAnimationFrame: (f) => setTimeout(f, 0),
    fetch: async (url, opts) => { fetchCalls.push({ url, opts }); return fetchImpl ? fetchImpl(url, opts) : { ok: true, json: async () => ({}) }; },
    Promise, JSON, Math, Date, Object, Array, Set, Map, Error, TypeError, parseInt, parseFloat, encodeURIComponent, decodeURIComponent,
    Intl, URL, AbortController, structuredClone,
  };
  win.window = win; win.document = documentStub; win.navigator = sandbox.navigator; win.localStorage = localStorage;
  win.SpeechSynthesisUtterance = FakeUtterance; win.fetch = sandbox.fetch;
  sandbox.globalThis = sandbox;
  const ctx = vm.createContext(sandbox);
  return { ctx, sandbox, win, elements, get, spoken, statusMessages, fetchCalls, synthesis, localStorage,
           setVoices: (v) => synthesis._setVoices(v) };
}

function load(env, file) {
  const src = fs.readFileSync(path.join(DIST, file), "utf8");
  // top-level `const`/`let`/`function` in a vm script are visible to later scripts in the same context
  vm.runInContext(src, env.ctx, { filename: file });
  // script.js assigns its own window.showStatus (as in a real browser); wrap the real one so tests can both record
  // the messages and still exercise the real function (which writes to #status-indicator).
  if (typeof env.win.showStatus === "function" && !env.win.showStatus._recorded) {
    const original = env.win.showStatus;
    const recorder = (message) => { env.statusMessages.push(message); return original(message); };
    recorder._recorded = true;
    env.win.showStatus = recorder;
  }
  return src;
}

const VOICES = {
  neerja: { name: "Microsoft Neerja Online (Natural) - English (India)", lang: "en-IN", localService: false },
  aarohi: { name: "Microsoft Aarohi Online (Natural) - Marathi (India)", lang: "mr-IN", localService: false },
  swara: { name: "Microsoft Swara Online (Natural) - Hindi (India)", lang: "hi-IN", localService: false },
  heera: { name: "Microsoft Heera - Hindi (India)", lang: "hi-IN", localService: true },
  zira: { name: "Microsoft Zira - English (United States)", lang: "en-US", localService: true },
  tamil: { name: "Microsoft Valluvar - Tamil (India)", lang: "ta-IN", localService: true },
};

module.exports = { createEnv, load, makeElement, DIST, VOICES, FakeUtterance };
