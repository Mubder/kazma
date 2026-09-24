/**
 * mergeReasoningPart against the shared fixture (Python reads the same file:
 * tests/test_reasoning_merge.py). Live 2026-09-24: Thoughts showed the
 * post-approval notes twice, the first copy stopping mid-sentence.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js",
), "utf8");
const sandbox = { console, globalThis: {} };
sandbox.globalThis = sandbox;
vm.runInNewContext(src, sandbox);
const TD = sandbox.KazmaTurnDocument;

const fixture = JSON.parse(fs.readFileSync(path.join(
  __dirname, "..", "fixtures", "unified_turn", "merge", "reasoning_merge.json",
), "utf8"));

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || "").slice(0, 240));
}

ok("fixture has cases", fixture.cases.length >= 6);
for (const c of fixture.cases) {
  const got = TD.mergeReasoningPart(
    { type: "reasoning", text: c.old },
    { type: "reasoning", text: c.new },
  );
  ok(c.name, got.text === c.expect, JSON.stringify(got.text));
}

process.exit(fail ? 1 : 0);
