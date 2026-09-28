/**
 * Every member a page reads from a Kazma namespace exists on it.
 *
 * The Swarm page's Templates tab called `KazmaUtils.esc`, which never
 * existed (the helper is `escapeHtml`): rendering threw, the fetch's catch
 * took the TypeError for a network failure, and the tab said "Failed to load
 * templates" over three good templates (found on live 2026-09-28). Nothing
 * failed at load, so the page-load gates could not see it.
 *
 * This loads every script that defines a namespace (`window.KazmaX = ...`,
 * `var KazmaStream = ...`, the ES module's exports) in a sandbox where no
 * page is ready, reads what each namespace actually holds, and checks every
 * `KazmaX.member` written in the static JavaScript and the templates
 * against it. A member some code assigns (`KazmaX.member = ...`) counts as
 * defined.
 *
 * Run: node tests/js/test_namespace_members.js
 */
"use strict";

const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const { pathToFileURL } = require("url");

const UI = path.join(__dirname, "..", "..", "kazma-ui", "kazma_ui");
const JS = path.join(UI, "static", "js");
const TEMPLATES = path.join(UI, "templates");

function walk(dir, exts, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      if (ent.name === "vendor" || ent.name === "node_modules") continue;
      walk(p, exts, out);
    } else if (exts.some((e) => ent.name.endsWith(e)) && !ent.name.endsWith(".min.js")) {
      out.push(p);
    }
  }
  return out;
}

/** A value that is anything: callable, constructible, any property. */
function anything(store = {}) {
  const fn = function () {};
  return new Proxy(fn, {
    get(target, prop) {
      if (prop in store) return store[prop];
      if (prop === Symbol.toPrimitive) return () => "";
      if (prop === "then") return undefined; // never a thenable
      if (prop === Symbol.iterator) return function* () {};
      if (prop === "length") return 0;
      if (typeof prop === "symbol") return undefined;
      return anything();
    },
    set(target, prop, value) {
      store[prop] = value;
      return true;
    },
    has() {
      return true;
    },
    apply() {
      return anything();
    },
    construct() {
      return anything();
    },
  });
}

/** A sandbox where no page is ready: init code waits for events that never come. */
function sandbox() {
  const noop = () => {};
  const storage = new Map();
  const doc = anything({
    readyState: "loading",
    addEventListener: noop,
    removeEventListener: noop,
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
    documentElement: anything({ lang: "en", dir: "ltr" }),
    cookie: "",
  });
  const ctx = {
    console: { log: noop, info: noop, warn: noop, error: noop, debug: noop },
    document: doc,
    navigator: anything({ userAgent: "node", language: "en" }),
    location: anything({ pathname: "/", search: "", hash: "", href: "http://localhost/", origin: "http://localhost" }),
    localStorage: { getItem: (k) => (storage.has(k) ? storage.get(k) : null), setItem: (k, v) => storage.set(k, String(v)), removeItem: (k) => storage.delete(k) },
    sessionStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    setTimeout: () => 0,
    clearTimeout: noop,
    setInterval: () => 0,
    clearInterval: noop,
    requestAnimationFrame: () => 0,
    cancelAnimationFrame: noop,
    queueMicrotask: noop,
    fetch: () => new Promise(() => {}),
    addEventListener: noop,
    removeEventListener: noop,
    dispatchEvent: noop,
    matchMedia: () => anything({ matches: false, addEventListener: noop, addListener: noop }),
    getComputedStyle: () => anything(),
    MutationObserver: function () { return anything(); },
    ResizeObserver: function () { return anything(); },
    IntersectionObserver: function () { return anything(); },
    CustomEvent: function () { return anything(); },
    Event: function () { return anything(); },
    EventSource: function () { return anything(); },
    WebSocket: function () { return anything(); },
    AbortController: function () { return anything(); },
    URL, URLSearchParams, TextEncoder, TextDecoder, Promise, Map, Set, WeakMap, WeakSet,
    Date, Math, JSON, Object, Array, String, Number, Boolean, RegExp, Error, TypeError,
    Symbol, Proxy, Reflect, Intl, parseInt, parseFloat, isNaN, isFinite,
    encodeURIComponent, decodeURIComponent, structuredClone,
  };
  ctx.window = ctx;
  ctx.self = ctx;
  ctx.globalThis = ctx;
  vm.createContext(ctx);
  return ctx;
}

const NS_DEF = /(?:\bwindow\.|\broot\.|\bvar\s+|\bconst\s+|\blet\s+|export\s+const\s+)(Kazma[A-Z]\w*)\s*=(?!=)/g;
const MEMBER_REF = /(?<![\w$.])(?:window\.)?(Kazma[A-Z]\w*)\.([A-Za-z_$][\w$]*)/g;
const MEMBER_SET = /(?<![\w$.])(?:window\.|root\.)?(Kazma[A-Z]\w*)\.([A-Za-z_$][\w$]*)\s*=(?!=)/g;

