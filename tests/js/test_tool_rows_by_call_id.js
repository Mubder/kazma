/**
 * Every tool call is its own row, live as on a reload.
 *
 * Live 2026-11-05 (an Arabic DOCX turn): the stored turn held 16 tool calls
 * and the live page showed 12 rows, some still "Running" after they ended.
 * The page sends each call's start and end through logProgress as a
 * 'progress' event whose step carries the call's id ('tool#<id>'), and the
 * projector dropped an event it had "seen": the dedupe key was the tool's
 * name, the state and the first 40 characters of the detail. Two calls of
 * one tool that read the same file, or two results that both said "No
 * results", were one event to it -- the second call never got a row, or
 * never ended. Runs the REAL modules/turn_document.js. Negative control: the
 * same module with the old key loses the second call.
 *
 * Run: node tests/js/test_tool_rows_by_call_id.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js",
);
const src = fs.readFileSync(srcPath, "utf8").replace(/\r\n/g, "\n");

function load(source) {
  const sandbox = { console, globalThis: {} };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(source, sandbox);
  return sandbox.KazmaTurnDocument;
}

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail).slice(0, 400));
}

// What chat.js's logProgress hands the projector for a tool row.
function progress(callId, title, detail, state) {
  return {
    type: "progress", source: "progress", turn_id: "turn-1",
    step: { kind: "tool", title: title, detail: detail, state: state, id: "tool#" + callId },
  };
}

function rows(TD, events) {
  let doc = TD.empty("turn-1");
  for (const ev of events) doc = TD.applyEvent(doc, ev);
  return (doc.parts || []).filter((p) => p.type === "tool")
    .map((p) => p.call_id + ":" + p.state);
}

const SAME_READ = 'file_read "reports/backup-benefits-ar.docx"';
const LIVE = [
  // Two reads of the same file: the same detail while running.
  progress("c1", "file_read", SAME_READ, "running"),
  progress("c1", "file_read", "PK\u0003\u0004 ... 4,812 characters", "done"),
  progress("c2", "file_read", SAME_READ, "running"),
  progress("c2", "file_read", "PK\u0003\u0004 ... 4,812 characters", "done"),
  // Two searches whose results open alike; the second one failed.
  progress("c3", "file_search", 'file_search "backup-benefits"', "running"),
  progress("c3", "file_search", "No files matched the pattern in the workspace root", "done"),
  progress("c4", "file_search", 'file_search "backup-benefits-ar"', "running"),
  progress("c4", "file_search", "No files matched the pattern in the workspace root", "failed"),
  // Two calls that both answered the same text.
  progress("c5", "document_read", "read", "running"),
  progress("c5", "document_read", "No results", "done"),
  progress("c6", "document_read", "read", "running"),
  progress("c6", "document_read", "No results", "done"),
];

{
  const TD = load(src);
  const got = rows(TD, LIVE);
  ok("every call is a row", got.length === 6, got.join(" "));
  ok("each row ends as its call ended",
     got.join(" ") === "c1:done c2:done c3:done c4:failed c5:done c6:done", got.join(" "));

  // The same frame twice -- the SSE stream and the socket both deliver it,
  // or a replay -- is still one event.
  const twice = [];
  for (const ev of LIVE) { twice.push(ev); twice.push(JSON.parse(JSON.stringify(ev))); }
  const again = rows(TD, twice);
  ok("a frame delivered twice is still one event",
     again.join(" ") === "c1:done c2:done c3:done c4:failed c5:done c6:done", again.join(" "));

  // A step without an id (legacy telemetry) keeps the old rule.
  let doc = TD.empty("turn-1");
  const legacy = { type: "progress", source: "progress",
                   step: { kind: "status", title: "Thinking", state: "running" } };
  doc = TD.applyEvent(doc, legacy);
  const before = (doc.parts || []).length;
  doc = TD.applyEvent(doc, JSON.parse(JSON.stringify(legacy)));
  ok("a step without an id is deduped as before", (doc.parts || []).length === before);
}

{
  // Negative control: the module with the key it had before 2026-11-05.
  const start = src.indexOf("    if (t === 'progress' && step.id) {");
  const end = src.indexOf("    var c = String((ev && (ev.content", start);
  ok("the new key is in the module", start > 0 && end > start);
  const old = load(src.slice(0, start) + src.slice(end));
  const got = rows(old, LIVE);
  ok("negative control: the old key loses calls or their endings",
     got.join(" ") !== "c1:done c2:done c3:done c4:failed c5:done c6:done", got.join(" "));
  ok("negative control: the second read of the same file had no row",
     !got.some((r) => r.indexOf("c2:") === 0), got.join(" "));
}

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
