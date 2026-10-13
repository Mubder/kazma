# Scheduled audit — merged, 2026-10-13

> Source reports, witness files and logs: `session-artifacts/audits-2026-10-13/` (local working evidence, not committed). A file named below without a folder is there.

Source reports, both in `session-artifacts/audits-2026-10-13/`:

- **Engine half:** `scheduled-engine.md`, findings `SCH-E01` to `SCH-E14`
  plus its tracker row `SCH-DOC`. Witness file `scheduled_engine_probes.py`;
  logs `scheduled-engine-probes.log` and
  `scheduled-engine-existing-tests.log`.
- **Surfaces half:** `scheduled-surfaces.md`, findings `SCH-S01` to
  `SCH-S12`. Witness file `test_scheduled_surfaces_probes.py`; logs
  `scheduled-surfaces-probes.log` and `scheduled-surfaces-existing-tests.log`.

All four `.err` files are empty.

The Scheduled area covers cron reminders, scheduled agent tasks and scheduled
X posts. This document merges the two halves into one ranked list. It adds
no finding. The merge rules are:

- Every original ID is kept, so each witness stays traceable.
- Where both halves describe the same defect, one entry lists every ID.
- Where one original finding covers two different defects, it is split, and
  each entry names the half it carries, for example "SCH-S05 (delivery half)".
- A merged entry takes the higher priority of its parts.

Where the merge re-read the code, the current `file:line` is marked
"(re-read)". Other line references are the auditors'.

Reviewed state: commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the
uncommitted working tree of 2026-10-13. Both halves found no uncommitted
change in the files in scope. During the merge, `git status` was re-run on
the in-scope paths: the cron package, `x_api/`, `safety/commitment/`,
`tools/send_message.py`, `agent/turn.py`, `graph_tool_worker.py`,
`tool_registry.py`, `tool_builtins/system.py`, `kazma-skills/`,
`scheduled_api.py`, `scheduled.js`, `scheduled.html`, `health.py` and
`app.py`. The only modified file among them was
`kazma-skills/kazma_skills/native/document_platform/tools.py`, which is
outside this scope.

## 1. Verdict

### 1.1 Scores

Each half's scores are given as reported. The merge does not compute a new
score.

| Half | Subject | Capability / design | Demonstrated industrial readiness |
|---|---|---|---|
| Engine | Cron reminders and scheduled agent tasks (`kazma_core/cron/scheduler.py`) | 4/10 | 3/10 |
| Engine | Scheduled X posts (`x_api/publication_service.py`, `publication_store.py`) | 7.5/10 | 6/10 |
| Engine | Combined Scheduled engine | about 5/10 | about 4/10 |
| Surfaces | Pages, routes, agent tools and boundaries | 6/10 (design intent) | 4/10 |

Both halves call these engineering judgements, not benchmark results.

### 1.2 Summary

The area has two engines of different maturity. The surfaces built on them
do not yet hold the boundaries the design intends.

**The X publisher is close to an industrial design.** It has:

- a durable operation per post;
- a compare-and-swap claim and a lease taken before any network write;
- exact account and credential binding;
- a typed `outcome_unknown` state that is never re-sent;
- rolling quota reservations;
- a missed-fire hold;
- a notification outbox.

What remains on the X side is P2 or P3 (4.13, 4.14, 4.20).

**The cron engine is effectively at-most-once, and it fails silently:**

- A failed LLM turn is delivered and recorded as a success (SCH-E01).
- A refused delivery is recorded as DONE (SCH-E02, SCH-S05).
- A restart during a fire turns a reminder into a silent FAILED, or leaves a
  daily job stuck for good (SCH-E04).
- A naive time booked from chat is shifted by the operator's UTC offset
  (SCH-E03).
- A recurring reminder booked from chat is refused, or turned into a one-shot
  (SCH-E05).

Code comments claim the opposite of several of these (section 6).

**On the surfaces:**

