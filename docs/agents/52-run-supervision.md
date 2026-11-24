<!-- AGENTS.md, piece 52-run-supervision. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 52. Run supervision: every running task watched, incidents you act on (`kazma_core/runs/`, 2026-10-25)

The owner asked for Kazma to be interactive about work that gets stuck or
fails, whatever it is. Before this each kind watched itself its own way (the
turn-liveness watchdog, the supervisor heartbeat with non-stop on, the
acquisition monitor) or not at all, and every notice was text with nothing to
press. Plan and evidence: `docs/plans/RUN_SUPERVISION_PLAN.md`; guide:
`docs/docs/guide/running-work.md`.

- **One read model, the stores stay the truth.** `kinds.KINDS` declares each
  kind (chat turns, swarm tasks, scheduled jobs, document jobs, research
  sessions, crawls and pipelines, X posts, memory tasks, maintenance sweeps):
  its adapter, its store, its budgets, what already announces which
  condition. An adapter reads its kind's store off the loop and NEVER writes
  it; `registry.list_runs` returns `contract.Run` records with the verdict. A
  kind whose store cannot be read is in `errors`, never "nothing running".
  Gate: `tests/test_run_kinds_declared.py` -- every product table with a
  status or state column is a kind's (`KindSpec.tables`) or in `NOT_RUNS`
  with why, and each adapter maps every native status, read from the kind's
  own enum or the status words its module writes. A new store holding a
  "running" status needs a kind or a reason.
- **Judged by progress** (`contract.judge`, pure): dead (owner gone past
  `dead_after_s`), stalled (alive, no meaningful progress past
  `stall_after_s`), slow, failed. Waiting on a human is never stalled. A
  verdict never cancels or fails a run. Progress: a chat turn's graph
  heartbeat, a swarm task's engine events (`SseBridge.emit` stamps
  `runs.progress`), a document job's `heartbeat_at`, the acquisition
  monitor's `since`.
- **Incidents** (`incidents.py`, `run_incidents.db`, a bundle store): one row
  per run and condition while live, every change a compare-and-set in
  `BEGIN IMMEDIATE` on a short-lived connection. A decided incident (closed,
  dismissed, expired) never opens again for the same run, condition and run
  version. Delivery is an outbox on the row: `pending` committed with the
  incident, `sending` just before the message leaves; a process killed at
  `pending` sends at the next start (once), at `sending` is `uncertain` and
  never resent; an `acting` row at start is closed `interrupted` and never
  replayed (`recover`). Incidents are NOT gate rows: a row in
  `hitl_gates.db` would hold a chat turn open (§30-31); a gate in
  `tests/test_run_decisions.py` keeps the runs code off the gate registry.
- **Telling** (`notify.py`): to the run's chat (its delivery target, captured
  at start: swarm dispatches record `source_delivery_target`, a chat turn's is
  its checkpoint's `_gateway` block) with `send_message(..., run_card=)`,
  which the gateway's send backend renders as buttons
  (`kazma_gateway/run_cards.py`); else `ops_alerts.alert` keyed per incident
  (never per condition: two stuck runs are two messages); always the bell
  (`/api/alerts/recent`). A condition another mechanism announces
  (`KindSpec.announced_by`: cron's alert outbox, the acquisition monitor, X
  notices, `memory.dead_letter`; a crawl an agent ran) is recorded with
  `notify = suppressed`. A failure that ended before this process started
  watching is quiet. One recovery note when a stalled run moves again. The
  turn-liveness watchdog no longer pages `turn.stuck_open`: the chat-turn
  adapter reports the abandoned turn and its card offers Close. A failed
  crawl's or research pipeline's reason is `crawl_jobs.failure_reason`: what
  the job recorded at its end (`_CrawlJob.finish(failure=)`, the pipeline's
  "found no URLs"), else the interruption, the stage, the page counts, else
  nothing; it gave "0 failed, 0 uncertain" for a search that found nothing
  (2026-11-05, `tests/test_failed_run_says_why.py`).
- **Deciding** (`decide.py`): claim (the first decision wins; a second is
  told who decided what, where), then `registry.perform` asks the run as it
  is NOW and refuses an action it no longer offers, then the kind's own
  control -- the same function its page uses (`swarm_panel/task_actions.py`,
  `turn_abort.abort_turn`, `task_queue.retry_failed_task`, the Scheduled,
  Documents, Research and X controls). A run whose effects are unknown offers
  only `retry_checked`. A button press is `/runs act <incident> <action>`
  from the presser (`platform_callbacks`, kind `run`), admin-only
  (`changes_global_config`, and each platform's press handler gates `run` with
  `hitl` and `swarm`). Web: `POST /api/runs/incidents/{id}/act` (admin, same
  origin). Gate: `tests/test_run_decisions.py` (every action of every kind
  reaches the page's control; an adapter wired elsewhere as the control).
- **Runbooks** (`runbooks.py`, setting `runs.runbooks`, all off): at most one
  automatic first step per lineage (`Run.lineage`: a retried task names its
  original) a day, never on unknown effects or through `retry_checked`.
  **Diagnose** (`diagnose.py`): a read-tier one-shot graph
  (`cron_fire_graph(agent, capabilities="read")`), the incident fenced, one at
  a time per incident; it decides nothing. Gate: `tests/test_run_runbooks.py`.
- **The supervisor** (`supervisor.py`, every 30 s from app startup, not under
  pytest) pages its own outage only after five minutes without a pass, and
  its recovery once. Surfaces: the Dashboard's Running work card
  (`static/js/runs_panel.js`, `/api/runs`), `/runs` in every chat (web:
  `sse_chat/_runs.py`, chat apps: `agent_handler/runs_command.py`), the
  agent's read tools `list_runs` / `run_status` (fenced). Gates: the files
  above plus `tests/test_run_adapters.py` (each kind's real store),
  `tests/test_run_supervisor.py` (fake clock), `tests/test_run_incident_crash.py`
  (a real child killed at each stage; sending before committing as the
  control), `tests/test_run_supervision_soak_harness.py`
  (`scripts/run_supervision_soak.py`, planted bugs as controls),
  `tests/e2e/test_runs_panel.py` (EN/AR, by the sidebar).

