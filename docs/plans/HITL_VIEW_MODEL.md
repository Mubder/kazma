# Plan: HITL view model — join before paint, one resolver

**Date:** 2026-09-19
**Status:** BINDING (C landing)
**Owner:** this series is held by one agent. Bouncing the working copy is how two locally-correct patches still broke the live install.
**Does not replace:** [`HITL_GATE_REGISTRY_PLAN.md`](HITL_GATE_REGISTRY_PLAN.md) (P6 — decision SoT) or [`TURN_RENDER_V2_KEYED_SLOTS.md`](TURN_RENDER_V2_KEYED_SLOTS.md) (keyed bubble renderer). Those layers stay. This plan is the missing **join** between them, plus every other mouth that still derives “what should I show?” on its own.

**Word ban:** do not write `industrial` in a commit subject, PR title, or operator-facing claim until PR F is green on CI. The last two rounds that used it broke the live install the same afternoon.

---

## 1. The one path

The server is the only place that answers “what state is this gate in.” It does that in **one function**, at the serialization boundary, and ships a `view` the client paints.

Join the transcript with the registry **before any surface paints**. Every mouth **submits** through `POST /api/approve/{thread_id}`. Delete every second card and every client-authored timeout. Each behavioral PR turns its numbered Playwright (or harness-honest substitute) green **in the same PR**.

That is the whole plan. The PRs only sequence it so no hand goes dark.

What this is **not:** another bubble patch (`awaiting`, `decided_locally`, `rebuild` as product state, Alpine “for safety”). Those exist because the client paints, then guesses. This series removes the guess.

---

## 2. Why “one resolver” kept failing

P6 already claimed Step Functions / Temporal shape for **decision** truth. That registry is real.

The last three weeks of live failures were the **view**:

| Claim that shipped | What was still true |
|---|---|
| Keyed slots own the bubble | Alpine strip, dashboard poller, TUI poller, `setCardState`, countdown, abort, and fossil reconcile still mutate cards |
| One resolver for order and label | `_hitlDisplayState` reads hydrate flag, local-click flag, DOM claimed-scan, `/status` snapshot, and `part.state` |
| Hydration cannot mint ghost buttons | `loadSession` fetches **messages only**, paints, then `/status`. The gap is ghosts, frozen “Waiting…”, and `rebuild` |
| Green fixtures | All used the default resolver. Disagreement was never a fixture. No Playwright Approve click exists |

`live_gates()` returns only `LIVE_STATES` (`pending`, `claimed`, `resuming`). `/status.gates` is built from that list. A session with twenty past approvals has **twenty gates with no covering row**. A table that only specifies “stale pending → error” leaves the common case unspecified. If JS then reads `part.state` for the chip, the second resolver is back in the place that looks harmless.

`isPendingGate(part)` is already deleted on `main` (`b4ae89c6`). Do not resurrect it.

---

## 3. Definition of done

A change in this series is **not done** when the unit suite is green.

Numbered incidents (the live install, 2026-09-19 and the two weeks before):

| # | Incident | Feasible today? |
|---|---|---|
| **2** | Refresh **mid-pause**: live buttons exist, composer locked, **no** Alpine twin, **no** “Waiting…” with no buttons | Yes. Seed `persist_reply` + `register_gate`, open `/chat`, reload. Same shape as `tests/e2e/test_smoke.py` |
| **3** | Refresh **after settle**: no live buttons, answer visible, no red timeout on an approved gate | Yes. Same seed, terminal part, empty live list, `gates_authoritative` |
| **4** | Approve then look **before** the next `/status` poll: card stays above the answer because the **200 body carries the view**, not because a client bit outranks the registry | Needs a paused checkpoint on the **app** graph (`POST /api/approve`). Mini graph in `tests/test_hitl_graph_integration.py` is proven; `create_app()` is not |
| **1** | Sequential allow-tool: `file_write` → Approve → `file_delete` → Approve → reply in the **same** bubble, settled cards **above** the reply | Same harness as 4, plus a second interrupt after resume |

