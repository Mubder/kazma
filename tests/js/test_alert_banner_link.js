/**
 * The system-alerts banner links only to this site's own pages, and reads
 * the notifications store instead of polling on its own.
 *
 * A model fallback banner (2026-09-25) carries "Open Providers" ->
 * /settings?tab=providers_connectors in its callback_id as "link:<path>".
 * The server drops any link that is not a local path; alertLink() drops it
 * again here, so a payload can never turn the banner button into an
 * off-site or javascript: link.
 *
 * The banner also polled /api/alerts/recent itself, every 10 s beside the
 * notifications store's 15 s, on every page (2026-09-26). It now derives the
 * newest undismissed alert from the store. Runs the REAL code extracted from
 * modules/components.js.
 *
 * Run: node tests/js/test_alert_banner_link.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

// LF-normalised: a Windows checkout has CRLF.
const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "components.js"),
  "utf8").replace(/\r\n/g, "\n");
// The banner and the store accessor it reads (defined just above it).
const start = src.indexOf("function alertsStore()");
const bannerAt = src.indexOf("export function systemAlertsBanner()");
const end = src.indexOf("\n}\n", bannerAt) + 3;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("systemAlertsBanner is extractable", start >= 0 && bannerAt > start && end > bannerAt);

// The banner reads the notifications store; it must never fetch on its own.
const fetched = [];
const store = { items: [], refreshes: 0, refresh() { this.refreshes += 1; } };
const windowStub = { Alpine: { store: (name) => (name === "notifications" ? store : null) } };
// eslint-disable-next-line no-new-func
const systemAlertsBanner = new Function(
  "fetch", "showToast", "window",
  src.slice(start, end).replace("export function", "function") + "\nreturn systemAlertsBanner;",
)(async (url) => { fetched.push(url); return { ok: false }; }, () => {}, windowStub);

function linkFor(callbackId) {
  store.items = callbackId === undefined ? [] : [{ id: "a1", timestamp: 1, callback_id: callbackId }];
  return systemAlertsBanner().alertLink();
}

ok("a local path is kept",
  linkFor("link:/settings?tab=providers_connectors") === "/settings?tab=providers_connectors",
  linkFor("link:/settings?tab=providers_connectors"));
ok("a protocol-relative link is dropped", linkFor("link://evil.example/x") === "");
ok("an absolute URL is dropped", linkFor("link:https://evil.example/") === "");
ok("javascript: is dropped", linkFor("link:javascript:alert(1)") === "");
ok("the install button id is not a link", linkFor("sentence-transformers") === "");
ok("an empty id is not a link", linkFor("") === "");
ok("no alert, no link", linkFor(undefined) === "");
ok("a non-string id is not a link", linkFor(42) === "");

// One poller per page: the store's. The banner shows the newest alert the
// reader has not dismissed, and asks the store (not the network) to refresh.
{
  store.items = [
    { id: "old", timestamp: 1, title: "old" },
    { id: "new", timestamp: 5, title: "new" },
  ];
  const banner = systemAlertsBanner();
  ok("the newest alert shows", banner.activeAlert && banner.activeAlert.id === "new", banner.activeAlert);
  banner.dismissAlert();
  ok("a dismissed alert gives way to the next", banner.activeAlert && banner.activeAlert.id === "old");
  banner.dismissAlert();
  ok("all dismissed, nothing shows", banner.activeAlert === null);
  banner.refreshAlerts();
  ok("a refresh goes through the store", store.refreshes === 1, store.refreshes);
  ok("the banner never fetches by itself", fetched.length === 0, fetched);
  ok("the banner has no poller of its own", typeof banner.init !== "function" && !("pollInterval" in banner));
}

process.exit(fail ? 1 : 0);
