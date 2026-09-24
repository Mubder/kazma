/**
 * Minimal DOM shim for Node-run chat render tests.
 *
 * The repo runs its JS behaviour tests under bare `node` with no npm tree
 * (see tests/js/test_place_hitl_card.js, which carries its own smaller
 * copy). jsdom would mean a package.json, a lockfile and a network install
 * in CI for code whose DOM surface is a dozen methods wide. This shim
 * implements exactly that surface, so the tests stay hermetic and fast.
 *
 * Implemented: element tree, classList, attributes, textContent,
 * isConnected, and class/tag/attribute selectors for querySelector,
 * querySelectorAll and closest.
 * Deliberately NOT implemented: layout, CSS, events beyond addEventListener
 * capture-and-ignore, innerHTML parsing beyond a plain text sink.
 *
 * Usage:  const { makeDocument } = require("./_dom.js");
 */
"use strict";

function parseSelector(sel) {
  // Supports: ".cls", "tag", "[attr]", "[attr=\"v\"]" and simple conjunctions
  // like ".a.b" or "div.cls". Enough for the render code under test.
  const s = String(sel || "").trim();
  const out = { tag: "", classes: [], attrs: [] };
  const re = /(^[a-zA-Z][\w-]*)|\.([\w-]+)|\[([\w-]+)(?:=["']?([^\]"']*)["']?)?\]/g;
  let m;
  while ((m = re.exec(s)) !== null) {
    if (m[1]) out.tag = m[1].toUpperCase();
    else if (m[2]) out.classes.push(m[2]);
    else if (m[3]) out.attrs.push([m[3], m[4] === undefined ? null : m[4]]);
  }
  return out;
}

function matches(el, parsed) {
  if (el.nodeType !== 1) return false;
  if (parsed.tag && el.tagName !== parsed.tag) return false;
  for (const c of parsed.classes) if (!el.classList.contains(c)) return false;
  for (const [k, v] of parsed.attrs) {
    const got = el.getAttribute(k);
    if (got == null) return false;
    if (v !== null && String(got) !== v) return false;
  }
  return true;
}

