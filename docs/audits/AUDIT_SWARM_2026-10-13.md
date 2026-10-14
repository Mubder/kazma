# Kazma Swarm audit, merged — 2026-10-13

> Source reports, witness files and logs: `session-artifacts/audits-2026-10-13/` (local working evidence, not committed). A file named below without a folder is there.

This file merges the two halves of the 2026-10-13 Swarm audit into one audit:

- **Engine half:** `session-artifacts/audits-2026-10-13/swarm-engine.md` — lifecycle, recovery, retries, task store, breakers, routing, autoscaler, durable/Temporal paths. Finding IDs `SW-E01`–`SW-E16`.
- **Surfaces half:** `session-artifacts/audits-2026-10-13/swarm-surfaces.md` — Web panel and routes, task stream, HITL bus and platform adapters, agent tools that dispatch swarm work, gateway `/swarm`, template/registry editing, swarm docs. Finding IDs `SW-S01`–`SW-S17`.

It adds no new findings. Every finding keeps its original ID so the witnesses stay traceable. Where this merge checked a claim in the code, it cites `file:line` as read at merge time and marks it "merge check". The tracker for the remediation is `SWARM_INDUSTRIAL_HARDENING_GOAL.md` in this folder.

## Verdict

**The Swarm is not industrial.** The scores below are each auditor's engineering judgment, not benchmark scores or a certification. This merge does not compute a combined score.

| Half | Capability and architecture | Demonstrated industrial readiness |
|---|---|---|
| Engine (`swarm-engine.md`) | 6/10 | about 3/10 |
| Surfaces (`swarm-surfaces.md`) | 6/10 | about 4/10 |

Two gaps decide the verdict.

1. **The approval boundary does not hold for swarm work** (SW-S01, SW-S02, SW-S03). THREAT_MODEL.md:53 names approval as "the boundary that matters". A swarm task started from a chat turn runs danger tools classified HIGH or NONE with no approval on any surface. Swarm-bus approval ids can come from the tool's own arguments, and a settled "yes" is permanent, so a later request that reuses the id is approved without a human. The bus card shows about 100 characters of the arguments and does not say they were cut.
2. **The task lifecycle is not an authoritative state machine** (SW-E01–SW-E04). Status lives in an in-memory dict and is written with unconditional upserts. "Cancelled", "timeout" and "paused" are labels on a task, not control over its execution. Work the system has declared ended keeps running and producing effects; terminal records are overwritten; one task id can execute twice; an approved pipeline that dies mid-resume is offered for approval again and re-executes steps that already ran. The effect-uncertainty design of AGENTS §30G works inside the per-worker retry boundary and is discarded by every path that cuts work short from outside it.

Both halves find the architecture sound in its shape: one engine behind every transport, the §9 module split, a global admission cap on `dispatch()`, effect tracking inside the worker boundary, no blind replay after a crash, a single fail-closed admin decision, platform buttons that check both the allowlist and gateway admin, and a task stream that always ends.

Withhold these claims until the stage gates below pass:
- swarm approvals are a security boundary;
- swarm tasks with mutating tools are safe to run unattended;
- swarm crash recovery is effect-safe;
- the Swarm page is a truthful operator console.

## Scope and evidence

### Reviewed state

Both halves reviewed commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the uncommitted working tree as of each audit. The engine half states that no file under `kazma-core/kazma_core/swarm/`, `routing_engine.py`, `agent/effect_journal.py` or `agent/dispatch_effects.py` differed from that commit.

Merge check: `kazma-core/kazma_core/swarm/task_store.py` was modified after the engine report was written (file time 16:07; report 15:54). The change is three lines in the Postgres DDL path (`ensure_schema(cur, ddl_extra)` in place of `cur.execute(ddl_extra)`, around task_store.py:176-180). It does not touch any cited defect, but task_store.py line numbers after about line 175 are now 2 higher than the engine report's (for example `persist_task` now starts at task_store.py:238, the upserts are at :285 and :321, and the tenant filter is at :394-409).

### What each half read

| | Engine half | Surfaces half |
|---|---|---|
| Read in full / in depth | `swarm/engine.py`, `dispatch_inner.py`, `worker_dispatch.py`, `worker.py`, `reliability.py`, `reliability_registry.py`, `task.py`, `task_store.py`, `task_control.py`, `task_lifecycle.py`, `checkpoint.py`, `checkpoint_manager.py`, `patterns.py`, `broadcast.py`, `autoscaler.py`, `durable.py`, `durable_temporal.py`, `handoff.py`, `handoff_guards.py`, `dispatch_helpers.py`, `worker_factory.py`, `manager.py`, `phonebook.py`, `semantic_router.py`, `routing_engine.py`, `agent/dispatch_effects.py` | `kazma_ui/swarm_panel/` (routes_general, routes_tasks, routes_workers), `swarm_sse.py`, `services.py`, `templates/swarm.html`, `static/js/swarm.js`, `swarm/safety.py`, `bus.py`, `shared_approvals.py`, Telegram/Discord/Slack bus adapters and callback routing, `agent/tool_builtins/system.py`, HITL ContextVars in `tool_registry.py` and `graph_tool_worker.py`, commitment scope and side-effect registry, gateway `/swarm`, `swarm_notify.py`, `platform_rbac.py`, `auth.admin_decision`, swarm docs |
| Read in part | `metrics.py`, `registry.py`, `consultation.py`, `agent/effect_journal.py`, `db/postgres_pool.py`, `db/pg_helpers.py`, `config_store.apply_sqlite_pragmas`, and the engine's callers (`kazma_ui/app.py`, `routes_tasks.py`, gateway `swarm_dispatch.py`, `ide/service.py`, `agent/tool_builtins/system.py`) | Engine dispatch, broadcast, approve/reject, worker-dispatch scope, pipeline hand-on and task-store tenant filtering, only where they shape a surface |

### Fresh runs (both halves)

| Run | Result | File and log |
|---|---|---|
| Engine witnesses | **24 passed in 3.91 s** | `test_swarm_engine_witnesses.py`; `swarm-engine-witnesses.log` (one `SW_E..=True` marker line per witness) |
| Surfaces witnesses | **15 passed in 20.30 s** | `test_swarm_surfaces_probes_2026_10_13.py`; `swarm-surfaces-probes.log` |
| Existing engine regression subset (11 files: `test_swarm_reliability`, `test_circuit_breaker_hardening`, `test_handoff_guards`, `test_handoff_breaker_accounting`, `test_swarm_recovery_no_replay`, `test_swarm_task_retention`, `test_execution_recovery_scope`, `test_swarm_admission_hop`, `test_swarm_timeout_validation_concurrency`, `test_swarm_auto_worker`, `test_swarm_paused_task_endings`) | **177 passed in 16.71 s** | `swarm-engine-regression.log` |
| Existing surfaces regression subset (15 files: `test_swarm_api`, `test_swarm_sse`, `test_swarm_task_stream_ends`, `test_swarm_ui_panel`, `test_shared_approvals`, `test_bus_approval_can_be_answered`, `test_swarm_safety_yolo`, gateway `test_swarm_approval_callbacks`, `test_swarm_breaker_reset`, `test_swarm_auto_worker`, `test_swarm_page_result_fields`, `test_ide_send_to_swarm`, `test_hitl_gate_swarm_pipeline`, `test_swarm_hitl_checkpoints`) | **193 passed in 26.26 s** | `swarm-surfaces-existing-gates.log` |
| JS: `tests/js/test_swarm_event_text.js`, `test_swarm_breaker.js` | all ok | `swarm-surfaces-js.log` |

Notes:
- **A witness asserts that a defect is present.** A green witness run confirms the findings; it certifies nothing. Most witnesses carry a control assertion showing the sibling path behaving correctly.
- The two existing-suite counts overlap: `tests/test_swarm_auto_worker.py` (12 tests) ran in both. They are not a unique total.
- The existing suites were green with every witnessed defect present.
- Engine witnesses are inert: temporary SQLite stores under `tmp_path`, stub workers, the pipeline Refiner, self-improvement hook and worker memory index stubbed (`test_swarm_engine_witnesses.py:38-60`), Temporal and the gate registry off, shared breakers only against a recording fake. Surfaces witnesses use temporary stores and replace every platform HTTP call with an in-process recorder; no LLM is called.
- The first version of the SW-S04 witness assumed the durable row stays `pending` after a timeout. A run showed an orphaned waiter sometimes writes a reject vote first, so the assertion accepts either state. The defect itself (a card never edited) reproduced on every run.

### Witness index

The engine witness function numbers do **not** match the engine finding IDs (the file was numbered before the report was ranked). Use this table.

