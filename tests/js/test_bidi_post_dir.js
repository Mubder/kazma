/**
 * KazmaBidi.textDir must pin Arabic tweet bodies to rtl.
 * dir=auto / first-strong is what made /x Saved drafts + Posted LTR.
 *
 * Run: node tests/js/test_bidi_post_dir.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "bidi.js",
);
const src = fs.readFileSync(srcPath, "utf8");

global.window = global;
global.document = {
  readyState: "loading",
  addEventListener() {},
  documentElement: { getAttribute: (n) => (n === "dir" ? "rtl" : "") },
};
eval(src); // eslint-disable-line no-eval

const bidi = global.KazmaBidi;
if (!bidi || typeof bidi.textDir !== "function") {
  console.error("KazmaBidi.textDir missing");
  process.exit(1);
}

function assert(name, cond) {
  if (!cond) {
    console.error("FAIL", name);
    process.exit(1);
  }
  console.log("ok", name);
}

const tweet = "كاظمه لا تجيب فقط — بل تنفّذ وفق جدول";
const wrap =
  'Call x_post with EXACTLY this text: "' + tweet + '"';
const mixed =
  "See https://example.com/a/very/long/article-path and more English " +
  "words about the launch: مرحبا بالعالم";

assert("plain arabic is rtl", bidi.textDir(tweet) === "rtl");
assert("wrapper extracts then rtl", bidi.textDir(wrap) === "rtl");
assert("extracted body is the tweet", bidi.extractPostBody(wrap).startsWith("كاظمه"));
assert("latin-heavy mixed is still rtl", bidi.textDir(mixed) === "rtl");
assert("english stays ltr", bidi.textDir("hello from kazma") === "ltr");
assert("never auto for tweets", bidi.textDir(mixed) !== "auto");
assert("empty inherits arabic page dir", bidi.textDir("") === "rtl");
assert("empty inherits arabic page dir on spaces", bidi.textDir("   ") === "rtl");
console.log("all ok");