**F0 answers the harness question before any behavioral PR claims 1 or 4.** Tests that cannot pause the app graph must not skip and call that coverage. If the spike fails, the plan says so in F0’s report; 1 and 4 stay pytest/graph-integration + TurnView fixtures until the harness exists. The word ban still holds.

**F0 spike verdict (2026-09-19): `no`.** `create_app()` + TestClient + `ainvoke({tool_calls_pending: file_write})` ran the supervisor LLM path (HTTP 401, `turn_failed`) and never `interrupt()`’d. Entry is `NodeName.SUPERVISOR`, not `tool_worker`. The mini graph in `tests/test_hitl_graph_integration.py` still pauses. **Playwright 1 and 4 stay unclaimed.** Re-measure with `KAZMA_F0_SPIKE_LIVE=1`.

Two more, same series, not the word-ban:

| # | Incident |
|---|---|
| **5** | Approve on dashboard → chat card settles without a third click (and the reverse) |
| **6** | Gateway `hitl approve {thread_id}` still resumes; sequential second card still appears. Do **not** “unify” onto per-gate slash ids |

**Merge rule:** a PR that claims to fix numbered incident N must turn test N green in that PR (or the F0 report has already declared N unharnessable, and then that PR must not claim N).

---

## 4. Five laws

1. **Join before paint.** `loadSession` does `Promise.all([messages, status])`. One TurnView pass. No `_hydratingSession` HITL branch. If the snapshot is not in, **omit HITL chrome** — honest empty, not a lying card.
2. **One function, Python, at the wire.** `resolve_gate_views(parts, live_rows, *, authoritative) -> list[GateView]`. Called when `/messages` is serialized, when `/status` is built, when a journal `hitl` frame is emitted, and when approve returns 200. JS **consumes** `part.view` / `status.gate_views`. It does not re-derive. A shared JSON corpus of two languages is the sort/paint split across a process boundary; we do not do that.
3. **Registry pending is the only interactive state.** Authoritative + no covering live row → never Approve. Non-authoritative → omit chrome. Terminal **labels** come from the part stamp **inside the Python function**, never from JS reading `part.state`.
4. **Click is a bounded overlay, not a stamp that beats the registry.** Optimistic `inflight` until the 200 view arrives. Cleared by **200, 409, 4xx, network catch, or 15s then resync**. Never “whenever a snapshot happens to arrive.”
5. **One submit path, many mounts, zero extra cards.** Web / dashboard / TUI → `POST /api/approve/{thread_id}`. Gateway slash/buttons already `gate_claimed_for_thread`. Swarm FanOut stays tri-state (H-12). Alpine **card** is deleted only when test 2 is green in that PR. Client countdown does **not** write `timeout` into the document; `hitl_timeout.py` is the only timeout author.

A PR that cannot say how **every hand in §6** gets the same answer is another bubble patch. Stop and re-scope.

---

## 5. The function (the table both the corpus and the code implement)

`kazma_ui/gate_view.py` — `resolve_gate_views(parts, live_rows, *, authoritative) -> list[GateView]`

Each `GateView`: `{ gate_id, interrupt_id, tool, kind, state, interactive, slot }`

- `slot`: `pending` (below the answer) or `settled` (above the answer)
- `interactive`: live buttons, composer lock
- `state`: what the card is **labelled**

Match a part to a live row by `interrupt_id` / `gate_id` / alias (two-id rule). `live_rows` is `LIVE_STATES` only.

| Inputs | `state` | `interactive` | `slot` |
|---|---|---|---|
| Covering row `pending` | `pending` | true | pending |
| Covering row `claimed` or `resuming` | `inflight` | false | settled |
| Covering row terminal (`timeout` / `error` / settled-with-decision) | that outcome | false | settled |
| `authoritative=true`, **no** covering row, part **pending** | `error` | false | settled |
| `authoritative=true`, **no** covering row, part **terminal** (`approved` / `denied` / `timeout` / `error`) | **that stamp** (Approved vs Denied, not “resolved”) | false | settled |
| `authoritative=false` (or live list missing) | **omit** the gate from the plan | — | — |
| Optimistic overlay (client only, not this function) | `inflight` for that id until §4 clears it | false | settled |

