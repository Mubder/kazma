/**
 * Shared unified-turn fixtures — JavaScript side.
 *
 * docs/plans/UNIFIED_TURN_BLOCK.md section 6:
 *
 *   "Use shared JSON fixtures to prove Python normalization/serialization
 *    and JavaScript projection agree. Do not rely on two implementations
 *    with independently written examples."
 *
 * Two independently written example sets is exactly how turn_document.py
 * and turn_document.js drifted: both suites were green while
 * `legacy_turn_id` produced a different id in each language. This driver
 * and tests/test_unified_turn_fixtures.py read the SAME files under
 * tests/fixtures/unified_turn/messages/, so a drift fails once with a diff
 * instead of passing twice.
 *
 * Run:  node tests/js/test_unified_turn_fixtures.js          (self-check)
 *       node tests/js/test_unified_turn_fixtures.js --emit   (print JSON)
 *
 * --emit is what the Python comparator consumes. Keep its shape in step
 * with `_project` in that file; the shape itself is asserted below.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");
const MODULE = path.join(
  ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"
);
const FIXTURES = path.join(ROOT, "tests", "fixtures", "unified_turn", "messages");

global.window = global;
require(MODULE);
const TD = global.KazmaTurnDocument;

/** Drop keys whose value is null/undefined so the two languages compare on
 *  content rather than on how each spells "absent". The one place that
 *  difference is load-bearing (activity `ts`) is a recorded divergence in
 *  docs/plans/UNIFIED_TURN_BLOCK_PHASE0.md section 6 and is asserted
 *  separately by the Python driver. */
function dense(obj) {
  if (Array.isArray(obj)) return obj.map(dense);
  if (obj && typeof obj === "object") {
    const out = {};
    for (const k of Object.keys(obj)) {
      if (obj[k] === null || obj[k] === undefined) continue;
      out[k] = dense(obj[k]);
    }
    return out;
  }
  return obj;
}

function project(message) {
  const h = TD.hydrateMessage(message);
  const parts = h.parts || [];
  return {
    turn_id: String(h.turn_id || h.turnId || ""),
    text: TD.textOf(parts),
    part_keys: parts.map(TD.partKey),
    gates: parts
      .filter((p) => p && p.type === "hitl")
      .map((p) => ({
        interrupt_id: String(p.interrupt_id || ""),
        tool: String(p.tool || ""),
        state: String(p.state || ""),
      })),
    activity: TD.activityOf(parts),
  };
}

function fixtureFiles() {
  return fs
    .readdirSync(FIXTURES)
    .filter((f) => f.endsWith(".json"))
    .sort()
    .map((f) => path.join(FIXTURES, f));
}

function loadAll() {
  const out = {};
  for (const file of fixtureFiles()) {
    const fx = JSON.parse(fs.readFileSync(file, "utf8"));
    out[fx.name] = { fixture: fx, projection: project(fx.message) };
  }
  return out;
}

if (process.argv.includes("--emit")) {
  const emitted = {};
  for (const [name, entry] of Object.entries(loadAll())) {
    emitted[name] = entry.projection;
  }
  process.stdout.write(JSON.stringify(emitted, null, 2) + "\n");
  process.exit(0);
}

let checked = 0;
for (const [name, entry] of Object.entries(loadAll())) {
  const expect = entry.fixture.expect || {};
  const got = dense(entry.projection);
  const diverged = new Set(
    (entry.fixture.known_divergences || []).map((d) => d.field)
  );
  for (const field of Object.keys(expect)) {
    if (diverged.has(field)) continue;
    assert.deepStrictEqual(
      got[field],
      dense(expect[field]),
      `${name}.${field}: JavaScript projection does not match the shared fixture`
    );
    checked++;
  }
  // A fixture that lists a divergence must still HAVE that field, or the
  // record is stale and nobody would notice it had been fixed.
  for (const d of entry.fixture.known_divergences || []) {
    assert.ok(
      Object.prototype.hasOwnProperty.call(entry.projection, d.field),
      `${name}: known_divergences names '${d.field}', which the projection does not produce`
    );
  }
}

assert.ok(checked > 0, "no fixture fields were checked");
console.log(`ok — ${checked} fixture fields agree (JavaScript side)`);
