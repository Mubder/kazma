/**
 * Behavioral tests for the Live Task Card state machine in chat.js.
 *
 * The card's Python tests assert that source substrings EXIST — they pass
 * happily while the branch they name leaks a hide timer. This drives the
 * real state machine on a fake clock and a fake DOM and asserts what the
 * user actually sees, which is the only thing that caught the 2026-09-03
 * "card vanished after approve, no response" class of bug.
 *
 * Run: node tests/js/test_live_task_card.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "chat.js",
);
const src = fs.readFileSync(srcPath, "utf8");

const BEGIN = ">>> LIVE_TASK_CARD_BEGIN";
const END = "<<< LIVE_TASK_CARD_END";
const b = src.indexOf(BEGIN);
const e = src.indexOf(END);
if (b < 0 || e < 0) {
  throw new Error("Live Task Card markers missing from chat.js — the block " +
    "moved or was renamed; this harness extracts it verbatim.");
}
const cardBlock = src.slice(src.indexOf("\n", b) + 1, src.lastIndexOf("\n", e));

/** _tcArgSummary lives outside the block (call sites use it directly). */
function sliceFn(name, next) {
  const start = src.indexOf("function " + name + "(");
  const end = src.indexOf("function " + next + "(", start + 1);
  if (start < 0 || end < 0) throw new Error("cannot slice " + name);
  return src.slice(start, end);
}
// Start at the lookup tables above it, not at the function itself.
const argBlock = src.slice(
  src.indexOf("var _TC_ARG_SKIP ="),
  src.indexOf("function _setStoreThinking("),
);

// ── Fake DOM ────────────────────────────────────────────────────────────
function El(cls) {
  this.className = cls || "";
  this.textContent = "";
  this._html = "";
  this.hidden = false;
  this.attrs = {};
  this.children = [];
  this.scrollTop = 0;
  this.scrollHeight = 0;
  this.clientHeight = 0;
  this.handlers = {};
}
El.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
El.prototype.getAttribute = function (k) {
  return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null;
};
El.prototype.addEventListener = function (t, fn) {
  (this.handlers[t] = this.handlers[t] || []).push(fn);
};
El.prototype.click = function () {
  (this.handlers.click || []).forEach(function (fn) { fn.call(this); }, this);
};
/** Writing rows grows the box — the tail-pin check reads scrollHeight
 *  BEFORE the write and scrollTop after it, so a static fake proves nothing. */
Object.defineProperty(El.prototype, "innerHTML", {
  get() { return this._html || ""; },
  set(v) {
    this._html = String(v);
    const rows = (this._html.match(/<li /g) || []).length;
    this.scrollHeight = rows * 30;
  },
  configurable: true,
});
El.prototype.querySelector = function (sel) {
  const want = sel.replace(/^\./, "");
  for (const c of this.children) if (c.className.split(/\s+/).includes(want)) return c;
  return null;
};

function buildCard() {
  const card = new El("live-task-card");
  [
    "live-task-header", "live-task-toggle", "live-task-phase", "live-task-label",
    "live-task-meta", "live-task-stall", "live-task-chevron", "live-task-body",
    "live-task-steps", "live-task-live", "live-task-stop", "live-task-jump",
    "live-task-retry",
  ].forEach(function (c) { card.children.push(new El(c)); });
  return card;
}

// ── Fake clock + timers ─────────────────────────────────────────────────
function Clock() {
  this.now = 1000000;
  this.seq = 1;
  this.intervals = new Map();
  this.timeouts = new Map();
}
Clock.prototype.setInterval = function (fn, ms) {
  const id = this.seq++;
  this.intervals.set(id, { fn: fn, ms: ms, next: this.now + ms });
  return id;
};
Clock.prototype.clearInterval = function (id) { this.intervals.delete(id); };
Clock.prototype.setTimeout = function (fn, ms) {
  const id = this.seq++;
  this.timeouts.set(id, { fn: fn, at: this.now + ms });
  return id;
};
Clock.prototype.clearTimeout = function (id) { this.timeouts.delete(id); };
/** Advance in 250ms slices so intervals and timeouts fire in real order. */
Clock.prototype.advance = function (ms) {
  const target = this.now + ms;
  while (this.now < target) {
    this.now = Math.min(target, this.now + 250);
    for (const [id, t] of Array.from(this.timeouts)) {
      if (t.at <= this.now) { this.timeouts.delete(id); t.fn(); }
    }
    for (const [, iv] of Array.from(this.intervals)) {
      while (iv.next <= this.now) { iv.next += iv.ms; iv.fn(); }
    }
  }
};