**Interactivity is never taken from the part stamp.** The stamp may label a gate that is already known not-interactive.

### Corpus (must exist before A0 code)

Disagreement cases — the ones sixty default-resolver fixtures missed:

1. Part `pending`, row `claimed` → inflight, **above** answer (2026-09-19 screenshot).
2. Part `approved`, row still `pending`, **no** overlay → pending, **below** answer, live buttons (never invent Approved).
3. Same as 2, **with** overlay from this tab’s 200 → inflight, above answer.
4. Authoritative, no row, part `pending` → error chrome, no buttons (ghost).
5. Authoritative, no row, part `approved` → **Approved**, above answer, no buttons. **Not** generic resolved. **Not** pending.
6. Authoritative, no row, part `denied` → Denied, above answer, no buttons.
7. Non-authoritative, part `pending` → omitted.
8. Two parts, two rows, one pending one claimed → one below, one above, ask order preserved.

`tests/test_gate_view.py` is the SoT. There is **no** parallel JS table.

### Where it runs

| Mouth | Call site | What the client receives |
|---|---|---|
| Hydration | `GET /api/chat/sessions/{id}/messages` (`sse_chat/__init__.py` `get_session_messages`) | Each HITL part has `view` stamped at read time |
| Live snapshot | `GET …/status` | `gate_views` + `gates_authoritative`. **No** `gates` after A1 |
| Live frames | journal emit of `hitl` (`delivery.py` / SSE/WS) | event carries `view` (server has the registry at emit) |
| Click | `POST /api/approve/{thread_id}` 200 and 409 | body carries the updated `view` (and `gate_id`) |

Old persisted parts without `view` get it at **read**, not via a client fallback.

JS paint rule, total:

```text
view = live_gate_views[interrupt_id] ?? part.view
if (!view) omit HITL chrome
else TurnView paints view.state / view.slot / view.interactive
```

No `part.state`. No `_hitlDisplayState`. Overlay is a map `id → inflight` with the §4 clear rules, applied **after** lookup, never as a competing table.

---

## 6. Related parts (every hand)

| Hand | Today | After this series |
|---|---|---|
| Web inline card | TurnView + impure `_hitlDisplayState` | TurnView paints `view`. Only DOM writer for the bubble |
| Alpine strip (`chat.html` ~90–108) | Second `.hitl-approval-card`, no interrupt id | **Deleted in C**, and only if test **2 is green in C** |
| Dashboard (`hitl_approval.js`) | Own HTML, 5s poll, **one card per thread** | Same pending list. Dedup by **gate_id / alias**, not `thread_id`. Same POST |
| TUI (`kazma_tui/app.py`) | Same poll, `_shown_approvals` by thread | Same list + same POST. Native modal stays. Key shown-set by gate id |
| Telegram / Discord / Slack | Text + keyboard, `hitl approve {thread_id}` | Unchanged mouth. Graph has one open interrupt; thread-id claim is correct |
| Swarm bus / FanOut | In-memory Events + tri-state | Registry row already. **Do not** retarget web `claim_gate` |
| Pipeline checkpoints | `pipeline-{task}-step{n}` | Same registry, swarm panel only |
| Semantic cards | Same class, option buttons | Same `view`, `kind` field |
| Composer / steer / abort | Mix of `_awaitingApproval`, registry lock, DOM | Lock ⇔ any view is `interactive` |
| Live Task Card jump | DOM scan for enabled buttons | Jump to the pending slot key |
| Voice turn | `hitl_thread_status` | Unchanged helper |
| YOLO / Allow-tool | Grants suppress **new** cards | Snapshot drops those rows everywhere at once |
| Countdown | Client can stamp document `timeout` | Remaining-time CSS only. Zero is not a verdict |
| Recovery | `_resyncDelivery` / attach / `recoverMissedApproval` can decline | Catch-up **never** skips because a card “looks” settled |
| Delivery journal | SSE/WS journal `hitl` parts | Unchanged journal. Frames **carry** `view` |