| Finding | Witness function(s) | Log marker or captured result |
|---|---|---|
| SW-E01 | `test_sw_e01_approved_pipeline_reexecutes_after_restart` | `SW_E01_POST_CHECKPOINT_STEP_REEXECUTED=True` |
| SW-E02 | `test_sw_e02_reaper_records_timeout_but_work_continues`; `test_sw_e03_cancel_without_handle_is_cosmetic` | `SW_E02_REAPED_TASK_KEPT_RUNNING=True; NO_DEADLINE_TASK_REAPED=True`; `SW_E03_CANCEL_WITHOUT_HANDLE_DID_NOT_STOP_WORK=True` |
| SW-E03 | `test_sw_e02b_reaped_pipeline_is_resurrected_as_paused`; `test_sw_e08_same_task_id_executes_twice` | `SW_E02B_TIMEOUT_TASK_RESURRECTED_AND_COMPLETED=True`; `SW_E08_DUPLICATE_DISPATCH_EXECUTES=True` |
| SW-E04 | `test_sw_e04_caller_timeout_becomes_user_cancel`; `test_sw_e05_engine_deadline_discards_effect_evidence`; `test_sw_e05b_pattern_step_timeout_labels_effect_retry_safe` | `SW_E04_TIMEOUT_REPORTED_AS_USER_CANCEL=True; EFFECT_EVIDENCE_LOST=True`; `SW_E05_DISPATCH_DEADLINE_DROPS_EFFECT_EVIDENCE=True`; `SW_E05B_PATTERN_TIMEOUT_MARKED_RETRY_SAFE=True` |
| SW-E05 | `test_sw_e06_resumed_pipeline_runs_as_paused` | `SW_E06_RESUME_OUTSIDE_ADMISSION_AND_WATCHDOG=True` |
| SW-E06 / SW-S06 | `test_sw_e07_public_broadcast_bypasses_lifecycle`; `test_sw_s06_web_broadcast_bypasses_admission_and_tenant_stamp` | `SW_E07_BROADCAST_BYPASSES_ADMISSION_PERSIST_CANCEL=True`; S06 passed |
| SW-E07 | `test_sw_e11_breaker_and_store_io_on_event_loop`; `test_sw_e11b_auto_routing_embeds_on_event_loop` | `SW_E11_BREAKER_SETTINGS_IO_ON_LOOP=3; TASKSTORE_WRITES_ON_LOOP=2`; `SW_E11B_SEMANTIC_ROUTER_ON_LOOP=True` |
| SW-E08 | `test_sw_e09_failed_mutating_step_is_stored_completed`; `test_sw_e10_retry_drops_target_and_ignores_uncertainty`; `test_sw_e14_non_retryable_errors_are_retried` | `SW_E09_PARTIAL_WITH_UNCERTAIN_EFFECT_STORED_COMPLETED=True`; `SW_E10_RETRY_DROPS_WORKSPACE_SCHEMA_AGGREGATION=True`; `SW_E14_AUTH_ERROR_RETRIED=True` |
| SW-E09 | `test_sw_e16_reject_after_history_eviction_is_not_saved` | `SW_E16_REJECT_AFTER_EVICTION_NOT_PERSISTED=True` |
| SW-E10 | `test_sw_e11c_semantic_router_mixes_embedding_spaces` | `SW_E11C_PROFILE_AND_QUERY_EMBEDDINGS_FROM_DIFFERENT_MODELS=True` |
| SW-E11 | `test_sw_e12_stale_success_closes_half_open_breaker` | `SW_E12_STALE_OUTCOME_CLOSES_HALF_OPEN=True` |
| SW-E12 | `test_sw_e13_autoscaler_reaps_worker_with_dispatch_in_flight` | `SW_E13_POOL_WORKER_REAPED_MID_DISPATCH=True` |
| SW-E13 | `test_sw_e15_no_shipped_worker_can_request_a_handoff` | `SW_E15_HANDOFF_UNREACHABLE_FROM_SHIPPED_WORKERS=True` |
| SW-E14 | `test_sw_e17_running_row_failure_is_debug_only`; `test_sw_e19_open_breaker_rejection_counted_twice` | `SW_E17_RUNNING_PERSIST_FAILURE_DEBUG_ONLY=True`; `SW_E19_BREAKER_REFUSAL_DOUBLE_COUNTED=True` |
| SW-E15 | `test_sw_e18_refined_output_dropped_and_payload_amplified` | `SW_E18_REFINER_OUTPUT_DROPPED=True; STORED_BYTES_PER_OUTPUT_BYTE=4.51` |
| SW-E16 | `test_sw_e20_postgres_text_columns_not_nul_stripped` | `SW_E20_PG_TEXT_COLUMNS_KEEP_NUL=True` |
| SW-S01 | `test_sw_s01_dispatch_swarm_from_a_graph_turn_skips_every_approval` | passed |
| SW-S02 | `test_sw_s02_a_reused_approval_id_is_approved_without_a_click`; `test_sw_s02b_the_id_comes_from_the_tools_own_arguments` | passed |
| SW-S03 | `test_sw_s03_the_approval_card_hides_the_tail_of_the_arguments` | passed |
| SW-S04 | `test_sw_s04_timeout_leaves_the_card_live_and_the_row_pending` | passed (row state accepted either way, see above) |
| SW-S05 | `test_sw_s05_fanout_rejecting_card_says_rejected_while_the_tool_is_approved` | passed |
| SW-S07 | `test_sw_s07_worker_and_output_target_writes_skip_the_admin_gate` | passed |
| SW-S08 | `test_sw_s08_approve_answer_and_the_page_disagree`; `test_sw_s08b_no_dispatch_from_the_page_asks_for_background` | passed (JS half read from source) |
| SW-S09 | `test_sw_s09_reconnect_replays_without_ids_and_no_keepalive` | passed |
| SW-S10 | `test_sw_s10_check_swarm_task_returns_worker_output_unfenced` | passed |
| SW-S11 | `test_sw_s11_unclassified_tool_is_gated_by_graph_but_not_by_swarm_bus` | passed |
| SW-S12 | `test_sw_s12_bus_approval_touches_settings_on_the_event_loop` | passed |
| SW-S13 | `test_sw_s13_bus_adapters_skip_the_mention_sanitizer_the_chat_adapters_use` | passed |
| SW-S14, SW-S15, SW-S16, SW-S17 | none (code reading) | — |

### The unplanned network request

On the surfaces half's first probe run, the product's embedder, loaded while a swarm worker's result was stored, sent **one unauthenticated metadata request to the Hugging Face Hub**. Every later run set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` and used the local cache only. The surfaces auditor records this as a mild product observation: a swarm dispatch can reach the network for model metadata. It has no finding ID.

Merge checks:
- The retained `swarm-surfaces-probes.log` (the final run) shows the embedder loading during SW-S01 ("Loading weights … 391/391", line 24).
- The probe file sets neither variable itself, and `git grep` finds no tracked file that sets `HF_HUB_OFFLINE` or `TRANSFORMERS_OFFLINE`. A test run is offline only if the person running it sets them.
- The engine witnesses stub the worker-result memory index `_index_worker_l4_memory` (defined at worker_dispatch.py:34, called at :363) and made no network call.

### Not run

- **Postgres:** no real Postgres in either half. The engine half read the PG path and probed it with a fake pool; no Postgres TaskStore or Postgres settings store was run.
- **Temporal, Chroma, LLM:** Temporal 1.34.0 is installed but its test server would be downloaded; real Chroma would fetch its default model; no LLM call was made.
- **Processes and load:** no multi-process or multi-replica run, no load, soak or process-level fault injection. Process death was simulated by cancelling the coroutine and reopening the store; a real kill can differ at the margins (for example unflushed SQLite pages under `synchronous=NORMAL`).
- **Pipeline suites:** suites that execute pipelines through the real Refiner LLM path were not run by the engine half. Its report says a parallel full-suite run covers them; no full-suite log is among the audit artifacts, so this merge does not count that run as evidence. Neither half ran the full suite.
- **Browser and platforms:** no browser session (the SW-S08 JavaScript behaviour was read from source), no proxy-timeout reproduction (the Cloudflare effect in SW-S08 is inferred from code), no real Telegram, Discord or Slack round trip.
- **Exploit chain:** the SW-S01 chain (a web page leading to `dispatch_swarm` and then to `vault_retrieve` or `send_message`) was not executed. Only a stand-in `file_write` ran.
- **Not reviewed (surfaces):** TUI swarm views, `kazma swarm` CLI beyond its documented routes, `/api/pipelines/*`, IDE `send_to_swarm` beyond its existing tests, the `/swarm` template's RTL layout, accessibility, the inline-HTML fallback page.
- **Read but not deeply audited (engine):** the `InProcessWorker` ReAct loop beyond its effect and exception paths, `consultation.py`/`aggregator.py` synthesis, `tracing.py`, `swarm/memory/`, `blackboard.py` concurrency, dialect routing, `registry.py` persistence (atomic replace confirmed, no fsync), `dispatch_swarm` recursion (bounded by the admission cap).

## What is already good

| Area | Assessment | Half |
|---|---|---|
| Module structure | The §9 split holds: reliability, phonebook and checkpoint state are separate modules; engine delegates are thin; constructors are stable. | Engine |
| Admission | A global cap on non-paused in-flight tasks (`KAZMA_SWARM_MAX_ACTIVE` / `max_concurrent_tasks`, default 10) bounds recursive `dispatch_swarm` fan-out and spend; ad-hoc workers capped at 10 and reaped; template `max_instances` clamped to 50; semaphore cache bounded. (Broadcast and resumed pipelines bypass it: SW-E05, SW-E06.) | Engine |
| Effect tracking inside the worker boundary | `DispatchEffects` survives retries; a failed dispatch after a mutator invocation is `retry_safe=False` and stops `RetryPolicy`, `FallbackChain` and handoff fallback; `InProcessWorker` stops on `EffectUncertain` without synthesis; per-tool timeouts on mutators raise uncertainty. Tested in `test_execution_recovery_scope.py`. | Engine |
| No blind replay after a crash | Orphaned RUNNING rows become FAILED with `execution_outcome=unknown`; legacy PENDING rows are finalized, not re-dispatched; Temporal uses `maximum_attempts=1` and `REJECT_DUPLICATE` workflow ids and reports "outcome unknown". | Engine |
| Breaker and cancellation hygiene | Half-open probe flag released in `finally`; busy flag released on cancellation; handoff/fallback hop budget threaded (M13); cycle guard (depth 5, 2 visits) correct as code. | Engine |
| Pipeline checkpoints | In-process claim before any await (`try_claim`); auto-reject re-armed after restart; every terminal finalize closes the checkpoint and settles its gate; gate rows registered idempotently; terminal-state guard (M-S1); "already final?" decided before reject. | Both |
| TaskStore | WAL + `busy_timeout`, autocommit connection, `add_missing_columns` migrations, `json_each` worker filter, retention of terminal rows only (default 30 days), Postgres variant with NUL-safe JSON and SQL-side metric increments, tables in the PG backup list. | Engine |
| Scope propagation | Tenant, per-task workspace and the default commitment swarm scope are bound around each worker dispatch and survive handoffs; workers run under a scope capped at HIGH by default (`scope.py:123`). | Both |
| Admin decision | `admin_decision` is one fail-closed implementation (`auth.py:1140`); approve, reject, cancel, retry, templates and reap use it. | Surfaces |
| Platform buttons | Telegram, Discord and Slack check the allowlist and `is_gateway_admin` before a swarm or HITL button resolves anything; unknown callback data is ignored (L-29). | Surfaces |
| Fail-closed gates | NullBus denies danger tools; the registry blocks when SafetyMiddleware is unavailable; the MCP gate fails closed and no longer trusts the graph-wide flag (`mcp/manager.py:2346-2366`). | Surfaces |
| Task stream | Always ends with `task_completed`, rebuilt from the store when the bus has forgotten the task; store reads in a thread; history capped at 1,000 events per task with a TTL; bounded subscriber queues. | Surfaces |
| Escaping | Telegram MarkdownV2 escaped on cards; Telegram HTML reports use `tg_escape`/`tg_quote`; `swarm.js` escapes through `esc()` and marks content `translate="no"`. | Surfaces |
| Secrets | `GET /api/swarm/output-target` masks the bot token; nested secrets vault-encrypted on write. | Surfaces |
| Worker prompts | Self-improvement deltas in a worker's Soul are fenced (`worker.py:325-338`). | Surfaces |
| Tests | Many incident-specific tests and class gates (terminal-status scanner, paused-task endings, recovery no-replay); JS tests with negative controls. The targeted subsets are green. | Both |

## Ranked findings

### Priority definitions (merged)

- **P1:** blocks any claim that swarm approvals are a boundary, or that swarm execution with mutating tools is effect-safe or safe unattended. (Engine half: "fix before claiming unattended/effect-safe swarm execution or industrial readiness"; surfaces half: "fix before relying on approvals as a boundary, in any posture".)
- **P2:** significant correctness, availability, operability or boundary gap.
- **P3:** hardening, truthfulness, hygiene, multi-user-only, or documentation.

Each finding keeps the priority its half gave it. Ranking within a priority is this merge's: within P1, the three approval-boundary findings come first because THREAT_MODEL.md:53 names approval as the boundary that matters and SW-S01 is reachable by untrusted content; the four lifecycle findings follow in the engine half's order.

### Index

