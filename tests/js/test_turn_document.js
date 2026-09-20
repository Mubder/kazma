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
const grown = TD.mergeParts(
  [{ type: "reasoning", text: "First notes." }],
  [{ type: "reasoning", text: "Second hop." }],
);
const rparts = grown.filter((p) => p.type === "reasoning");
assert("one thoughts fold", rparts.length === 1);
assert("fold keeps both hops", rparts[0].text.indexOf("First notes.") >= 0
  && rparts[0].text.indexOf("Second hop.") >= 0);
assert("partKey is stable for reasoning",
  TD.partKey({ type: "reasoning", text: "a" }) === TD.partKey({ type: "reasoning", text: "bbb" }));
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

// ── view on a decision event (B: approve 200, not decided_locally) ──
// The renderer lets it outrank a stale gate-registry row, so where it comes
// from matters. 2026-09-19: between an approve and the next /status resync,
// the registry still listed the gate as pending and sorted the settled card
// back underneath the answer until a refresh.
var viewed = TD.applyEvent(TD.empty("t9"), {
  type: "hitl", state: "approved", interrupt_id: "L1", tool: "shell_exec",
  view: { interrupt_id: "L1", state: "inflight", interactive: false, slot: "settled" },
});
var vp = viewed.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("approve 200 view rides on the part", vp && vp.view && vp.view.state === "inflight");
assert("decided_locally is gone", vp && vp.decided_locally === undefined);

var rehydrated = TD.applyEvent(TD.empty("t9"), {
  type: "hydrate",
  parts: [{ type: "hitl", state: "approved", interrupt_id: "L1", view: { state: "approved" } }],
  content: "done",
});
var rp = rehydrated.parts.filter(function (p) { return p.type === "hitl"; })[0];
assert("hydrate keeps the view", rp && rp.view && rp.view.state === "approved");
assert("hydrate keeps the rest of the gate", rp && rp.interrupt_id === "L1" && rp.state === "approved");

// ── activityOf is not a third opinion about a gate ──────────────────
// The workbench row could print "Waiting for approval" beside a card
// reading "Approved", because it labelled from the raw part stamp while the
// renderer labelled from the host's resolver. Same fact, two answers.
var gateParts = [{ type: "hitl", interrupt_id: "w1", state: "pending", tool: "file_delete" }];
var rawRow = TD.activityOf(gateParts)[0];
assert("without a resolver it reads the part stamp", rawRow.title === "Waiting for approval");
var resolvedRow = TD.activityOf(gateParts, function () { return "inflight"; })[0];
assert("with a resolver the row follows it", resolvedRow.title === "Approved",
  resolvedRow.title);
var awaitingRow = TD.activityOf(gateParts, function () { return "awaiting"; })[0];
assert("'awaiting' reads as waiting, not as resolved",
  awaitingRow.title === "Waiting for approval", awaitingRow.title);
assert("the row still names the tool", resolvedRow.detail === "file_delete");

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

// ── Tool call identity (UNIFIED_TURN_BLOCK.md Phase 1) ─────────────────
// The old key was name + state + result[:80]. Two things followed from it,
// both wrong: the SAME call changed identity when it finished, so the
// renderer tore its row down and rebuilt it (losing expansion and focus) on
// every update; and two concurrent calls to one tool shared a key, so the
// second overwrote the first. Both are now keyed by the graph's run id.

var toolRunning = { type: "tool", name: "file_read", call_id: "run-1",
  result: "", state: "running" };
var toolDone = { type: "tool", name: "file_read", call_id: "run-1",
  result: "alpha", state: "done" };

assert("tool key is the call id",
  TD.partKey(toolRunning) === "tool#run-1");
assert("tool key is stable across running -> done",
  TD.partKey(toolRunning) === TD.partKey(toolDone));
assert("two calls to one tool are two keys",
  TD.partKey(toolDone) !== TD.partKey({ type: "tool", name: "file_read",
    call_id: "run-2", result: "alpha", state: "done" }));
