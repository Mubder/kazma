"use strict";
// The chat page's model picker (providers stage 3, 2026-11-08), run on the
// real functions sliced from chat.js.
//
// - A failed read of the model list keeps what the picker had (2026-10-15: a
//   502 while the server restarted erased the user's choice).
// - The picker shows what the next message runs with: this chat's own pick,
//   else the model the chat last ran with, else the default.
// - A pick is this chat's: stored under the chat's id, and the install's
//   default is never written (it switched the model for every chat,
//   Telegram included).
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = source.indexOf("  // ── Model selector ───");
const end = source.indexOf("  // ── Send message via SSE", start);
assert(start > 0 && end > start, "the model selector's code was not found");
const PICKER = source.slice(start, end);

const OPTIONS = {
  default: "openai:gpt-5-mini",
  options: [
    { ref: "openai:gpt-5-mini", provider: "openai", provider_label: "OpenAI", model: "gpt-5-mini" },
    { ref: "groq:llama-3.3-70b", provider: "groq", provider_label: "Groq", model: "llama-3.3-70b" },
  ],
};

function page(sessionId, stored) {
  const store = Object.assign({}, stored || {});
  const fetched = [];
  const select = {
    innerHTML: "",
    _value: "",
    get value() { return this._value; },
    set value(v) { this._value = this.innerHTML.includes('value="' + v + '"') ? v : ""; },
  };
  const context = {
    modelSelectorEl: select,
    selectedModel: "",
    chatSessionId: sessionId,
    MODEL_LS_KEY: "kazma.selectedModel",
    SESSION_LS_KEY: "kazma.session",
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    escapeHtml: (s) => String(s),
    ti: (key, fallback) => fallback,
    tiFmt: (key, fallback, vars) => fallback.replace("{model}", vars.model),
    KS: { toast: () => {} },
    document: { dispatchEvent: () => {} },
    CustomEvent: function CustomEvent(name, init) { this.name = name; this.detail = init && init.detail; },
    fetch: (url, init) => { fetched.push([url, init]); return new Promise(() => {}); },
  };
  context.window = context;
  vm.createContext(context);
  vm.runInContext(PICKER + "\nthis.populate = populateModelSelector; this.change = onModelChange;" +
    "\nthis.noteRan = _noteChatRunModel; this.setOptions = (o) => { _modelOptions = o; };", context);
  context.setOptions(OPTIONS);
  const groups = [
    { name: "openai", label: "OpenAI", models: [OPTIONS.options[0]] },
    { name: "groq", label: "Groq", models: [OPTIONS.options[1]] },
  ];
  return { context, select, store, fetched, groups };
}

let fail = 0;
function check(name, fn) {
  try { fn(); console.log("ok   " + name); } catch (e) { fail++; console.log("FAIL " + name + "\n  " + e.message); }
}

check("a chat with no pick shows the default", () => {
  const p = page("chat-a");
  p.context.populate(p.groups, [], true);
  assert.strictEqual(p.select.value, "openai:gpt-5-mini");
  assert.strictEqual(p.context.selectedModel, "openai:gpt-5-mini");
});

check("a chat shows the model it last ran with", () => {
  const p = page("chat-a");
  p.context.populate(p.groups, [], true);
  p.context.noteRan("chat-a", "groq:llama-3.3-70b");
  assert.strictEqual(p.select.value, "groq:llama-3.3-70b");
});

check("a chat's own pick wins, and is kept under the chat", () => {
  const p = page("chat-a", { "kazma.chatModel.chat-a": "groq:llama-3.3-70b" });
  p.context.populate(p.groups, [], true);
  assert.strictEqual(p.select.value, "groq:llama-3.3-70b");
});

check("a pick is this chat's, and the default is never written", () => {
  const p = page("chat-a");
  p.context.populate(p.groups, [], true);
  p.select.value = "groq:llama-3.3-70b";
  p.context.change();
  assert.strictEqual(p.store["kazma.chatModel.chat-a"], "groq:llama-3.3-70b");
  assert.deepStrictEqual(p.fetched, [], "the chat's picker wrote the install's model");
  const other = page("chat-b", p.store);
  other.context.populate(other.groups, [], true);
  assert.strictEqual(other.select.value, "openai:gpt-5-mini");
});

check("a failed read keeps what the picker had", () => {
  const p = page("chat-a");
  p.context.populate(p.groups, [], true);
  const before = p.select.innerHTML;
  p.context.populate([], [], false);
  assert.strictEqual(p.select.innerHTML, before);
  assert.strictEqual(p.context.selectedModel, "openai:gpt-5-mini");
  // Control: the same empty list read as complete clears the picker.
  p.context.populate([], [], true);
  assert.notStrictEqual(p.select.innerHTML, before);
});

if (fail) { console.log(fail + " failure(s)"); process.exit(1); }
console.log("all ok");
