/**
 * Behaviour tests for the keyed turn renderer (modules/turn_view.js).
 *
 * These are the fixtures the old architecture could not have: they replay
 * real event sequences through the real projector and assert the resulting
 * DOM. tests/test_delivery_v2_client.py greps chat.js for forbidden
 * substrings, which locks deleted mechanisms out but cannot detect a paint
 * landing on the wrong node — the actual failure in every "reply never
 * replaced the HITL placeholder" incident.
 *
 * Each INCIDENT block below is a bug that shipped, reproduced as a frame
 * sequence. If the render half regresses, one of them goes red.
 *
 * Run: node tests/js/test_turn_view.js
 */
"use strict";

const fs = require("fs");
const path = require("path");
const { makeDocument } = require("./_dom.js");

const MODULES = path.join(
  __dirname, "..", "..",
  "kazma-ui", "kazma_ui", "static", "js", "modules",
);

global.window = global;
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(MODULES, "turn_document.js"), "utf8"));
// eslint-disable-next-line no-eval
eval(fs.readFileSync(path.join(MODULES, "turn_view.js"), "utf8"));

const TD = global.KazmaTurnDocument;
const TV = global.KazmaTurnView;

let fail = 0;
function assert(name, cond, detail) {
  if (!cond) {
    console.error("FAIL", name, String(detail === undefined ? "" : detail).slice(0, 400));
    fail++;
  } else {
    console.log("OK", name);
  }
}

/** Class names of a bubble's content children, in DOM order.
 *
 *  The approvals region is summarised by its ROWS, in DOM order, because
 *  that is what the layout rules are about now: one region, rows keyed by
 *  gate, never moving the answer. */
function shape(bubble) {
  const content = bubble.querySelector(".message-content");
  return content.children.map((c) => {
    const cls = c.className.split(/\s+/);
    if (cls.indexOf("message-text") >= 0) return "text";
    if (cls.indexOf("turn-header") >= 0) return "header";
    if (cls.indexOf("agent-progress") >= 0) return "workbench";
    if (cls.indexOf("turn-approvals") >= 0) return "approvals(" + rows(bubble).join(",") + ")";
    if (cls.indexOf("hitl-approval-card") >= 0) {
      return "loose(" + (c.getAttribute("data-interrupt-id") || "") + ":" +
        (c.getAttribute("data-state") || "") + ")";
    }
    if (cls.indexOf("message-meta") >= 0) return "meta";
    if (cls.indexOf("message-actions") >= 0) return "actions";
    return c.className;
  });
}

/** The approval rows on screen, in DOM order, as "<gate>:<state>". */
function rows(bubble) {
  const group = bubble.querySelector(".turn-approvals");
  if (!group) return [];
  const host = group.querySelector(".turn-approvals-rows") || group;
  return host.children.map((c) =>
    (c.getAttribute("data-interrupt-id") || "") + ":" +
    (c.getAttribute("data-state") || ""));
}

/** The one approvals region, or null. */
function group(bubble) {
  return bubble.querySelector(".turn-approvals");
}

/**
 * Renderers shaped like the ones chat.js supplies: they build the same
 * class names, and the text slot records what it painted in data-md exactly
 * as the real painter does (that attribute is what the invariant reads).
 */
