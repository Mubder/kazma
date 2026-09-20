# Unified turn block — Phase 1 report: state and durable recovery

Date: 2026-09-20

Status: Phase 1 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
Baseline: [`UNIFIED_TURN_BLOCK_PHASE0.md`](UNIFIED_TURN_BLOCK_PHASE0.md).

No UI change. Phase 1 is the layer underneath: identity, revision,
normalization, and what survives a restart.

---

## 1. What Phase 1 changed

| Deliverable (plan §12) | State |
|---|---|
| Stable identities | Done — §2 |
| Explicit snapshot coverage / revisions | Partly — §3. Revisions done; a *declared* coverage field is still open. |
| Normalization + shared protocol fixtures | Done — §2.4 |
| Canonical gate-view coverage, live and historical | Unchanged and already correct — §6 |
| Durable publication where the baseline showed a gap | Done, with an explicitly bounded guarantee — §4 |
| Migration / old-record compatibility | Done — §5 |

---

## 2. Stable identities

### 2.1 One turn id per stored row

`legacy_turn_id` minted a different id in each language: `sha256[:16]` in
`turn_document.py`, a 32-bit string hash in `turn_document.js`. The same
stored row was `legacy-879abd24dca7291f` on the server and `legacy-9587b3b6`
in the browser. Both suites were green the whole time, because each had
written its own examples.

Both sides now run two FNV-1a-32 passes with different offset bases: 64 bits
without 64-bit arithmetic, which JavaScript cannot do exactly. sha256 was
the alternative and would have meant shipping a hash implementation to the
browser for an id nobody verifies. The hash walks UTF-8 bytes, so it agrees
on Arabic and on emoji (surrogate pairs), which for this product is the
normal case rather than the edge case.

Read-side only: the value is recomputed on every load, so changing the
algorithm renames nothing that was stored.

### 2.2 One row per tool call

A tool part was keyed by `name + state + result[:80]`. Two consequences:

- the **same** call changed identity the moment it finished, so the renderer
  tore its row down and rebuilt it — losing expansion and focus — on every
  update;
- two **concurrent** calls to one tool collided on one key, and the second
  overwrote the first.

Tool parts are now keyed by the graph's own run id (`tool#<id>`), stamped by
the producer on both `tool_call` and `tool_result`. Parts with no call id
keep the content-derived key so existing transcripts dedupe the way they
were written.

A stable key means the second stamp of a call arrives as a merge rather than
as a new part, and the old duplicate-key branch dropped those — which would
have frozen every tool row at "running". Hence `merge_tool_part` /
`mergeToolPart`, ranked so a replayed start cannot un-finish a call and an
empty terminal result cannot blank a delivered one.

### 2.3 One id per activity row

Every `activity_of` row carries `id`, the part's own key. The renderer needs
a stable handle to keep an expanded row expanded and a focused control
focused while the row's content changes (plan §3); deriving it in the
document means the row id and the part key cannot drift apart.

### 2.4 Shared fixtures

`tests/fixtures/unified_turn/messages/` is one corpus read by
`tests/test_unified_turn_fixtures.py` and
`tests/js/test_unified_turn_fixtures.js`. All three Phase 0 divergences
(§6.1 of the baseline) are closed, and `known_divergences` is now an empty,
loud escape hatch: a field listed there is excluded from the agreement check
and immediately owned by a `strict=True` xfail, so aligning it turns the
suite red until the record is deleted.

---

## 3. Document revision

`seq` orders **frames** on a thread. Nothing ordered **writes** to one
turn's durable state. `_resyncDelivery` fetches `/status` and `/messages` in
parallel and either can land late; the only thing stopping a stale row from
repainting was that `mergeParts` happens to be additive — which says nothing
about `status`, so an old snapshot could stamp a turn done, or paused, over
the truth. Invariant U05 had nothing behind it.

- `reply_sink.upsert_reply` bumps a monotone `rev` on every successful write
  and stamps `TURN_SCHEMA_VERSION`.
