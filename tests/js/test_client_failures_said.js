/**
 * Client paths that went quiet on a failure (audit 2026-10-26).
 *
 *  - AUD-058: the Documents library on an `ok: false` answer left the list
 *    blank, with no loading, empty or error state.
 *  - AUD-067: X Studio's destroy() cancelled the pending composer autosave,
 *    so the last 600 ms of typing were lost on a soft navigation.
 *  - AUD-068: a semantic-option decision on the Dashboard's approvals panel
 *    ignored the response status and refreshed as if it had been taken.
 *  - AUD-069: a sidebar link whose page answered 403 did nothing, silently.
 *
 * Each case has a negative control built from the code that shipped.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");
const { pathToFileURL } = require("node:url");
const { test } = require("node:test");
const { installLeaveHooks } = require("./_leave_hooks.js");

const JS = path.join(__dirname, "../../kazma-ui/kazma_ui/static/js");
const read = (name) => fs.readFileSync(path.join(JS, name), "utf8").replace(/\r\n/g, "\n");

function revert(src, from, to) {
  assert.ok(src.includes(from), "negative control: not found: " + from.slice(0, 80));
  return src.split(from).join(to);
}

// ── AUD-058 ──────────────────────────────────────────────────────────────

async function documentsAfterRefusal(code) {
  const ctx = vm.createContext({
    window: { showToast() {} },
    kazmaT: (k, fallback) => fallback,
    console,
    fetch: async () => ({ ok: false, status: 503, json: async () => ({ ok: false, error: "Document platform is not available" }) }),
  });
  vm.runInContext(code, ctx);
  const page = vm.runInContext("documentsPage()", ctx);
  page.documents = [{ document_id: "kept" }];
  await page.loadDocuments();
  return page;
}

test("an ok:false library read says it failed and keeps the earlier list", async () => {
  const page = await documentsAfterRefusal(read("documents.js"));
  assert.equal(page.documentsError, "Document platform is not available");
  assert.equal(page.documentsLoading, false);
  assert.deepEqual(page.documents.map((d) => d.document_id), ["kept"]);
});

test("negative control: the shipped loader left no trace of the failure", async () => {
  const old = revert(read("documents.js"),
    "        } else {\n          // An `ok: false` answer", "        }\n        if (false) {\n          // An `ok: false` answer");
  const page = await documentsAfterRefusal(old);
  assert.equal(page.documentsError, "");
  assert.equal(page.documentsLoaded, false);
});

// ── AUD-067 ──────────────────────────────────────────────────────────────

function xStudioWithUnsavedTyping(code) {
  const puts = [];
  const sandbox = {
    window: {}, console,
    document: { documentElement: { lang: "en", getAttribute: () => "ltr" } },
    showToast() {},
    fetch: async (url, opts) => {
      if (opts && opts.method === "PUT") puts.push(JSON.parse(opts.body).text);
      return { ok: true, json: async () => ({ ok: true, revision: 2, updated_at: "now" }) };
    },
    setTimeout: () => 1, clearTimeout() {},
  };
  sandbox.window = sandbox;
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  const page = vm.runInContext("xStudioPage()", sandbox);
  page._composerLoaded = true;
  page._composerRevision = 1;
  page._composerSaved = page._composerSignature();
  page.text = "typed just before leaving";
  page.queueComposerSave();   // the 600 ms autosave, never reached
  page.destroy();             // Alpine, on a sidebar link
  return puts;
}

test("leaving X Studio saves the composer typing that was waiting", async () => {
  const puts = xStudioWithUnsavedTyping(read("x_studio.js"));
  await new Promise(setImmediate);
  assert.deepEqual(puts, ["typed just before leaving"]);
});

test("negative control: the shipped destroy() dropped it", async () => {
  const old = revert(read("x_studio.js"),
    "          && this._composerSignature() !== this._composerSaved) {\n        this.saveComposer();",
    "          && this._composerSignature() !== this._composerSaved) {\n        void 0;");
  const puts = xStudioWithUnsavedTyping(old);
  await new Promise(setImmediate);
  assert.deepEqual(puts, []);
});

// ── AUD-068 ──────────────────────────────────────────────────────────────

/** Run hitl_approval.js with one semantic card and press its option. */
async function pressSemanticOption(code, answer) {
  let actions = { innerHTML: "" };
  const card = {
    getAttribute: (k) => ({ "data-interrupt-id": "i1", "data-gate-id": "g1" })[k] || "",
    querySelectorAll: () => [],
    querySelector: (sel) => (sel === ".hitl-approval-actions" ? actions : null),
  };
  let clickHandler = null;
  const btn = {
    getAttribute: (k) => ({ "data-thread-id": "t1", "data-tcid": "c1", "data-opt": "yes" })[k],
    closest: () => card,
    addEventListener: (_type, fn) => { clickHandler = fn; },
  };
  const list = {
    set innerHTML(_v) {},
    querySelectorAll: (sel) => (sel === ".hitl-sem-opt-btn" ? [btn] : []),
  };
  const panel = { querySelector: (sel) => (sel === ".hitl-approval-list" ? list : null), classList: { add() {}, remove() {} } };
  const fetched = [];
  const window = {
    addEventListener() {},
    t: (k) => k,
  };
  installLeaveHooks(window);
  const sandbox = {
    window, console,
    document: {
      readyState: "complete",
      getElementById: (id) => (id === "hitl-approvals-panel" ? panel : null),
      addEventListener() {},
    },
    setInterval: () => 1, clearInterval() {},
    setTimeout: () => 1,
    AbortController,
    fetch: async (url, opts) => {
      fetched.push(url);
      if (url === "/api/pending-approvals") {
        return { ok: true, json: async () => ({ pending: [{ kind: "semantic_clarify", thread_id: "t1", gate_id: "g1", items: [{ question: "Which?", tool_call_id: "c1", options: [{ id: "yes", label: "Yes" }] }] }] }) };
      }
      return answer;
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  await new Promise(setImmediate);  // the first refresh paints the card
  assert.ok(clickHandler, "the semantic option was wired");
  fetched.length = 0;
  clickHandler();
  for (let i = 0; i < 5; i++) await new Promise(setImmediate);
  return { actions, fetched };
}

const REFUSED = { ok: false, status: 500, json: async () => ({ ok: false, error: "resume failed" }) };

test("a refused semantic decision says so instead of refreshing as taken", async () => {
  const { actions, fetched } = await pressSemanticOption(read("hitl_approval.js"), REFUSED);
  assert.match(actions.innerHTML, /chat\.gate_failed_retry|Failed/);
  assert.match(actions.innerHTML, /resume failed/);
  assert.deepEqual(fetched, ["/api/approve/t1"], "no refresh over a refused decision");
});

test("a taken semantic decision refreshes the panel", async () => {
  const taken = { ok: true, status: 200, json: async () => ({ ok: true }) };
  const { fetched } = await pressSemanticOption(read("hitl_approval.js"), taken);
  assert.deepEqual(fetched, ["/api/approve/t1", "/api/pending-approvals"]);
});

test("negative control: the shipped handler refreshed over a refusal", async () => {
  const src = read("hitl_approval.js");
  const start = src.indexOf("        }).then(function (r) {\n          // A refused decision");
  const end = src.indexOf("          .catch(function() { if (act) act.innerHTML", start);
  assert.ok(start > 0 && end > start, "the handler under test");
  const old = src.slice(0, start) + "        }).then(function() { refreshPending(); })\n" + src.slice(end);
  const { actions, fetched } = await pressSemanticOption(old, REFUSED);
  assert.doesNotMatch(actions.innerHTML, /resume failed/);
  assert.deepEqual(fetched, ["/api/approve/t1", "/api/pending-approvals"]);
});

// ── AUD-069 ──────────────────────────────────────────────────────────────

/** Import a copy of nav.js (fresh module state) and click a link answered 403. */
async function clickLinkAnswered(status, navSource) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kazma-nav-"));
  const file = path.join(dir, "nav.mjs");
  fs.writeFileSync(file, navSource);
  const documentEvents = new Map();
  global.window = { addEventListener() {}, removeEventListener() {} };
  global.location = { href: "http://localhost/chat", origin: "http://localhost", pathname: "/chat", search: "" };
  window.location = global.location;
  global.history = { state: { kazmaNavIndex: 1, kazmaNavHistory: "h" }, replaceState(s) { this.state = s; }, pushState(s) { this.state = s; }, go() {} };
  global.document = {
    querySelectorAll: () => [], documentElement: { classList: { toggle() {} } }, body: { classList: { toggle() {} } },
    addEventListener: (type, fn) => documentEvents.set(type, fn),
  };
  global.fetch = async () => ({ status, ok: false, redirected: false, url: "", headers: { get: () => "text/html" }, text: async () => "" });
  const nav = await import(pathToFileURL(file).href);
  nav.initSoftNav();
  const anchor = { href: "http://localhost/dashboard", origin: "http://localhost", target: "", hasAttribute: () => false };
  documentEvents.get("click")({ button: 0, target: { closest: () => anchor }, preventDefault() {} });
  for (let i = 0; i < 5; i++) await new Promise(setImmediate);
  fs.rmSync(dir, { recursive: true, force: true });
  return window.location.href;
}

test("a link whose page answers 403 loads the page whole", async () => {
  assert.equal(await clickLinkAnswered(403, read("modules/nav.js")), "http://localhost/dashboard");
});

test("negative control: the shipped soft navigation did nothing on a 403", async () => {
  const old = revert(read("modules/nav.js"),
    "            if (res.status === 401) {\n                return;\n            }\n            if (res.status === 403) {\n                window.location.href = url;\n                return;\n            }",
    "            if (res.status === 401 || res.status === 403) {\n                return;\n            }");
  assert.equal(await clickLinkAnswered(403, old), "http://localhost/chat");
});