function makeRenderers(env, opts) {
  opts = opts || {};
  return {
    has(kind, doc) {
      if (kind === "text") return !!TD.textOf(doc.parts) && !opts.refuseText;
      if (kind === "workbench") return TD.activityOf(doc.parts).length > 0;
      return true;
    },
    build(entry) {
      const el = env.document.createElement("div");
      if (entry.kind === "text") el.className = "message-text";
      else if (entry.kind === "header") el.className = "turn-header";
      else if (entry.kind === "workbench") el.className = "agent-progress";
      else if (entry.kind === "approvals") {
        // Shaped like chat.js:_buildApprovalGroup — a region with a rows
        // host, because the rows are what the layout rules are about.
        el.className = "turn-approvals";
        const host = env.document.createElement("div");
        host.className = "turn-approvals-rows";
        el.appendChild(host);
      } else if (entry.kind === "hitl") {
        el.className = "hitl-approval-card";
        el.setAttribute("data-interrupt-id", TD.interruptIdOf(entry.part));
      }
      env.built.push(entry.key);
      return el;
    },
    paint(entry, el, ctx) {
      if (entry.kind === "text") {
        const t = TD.textOf(ctx.doc.parts);
        el.setAttribute("data-md", t);
        el.textContent = t;
      } else if (entry.kind === "approvals") {
        // Keyed rows, mirroring chat.js:_paintApprovalGroup: one row per
        // gate, created once, repainted in place, ask order. A row the
        // plan stops mentioning is KEPT (contract 4).
        const host = el.querySelector(".turn-approvals-rows");
        const byKey = el.__rows || (el.__rows = {});
        const ordered = [];
        for (const row of entry.rows || []) {
          let node = byKey[row.key];
          // Mirrors chat.js:_paintApprovalGroup — a row frozen in the
          // hydration 'awaiting' posture cannot have its live buttons
          // painted back, so it is torn out and rebuilt once the
          // registry says the gate is live. Inside the region, so the
          // answer does not move.
          if (node && String(row.state || "") === "pending") {
            const shown = String(node.getAttribute("data-hitl-shown") || "");
            if (shown && shown !== "pending") {
              if (node.parentNode) node.parentNode.removeChild(node);
              delete byKey[row.key];
              node = null;
            }
          }
          if (!node) {
            node = env.document.createElement("div");
            node.className = "hitl-approval-card";
            node.setAttribute("data-interrupt-id", TD.interruptIdOf(row.part));
            node.setAttribute("data-gate-key", row.key);
            byKey[row.key] = node;
            env.built.push(row.key);
          }
          // From row.state — the value TurnView resolved. Reading
          // row.part.state here is the bug the contract exists to stop.
          node.setAttribute("data-state", String(row.state || row.part.state || "pending"));
          env.painted.push(row.key);
          ordered.push(node);
        }
        for (const kid of host.children) {
          if (ordered.indexOf(kid) < 0) ordered.push(kid);
        }
        let cursor = null;
        for (const node of ordered) {
          const want = cursor ? cursor.nextElementSibling : host.firstElementChild;
          if (node !== want) host.insertBefore(node, want || null);
          cursor = node;
        }
      } else if (entry.kind === "hitl") {
        el.setAttribute("data-state", String(entry.state || entry.part.state || "pending"));
      } else if (entry.kind === "workbench") {
        el.textContent = TD.activityOf(ctx.doc.parts).length + " steps";
      }
      env.painted.push(entry.key);
    },
    discard: opts.discard || function () { return false; },
  };
}

function newEnv(viewOpts) {
  const env = makeDocument();
  env.built = [];
  env.painted = [];
  env.invariants = [];
  env.view = TV.create(Object.assign({
    document: env.document,
    turnDocument: TD,
    onInvariant: (i) => env.invariants.push(i),
  }, viewOpts || {}));
  return env;
}

function feed(events, turnId) {
  let doc = TD.empty(turnId || "t1");
  for (const ev of events) doc = TD.applyEvent(doc, ev);
  return doc;
}

// ══════════════════════════════════════════════════════════
// 1. Basics
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([{ type: "token", content: "hello world" }]);
  const r = env.view.render(bubble, doc, makeRenderers(env));
  // The header is slot 0 of every turn now (UNIFIED_TURN_BLOCK.md §3),
  // so "the answer is first" became "the answer follows the header".
  assert("header paints first", shape(bubble)[0] === "header", shape(bubble));
  assert("text paints", shape(bubble)[1] === "text", shape(bubble));
  assert("chrome stays last",
    shape(bubble).slice(-2).join(",") === "meta,actions", shape(bubble));
  assert("no invariant on a healthy paint", env.invariants.length === 0, env.invariants);
  assert("report names the plan", r.plan.join(",") === "header,text", r.plan);
}

{
  // The adopted .message-text from appendMessage is REUSED, never duplicated.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const original = bubble.querySelector(".message-text");
  const doc = feed([{ type: "token", content: "hi" }]);
  env.view.render(bubble, doc, makeRenderers(env));
  const content = bubble.querySelector(".message-content");
  assert("adopts the existing text node",
    content.querySelectorAll(".message-text").length === 1);
  assert("adoption reuses the same element",
    content.querySelector(".message-text") === original);
  assert("adopted slot was never built", env.built.indexOf("text") < 0, env.built);
}

// ══════════════════════════════════════════════════════════
// 2. Idempotence — contract: same doc twice = zero mutations
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([
    { type: "token", content: "working" },
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "shell_exec",
      payload: { tool: "shell_exec", interrupt_id: "g1" } },
  ]);
  const rend = makeRenderers(env);
  const first = env.view.render(bubble, doc, rend);
  const shapeAfterFirst = shape(bubble).join(",");
  const second = env.view.render(bubble, doc, rend);
  assert("second render moves nothing", second.moves === 0,
    "moves=" + second.moves + " first=" + first.moves);
  assert("second render changes no shape", shape(bubble).join(",") === shapeAfterFirst);
  const third = env.view.render(bubble, doc, rend);
  assert("render is stable under repetition", third.moves === 0);
}

// ══════════════════════════════════════════════════════════
// 3. INCIDENT 2026-09-04 — a pending gate follows the text it provoked
//    (_placeHitlCard's compareDocumentPosition rule, now declarative)
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([
    { type: "token", content: "I'll install that skill. Fetching the docs" },
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "web_fetch",
      payload: { tool: "web_fetch", interrupt_id: "g1" } },
  ]);
  env.view.render(bubble, doc, makeRenderers(env));
  const s = shape(bubble);
  // The rule this locked was "the question follows the content that
  // provoked it", encoded as POSITION relative to the answer. Position
  // now encodes nothing: the region is above the answer for the whole
  // turn, so deciding a gate no longer moves the reply across it
  // (UNIFIED_TURN_BLOCK.md §3). What still has to hold is that the
  // pending request is on screen and actionable.
  assert("the pending gate is a row in the one region",
    rows(bubble).join(",") === "g1:pending", rows(bubble));
  assert("the region sits above the answer",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
  assert("turn is paused", doc.status === "paused", doc.status);
}

