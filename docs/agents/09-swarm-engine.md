<!-- AGENTS.md, piece 09-swarm-engine. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 9. SwarmEngine Module Structure (P2-1 refactor — 3 extractions)

The original 1878-line `engine.py` god class was split into focused modules.
SwarmEngine remains the central orchestrator with thin delegates for backward
compatibility. **All public API methods and constructors are unchanged.**

| Module | Responsibility | When to open it |
|--------|---------------|-----------------|
| `engine.py` | Dispatch, handoff, task lifecycle, worker registry | Always — the orchestrator |
| `reliability_registry.py` | Circuit breakers, retries, timeouts, validators, concurrency | Configuring per-worker reliability |
| `phonebook.py` | WorkerRegistry summon + the fenced memory context of `dispatch_by_name` (which runs through `dispatch()`) | Topology/DAG worker lookup |
| `checkpoint_manager.py` | HITL pipeline checkpoint state, timeout auto-reject, persistence | Pipeline pause/resume logic |
| `shared_approvals.py` / `approval_card.py` | A bus approval request's one decision; the card's text | Swarm-bus HITL (§7B) |
| `limits.py` | Input, pause and parallelism limits | A new task field or limit |
| `task_model.py` | A task's own model call outside a worker (the Refiner, a synthesis): ledgered, accounted, bounded | A new model call in a pattern |
| `semantic_router.py` | Semantic routing: Kazma's embedder, exact comparison, the measured floor | Routing by meaning |
| `single_operator.py` | The multi-user refusal (decision D1) | A new swarm surface |

**Rules after refactor:**
- New reliability features go in `reliability_registry.py`, not `engine.py`.
- **The engine owns every run** (Swarm audit Stage 2-3, 2026-10-14):
  `_run_owned` registers it before its first await, one run per task id; the
  RUNNING row is an admission compare-and-set (`TaskStore.admit`, `generation`
  and `run_id`); `persist_task` follows one transition table (a finished
  status is never left, an older generation's write is refused and kept as
  `orphan_results`). An interrupted run records `interrupted_by` and
  `execution_outcome` (`none_invoked` / `unknown` with `invoked_tools`) from
  its effect tracker; an uncertain result is stored `failed`; retrying an
  unknown outcome needs `acknowledge_unknown`; a resumed pipeline skips its
  journaled steps. Task writes run in threads, one task's in order
  (`_persist` / `_flush_writes`); the admission write is the first of them,
  and a stop that lands during it is the run's interruption like any other
  (`_stopped_at_admission`, 2026-10-15: the SQ01 24-hour soak found a
  shutdown leaving five tasks admitted, with RUNNING rows, and never ended).
  An approval stopped during its admission write has resumed nothing: the
  checkpoint is offered again and its auto-reject armed again
  (`_approval_stopped_at_admission`, decided by the write's own outcome:
  admitted, its RUNNING row goes back to PAUSED; failed, the PAUSED row
  stays; refused, another run's task is left to that run; an outcome not
  known in time keeps the claim, and the next start decides from the row).
  Gates: `tests/test_swarm_owned_lifecycle.py`,
  `tests/test_swarm_interrupted_effects.py`, `tests/test_swarm_stop_at_admission.py`.
- **A store fault never becomes a stored-looking success** (Swarm tracker
  SQ04, 2026-10-15). A task write the store refuses for a reason that passes
  (lock, busy, database away: `_store_error_passes`) is tried again
  (`_WRITE_RETRY_WAITS`); a run whose ending never landed is reported
  `failed` with `unsaved`/`store_error`/`result_status` to its caller, the
  engine's record and the stream (`_unsaved_ending`; the row stays RUNNING and
  the next start holds it unknown). An approval whose admission write fails
  resumes nothing (`_admit_to_store(must_record=True)`, `503
  approval_not_recorded`): resumed, a crash would offer the checkpoint again.
  **A new task runs only with its RUNNING row** (2026-10-15): the admission
  write is tried again the same way, and one that still fails refuses the
  task, nothing run (`_store_refused`: `task_store_unavailable`, retryable,
  the slot freed, counted `running_row`; the dispatch route answers 503 with
  Retry-After, the Swarm page words it in its language). It used to run with
  no RUNNING row, so a crash in it left effects no recovery could see. The
  refusal is the task's ending in this process only (history and stream, so a
  background dispatch's page hears it); dispatching the same task again runs
  it. A refused attempt whose write landed before its connection was lost is
  our own row (same `run_id` and generation): admitted. A durable dispatch
  that never ran is finalized (`durable._not_run`). Lost writes are counted
  (`task_write`, `step_journal`, `worker_metric`, `breaker_read`,
  `breaker_persist`, `breaker_lease`). Harness: `scripts/swarm_chaos.py`;
  gate: `tests/test_swarm_chaos.py` (each fix's old code as its negative
  control, the run-anyway admission included; the Postgres cases
  `@pytest.mark.postgres`).
