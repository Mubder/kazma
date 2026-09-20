/**
 * The convergence oracle — docs/plans/UNIFIED_TURN_BLOCK.md §10.
 *
 *     "For a fixture's covered final revision, compare normalized content
 *      and server-authoritative status from:
 *        1. uninterrupted live delivery;
 *        2. delivery with disconnect, replay, and duplicates;
 *        3. fresh history hydration;
 *        4. restart recovery.
 *      Compare answer text, ordered activity IDs/content, gate IDs/views,
 *      and lifecycle. Exclude local expansion, transient connection state,
 *      and elapsed display sampled at different times."
 *
 * Sources 1-3 are projector-level and live here. Source 4 (restart
 * recovery) needs a real process and lives in the Python suite; this file
 * asserts the shape a recovered row has to project to, so the two halves
 * meet at the same normalized document.
 *
 * Why this and not more unit tests: every past incident in this class was
 * ONE of these paths disagreeing with the others while each looked
 * correct on its own. A replayed frame that appends text twice passes
 * "tokens append"; a hydrate that drops a gate passes "hydrate merges".
 * Only the comparison catches them.
 *
 * Run: node tests/js/test_turn_convergence.js
 */
"use strict";

const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
const TD = global.KazmaTurnDocument;

let pass = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log("OK   " + name); return; }
  console.log("FAIL " + name + (detail === undefined ? "" : "\n       " + detail));
  process.exitCode = 1;
}

// ── The fixture: one turn, two gates, tools, a final answer ────────────
//
// Shaped like what the server actually emits: seq-stamped frames, the
// tool call id on both halves of a tool, a gate that settles, and a
// terminal frame whose content replaces the stream.
const SCRIPT = [
  { seq: 1, type: "status", message: "Planning" },
  { seq: 2, type: "token", content: "I will write the file" },
  { seq: 3, type: "tool_call", tool_name: "file_read", tool_call_id: "r1", inputs: "{}" },
  { seq: 4, type: "tool_result", tool_name: "file_read", tool_call_id: "r1", result: "ok" },
  { seq: 5, type: "hitl", state: "pending", interrupt_id: "g1", tool: "file_write",
    payload: { tool: "file_write", interrupt_id: "g1" } },
  { seq: 6, type: "hitl", state: "approved", interrupt_id: "g1", tool: "file_write",
    payload: { tool: "file_write", interrupt_id: "g1" } },
  { seq: 7, type: "tool_call", tool_name: "file_write", tool_call_id: "r2", inputs: "{}" },
  { seq: 8, type: "tool_result", tool_name: "file_write", tool_call_id: "r2", result: "written" },
  { seq: 9, type: "hitl", state: "pending", interrupt_id: "g2", tool: "file_delete",
    payload: { tool: "file_delete", interrupt_id: "g2" } },
  { seq: 10, type: "hitl", state: "approved", interrupt_id: "g2", tool: "file_delete",
    payload: { tool: "file_delete", interrupt_id: "g2" } },
  { seq: 11, type: "turn_heartbeat", elapsed_s: 12 },
  { seq: 12, type: "token", content: " and remove the old one" },
  { seq: 13, type: "turn_complete", content: "Wrote README.md and removed src/old.js.",
    duration_ms: 18000 },
];

/**
 * What convergence compares.
 *
 * Deliberately EXCLUDES the things §10 says to exclude: local expansion
 * (not in the document at all), transient connection state, and elapsed
 * sampled at different times. Including elapsed would make every
 * comparison a race, and it is display, not content.
 */
function normalize(doc) {
  const parts = (doc && doc.parts) || [];
  return {
    status: String(doc.status || ""),
    answer: TD.textOf(parts),
    activity: TD.activityOf(parts).map((r) => ({
      id: r.id, kind: r.kind, title: r.title, detail: r.detail || "", state: r.state,
    })),
    partKeys: parts.map(TD.partKey),
    gates: parts.filter((p) => p.type === "hitl").map((p) => ({
      id: TD.interruptIdOf(p), tool: p.tool || "", state: p.state || "",
    })),
  };
}

function apply(events, turnId) {
  let doc = TD.empty(turnId || "t-conv");
  for (const ev of events) doc = TD.applyEvent(doc, ev);
  return doc;
}

// ── 1. Uninterrupted live delivery ─────────────────────────────────────
const live = apply(SCRIPT);
const expected = normalize(live);

ok("the fixture produces an answer",
  expected.answer === "Wrote README.md and removed src/old.js.", expected.answer);
ok("...two gates", expected.gates.length === 2, JSON.stringify(expected.gates));
ok("...and two tool rows",
  expected.activity.filter((r) => r.kind === "tool").length === 2,
  JSON.stringify(expected.activity));
ok("...finishing done", expected.status === "done", expected.status);

// ── 2. Disconnect, replay, duplicates ──────────────────────────────────
//
// The client drops after seq 6, reconnects, and the journal replays from
// its cursor — which means seq 5 and 6 arrive AGAIN, and the frames in
// between arrive late. Every one of these is a real shape: seq dedupe is
// what makes the replay safe, and a gap is what triggers the resync.
{
  const withReplay = SCRIPT.slice(0, 6)            // live up to the approve
    .concat(SCRIPT.slice(3, 8))                    // replay from the cursor
    .concat(SCRIPT.slice(6))                       // then the rest
    .concat([SCRIPT[12], SCRIPT[12]]);             // a duplicated terminal
  const got = normalize(apply(withReplay));
  ok("replay + duplicates converge on the same answer",
    got.answer === expected.answer, got.answer);
  ok("...the same activity, in the same order",
    JSON.stringify(got.activity) === JSON.stringify(expected.activity),
    JSON.stringify(got.activity));
  ok("...the same gates",
    JSON.stringify(got.gates) === JSON.stringify(expected.gates),
    JSON.stringify(got.gates));
  ok("...and the same lifecycle", got.status === expected.status, got.status);
}