**Do not flatten mouths that are already honest.** Gateway sequential approve-by-thread is more honest than the web client’s five ambient flags. FanOut first-wins was a named incident (H-12). This plan shares **identity**, not **UX**.

Dashboard and TUI do **not** need slot order. They poll `/api/pending-approvals` and show pending-or-not. They are not a reason to implement the table twice.

---

## 7. What stays

- `hitl_gates.db` CAS and `hitl_gate_bridge.py`
- `hitl_thread_status` / `close_turn` (pending row keeps the turn open)
- TurnDocument parts keyed `hitl:<interrupt_id>`
- `modules/turn_view.js` keyed slots, declared order, invariant reporter
- Journal + cursor resume
- `POST /api/approve/{thread_id}` as web/dashboard/TUI submit
- Platform-native keyboards
- FanOut tri-state
- `test_dom_movers_stay_deleted`
- `isPendingGate` **staying gone**

TurnView **is** the keyed reconciler. Finishing the model above it is the move. A React rewrite of `chat.js` in this series would cripple every other hand.

---

## 8. What this series deletes (same commits, not “later”)

If a PR cannot delete at least one of these (once that PR’s turn has come), it is not this series.

- `_hitlDisplayState`
- `_hydratingSession` as a HITL fail-posture (`awaiting` branch)
- `decided_locally` (flag, hydrate strip, display override)
- Alpine `pendingApproval` **card** (`_showStoreApproval`). Composer may still pause from `interactive`
- `_hitlAlreadyClaimed` **DOM** half
- Client `applyTurnEvent({ state: 'timeout' })` from the ticker / `markApprovalTimedOut`
- `_reconcileHitlCardsWithGates` / `setCardState` / abort **className** writes — next `renderTurn` is the stamp
- `hasInlineApprovalCard()` as recovery or `endTurn` condition
- Dashboard `seenTid[thread_id]` collapse of **distinct** live gates (keep two-id alias collapse)
- `/status.gates` (dropped at A1; `live_gates()` stays internal)
- Source-grep as the **primary** lock for display state (keep grep for deleted movers and “`_hitlDisplayState` is gone”)

`rebuild()` may remain as TurnView internals. It is not a product state and must not be required for refresh-mid-pause once A1 lands.

Leftover, **not** A1: the singular `hitl` object on `/status`. Kill in E or a one-line follow-up once `gate_views` is the live question.

---

## 9. `/status` wire

Today (`sse_chat/__init__.py`):

```json
{ "generating", "paused", "hitl", "gates", "gates_authoritative" }
```

`gates` is `live_gates_async` rows. Chat.js is the only JS consumer (`_serverGates`). Dashboard/TUI do not read it.

| PR | Wire |
|---|---|
| **A0** | **Add** `gate_views`. Keep `gates`. Old clients ignore the new field |
| **A1** | Chat consumes `gate_views`. **`gates` dropped from the JSON.** |

Keeping both on the wire is two representations of one fact on one endpoint — the sort/paint split again. This is the operator app’s own `/status`; there is no external contract worth a compatibility window.

Internal `paused` computation may still inspect live rows on the server. That is not an API.

---

## 10. Implementation (ordered PRs)

Each PR is mergeable the same day. Do not skip to C. Do not wait for four Playwright specs that cannot pause the app graph.

### F0 — Proof that can exist now + harness spike

**Hands:** none behavioral. Chat unchanged.

- `tests/e2e/test_hitl_view_model.py`: tests **2** and **3** against **current** `main` (seeded persist + `register_gate`, real Chromium, in-process uvicorn — same fixture family as `test_delivery_v2_e2e.py` / `test_smoke.py`). They may fail. That is the point.
- Spike: **no** (see §3). `tests/test_f0_hitl_app_graph_spike.py` records the verdict and must not skip. Playwright 1/4 stay unclaimed.
- CI: do **not** gate the main suite on red 2/3 yet. Land the file. The gate turns on when A1 (or whichever PR is supposed to go green) lands.

