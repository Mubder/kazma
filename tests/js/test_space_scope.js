/**
 * The page names its space on every call, and two tabs never mix
 * (Spaces S1-06). Run: node tests/js/test_space_scope.js
 *
 * space_scope.js adds the X-Kazma-Space header to every fetch to this
 * server and a `space` query value to event streams, sockets and links. The
 * space is the tab's (the page's meta tag, remembered in sessionStorage),
 * never the session's. The negative control keeps the space in one shared
 * place, as a session would: two tabs then send the same space.
 *
 * Only on an install that enforces Spaces. On any other install the page
 * names nothing and reloads nothing: an older multi-user install's users and
 * OIDC sign-ins are members of no space, and a page that named the space it
 * was rendered for had every call after its load refused.
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

/** A window with one tab's page: its meta space, its own sessionStorage.
 *  `enforced` is what the page says about the install (default: enforced). */
function makeTab(pageSpace, href, sharedStorage, enforced) {
  const pageEnforces = enforced === undefined ? true : enforced;
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
        if (sel === 'meta[name="kazma-spaces-enforced"]') {
          return { getAttribute: () => (pageEnforces ? "1" : "") };
        }
        if (sel !== 'meta[name="kazma-space"]') return null;
        return { getAttribute: () => pageSpace };
      },
      getElementById: () => null,
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

// ── A chat the person moved to another space is opened there (S2-12) ─────
{
  const { win, store } = makeTab("family");
  win.kazmaSpace.openChat("default", "s & 1");
  const opened = new URL(win.location.assigned);
  assert("the moved chat is opened where it lives", opened.pathname === "/chat"
    && opened.searchParams.get("s") === "s & 1" && opened.searchParams.get("space") === "default", opened.href);
  assert("and the tab stays in that space", Object.values(store).includes("default"), store);
  const other = makeTab("family");
  other.win.kazmaSpace.openChat("default", "");
  other.win.kazmaSpace.openChat("//evil.example", "s1");
  assert("no chat or no valid space: the tab stays", other.win.location.assigned === null, other.win.location);
  const off = makeTab("family", undefined, undefined, false);
  off.win.kazmaSpace.openChat("default", "s1");
  assert("nothing on an install that does not enforce Spaces", off.win.location.assigned === null);
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

// ── An install that does not enforce Spaces: the page is as it was ──────
{
  const { win, sent, store } = makeTab("default", ORIGIN + "/chat", {}, false);
  win.fetch("/api/chat/sessions");
  win.fetch("/api/chat/stream", { method: "POST", body: "{}" });
  assert("not enforced: a fetch names no space",
    sent.every((s) => s.space === null), sent.map((s) => s.space));
  const es = new win.EventSource("/api/swarm/tasks/t1/stream");
  assert("not enforced: an event stream's address is unchanged",
    es.url === "/api/swarm/tasks/t1/stream", es.url);
  const ws = new win.WebSocket("wss://kazma.example/ws/chat/abc");
  assert("not enforced: a socket's address is unchanged",
    ws.url === "wss://kazma.example/ws/chat/abc", ws.url);
  assert("not enforced: a link is unchanged",
    win.kazmaSpace.url("/api/chat/files/f1") === "/api/chat/files/f1");
  assert("not enforced: the page still knows the space it was drawn for",
    win.kazmaSpace.id === "default");
  assert("not enforced: nothing is remembered for the tab", !("kazma.space" in store), store);
  win.kazmaSpace.switchTo("family");
  assert("not enforced: there is nothing to switch to", win.location.assigned === null);

  // The tab was used by a user of tenant "acme"; now another sign-in's page
  // loads in it, drawn for "default". The page stays where the server put it.
  const stale = { "kazma.space": "acme" };
  const next = makeTab("default", ORIGIN + "/memory", stale, false);
  assert("not enforced: a space remembered from another sign-in reloads nothing",
    next.win.location.replaced === null, next.win.location.replaced);
  next.win.fetch("/api/memory/v2/health");
  assert("not enforced: and its calls name no space", next.sent[0].space === null, next.sent[0]);

  // Control: the same tab on a page that names its space whatever the
  // install is (the script as first written) is sent to the old space, and
  // names the drawn one on every call: what an older install then refused.
  const control = makeTab("default", ORIGIN + "/memory", { "kazma.space": "acme" }, true);
  assert("control: naming regardless of the install sends the tab to the old space",
    control.win.location.replaced === ORIGIN + "/memory?space=acme", control.win.location.replaced);
  const naming = makeTab("default", ORIGIN + "/chat", {}, true);
  naming.win.fetch("/api/chat/sessions");
  assert("control: naming regardless of the install names the drawn space",
    naming.sent[0].space === "default", naming.sent[0]);
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

// ── The header says which space and which kind (Spaces S2-12) ────────────
{
  const WORDS = { "data-personal": "Personal", "data-shared": "Shared", "data-project": "Shared, with a project",
    "data-personal-label": "My space" };
  function fakeDoc(switcherShown) {
    const els = {};
    for (const id of ["space-kind", "space-name", "new-private-chat", "space-switcher"]) {
      els[id] = {
        hidden: id === "space-switcher" ? !switcherShown : true,
        textContent: "",
        attrs: {},
        getAttribute(name) { return WORDS[name] || this.attrs[name] || null; },
        setAttribute(name, value) { this.attrs[name] = value; },
      };
    }
    return { els, getElementById: (id) => els[id] || null };
  }
  const SPACES = [
    { id: "layla-own", name: "", personal: true, kind: "sandboxed" },
    { id: "family", name: "Family", personal: false, kind: "sandboxed" },
    { id: "dev", name: "<b>Dev</b>", personal: false, kind: "host" },
  ];

  const own = fakeDoc(true);
  const mine = scope.showSpace(own, SPACES, "layla-own");
  assert("in the personal space the kind is personal",
    own.els["space-kind"].textContent === "Personal" && own.els["space-kind"].hidden === false);
  assert("no private chat offered in the personal space", own.els["new-private-chat"].hidden === true);
  assert("the personal space is found", mine && mine.id === "layla-own");
  assert("with a switcher, the name is the switcher's to show", own.els["space-name"].hidden === true);

  const family = fakeDoc(true);
  scope.showSpace(family, SPACES, "family");
  assert("a plain shared space says shared", family.els["space-kind"].textContent === "Shared"
    && family.els["space-kind"].attrs["data-kind"] === "shared");
  assert("a shared space offers a new private chat", family.els["new-private-chat"].hidden === false);

  const dev = fakeDoc(false);
  scope.showSpace(dev, [SPACES[0], SPACES[2]].slice(1), "dev");
  assert("a shared space with a project folder says so",
    dev.els["space-kind"].textContent === "Shared, with a project");
  assert("with no switcher the name is shown, as text",
    dev.els["space-name"].hidden === false && dev.els["space-name"].textContent === "<b>Dev</b>");
  assert("no personal space known: no private chat offered", dev.els["new-private-chat"].hidden === true);

  const lone = fakeDoc(false);
  scope.showSpace(lone, [SPACES[0]], "layla-own");
  assert("one personal space with no name takes the page's word",
    lone.els["space-name"].textContent === "My space" && lone.els["space-name"].hidden === false);
}

process.exit(fail ? 1 : 0);