// ══════════════════════════════════════════════════════════
// 4. INCIDENT 2026-09-01 — a settled gate sits ABOVE the answer
//    (_parkClaimedHitlCard's node move, now declarative)
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  let doc = feed([
    { type: "token", content: "checking" },
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "web_fetch",
      payload: { tool: "web_fetch", interrupt_id: "g1" } },
  ]);
  const rend = makeRenderers(env);
  env.view.render(bubble, doc, rend);
  doc = TD.applyEvent(doc, { type: "hitl", state: "approved", interrupt_id: "g1", tool: "web_fetch" });
  doc = TD.applyEvent(doc, { type: "done", content: "Installed the skill from the docs URL." });
  env.view.render(bubble, doc, rend);
  const s = shape(bubble);
  assert("settled gate moves above the answer",
    s.indexOf("card(g1:approved)") < s.indexOf("text"), s);
  assert("the answer is on screen",
    bubble.querySelector(".message-text").getAttribute("data-md")
      === "Installed the skill from the docs URL.");
  assert("no silence reported", env.invariants.length === 0, env.invariants);
}

// ══════════════════════════════════════════════════════════
// 5. INCIDENT 2026-09-19 — SEQUENTIAL APPROVE THEN SILENCE
//    "Skill install from a docs URL approved twice then went silent: the
//     server finished (1815 chars) but the bubble kept the HITL placeholder
//     until refresh."  This is the fixture that whole class reduces to.
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  let doc = feed([{ type: "token", content: "Installing the skill" }]);

  // Gate one: asked, then approved.
  doc = TD.applyEvent(doc, { type: "hitl", state: "pending", interrupt_id: "g1",
    tool: "web_fetch", payload: { tool: "web_fetch", interrupt_id: "g1" } });
  env.view.render(bubble, doc, rend);
  doc = TD.applyEvent(doc, { type: "hitl", state: "approved", interrupt_id: "g1", tool: "web_fetch" });
  env.view.render(bubble, doc, rend);

  // Gate two: a DIFFERENT tool in the SAME turn.
  doc = TD.applyEvent(doc, { type: "hitl", state: "pending", interrupt_id: "g2",
    tool: "file_write", payload: { tool: "file_write", interrupt_id: "g2" } });
  env.view.render(bubble, doc, rend);
  let s = shape(bubble);
  assert("gate one survives gate two",
    rows(bubble).indexOf("g1:approved") >= 0, rows(bubble));
  assert("both gates are on screen",
    rows(bubble).indexOf("g2:pending") >= 0, rows(bubble));
  // Ask order, not state order. Sorting by state is what made a decision
  // reshuffle the transcript.
  assert("rows are in ask order",
    rows(bubble).join(",") === "g1:approved,g2:pending", rows(bubble));
  assert("and they share ONE region",
    bubble.querySelectorAll(".turn-approvals").length === 1);
  assert("still paused on gate two", doc.status === "paused", doc.status);

  // Approve gate two; the server finishes the turn.
  doc = TD.applyEvent(doc, { type: "hitl", state: "approved", interrupt_id: "g2", tool: "file_write" });
  doc = TD.applyEvent(doc, { type: "done", content: "Skill installed: 1815 chars written." });
  env.view.render(bubble, doc, rend);
  s = shape(bubble);
  assert("THE REPLY PAINTS AFTER A SEQUENTIAL APPROVE",
    bubble.querySelector(".message-text").getAttribute("data-md")
      === "Skill installed: 1815 chars written.",
    bubble.querySelector(".message-text").getAttribute("data-md"));
  assert("both decisions stay in the transcript",
    rows(bubble).join(",") === "g1:approved,g2:approved", rows(bubble));
  assert("answer sits below both decisions",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
  // THE thing the group is for: the answer did not move when either gate
  // was decided.
  assert("the answer never changed container",
    s.indexOf("text") === s.length - 3, s);
  assert("no invariant raised on the sequential path",
    env.invariants.length === 0, env.invariants);
}