assert("a call-id-less part keeps the legacy content key",
  TD.partKey({ type: "tool", name: "file_read", result: "x", state: "done" })
    === "tool:file_read:done:x");

// merge must ADVANCE the row, not drop the second stamp. The old
// duplicate-key branch returned early, which with a stable key would have
// frozen every tool row at "running".
var merged = TD.mergeParts([toolRunning], [toolDone]);
var mergedTools = merged.filter(function (p) { return p.type === "tool"; });
assert("one call stays one part", mergedTools.length === 1);
assert("the finished stamp wins", mergedTools[0].state === "done"
  && mergedTools[0].result === "alpha");

// ...and never backwards. A replayed start frame after the result landed
// must not un-finish the call.
var backwards = TD.mergeParts([toolDone], [toolRunning]);
var backTools = backwards.filter(function (p) { return p.type === "tool"; });
assert("a late running frame does not un-finish a call",
  backTools.length === 1 && backTools[0].state === "done"
  && backTools[0].result === "alpha");

// A terminal frame with no result must not blank the one already delivered.
var blanked = TD.mergeToolPart(toolDone,
  { type: "tool", name: "file_read", call_id: "run-1", result: "", state: "done" });
assert("an empty terminal result does not erase the delivered one",
  blanked.result === "alpha");

// Through applyEvent, which is the path the SSE frames actually take.
var tDoc = TD.applyEvent(TD.empty("live"), {
  type: "tool_call", tool_name: "file_read", tool_call_id: "run-9",
  inputs: "{}", seq: 1,
});
tDoc = TD.applyEvent(tDoc, {
  type: "tool_result", tool_name: "file_read", tool_call_id: "run-9",
  result: "done!", seq: 2,
});
var liveTools = tDoc.parts.filter(function (p) { return p.type === "tool"; });
assert("start + result is ONE row through applyEvent", liveTools.length === 1);
assert("the row carries the final result", liveTools[0].result === "done!");
assert("the row keeps the call id", liveTools[0].call_id === "run-9");

// Two concurrent calls to the same tool stay two rows.
var twoDoc = TD.applyEvent(TD.empty("live"), {
  type: "tool_call", tool_name: "file_read", tool_call_id: "run-a", seq: 1,
});
twoDoc = TD.applyEvent(twoDoc, {
  type: "tool_call", tool_name: "file_read", tool_call_id: "run-b", seq: 2,
});
assert("concurrent calls to one tool are two rows",
  twoDoc.parts.filter(function (p) { return p.type === "tool"; }).length === 2);

// ── Activity rows carry the part key ──────────────────────────────────
var actRows = TD.activityOf(twoDoc.parts);
assert("activity rows carry an id", actRows.length === 2
  && actRows[0].id === "tool#run-a" && actRows[1].id === "tool#run-b");
assert("a row with no timestamp omits ts (Python omits it too)",
  !Object.prototype.hasOwnProperty.call(actRows[0], "ts"));
// ...and the id survives the round trip back into parts, or a history load
// would split one call into two rows.
var roundTrip = TD.mergeParts([], TD.activityToParts(actRows));
assert("activity -> parts keeps the call id",
  roundTrip.filter(function (p) { return p.type === "tool"; }).length === 2);

// ── legacyTurnId agrees with turn_document.py ─────────────────────────
// Pinned, not just prefix-checked. The whole point is that the SERVER
// computes the same string; a "starts with legacy-" assertion is what let
// sha256 on one side and a 32-bit string hash on the other both look
// correct for months (UNIFIED_TURN_BLOCK_PHASE0.md §6.1).
assert("legacyTurnId is the shared value",
  TD.legacyTurnId({ ts: "2026-09-20T10:00:00Z", content: "Hello there." })
    === "legacy-0cdee065e20a7d91");
// The hash walks UTF-8 bytes. JavaScript strings are UTF-16, so a naive
// port agrees on ASCII and diverges on the first Arabic character or emoji
// (a surrogate pair) — in this product, the normal case.
assert("legacyTurnId walks UTF-8, not UTF-16",
  TD.legacyTurnId({ ts: "2026-09-20T11:30:00Z", content: "تم الحفظ 😀" })
    === "legacy-4373231117a879b5");