// ── 2b. Out-of-order arrival ───────────────────────────────────────────
//
// §10: "Add randomized event schedules around supported ordering/recovery
// semantics using recorded seeds." A deterministic LCG, so a failure is
// reproducible from the seed printed with it.
{
  // §10: "Do not require arbitrary impossible event permutations to
  // succeed silently." A seq-ordered journal cannot deliver frame 10
  // before frame 3 — a cursor replay is in order too. What it CAN do is
  // deliver late and deliver twice, so that is the schedule space:
  // relative order preserved, frames duplicated and re-delivered.
  //
  // Full reordering is left out on purpose, and it is worth saying why
  // it fails: the document has no independent ask order for gates, so
  // gates arriving reversed are recorded reversed. That is correct for
  // an input that cannot occur, and building an ask-order index to
  // satisfy it would be a second ordering authority next to `seq`.
  function delayedAndDuplicated(seed) {
    let s = seed;
    const rnd = (m) => { s = (s * 1103515245 + 12345) & 0x7fffffff; return s % m; };
    const out = [];
    const held = [];
    for (const ev of SCRIPT) {
      // Hold a frame back a little (a slow socket), release earlier ones.
      if (rnd(3) === 0) held.push(ev); else out.push(ev);
      while (held.length && rnd(2) === 0) out.push(held.shift());
      // ...and re-deliver something already sent (a cursor replay).
      if (out.length && rnd(4) === 0) out.push(out[rnd(out.length)]);
    }
    return out.concat(held);
  }
  let stable = true;
  let firstBad = null;
  for (let seed = 1; seed <= 60; seed++) {
    const got = normalize(apply(delayedAndDuplicated(seed)));
    if (JSON.stringify(got) !== JSON.stringify(expected)) {
      stable = false;
      firstBad = "seed=" + seed
        + " answer=" + JSON.stringify(got.answer)
        + " gates=" + JSON.stringify(got.gates)
        + " activity=" + JSON.stringify(got.activity.map((r) => r.id));
      break;
    }
  }
  ok("60 delayed/duplicated schedules converge exactly", stable, firstBad);
}

// ── 3. Fresh history hydration ─────────────────────────────────────────
//
// The server persists parts + activity, and a reload projects them with
// fromMessage. This is the comparison that catches a normalizer that
// stores one shape and reads back another.
{
  const stored = {
    role: "assistant",
    turn_id: "t-conv",
    rev: 7,
    schema: 2,
    content: live.parts.filter((p) => p.type === "text").pop().text,
    parts: live.parts,
    activity: TD.activityOf(live.parts),
  };
  const got = normalize(TD.fromMessage(stored));
  ok("a reload converges on the same answer",
    got.answer === expected.answer, got.answer);
  ok("...the same activity",
    JSON.stringify(got.activity) === JSON.stringify(expected.activity),
    JSON.stringify(got.activity));
  ok("...the same gates",
    JSON.stringify(got.gates) === JSON.stringify(expected.gates),
    JSON.stringify(got.gates));
  ok("...the same part identities",
    JSON.stringify(got.partKeys) === JSON.stringify(expected.partKeys),
    JSON.stringify(got.partKeys));
  ok("...and a finished turn reads finished", got.status === "done", got.status);
}

// ── 3b. Hydration from ACTIVITY alone (a legacy row) ───────────────────
//
// Older rows stored activity without parts. The adapter rebuilds parts
// from it, and the tool rows must come back as the same rows — that
// round trip is what keeps a legacy transcript from splitting one call
// into two.
{
  const legacy = {
    role: "assistant",
    turn_id: "t-conv",
    content: expected.answer,
    activity: TD.activityOf(live.parts),
  };
  const got = normalize(TD.fromMessage(legacy));
  ok("a legacy row converges on the same answer",
    got.answer === expected.answer, got.answer);
  const toolIds = got.activity.filter((r) => r.kind === "tool").map((r) => r.id);
  ok("...with its tool rows intact and distinct",
    toolIds.length === 2 && toolIds[0] !== toolIds[1], JSON.stringify(toolIds));
}

// ── 4. The shape restart recovery has to produce ───────────────────────
//
// The Python half (tests/test_turn_durable_presentation.py, the app-graph
// harness) owns "does a restart keep it". What it has to keep is THIS:
// the normalized document a fresh hydration produces. Stating it here
// means the two halves cannot drift into agreeing about different things.
{
  const durable = {
    role: "assistant",
    turn_id: "t-conv",
    rev: 7,
    schema: 2,
    content: expected.answer,
    parts: live.parts,
  };
  const got = normalize(TD.fromMessage(durable));
  ok("restart recovery has one target shape, and it is hydration's",
    JSON.stringify(got) === JSON.stringify(normalize(TD.fromMessage({
      role: "assistant", turn_id: "t-conv", rev: 7, schema: 2,
      content: expected.answer, parts: live.parts,
      activity: TD.activityOf(live.parts),
    }))),
    JSON.stringify(got.activity));
}

// ── Excluded by §10, and demonstrably so ───────────────────────────────
{
  const a = apply(SCRIPT);
  const b = apply(SCRIPT.map((e) =>
    e.type === "turn_heartbeat" ? Object.assign({}, e, { elapsed_s: 99 }) : e));
  ok("elapsed is excluded from convergence",
    JSON.stringify(normalize(a)) === JSON.stringify(normalize(b)));
  ok("...but it did differ, so the exclusion is doing work",
    a.elapsedS !== b.elapsedS, a.elapsedS + " vs " + b.elapsedS);
}

assert.ok(pass > 0);
console.log(pass + " convergence checks passed");
