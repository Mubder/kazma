# Plan: CoT workbench and streaming thoughts — one document, one fold

> **SUPERSEDED IN PART — 2026-09-20.** [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md)
> supersedes this document's **separate live header bar** and **any automatic
> opening of thoughts** on adoption of that plan. Thoughts/activity are one
> disclosure inside the assistant turn block, collapsed by default, and event
> processing never changes the user's expansion choice (invariant U08).
> Everything else here — one document, one fold, no second CoT painter —
> stays binding. Retained as history for the incidents it records.

**Date:** 2026-09-20
**Status:** BINDING (overhaul landing)
**Does not replace:** [`TURN_RENDER_V2_KEYED_SLOTS.md`](TURN_RENDER_V2_KEYED_SLOTS.md) (keyed bubble) or [`HITL_VIEW_MODEL.md`](HITL_VIEW_MODEL.md) (gate `view`). Those stay. This is the leftover **live CoT / thoughts** half: the same disease HITL just closed, one layer down.

**Governing rule** (same as Turn Delivery V2 / Turn Render V2):

> Every fix must answer YES to both: (1) is this the standard pattern for this class? (2) does it remove the class, not the incident?

---

## 1. The one path

Thinking and tools are **parts of the turn document**. One workbench slot in the bubble paints them. That slot is the same node while the turn runs and after it finishes. Expanding it always shows the full thoughts. Clamp is CSS. Nothing unmounts the thoughts into a second widget.

That is the whole plan.

What this is **not:** a React rewrite, a third “live thoughts” panel, or dumping reasoning into the answer because the answer slot was empty.

---

## 2. What products that do this well actually do

| Product | Pattern |
|---------|---------|
| Claude | One collapsible **Thinking** block **above** the answer. Live it grows. Done it becomes “Thought for Ns” and expands to the same text. It never leaves the message. |
| ChatGPT | Same: accordion on the message, persisted, re-readable after refresh. |
| Cursor / Codex | Tool calls are a **timeline of cards** on the message; thinking is a fold, not a toast. |

Shared rules:

1. Thinking is **transcript**, not chrome that docks then dies.
2. **One fold per turn** (or per hop), not a live card plus a restored clone.
3. Tools are rows in that fold (or sibling cards), not a second log.
4. Truncation is a **display** clamp with “show more,” never deletion.

Kazma already has the document (`reasoning` / `tool` / `status` parts) and the keyed slot (`workbench`). Live still paints somewhere else.

---

## 3. Why thoughts vanish today (verified)

Three writers, then a handoff that drops the body.

**A. Live Task Card is a second CoT.**
`chat.js` says the running step list is the Live Task Card’s job; the in-bubble workbench is only the finished summary. The card **clamps reasoning to 2 lines**, **caps 50 rows**, then **unmounts on done** and “finalizes into the transcript bubble.” That is two DOM trees and a copy. The copy is lossy. After unmount you cannot open the live card and read the thoughts again.

**B. Restored workbench is a third tree.**
Refresh builds `_buildRestoredWorkbench` (`is-collapsed`). Same activity, different node, collapsed by default. If persist already dropped reasoning, expand is empty.

**C. The document sometimes eats thoughts as the answer.**
`split_stream_and_final`: if streamed text is a prefix of the final answer, **reasoning is `""`**. Hop-0 narration that became the reply is gone as CoT. `_answerFromDoc` then **falls back to the last reasoning part** when there is no text — thoughts paint as the answer and disappear as thoughts.

**D. Reasoning identity is the first 240 characters.**
`partKey` for reasoning is `("reasoning", text[:240])`. A growing thought is many keys; a later hop does not append, it mints another part or fails to merge. There is no stable “this turn’s thinking” id.

**E. `tokenAccum` is still a live-text cache.**
Tokens already go through `applyTurnEvent` → document; `_paintTextSlot` copies into `tokenAccum`; the throttle paints from the copy. Eleven independent blanks. Same class as HITL overlay vs `view`. This PR series can take that as a **later cut**, not the first.

**F. `logProgress` still exists next to TurnView.**
When TurnDocument is present, `logProgress` already feeds `applyTurnEvent` and returns. The leftover DOM path (`ensureProgressPanel` mutating `_progressEl`) is the dual writer if anything still calls it without going through the document.

---

## 4. Target

```
Turn document
  reasoning[]   append-only hops (stable id per hop, not text[:240])
  tool[]        timeline
  status[]      heartbeats (coalesce, do not store 50 copies)
  hitl[]        unchanged
  text          user-facing answer only — never a thought fallback

TurnView slot `workbench`   THE only CoT painter
  header bar    “Thought for 12s · 3 tools”  (collapsed default when done)
  body          full thoughts + tool rows     (expand = same node as live)

Live Task Card  HEADER ONLY while the turn runs
  phase, elapsed, Stop, Review (HITL jump)
  no step list, no 2-line thought clamp, no unmount-copy
```

Order stays Turn Render V2:

`[workbench] [settled HITL…] [answer] [pending HITL…] [chrome]`

Industry mapping: Claude’s thinking block **is** the workbench slot. The Live Task Card is Cursor’s status bar (what is it doing **now**), not a second transcript.

---

## 5. Invariants (removing any reintroduces “thoughts gone”)

