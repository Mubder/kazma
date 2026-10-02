/**
 * The turn's memory row: what memory the model was shown, drawn inside the
 * turn block (restored 2026-09-27 -- the "Memory context" panel drew nothing
 * from Phase 5's removal of its markup on 2026-09-20).
 *
 * Runs the REAL row renderer and activity writer extracted from chat.js on
 * rows the REAL projector (modules/turn_document.js) builds from the shared
 * fixture's memory_explain payload (tests/fixtures/unified_turn/messages/
 * memory_used.json), with ti/tiCount/_stepRowHtml stubbed to record what they
 * are asked. Held: the row never spins (state info), the title counts only
 * the kinds that were shown, one line per hit in the order the model saw
 * them, and an empty recall says so. Negative control: without its branch
 * the activity writer draws the memory row as a bare status row.
 *
 * Run: node tests/js/test_memory_row.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..", "..");
// LF-normalised: a Windows checkout has CRLF.
const src = fs.readFileSync(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "chat.js"), "utf8")
  .replace(/\r\n/g, "\n");
const fixture = JSON.parse(fs.readFileSync(path.join(
  ROOT, "tests", "fixtures", "unified_turn", "messages", "memory_used.json"), "utf8"));

global.window = global;
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "modules", "turn_document.js"));
const TD = global.KazmaTurnDocument;

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("OK " + name); return; }
  fail += 1;
  console.log("FAIL " + name + "  " + String(detail === undefined ? "" : detail).slice(0, 400));
}

function slice(startMarker, endMarker) {
  const a = src.indexOf(startMarker);
  const b = src.indexOf(endMarker, a);
  return a >= 0 && b > a ? src.slice(a, b) : "";
}

const memoryFns = slice("  function _memoryCountLabel(kind, n) {", "  function _formatElapsed(ms) {");
const rowsStart = src.indexOf("  function _activityRowsHtml(activity) {");
const rowsFn = rowsStart >= 0 ? src.slice(rowsStart, src.indexOf("\n  }\n", rowsStart) + 4) : "";
ok("the renderer is extractable", memoryFns.includes("function _memoryRowHtml(row)") && rowsFn.length > 0);

const steps = [];
const stubs = {
  ti: (key, fallback) => fallback,
  tiCount: (base, n, one, other) => (n === 1 ? one : other).replace("{n}", String(n)),
  _stepRowHtml: (o) => { steps.push(o); return "<step>" + o.title + "</step>"; },
  escapeHtml: (s) => String(s),
  _friendlyToolName: (s) => s,
  _isThinkingStatus: () => false,
  _localizeCotTitle: (s) => s,
  _normalizeStatusTitle: (s) => s,
};

function build(body, exportName) {
  // eslint-disable-next-line no-new-func
  return new Function(...Object.keys(stubs), body + "\nreturn " + exportName + ";")(...Object.values(stubs));
}

const memoryRowHtml = build(memoryFns, "_memoryRowHtml");
const activityRowsHtml = build(memoryFns + "\n" + rowsFn, "_activityRowsHtml");

const row = TD.activityOf([TD.memoryPartOf(fixture.memory_payload)])[0];
ok("the projector makes a memory row", row && row.kind === "memory" && row.state === "info", JSON.stringify(row));

steps.length = 0;
const html = memoryRowHtml(row);
ok("the row never spins", /state-info/.test(html) && !/state-running/.test(html), html);
ok("the row is keyed as memory", /data-kind="memory"/.test(html) && /step-memory/.test(html), html);
const step = steps[0] || {};
ok("the title counts the kinds shown, and only those",
  step.title === "Memory used: 2 facts · 1 memory · 1 weekly summary", step.title);
const lines = String(step.detail || "").split("\n");
ok("one line per hit, as the model saw them", lines.length === 4 &&
  lines[0].startsWith("Fact · user lives_in kuwait [fts5, dense, ppr]") &&
  lines[2].startsWith("Memory · User: what phase is ShipX on?") &&
  lines[3].startsWith("Weekly summary · Week of 2026-09-14"), JSON.stringify(lines));
ok("a hit with no sources has no brackets", !/\[/.test(lines[1]), lines[1]);
ok("no timestamp: a reload must not show the time of the reload", step.tsIso === null, step.tsIso);

steps.length = 0;
memoryRowHtml(TD.activityOf([TD.memoryPartOf({ beliefs: [], episodes: [], weekly_summaries: [], knowledge: [] })])[0]);
ok("an empty recall says so", steps[0] && steps[0].title === "Memory used: nothing matched", steps[0] && steps[0].title);

steps.length = 0;
const through = activityRowsHtml([row]);
ok("the activity writer draws it through the memory renderer",
  /step-memory/.test(through) && steps[0] && /2 facts/.test(steps[0].title), through);

// Each memory on its own line, in its own language (2026-10-02). The kind
// label is the page's, and it led each line of one text block, so in the
// Arabic UI the label's letter decided the line: an English memory ran
// right-to-left behind it.
global.document = {
  readyState: "loading",
  addEventListener() {},
  documentElement: { getAttribute: () => "rtl" },
};
require(path.join(ROOT, "kazma-ui", "kazma_ui", "static", "js", "bidi.js"));
const AR_LABELS = {
  memory_kind_fact: "حقيقة", memory_kind_turn: "ذكرى",
  memory_kind_weekly: "ملخص أسبوعي", memory_kind_knowledge: "مكتبة",
};
const arStubs = Object.assign({}, stubs, { ti: (key, fallback) => AR_LABELS[key] || fallback });
// eslint-disable-next-line no-new-func
const arMemoryRowHtml = new Function(...Object.keys(arStubs), memoryFns + "\nreturn _memoryRowHtml;")(
  ...Object.values(arStubs));
steps.length = 0;
arMemoryRowHtml(row);
const lineHtml = String((steps[0] || {}).detailHtml || "");
const lineDirs = [...lineHtml.matchAll(/<div class="step-detail-line" dir="(\w+)">/g)].map((m) => m[1]);
ok("each memory is its own line, in its own language",
  JSON.stringify(lineDirs) === JSON.stringify(["ltr", "rtl", "ltr", "ltr"]), lineHtml);
ok("the label and the memory are isolated from each other",
  lineHtml.includes('<bdi>ذكرى</bdi> · <bdi translate="no">User: what phase is ShipX on?'), lineHtml);
// Negative control: the plain line drawn before -- its first letter, which
// decides dir="auto", is the label's.
const oldLine = String((steps[0] || {}).detail || "").split("\n")[2] || "";
const firstLetter = [...oldLine].find((ch) => /\p{L}/u.test(ch)) || "";
ok("the old line's first letter was the Arabic label's",
  /^ذكرى · User:/.test(oldLine) && /[؀-ۿ]/.test(firstLetter), oldLine);

// Negative control: the same writer with its memory branch taken out.
const withoutBranch = rowsFn.replace(/\n\s*if \(row\.kind === 'memory'\)[^\n]*\n/, "\n");
ok("the negative control removed the branch", withoutBranch !== rowsFn);
steps.length = 0;
const bare = build(memoryFns + "\n" + withoutBranch, "_activityRowsHtml")([row]);
ok("without the branch it is a bare status row", !/step-memory/.test(bare) &&
  steps[0] && steps[0].title === "Memory used", bare);

process.exit(fail ? 1 : 0);
