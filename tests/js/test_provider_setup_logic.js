/**
 * The guided provider setup's own rules (settings_provider_setup.js):
 * a pasted key names its provider in the page, an address is filled from the
 * typed values, a name is free, and the resumable draft holds no secret.
 * Run: node tests/js/test_provider_setup_logic.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const dir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const stored = {};
const sandbox = {
  console, URL,
  window: {
    localStorage: {
      getItem: (k) => (k in stored ? stored[k] : null),
      setItem: (k, v) => { stored[k] = v; },
      removeItem: (k) => { delete stored[k]; },
    },
  },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(dir, "providers.js"), "utf8"), sandbox);
vm.runInContext("var ProvidersManager = window.ProvidersManager;", sandbox);
vm.runInContext(fs.readFileSync(path.join(dir, "settings_provider_setup.js"), "utf8"), sandbox);
const S = sandbox.window.ProviderSetup;

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) { fail++; console.error("FAIL", name, detail === undefined ? "" : detail); }
  else console.log("ok  ", name);
}

// ── A key's shape ──
const shapes = [
  { pattern: "^sk-or-", providers: ["openrouter"] },
  { pattern: "^sk-", providers: ["openai", "deepseek"] },
];
ok("the longest shape wins", JSON.stringify(S.guess("sk-or-demo-key", shapes)) === '["openrouter"]');
ok("a shared shape names them all", S.guess("sk-demo-key-1", shapes).length === 2);
ok("a search word is no key", S.guess("openrouter", shapes).length === 0 && S.guess("sk-or a b", shapes).length === 0);

// ── Addresses, names, groups ──
ok("an address is filled", S.fill("https://{resource}.openai.azure.com", { resource: "my-r" }) === "https://my-r.openai.azure.com");
ok("a missing value stays a placeholder", S.fill("https://{a}.b", {}) === "https://{a}.b");
ok("a catalog row nobody set up is filled in", S.freeName("openai", [{ name: "openai", enabled: false, api_key: "" }]) === "openai");
ok("a row with a key is not", S.freeName("openai", [{ name: "OpenAI", enabled: false, api_key: "****1234" }, { name: "openai-2", enabled: true }]) === "openai-3");
const groups = S.groups([{ id: "a", name: "Alpha", group: "api" }, { id: "b", name: "Beta", group: "host" }], ["api", "host", "local"], "bet");
ok("search keeps only what it finds, by group", groups.length === 1 && groups[0].rows[0].id === "b");
ok("a tile's letters", S.monogram("xAI / Grok") === "XG" && S.monogram("OpenAI") === "OP" && S.monogram("") === "?");

// ── The draft ──
const typed = Object.assign(S.empty(), {
  open: true, preset: "openrouter", step: 4, query: "sk-or-demo-pasted", apiKey: "sk-or-demo-key", awsSecret: "demo-secret",
  awsToken: "demo-token", awsKeyId: "DEMOKEYID", headerRows: [{ name: "X", value: "demo-header" }],
  customUrl: "https://api.example/v1", name: "openrouter",
});
const draft = S.draft(typed);
const text = JSON.stringify(draft);
ok("the draft has the choices", draft.preset === "openrouter" && draft.customUrl === "https://api.example/v1");
["sk-or-demo", "demo-secret", "demo-token", "DEMOKEYID", "demo-header"].forEach((s) => ok("the draft holds no " + s, text.indexOf(s) === -1));
// Every field of the dialog is either in the draft's list or never kept.
const kept = new Set(S.DRAFT_FIELDS);
const secret = ["apiKey", "awsKeyId", "awsSecret", "awsToken", "headerRows", "query"];
ok("no secret field is in the list", secret.every((f) => f in S.empty() && !kept.has(f)));
["https://u:pw@api.example/v1", "https://api.example/v1?key=demo"].forEach((url) =>
  ok("an address with a secret in it is not kept", S.draft(Object.assign({}, typed, { customUrl: url })).customUrl === ""));
ok("nothing chosen, nothing kept", S.draft(S.empty()) === null && S.draft(Object.assign({}, typed, { saved: "x" })) === null);
const back = S.restore(draft, [{ id: "openrouter" }]);
ok("it is resumed where the key is typed again", back.preset === "openrouter" && back.step === 2 && back.apiKey === "" && back.resumed);
ok("a provider that is gone is not resumed", S.restore(draft, []) === null && S.restore("x", []) === null);

// ── The component keeps the key in memory only ──
const app = sandbox.window.KazmaSettingsMixins.providerSetup();
app.setup = Object.assign(S.empty(), typed);
app.keepSetupDraft();
ok("what is stored holds no key", Object.keys(stored).length === 1 && JSON.stringify(stored).indexOf("sk-or-demo") === -1);
ok("the key is still in the dialog", app.setup.apiKey === "sk-or-demo-key");

// ── A late answer about an earlier key is dropped ──
(async () => {
  let release;
  sandbox.fetch = () => new Promise((r) => { release = r; });
  app.setup = Object.assign(S.empty(), typed, { step: 3 });
  const late = app._setupPost("/api/providers/setup/models", {}, "find");
  app.setupKeyChanged();
  release({ ok: true, status: 200, json: async () => ({ models: [{ id: "m" }] }) });
  ok("a late answer is dropped", (await late) === null && app.setup.found === null && app.setup.busy === "");
  if (fail) { console.error(fail + " failed"); process.exit(1); }
})();
