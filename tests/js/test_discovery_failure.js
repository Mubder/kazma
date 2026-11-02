/**
 * A failed model discovery is shown as a failure (FA-107).
 * Run: node tests/js/test_discovery_failure.js
 *
 * The 2026-11 functional audit: discovery against a closed port answered the
 * list an earlier discovery had found, and Settings showed "2 models
 * discovered" as a success. The route now answers 502 {ok: false, error,
 * cached}; the page shows an error naming the reason, says the list is an
 * earlier one, and labels the provider's list (hubDiscoveryStale).
 * Negative control: the old page code, given the same 502, showed success.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const MIXINS = ["settings_core.js", "settings_hub.js", "settings_agent.js",
  "settings_integrations.js", "settings_ops.js"];

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

const FAILED = { status: 502, body: { ok: false, name: "p", error: "unreachable",
  detail: "The provider could not be reached (ConnectError).", cached: ["old-a", "old-b"], count: 0 } };
const FOUND = { status: 200, body: { ok: true, name: "p", models: ["m-1"], count: 1, speech_omitted: 0 } };

function load(answer, patch) {
  const toasts = [];
  const sb = { console, setTimeout, clearTimeout, setInterval, clearInterval, URL, URLSearchParams };
  sb.window = sb;
  sb.globalThis = sb;
  sb.showToast = (msg, type) => toasts.push({ msg: String(msg), type: type || "info" });
  sb.kazmaT = (key, en, vars) => fill(en, vars);
  sb.kazmaCount = (key, n) => String(n) + " models discovered";
  sb.t = (key) => key;
  sb.tOr = (key, fallback) => fallback;
  sb.kazmaSave = async () => ({ ok: true });
  sb.fetch = async (url) => {
    if (String(url).endsWith("/discover")) {
      return { ok: answer.status < 400, status: answer.status, json: async () => answer.body };
    }
    return { ok: true, status: 200, json: async () => ({ providers: [] }), text: async () => "{}" };
  };
  const el = { style: {}, setAttribute() {}, removeAttribute() {}, classList: { add() {}, remove() {}, toggle() {} } };
  sb.document = { documentElement: el, getElementById: () => null, querySelector: () => null, querySelectorAll: () => [] };
  sb.Alpine = { $data: () => null, store: () => ({}) };
  sb.ModelsManager = { refresh() {}, load() {}, setDefault() {} };
  sb.location = { href: "http://kazma.test/settings", search: "", pathname: "/settings", hash: "" };
  sb.history = { replaceState() {} };
  sb.localStorage = { getItem() { return null; }, setItem() {}, removeItem() {} };
  sb.matchMedia = () => ({ matches: false, addEventListener() {} });
  for (const f of MIXINS.concat(["settings.js"])) {
    let src = fs.readFileSync(path.join(jsDir, f), "utf8");
    if (patch && patch.file === f) src = patch.apply(src);
    vm.runInNewContext(src, sb, { filename: f });
  }
  const app = sb.settingsApp();
  app.loadHubProviders = async () => {};
  return { app, toasts };
}

/** The page's discover method as it was before FA-107. */
function oldDiscover(src) {
  const start = src.indexOf("        /** The page's words for a failed discovery's code (FA-107). */");
  const end = src.indexOf("        filteredDiscoveredModels(provider) {");
  if (start < 0 || end < 0) throw new Error("negative control: the method moved");
  return src.slice(0, start) + `        async discoverHubProvider(name) {
            this.hubDiscoveringProvider = name;
            try {
                const resp = await fetch(\`/api/providers/\${encodeURIComponent(name)}/discover\`, { method: 'POST' });
                const data = await resp.json();
                const count = data.count || 0;
                const speech = data.speech_omitted || 0;
                var msg = window.kazmaCount('settings.hub.models_discovered', count);
                if (speech) msg += window.kazmaCount('settings.hub.speech_models_hidden', speech);
                showToast(msg, count > 0 ? 'success' : 'warning');
                await this.loadHubProviders();
            } catch (e) {
                showToast(_k('settings.hub.discover_failed', 'Discover failed: ') + e.message, 'error');
            }
            this.hubDiscoveringProvider = null;
        },

` + src.slice(end);
}

(async () => {
  {
    const { app, toasts } = load(FAILED);
    await app.discoverHubProvider("p");
    const t = toasts[toasts.length - 1] || {};
    ok("a failed discovery is an error toast", t.type === "error", t);
    ok("it names the reason", /could not be reached/.test(t.msg), t.msg);
    ok("it says the list is an earlier one", /earlier discovery/.test(t.msg), t.msg);
    ok("the provider's list is labelled as earlier", app.hubDiscoveryStale.p === true, app.hubDiscoveryStale);
    ok("the button is released", app.hubDiscoveringProvider === null);
  }
  {
    const { app, toasts } = load({ status: 502, body: Object.assign({}, FAILED.body, { error: "http_401" }) });
    await app.discoverHubProvider("p");
    ok("an HTTP status is named", /HTTP 401/.test((toasts[toasts.length - 1] || {}).msg));
  }
  {
    const { app, toasts } = load(FOUND);
    app.hubDiscoveryStale = { p: true };
    await app.discoverHubProvider("p");
    const t = toasts[toasts.length - 1] || {};
    ok("a found list is a success", t.type === "success", t);
    ok("and no longer labelled earlier", app.hubDiscoveryStale.p === false);
  }
  {
    // Negative control: the old method, given the same failure, said success
    // with no reason -- the audit's "Found 2 models" shape (count 0 here: the
    // old route also answered count 2, from the cache).
    const { app, toasts } = load({ status: 200, body: { name: "p", models: ["old-a", "old-b"], count: 2 } },
      { file: "settings_hub.js", apply: oldDiscover });
    await app.discoverHubProvider("p");
    const t = toasts[toasts.length - 1] || {};
    ok("negative control: the old page called the cached list a success", t.type === "success", t);
  }
  if (fail) {
    console.error(`${fail} failure(s)`);
    process.exit(1);
  }
  console.log("all passed");
})();
