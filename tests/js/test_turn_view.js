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

/** Class names of a bubble's content children, in DOM order. */
function shape(bubble) {
  const content = bubble.querySelector(".message-content");
  return content.children.map((c) => {
    const cls = c.className.split(/\s+/);
    if (cls.indexOf("message-text") >= 0) return "text";
    if (cls.indexOf("agent-progress") >= 0) return "workbench";
    if (cls.indexOf("hitl-approval-card") >= 0) {
      return "card(" + (c.getAttribute("data-interrupt-id") || "") + ":" +
        (c.getAttribute("data-state") || "") + ")";
    }
    if (cls.indexOf("message-meta") >= 0) return "meta";
    if (cls.indexOf("message-actions") >= 0) return "actions";
    return c.className;
  });
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
      else if (entry.kind === "workbench") el.className = "agent-progress";
      else if (entry.kind === "hitl") {
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
      } else if (entry.kind === "hitl") {
        // From entry.state — the value TurnView ORDERED by. Reading
        // entry.part.state here is the bug this contract exists to stop.
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
  assert("text paints", shape(bubble)[0] === "text", shape(bubble));
  assert("chrome stays last",
    shape(bubble).slice(-2).join(",") === "meta,actions", shape(bubble));
  assert("no invariant on a healthy paint", env.invariants.length === 0, env.invariants);
  assert("report names the plan", r.plan.join(",") === "text", r.plan);
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
  assert("pending gate sits BELOW the interim text",
    s.indexOf("text") < s.indexOf("card(g1:pending)"), s);
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
    s.indexOf("card(g1:approved)") >= 0, s);
  assert("both gates are on screen",
    s.indexOf("card(g2:pending)") >= 0, s);
  assert("the settled gate stays above the pending one",
    s.indexOf("card(g1:approved)") < s.indexOf("card(g2:pending)"), s);
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
    s.indexOf("card(g1:approved)") >= 0 && s.indexOf("card(g2:approved)") >= 0, s);
  assert("answer sits below both decisions",
    s.indexOf("card(g2:approved)") < s.indexOf("text"), s);
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
  assert("workbench renders first",
    shape(bubble)[0] === "workbench", shape(bubble));
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
  assert("card present before the drop", shape(bubble).indexOf("card(g1:approved)") >= 0);

  // A frame that loses the gate (truncated resync, partial hydrate) must
  // NOT take the decision off screen: removing is the silent failure.
  const thin = TD.empty("t1");
  const thinDoc = TD.applyEvent(thin, { type: "done", content: "one" });
  env.view.render(bubble, thinDoc, rend);
  assert("a vanished part is KEPT, not deleted",
    shape(bubble).indexOf("card(g1:approved)") >= 0, shape(bubble));

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
    s.indexOf("card(g1:approved)") < s.indexOf("text") &&
    s.indexOf("card(g2:approved)") < s.indexOf("text"), s);
  assert("replayed gates keep ask order",
    s.indexOf("card(g1:approved)") < s.indexOf("card(g2:approved)"), s);
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
  rend.paint = (entry, el, ctx) => {
    if (entry.kind === "text") {
      const t = TD.textOf(ctx.doc.parts);
      el.setAttribute("data-md", t); el.textContent = t;
    } else if (entry.kind === "hitl") {
      // Label from the entry, never from the part.
      el.setAttribute("data-state", entry.state);
    }
  };

  const doc = feed([
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "file_delete",
      payload: { interrupt_id: "g1" } },
    { type: "done", content: "Longer run complete." },
  ]);
  env.view.render(bubble, doc, rend);

  const s = shape(bubble);
  assert("a claimed gate sorts ABOVE the answer, not below it",
    s.indexOf("card(g1:inflight)") < s.indexOf("text"), s);
  assert("the label matches the ordering",
    s.indexOf("card(g1:inflight)") >= 0, s);
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
  assert("the resolver decides ordering, not the raw part stamp",
    s.indexOf("text") < s.indexOf("card(g1:pending)"), s);
}

{
  // slotPlan carries the resolved state on the entry, so a painter cannot
  // disagree with the ordering even by accident.
  const plan = TV.slotPlan(
    { parts: [{ type: "hitl", interrupt_id: "a", state: "pending" }] },
    { text: true }, TD,
    () => "approved",
  );
  const gate = plan.find(e => e.kind === "hitl");
  assert("the entry carries the resolved state", gate && gate.state === "approved");
  assert("and it sorted by that state", plan.map(e => e.key).join(",") === "hitl:a,text",
    plan.map(e => e.key));
  // No resolver → falls back to the part's own stamp.
  const bare = TV.slotPlan(
    { parts: [{ type: "hitl", interrupt_id: "a", state: "pending" }] },
    { text: true }, TD,
  );
  assert("without a resolver it falls back to part.state",
    bare.map(e => e.key).join(",") === "text,hitl:a", bare.map(e => e.key));
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
}

{
  // Hydration paints a pending part as 'awaiting' (disabled, sorted as
  // settled) because the registry has not answered yet. Paint-in-place
  // cannot put the buttons back — awaiting replaced the actions HTML.
  // When the registry later says pending, rebuild must tear the frozen
  // node out so build() mints a live card, which then sorts BELOW the
  // answer. Without this, a refresh on a live pause leaves "Waiting for
  // approval…" with no buttons forever.
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
    { type: "hitl", state: "pending", interrupt_id: "g1", tool: "file_write",
      payload: { interrupt_id: "g1" } },
    { type: "done", content: "working…" },
  ]);
  env.view.render(bubble, doc, rend);
  let s = shape(bubble);
  assert("hydrate awaiting sorts ABOVE the answer",
    s.indexOf("card(g1:awaiting)") < s.indexOf("text"), s);

  env.built = [];
  rend.gateState = () => "pending";
  env.view.render(bubble, doc, rend);
  s = shape(bubble);
  assert("a live registry row rebuilds the frozen card",
    env.built.indexOf("hitl:g1") >= 0, env.built);
  assert("the rebuilt card sorts BELOW the answer",
    s.indexOf("text") < s.indexOf("card(g1:pending)"), s);
  assert("and the label matches the new order",
    s.indexOf("card(g1:pending)") >= 0, s);
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
  assert("plan order is workbench, settled, text, pending",
    plan.map((p) => p.key).join(",") === "workbench,hitl:a,text,hitl:b",
    plan.map((p) => p.key));
  assert("empty doc plans nothing",
    TV.slotPlan({}, {}, TD).length === 0);
  assert("plan is a pure function of the doc",
    JSON.stringify(TV.slotPlan({ parts: [] }, { text: true }, TD).map((p) => p.key))
      === JSON.stringify(["text"]));
}

if (fail) {
  console.error("\n" + fail + " assertion(s) failed");
  process.exit(1);
}
console.log("\nall ok");
