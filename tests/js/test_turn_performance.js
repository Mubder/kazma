/**
 * Performance baseline — docs/plans/UNIFIED_TURN_BLOCK.md §11.
 *
 *     "Establish a baseline before implementation with fixtures of 100,
 *      1,000, and 10,000 activity entries and a long answer."
 *
 * What this file gates on and what it merely reports are deliberately
 * different things.
 *
 * GATED: algorithmic properties. A second render of an unchanged document
 * must perform zero DOM mutations no matter how much history is mounted,
 * and the work a render does must not grow super-linearly with it. Those
 * are true or false on any machine, and they are what actually goes
 * wrong: the failure mode here is not "slow", it is "rebuilds all
 * activity HTML for every token" (§11), which is O(history) per frame and
 * becomes O(history x tokens) per turn.
 *
 * REPORTED, not gated: wall-clock. §11 says to "record browser/hardware
 * and tune targets once from baseline with justification, not after
 * failures to make them pass". A number asserted here would be tuned to
 * whatever CI does on a bad morning, which is how a performance gate
 * becomes a ratchet nobody trusts. The numbers are printed so a baseline
 * exists to tune FROM.
 *
 * Run: node tests/js/test_turn_performance.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const ROOT = path.resolve(__dirname, "..", "..");
const MODULES = path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules");

global.window = global;
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(MODULES, "turn_document.js"), "utf8"));
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(MODULES, "turn_view.js"), "utf8"));
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(MODULES, "turn_presentation.js"), "utf8"));

const TD = global.KazmaTurnDocument;
const TV = global.KazmaTurnView;
const TP = global.KazmaTurnPresentation;

let pass = 0;
function ok(name, cond, detail) {
  if (cond) { pass++; console.log("OK   " + name); return; }
  console.log("FAIL " + name + (detail === undefined ? "" : "  " + detail));
  process.exitCode = 1;
}

const LONG_ANSWER = "The quick brown fox. ".repeat(500);   // ~10 KB

/** A turn with `n` activity entries, four gates and a long answer. */
function bigDoc(n) {
  const parts = [];
  for (let i = 0; i < n; i++) {
    if (i % 5 === 0) {
      parts.push({ type: "status", title: "Step " + i, state: "done" });
    } else {
      parts.push({
        type: "tool", name: "tool_" + (i % 17), call_id: "run-" + i,
        result: "result " + i, state: "done",
      });
    }
  }
  for (let g = 1; g <= 4; g++) {
    parts.push({
      type: "hitl", interrupt_id: "g" + g, tool: "file_write",
      state: "approved", payload: { interrupt_id: "g" + g },
    });
  }
  parts.push({ type: "text", text: LONG_ANSWER });
  return { turnId: "perf", status: "done", parts: parts };
}

function ms(fn, iterations) {
  const n = iterations || 1;
  const t0 = process.hrtime.bigint();
  for (let i = 0; i < n; i++) fn();
  const t1 = process.hrtime.bigint();
  return Number(t1 - t0) / 1e6 / n;
}

// ══════════════════════════════════════════════════════════════════════
// Baseline, reported
// ══════════════════════════════════════════════════════════════════════

const SIZES = [100, 1000, 10000];
const report = [];

for (const n of SIZES) {
  const doc = bigDoc(n);
  const activityMs = ms(() => TD.activityOf(doc.parts), 3);
  const planMs = ms(() => TV.slotPlan(doc, { header: true, workbench: true, text: true }, TD), 3);
  const headerMs = ms(() => TP.header(doc, {}), 3);
  report.push({ n, activityMs, planMs, headerMs });
}

console.log("\n  activity entries |  activityOf |   slotPlan |     header");
console.log("  -----------------+-------------+------------+-----------");
for (const r of report) {
  console.log(
    "  " + String(r.n).padStart(16) + " | "
    + r.activityMs.toFixed(2).padStart(9) + "ms | "
    + r.planMs.toFixed(2).padStart(8) + "ms | "
    + r.headerMs.toFixed(2).padStart(7) + "ms"
  );
}
console.log("  node " + process.version + " on " + process.platform + "\n");

// ══════════════════════════════════════════════════════════════════════
// Gated: the properties, not the clock
// ══════════════════════════════════════════════════════════════════════

