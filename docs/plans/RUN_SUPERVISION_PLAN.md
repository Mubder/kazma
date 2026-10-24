# Interactive run supervision

Status: PROPOSED -- 2026-10-24. No code yet. Owner decisions are listed in "Open decisions".

## Objective

When anything Kazma runs gets stuck or fails, the owner hears about it in the
chat the work started from, sees what happened, and can act on it from that
message: retry, resume, cancel, look closer, or ask Kazma to diagnose. The same
facts are on one web view and answer "what is running?" in any chat.

"Anything Kazma runs" means every unit of work that outlives a single request:
chat turns, swarm tasks, scheduled jobs, document jobs, research sessions,
crawls and pipelines, X publication operations, the memory work queue, and the
maintenance jobs.

## Baseline (what exists on 2026-10-24)

Every kind already keeps durable state and most have their own controls. What
is missing is the layer above them.

- **No single view.** Each kind has its own page, or none. `/health/activity`
  counts running turns, scheduled fires and pending approvals, for the guard
  only, and names nothing.
- **No shared rule for "stuck".** Three different detectors exist:
  - `kazma_ui/turn_liveness.py` pages `turn.stuck_open` when a turn has been
    open for 10 minutes with no task and no approval. Healing is off.
  - `kazma_core/agent/supervisor_watchdog.py` detects a turn that stops
    heartbeating, but only with the non-stop mode on.
  - `kazma_core/web_acquire/monitor.py` judges research and crawl stalls by
    meaningful progress (stage, counts, effects; 180 s by default), keeps a
    durable observation ledger, commits before delivering, and says when
    progress resumes. It is the model this plan generalizes.

  Swarm tasks, document jobs and the memory queue have deadlines, leases or
  retry budgets, but nothing tells the owner while they sit.
- **Notices are dead ends.** `observability/ops_alerts.alert` is one-way text,
  deduplicated by condition key, so two stuck runs of one kind become one
  message. The alert dispatcher's button field (`callback_id`, `button_text`)
  reaches the bus but nothing handles a press.
- **Buttons exist only for approvals.** The gate registry (`hitl_gates.db`,
  AGENTS.md §30) and the swarm bus's shared decision (`swarm/shared_approvals.py`,
  §7B) already give: one decision record per request, cards on Web, Telegram,
  Discord and Slack, the first human decision wins, every card shows the
  outcome, and timeout and cancellation are their own endings. Semantic
  clarify cards (§20B) already render per-option buttons on every platform.
  This is the hardest part of the interactive layer, and it is built.

## Design rules

1. **The registry is a read model.** Each kind's store stays the source of
   truth for its state. The registry projects it and never writes a kind's
   state itself, the same split as decision truth and execution truth in §30.
2. **Liveness is judged by progress.** A heartbeat proves the owner process
   is alive; progress proves the work moves. Four verdicts:
   - **dead:** the owner is gone (no task, lease lapsed, process gone);
   - **stalled:** the owner is alive and nothing meaningful changed within the
     kind's budget;
   - **slow:** progressing, past its expected duration, inside its deadline;
   - **failed:** the kind's own store says it ended in failure.
   A stall never cancels or fails a run by itself.
3. **Actions go through the kind's existing control, never a new path.** A
   Retry button calls `SwarmEngine.retry_task`, `DocumentIngestionService.retry_job`
   and so on, with the same checks as the page that already offers it.
4. **Effects decide what is offered.** A run whose effects are unknown never
   offers a plain Retry: it offers "Retry, I checked" as a second step, as the
   swarm's `acknowledge_unknown` does (SW-E08). A delivery recorded as unknown
   is never sent again (§16E). The tool effect journal's receipts (§30G) still
   hold on any replay.
5. **No fourth notifier** (§33). Text incidents go through `ops_alerts` and
   the bell; actionable cards go through the gate registry's card renderers.
6. **One message per run and condition.** Throttling is per run, not per
   condition key. A run that recovers says so once.
7. **Off the loop.** Every adapter read is a store read: adapters run in
   threads and are loop-stall helpers (§35).
8. **Single operator** (decision D1). Actions are admin-only; an install with
   platform users refuses them like every other Scheduled and Swarm surface.
9. **Both languages.** Every word on a card, the view and the slash command
   comes from the catalog in English and Arabic; run titles are content
   (`translate="no"`).

## The run contract

Each kind supplies an adapter that returns, for each of its runs:

