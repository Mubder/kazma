/**
 * A page left by soft navigation leaves nothing running (audit 2026-10-26
 * AUD-030).
 *
 * modules/nav.js runs a page's scripts again on every visit, each run in a
 * new closure, then calls the page's leave hooks on the way out and lets
 * Alpine destroy the page's components. Every document/window listener,
 * interval, socket, stream or worker a run started and did not end kept
 * running on every later page: chat.js's Escape could stop a turn from
 * another page, the Dashboard's socket prepended each trace row once per
 * visit, the HITL panel polled once more per visit, the Workspace ran
 * `git status` once more per visit every 10 s, the Memory page polled its
 * health from every page, Research's Archive POSTed twice, each Chat visit
 * left a Web Worker ticking, and a document job's poll outlived the page.
 *
 * Each case boots the page's script twice (two visits), leaves after each,
 * and counts what is still live. Each has a negative control built from the
 * shape that shipped.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { installLeaveHooks, leavePage } = require("./_leave_hooks.js");

const UI = path.join(__dirname, "../../kazma-ui/kazma_ui");
const read = (rel) => fs.readFileSync(path.join(UI, rel), "utf8").replace(/\r\n/g, "\n");

/** Replace `from` with `to` in `src`, failing when `from` is not there. */
function revert(src, from, to) {
  assert.ok(src.includes(from), "negative control: not found in the source: " + from.slice(0, 80));
  return src.split(from).join(to);
}

/**
 * A page element that answers anything: the page's own elements die with
 * the page, so their listeners are not counted; only what the scripts bind
 * to document and window (which outlive the page) is.
 */
function anything() {
  const fn = function () {};
  return new Proxy(fn, {
    get(_t, key) {
      if (key === Symbol.toPrimitive) return () => "";
      if (key === "then") return undefined;
      if (key === "length" || key === "scrollTop" || key === "scrollHeight" || key === "clientHeight") return 0;
      if (key === "value" || key === "textContent" || key === "innerHTML" || key === "className") return "";
      if (key === "children" || key === "childNodes") return [];
      if (key === "querySelectorAll") return () => [];
      if (key === "getAttribute") return () => null;
      if (key === "closest" || key === "querySelector") return () => null;
      return anything();
    },
    set() { return true; },
    apply() { return anything(); },
    construct() { return anything(); },
  });
}