**Files:** `tests/e2e/test_hitl_view_model.py`, maybe a fixture helper. No product code unless the spike needs a test-only seam — prefer none.

### A0 — Function + corpus + additive wire

**Hands:** chat/dashboard/TUI/gateway/swarm **untouched**.

- `kazma_ui/gate_view.py` + `tests/test_gate_view.py` (full corpus, including cases 5–6).
- `/status` **adds** `gate_views` (computed from current turn parts + `live_gates`). Clients ignore it.
- `/messages` **adds** `view` on HITL parts at read. Old clients ignore unknown keys on parts.
- Journal emit **may** start attaching `view` (forward compatible). Chat still uses `_hitlDisplayState`.

**Files:** `kazma_ui/gate_view.py`, `sse_chat/__init__.py` (status + messages), `delivery.py` or SSE emit site, `tests/test_gate_view.py`, this plan.

### A1 — Join before paint; consume `gate_views`; drop `gates`

**Hands:** chat hydration. Dashboard/TUI/gateway still ignore `gate_views`.

- `loadSession`: `Promise.all` of `/messages?stats=1` and `/status`. Set live views **before** the first `TV.render`.
- Delete HITL use of `_hydratingSession` / `awaiting`.
- `gateState` adapter: lookup §5 JS paint rule only.
- Drop `gates` from `/status` JSON. `_serverGates` becomes `_serverGateViews` (or equivalent). Update `tests/test_delivery_v2_client.py` / steer-composer greps in the **same** PR.
- Tests **2** and **3** go **green** here if they were red. If they do not, A1 is not done.

**Files:** `chat.js` (`loadSession`, `_resyncDelivery`, `_turnRenderers`), `sse_chat/__init__.py`, `base.html` only if a new module is needed (prefer no new JS module — consume JSON), tests.

### B — Approve 200 carries the view; delete `decided_locally`; ticker is not an author

**Hands:** sequential web + test **4** if F0 harness said yes.

- `approve_tool` 200 and 409 include `view` + `gate_id` (same shape as `gate_views[]`).
- Click path: merge that view, drop overlay, `renderTurn`. No `decided_locally`.
- Overlay clear: 200 / 409 / 4xx / catch / 15s→resync.
- Countdown at zero: freeze remaining-time label only. `markApprovalTimedOut` must not `_noteGateDecided(..., 'timeout')`.
- Delete hydrate strip of `decided_locally` once no writer sets it.

**Files:** `routes_direct/misc.py`, `chat.js`, `turn_document.js`, tests that currently require `decided_locally`.

### C — One card; TurnView is the only bubble writer

**Merge gate: test 2 is green in this PR.** If not, do not delete Alpine.

- Remove `chat.html` Alpine strip. `_showStoreApproval` deleted. Composer pause from `interactive` only.
- `setCardState` / abort / fossil reconcile: events → `renderTurn` only. No `card.className = …` outside `_paintHitlSlotCard`.
- `hasInlineApprovalCard` unused by recovery / `endTurn`.
- `agentStore._pauseForApproval` must not mint a second card.

**Files:** `chat.html`, `chat.js`, `stores/agentStore.js`, `tests/test_chat_steer_composer.py`, `tests/test_audit_wave8.py` if it pins the strip.

### D — Dashboard and TUI: same list identity

**Hands:** chat + dashboard must agree on sequential.

- Dedup pending list by `gate_id` (and alias), **not** `thread_id`.
- Send `gate_id` / `interrupt_id` on POST when present.
- TUI `_shown_approvals` keyed by gate id.
- Test **5** here if cheap; else with F.

**Files:** `hitl_approval.js`, `kazma_tui/app.py`, `hitl_gate_bridge.py` if `gate_id` is missing from pending items.