// ── Build one card instance ─────────────────────────────────────────────
/** A fresh card wired to a fake clock, fake DOM and a mutable activity list
 *  (`api.env.activity` — mutate it, the card reads it through the closure). */
function makeCardWithActivity(rows, seed) {
  const clock = new Clock();
  const card = buildCard();
  const calls = { resync: [], abort: 0 };
  const store = {
    data: Object.assign({}, seed || {}),
    getItem(k) { return k in this.data ? this.data[k] : null; },
    setItem(k, v) { this.data[k] = String(v); },
  };
  const env = {
    card: card, clock: clock, calls: calls, docs: { live: { parts: [] } },
    liveTurnId: "live", activity: rows || [], store: store,
  };
  const preamble = `
    var _clock = env.clock, _calls = env.calls;
    var Date = { now: function () { return _clock.now; } };
    function setInterval(fn, ms) { return _clock.setInterval(fn, ms); }
    function clearInterval(id) { return _clock.clearInterval(id); }
    function setTimeout(fn, ms) { return _clock.setTimeout(fn, ms); }
    function clearTimeout(id) { return _clock.clearTimeout(id); }
    var document = { getElementById: function (id) {
      return id === 'live-task-card' ? env.card : null;
    } };
    var KazmaTurnDocument = {
      activityOf: function () { return env.activity || []; },
    };
    var window = { localStorage: env.store, KazmaTurnDocument: KazmaTurnDocument };
    var messagesEl = null;
    function ti(key, fb) { return fb || key; }
    function tiFmt(key, fb, vars) {
      var s = fb || key;
      if (vars) Object.keys(vars).forEach(function (k) {
        s = s.replace(new RegExp('\\\\{' + k + '\\\\}', 'g'), String(vars[k]));
      });
      return s;
    }
    function escapeHtml(s) { return String(s).replace(/[&<>"]/g, '_'); }
    function truncateStr(s, n) { return String(s).slice(0, n); }
    var _docs = env.docs;
    var _liveTurnId = env.liveTurnId;
    function _resyncDelivery(why) { _calls.resync.push(why); }
    function abortGeneration() { _calls.abort += 1; }
  `;
  const body = preamble + "\n" + cardBlock + "\n" + argBlock + "\n" + `
    return {
      tc: _tc, ev: _taskCardEvent, tick: _tcTick, label: _tcPhaseLabel,
      argSummary: _tcArgSummary, steps: _tcStepsFromDoc,
    };
  `;
  const api = new Function("env", body)(env);
  api.card = card;
  api.clock = clock;
  api.calls = calls;
  api.env = env;
  api.q = function (c) { return card.querySelector("." + c); };
  return api;
}

const OPEN = { "kazma.taskcard.open": "1" };

// ── Assertions ──────────────────────────────────────────────────────────
let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed += 1; }
  catch (err) { failures.push(name + ": " + (err && err.message || err)); }
}
function assert(cond, msg) { if (!cond) throw new Error(msg || "assertion failed"); }
function eq(a, b, msg) {
  if (a !== b) throw new Error((msg || "not equal") + " — got " + JSON.stringify(a) +
    ", want " + JSON.stringify(b));
}

// ── The disappearing card (bug: approve → blank, no response) ───────────
test("approval cancels a pending hide from a previous terminal frame", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: true });
  c.clock.advance(400);                 // inside the 1.6s hide window
  c.ev({ t: "approval", deadline: c.clock.now / 1000 + 180 });
  c.clock.advance(5000);                // the old hide timer would have fired
  eq(c.card.hidden, false, "card hidden while awaiting approval");
  eq(c.tc.visible, true, "card marked invisible while awaiting approval");
  eq(c.tc.phase, "awaiting", "phase");
});

test("resuming cancels a pending hide and restarts the clock", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: true });
  c.clock.advance(400);
  c.ev({ t: "resuming" });
  c.clock.advance(5000);
  eq(c.card.hidden, false, "card vanished mid-resume");
  eq(c.tc.phase, "resuming", "phase");
  assert(c.tc.tickTimer !== null, "tick timer not restarted on resume");
  assert(c.tc.elapsedS >= 5, "elapsed frozen after resume: " + c.tc.elapsedS);
});