| Field | Meaning |
|---|---|
| `run_id` | `<kind>:<the kind's own id>` |
| `kind` | One of the kinds below |
| `title` | Short, from the run's own data (content, fenced when shown to a model) |
| `origin` | The chat that started it: platform and delivery target captured at start, or none |
| `state` | `queued`, `running`, `waiting_human`, `stalled`, `slow`, `failed`, `done`, `cancelled`, `unknown` |
| `native_state` | The kind's own status word, unchanged |
| `progress_at` | When the run last made meaningful progress |
| `progress_note` | What that progress was ("page 14 of 54", "step 3 of 5") |
| `owner_alive` | Whether the owning task, lease or process is alive |
| `deadline_at` | The kind's own deadline, if any |
| `effects` | `none`, `known`, `unknown` |
| `version` | The store's version or update time, for compare-and-set actions |
| `actions` | The actions allowed now, each with its control function |

The adapter also declares the kind's stall budget, its expected duration and
its incident classes for runbooks (Phase 4).

## Per-kind adapters

| Kind | Source of truth | Native states | Progress signal | Stuck detection today | Alerts today | Controls today | Effect safety | Origin chat known |
|---|---|---|---|---|---|---|---|---|
| Chat turn (web, Telegram, Discord, Slack) | `active_turns` registry, `reply_sink` open turns, the checkpoint | running, paused on approval, closed | Turn journal frames; supervisor heartbeats | `turn_liveness` (10 min, no task); turn time budget; supervisor watchdog only with non-stop | `turn.stuck_open`, `turn.timed_out` (text) | Stop / abort, steer (web and `/abort`) | Effect journal receipts; an unknown receipt stops replay | Yes: thread and `_gateway` routing |
| Swarm task | Task store (`kazma_swarm_tasks` / SQLite) and the engine's in-flight runs | pending, running, paused, completed, failed, timeout, cancelled; `execution_outcome` | Task events, the pipeline step journal, in-flight count | Engine deadline and watchdog; Temporal heartbeat on the durable path | Counted failures (`kazma_swarm_failures_total`), no page per task | Swarm page: cancel, retry with `acknowledge_unknown`, checkpoint approve / reject | `execution_outcome` and `retry_safe` on the result | Partly: the dispatching chat or the swarm output target |
| Scheduled job | `cron.db`: jobs, deliveries, alert outbox | pending, running, delivering, done, failed, cancelled, undeliverable, missed, delivery_unknown | Fire lease and phase; delivery attempts | Lease expiry and boot recovery; poll heartbeat in readiness | `cron.job_failure`, `cron.job_failed`, `cron.delivery_failed`, `cron.delivery_unknown` (alert outbox) | Scheduled page: edit, cancel, restore (server-listed `actions`) | `delivery_unknown` is never re-sent | Yes: delivery target captured at booking |
| Document job | `document_jobs` and job events (SQLite or Postgres) | received through ready, retry_wait, rejected, cancelled, dead_letter | Job events; OCR page progress (`documents/ocr_progress.py`) | Lease recovery; retry budget, then dead letter | None per job; readiness counts stranded intakes | Documents page and API: cancel, retry (write ACL) | Outages retried, content rejected for good | No: actor only; add origin at intake |
| Research session | `research_sessions.db` | pending, running, cancelling, cancelled, done, partial, error, interrupted | Stage, counts and effects (the monitor's rule) | Acquisition monitor: no progress for 180 s | Research outcomes to the bell and chat (text) | Research page: cancel, delete, archive, explicit resume | Uncertain stages settle uncertain; resume refuses uncertain required stages | Yes for chat-started sessions |
| Crawl and research pipeline | The crawl journal (`acquisition.crawl.<digest>`, kinds `kb`, `research_crawl`, `research_pipeline`) | running, completed, partial, failed, interrupted | Pages done, frontier size, stage effects | Monitor stall; owner lease of 300 s | KB crawl outcomes; agent-run crawls report to the agent only | Knowledge page resume; `crawl_site(resume_job_id=)` | Completed pages skipped, uncertain ones fetched again | KB: the library; agent runs: the turn |
| X publication operation | `x_operations` (publication store) | awaiting_approval, scheduled, deferred, sending, outcome_unknown, published, cancelled | State transitions | 5-minute hold; a failing dispatch is held for review | Success and failure notices to chat | X Studio and Scheduled: reschedule, cancel | `outcome_unknown` is never re-sent | Yes: booking chat |
| Memory work queue | `memory_ops.db` task queue | pending, processing, completed, failed (dead letter) | Attempts, `next_attempt_at` | Stuck `processing` reclaimed after 300 s; sweep health | `memory.dead_letter`, `memory.enqueue_failed` | Memory page queue: retry, clear | Per handler; deep pass is idempotent by version | No: background |
| Maintenance and backups | Queue tasks and cadence stamps | last run, last outcome | Last-run stamp | Cadence and the weekly firing ledger | `backup.*` and sweep alerts | None in the UI | Not applicable | No: background |

The last two kinds are read-only in the registry for the first version: they
are shown and they page, but they offer no buttons beyond Details until a
phase adds them deliberately.

## Phases

Every phase lands complete, with its gates green and their negative controls
failing on the old behaviour. Nothing is deployed mid-phase.

### Phase 0: contract and inventory

Deliverables:
- The run contract as a typed module in `kazma_core` (no behaviour).
- A declaration per kind: its adapter, or the reason it has none.

Gates:
- **Every running state is declared.** A class gate reads the product source
  for durable stores that hold a running status (status columns and enums
  whose values include a running word) and fails on one no adapter covers
  and no declaration names. Negative control: a planted store with a
  `running` status column.
- The contract's `state` set and each adapter's mapping cover every native
  state of the kind, read from the kind's own enum or SQL (no hand-kept list).
  Negative control: a mapping with one state removed.

### Phase 1: read-only registry and views

Deliverables:
- Adapters for every kind in the table, read off the loop.
- `GET /api/runs` (any signed-in user, single-operator rule) and a Runs
  section on the Dashboard, EN and AR.
- A read-tier agent tool (`list_runs`, `run_status`) with titles fenced, and
  `/runs` in the chat apps and the web chat, answered without the model like
  `/replay` (§12H).
- No new alerts in this phase.

Gates:
- **Adapter parity, per kind, on the real store:** seed each native state,
  read the registry, compare. Negative control: an adapter that maps one
  state wrong.
- The registry and API never write: every route runs inside
  `read_only_diagnostic`, and the GET route walk
  (`tests/test_get_routes_off_the_loop.py`) covers `/api/runs`.
- Adapter readers are in `_LOOP_STALL_HELPERS`; the static gate fails a call
  from async code.
- `/runs` is in the `/` menu, `/help` and the slash-command reference
  (`tests/test_slash_help_and_menu.py`).
- The tool is read tier (`test_every_registered_tool_has_a_tier`); being a
  reader, it needs no `TOOL_WRITES` entry.
- i18n: catalog keys in both languages, plural forms for counts, the Arabic
  page tour (`tests/e2e/test_pages_read_in_arabic.py`), arrival by the
  sidebar (`tests/e2e`, soft navigation).
- The view shows its empty state only after it loaded, and a failed read says
  it failed (`tests/test_empty_states_after_load.py`).

### Phase 2: supervisor and text incidents

Deliverables:
- One supervisor, a 30-second loop started from an existing boot list through
  `spawn_background` (never a loose task) and stopped at shutdown, applying
  each kind's budget to the registry.
- An incident store: one row per run and condition, compare-and-set states
  `open`, `acknowledged`, `resolved` (by itself), `closed` (by an action) and
  `expired`; carried in migration bundles; retention like the gate registry.
- Delivery through `ops_alerts` to the origin chat, falling back to the ops
  channels, and to the bell. Commit before delivery; a delivery interrupted
  after commit is uncertain and never replayed (the monitor's rule and the
  cron alert outbox, §16 E2).
- A "progress resumed" notice once when a stalled run moves again.
- `turn_liveness` and the monitor's stall detection become two adapters'
  budgets. Their alert keys stay until Phase 2 ships, then retire with a
  firing-ledger signature update (§27C).
- Metrics: `kazma_runs{kind,state}`, `kazma_run_incidents_total{kind,condition}`,
  time to acknowledge and to resolve. A daily digest line under "Needs
  attention".

Gates:
- On a fake clock, per kind: a run silent for its budget plus one tick opens
  exactly one incident; progress before the budget opens none; progress after
  it resolves the incident and sends one notice. Negative control: a
  heartbeat-only detector misses a run that is alive and stalled.
- A run waiting on a human (approval, `awaiting_approval`, a paused pipeline)
  never opens a stall incident. Negative control: the rule removed.
- Two stalled runs of one kind give two messages. Negative control: the
  condition-key throttle.
- A real child process killed after the incident commit and before delivery
  raises it once at the next start, never twice; killed after delivery, never
  again. Negative control: deliver before commit.
- A database outage is ridden out: the supervisor pages its own failure only
  after five minutes without a check, and says when it recovers (the
  2026-10-22 monitor rule). Negative control: page on first failure.
- No alert key the supervisor emits is missing from the firing ledger's
  signatures (`test_every_ledger_signature_matches_a_line_the_code_emits`).

### Phase 3: actionable cards

Deliverables:
- Incidents become decision requests with an option set taken from the run's
  `actions`. Recommendation: extend `swarm/shared_approvals.py` from
  approve/deny to an option set (it is already detached from chat turns, has
  the web deep link and first-decision-wins across platforms), and render
  options with the semantic card renderers. The gate row's mechanism is
  `run_incident`.
- Options: Retry, Resume, Cancel, Details, Snooze (owner decision), Ask
  Kazma to diagnose (Phase 4). "Retry, I checked" replaces Retry when
  effects are unknown.
- A press is checked against the run's `version` and current `actions`
  before anything runs; a run that changed answers "already handled" with its
  new state. The action runs through the kind's control function under the
  same admin rule as its page.
- The web view's row and the card answer the same request (deep link
  `/runs?incident=<id>`).