### E — Recovery cannot decline

**Hands:** gateway and swarm unchanged.

Remove or invert early-returns when the job is catch-up:

- `_resyncDelivery` returning solely because `_streamIsLive()` or a card “looks” live when durable messages/views still need projecting (keep “don’t paint the **previous** turn’s answer over a live pause” as a **document** check)
- `onError` skipping resync because `_awaitingApproval`
- `_hitlAlreadyClaimed` dropping frames from DOM shape
- `recoverMissedApproval` no-op on omitted/frozen chrome
- Attach reopen budget leaving a **paused** thread with no stream

`hasLiveGate()` = any view `interactive`. Test **1** here if F0 harness said yes.

**Files:** `chat.js`, `tests/test_approval_reattaches_the_stream.py`, `tests/test_delivery_reconciles.py`.

Kill singular `/status.hitl` here or immediately after, once nothing reads it.

### F — CI gate; grep demoted; diagnosis map

- Playwright job next to smoke. **No** `|| true`. `importorskip` only when Chromium is absent, same as smoke. Gate on whatever of 1–4 F0 declared harnessable; 2 and 3 are mandatory.
- Source-grep keeps movers-deleted and `_hitlDisplayState` gone. It is not the proof of display-state correctness.
- Update `docs/docs/ops/diagnosis-map.md` HITL row in this PR (multi-path element rule): join-before-paint, `gate_views`, which mouth submits where.
- `CHANGELOG.md`.

The word ban lifts when this job is green.

---

## 11. Proof map

| Live incident | PR that makes it unrepresentable | Test |
|---|---|---|
| Approved card under the reply | A0 table + A1 consume | corpus 1 + TurnView |
| Refresh: “Waiting…” under a delete that already ran | A1 join | **3** |
| Refresh mid-pause: no buttons | A1 (no awaiting freeze) | **2** |
| Sequential approve, silence until refresh | E | **1** if harnessed |
| `/status` still pending after click, card jumps down | B | **4** if harnessed |
| Countdown stamps approved gate timed out | B | unit + **3** |
| Alpine twin / ghost strip | C | **2** (exactly one live card) |
| Dashboard hides second question by thread | D | **5** / dashboard unit |
| Green grep, red live install | F | CI job |

---

## 12. Non-goals

- Rewrite `chat.js` in React/Vue this series.
- Per-gate Telegram slash unless the graph is actually multi-pending on one thread. Today it is not.
- Making FanOut first-claim-wins or making web claim tri-state.
- A new event stream for gates (P6: gates ride the existing turn journal; this plan only **stamps `view` on those events**).
- Multi-replica `hitl_gates.db`.
- Two-language corpus “so they don’t drift.”
- Painting HITL during the loading spinner.
- Calling F0 “industrial.”

---

## 13. Suggested commit subjects

- F0: `test(hitl): refresh mid-pause and after settle (may fail)`
- A0: `feat(hitl): resolve_gate_views at the wire; corpus includes settled-no-row`
- A1: `fix(hitl): join transcript and registry before the first paint`
- B: `fix(hitl): approve response carries the view; the ticker is not an author`
- C: `fix(hitl): one card; TurnView is the only bubble writer`
- D: `fix(hitl): dashboard and TUI key pending rows by gate, not thread`
- E: `fix(hitl): catch-up cannot decline a live pause`
- F: `test(hitl): view-model Playwright is a CI gate`

---

## 14. Operator / agent rules after landing

- A PR that adds a hydrate flag, a local bit that outranks the registry, or a second card “for safety” is rejected.
- `resolve_gate_views` is the only place “what state is this gate in” may be answered. A new surface consumes `view` / `/api/pending-approvals` / `/status.gate_views` and submits through `/api/approve`.
- Never start or restart the operator’s live server as part of this work. Playwright boots in-process. Deploy remains: commit to `main`, operator pulls `C:\Users\balfa\kazma`, `kazma_guard --reload`.
