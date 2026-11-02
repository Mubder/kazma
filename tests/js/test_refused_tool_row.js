/**
 * A call refused before it ran ends as a failed tool row (2026-11, FA-253).
 *
 * The stream's tool_result frame carries `state: "failed"` for a call that
 * ended in an error or was refused; the REAL projector
 * (modules/turn_document.js) must keep it, so the live row and the reloaded
 * row say the same thing. Negative control: a frame without the state ends
 * `done`, as every frame did before.
 *
 * Run: node tests/js/test_refused_tool_row.js
 */
"use strict";

const path = require("path");

const ROOT = path.join(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
const TD = global.KazmaTurnDocument;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail).slice(0, 400));
}

function toolState(resultFrame) {
  let doc = TD.empty("turn-1");
  doc = TD.applyEvent(doc, { type: "tool_call", tool_name: "config_save", tool_call_id: "c1", seq: 1 });
  doc = TD.applyEvent(doc, Object.assign({ type: "tool_result", tool_name: "config_save",
                                           tool_call_id: "c1", seq: 2 }, resultFrame));
  const tools = (doc.parts || []).filter(function (p) { return p.type === "tool"; });
  return tools.length === 1 ? tools[0].state : "rows:" + tools.length;
}

ok("a refused call ends failed",
   toolState({ result: "config: key 'safety.hitl.enabled' is protected", state: "failed" }) === "failed",
   toolState({ result: "refused", state: "failed" }));
ok("a call that ran ends done", toolState({ result: "saved" }) === "done");
ok("negative control: without the state the refused call ended done",
   toolState({ result: "refused" }) === "done");

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