Gates:
- **Every option reaches the control the kind's own page uses**, enumerated
  from the adapters. Negative control: an option wired to a function no page
  calls.
- First decision wins: a press on Telegram, then on the web, runs one action;
  every card shows the outcome. Negative control: per-card decisions.
- A double press runs the action once (compare-and-set on the incident and
  the run version). Negative control: no version check.
- A run that recovered or finished before the press: nothing runs, the card
  says so.
- Unknown effects: one press never retries. Negative control: plain Retry
  offered.
- **Incident gates never hold a chat turn open:** `close_turn`, the turn
  liveness readers and `/api/pending-approvals` ignore mechanism
  `run_incident`. Negative control: the filter removed keeps a finished turn
  open. This is the main collision risk with §30 and §31.
- A timeout or cancellation of the card is its own outcome and does nothing
  to the run.
- Browser, EN and AR, arriving by the sidebar: the card's action on the web
  view updates the row; a Telegram card is decided from the web.
- Button labels match their actions (`tests/test_button_label_matches_action.py`).

### Phase 4: runbooks and diagnosis

Deliverables:
- A per-kind runbook table: for each incident class, at most one bounded
  automatic step before the card. Examples: a transient model error retries
  once; a dead owner on this host resumes from its checkpoint or journal. Off
  by default per class until the owner turns it on.
