/**
 * Settings' "Restart server" reloads the page onto the NEW server.
 * Run: node tests/js/test_settings_restart_reload.js
 *
 * The page reloaded as soon as /health/live answered after 3 s. Under the
 * guard (2026-09-30) the restart is a graceful reload: the old process
 * keeps answering while it drains, so the page reloaded onto the server
 * that was about to go away. It now reloads when a different process
 * answers (/health/live's build.started_at changes).
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

/** A page whose server answers /health/live from `lives` in order. */
function page(lives) {
  let now = 1_000_000;
  const calls = [];
  const state = { reloadedAfter: null, toasts: [] };
  const sandbox = {
    console, clearTimeout, setInterval, clearInterval,
    // Timers run at once and advance the page's clock by their delay.
    setTimeout(fn, ms) { now += ms || 0; Promise.resolve().then(fn); return 0; },
    Date: class extends Date { static now() { return now; } },
  };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  sandbox.showToast = (msg, kind) => state.toasts.push([kind, msg]);
  sandbox.kazmaConfirm = async () => true;
  sandbox.location = { reload() { state.reloadedAfter = calls.length; } };
  sandbox.fetch = async (url, opts) => {
    calls.push(url);
    if (url === "/api/settings/system/restart") {
      return { ok: true, json: async () => ({ status: "ok", via: "guard" }) };
    }
    const next = lives.shift();
    if (next === undefined || next === "down") throw new Error("connection refused");
    return { ok: true, json: async () => ({ status: "alive", build: { started_at: next } }) };
  };
  for (const f of MIXINS.concat(["settings.js"])) {
    vm.runInNewContext(fs.readFileSync(path.join(jsDir, f), "utf8"), sandbox, { filename: f });
  }
  return { app: sandbox.settingsApp(), calls, state };
}

async function settle(state) {
  for (let i = 0; i < 200 && state.reloadedAfter === null; i++) {
    await new Promise((r) => setImmediate(r));
  }
}

(async () => {
  // Before the restart, then the old process still answering twice while
  // it drains, then down, then the new one.
  const p = page([100, 100, 100, "down", 200]);
  await p.app.restartServer({ restartNeeded: () => true, setBusy: () => {} });
  await settle(p.state);
  ok("the page reloads", p.state.reloadedAfter !== null, p.calls);
  ok("only after a different process answered",
     p.state.reloadedAfter === p.calls.length && p.calls.filter((u) => u === "/health/live").length === 5,
     p.calls);

  // Negative control: without a start time the page falls back to what it
  // did for every restart before -- reload on any answer after 3 s -- and
  // reloads while the OLD process is still draining ("old" answers).
  const q = page([null, null, null, null, null, "down", 200]);
  await q.app.restartServer({ restartNeeded: () => true, setBusy: () => {} });
  await settle(q.state);
  ok("the time-based rule reloads onto the draining server (the bug)",
     q.state.reloadedAfter !== null && q.state.reloadedAfter < 6, q.calls.slice(0, 8));

  if (fail) {
    console.error(`${fail} failed`);
    process.exit(1);
  }
  console.log("all passed");
})();
