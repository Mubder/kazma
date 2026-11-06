/**
 * A switched-off provider shows "Off" and nothing else (resilience plan I).
 * Run: node tests/js/test_provider_off_state.js
 *
 * The live Providers page showed old states on providers the owner had
 * switched off: a stored "down", a test from before the switch. A provider
 * Kazma does not use has no health to show. Negative control: the same
 * provider switched on keeps its stored state.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "providers.js"), "utf8");
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const PM = sandbox.window.ProvidersManager;

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

const off = { name: "zai", enabled: false, health: "down" };
const failedTest = { models_ok: false, chat_ok: false, error: "refused" };
ok("an off provider is off", PM.cardState(off, null) === "off");
ok("an earlier test does not show on an off provider", PM.cardState(off, failedTest) === "off");
ok("its label is Off", PM.stateLabel("off") === "Off");
ok("no checks are shown", PM.checksFor(failedTest, "down", false).length === 0);
ok("no stored detail", PM.storedDetail(off) === "");

const on = { name: "zai", enabled: true, health: "down" };
ok("control: switched on, its stored state shows", PM.cardState(on, null) === "unreachable");
ok("control: switched on, its checks show", PM.checksFor(null, "down", true).length > 0);

if (fail) {
  console.error(`${fail} failed`);
  process.exit(1);
}
console.log("all passed");
