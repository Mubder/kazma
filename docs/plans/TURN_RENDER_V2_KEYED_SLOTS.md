# Plan: Turn Render V2 — Keyed Slots (the render half of Turn Delivery V2)

**Date:** 2026-09-19
**Status:** IMPLEMENTED
**Completes:** `docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md` — **KD-4**, which shipped only on the transport side.

---

## Governing rule (unchanged, binding)

> Every fix must answer YES to both: (1) **Is this the industry-standard fix for this problem class?**
> (2) **Is it the best-known instance of that standard — i.e. does it remove the root cause class, not the incident?**

---

## Why this plan exists

Turn Delivery V2 (2026-08-23) shipped the delivery half: a per-thread journal,
monotonic `seq`, cursor resume on both transports, and an unconditional
snapshot resync. That half works. KD-4 also specified the other half:

> **KD-4 — Client paints from state, not events.** A single `TurnChannel` owns
> transport + cursor + dedupe; it mutates a plain turn-state object; one
> `render()` applies state→DOM idempotently.

The turn-state object was built (`modules/turn_document.js`, a pure,
seq-deduped projector). **`render()` never was.** `renderTurn` kept asking the
DOM where to paint, and the result was a bug class that was fixed roughly
fifteen times between 2026-09-01 and 2026-09-19 — each time through a
different path, each fix narrowing one guard until the next path found
another.

The operator's summary of it: *"the chat bubble fails to replace the HITL
placeholder … we fixed this 10–20 times in the last two weeks and it keeps
coming back."*

---

## Root cause (three layers, all required)

### 1. The document could not represent the transcript

```python
# turn_document.py, _part_key
if kind == "hitl":
    # One HITL slot per turn — pending → approved/denied/timeout replaces.
    return ("hitl",)
```

A turn that pauses twice — sequential *Allow this tool* clicks, or
`file_write` then `file_delete` — **cannot be expressed**. The second gate
overwrote the first. The DOM kept both cards (`renderHitlCard` never removed a
claimed one), so the document said one decision and the screen said two, and
every reconciliation between them was a guess.

This is why the DOM had become the state store: it was the only place holding
the full gate history.

### 2. The renderer asked the DOM where to paint

`renderTurn` resolved its target by `querySelector` on the turn id → fall back
to `currentMsgEl` → fall back to "last assistant bubble after the last user
row" → then walk `nextElementSibling` to *guess* whether that bubble was
historical → then `_rescueTurnDom` to lift nodes back out of a collapsed
progress panel → then `_hitlCardIsTrapped` to decide whether an approval card
had swallowed the text host.

Every one of those is a heuristic over DOM **shape**, so every change to the
markup re-broke one of them.

Three writers fought over one bubble's children (`appendMessage`,
`_syncCotPanel`, `renderHitlCard`, plus `_placeHitlCard` and
`_parkClaimedHitlCard` moving nodes on click), and `_rescueTurnDom` ran
afterwards to repair the mis-nesting.

### 3. The recovery net disarmed itself

`hasInlineApprovalCard()` scanned the transcript for an enabled `<button>`, so
it answered **true for a fossil card** whose gate had already settled. Eight
recovery paths early-returned on it — including `_resyncDelivery`, the only
unconditional route back to server truth. The net switched itself off exactly
when a turn had gone quiet.

And nothing could detect the failure: the server had the answer, the bubble
showed a placeholder, and the **operator** was the error channel.

---

## Target architecture

> One writer owns the bubble's children, and that child list is a pure
> function of the turn document. Identity is shared between the two, so
> "which node is this part" cannot drift from "which part is this".

| Pattern | Reference implementations |
|---|---|
| Keyed reconciliation (stable key → node, diff + reorder) | React/Preact keyed children, Vue `:key`, Svelte keyed `{#each}` |
| Render-from-store, single writer | The same SWR / Linear-sync model KD-5 already cites |
| Identity from the domain model, not the view | Any list-diffing UI; here `partKey` is shared verbatim |
| Post-render invariant + self-report | Assertion-based UI conformance checks |

### Layer 1 — the document (`turn_document.py` + `modules/turn_document.js`)

`partKey` for a HITL part becomes `("hitl", interrupt_id)`. One slot **per
gate**, not per turn. Consequences:

- `merge_hitl_part` only ever merges a gate with a newer stamp of *itself*
  (rank-monotonic, so a replayed `pending` cannot walk back a claim).
- `hitl_parts_of()` returns every gate in ask order; `hitl_part_of()` returns
  the gate still **waiting**, falling back to the most recent.
- Turn status is `paused` when **any** gate is pending — not when the newest
  one is. Reading the newest reported "streaming" while the graph sat blocked
  on an earlier gate.

### Layer 2 — the renderer (`modules/turn_view.js`, new)

`TurnView` owns the child list of one assistant bubble's `.message-content`.

**Contracts (enforced by tests/js/test_turn_view.js):**

1. **Identity is shared with the document.** Slot keys come from
   `KazmaTurnDocument.partKey` — the same function the document dedupes with.
2. **Order is declared, not patched:**
   `[workbench] [settled gates…] [answer] [pending gates…] [chrome]`
   A settled gate sits above the answer (what `_parkClaimedHitlCard` achieved
   by moving nodes); a gate still being asked sits below the text so far (what
   `_placeHitlCard` achieved with `compareDocumentPosition`). One declarative
   rule replaces two imperative movers fighting over the same children.
