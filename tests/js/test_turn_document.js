/**
 * Node tests for static/js/modules/turn_document.js
 * Run: node tests/js/test_turn_document.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js",
);
const src = fs.readFileSync(srcPath, "utf8");
const sandbox = { console, globalThis: {} };
sandbox.globalThis = sandbox;
vm.runInNewContext(src, sandbox);

const TD = sandbox.KazmaTurnDocument;
let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail ?? "").slice(0, 300));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

assert("exports", TD && typeof TD.activityOf === "function" && typeof TD.textOf === "function");

const parts = [
  { type: "reasoning", text: "Let me query the live API." },
  { type: "tool", name: "file_read", result: "hitl.py", state: "done" },
  { type: "text", text: "The timeout is 300 seconds." },
];
assert("textOf", TD.textOf(parts) === "The timeout is 300 seconds.");
const act = TD.activityOf(parts);
assert("activity has thought", act.some((r) => r.kind === "thought"));
assert("activity has tool", act.some((r) => r.kind === "tool"));
assert("activityForMessage prefers activity", TD.activityForMessage({
  activity: [{ kind: "tool", title: "x" }],
  parts,
}).length === 1);
assert("activityForMessage falls back to parts", TD.activityForMessage({
  parts,
}).some((r) => r.kind === "thought"));
assert("empty parts", TD.activityOf([]).length === 0);
assert("idempotent activityOf", JSON.stringify(TD.activityOf(parts)) === JSON.stringify(TD.activityOf(parts)));

if (fail) process.exit(1);
console.log("all ok");