// ══════════════════════════════════════════════════════════
// 6. INCIDENT 2026-09-02 — a card cannot trap the answer
//    Every slot is a FLAT sibling of .message-content, so there is no
//    container for the text to be nested inside. _rescueTurnDom's job.
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([
    { type: "progress", step: { kind: "tool", title: "web_fetch", state: "done" } },
    { type: "hitl", state: "approved", interrupt_id: "g1", tool: "web_fetch" },
    { type: "done", content: "Done." },
  ]);
  env.view.render(bubble, doc, makeRenderers(env));
  const content = bubble.querySelector(".message-content");
  const slotNodes = content.querySelectorAll("[data-slot-key]");
  assert("every slot is a direct child of .message-content",
    slotNodes.every((n) => n.parentNode === content),
    slotNodes.map((n) => n.getAttribute("data-slot-key") + "->" + n.parentNode.className));
  // "First" now means "first of the CONTENT slots": the header is slot 0
  // of every turn (UNIFIED_TURN_BLOCK.md §3). What this line protects is
  // that activity precedes the answer, which is unchanged.
  assert("workbench renders before the answer",
    shape(bubble).indexOf("workbench") < shape(bubble).indexOf("text")
      && shape(bubble)[1] === "workbench", shape(bubble));
  assert("no text node is nested in the workbench",
    content.querySelector(".agent-progress").querySelectorAll(".message-text").length === 0);
}

// ══════════════════════════════════════════════════════════
// 6b. Thoughts survive done — they are a workbench fold, not the answer
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  let doc = TD.empty("t1");
  doc = TD.applyEvent(doc, {
    type: "progress",
    step: { kind: "thought", title: "Thoughts", detail: "secret working notes" },
  });
  doc = TD.applyEvent(doc, { type: "done", content: "The public answer." });
  env.view.render(bubble, doc, makeRenderers(env));
  const s = shape(bubble);
  assert("workbench still there after done", s.indexOf("workbench") >= 0, s);
  assert("answer is not the thought",
    bubble.querySelector(".message-text").getAttribute("data-md") === "The public answer.",
    bubble.querySelector(".message-text") &&
      bubble.querySelector(".message-text").getAttribute("data-md"));
  const thoughts = TD.activityOf(doc.parts).filter((r) => r.kind === "thought");
  assert("thoughts still in the document",
    thoughts.length === 1 && thoughts[0].detail.indexOf("secret working notes") >= 0,
    JSON.stringify(thoughts));
}

// ══════════════════════════════════════════════════════════
// 7. Silence is LOUD — the invariant that replaces the operator
// ══════════════════════════════════════════════════════════

{
  // Shape A — "the bubble kept the placeholder": the host element is right
  // there on screen, and empty. This is what every past incident LOOKED
  // like, and what nothing in the old architecture could detect.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([{ type: "done", content: "the answer the server produced" }]);
  env.view.render(bubble, doc, makeRenderers(env, { refuseText: true }));
  assert("an answer in the document and a blank bubble reports itself",
    env.invariants.length === 1 && env.invariants[0].code === "text-blank",
    JSON.stringify(env.invariants));
  assert("the report carries enough to debug it",
    env.invariants[0].wantLen === "the answer the server produced".length &&
    env.invariants[0].turnId === "t1" &&
    env.invariants[0].status === "done",
    JSON.stringify(env.invariants[0]));
}

{
  // Shape B — no host at all. Same meaning, different diagnosis.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1", text: false });
  env.ROOT.appendChild(bubble);
  const doc = feed([{ type: "done", content: "an answer with nowhere to go" }]);
  env.view.render(bubble, doc, makeRenderers(env, { refuseText: true }));
  assert("an answer with no host reports itself",
    env.invariants.length === 1 && env.invariants[0].code === "text-missing",
    JSON.stringify(env.invariants));
}

{
  // And the healthy path stays quiet — an invariant that cries wolf gets
  // muted, and then it is worth nothing.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  for (const doc of [
    feed([{ type: "token", content: "partial" }]),
    feed([{ type: "token", content: "partial" }, { type: "done", content: "whole" }]),
    feed([{ type: "hitl", state: "pending", interrupt_id: "g", tool: "t",
      payload: { interrupt_id: "g" } }]),
    TD.empty("t1"),
  ]) {
    env.view.render(bubble, doc, rend);
  }
  assert("healthy renders raise nothing", env.invariants.length === 0,
    JSON.stringify(env.invariants));
}

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const doc = feed([
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "shell_exec",
      payload: { tool: "shell_exec", interrupt_id: "g1" } },
  ]);
  // A renderer that cannot build the card must not fail quietly either.
  env.view.render(bubble, doc, {
    has: () => true,
    build: (e) => (e.kind === "hitl" ? null : env.document.createElement("div")),
    paint: () => {},
  });
  assert("a gate with no card reports itself",
    env.invariants.length === 1 && env.invariants[0].code === "gate-missing",
    JSON.stringify(env.invariants));
}

