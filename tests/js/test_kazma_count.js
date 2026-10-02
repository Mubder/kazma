/**
 * window.kazmaCount (base.html) against the shared fixture: the catalog's
 * forms of a key, the one KazmaFormat.pluralCategory picks -- six in Arabic.
 * Run: node tests/js/test_kazma_count.js
 *
 * Settings -> MCP printed "1 أدوات" (2026-10-02): a number glued to a word in
 * one form, (s.tool_count || 0) + ' أدوات'. Python checks the fixture against
 * the catalog and t_plural (tests/test_kazma_count.py), so a script's count
 * reads the same as a template's t_plural.
 *
 * Runs the REAL function extracted from base.html, with the REAL
 * locale_format.js for the plural rule. Negative control: the glued form.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const UI = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui");
const base = fs.readFileSync(path.join(UI, "templates", "base.html"), "utf8").replace(/\r\n/g, "\n");
const localeFormat = fs.readFileSync(path.join(UI, "static", "js", "locale_format.js"), "utf8");
const fixture = JSON.parse(fs.readFileSync(path.join(
  __dirname, "..", "fixtures", "i18n", "kazma_count.json"), "utf8"));

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

const start = base.indexOf("        window.kazmaCount = function(");
const end = base.indexOf("\n        };\n", start) + "\n        };\n".length;
ok("kazmaCount is extractable from base.html", start > 0 && end > start);
const kazmaCountSrc = base.slice(start, end);

function load(lang) {
  // As in a browser: the page's language on <html lang>, which
  // locale_format.js reads through window.document.
  const document = { documentElement: { lang: lang, getAttribute: () => lang } };
  const window = { KAZMA_I18N: fixture.catalog, KAZMA_LANG: lang, document };
  const ctx = { window, document, globalThis: window, Intl, Math, Number, String };
  vm.createContext(ctx);
  vm.runInContext(localeFormat, ctx);
  if (!window.KazmaFormat && ctx.KazmaFormat) window.KazmaFormat = ctx.KazmaFormat;
  vm.runInContext(kazmaCountSrc, ctx);
  return window;
}

const byLang = { en: load("en"), ar: load("ar") };
ok("the real plural rule is loaded", !!byLang.ar.KazmaFormat && byLang.ar.KazmaFormat.pluralCategory(11) === "many");
for (const c of fixture.cases) {
  const got = byLang[c.lang].kazmaCount(c.key, c.n, c.vars);
  ok(`${c.lang} ${c.key} ${c.n}`, got === c.expect, JSON.stringify(got) + " != " + JSON.stringify(c.expect));
}

// A key the catalog does not have still shows the number, never the key.
ok("an unknown key shows the number", byLang.ar.kazmaCount("no.such.count", 7) === "7");

// Negative control: the shipped glued label, one Arabic form for every count.
const glued = (n) => n + " " + "أدوات";
const arOne = fixture.cases.find((c) => c.lang === "ar" && c.key === "mcp.tool_count" && c.n === 1);
ok("negative control: the glued label reads 1 with the plural of 3-10",
  glued(1) !== arOne.expect && glued(1) === "1 أدوات");

if (fail) { console.log(fail + " failure(s)"); process.exit(1); }
console.log("all ok");