- A background swarm dispatch inherits the graph's "gated" flag and skips
  approval. A fired reminder can reach it, and so can ordinary chat
  (SCH-S01, the same mechanism as the Swarm audit's SW-S01).
- The page's own Edit button silently truncates or rewrites the stored
  prompt (SCH-S02).
- Cron mutations ignore the tenant, while reads respect it (SCH-S03; this
  matters in the multi-user/production profile).
- The page counts every current X booking as "Completed" and offers no
  controls for them (SCH-S04).

**Bar for an industrial claim.** The two halves set the bar the same way:

- Engine: do not claim cron is reliable for time-critical reminders until
  SCH-E01 to SCH-E05 are closed with their gates.
- Surfaces: the area is not industrial, and not fit for more than one
  tenant, until SCH-S01 to SCH-S05 are fixed and gated.

This merged audit puts the bar at the Stage 1, 2 and 3 exit gates (section
7), which include both conditions. The qualification soak in Stage 5 is
needed before calling the area industrial.

## 2. Scope and evidence

### 2.1 Reviewed (union of both halves)

**Engine half:**

- `kazma-core/kazma_core/cron/scheduler.py` (whole file);
- `task_scheduler_cron/tools.py`;
- `safety/commitment/authorize.py`: the remind, cancel_job and
  proposal-backed post resolvers;
- `relative_time.py`, and `constraints.cron_pending_jobs`;
- the commitment gate in `agent/graph_tool_worker.py`;
- `agent/turn.py:run_agent_turn`;
- `tools/send_message.py`, and the gateway send backend
  (`kazma_gateway/agent_handler/graph.py:508-559, 2380-2402`);
- the X modules `schedule.py`, `scheduled_fire.py`,
  `publication_service.py`, `publication_store.py`, `notifications.py`,
  `booking.py`, `client.py` (request path) and `account_binding.py`;
- `x_publisher.x_schedule_post`;
- cron and X wiring and shutdown in `kazma-ui/kazma_ui/app.py`;
- `health.py` (`check_cron`, `check_schedulers`, `/health/activity`);
- `active_turns.running_turn_count`, and `agent_runner.build_child_graph`.

**Surfaces half:**

- every `/api/scheduled/*` route in `kazma-ui/kazma_ui/scheduled_api.py`;
- `templates/scheduled.html` and `static/js/scheduled.js`;
- the scheduling parts of X Studio (`x_api.py` queue and audit routes,
  `static/js/x_studio.js`);
- the agent tools `task_scheduler_cron/tools.py` and `x_publisher/tools.py`;
- `x_api/booking.py`, `publication_service.py` and `schedule.py`;
- the `remind` and `cancel_job` resolvers (`authorize.py`, `constraints.py`);
- the HITL tiers (`safety/hitl.py`, `side_effects.py`);
- the cron child graph (`agent_runner.build_child_graph` and its `app.py`
  wiring);
- the gates in `graph_tool_worker.py` and `tool_registry.py`;
- `tool_builtins/system.py` (`dispatch_swarm`) and `swarm/worker.py`;
- delivery-target resolution (`tools/send_message.py`);
- the i18n catalog slice;
- the semantic card painter in `chat.js`;
- the gateway `/x` command and the command catalog;
- the docs pages that describe scheduling.

Neither half changed product code, tests, docs, CI or config. Neither
started or reloaded a server, touched the live install, made git writes, or
called an external service or a real LLM.

### 2.2 Witness runs (from the reports and logs; not re-run for this merge)

| Run | File | Result | Log |
|---|---|---|---|
| Engine witnesses | `scheduled_engine_probes.py`: 16 test functions, one parametrized EN/AR | **17 passed in 2.18 s** | `scheduled-engine-probes.log` |
| Surface witnesses | `test_scheduled_surfaces_probes.py`: 14 test functions | **14 passed in 0.96 s** (1 Starlette deprecation warning) | `scheduled-surfaces-probes.log` |

**What a green witness means.** Every witness asserts that an observed
defect is **present**. A green run confirms the findings and certifies
nothing.

**How the witnesses isolate the product:**

- All stores live in `tmp_path`.
- The LLM is a stub graph (engine) or a fake turn (surfaces).
- Chat delivery, `ops_alerts.alert` and the HITL bus are captured fakes.
- The X publication probe replaces the client with one that fails the test
  if it is ever reached.
- The surface witnesses run the real `CronScheduler`, `SQLiteCronStore` and
  router behind a FastAPI `TestClient`, with a header middleware standing in
  for the auth middleware's tenant binding.
- `scheduled.js` is evaluated in a node `vm` sandbox.
- Crash windows are simulated with a `BaseException` raised at exact
  statement boundaries. This shows the persisted state, not OS-level
  durability.

The engine runs used `BelowNormal` priority, because a full suite was
running on the machine.

### 2.3 Existing suites (from the reports and logs)

| Run | Files | Result | Log |
|---|---|---|---|
| Engine half | `test_cron.py`, `test_cron_store_write_lock.py`, `integration/test_cron_and_adapters.py`, `test_cron_approval_pipeline.py`, `test_cron_tenant_context.py`, `test_x_publication_service.py`, `test_x_scheduled.py`, `test_remind_args_first.py`, `test_send_results_read.py` | **140 passed in 12.47 s** | `scheduled-engine-existing-tests.log` |
| Surfaces half | `test_scheduled_api.py`, `test_cron.py`, `test_x_scheduled.py`, `test_cron_approval_pipeline.py`, `test_cron_tenant_context.py`, `test_commitment_authorize_remind.py`, `test_remind_args_first.py` | **118 passed, 1 warning, in 7.79 s** | `scheduled-surfaces-existing-tests.log` |

The two runs share five files. Counted from the two logs, their union is 155
distinct tests in 11 files. The two counts must not be added together.

The existing tests are green while every witness is green, so the defects
below fall in a test gap. The surfaces half names one cause:
`tests/test_scheduled_api.py` uses a fake scheduler and seeds only
`pending` rows.

### 2.4 Not run (either half)

- A fault-injection run with real process death.
- A DST soak on a fake clock, a multi-day soak, or a load test.
- A browser session, a live UI tour or the Playwright e2e suite.
- A full-suite run.
- Postgres-backed stores.
- Real Telegram, Slack, Discord or X calls, or a real LLM.
- SCH-S01 end to end. The scheduled witness shows it at the registry
  boundary plus a located source chain. The Swarm audit's SW-S01 witness
  ran the real `dispatch_swarm`, a real `SwarmEngine` and a real
  `LocalToolRegistry`, with a stand-in worker.
- TUI scheduling surfaces, X Studio beyond its schedule and reschedule
  paths, the real auth middleware, OIDC and RBAC roles.
- A penetration test.
- The live install's `cron.db`, `x_publications.db` and `cron.timezone`
  value.

### 2.5 Re-checked during this merge (code read only, no execution)

These lines read as the reports state:

- `scheduler.py:110` (`prompt[:200]`).
- `scheduler.py:1125-1155`: only `_turn.text` is read, and the "never a
  silent loss" comment.
- `scheduler.py:1168-1180`: three separate statements in the daily
  finalize.
- `scheduler.py:1224-1295`: the delivery fallbacks and the three
  CRITICAL-then-return exits.
- `scheduler.py:626-632`: `update_job` forces `pending`.
- `scheduler.py:683-692`: cancel matches only `pending`.
- `scheduler.py:960-985`: the stale one-shot skip is marked DONE and logged
  at INFO.
- `relative_time.py:335-357, 501-504`: naive time is treated as UTC.
- `authorize.py:484-494`: `timing` is rewritten to `_abs_dt.isoformat()`.
- `authorize.py:1150-1185`: only `discarded` drafts are refused.
- `publication_store.py:196-205`: an existing key is returned whatever its
  state.
- `publication_service.py:283-287`: the 5-minute missed hold.
- `graph_tool_worker.py:814-819, 1592-1597`: the flag is set for the step
  and reset only at its end.
- `tool_registry.py:684-690`: the bus check is skipped while the flag is
  set.
- `tool_builtins/system.py:796-802`: `create_task(engine.dispatch(task))`.
- `agent_runner.py:970-981`: `hitl=None` when HITL is disabled, and
  `auto_deny` when there is no checkpointer.
- `scheduled.js:375-386, 394-406`: running jobs offered Edit and Delete;
  `openEdit` pre-fills from `summary`.
- `scheduled_api.py:232-266`: 404 for every refused edit or cancel.
- `app.py:2349`, `health.py:200-202`, `x_api/schedule.py:14-17` and
  `x_publisher/tools.py:164`: the stale comments and docstring (section 6).
- `docs/docs/guide/x-publisher.md:85-88, 120-123`,
  `docs/docs/guide/configuration.md:269-272` and
  `x_publisher/skill_manifest.yaml:70-73`: the docs and manifest items in
  section 6.3.
- `tests/test_bus_approval_can_be_answered.py:171-185`.
- `AGENTS.md:242-243, 1347, 1363-1366, 2013-2015, 2031-2032, 2063,
  3136-3138, 5144-5145`.

## 3. What is already good

| Area | Assessment | Half |
|---|---|---|
| X publication ownership | `claim` moves `scheduled`/`deferred` to `sending` with an owner and a lease, in one `BEGIN IMMEDIATE`, before any network I/O. `confirm` and `fail` check the owner. On recovery, expired `sending` rows become `outcome_unknown` and never go back to the queue. An ambiguous outcome is never re-sent. Repair accepts only an exact, operation-correlated audit receipt (`publication_store.py:258-362`, `publication_service.py:198-227`). | Engine |
| X account and credential binding | Every operation binds `account_id` and a credential revision. A change holds the post (`awaiting_approval`) instead of rerouting it. Live policy and caps are re-read after the claim, just before dispatch (`publication_service.py:289-348`). | Engine |
| X time policy | A post more than 5 minutes late is held for review with a notice and never caught up automatically. Booking refuses recurring, past, DST-gap and DST-fold times (`booking.py:37-90`). One shared `book_x_post` serves both surfaces. Quota and duplicate checks are reserved at booking, the proposal id binds the stored text, and the X stores are tenant-scoped. X routes are plain `def`. | Both |
| X quotas, rate limits, notifications | Rolling day and month reservations count pending, sending and unknown posts. HTTP 429 is deferred by Retry-After, with at most 8 attempts and a 24 h cap. A transactional notification outbox has a lease, exponential backoff and a superseded-revision check, and acknowledges only when the transport accepts the message (`notifications.py`). This is the pattern cron lacks. | Engine |
| Booking authority | `schedule_task`, `edit_scheduled` and `cancel_scheduled` are `danger` tier, and the X tools are `ALWAYS_HITL` (`safety/hitl.py:154-156, 186-189`). The approval card shows the full arguments, or says how many characters it hides (`agent_handler/hitl.py:423-468`). | Surfaces |
| Fire-time containment | Cron child graphs have no checkpointer, so `build_child_graph` forces `auto_deny` (`agent_runner.py:976-980`). Danger tools are denied, not left waiting on an interrupt that can never resume. Semantic clarifies fail closed. Limits: SCH-S01, and SCH-S05 (authority half). | Both |
| Tenant and delivery identity at fire time | `_execute` installs `job.tenant_id` and resets it in a `finally` (`scheduler.py:1097-1164`), with a dedicated test. The delivery address is captured at booking (`task_scheduler_cron/tools.py:39-48`, `send_message.resolve_delivery_target`). A booking with no address fails closed on both paths. Through the cron-parent ContextVar, a job booked from inside a fire inherits the delivery identity. | Both |
| Cron claim and store hygiene | A compare-and-swap `pending` to `running` claim (`scheduler.py:548-561`). The runtime-ownership fence keeps a second runtime off one data directory. Autocommit on one connection (AGENTS §16C, gated by `tests/test_cron_store_write_lock.py`). WAL pragmas, migration through the shared helper, a hard cap with age-based purge (but see SCH-E07), and a concurrency semaphore of 4. | Engine |
| Delivery result at the send boundary | The gateway backend returns `Error:` on refusal (`graph.py:555-557`), and every caller reads `send_failed` (`tests/test_send_results_read.py`). Cron reads it at `scheduler.py:1285`, but only the log level changes (SCH-E02). | Engine |
| Timezone foundations | `parse_timing` anchors daily times and naive ISO strings in the operator's zone, with a test west of UTC (`scheduler.py:272-335`, `tests/test_cron.py:106-195`). Daily wall-clock arithmetic stays correct across DST in the sampled zones. The commitment gate does not follow this rule (SCH-E03). | Engine |
| Remind and cancel gates | The remind gate anchors to memory, accepts compact relative timing, and gives honest reasons when it denies. The `cancel_job` resolver checks that a pending job exists in the caller's tenant before allowing a cancel (`authorize.py:649-759`, `constraints.py:61-111`). Recurrence and time zones are open: SCH-E03, SCH-E05. | Surfaces |
| CSRF and page craft | Mutations require `X-Requested-With`, on top of the global Origin middleware. The page compares viewer and scheduler time zones and notes a difference, gives each summary its own text direction, and strips raw X audit bodies. An Arabic page tour covers Scheduled. | Surfaces |

## 4. Findings

### 4.0 Priorities and ID map

Priority definitions (the two halves' definitions, combined):

- **P1:** fix before relying on scheduled reminders or tasks unattended or
  for anything time-critical, or before a multi-user production claim.
- **P2:** a significant correctness, truthfulness, recovery or operability
  gap.
- **P3:** hardening, consistency, drift.

After merging: **23 entries — 8 P1, 9 P2, 6 P3.** The originals were 26
findings plus the engine half's `SCH-DOC` tracker row.

| Rank | Original IDs | Priority | Title | Stage |
|---|---|---|---|---|
| 4.1 | SCH-S01 (cross-area: SW-S01) | P1 | Background swarm dispatch inherits the graph's gated flag and skips approval | 1 |
| 4.2 | SCH-E02 + SCH-S05 (delivery half) | P1 | Delivery is fire-and-forget; a refused or undeliverable reminder is DONE | 2 |
| 4.3 | SCH-E04 | P1 | A restart or crash during a fire loses the reminder or strands a daily job | 2 |
| 4.4 | SCH-E01 | P1 | A failed agent turn is recorded, and delivered, as a successful fire | 2 |
| 4.5 | SCH-S02 | P1 | The page's Edit silently truncates or rewrites the stored prompt | 2 |
| 4.6 | SCH-E03 | P1 | Chat-booked naive times are shifted to UTC by the commitment gate | 3 |
| 4.7 | SCH-E05 + SCH-S09 (recurring items) + SCH-E09 (docstring item) | P1 | Recurring schedules are advertised, then refused or turned into a one-shot | 3 |
| 4.8 | SCH-S03 | P1 (multi-user/production profile) | Cron edit, cancel and the edit tool ignore the tenant; reads are scoped | 1 |
| 4.9 | SCH-S05 (authority half) | P2 | A fired reminder runs as the user with the whole agent minus danger tools | 1 |
| 4.10 | SCH-E06 | P2 | The missed-fire policy is silent and inconsistent | 2 |
| 4.11 | SCH-E12 + SCH-S07 | P2 | Actions that race a fire or a terminal state are lost, misreported or resurrect jobs | 2 |
| 4.12 | SCH-E07 | P2 | Retention erases fresh failure evidence; recovery sees only 500 rows | 2 |
| 4.13 | SCH-S04 | P2 | The page shows current X bookings as "Completed" with no controls; its API reschedules what X Studio refuses | 4 |
| 4.14 | SCH-E08 | P2 | Rebooking a cancelled or failed X proposal reports "scheduled" | 4 |
| 4.15 | SCH-E09 (parser items) + SCH-S06 (timing items) | P2 | Timing parsing and validation: gaps and inconsistencies | 3 |
| 4.16 | SCH-E14 + SCH-S06 (bounds items) | P2 | Unbounded inputs and job counts, fixed execution budget | 5 |
| 4.17 | SCH-S08 | P2 | Remind and cancel clarify cards are English diagnostics with date-only options | 4 |
| 4.18 | SCH-E10 + SCH-S11 | P3 | Settings, RBAC and graph-build work on the event loop | 5 |
| 4.19 | SCH-E11 | P3 | No liveness or outcome observability for cron | 5 |
| 4.20 | SCH-E13 | P3 | Smaller X-engine robustness items (not witnessed) | 5 |
| 4.21 | SCH-S10 | P3 | Scheduling tools hand the chat id to the model | 5 |
| 4.22 | SCH-S12 | P3 | UI convention drift, a third same-origin copy, no actor audit | 5 |
| 4.23 | SCH-DOC + SCH-S09 (documentation items) | P3 | Docs, comments and menus contradict behaviour | 5 |

Original ID to entry:

| Original ID | Entry |
|---|---|
| SCH-E01 | 4.4 |
| SCH-E02 | 4.2 |
| SCH-E03 | 4.6 |
| SCH-E04 | 4.3 |
| SCH-E05 | 4.7 |
| SCH-E06 | 4.10 |
| SCH-E07 | 4.12 |
| SCH-E08 | 4.14 |
| SCH-E09 | 4.7 (docstring item), 4.15 (parser items) |
| SCH-E10 | 4.18 |
| SCH-E11 | 4.19 |
| SCH-E12 | 4.11 |
| SCH-E13 | 4.20 |
| SCH-E14 | 4.16 |
| SCH-DOC | 4.23 |
| SCH-S01 | 4.1 |
| SCH-S02 | 4.5 |
| SCH-S03 | 4.8 |
| SCH-S04 | 4.13 |
| SCH-S05 | 4.2 (delivery half), 4.9 (authority half) |
| SCH-S06 | 4.15 (timing items), 4.16 (bounds items) |
| SCH-S07 | 4.11 |
| SCH-S08 | 4.17 |
| SCH-S09 | 4.7 (recurring items), 4.23 (documentation items) |
| SCH-S10 | 4.21 |
| SCH-S11 | 4.18 |
| SCH-S12 | 4.22 |

How priorities changed in the merge:

| Entry | Parts | Merged priority |
|---|---|---|
| 4.2 | SCH-E02 P1, SCH-S05 P2 | P1 |
| 4.7 | SCH-E05 P1, SCH-S09 P3, SCH-E09 P3 | P1 |
| 4.11 | SCH-E12 P3, SCH-S07 P2 | P2 |
| 4.15 | SCH-E09 P3, SCH-S06 P2 | P2 |
| 4.16 | SCH-E14 P3, SCH-S06 P2 | P2 |
| 4.18 | SCH-E10 P3, SCH-S11 P3 | P3 |

Witness names below refer to `scheduled_engine_probes.py` (`test_e*`) and
`test_scheduled_surfaces_probes.py` (all others). Printed markers are quoted
from the logs.

### 4.1 SCH-S01 (cross-area: Swarm SW-S01) — P1: a background swarm dispatch inherits the graph's "gated" flag and skips approval; a fired reminder can reach it

**Evidence:**

1. **The flag covers the whole step.** When HITL is active,
   `graph_tool_worker.py:818` (re-read) sets `_graph_hitl_gate_ctx=True`
   before safe tools run. `:1596` (re-read) resets it only in the parent.
   Cron child graphs run HITL-active with `auto_deny`
   (`agent_runner.py:976-981`, re-read), so the flag is set during a fire
   too.
2. **The dispatch copies it.** `dispatch_swarm` is `write` tier and never
   auto-denied (`hitl.py:259`). It starts the work with
   `asyncio.create_task(engine.dispatch(task))`
   (`tool_builtins/system.py:799`, re-read). The new task copies the
   context, flag included.
3. **The worker reaches the local registry.** The swarm worker runs tools
   through `execute_effect(tool_registry, ...)` (`swarm/worker.py:526-531`).
4. **The registry skips the bus.** `LocalToolRegistry._execute_inner` skips
   the bus `safety.check()` whenever the flag is set
   (`tool_registry.py:687`, re-read).
5. **Danger tools are within scope.** The default worker scope allows HIGH
   acts (`commitment/scope.py:123-146`). `file_write` is HIGH and `danger`
   (`side_effects.py:113`), and the auto-registered `researcher` worker
   includes it.

The Swarm audit (`swarm-surfaces.md:76-109`) lists more HIGH- or
NONE-classified danger tools that run without a card:

- `file_apply_patch`;
- `git_commit`, `git_push`, `git_merge`;
- `github_create_pr`;
- `run_unit_tests`, `browser_eval_js`;
- `vault_retrieve` and `send_message`, which are classified NONE.

It also notes that a worker with no tool list gets every tool definition
(`worker.py:300-311`).

**A test pins the enabler.**
`tests/test_bus_approval_can_be_answered.py:171-185`
(`test_both_gates_honour_an_actual_approval`) asserts that
`_graph_hitl_gate_ctx` stays in the local registry and stays out of the MCP
manager. The MCP manager already ignores the flag.

**Failure scenario:** a stored reminder prompt fires at 03:00, and the model
calls `dispatch_swarm`. The worker's `file_write`, or any HIGH danger tool
on the worker, runs with no card and no human. The same path exists in
ordinary chat. The Swarm audit's example is a web page read in chat that
steers the model to `dispatch_swarm`.

**Not shown:** CRITICAL acts (`shell_exec`, python, email, `file_delete`,
config) are still refused by the default worker scope while
`swarm_scope_enforce` is on. No witness demonstrates remote code execution.

**Witness:** `test_background_task_inherits_graph_gate_and_skips_bus_approval`
(`INHERITED_GRAPH_GATE_SKIPS_BUS_APPROVAL=True`, `CHAIN_PRESENT=True`).

- Control: a direct danger call asks the bus, is denied, and does not run.
- With the flag set and a background task created, the tool runs and the
  bus is never asked.
- Swarm witnesses: `test_sw_s01_*` in
  `test_swarm_surfaces_probes_2026_10_13.py`.

**Fix direction:**

- The flag must mean "the graph gated THIS call". Bind it per call, only
  around the execution of calls the graph classified and gated, the way
  `_hitl_approved_ctx` already works.
- Start swarm, sub-agent and background work from a clean context, with
  both HITL ContextVars cleared.
- Consider auto-denying `dispatch_swarm` and `spawn_agent(s)` in
  checkpointer-less graphs, or use the declared capability set of 4.9.

One fix closes both SCH-S01 and SW-S01.

**Gate and negative control:**

- A behavioural test drives a real tool-worker turn: `dispatch_swarm`, then
  a fake engine, then `registry.execute("file_write")`. The call must reach
  the bus or be refused.
- A class gate checks that every `create_task`, `spawn_background` or
  `to_thread` started from a tool body carries neither flag (AST scan plus
  runtime probe), with a planted violation as its control.
- Negative control: the current node-wide flag lifetime.
- Re-point `test_both_gates_honour_an_actual_approval` to the per-call rule;
  do not delete it.

### 4.2 SCH-E02 + SCH-S05 (delivery half) — P1: delivery is fire-and-forget; a refused or undeliverable reminder is recorded as DONE

**Evidence:**

- **Three exits, each a log line and a return.** In `scheduler.py:1263-1292`
  (re-read), an invalid target (after the sibling and web-session
  fallbacks, `:1226-1262`), a send exception and `send_failed(result)` each
  produce one `logger.critical` and a `return`.
- **The job is then finalized as a success.** `_execute` calls
  `_finalize(failed=False)` (`scheduler.py:1150-1151`, re-read). The job
  becomes DONE, or a daily job is rescheduled as a success. `last_result`
  holds the agent text.
- **Nothing else records it.** There is no retry, no outbox, no ops alert,
  and no digest or ledger signature. A grep by the engine half found no
  cron reference in `observability/`.
- **No gateway means every fire fails.** The send backends are registered
  only when the gateway handler is built (`graph.py:2386-2398`). Without a
  gateway, every fire answers `Error: no backend 'telegram'`.
- **The page hides recurring failures.** A recurring job's first and second
  failures appear only as a grey "Last run: Error..." on a `pending` row
  (`scheduled.js:194-211`).
- **AGENTS §51 is only half true here.** Cron reads `send_failed`
  (`scheduler.py:1285`), but that changes only the log level.

**Failure scenario:** Telegram, Slack or Discord is briefly unreachable at
fire time, the bot was removed or blocked, or the gateway did not start. The
reminder is lost for good, and the Scheduled page shows it as done. A
CRITICAL line in `kazma.log` is the only trace, and nobody is paged.

**Witnesses:**

- `test_e02_refused_delivery_is_done`, with a refusing backend:
  - the job is DONE;
  - `last_result` does not mention the refusal;
  - no alert is raised;
  - a later poll neither re-sends nor re-runs (1 send, 1 turn).
- `test_refused_delivery_is_recorded_as_done`
  (`REFUSED_DELIVERY_SHOWN_AS_DONE=True`).
- Both logs show
  `CRITICAL ... scheduler.py:1288 [CronScheduler] delivery FAILED ...`.

**Fix direction:** reuse the X notification outbox pattern
(`x_api/notifications.py`):

1. Write a durable delivery row together with the fire outcome.
2. Drain it with a leased worker and bounded exponential retries.
3. Acknowledge only on `sent:`.
4. After N attempts or T hours, reach a terminal `undeliverable` state with
   an ops alert (`cron.delivery_failed`) and a visible status.
5. Record the delivery outcome on the job (`delivered`, `undeliverable`, or
   `refused` with its reason), and show it on the page as a failure.
6. Count deliveries and failures in the daily digest.

**Gate and negative control:**

- A refusing backend leaves the job `delivery_pending`, then
  `undeliverable`, never `done`, with a retry history and exactly one alert
  per job.
- A backend that recovers before the deadline delivers exactly once.
- The page and the API show an undelivered job as a failure.
- Negative control: today's `_deliver`-then-`_finalize` order.

### 4.3 SCH-E04 — P1: a restart or crash during a fire silently loses the reminder, or strands a daily job forever

**Evidence:**

- **Shutdown does not drain fires.**
  - `CronScheduler.stop` (`scheduler.py:805-814`) cancels only the poll
    loop. In-flight `_exec_tasks` are neither awaited nor drained, and
    shutdown then closes the agent under them (`app.py:2349-2392`).
  - The "1) Stop cron first" step (`app.py:2349`, re-read) runs only after
    the document workers and the memory worker are drained, which can take
    up to 45 s + 30 s (`app.py:2284-2346`).
  - The poll loop exits only at its next 30 s tick.
- **Idle checks do not see fires.** `/health/activity` counts only
  registered chat turns (`health.py:353-381`, `active_turns.py:62`).
  `run_agent_turn` never registers a cron fire, so
  `kazma_guard.py --reload --when-idle` can reload during a fire.
- **Boot recovery gives up.** `_recover_stale_running` marks every RUNNING
  one-shot FAILED, with no retry and no alert (`scheduler.py:761-803`). This
  applies whether or not the reminder was already delivered.
- **The daily finalize is not atomic.** It is three separate autocommit
  statements (`scheduler.py:1171-1178`, re-read). A crash after the first
  leaves the job DONE. Recovery handles only RUNNING, and `list_active`
  filters DONE out, so the job never fires again.
- **Exceptions in the error path are lost.** An exception raised in the
  `except` path escapes the exec task. `_exec_done` never retrieves it, and
  the job stays RUNNING until the next boot.
- The comment at `scheduler.py:1143-1149` (re-read) claims "never a silent
  loss".

**Failure scenario:** a guard reload, a package update or a crash lands
inside the fire window, which can last up to 120 s. The one-shot is never
sent and shows "Marked failed on scheduler restart". If the crash lands
inside `_finalize`, a daily reminder stops for good with status DONE. The
live install restarts several times a day (AGENTS §15B0).

**Witnesses:**

- `test_e04_restart_mid_fire_loses_reminder`:
  - the job is RUNNING while `running_turn_count() == 0`;
  - `stop()` leaves the fire task running;
  - after teardown and a new boot, the job is FAILED ("stale RUNNING"), with
    no send and no alert. Log:
    `WARNING ... scheduler.py:800 ... recovered stale RUNNING job ... → FAILED`.
- `test_e04_crash_inside_finalize_strands_daily_job`: the job is delivered
  once, then a simulated death after the DONE statement leaves the daily job
  DONE and absent from `list_active`.

**Fix direction:**

1. Replace RUNNING-as-a-flag with a leased fire record: owner,
   `lease_until`, attempt, and phase receipts (`turn_done`, `delivered`).
2. Make each phase transition one `BEGIN IMMEDIATE` compare-and-swap, and
   advance recurrence together with finalization.
3. Recover per phase:
   - no turn receipt: retry within the job's catch-up window;
   - turn done but not delivered: deliver the stored result;
   - delivered: finalize, never mark failed.
4. On shutdown, drain fires within a bound with `asyncio.wait` (never
   `wait_for`), before the agent closes.
5. Count active fires in `/health/activity`.
6. Retrieve and log exec-task exceptions.

**Gate and negative control:**

- A fault matrix kills a **real child process** after each statement of
  `_execute`, `_deliver` and `_finalize`, in the pattern of
  `tests/test_document_crash_recovery.py`. After restart, each case must end
  with exactly one delivery, or one honest terminal state with an alert. A
  daily job must stay pending with a correct `next_run`.
- `/health/activity` reports busy during a fire, and a `--when-idle` reload
  waits for it.
- Negative control: the current three-statement `_finalize` and the
  RUNNING-to-FAILED recovery.

### 4.4 SCH-E01 — P1: a failed agent turn is recorded, and delivered, as a successful fire

**Evidence:**

- **The turn reports its failure.** `agent/turn.py:263-276`: `run_agent_turn`
  catches every exception and returns `TurnResult(error=..., text="")`.
  `turn.py:249-262` returns `turn_failed=True` with the supervisor's "⚠️"
  text as `text`.
- **The scheduler ignores it.** `scheduler.py:1129-1151` (re-read) reads
  only `_turn.text`, never `.error` or `.turn_failed`, then delivers and
  finalizes as a success.
- **Failure handling is unreachable from the turn.** The daily failure
  budget and the `cron.job_failure` alert (`scheduler.py:1180-1206`) are
  reached only by exceptions raised outside the turn, such as a graph build
  or store error.

**Failure scenario:** the LLM provider is down, or its key is invalid, at
09:00.

- A daily reminder delivers "⏰ Reminder" followed by the stored prompt.
- An agent task ("check BTC and report") delivers the instruction instead of
  a result.
- `failure_count` stays 0, there is no alert, and `last_result` is empty.
- If the supervisor surfaces `turn_failed`, the reminder text is replaced by
  "⚠️ The model provider rejected the request..." and the job is marked
  DONE.

**Witnesses:**

- `test_e01_provider_failure_is_a_successful_fire`
  (`E01_PROVIDER_FAILURE_COUNTED=False`).
- `test_e01_turn_failed_warning_replaces_the_reminder`: the delivered body
  starts with "⚠️", the reminder text is absent, and the status is DONE.

**Fix direction:**

1. Treat `turn.error or turn.turn_failed` as a failed fire: record a typed
   outcome (`turn_failed`), use the failure budget, and alert.
2. Never deliver an error string as the user's reminder.
3. Separate reminders from agent tasks. A reminder's message is fixed when
   it is booked, so deliver it with no LLM at fire time, as the X engine
   does. Only an agent task runs a turn.
4. If an enrichment turn for a reminder fails, still deliver the stored
   message, with a degraded marker.

**Gate and negative control:**

- A stub turn that raises, or returns `turn_failed`, leaves the job
  `failed`, or delivers the stored reminder with an explicit degraded
  marker.
- `failure_count` is incremented for daily jobs, and `cron.job_failure` is
  emitted.
- Negative control: the current `_execute`.

### 4.5 SCH-S02 — P1: the Scheduled page's Edit silently truncates or rewrites the stored prompt

**Evidence:**

1. `ScheduledJob.to_dict()` cuts `prompt[:200]` (`scheduler.py:110`,
   re-read).
2. The list route then shows `extract_post_body(prompt) or prompt`
   (`scheduled_api.py:133-141`).
3. `openEdit` pre-fills the prompt field with `task.summary`
   (`scheduled.js:396-404`, re-read).
4. `saveCron` requires a prompt and always sends it
   (`scheduled.js:418-436`).
5. `reschedule` stores whatever arrives (`scheduler.py:896-935`; `update_job`
   at `:604-639`).

**Failure scenario:** the operator opens a job only to move it by an hour
and presses Save, and the page says "Saved".

- A 600-character instruction is cut to 200 characters.
- A batch job ("POST ONE TWEET ONLY. Call x_post with EXACTLY this text:
  ...") is reduced to the tweet body. At fire time it no longer posts; it
  only delivers the text as a reminder.

**Witness:** `test_page_edit_round_trip_truncates_and_rewrites_prompt` runs
the real router, the real store and the real `scheduled.js` form state.

- `PAGE_EDIT_TRUNCATES_PROMPT=True`: the stored prompt goes from 667 to 200
  characters.
- `PAGE_EDIT_DROPS_WRAPPER_INSTRUCTION=True`: only `Hello from Kazma` is
  left.

**Fix direction:**

- The list returns a display summary, never a truncated "prompt".
- The edit dialog fetches the full prompt from
  `GET /api/scheduled/cron/{id}`.
- A timing-only edit sends no prompt; the server already treats an empty
  prompt as "unchanged".
- An `expected_version` stops a stale form from overwriting a newer prompt.
  This is the same mechanism as 4.11.

**Gate and negative control:**

- A route round trip on the real scheduler (list, open edit, save timing
  only) leaves the stored prompt byte-identical.
- Negative control: the current `openEdit`/`saveCron` pair.
- Replace the fake scheduler in `tests/test_scheduled_api.py` with the real
  one for this test.

### 4.6 SCH-E03 — P1: chat-booked naive times are shifted to UTC by the commitment gate

**Evidence:**

- **The gate reads a naive time as UTC.** `relative_time.py:335-357`
  (re-read): `parse_absolute_timing` goes through `_to_utc`, which gives a
  naive value UTC (`:501-504`, re-read).
- **The gate rewrites the booking to that instant.** `authorize.py:476-510`
  sets `timing` to `_abs_dt.isoformat()` (`:490`, re-read), and the tool
  worker applies the rewrite (`graph_tool_worker.py:218-219`).
- **The scheduler reads the same string in the operator's zone.**
  `parse_timing` documents and tests that a naive ISO time is in the
  operator's zone (`scheduler.py:279-280, 326-331`;
  `tests/test_cron.py:118-122`; audit M14).
- **Two other paths bypass the rewrite.** `edit_scheduled` (act `None`,
  `side_effects.py:89`) and the Web create path (`scheduled_api.py:218`)
  read the string in the operator's zone. `edit_scheduled` also skips the
  memory guard that `schedule_task` enforces.

**Failure scenario:** an operator in Asia/Kuwait asks for 09:00 tomorrow,
and the model passes `"2026-10-14T09:00:00"`.

- Booked from chat, it fires at 12:00 Kuwait time.
- The same string entered on the Web page or through `edit_scheduled`
  fires at 09:00.

This brings back the "9am fires at noon for UTC+3" bug that audit M14
fixed.

**Witness:** `test_e03_commitment_rewrite_shifts_naive_iso`. The rewritten
timing resolves to 09:00Z, the direct parse to 06:00Z
(`E03_SHIFT_HOURS= 3.0`).

**Fix direction:**

1. Create one timing module for cron, the commitment gate and X booking.
2. A naive time always means the operator's zone. Resolve it once, at the
   boundary, into an aware instant plus the zone name.
3. The gate validates against memory, but rewrites only to an aware ISO
   string in the operator's zone, or leaves the string untouched.
4. Route `edit_scheduled` through the same resolver.

This changes AGENTS §20's rewrite-on-allow wording (section 6).

**Gate and negative control:**

- One shared fixture of timing strings, parsed by every path
  (`authorize_effect`, Web create, `edit_scheduled`, X booking), gives the
  same instant in UTC, Asia/Kuwait and America/New_York.
- Negative control: the current UTC-assuming `parse_absolute_timing`.

### 4.7 SCH-E05 + SCH-S09 (recurring items) + SCH-E09 (docstring item) — P1: recurring schedules are advertised, then refused or turned into a one-shot

**Evidence, cron from chat (SCH-E05):**

1. `schedule_task` maps to act `remind` (`side_effects.py:83-84`), so every
   chat booking goes through `_resolve_remind_act`
   (`authorize.py:240-248`).
2. `"daily at 9am"` is neither compact nor absolute
   (`authorize.py:409-481`), so the resolver parses the user's text instead
   (`relative_time.py:802-931`). That parser has no concept of recurrence.
3. With no time phrase it understands, the call is **denied** with "Pass
   schedule_task.timing as Nm/Nh (e.g. 247m) or ISO", which steers the
   model to a one-shot (`authorize.py:583-624`).
4. With any relative phrase, the call is **allowed and rewritten** to
   `request_at + lead` (`relative_time.py:884-912`,
   `authorize.py:565-581`). Examples: "starting tomorrow", or the Arabic
   "كل يوم", where `يوم` defaults to one day (`relative_time.py:91,
   578-613`).

The scheduler itself supports `daily at ...`; the problem is entirely in
the gate.

**Evidence, X (SCH-S09 recurring items; SCH-E09 docstring item):**

- The `x_schedule_post` docstring, which is the schema the model sees,
  offers `'daily at 9am'` (`x_publisher/tools.py:164`, re-read).
- `_parse_when` refuses it (`booking.py:34-65`) after the always-HITL
  approval has been spent.
- The X dialog on the Scheduled page reuses the cron hint "recurring (daily
  at 9am)" (`scheduled.html:299`).
- `docs/docs/guide/x-publisher.md:122` (re-read) repeats `'daily at 9am'`.

**Failure scenario:**

- "Remind me every day at 9am starting tomorrow", or the Arabic equivalent,
  becomes one reminder tomorrow at the current time of day (15:17 UTC in
  the witness). Both the recurrence and the 09:00 anchor are dropped.
- Without such a phrase, the daily reminder is refused, and a model that
  retries books a one-shot.
- On X, the model offers a daily post, the user approves it, and booking
  refuses it.

**Witnesses:**

- `test_e05_daily_reminder_denied_by_commitment_gate`: decision `deny`;
  `E05_REASON` steers to Nm/Nh/ISO.
- `test_e05_daily_reminder_rewritten_to_one_shot`, with an English case and
  an Arabic case: decision `allow`, and both print
  `E05B_REWRITTEN= 2026-10-14T15:17:00+00:00`, which is `request_at` plus
  one day.
- `test_recurring_x_time_is_advertised_then_refused`
  (`RECURRING_X_TIME_ADVERTISED_THEN_REFUSED=True`).

**Fix direction:**

- Add a typed recurrence to the shared timing module (4.6), and let the
  resolver recognize it.
- Validate a recurring timing (zone, anchor, memory conflicts for its first
  occurrence), and never rewrite it into a one-shot.
- Parse a clock time in the user's text ("at 9am", "الساعة 9") instead of
  adding a bare day.
- Generate tool docstrings and UI hints from the grammar each engine
  accepts, and split the X hint from the cron hint.

**Gate and negative control:**

- An English and Arabic corpus of recurring requests, run through
  `authorize_effect`, keeps the recurrence, and its first occurrence
  matches `parse_timing`.
- Every timing example in tool docstrings, manifests and UI hints is
  accepted by its own validator.
- Negative controls: the current resolver (deny, or a one-shot rewrite),
  and the current docstring.

### 4.8 SCH-S03 — P1 (multi-user/production profile): cron edit, cancel and the chat edit tool ignore the tenant; reading is scoped

**Evidence:**

- **Mutations take no tenant.** `CronScheduler.cancel` and `reschedule`
  take only `job_id` (`scheduler.py:889-935`). `SQLiteCronStore.cancel`,
  `job_exists` and `update_job` have no tenant predicate (`:604-691`).
- **The routes and the tool pass the id straight through:** `PUT` and
  `DELETE /api/scheduled/cron/{job_id}` (`scheduled_api.py:232-266`), and
  `edit_scheduled` (`task_scheduler_cron/tools.py:113-140`), whose effect
  profile has no act resolver (`side_effects.py:89`).
- **Reads are scoped.** `list_jobs` scopes reads in multi-user or
  production mode (`scheduler.py:876-887`), and the `cancel_job` resolver is
  tenant-scoped.
- **A missing tenant falls back to `default`.** `schedule()` uses tenant
  `"default"` when no tenant is bound (`:841-846`); X refuses in the same
  case (`x_api/ownership.py:6-16`).
- **Every tenant's jobs go to the operator's Telegram.** Every Web-booked
  job, whatever its tenant, is delivered there (`send_message.py:127-176`).
  This matches AGENTS §16B2 for one operator.

**Failure scenario:** tenant B learns a job id. Ids are `cron-` plus 32
random bits; they are not listed across tenants, but they appear in logs,
ops alerts and notifications.

- B rewrites A's prompt, and at fire time it runs under A's tenant, with A's
  tenant-scoped secrets and memory.
- B can also cancel A's reminder.
- The edit approval card shows the new prompt, not the one it replaces.

**Witness:** `test_cron_mutations_ignore_tenant_while_list_is_scoped`, with
`KAZMA_PRODUCTION=1` and two tenants (`CROSS_TENANT_PROMPT_REWRITE=True`,
`CROSS_TENANT_CANCEL=True`).

- B's list hides A's job.
- B's `PUT` rewrites the prompt, and the row keeps `tenant_id=tenant-a`.
- B's `edit_scheduled` rewrites it again.
- B's `DELETE` cancels it.

**Scope:** single-operator installs are not affected in practice (the
owner's rule: one operator per install). The code advertises a multi-user
mode, so the defect is real there.

**Fix direction:**

- Tenant-scope every cron store mutation (`WHERE job_id=? AND tenant_id=?`).
- Answer another tenant's id exactly like an unknown id.
- Refuse a missing tenant in multi-user mode, as `x_tenant_id` does.
- Give `edit_scheduled` a resolver that checks the job is in the caller's
  tenant, and show the old prompt on the card.
- In multi-user mode, deliver to the booking principal's own address.

**Gate and negative control:**

- A route-by-tool tenant matrix in the style of
  `tests/test_memory_routes_tenant_scope.py`: every `/api/scheduled/*` route
  and every scheduling tool, called as tenant B with A's ids, changes
  nothing.
- Negative control: the current store methods.

### 4.9 SCH-S05 (authority half) — P2: a fired reminder runs as the user with the whole agent minus danger tools

**Evidence:**

- The stored prompt is injected as a `role: user` message, without a fence
  (`scheduler.py:1110-1124`).
- The child graph has the full tool set (`app.py:1225-1229`,
  `agent_runner.py:935-990`). Every `write`-tier tool stays available with
  no human, including:
  - `spawn_agent(s)`, `dispatch_swarm`, `run_research_pipeline`;
  - `knowledge_ingest_*`, `read_url_to_file`, `generate_*`;
  - `memory_store`, `activate_skill`.
- With `safety.hitl.enabled: false`, `build_child_graph` passes `hitl=None`
  (`agent_runner.py:972-973`, re-read). Then only `ALWAYS_HITL` tools and
  git writes are denied, so a fired job may run `shell_exec` or
  `file_write`, or book more jobs.
- The 120 s `wait_for` (`scheduler.py:1129-1138`) does not bound the swarm
  or background work that the turn started.
- No doc states what a fired reminder may do.

**Witness:** none. This half comes from reading the source; the surfaces
report witnessed only the delivery half (4.2).

**Fix direction:**

- Give fired jobs an explicit, declared capability set, shown at booking.
  Default: read tools plus the single delivery.
- Make write-tier delegation and booking opt-in per job, and show them on
  the approval card.
- Label the injected prompt as a stored instruction of known provenance.
- Refuse to fire jobs while HITL is disabled, unless the operator opts in.

**Gate and negative control:**

- A cron-graph tool-surface test: the tool definitions offered to a fired
  job equal the declared set.
- Negative control: the full registry.

### 4.10 SCH-E06 — P2: the missed-fire policy is silent and inconsistent

**Evidence:**

- **Cron.** A one-shot more than 24 h late is marked DONE with "Skipped:
  ...", logged at INFO, with no notice and no alert (`scheduler.py:963-982`,
  re-read, and `:1026-1051`). A fire up to 24 h late is delivered with no
  lateness note. A daily job fires once at any lateness, for example a
  "9am" reminder at 23:00, also with no note.
- **X.** A post more than 5 minutes late is held for review, with a notice
  (`publication_service.py:283-287`, re-read).

**Failure scenario:** after a day of downtime, a reminder is silently
dropped and shown as done. After shorter outages, stale reminders arrive as
if they were on time.

**Witness:** `test_e06_missed_one_shot_skipped_silently`: status DONE, a
"Skipped..." result, no send, no alert, no turn.

**Fix direction:**

- Add an explicit `missed` state.
- Give each job a catch-up window, defaulting to something like the X grace
  period and configurable per reminder.
- Send a "missed while Kazma was offline" notice through the delivery outbox
  (4.2).
- Mark a late delivery with its original due time.
- Document the policy for each kind: reminder, agent task, X post.

**Gate and negative control:**

- Fake-clock tests for each lateness band. "Missed" never reads as "done",
  and each missed job yields exactly one notice.
- Negative control: the current stale skip.

### 4.11 SCH-E12 + SCH-S07 — P2: actions that race a fire or a terminal state are lost, refused without a reason, or resurrect jobs

**Evidence:**

- **Cancel and delete fail on a running job.**
  - `cancel` matches only `status='pending'` (`scheduler.py:683-691`,
    re-read). A RUNNING job answers `not_found`, so a daily job cannot be
    stopped during its fire.
  - The page still offers Edit and Delete on running jobs: `canEdit` and
    `canDelete` include `running` (`scheduled.js:377-384`, re-read).
  - The DELETE route then returns 404 with no `error` field
    (`scheduled_api.py:254-266`, re-read), so the toast says only "Delete
    failed".
- **Edits during a fire are accepted, then lost.** `update_job` forces
  `status='pending'` whenever timing is given (`scheduler.py:628-631`,
  re-read). An edit during a fire is answered `rescheduled`, then `_finalize`
  overwrites it from the in-memory timing: DONE for a one-shot, the old
  cadence for a daily job.
- **The same write brings back finished jobs.** A timing edit makes a DONE,
  FAILED or CANCELLED job pending again, so the chat tool can bring back a
  reminder the user cancelled. A prompt-only edit of a finished job reports
  `rescheduled`, but nothing will run.
- **The page leaves out what the operator needs.** It never shows a job's
  recurrence (`timing`), failure count or destination
  (`scheduled.html:155-182`).
- Cancel and reschedule take no tenant (see 4.8).

**Witnesses:**

- `test_e12_reschedule_during_fire_is_lost`: cancel answers `not_found` for
  a running job; a "2h" edit is answered "rescheduled", and the job ends
  DONE with `timing == "2h"`.
- `test_running_delete_and_cancelled_resurrection`
  (`RUNNING_DELETE_SHOWN_CANNOT_SUCCEED=True`,
  `EDIT_RESURRECTS_CANCELLED_JOB=True`).

**Fix direction:**

- Offer only the actions the state machine allows.
- Enforce them on the server with `expected_status` and a version
  compare-and-swap.
- Record a pending edit or cancellation that `_finalize` honours.
- Return `busy` (409) with a reason, instead of `not_found`/404.
- Make resurrection an explicit "Run again" action.
- Show the recurrence, failure count, last delivery result and destination.

**Gate and negative control:**

- A state-by-action matrix against the real store.
- An edit or cancel during a blocked fire is applied after the fire.
- A JS test that every offered control maps to an allowed transition.
- Negative controls: the current `update_job` and `canDelete`.

### 4.12 SCH-E07 — P2: retention erases failure evidence immediately, and recovery sees only 500 rows

**Evidence:**

- **Purge uses the creation date.** `purge_terminal_jobs` selects by
  `created_at` (`scheduler.py:497-537`). A job created 30 days ago that
  fails today is purged at the next hourly or boot purge.
- **Recovery reads a limited list.** `_recover_stale_running` uses
  `list_all()` with its default `LIMIT 500 ORDER BY created_at DESC`
  (`scheduler.py:764, 467-495`). An old RUNNING daily job behind 500 newer
  rows is never recovered. The poll loop then retries its claim every 30 s,
  and the job never fires.

**Witnesses:**

- `test_e07_purge_erases_fresh_failure_of_old_job`: purge returns 1, and the
  job is gone.
- `test_e07_recovery_misses_old_running_job`: with 520 newer DONE rows, an
  old daily job is still RUNNING after boot.

**Fix direction:**

- Add `finished_at` and purge by it, keeping the last outcome of every
  recurring job.
- Make recovery query `WHERE status='running'` with no limit.
- Add an index on `(status, next_run)` (also in 4.16).

**Gate and negative control:**

- Purge and recovery behave the same with 0, 499 and 5,000 rows.
- Negative controls: the `created_at` purge and the limited `list_all`.

### 4.13 SCH-S04 — P2: the page shows every current X booking as "Completed", hides held and unknown posts, and offers no controls; its API reschedules what X Studio refuses

**Evidence:**

- **New bookings project as `managed`.** They go through the publication
  service and appear in `x_scheduled_posts` as `managed`
  (`x_api/schedule.py:211-213`). `held`, `sending`, `outcome_unknown` and
  `expired` are projected the same way.
- **The page files them as done.** `scheduled.js` treats only `pending` and
  `running` as upcoming; everything else is counted under Completed
  (`scheduled.js:37-70`). `canEdit` and `canDelete` are false for them
  (`:377-384`).
- **The status text is raw.** There are no `scheduled.st_managed`,
  `st_held` or `st_outcome_unknown` keys, so the raw English status shows in
  both languages (`scheduled.js:219-224`, catalog `i18n/catalog/scheduled.py`).
- **The page's reschedule skips checks X Studio makes.**
  `PUT /api/scheduled/x/{id}` calls `reschedule_legacy`, which takes no
  `expected_version` and skips the account and credential-binding check that
  X Studio's `update_schedule` enforces (`publication_service.py:51-70` vs
  `:472-488`). The fire step then holds the post (`:283-288`), so the API
  answers "ok" for a reschedule that will not publish.

