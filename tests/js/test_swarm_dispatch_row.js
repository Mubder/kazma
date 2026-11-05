/**
 * A dispatched swarm task is a tool row that ends; nothing is written into
 * the answer.
 *
 * Live 2026-11-05: a chat that sent a task to the swarm kept a pulsing
 * "Background Task Active: ... Use check_swarm_task('task-...') to get the
 * result." box inside its finished answer, under the task's result. The
 * stream's onToolResult kept the dispatch row "running" and appended that box
 * (the model's own instructions, quoted) into the bubble; neither was in the
 * stored turn, so a reload showed a finished row and no box. Runs the REAL
 * onToolResult from chat.js on the DOM shim. Negative control: the old
 * handler leaves the row running and the box in the answer.
 *
 * Run: node tests/js/test_swarm_dispatch_row.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const src = fs.readFileSync(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
// The send stream's handler: the one followed by onMemoryExplain (the attach
// callbacks earlier in the file have a short onToolResult of their own).
const head = "      onToolResult: ";
const end = src.indexOf("\n\n      onMemoryExplain:");
const start = src.lastIndexOf(head, end);

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail).slice(0, 400));
}
ok("the handler is extractable", start > 0 && end > start);

const DISPATCHED = "Swarm task dispatched to the worker best suited to it (id: task-af10). " +
  "It's visible in the Swarm panel. Use check_swarm_task('task-af10') to get the result.";

function run(handlerSrc, frame) {
  const { document, ROOT } = makeDocument();
  const msg = document.createElement("div");
  msg.className = "message message-assistant";
  const content = document.createElement("div");
  content.className = "message-content";
  msg.appendChild(content);
  ROOT.appendChild(msg);
  const rows = [];
  const deps = {
    document: document,
    currentMsgEl: msg,
    _mine: () => true,
    noteTurnActivity: () => {},
    logProgress: (row) => rows.push(row),
    _toolDetailWithGist: (summary) => summary,
    _toolResultSummary: (r) => String(r || ""),
    scrollToBottom: () => {},
    ti: (key, english) => english,
    escapeHtml: (s) => String(s),
    truncateStr: (s, n) => String(s).slice(0, n),
  };
  const names = Object.keys(deps);
  // eslint-disable-next-line no-new-func
  const handler = new Function(...names, "return (" + handlerSrc + ");")(...names.map((n) => deps[n]));
  handler(frame);
  return { rows, content };
}

const current = src.slice(start + head.length, end).replace(/,\s*$/, "");

for (const tool of ["dispatch_swarm", "swarm_dispatch"]) {
  const { rows, content } = run(current, { tool_name: tool, tool_call_id: "c1", result: DISPATCHED });
  ok(tool + ": one row", rows.length === 1, rows.length);
  ok(tool + ": the dispatch row ends done, as the stored turn has it",
     rows[0] && rows[0].state === "done", rows[0] && rows[0].state);
  ok(tool + ": the row keeps the call id", rows[0] && rows[0].id === "tool#c1", rows[0] && rows[0].id);
  ok(tool + ": nothing is written into the answer", content.children.length === 0
     && !String(content.textContent).includes("check_swarm_task"), content.children.length);
}

{
  const { rows } = run(current, { tool_name: "dispatch_swarm", result: "refused", state: "failed" });
  ok("a refused dispatch still ends failed (FA-253)", rows[0] && rows[0].state === "failed",
     rows[0] && rows[0].state);
}

{
  const { rows } = run(current, { tool_name: "file_read", result: "Swarm task dispatched? no: a file says so" });
  ok("another tool quoting the words ends done", rows[0] && rows[0].state === "done");
}

{
  // Negative control: the handler before 2026-11-05.
  const old = [
    "function(data) {",
    "        if (!_mine()) return;",
    "        noteTurnActivity();",
    "        if (!currentMsgEl) return;",
    "        var isSwarm = (data.tool_name === 'dispatch_swarm' || data.tool_name === 'swarm_dispatch' || (data.result && data.result.indexOf('Swarm task dispatched') !== -1));",
    "        logProgress({",
    "          kind: 'tool',",
    "          title: data.tool_name || 'tool',",
    "          detail: _toolDetailWithGist(_toolResultSummary(data.result), data.result),",
    "          state: data.state === 'failed' ? 'failed' : (isSwarm ? 'running' : 'done'),",
    "          id: data.tool_call_id ? 'tool#' + data.tool_call_id : undefined,",
    "        });",
    "        if (isSwarm) {",
    "          var content = currentMsgEl.querySelector('.message-content');",
    "          var resultBox = document.createElement('div');",
    "          resultBox.className = 'swarm-bg-badge';",
    "          resultBox.innerHTML = '<span class=\"pulse-dot\"></span><div><strong>' + escapeHtml(ti('background_task_active', 'Background Task Active:')) + '</strong> ' + escapeHtml(truncateStr(data.result, 300)) + '</div>';",
    "          content.appendChild(resultBox);",
    "        }",
    "        scrollToBottom();",
    "      }",
  ].join("\n");
  const { rows, content } = run(old, { tool_name: "dispatch_swarm", tool_call_id: "c1", result: DISPATCHED });
  ok("negative control: the old handler left the dispatch row running",
     rows[0] && rows[0].state === "running", rows[0] && rows[0].state);
  ok("negative control: the old handler wrote the box into the answer",
     content.querySelector(".swarm-bg-badge") !== null);
}

if (fail) { console.log(fail + " failed"); process.exit(1); }
console.log("all passed");
