/**
 * `kazma:themechange`: one event on document whenever the theme or the
 * accent changes, and none when nothing changed.
 * Run: node tests/js/test_theme_change_event.js
 *
 * Pages that draw with colour tokens on a canvas or an SVG (charts, the
 * memory graph) read the tokens once; without a signal they kept the old
 * theme's colours until a reload (UI refresh, 2026-11-09, stage 2). The
 * shell dispatches `kazma:themechange` with detail {theme, reason} from the
 * two functions every path goes through: syncDocumentColorScheme (the
 * header's toggle, Settings' save and preview, Auto following the OS) and
 * applyAccentColor (Settings' accent). Auto also re-resolves the theme
 * when the OS switches light/dark while the page is open.
 *
 * Negative control: the same module with the dispatch removed announces
 * nothing, and the gate catches it.
 */
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.resolve(__dirname, "..", "..");
const src = fs.readFileSync(
  path.join(root, "kazma-ui", "kazma_ui", "static", "js", "modules", "components.js"), "utf8",
).replace(/\r\n/g, "\n");

let fail = 0;
function ok(name, cond, detail) {
  if (cond) { console.log("ok  ", name); return; }
  fail += 1;
  console.error("FAIL", name, detail === undefined ? "" : detail);
}

/** A tiny DOM: <html> with attributes and an inline style, metas, events. */
function makeWorld({ systemDark = false, serverTheme = "light" } = {}) {
  const attrs = new Map([["data-theme", "light"]]);
  const styleProps = new Map();
  const listeners = {};
  const events = [];
  const mqListeners = [];
  const metas = new Map();
  const documentElement = {
    getAttribute: (k) => (attrs.has(k) ? attrs.get(k) : null),
    setAttribute: (k, v) => attrs.set(k, String(v)),
    hasAttribute: (k) => attrs.has(k),
    lang: "en",
    style: {
      setProperty: (k, v) => styleProps.set(k, String(v)),
      removeProperty: (k) => styleProps.delete(k),
      getPropertyValue: (k) => styleProps.get(k) || "",
    },
  };
  const document = {
    documentElement,
    head: { appendChild: () => {} },
    querySelector: (sel) => {
      const m = /meta\[name="([^"]+)"\]/.exec(sel);
      if (!m) return null;
      if (!metas.has(m[1])) metas.set(m[1], { setAttribute() {}, getAttribute() { return ""; } });
      return metas.get(m[1]);
    },
    createElement: () => ({ setAttribute() {} }),
    addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
    dispatchEvent: (ev) => {
      events.push(ev);
      (listeners[ev.type] || []).forEach((fn) => fn(ev));
      return true;
    },
  };
  class CustomEvent {
    constructor(type, init) { this.type = type; this.detail = init && init.detail; }
  }
  const mq = {
    matches: systemDark,
    addEventListener: (type, fn) => { if (type === "change") mqListeners.push(fn); },
  };
  const window = {
    matchMedia: () => mq,
    innerWidth: 1280,
    addEventListener: () => {},
    kazmaGetJson: async () => ({ theme: serverTheme }),
  };
  const localStorage = { getItem: () => null, setItem: () => {} };
  const fetch = async () => ({ ok: true, json: async () => ({ theme: serverTheme }) });
  return {
    sandbox: {
      document, window, localStorage, fetch, CustomEvent, Math, Object, parseInt, Promise,
      getComputedStyle: () => ({ getPropertyValue: () => "" }),
      console,
    },
    attrs, styleProps, events, mq, mqListeners,
  };
}

/** The whole module, its exports as plain functions, in *world*. */
function load(world, patch) {
  let code = src.replace(/^export function /gm, "function ");
  if (patch) {
    if (!code.includes(patch.from)) throw new Error("negative control: patch target moved");
    code = code.split(patch.from).join(patch.to);
  }
  vm.runInNewContext(code + "\nthis.__m = { syncDocumentColorScheme, applyAccentColor, kazmaApp };", world.sandbox);
  return world.sandbox.__m;
}

const themeEvents = (w) => w.events.filter((e) => e.type === "kazma:themechange");
const tick = () => new Promise((r) => setTimeout(r, 0));