function makeDocument() {
  class El {
    constructor(tag) {
      this.tagName = String(tag || "div").toUpperCase();
      this.nodeType = 1;
      this.childNodes = [];
      this.parentNode = null;
      this._attrs = {};
      this._text = "";
      const self = this;
      this.classList = {
        contains(c) { return self._classes().indexOf(c) >= 0; },
        add(...cs) {
          const list = self._classes();
          for (const c of cs) if (list.indexOf(c) < 0) list.push(c);
          self.className = list.join(" ");
        },
        remove(...cs) {
          self.className = self._classes()
            .filter((x) => cs.indexOf(x) < 0).join(" ");
        },
        toggle(c, force) {
          const on = force === undefined ? !this.contains(c) : !!force;
          if (on) this.add(c); else this.remove(c);
          return on;
        },
      };
    }

    _classes() {
      return String(this.className || "").split(/\s+/).filter(Boolean);
    }

    get className() { return this._attrs["class"] || ""; }
    set className(v) { this._attrs["class"] = String(v || ""); }

    setAttribute(k, v) { this._attrs[String(k)] = String(v); }
    getAttribute(k) {
      const v = this._attrs[String(k)];
      return v === undefined ? null : v;
    }
    removeAttribute(k) { delete this._attrs[String(k)]; }
    hasAttribute(k) { return this._attrs[String(k)] !== undefined; }

    get children() { return this.childNodes.filter((n) => n.nodeType === 1); }
    get firstChild() { return this.childNodes[0] || null; }
    get firstElementChild() { return this.children[0] || null; }
    get lastElementChild() {
      const c = this.children;
      return c[c.length - 1] || null;
    }
    get parentElement() { return this.parentNode; }

    _siblingAt(offset, elementsOnly) {
      if (!this.parentNode) return null;
      const list = elementsOnly ? this.parentNode.children : this.parentNode.childNodes;
      const i = list.indexOf(this);
      if (i < 0) return null;
      return list[i + offset] || null;
    }
    get nextSibling() { return this._siblingAt(1, false); }
    get nextElementSibling() { return this._siblingAt(1, true); }
    get previousElementSibling() { return this._siblingAt(-1, true); }

    get isConnected() {
      let n = this;
      while (n.parentNode) n = n.parentNode;
      return n === ROOT;
    }

    appendChild(ch) {
      if (ch.parentNode) ch.parentNode.removeChild(ch);
      ch.parentNode = this;
      this.childNodes.push(ch);
      return ch;
    }
    insertBefore(ch, ref) {
      if (ref == null) return this.appendChild(ch);
      const i = this.childNodes.indexOf(ref);
      if (i < 0) throw new Error("NotFoundError: ref is not a child");
      if (ch.parentNode) ch.parentNode.removeChild(ch);
      ch.parentNode = this;
      this.childNodes.splice(this.childNodes.indexOf(ref), 0, ch);
      return ch;
    }
    removeChild(ch) {
      const i = this.childNodes.indexOf(ch);
      if (i < 0) throw new Error("NotFoundError");
      this.childNodes.splice(i, 1);
      ch.parentNode = null;
      return ch;
    }
    remove() { if (this.parentNode) this.parentNode.removeChild(this); }
    replaceWith(node) {
      if (!this.parentNode) return;
      this.parentNode.insertBefore(node, this);
      this.remove();
    }

    get textContent() {
      if (this.childNodes.length === 0) return this._text;
      return this.children.map((c) => c.textContent).join("");
    }
    set textContent(v) {
      this.childNodes.splice(0).forEach((c) => { c.parentNode = null; });
      this._text = String(v == null ? "" : v);
    }

    // innerHTML is a TEXT SINK here: the render code under test only ever
    // reads it back through textContent, and parsing HTML is not this
    // shim's job. A test that needs structure builds it with createElement.
    get innerHTML() { return this._text; }
    set innerHTML(v) {
      this.childNodes.splice(0).forEach((c) => { c.parentNode = null; });
      this._text = String(v == null ? "" : v);
    }

    querySelectorAll(sel) {
      const parsed = parseSelector(sel);
      const out = [];
      const walk = (n) => {
        for (const c of n.children) {
          if (matches(c, parsed)) out.push(c);
          walk(c);
        }
      };
      walk(this);
      return out;
    }
    querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
    closest(sel) {
      const parsed = parseSelector(sel);
      let n = this;
      while (n && n.nodeType === 1) {
        if (matches(n, parsed)) return n;
        n = n.parentNode;
      }
      return null;
    }
    contains(other) {
      let n = other;
      while (n) { if (n === this) return true; n = n.parentNode; }
      return false;
    }
    // Listeners are kept so a test can fire them with click(); nothing in
    // the shim ever fires one on its own (there is no event loop).
    addEventListener(type, fn) {
      (this._listeners || (this._listeners = {}))[type] =
        ((this._listeners || {})[type] || []).concat([fn]);
    }
    click() {
      const ev = { target: this, stopPropagation() {}, preventDefault() {} };
      for (let n = this; n; n = n.parentNode) {
        for (const fn of ((n._listeners || {}).click || [])) fn.call(n, ev);
      }
    }
    getBoundingClientRect() { return { top: 0, bottom: 0, left: 0, right: 0 }; }
    scrollIntoView() { /* no layout */ }
  }

  const ROOT = new El("html");
  const document = {
    documentElement: ROOT,
    hidden: false,
    createElement: (tag) => new El(tag),
    querySelector: (s) => ROOT.querySelector(s),
    querySelectorAll: (s) => ROOT.querySelectorAll(s),
  };

  /** Build an assistant bubble shaped exactly like appendMessage's output. */
  function assistantBubble(opts) {
    opts = opts || {};
    const wrap = new El("div");
    wrap.className = "message message-assistant";
    if (opts.turnId) wrap.setAttribute("data-turn-id", opts.turnId);
    const content = new El("div");
    content.className = "message-content";
    wrap.appendChild(content);
    if (opts.text !== false) {
      const t = new El("div");
      t.className = "message-text";
      content.appendChild(t);
    }
    const meta = new El("div");
    meta.className = "message-meta";
    content.appendChild(meta);
    const actions = new El("div");
    actions.className = "message-actions";
    content.appendChild(actions);
    return wrap;
  }

  function userBubble() {
    const wrap = new El("div");
    wrap.className = "message message-user";
    const content = new El("div");
    content.className = "message-content";
    wrap.appendChild(content);
    return wrap;
  }

  return { document, El, ROOT, assistantBubble, userBubble };
}

module.exports = { makeDocument, parseSelector };