| # | ID(s) | P | Title | Half | Stage |
|---|---|---|---|---|---|
| 1 | SW-S01 | P1 | A swarm task started from a chat turn runs danger tools with no approval anywhere | Surfaces | 1 |
| 2 | SW-S02 | P1 | Swarm-bus approval ids are reusable, and a settled "yes" is permanent | Surfaces | 1 |
| 3 | SW-S03 | P1 | The bus approval card shows about 100 characters of the arguments, without saying so | Surfaces | 1 |
| 4 | SW-E01 | P1 | An approved pipeline that dies mid-resume is offered again and re-executes completed steps | Engine | 3 |
| 5 | SW-E02 | P1 | Timeouts, the watchdog and cancel record an ending but do not stop the work | Engine | 2 |
| 6 | SW-E03 | P1 | No transition rules: terminal rows are overwritten and one task id runs twice | Engine | 2 |
| 7 | SW-E04 | P1 | Effect uncertainty is lost on every path that cuts work short from outside the worker | Engine | 3 |
| 8 | SW-E06 / SW-S06 | P2 | Broadcast bypasses `engine.dispatch()`; a capacity refusal reads "dispatched" | Both | 2 |
| 9 | SW-E05 | P2 | An approved pipeline runs as PAUSED, outside admission and the watchdog | Engine | 2 |
| 10 | SW-E09 | P2 | Restored paused pipelines live only in a 500-entry LRU; reject answers success but saves nothing | Engine | 2 |
| 11 | SW-E08 | P2 | Outcome classification and retry are not truthful | Engine | 3 |
| 12 | SW-S04 | P2 | An approval that times out leaves a live card and orphaned waiters | Surfaces | 1 |
| 13 | SW-S05 | P2 | FanOut: the platform where a human pressed Reject says REJECTED while the tool runs | Surfaces | 1 |
| 14 | SW-S11 | P2 | The bus classifier is not the tier floor that AGENTS.md and the docs describe | Surfaces | 1 |
| 15 | SW-S10 | P2 | Worker output reaches the chat model and the next worker unfenced | Surfaces | 5 |
| 16 | SW-S08 | P2 | The Swarm page misreports approvals and runs every dispatch in the foreground | Surfaces | 4 |
| 17 | SW-S07 | P2 | Mutating swarm routes are inconsistently gated | Surfaces | 5 |
| 18 | SW-E07 | P2 | Blocking database and settings I/O on the event loop for every dispatch | Engine | 4 |
| 19 | SW-S12 | P2 | The approval path reads and writes the settings store on the event loop | Surfaces | 4 |
| 20 | SW-S09 | P2 | The task stream replays everything on reconnect, has no event ids or keepalive, and is unbounded | Surfaces | 4 |
| 21 | SW-E10 | P3 | Semantic routing mixes two embedding models and fails silently | Engine | 5 |
| 22 | SW-E11 | P3 | Circuit-breaker outcomes are not tied to the probe; shared mode fails open quietly | Engine | 5 |
| 23 | SW-E12 | P3 | The autoscaler can reap a pool worker with a dispatch in flight | Engine | 5 |
| 24 | SW-E13 | P3 | Handoff and its cycle guard are unreachable from any shipped worker | Engine | 5 |
| 25 | SW-E14 | P3 | Silent and double-counted failures (engine) | Engine | 5 |
| 26 | SW-S15 | P3 | Observability and notification gaps (surfaces) | Surfaces | 5 |
| 27 | SW-E15 | P3 | Unbounded and unaccounted resources | Engine | 4 |
| 28 | SW-S14 | P3 | No input bounds on dispatch | Surfaces | 4 |
| 29 | SW-E16 | P3 | Backend parity and durability gaps | Engine | 5 |
| 30 | SW-S13 | P3 | Bus adapters post worker text without the chat adapters' mention sanitizer | Surfaces | 5 |
| 31 | SW-S16 | P3 | Tenancy and privacy (multi-user deployments only) | Surfaces | 5 |
| 32 | SW-S17 | P3 | Docs drift | Surfaces | 5 |

Totals: 33 original IDs (16 engine, 17 surfaces), 32 merged entries. P1: 7. P2: 13 (one of them the merged SW-E06 / SW-S06). P3: 12.

Only SW-E06 and SW-S06 describe the same defect and are merged. Several other pairs share a class or a code branch; they stay separate entries with cross-references, because their fixes touch different code: SW-E07 / SW-S12 (loop I/O), SW-E14 / SW-S15 (silent failures), SW-E15 / SW-S14 (bounds), SW-E04 / SW-S16 (the gateway timeout branch), SW-E02 / SW-S08 (foreground dispatch has no handle), SW-E01 / SW-E05 / SW-S08 (approve and resume), SW-S01 / SW-S10 (the injection chain), SW-S02 / SW-S04 (late clicks write permanent approvals).

None of the witnesses demonstrates remote code execution.

---

### 1. SW-S01 — P1: a swarm task started from a chat turn runs danger tools with no approval anywhere

**Half:** surfaces.

**Evidence (four steps):**
1. `graph_tool_worker.py:815-818` sets `_graph_hitl_gate_ctx=True` for the whole tool-worker step when HITL is active, and `:1593-1596` resets it (merge check).
2. `dispatch_swarm` is tier `write`, so it is never gated (`hitl.py:259`, merge check). It runs inside that step and starts the work with `asyncio.create_task(engine.dispatch(task))` (`system.py:720`, `:799`, merge check). `create_task` copies the context, flag included.
3. The worker's tool calls reach `LocalToolRegistry.execute`: `InProcessWorker` → `effect_journal.execute_effect` → `UnifiedToolExecutor` → local registry (`mcp/manager.py:2340`).
4. The registry skips the bus gate when the flag is set (`tool_registry.py:687`, merge check), and no graph interrupt covers a swarm worker.

**What still runs unasked:** the default worker scope caps tools at HIGH (`scope.py:123-146`), so CRITICAL tools (shell, python, email, file_delete, config) are still denied by the commitment layer. Danger tools classified HIGH or NONE run with no card: `file_write`, `file_apply_patch`, `git_commit`, `git_push`, `git_merge`, `github_create_pr`, `run_unit_tests`, `browser_eval_js`, and `vault_retrieve` and `send_message`, which are classified NONE (merge check: `kazma_core/safety/side_effects.py:148`, `:186`; `file_write` HIGH at `:113`, `git_push` HIGH at `:173`). Two defaults widen the reach: the auto-registered `researcher` includes `file_write`, and a worker with no tool list gets every tool definition (`worker.py:300-311`).

**Failure scenario:** a web page read in chat steers the model (prompt injection) to call `dispatch_swarm`; the worker pushes to git, writes files or reads a vault secret with no approval card on Web, Telegram, Discord or Slack.

**Witness:** `test_sw_s01_dispatch_swarm_from_a_graph_turn_skips_every_approval`, with the real `dispatch_swarm`, a real `SwarmEngine` and a real `LocalToolRegistry`. Control 1: a direct danger call asks the bus and is refused. Control 2: `dispatch_swarm` called outside a graph turn asks the bus. Defect: the same call inside a simulated tool-worker step — the tool ran and the bus was never asked. The stand-in worker calls the same `LocalToolRegistry.execute` entry that `InProcessWorker` reaches. The vault-to-send chain was not executed.

**An existing test pins the enabler:** `tests/test_bus_approval_can_be_answered.py:171` (`test_both_gates_honour_an_actual_approval`) asserts that the graph flag stays honoured on the local registry. It must be re-pointed, not deleted, when this is fixed. The MCP manager already learned the lesson ("The graph flag is set for the whole turn, including calls nobody approved", `mcp/manager.py:2346-2366`).

**Fix direction:** make graph authority per call, never per node: bind it to the specific tool-call ids the graph classified and gated, and set it only around `_exec_one` for those calls. Reset both HITL ContextVars at every boundary that starts detached work (`engine.dispatch`, any `create_task` or `spawn_background` reached from a tool, sub-agents, research jobs), for example by running them in a context copy with both flags cleared.

**Gate + negative control:** a behavioural test like the witness for every detached-work starter, found by enumerating the `create_task` / `spawn_background` calls reachable from tool functions. The current node-wide flag is the negative control.

**Related:** SW-S10 (unfenced worker output is the other half of the injection chain), SW-S11 (which tools the bus gates).

### 2. SW-S02 — P1: swarm-bus approval ids are reusable, and a settled "yes" is permanent

**Half:** surfaces.

**Evidence:**
- **Where the id comes from:** the local registry and the MCP gate pass `arguments.get("task_id")` as the approval key (`tool_registry.py:696-702`, merge check; `mcp/manager.py:2420`). `safety.py:451-455` uses it whenever it is present; the minted gate id is used only when the caller passed none (merge check).
- **Why a "yes" is permanent:** `shared_approvals.create_pending` returns early when the durable row `swarm.approval.<id>` is already resolved True ("A settled True is never reset", `shared_approvals.py:136-145`, merge check), and `wait_for_resolution` returns it at once (`:288-290`). Durable rows are never deleted; only the in-memory maps are evicted (`:50-66`). `resolve()` accepts an id that was never posted and creates a resolved row for it.
- **Concurrent requests:** adapters key their in-flight state by the same id (`telegram_bus.py:371-405`, same in Discord and Slack), so one click answers two concurrent cards that share it.
- **A second decision store:** the ConfigStore rows `swarm.approval.*` decide beside `hitl_gates.db`, against AGENTS §30 ("Decision truth = hitl_gates.db"). The gate-registry row is settled afterwards with actor `swarm_bus` (`safety.py:480-494`).

**Failure scenario:** a danger tool whose schema has a `task_id` parameter (common in task-tracker MCP servers) is approved once. Every later call carrying that id is approved, including a different tool with different arguments, and including after a restart.

**Witnesses:** `test_sw_s02_a_reused_approval_id_is_approved_without_a_click` uses the real Telegram adapter with the HTTP layer recorded: the first card is approved by one click; in-memory state is cleared (a simulated restart); a second card for `git_push … --force` with the same id is approved in under 3 s with no click. `test_sw_s02b_the_id_comes_from_the_tools_own_arguments` pins where the id comes from.

**An existing test locks the behaviour in:** `tests/test_bus_approval_can_be_answered.py:134` (`test_an_explicit_task_id_is_still_used`). Re-point it when this is fixed.

**Fix direction:** the approval id is the gate-registry `gate_id`, minted per request, never caller-supplied. Bind the decision to `(gate_id, tool, args_hash)`. Make `hitl_gates` the single decision record (CAS claim with the human actor), and delete or expire the shared row once its waiter has read it. A callback for an unknown or settled id is a no-op.

**Gate + negative control:** a reused or argument-supplied id must create a new pending card; the current `create_pending` is the negative control. Add a source gate that no caller passes argument-derived ids.

**Related:** SW-S04 (a late click on a pending row writes a permanent True), SW-S15 (gate rows name `swarm_bus`, not the human).

### 3. SW-S03 — P1: the bus approval card shows about 100 characters of the arguments, without saying so

**Half:** surfaces.

**Evidence:** the registry cuts the arguments to 200 characters (`json.dumps(arguments)[:200]`, `tool_registry.py:700`, merge check); `safety.py:463` puts `tool_args[:100]` in the card description (merge check); Telegram shows `[:200]` of that description and Discord `[:300]`; `proposed_output` is a fixed sentence; no ellipsis or "truncated" marker appears. Graph-path cards, by contrast, render the stored arguments.

**Failure scenario:** the model, or an injected instruction, orders the arguments as `{"content": "<benign>", "path": "~/.ssh/authorized_keys"}`. The operator approves what looks like a note.