3. **Slots are flat siblings** of `.message-content` — required by CSS anyway,
   and it makes "the card trapped the answer inside the panel"
   *unrepresentable*. `_rescueTurnDom` has nothing left to rescue.
4. **Ambiguity never deletes.** A slot is removed only when the caller's
   `discard` explicitly returns true; chat.js answers false for every slot.
   A stale node is visible and reportable; a removed one is silent, and
   silence is the bug class. Always fail toward visible.
5. **The render reports on itself.** Every pass re-derives what should be on
   screen and compares. `text-blank` / `text-missing` / `gate-missing` go to
   `onInvariant` → console + `diag()` + one authoritative resync.

The turn→bubble **registry** replaces `querySelector`: `'live'` is a map key
that gets *renamed* on promotion, so it can never be a selector matching a
leftover bubble (the 2026-09-03 crossed-bubble magnet).

Rendering the same document twice performs **zero DOM mutations** — locked by
test, which is what makes "did it churn?" answerable at all.

### Layer 3 — one writer

Every HITL source — SSE frame, WS frame, gate registry, `/api/pending-approvals`
recovery, session hydration, and the operator's own click — feeds
`applyTurnEvent`. `renderHitlCard` builds a card and returns it; it no longer
decides placement, and it no longer carries the guard family that existed only
to answer "does a card for this gate already exist?".

The click path now records its decision in the document immediately
(`_noteGateDecided`), instead of moving the node by hand and leaving the model
saying `pending` until a server frame happened to arrive.

### Layer 4 — the guards stop disarming

Logic decisions consult `hasLiveGate()` (document + registry). The DOM scan
survives for exactly one job: deciding whether the Alpine store fallback is
needed. **A recovery path that can decline is not a recovery path.**

---

## Deleted (same series, no dual paths)

`_placeHitlCard`, `_parkClaimedHitlCard`, `_rescueTurnDom`, `_syncCotPanel`,
`_hitlCardIsTrapped`, `_hitlHostContent`, `_paintHitlFromDoc`, the
`nextElementSibling` historical scan, the `data-turn-id="live"` lookup/stamp
pair, `renderHitlCard`'s four-guard preamble and its card-removal sweep, and
the `_Action required…_` placeholder written into `.message-text`.

That last one is the line the bug class was named after. The reply had to
*replace* it; every incident was a path where the replacement did not happen.
The answer slot now holds the answer or nothing, and the pause is a sibling
slot — **there is nothing left to replace.**

Locked by `test_dom_movers_stay_deleted`: while a mover exists, something
starts calling it again.

---

## Tests

`tests/js/test_turn_view.js` (new, 60 assertions, Node + `tests/js/_dom.js`)
replays real frame sequences and asserts the resulting DOM. One block per
incident that shipped:

| Fixture | Incident |
|---|---|
| sequential approve ×2 → reply paints | 2026-09-19 (silence after two approvals) |
| pending gate below the interim text | 2026-09-04 |
| settled gate above the answer | 2026-09-01 |
| every slot a direct child; no nesting | 2026-09-02 (card trapped in CoT) |
| `'live'` promotion, two turns, no crossing | 2026-09-03 (crossed bubbles) |
| collapsed workbench stays collapsed | 2026-09-03 (auto-expand) |
| answer in doc + blank bubble → reports itself | the detector the class never had |
| replay/out-of-order arrival converges | journal replay |
| idempotence: second render moves nothing | render churn |
| a vanished part is kept, not deleted | contract 4 |

`tests/js/test_boot.js` (new) evaluates all three client modules together
against the shim DOM and checks the `KazmaChat` surface is wired and a render
round-trips. This closes a real gap in the strategy: `node --check` proves a
file *parses*, and the rest of the client suites assert on source **text** —
so a rename that misses one call site produces a file that parses, throws
`ReferenceError` on load, blanks the chat page, and leaves every suite green.

The source-grep suites were re-pointed at the new contracts rather than
deleted: `test_chat_steer_composer.py`, `test_turn_delivery_cqrs.py`,
`test_delivery_v2_client.py`, `test_turn_ledger_abc.py`,
`test_chat_as_product.py`. `tests/js/test_place_hitl_card.js` was removed —
it tested a function that no longer exists, and its contracts are now
behavioural in `test_turn_view.js`.

## Follow-ups (not blocking)

- `tests/js/test_markdown_render.js` fails at `main` and still fails here —
  its own harness never defines `window`, which `renderTable` reaches for via
  `KazmaBidi`. Pre-existing, unrelated to this plan, and **not driven by
  pytest**, so nothing in CI covers that file today.
- The server projects `parts` but has no endpoint that returns a whole
  TurnDocument; `/messages` carries `parts` per row, which is what hydration
  uses. A first-class `GET …/turns/{id}` snapshot would let the client rebuild
  one turn without refetching the transcript. Not needed for this fix.

---

## What this does not change

Transport, journal, cursor resume, snapshot resync, the detached pump,
SessionStore persistence, the HITL graph interrupts and `/api/approve`
endpoints, the gate registry, the Live Task Card, and platform isolation.
This plan is strictly the client's paint path.
