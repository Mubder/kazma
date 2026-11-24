<!-- AGENTS.md, piece 14-swarm-autoscaler. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 14. Swarm Autoscaler (`kazma-core/kazma_core/swarm/autoscaler.py`)

Dynamic worker creation: when a task with `workers=["auto"]` has no matching
registered worker, the autoscaler spawns one from `swarm_templates.json` so the
swarm works with zero pre-registered workers.

- **`engine.get_autoscaler()` is a lazy singleton.** It loads
  `swarm_templates.json` once on first access. The dispatch fallback that calls
  `maybe_scale()` is in `dispatch_inner.py` — it only fires on
  `NoCapableWorkersError`, never when a named worker is requested or when routing
  succeeds. Do not call `maybe_scale` from elsewhere.
- **`swarm_templates.json` ships production templates** (coder/researcher/generalist)
  with `model`/`provider` left EMPTY so best-model selection (`models/selection.py`)
  fires at dispatch. Do not assume `swarm_registry.json` is the only worker source.
- **The worker registry is a data-dir store** (`registry.default_registry_path()`:
  `<data dir>/swarm_registry.json`, 2026-10-01). It was
  `Path("swarm_registry.json").resolve()` at import -- the process's working
  directory: no backup copied it, and a test run inside an install wrote the
  install's. An older build's file there (or at the install root) is moved in
  once and renamed `.migrated` -- never into a temporary data dir (a test's
  subprocess has no conftest pin, and its install root is the checkout the
  tests run in); writes are atomic; every change and every read of the
  entries holds the registry's lock (route threads write while the swarm
  reads on the loop). `tests/test_worker_registry_store.py`. A migration
  bundle carries it, and the operator's templates, even with `--no-assets`
  (§18, `store_registry.DATA_DIR_ENTRIES`).
- **"Finished" is one list** (`swarm.task.TERMINAL_STATUSES` / `is_terminal`,
  2026-10-02): the engine (finalize, approve, reject), the task store's
  retention and the task stream all ask it. The stream kept a copy without
  `cancelled`, so a cancelled task's stream never ended. **A finished task's
  stream ends with `task_completed`** whatever the bus remembers
  (`swarm_sse._final_event`, from the stored result): the Swarm page's
  client reconnects on every close, and the bus forgets a task at a restart
  and at cleanup -- a deploy left cards counting through ten reconnects.
  `tests/test_swarm_task_stream_ends.py`; what the page reads off a result
  must be a `TaskResult` field (`tests/test_swarm_page_result_fields.py`:
  the pipeline view and Play mode read `synthesis`/`response`, which no
  result has).
- **`matches_task` uses word-boundary token matching** (not raw substring). When
  adding expertise tags to a template, pick whole words — the tag `code` would
  not match "barcode" (intentional). The template using the most of its words
  wins (`WorkerTemplate.match_score`, each word once); file order (specialist →
  general) breaks a tie, then the catch-all (2026-11-15: first-match gave a
  research task mentioning Python to the coder; `tests/test_swarm_template_choice.py`,
  the yes/no rule as the control). A language's name is context for
  `classify_prompt` (`_CODING_CONTEXT`), never coding work alone.
- **Best-model-per-task** (`models/selection.py`): spawned workers classify their
  prompt (`models/router.py::classify_prompt`) and pick the best available model
  (user `models.defaults.<kind>` → heuristic → active). The selection never
  mutates the active profile. Env-lock (`KAZMA_MODEL`) always wins.
- **Handoff cycle guards (§4) still apply** to auto-spawned instances — they are
  regular `InProcessWorker`s once spawned, counted as their template, and a
  handoff may name a template (an instance is spawned for it). Idle-reap after 5 min;
  `record_activity` refreshes the timer, and an instance with a dispatch in
  flight is never reaped (`SwarmWorker.in_flight`, SW-E12). Instances are named
  `<template>-pool-<boot tag>-<n>` (a new tag each start, so an earlier run's
  shared breaker is never inherited); a reaped instance's shared breaker keys
  are deleted.
- **The Swarm page offers "auto"** in both worker pickers, chosen by default
  when nothing is registered, and the status says "On demand" when templates
  exist and nothing runs (`/api/swarm/status` `templates`). Until 2026-09-28
  the page refused to dispatch without a registered worker and said "Stopped"
  over a swarm that answered through the API in 3 s.
  `tests/test_swarm_auto_worker.py`.
- **A template with `catch_all: true` takes any `auto` task no other template
  matches** (the shipped `generalist`). Matching is by the prompt's words:
  "In one sentence: what is idempotency?" matched nothing and failed with "No
  capable workers" (2026-09-28). The shipped file is found from the install
  containing the package (`_default_templates_path`), never the CWD; the
  operator's edits are saved to `<data dir>/swarm_templates.json`, which then
  replaces the shipped list -- writing them into the tracked file left a
  modified checkout that `git pull` refuses.
- **An idle instance takes the next task before a new one is spawned**
  (`AutoScaler._take`, for `maybe_scale` and `spawn_by_name`, 2026-10-15): no
  dispatch in flight and no task already handed to it. A handed-out instance
  is claimed (`_claims`) until its dispatch begins (`record_activity`; its
  in-flight count covers it from then) or its breaker refuses it, else for
  `_CLAIM_S` (60 s); a claimed instance is never reaped. The router matches instances by
  their template's words, so before this a task the catch-all took never
  reached an existing catch-all instance: each spawned a new one up to the
  bound, then failed "No capable workers" until the idle ones were reaped (the
  SQ01 soak found it). Gate: `tests/test_swarm_autoscaler_reuse.py` (never
  reuse as the negative control). An open breaker is not dodged by spawning:
  its instance is reused and refuses, as the breaker means (a lingering claim
  as the control).

