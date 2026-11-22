/**
 * The Settings component keeps its mixins' getters live.
 * Run: node tests/js/test_settings_mixins.js
 *
 * settingsApp() (static/js/settings.js) composes five tab mixins. It used
 * Object.assign, which READS every getter once -- on the bare mixin, before
 * any data exists -- and stores the result as a plain value. Found touring
 * the live install's pages, 2026-09-26: `filteredPkgCore` froze as
 * undefined, so the Packages tab threw on load and never listed anything,
 * and the Skills and Tools searches, the timezone lists and the offsite
 * backup label never recomputed. The real mixin files are loaded here and
 * composed by the real settingsApp().
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");
const MIXINS = ["settings_core.js", "settings_hub.js", "settings_agent.js",
  "settings_integrations.js", "settings_ops.js", "settings_alerts.js"];

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

function load() {
  const sandbox = { console, setTimeout, clearTimeout, setInterval, clearInterval };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  for (const f of MIXINS.concat(["settings.js"])) {
    vm.runInNewContext(fs.readFileSync(path.join(jsDir, f), "utf8"), sandbox, { filename: f });
  }
  return sandbox;
}

/** Every accessor any mixin declares, by name. */
function mixinGetters(sandbox) {
  const names = [];
  for (const key of Object.keys(sandbox.KazmaSettingsMixins)) {
    const part = sandbox.KazmaSettingsMixins[key]();
    for (const [name, d] of Object.entries(Object.getOwnPropertyDescriptors(part))) {
      if (typeof d.get === "function") names.push(name);
    }
  }
  return names;
}

const sb = load();
const getters = mixinGetters(sb);
ok("the mixins declare getters (the scan works)", getters.length >= 5, getters);

const app = sb.settingsApp();
for (const name of getters) {
  const d = Object.getOwnPropertyDescriptor(app, name);
  ok(`${name} is still a getter on the component`, !!(d && typeof d.get === "function"), d);
}

// Behaviour: the Packages filter reads the component's live data.
app.pkgCore = [{ name: "httpx", description: "HTTP client" }, { name: "numpy", description: "arrays" }];
app.pkgSearch = "";
ok("filteredPkgCore lists every package with no search", app.filteredPkgCore.length === 2, app.filteredPkgCore);
app.pkgSearch = "http";
ok("filteredPkgCore filters on the search", app.filteredPkgCore.length === 1
  && app.filteredPkgCore[0].name === "httpx", app.filteredPkgCore);

// Methods still see the composed component, not the mixin they came from.
ok("init is wrapped and callable", typeof app.init === "function");
ok("conflictLine reads the conflict", app.conflictLine({ action1: "a", action2: "b", keys: "Ctrl+K" })
  .indexOf('"a"') === 0, app.conflictLine({ action1: "a", action2: "b", keys: "Ctrl+K" }));

// Negative control: the merge that shipped freezes the getters.
{
  const parts = Object.keys(sb.KazmaSettingsMixins).map((k) => sb.KazmaSettingsMixins[k]());
  let frozen;
  try {
    frozen = Object.assign.apply(Object, [{}].concat(parts));
  } catch (e) {
    frozen = null;
  }
  const d = frozen && Object.getOwnPropertyDescriptor(frozen, "filteredPkgCore");
  ok("negative control: Object.assign leaves no getter", !frozen || !(d && d.get), d);
}

// ── Every member is defined once across the mixins (Spaces S2-01) ──────
// A second definition of a name wins silently: in one object literal the
// last key wins, across mixins the later mixin does. People's per-person
// tool box was named `toggleTool`, the Tools tab's switch's name: the box
// called the Tools tab's switch with a person object, and the PUT went to
// /api/settings/tools/[object Object]/toggle.
const KEYWORDS = new Set(["if", "for", "while", "switch", "return", "catch", "function", "else",
  "try", "do", "new", "var", "let", "const", "await", "throw", "typeof", "case", "default"]);

/** Every member a mixin defines, read from its source: a name at the
 * mixin object's own indentation (eight spaces), followed by `(` or `:`. */
function memberDefinitions(sources) {
  const seen = new Map();
  for (const [file, text] of sources) {
    text.split(/\r?\n/).forEach((line, i) => {
      const m = /^ {8}(?:async\s+)?(?:get\s+|set\s+)?([A-Za-z_$][\w$]*)\s*(?:\(|:)/.exec(line);
      if (!m || KEYWORDS.has(m[1]) || /^ {8}(?:\/\/|\*|\/\*)/.test(line)) return;
      const accessor = /^ {8}(?:get|set)\s/.test(line) ? line.trim().slice(0, 3) : "";
      const key = m[1] + (accessor ? " " + accessor : "");
      if (!seen.has(key)) seen.set(key, []);
      seen.get(key).push(`${file}:${i + 1}`);
    });
  }
  return seen;
}

function twice(seen) {
  return [...seen.entries()].filter(([, where]) => where.length > 1);
}

const sources = MIXINS.map((f) => [f, fs.readFileSync(path.join(jsDir, f), "utf8")]);
const defined = memberDefinitions(sources);
ok("the scan finds the mixins' members", defined.has("togglePersonTool") && defined.has("toggleTool")
  && defined.size > 300, defined.size);
ok("no member of the Settings mixins is defined twice", twice(defined).length === 0, twice(defined));
{
  // Negative control: the S2-01 draft, a second `toggleTool` in settings_ops.js.
  const planted = sources.map(([f, text]) => [f, f === "settings_ops.js"
    ? text.replace(/^( {8}(?:async\s+)?)togglePersonTool\(/m, "$1toggleTool(")
    : text]);
  const found = twice(memberDefinitions(planted)).map(([name]) => name);
  ok("negative control: a planted second toggleTool is found", found.length === 1 && found[0] === "toggleTool", found);
}

process.exit(fail ? 1 : 0);
