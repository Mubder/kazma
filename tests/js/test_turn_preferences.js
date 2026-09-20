/**
 * Turn disclosure preferences — invariant U08.
 *
 *     "Event processing never changes disclosure preferences."
 *
 * The store itself, plus the two chat.js helpers that read and write it,
 * extracted and driven on a fake DOM. Substring assertions would pass
 * happily while the fold still snapped back on the next token — which is
 * the bug this whole module exists to end, and it has been fixed in both
 * directions twice already (afbd22dd opened it live, and the commit before
 * that collapsed it at the terminal frame and yanked the answer).
 *
 * Run: node tests/js/test_turn_preferences.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

const ROOT = path.resolve(__dirname, "..", "..");
global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_preferences.js"));
const TP = global.KazmaTurnPreferences;

let pass = 0;
function ok(name, cond) {
  if (cond) { pass++; console.log("OK   " + name); return; }
  console.log("FAIL " + name);
  process.exitCode = 1;
}

// ── A storage shim that behaves like the real one, including badly ──────
function MemStorage() { this.map = {}; }
MemStorage.prototype.getItem = function (k) {
  return Object.prototype.hasOwnProperty.call(this.map, k) ? this.map[k] : null;
};
MemStorage.prototype.setItem = function (k, v) { this.map[k] = String(v); };

function ThrowingStorage() {}
ThrowingStorage.prototype.getItem = function () { throw new Error("blocked"); };
ThrowingStorage.prototype.setItem = function () { throw new Error("quota"); };

// ── Defaults ────────────────────────────────────────────────────────────
const store = new MemStorage();
let prefs = TP.create({ storage: store, sessionId: "s1" });

ok("no preference means collapsed", prefs.isExpanded("t1", "activity", false) === false);
ok("no preference is reported as absent", prefs.has("t1", "activity") === false);

prefs.setExpanded("t1", "activity", true);
ok("a recorded preference is returned", prefs.isExpanded("t1", "activity", false) === true);
ok("...and reported as present", prefs.has("t1", "activity") === true);
ok("a sibling turn is unaffected", prefs.isExpanded("t2", "activity", false) === false);

// ── It survives a reload of the same session ────────────────────────────
const reloaded = TP.create({ storage: store, sessionId: "s1" });
ok("a preference survives a page reload",
  reloaded.isExpanded("t1", "activity", false) === true);

// ── ...and does NOT leak across sessions ────────────────────────────────
const other = TP.create({ storage: store, sessionId: "s2" });
ok("another session starts with no preference",
  other.isExpanded("t1", "activity", false) === false);
other.setExpanded("t1", "activity", true);
ok("...and writing there does not touch the first",
  TP.create({ storage: store, sessionId: "s1" }).isExpanded("t1", "activity", false) === true);

prefs.setSession("s2");
ok("setSession switches which preferences are visible",
  prefs.isExpanded("t1", "activity", false) === true);
prefs.setSession("s3");
ok("...and an unseen session is empty again",
  prefs.isExpanded("t1", "activity", false) === false);

// ── promote ─────────────────────────────────────────────────────────────
// THE bug these tests missed, found by opening the real page: a turn opens
// under the 'live' placeholder and is renamed on the first stamped frame.
// The fold was written under 'live' and read back under the real id, so it
// shut again on the next token. Everything above drove one constant id and
// never promoted — a fixture that cannot express the defect.
{
  const promo = TP.create({ storage: new MemStorage(), sessionId: "promo" });
  promo.setExpanded("live", "activity", true);
  ok("a choice made under the placeholder is recorded",
    promo.isExpanded("live", "activity", false) === true);
  ok("promote moves it", promo.promote("live", "turn-7") === true);
  ok("...to the real id", promo.isExpanded("turn-7", "activity", false) === true);
  ok("...and leaves nothing behind",
    promo.has("live", "activity") === false);
  ok("promoting again is a no-op", promo.promote("live", "turn-7") === false);
  ok("promote to itself is a no-op", promo.promote("turn-7", "turn-7") === false);

  // A choice already recorded under the real id is newer and must win.
  const clash = TP.create({ storage: new MemStorage(), sessionId: "clash" });
  clash.setExpanded("live", "activity", true);
  clash.setExpanded("turn-9", "activity", false);
  clash.promote("live", "turn-9");
  ok("promotion never clobbers a newer choice",
    clash.isExpanded("turn-9", "activity", true) === false);
  ok("...and still clears the placeholder",
    clash.has("live", "activity") === false);

  // It survives a reload, i.e. it was actually persisted under the new key.
  const store2 = new MemStorage();
  const a = TP.create({ storage: store2, sessionId: "persist" });
  a.setExpanded("live", "activity", true);
  a.promote("live", "turn-3");
  ok("a promoted choice is persisted, not just in memory",
    TP.create({ storage: store2, sessionId: "persist" })
      .isExpanded("turn-3", "activity", false) === true);
}

// ── forget ──────────────────────────────────────────────────────────────
prefs.setSession("s1");
prefs.setExpanded("t9", "activity", true);
ok("forget clears a turn", prefs.forget("t9") === true
  && prefs.isExpanded("t9", "activity", false) === false);
ok("forgetting an unknown turn changes nothing", prefs.forget("nope") === false);

// ── Hostile storage must not break the page ─────────────────────────────
const blocked = TP.create({ storage: new ThrowingStorage(), sessionId: "s1" });
ok("a throwing store still constructs", !!blocked);
ok("...still answers the default", blocked.isExpanded("t1", "activity", false) === false);
blocked.setExpanded("t1", "activity", true);
ok("...and honours the choice in memory",
  blocked.isExpanded("t1", "activity", false) === true);

const none = TP.create({ storage: null, sessionId: "s1" });
none.setExpanded("t1", "activity", true);
ok("no storage at all still works in memory",
  none.isExpanded("t1", "activity", false) === true);

// ── Bounded ─────────────────────────────────────────────────────────────
const bounded = TP.create({ storage: new MemStorage(), sessionId: "big" });
for (let i = 0; i < TP.MAX_TURNS + 50; i++) {
  bounded.setExpanded("turn-" + i, "activity", true);
}
ok("the store is bounded", bounded.stats().entries <= TP.MAX_TURNS);
ok("...keeping the most recent", bounded.isExpanded("turn-" + (TP.MAX_TURNS + 49), "activity", false) === true);
ok("...and dropping the oldest as 'no preference'",
  bounded.isExpanded("turn-0", "activity", false) === false);

// ══════════════════════════════════════════════════════════════════════
// The chat.js half: the fold follows the reader, not the turn
// ══════════════════════════════════════════════════════════════════════
//
// _applyActivityFold and _toggleActivityFold are extracted from chat.js and
// driven against a minimal panel. The point is the INVARIANT: applying the
// fold repeatedly, at any execution state, never changes what the reader
// chose.

const src = fs.readFileSync(
  path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8"
);
const foldBlock = src.slice(
  src.indexOf("  function _applyActivityFold("),
  src.indexOf("  function _buildRestoredWorkbench(")
);
assert.ok(foldBlock.indexOf("_toggleActivityFold") > 0, "fold helpers moved");

function Panel() {
  this.classes = {};
  this.attrs = {};
  this.chev = { textContent: "" };
  this.header = {
    attrs: {},
    getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
  };
  const self = this;
  this.classList = {
    contains(c) { return !!self.classes[c]; },
    add(c) { self.classes[c] = true; },
    remove(c) { delete self.classes[c]; },
    toggle(c, on) {
      if (on === undefined) { if (self.classes[c]) delete self.classes[c]; else self.classes[c] = true; }
      else if (on) self.classes[c] = true; else delete self.classes[c];
    },
  };
}
Panel.prototype.querySelector = function (sel) {
  if (sel === ".agent-progress-chevron") return this.chev;
  if (sel === ".agent-progress-header") return this.header;
  return null;
};

const foldPrefs = TP.create({ storage: new MemStorage(), sessionId: "fold" });
const sandbox = {
  _turnPrefs: () => foldPrefs,
  _activityExpanded: (turnId) => foldPrefs.isExpanded(String(turnId || ""), "activity", false),
};
// eslint-disable-next-line no-new-func
new Function("_turnPrefs", "_activityExpanded", "exports",
  foldBlock + "\nexports._applyActivityFold = _applyActivityFold;" +
  "\nexports._toggleActivityFold = _toggleActivityFold;"
)(sandbox._turnPrefs, sandbox._activityExpanded, sandbox);

const panel = new Panel();
sandbox._applyActivityFold(panel, "t1");
ok("a fresh turn renders collapsed", panel.classList.contains("is-collapsed") === true);
ok("...and says so to a screen reader", panel.header.getAttribute("aria-expanded") === "false");

sandbox._toggleActivityFold(panel, "t1");
ok("the reader can open it", panel.classList.contains("is-collapsed") === false);
ok("...announced", panel.header.getAttribute("aria-expanded") === "true");
ok("...and recorded", foldPrefs.isExpanded("t1", "activity", false) === true);

// THE invariant: re-render 50 times, as tokens would, in every execution
// state. The fold must not move.
for (let i = 0; i < 50; i++) sandbox._applyActivityFold(panel, "t1");
ok("50 repaints do not close what the reader opened",
  panel.classList.contains("is-collapsed") === false);

sandbox._toggleActivityFold(panel, "t1");
ok("the reader can close it again", panel.classList.contains("is-collapsed") === true);
for (let i = 0; i < 50; i++) sandbox._applyActivityFold(panel, "t1");
ok("50 repaints do not reopen what the reader closed",
  panel.classList.contains("is-collapsed") === true);
ok("...and the chevron agrees", panel.chev.textContent === "▸");

// A new turn is collapsed because it has no preference, not because it is
// new — the same panel code, a different id.
const panel2 = new Panel();
sandbox._applyActivityFold(panel2, "t2");
ok("a new turn starts collapsed", panel2.classList.contains("is-collapsed") === true);

console.log(pass + " checks passed");