// ══════════════════════════════════════════════════════════
// 8. Ambiguity never deletes (contract 4)
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  let doc = feed([
    { type: "token", content: "one" },
    { type: "hitl", state: "approved", interrupt_id: "g1", tool: "t" },
  ]);
  env.view.render(bubble, doc, rend);
  assert("row present before the drop",
    rows(bubble).indexOf("g1:approved") >= 0, rows(bubble));

  // A frame that loses the gate (truncated resync, partial hydrate) must
  // NOT take the decision off screen: removing is the silent failure.
  const thin = TD.empty("t1");
  const thinDoc = TD.applyEvent(thin, { type: "done", content: "one" });
  env.view.render(bubble, thinDoc, rend);
  // The region keeps a row the plan stopped mentioning, for the same
  // reason the slot table kept a card: removing is the silent failure.
  assert("a vanished part is KEPT, not deleted",
    rows(bubble).indexOf("g1:approved") >= 0, rows(bubble));

  // ...unless the caller says so explicitly.
  const env2 = newEnv();
  const b2 = env2.assistantBubble({ turnId: "t1" });
  env2.ROOT.appendChild(b2);
  const r2 = makeRenderers(env2, { discard: (key) => key.indexOf("hitl:") === 0 });
  env2.view.render(b2, doc, r2);
  env2.view.render(b2, thinDoc, r2);
  assert("an explicit discard does remove it",
    shape(b2).indexOf("card(g1:approved)") < 0, shape(b2));
}

// ══════════════════════════════════════════════════════════
// 9. INCIDENT 2026-09-03 — the 'live' placeholder is not an identity
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const b1 = env.assistantBubble({});
  const b2 = env.assistantBubble({});
  env.ROOT.appendChild(b1);
  env.ROOT.appendChild(b2);

  env.view.bind("live", b1);
  assert("registry finds the open turn", env.view.elFor("live") === b1);
  env.view.promote("live", "turn-42");
  assert("promotion is a rename", env.view.elFor("turn-42") === b1);
  assert("the placeholder is released", env.view.elFor("live") === null);

  // The NEXT turn opens under the same placeholder and must get its OWN
  // bubble — the old one is no longer reachable by that key.
  env.view.bind("live", b2);
  assert("a new turn's placeholder does not resolve to the old bubble",
    env.view.elFor("live") === b2);
  assert("the promoted turn keeps its own bubble",
    env.view.elFor("turn-42") === b1);

  // Two turns, two documents, no crossing.
  const rend = makeRenderers(env);
  env.view.render(b1, feed([{ type: "done", content: "first answer" }], "turn-42"), rend);
  env.view.render(b2, feed([{ type: "done", content: "second answer" }], "live"), rend);
  assert("turn one keeps its own answer",
    b1.querySelector(".message-text").getAttribute("data-md") === "first answer");
  assert("turn two keeps its own answer",
    b2.querySelector(".message-text").getAttribute("data-md") === "second answer");
}

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  env.view.bind("t1", bubble);
  bubble.remove();
  assert("a detached bubble is never painted into", env.view.elFor("t1") === null);
}

// ══════════════════════════════════════════════════════════
// 10. Order survives out-of-order arrival (replay / resync)
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  // Terminal frame first (a resync that beat the live stream), then the
  // gates replay behind it. The final shape must still read correctly.
  let doc = feed([{ type: "done", content: "final" }]);
  env.view.render(bubble, doc, rend);
  doc = TD.applyEvent(doc, { type: "hitl", state: "approved", interrupt_id: "g1", tool: "a" });
  doc = TD.applyEvent(doc, { type: "hitl", state: "approved", interrupt_id: "g2", tool: "b" });
  env.view.render(bubble, doc, rend);
  const s = shape(bubble);
  assert("replayed gates land above the answer",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
  assert("replayed gates keep ask order",
    rows(bubble).join(",") === "g1:approved,g2:approved", rows(bubble));
  assert("answer still painted after replay",
    bubble.querySelector(".message-text").getAttribute("data-md") === "final");
  const again = env.view.render(bubble, doc, rend);
  assert("post-replay render is idempotent", again.moves === 0, again.moves);
}

// ══════════════════════════════════════════════════════════
// 11. INCIDENT 2026-09-03 — nothing auto-expands the workbench
//     The user's chevron is the only opener. Reordering a card used to be
//     done by expanding the panel that held it, which pushed the approval
//     below the fold while the reader was scrolled elsewhere.
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const content = bubble.querySelector(".message-content");
  const panel = env.document.createElement("div");
  panel.className = "agent-progress is-done is-collapsed";
  content.insertBefore(panel, content.firstElementChild);

  const doc = feed([
    { type: "progress", step: { kind: "tool", title: "web_fetch", state: "done" } },
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "web_fetch",
      payload: { interrupt_id: "g1" } },
    { type: "token", content: "looking" },
  ]);
  env.view.render(bubble, doc, makeRenderers(env));
  assert("a collapsed workbench stays collapsed",
    panel.classList.contains("is-collapsed"), panel.className);
  assert("the stray panel is ADOPTED, not duplicated",
    content.querySelectorAll(".agent-progress").length === 1,
    content.querySelectorAll(".agent-progress").length);
  assert("the adopted panel is the workbench slot",
    env.view.slot(bubble, "workbench") === panel);
}

