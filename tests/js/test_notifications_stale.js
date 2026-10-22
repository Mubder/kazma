/**
 * "Alerts could not be refreshed" means the alerts really could not be read,
 * not that one request was lost.
 *
 * On the live install about 1 % of the notifications store's polls never
 * reached the server (behind a tunnel), while the server answered every one
 * that did with 200; the banner turned on at each lost poll and off at the
 * next, a minute later in a background tab, so it came and went all day
 * (2026-10-22). Now a failed read is tried again after 5 s, and the banner
 * shows only when that fails too. Runs the REAL store literal from
 * modules/stores.js; the old one-failure rule is the negative control.
 *
 * Run: node tests/js/test_notifications_stale.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "stores.js"),
  "utf8").replace(/\r\n/g, "\n");
const open = "Alpine.store('notifications', ";
const at = src.indexOf(open);
const end = src.indexOf("\n        });", at);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("the notifications store is extractable", at >= 0 && end > at);
const literal = src.slice(at + open.length, end + "\n        }".length);

// The old store: one failed read set the banner.
const OLD = `({
  items: [], count: 0, loaded: false, error: '',
  init() {},
  async refresh() {
    try {
      const res = await fetch('/api/alerts/recent');
      if (!res.ok) { this.error = 'alerts unavailable'; return; }
      const alerts = await res.json();
      this.items = alerts; this.count = alerts.length; this.loaded = true; this.error = '';
    } catch (e) { this.error = 'alerts unavailable'; }
  },
})`;

function build(text, answers) {
  const timers = [];
  const listeners = {};
  const doc = {
    hidden: false,
    addEventListener(name, fn) { listeners[name] = fn; },
  };
  let calls = 0;
  const fetch = async () => {
    const a = answers[Math.min(calls, answers.length - 1)];
    calls += 1;
    if (a === "lost") throw new TypeError("Failed to fetch");
    if (a === 502) return { ok: false, status: 502, json: async () => ({}) };
    return { ok: true, status: 200, json: async () => [{ id: "a" }] };
  };
  // eslint-disable-next-line no-new-func
  const store = new Function(
    "fetch", "setTimeout", "clearTimeout", "setInterval", "document",
    "return (" + text + ");",
  )(fetch,
    (fn, ms) => { timers.push({ fn, ms }); return timers.length; },
    () => {},
    () => 0,
    doc);
  return { store, timers, listeners, doc, calls: () => calls };
}

async function scenario(text, answers) {
  const t = build(text, answers);
  await t.store.refresh();
  return t;
}

(async () => {
  // One lost request: no banner, and one quick retry, which clears it.
  {
    const t = await scenario(literal, ["lost", 200]);
    ok("one lost request shows no banner", t.store.error === "", t.store.error);
    ok("a lost request is retried after 5 s",
      t.timers.length === 1 && t.timers[0].ms === 5000, JSON.stringify(t.timers.map((x) => x.ms)));
    t.timers[0].fn();
    await new Promise((r) => setImmediate(r));
    ok("the retry reads the alerts", t.store.loaded && t.store.count === 1 && t.store.error === "");
  }
  // The control: the old store showed the banner on one lost request.
  {
    const t = await scenario(OLD, ["lost"]);
    ok("negative control: the old rule showed the banner on one lost request",
      t.store.error === "alerts unavailable", t.store.error);
  }
  // Two failures in a row: the alerts really could not be read.
  {
    const t = await scenario(literal, ["lost", 502]);
    t.timers[0].fn();
    await new Promise((r) => setImmediate(r));
    ok("a failed retry shows the banner", t.store.error === "alerts unavailable", t.store.error);
    ok("only one retry is pending at a time", t.timers.length === 2, t.timers.length);
  }
  // An error status counts the same as a lost request.
  {
    const t = await scenario(literal, [502]);
    ok("one error status shows no banner", t.store.error === "");
  }
  // Recovery clears the banner and the count.
  {
    const t = await scenario(literal, ["lost", "lost", 200]);
    t.timers[0].fn();
    await new Promise((r) => setImmediate(r));
    ok("still failing: the banner shows", t.store.error === "alerts unavailable");
    await t.store.refresh();
    ok("the next good read clears it", t.store.error === "" && t.store._failures === 0);
  }
  // A background tab reads again the moment it is looked at.
  {
    const t = build(literal, [200]);
    t.store.init();
    await new Promise((r) => setImmediate(r));
    const before = t.calls();
    ok("init listens for the tab being shown", typeof t.listeners.visibilitychange === "function");
    t.doc.hidden = true;
    t.listeners.visibilitychange();
    ok("hidden: no extra read", t.calls() === before);
    t.doc.hidden = false;
    t.listeners.visibilitychange();
    await new Promise((r) => setImmediate(r));
    ok("shown: read at once", t.calls() === before + 1, t.calls() - before);
  }

  if (fail) {
    console.log(fail + " failed");
    process.exit(1);
  }
  console.log("all passed");
})();