**Witness:** `test_sw_s03_the_approval_card_hides_the_tail_of_the_arguments` — the description the bus receives contains the tool name, not the path, and no truncation marker.

**Fix direction:** render every argument name with bounded values and an explicit "[N chars hidden]" marker, plus an arguments hash; link to the full gate on the Web dashboard; refuse from chat cards a danger call whose arguments cannot be shown.

**Gate + negative control:** for every danger tool, each schema property appears on the card; the current renderer is the negative control.

### 4. SW-E01 — P1: an approved pipeline that dies mid-resume is offered again and re-executes completed steps

**Half:** engine.

**Evidence:** `engine.py:1339-1379` — `approve_checkpoint` pops the paused entry and calls `resume_pipeline` without setting RUNNING, persisting a step cursor or journaling the approval. `checkpoint_manager.py:327-394` restores any `paused` row as a pending checkpoint. `worker.py:211-218` — effect receipts use a random per-dispatch thread id, so a receipt cannot recognise a re-run step.

**Failure scenario:** pipeline `draft → [checkpoint] → publish`. The operator approves; `publish` posts or writes, and the process is reloaded or crashes before the step reports. The row still reads `paused at step 1`; at boot the checkpoint is restored and offered; a second approval runs `publish` again. The guard reloads the live install several times a day, so the window is real.

**Witness:** `test_sw_e01_approved_pipeline_reexecutes_after_restart` — the store reads `paused` while step 2 runs; after the simulated restart `get_checkpoint_info` is offered again and the effect list is `["published", "published"]`.

**Fix direction:** record the approval and a RUNNING status with a step cursor before resuming (CAS from PAUSED). Journal each pipeline step's begin and commit under a stable `(task_id, step, generation)` identity and pass that identity to the effect journal. At boot, a step with a begin and no commit becomes `execution_outcome=unknown`, held for reconciliation, never a re-offered approval. Settle the gate as "resumed" and refuse a second approval for the same checkpoint generation.

**Gate + negative control:** crash at each resume point (before the first step, mid-step, after a step commit, before finalize) on SQLite and Postgres; assert at most one invocation of each post-checkpoint effect and no re-offered checkpoint. The current "pop the entry, then resume" order must fail the gate.

**Related:** SW-E05 (resume runs as PAUSED), SW-S08 (the approve route holds the request until the pipeline finishes; its fix — claim, answer 202, resume in the background — must use this RUNNING transition and step journal).

### 5. SW-E02 — P1: timeouts, the watchdog and cancel record an ending but do not stop the work

**Half:** engine.

**Evidence:**
- `engine.py:524-593` — `reap_stale_tasks` calls `_finalize_task`, which pops the handle without cancelling it (`engine.py:1140-1143`, merge check).
- `task_control.py:63-82` — with no live handle, cancel finalizes "cancelled" at once (`finalize` at `:75-81`, merge check) and the coroutine keeps running.
- Handles are registered only by the panel's background paths and by `dispatch_swarm` (`routes_tasks.py:483`, `:499`; `system.py:799-802`). They are not registered by the gateway `/swarm` (`swarm_dispatch.py:507`), IDE `send_to_swarm` (`ide/service.py:776`), `SwarmManager.dispatch`, the panel's foreground dispatch (merge check: `routes_tasks.py:495`, `:511` await without registering), or an approve-driven resume.
- `engine.py:540` reaps tasks whose `timeout<=0` ("no timeout", `dispatch_helpers.wait_timeout`) at 300 s + 30 s (merge check: `float(getattr(t, "timeout", None) or 300.0)`).

**Failure scenario:** a user cancels a running task from the panel, or the watchdog times it out. The UI and store say cancelled or timeout while the worker keeps calling tools for minutes, and its result is silently discarded by `_finalize_task`'s idempotency guard.

**Witnesses:** `test_sw_e02_reaper_records_timeout_but_work_continues` (registered handle not cancelled; an effect after TIMEOUT recorded; a `timeout=0` task reaped); `test_sw_e03_cancel_without_handle_is_cosmetic` (row `cancelled`, effect still happens).

**Merge-time inference (not separately witnessed):** SW-S08b shows that no Swarm-page dispatch requests background mode, and the foreground route registers no handle. A Stop on a task the Swarm page dispatched therefore takes the no-handle branch this finding witnesses. Separately, "no deadline" is honoured inconsistently: the dispatch wait turns `timeout<=0` into a one-year sentinel (`dispatch_helpers.py:25-45`, per SW-S14), while the watchdog reaps the same task at 330 s without stopping it.

**Fix direction:** the engine owns execution. `dispatch()` runs the work in an engine-created task registered before the first await; cancel, reap and timeout cancel that task, await it with a bound, and only then record the ending (`cancelled` / `timeout`, plus `execution_outcome=unknown` when effects were invoked). Honour `timeout<=0` in the watchdog or reject it at admission.

**Gate + negative control:** for every dispatch entry point, enumerated from source as `test_swarm_paused_task_endings.py` does, cancel and reap a blocked worker and assert that the worker coroutine was cancelled before the terminal row was written and that no effect occurs after it. Today's finalize-without-cancel is the negative control.

**Related:** SW-S08, SW-S14, SW-E03.

### 6. SW-E03 — P1: no transition rules — terminal rows are overwritten and one task id runs twice

**Half:** engine.

**Evidence:** `task_store.py:236-335` (now `:238-337`) — `INSERT OR REPLACE` / `ON CONFLICT DO UPDATE` with no status precondition (merge check: `:285`, `:321`). `checkpoint_manager.py:128-212` — `handle_pipeline_checkpoint` sets PAUSED and persists without checking for a terminal status. `engine.py:381-405` — dispatch sets RUNNING and persists without checking `task.id in _active_tasks` or the stored status. `engine.py:1114-1123` — the only guard is in memory and skips `paused` (merge check).

**Failure scenario:** a pipeline reaped as TIMEOUT keeps running (SW-E02), reaches its checkpoint, is rewritten as PAUSED, is approved, and ends COMPLETED — after the user was told it timed out. A retried HTTP request or a double click that re-submits the same task id executes the workers twice; a finished task that is re-dispatched flips its row back to RUNNING and runs again.

**Witnesses:** `test_sw_e02b_reaped_pipeline_is_resurrected_as_paused` (TIMEOUT → PAUSED → COMPLETED); `test_sw_e08_same_task_id_executes_twice` (two concurrent executions, then a third on a COMPLETED id).

**Fix direction:** one transition table (PENDING→RUNNING→{PAUSED, terminal}; PAUSED→RUNNING or terminal; terminal is absorbing), enforced by CAS in both backends (`UPDATE … WHERE id=? AND status=? AND generation=?`); an owner/run id and a generation on the row; idempotent admission (the `INSERT` of a task id fails or returns the existing run). Late results from a superseded generation are recorded as orphan evidence, never as the task's result.

**Gate + negative control:** property test over all (from, to) pairs on SQLite and Postgres; a concurrent double dispatch of one id executes once. Extend the existing terminal-status AST gate to the reverse direction (no non-terminal assignment that can follow a terminal one). The current upsert is the negative control.

**Related:** SW-E16 (owner/run id lands here).

### 7. SW-E04 — P1: effect uncertainty is lost on every path that cuts work short from outside the worker

**Half:** engine.

**Evidence:**
- `engine.py:476-509` — the whole-task `wait_for(task.timeout)` is created before the inner `TimeoutGuard` with the same value (`dispatch_inner.py:330-337`, `worker_dispatch.py:231-235`), so it always fires first and finalizes with `worker_results=[]` (merge check: `engine.py:486-499`).
- `patterns.py:326-347`, `454-475`, `727-749` build a fresh `WorkerResult(status="timeout")` with the default `retry_safe=True`.
- `engine.py:500-509` (and `438-445`) catch `CancelledError`, return normally and label the result "Cancelled by user" (merge check: `:500-509`).

**Failure scenario:** a single-worker task with the default 300 s deadline invokes `file_write` or `shell_exec` and overruns. The record says `timeout`, has no worker result, and nothing says an effect may have happened — and `retry_task` re-runs it (SW-E08). A caller's own `wait_for` (gateway, `swarm_dispatch.py:505-509`) never sees `TimeoutError`; its `except asyncio.TimeoutError` branch (`swarm_dispatch.py:560`) is dead, and the chat is told "Task failed: Cancelled by user". At process teardown, tasks still running are cancelled and recorded as user cancellations on SQLite; on Postgres the closed pool leaves them RUNNING for orphan recovery, so the record depends on the backend.

**Merge note:** the dead branch holds the reply SW-S16 flags as misleading — "Partial work was discarded" (merge check: `swarm_dispatch.py:566-570`). Fixing this finding makes that text reachable; correct it in the same change.

**Witnesses:** `test_sw_e04_caller_timeout_becomes_user_cancel` (no `TimeoutError`; `cancelled` / "Cancelled by user"; no effect marker); `test_sw_e05_engine_deadline_discards_effect_evidence` (control: a worker-side failure is marked `retry_safe=False`; the deadline path has `worker_results=[]`); `test_sw_e05b_pattern_step_timeout_labels_effect_retry_safe` (`retry_safe is True` after a mutator invocation).

**Fix direction:** hoist the `DispatchEffects` tracker to task level (one per task attempt, shared by all steps). On any timeout, cancel or reap, record `execution_outcome=unknown` plus the invoked tools in `TaskResult.metadata`; build pattern timeout results from the tracker. Re-raise `CancelledError` after recording (use `uncancel()` or a distinct "interrupted" status for shutdown) so callers' deadlines behave. Distinguish `cancelled_by_user`, `deadline` and `shutdown`.

**Gate + negative control:** a matrix {dispatch, pipeline, fan-out, consult, conditional, broadcast} × {engine deadline, step deadline, caller `wait_for`, cancel, reap, shutdown} with a worker that invokes a mutator and then blocks; assert the uncertainty marker and the correct cause. Today's code fails every cell except worker-internal failure.

**Related:** SW-E08 (retry ignores uncertainty), SW-S16.

### 8. SW-E06 / SW-S06 — P2: broadcast bypasses `engine.dispatch()`, and a capacity refusal reads "dispatched"

**Halves:** both (engine SW-E06, P2; surfaces SW-S06, P2). Same defect.

**Evidence (engine half):** `engine.py:680-684` — `broadcast()` is public. `broadcast.py:33-101` adds the task to `_active_tasks` (merge check: `:37`) with no admission, no RUNNING row and no durable path, and catches only `except Exception` (merge check: `:91`), so a `CancelledError` escapes without finalizing. `routes_tasks.py:276-277` calls `engine.broadcast` inside `_run_and_route_task` (merge check). `SwarmEngine.dispatch_by_name` / `summon` (`phonebook.py`) is a dormant public path with no admission, persistence, effect tracking, workspace/tenant scope or default swarm scope token (no product caller today).

