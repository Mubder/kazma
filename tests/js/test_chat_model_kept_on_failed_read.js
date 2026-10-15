"use strict";
// The chat page keeps the model it chose when the provider list could not be
// read (2026-10-15). populateModelSelector drops a persisted model the list no
// longer offers (a keyless default would pin a 401) -- and it did so after a
// failed /api/providers read too: a 502 while the server restarts, beside the
// saved profiles, erased the user's choice from storage. Run on the real
// functions sliced from chat.js; the old drop rule is the negative control.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = source.indexOf("  // The model this page last chose, offered on its own when the provider");
const end = source.indexOf("  function onModelChange()", start);
assert(start > 0 && end > start, "the model selector's code was not found");
const SELECTOR = source.slice(start, end);

function page(code, chosen) {
  const stored = { model: chosen };
  const select = {
    innerHTML: "",
    _value: "",
    get value() { return this._value; },
    // A <select> takes a value only when one of its options carries it.
    set value(v) { this._value = this.innerHTML.includes('value="' + v + '"') ? v : ""; },
  };
  const context = {
    modelSelectorEl: select,
    selectedModel: chosen,
    MODEL_LS_KEY: "model",
    localStorage: { setItem: (k, v) => { stored.model = v; } },
    escapeHtml: (s) => String(s),
    ti: (key, fallback) => fallback,
  };
  vm.createContext(context);
  vm.runInContext(code + "\nthis.populate = populateModelSelector; this.persisted = _persistedModelGroup;", context);
  return { context, select, stored };
}

const saved = [{ value: "profile-model", label: "Work (profile-model)", isProfile: true }];
const groups = [{ name: "deepseek", label: "DeepSeek", models: ["deepseek-chat"] }];

// A failed providers read beside saved profiles: the choice stays, offered.
{
  const p = page(SELECTOR, "deepseek-reasoner");
  p.context.populate(p.context.persisted(), saved, false);
  assert.strictEqual(p.context.selectedModel, "deepseek-reasoner");
  assert.strictEqual(p.stored.model, "deepseek-reasoner");
  assert.strictEqual(p.select.value, "deepseek-reasoner");
}

// A complete list that no longer offers it: dropped, as before.
{
  const p = page(SELECTOR, "gpt-4o-mini");
  p.context.populate(groups, saved, true);
  assert.notStrictEqual(p.context.selectedModel, "gpt-4o-mini");
  assert.notStrictEqual(p.stored.model, "gpt-4o-mini");
}

// A complete list that offers it: kept and selected.
{
  const p = page(SELECTOR, "deepseek-chat");
  p.context.populate(groups, saved, true);
  assert.strictEqual(p.context.selectedModel, "deepseek-chat");
  assert.strictEqual(p.select.value, "deepseek-chat");
}

// Negative control: the old rule dropped the choice after a failed read.
{
  const old = SELECTOR.replace("complete === true && ", "");
  assert.notStrictEqual(old, SELECTOR, "the drop rule was not found");
  const p = page(old, "deepseek-reasoner");
  p.context.populate([], saved, false);
  assert.notStrictEqual(p.stored.model, "deepseek-reasoner", "the old rule kept the choice; the control proves nothing");
}

console.log("chat model kept on a failed read: ok");