/** Everything a page run can leave running, counted. */
function makeEnv(opts) {
  const pageElements = !!(opts && opts.pageElements);
  const live = { listeners: new Set(), intervals: new Set(), sockets: new Set(), workers: new Set() };
  let nextId = 1;
  const fetched = [];

  function eventTarget(name, extra) {
    const mine = [];
    return Object.assign({
      addEventListener(type, fn, opts) {
        const entry = { name, type, fn };
        mine.push(entry);
        const signal = opts && typeof opts === "object" ? opts.signal : null;
        if (signal && signal.aborted) return;
        live.listeners.add(entry);
        if (signal) signal.addEventListener("abort", () => live.listeners.delete(entry));
      },
      removeEventListener(type, fn) {
        for (const e of mine) if (e.type === type && e.fn === fn) live.listeners.delete(e);
      },
      dispatch(type, event) {
        for (const e of [...live.listeners]) if (e.name === name && e.type === type) e.fn(event || {});
      },
    }, extra || {});
  }

  const document = eventTarget("document", {
    readyState: "complete",
    hidden: false,
    cookie: "",
    getElementById: () => (pageElements ? anything() : null),
    querySelector: () => (pageElements ? anything() : null),
    querySelectorAll: () => [],
    createElement: () => ({ style: {}, classList: { add() {}, remove() {} }, setAttribute() {}, appendChild() {} }),
    documentElement: { lang: "en", dataset: {}, getAttribute: () => "ltr", classList: { add() {}, remove() {}, contains: () => false } },
    body: { classList: { add() {}, remove() {}, contains: () => false } },
  });

  function track(set, obj) { set.add(obj); return obj; }
  function FakeSocket() {
    const s = { readyState: 0, send() {}, close() { s.readyState = 3; live.sockets.delete(s); } };
    return track(live.sockets, s);
  }
  FakeSocket.OPEN = 1; FakeSocket.CONNECTING = 0; FakeSocket.CLOSED = 3;
  function FakeEventSource() {
    const s = { close() { live.sockets.delete(s); } };
    return track(live.sockets, s);
  }
  function FakeWorker() {
    const w = { terminate() { live.workers.delete(w); }, postMessage() {} };
    return track(live.workers, w);
  }

  const window = eventTarget("window", {
    location: { pathname: "/", search: "", hash: "", href: "http://localhost/", origin: "http://localhost", host: "localhost", protocol: "http:" },
    KAZMA_I18N: {}, KAZMA_LANG: "en", CHAT_I18N: {}, KAZMA_SLASH_COMMANDS: [],
    kazmaT: (k, fallback) => fallback || k,
    t: (k) => k,
    tOr: (k, fallback) => fallback,
    kazmaCount: (k, n) => String(n),
    kazmaGetJson: async () => null,
    kazmaConfirm: async () => false,
    kazmaSave: async () => ({}),
    showToast() {},
    KazmaFormat: { relative: () => "", date: () => "", pluralCategory: () => "other" },
    KazmaUtils: { escapeHtml: (x) => String(x) },
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
  });
  // The stream helpers a page calls; anything else answers harmlessly.
  const stream = {
    markdown: (s) => String(s), toast() {}, sse: () => ({}),
    ws: () => new FakeSocket(),
  };
  window.KazmaStream = new Proxy(stream, { get: (t, k) => (k in t ? t[k] : anything()) });
  installLeaveHooks(window);

  const ctx = {
    window, document, console: { log() {}, info() {}, warn() {}, error() {}, debug() {} },
    localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    sessionStorage: { getItem: () => null, setItem() {}, removeItem() {} },
    navigator: { userAgent: "node", language: "en" },
    location: window.location,
    fetch: (url) => {
      fetched.push(String(url));
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => "" }, json: async () => ({}), text: async () => "" });
    },
    setInterval: () => { const id = nextId++; live.intervals.add(id); return id; },
    clearInterval: (id) => { live.intervals.delete(id); },
    setTimeout: () => nextId++,
    clearTimeout() {},
    requestAnimationFrame: () => nextId++,
    cancelAnimationFrame() {},
    WebSocket: FakeSocket, EventSource: FakeEventSource, Worker: FakeWorker,
    Blob: function () {}, URL: Object.assign(function () {}, { createObjectURL: () => "blob:x", revokeObjectURL() {} }),
    URLSearchParams, AbortController, Promise, Map, Set, JSON, Math, Date, Object, Array, String, Number,
    Boolean, RegExp, Error, TypeError, Symbol, Proxy, Reflect, Intl, parseInt, parseFloat, isNaN,
    encodeURIComponent, decodeURIComponent, queueMicrotask,
    MutationObserver: function () { return { observe() {}, disconnect() {} }; },
    ResizeObserver: function () { return { observe() {}, disconnect() {} }; },
    CustomEvent: function (type, init) { return { type, detail: init && init.detail }; },
    getComputedStyle: () => ({ getPropertyValue: () => "" }),
  };
  // A page script reads its globals bare or through window: give both.
  for (const k of Object.keys(ctx)) if (!(k in window)) window[k] = ctx[k];
  vm.createContext(ctx);
  const run = (code) => vm.runInContext(code, ctx);
  const count = () => ({
    listeners: live.listeners.size, intervals: live.intervals.size,
    sockets: live.sockets.size, workers: live.workers.size,
  });
  return { window, document, ctx, run, count, fetched, live };
}

const NOTHING = { listeners: 0, intervals: 0, sockets: 0, workers: 0 };

/** Two visits of a classic page script, leaving after each. */
function twoVisits(code, opts) {
  const env = makeEnv(opts);
  env.run(code);
  leavePage(env.window);
  env.run(code);
  leavePage(env.window);
  return env;
}

// ── Dashboard: dashboard.js + hitl_approval.js on one page ──────────────

const DASHBOARD = read("static/js/dashboard.js");
const HITL = read("static/js/hitl_approval.js");