- `applyEvent`'s hydrate branch refuses a snapshot stamped below the revision
  it already holds. The comparison is `<`, not `<=`: an identical hydrate is
  already deduped by `eventKey`, and conflating the content dedupe with the
  revision rule would lose a legitimate repaint.
- The hydrate branch no longer **assigns** `ev.parts` over the document. A
  snapshot covers what was durable when it was taken; tokens streamed since
  are not in it, and an omitted part is ambiguity, not an authoritative
  removal (plan §5, §6.6).
- Rows written before revisions exist read as rev 0 / schema 1, stated rather
  than inferred.

Caught by the app-graph assertion rather than by review: the `/messages`
serializer is a whitelist, so `rev` and `schema` were invisible to the client
on the first run even though the store held them.

**Still open:** a partial snapshot cannot yet *declare* what it covers, and
there is no explicit delta/upsert/snapshot/terminal event-kind
discriminator. Both are §6 requirements and both are carried forward.

---

## 4. Durable presentation — what is and is not guaranteed

### 4.1 The gap

Phase 0 §5.2 found no write inside the token loop. Worse, measured during
Phase 1: **tool activity had no producer at all.** Kazma's tool worker calls
`tool_registry.execute()` directly rather than invoking a LangChain tool
runnable, so `astream_events` emits no `on_tool_*` for it — a turn that
really writes a file produces only `on_chain_*`. Both transports derive
their tool rows from those events (`_streaming.py` `on_tool_start`/`on_tool_end`,
`tracing/events.py` → `tool_lifecycle`), so a finished turn's stored
activity carried gate rows and nothing else.

And the approved work runs on the **resume** leg, which used `ainvoke` and
never registered a delta queue. Measured on the four-gate scenario: all four
tools ran on resume legs, all eight activity events were emitted, and every
one was dropped because no queue was bound to the thread. The operator saw
one backfilled token per leg and no tool rows.

### 4.2 What changed

1. `llm_stream.emit_tool_activity` injects synthetic `on_tool_start` /
   `on_tool_end` into the **same** per-thread queue `emit_token_delta` uses,
   in the **same** vocabulary both consumers already implement. One producer,
   both mouths, no new frame type and no second broker (plan §9). The model's
   own `tool_call_id` rides along as `run_id`.
2. The tool worker calls it around execution. Never raises; never gates the
   tool.
3. The resume leg binds a delta queue and maps the three kinds that can
   appear there. This is not a second copy of the astream event loop and
   must not grow into one.
4. `DurablePresentation` writes incremental state through the same
   `reply_sink` upsert as every other turn write — one durable owner, keyed
   by the same `reply_turn_id`, so a checkpoint and the terminal write
   converge on one row rather than racing.

### 4.3 The guarantee, stated exactly

| Content | Rule | Recoverable after a crash? |
|---|---|---|
| Tool activity (`tool_call` / `tool_result`) | **Persist-then-publish.** Committed before the frame is emitted. | Yes — anything the client was told. |
| Gate decisions | Unchanged: the registry is the authority; the part is stamped on transition. | Yes. |
| Answer text | **Checkpointed** every `DURABLE_TEXT_CHARS` (600) or `DURABLE_TEXT_INTERVAL_S` (2.0 s), whichever first. | Up to the last checkpoint. Tokens published since are **not** durable. |
| Reasoning (displaced narration) | Terminal write only, unchanged. | No, until the turn settles. |

Plan §8: "Do not claim this guarantee if persistence is only periodic." For
text it is periodic, so the strong guarantee is **not claimed**. The bound is
the checkpoint interval, both thresholds are environment-overridable
(`KAZMA_TURN_DURABLE_CHARS`, `KAZMA_TURN_DURABLE_INTERVAL_S`), and the bound
is measured, not asserted in prose — see §4.4.

A failed checkpoint re-queues its parts rather than dropping them, and is
reported. Dropping them would lose exactly the activity the mechanism
exists to keep, and the next checkpoint would have no idea anything was
missing.

### 4.4 Write amplification, measured