- **An admitted task keeps its slot until its ending is stored**
  (2026-11-27). The limit counts `_active_tasks` (a paused task does not;
  a task that has finished still counts until the ending is stored). The
  soak counts stored `started_at` / `completed_at`, and a row with no
  `completed_at` counts as running until the check. `_finalize_task` used to
  drop the slot and then write the ending in a thread: under load the next
  task was admitted and its start stored while the previous ending was still
  unwritten, and the store showed 7 tasks in flight against a limit of 6.
  The slots had peaked at 6; once the writes landed the stored intervals did
  not overlap. The slot is released when that write returns, before the
  write is marked done (`_release_admission_slot`); a cancelled write
  releases from its done callback so the slot cannot stay held. A new task
  cannot start until that `completed_at` is stored. A refusal before a
  RUNNING row (`_store_refused`, a share refusal, a duplicate, a stop during
  admission) still drops the slot at once: nothing of this run was stored as
  running.
  **That slot is not active work** (2026-11-28). While the ending is stored
  the task stays in the table, and its status is already one it never leaves
  (or its id is in `_endings_recorded`). `task_has_ended` is the one check.
  `list_active_tasks` and `get_active_task` leave it out, so the panel's
  Active tab, the `kazma_active_tasks` gauge and the run list do too.
  `cancel_task` returns false and the watchdog does not reap it: those
  callers wrote "cancelled" or "timeout" over a success and told the user
  the success was cancelled. The cap still counts the slot.
  An exception while the ending is recorded releases the slot in
  `_finalize_task`'s `finally` (the id is recorded in `_endings_recorded`
  before the checkpoint close, the history, the chat delivery and the
  snapshot). `reject_checkpoint` does not pop the slot on its own. Its
  `finally` releases only when the ending is recorded and no store write is
  in flight, so a raise before the write was scheduled cannot hold the slot
  until restart, and a write that is still running is not cut short. Gate:
  `tests/test_swarm_slot_matches_store.py` (the old pop beside the end stamp,
  a reader that returns the raw table, and a removal through an alias, as
  the controls).
- **A shared breaker never takes an older state** (SQ01 soak, 2026-10-15):
  an outcome changes the breaker under its lock and shares it in a second
  step, so a refresh between the two read the generation before;
  `refresh_from_shared` takes a NEWER generation's state only, and from the
  same generation only its counters and settings (never its state or open
  time). The soak harness test failed 2 runs in 10 on it. Gate:
  `tests/test_swarm_breaker_shared_refresh.py` (the old refresh as control).
- **The durable path (Temporal) runs the engine's own run, where it was
  admitted** (Swarm tracker SQ03, SW-E16, 2026-10-15; rules in
  `swarm/durable.py`'s docstring). One workflow per run (`kazma-swarm-<id>`,
  `-g<generation>` for an approved resume, which takes the durable path too);
  its one activity is scheduled on a queue only the admitting process polls
  (`activity_queue`) and runs only for a run registered and waiting there,
  in the run's own context, the body being `SwarmEngine._run_admitted` (or
  `_resume`) -- the same deadline, watchdog and endings as in process (one
  deadline owner). Temporal's limits come from the same budget
  (`engine.task_budget`, `durable.activity_timeouts`) and sit after the
  engine's own end of the run; one Temporal does fire, or a cancel or
  terminate made through it, is settled in the run (`durable._settle`:
  `deadline`, `worker_lost` or `temporal_cancel`, with `execution_outcome`).
  Never retried (`maximum_attempts=1`: the worker's own retries run inside,
  under §30 G). A stop stops the work here first, then cancels the
  workflow. A heartbeat every `HEARTBEAT_EVERY_S` lets Temporal call a dead
  process's worker lost; the workflow worker keeps no cache (no sticky
  queue), so a restarted Kazma settles that workflow at once; the activity
  uses TRY_CANCEL (the SDK cannot evict a workflow waiting on a cancel).
  The workflow's result is a summary (no prompt or output); the task store
  holds the result, and a run whose work ended does not wait past
  `WORKFLOW_CLOSE_WAIT_S` for Temporal (counted `durable_workflow`). A first
  run of a pipeline journals its steps too, so a killed process's task is
  held unknown naming the step it stopped in. Gate:
  `tests/test_swarm_temporal_durable.py` on Temporal's time-skipping test
  server (a real child process killed mid-step; the old activity body, the
  old ending, the worker's own context, the in-process resume and no
  heartbeat/journal as controls). The test server does
  not skip time while an activity is scheduled or running: those limits run
  short and real there.