**Failure scenario:**

- Upcoming reads 0 while three tweets are queued to publish.
- A `held` post (awaiting approval) or an `outcome_unknown` post is filed as
  "Completed", which is the opposite of what needs attention.
- The page cannot cancel any of them; only X Studio can.

**Witness:** `test_new_x_bookings_are_managed_and_page_treats_them_as_done`:

- `MANAGED_X_POST_COUNTED_DONE_NO_CONTROLS=True`: `counts.upcoming=0`,
  `done=1`, no Edit, no Delete, label `managed`.
- `HELD_AND_UNKNOWN_COUNTED_DONE=True`.
- `SCHEDULED_PAGE_RESCHEDULES_WHAT_X_STUDIO_REFUSES=True`: after an account
  change, `update_schedule` raises, while `PUT /api/scheduled/x` returns
  200.

**Fix direction:**

- Read the operation truth (`queue_inventory`), not the legacy projection.
- Map each state to a declared presentation class: upcoming, needs
  attention, published, cancelled, failed.
- Route Edit and Cancel through the revision-bound queue actions.
- Retire `PUT/DELETE /api/scheduled/x/{id}`, or align them with the queue
  actions.

**Gate and negative control:**

- A JS test enumerates every projected X status (from `project_operation`'s
  map) and every cron status. Each must map to an explicit class with
  catalog keys in both languages. Negative control: the current
  `else c.done++`.