/** Every check; returns what went wrong (empty = the gate passes). */
async function check(patch) {
  const problems = [];

  // 1. The theme: an event per change, none for a call that changes nothing.
  {
    const w = makeWorld();
    const m = load(w, patch);
    m.syncDocumentColorScheme("dark");
    const ev = themeEvents(w);
    if (ev.length !== 1) problems.push("light -> dark announced " + ev.length + " times");
    else if (!ev[0].detail || ev[0].detail.theme !== "dark") problems.push("detail.theme is not 'dark': " + JSON.stringify(ev[0].detail));
    m.syncDocumentColorScheme("dark");
    if (themeEvents(w).length !== 1) problems.push("an unchanged theme was announced");
    m.syncDocumentColorScheme("light");
    const last = themeEvents(w).slice(-1)[0];
    if (themeEvents(w).length !== 2 || !last || last.detail.theme !== "light") problems.push("dark -> light not announced as light");
  }

  // 2. The accent: an event when its variables change (with the theme it is
  //    in), none when the same accent is applied again.
  {
    const w = makeWorld();
    const m = load(w, patch);
    w.attrs.set("data-theme", "dark");
    m.applyAccentColor("#10b981");
    let ev = themeEvents(w);
    if (ev.length !== 1) problems.push("a new accent announced " + ev.length + " times");
    else if (ev[0].detail.theme !== "dark" || ev[0].detail.reason !== "accent") problems.push("accent detail: " + JSON.stringify(ev[0].detail));
    m.applyAccentColor("#10b981");
    if (themeEvents(w).length !== 1) problems.push("re-applying the same accent was announced");
    m.applyAccentColor("");
    ev = themeEvents(w);
    if (ev.length !== 2) problems.push("clearing the accent was not announced");
  }

  // 3. The header's toggle (the app's own path).
  {
    const w = makeWorld();
    const m = load(w, patch);
    const app = m.kazmaApp();
    app.theme = "light";
    app.toggleTheme();
    const ev = themeEvents(w);
    if (ev.length !== 1 || ev[0].detail.theme !== "dark") problems.push("the toggle: " + JSON.stringify(ev.map((e) => e.detail)));
  }

  // 4. Auto follows the OS while the page is open, and announces it; a fixed
  //    choice does not follow.
  for (const [pref, expectFollow] of [["auto", true], ["light", false]]) {
    const w = makeWorld({ systemDark: false, serverTheme: pref });
    const m = load(w, patch);
    const app = m.kazmaApp();
    app.$watch = () => {};
    app.init();
    await tick(); await tick(); await tick();
    const before = themeEvents(w).length;
    w.mq.matches = true;                       // the OS switches to dark
    w.mqListeners.forEach((fn) => fn({ matches: true }));
    await tick(); await tick(); await tick();
    const after = themeEvents(w).length;
    if (w.mqListeners.length === 0) problems.push(pref + ": no OS listener");
    if (expectFollow) {
      if (w.attrs.get("data-theme") !== "dark") problems.push("auto: the theme did not follow the OS");
      if (after !== before + 1) problems.push("auto: following the OS was announced " + (after - before) + " times");
    } else {
      if (w.attrs.get("data-theme") !== "light") problems.push(pref + ": a fixed theme followed the OS");
      if (after !== before) problems.push(pref + ": announced without a change");
    }
  }
  return problems;
}

(async () => {
  const problems = await check();
  ok("the theme and the accent are announced once per change, Auto follows the OS",
    problems.length === 0, problems);

  // Negative control: no dispatch at all -> the gate fails.
  const control = await check({
    from: "document.dispatchEvent(new CustomEvent('kazma:themechange'",
    to: "void (0, new CustomEvent('kazma:themechange'",
  });
  ok("negative control: a module that never dispatches is caught",
    control.some((p) => p.includes("announced 0 times") || p.includes("not announced")), control);

  // Negative control: announcing every call (even unchanged) is caught.
  const noisy = await check({ from: "if (changed) announceThemeChange('theme');", to: "announceThemeChange('theme');" });
  ok("negative control: announcing an unchanged theme is caught",
    noisy.some((p) => p.includes("unchanged theme")), noisy);

  if (fail) { console.log(fail + " failure(s)"); process.exit(1); }
  console.log("all ok");
})();
