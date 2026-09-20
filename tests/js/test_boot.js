/**
 * Boot smoke test for the chat client.
 *
 * `node --check` proves a file PARSES. It does not prove the module-level
 * code RUNS: a refactor that renames a function and misses one call site,
 * or drops a helper something still references, produces a file that parses
 * cleanly and then throws ReferenceError in the browser on load — with a
 * blank chat page and nothing in any suite going red, because the rest of
 * the client's tests assert on source text.
 *
 * This evaluates turn_document.js, turn_view.js and chat.js together against
 * the shim DOM, then checks the public surface is wired and that a render
 * actually round-trips. It is deliberately shallow and fast; the behavioural
 * depth lives in test_turn_view.js.
 *
 * Run: node tests/js/test_boot.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const JS = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js",
);

const env = makeDocument();
const doc = env.document;
doc.addEventListener = () => {};
doc.getElementById = () => null;
doc.readyState = "loading";
doc.body = env.ROOT;

global.window = global;
global.document = doc;
global.localStorage = {
  _m: {},
  getItem(k) { return this._m[k] ?? null; },
  setItem(k, v) { this._m[k] = String(v); },
  removeItem(k) { delete this._m[k]; },
};
global.addEventListener = () => {};
global.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
global.KS = { markdown: (s) => String(s), toast: () => {}, sse: () => ({}) };
global.KazmaStream = global.KS;
global.CHAT_I18N = {};
global.KAZMA_SLASH_COMMANDS = [];

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail === undefined ? "" : detail).slice(0, 300));
    fail++;
  } else {
    console.log("OK", name);
  }
}

for (const f of ["modules/turn_document.js", "modules/turn_view.js", "chat.js"]) {
  const src = fs.readFileSync(path.join(JS, f), "utf8");
  try {
    // eslint-disable-next-line no-eval
    (0, eval)(src);
    console.log("OK evaluates:", f);
  } catch (e) {
    console.error("FAIL evaluates:", f, e.message);
    console.error(e.stack.split("\n").slice(0, 6).join("\n"));
    process.exit(1);
  }
}

assert("KazmaTurnDocument is exported", typeof window.KazmaTurnDocument === "object");
assert("KazmaTurnView is exported", typeof window.KazmaTurnView === "object");
assert("KazmaChat is exported", typeof window.KazmaChat === "object");

// The surface other modules (agentStore, nav, voice, inline handlers) call.
for (const fn of [
  // `taskCard` was the Live Task Card's event sink, removed with the
  // bar itself (UNIFIED_TURN_BLOCK.md §3 — one status surface, inside
  // the turn).
  "sendMessage", "newSession", "retry", "destroy", "beginTurn",
  "beginVoiceTurn", "_hitlApproval", "hasLiveGate", "hasInlineApprovalCard",
  "hitlCardExistsFor", "markApprovalTimedOut",
]) {
  assert("KazmaChat." + fn + " is wired",
    typeof window.KazmaChat[fn] === "function",
    typeof window.KazmaChat[fn]);
}

// A render must round-trip end to end.
const view = window.KazmaTurnView.create({
  document: doc,
  turnDocument: window.KazmaTurnDocument,
  onInvariant: () => {},
});
const bubble = env.assistantBubble({ turnId: "t1" });
env.ROOT.appendChild(bubble);
const d = window.KazmaTurnDocument.applyEvent(
  window.KazmaTurnDocument.empty("t1"),
  { type: "done", content: "hello" },
);
const report = view.render(bubble, d, {
  has: () => true,
  build: () => {
    const e = doc.createElement("div");
    e.className = "message-text";
    return e;
  },
  paint: (entry, el) => { el.setAttribute("data-md", "hello"); },
});
assert("a render round-trips", report.painted === true, report.reason);
assert("the answer reached the DOM",
  bubble.querySelector(".message-text").getAttribute("data-md") === "hello");

if (fail) {
  console.error("\n" + fail + " assertion(s) failed");
  process.exit(1);
}
console.log("\nall ok");
// chat.js arms timers on load; without this the event loop never drains.
process.exit(0);
