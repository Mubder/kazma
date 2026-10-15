"use strict";
// The Swarm page's model picker says when the provider list could not be
// read (2026-10-15). A refused or failed /api/providers read left the picker
// on "— no models available —": a claim about the install the page never
// received. It now says the read failed; models an earlier read gave stay
// offered; an answer with no usable model still says there are none. Run on
// the real loader and renderer sliced from swarm.js; the old loader (a
// refused read read as "no providers") is the negative control.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");

const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/swarm.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = source.indexOf("  // Provider -> models map fetched from /api/providers");
const end = source.indexOf("  function providerForModel(", start);
assert(start > 0 && end > start, "the model picker's code was not found");
const PICKER = source.slice(start, end);

async function settle() {
  for (let i = 0; i < 6; i++) await new Promise((resolve) => setImmediate(resolve));
}

function page(code, answers) {
  const selects = { "add-model-select": { innerHTML: "", value: "" }, "edit-worker-model": { innerHTML: "", value: "" } };
  const context = {
    $: (id) => selects[id] || null,
    esc: (s) => String(s),
    t: (key) => "[" + key + "]",
    defaultModelOption: () => "",
    fetch: async () => answers.shift()(),
  };
  vm.createContext(context);
  vm.runInContext(code + "\nthis.populate = populateSwarmModelSelects;", context);
  return { selects, populate: context.populate };
}

const refused = () => ({ ok: false, status: 503, json: async () => ({ error: "unavailable" }) });
const offline = () => { throw new TypeError("Failed to fetch"); };
const none = () => ({ ok: true, json: async () => [{ name: "p", enabled: true, models: [] }] });
const two = () => ({ ok: true, json: async () => [{ name: "deepseek", enabled: true, models: ["deepseek-chat", "deepseek-reasoner"] }] });

(async () => {
  // A refused read and a network failure: the picker says the read failed.
  for (const failure of [refused, offline]) {
    const p = page(PICKER, [failure]);
    p.populate();
    await settle();
    for (const id of ["add-model-select", "edit-worker-model"]) {
      assert.ok(p.selects[id].innerHTML.includes("[swarm.models_load_failed]"), id + ": " + p.selects[id].innerHTML);
      assert.ok(!p.selects[id].innerHTML.includes("[swarm.no_models]"), id);
    }
  }

  // The server answered and no provider has a model: that IS "no models".
  {
    const p = page(PICKER, [none]);
    p.populate();
    await settle();
    assert.ok(p.selects["add-model-select"].innerHTML.includes("[swarm.no_models]"));
  }

  // A later failed read keeps the models an earlier one gave.
  {
    const p = page(PICKER, [two, refused]);
    p.populate();
    await settle();
    assert.ok(p.selects["add-model-select"].innerHTML.includes("deepseek-reasoner"));
    p.populate();
    await settle();
    assert.ok(p.selects["add-model-select"].innerHTML.includes("deepseek-reasoner"),
      "a failed refresh dropped models the page had read");
    assert.ok(!p.selects["add-model-select"].innerHTML.includes("models_load_failed"));
  }

  // Negative control: the old loader read a refused answer as an empty list.
  {
    const old = PICKER
      .replace(/\.then\(function\(r\) \{\n\s*if \(!r\.ok\) throw new Error\('HTTP ' \+ r\.status\);\n\s*return r\.json\(\);\n\s*\}\)/,
        ".then(function(r) { return r.ok ? r.json() : []; })")
      .replace("if (!Array.isArray(providers)) throw new Error('providers unavailable');", "");
    assert.notStrictEqual(old, PICKER, "the control must change the loader");
    const p = page(old, [refused]);
    p.populate();
    await settle();
    assert.ok(p.selects["add-model-select"].innerHTML.includes("[swarm.no_models]"),
      "the control did not reproduce the old answer: " + p.selects["add-model-select"].innerHTML);
  }
  console.log("swarm model picker: a failed providers read is said, never 'no models'");
})().catch((err) => { console.error(err); process.exit(1); });
