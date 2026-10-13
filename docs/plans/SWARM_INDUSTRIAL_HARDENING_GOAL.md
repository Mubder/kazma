# Swarm industrial hardening — continuous execution

Status: OPEN -- 2026-10-13

Objective: execute the complete Swarm audit remediation and qualification
plan, with current evidence for each requirement. Industrial readiness is not
reached by fixing only the witnessed defects or by a green unit suite: the
existing Swarm suites (177 engine and 193 surfaces tests) were green with
every witnessed defect present. Two things must become true: swarm approvals
are a real boundary (no danger tool runs without a human decision the human
could read), and the task lifecycle is an owned state machine (an ending
stops the work, terminal states are final, an interruption is recorded as
uncertain, and nothing approved runs twice).

Baseline: the uncommitted working tree of 2026-10-13 on top of commit
`d7e27c194d3f170092a9c405709b9197a2c00161`. When audited, no Swarm engine
file differed from that commit. After the engine report was written,
`kazma-core/kazma_core/swarm/task_store.py` gained a three-line Postgres DDL
change (`ensure_schema(cur, ddl_extra)`, around lines 176-180); it touches no
cited defect but shifts later task_store.py line numbers by +2.

- Audit: `docs/audits/AUDIT_SWARM_2026-10-13.md`
  (merged; 32 entries from 33 findings: P1 7, P2 13, P3 12). Halves:
  `swarm-engine.md` (scores 6/10 capability and architecture, about 3/10
  demonstrated industrial readiness) and `swarm-surfaces.md` (6/10, about
  4/10).
- Witnesses, in `session-artifacts/audits-2026-10-13/`:
  `test_swarm_engine_witnesses.py`, **24 passed in 3.91 s**
  (`swarm-engine-witnesses.log`), and
  `test_swarm_surfaces_probes_2026_10_13.py`, **15 passed in 20.30 s**
  (`swarm-surfaces-probes.log`). Each witness asserts that a defect is
  present. **Witnesses must never become permanent tests that require the
  defects.** When a fix lands, its witness goes red (that red run is
  evidence), and the fix gets a regression in `tests/` asserting the fixed
  behaviour with the original defect as its negative control. The engine
  witness function numbers do not match the finding IDs; use the witness
  index in the merged audit.
- Existing regression subsets: engine 177 passed in 16.71 s
  (`swarm-engine-regression.log`); surfaces 193 passed in 26.26 s
  (`swarm-surfaces-existing-gates.log`); JS `test_swarm_event_text.js` and
  `test_swarm_breaker.js` ok (`swarm-surfaces-js.log`). The two subsets
  share `tests/test_swarm_auto_worker.py` (12 tests).
- Not yet measured anywhere: real Postgres, Temporal, Chroma, a browser, real
  chat platforms, a real process kill, load or soak. Neither half ran the
  full suite.

## Stages

Rows below are ordered by stage. Each stage's exit gate is part of the
evidence for its rows.

| Stage | Rows | Exit gate |
|---|---|---|
| 1. Approval boundary | SW-S01, SW-S02, SW-S03, SW-S11, SW-S04, SW-S05 | Witnesses inverted; two-platform drill (approve, deny, timeout, cancel) shows a correct final card on every platform; no danger call from any detached path runs without a card |
| 2. One owned lifecycle | SW-E02, SW-E03, SW-E06 / SW-S06, SW-E05, SW-E09 | Transition property tests on SQLite and real Postgres; every entry point enumerated; cancel/reap stop the coroutine before the terminal write; duplicate submission executes once; resumed and restored tasks admitted and watched; no `engine.broadcast` caller outside the engine |
| 3. Effect-safe interruption and resume | SW-E01, SW-E04, SW-E08 | Real child-process kill matrix at every step/approve/resume/finalize boundary on both backends: zero duplicate effects, zero re-offered approvals, every interruption `unknown` with its invoked tools |
| 4. Truthful, live, non-blocking | SW-S08, SW-E07, SW-S12, SW-S09, SW-S14, SW-E15 | Playwright through a 30 s-cutting, re-chunking proxy in English and Arabic (5-minute pipeline with a checkpoint); loop tracer green for every task type and the approval path; no stall above 1 s during 20 parallel approvals on a Postgres settings store |
| 5. Boundaries, hygiene, observability | SW-S10, SW-S07, SW-E10, SW-E11, SW-E12, SW-E13, SW-E14, SW-S15, SW-E16, SW-S13, SW-S16, SW-S17 | Route-policy and fence gates green; log-level gate over `except` blocks in `swarm/` and the bus adapters; weekly report shows human actors and separate timeouts; docs references checked |
| 6. Qualification | SQ01–SQ05 | See the qualification table |

