/**
 * DOM-order tests for chat.js HITL placement.
 * Extracts _placeHitlCard + host helpers and runs them on a fake DOM.
 *
 * Run: node tests/js/test_place_hitl_card.js
 */
"use strict";

const fs = require("fs");
const path = require("path");

const srcPath = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "chat.js",
);
const src = fs.readFileSync(srcPath, "utf8");

function sliceFn(name, next) {
  const start = src.indexOf("function " + name + "(");
  const end = src.indexOf("function " + next + "(", start + 1);
  if (start < 0 || end < 0) {
    throw new Error("Could not extract " + name + " .. " + next);
  }
  return src.slice(start, end);
}

const chunk = [
  sliceFn("_hitlCardIsTrapped", "_outerAssistantBubble"),
  sliceFn("_outerAssistantBubble", "_hitlHostContent"),
  sliceFn("_hitlHostContent", "hasInlineApprovalCard"),
  sliceFn("_placeHitlCard", "renderHitlCard"),
].join("\n");

function El(tag, className) {
  this.tagName = String(tag || "div").toUpperCase();
  this.nodeType = 1;
  this.className = className || "";
  this.childNodes = [];
  this.parentNode = null;
  this.attrs = {};
  const self = this;
  this.classList = {
    contains: function (c) {
      return (" " + self.className + " ").indexOf(" " + c + " ") >= 0;
    },
    add: function (c) {
      if (!this.contains(c)) self.className = (self.className + " " + c).trim();
    },
    remove: function (c) {
      self.className = self.className.split(/\s+/).filter(function (x) {
        return x && x !== c;
      }).join(" ");
    },
  };
}
Object.defineProperty(El.prototype, "parentElement", {
  get: function () { return this.parentNode; },
});
Object.defineProperty(El.prototype, "children", {
  get: function () {
    return this.childNodes.filter(function (n) { return n.nodeType === 1; });
  },
});
Object.defineProperty(El.prototype, "nextSibling", {
  get: function () {
    if (!this.parentNode) return null;
    const ns = this.parentNode.childNodes;
    const i = ns.indexOf(this);
    return i >= 0 && i + 1 < ns.length ? ns[i + 1] : null;
  },
});
El.prototype.appendChild = function (ch) {
  if (ch.parentNode) ch.parentNode.removeChild(ch);
  ch.parentNode = this;
  this.childNodes.push(ch);
  return ch;
};
El.prototype.removeChild = function (ch) {
  const i = this.childNodes.indexOf(ch);
  if (i < 0) throw new Error("NotFoundError");
  this.childNodes.splice(i, 1);
  ch.parentNode = null;
  return ch;
};
El.prototype.remove = function () {
  if (this.parentNode) this.parentNode.removeChild(this);
};
El.prototype.insertBefore = function (ch, ref) {
  if (!ref) return this.appendChild(ch);
  const i = this.childNodes.indexOf(ref);
  if (i < 0) throw new Error("NotFoundError");
  if (ch.parentNode) ch.parentNode.removeChild(ch);
  ch.parentNode = this;
  this.childNodes.splice(i, 0, ch);
  return ch;
};
El.prototype.getAttribute = function (name) {
  return this.attrs[name] || "";
};
El.prototype.setAttribute = function (name, val) {
  this.attrs[name] = String(val);
};
El.prototype.closest = function (sel) {
  const cls = String(sel || "").replace(/^\./, "").split(".").filter(Boolean);
  let n = this;
  while (n) {
    if (n.classList && cls.every(function (c) { return n.classList.contains(c); })) {
      return n;
    }
    n = n.parentNode;
  }
  return null;
};
El.prototype.querySelectorAll = function (sel) {
  const classes = String(sel || "").match(/\.([a-zA-Z0-9_-]+)/g) || [];
  const want = classes.map(function (c) { return c.slice(1); });
  const out = [];
  function walk(n) {
    (n.childNodes || []).forEach(function (c) {
      if (c.classList && want.every(function (w) { return c.classList.contains(w); })) {
        out.push(c);
      }
      walk(c);
    });
  }
  walk(this);
  return out;
};
El.prototype.querySelector = function (sel) {
  return this.querySelectorAll(sel)[0] || null;
};

