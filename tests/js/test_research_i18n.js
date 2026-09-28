/**
 * The Research page's i18n() returns text, never a catalog entry.
 * Run: node tests/js/test_research_i18n.js
 *
 * research.js read window.KAZMA_I18N[key] as it was. research.html merges
 * flat, already-translated strings (research_*) into it, so those worked; a
 * dotted catalog key is an {en, ar} object there, and the Compare view
 * printed "[object Object]" for its Metric / Run A / Run B / Delta headings
 * (found 2026-09-28). A missing key came back as the key itself, so the
 * `i18n(k) || 'English'` fallbacks never applied.
 *
 * The function is taken from the real research.js source and run against
 * the three shapes. Negative control: the old one-liner fails the same checks.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

// The checkout may hold CRLF line endings; the blob is LF.
const src = fs.readFileSync(path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "research.js"), "utf8")
  .replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

/** The page's i18n function from *code*, bound to a window in *lang*. */
function build(code, lang) {
  const window = {
    KAZMA_LANG: lang,
    KAZMA_I18N: {
      "research.metric": { en: "Metric", ar: "المقياس" },
      research_cancelled: "أُلغي البحث",
    },
  };
  const sandbox = { window };
  vm.runInNewContext(code + "\nthis.i18n = i18n;", sandbox);
  return sandbox.i18n;
}

function extract(text) {
  const start = text.indexOf("  function i18n(");
  if (start < 0) throw new Error("research.js has no i18n() any more");
  // The function ends at the first line that closes it at its own indent.
  const end = text.indexOf("\n  }\n", start);
  const oneLine = text.indexOf("\n", start);
  const body = text.slice(start, oneLine).trim().endsWith("}") ? text.slice(start, oneLine) : text.slice(start, end + 4);
  return body;
}

function check(label, i18n, expectPass) {
  const results = {
    catalog: i18n("research.metric", "Metric"),
    flat: i18n("research_cancelled", "Research cancelled"),
    missing: i18n("research.no_such_key", "Fallback text"),
    vars: typeof i18n.length === "number" && i18n.length >= 3
      ? i18n("research.no_such_key", "Exported: {name}", { name: "a.md" }) : "Exported: a.md",
  };
  const pass = results.catalog === "المقياس" && results.flat === "أُلغي البحث" &&
    results.missing === "Fallback text" && results.vars === "Exported: a.md";
  ok(label, pass === expectPass, results);
}

check("the page's i18n resolves a catalog entry, a flat string and a missing key", build(extract(src), "ar"), true);
check("negative control: the old one-liner fails",
  build("function i18n(key) { return (window.KAZMA_I18N && window.KAZMA_I18N[key]) || key; }", "ar"), false);

if (fail) {
  console.error(fail + " failure(s)");
  process.exit(1);
}
console.log("all ok");
