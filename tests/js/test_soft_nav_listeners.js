/**
 * Page scripts that soft navigation runs again leave nothing running behind
 * them (audit 2026-10-26 AUD-011, AUD-012).
 *
 * modules/nav.js re-runs a page's scripts on every visit, each in a new
 * closure, and calls window.kazmaOnSoftNavLeave on the way out.
 *  - swarm.js bound three document listeners on every run and removed none:
 *    after one round trip through another page, Retry sent two POSTs and
 *    two new tasks ran.
 *  - voice.js stopped its audio on pagehide only, which a sidebar move never
 *    fires: Live voice kept the microphone and /ws/voice open on every page.
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const { installLeaveHooks, leavePage } = require("./_leave_hooks.js");

const JS = path.join(__dirname, "../../kazma-ui/kazma_ui/static/js");
const read = (name) => fs.readFileSync(path.join(JS, name), "utf8").replace(/\r\n/g, "\n");

/** A document whose addEventListener honours {signal}, as browsers do. */
function makeDoc() {
  const listeners = [];
  return {
    listeners,
    readyState: "complete",
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    addEventListener(type, fn, opts) {
      const entry = { type, fn };
      listeners.push(entry);
      const signal = opts && opts.signal;
      if (signal) signal.addEventListener("abort", () => {
        const i = listeners.indexOf(entry);
        if (i >= 0) listeners.splice(i, 1);
      });
    },
    removeEventListener() {},
    dispatch(type, event) {
      for (const l of listeners.slice()) if (l.type === type) l.fn(event);
    },
  };
}

function bootSwarm(code, document, window, posts) {
  const fetch = async (url, opts) => {
    if (opts && opts.method === "POST") posts.push(url);
    return { ok: true, status: 200, json: async () => ({}) };
  };
  vm.runInNewContext(code, {
    window, document, fetch, console, setInterval: () => 1, clearInterval() {},
    setTimeout: () => 1, clearTimeout() {}, AbortController, EventSource: function () {},
    localStorage: { getItem: () => null, setItem() {} },
  });
}

function retryClick() {
  const btn = { dataset: { action: "retry", taskId: "task-1" } };
  return { target: { closest: (sel) => (sel.indexOf("button[data-action]") >= 0 ? btn : null) } };
}

function swarmRoundTrip(code) {
  const document = makeDoc();
  const window = {
    KazmaStream: {}, kazmaT: (k) => k, location: { search: "" },
    kazmaGetJson: async () => null, kazmaConfirm: async () => false,
    KazmaUtils: { escapeHtml: (x) => String(x) }, KazmaFormat: {},
  };
  installLeaveHooks(window);
  const posts = [];
  bootSwarm(code, document, window, posts);         // first visit
  leavePage(window);
  bootSwarm(code, document, window, posts);         // back again
  document.dispatch("click", retryClick());
  return posts.filter((u) => u.indexOf("/retry") >= 0);
}

test("one Retry after a round trip through another page sends one request", () => {
  assert.deepEqual(swarmRoundTrip(read("swarm.js")), ["/api/swarm/tasks/task-1/retry"]);
});

test("negative control: listeners bound without the controller stack", () => {
  const old = read("swarm.js").replace(/\}, once\);/g, "});");
  assert.equal(swarmRoundTrip(old).length, 2);
});

test("leaving Chat tears Live voice down; the old closure kept it", () => {
  const voice = read("voice.js");
  assert.match(voice, /teardown: teardown/);
  const chat = read("chat.js");
  const destroy = chat.slice(chat.indexOf("function destroyChatMouth()"),
    chat.indexOf("window.kazmaAddLeaveHook(destroyChatMouth);"));
  assert.ok(destroy.length > 0 && destroy.length < 4000, "destroyChatMouth not found");
  assert.match(destroy, /KazmaVoice\.teardown\(\)/);
});

test("voice teardown closes the live socket and the microphone", () => {
  // Run voice.js, open a fake live session through its own state, tear down.
  const code = read("voice.js");
  const closed = [];
  const window = { KAZMA_I18N: {}, addEventListener() {}, location: { protocol: "http:", host: "x" } };
  const document = makeDoc();
  const probed = code.replace("  window.KazmaVoice = {",
    "  window.__probe = { setWs: function(w){ ws = w; isStreaming = true; }," +
    " setMic: function(m){ micStream = m; } };\n  window.KazmaVoice = {");
  assert.notEqual(probed, code, "the probe found no place in voice.js");
  window.kazmaGetJson = async () => null;
  vm.runInNewContext(probed, {
    window, document, console, localStorage: { getItem: () => null, setItem() {} },
    fetch: async () => ({ ok: false, json: async () => ({}) }),
    navigator: {}, setTimeout: () => 1, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    WebSocket: { OPEN: 1 }, showToast() {},
  });
  window.__probe.setWs({ readyState: 1, send() {}, close() { closed.push("ws"); } });
  window.__probe.setMic({ getTracks: () => [{ stop() { closed.push("mic"); } }] });
  window.KazmaVoice.teardown();
  assert.ok(closed.includes("ws"), "the /ws/voice socket stayed open");
  assert.ok(closed.includes("mic"), "the microphone stayed on");
  assert.equal(window.KazmaVoice.isStreaming(), false);
});