// ── Document revision (UNIFIED_TURN_BLOCK.md Phase 1, invariant U05) ───
// _resyncDelivery fetches /status and /messages in PARALLEL and either can
// land late. Before revisions the only defence was that mergeParts happens
// to be additive — which says nothing about `status`, so a stale row could
// stamp the turn done, or paused, over the truth.

function hydrateEv(rev, extra) {
  var ev = { type: "hydrate", turn_id: "t-rev", rev: rev, schema: 2 };
  for (var k in (extra || {})) {
    if (Object.prototype.hasOwnProperty.call(extra, k)) ev[k] = extra[k];
  }
  return ev;
}

var revDoc = TD.applyEvent(TD.empty("t-rev"), hydrateEv(7, {
  content: "the newer answer", parts: [{ type: "text", text: "the newer answer" }],
}));
assert("a hydrate carries its revision", revDoc.rev === 7);
assert("...and its schema", revDoc.schema === 2);

var stale = TD.applyEvent(revDoc, hydrateEv(3, {
  content: "an older answer",
  parts: [{ type: "text", text: "an older answer" }],
  open: true,
}));
assert("a stale snapshot is refused outright", stale === revDoc);
assert("...so it cannot regress the answer",
  TD.textOf(stale.parts) === "the newer answer");
assert("...and cannot regress the status", stale.status === "done");

var newer = TD.applyEvent(revDoc, hydrateEv(9, {
  content: "the newest answer",
  parts: [{ type: "text", text: "the newest answer" }],
}));
assert("a newer snapshot applies", TD.textOf(newer.parts) === "the newest answer");
assert("...and advances the revision", newer.rev === 9);

// Equal revisions are NOT stale. The comparison is `<`, not `<=`: two
// hydrates at one revision can still differ (a /messages read and a
// /status-driven repaint of the same row reach applyEvent by different
// routes), and refusing the second would be a lost repaint, not a
// protected one. An IDENTICAL hydrate is still deduped, but by eventKey —
// that is the content dedupe, a separate rule from the revision rule, and
// conflating them is how a refresh mid-pause loses its own paint.
var sameRev = TD.applyEvent(revDoc, hydrateEv(7, {
  content: "the newer answer, corrected",
  parts: [{ type: "text", text: "the newer answer, corrected" }],
  open: true, pending: true,
}));
assert("an equal revision is not refused", sameRev !== revDoc
  && sameRev.status === "paused"
  && TD.textOf(sameRev.parts) === "the newer answer, corrected");
var identical = TD.applyEvent(revDoc, hydrateEv(7, {
  content: "the newer answer",
  parts: [{ type: "text", text: "the newer answer" }],
}));
assert("an identical hydrate is deduped by content, not by revision",
  identical === revDoc);

// A row written before revisions existed reads as 0 and must still paint
// into a fresh document, or every legacy transcript would go blank.
var legacyRev = TD.applyEvent(TD.empty("t-rev"), {
  type: "hydrate", turn_id: "t-rev", content: "old row",
  parts: [{ type: "text", text: "old row" }],
});
assert("an unversioned row still hydrates", TD.textOf(legacyRev.parts) === "old row");

// A snapshot MERGES, it does not replace. It covers what was durable when
// it was taken; tokens streamed since are not in it, and an omitted part
// is ambiguity rather than an authoritative removal.
var live = TD.applyEvent(TD.empty("t-rev"), {
  type: "tool_call", tool_name: "file_read", tool_call_id: "live-1", seq: 1,
});
var afterSnap = TD.applyEvent(live, hydrateEv(2, {
  content: "persisted answer",
  parts: [{ type: "text", text: "persisted answer" }],
}));
assert("a snapshot does not drop live parts not in it",
  afterSnap.parts.filter(function (p) { return p.type === "tool"; }).length === 1);
assert("...while still delivering its own",
  TD.textOf(afterSnap.parts) === "persisted answer");

if (fail) process.exit(1);
console.log("all ok");
