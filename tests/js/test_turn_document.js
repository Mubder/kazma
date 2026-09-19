/**
 * Node tests for static/js/modules/turn_document.js
 * Run: node tests/js/test_turn_document.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js",
);
const src = fs.readFileSync(srcPath, "utf8");
const sandbox = { console, globalThis: {} };
sandbox.globalThis = sandbox;
vm.runInNewContext(src, sandbox);

const TD = sandbox.KazmaTurnDocument;
let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail ?? "").slice(0, 300));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

assert("exports", TD && typeof TD.activityOf === "function" && typeof TD.textOf === "function");

const parts = [
  { type: "reasoning", text: "Let me query the live API." },
  { type: "tool", name: "file_read", result: "hitl.py", state: "done" },
  { type: "text", text: "The timeout is 300 seconds." },
];
assert("textOf", TD.textOf(parts) === "The timeout is 300 seconds.");
const act = TD.activityOf(parts);
assert("activity has thought", act.some((r) => r.kind === "thought"));
assert("activity has tool", act.some((r) => r.kind === "tool"));
assert("activityForMessage prefers activity", TD.activityForMessage({
  activity: [{ kind: "tool", title: "x" }],
  parts,
}).length === 1);
assert("activityForMessage falls back to parts", TD.activityForMessage({
  parts,
}).some((r) => r.kind === "thought"));
assert("empty parts", TD.activityOf([]).length === 0);
assert("idempotent activityOf", JSON.stringify(TD.activityOf(parts)) === JSON.stringify(TD.activityOf(parts)));

assert("exports reducer", TD && typeof TD.applyEvent === "function" && typeof TD.empty === "function");

var doc = TD.empty("t1");
var d1 = TD.applyEvent(doc, { type: "token", content: "Hello", seq: 1, turn_id: "t1" });
assert("token appends", TD.textOf(d1.parts) === "Hello");
var d1b = TD.applyEvent(d1, { type: "token", content: "Hello", seq: 1, turn_id: "t1" });
assert("seq dedupe same object", d1b === d1);
var d2 = TD.applyEvent(d1, { type: "token", content: " world", seq: 2 });
assert("second token appends", TD.textOf(d2.parts).indexOf("Hello") === 0 && TD.textOf(d2.parts).indexOf("world") >= 0);
var d3 = TD.applyEvent(d2, {
  type: "done",
  content: "Everything checks out.",
  seq: 3,
});
assert("done keeps reasoning", d3.parts.some(function (p) { return p.type === "reasoning"; }));
assert("done text wins", TD.textOf(d3.parts) === "Everything checks out.");
var d3b = TD.applyEvent(d3, { type: "done", content: "Everything checks out.", seq: 3 });
assert("done seq dedupe", d3b === d3);

var hydrated = TD.fromMessage({
  turn_id: "t9",
  content: "Everything checks out.",
  parts: parts,
});
assert("hydrate text", TD.textOf(hydrated.parts) === "Everything checks out.");
assert("hydrate keeps prior text as reasoning", hydrated.parts.some(function (p) {
  return p.type === "reasoning" && String(p.text).indexOf("300 seconds") >= 0;
}));
assert("hydrate activity", TD.activityOf(hydrated.parts).some(function (r) { return r.kind === "thought" || r.kind === "tool"; }));

var runThenDone = TD.empty("t2");
runThenDone = TD.applyEvent(runThenDone, {
  type: "progress",
  step: { kind: "tool", title: "file_read", detail: "", state: "running" },
});
runThenDone = TD.applyEvent(runThenDone, {
  type: "progress",
  step: { kind: "tool", title: "file_read", detail: "hitl.py", state: "done" },
});
var tools = runThenDone.parts.filter(function (p) { return p.type === "tool"; });
assert("tool running replaced by done", tools.length === 1 && tools[0].state === "done");

var old = TD.hydrateMessage({
  role: "assistant",
  content: "The timeout is 300 seconds.",
  activity: [{ kind: "tool", title: "file_read", detail: "hitl.py", state: "done" }],
});
assert("hydrateMessage turn_id", String(old.turn_id || "").indexOf("legacy-") === 0);
assert("hydrateMessage parts", TD.textOf(old.parts) === "The timeout is 300 seconds.");
assert("hydrateMessage activity", TD.activityOf(old.parts).some(function (r) { return r.kind === "tool"; }));

var approved = TD.empty("hitl-1");
approved = TD.applyEvent(approved, {
  type: "hitl",
  state: "pending",
  interrupt_id: "abc",
  tool: "file_write",
  payload: { tool: "file_write", interrupt_id: "abc", path: "x" },
});
assert("hitl pending pauses", approved.status === "paused");
approved = TD.applyEvent(approved, {
  type: "hitl",
  state: "approved",
  interrupt_id: "abc",
  tool: "file_write",
  payload: { tool: "file_write", interrupt_id: "abc", path: "x" },
});
assert("hitl approved streams", approved.status === "streaming");
var replayed = TD.applyEvent(approved, {
  type: "hitl",
  state: "pending",
  interrupt_id: "abc",
  tool: "file_write",
  payload: { tool: "file_write", interrupt_id: "abc" },
});
var replayHitl = replayed.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("replay cannot regress approved to pending", replayHitl && replayHitl.state === "approved");
assert("replay keeps payload", replayHitl && replayHitl.payload && replayHitl.payload.path === "x");
assert("replay status stays streaming", replayed.status === "streaming");

// ── Sequential gates: one part PER GATE, never one per turn ─────────
// The 2026-09-19 "approved twice then silence" incident. partKey used to
// return a bare "hitl", so this second gate OVERWROTE the first: the
// document held one decision while the transcript showed two cards, and
// the renderer had no authority left to reconcile against. Both gates must
// survive, in the order they were asked, each keeping its own state.
var secondGate = TD.applyEvent(replayed, {
  type: "hitl",
  state: "pending",
  interrupt_id: "def",
  tool: "python_exec",
  payload: { tool: "python_exec", interrupt_id: "def" },
});
var gates = secondGate.parts.filter(function (p) { return p.type === "hitl"; });
assert("both gates survive", gates.length === 2, "got " + gates.length);
assert("first gate keeps its claim", gates[0] && gates[0].interrupt_id === "abc" && gates[0].state === "approved");
assert("second gate is its own part", gates[1] && gates[1].interrupt_id === "def" && gates[1].state === "pending");
assert("gates keep ask order", gates[0].interrupt_id === "abc" && gates[1].interrupt_id === "def");
assert("new gate pauses", secondGate.status === "paused");

// A pending gate anywhere blocks the turn, even when a LATER gate settled.
// Reading status off "the last hitl part" reported streaming while the
// graph sat blocked on an earlier gate.
var secondApproved = TD.applyEvent(secondGate, {
  type: "hitl", state: "approved", interrupt_id: "def", tool: "python_exec",
});
assert("all gates settled resumes the turn", secondApproved.status === "streaming");
var thirdPending = TD.applyEvent(secondApproved, {
  type: "hitl", state: "pending", interrupt_id: "ghi", tool: "shell_exec",
  payload: { tool: "shell_exec", interrupt_id: "ghi" },
});
assert("three gates tracked", thirdPending.parts.filter(function (p) { return p.type === "hitl"; }).length === 3);
assert("a pending gate after settled ones still pauses", thirdPending.status === "paused");

// hitlPartOf answers "what is being asked right now", not "what is newest".
assert("hitlPartOf prefers the pending gate", TD.hitlPartOf(thirdPending.parts).interrupt_id === "ghi");
assert("hitlPartsOf returns every gate", TD.hitlPartsOf(thirdPending.parts).length === 3);
assert("hitlPartOf falls back to newest when all settled",
  TD.hitlPartOf(secondApproved.parts).interrupt_id === "def");

// Identity is shared with the renderer: distinct gates MUST key apart, or
// two cards collapse onto one DOM slot.
assert("partKey separates gates",
  TD.partKey(gates[0]) !== TD.partKey(gates[1]));
assert("partKey is stable across a state change",
  TD.partKey(gates[1]) === TD.partKey({ type: "hitl", interrupt_id: "def", state: "approved" }));

// ── decided_locally: evidence this tab watched the decision ─────────
// The renderer lets it outrank a stale gate-registry row, so where it comes
// from matters. 2026-09-19: between an approve and the next /status resync,
// the registry still listed the gate as pending and sorted the settled card
// back underneath the answer until a refresh.
var localDecision = TD.applyEvent(TD.empty("t9"), {
  type: "hitl", state: "approved", interrupt_id: "L1", tool: "shell_exec",
  decided_locally: true,
});
var lp = localDecision.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("a local decision is marked on the part", lp && lp.decided_locally === true);

var serverStamp = TD.applyEvent(TD.empty("t9"), {
  type: "hitl", state: "approved", interrupt_id: "L1", tool: "shell_exec",
});
var sp = serverStamp.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("a server frame is NOT marked local", sp && sp.decided_locally === undefined);

// It must not survive a round trip through the server. A persisted part
// cannot vouch for "this tab watched the operator decide", and inheriting
// the flag across a refresh would let a stale part beat a pending gate row —
// re-opening the invent-an-approval hole the registry rule exists to close.
var rehydrated = TD.applyEvent(TD.empty("t9"), {
  type: "hydrate",
  parts: [{ type: "hitl", state: "approved", interrupt_id: "L1", decided_locally: true }],
  content: "done",
});
var rp = rehydrated.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("hydrate strips decided_locally", rp && rp.decided_locally === undefined);
assert("hydrate keeps the rest of the gate", rp && rp.interrupt_id === "L1" && rp.state === "approved");

// ── Capacity fast-path: content-key dedupe + reset semantics ────────
// chat.js paintCapacityReply forwards reply+turn_id but NOT seq, so the
// eventKey for capacity events is content-derived. This locks the
// 2026-09-04 live-delivery incident: instant slashes skip beginTurn, the
// stale _docs.live kept the prior capacity key, and the identical Retry
// re-send hit doc.seen[key] and silently dropped (the "_No response
// received." card). The fix resets the doc per send — locked here.
var capDoc = TD.empty("live");
var cap1 = TD.applyEvent(capDoc, {
  type: "capacity", reply: "MISSION ON", turn_id: "live", source: "capacity",
});
assert("capacity reply paints", TD.textOf(cap1.parts) === "MISSION ON");
assert("capacity marks turn done", cap1.status === "done");
var cap1b = TD.applyEvent(cap1, {
  type: "capacity", reply: "MISSION ON", turn_id: "live", source: "capacity",
});
assert("identical re-send deduped on a STALE doc", cap1b === cap1);
// What _resetTurnState() guarantees per send: a fresh empty document
// accepts the identical event again — the Retry path after the fix.
var cap2 = TD.applyEvent(TD.empty("live"), {
  type: "capacity", reply: "MISSION ON", turn_id: "live", source: "capacity",
});
assert("fresh doc (post _resetTurnState) repaints identical reply",
  cap2 !== cap1 && TD.textOf(cap2.parts) === "MISSION ON");
// A DIFFERENT reply must never be deduped even on the stale doc.
var cap3 = TD.applyEvent(cap1, {
  type: "capacity", reply: "YOLO OFF", turn_id: "live", source: "capacity",
});
assert("different reply paints even on stale doc", cap3 !== cap1 && TD.textOf(cap3.parts) === "YOLO OFF");
// Capacity REPLACEs the stream — never appends. The 2026-09-05 doubling
// regression fed the ack through the token path (append); a late capacity
// repaint over an already-streamed partial must leave ONLY the ack.
var capTok = TD.applyEvent(TD.empty("live"), {
  type: "token", content: "partial streamed text", turn_id: "live",
});
var capRep = TD.applyEvent(capTok, {
  type: "capacity", reply: "MISSION ON", turn_id: "live", source: "capacity",
});
assert("capacity REPLACES streamed text (never appends)",
  TD.textOf(capRep.parts) === "MISSION ON" && capRep.stream === "MISSION ON");

if (fail) process.exit(1);
console.log("all ok");