**Evidence (surfaces half):** the dispatch route sends BROADCAST to that path at `routes_tasks.py:480-495` (merge check), including the default type when several workers are named without a pattern (`_coerce_task_type`). `engine.dispatch` holds everything the route then skips (`engine.py:337-398`): the durable-required refusal, reaping, the global admission cap (audit M11), the `tenant_id` stamp and the RUNNING-row persist (audit H-9); it routes BROADCAST itself (`engine.py:673`, merge check). In production or multi-user posture the task store hard-filters by `tenant_id` (`task_store.py:392-407`, now `:394-409`), so unstamped broadcasts never appear in Task History. Separately, an admission-denied dispatch returns `{"status": "ok", "message": "Task dispatched to 1 worker(s)", "result_status": "failed"}` (`routes_tasks.py:523-531`), and the page shows a "dispatched" toast (`swarm.js:598-600`).

**Failure scenario:** broadcasts, the most expensive pattern, ignore the capacity cap; a crash mid-broadcast leaves no row; cancelling a background broadcast leaves it RUNNING in `_active_tasks` until the watchdog records a timeout; in the production posture broadcasts disappear from Task History; a refused dispatch reads "dispatched".

**Witnesses:** `test_sw_e07_public_broadcast_bypasses_lifecycle` (control: `dispatch()` denies at the cap; `broadcast()` runs, writes no row, and leaks RUNNING after cancel). `test_sw_s06_web_broadcast_bypasses_admission_and_tenant_stamp` (real engine and TaskStore, `KAZMA_SWARM_MAX_ACTIVE=1`, one active task): dispatch is refused and the refusal reads "dispatched"; broadcast runs; with `KAZMA_PRODUCTION=1`, Task History lists the dispatched task and not the broadcast; the stored broadcast row has no `tenant_id`.

**Fix direction:** make `broadcast()` and `dispatch_by_name()` private, or thin wrappers over `dispatch()`; one entry point owns admission, persistence, durability, effect tracking and scopes. Routes call only `engine.dispatch`. `admission_denied` maps to HTTP 429 or 503 with its message.

**Gate + negative control:** an AST gate that product code reaches workers only through `SwarmEngine.dispatch` (engine internals allow-listed), with a planted direct `broadcast()` call as the control; plus the behavioural route test above.

### 9. SW-E05 — P2: an approved pipeline runs as PAUSED — outside admission and the watchdog

**Half:** engine.

**Evidence:** `engine.py:1316-1405` never sets RUNNING on resume; `engine.py:359-362` excludes PAUSED from the admission count (merge check); `engine.py:535-539` skips PAUSED tasks that have `checkpoint_timeout` (merge check).

**Failure scenario:** any number of resumed pipelines can run at once while the cap admits new work; a hung resumed step is never reaped; the store and panel show "paused" for work that is executing (which also feeds SW-E01). Conversely, a paused pipeline without `checkpoint_timeout` is reaped `timeout × steps + 30 s` after its original start (about 15 min by default) — an undocumented approval deadline — but only until the next restart: restored tasks are not in `_active_tasks`, so they never expire.

**Witness:** `test_sw_e06_resumed_pipeline_runs_as_paused` (status PAUSED during step 2; a second task admitted with cap 1; the reaper returns 0).

**Fix direction:** resume is a RUNNING transition through the same admission path as dispatch, with its own start time for the watchdog. Paused lifetime is one rule (checkpoint timeout, or an explicit "no expiry") applied identically before and after a restart.

**Gate + negative control:** admission and watchdog tests that include resumed and restored tasks; the current PAUSED-during-resume is the negative control.

### 10. SW-E09 — P2: restored paused pipelines live only in a 500-entry LRU — reject answers success but saves nothing

**Half:** engine.

**Evidence:** `checkpoint_manager.py:350-354` restores into `_task_history` only; `task_lifecycle.py:83-89` trims history; `engine.py:1425-1511` — after eviction `_hist_update_task` returns `None`, `complete_pipeline` is a no-op and nothing is persisted; `task_control.py:40-62` answers "not active" for cancel.

**Failure scenario:** a pipeline paused before a restart, followed by 500 newer tasks: Reject returns 200 with a failed result, but the row stays `paused` and is restored again at the next boot (the 2026-09-25 class of bug, through another path). Cancel reports "not active".

**Witness:** `test_sw_e16_reject_after_history_eviction_is_not_saved` (a history cap of 1 stands in for 500).

**Fix direction:** track restored paused tasks in a dedicated, unevicted map (or in `_active_tasks` with status PAUSED); reject and cancel persist from the handler's entry, not from the LRU.

**Gate + negative control:** reject and cancel after a forced eviction must persist; today's flow is the negative control.

### 11. SW-E08 — P2: outcome classification and retry are not truthful

**Half:** engine.

**Evidence:**
- `engine.py:1125-1135` maps every status other than timeout, failed, paused and cancelled — including `partial` (`dispatch_helpers.py:61`, `patterns.py:55-61`) — to `TaskStatus.COMPLETED` (merge check).
- `task_control.py:113-124` rebuilds a retry without `workspace_id`, `validation_schema`, `aggregation` and `priority`, and without looking at `retry_safe` / `execution_outcome`.
- `reliability.py:115-176` — `_attempt` converts every exception to a dict, so `_is_retryable_exception` (`reliability.py:47-65`) never runs for worker dispatch: 401, 403 and "invalid key" are retried. Its list also treats "rate limit" as permanent, the opposite of `LLMError` (429 is transient, AGENTS §3).

**Failure scenario:** a pipeline whose second step half-applied a write is listed and filtered as "completed". The operator presses Retry; the new task runs against whichever workspace is globally active, not the repository the original targeted, with no warning that the original left uncertain effects.

**Witnesses:** `test_sw_e09_failed_mutating_step_is_stored_completed`; `test_sw_e10_retry_drops_target_and_ignores_uncertainty`; `test_sw_e14_non_retryable_errors_are_retried` (three attempts on a 401 while the classifier itself says non-retryable).

**Fix direction:** add PARTIAL (or store `result_status` and an `effects_uncertain` flag) and map statuses exhaustively. Retry copies every execution-relevant field (a gate enumerates `SwarmTask` fields) and requires explicit acknowledgement when the original's outcome is unknown. Classify worker errors by a typed taxonomy shared with `LLMError.transient`.

**Gate + negative control:** an exhaustive status-mapping test that fails on any unmapped string; a field-coverage gate for `build_retry_task`; a retry-classification table test. The current mapping and copier are the controls.

### 12. SW-S04 — P2: an approval that times out leaves a live card and orphaned waiters

**Half:** surfaces.

**Evidence:** `SwarmMessageBus.request_approval` wraps the adapter in `asyncio.wait_for(timeout)` (`bus.py:381-389`). The adapters start their own equal timeouts later (`telegram_bus.py:380-386`; Discord `:233-240`; Slack `:270-275`), so the outer timeout fires first, and the adapter is cancelled before it edits the card (`telegram_bus.py:406-411`). Its inner `shared_task` and `local_task` are cancelled only on normal completion, so they are orphaned. FanOut cancellation (`bus.py:226-235`) and a swarm task cancelled while it awaits approval leave cards live the same way. The surfaces report states that the probe log showed "Task exception was never retrieved … TimeoutError"; the retained final-run log (`swarm-surfaces-probes.log`) does not contain that line (merge check), so it comes from an earlier run.

**Failure scenario:** the card keeps "APPROVAL REQUIRED … auto-reject in 300s" with working buttons forever. Depending on a race, the durable row ends either `pending` or resolved False by an orphan. A later click on a pending row writes a permanent True, which feeds SW-S02.

**Witness:** `test_sw_s04_timeout_leaves_the_card_live_and_the_row_pending` — the card is posted with buttons and never edited; the durable row is left `pending` or `resolved` depending on the race.

**Fix direction:** one deadline owner. The adapters' `finally` must cover `CancelledError`: edit the card to "Timed out" or "Cancelled", and cancel and join the inner tasks. Record a timeout as `timeout`, not as `deny`.

**Gate + negative control:** a timeout and a cancellation each end with an edited card and no pending tasks; the current adapter is the negative control. The regression must be deterministic, unlike the witness.

**Related:** SW-S02, SW-S15 (timeouts recorded as `deny`).

### 13. SW-S05 — P2: FanOut — the platform where a human pressed Reject says REJECTED while the tool runs

**Half:** surfaces.

**Evidence:** a Discord Reject sets its local result to False and edits that card to "❌ REJECTED" (`discord_bus.py:247-266`). Shared tri-state counts an explicit human Reject as a vote (`shared_approvals.resolve:223-236`), so a later Telegram Approve wins (`bus.py:216-225`). Deny-as-vote is the declared policy (AGENTS §7B, H-12), but the rejecting card's text becomes false and nothing updates it.

**Failure scenario:** the operator presses Reject on Discord and sees REJECTED; someone else approves on Telegram, and the tool runs.

**Witness:** `test_sw_s05_fanout_rejecting_card_says_rejected_while_the_tool_is_approved` — FanOut returns True while the Discord card reads REJECTED.

**Fix direction:** decide explicitly: either an explicit human Deny is final (only a timeout is a vote), or the card says "Your reject was recorded; waiting for N other platforms". Either way, edit every card with the final outcome.

**Gate:** a two-adapter test asserting that every card shows the final decision.

### 14. SW-S11 — P2: the bus classifier is not the tier floor that AGENTS.md and the docs describe

**Half:** surfaces.

**Evidence:** `SafetyMiddleware.is_danger_tool` gates only ALWAYS_HITL tools, listed tools and tier `danger`/`unsafe` (`safety.py:178-202`, merge check). `requires_approval` gates unknown tiers too (`hitl.py:596-674`, audit F-04 default-deny). AGENTS §7B says "H-9: `is_danger_tool()` must call `requires_approval()`"; `swarm-orchestration.md:28-30` says the same. Mitigation: `test_every_registered_tool_has_a_tier` covers registered builtins; skill and runtime-added tools are not covered.

**Witness:** `test_sw_s11_unclassified_tool_is_gated_by_graph_but_not_by_swarm_bus`.

**Fix direction:** delegate to `requires_approval` (minus YOLO and grants, which `check` applies itself).

**Gate:** parity across `TOOL_TIERS` plus an unknown name.

### 15. SW-S10 — P2: worker output reaches the chat model and the next worker unfenced

**Half:** surfaces.

**Evidence:** `check_swarm_task` returns the aggregated or worker output raw to the supervisor model (`system.py:833-883`). The pipeline hand-on (`Previous worker (...) output:` plus the blackboard snapshot) is unfenced (`patterns.py:256-283`). The worker's user message concatenates its context raw (`worker.py:395`). Worker text is model output derived from fenced web and MCP content, so it is a laundering channel past AGENTS §26G. The per-function fence gate `FENCED_TOOL_FUNCTIONS` (`tests/test_static_gates.py:1811`) does not list `tool_builtins/system.py` (merge check: the file name does not occur in `test_static_gates.py`).

**Witness:** `test_sw_s10_check_swarm_task_returns_worker_output_unfenced` — an injection string in a stored result comes back verbatim with no `<kazma:data` fence; a source check confirms the pipeline hand-on is unfenced.

**Fix direction:** `fence_untrusted(source="swarm_worker:<name>")` at `check_swarm_task` and at every inter-worker hand-on (pipeline, handoff, blackboard).

**Gate:** add the swarm readers to `FENCED_TOOL_FUNCTIONS`, with a behavioural test and a negative control.