test("a resume keeps the resume label instead of the thinking override", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "approval", deadline: 0 });
  c.ev({ t: "resuming" });
  c.ev({ t: "text", msg: "Kazma is thinking…" });  // what beginTurn used to do
  eq(c.label(), "Resuming after approval", "override beat the phase");
});

// ── Liveness / elapsed ──────────────────────────────────────────────────
test("elapsed keeps advancing through a total signal blackout", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.clock.advance(60000);               // no events at all
  assert(c.tc.elapsedS >= 59, "clock froze during the gap: " + c.tc.elapsedS);
});

test("server elapsed wins, and never runs the turn clock backwards", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.clock.advance(30000);
  c.ev({ t: "hb", phase: "llm", elapsed_s: 120 });   // server is ahead
  assert(c.tc.elapsedS >= 119, "server clock ignored: " + c.tc.elapsedS);
  const before = c.tc.elapsedS;
  c.ev({ t: "hb", phase: "resuming", elapsed_s: 1 }); // resumed run restarts
  c.tick();
  assert(c.tc.elapsedS >= before - 1,
    "turn clock went backwards on resume: " + before + " -> " + c.tc.elapsedS);
});

test("stall recovery retries with backoff, then says it gave up", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.clock.advance(25000);                       // first gap
  eq(c.tc.stalled, true, "stall not detected");
  eq(c.calls.resync.length, 1, "no first resync");
  c.clock.advance(31000);
  eq(c.calls.resync.length, 2, "no backoff retry");
  c.clock.advance(31000);
  eq(c.calls.resync.length, 3, "backoff stopped early");
  c.clock.advance(31000);
  eq(c.calls.resync.length, 3, "retried past the cap");
  eq(c.tc.dead, true, "never escalated to 'not responding'");
  assert(c.card.className.includes("is-dead"), "no is-dead class: " + c.card.className);
  eq(c.q("live-task-retry").hidden, false, "Retry button not offered");
});

test("one live frame clears the stall", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.clock.advance(25000);
  eq(c.tc.stalled, true, "stall not detected");
  c.ev({ t: "hb", phase: "tool", current: "shell_exec" });
  c.tick();
  eq(c.tc.stalled, false, "stall stuck after a live heartbeat");
  eq(c.calls.resync.length, 1, "extra resync after recovery");
});

test("a live frame clears the warning in the SAME render", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.clock.advance(25000);
  eq(c.tc.stalled, true, "stall not detected");
  c.ev({ t: "approval", deadline: c.clock.now / 1000 + 60 });
  // Not "a tick later": the render that shows the new phase must not also
  // show "no signal 0s" beside it.
  eq(c.tc.stalled, false, "stale warning survived a live frame");
  eq(c.q("live-task-stall").hidden, true, "warning still on screen");
  assert(!c.card.className.includes("is-stalled"), c.card.className);
});

test("a turn that delivered nothing does not wear a checkmark", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: false });
  eq(c.q("live-task-phase").textContent, "⚠", "empty turn shows a tick");
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: true });
  eq(c.q("live-task-phase").textContent, "✓", "good turn lost its tick");
});

test("an approval pause is not a stall", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "approval", deadline: c.clock.now / 1000 + 600 });
  c.clock.advance(120000);
  eq(c.tc.stalled, false, "waiting on the user read as a hung turn");
  eq(c.calls.resync.length, 0, "resynced while parked on an approval");
});

// ── Informative header ──────────────────────────────────────────────────
test("the tool's target shows in the header, and its own clock", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "tool", name: "file_search", detail: "“auth middleware”" });
  eq(c.label(), "Running file_search “auth middleware”", "no target in the label");
  c.clock.advance(20000);
  assert(c.q("live-task-meta").textContent.includes("in this tool"),
    "no phase-scoped clock: " + c.q("live-task-meta").textContent);
});

test("a short tool call does not claim a phase clock", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "tool", name: "read_file" });
  c.clock.advance(4000);
  assert(!c.q("live-task-meta").textContent.includes("in this tool"),
    "phase clock shown for a 4s tool");
});

test("the terminal frame carries the shape of the turn", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: true, summary: "12 steps · 3 tools · 18.4s" });
  eq(c.q("live-task-meta").textContent, "12 steps · 3 tools · 18.4s", "summary lost");
  eq(c.q("live-task-label").textContent, "Done", "label");
});

