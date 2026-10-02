/**
 * Each paragraph takes its direction from its own words (KazmaBidi.blockDir),
 * whatever the UI's language -- the owner's rule, 2026-10-02 -- and the
 * markdown renderer gives every block that direction.
 *
 * dir="auto" asks only a paragraph's first letter, so an Arabic sentence that
 * opens with an English word ran left-to-right; counting letters
 * (isArabicDominant) does the same to an Arabic sentence holding one long
 * path. Negative controls: both old rules get those sentences wrong.
 *
 * The browser half (tests/e2e/test_text_follows_its_language.py) measures
 * where the lines of real turns sit, in both UIs.
 *
 * Run: node tests/js/test_block_direction.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "..");
const JS = path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js");

global.window = global;
global.document = {
  readyState: "loading",
  addEventListener() {},
  documentElement: { getAttribute: () => "rtl" },
};
require(path.join(JS, "bidi.js"));
const B = global.KazmaBidi;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail).slice(0, 400));
}

const CASES = [
  ["Kazma checked the cron store for you.", "ltr"],
  ["ملاحظة: يمكنك قراءة المهام المجدولة عبر أداة list_scheduled بدلاً من فتح الملف مباشرة.", "rtl"],
  ["PDF الملف جاهز للتنزيل الآن من صفحة المستندات.", "rtl"],
  ["اقرأ المقال هنا https://example.com/a/very/long/article-path-with-many-words-in-it", "rtl"],
  ["Send it to محمد عبدالله tomorrow please", "ltr"],
  ["Safety: Kazma control-plane store — not readable by file tools.", "ltr"],
  ["`list_scheduled` يعرض المهام", "rtl"],
  ["[رابط المقال](https://example.com/very/long/english/path) هنا", "rtl"],
  ["Kazma كاظمة", "ltr"],
  ["كاظمة Kazma", "rtl"],
  ["שלום world עולם", "rtl"],
  ["Привет мир", "ltr"],
  ["42", ""],
  ["---", ""],
  ["", ""],
  // Arabic technical prose: terms joined by و or Arabic commas, and names
  // (acronyms, CamelCase, digits) that count for little. All four are
  // Arabic sentences; a plain word count calls them English.
  ["توليد مستندات PDF وDOCX وXLSX وMarkdown.", "rtl"],
  ["WebDAV — يعمل مع WD MyCloud OS5 وأي جهاز NAS.", "rtl"],
  ["استخدم Python, JavaScript, Go, Rust", "rtl"],
  ["يدعم Telegram، Discord، Slack، Web", "rtl"],
  // English with the same kinds of names stays English.
  ["The OpenAI API returns JSON for GPT models.", "ltr"],
  ["Run npm install and then npm run build in the repo.", "ltr"],
  ["HypertFit Rebranding", "ltr"],
  ["Live test 3, reply briefly in English. Use sqlite_query on the relative path.", "ltr"],
];
for (const [text, want] of CASES) {
  ok("blockDir " + JSON.stringify(text.slice(0, 40)) + " -> " + (want || "none"),
    B.blockDir(text) === want, B.blockDir(text));
}

// Negative controls: the rules blockDir replaces.
function firstLetter(text) {  // what dir="auto" asks
  for (const ch of String(text)) {
    if (/\p{L}/u.test(ch)) return /[֐-ࣿיִ-﷿ﹰ-﻿]/.test(ch) ? "rtl" : "ltr";
  }
  return "";
}
ok("dir=auto's first letter puts an Arabic sentence opening in English left-to-right",
  firstLetter(CASES[2][0]) === "ltr");
ok("counting letters puts an Arabic sentence with a long path left-to-right",
  B.isArabicDominant(CASES[3][0]) === false);
function plainWordCount(text) {  // every word counts once, by its majority script
  let rtl = 0, ltr = 0;
  for (const w of String(text).split(/\s+/)) {
    let r = 0, l = 0;
    for (const ch of w) {
      if (!/\p{L}/u.test(ch)) continue;
      if (/[֐-ࣿיִ-﷿ﹰ-﻿]/.test(ch)) r++; else l++;
    }
    if (r > l) rtl++; else if (l) ltr++;
  }
  return rtl > ltr ? "rtl" : "ltr";
}
ok("a plain word count calls Arabic technical prose English",
  plainWordCount("توليد مستندات PDF وDOCX وXLSX وMarkdown.") === "ltr" &&
  plainWordCount("WebDAV — يعمل مع WD MyCloud OS5 وأي جهاز NAS.") === "ltr");

// The renderer: every block its own direction; a block with no letters takes
// its container's (a number column in an Arabic table stays on its right).
const src = fs.readFileSync(path.join(JS, "streaming.js"), "utf8");
const start = src.indexOf("var mdRender = (function()");
const end = src.indexOf("function copyCode", start);
ok("the renderer is extractable", start >= 0 && end > start);
// eslint-disable-next-line no-eval
const mdRender = eval("(" + src.slice(start, end).trim().replace(/^var mdRender = /, "").replace(/;?\s*$/, "") + ")");

const html = mdRender([
  "Kazma checked it.",
  "",
  "ملاحظة: يمكنك قراءة المهام عبر أداة list_scheduled.",
  "",
  "## العنوان",
  "",
  "| العمود | القيمة |",
  "|---|---|",
  "| عدد المهام | 42 |",
  "",
  "> Safety: read it with list_scheduled.",
  "",
  "- one item in English",
  "- عنصر",
].join("\n"));
ok("an English paragraph is ltr", /<p dir="ltr">Kazma checked it\./.test(html), html);
ok("an Arabic paragraph holding a tool name is rtl", /<p dir="rtl">ملاحظة/.test(html), html);
ok("a heading takes its own", /<h2 dir="rtl">العنوان<\/h2>/.test(html), html);
ok("an Arabic table runs right-to-left", /<table class="md-table" dir="rtl">/.test(html), html);
ok("a cell takes its own", /<td dir="rtl">عدد المهام<\/td>/.test(html), html);
ok("a number cell takes its table's", /<td>42<\/td>/.test(html), html);
ok("a quote takes its own", /<blockquote class="md-quote" dir="ltr">/.test(html), html);
ok("list items each take their own",
  /<li dir="ltr">one item in English/.test(html) && /<li dir="rtl">عنصر/.test(html), html);
ok("nothing is left to the first letter", !/dir="auto"/.test(html), html);

// Without the helper (a page that does not load bidi.js) the first letter
// decides, as before.
const saved = B.blockDir;
B.blockDir = undefined;
const bare = mdRender("ملاحظة: نص.\n\nKazma.");
B.blockDir = saved;
ok("without the helper blocks fall back to dir=auto", (bare.match(/dir="auto"/g) || []).length === 2, bare);

process.exit(fail ? 1 : 0);
