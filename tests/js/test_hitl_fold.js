/**
 * A decided approval card folds, and the READER owns the fold.
 *
 * Reported 2026-09-24: a permission card, once expanded, could not be
 * collapsed again. Measured in the real app: the painter calls
 * _collapseClaimedHitlCard on every state change and that function ADDED
 * hitl-collapsed each time, so a card opened while it read "Approved —
 * running…" snapped shut when the tools finished; an open card also had no
 * chevron or pointer to close it with. And a card that arrived already
 * collapsed returned before its click handler was wired.
 *
 * Drives the REAL functions from chat.js and the real turn_preferences
 * store; "the painter repaints" is exactly a second _collapseClaimedHitlCard
 * call, which is what _paintHitlSlotCard does on a state change.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const ROOT = path.join(__dirname, "..", "..");
const JS = path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js");

let failures = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  failures++;
  console.log("FAIL " + name + (detail ? "  " + detail : ""));
}

require(path.join(JS, "modules", "turn_preferences.js"));
const TP = global.KazmaTurnPreferences;
function MemStorage() { this.map = {}; }
MemStorage.prototype.getItem = function (k) {
  return Object.prototype.hasOwnProperty.call(this.map, k) ? this.map[k] : null;
};
MemStorage.prototype.setItem = function (k, v) { this.map[k] = String(v); };

// The fold functions, exactly as shipped.
const src = fs.readFileSync(path.join(JS, "chat.js"), "utf8");
const start = src.indexOf("  function _hitlFoldKey(");
const end = src.indexOf("  function _revealHitlCard(");
ok("fold functions are extractable", start > 0 && end > start);

function load(prefs, doc) {
  // eslint-disable-next-line no-new-func
  return new Function("_turnPrefs", "document",
    src.slice(start, end) +
    "\nreturn { collapse: _collapseClaimedHitlCard, open: _hitlCardOpen };"
  )(() => prefs, doc);
}

function card(env, gate, opts) {
  const c = env.document.createElement("div");
  c.className = "hitl-approval-card hitl-approved" + (opts && opts.collapsed ? " hitl-collapsed" : "");
  c.setAttribute("data-interrupt-id", gate);
  const h = env.document.createElement("div");
  h.className = "hitl-approval-header";
  const title = env.document.createElement("span");
  title.className = "hitl-header-title";
  title.textContent = "python_exec";
  h.appendChild(title);
  const actions = env.document.createElement("div");
  actions.className = "hitl-approval-actions";
  const status = env.document.createElement("span");
  status.className = "hitl-status hitl-approved";
  status.textContent = "Approved";
  actions.appendChild(status);
  c.appendChild(h);
  c.appendChild(actions);
  env.ROOT.appendChild(c);
  return c;
}

const collapsed = (c) => c.classList.contains("hitl-collapsed");
const header = (c) => c.querySelector(".hitl-approval-header");

{
  const env = makeDocument();
  const store = new MemStorage();
  const prefs = TP.create({ storage: store, sessionId: "s1" });
  const f = load(prefs, env.document);
  const c = card(env, "gate-1");

  f.collapse(c);
  ok("a decided card starts collapsed", collapsed(c));

  header(c).click();
  ok("the header opens it", !collapsed(c));

  f.collapse(c);   // the painter, on "Approved — running…" -> "Approved"
  ok("a state change does not snap an opened card shut", !collapsed(c));

  header(c).click();
  ok("the header closes it again", collapsed(c));
  f.collapse(c);
  ok("...and a repaint keeps it closed", collapsed(c));

  header(c).click();
  const chev = header(c).querySelector(".hitl-collapse-chevron");
  ok("an open card still shows its chevron as open", chev && chev.textContent === "▾",
    chev && chev.textContent);
  ok("the header says whether it is open", header(c).getAttribute("aria-expanded") === "true");
  ok("an open card is marked as a toggle", c.classList.contains("hitl-collapsible"));

  // A reload builds a new node for the same gate: the reader's choice holds.
  const env2 = makeDocument();
  const again = load(TP.create({ storage: store, sessionId: "s1" }), env2.document);
  const rebuilt = card(env2, "gate-1");
  again.collapse(rebuilt);
  ok("the reader's choice survives a rebuild", !collapsed(rebuilt));
}

{
  // Arrived already collapsed (hydrated markup): used to return before the
  // click handler was wired, so it could never be opened.
  const env = makeDocument();
  const f = load(TP.create({ storage: new MemStorage(), sessionId: "s2" }), env.document);
  const c = card(env, "gate-2", { collapsed: true });
  f.collapse(c);
  header(c).click();
  ok("a card that arrived collapsed still opens", !collapsed(c));
}

{
  // A click on a button inside the header is that button's, not the fold's.
  const env = makeDocument();
  const f = load(TP.create({ storage: new MemStorage(), sessionId: "s3" }), env.document);
  const c = card(env, "gate-3");
  const btn = env.document.createElement("button");
  header(c).appendChild(btn);
  f.collapse(c);
  btn.click();
  ok("a button in the header does not toggle the fold", collapsed(c));
}

if (failures) {
  console.log("\n" + failures + " failure(s)");
  process.exitCode = 1;
} else {
  console.log("\nall ok");
}