test("the Dashboard leaves no socket, stream, poller or listener behind", () => {
  const env = twoVisits(DASHBOARD + "\n;\n" + HITL);
  assert.deepEqual(env.count(), NOTHING);
});

test("negative control: the Dashboard as it shipped kept its socket and pollers", () => {
  let dash = revert(DASHBOARD, "    window.kazmaAddLeaveHook(teardown);\n", "");
  dash = revert(dash, "    _resourceStream = eventSource;",
    "    window.kazmaOnSoftNavLeave = function () { try { eventSource.close(); } catch (e) {} };");
  const env = twoVisits(dash);
  const left = env.count();
  assert.equal(left.sockets, 2, "one /ws/dashboard socket per visit");
  assert.equal(left.intervals, 4, "two pollers per visit");
});

test("negative control: dashboard.js overwrote hitl_approval.js's hook", () => {
  // hitl_approval.js runs after dashboard.js; dashboard.js assigning the hook
  // at boot (the shipped order: boot ran first) dropped nothing then, but a
  // later assignment drops every hook before it.
  const env = makeEnv();
  env.run(HITL);
  env.window.kazmaOnSoftNavLeave = function () {};  // what dashboard.js did
  leavePage(env.window);
  assert.equal(env.count().intervals, 1, "the approvals poller kept running");
});

test("the approvals panel polls once, however often the page is visited", () => {
  const env = makeEnv();
  env.run(HITL);
  env.run(HITL);  // a second run before leaving (a re-injected script)
  assert.equal(env.count().intervals, 1);
  leavePage(env.window);
  assert.deepEqual(env.count(), NOTHING);
});

test("negative control: a closure-local guard stacked one poller per run", () => {
  let old = revert(HITL, "    _stopPolling();\n    window.__kazmaHitlPollTimer = setInterval(refreshPending, POLL_INTERVAL_MS);",
    "    if (_localTimer) return;\n    _localTimer = setInterval(refreshPending, POLL_INTERVAL_MS);");
  old = revert(old, "  function _stopPolling() {", "  var _localTimer = null;\n  function _stopPolling() {");
  old = revert(old, "  window.kazmaAddLeaveHook(teardown);\n", "");
  const env = makeEnv();
  env.run(old);
  leavePage(env.window);
  env.run(old);
  assert.equal(env.count().intervals, 2);
});

// AUD-081: globals nothing read, and a call to a function nothing defines.
const UNREAD_GLOBALS = ["KazmaDashboard", "KazmaHITL", "__hitl_approval__"];

function deadSurface(env, sources) {
  const published = UNREAD_GLOBALS.filter((name) => name in env.window);
  const deadCalls = sources.filter((src) => /KazmaApp\.setIsThinking\(/.test(src)).length;
  return { published, deadCalls };
}

test("the Dashboard's scripts publish no global that nothing reads", () => {
  const env = makeEnv();
  env.run(DASHBOARD + "\n;\n" + HITL);
  assert.deepEqual(deadSurface(env, [DASHBOARD, HITL]), { published: [], deadCalls: 0 });
  // Nothing in the UI reads them (they were removed, not renamed).
  const reads = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (fs.statSync(full).isDirectory()) { walk(full); continue; }
      if (!/\.(js|html)$/.test(name) || name.endsWith(".min.js")) continue;
      // Code only: the comments that say they were removed name them.
      const src = fs.readFileSync(full, "utf8").replace(/\/\/.*$/gm, "");
      for (const g of UNREAD_GLOBALS) if (src.includes(g)) reads.push(name + ": " + g);
    }
  };
  walk(UI);
  assert.deepEqual(reads, []);
});

test("negative control: the shipped scripts published them and called setIsThinking", () => {
  const env = makeEnv();
  const shippedDash = DASHBOARD + "\n;window.KazmaDashboard = { refresh: function () {} };";
  const shippedHitl = HITL + "\n;window.KazmaHITL = {}; window.__hitl_approval__ = {};" +
    "\nif (window.KazmaApp && window.KazmaApp.setIsThinking) { window.KazmaApp.setIsThinking(false); }";
  env.run(shippedDash + "\n;\n" + shippedHitl);
  const found = deadSurface(env, [shippedDash, shippedHitl]);
  assert.deepEqual(found.published, UNREAD_GLOBALS);
  assert.equal(found.deadCalls, 1);
});

