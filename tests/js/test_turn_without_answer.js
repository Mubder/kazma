/**
 * A finished turn with no answer says so (resilience plan I).
 * Run: node tests/js/test_turn_without_answer.js
 *
 * Live, an old OCR turn read "Completed" over nothing. A turn the server
 * finished ("done") without any answer text is "Ended without an answer",
 * terminal, and offers Retry. Negative control: the same turn with its
 * answer is Completed.
 */
"use strict";

const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_presentation.js"));
const TP = global.KazmaTurnPresentation;

let fail = 0;
function ok(name, cond, got) {
  if (cond) { console.log("OK   " + name); return; }
  fail++;
  console.log("FAIL " + name + (got === undefined ? "" : "  (got " + JSON.stringify(got) + ")"));
}

const empty = TP.header({ turnId: "t", status: "done", parts: [{ type: "tool", name: "ocr" }], stream: "" }, { retrySupported: true });
ok("done with no answer is not Completed", empty.phase === "no_answer", empty.phase);
ok("...it is terminal", empty.terminal === true);
ok("...and offers Retry", empty.canRetry === true);
const answered = TP.header({ turnId: "t", status: "done", parts: [{ type: "text", text: "Here it is." }] }, {});
ok("control: done with its answer is Completed", answered.phase === "completed", answered.phase);
const streamed = TP.header({ turnId: "t", status: "done", parts: [], stream: "partial text" }, {});
ok("streamed text counts as an answer", streamed.phase === "completed", streamed.phase);

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
