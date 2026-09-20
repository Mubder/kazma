# Unified turn block — Phase 0 baseline report

Date: 2026-09-20

Status: Phase 0 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
This document records what the code does **today**. It claims no behavior
change. Every statement below is a repository observation with a file and
line reference; where a question could not be answered by inspection it is
recorded as OPEN rather than guessed.

---

## 1. Method and limits

Established by reading the repository at `afbd22dd` plus the uncommitted
plan. Not established: live incident reproduction against the operator's
install (out of bounds — the live clone is read-only), and timing/latency
numbers (Phase 4 measures those).

Where this report says "not persisted", it means no write site was found on
that path; the reproduction tests in §7 are what turn that into evidence.

---

## 2. Producer inventory (server → wire)

### 2.1 Frame producers

| Frame | Emitted by | Notes |
|---|---|---|
| `status`, `status_update` | `sse_chat/_streaming.py:439`, `:773` | Free-text phase titles, become `status` parts. |
| `token` | `sse_chat/_streaming.py:694`, `:696`, `:887`, `:892`, `:1013`, `:1028`, `:1205` | Seven distinct token emitters: live delta, separator, checkpoint backfill, notices, recovery text. |
| `turn_heartbeat` | `sse_chat/_streaming.py:495`, `:636` | Liveness only; feeds the stall detector in the bottom bar. |
| `tool_*` / progress | `sse_chat/_streaming.py:729`, `:749`, `:764` | Tool lifecycle. |
| `context_compacted` | `sse_chat/_streaming.py:801` | Aggregate notice. |
| `error` | `sse_chat/_streaming.py:843`, `:1220` | Excluded from resume replay (`delivery.py:REPLAY_SKIP_TYPES`). |
| `done` + `turn_complete` | `sse_chat/_streaming.py:1170`, `:1171` | Two frames, one payload. `turn_complete` has replace semantics (AGENTS.md §31B). |
| `snapshot` | `sse_chat/_streaming.py:1183` | Post-terminal snapshot info. |
| HITL frames | `hitl_approval.py`, `hitl_gate_bridge.py`, gate views via `gate_view.py` | Decision authority is the gate registry, not the frame. |

All of the above funnel through **one** choke point: `TurnBroker.emit`
(`delivery.py:220`+), which stamps a monotonic per-thread `seq` and fans out
to every bound socket and subscriber queue.

### 2.2 Transports

- SSE: `sse_chat/__init__.py` (2110 lines), `_streaming.py` (1337).
- WS: `routes/ws_chat.py`.
- Gateway graph: `kazma_gateway/agent_handler/graph.py`.

All three close through `turn_runtime.close_turn` (AGENTS.md §31A, §31D).

### 2.3 Canonical gate view

`kazma_ui/gate_view.py` (521 lines) is the server-side resolver that answers
"what state is this gate in". `hitl_status.py` (297) is the single status
helper `close_turn` consults. `hitl_gate_bridge.py` bridges registry rows to
turn parts (`ensure_paused_gate`).

---

## 3. Projector inventory (client state)

| Concern | Location |
|---|---|
| Pure event reduction | `static/js/modules/turn_document.js` — `applyEvent` is the only mutator, `eventKey` dedupes. |
| Part identity | `turn_document.js:partKey` / `interruptIdOf`; mirrored in `turn_document.py:_part_key`. |
| Gate merge ordering | `turn_document.js:HITL_RANK` + `mergeHitlPart`; mirrored in `turn_document.py:merge_hitl_part`. |
| Activity derivation | `turn_document.js:activityOf(parts, gateState)`. |
| Cursor / resume | `static/js/modules/delivery_cursor.js`, `static/js/streaming.js`. |
| Per-turn document store | `chat.js:29` — `var _docs = {}`. |
| Live turn identity | `chat.js:20` — `_liveTurnId`; `chat.js:23` — `_retiredTurnIds` (cap 32). |

### 3.1 Known second authorities in the projector layer

1. **`HITL_RANK`** (`turn_document.js:127`) is a *client-side rank table for
   authorization transitions*. §6.5 of the plan forbids exactly this. It is
   currently load-bearing: `mergeHitlPart` refuses a lower-ranked stamp.
