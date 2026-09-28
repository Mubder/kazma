/**
 * Dates, times and numbers follow the page's language (2026-09-28).
 *
 * Pages formatted with the browser's own locale and wrote "ago", "msgs" and
 * "am" by hand, so an Arabic page showed English dates. Runs the REAL
 * locale_format.js with <html lang> set to each language.
 *
 * Run: node tests/js/test_locale_format.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "locale_format.js"), "utf8");

function load(lang) {
  const ctx = { document: { documentElement: { lang } }, Intl, Date, String, Number, isNaN, Math };
  ctx.window = ctx;
  vm.createContext(ctx);
  vm.runInContext(src, ctx);
  return ctx.KazmaFormat;
}

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail));
}

const ARABIC = /[؀-ۿ]/;
const now = Date.parse("2026-09-28T12:00:00Z");
const twoHoursAgo = "2026-09-28T10:00:00Z";

const en = load("en");
ok("English relative time", en.relative(twoHoursAgo, now) === "2 hours ago", en.relative(twoHoursAgo, now));
ok("epoch seconds are dates", en.relative(Date.parse(twoHoursAgo) / 1000, now) === "2 hours ago");
ok("an empty value is empty", en.relative("", now) === "" && en.dateTime(null) === "");
ok("a bad value is empty", en.dateTime("not a date") === "");

const ar = load("ar");
const arRel = ar.relative(twoHoursAgo, now);
ok("Arabic relative time is Arabic", ARABIC.test(arRel) && !/ago/.test(arRel), arRel);
ok("...with Latin digits like the rest of the page", !/[٠-٩]/.test(ar.dateTime(twoHoursAgo)), ar.dateTime(twoHoursAgo));
ok("Arabic date and time are Arabic", ARABIC.test(ar.dateTime(twoHoursAgo)), ar.dateTime(twoHoursAgo));
ok("numbers keep Latin digits", ar.number(1234) === "1,234" || ar.number(1234) === "1٬234", ar.number(1234));
ok("the locale says so", ar.locale() === "ar-u-nu-latn" && en.locale() === "en");

const regional = load("ar-KW");
ok("a regional tag reads as its language", regional.lang() === "ar");

// Counts take the catalog's plural forms (the Knowledge page read
// "4539 مقطع"; Arabic needs "4539 مقطعاً"). Same rule as i18n.t_plural.
const chunksAr = { zero: "لا توجد مقاطع", one: "مقطع واحد", two: "مقطعان",
  few: "{n} مقاطع", many: "{n} مقطعاً", other: "{n} مقطع" };
const arF = load("ar");
const cases = [[0, "لا توجد مقاطع"], [1, "مقطع واحد"], [2, "مقطعان"], [5, "5 مقاطع"],
  [15, "15 مقطعاً"], [100, "100 مقطع"], [4539, "4539 مقطعاً"], [103, "103 مقاطع"]];
for (const [n, want] of cases) {
  ok("Arabic count " + n, arF.count(chunksAr, n) === want, arF.count(chunksAr, n));
}
ok("English count", en.count({ one: "1 chunk", other: "{n} chunks" }, 4539) === "4539 chunks");
ok("English one", en.count({ one: "1 chunk", other: "{n} chunks" }, 1) === "1 chunk");
ok("missing forms fall back", arF.count(null, 7, "{n} items") === "7 items");
// Negative control: the old hand-built label is the wrong form for 4539.
ok("control: noun + number was ungrammatical", "4539 " + "مقطع" !== arF.count(chunksAr, 4539));

// Negative control: the browser's own locale (what pages used) is English
// whatever the page's language.
ok("control: toLocaleString ignores the page language",
  !ARABIC.test(new Date(twoHoursAgo).toLocaleString("en-US")));

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
