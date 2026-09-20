# Unified turn block — Phase 3 report: one approval group

Date: 2026-09-20

Status: Phase 3 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
Builds on [Phase 0](UNIFIED_TURN_BLOCK_PHASE0.md),
[Phase 1](UNIFIED_TURN_BLOCK_PHASE1.md) and
[Phase 2](UNIFIED_TURN_BLOCK_PHASE2.md).

---

## 1. What changed

Four requests in one turn were four independent top-level cards, **sorted
by state**: settled ones above the answer, still-asking ones below it.

Two consequences, both of them the reader's problem:

1. **Deciding a gate moved the answer.** A gate crossed the text when it
   settled, so the reply relocated mid-turn. That was not a bug in the
   sort — it was the sort. Position encoded state, so changing state
   changed position.
2. **Four requests read as four turns.** Nothing tied them together, and
   the transcript gave no count.

Now: one `approvals` region, above the answer for the whole turn, holding
one row per gate **in ask order**. Settling a gate changes its label, not
its place. The region is a flat sibling of the answer — never its ancestor
— so a collapsed disclosure still cannot swallow the reply.

The cards themselves are unchanged. `renderHitlCard` still builds them with
the same argument preview, scope explanation, countdown and controls
(plan §3: "Preserve current argument/diff preview, scope explanation,
timeout information, and semantic clarification controls"). What changed is
that they are rows in one region instead of siblings of the answer.

---

## 2. Plan §3 requirements, line by line

| Requirement | Where |
|---|---|
| Zero groups when there are no gates; exactly one when at least one exists | `slotPlan` pushes the region only when `rows.length` |
| One row per actual gate ID | Rows keyed by `partKey` — the same string the document dedupes with |
| Pending rows expose controls even when thoughts are collapsed | The region is a sibling of the activity disclosure, not inside it |
| Multiple simultaneous pending gates each retain explicit controls | Each row is its own card; nothing assumes one pending gate |
| Decision and execution labels are separate | Unchanged — the card's own labels, from the server view |
| Approval group location stays stable above the answer | Ask order, and the region precedes `text` in every phase |
| Rows update in request order without moving the answer | Asserted in the browser: the answer's index does not change when a gate settles |

---

## 3. HITL_VIEW_MODEL.md Playwright 1 and 4 — claimed

Those two were declared unharnessable on 2026-09-19:

> **F0 spike verdict (2026-09-19): `no`.** `create_app()` + TestClient +
> `ainvoke({tool_calls_pending: file_write})` ran the supervisor LLM path
> (HTTP 401, `turn_failed`) and never `interrupt()`'d. […] **Playwright 1
> and 4 stay unclaimed.**

The measurement is correct and still locked by
`tests/test_f0_hitl_app_graph_spike.py`. The conclusion drawn from it was
too broad: the app graph pauses fine when the supervisor **produces** the
tool call rather than being handed one.

`tests/e2e/test_unified_turn_browser.py` claims both:

* **1 — sequential allow-tool.** Four gates, four real Approve clicks on
  real buttons that POST `/api/approve/{thread_id}`, one bubble, four rows
  in ask order, the reply in the same bubble.
* **4 — approve then look before the next poll.** Every poller is
  suspended after the click, so whatever the row says next came from the
  approval response. Two things are checked: the row stops offering a
  decision, and the **answer has not moved**. Under the old layout the
  second was a claim about where the reply ended up; it is now structural.

The plan's merge rule — "a PR that claims to fix numbered incident N must
turn test N green in that PR" — is satisfied.

---

## 4. Test re-pointing

Twenty-two assertions in `tests/js/test_turn_view.js` and four in
`tests/test_chat_steer_composer.py` encoded the old flat layout. Every one
was re-pointed, none deleted, because every one is an incident lock:

| Lock | Was | Is |
|---|---|---|
| "pending gate sits BELOW the interim text" | position relative to the answer | the pending request is a row, and the region is above the answer |
| "the settled gate stays above the pending one" | state-sorted position | ask order — a decision changes a label, not a place |
| "a claimed gate sorts ABOVE the answer" | position | the LABEL comes from the resolver, not the part stamp |
| "the rebuilt card sorts BELOW the answer" | rebuild re-sorted the card, moving the reply | the row is rebuilt in place and the answer never moves |
| "the declared slot order" | `workbench, settled, text, pending` | `header, workbench, approvals, text` |

The renderer test harness gained a real approvals painter with keyed rows,
so the suite exercises the same reconciliation `chat.js` performs rather
than asserting on a stub.

---

## 5. One count, not two

Found in the browser mid-resume: the header read "4 approvals" while the
group beneath it read "3 requests". The header counted **parts**; the group
counted **rendered rows**, and a pending gate whose view the registry has
not confirmed is deliberately not rendered (minting Approve buttons for an
unconfirmed gate is the ghost-button defect).

One fact, two answers — the class this plan exists to remove, reproduced
inside the fix for it. `turn_presentation.gateRows()` now applies the same
omission rule `slotPlan` does and both surfaces count its output. The
omission stays loud rather than silent: `turn_view.verify()` raises
`gate-missing` and the renderer resyncs once, which is how the view
arrives.

---

## 6. Evidence

| Claim | Test |
|---|---|
| One region, four rows, ask order, pending and settled together | `tests/js/test_unified_turn_block_phase0.js` (all five checks now green) |
| Rows keyed by gate; a rebuild does not move the answer | `tests/js/test_turn_view.js` |
| The header and the group count the same thing | `tests/js/test_turn_presentation.js` |
| Four real approvals through the real routes and graph | `tests/e2e/test_unified_turn_app_graph.py` |
| What the operator sees, four real clicks | `tests/e2e/test_unified_turn_browser.py` |
| Refresh mid-pause rebuilds the REGION, not loose cards | `::test_refresh_mid_pause_rebuilds_the_group` |
| Refresh mid-pause and after settle still pass unchanged | `tests/e2e/test_hitl_view_model.py` (the existing CI gate) |

Manual browser observation of the finished four-gate turn:

```
turn-header      ✓ Completed · 4 tools · 4 approvals
agent-progress   (collapsed)
turn-approvals   Approvals · 4 requests   [4 rows, all approved, no live buttons]
message-text     Scaffold ready. I wrote README.md and src/index.js …
```

One bubble. No loose cards. No bottom bar. A reload reconstructs it
identically.

---

## 7. What Phase 3 does NOT claim

* **Concurrent-client tests are not written.** Plan §12 asks for
  "concurrent-client integration/browser tests" — two tabs approving at
  once, a lost approval response, a 409 resolving to the server view. The
  registry's CAS behavior is unchanged and covered by
  `tests/test_hitl_gates.py`, but the two-tab UI path is not. Carried to
  Phase 4's acceptance matrix.
* **The acceptance matrix is not complete.** Disconnect mid-token, journal
  retention gap, restart mid-pause, session switch during updates, long
  activity, RTL and mobile are Phase 4.
* **The legacy progress painter is still in the file** — unchanged from
  Phase 2 §6, still carried to Phase 5.
