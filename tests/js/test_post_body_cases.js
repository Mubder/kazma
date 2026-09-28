/**
 * The page's copy of the post-body rule gives the server's answers.
 * Run: node tests/js/test_post_body_cases.js
 *
 * KazmaBidi.extractPostBody (bidi.js) and kazma_core.text_display.
 * extract_post_body are one rule in two languages. On 2026-09-28 the server
 * copy was fixed and the Scheduled page kept listing the owner's reminders
 * as "done" from its own. Both now read tests/fixtures/post_body_cases.json
 * (tests/test_text_display.py is the Python half).
 *
 * Negative control: the extractor as it was answers "done" for the reminder.
 */
"use strict";

const fs = require("fs");
const path = require("path");

const root = path.join(__dirname, "..", "..");
const src = fs.readFileSync(path.join(root, "kazma-ui", "kazma_ui", "static", "js", "bidi.js"), "utf8");
const fixture = JSON.parse(fs.readFileSync(path.join(root, "tests", "fixtures", "post_body_cases.json"), "utf8"));

global.window = global;
global.document = {
  readyState: "loading",
  addEventListener() {},
  documentElement: { getAttribute: () => "" },
};
eval(src); // eslint-disable-line no-eval
const bidi = global.KazmaBidi;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("ok  ", name); return; }
  fail += 1;
  console.log("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
}

ok("the fixture has cases", fixture.cases.length >= 8, fixture.cases.length);
for (const c of fixture.cases) {
  const got = bidi.extractPostBody(c.text);
  ok(c.name, got === c.body, got);
}

// The Scheduled page's kicker for the reminder: none (it is all one text).
const reminder = fixture.cases[0].text;
ok("no kicker is cut out of the reminder", bidi.displayKicker(reminder) === "", bidi.displayKicker(reminder));

// Negative control: the extractor before 2026-09-28.
function oldExtract(text) {
  const raw = String(text || "").replace(/\s+/g, " ").trim();
  const candidates = [];
  const re = /["“]([^"”]{4,})["”]/g;
  let m;
  while ((m = re.exec(raw))) candidates.push(m[1].trim());
  return candidates.length ? candidates.reduce((a, b) => (b.length > a.length ? b : a)) : raw;
}
ok("control: the old rule said \"done\"", oldExtract(reminder) === "done", oldExtract(reminder));

if (fail) {
  console.log(fail + " failure(s)");
  process.exit(1);
}
console.log("all ok");
