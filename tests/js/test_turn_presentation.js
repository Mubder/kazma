/**
 * The derived header model — docs/plans/UNIFIED_TURN_BLOCK.md §3 and §7.
 *
 * §7 is explicit that the header is a MAPPING from server facts, not a
 * second execution state machine in the browser. These tests are mostly
 * about what the mapping must REFUSE to say: that a turn finished, that a
 * cancellation took, that a gate is settled, or that a dropped socket is
 * an outcome. Each of those has its own incident behind it.
 *
 * Run: node tests/js/test_turn_presentation.js
 */
"use strict";

const path = require("path");

const ROOT = path.resolve(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_presentation.js"));

const TD = global.KazmaTurnDocument;
const TP = global.KazmaTurnPresentation;
const P = TP.PHASE;

let pass = 0;
function ok(name, cond, got) {
  if (cond) { pass++; console.log("OK   " + name); return; }
  console.log("FAIL " + name + (got === undefined ? "" : "  (got " + JSON.stringify(got) + ")"));
  process.exitCode = 1;
}

function doc(over) {
  return Object.assign(
    { turnId: "t1", status: "streaming", parts: [], stream: "", elapsedS: 0, elapsedAtMs: 0 },
    over || {}
  );
}
const TEXT = { type: "text", text: "hello" };
function gate(id, state) {
  return { type: "hitl", interrupt_id: id, tool: "file_write", state: state };
}

// ── Phase: before the first token ──────────────────────────────────────
ok("an acknowledged turn with nothing in it is Queued",
  TP.header(doc(), { serverGenerating: true }).phase === P.QUEUED);
ok("...and still offers Stop",
  TP.header(doc(), { serverGenerating: true }).canStop === true);
ok("content makes it Working",
  TP.header(doc({ parts: [TEXT] }), { serverGenerating: true }).phase === P.WORKING);

// ── Terminal states are distinguishable ────────────────────────────────
ok("done is Completed",
  TP.header(doc({ status: "done", parts: [TEXT] }), {}).phase === P.COMPLETED);
ok("error is Failed",
  TP.header(doc({ status: "error" }), {}).phase === P.FAILED);
ok("a finished turn does not offer Stop",
  TP.header(doc({ status: "done", parts: [TEXT] }), {}).canStop === false);
ok("a failed turn offers Retry",
  TP.header(doc({ status: "error" }), { retrySupported: true }).canRetry === true);
ok("...and a completed one does not",
  TP.header(doc({ status: "done" }), { retrySupported: true }).canRetry === false);

// ── A stop request is NOT a cancellation ───────────────────────────────
// Plan §7: "Await server acknowledgement; do not prematurely mark
// cancelled." The client may say it asked; only the server says it took.
const stopping = TP.header(doc({ parts: [TEXT] }),
  { serverGenerating: true, stopRequested: true });
ok("asking to stop reads as Stopping", stopping.phase === P.STOPPING, stopping.phase);
ok("...not as Cancelled", stopping.phase !== P.CANCELLED);
ok("the server saying cancelled reads as Cancelled",
  TP.header(doc({ parts: [TEXT] }), { cancelled: true }).phase === P.CANCELLED);

// ── Approval ───────────────────────────────────────────────────────────
const paused = doc({ status: "paused", parts: [gate("g1", "pending"), TEXT] });
const livePaused = { gateViews: [
  { interrupt_id: "g1", state: "pending", interactive: true },
] };
ok("a pending gate reads as Approval required",
  TP.header(paused, livePaused).phase === P.APPROVAL);
ok("...and says how many are waiting",
  TP.header(paused, livePaused).awaiting === 1);

// A pending part the registry has NOT confirmed is not yet a row: the
// renderer omits it rather than mint Approve buttons for a gate nobody
// has vouched for, and the header counts what the group shows. Claiming
// "1 awaiting your decision" with no row to act on points the reader at
// something invisible — and mid-resume it produced a header reading "4
// approvals" above a group reading "3 requests" (observed in the
// browser). The omission is loud, not silent: turn_view's verify() raises
// gate-missing and the renderer resyncs once.
ok("an unconfirmed pending gate is not counted as waiting",
  TP.header(paused, {}).awaiting === 0);
ok("...and the header does not claim a decision is due",
  TP.header(paused, {}).phase !== P.APPROVAL);
// A SETTLED stamp needs no confirmation — it is history, and history
// with no row would be a decision the reader cannot see.
ok("a settled gate is counted without a live view",
  TP.header(doc({ status: "done", parts: [gate("g0", "approved"), TEXT] }), {})
    .counts.gates === 1);

// The SERVER's view outranks the part stamp. A part still stamped pending
// while the registry has recorded the decision is exactly what put
// "Approved — running…" under a finished reply (2026-09-19, live install).
const settledView = TP.header(paused, {
  gateViews: [{ interrupt_id: "g1", state: "approved", interactive: false }],
});
ok("a settled gate view outranks a stale pending stamp",
  settledView.awaiting === 0, settledView.awaiting);
ok("...so the header stops asking for a decision",
  settledView.phase !== P.APPROVAL, settledView.phase);

const twoPending = TP.header(
  doc({ status: "paused", parts: [gate("g1", "pending"), gate("g2", "pending")] }),
  { gateViews: [
    { interrupt_id: "g1", state: "pending", interactive: true },
    { interrupt_id: "g2", state: "pending", interactive: true },
  ] });
ok("two simultaneous gates are both counted", twoPending.awaiting === 2);

// A pause with no pending gate is not "approval required" — the gate
// settled and the graph has not reported back.
ok("a settled pause reads as Resuming",
  TP.header(doc({ status: "paused", parts: [gate("g1", "approved")] }),
    { serverGenerating: true }).phase === P.RESUMING);

// ── Connection is reported separately from execution ───────────────────
// Plan §3: "Disconnection is not completion or failure."
const dropped = TP.header(doc({ parts: [TEXT] }),
  { serverGenerating: true, streamLive: false });
ok("a dropped stream still reads as Working", dropped.phase === P.WORKING);
ok("...and says the connection is the problem",
  dropped.connection === "reconnecting", dropped.connection);
ok("a live stream reads live",
  TP.header(doc({ parts: [TEXT] }), { streamLive: true }).connection === "live");
ok("an idle finished turn is neither",
  TP.header(doc({ status: "done", parts: [TEXT] }), {}).connection === "idle");

// ── Elapsed comes from the server, and says when ───────────────────────
let d = TD.applyEvent(TD.empty("t1"), {
  type: "turn_heartbeat", elapsed_s: 12.5, seq: 1,
});
ok("a heartbeat records the server's elapsed", d.elapsedS === 12.5);
ok("...and when it arrived", d.elapsedAtMs > 0);

// Monotone: a replayed older heartbeat must not walk the clock backwards.
d = TD.applyEvent(d, { type: "turn_heartbeat", elapsed_s: 3.0, seq: 2 });
ok("an older heartbeat does not rewind the clock", d.elapsedS === 12.5);
d = TD.applyEvent(d, { type: "turn_heartbeat", elapsed_s: 30.0, seq: 3 });
ok("a newer one advances it", d.elapsedS === 30.0);

d = TD.applyEvent(d, {
  type: "turn_complete", content: "done", duration_ms: 41000, seq: 4,
});
ok("the terminal frame's measured duration wins", d.elapsedS === 41);
ok("...and the turn is Completed", TP.header(d, {}).phase === P.COMPLETED);
ok("...frozen: the header must not keep counting",
  TP.header(d, {}).terminal === true);

// A document that was never told an elapsed reports zero rather than
// inventing one from a client clock — the "Done 0s while still working"
// defect was a client clock answering a question only the server can.
ok("no server stamp means no elapsed",
  TP.header(doc(), { serverGenerating: true }).elapsed.seconds === 0);

// ── Counts ─────────────────────────────────────────────────────────────
const busy = doc({
  parts: [
    { type: "tool", name: "a", call_id: "1", state: "done" },
    { type: "tool", name: "b", call_id: "2", state: "done" },
    { type: "status", title: "Planning" },
    { type: "reasoning", text: "thinking" },
    gate("g1", "approved"),
    TEXT,
  ],
});
const counts = TP.header(busy, {}).counts;
ok("tools are counted", counts.tools === 2, counts);
ok("thoughts are counted", counts.thoughts === 1, counts);
ok("gates are counted", counts.gates === 1, counts);
ok("steps cover tools, statuses and gates", counts.steps === 4, counts);

// ── It must not crash on nonsense ──────────────────────────────────────
ok("a null document still produces a header", !!TP.header(null, {}));
ok("...as Queued", TP.header(null, {}).phase === P.QUEUED);
ok("no facts at all is fine", !!TP.header(doc(), undefined));

console.log(pass + " checks passed");
