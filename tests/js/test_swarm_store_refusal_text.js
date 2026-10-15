"use strict";
// A dispatch the task store could not record (Swarm tracker SQ04,
// 2026-10-15): the page words the server's code in its own language, for the
// foreground answer (HTTP 503) and for a background dispatch's stream
// (task_completed carrying the refusal). Negative control: a code the page
// does not know shows the server's message, as before.
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const assert = require("assert");
const source = fs.readFileSync(path.join(__dirname, "../../kazma-ui/kazma_ui/static/js/swarm.js"), "utf8")
  .replace(/\r\n/g, "\n");
const start = source.indexOf("  function dispatchRefusalText(");
const end = source.indexOf("\n  }\n", start) + 4;
assert(start > 0 && end > start, "dispatchRefusalText not found");
const said = [];
const context = {
  t(key, vars) { said.push(key); return "[" + key + (vars ? " " + JSON.stringify(vars) : "") + "]"; },
};
vm.createContext(context);
vm.runInContext(source.slice(start, end), context);

assert.strictEqual(context.dispatchRefusalText({code: "task_store_unavailable", message: "English from the server"}, "x"),
                   "[swarm.task_store_unavailable]");
assert.strictEqual(context.dispatchRefusalText({code: "swarm_at_capacity", max_concurrent_tasks: 3}, "x"),
                   '[swarm.at_capacity {"n":3}]');
// Control: an unknown code keeps the server's message.
assert.strictEqual(context.dispatchRefusalText({code: "other", message: "server text"}, "x"), "server text");

// An approval deferred because Temporal is required and down (SW-E16).
const dStart = source.indexOf("  function decisionErrorText(");
const dEnd = source.indexOf("\n  }\n", dStart) + 4;
vm.runInContext(source.slice(dStart, dEnd), context);
assert.strictEqual(context.decisionErrorText({code: "temporal_unavailable", message: "English"}),
                   "[swarm.temporal_unavailable]");
assert.strictEqual(context.decisionErrorText({code: "other", message: "server text"}), "server text");

// The background dispatch's stream: its task_completed handler shows the refusal.
const handler = source.slice(source.indexOf("evtSource.addEventListener('task_completed'"));
const shown = handler.slice(0, handler.indexOf("evtSource.close();"));
assert(/metadata\.code === 'task_store_unavailable'/.test(shown) && /showError\(dispatchRefusalText\(/.test(shown),
       "a background dispatch's refusal is not shown when its stream ends");
console.log("swarm store refusal worded from the catalog, foreground and background");