- A route test checks that both X reschedule paths enforce the same binding.
- The Arabic page tour seeds a `managed` post and a `held` post.

### 4.14 SCH-E08 — P2: rebooking a cancelled or failed X proposal reports "scheduled" but nothing will fire

**Evidence:**

- **Both surfaces reuse a fixed key.** Chat
  (`x_publisher/tools.py:174-188`) and Web (`scheduled_api.py:300-308`)
  book with `idempotency_key = "proposal:" + proposal_id`.
- **The store replays any existing operation.** `reserve` returns an
  existing operation with that key whatever its state
  (`publication_store.py:198-203`, re-read).
- **The reply reports success.** `schedule()` then answers
  `(True, {"scheduled": True, ..., "state": "cancelled"})`, and echoes the
  **requested** `fire_at` (`publication_service.py:439-450`). The tool adds
  `ok: true` and "Kazma publishes it at the scheduled time".
- **The gate does not refuse used drafts.** The proposal gate refuses only
  discarded drafts (`authorize.py:1156-1180`, re-read).

**Failure scenario:**

- The operator cancels a scheduled post, or it fails as rejected, then
  rebooks the same draft for a new time. Chat and the API report success at
  the new time, while the stored operation is still cancelled or failed,
  with the old time.
- Rebooking a still-pending proposal at a different time also reports the
  new time while keeping the old one.