`tests/test_turn_durable_presentation.py::test_write_amplification_is_bounded_and_measured`
streams 12,000 characters as 300 token events at the shipped 600-character
threshold with the time trigger disabled:

| | Writes |
|---|---|
| One per token (rejected design) | 300 |
| Shipped: one per 600 chars | **20** |

Per turn, add one write per tool event (two per tool call) and one terminal
write. A four-tool turn with a 3,000-character answer costs roughly
8 + 5 + 1 = 14 transactions, against 1 before this change and ~800 under
per-token persistence.

Latency: activity commits are `await asyncio.to_thread(...)`, so a tool
frame waits on one SQLite transaction off the event loop rather than
stalling the loop. Text commits are on the same path and only on a
threshold.

**Not measured yet:** end-to-end chat latency on a loaded install, and
behavior under a Postgres backend. Plan §11 requires the former before
accepting the design at release; it is carried into Phase 4.

---

## 5. Migration and old records

Additive only. No migration runs, nothing is rewritten.

| Old shape | Read as |
|---|---|
| Row with no `rev` | rev 0 — older than anything, so any newer snapshot wins |
| Row with no `schema` | schema 1 |
| Tool part with no `call_id` | legacy content-derived key, deduped as before |
| Activity row with no `id` | derived from the part on read |
| Row with no `turn_id` | `legacy_turn_id`, now identical in both languages |

A previous build reading a new row ignores `rev`, `schema`, `call_id` and
`id` as unknown keys, which is the behavior it already has for every field
added since. Rollback therefore needs no data step.

---

## 6. Canonical gate view

Unchanged. `gate_view.py` already stamps parts for read via
`stamp_parts_for_read(parts, live_rows, authoritative=...)` on the
`/messages` route, for live and historical rows alike, and the app-graph
harness confirms four settled gates come back with four distinct identities
after a real turn. No Phase 1 change was warranted.

---

## 7. Evidence

| Claim | Test |
|---|---|
| One turn id, both languages, incl. non-ASCII | `test_turn_document.py::test_legacy_turn_id_is_the_shared_value`, `tests/js/test_turn_document.js` |
| One row per tool call; merge advances, never regresses | `test_turn_document.py::test_tool_merge_advances_but_never_regresses` + JS mirror |
| Activity rows carry the part key | `test_turn_document.py::test_activity_rows_carry_the_part_key` |
| Python and JavaScript agree over one corpus | `test_unified_turn_fixtures.py::test_python_and_javascript_agree` |
| Revision is monotone per write | `test_turn_document.py::test_upsert_bumps_the_revision_on_every_write` |
| A stale snapshot cannot regress content or status | `tests/js/test_turn_document.js` (revision block) |
| Activity is persist-then-publish | `test_turn_durable_presentation.py::test_activity_is_durable_immediately`, `::test_the_pump_commits_before_it_publishes` |
| A failed checkpoint keeps its parts | `::test_a_failed_checkpoint_keeps_its_parts` |
| Write amplification is bounded | `::test_write_amplification_is_bounded_and_measured` |
| Tool activity has a producer at all | `::test_tool_activity_has_a_producer`, `::test_resume_leg_binds_a_delta_queue` |
| All of it survives a real four-gate turn | `tests/e2e/test_unified_turn_app_graph.py::test_persisted_row_carries_the_protocol_contract` |

---

## 8. Phase 1 exit check

Plan exit: "model/server convergence and restart tests pass; persistence
guarantees and any bounded limits are documented and measured."

- Model/server convergence: **passing** over a shared corpus, with no
  excluded fields.
- Persistence guarantees: **documented** in §4.3 as two different rules, one
  of which is explicitly not the strong guarantee.
- Bounded limits: **measured** in §4.4.
- Restart tests: **partial.** The crash-window unit tests exist (§7) and the
  durable state is verified after a real turn, but a full
  kill-the-process-and-reboot test is not yet written. It belongs with the
  Phase 4 recovery matrix and is carried there explicitly rather than
  counted here.

Not claimed by Phase 1: any UI change, any invariant U01–U15 beyond U05, or
end-to-end latency under load.
