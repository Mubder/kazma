/**
 * A Settings save the server accepted never reports "Save failed".
 * Run: node tests/js/test_settings_saves_report_truth.js
 *
 * Found 2026-09-28 translating the Settings scripts: saveProxy's success
 * toast read an `I18N` global that only the Dashboard defines. On Settings
 * that line threw AFTER the proxy settings were saved, and the catch showed
 * "Save failed" -- the operator was told the opposite of what happened.
 *
 * Every save* method of the composed Settings component (settingsApp() over
 * the real mixin files) is run against a server that accepts everything. A
 * method may refuse to send (a required field is empty) -- those are listed
 * in VALIDATES, and must then not call the server at all. Every other one
 * must reach the server, must not throw, and must not show an error toast.
 * Negative control: the old proxy line is put back and must be caught.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const MIXINS = ["settings_core.js", "settings_hub.js", "settings_agent.js",
  "settings_integrations.js", "settings_ops.js"];

// Methods that check a required field first; with the component's initial
// (empty) data they refuse before any request, which is correct.
const VALIDATES = {
  saveModelProfile: "needs a profile name",
  saveHubProvider: "needs a name and a base URL",
  saveHubConnector: "needs a connector name",
  saveHubProfile: "needs a profile name",
  saveMcpServer: "needs a server name",
  saveGmailProtocol: "needs an email and an app password",
  saveMsProtocol: "needs an email and a password",
  saveGmailOAuthClient: "needs a Google client id and secret",
  saveGmail: "needs an email and an app password",
  saveMsClient: "needs an Azure client id",
};

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

function fill(text, vars) {
  let s = String(text);
  if (vars) for (const k of Object.keys(vars)) s = s.split("{" + k + "}").join(String(vars[k]));
  return s;
}

/** A Settings page in a sandbox, with *patch* applied to one file's source. */
function load(patch) {
  const toasts = [];
  const calls = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = (msg, type) => toasts.push({ msg: String(msg), type: type || "info" });
  sb.kazmaT = (key, en, vars) => fill(en, vars);
  // base.html's count helper; the catalog is not loaded here, so the number.
  sb.kazmaCount = (key, n) => String(n);
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  const answer = { ok: true, success: true, message: "", adapters_count: 0, adapters: [] };
  sb.fetch = async (url) => {
    calls.push(String(url));
    return { ok: true, status: 200, statusText: "OK", json: async () => answer, text: async () => "{}" };
  };
  sb.kazmaSave = async (url) => { calls.push(String(url)); return answer; };
  sb.kazmaConfirm = async () => true;
  sb.kazmaAlert = async () => undefined;
  sb.kazmaPrompt = async () => null;
  const el = { style: {}, setAttribute() {}, removeAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } };
  sb.document = {
    documentElement: el,
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  sb.Alpine = { $data: () => null, store: () => ({}) };
  sb.ModelsManager = { refresh() {}, load() {}, setDefault() {} };
  sb.location = { href: "http://kazma.test/settings", search: "", pathname: "/settings", hash: "" };
  sb.history = { replaceState() {} };
  sb.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  sb.matchMedia = () => ({ matches: false, addEventListener() {} });
  for (const f of MIXINS.concat(["settings.js"])) {
    let src = fs.readFileSync(path.join(jsDir, f), "utf8");
    if (patch && patch.file === f) {
      if (!src.includes(patch.from)) throw new Error("negative control: patch target moved in " + f);
      src = src.replace(patch.from, patch.to);
    }
    vm.runInNewContext(src, sb, { filename: f });
  }
  return { sb, toasts, calls };
}

/** Each save* method: what it showed and whether it reached the server. */
async function runSaves(patch, only) {
  const out = {};
  const first = load(patch).sb.settingsApp();
  const names = Object.keys(Object.getOwnPropertyDescriptors(first))
    .filter((k) => /^save/.test(k) && typeof first[k] === "function")
    .filter((k) => !only || only.includes(k));
  for (const name of names) {
    // A page of its own: a timer another save left running cannot call
    // the server during this one.
    const { sb, toasts, calls } = load(patch);
    const app = sb.settingsApp();
    let threw = null;
    let settled = false;
    try {
      await Promise.race([
        Promise.resolve(app[name]()).then(() => { settled = true; }),
        new Promise((r) => setTimeout(r, 3000)),
      ]);
    } catch (e) {
      settled = true;
      threw = String(e && e.message);
    }
    out[name] = {
      errors: toasts.filter((t) => t.type === "error").map((t) => t.msg),
      calledServer: calls.length > 0,
      settled,
      threw,
    };
  }
  return out;
}

(async () => {
  const results = await runSaves();
  const names = Object.keys(results);
  ok("the component has save methods (the scan works)", names.length >= 30, names);
  for (const name of Object.keys(VALIDATES)) {
    ok("the validating list names a real method: " + name, names.includes(name));
  }
  for (const name of names) {
    const r = results[name];
    if (VALIDATES[name]) {
      ok(name + " refuses before calling the server (" + VALIDATES[name] + ")",
        r.settled && !r.calledServer && !r.threw && r.errors.length > 0, r);
      continue;
    }
    ok(name + " reports what the server said", r.settled && !r.threw && r.errors.length === 0, r);
  }

  // Negative control: the 2026-09-28 proxy line, put back.
  const broken = await runSaves({
    file: "settings_agent.js",
    from: "showToast(_k('settings.proxy_saved', 'Proxy settings saved'), 'success');",
    to: "showToast(I18N?.proxy_saved || 'Proxy settings saved', 'success');",
  }, ["saveProxy"]);
  ok("negative control: a save whose success line throws is caught",
    broken.saveProxy && broken.saveProxy.errors.length > 0, broken.saveProxy);

  if (fail) {
    console.error(fail + " failure(s)");
    process.exit(1);
  }
  console.log("all ok");
})();