2. **`chat.js:_serverGateViews` / `_serverGatesAuth`** (`chat.js:1285`,
   `:1286`) is a parallel gate store that `_hitlDisplayState` (`chat.js:7846`)
   joins against the document. That join is correct in intent (§5 of the
   plan wants exactly one resolver) but it lives in the page, not in a
   projector module.
3. **`_hitlOverlay`** (`chat.js:1399`, 15 s TTL) is a third, time-bounded
   opinion used to outrank a stale registry row.

---

## 4. Painter inventory (client DOM)

### 4.1 Turn block renderer

`static/js/modules/turn_view.js` owns one assistant bubble's
`.message-content` child list. `slotPlan` (`turn_view.js:141`) emits:

```
[workbench] [settled gate…] [text] [pending gate…] [chrome]
```

— i.e. **one top-level slot per gate**, which is the layout contract the
unified plan replaces with one keyed approval region (§5, §9).

Adapter in `chat.js`:

| Slot | build | paint |
|---|---|---|
| `text` | `chat.js:7721` (inline) | `_paintTextSlot` `chat.js:7608` |
| `workbench` | `_buildRestoredWorkbench` `chat.js:4921` | `_paintWorkbenchSlot` `chat.js:7647` |
| `hitl:<id>` | `_buildHitlSlotCard` `chat.js:7865` | `_paintHitlSlotCard` `chat.js:7889` |

`renderTurn` `chat.js:8074` is the entry point; `applyTurnEvent`
`chat.js:8122` is the event→document→render pipeline.

### 4.2 The separate status bar

Markup: `templates/chat.html:99-116` — `#live-task-card` with header,
toggle, phase, label, meta, chevron, an `aria-live` region, Review / Retry /
Stop buttons, and a `#live-task-body` step log. Plus `#thinking-indicator`
at `templates/chat.html:121` (legacy, hidden).

Controller: `chat.js:1572-2171` — an independent state machine
(`_tc` at `chat.js:1579`) with its own mount, phase icon/label, elapsed
clock, stall detector (`_TC_STALL_MS = 20000`), announcer, tick timer,
terminal test, and step projection `_tcStepsFromDoc` `chat.js:1872`.

CSS: `static/css/kazma.css:2258-2426`.

Tests that lock it in place: `tests/js/test_live_task_card.js`,
`tests/test_chat_as_product.py:259-260`,
`tests/test_chat_steer_composer.py:840`, `:890`, `:908`.

This is the "second status surface" the plan removes. It duplicates phase,
elapsed, step count and Stop — all of which the unified header owns.

### 4.3 Forced-open thoughts

`_paintWorkbenchSlot` `chat.js:7647`:

```js
if (!done) {
  panel.classList.remove('is-collapsed', 'kazma-cot-restored');
  …chevron = '▾'; header.aria-expanded = 'true';
}
```

and `build` for the workbench slot (`chat.js:7727`) does the same on
creation. Every live paint therefore re-opens the panel, discarding a user
collapse. This is `afbd22dd` ("live CoT fold opens") working as written and
is what invariant **U08** forbids.

`_collapseFinishedWorkbenches` `chat.js:2222` folds *previous* turns at
`beginTurn`, which is the only collapse authority today.

### 4.4 `tokenAccum` — the second text authority

24 references, `chat.js:19` and 23 call sites. Roles found:

| Kind | Lines |
|---|---|
| Reset to `''` | 1308, 2240, 2416, 3240, 3273, 6039, 6242, 7218, 8177, 8303, 8334 |
| Written from a paint | 5218 (`_paintLiveTextNow`), 7612 (`_paintTextSlot`), 8051 (`_forcePaintDoneContent`) |
| **Read as a decision input** | 2558 (`replyPainted`), 3361, 3537, 3543 (terminal "did anything paint?" guards), 5217 (fallback text source), 5227/5242 (paint source) |

