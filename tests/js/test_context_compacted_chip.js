/**
 * One "Earlier context compacted" chip per turn.
 *
 * Live 2026-11-05: a two-turn chat showed two chips under its second turn.
 * The tab that sends a turn hears every journaled frame through its stream
 * AND its WebSocket (AGENTS.md §31 D), and the chip painter added a chip for
 * each copy. Runs the REAL _paintContextCompacted from chat.js on the DOM
 * shim. Negative control: the old painter (a chip per call) leaves two.
 *
 * Run: node tests/js/test_context_compacted_chip.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = src.indexOf("  function _paintContextCompacted(");
const end = src.indexOf("\n  function applyJournalFrame(", start);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail));
}
ok("the painter is extractable", start > 0 && end > start);

function painterOn(body) {
  const { document, ROOT } = makeDocument();
  const messagesEl = document.createElement("div");
  ROOT.appendChild(messagesEl);
  const ti = (key, english) => english;
  // eslint-disable-next-line no-new-func
  const paint = new Function("document", "messagesEl", "ti",
    body + "\nreturn _paintContextCompacted;")(document, messagesEl, ti);
  return { paint, messagesEl, document };
}

function chips(el) {
  return el.querySelectorAll(".context-compacted-chip");
}

const live = { detail: "collapsed 2 prior tool-chain segments", stubbed_segments: 2, turn_id: "t-2" };

{
  const { paint, messagesEl } = painterOn(src.slice(start, end));
  paint(live);                          // the stream's copy
  paint(Object.assign({}, live));      // the WebSocket's copy
  ok("both mouths' copies of one frame make one chip", chips(messagesEl).length === 1,
     chips(messagesEl).length);
  ok("the chip names its turn", chips(messagesEl)[0].getAttribute("data-turn-id") === "t-2");
  ok("the server's detail is shown as written",
     chips(messagesEl)[0].querySelector(".context-compacted-chip-detail").getAttribute("translate") === "no");

  paint({ detail: "collapsed 3 prior tool-chain segments", turn_id: "t-3" });
  ok("another turn's compaction gets its own chip", chips(messagesEl).length === 2,
     chips(messagesEl).length);

  paint({ detail: "4 user turns dropped by context trim", turn_id: "t-3" });
  ok("a later copy for the same turn updates its chip", chips(messagesEl).length === 2
     && chips(messagesEl)[1].getAttribute("data-detail") === "4 user turns dropped by context trim",
     chips(messagesEl)[1].getAttribute("data-detail"));
}

{
  const { paint, messagesEl, document } = painterOn(src.slice(start, end));
  paint({ detail: "collapsed 1 prior tool-chain segments" });
  paint({ detail: "collapsed 1 prior tool-chain segments" });
  ok("a frame naming no turn: two copies in a row make one chip", chips(messagesEl).length === 1,
     chips(messagesEl).length);
  messagesEl.appendChild(document.createElement("div"));   // the next turn
  paint({ detail: "collapsed 1 prior tool-chain segments" });
  ok("a frame naming no turn after other content is a new chip", chips(messagesEl).length === 2,
     chips(messagesEl).length);
}

{
  // Negative control: the painter before 2026-11-05 added a chip per call.
  const old = [
    "  function _paintContextCompacted(data) {",
    "    if (!messagesEl) return;",
    "    var chip = document.createElement('div');",
    "    chip.className = 'context-compacted-chip';",
    "    messagesEl.appendChild(chip);",
    "  }",
  ].join("\n");
  const { paint, messagesEl } = painterOn(old);
  paint(live);
  paint(Object.assign({}, live));
  ok("negative control: the old painter leaves two chips", chips(messagesEl).length === 2,
     chips(messagesEl).length);
}

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