{
  // ensureProgressPanel and friends may still insert a panel mid-turn.
  // The reconciler must converge on whatever DOM it is handed rather than
  // minting a second one beside it.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  const doc = feed([
    { type: "progress", step: { kind: "tool", title: "t", state: "done" } },
    { type: "done", content: "answer" },
  ]);
  env.view.render(bubble, doc, rend);
  const content = bubble.querySelector(".message-content");
  const intruder = env.document.createElement("div");
  intruder.className = "agent-progress";
  content.appendChild(intruder);      // a legacy writer barges in
  env.view.render(bubble, doc, rend);
  assert("a late foreign panel does not become a second workbench",
    content.querySelectorAll(".agent-progress").length <= 2,
    content.querySelectorAll(".agent-progress").length);
  assert("the answer survives a foreign insert",
    bubble.querySelector(".message-text").getAttribute("data-md") === "answer");
  assert("chrome is still last after a foreign insert",
    shape(bubble).slice(-2).join(",") === "meta,actions", shape(bubble));
}

// ══════════════════════════════════════════════════════════
// 12. INCIDENT 2026-09-19 (live install) — ONE answer per gate
//
//     "Approved — running…" sitting BELOW the finished reply, and after a
//     refresh "Waiting for approval" below a delete that had already run.
//
//     Ordering read part.state; the painter read the host's display
//     resolver. A part still stamped `pending` whose decision the registry
//     had claimed was therefore sorted as pending (after the answer) and
//     painted as approved. Two sources of truth for one fact — the same
//     defect this module exists to remove, one level in.
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);

  // The document still says pending; the host resolves it to inflight
  // (the registry says the decision was claimed).
  const rend = makeRenderers(env);
  rend.gateState = (p) => (p.interrupt_id === "g1" ? "inflight" : String(p.state || "pending"));
  const basePaint = rend.paint;
  rend.paint = (entry, el, ctx) => {
    if (entry.kind === "text") {
      const t = TD.textOf(ctx.doc.parts);
      el.setAttribute("data-md", t); el.textContent = t;
      return;
    }
    // The rows carry entry.state; the group painter stamps it. Labelling
    // from row.part.state instead is the bug this block exists to catch,
    // and the shared painter is where that would now happen.
    basePaint(entry, el, ctx);
  };

  const doc = feed([
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "file_delete",
      payload: { interrupt_id: "g1" } },
    { type: "done", content: "Longer run complete." },
  ]);
  env.view.render(bubble, doc, rend);

  const s = shape(bubble);
  // Position no longer encodes state — the region is above the answer
  // whatever the gates are doing — so what this locks is that the LABEL
  // comes from the resolver and not from the part's own stamp.
  assert("the row is labelled from the resolver, not the part stamp",
    rows(bubble).join(",") === "g1:inflight", rows(bubble));
  assert("and it is above the answer, as always",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
  assert("nothing is sorted as pending while painted as approved",
    !s.some(x => x.includes(":pending")), s);
}

