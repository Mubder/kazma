/**
 * chat.js tiCount against the shared fixture (Python reads the same file:
 * tests/test_count_labels.py). Runs the REAL _pluralCategory and tiCount
 * extracted from chat.js, with the page language and the catalog forms the
 * template would have injected.
 */
"use strict";

const fs = require("fs");
const path = require("path");

// LF-normalised: a Windows checkout has CRLF, and the end marker below is
// written with \n.
const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  function _pluralCategory(");
const tiCountAt = src.indexOf("  function tiCount(", start);
const end = src.indexOf("\n  }\n", tiCountAt) + 4;
const fixture = JSON.parse(fs.readFileSync(path.join(
  __dirname, "..", "fixtures", "i18n", "count_labels.json"), "utf8"));

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}
ok("tiCount is extractable", start > 0 && tiCountAt > start && end > tiCountAt);

function load(lang) {
  const document = { documentElement: { getAttribute: () => lang } };
  const window = { CHAT_I18N: { plural: fixture.forms[lang] } };
  // eslint-disable-next-line no-new-func
  return new Function("document", "window",
    src.slice(start, end) + "\nreturn tiCount;")(document, window);
}

for (const c of fixture.cases) {
  const tiCount = load(c.lang);
  const got = tiCount(c.base, c.n, "{n} x", "{n} xs");
  ok(`${c.lang} ${c.base} ${c.n}`, got === c.expect, JSON.stringify(got));
}

// No catalog forms injected (a template that predates them): English
// fallbacks still pluralise.
{
  const document = { documentElement: { getAttribute: () => "en" } };
  // eslint-disable-next-line no-new-func
  const tiCount = new Function("document", "window",
    src.slice(start, end) + "\nreturn tiCount;")(document, {});
  ok("fallback singular", tiCount("count_tools", 1, "{n} tool", "{n} tools") === "1 tool");
  ok("fallback plural", tiCount("count_tools", 4, "{n} tool", "{n} tools") === "4 tools");
}

process.exit(fail ? 1 : 0);