1. **Thoughts are parts.** A reasoning hop is persisted on the assistant row. Refresh expands the same text the live fold showed.
2. **One workbench node.** `TurnView` owns `.agent-progress`. Live Task Card must not render a step list. `logProgress` may only `applyTurnEvent`.
3. **Ambiguity never deletes thoughts.** Cap and clamp are display. `discard` stays false for workbench. No 50-row drop of reasoning.
4. **Answer is never stolen from thoughts.** `_answerFromDoc` does not fall back to `reasoning`. Empty answer is empty, not a recycled thought.
5. **Prefix merge does not eat distinct narration.** `split_stream_and_final` may collapse true prefixes of the *same* hop; superseded narration (`_narration_acc`) stays a reasoning part (already the persist path — keep it on the wire for the client too).
6. **Live Task Card unmount does not take the thoughts with it.** Unmount is hide-the-header, not destroy-the-log.

---

## 6. PRs (same sequencing rule as HITL: each cut deletes a second writer)

### F0 — Fixtures that fail today

Hands: none behavioral.

- Node/TurnView fixture: reasoning part in the doc → workbench expand shows full text after `status: done`.
- Fixture: live card unmount must not empty workbench.
- Fixture: `_answerFromDoc` with only reasoning returns `""`.
- Fixture: streamed prefix of final still keeps `_narration_acc` as reasoning in the document the client sees.
- Do not skip. If a fixture needs persist, say so.

**Files:** `tests/js/test_turn_view.js` (new blocks), maybe `tests/test_turn_document.py`.

### A — Document: thoughts are durable and not the answer

Hands: persist + projector. UI still dual-paints.

- Stable reasoning key: `("reasoning", hop_id)` or a single growing `("reasoning",)` merge (append text), not `text[:240]`.
- Client `applyEvent` for tokens/done must keep superseded narration as reasoning (mirror `_narration_acc`).
- `_answerFromDoc` drops the reasoning fallback.
- Persist already writes reasoning parts — keep that; add a test that `/messages` round-trips them.

**Files:** `turn_document.py`, `turn_document.js`, `chat.js` (`_answerFromDoc`), persist tests.

### B — One painter: workbench slot is CoT; Live Task Card is a bar

Hands: web chat.

- `_docHasBubbleContent`: a running turn **with reasoning or tools** mints the bubble (so the fold exists live, not only after done).
- `_paintWorkbenchSlot` is the only step-list writer. Delete or no-op the Live Task Card body (`_tcStepsFromDoc` / `.live-task-steps`).
- `logProgress` stays an `applyTurnEvent` feeder; delete the `ensureProgressPanel` DOM fallback.
- On done, **do not unmount-copy**. Collapse the same workbench (`is-collapsed`, header “Thought for Ns · K tools”). Expand is the live body.

**Files:** `chat.js`, `chat.html` (task card body optional/hidden), `tests/js/test_live_task_card.js`, `test_turn_view.js`.

### C — Thoughts stay readable

Hands: workbench body.

- One **Thoughts** row (or section) in the workbench: full reasoning text, clamp with Show more, never 2-line-and-gone.
- Tool rows stay as they are (`_stepRowHtml`).
- Status heartbeats still coalesce.
- Refresh: hydrate paints the same fold open/closed from document, not a second restored template if TurnView already built it.

**Files:** `chat.js` workbench painters, CSS, TurnView fixture “expand after done shows full thought.”

### D — Streaming answer is the document (`tokenAccum` derived)

Hands: live text.

- Paint live markdown from `TD.textOf(doc.parts)` (or `doc.stream`).
- Delete the eleven `tokenAccum = ''` sites; keep one local cache only if the throttle needs a string, filled from the document in `_paintTextSlot`.
- Empty-accumulator guard comes out.

**Files:** `chat.js`. Can ship after A–C if A–C already make thoughts survive.

### E — Diagnosis map + CHANGELOG

Update `docs/docs/ops/diagnosis-map.md`: CoT has one fold; Live Task Card is phase chrome; thoughts are parts. No fourth painter.

---

## 7. What stays

- HITL `view` / join-before-paint
- TurnView keyed slots and declared order
- FanOut / gateway / TUI (they do not paint this CoT). Do not drag them into a React rewrite.
- Plan fence (` ```plan `) as workbench checklist, not answer
- Persist of `parts` on the assistant row

## 8. Non-goals

- Rewriting `chat.js` in React
- Showing provider “hidden chain-of-thought” that the API does not send (Kazma only has streamed narration + tool rows)
- Per-token DOM paint (150ms throttle stays)
- Splitting batched HITL into four cards (operator chose one card per pause)

---

## 9. Suggested commit subjects

- F0: `test(cot): thoughts survive done and refresh (may fail)`
- A: `fix(cot): reasoning is a durable part, not an answer fallback`
- B: `fix(cot): one workbench painter; task card is a bar`
- C: `fix(cot): thoughts expand to full text after the turn`
- D: `fix(cot): live answer paints from the document`
- E: `docs(cot): diagnosis-map — one fold`

---

## 10. Operator / agent rules after landing

- A PR that adds a live-only thoughts panel “for the running turn” is rejected.
- A PR that unmounts CoT on done and restores a clone is rejected.
- `_answerFromDoc` falling back to reasoning is rejected.
- Live Task Card may change phase/elapsed/Stop. It may not own a step list.