{
  // And the resolver is honoured for the pending direction too: a part the
  // host says is still live sorts below the text even if its own stamp
  // claims otherwise. Ordering follows the resolver, always.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  rend.gateState = () => "pending";
  const doc = feed([
    { type: "hitl", state: "approved", interrupt_id: "g1", tool: "t" },
    { type: "done", content: "answer" },
  ]);
  env.view.render(bubble, doc, rend);
  const s = shape(bubble);
  assert("the resolver decides the label, not the raw part stamp",
    rows(bubble).join(",") === "g1:pending", rows(bubble));
  assert("...and the region stays above the answer either way",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
}

{
  // slotPlan carries the resolved state on the entry, so a painter cannot
  // disagree with the ordering even by accident.
  const plan = TV.slotPlan(
    { parts: [{ type: "hitl", interrupt_id: "a", state: "pending" }] },
    { text: true }, TD,
    () => "approved",
  );
  const region = plan.find(e => e.kind === "approvals");
  const gate = region && region.rows[0];
  assert("the row carries the resolved state", gate && gate.state === "approved");
  assert("and the region sits above the answer",
    plan.map(e => e.key).join(",") === "approvals,text", plan.map(e => e.key));
  // No resolver → falls back to the part's own stamp for the LABEL. The
  // position is the same either way, which is the point: a decision
  // changes what a row says, never where the answer is.
  const bare = TV.slotPlan(
    { parts: [{ type: "hitl", interrupt_id: "a", state: "pending" }] },
    { text: true }, TD,
  );
  assert("without a resolver it falls back to part.state",
    bare.find(e => e.kind === "approvals").rows[0].state === "pending",
    JSON.stringify(bare.map(e => e.key)));
  assert("and the layout is unchanged by which way it resolved",
    bare.map(e => e.key).join(",") === "approvals,text", bare.map(e => e.key));
  const omitted = TV.slotPlan(
    { parts: [
      { type: "hitl", interrupt_id: "ghost", state: "pending" },
      { type: "text", text: "x" },
    ] },
    { text: true }, TD,
    () => null,
  );
  assert("a null view omits the gate (honest empty)",
    omitted.map((p) => p.key).join(",") === "text", omitted.map((p) => p.key));
  const claimed = TV.slotPlan(
    { parts: [
      { type: "hitl", interrupt_id: "done", state: "approved" },
      { type: "text", text: "x" },
    ] },
    { text: true }, TD,
    () => null,
  );
  assert("a claimed gate with no view still gets a row",
    claimed.map((p) => p.key).join(",") === "approvals,text",
    claimed.map((p) => p.key));
  assert("...and the row keeps the stamp as its label",
    claimed.find((e) => e.kind === "approvals").rows[0].state === "approved");
}

{
  // Hydration paints a pending part as 'awaiting' (disabled, sorted as
  // settled) because the registry has not answered yet. Paint-in-place
  // cannot put the buttons back — awaiting replaced the actions HTML.
  // When the registry later says pending, the frozen row must be torn
  // out so a live card is minted in its place. Without this, a refresh
  // on a live pause leaves "Waiting for approval…" with no buttons
  // forever. It happens INSIDE the approvals region now, so the answer
  // stays where it was — the old rule re-sorted the card below the text
  // and moved the reply the moment the registry answered.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  // No slot-level rebuild any more: the region is stable for the life of
  // the turn and the rebuild happens per ROW, inside it.
  const origPaint = rend.paint;
  rend.paint = (entry, el, ctx) => {
    origPaint(entry, el, ctx);
    // The hydration posture is stamped on the ROW now, which is where
    // chat.js:_paintApprovalGroup reads it to decide a rebuild.
    if (entry.kind === "approvals") {
      const host = el.querySelector(".turn-approvals-rows");
      for (const row of entry.rows || []) {
        const node = host.querySelector('[data-gate-key="' + row.key + '"]');
        if (node) node.setAttribute("data-hitl-shown", row.state);
      }
    }
  };
  rend.gateState = () => "awaiting";
  const doc = feed([
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "file_write",
      payload: { interrupt_id: "g1" } },
    { type: "done", content: "working…" },
  ]);
  env.view.render(bubble, doc, rend);
  let s = shape(bubble);
  assert("a hydrated gate is a row, above the answer",
    rows(bubble).join(",") === "g1:awaiting", rows(bubble));
  assert("...and the answer is below the region",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);

  env.built = [];
  rend.gateState = () => "pending";
  env.view.render(bubble, doc, rend);
  s = shape(bubble);
  // The rebuild is still required — a card frozen in the awaiting
  // posture cannot have its live buttons painted back — but it happens
  // inside the region, so the row is rebuilt and the ANSWER does not
  // move. That is the whole difference: the old rule tore the card out
  // and re-sorted it below the text, which relocated the reply the
  // moment the registry answered.
  assert("a live registry row rebuilds the frozen row",
    env.built.indexOf("hitl:g1") >= 0, env.built);
  assert("the rebuilt row is live", rows(bubble).join(",") === "g1:pending",
    rows(bubble));
  assert("and the answer never moved",
    s.findIndex((x) => x.indexOf("approvals(") === 0) < s.indexOf("text"), s);
}

{
  // Rebuild is consent, not a second deleter. A settled freeze
  // (awaiting → approved) must keep the same node — contract 4.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  rend.rebuild = (entry, el) => {
    if (entry.kind !== "hitl" || String(entry.state || "") !== "pending") return false;
    const shown = String(el.getAttribute("data-hitl-shown") || "");
    return !!(shown && shown !== "pending");
  };
  const origPaint = rend.paint;
  rend.paint = (entry, el, ctx) => {
    origPaint(entry, el, ctx);
    if (entry.kind === "hitl") el.setAttribute("data-hitl-shown", entry.state);
  };
  rend.gateState = () => "awaiting";
  const doc = feed([
    { type: "hitl", state: "pending", interrupt_id: "g1", payload: { interrupt_id: "g1" } },
    { type: "done", content: "done" },
  ]);
  env.view.render(bubble, doc, rend);
  env.built = [];
  rend.gateState = () => "approved";
  env.view.render(bubble, doc, rend);
  assert("settling an awaiting card does not rebuild",
    env.built.indexOf("hitl:g1") < 0, env.built);
  const s = shape(bubble);
  assert("the settled card stays above the answer",
    s.indexOf("card(g1:approved)") < s.indexOf("text"), s);
}

// ══════════════════════════════════════════════════════════
// 13. INCIDENT 2026-09-01 — the You bubble is never a paint target
// ══════════════════════════════════════════════════════════

{
  const env = newEnv();
  const user = env.userBubble();
  env.ROOT.appendChild(user);
  const before = user.querySelector(".message-content").children.length;
  const r = env.view.render(user, feed([{ type: "done", content: "x" }]),
    makeRenderers(env));
  assert("a user bubble is refused", r.painted === false && r.reason === "user-bubble");
  assert("the user's row is untouched",
    user.querySelector(".message-content").children.length === before);
}