- **Swarm failures are loud and counted** (SW-E14): lost evidence or a lost
  decision is a WARNING and `kazma_core.metrics.record_swarm_failure(site)`
  (`kazma_swarm_failures_total{site}`). A catch-all handler in `swarm/` or the
  bus adapters that stays below WARNING is declared with its reason in
  `tests/test_swarm_boundaries_and_signals.py::_QUIET_HANDLERS`. Each worker
  result is counted once in the worker metrics (`_finalize_task`).
- **A deadline on work keeps the work in its caller's task** (2026-11-11):
  the run (`_run_admitted`), a dispatch (`TimeoutGuard.execute`), a pattern
  step, a consult, a worker's tool call and a scheduled fire's turn are
  bounded by `asyncio.timeout`, never `asyncio.wait_for`. On Python 3.11 (CI's
  and the live install's) `wait_for` ran the work in a task of its own, and a
  second cancel of the caller (a shutdown after a stop) left it running after
  the run had ended: Linux CI's Swarm soak saw a worker with a dispatch in
  flight after the engine stopped, and the Scheduled soak's clock could not
  see a fire's turn. 3.12's `wait_for` already ran it in the caller's task.
  Every other `wait_for` in `swarm/` and `cron/` is declared with why it bounds
  no work: `tests/test_work_deadlines_stay_in_the_task.py` (a copy of 3.11's
  `wait_for` as the negative control, on every Python).
- **A worker's text reaches a model fenced** (SW-S10): `check_swarm_task`,
  pipeline and handoff hand-ons, the synthesis prompt, the Refiner. Same gate
  file (`_RESULT_TEXT_ONLY` declares the stored-result builders).
- **The Refiner's report and every synthesis are the task's** (SW-E15,
  decision D8: fixed, 2026-10-15). A pipeline that succeeded (first run or
  resume) is refined by one model call: the report is the result's
  `synthesized_output`, the last step's output stays `aggregated_output` and
  every step's stays in `worker_results`; a failed or partial one is not
  refined. Every model call the swarm makes for a task outside a worker -- the
  Refiner, a fan-out or consult synthesis -- goes through
  `swarm/task_model.call_task_model` (`resilient_chat`: retries, failover,
  the per-call ledger with the task's id as `thread_id`; bounded output and
  deadline; the watchdog counts the Refiner as one more pipeline step), and
  its tokens and cost join `total_tokens` / `total_cost`
  (`PatternExecution.synthesis_tokens` / `synthesis_cost` into
  `_finalize_task`). A Refiner that cannot answer leaves the unrefined result
  with a WARNING and `kazma_swarm_failures_total{site="refiner"}`, never a
  stand-in report. Gates: `tests/test_swarm_refiner.py` (no direct `.chat(`
  in `swarm/` outside `worker.py`, the old Refiner as its control).
- **Semantic routing has one embedding space** (SW-E10, decision D8: fixed,
  2026-10-15). `swarm/semantic_router.py` embeds the task and each worker
  profile with Kazma's embedder (`memory.embedder.get_embedder`) and compares
  them exactly in the process -- no vector store, no default embedding
  function, no other model (gate). A profile matches only when it stands out
  from the others (`MATCH_FLOOR`, measured with bge-m3 on
  `tests/fixtures/swarm_routing/`, `scripts/swarm_routing_bench.py`; the
  nearest worker with no floor is the negative control); otherwise keywords
  route, and the autoscaler takes what nothing matched. In a thread, within
  `routing_engine.SEMANTIC_DEADLINE_S`; an embedder that is missing, failing,
  slow or busy routes by keywords, said once at WARNING
  (`site="semantic_routing"`). A DISPATCH stores in `task.workers` only the
  worker that ran; the candidates are in `metadata.routing_diagnostics`.
  Changing a profile's text, the dataset or the embedder's model needs the
  vectors recorded again (`scripts/swarm_routing_bench.py vectors`). Gate:
  `tests/test_swarm_semantic_routing.py`.
- **`/api/swarm/*` has one role table** (`kazma_ui/swarm_panel/route_roles.py`,
  SW-S07): GET is any signed-in user, everything else admin, enforced by the
  routers' `RoleCheckedRoute`. On an install with platform users every swarm
  surface refuses (`409 swarm_single_operator`, decision D1). Gate:
  `tests/test_swarm_route_roles.py`.
- **Bus posts are mention-safe** (SW-S13): every Discord and Slack bus post and
  edit goes through `_post_message` / `_patch_message` / `_update_message`,
  which apply the chat adapters' `sanitize_outbound` (and Discord's
  `allowed_mentions: {"parse": []}`).
