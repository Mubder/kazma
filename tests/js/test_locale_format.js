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

// A stored week and month are named from the key, in UTC. Week 2026-W23
// is Monday 1 June. The instant a Kuwait server stored for that midnight
// is 31 May 21:00 UTC, which a UTC clock reads as May.
const KUWAIT_JUNE = "2026-05-31T21:00:00Z";
ok("control: the Kuwait instant is May in UTC",
  new Intl.DateTimeFormat("en", { month: "long", year: "numeric", timeZone: "UTC" }).format(new Date(KUWAIT_JUNE))
    === "May 2026");
ok("week 23 is 1 June, not May", en.weekOfKey("2026-W23") === "Jun 1, 2026", en.weekOfKey("2026-W23"));
ok("June's key is June", en.monthOfKey("2026-06") === "June 2026", en.monthOfKey("2026-06"));
ok("week 40 starts in September", en.monthOfKey("2026-W40") === "September 2026", en.monthOfKey("2026-W40"));
ok("week 40 ends in October", en.monthOfKey("2026-W40", "end") === "October 2026", en.monthOfKey("2026-W40", "end"));
ok("week 1 of 2026 is 29 December 2025", en.weekOfKey("2026-W01") === "Dec 29, 2025", en.weekOfKey("2026-W01"));
ok("a week the year does not have is empty", en.weekOfKey("2025-W53") === "" && en.monthOfKey("2026-W00") === "");
ok("a month number and a project key are empty",
  en.monthOfKey("2026-13") === "" && en.weekOfKey("project:abcd") === "" && en.monthOfKey("2026-W54") === "");
const arWeek = ar.weekOfKey("2026-W23");
ok("Arabic week 23 is June's digits", arWeek.indexOf("06") !== -1 && arWeek.indexOf("05") === -1, arWeek);
const arJune = ar.monthOfKey("2026-06");
ok("Arabic June is يونيو", arJune.indexOf("يونيو") !== -1 && arJune.indexOf("مايو") === -1, arJune);
const arEnd = ar.monthOfKey("2026-W40", "end");
ok("Arabic week 40 ends in أكتوبر", arEnd.indexOf("أكتوبر") !== -1 && arEnd.indexOf("سبتمبر") === -1, arEnd);
const byKeys = en.projectMonths(["2026-W23", "2026-W40"], ["2026-01", "2026-02"]);
ok("a project's weeks name it", byKeys.from === "June 2026" && byKeys.to === "October 2026",
  JSON.stringify(byKeys));
const byStored = en.projectMonths([], ["2026-06", "2026-09"]);
ok("no week parts fall back to the stored months",
  byStored.from === "June 2026" && byStored.to === "September 2026", JSON.stringify(byStored));
const blank = en.projectMonths([], []);
ok("no keys and no months are blank", blank.from === "" && blank.to === "");
const arStored = ar.projectMonths(null, ["2026-06", "2026-09"]);
ok("Arabic stored months are يونيو to سبتمبر",
  arStored.from.indexOf("يونيو") !== -1 && arStored.to.indexOf("سبتمبر") !== -1, JSON.stringify(arStored));

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
