/**
 * After sign-in the page goes only to a path on this server (audit
 * 2026-09-30, AUD-019). Run: node tests/js/test_safe_next.js
 */
"use strict";

const path = require("path");
const fs = require("fs");

const kazmaSafeNext = require(path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "safe_next.js"));

const ORIGIN = "https://kazma.example";
let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, detail || "");
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

const BS = "\\";
const offSite = [
  "/" + BS + "evil.example",
  "/" + BS + "/evil.example",
  "/\t/evil.example",
  "/\n/evil.example",
  "//evil.example",
  "https://evil.example/",
  "javascript:alert(1)",
  "data:text/html,hi",
];
for (const raw of offSite) {
  assert("refused: " + JSON.stringify(raw), kazmaSafeNext(raw, ORIGIN) === "/", kazmaSafeNext(raw, ORIGIN));
}

const kept = [
  ["/chat", "/chat"],
  ["/settings?tab=email#x", "/settings?tab=email#x"],
  ["https://kazma.example/memory", "/memory"],
  ["", "/"],
  [null, "/"],
];
for (const [raw, want] of kept) {
  assert("kept: " + JSON.stringify(raw), kazmaSafeNext(raw, ORIGIN) === want, kazmaSafeNext(raw, ORIGIN));
}

// The login page uses it, and the old prefix check is gone.
const login = fs.readFileSync(
  path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "templates", "login.html"),
  "utf8",
);
assert("login.html loads safe_next.js (cache-busted)", login.indexOf('src="/static/js/safe_next.js?v=') >= 0);
assert("login.html takes next through kazmaSafeNext", login.indexOf("window.kazmaSafeNext(params.get('next'))") >= 0);
assert("the old prefix check is gone", login.indexOf("startsWith('//')") < 0);

// Negative control: the check this replaced, and where a browser goes.
function oldCheck(raw) {
  let next = raw || "/";
  if (!next.startsWith("/") || next.startsWith("//")) next = "/";
  return next;
}
for (const raw of ["/" + BS + "evil.example", "/\t/evil.example"]) {
  const passed = oldCheck(raw);
  assert(
    "negative control: the old check let " + JSON.stringify(raw) + " reach another host",
    passed === raw && new URL(passed, ORIGIN).origin === "https://evil.example",
    new URL(passed, ORIGIN).href,
  );
}

if (fail) {
  console.error(fail + " failure(s)");
  process.exit(1);
}
console.log("all passed");