function stripComments(src) {
  // Block and line comments; strings are left alone (a URL in a string keeps
  // its "//"): good enough for finding member references in code.
  // A block comment keeps its newlines, so reported line numbers stay true.
  return src
    .replace(/\/\*[\s\S]*?\*\//g, (c) => c.replace(/[^\n]/g, ""))
    .replace(/(^|[^:"'`\\])\/\/[^\n]*/g, "$1");
}

async function namespaces() {
  const jsFiles = walk(JS, [".js"]);
  const found = new Map(); // name -> Set(members)
  const problems = [];
  const ctx = sandbox();
  for (const file of jsFiles) {
    const src = fs.readFileSync(file, "utf8");
    const isModule = /^\s*(import|export)\s/m.test(src);
    // A module defines a namespace by exporting it; its `window.KazmaX = X`
    // lines (app.js) only re-publish what another module exported.
    const pattern = isModule ? /export\s+const\s+(Kazma[A-Z]\w*)\s*=/g : NS_DEF;
    const defined = new Set();
    for (const m of src.matchAll(pattern)) defined.add(m[1]);
    if (!defined.size) continue;
    try {
      if (isModule) {
        const mod = await import(pathToFileURL(file).href);
        for (const name of defined) {
          if (mod[name] && typeof mod[name] === "object") ctx[name] = mod[name];
        }
      } else {
        vm.runInContext(src, ctx, { filename: file });
      }
    } catch (err) {
      problems.push(`${path.relative(UI, file)} did not load in the sandbox: ${err && err.message}`);
      continue;
    }
    for (const name of defined) {
      let value;
      try {
        value = vm.runInContext(`typeof ${name} !== "undefined" ? ${name} : window.${name}`, ctx);
      } catch (_) {
        value = ctx[name];
      }
      if (value == null || (typeof value !== "object" && typeof value !== "function")) continue;
      const members = found.get(name) || new Set();
      for (let o = value; o && o !== Object.prototype && o !== Function.prototype; o = Object.getPrototypeOf(o)) {
        for (const k of Object.getOwnPropertyNames(o)) members.add(k);
      }
      found.set(name, members);
    }
  }
  return { found, problems };
}

function references(files) {
  const refs = []; // {ns, member, where}
  const assigned = new Map(); // ns -> Set(members)
  for (const file of files) {
    const code = stripComments(fs.readFileSync(file, "utf8"));
    for (const m of code.matchAll(MEMBER_SET)) {
      if (!assigned.has(m[1])) assigned.set(m[1], new Set());
      assigned.get(m[1]).add(m[2]);
    }
    for (const m of code.matchAll(MEMBER_REF)) {
      const line = code.slice(0, m.index).split("\n").length;
      refs.push({ ns: m[1], member: m[2], where: `${path.relative(UI, file)}:${line}` });
    }
  }
  return { refs, assigned };
}

function missing(found, refs, assigned) {
  const out = [];
  for (const r of refs) {
    const members = found.get(r.ns);
    if (!members) continue; // not a namespace this UI defines (or not an object)
    if (members.has(r.member)) continue;
    if (assigned.get(r.ns) && assigned.get(r.ns).has(r.member)) continue;
    out.push(`${r.where}: ${r.ns}.${r.member} does not exist`);
  }
  return out;
}

(async () => {
  const { found, problems } = await namespaces();
  assert.deepStrictEqual(problems, [], problems.join("\n"));

  // The enumeration is not blind: the namespaces pages lean on most.
  for (const name of ["KazmaUtils", "KazmaAPI", "KazmaIcons", "KazmaStream", "KazmaChat", "KazmaSwarm"]) {
    assert.ok(found.has(name), `${name} was not found or did not load`);
  }
  assert.ok(found.get("KazmaUtils").has("escapeHtml"));

  const files = walk(JS, [".js"]).concat(walk(TEMPLATES, [".html"]));
  const { refs, assigned } = references(files);
  assert.ok(refs.length > 200, `only ${refs.length} namespace references found`);
  const bad = missing(found, refs, assigned);
  assert.deepStrictEqual(bad, [], "\n" + bad.join("\n"));

  // Negative control: the call the Templates tab made until 2026-09-28.
  const control = path.join(require("os").tmpdir(), `kazma_ns_control_${process.pid}.js`);
  fs.writeFileSync(control, "c.innerHTML = '<span>' + KazmaUtils.esc(tmpl.name) + '</span>';\n");
  try {
    const caught = missing(found, references([control]).refs, new Map());
    assert.strictEqual(caught.length, 1, "the control was not caught");
    assert.ok(caught[0].endsWith("KazmaUtils.esc does not exist"), caught[0]);
  } finally {
    fs.unlinkSync(control);
  }

  console.log(`ok - ${refs.length} namespace references across ${found.size} namespaces`);
})().catch((err) => {
  console.error(err && err.stack ? err.stack : err);
  process.exit(1);
});