The reads at 2558/3361/3537/3543 are the reason removal is not a one-line
change: they are "has this turn produced visible content" guards used to
decide whether to synthesize a recovery notice. Replacing them requires a
document selector (`_answerFromDoc`, `chat.js:7528`) at each site.

### 4.5 Other writers into the bubble

- `appendMessage` `chat.js:5024` — history hydration; creates
  `.message-text`, approval cards and workbench markup that `turn_view.adopt`
  then claims.
- `renderHitlCard` `chat.js:5973` — 430-line legacy card builder still
  reachable outside the slot path.
- `_collapseClaimedHitlCard` `chat.js:5891`, `_revealHitlCard` `chat.js:5933`
  — imperative card mutators.
- `ensureProgressPanel` `chat.js:4024`, `logProgress` `chat.js:4634`,
  `finalizeProgress` `chat.js:4818` — the pre-V2 progress panel path.

Each of these is a candidate "competing turn-content writer" for the Phase 2
exit check.

---

## 5. Persistence and crash-window report

### 5.1 Write sites

| Site | When | What |
|---|---|---|
| `turn_runtime.persist_reply` `turn_runtime.py:105` | Called by every terminal/pause path | Upserts one row keyed by `(session_id, reply_turn_id)` through `reply_sink.upsert_reply`. |
| `turn_runtime.close_turn` `turn_runtime.py:193` | After every graph settlement | Reads the checkpoint, consults the gate registry, decides open/paused/done, then calls `persist_reply`. |
| `sse_chat/_streaming.py:1128`, `:1212` | Terminal / error tail of the SSE pump | Terminal write including `_hitl_persist_parts`. |
| `sse_chat/_streaming.py:208` | `stamp_hitl_part_state` | Stamps one gate's state onto the stored parts. |
| `sse_chat/_persistence.py:99` | Detached-pump done-callback | `close_turn` for a turn whose browser left. |
| `sse_chat/_persistence.py:266` | `_checkpoint_backfill_unanswered` | Session-load heal from the checkpoint. |
| `routes/ws_chat.py:635`, `:759`, `:1856` | WS terminal / pause | Same sink. |
| `hitl_gate_bridge.py:252` | Gate backfill | Same sink. |

### 5.2 Cadence — the durability gap

**There is no write site inside the token loop.** Searching every
`persist_reply` / `upsert_reply` caller finds only turn-boundary writes:
terminal, pause, detached-done, and heal. Between "first token" and "pause
or terminal", the only copy of the turn's presentation state is:

