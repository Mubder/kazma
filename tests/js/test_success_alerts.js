/**
 * A success is news, not a problem (live 2026-10-31: "Crawl completed" held
 * the yellow warning banner on every page).
 *
 * The notifications store shows a recent SUCCESS alert once, as a green toast
 * that fades, remembered per browser; an old one stays in the bell only. The
 * banner never shows a success, and an error is red. Runs the REAL store
 * literal from modules/stores.js and the REAL banner from components.js.
 *
 * Run: node tests/js/test_success_alerts.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules");
const stores = fs.readFileSync(path.join(root, "stores.js"), "utf8").replace(/\r\n/g, "\n");
const components = fs.readFileSync(path.join(root, "components.js"), "utf8").replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

const open = "Alpine.store('notifications', ";
const at = stores.indexOf(open);
const end = stores.indexOf("\n        });", at);
ok("the notifications store is extractable", at >= 0 && end > at);
const literal = stores.slice(at + open.length, end + "\n        }".length);

function world(saved) {
  const toasts = [];
  const storage = {
    getItem: (k) => (k in saved ? saved[k] : null),
    setItem: (k, v) => { saved[k] = String(v); },
  };
  const win = {
    localStorage: storage,
    kazmaT: (key, english) => english,
    location: { href: "" },
    Alpine: { store: (name) => (name === "toast" ? { add: (...a) => toasts.push(a) } : null) },
  };
  return { win, toasts };
}

function storeFor(win, alerts) {
  const fetch = async () => ({ ok: true, status: 200, json: async () => alerts });
  // eslint-disable-next-line no-new-func
  return new Function("fetch", "setTimeout", "clearTimeout", "setInterval", "document", "window",
    "return (" + literal + ");")(fetch, () => 0, () => {}, () => 0, { addEventListener() {} }, win);
}

const now = Date.now() / 1000;
const success = { id: "s1", title: "Crawl completed", severity: "SUCCESS", timestamp: now - 30,
                  callback_id: "link:/knowledge?job=docs%3Ar2" };
const oldSuccess = { id: "s0", title: "Crawl completed", severity: "SUCCESS", timestamp: now - 7200 };
const failure = { id: "f1", title: "Crawl failed", severity: "ERROR", timestamp: now - 60 };

(async () => {
  const saved = {};
  {
    const { win, toasts } = world(saved);
    const store = storeFor(win, [oldSuccess, success, failure]);
    await store.refresh();
    ok("a recent success is a green toast", toasts.length === 1 && toasts[0][1] === "success"
      && toasts[0][0] === "Crawl completed", JSON.stringify(toasts));
    ok("its toast opens the run", toasts[0][3] && toasts[0][3].label === "Open");
    toasts[0][3].run();
    ok("to the run's page", win.location.href === "/knowledge?job=docs%3Ar2", win.location.href);
    await store.refresh();
    ok("once per page", toasts.length === 1, toasts.length);
    ok("the bell still lists all three", store.items.length === 3);
  }
  {
    const { win, toasts } = world(saved);  // a reload: the same browser's storage
    await storeFor(win, [oldSuccess, success, failure]).refresh();
    ok("once per browser across a reload", toasts.length === 0, toasts.length);
  }
  {
    const { win, toasts } = world({});
    const store = storeFor(win, [failure, { id: "i1", severity: "INFO", timestamp: now }]);
    await store.refresh();
    ok("a failure or info is never a success toast", toasts.length === 0);
    ok("symbols by severity", store.tone(success).mark === "✓" && store.tone(failure).mark === "✕"
      && store.tone({ severity: "WARNING" }).mark === "⚠");
  }

  // The banner: never a success; an error red, a warning yellow.
  const start = components.indexOf("function alertsStore()");
  const bannerAt = components.indexOf("export function systemAlertsBanner()");
  const stop = components.indexOf("\n}\n", bannerAt) + 3;
  const items = [];
  const win = { Alpine: { store: () => ({ items }) } };
  // eslint-disable-next-line no-new-func
  const banner = new Function("fetch", "showToast", "window",
    components.slice(start, stop).replace("export function", "function") + "\nreturn systemAlertsBanner;",
  )(async () => ({}), () => {}, win);
  items.push(success);
  ok("a success never takes the banner", banner().activeAlert === null);
  items.push(failure);
  const b = banner();
  ok("an error takes it, red", b.activeAlert.id === "f1" && b.bannerColor() === "var(--danger)"
    && b.bannerTone().borderLeft === "4px solid var(--danger)");
  items.length = 0;
  items.push({ id: "w1", severity: "WARNING", timestamp: 1 });
  ok("a warning is yellow", banner().bannerColor() === "var(--warning)");

  process.exit(fail ? 1 : 0);
})();