// ══════════════════════════════════════════════════════════
// 13. slotPlan is pure and total
// ══════════════════════════════════════════════════════════

{
  const plan = TV.slotPlan(
    { parts: [
      { type: "hitl", interrupt_id: "a", state: "approved" },
      { type: "text", text: "x" },
      { type: "hitl", interrupt_id: "b", state: "pending" },
    ] },
    { workbench: true, text: true }, TD,
  );
  assert("plan order is workbench, approvals, text",
    plan.map((p) => p.key).join(",") === "workbench,approvals,text",
    plan.map((p) => p.key));
  assert("...with both gates as rows in ask order",
    plan.find((e) => e.kind === "approvals").rows.map((r) => r.key).join(",")
      === "hitl:a,hitl:b");
  assert("empty doc plans nothing",
    TV.slotPlan({}, {}, TD).length === 0);
  assert("plan is a pure function of the doc",
    JSON.stringify(TV.slotPlan({ parts: [] }, { text: true }, TD).map((p) => p.key))
      === JSON.stringify(["text"]));

  // ── The turn header (UNIFIED_TURN_BLOCK.md §3) ──────────────────────
  // "Exists from the first acknowledged turn state, including before the
  // first token." A header that only appears once there is content is
  // precisely the gap #live-task-card was invented to fill, and filling it
  // from outside the turn is what gave one turn two status surfaces.
  assert("the header is first",
    TV.slotPlan(
      { parts: [
        { type: "hitl", interrupt_id: "a", state: "approved" },
        { type: "text", text: "x" },
      ] },
      { header: true, workbench: true, text: true }, TD,
    ).map((p) => p.key).join(",") === "header,workbench,approvals,text");

  assert("a turn with nothing in it still has a header",
    TV.slotPlan({ parts: [] }, { header: true }, TD)
      .map((p) => p.key).join(",") === "header");

  assert("...and one with no header asked for has none",
    TV.slotPlan({ parts: [{ type: "text", text: "x" }] }, { text: true }, TD)
      .filter((p) => p.kind === "header").length === 0);

  // Exactly one, no matter how many times it is planned (invariant U02).
  const twice = TV.slotPlan(
    { parts: [{ type: "text", text: "x" }] },
    { header: true, text: true }, TD,
  );
  assert("exactly one header per turn",
    twice.filter((p) => p.kind === "header").length === 1);
}

// ── orphaned-gate: a card the document has stopped tracking ──
//
// The 2026-09-20 report. The turn id changed mid-turn, the client
// minted a NEW document, and gates decided under the old id vanished
// from doc.parts. Their cards stayed on screen: not repositioned (not
// in the plan), not repainted (paint runs for planned slots only), not
// removed (contract 4). The painter pushed them below the tracked rows
// and the reader saw an early approval sitting under later ones.
//
// Every other invariant passed throughout, because each was true of
// the gates the document still knew about. Nothing walked DOM -> doc.
{
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);

  const both = feed([
    { type: "hitl", state: "approved", interrupt_id: "g1", tool: "a",
      payload: { interrupt_id: "g1" } },
    { type: "hitl", state: "pending", interrupt_id: "g2", tool: "b",
      payload: { interrupt_id: "g2" } },
  ]);
  env.view.render(bubble, both, rend);
  env.invariants.length = 0;

  // ...then a document that has LOST g1 — a new turn id, in the field.
  const lost = feed([
    { type: "hitl", state: "pending", interrupt_id: "g2", tool: "b",
      payload: { interrupt_id: "g2" } },
  ]);
  env.view.render(bubble, lost, rend);

  const codes = env.invariants.map((r) => r.code);
  assert("a card the document dropped is reported",
    codes.indexOf("orphaned-gate") >= 0, JSON.stringify(env.invariants));
  const rep = env.invariants.filter((r) => r.code === "orphaned-gate")[0];
  assert("...and it names the gate",
    !!rep && String(rep.issues.join(",")).indexOf("g1") >= 0,
    JSON.stringify(rep));
}

{
  // ...but a document that tracks NO gates is ambiguity, not an orphan:
  // a fresh doc before hydration, or a truncated snapshot. Contract 4
  // protects it, and an invariant that fires there gets muted.
  const env = newEnv();
  const bubble = env.assistantBubble({ turnId: "t1" });
  env.ROOT.appendChild(bubble);
  const rend = makeRenderers(env);
  env.view.render(bubble, feed([
    { type: "hitl", state: "pending", interrupt_id: "g9", tool: "t",
      payload: { interrupt_id: "g9" } },
  ]), rend);
  env.invariants.length = 0;
  env.view.render(bubble, TD.empty("t1"), rend);
  assert("an empty document does not orphan-flag an existing card",
    env.invariants.filter((r) => r.code === "orphaned-gate").length === 0,
    JSON.stringify(env.invariants));
}

if (fail) {
  console.error("\n" + fail + " assertion(s) failed");
  process.exit(1);
}
console.log("\nall ok");