function _directChildByClass(parent, cls) {
  if (!parent || !parent.children) return null;
  for (let i = 0; i < parent.children.length; i++) {
    if (parent.children[i].classList && parent.children[i].classList.contains(cls)) {
      return parent.children[i];
    }
  }
  return null;
}
function _bubbleContent(el) {
  if (!el) return null;
  if (el.classList && el.classList.contains("message-content")) return el;
  return _directChildByClass(el, "message-content");
}

let messagesEl = new El("div", "chat-messages");

const api = eval(
  "(function(messagesEl, _bubbleContent){\n"
  + chunk
  + "\nreturn { _placeHitlCard: _placeHitlCard, _hitlCardIsTrapped: _hitlCardIsTrapped,"
  + " _hitlHostContent: _hitlHostContent, _outerAssistantBubble: _outerAssistantBubble };"
  + "\n})",
)(messagesEl, _bubbleContent);

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail ?? "").slice(0, 400));
    fail += 1;
  } else {
    console.log("OK", name);
  }
}

assert("extracted _placeHitlCard", typeof api._placeHitlCard === "function");
assert("extracted _hitlCardIsTrapped", typeof api._hitlCardIsTrapped === "function");

function bubble() {
  // Mutate the same root the extracted helpers closed over.
  while (messagesEl.childNodes.length) {
    messagesEl.removeChild(messagesEl.childNodes[0]);
  }
  const asst = new El("div", "message message-assistant");
  const content = new El("div", "message-content");
  const progress = new El("div", "agent-progress is-active");
  const body = new El("div", "agent-progress-body");
  const text = new El("div", "message-text");
  progress.appendChild(body);
  content.appendChild(progress);
  content.appendChild(text);
  asst.appendChild(content);
  messagesEl.appendChild(asst);
  return { asst: asst, content: content, progress: progress, body: body, text: text };
}

function childNames(el) {
  return el.children.map(function (c) { return c.className; });
}

{
  const t = bubble();
  const card = new El("div", "hitl-approval-card");
  api._placeHitlCard(t.content, card);
  assert(
    "first card is sibling of CoT, not inside it",
    card.parentNode === t.content && !api._hitlCardIsTrapped(card),
    childNames(t.content).join(" | "),
  );
  const names = childNames(t.content);
  assert(
    "first card sits after CoT",
    names.indexOf("agent-progress is-active") === 0
      && names.indexOf("hitl-approval-card") === 1,
    names.join(" | "),
  );
}

{
  const t = bubble();
  const claimed = new El("div", "hitl-approval-card hitl-approved");
  const live = new El("div", "hitl-approval-card");
  api._placeHitlCard(t.content, claimed);
  api._placeHitlCard(t.content, live);
  const names = childNames(t.content);
  const iClaimed = names.indexOf("hitl-approval-card hitl-approved");
  const iLive = names.lastIndexOf("hitl-approval-card");
  assert("chained live card is below claimed", iClaimed >= 0 && iLive > iClaimed, names.join(" | "));
  assert("live card not trapped", !api._hitlCardIsTrapped(live));
}

{
  const t = bubble();
  const trapped = new El("div", "hitl-approval-card");
  t.body.appendChild(trapped);
  assert("setup: card starts trapped", api._hitlCardIsTrapped(trapped));
  api._placeHitlCard(t.content, trapped);
  assert("lifted out of CoT body", !api._hitlCardIsTrapped(trapped), childNames(t.content).join(" | "));
  assert("lifted card parent is message-content", trapped.parentNode === t.content);
}

{
  const t = bubble();
  t.progress.classList.add("is-collapsed");
  const nestedAsst = new El("div", "message message-assistant");
  const nestedContent = new El("div", "message-content");
  nestedAsst.appendChild(nestedContent);
  t.body.appendChild(nestedAsst);
  const card = new El("div", "hitl-approval-card");
  api._placeHitlCard(nestedContent, card);
  assert(
    "nested host still paints on the outer bubble",
    card.parentNode === t.content && !api._hitlCardIsTrapped(card),
    "parent=" + (card.parentNode && card.parentNode.className),
  );
  assert("CoT uncollapsed when placing", !t.progress.classList.contains("is-collapsed"));
}

if (fail) {
  console.error(fail + " failed");
  process.exit(1);
}
console.log("all ok");
