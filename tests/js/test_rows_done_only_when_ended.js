/**
 * A row shows done only when its record says it ended (resilience plan I).
 * Run: node tests/js/test_rows_done_only_when_ended.js
 *
 * Live, "Kazma is thinking…" and "Composing response…" rows of finished turns
 * were drawn with a check: a row still "running" when the turn ended was
 * turned into "done". It is drawn plain now (no spinner, no check); a row
 * whose record says it ended keeps its check. Negative control: the old
 * mapping said done.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const src = fs.readFileSync(path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8");
const start = src.indexOf("  function _endedRows(rows, doc) {");
const end = src.indexOf("\n  }\n", start) + 4;
if (start < 0 || end <= start) { console.log("FAIL _endedRows not found"); process.exit(1); }
const ctx = { Object };
vm.createContext(ctx);
vm.runInContext(src.slice(start, end), ctx);

let fail = 0;
function ok(name, cond, got) {
  if (cond) { console.log("OK   " + name); return; }
  fail++;
  console.log("FAIL " + name + (got === undefined ? "" : "  (got " + JSON.stringify(got) + ")"));
}

const rows = [
  { kind: "status", title: "Kazma is thinking…", state: "running" },
  { kind: "tool", title: "file_read", state: "done" },
  { kind: "status", title: "Composing response…", state: "running" },
];
const done = ctx._endedRows(rows, { status: "done" });
ok("a running row of a finished turn is plain, not done", done[0].state === "info", done[0]);
ok("...the composing row too", done[2].state === "info", done[2]);
ok("a row whose record says it ended keeps its check", done[1].state === "done");
const live = ctx._endedRows(rows, { status: "streaming" });
ok("a running turn's rows are left as they are", live[0].state === "running");
const oldRule = rows.map(r => (r.state === "running" ? "done" : r.state));
ok("control: the old mapping called it done", oldRule[0] === "done");

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
