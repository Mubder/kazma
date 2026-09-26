/**
 * The two duration formatters agree, and read a long turn in minutes.
 * Run: node tests/js/test_format_duration.js
 *
 * streaming.js (KazmaStream.formatDuration -- the chat's stats line) and
 * modules/util.js (KazmaUtils.formatDuration) are separate copies: one is a
 * classic script, the other an ES module. The chat's copy printed a
 * four-minute turn as "240.0s" (2026-09-26) while the module's printed
 * "4m 0s". Both run the REAL source here against one table.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const jsDir = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js");

function loadStreaming() {
  const sandbox = { console };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.runInNewContext(fs.readFileSync(path.join(jsDir, "streaming.js"), "utf8"), sandbox);
  return sandbox.KazmaStream.formatDuration;
}

function loadUtil() {
  // An ES module: strip the export keywords and hand the object back.
  const src = fs.readFileSync(path.join(jsDir, "modules", "util.js"), "utf8")
    .replace(/^export\s+/gm, "");
  const sandbox = { console };
  sandbox.window = sandbox;
  sandbox.globalThis = sandbox;
  vm.runInNewContext(src + "\nglobalThis.__utils = KazmaUtils;", sandbox);
  return sandbox.__utils.formatDuration.bind(sandbox.__utils);
}

const CASES = [
  [0, "0s"],
  [null, "0s"],
  [undefined, "0s"],
  [-5, "0s"],
  ["4300", "4.3s"],
  [1, "1ms"],
  [999, "999ms"],
  [999.4, "999ms"],
  [999.6, "1.0s"],
  [1000, "1.0s"],
  [4300, "4.3s"],
  [59940, "59.9s"],
  [59960, "1m 0s"],
  [60000, "1m 0s"],
  [240000, "4m 0s"],
  [245400, "4m 5s"],
  [3599400, "59m 59s"],
  [3599600, "1h 0m"],
  [5400000, "1h 30m"],
];

let fail = 0;
function ok(name, cond, detail) {
  if (!cond) {
    fail++;
    console.error("FAIL", name, detail === undefined ? "" : detail);
  } else {
    console.log("ok  ", name);
  }
}

const copies = { streaming: loadStreaming(), util: loadUtil() };
for (const [name, fmt] of Object.entries(copies)) {
  for (const [input, expect] of CASES) {
    const got = fmt(input);
    ok(`${name} ${JSON.stringify(input)} -> ${expect}`, got === expect, JSON.stringify(got));
  }
}

// Negative control: the rule "round before comparing" is what the table
// tests. The pre-fix streaming copy, run on the same table, fails it.
{
  const before = function (ms) {
    if (!ms) return "0ms";
    if (ms < 1000) return Math.round(ms) + "ms";
    return (ms / 1000).toFixed(1) + "s";
  };
  const misses = CASES.filter(([input, expect]) => before(input) !== expect).length;
  ok("negative control: the old streaming copy fails the table", misses > 0, misses);
}

process.exit(fail ? 1 : 0);
