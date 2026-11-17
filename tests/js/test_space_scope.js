/**
 * The page names its space on every call, and two tabs never mix
 * (Spaces S1-06). Run: node tests/js/test_space_scope.js
 *
 * space_scope.js adds the X-Kazma-Space header to every fetch to this
 * server and a `space` query value to event streams, sockets and links. The
 * space is the tab's (the page's meta tag, remembered in sessionStorage),
 * never the session's. The negative control keeps the space in one shared
 * place, as a session would: two tabs then send the same space.
 */
"use strict";

const path = require("path");

const scope = require(path.join(
  __dirname, "..", "..", "kazma-ui", "kazma_ui", "static", "js", "space_scope.js"));

const ORIGIN = "https://kazma.example";
let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, detail === undefined ? "" : JSON.stringify(detail));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

/** A window with one tab's page: its meta space, its own sessionStorage. */
function makeTab(pageSpace, href, sharedStorage) {
  const sent = [];
  const store = sharedStorage || {};
  const win = {
    location: {
      origin: ORIGIN,
      host: "kazma.example",
      href: href || ORIGIN + "/chat",
      replaced: null,
      assigned: null,
      replace(url) { this.replaced = url; },
      assign(url) { this.assigned = url; },
    },
    document: {
      querySelector(sel) {
        if (sel !== 'meta[name="kazma-space"]') return null;
        return { getAttribute: () => pageSpace };
      },
    },
    sessionStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { store[k] = String(v); },
    },
    fetch(input, init) {
      const headers = new Headers((init && init.headers) || undefined);
      sent.push({ url: typeof input === "string" ? input : input.url, space: headers.get("X-Kazma-Space"), init });
      return Promise.resolve({ ok: true });
    },
    EventSource: function (url) { this.url = url; },
    WebSocket: function (url, protocols) { this.url = url; this.protocols = protocols; },
  };
  win.WebSocket.OPEN = 1;
  scope.install(win);
  return { win, sent, store };
}

// ── Every request to this server names the space ────────────────────────
{
  const { win, sent } = makeTab("family");
  win.fetch("/api/chat/sessions");
  win.fetch(ORIGIN + "/api/memory/v2/health", { method: "GET", headers: { Accept: "application/json" } });
  win.fetch("/api/chat/stream", { method: "POST", body: "{}" });
  assert("a relative fetch names the space", sent[0].space === "family", sent[0]);
  assert("an absolute same-origin fetch names the space", sent[1].space === "family", sent[1]);
  assert("the caller's own headers are kept",
    new Headers(sent[1].init.headers).get("Accept") === "application/json");
  assert("a POST names the space and keeps its body",
    sent[2].space === "family" && sent[2].init.body === "{}" && sent[2].init.method === "POST", sent[2]);

  win.fetch("https://elsewhere.example/api/x");
  assert("another site is never told the space", sent[3].space === null, sent[3]);

  win.fetch("/api/x", { headers: { "X-Kazma-Space": "chosen" } });
  assert("a caller that names a space itself is not overridden", sent[4].space === "chosen", sent[4]);

  const es = new win.EventSource("/api/swarm/tasks/t1/stream");
  assert("an event stream carries the space in its address",
    es.url === "/api/swarm/tasks/t1/stream?space=family", es.url);
  const es2 = new win.EventSource("/api/x?a=1#frag");
  assert("an address keeps its own query and fragment", es2.url === "/api/x?a=1&space=family#frag", es2.url);
  const ws = new win.WebSocket("wss://kazma.example/ws/chat/abc", ["p"]);
  assert("a socket carries the space", ws.url === "wss://kazma.example/ws/chat/abc?space=family", ws.url);
  assert("a socket keeps its protocols and constants",
    ws.protocols[0] === "p" && win.WebSocket.OPEN === 1);
  const foreign = new win.WebSocket("wss://elsewhere.example/ws");
  assert("a socket to another host is not told the space",
    foreign.url === "wss://elsewhere.example/ws", foreign.url);

  assert("a link gets the space", win.kazmaSpace.url("/api/chat/files/f1") === "/api/chat/files/f1?space=family");
  assert("a link that names a space keeps it",
    win.kazmaSpace.url("/api/chat/files/f1?space=other") === "/api/chat/files/f1?space=other");
  assert("an off-site link is left alone",
    win.kazmaSpace.url("https://elsewhere.example/x") === "https://elsewhere.example/x");
  assert("the tab knows its space", win.kazmaSpace.id === "family");
}

