/**
 * A finished turn's header shows no clock; a running one ticks from the
 * server's stamp.
 *
 * Live 2026-09-28: a turn that took 24.2 s (the answer's meta line) kept
 * "0:13" in its header -- frozen at the last heartbeat's stamp -- while a
 * reload of the same turn showed no clock. Runs the REAL _headerElapsedText
 * and _fmtMMSS from chat.js.
 *
 * Run: node tests/js/test_header_clock.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");

function slice(startMarker, endMarker) {
  const start = src.indexOf(startMarker);
  const end = src.indexOf(endMarker, start);
  if (start < 0 || end < 0) throw new Error("could not find " + startMarker);
  return src.slice(start, end);
}

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail || ""));
}

// eslint-disable-next-line no-new-func
const api = new Function(
  slice("  function _fmtMMSS(total) {", "\n  }\n") + "\n  }\n" +
  slice("  function _headerElapsedText(model) {", "  function _headerCountsText(") +
  "\nreturn { text: _headerElapsedText };")();

const now = Date.now();
const finished = { terminal: true, elapsed: { seconds: 13, stampedAtMs: now - 11000 } };
const running = { terminal: false, elapsed: { seconds: 13, stampedAtMs: now } };

ok("a finished turn shows no clock", api.text(finished) === "", api.text(finished));
ok("a running turn shows its elapsed time", api.text(running) === "0:13", api.text(running));
ok("a running turn ticks forward from the stamp",
  api.text({ terminal: false, elapsed: { seconds: 13, stampedAtMs: now - 5000 } }) === "0:18");
ok("no stamp yet: no clock", api.text({ terminal: false, elapsed: { seconds: 0, stampedAtMs: 0 } }) === "");

// Negative control: the header as it was until 2026-09-28 froze a finished
// turn at its last heartbeat.
function oldText(model) {
  if (!model) return "";
  let secs = model.elapsed.seconds;
  if (!model.terminal && model.elapsed.stampedAtMs) {
    const drift = (Date.now() - model.elapsed.stampedAtMs) / 1000;
    if (drift > 0 && drift < 3600) secs += drift;
  }
  return secs <= 0 ? "" : String(Math.floor(secs / 60)) + ":" + String(Math.floor(secs % 60)).padStart(2, "0");
}
ok("control: the old header showed 0:13 on a finished turn", oldText(finished) === "0:13", oldText(finished));

if (fail) {
  console.log(fail + " failed");
  process.exit(1);
}
console.log("all passed");