test("a turn that delivered nothing does not claim success", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done", ok: false, summary: "2 steps" });
  eq(c.label(), "No reply received", "empty turn labelled Done");
  assert(c.card.className.includes("is-empty"), "no is-empty: " + c.card.className);
});

test("an aborted turn is not flagged as empty", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "done" });                  // forceEndTurn passes no `ok`
  eq(c.tc.emptyTurn, false, "abort mislabelled as an empty turn");
  eq(c.label(), "Done", "label");
});

test("awaiting shows the countdown and swaps Stop for Review", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  eq(c.q("live-task-stop").hidden, false, "Stop missing while running");
  c.ev({ t: "approval", deadline: c.clock.now / 1000 + 192 });
  const meta = c.q("live-task-meta").textContent;
  assert(meta.includes("3:12"), "countdown wrong: " + meta);
  eq(c.q("live-task-jump").hidden, false, "Review missing while awaiting");
  eq(c.q("live-task-stop").hidden, true, "Stop offered while parked on approval");
});

test("empty status text clears a stale override", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  c.ev({ t: "status", status: "synthesizing" });
  eq(c.label(), "Writing the reply…", "override not applied");
  c.ev({ t: "text", msg: "" });          // _clearStatusStrip
  eq(c.label(), "Thinking", "empty msg did not clear the override");
});