**Related:** SW-S01 (with SW-S01 open, laundered text can steer `dispatch_swarm` and the worker's tools run unasked).

### 16. SW-S08 — P2: the Swarm page misreports approvals and runs every dispatch in the foreground

**Half:** surfaces.

**Evidence:**
- **Approve route:** returns `status: result.status` (`"success"`, `"paused"` …), never `"ok"`, and holds the HTTP request until the rest of the pipeline has run (`routes_tasks.py:742-757`; `engine.py:1316-1405`).
- **Approve handlers in `swarm.js`:** `approvePipelineCheck` requires `data.status === 'ok'` (`:2541-2558`), so a real success shows "Approval failed: Checkpoint approved, pipeline resumed". `approveCheckpoint`, `rejectCheckpoint` and `approvePlaygroundHitl` (`:920-940`, `:2832-2880`) never read `r.ok`, so a 403, 409 or 500 shows "Checkpoint approved" and hides the card.
- **Dispatch:** none of the three page dispatches sends `background` (`:543-551`, `:2360-2372`, `:2626-2632`). The POST holds for the whole task (300 s by default, unbounded with `timeout: 0`, see SW-S14). The "live" stream is opened only after completion (`:606`, `:781`). Behind a proxy with a response timeout (Cloudflare's is about 100 s), a long task shows an error while the work continues.
- **Language:** the English words "approving" and "rejecting" are injected into an Arabic string (`:2556`, `:2576`); the catalog's `swarm.network_error_approving` exists and is not used.

**Witnesses:** `test_sw_s08_approve_answer_and_the_page_disagree` measures the route's answer for a successful approve and for a 403 and compares it with what the handlers check; `test_sw_s08b_no_dispatch_from_the_page_asks_for_background`. The JavaScript half was checked from source, not in a browser.

**Fix direction:** answers carry `{status: ok|error, task_status}`; approve claims the gate, returns 202 and resumes in the background; the page reads `r.ok` everywhere, dispatches with `background: true` and connects the stream immediately.

**Gate + negative control:** a browser test through a proxy that cuts responses at 30 s, in English and Arabic; the current page is the negative control.

**Related:** SW-E01 and SW-E05 (the background resume must be the RUNNING transition with the step journal), SW-E02 (a foreground dispatch registers no handle, so its Stop is cosmetic).

### 17. SW-S07 — P2: mutating swarm routes are inconsistently gated

**Half:** surfaces.

**Evidence:**
- **Admin-gated:** templates and reap (`routes_general.py:158-246`); approve, reject, cancel and retry (`routes_tasks.py:107-122`).
- **Not gated:** adding a worker (`POST /api/swarm/workers`), spawning one (`/workers/spawn`) and updating one (`PUT /workers/{name}`), all three persisting an arbitrary `system_prompt` into the WorkerRegistry; `DELETE /api/swarm/workers/{name}`, start and stop (all and per worker) and the breaker reset (`routes_workers.py:118-475`); `PUT /api/swarm/output-target`, which sets the Telegram chat and bot token that receive swarm results (`routes_general.py:114-132`); `POST /api/swarm/dispatch` (`routes_tasks.py:314`).
- `/api/swarm` is not in `_ADMIN_PREFIXES` (`platform_rbac.py:50-70`), so an operator-role user passes. The audit-H-5 rationale for templates ("workers that spawn with arbitrary system prompts") applies equally to workers. The gateway's equivalent, `/swarm config`, is admin-only (AGENTS §46).
- Under THREAT_MODEL §6 (one operator) this is not a security boundary. It matters for installs that turn on platform roles.

**Witness:** `test_sw_s07_worker_and_output_target_writes_skip_the_admin_gate` sets `admin_decision` to "forbidden": templates answer 403 (the control), while worker create and update persist `system_prompt`, the output target changes, and the breaker reset succeeds.

**Fix direction:** one route-to-role table for `/api/swarm/*`. The surfaces half suggests: reads for viewers; dispatch, cancel and approve for operators; workers, templates and routing for admins.

**Gate:** enumerate the router's routes and fail on any route missing from the table, like `tests/test_thread_route_policy.py`.

### 18. SW-E07 — P2: blocking database and settings I/O on the event loop for every dispatch

**Half:** engine.

**Evidence:** `engine.py:398-405`, `1189-1196`, `1327-1330` (`persist_task` / `get_task` on the loop); `checkpoint_manager.py:205-212`; `metrics.py:135-153` (one SQL write per worker result); `reliability_registry.py:70-85` and `reliability.py:330-351`, `482-574` (with shared breakers — on by default in production and multi-user — a ConfigStore read on every `get_circuit_breaker` / `check_or_raise` and a write per outcome); `routing_engine.py:117-128` with `semantic_router.py:154-161`, `230-237` (the first auto-route loads the shared encoder on the loop; every auto-route embeds on the loop). The static gates cannot see these: they are sync helpers called from async code, and `persist_task` / `get_task` are not in `_LOOP_STALL_HELPERS` (`tests/test_static_gates.py:886`) while `delete_task` is (merge check: `delete_task` at `:966`; `persist_task` and `get_task` absent).

**Failure scenario:** during a Postgres hiccup (AGENTS §44) each swarm dispatch blocks the loop for up to the pool timeout several times; a first `workers=["auto"]` task stalls every chat stream while the embedding model loads.

**Witnesses:** `test_sw_e11_breaker_and_store_io_on_event_loop` (3 breaker settings round trips and 2 TaskStore writes per dispatch, all on the loop thread); `test_sw_e11b_auto_routing_embeds_on_event_loop`.

**Fix direction:** an async facade for TaskStore and metrics (`to_thread`); breaker state read and written off the loop (or cached with an async refresh); routing embeddings via `to_thread`. Add the helpers to `_LOOP_STALL_HELPERS`.

**Gate + negative control:** a behavioural tracer like `tests/test_get_routes_off_the_loop.py` that runs a swarm dispatch of every task type and fails on any SQLite statement or settings call on the loop thread; the current code is the negative control.

**Related:** SW-S12 (same AGENTS §35 rule on the approval path); one tracer can cover both.

### 19. SW-S12 — P2: the approval path reads and writes the settings store on the event loop

**Half:** surfaces.

**Evidence:** `SafetyMiddleware.check` is async. It reaches `get_hitl_config`, a declared loop-stall helper (`tests/test_static_gates.py:903`, merge check), through `is_danger_tool` and the `approval_timeout` property; the name-based gate cannot see calls through a method or a property. In the adapters' async waits, `shared_approvals` reads the ConfigStore every 150 ms per adapter and writes it (`shared_approvals.py:295-311`, `create_pending`). Platform callbacks call `resolve`, which runs `atomic_update` on the loop. AGENTS §35 forbids settings-store use on the loop: on Postgres a cache miss or a write is a round trip under the store lock.

**Witness:** `test_sw_s12_bus_approval_touches_settings_on_the_event_loop` traces `get_hitl_config`, `config_store.get` and `config_store.set` on a running loop.

**Fix direction:** read the configuration once off the loop per check; move `shared_approvals` I/O into `to_thread` or an async store; replace polling with an event plus a slower durable poll.

**Gate:** extend the loop-stall gate to methods and properties of `SafetyMiddleware` and `shared_approvals`.

**Related:** SW-E07.

### 20. SW-S09 — P2: the task stream replays everything on reconnect, has no event ids or keepalive, and is unbounded

**Half:** surfaces.

**Evidence:** each connection replays the full `get_history` (`swarm_sse.py:281-283`) and frames carry no `id:` (`:283`, `:305`, `:313`). Nothing is sent while a task is quiet, not even a comment line. Every open stream reads the TaskStore once a second (`:298-299`). There is no cap on concurrent streams and no maximum lifetime; a paused pipeline without `checkpoint_timeout` keeps its streams polling indefinitely. The client reconnects up to 10 times (`swarm.js:712-779`) and re-appends every line through `addEventLine`. The stream route has no per-task authorization beyond the session (`:187`).

**Witness:** `test_sw_s09_reconnect_replays_without_ids_and_no_keepalive` — two connections receive identical full histories, no `id:` lines, and no frame during 2.5 s of a quiet running task.

**Fix direction:** sequence ids and `Last-Event-ID` resume; a heartbeat comment at least every 15 s; caps per principal and in total, and a maximum lifetime; deduplication on the client.

**Gate:** a re-chunking proxy test (the pattern of `tests/e2e/test_chunked_stream_browser.py`) asserting no duplicate lines after a reconnect.

### 21. SW-E10 — P3: semantic routing mixes two embedding models and fails silently

**Half:** engine.

**Evidence:** `semantic_router.py:109-141` creates the Chroma collection without an `embedding_function`; `:216-223` adds profiles as raw `documents` (embedded by Chroma's default function); `:241-258` queries with Kazma's encoder (`BAAI/bge-m3`, 1024-d). With chromadb 1.5.9 installed in the dev environment this path is live. Chroma's documented default embedder is a 384-d MiniLM model fetched on first use, so queries are expected to fail on dimensionality and fall back to keyword routing with a WARNING per auto-route (not executed: it would download a model). AGENTS §24F's "every write and search passes its own vectors" rule is not followed here. Separately, auto-routing records up to five workers in `task.workers` (`routing_engine.py`, `DEFAULT_TOP_N=5`) while a DISPATCH runs only `task.workers[0]` (`dispatch_inner.py:247`), so the stored row names workers that never ran.

**Witness:** `test_sw_e11c_semantic_router_mixes_embedding_spaces` (profiles added without vectors; a 1024-d query; no `embedding_function` in the module).

**Fix direction:** embed profiles with the same encoder (`to_thread`), create the collection with no default function, test against the real chromadb in the `rag` CI path; record only the executed worker and keep the candidates in routing diagnostics.

### 22. SW-E11 — P3: circuit-breaker outcomes are not tied to the probe; shared mode fails open quietly

**Half:** engine.

**Evidence:** `reliability.py:356-399` — any `record_success` / `record_failure` clears `_probe_in_flight` and decides the half-open state, including outcomes of dispatches admitted while CLOSED. `reliability.py:522-526` — a ConfigStore failure grants the probe lease ("fail-open") at DEBUG. `reliability.py:330-351` with `persist_shared` is read-modify-write without CAS (lost updates between replicas). Keys `swarm.breaker.<name>` accumulate for every ad-hoc and pool name. A user cancellation counts as a worker failure (`worker_dispatch.py:396-402`).

**Witness:** `test_sw_e12_stale_success_closes_half_open_breaker` (control: a second probe is refused; a stale success then closes the breaker while the probe is in flight).

**Fix direction:** tag each admission with the breaker generation or probe token; only the probe's outcome resolves HALF_OPEN; stale outcomes update counters only. Shared mode: CAS updates, a WARNING on lease-store failure, TTL or cleanup for transient worker keys; exclude user cancellation from failure counts.

### 23. SW-E12 — P3: the autoscaler can reap a pool worker with a dispatch in flight

**Half:** engine.

**Evidence:** `worker.py:67`, `118-133` — `busy` is a bool set and cleared per dispatch. `autoscaler.py:399-431` reaps when `busy` is false and the last activity is older than the TTL (busy check at `:421-422`, merge check). Pattern tasks never use `spawn_by_name` (only a single DISPATCH does, `dispatch_inner.py:247-263`), so a template name in a pipeline fails "not found". Pool instance names restart at `-pool-1` after a reboot and inherit shared breaker state under the same key.

**Witness:** `test_sw_e13_autoscaler_reaps_worker_with_dispatch_in_flight`.

**Fix direction:** an in-flight counter (or lease) per worker, reaping only at zero; per-boot unique instance ids; one name-resolution function for all patterns.

### 24. SW-E13 — P3: handoff and its cycle guard are unreachable from any shipped worker

**Half:** engine.

**Evidence:** no product module raises `HandoffRequest` or calls `request_handoff` (AST scan). `worker_factory.py` builds only `InProcessWorker`, whose `dispatch()` catches `Exception` (`worker.py:632`); `HandoffRequest` is an `Exception`, so a handoff raised inside its loop becomes an error result.

**Witness:** `test_sw_e15_no_shipped_worker_can_request_a_handoff`.

**Impact:** the AGENTS §4 mechanism and its tests exercise synthetic workers only; it is a library capability, not a product feature.

**Fix direction:** either expose a handoff tool (and let `HandoffRequest` propagate through the worker) with end-to-end tests, or document handoff as an extension point and stop describing it as live behaviour.

### 25. SW-E14 — P3: silent and double-counted failures (engine)

**Half:** engine.

**Evidence:** the RUNNING-row write — the only crash evidence — fails at DEBUG (`engine.py:398-405`; the witness log shows the write at `:400` and the DEBUG line at `:402`). Maintenance watchdog failures are DEBUG (`engine.py:650`, `656`), so a reaper failing every minute is invisible (AGENTS §15B requires WARNING). Gate register and settle failures are DEBUG (`checkpoint_manager.py:55-56`, `83-84`; `engine.py:1362-1363`). Breaker persistence uses `except: pass` (`worker_dispatch.py:336-344`, `engine.py:1081-1089`). A breaker refusal is recorded at `worker_dispatch.py:149` and again in `_finalize_task` (`engine.py:1146-1147`). `swarm_worker_metrics` is never pruned.

**Witnesses:** `test_sw_e17_running_row_failure_is_debug_only` (no row while the task runs); `test_sw_e19_open_breaker_rejection_counted_twice` (`tasks_failed == 2`).

**Fix direction:** WARNING (and an ops alert counter) for lost crash evidence and failing sweeps; record metrics once per result id; retention for worker metrics.

**Gate:** a log-level gate over `except` blocks in `kazma_core/swarm/`; with SW-S15, extend it to the bus adapters.

**Related:** SW-S15.

### 26. SW-S15 — P3: observability and notification gaps (surfaces)

**Half:** surfaces. No witness (code reading).

- **Wrong actor and outcome in the gate registry:** rows record actor `swarm_bus` or `pipeline`, never the human (`safety.py:482-486`, `checkpoint_manager.py:76`); a timeout is recorded as `deny`; the daily digest counts approvals from the gate registry (AGENTS §27E), so it reports bus timeouts as human denials.
- **Silent write failures:** `approve()` and `reject()` swallow durable-resolve failures with `except Exception: pass` in the Telegram, Discord and Slack bus adapters. AGENTS §45 calls this a bug.
- **A fourth notifier:** `swarm_notify.maybe_notify_dispatch` (`SWARM_BOT_TOKEN`, `swarm_notify.py:391-416`, called from `routes_tasks.py:282-311`) exists beside the three paths AGENTS §33 allows. It parses untrusted text as Markdown, and it returns True when Telegram answers `ok=false`, contrary to AGENTS §51.
- **Raw exceptions in chat:** exception text goes to the output chat (`routes_tasks.py:299`); the gateway uses `sanitize_error` instead.
- **Unretrieved task errors:** background handles re-raise and nothing retrieves them (`routes_tasks.py:312`, `:482-487`).

**Gate:** a digest / firing-ledger test showing human actors and separate timeouts; the debt ratchet lowered.

**Related:** SW-E14, SW-S02, SW-S04.

### 27. SW-E15 — P3: unbounded and unaccounted resources

**Half:** engine.

**Evidence:** `patterns.py:214-254` runs the Refiner LLM (`_synthesize_refined_output`, `patterns.py:105-145`) for every pipeline, but `dispatch_inner.py:100-109` and `engine.py:1397-1405` drop `synthesized_output`, and its tokens and cost are not added to the task. Pipeline results store every output in `worker_results`, `aggregated_output`, `metadata.blackboard` and `metadata.blackboard_snapshot`. Paused tasks are excluded from admission and unbounded in number. There is no engine-level cap on prompt or context size. Fan-out, consult and broadcast share one semaphore per limit across tasks (`reliability_registry.py:207-232`), so one task's width starves others with the same limit.

**Witness:** `test_sw_e18_refined_output_dropped_and_payload_amplified` — the Refiner is called once and its output is absent from the result; 4.51 stored bytes per output byte.

**Fix direction:** either return and account the refined output or remove the call; store outputs once and reference them; caps on paused tasks, prompt/context and result size; per-task plus global concurrency.

**Related:** SW-S14 (route-level bounds). One bound definition belongs at the engine, with the routes mapping it to 422.

### 28. SW-S14 — P3: no input bounds on dispatch

**Half:** surfaces. No witness (code reading).

**Evidence:** `_coerce_timeout` accepts 0, negative, huge or NaN values (`routes_tasks.py:60-65`); a value of 0 or less becomes "no deadline", a one-year sentinel (`dispatch_helpers.py:25-45`). `task`, `context`, `metadata` (including `hitl_checkpoints` and `routes`) and the worker list have no length or count bound. No global request-body cap exists; only Settings returns 413. Rate limiting applies only when auth is enabled (AGENTS §24C). `GET /api/swarm/tasks?q=` loads up to 10,000 full rows per request (`routes_tasks.py:583-597`).

**Fix direction:** clamp the timeout to a range (the surfaces half suggests 10 to 3,600 s); cap prompt and context bytes, workers and checkpoints, and reject out-of-range values with 422; search in SQL with a limit.

**Related:** SW-E02 (the watchdog reaps "no deadline" tasks at 330 s), SW-E15.

### 29. SW-E16 — P3: backend parity and durability gaps

**Half:** engine.

**Evidence:** Postgres `prompt` / `context` are `TEXT` (`postgres_pool.py:270-284`) and are passed raw (`task_store.py:305-308`, now about `:307-310`) while only JSON is NUL-stripped; `pg_helpers.strip_nul` itself documents that Postgres "cannot store NUL in text". SQLite runs `synchronous=NORMAL` (`config_store.py:397-407`), so the RUNNING row can be lost on power failure (the effect journal uses FULL). The Temporal activity's `start_to_close` is `max(timeout, 60) + 120` s for the whole task (`durable_temporal.py:31-38`) while the engine budgets patterns at `timeout × steps + 30` s, and an approved resume runs outside Temporal. Rows carry no owner or run id, so orphan recovery is correct only because `RuntimeOwnership` guarantees one runtime.

**Witness:** `test_sw_e20_postgres_text_columns_not_nul_stripped` (fake pool; prompt and context reach TEXT with NUL; result JSON clean).

**Fix direction:** strip NUL (or reject) for every TEXT parameter; `synchronous=FULL` for the task store, or move crash evidence to the effect-journal database; align Temporal timeouts with pattern budgets and route resumes through the durable path; add owner/run id with SW-E03.

### 30. SW-S13 — P3: bus adapters post worker text without the chat adapters' mention sanitizer

**Half:** surfaces.

**Evidence:** `discord_bus.py:102-128` and `:190-220`, and `slack_bus.py:107-125`, post raw content; `discord_send.sanitize_outbound` and `slack_send.sanitize_outbound` (audit G9b) are not used; no `allowed_mentions` is set.

**Witness:** `test_sw_s13_bus_adapters_skip_the_mention_sanitizer_the_chat_adapters_use` — `@everyone` and `<!channel>` reach the payloads; the sanitizers (the control) neutralize them.

**Fix:** sanitize in the bus adapters, with a gate covering every Discord and Slack post path.

### 31. SW-S16 — P3: tenancy and privacy (multi-user deployments only)

**Half:** surfaces. No witness (code reading). THREAT_MODEL §6 (THREAT_MODEL.md:250) declares multi-tenant isolation out of scope; the finding matters only for installs that turn on multiple tenants.

- **Tenant checks:** the list is tenant-filtered in multi-user mode, but detail, stream, active-task list, approve, reject, cancel and retry are addressed by id with no tenant check (`routes_tasks.py:548-873`, `swarm_sse.py:187`), and `/tasks/active` lists every tenant's in-flight tasks.
- **Platform ids in the Web API:** gateway-dispatched tasks persist `source_chat_id` and `source_user` (`swarm_dispatch.py:470-477`); the Web API and the CSV export return them.
- **Routing:** the one-off `-> telegram:<chat_id>` routing is open to any allowlisted user, while `/swarm config` is admin-only (`commands.py:136`; `swarm_dispatch.py:295+`).
- **Misleading timeout reply:** the gateway timeout reply says "Partial work was discarded" (`swarm_dispatch.py:565-569` in the report; `:566-570` at merge time). Side effects already performed are not discarded. Merge note: SW-E04 shows this branch is unreachable today; it becomes reachable when SW-E04 is fixed.

### 32. SW-S17 — P3: docs drift

**Half:** surfaces. No witness.

- `swarm-orchestration.md:301-302` and `api-and-extension-points.md:109-110` cite `routes_tasks.py:612/657`; the routes are now at lines 699 and 759.
- `swarm-orchestration.md:243` says the TaskStore migration is "wrapped in try/except"; the code uses `add_missing_columns` (`task_store.py:28`), as AGENTS §6 requires.
- The H-9 claim (SW-S11) appears in both AGENTS §7B and `swarm-orchestration.md:28-30`.
- Neither AGENTS §7A (double-gating prevention) nor §30 ("decision truth = hitl_gates.db") holds for detached swarm work or the swarm bus (SW-S01, SW-S02).
- `api-routes.md:45` documents `/api/swarm/*` as "Session / RBAC" without saying which mutators are admin-only (SW-S07).
- Merge check, not in the surfaces report: AGENTS.md:2055-2056 lists `side_effects.py` under "Components (`kazma_core/safety/commitment/`)"; the file is `kazma-core/kazma_core/safety/side_effects.py`. It is a path drift only, recorded here so the docs pass corrects it.

## AGENTS.md claims that do not match the code

Rows come from both halves: the engine half's table, plus the AGENTS claims the findings themselves cite (SW-S01, SW-S02, SW-S05, SW-S10, SW-S11, SW-S15, SW-S17, SW-E14). Cells marked "merge check" were confirmed or added by reading the code at merge time; only the §20D row is new. Two attributions in the engine half's table are corrected: the phrase "statuses a task never leaves" is in `swarm/task.py:100` and `:114`, not in AGENTS.md; and "skips busy workers" is the autoscaler's own code (`autoscaler.py:421-422`), not AGENTS.md text.

| AGENTS.md claim | What the code does | Finding |
|---|---|---|
| §7A: the graph's ContextVars (`_graph_hitl_gate_ctx` / `_hitl_approved_ctx`) are double-gating prevention, so `LocalToolRegistry.execute` does not re-prompt the bus | The flag is node-wide (`graph_tool_worker.py:815-818`) and is inherited by detached swarm work started inside the node, whose danger tools then reach no gate at all | SW-S01 |
| §7B: "H-9: `is_danger_tool()` must call `requires_approval()` (tier floor)" | `is_danger_tool` checks ALWAYS_HITL, the listed tools and tier `danger`/`unsafe` only; unknown tiers are not gated by the bus (`safety.py:178-202`) | SW-S11 |
| §30: "one gate, one row, one truth"; "Decision truth = hitl_gates.db" | Swarm-bus decisions live in ConfigStore rows `swarm.approval.<id>`, are never deleted, and a settled True approves later requests; the gate row is settled afterwards with actor `swarm_bus` | SW-S02, SW-S15 |
| §7B: swarm fan-out is tri-state; `False` is a vote | Accurate as code. The rejecting platform's card still reads REJECTED when another platform approves | SW-S05 (gap, not mismatch) |
| §7C: "Every ending of a task goes through `SwarmEngine._finalize_task`" | Not for a cancelled `engine.broadcast()` (`broadcast.py:91` catches `Exception` only); a terminal ending can be undone by a later pause | SW-E06 / SW-S06, SW-E03 |
| §7C: a pipeline paused before a restart is restored into history only; "reject and cancel reach it there" | Not after history eviction: reject answers success and saves nothing; cancel says "not active" | SW-E09 |
| §14: "Finished" is one list (`TERMINAL_STATUSES`); its definition calls these the statuses "a task never leaves" (`task.py:100`, `:114`) | TIMEOUT → PAUSED → COMPLETED reproduced | SW-E03 |
| §14: idle-reap after 5 min; `record_activity` refreshes the timer | The reap's busy check (`autoscaler.py:421-422`) reads a bool that overlapping dispatches clear, so a worker with a dispatch in flight can be reaped | SW-E12 (incomplete, not wrong) |
| §30G: effect uncertainty and `retry_safe=False` "stop both retry and fallback chains" | True inside the worker `TimeoutGuard`; lost on every outer deadline, pattern step deadline, caller cancel, reap and shutdown | SW-E04 |
| §5: "only ONE dispatch passes through half-open state" | A stale outcome of a dispatch admitted while CLOSED closes half-open while the probe runs | SW-E11 |
| §35: the settings store is never used on the loop; §26E: no blocking I/O inline in async | Shared breakers, the TaskStore, metrics and routing embeddings on every dispatch; the approval path through `get_hitl_config` and `shared_approvals` | SW-E07, SW-S12 |
| §26G: fetched/untrusted text is fenced before it reaches the model | `check_swarm_task` and the pipeline hand-on pass worker output unfenced; `FENCED_TOOL_FUNCTIONS` does not list `tool_builtins/system.py` | SW-S10 |
| §20: the default worker scope (HIGH cap) "denies exec/outbound/config/identity CRITICAL" | Accurate for tools classified CRITICAL. `send_message` and `vault_retrieve` are classified NONE (merge check: `safety/side_effects.py:186`, `:148`), so the cap does not stop them | SW-S01 |
| §20D: `side_effects.py` is listed under "Components (`kazma_core/safety/commitment/`)" (AGENTS.md:2055-2056) | The file is `kazma-core/kazma_core/safety/side_effects.py`; there is no `side_effects.py` in `safety/commitment/` (merge check) | none (merge check; listed under SW-S17 for the docs pass) |
| §33: "three paths, no fourth notifier" | `swarm_notify.maybe_notify_dispatch` sends swarm dispatch notices through its own `SWARM_BOT_TOKEN` path | SW-S15 |
| §51: a send says whether it was delivered | `swarm_notify` returns True when Telegram answers `ok=false` | SW-S15 |
| §45: a silent handler around a write is a bug | Bus adapters' `approve()` / `reject()` swallow durable-resolve failures; engine breaker persistence uses `except: pass` | SW-S15, SW-E14 |
| §15B: a failing maintenance sweep is logged at WARNING | The swarm watchdog's failures are DEBUG (`engine.py:650`, `656`) | SW-E14 |
| §4: handoff cycle detection (depth 5, two visits) | Accurate as code; no shipped worker can raise a handoff, so it runs only for synthetic test workers | SW-E13 |
| §6: WAL + `busy_timeout`, `json_each` worker filter, `add_missing_columns` migrations | Accurate. The store runs `synchronous=NORMAL` | SW-E16 (note) |
| §9: the SwarmEngine split | Accurate | — |

## Staged path to industrial readiness

The engine half ordered its stages lifecycle first; the surfaces half ordered approval boundary first. This merge puts the approval boundary first: it is reachable by untrusted content, it is the boundary the threat model names, and its fixes do not depend on the lifecycle rework. The lifecycle stages follow in the engine half's order. Stage 4's approve-in-background (SW-S08) depends on Stage 3's resume transition.

Every stage's exit gate requires: the stage's witnesses go red, and each is inverted into a regression test in `tests/` with the old behaviour as its negative control (AGENTS §28); class gates enumerate siblings from the real source (AGENTS §35).

### Stage 1 — Restore the approval boundary (SW-S01, SW-S02, SW-S03, SW-S11, SW-S04, SW-S05)

1. Graph authority per call; clear the HITL ContextVars at every detached-work boundary.
2. Approval ids minted only by the gate registry; `hitl_gates.db` the single decision record, with the human actor and a separate `timeout` outcome; sticky shared rows deleted once their waiter has read them.
3. Every argument shown on bus cards, bounded and marked when cut.
4. `is_danger_tool` equal to the `requires_approval` floor.
5. Every adapter exit updates its card; the FanOut deny policy decided and every card shows the final outcome.

**Exit gate:**
- Every SW-S01–SW-S05 and SW-S11 witness inverted into a regression with the old code as its negative control; `test_both_gates_honour_an_actual_approval` and `test_an_explicit_task_id_is_still_used` re-pointed.
- A two-platform drill (Telegram and Discord test bots, or a recorded fake) shows a correct final card on every platform for approve, deny, timeout and cancel.
- No danger call from any detached path runs without a card (starters enumerated from source).

### Stage 2 — One owned lifecycle (SW-E02, SW-E03, SW-E06 / SW-S06, SW-E05, SW-E09; owner/run id from SW-E16)

One transition table with CAS in both stores (status, owner/run id, generation, started/heartbeat); `dispatch()` the only entry point (broadcast, resume, retry and durable activities call it); the engine creates and registers the execution task itself; cancel, timeout and reap cancel, then record; idempotent admission on the task id; restored paused tasks held outside the LRU.

**Exit gate:**
- Transition property tests on SQLite and real Postgres.
- Every entry point enumerated from source; cancel and reap stop the coroutine before the terminal write.
- A duplicate submission executes once.
- Restored and resumed tasks count in admission and the watchdog.
- No product caller of `engine.broadcast` outside the engine; admission refusals are errors (429/503).

### Stage 3 — Effect-safe interruption and resume (SW-E01, SW-E04, SW-E08)

A task-level effect tracker attached to every result; stable `(task, step, generation)` identities passed to the effect journal; a durable step journal for patterns; resume is a recorded RUNNING transition; boot reconciliation holds begin-without-commit steps as `unknown`; truthful status mapping; retry copies every execution field and requires acknowledgement after uncertain effects.

**Exit gate:** a process-kill matrix (a real child process, not coroutine cancellation) at each pattern step, approve, resume and finalize boundary, on both backends: zero duplicate effects, zero re-offered approvals, every interruption recorded as `unknown` with the invoked tools; callers' deadlines raise `TimeoutError`.

### Stage 4 — Truthful, live and non-blocking (SW-S08, SW-E07, SW-S12, SW-S09, SW-S14, SW-E15)

The page dispatches in the background, streams live, reads every answer, and is translated in both languages; approve claims, answers 202 and resumes through Stage 3's transition; the stream gets ids and resume, a heartbeat, caps and a lifetime; engine and approval I/O move off the loop; dispatch inputs and stored payloads are bounded.

**Exit gate:**
- A Playwright run through a proxy that cuts responses at 30 s and re-chunks streams, in English and Arabic: a 5-minute pipeline with a checkpoint shows live progress, a correct approve result and no duplicate lines.
- A loop-thread tracer green for every task type and for the approval path; a measured loop-stall budget under a 10-task concurrent load.
- No loop stall above 1 s during 20 parallel approvals on a Postgres settings store.
- Admission refusals and out-of-range inputs answer as errors.

### Stage 5 — Boundaries, correctness hygiene and observability (SW-S10, SW-S07, SW-E10, SW-E11, SW-E12, SW-E13, SW-E14, SW-S15, SW-E16, SW-S13, SW-S16, SW-S17)

Fence worker output at every hand-on; a `/api/swarm/*` route-to-role table; semantic routing fixed or removed; probe-tied breaker outcomes and CAS shared state; in-flight counters for reaping; handoff made reachable or documented as an extension point; WARNING-level crash-evidence and sweep failures with metrics; human actors and separate timeouts in gate rows; `swarm_notify` folded into the ops-alert routes; NUL parity and durable crash evidence; aligned Temporal timeouts; mention sanitizing on bus posts; tenant checks for multi-user installs; docs corrected.

**Exit gate:**
- Route-policy test green; the fence gate covers the swarm readers.
- No WARNING-free failure path (a log-level gate over `except` blocks in `kazma_core/swarm/` and the bus adapters).
- The weekly report shows swarm approvals with human actors and separate timeouts.
- Docs line references checked or removed; anchor and file-ref gates green.

### Stage 6 — Qualification

A concurrency soak (admission, breakers, autoscaler under hundreds of mixed tasks); Postgres parity in the CI Postgres job; Temporal with a time-skipping test server in a dedicated CI job; chaos (DB locked or down, Temporal unreachable, shutdown mid-pattern); published limits (max tasks, max width, max payload, recovery semantics). Then revise AGENTS.md §4, §5, §7, §14, §20, §30 and §33 to the measured behaviour.

## Limits

- This merge adds no findings and runs nothing. Its merge checks are reads of the code on 2026-10-13, after both audits; they confirm cited lines and note one post-audit edit (`swarm/task_store.py`).
- Each half's limits stand: no real Postgres, Temporal, Chroma, LLM, browser, platform API, multi-process, load, soak or process-level fault run; process death simulated by coroutine cancellation and store reopen; the SW-S01 exploit chain not executed; SW-S08 JavaScript read from source; the full suite not run.
- One unplanned network request (Hugging Face metadata) was sent on the surfaces half's first probe run; later runs were offline by environment, which no tracked file enforces.
- The scores are the auditors' judgments. The witnesses establish specific defects, not overall quality. A green witness confirms a defect; it does not certify anything.
- SW-S07 and SW-S16 matter for installs that use platform roles or multiple tenants; THREAT_MODEL.md §6 declares multi-tenant isolation out of scope, and they are ranked accordingly.
- Line numbers refer to the reviewed state and drift; task_store.py lines after about line 175 are already 2 higher.
- The SW-S04 witness accepts a race in the durable row state; its regression must not.