// Scaling. A 100x increase in history must not cost dramatically more
// than 100x — that is the difference between linear and quadratic, and
// quadratic is what "rebuild all activity HTML per token" looks like.
// The allowance is deliberately loose (4x the linear factor): this is a
// shape check, not a stopwatch.
{
  const small = report[0];
  const big = report[report.length - 1];
  const factor = big.n / small.n;
  for (const key of ["activityMs", "planMs"]) {
    const ratio = (big[key] + 0.01) / (small[key] + 0.01);
    ok(key + " scales roughly linearly with history",
      ratio < factor * 4,
      "x" + ratio.toFixed(1) + " for x" + factor + " more entries");
  }
}

// The header is derived per paint, and it is painted on a 1s ticker while
// a turn runs. It must not walk history linearly — a 10,000-entry turn
// would then pay for its whole history every second.
{
  const small = report[0].headerMs;
  const big = report[report.length - 1].headerMs;
  ok("the header model stays cheap on a long turn",
    big < Math.max(small * 200, 20),
    big.toFixed(2) + "ms at 10k entries vs " + small.toFixed(2) + "ms at 100");
}

// ── Idempotence at scale ──────────────────────────────────────────────
//
// THE property. The renderer's contract is that rendering the same
// document twice performs zero mutations; if that holds only for small
// documents it does not hold.
{
  for (const n of [100, 1000]) {
    const env = makeDocument();
    const view = TV.create({ document: env.document, turnDocument: TD });
    const bubble = env.assistantBubble({ turnId: "perf" });
    env.ROOT.appendChild(bubble);
    const doc = bigDoc(n);

    let built = 0;
    const renderers = {
      has: (kind) => kind !== "workbench" || TD.activityOf(doc.parts).length > 0,
      build(entry) {
        built++;
        const el = env.document.createElement("div");
        if (entry.kind === "text") el.className = "message-text";
        else if (entry.kind === "header") el.className = "turn-header";
        else if (entry.kind === "workbench") el.className = "agent-progress";
        else if (entry.kind === "approvals") {
          el.className = "turn-approvals";
          const host = env.document.createElement("div");
          host.className = "turn-approvals-rows";
          el.appendChild(host);
        }
        return el;
      },
      paint(entry, el, ctx) {
        if (entry.kind === "text") el.setAttribute("data-md", TD.textOf(ctx.doc.parts));
      },
      discard: () => false,
    };

    view.render(bubble, doc, renderers);
    const builtAfterFirst = built;
    const second = view.render(bubble, doc, renderers);
    const third = view.render(bubble, doc, renderers);

    ok("render is idempotent at " + n + " entries",
      second.moves === 0 && third.moves === 0,
      "moves=" + second.moves + "/" + third.moves);
    ok("...and builds nothing on a repeat at " + n,
      built === builtAfterFirst, built + " vs " + builtAfterFirst);
  }
}

// ── A token does not repaint history ──────────────────────────────────
//
// §11: "Update affected keyed rows; avoid rebuilding all activity HTML
// for every token." The plan is what decides how much gets touched, so
// the check is that appending a token does not change the slot list.
{
  const doc = bigDoc(1000);
  const before = TV.slotPlan(doc, { header: true, workbench: true, text: true }, TD)
    .map((e) => e.key).join(",");
  const after = TV.slotPlan(
    Object.assign({}, doc, {
      parts: doc.parts.slice(0, -1).concat([{ type: "text", text: LONG_ANSWER + "more" }]),
    }),
    { header: true, workbench: true, text: true }, TD,
  ).map((e) => e.key).join(",");
  ok("a new token changes no slot identity", before === after);
}

// ── Bounded stores ────────────────────────────────────────────────────
{
  const prefs = require(path.join(MODULES, "turn_preferences.js"));
  void prefs;
  const TPref = global.KazmaTurnPreferences;
  const store = TPref.create({ storage: null, sessionId: "perf" });
  for (let i = 0; i < TPref.MAX_TURNS * 3; i++) {
    store.setExpanded("t" + i, "activity", true);
  }
  ok("the preference store is bounded under churn",
    store.stats().entries <= TPref.MAX_TURNS, store.stats().entries);
}

console.log("\n" + pass + " performance properties held");