- "Ask Kazma to diagnose" starts a turn in the origin chat with the incident
  summary fenced as data and read-tier tools only (run status, the run's
  events, the log tail for the run's id). It proposes; any action still goes
  through the card.

Gates:
- No automatic step runs when effects are `unknown` or `retry_safe` is false,
  for every kind and class. Negative control: a planted runbook entry that
  retries an unknown run.
- Automatic attempts are bounded per run and recorded on the incident.
- The diagnose turn is offered exactly the read tier (like a scheduled task's
  declared tools, §16 A2). Negative control: the whole registry.
- The diagnose turn passes the turn-note rules (§43): the incident note is a
  registered kind.

### Phase 5: qualification

Deliverables and gates:
- A crash matrix per kind: a real child killed at each stage of a run and of
  an action; the result is exactly one incident and at most one action, or an
  honest unknown state with its alert.
- A soak harness (`scripts/run_supervision_soak.py`) on private stores and a
  fake clock: thousands of runs across kinds, injected stalls, outages,
  restarts and button presses; it checks each incident against ground truth.
  Held by a harness test with planted misses as negative controls.
- Measured time to detect and false-page rate, recorded in this plan.

## Open decisions (owner)

1. Default stall budgets per kind. Proposal: chat turn 10 min, swarm task 5
   min without an event, research and crawl 3 min (today's), document job 10
   min, memory queue two failed attempts.
2. Whether Snooze exists, and for how long.
3. Which runbook steps start on.
4. Where a background run's incident goes: the ops channels (proposal), or a
   chosen chat.

## Risks

- **Turn lifecycle collision.** Incident decision rows live beside chat
  approvals; one leaking into `close_turn` or the pending-approvals page would
  keep turns open or show ghost cards. Phase 3's filter gate guards it.
- **Noise.** A detector that pages on ordinary slowness teaches the owner to
  ignore it. Budgets are per kind, slow is shown and never paged, and the false
  page rate is measured in Phase 5.
- **Drift.** Adapters can fall behind their kinds. Phase 0's declaration gate
  and Phase 1's parity gate fail when a kind gains a state.

## Not covered

- Multi-user installs: refused (D1).
- Runs of another install sharing the database.
- MCP servers' own work outside a tool call.