1. the in-process journal (`delivery.py` — explicitly "process-local memory
   only", docstring line 27), and
2. the LangGraph checkpoint, which holds **messages**, not emitted reasoning
   summaries, tool activity rows, or partial answer text as a presentation
   record.

### 5.3 Crash windows

| Window | Lost on restart | Evidence |
|---|---|---|
| Between first token and first pause/terminal | Partial answer text, all activity rows, all reasoning | No write site in the loop (§5.2); journal is memory-only (`delivery.py:27`). |
| Between `interrupt()` and the pause write | The gate is durable (registry row is written by the bridge) but the *presentation* parts for that pause may not be | `close_turn` writes after the settlement; `ensure_paused_gate` backfills the row. |
| Between gate settle and resume write | Decision durable (registry), activity since the pause not | Registry is a separate store; `close_turn` reconciles at the next settlement. |
| Detached pump crash | Everything since the last boundary; `_checkpoint_backfill_unanswered` can recover *final text only* | `_persistence.py:153-276` recovers `asst` text, not activity or reasoning. |

### 5.4 Reasoning durability

`persist_reply` derives parts via
`turn_document.parts_from_stream(streamed, final, activity)`. Reasoning is
produced only by `split_stream_and_final` **displacement** — i.e. streamed
text that the final answer does not prefix becomes a `reasoning` part. There
is no path that stores a provider's reasoning summary as such.

Consequence for the plan's §3 ("Do not manufacture unavailable model
reasoning"): on restart, historical turns can honestly show displaced
narration and tool activity. Anything else must be labelled unavailable.

### 5.5 Cross-store atomicity

Three stores participate and none share a transaction:

1. `hitl_gates.db` — decision authority (`kazma_core/safety/hitl_gates.py`).
2. The LangGraph checkpointer — execution authority.
3. SessionStore via `reply_sink` — presentation record.

`close_turn` is the reconciler: it reads (1) and (2), then writes (3).
Ordering is read-registry → read-checkpoint → write-session. A crash after
(1) and before (3) leaves a durable decision with a stale presentation row;
the next `close_turn` or session load reconciles it. A crash after (3) and
before a registry settle leaves a pending row that
`close_turn`'s orphan-settle branch (`turn_runtime.py:276-286`) clears.

**Not proven by inspection:** that every one of these windows actually
recovers. Phase 1 must test each.

---

## 6. Protocol / compatibility inventory

Fields present today:

| Semantic (plan §6) | Present as | Gap |
|---|---|---|
| Session / thread ID | `session_id`, `thread_id` | — |
| Stable turn ID | `turn_id` / `reply_turn_id`; client placeholder `'live'` until stamped (`chat.js:_bubbleForTurn`) | Placeholder promotion is client-side only. |
| Message identity | Session row keyed by `turn_id` | — |
| Stream epoch / cursor scope | `chat.js:_sseEpoch` (`:353`), `_lastSeqSeen` (`:356`) | Epoch is client-local; not on the wire. |
| Ordered delivery sequence | `seq` from `TurnBroker.emit` | — |
| **Authoritative document revision** | **absent** | Only `seq` exists. §6 forbids treating a delivery counter as a document revision. |
| **Snapshot completeness / coverage** | `snapshot` frame (`_streaming.py:1183`) carries info, not a declared covered revision | Gap. |
| Server lifecycle state | `open` / `pending` / `interrupted` flags | No single enum. |
| Timestamps | `ts` on some parts | Elapsed is client-derived (`_tcElapsed` `chat.js:1718`). Plan §3 requires server timestamps. |
| Reasoning / activity IDs | **absent** — `partKey` derives identity from content (`turn_document.js:107-125`) | Gap: a tool row's key embeds name+state+first 80 chars of result, so a result change re-keys the row. |
| Tool-call IDs | Present in the graph (`ToolCall.id`), **not** carried into parts | Gap. |
| Gate IDs | `interrupt_id` end to end | — |
| Canonical gate view | `gate_view.py` | Coverage for historical rows is OPEN. |
| Explicit event kind | Inferred from `type` in `applyEvent` | No delta/upsert/snapshot/replace discriminator. |
| Schema version | **absent** | Gap. |

Legacy normalization already exists in one place:
`turn_document.js:hydrateMessage` / `turn_document.py:hydrate_message` plus
`legacy_turn_id`. That is the adapter §6.7 requires; it must not be
duplicated.

### 6.0 What Phase 1 closed

The table above is the Phase 0 baseline and is left as written. For current
state, these gaps are closed:

| Gap | Closed by |
|---|---|
| Authoritative document revision | `rev` on the durable row, bumped by `reply_sink.upsert_reply`, refused when stale by `turn_document.js:applyEvent` (U05) |
| Schema version | `turn_document.TURN_SCHEMA_VERSION`; unversioned rows read as schema 1 |
| Reasoning / activity IDs | every `activity_of` row carries `id`, the part's own key |
| Tool-call IDs | `_streaming.py` stamps the graph's `run_id` on `tool_call`/`tool_result`; the part key is `tool#<id>` |
| Snapshot replaces vs merges | a hydrate now merges; it covers only what was durable when taken |

Still open at the end of Phase 1a/1b: explicit snapshot **coverage
declaration** (a partial snapshot cannot yet say what it covers), an
explicit event-kind discriminator, and everything in §5 (durable
publication).

### 6.1 Measured cross-language divergences

Found in Phase 0 by running both implementations over the same fixtures (all three are closed as of Phase 1a)
(`tests/fixtures/unified_turn/messages/`, driven by
`tests/test_unified_turn_fixtures.py`). Both suites were green before this;
neither had an example that made the other disagree.

| Divergence | Python | JavaScript | Assessment |
|---|---|---|---|
| `legacy_turn_id` for the same row | `legacy-879abd24dca7291f` (sha256[:16]) | `legacy-9587b3b6` (32-bit string hash) | **Real.** Two implementations of one identity function. Only reachable where the server did not hydrate first (`sse_chat/__init__.py:1711` does hydrate `/messages`); where it is reachable, one stored message can bind under two turn ids, which is invariant U01. Owned by Phase 1 and locked by a strict-xfail. |
| Activity row `ts` when absent | key omitted | `ts: null` | **Benign but recorded.** Both are falsy to every reader. The comparator drops nulls on both sides; the canonical shape in the fixtures omits the key. Phase 1 should make JavaScript omit it when it introduces stable activity IDs, since the two changes touch the same rows. |
| Unknown part type fallback key | `(kind, repr(part)[:80])` | `kind + ':' + JSON.stringify(part).slice(0,80)` | **Latent.** No modelled type reaches it and no fixture exercises it. Phase 1 aligns or deletes the branch rather than leaving two spellings of one key. |

Everything else the fixtures cover — text selection, part keys for every
modelled type, gate identity/order, and activity row content — agrees
exactly.

---

## 7. Reproduction status

Phase 0 reproductions live in `tests/test_unified_turn_block_phase0.py` and
`tests/js/test_unified_turn_block_phase0.js`. They assert the **target**
behavior and are currently `xfail(strict=True)` / recorded-red, because
committing an unconditionally red suite to `main` would stop every unrelated
change.

`strict=True` is the honest form: if the behavior lands accidentally the
suite fails and forces the marker off. **These markers are removal
obligations of Phases 2 and 3, not acceptance.** Plan §13 forbids treating
xfail as acceptance, and no acceptance claim is made here.

| Repro | Target invariant | File |
|---|---|---|
| No `#live-task-card` in the chat template | U02 | `test_unified_turn_block_phase0.py::test_no_separate_live_task_card` |
| No independent task-card controller in `chat.js` | U03 | `::test_no_independent_task_card_controller` |
| `slotPlan` emits one `approvals` region, not one slot per gate | U02, U09 | `test_unified_turn_block_phase0.js` |
| Live paint does not re-open a collapsed workbench | U08 | `test_unified_turn_block_phase0.js` |
| `tokenAccum` is not read as a content decision | U06 | `::test_no_token_accum_content_decisions` |

---

## 8. Harness status

`tests/e2e/test_hitl_view_model.py` seeds gates **directly into the registry
and the session store**; it never makes the graph pause. Its own docstring
records why: "F0 found that `create_app()` does not pause from preloaded
`tool_calls_pending`."

The Phase 0 harness is `tests/e2e/_unified_turn_harness.py`, driven by
`tests/e2e/test_unified_turn_app_graph.py`. It scripts the provider
boundary (`LLMProvider.chat` / `chat_stream`), which `AGENTS.md` §3 names as
the single OpenAI-compatible path all transports share, and nothing else.
The script asks for four canonical danger tools
(`safety/hitl.py:CANONICAL_DANGER_TOOLS`), so the real
`graph_tool_worker.py:1073` `interrupt()` fires, and every resume is a real
`POST /api/approve/{thread_id}` followed by the product's own journal
re-attach (`chat.js:_attachJournal`).

**Status: green.** Five tests, 34.6 s on the development machine:

| What it proves | Test |
|---|---|
| Four pauses, four real approvals, one finished turn with the final answer | `test_four_sequential_gates_complete_the_turn` |
| Two `file_write` pauses keep distinct gate ids | `test_two_gates_can_share_a_tool_and_stay_distinct` |
| A denial at step 2 does not end the turn | `test_denial_does_not_end_the_turn` |
| An approved `file_write` really writes | `test_approved_tools_actually_execute` |
| Every gate left a settled registry row; none stayed non-terminal | `test_gates_are_registered_and_settled` |

Sequential approval therefore works **at the lifecycle level**. No claim is
made about what the operator sees — that is Phase 3/4 and needs the browser.

Harness isolation notes, each of which was a real leak on the first run:

- `KAZMA_DATA_DIR` alone is not isolation. The shipped `kazma.yaml` resolves
  `storage.path` relative to the working directory, so the first run opened
  the repository's own `kazma-data/checkpoints.db`. The harness now writes
  an isolated config and passes it as `config_path`, which also moves the
  `kazma.local.yaml` lookup away from the operator's.
- The shipped config starts a workspace-bound MCP filesystem server against
  the repository root. The harness config has no MCP servers.
- The active LLM profile comes from ConfigStore, not the YAML
  (`model_registry._resolve_provider_config`), so the harness writes the same
  three `llm.*` keys Settings > Models writes, pointed at a loopback URL.
- Approved tool calls really execute, so every path argument is rebased into
  the temporary directory (plan §14.1).

---

## 9. Invariant → verification layer

| ID | Layer | Home |
|---|---|---|
| U01 | 2 renderer, 4 browser | `tests/js/test_turn_view.js`, e2e |
| U02 | 2 renderer, 4 browser | `tests/js/`, `tests/test_unified_turn_block_phase0.py` |
| U03 | 1 static boundary check + 2 | architecture check (plan §13) |
| U04 | 1 pure model | `tests/js/test_turn_document.js`, `tests/test_turn_document.py` |
| U05 | 1 pure model + 3 server | shared fixtures (§6) |
| U06 | 2 renderer | `tests/js/` |
| U07 | 3 server + 4 browser | persistence round trip |
| U08 | 2 renderer + 4 browser | `tests/js/test_unified_turn_block_phase0.js` |
| U09 | 1 + 2 | `partKey` identity tests |
| U10 | 3 server | `gate_view.py` tests |
| U11 | 3 + 4 | `close_turn` tests, disconnect e2e |
| U12 | convergence oracle (§10) | all four sources |
| U13 | 2 + 4 | session-switch e2e |
| U14 | 2 | renderer teardown tests |
| U15 | 3 + 4 | restart recovery |

---

## 10. Supersession actions taken

Notices added to:

- `docs/plans/COT_AND_THOUGHTS.md`
- `docs/plans/TURN_RENDER_V2_KEYED_SLOTS.md`
- `docs/plans/HITL_VIEW_MODEL.md`

Each records which rules are superseded on adoption and which survive.
Historical incident records are retained as history.

---

## 11. Phase 0 exit check

Plan exit: "tests fail for the actual missing behaviors, not fixture/setup
errors; every invariant has an assigned verification layer. No claim that
sequential approval works until the app-graph harness proves it."

| Deliverable | State |
|---|---|
| Producer / projector / persistence / painter inventory | §2–§5, with file and function references |
| Reproduction of split bar, separate gate cards, forced-open thoughts | §7 — 10 strict-xfail assertions, each red for the stated reason |
| Deterministic app-graph harness, four approval/resume cycles | §8 — green, 5 tests |
| Persistence / crash-window report | §5 |
| Protocol compatibility inventory | §6, including three measured cross-language divergences |
| Agreed layout fixture | `tests/fixtures/unified_turn/layout/four_sequential_gates.json` |
| Supersession notices | §10 |
| Every invariant has a verification layer | §9 |

CI: `unified-turn-lifecycle` in `.github/workflows/ci.yml` runs the harness,
the shared fixtures and the reproductions. It is a workflow job, **not yet a
required check** — making it required in branch protection is Phase 5 work
and is recorded there rather than assumed here.

Not claimed by Phase 0: that any invariant U01–U15 holds, that the
persistence gaps in §5 are fixed, or that the UI does anything differently.

---

## 12. Open questions carried into Phase 1

1. Does any provider in use emit reasoning summaries Kazma could store? (§5.4
   says nothing stores them today; whether anything *produces* them is open.)
2. Can `reply_sink`'s row support incremental presentation state at token
   cadence without unacceptable write amplification? (Plan §8 prefers
   extending it; §11 requires measuring it.)
3. What is the authoritative document revision going to be — a new field, or
   a promoted `seq` scoped per turn rather than per thread?
4. Does `gate_view.py` produce a complete view for a historical, already
   settled gate, or only for live rows?
