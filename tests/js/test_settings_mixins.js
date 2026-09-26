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

process.exit(fail ? 1 : 0);