- **A tool long by design gets its time** (`graph_helpers.LONG_RUNNING_TOOLS`,
  2026-10-31): every tool call is bounded at `agent.tool_timeout_seconds`
  (120 s, a hung tool's bound), and `run_research_pipeline` and
  `knowledge_ingest_site` get at least 1,500 s, `crawl_site` 900 s (the turn's
  budget still bounds the turn; a disabled bound stays disabled). Cut at 120
  s, a deep research from chat never finished: live, a Kuwait history paper
  became a page-by-page search by the model
  (`tests/test_long_running_tool_timeout.py`).
- **`check_swarm_task` waits for the result** (`wait_seconds`, default 60,
  at most 90: below the 120 s tool timeout), answering the moment the task
  finishes. Answering "still running" at once, with "poll every few seconds"
  in its description, had the chat model poll three times in two seconds;
  the stagnation guard ended the turn 3.8 s into a 36 s task (live
  2026-10-20). Gate: `tests/test_check_swarm_task_waits.py` (no wait as the
  negative control).
- **A wait is progress, not a loop; a finished task goes back to its chat;
  `/unrestricted` reaches the worker** (2026-11-14, live: a 15-minute data
  task from an `/unrestricted` Telegram chat ended "partial" on the third
  90 s wait, the worker ran out of 40 rounds, and no one sent its result).
  `check_swarm_task`'s "still running" answer begins
  `tool_loop_breaker.STILL_RUNNING_PREFIX`, and `stagnation_signatures`
  leaves such an answer out of the stagnation window -- only from a tool in
  `WAITING_TOOLS`; the task's time limit and the turn's budgets still bound
  it. `SwarmEngine._finalize_task` calls
  `swarm.chat_delivery.deliver_finished_task` for every ending but a pause: a
  task whose `source_platform` is the chat app of its `source_delivery_target`
  (Telegram, Discord, Slack; a web turn's target is the operator's Telegram,
  so not sent) is sent there after `HANDOVER_GRACE_S`, unless the chat's turn
  read the result (`note_result_handed`); past `INLINE_MAX_CHARS` the result
  is a Markdown file. Under `/unrestricted` (`agent.unrestricted`:
  `is_unrestricted`, recorded on the task as `unrestricted` and bound around
  the worker by `worker_dispatch` with `unrestricted_scope`) the task's time
  limit is one to six hours, the worker gets `UNRESTRICTED_WORKER_ROUNDS` and
  reads `UNRESTRICTED_TOOL_RESULT_CHARS` of each tool result. The forced
  report at the round limit turns hidden reasoning off. It lifts no safety
  gate (always-ask tools, outbound allowlist, protected keys, denylist) and
  not the browser's network isolation, which is the install's
  `KAZMA_BROWSER_NETWORK_MODE` (one shared browser, not a chat's). A message
  that is a template with blanks still in it (`turn_input.unfilled_blanks`:
  `<FILL IN ...>`, `[FILL IN]`, `{{name}}`, EN/AR) gets "ask, don't guess"
  in its latest-message note. Gate: `tests/test_unrestricted_swarm_runs.py`
  (the old window and planted look-alikes as negative controls).
- **The task store** strips NUL from every Postgres parameter
  (`_pg_execute`) and runs SQLite at `synchronous=FULL` (the RUNNING row is
  crash evidence, SW-E16); worker-metric days follow the task retention.
- **The concurrency soak** (SQ01): `scripts/swarm_soak.py` is the soak harness
  (a real engine on a private SQLite store, scripted workers, bursts, outages,
  cancels, `auto` tasks and restarts; checks every cycle from the task store's
  own status log), held by `tests/test_swarm_soak_harness.py`. Its first
  trial found shutdown's `stop_all_runs` returning before the tasks it
  stopped were written (the writes run in threads), so a process that exited
  then left the next boot to record a failure of unknown outcome; it now
  waits for them, bounded (`_flush_stopped_writes`, a WARNING and
  `site="shutdown_write"` when one is left), and the soak counts one left as
  a violation. `--duration` is seconds of LOAD: the time a restart takes is
  added (one that waits out the stop grace for a task ignoring its stop took
  12 s of the test's 24 s run and left the breaker no window to recover in).
- `engine.py` public methods are thin delegates — the real logic lives in the extracted modules.
- The de-facto public attrs (`_workers`, `_task_handles`, `_metrics_collector`) remain on `SwarmEngine`. `_active_tasks` is the admission table, not the list of active work: callers ask `list_active_tasks` and `get_active_task`.
- Constructor signature is unchanged — test fixtures work without modification.