// ── Memory ──────────────────────────────────────────────────────────────

const MEMORY = read("static/js/memory_console.js");

test("the Memory page leaves no poller behind", () => {
  const env = twoVisits(MEMORY, { pageElements: true });
  assert.equal(env.count().intervals, 0);
  assert.equal(env.count().listeners, 0);
});

test("negative control: the two raw setIntervals outlived the page", () => {
  let old = revert(MEMORY, "  _memEvery(15000, function() { if (document.hidden) return;",
    "  setInterval(function() { if (document.hidden) return;");
  old = revert(old, "    _memEvery(5000, function() { if (!document.hidden) pollV2Health(); });",
    "    setInterval(function() { if (!document.hidden) pollV2Health(); }, 5000);");
  const env = twoVisits(old, { pageElements: true });
  assert.ok(env.count().intervals >= 2, JSON.stringify(env.count()));
});

// ── Research ────────────────────────────────────────────────────────────

const RESEARCH = read("static/js/research.js");

function archiveClicks(code) {
  const env = makeEnv({ pageElements: true });
  env.run(code);
  leavePage(env.window);
  env.run(code);
  const archived = [];
  env.window.KazmaResearch.archive = (id) => archived.push(id);
  const btn = { getAttribute: (k) => ({ "data-act": "archive", "data-task-id": "t1" })[k], closest: (sel) => (sel.indexOf("#research-list") >= 0 ? {} : null) };
  env.document.dispatch("click", { target: { closest: (sel) => (sel === "[data-act]" ? btn : null) }, stopPropagation() {} });
  leavePage(env.window);
  return { archived, env };
}

test("one Archive click after a round trip archives once, and nothing is left", () => {
  const { archived, env } = archiveClicks(RESEARCH);
  assert.deepEqual(archived, ["t1"]);
  assert.deepEqual(env.count(), NOTHING);
});

test("negative control: the closure-local guard bound a listener per visit", () => {
  const old = revert(RESEARCH, "    }, { signal: _delegatedListeners.signal });", "    });");
  assert.deepEqual(archiveClicks(old).archived, ["t1", "t1"]);
});

// ── Chat ────────────────────────────────────────────────────────────────

const CHAT = read("static/js/chat.js");

test("Chat leaves no listener and no desync healer behind", () => {
  const env = twoVisits(CHAT, { pageElements: true });
  const left = env.count();
  assert.equal(left.listeners, 0, "document/window listeners left: " + left.listeners);
  assert.equal(left.intervals, 0, "timers left: " + left.intervals);
});

test("negative control: Chat as it shipped kept its listeners and its 1.5 s timer", () => {
  let old = CHAT.split("}, _onPage);").join("});");
  old = revert(old, "    try { _pageListeners.abort(); } catch (e) {}\n", "");
  old = revert(old, "    if (_turnSyncTimer) { clearInterval(_turnSyncTimer); _turnSyncTimer = null; }\n", "");
  const env = twoVisits(old, { pageElements: true });
  const left = env.count();
  assert.ok(left.listeners >= 14, "two visits' listeners: " + left.listeners);
  assert.equal(left.intervals, 2);
});

// ── The agent store (Chat's WebSocket bus) ──────────────────────────────

const AGENT_STORE = read("static/js/stores/agentStore.js");

function storeVisits(code) {
  const env = makeEnv();
  const stores = {};
  env.window.Alpine = env.ctx.Alpine = { store: (name, obj) => (obj ? (stores[name] = obj) : stores[name]) };
  for (let visit = 0; visit < 2; visit++) {
    env.run(code);
    stores.agent._startLivenessTicker();
    stores.agent.disconnect();  // chat.js destroyChatMouth on leaving
  }
  return env;
}

test("leaving Chat ends the store's liveness worker", () => {
  assert.equal(storeVisits(AGENT_STORE).count().workers, 0);
});

test("negative control: the worker was never terminated", () => {
  const old = revert(AGENT_STORE, "      this._stopLivenessTicker();\n      this._resetTurnState();",
    "      this._resetTurnState();");
  assert.equal(storeVisits(old).count().workers, 2);
});