## Requirements and completion evidence

| ID | Required result | Evidence required | State |
|---|---|---|---|
| SW-S01 | Detached work started from a chat turn never inherits graph HITL authority: graph authority is per gated tool call (set only around `_exec_one` for the calls the graph gated), and both HITL ContextVars are cleared at every detached-work boundary (`engine.dispatch`, every `create_task` / `spawn_background` reachable from a tool, sub-agents, research jobs). A swarm worker's danger tools reach the bus | `test_sw_s01_dispatch_swarm_from_a_graph_turn_skips_every_approval` red; regression in `tests/` (tool refused, bus asked once) with the node-wide flag as negative control; a gate that enumerates detached-work starters from source and runs the behavioural check for each; `tests/test_bus_approval_can_be_answered.py:171` (`test_both_gates_honour_an_actual_approval`) re-pointed, not deleted | Open |
| SW-S02 | Approval ids are minted per request by the gate registry and never taken from tool arguments; each decision is bound to `(gate_id, tool, args_hash)`; `hitl_gates.db` is the one decision record, claimed by CAS with the human actor; shared `swarm.approval.*` rows are removed or expired once their waiter has read them; a callback for an unknown or settled id is a no-op | `test_sw_s02_a_reused_approval_id_is_approved_without_a_click` and `test_sw_s02b_the_id_comes_from_the_tools_own_arguments` red; regressions: a reused or argument-supplied id creates a new pending card (also after a simulated restart), two concurrent cards sharing an id need two clicks, `resolve` of an unknown id changes nothing; source gate: no caller passes an argument-derived id; current `create_pending` as negative control; `tests/test_bus_approval_can_be_answered.py:134` (`test_an_explicit_task_id_is_still_used`) re-pointed | Open |
| SW-S03 | Bus cards on every platform show every argument name with a bounded value, an explicit "[N chars hidden]" marker when a value is cut, an arguments hash, and a link to the full gate on the Web dashboard; a danger call whose arguments cannot be shown is refused from chat cards | Property test over every danger tool and Telegram, Discord and Slack (each schema property on the card); the `{"content": …, "path": "~/.ssh/authorized_keys"}` ordering case; `test_sw_s03_the_approval_card_hides_the_tail_of_the_arguments` red; current renderer as negative control | Open |
| SW-S11 | `SafetyMiddleware.is_danger_tool` equals the `requires_approval` floor (unknown tiers gated; YOLO and grants still applied in `check`) | Parity test over every `TOOL_TIERS` entry plus an unknown and a runtime-added name; `test_sw_s11_unclassified_tool_is_gated_by_graph_but_not_by_swarm_bus` red; current classifier as negative control | Open |
| SW-S04 | One deadline owner for a bus approval. A timeout or cancellation (including FanOut cancellation and a swarm task cancelled while waiting) ends with the card edited to "Timed out" or "Cancelled" on every platform, inner tasks cancelled and joined, and a `timeout` / `cancelled` outcome, never `deny` | `test_sw_s04_timeout_leaves_the_card_live_and_the_row_pending` red; deterministic regression per adapter (unlike the witness, no race-dependent acceptance) asserting the edited card, no pending tasks and no unretrieved task exception; current adapter as negative control | Open |
| SW-S05 | Every FanOut card shows the final decision. The explicit-deny policy is decided (an explicit human Deny is final, or the card says "Your reject was recorded; waiting for N other platforms") and AGENTS §7B states it | Two-adapter regression asserting every card's final text for approve, deny, timeout and cancel; `test_sw_s05_fanout_rejecting_card_says_rejected_while_the_tool_is_approved` red; the Stage 1 two-platform drill (Telegram and Discord test bots, or a recorded fake) | Open |
| SW-E02 | The engine owns execution: `dispatch()` runs the work in an engine-created task registered before the first await, for every entry point. Cancel, the watchdog and every timeout cancel that task and await it (bounded) before recording the ending, with `execution_outcome=unknown` when effects were invoked. `timeout<=0` is honoured by the watchdog or refused at admission | Entry-point gate enumerated from source (pattern of `tests/test_swarm_paused_task_endings.py`: gateway `/swarm`, IDE `send_to_swarm`, `SwarmManager.dispatch`, panel foreground and background, `dispatch_swarm`, approve-driven resume, retry) asserting the coroutine was cancelled before the terminal row and no effect after; `test_sw_e02_reaper_records_timeout_but_work_continues` and `test_sw_e03_cancel_without_handle_is_cosmetic` red; finalize-without-cancel as negative control | Open |
| SW-E03 | One transition table (PENDING→RUNNING→{PAUSED, terminal}; PAUSED→RUNNING or terminal; terminal absorbing) enforced by CAS in SQLite and Postgres, with an owner/run id and a generation on each row; idempotent admission on the task id; late results from a superseded generation kept as orphan evidence, never as the result | Property test over every (from, to) pair on SQLite and real Postgres; concurrent double dispatch of one id executes once; the terminal-status AST gate extended to the reverse direction; `test_sw_e02b_reaped_pipeline_is_resurrected_as_paused` and `test_sw_e08_same_task_id_executes_twice` red; current upsert as negative control | Open |
| SW-E06 / SW-S06 | All product dispatch goes through `SwarmEngine.dispatch`. `broadcast()` and `dispatch_by_name()` / `summon` are private or thin wrappers over it. Broadcasts are admitted, persisted RUNNING, tenant-stamped, listed in Task History in the production posture, and finalized on cancellation. An admission refusal answers HTTP 429 or 503 with its message, and the page shows it as a refusal | AST gate: no product caller of `engine.broadcast` / `dispatch_by_name` outside the engine, with a planted direct call as negative control; `test_sw_e07_public_broadcast_bypasses_lifecycle` and `test_sw_s06_web_broadcast_bypasses_admission_and_tenant_stamp` red; route test with `KAZMA_SWARM_MAX_ACTIVE=1` and `KAZMA_PRODUCTION=1` | Open |
| SW-E05 | Resume is a RUNNING transition through the same admission path as dispatch, with its own watchdog start time; resumed and restored tasks count against admission; paused lifetime is one rule (checkpoint timeout or an explicit "no expiry"), identical before and after a restart | Admission and watchdog tests that include resumed and restored tasks; `test_sw_e06_resumed_pipeline_runs_as_paused` red; PAUSED-during-resume as negative control | Open |
| SW-E09 | Reject and cancel of a restored paused pipeline always persist, whatever the history LRU holds: restored paused tasks live in a dedicated unevicted map (or in `_active_tasks` as PAUSED) | Forced-eviction regression: reject persists and the task is not restored at the next boot; cancel answers truthfully; `test_sw_e16_reject_after_history_eviction_is_not_saved` red; today's flow as negative control | Open |
| SW-E01 | An approved pipeline interrupted mid-resume never re-executes a committed step and is never re-offered: approval, RUNNING and a step cursor are recorded before resuming (CAS from PAUSED); each step's begin and commit are journaled under `(task_id, step, generation)`, which is passed to the effect journal; at boot a begin without a commit is held as `execution_outcome=unknown`; the gate is settled "resumed" and a second approval of the same checkpoint generation is refused | Real child-process kill matrix (before the first step, mid-step, after a step commit, before finalize) on SQLite and real Postgres: at most one invocation of each post-checkpoint effect and no re-offered checkpoint; `test_sw_e01_approved_pipeline_reexecutes_after_restart` red; "pop the entry, then resume" as negative control | Open |
| SW-E04 | Every interrupted task with invoked mutators records `execution_outcome=unknown`, the invoked tools and its cause (`cancelled_by_user`, `deadline`, `shutdown`) from a task-level effect tracker shared by all steps; pattern timeout results are built from that tracker; `CancelledError` is re-raised after recording so callers' deadlines raise `TimeoutError`; the gateway timeout reply no longer says partial work was discarded (SW-S16's text in the branch this makes reachable) | Matrix {dispatch, pipeline, fan-out, consult, conditional, broadcast} × {engine deadline, step deadline, caller `wait_for`, cancel, reap, shutdown} with a worker that invokes a mutator and then blocks; `test_sw_e04_caller_timeout_becomes_user_cancel`, `test_sw_e05_engine_deadline_discards_effect_evidence` and `test_sw_e05b_pattern_step_timeout_labels_effect_retry_safe` red; current code as negative control (it fails every cell except worker-internal failure) | Open |
| SW-E08 | Status mapping is exhaustive (a PARTIAL status, or `result_status` plus `effects_uncertain`); retry copies every execution-relevant `SwarmTask` field (workspace, schema, aggregation, priority …) and requires explicit acknowledgement when the original's outcome is unknown; worker errors are classified by a taxonomy consistent with `LLMError.transient` (401/403/invalid key permanent, 429 transient) | Exhaustive mapping test that fails on an unmapped status; field-coverage gate for `build_retry_task` enumerating `SwarmTask` fields; classification table test; `test_sw_e09_failed_mutating_step_is_stored_completed`, `test_sw_e10_retry_drops_target_and_ignores_uncertainty` and `test_sw_e14_non_retryable_errors_are_retried` red; current mapping, copier and classifier as negative controls | Open |
| SW-S08 | The Swarm page reads every HTTP answer (approve, reject, playground HITL, dispatch); answers carry a `status` of `ok` or `error` plus `task_status`; approve claims the gate and answers 202, resuming in the background through SW-E01's RUNNING transition; page dispatches request background mode and connect the stream at once; English and Arabic wording comes from the catalog (`swarm.network_error_approving`) | Playwright through a proxy that cuts responses at 30 s, in English and Arabic, with the current page as negative control; `test_sw_s08_approve_answer_and_the_page_disagree` and `test_sw_s08b_no_dispatch_from_the_page_asks_for_background` red; Arabic page tour run (fast_test skips `tests/e2e`) | Open |
| SW-E07 | No engine database, settings or embedding call runs on the event loop thread: TaskStore and metrics behind an async facade (`to_thread`), shared breaker state read and written off the loop or cached with an async refresh, routing embeddings in `to_thread`; the helpers listed in `_LOOP_STALL_HELPERS` | Behavioural loop tracer (pattern of `tests/test_get_routes_off_the_loop.py`) over a dispatch of every task type, failing on any SQLite statement or settings call on the loop thread, current code as negative control; `test_sw_e11_breaker_and_store_io_on_event_loop` and `test_sw_e11b_auto_routing_embeds_on_event_loop` red; measured loop-stall budget under a 10-task concurrent load | Open |
| SW-S12 | No settings-store I/O on the loop in the bus approval path: configuration read once off the loop per check; `shared_approvals` I/O in `to_thread` or an async store; polling replaced by an event plus a slower durable poll; callbacks resolve off the loop | Loop-stall gate extended to methods and properties of `SafetyMiddleware` and `shared_approvals` (with a planted call as negative control); `test_sw_s12_bus_approval_touches_settings_on_the_event_loop` red; no loop stall above 1 s during 20 parallel approvals on a Postgres settings store in a disposable container | Open |
| SW-S09 | The task stream has sequence ids and `Last-Event-ID` resume, a heartbeat comment at least every 15 s, per-principal and global stream caps, and a maximum lifetime; the client deduplicates | Re-chunking proxy test (pattern of `tests/e2e/test_chunked_stream_browser.py`) with no duplicate lines after a reconnect; `test_sw_s09_reconnect_replays_without_ids_and_no_keepalive` red | Open |
| SW-S14 | Dispatch inputs are bounded: timeout clamped to a declared range (0, negative, huge and NaN refused), byte caps on prompt, context and metadata, count caps on workers and checkpoints, 422 on out-of-range; task-history search runs in SQL with a limit | Route tests for 0, negative, huge and NaN timeouts and oversize bodies (422); a search test proving a bounded query | Open |
| SW-E15 | The Refiner's output is returned and its tokens and cost accounted, or the call is removed; each output is stored once and referenced; caps on paused tasks, prompt/context and result size (one bound definition shared with SW-S14); per-task plus global concurrency for fan-out, consult and broadcast | Size and accounting tests; `test_sw_e18_refined_output_dropped_and_payload_amplified` red, with the stored-bytes-per-output-byte ratio (4.51 at baseline) reported after the fix | Open |
| SW-S10 | Worker output is fenced (`fence_untrusted(source="swarm_worker:<name>")`) at `check_swarm_task` and at every inter-worker hand-on (pipeline, handoff, blackboard, worker context) | `FENCED_TOOL_FUNCTIONS` (`tests/test_static_gates.py:1811`) extended to the swarm readers; behavioural test with negative control; `test_sw_s10_check_swarm_task_returns_worker_output_unfenced` red | Open |
| SW-S07 | `/api/swarm/*` has one route-to-role table enforced on every route (workers, output target, breaker reset, dispatch included); `api-routes.md` states the roles | Enumerating route-policy test (pattern of `tests/test_thread_route_policy.py`) that fails on a route missing from the table; negative control: a worker POST as an operator answers 403; `test_sw_s07_worker_and_output_target_writes_skip_the_admin_gate` red | Open |
| SW-E10 | Semantic routing uses one embedding space (Kazma's encoder, explicit vectors for profiles and queries, no default embedding function, off the loop) or is removed; the stored task names only the worker that ran, with candidates kept in routing diagnostics | Real-chromadb test with explicit vectors in the `rag` CI path (no default-model download); `test_sw_e11c_semantic_router_mixes_embedding_spaces` red | Open |
| SW-E11 | Only the probe's outcome resolves HALF_OPEN (each admission tagged with the breaker generation or probe token; stale outcomes update counters only); shared breaker updates use CAS; a lease-store failure logs WARNING; transient worker keys expire; user cancellation is not a failure | Breaker generation tests; shared-mode CAS test on SQLite and Postgres settings stores; `test_sw_e12_stale_success_closes_half_open_breaker` red | Open |
| SW-E12 | No worker is reaped with a dispatch in flight (in-flight counter or lease, reap at zero); pool instance ids unique per boot; one name-resolution function for every pattern | In-flight counter tests; a template name in a pipeline resolves; `test_sw_e13_autoscaler_reaps_worker_with_dispatch_in_flight` red | Open |
| SW-E13 | Handoff is either reachable end to end from a shipped worker (a handoff tool, with `HandoffRequest` propagating through `InProcessWorker`) or documented as an extension point, and AGENTS §4 says which | End-to-end handoff test, or the doc change; `test_sw_e15_no_shipped_worker_can_request_a_handoff` updated to the chosen outcome | Open |
| SW-E14 | A failed RUNNING-row write, failing watchdog sweeps and gate register/settle failures log WARNING with an ops counter; no `except: pass` around breaker persistence; each worker result counted once in metrics; `swarm_worker_metrics` retained and pruned | Log-level gate over `except` blocks in `kazma_core/swarm/` (shared with SW-S15) with a planted silent handler as negative control; `test_sw_e17_running_row_failure_is_debug_only` and `test_sw_e19_open_breaker_rejection_counted_twice` red; debt ratchet lowered in the same change | Open |
| SW-S15 | Gate rows carry the human actor and a separate `timeout` outcome; bus adapters' `approve()` / `reject()` report durable-resolve failures; `swarm_notify` is folded into the AGENTS §33 ops-alert routes and reports delivery truthfully (§51); chat error text is sanitized; background dispatch handles are retrieved | Daily-digest / firing-ledger test showing human actors and separate timeouts; the SW-E14 log-level gate extended to the bus adapters; no product send path outside §33's three; debt ratchet lowered | Open |
| SW-E16 | NUL stripped or refused for every Postgres TEXT parameter; crash evidence durable to power loss (`synchronous=FULL` for the task store, or the evidence moved to the effect-journal database); Temporal timeouts match pattern budgets and approved resumes take the durable path; owner/run id on rows (delivered with SW-E03) | Real-Postgres test (`@pytest.mark.postgres`) for NUL parity; pragma check; Temporal check under SQ03; `test_sw_e20_postgres_text_columns_not_nul_stripped` red | Open |
| SW-S13 | Bus adapters neutralize mentions the way the chat adapters do (`sanitize_outbound`, `allowed_mentions`) | Gate over every Discord and Slack post site; `test_sw_s13_bus_adapters_skip_the_mention_sanitizer_the_chat_adapters_use` red | Open |
| SW-S16 | Multi-user installs only: id-addressed task routes and the stream are tenant-checked; `/tasks/active` is tenant-filtered; platform ids (`source_chat_id`, `source_user`) are not exposed on the Web API or CSV export; one-off output routing follows the `/swarm config` admin rule | Route walk as tenant A with tenant B's ids (pattern of `tests/test_memory_routes_tenant_scope.py`) | Open |
| SW-S17 | Swarm docs and AGENTS.md match the code: route line references, the migration wording, the H-9 claim, §7A and §30 wording for swarm work, `/api/swarm/*` roles, the §20D component path (merge check) | Doc diff reviewed against the code; `tests/test_docs_anchors.py` and `tests/test_docs_file_refs.py` green | Open |

## Qualification requirements

Not findings: these are the qualification steps both halves name as the last
stage. They may remain open while implementation proceeds; do not remove or
shorten them.

| ID | Required result | Evidence required | State |
|---|---|---|---|
| SQ01 | Concurrency soak: admission, breakers and autoscaler under hundreds of mixed tasks | Measured run with receipts (task count, duration, failures, loop-stall dumps) | Open |
| SQ02 | Postgres parity for the task store, the settings store and the approval path | `@pytest.mark.postgres` tests green on real Postgres locally (disposable container) and in the CI Postgres job | Open |
| SQ03 | Temporal durable path qualified with a time-skipping test server | Dedicated CI job green; a local run only after the user approves the test-server download | Open |
| SQ04 | Chaos: database locked or down, Temporal unreachable, shutdown mid-pattern | Fault-injection runs with the recorded outcome per case | Open |
| SQ05 | Published limits (max tasks, max width, max payload, recovery semantics), and AGENTS.md §4, §5, §7, §14, §20, §30 and §33 revised to the measured behaviour | Docs and AGENTS diff tied to the measurements | Open |

## Cross-area dependencies

- **Every chat turn's HITL path (SW-S01):** the fix changes
  `agent/graph_tool_worker.py` and `agent/tool_registry.py`, which gate every
  single-agent chat turn (AGENTS §7A). Re-run the HITL and gate-registry
  suites, not only the Swarm ones.
- **Gate registry (SW-S02, SW-S04, SW-S15):** moving bus decisions into
  `hitl_gates.db` touches AGENTS §30 (decision truth), §30 D2 (one decision
  writer, `record_gate_decision`) and the daily digest's approval counts
  (§27E).
- **Effect journal (SW-E01, SW-E04):** stable step identities feed
  `agent/effect_journal.py` and `agent/dispatch_effects.py` (AGENTS §30G).
- **Ops alerts (SW-S15, SW-E14):** folding `swarm_notify` into the §33 routes
  changes what the weekly firing ledger counts (§27C).
- **Gateway (SW-E04, SW-S16):** the `/swarm` timeout branch becomes reachable;
  its reply text must change in the same commit.

## Open choices the reports leave

Per the owner's standing rule, pick the option the evidence supports and
record the reason in the execution log; ask the user before removing a
feature they may want.

- SW-S05: is an explicit human Deny final, or a vote with the card saying so?
  (AGENTS §7B, H-12, currently declares deny-as-vote.)
- SW-E13: make handoff a product feature (a tool plus end-to-end tests) or
  document it as an extension point.
- SW-E10: fix semantic routing or remove it.
- SW-E15: return and account the Refiner's output, or remove the call.
- SW-E02: honour `timeout<=0` in the watchdog, or refuse it at admission.
- SW-E05: the paused-lifetime rule (checkpoint timeout, or explicit no expiry).
- SW-S07: the exact role split (the surfaces half suggests viewers read;
  operators dispatch, cancel and approve; admins edit workers, templates and
  routing).
- SW-S14: the timeout range (the surfaces half suggests 10 to 3,600 s).

## Completion rules

A row is Implemented only with current evidence of its full scope: the
witness red, the regression and gate green with their negative controls, and
the backend runs the row names. Fix sibling paths together. Preserve the
HITL execution paths (AGENTS §7 A–C), the gate registry (§30), tenant and
workspace scope, the default commitment swarm scope (§20), the effect-journal
semantics (§30G) and the §9 module split. Keep the single-runtime profile
explicit; a Postgres backend is not proof of multi-replica safety. Do not
mark the goal complete while any row, including SQ01–SQ05, is open.

## Start here

**Read first, in this order:**
1. `docs/audits/AUDIT_SWARM_2026-10-13.md`
   (the merged audit and its witness index), then the halves
   `swarm-engine.md` and `swarm-surfaces.md` for full evidence.
2. AGENTS.md §4, §5, §6, §7 (A, B, C), §9, §14, §20 (swarm scope), §26 (B, E,
   G), §28, §30, §33, §35, §45; `docs/THREAT_MODEL.md` (line 53 and §6).
3. Stage 1 code: `kazma-core/kazma_core/agent/graph_tool_worker.py` (flag set
   at :815-818, reset at :1593-1596), `agent/tool_registry.py` (:687-705),
   `agent/tool_builtins/system.py` (`dispatch_swarm` from :720,
   `check_swarm_task` :833-883), `swarm/safety.py`, `swarm/bus.py`,
   `swarm/shared_approvals.py`, `safety/hitl.py`, `safety/hitl_gates.py`,
   `safety/side_effects.py`, `safety/commitment/scope.py`,
   `mcp/manager.py` (:2340-2420); `kazma-gateway/kazma_gateway/adapters/`
   `telegram_bus.py`, `discord_bus.py`, `slack_bus.py`.
4. Stage 2–3 code: `swarm/engine.py`, `dispatch_inner.py`,
   `worker_dispatch.py`, `broadcast.py`, `task.py`, `task_store.py`,
   `task_control.py`, `task_lifecycle.py`, `checkpoint_manager.py`,
   `patterns.py`, `durable_temporal.py`, `agent/dispatch_effects.py`,
   `agent/effect_journal.py`.
5. Surfaces: `kazma-ui/kazma_ui/swarm_panel/routes_tasks.py`,
   `routes_workers.py`, `routes_general.py`, `swarm_sse.py`,
   `static/js/swarm.js`, `templates/swarm.html`; gateway
   `kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py` and
   `kazma-gateway/kazma_gateway/swarm_notify.py`.
6. Tests to extend or re-point: `tests/test_bus_approval_can_be_answered.py`
   (:134 and :171 lock today's SW-S02 and SW-S01 behaviour — re-point them,
   do not delete them), `tests/test_swarm_paused_task_endings.py`,
   `tests/test_static_gates.py` (`_LOOP_STALL_HELPERS` at :886,
   `FENCED_TOOL_FUNCTIONS` at :1811), `tests/test_get_routes_off_the_loop.py`,
   `tests/test_thread_route_policy.py`,
   `tests/e2e/test_chunked_stream_browser.py`,
   `tests/test_execution_recovery_scope.py`.

Begin with SW-S01: it is the top-ranked finding, reachable by untrusted
content, and independent of the lifecycle rework.

**Run rules:**
- Run every test process at below-normal CPU priority. `scripts/fast_test.py`
  lowers itself; for targeted runs on Windows use for example
  `cmd /c start "" /belownormal /b /wait .venv\Scripts\python.exe -m pytest <files>`.
  Do not lower I/O priority (the runner's measurements show it makes runs
  far slower).
- Run targeted tests for each change. Run the full suite once, at the end,
  with `python scripts/fast_test.py` and no `--chunks`. Do not edit files
  while it runs. It skips `tests/e2e`, so run the browser tests (Stage 4, the
  Arabic page tour) separately.
- Witness files stay in `session-artifacts/audits-2026-10-13/`; never move
  them into `tests/`. Run the matching witness before and after a fix: green
  before (defect present), red after (evidence the defect is gone).
- Never touch the live install at `C:\Users\balfa\kazma`, and never start,
  stop or reload the Kazma server.
- Postgres only in a disposable, resource-capped container (for example
  `docker run --rm --cpus 1 --memory 512m … postgres:16` under a throwaway
  name), never the live database container; remove it afterwards; mark the
  tests `@pytest.mark.postgres`.
- No network calls from tests. Set `HF_HUB_OFFLINE=1` and
  `TRANSFORMERS_OFFLINE=1` for every run (no tracked file sets them; the
  surfaces audit's first run sent one Hugging Face request). Replace platform
  HTTP with in-process recorders; stub the Refiner, self-improvement and
  worker memory index as the engine witnesses do; never let Chroma's default
  embedder load. Ask the user before any download (the Temporal test server,
  a model).
- Fix the class, not the instance: each fix lands with a behavioural
  regression, a class gate that enumerates its siblings from the real source,
  and a negative control (AGENTS §28, §35). A lowered debt count goes into the
  ratchet baseline in the same change.
- Compile-check changed Python (`py_compile`) and `node --check` changed
  JavaScript.
- No commits or pushes unless the user asks.

## Execution log

- 2026-10-13: tracker created from the merged audit. No remediation started.