// ── Steps body ──────────────────────────────────────────────────────────
test("a new turn does not show the previous turn's steps", () => {
  const c = makeCardWithActivity([
    { kind: "tool", title: "old_tool", detail: "from last turn", state: "done" },
  ], OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  assert(c.q("live-task-steps").innerHTML.includes("old_tool"), "setup");
  c.env.activity = [];
  c.ev({ t: "begin" });
  eq(c.q("live-task-steps").innerHTML, "", "stale steps survived a new turn");
});

test("identical step markup is not re-assigned", () => {
  const c = makeCardWithActivity([
    { kind: "tool", title: "grep", detail: "x", state: "done" },
  ], OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  const el = c.q("live-task-steps");
  let writes = 0;
  let html = el.innerHTML;
  Object.defineProperty(el, "innerHTML", {
    get() { return html; },
    set(v) { writes += 1; html = v; },
  });
  c.ev({ t: "doc" });
  c.ev({ t: "doc" });
  eq(writes, 0, "rebuilt identical markup " + writes + " times");
});

test("the steps list stays pinned to the tail", () => {
  const rows = (n) => Array.from({ length: n }, (_, i) => (
    { kind: "status", title: "row" + i, state: "done" }));
  const c = makeCardWithActivity(rows(10), OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  const el = c.q("live-task-steps");
  eq(el.scrollHeight, 300, "fake list did not grow");
  el.clientHeight = 220;
  el.scrollTop = 80;                    // sitting at the bottom
  c.env.activity = rows(20);
  c.ev({ t: "doc" });
  eq(el.scrollTop, 600, "tail scroll not followed");
});

test("a reader who scrolled up keeps their place", () => {
  const rows = (n) => Array.from({ length: n }, (_, i) => (
    { kind: "status", title: "row" + i, state: "done" }));
  const c = makeCardWithActivity(rows(10), OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  const el = c.q("live-task-steps");
  el.clientHeight = 220;
  el.scrollTop = 0;                     // scrolled up on purpose
  c.env.activity = rows(20);
  c.ev({ t: "doc" });
  eq(el.scrollTop, 0, "yanked the reader to the bottom");
});

test("the open/closed choice survives the next mount", () => {
  const c = makeCardWithActivity();
  c.ev({ t: "begin" });
  eq(c.tc.open, false, "default is collapsed");
  c.q("live-task-toggle").click();
  eq(c.env.store.data["kazma.taskcard.open"], "1", "choice not persisted");
  const c2 = makeCardWithActivity(null, { "kazma.taskcard.open": "1" });
  c2.ev({ t: "begin" });
  eq(c2.tc.open, true, "choice not restored on the next load");
  eq(c2.q("live-task-body").hidden, false, "body still collapsed");
});

test("finishing a turn does not wipe the steps you are reading", () => {
  const rows = [{ kind: "tool", title: "web_search", detail: "notes", state: "done" }];
  const c = makeCardWithActivity(rows, OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  assert(c.q("live-task-steps").innerHTML.includes("web_search"), "setup");
  // The turn ends: _liveTurnId is retired and _docs is dropped, so the next
  // read comes back empty. That is an empty READ, not an empty turn.
  c.env.activity = [];
  c.ev({ t: "done", ok: true, summary: "1 step" });
  c.ev({ t: "text", msg: "" });          // endTurn's _clearStatusStrip
  assert(c.q("live-task-steps").innerHTML.includes("web_search"),
    "steps vanished the moment the turn finished");
});

test("a session change unmounts the card with no Done flash", () => {
  const c = makeCardWithActivity([{ kind: "status", title: "x", state: "done" }], OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "doc" });
  c.ev({ t: "reset" });
  eq(c.card.hidden, true, "card still on screen after a session change");
  eq(c.tc.visible, false, "still marked visible");
  eq(c.tc.tickTimer, null, "tick timer left running");
  eq(c.tc.doneTimer, null, "a retire animation was armed");
  eq(c.q("live-task-steps").innerHTML, "", "previous session's steps kept");
  // ...and it must STAY hidden — a leftover timer used to reveal it again.
  c.clock.advance(5000);
  eq(c.card.hidden, true, "card reappeared on a timer");
});

test("reset then a real turn still works", () => {
  const c = makeCardWithActivity(null, OPEN);
  c.ev({ t: "begin" });
  c.ev({ t: "reset" });
  c.ev({ t: "begin" });
  c.ev({ t: "tool", name: "grep" });
  eq(c.card.hidden, false, "card did not come back for a real turn");
  eq(c.label(), "Running grep", "label");
});

// ── a11y ────────────────────────────────────────────────────────────────
test("the live region announces phase, not every tick", () => {
  const c = makeCardWithActivity();
  const live = c.q("live-task-live");
  let writes = 0;
  let txt = "";
  Object.defineProperty(live, "textContent", {
    get() { return txt; },
    set(v) { writes += 1; txt = v; },
  });
  c.ev({ t: "begin" });
  const afterBegin = writes;
  c.clock.advance(9000);                // 9 ticks, same phase
  eq(writes, afterBegin, "announced on the tick: " + (writes - afterBegin) + " times");
  c.ev({ t: "tool", name: "shell_exec" });
  assert(writes > afterBegin, "phase change never announced");
  assert(txt.includes("shell_exec"), "announcement: " + txt);
});

test("the visible header is aria-hidden and the toggle name is static", () => {
  const html = fs.readFileSync(path.join(
    __dirname, "..", "..", "kazma-ui", "kazma_ui", "templates", "chat.html",
  ), "utf8");
  const card = html.slice(html.indexOf('id="live-task-card"'),
    html.indexOf("</div>", html.indexOf('class="live-task-body"')));
  for (const cls of ["live-task-label", "live-task-meta", "live-task-phase"]) {
    const at = card.indexOf(cls);
    assert(at > 0, "missing " + cls);
    const tag = card.slice(card.lastIndexOf("<", at), card.indexOf(">", at));
    assert(tag.includes('aria-hidden="true"'),
      cls + " is not aria-hidden — the header re-announces every tick");
  }
  assert(/class="sr-only"[^>]*>\{\{ t\('chat\.task_details'\)/.test(card) ||
         card.includes("chat.task_details"),
    "toggle has no static accessible name");
});

// ── Tool argument summary ───────────────────────────────────────────────
test("_tcArgSummary names what the tool is acting on", () => {
  const c = makeCardWithActivity();
  eq(c.argSummary('{"query":"auth middleware","session_id":"x"}'),
    "“auth middleware”", "preferred key");
  eq(c.argSummary({ session_id: "x", thread_id: "y", pattern: "TODO" }),
    "“TODO”", "skips identifiers");
  eq(c.argSummary("ls -la"), "“ls -la”", "plain string");
  eq(c.argSummary({}), "", "empty object");
  eq(c.argSummary(null), "", "null");
  eq(c.argSummary("   "), "", "blank string");
  assert(c.argSummary({ q: "x".repeat(200) }).length < 60, "not truncated");
});

// ── Report ──────────────────────────────────────────────────────────────
if (failures.length) {
  console.error("FAILED " + failures.length + " / " + (passed + failures.length));
  failures.forEach(function (f) { console.error("  ✗ " + f); });
  process.exit(1);
}
console.log("ok — " + passed + " Live Task Card behaviors");