// ── Alpine components: Documents and Workspace ──────────────────────────

/** What Alpine.destroyTree does for a component: call its destroy(). */
function alpineDestroy(component) {
  if (typeof component.destroy === "function") component.destroy();
}

const DOCUMENTS = read("static/js/documents.js");

test("a document job's poll ends with the page", () => {
  const env = makeEnv();
  env.run(DOCUMENTS);
  const page = env.run("documentsPage()");
  page.selected = { document_id: "d1" };
  page._startPoll();
  alpineDestroy(page);
  assert.equal(env.count().intervals, 0);
});

test("negative control: Documents had no destroy()", () => {
  const old = revert(DOCUMENTS, "    destroy() {\n      this._stopPoll();\n    },", "");
  const env = makeEnv();
  env.run(old);
  const page = env.run("documentsPage()");
  page._startPoll();
  alpineDestroy(page);
  assert.equal(env.count().intervals, 1);
});

const WORKSPACE_HTML = read("templates/workspace.html");
const WORKSPACE = (() => {
  const start = WORKSPACE_HTML.indexOf("<script>\nfunction workspaceApp()");
  assert.ok(start >= 0, "workspace.html's component script");
  const end = WORKSPACE_HTML.indexOf("</script>", start);
  return WORKSPACE_HTML.slice(start + "<script>".length, end);
})();

async function workspaceVisits(code) {
  const env = makeEnv();
  env.run(code);
  for (let visit = 0; visit < 2; visit++) {
    const app = env.run("workspaceApp()");
    // The loads the pollers run are not under test: answer them at once.
    for (const k of Object.keys(app)) {
      if (typeof app[k] === "function" && /^(load|refresh)/.test(k)) app[k] = async () => {};
    }
    await app.init();
    alpineDestroy(app);
  }
  return env;
}

test("the Workspace leaves no poller or message listener behind", async () => {
  const env = await workspaceVisits(WORKSPACE);
  assert.deepEqual(env.count(), NOTHING);
});

test("negative control: the Workspace had no destroy()", async () => {
  const old = revert(WORKSPACE, "    destroy() {\n      this._destroyed = true;", "    _unusedDestroy() {\n      this._destroyed = true;");
  const env = await workspaceVisits(old);
  assert.equal(env.count().intervals, 6, "three pollers a visit");
  assert.equal(env.count().listeners, 2, "a message listener a visit");
});

// ── The rule itself ─────────────────────────────────────────────────────

test("no page script assigns the leave hooks; each adds to them", () => {
  const JS = path.join(UI, "static", "js");
  const bad = [];
  const walk = (dir) => {
    for (const name of fs.readdirSync(dir)) {
      const full = path.join(dir, name);
      if (fs.statSync(full).isDirectory()) { if (name !== "vendor") walk(full); continue; }
      if (!name.endsWith(".js") || name.endsWith(".min.js")) continue;
      const rel = path.relative(JS, full).split(path.sep).join("/");
      if (rel === "modules/nav.js") continue;  // runs the hooks, then clears them
      const src = fs.readFileSync(full, "utf8");
      if (/kazmaOnSoftNavLeave\s*=(?!=)/.test(src)) bad.push(rel);
    }
  };
  walk(JS);
  for (const name of fs.readdirSync(path.join(UI, "templates"))) {
    if (!name.endsWith(".html") || name === "base.html") continue;
    if (/kazmaOnSoftNavLeave\s*=(?!=)/.test(fs.readFileSync(path.join(UI, "templates", name), "utf8"))) bad.push(name);
  }
  assert.deepEqual(bad, [], "use window.kazmaAddLeaveHook(fn): an assignment drops the hooks before it");
});

test("negative control: an assignment is found", () => {
  assert.ok(/kazmaOnSoftNavLeave\s*=(?!=)/.test("window.kazmaOnSoftNavLeave = destroySwarmMouth;"));
  assert.ok(!/kazmaOnSoftNavLeave\s*=(?!=)/.test("if (window.kazmaOnSoftNavLeave === x) {}"));
});
