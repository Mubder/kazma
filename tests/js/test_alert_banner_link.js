/**
 * The system-alerts banner links only to this site's own pages.
 *
 * A model fallback banner (2026-09-25) carries "Open Providers" ->
 * /settings?tab=providers_connectors in its callback_id as "link:<path>".
 * The server drops any link that is not a local path; alertLink() drops it
 * again here, so a payload can never turn the banner button into an
 * off-site or javascript: link. Runs the REAL alertLink extracted from
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
const start = src.indexOf("export function systemAlertsBanner()");
const end = src.indexOf("\n}\n", start) + 3;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("systemAlertsBanner is extractable", start >= 0 && end > start);

// eslint-disable-next-line no-new-func
const systemAlertsBanner = new Function(
  "fetch", "showToast",
  src.slice(start, end).replace("export function", "function") + "\nreturn systemAlertsBanner;",
)(async () => ({ ok: false }), () => {});

function linkFor(callbackId) {
  const banner = systemAlertsBanner();
  banner.activeAlert = callbackId === undefined ? null : { callback_id: callbackId };
  return banner.alertLink();
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

process.exit(fail ? 1 : 0);