**Witness:** `test_e08_rebook_after_cancel_reports_scheduled`:

- the reply says `scheduled: True`;
- the operation state is `cancelled`;
- the stored `due_at` is the first time, while the reply's `fire_at` is the
  new time;
- the X client is never reached.

**Fix direction:**

- An existing key in a terminal state either conflicts ("this draft's
  booking was cancelled; restore it or use a new booking") or mints a new
  key revision.
- A non-terminal key booked at a different time returns a conflict, or
  performs an explicit reschedule.
- The response always echoes the stored `due_at` and state, and `ok` is
  false unless the state is `scheduled`.

**Gate and negative control:**

- A rebook after cancel, after `failed_permanent`, and while pending at a
  new time each yields a non-success response or a new scheduled operation,
  never `scheduled: True` over a terminal row.
- Negative control: the current `reserve` replay.

### 4.15 SCH-E09 (parser items) + SCH-S06 (timing items) — P2: timing parsing and validation have gaps and disagree across paths

**Evidence:** `parse_timing` (`scheduler.py:272-335`) lowercases its input
(`:293`). The consequences:

- **The `Z` suffix is rejected** ("Unparseable timing"). The remind path
  survives only because the gate rewrites to `+00:00`; `edit_scheduled`, X
  booking and the Web create path fail.
- **Past ISO times are accepted** as "scheduled". They fire on the next
  poll, or are silently skipped as DONE when more than 24 h old (4.10).
- **`0m` is accepted.**
- **Very large numbers return 500.** `99999999999h` raises `OverflowError`
  (logged at `scheduler.py:300`). The route catches only `ValueError`
  (`scheduled_api.py:226-230`), so the answer is 500 `internal_error`.
- **Bad edit timing returns 404** (`scheduled_api.py:247`).
- **The form does not state the zone.** A naive ISO time is read in the
  scheduler's zone, which the form does not state for one-shot times.
- **DST gap and fold times are accepted** silently by cron, while X booking
  refuses them (`booking.py:67-85`).
- **Recurrence is limited** to `daily at <1-12>(am|pm)`: no minutes,
  weekdays, intervals, end date or count.

**Witnesses:**

- `test_e09_parser_gaps`: `Z` rejected, `daily at 9:30am` rejected, a past
  time scheduled, and an America/New_York gap time accepted by cron but
  refused by `_parse_when`.
- `test_cron_routes_accept_past_zero_huge_and_misreport`:
  `CRON_ACCEPTS_PAST_TIME=True` (X refuses the same input),
  `OVERFLOW_TIMING_IS_500=True`, `BAD_EDIT_TIMING_IS_404=True`, and `0m`
  accepted.

**Fix direction:** one shared, typed validator for cron and X, in the timing
module of 4.6 and 4.7:

- accepts RFC 3339;
- accepts future times only, in a finite range;
- applies one DST policy;
- supports a typed recurrence (an RRULE subset: FREQ daily or weekly,
  BYHOUR/BYMINUTE, BYDAY, UNTIL/COUNT, TZID);
- states the zone in errors and on the form;
- answers 400 for validation failures.

**Gate and negative control:**

- A table of timing inputs run through both the cron and X paths.
- Negative control: the current parser.

### 4.16 SCH-E14 + SCH-S06 (bounds items) — P2: unbounded inputs and job counts, fixed execution budget

**Evidence:**

- **No caps.** There is no cap on the number of jobs, per tenant or overall.
- **No size limits.** The engine does not bound the prompt size
  (`scheduler.py:816-857`), and `CronCreateBody` has no `max_length` on
  `prompt` or `timing` (`scheduled_api.py:37-44`).
- **No rate limit.** There is no rate-limit bucket for `/api/scheduled/*`.
- **Cost per poll and fire.** `list_active` is a full scan every 30 s with
  no index, and every fire builds a fresh supervisor graph.
- **A fixed time budget.** The 120 s `wait_for` (`scheduler.py:1129-1138`)
  means any agent task longer than 120 s always fails, and a daily one is
  permanently FAILED after 3 days.

**Witness:** `test_cron_routes_accept_past_zero_huge_and_misreport`
(`ONE_MEGABYTE_PROMPT_ACCEPTED=True`). The SCH-E14 items come from code
reading and have no witness.

**Fix direction:**

- Caps per tenant and per thread, with typed refusals.
- Body limits; the surfaces half suggests prompt ≤ 8 KB and timing ≤ 100
  characters.
- A rate bucket.
- A per-kind execution budget (reminder vs agent task) in configuration.
- An index on `(status, next_run)`.

**Gate:**

- Body-size and cap tests.
- A load test with 1,000 pending jobs and 50 due at once, with bounded
  latency.

### 4.17 SCH-S08 — P2: remind and cancel clarify cards are English diagnostics with date-only options

**Evidence:**

- **The labels are built in English on the server.** Examples: "Use the
  memory-anchored date (2026-10-14)", "Use the from-now date (...)",
  "Cancel", the cancel resolver's "Cancel cron-... — <prompt[:40]>", and
  "Don't cancel anything".
- **The question is an internal reason**, such as "job_id 'x' is not a
  pending job..." (`authorize.py:324-344, 745-758`).
- **The client paints the labels as they are.** `chat.js` renders
  `opt.label` raw (`chat.js:5690-5694`).
- **The options show only a date.** The two remind options use `.date()`,
  so two different instants on the same day look identical, with no time
  and no zone.

**Witness:** `test_remind_clarify_options_are_english_and_date_only`.

**Fix direction:**

- Send structured options: a `kind`, an ISO instant and the job id.
- Render them on the client with catalog keys and a `KazmaFormat` date and
  time in the scheduler's zone.
- Phrase the question for the user, and keep diagnostics for the model.

**Gate and negative control:**

- An Arabic e2e step that triggers a remind clarify and asserts there is no
  English and the time is visible.
- Add the semantic card to the `english_on` coverage.
- Negative control: the current label builder.

### 4.18 SCH-E10 + SCH-S11 — P3: settings, RBAC and graph-build work on the event loop, through wrappers the stall gate does not follow

**Evidence:**

- **Time zone reads (engine half).** `schedule`, `reschedule`,
  `_finalize`, `_recover_stale_running` and `list_jobs` (through `to_dict`,
  then `annotate_fire_times`) reach `get_config_store().get("cron.timezone")`
  synchronously from async code (`scheduler.py:145-147`).
- **Graph builds (engine half).** `_execute` calls `self._graph_builder()`
  inline (`scheduler.py:1107`). That builds a full supervisor graph and
  calls `get_hitl_config`, which is a declared loop-stall helper
  (`tests/test_static_gates.py:903`; `agent_runner.py:971`).
- **RBAC and settings reads (surfaces half).**
  - `GET /api/scheduled/tasks` and the `list_scheduled` tool call
    `multi_user_or_production()`, which calls `multi_user_enabled()`, which
    calls `_load_users_from_store`, a listed helper
    (`tests/test_static_gates.py:934`).
  - They also call `resolve_cron_timezone_name()` once per job.
  - `POST /api/scheduled/cron` and `schedule_task` read
    `connectors.telegram.*` (`operator_telegram_target`) and
    `cron.timezone` on the loop.
  - `x_cancel_scheduled_post` reads SQLite on the loop in its error path.
- **Why the gate misses them.** The gate follows only private,
  same-module wrappers. Public wrappers (`multi_user_or_production`,
  `get_cron_timezone`, `resolve_delivery_target`, `parse_timing`,
  `annotate_fire_times`) and an `app.py` closure hide the calls.
- **Impact not measured.** ConfigStore caching on Postgres was not
  measured; the stall risk is reasoned from AGENTS §35 and §44, not
  observed.

**Witnesses:**

- `test_e10_cron_reads_settings_on_the_loop`.
- `test_cron_routes_read_settings_and_rbac_on_the_loop`
  (`SETTINGS_AND_RBAC_READ_ON_LOOP=True {'rbac': 1, 'tz': 5, 'target': 1}`).

**Fix direction:**

- Resolve the time zone, tenant posture and delivery address once, in a
  thread, at the route or tool boundary, and pass them down.
- Build the child graph through `asyncio.to_thread`.
- Add these wrappers to `_LOOP_STALL_HELPERS`, or make the gate follow
  public and imported wrappers that reach a listed helper.

**Gate and negative control:**

- Extend the static gate, with a planted async caller of
  `multi_user_or_production` as its control.
- A behavioural loop-trace test.
- Add the scheduled routes to the GET-route loop walk, with the RBAC store
  traced.

Cross-area: section 5.

### 4.19 SCH-E11 — P3: no liveness or outcome observability for cron

**Evidence:**

- **Health checks a flag, not the loop.** `check_cron` and
  `check_schedulers` read `_running`, not the poll task or a heartbeat
  (`health.py:167-220`). A dead poll loop reports "ok".
- **Nothing is recorded per cycle or outcome.** The X loop calls
  `record_cycle`; cron records nothing. There are no counters for fires,
  lateness, failures or deliveries, and nothing in the daily digest or the
  weekly ledger.
- **One alert only.** The only alert is the first failure of a daily job.
  The final transition of a daily job to FAILED (`scheduler.py:1194-1199`)
  is logged with `logger.error` only.

**Witness:** `test_e11_health_ok_with_dead_poll_loop`.

**Fix direction:**

- A poll heartbeat with a staleness threshold, checked in readiness.
- Metrics and digest lines for fires, missed, failed and undelivered jobs,
  and lateness p50/p95.
- An alert on budget exhaustion.

**Gate:**

- Cancelling the poll task makes readiness degraded within one interval.
- An emitted-line ledger gate, in the pattern of `tests/_emitted_lines.py`.

### 4.20 SCH-E13 — P3: smaller X-engine robustness items (code reading, not witnessed)

**Items:**

- **One bad row stops the cycle.** `fire_due_operations`
  (`publication_service.py:526-529`) dispatches due rows with no per-row
  isolation. A row that raises aborts the cycle for every later row.
- **A large batch can miss its window.** With a 20 s client timeout, a large
  batch due in the same minute can push the tail past the 5-minute missed
  hold. The hold itself is safe, but those posts then need manual review.
- **Unsent posts are not retried.** `ConnectError`/`ConnectTimeout`
  (`not_sent`) become `failed_permanent` (`publication_store.py:315-325`)
  instead of a bounded retry, although the post is provably unsent.
- **No retention.** `x_operations`, `x_operation_events` and the delivered
  `x_notification_outbox` rows are never pruned. The posting caps bound the
  growth.
- **Mixed time zones in one reply.** The booking reply renders `fire_at` in
  the server's local zone, while `tz` reports the cron zone
  (`publication_service.py:448`).

**Fix direction:**

- A per-row try/except with a held state and a notice.
- A bounded automatic retry for `not_sent`, within the missed window.
- Retention of terminal operations by age.

**Gate:**

- A raising row does not stop a later due row from being claimed in the
  same cycle.
- A `not_sent` retry test.

### 4.21 SCH-S10 — P3: scheduling tools hand the chat id to the model

**Evidence:** the chat id reaches the model through two tools.

- `schedule()` returns `delivery_target` (`scheduler.py:871`), and
  `schedule_task` serializes it into the tool result
  (`task_scheduler_cron/tools.py:57-64`).
- `list_scheduled` returns `to_dict()`, which includes `delivery_target`,
  for every job.

The `telegram:<chat_id>` string then sits in the messages, the checkpoint,
the chat history, memory episodes and the provider request. This is against
the spirit of AGENTS §2.

**Witness:** `test_schedule_task_result_hands_the_chat_id_to_the_model`
(`SCHEDULE_TOOL_RETURNS_DELIVERY_TARGET=True`).

**Fix direction:** return the platform plus a non-identifying label ("your
Telegram"), and keep the address on the server.

**Gate and negative control:**

- No scheduling tool result matches `^\w+:\d+`.
- Negative control: the current result.

### 4.22 SCH-S12 — P3: UI convention drift, a third copy of the same-origin rule, and no actor audit

**Evidence:**

- **Dates bypass `KazmaFormat`.**
  - `fmtAbs` and `fmtRel` call `Intl` with `<html lang>` directly instead of
    `window.KazmaFormat` (`scheduled.js:98-128`). X Studio's `fmtWhen` does
    the same (`x_studio.js:674-681`).
  - The digits then depend on the browser engine. In Node 24 (CLDR 48),
    `ar` gives Latin digits, but `ar-EG`, `ar-SA` and `ar-KW` give
    Arabic-Indic digits.
  - No class gate catches direct `Intl` or `toLocale*` use, and the
    absolute time has no zone label.
- **A local same-origin copy.** `_verify_same_origin` is a local copy
  (`scheduled_api.py:62-76`; also in `x_api.py` and `email_api`). It
  compares the Origin host with `Host` only, and ignores the declared
  browser origins that the global middleware and the WebSocket check trust
  (AGENTS §45). A proxy that rewrites `Host` gets 403 on every Scheduled
  mutation.
- **No actor audit.** Cron rows record no actor (`created_by` or
  `updated_by`). Cancel logs nothing (`scheduler.py:889-894`).

**Witnesses:**

- `test_page_dates_bypass_kazmaformat_and_x_menu_label`
  (`PAGE_DATES_BYPASS_KAZMAFORMAT=True`).
- `test_local_csrf_copy_ignores_declared_public_origin`
  (`LOCAL_CSRF_REFUSES_DECLARED_ORIGIN_WHEN_HOST_DIFFERS=True`).
- `test_cron_rows_record_no_actor` (`CRON_ROWS_HAVE_NO_ACTOR=True`; the
  columns listed in the log contain no actor).

**Fix direction:**

- Format dates through `KazmaFormat`, and show the zone.
- Add a static gate that refuses `Intl.DateTimeFormat` and `toLocale*`
  outside `locale_format.js`, with declared exemptions.
- Replace the three same-origin copies with one helper over
  `configured_browser_origins()`.
- Record the actor and source (`web`, `chat` or `tool`) on every cron
  mutation.

**Gate and negative control:**

- The `Intl` gate, with a planted call as its control.
- A CSRF test with `KAZMA_PUBLIC_URL` and a rewritten `Host`; the current
  copy is the control.
- A test that a mutation's audit row names its principal.

**Note:** the visible Arabic-digit effect depends on the browser engine. It
was not observed as a defect in a real browser.

### 4.23 SCH-DOC + SCH-S09 (documentation items) — P3: docs, comments and menus contradict behaviour

`SCH-DOC` is the engine half's tracker row for code comments and AGENTS.md
text. The SCH-S09 documentation items are the docs pages, the manifest and
the `/x` menu. Every item is listed in section 6.

**Witness:** `test_page_dates_bypass_kazmaformat_and_x_menu_label`, menu
half (`X_MENU_PROMISES_SCHEDULED_POSTS=True`). Everything else in this entry
comes from reading the docs and the code.

**Fix direction:**

- Correct each item in the same change as the behaviour it describes.
- Add a "What a scheduled task can do when it fires" section (see 4.9).

**Gate:**

- A docs check that the route paths named in `x-publisher.md` exist in the
  built app.
- The docstring-example gate of 4.7.
- The behavioural tests of the entries each comment refers to.

## 5. Cross-area overlaps

| This entry | Other area | Relation |
|---|---|---|
| 4.1 SCH-S01 | Swarm **SW-S01** (`swarm-surfaces.md:76-109`, tracker row at `:432`) | Same mechanism: the node-wide `_graph_hitl_gate_ctx` is copied into the `dispatch_swarm` task (`system.py:799`), and the local registry skips the bus. The Swarm half reaches it from ordinary chat; the Scheduled half adds the fired-reminder path. **One fix and one evidence set close both rows.** The Swarm audit also states that neither AGENTS §7A nor §30 holds for detached swarm work (`swarm-surfaces.md:382`). |
| 4.18 SCH-E10 + SCH-S11 | KB-S06 (`kb-surfaces.md:254`), MEM-S10 (`memory-surfaces.md:234-240`), MEM-R03 (`memory-recall.md:165`), SW-E07 (`swarm-engine.md:131-137`), TT-07 (`time-travel.md:290`) | Each names helpers missing from `_LOOP_STALL_HELPERS`. KB-S06 and MEM-S10 state, as SCH-S11 does, that the gate's wrapper inference does not cross modules or public wrappers. Fixing the inference once serves all of them. Each area still lists its own helpers. |

## 6. Stale documentation, comments and AGENTS.md claims

### 6.1 AGENTS.md

| Claim | Where | What the code does | Source |
|---|---|---|---|
| "Legacy rows with empty `delivery_target` fall back to `thread_id`, then `"{platform}:unknown"`" | AGENTS §16B, `AGENTS.md:1365-1366` | The code adopts a sibling job's target, then tries a web-session lookup, then logs CRITICAL "UNDELIVERABLE" and sends nothing (`scheduler.py:1226-1271`, re-read). | Engine |
| "The fire-time SessionStore lookup is NOT a viable fallback" | AGENTS §16B, `AGENTS.md:1363` | The code still does a session lookup as step 3 (`scheduler.py:1249-1262`). Harmless, but it contradicts the text. | Engine |
| "Two invariants must hold" | AGENTS §16, `AGENTS.md:1347` | The section lists A, B, B2 and C. | Merge |
| The cron delivery "logs `delivery FAILED` (CRITICAL) or `delivered`" | AGENTS §51, `AGENTS.md:5144-5145` | True, but the job is still finalized DONE. Reading `send_failed` changes only the log level (4.2). | Engine |
| "Double-gating prevention: graph sets ContextVars ... so `LocalToolRegistry.execute` does not re-prompt the bus" | AGENTS §7A, `AGENTS.md:242-243` | The flag is set for the whole tool-worker step and copied into detached tasks, so work the graph never gated also skips the bus (4.1). The Swarm audit reaches the same conclusion (`swarm-surfaces.md:382`). | Surfaces, Swarm |
| The remind act "rewrites the tool args to the memory-correct fire_at"; "Rewrite-on-allow: the gate's fire_at wins over the model's args" | AGENTS §20B and §20C, `AGENTS.md:2013-2015, 2031-2032` | The rewrite re-zones a naive time to UTC (4.6) and turns a recurring request into a one-shot (4.7). The fix must restate the invariant: the instant is computed in the operator's zone, and a recurrence is never rewritten. | Merge (from SCH-E03, SCH-E05) |
| "`relative_time.py` — `resolve_remind` (EN+AR). G2-measured (0 false-allow)." | AGENTS §20D, `AGENTS.md:2063` | The measurement does not cover recurring requests. The SCH-E05 witness allows one English and one Arabic daily request and rewrites both to a one-shot. | Merge (from SCH-E05) |
| "what the user approves is what publishes" | AGENTS §29C, `AGENTS.md:3136-3138` | This holds for the text. On a rebook (4.14), the approved time is not the stored time, and the reply reports `scheduled: True` over a cancelled row. | Merge (from SCH-E08) |
| §16A (`graph_builder=`), §16B (target captured at booking), §16C (autocommit store) | AGENTS §16 | True (`app.py:1225-1236`; `task_scheduler_cron/tools.py:39-48`; `scheduler.py:391`). | Engine |

### 6.2 Code comments and docstrings

| Claim | Where | What the code does |
|---|---|---|
| "Deliver BEFORE finalizing ... worst case is a duplicate ..., never a silent loss" | `scheduler.py:1143-1149` (re-read) | False. Boot recovery marks an interrupted one-shot FAILED, with no retry and no alert, whether or not it was delivered (4.3). |
| "1) Stop cron first so no new jobs fire (audit C3)" | `app.py:2349` (re-read) | Not first. It runs after the document and memory workers are drained (up to 45 s + 30 s, `app.py:2284-2346`). The poll loop exits at its next 30 s tick, and in-flight fires are never drained (4.3). |
| "both schedulers fire overdue work on their next poll by design" | `health.py:200-202` (re-read) | X holds anything more than 5 minutes late (`publication_service.py:283-287`). Cron silently skips one-shots more than 24 h late (`scheduler.py:963-982`). |
| "Only a provably-unsent error (connection refused before send) is retried" | `x_api/schedule.py:14-17` (re-read) | Only HTTP 429 is deferred. A `not_sent` error becomes `failed_permanent` (`publication_store.py:315-325`) (4.20). |
| `x_schedule_post` `when`: "'5m', '1h', 'daily at 9am', or an ISO timestamp" | `x_publisher/tools.py:164` (re-read) | Booking refuses recurring times (`booking.py:34-65`) (4.7). |

### 6.3 Docs, UI text, manifests and menus (SCH-S09)

| Claim | Where | What the code does |
|---|---|---|
| `x_schedule_post` takes `'daily at 9am'` | `docs/docs/guide/x-publisher.md:122` (re-read) | Refused (4.7). |
| The X dialog hint "recurring (daily at 9am)" | `scheduled.html:299` | The X path refuses recurring times (4.7). |
| The Studio planner reschedules with `PUT /api/scheduled/x/{id}` | `x-publisher.md:87` (re-read) | The code uses `/api/x/queue/{op}/reschedule` and `/api/x/queue/{op}/cancel`. |
| The `x_list_scheduled` manifest lists four statuses (pending, fired, cancelled, failed) | `kazma-skills/kazma_skills/native/x_publisher/skill_manifest.yaml:71-72` (re-read) | Rows can also be `managed`, `held` or `outcome_unknown`. |
| The scheduling tool list | `docs/docs/guide/native-skills.md:249` | Leaves out `edit_scheduled`. |
| Auth "Session"; the list is "upcoming" | `docs/docs/reference/api-routes.md:231-238` | Platform RBAC requires operator or higher, and the list returns history too. |
| `_EXTENDED_DANGER` is "a separate, broader list" | `docs/docs/guide/configuration.md:271` (re-read) | AGENTS §7 (`AGENTS.md:288`) says it is a copy of the canonical list with identical contents. |
| The `/x` menu entry promises "scheduled posts" | `command_catalog.py:183-190` | `/x` has no scheduling subcommand (`commands.py:2349-2362`). |
| (missing) What a fired reminder may do | no page | See 4.9. |

## 7. Staged path to industrial readiness

The two halves each proposed three stages. They are combined here into
five. Stages 1 and 2 hold every P1 except 4.6 and 4.7, which need the
shared timing module of Stage 3.

### Stage 1 — Approval and tenancy boundaries (4.1, 4.8, 4.9)

- Make HITL approval state per call, and stop it crossing task boundaries.
  Coordinate this with SW-S01.
- Give fired jobs a declared capability set, shown at booking.
- Refuse to fire jobs while HITL is disabled, unless the operator opts in.
- Tenant-scope every cron mutation, and refuse a missing tenant in
  multi-user mode.
- Deliver to the booking principal's own address.

**Exit gate:**

- The SCH-S01/SW-S01 end-to-end test (a real tool worker, then
  `dispatch_swarm`, then a worker's danger call) reaches a gate or is
  refused.
- The detached-work class gate is green, with its planted violation.
- The route-by-tool tenant matrix is green, with its negative controls.
- A fired job's offered tools equal its declared set.

### Stage 2 — One durable fire record, honest outcomes, no silent loss (4.2, 4.3, 4.4, 4.5, 4.10, 4.11, 4.12)

Model each fire as a durable record, as the X publisher does:

- States: `pending`, then `claimed` (owner, lease), `turn_done`,
  `delivering`, `delivered`; terminal states `failed`, `undeliverable`,
  `missed` and `cancelled`.
- Every transition is one `BEGIN IMMEDIATE` compare-and-swap, and
  recurrence advances in the same step as finalization.
- Reminders deliver their stored message deterministically. Agent tasks run
  a turn, and a failed turn is a failure.
- Deliveries go through a leased outbox with bounded retries and an alert
  on terminal failure.
- Recovery decides per phase: retry, deliver the stored result, or
  finalize. Never "RUNNING means FAILED".
- Shutdown drains fires within a bound, and `/health/activity` counts them.
- Edits and cancels carry a version and an expected status; illegal actions
  are neither offered nor accepted.
- The edit dialog never sends a summary back as the prompt.
- Retention purges by `finished_at`, and recovery reads every running row.

**Exit gate:**

- A real-process crash matrix over every statement boundary gives exactly
  one delivery, or one honest terminal state with an alert, for both
  one-shot and daily jobs.
- A provider outage gives a failed fire plus an alert, never an error
  delivered as the reminder.
- A refusing chat platform gives `undeliverable` plus an alert, never DONE.
- A guard `--when-idle` reload waits for an in-flight fire.
- The edit round trip leaves the prompt byte-identical.
- The state-by-action matrix on the real store is green.
- A missed job never reads as done, and yields exactly one notice.

### Stage 3 — One time model (4.6, 4.7, 4.15)

Extract one timing and recurrence module, and use it in the commitment
resolver, `schedule_task`, `edit_scheduled`, the Web API and X booking:

- RFC 3339 input;
- a naive value always means the operator's zone;
- an explicit DST policy;
- past times refused;
- a typed recurrence with an anchor, an end and a count, kept from end to
  end;
- tool docstrings and hints generated from the grammar.

The commitment gate validates and annotates. It never turns a recurrence
into a one-shot, and never re-zones a time. AGENTS §20 is updated in the
same change.

**Exit gate:**

- One shared fixture gives the same instant or series on every path. The
  fixture covers English and Arabic text, ISO forms, recurring forms, and
  DST transitions in UTC, Asia/Kuwait, America/New_York and Europe/London.
- A fake-clock simulation across two DST transitions shows each daily
  occurrence exactly once, at the intended local time.
- Every timing example in docstrings, manifests and hints is accepted by its
  validator.

### Stage 4 — Surfaces tell the truth (4.13, 4.14, 4.17)

- One presentation map from cron and X operation states to classes, with
  catalog keys in both languages.
- The page reads X operation truth, and its Edit and Cancel are
  revision-bound.
- X rebook semantics: a conflict or a new revision, never a replay reported
  as success.
- Structured, localized clarify cards.
- The Arabic page tour is seeded with `managed`, `held`, `outcome_unknown`,
  `running` and failed-recurring rows.

**Exit gate:**

- JS tests over every projected status pass.
- The two X reschedule paths enforce the same binding.
- The rebook tests (after cancel, after failure, while pending) pass.
- The Arabic tour finds no English and no raw status ids.
- The Arabic clarify e2e step shows a time and a zone.

### Stage 5 — Bounds, operability, documentation and qualification (4.16, 4.18 to 4.23)

- One shared set of input bounds: body limits, per-tenant caps, a rate
  bucket and a per-kind execution budget.
- An index on `(status, next_run)`.
- Event-loop-safe boundaries, and the extended stall gate (coordinated with
  the other areas, section 5).
- A cron heartbeat in readiness, plus metrics and digest lines: fires,
  lateness p50/p95, missed, failed, undelivered.
- X per-row isolation, a bounded `not_sent` retry, and retention.
- No chat id in tool results.
- `KazmaFormat` with a zone label, one same-origin helper, and an actor
  audit.
- Docs, comments and AGENTS.md corrected (section 6).
- The fake scheduler in `tests/test_scheduled_api.py` replaced by the real
  store, and `scheduled.js` added to `tests/js`.

**Exit gate (qualification):** the engine half's 7-day fake-clock soak, with
thousands of jobs, injected provider, delivery and database failures, and
restarts every few hours. It must show:

- zero silent losses;
- every non-delivered job visible with its reason, and alerted once;
- p95 fire lateness at or below one poll interval plus the turn time, under
  the named load;
- readiness degraded within one interval of a dead loop;
- the 1,000-job / 50-due load test within its latency bound;
- every witness in this folder inverted into a regression test with its
  negative control, and the docs checks passing.

## 8. Limits

- **This is a merge, not a new audit.** No test, server or container was run
  for it. Witness and suite results are the auditors' runs (section 2). The
  merge re-read only the lines listed in 2.5. Other line numbers are the
  auditors', valid for `d7e27c19` plus the 2026-10-13 tree, in which the
  in-scope files are unmodified.
- **No combined score.** The two halves score different axes:
  capability/architecture against design intent. The merge does not average
  them.
- **Real failures were not exercised.** There was no real process kill, no
  real Telegram, Slack, Discord or X call, and no real LLM. Crash windows
  were simulated at statement boundaries.
- **SCH-S01** was shown at the registry boundary, plus a located source
  chain. The Swarm audit ran real `dispatch_swarm`, `SwarmEngine` and
  `LocalToolRegistry`, with a stand-in worker. Neither audit ran a full
  graph turn with the real worker. Neither enumerated which deployment
  worker templates expose HIGH danger tools beyond `file_write`.
- **Tenancy** was simulated with a header middleware and
  `KAZMA_PRODUCTION=1`. Job-id leakage paths were reasoned about, not
  measured. Single-operator installs are not affected in practice by 4.8.
- **No browser.** `scheduled.js` ran in a node `vm` sandbox. Template
  rendering and the Discord, Slack and Telegram renderings of the clarify
  card were not checked.
- **Multi-host.** Coordination is SQLite-only by design, and the
  runtime-ownership fence prevents two runtimes per data directory. Multiple
  replicas on separate hosts are unsupported and were not tested.
- **The SCH-E10/S11 impact was not measured.** ConfigStore caching on
  Postgres was not measured; the stall risk is reasoned, not observed.
- **SCH-E03 assumptions.** The impact assumes an operator zone away from
  UTC, such as Asia/Kuwait, the zone the code comments cite. The live
  install's `cron.timezone` was not read.
- **Partly or not reviewed:**
  - the X mentions and reply loop, thread dispatch (`x_threads`) and shadow
    mode, except where they touch scheduled posts;
  - the commitment layer's other resolvers;
  - the cron child graph's tool behaviour inside real LLM turns, beyond
    `auto_deny`;
  - DST beyond the sampled zones, and tzdata availability on every
    deployment profile;
  - TUI scheduling surfaces;
  - X Studio beyond its schedule and reschedule paths;
  - Postgres-backed stores;
  - performance and load.
- **No penetration test** was performed.
