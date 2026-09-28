/**
 * The Research page tags a session by where it came from.
 *
 * Live 2026-09-28: a one-line web search from the chat (recorded as session
 * rs_chat_...) was listed as "[Deep] latest stable Python release version",
 * like a deep research run. Runs the REAL sessionTag from research.js.
 *
 * Run: node tests/js/test_research_session_tag.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "research.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  var SESSION_TAGS = {");
const end = src.indexOf("  window.KazmaResearch = {", start);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("sessionTag is extractable", start > 0 && end > start);

function load(t) {
  // eslint-disable-next-line no-new-func
  return new Function("window", src.slice(start, end) + "\nreturn sessionTag;")({ t: t });
}

const english = load(undefined);
ok("a brief run is Brief", english({ id: "rs_1", depth: "brief" }) === "[Brief] ");
ok("a deep run is Deep", english({ id: "rs_2", depth: "deep" }) === "[Deep] ");
ok("a chat search is Chat", english({ id: "rs_chat_3", depth: "chat" }) === "[Chat] ");
ok("an old row without depth: chat by its id", english({ id: "rs_chat_0123456789ab" }) === "[Chat] ");
ok("an old row without depth: otherwise Deep", english({ id: "rs_0123456789abcdef" }) === "[Deep] ");
ok("no id is not a chat search", english({}) === "[Deep] ");

const arabic = load(function (k) {
  return { "research.source_chat": "محادثة", "research.depth_deep": "عميق", "research.depth_brief": "مختصر" }[k] || k;
});
ok("the tag follows the page language", arabic({ id: "rs_chat_x", depth: "chat" }) === "[محادثة] ");
ok("...for deep runs too", arabic({ id: "rs_x", depth: "deep" }) === "[عميق] ");
ok("...and brief ones", arabic({ id: "rs_y", depth: "brief" }) === "[مختصر] ");

const untranslated = load(function (k) { return k; });
ok("a missing translation falls back to English", untranslated({ id: "rs_x", depth: "brief" }) === "[Brief] ");

// Negative control: every session was tagged "[Deep] " until 2026-09-28.
const old = function () { return "[Deep] "; };
ok("control: the old tag called a Brief run Deep", old({ id: "rs_x", depth: "brief" }) === "[Deep] ");

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