// ── A page with no space names none ─────────────────────────────────────
{
  const { win, sent } = makeTab("");
  win.fetch("/api/auth/status");
  assert("the login page sends no space", sent[0].space === null, sent[0]);
  for (const bad of ["Family", "../x", "a b", "x".repeat(65)]) {
    const tab = makeTab(bad);
    tab.win.fetch("/api/x");
    assert("a malformed space is never sent: " + bad, tab.sent[0].space === null, tab.sent[0]);
  }
}

// ── Two tabs in two spaces do not mix ───────────────────────────────────
{
  const a = makeTab("family");
  const b = makeTab("layla-own");
  a.win.fetch("/api/chat/sessions");
  b.win.fetch("/api/chat/sessions");
  a.win.fetch("/api/chat/sessions");
  assert("tab A always names its own space",
    a.sent.every((s) => s.space === "family"), a.sent.map((s) => s.space));
  assert("tab B always names its own space",
    b.sent.every((s) => s.space === "layla-own"), b.sent.map((s) => s.space));
}

// ── A tab stays in its space across a reload or a bare link ─────────────
{
  const store = {};
  const first = makeTab("family", ORIGIN + "/chat?space=family", store);
  assert("the tab remembers its space", store["kazma.space"] === "family", store);
  assert("no reload when the page matches the tab", first.win.location.replaced === null);

  // The same tab opens /memory with no space: the server drew the person's
  // first space. The page loads again for the tab's space instead.
  const second = makeTab("default", ORIGIN + "/memory", store);
  assert("a bare link reloads the page for the tab's space",
    second.win.location.replaced === ORIGIN + "/memory?space=family", second.win.location.replaced);

  // A link that names a space is a choice: the tab moves to it.
  const third = makeTab("default", ORIGIN + "/memory?space=default", store);
  assert("a link that names a space wins", third.win.location.replaced === null);
  assert("and the tab remembers the new space", store["kazma.space"] === "default", store);

  third.win.kazmaSpace.switchTo("family");
  assert("switching loads the page for the chosen space",
    third.win.location.assigned === ORIGIN + "/memory?space=family", third.win.location.assigned);
  third.win.location.assigned = null;
  third.win.kazmaSpace.switchTo("Bad Id");
  assert("a malformed space is not switched to", third.win.location.assigned === null);
}

// ── Negative control: a space kept where every tab reads it mixes them ───
{
  // What keeping the space in the session would do: one value for the
  // browser, so the tab that switched last decides for both.
  const session = { space: "family" };
  function sessionFetch(log) {
    return scope.wrapFetch((input, init) => {
      log.push(new Headers(init.headers).get("X-Kazma-Space"));
      return Promise.resolve();
    }, () => session.space, ORIGIN);
  }
  const logA = [];
  const logB = [];
  const fetchA = sessionFetch(logA);
  const fetchB = sessionFetch(logB);
  fetchA("/api/chat/sessions");
  session.space = "layla-own"; // tab B switches
  fetchB("/api/chat/sessions");
  fetchA("/api/chat/sessions"); // tab A's next call
  assert("control: with one shared space, tab A's call lands in tab B's space",
    logA[0] === "family" && logA[1] === "layla-own", logA);
}

// ── The switcher shows only when there is something to switch ───────────
{
  function fakeSelect() {
    const select = {
      hidden: true,
      children: [],
      value: "",
      listeners: {},
      ownerDocument: { createElement: () => ({ value: "", textContent: "", selected: false }) },
      get firstChild() { return this.children[0] || null; },
      removeChild(node) { this.children.splice(this.children.indexOf(node), 1); },
      appendChild(node) { this.children.push(node); },
      getAttribute(name) { return name === "data-personal-label" ? "My space" : null; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
    };
    return select;
  }
  const chosen = [];
  const one = fakeSelect();
  assert("one space: no switcher",
    scope.fillSwitcher(one, [{ id: "default", name: "", personal: true }], "default", (id) => chosen.push(id)) === false
    && one.hidden === true);

  const two = fakeSelect();
  const shown = scope.fillSwitcher(two, [
    { id: "default", name: "", personal: true },
    { id: "family", name: "<b>Family</b>", personal: false },
    { id: "Bad Id", name: "x", personal: false },
  ], "family", (id) => chosen.push(id));
  assert("two spaces: the switcher is shown", shown === true && two.hidden === false);
  assert("a malformed space is not offered", two.children.length === 2, two.children.length);
  assert("a personal space with no name takes the page's word", two.children[0].textContent === "My space");
  assert("a space's name is set as text, never as markup", two.children[1].textContent === "<b>Family</b>");
  assert("the tab's space is the chosen option", two.children[1].selected === true && !two.children[0].selected);
  two.value = "default";
  two.listeners.change();
  assert("choosing a space asks to switch to it", chosen.length === 1 && chosen[0] === "default", chosen);
}

process.exit(fail ? 1 : 0);
