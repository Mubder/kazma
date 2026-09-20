/**
 * Phase 0 reproductions — renderer layer.
 *
 * docs/plans/UNIFIED_TURN_BLOCK.md Phase 0 asks for a reproduction of "the
 * current split bar, separate gate cards, and forced-open thoughts" before
 * anything is changed. These are the separate-gate-cards half, written
 * against the TARGET contract so they become the Phase 3 acceptance tests
 * unchanged.
 *
 * They fail today. That is the point: `slotPlan` currently emits one
 * top-level slot per gate, which is the layout contract the unified plan
 * replaces with one keyed approval region.
 *
 * Run:  node tests/js/test_unified_turn_block_phase0.js          (human)
 *       node tests/js/test_unified_turn_block_phase0.js --json   (machine)
 *
 * --json always exits 0 and prints {name: {ok, error}} so
 * tests/test_unified_turn_block_phase0.py can track each behavior
 * separately instead of collapsing four unmet requirements into one red
 * dot. Phase 3 deletes the xfail markers there; nothing here changes.
 *
 * ── Target contract asserted below ──────────────────────────────────────
 *
 *   slotPlan(doc, has, TD, gateState) -> [
 *     { key: 'workbench',  kind: 'workbench' },
 *     { key: 'approvals',  kind: 'approvals', rows: [
 *         { key: 'hitl:<gate id>', part, state }, ...   // ask order
 *     ]},
 *     { key: 'text',       kind: 'text' },
 *   ]
 *
 * One approvals entry when at least one gate exists, zero when none do
 * (plan §3). Rows in ask order, keyed by gate id, pending or settled alike
 * (U09). The answer is always last of the three and never a descendant of
 * the other two (plan §5, "keyed containment").
 */
"use strict";

const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_view.js"));

const TD = global.KazmaTurnDocument;
const TV = global.KazmaTurnView;

/** The stored form of tests/fixtures/unified_turn/layout/four_sequential_gates.json. */
function gate(id, tool, state) {
  return {
    type: "hitl",
    tool: tool,
    state: state,
    interrupt_id: id,
    payload: { tool: tool, interrupt_id: id },
  };
}

function fourGateDoc() {
  return {
    turnId: "utb-fixture-turn-1",
    status: "done",
    parts: [
      gate("gate-1", "file_write", "approved"),
      gate("gate-2", "shell_exec", "denied"),
      gate("gate-3", "file_write", "approved"),
      gate("gate-4", "file_delete", "approved"),
      { type: "text", text: "Scaffold ready." },
    ],
  };
}

function pendingDoc() {
  return {
    turnId: "utb-fixture-turn-3",
    status: "paused",
    parts: [
      gate("gate-1", "file_write", "approved"),
      gate("gate-p1", "file_delete", "pending"),
      { type: "text", text: "Working on it." },
    ],
  };
}

const HAS = { workbench: false, text: true };
const byPartState = (p) => String((p && p.state) || "pending");

function planOf(doc) {
  return TV.slotPlan(doc, HAS, TD, byPartState);
}

function kinds(plan) {
  return plan.map((e) => e.kind);
}

const CHECKS = {
  /** Plan §3: "Zero groups when there are no gates; exactly one when at
   *  least one exists." */
  approvals_is_one_region() {
    const plan = planOf(fourGateDoc());
    const groups = plan.filter((e) => e.kind === "approvals");
    assert.strictEqual(
      groups.length, 1,
      "four gates must produce exactly one approvals region, got " +
        groups.length + " (kinds: " + kinds(plan).join(", ") + ")"
    );
    assert.strictEqual(
      plan.filter((e) => e.kind === "hitl").length, 0,
      "no gate may still be a top-level slot"
    );
  },

  /** Plan §3 and U09: one row per actual gate ID, in ask order, even when
   *  two of them are the same tool. */
  four_gates_four_rows_in_ask_order() {
    const plan = planOf(fourGateDoc());
    const group = plan.find((e) => e.kind === "approvals");
    assert.ok(group, "no approvals region to hold rows");
    const keys = (group.rows || []).map((r) => r.key);
    assert.deepStrictEqual(
      keys,
      ["hitl:gate-1", "hitl:gate-2", "hitl:gate-3", "hitl:gate-4"],
      "rows must be one per gate id, in ask order"
    );
  },

  /** Plan §3: "Approval group location stays stable above the answer." A
   *  pending gate used to sort BELOW the text, so approving one moved the
   *  answer between containers. */
  approvals_stay_above_the_answer() {
    for (const doc of [fourGateDoc(), pendingDoc()]) {
      const plan = planOf(doc);
      const iApprovals = plan.findIndex((e) => e.kind === "approvals");
      const iText = plan.findIndex((e) => e.kind === "text");
      assert.ok(iApprovals >= 0, "no approvals region");
      assert.ok(iText >= 0, "no answer region");
      assert.ok(
        iApprovals < iText,
        "approvals must precede the answer in every phase (got " +
          kinds(plan).join(", ") + ")"
      );
    }
  },

  /** Plan §3: a pending gate is a row in the SAME group as the settled
   *  ones, not a separate container. Sequential approval must not split
   *  the group in two. */
  pending_and_settled_share_one_group() {
    const plan = planOf(pendingDoc());
    const groups = plan.filter((e) => e.kind === "approvals");
    assert.strictEqual(groups.length, 1, "pending + settled split the group");
    const rows = groups[0].rows || [];
    assert.deepStrictEqual(
      rows.map((r) => r.key),
      ["hitl:gate-1", "hitl:gate-p1"],
      "the pending gate must be a row beside the settled one"
    );
    const pending = rows.filter((r) => String(r.state) === "pending");
    assert.strictEqual(pending.length, 1, "the pending row lost its state");
  },

  /** Plan §3: "Zero groups when there are no gates". A turn that never
   *  paused must not mint an empty approvals region. */
  no_gates_no_group() {
    const plan = planOf({
      turnId: "t", status: "done", parts: [{ type: "text", text: "hi" }],
    });
    assert.strictEqual(
      plan.filter((e) => e.kind === "approvals").length, 0,
      "an approvals region was minted for a turn with no gates"
    );
  },
};

const results = {};
for (const [name, fn] of Object.entries(CHECKS)) {
  try {
    fn();
    results[name] = { ok: true, error: "" };
  } catch (err) {
    results[name] = { ok: false, error: String((err && err.message) || err) };
  }
}

if (process.argv.includes("--json")) {
  process.stdout.write(JSON.stringify(results, null, 2) + "\n");
  process.exit(0);
}

let failed = 0;
for (const [name, r] of Object.entries(results)) {
  if (r.ok) {
    console.log("ok   " + name);
  } else {
    failed++;
    console.log("FAIL " + name + "\n       " + r.error);
  }
}
console.log(
  failed
    ? failed + " of " + Object.keys(results).length +
      " target behaviors not implemented (expected before Phase 3)"
    : "all target behaviors implemented"
);
process.exit(failed ? 1 : 0);
