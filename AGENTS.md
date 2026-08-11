# Mission Guidance: Kazma Agent Framework

## Project Overview

Kazma is a multi-platform AI agent framework with a LangGraph supervisor brain,
swarm orchestration, cross-platform dispatch (Telegram/Discord/Slack/Web/TUI),
and an OpenAI-compatible LLM provider layer. See `docs/docs/guide/architecture.md`
(Docusaurus docs under `docs/docs/`) for the full system architecture and
`CHANGELOG.md` for recent work. Latest production audit:
`docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md`. System map:
`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`.

## Package Scope

All packages are in scope. The four main packages:

| Package | Path | Purpose |
|---------|------|---------|
| `kazma-core` | `kazma-core/kazma_core/` | Agent runner, LLM provider, swarm engine, model registry, config store, IDE service |
| `kazma-gateway` | `kazma-gateway/kazma_gateway/` | Platform adapters (Telegram/Discord/Slack), agent handler, slash commands, `/ide` commands |
| `kazma-ui` | `kazma-ui/kazma_ui/` | FastAPI web app, IDE page, swarm panel, settings, SSE chat, static JS/CSS |
| `kazma-tui` | `kazma-tui/kazma_tui/` | Textual-based TUI dashboard + IDE editor screen |

## Critical Subsystems (READ BEFORE MODIFYING)

### 1. Provider/Model Resolution (`kazma-core/kazma_core/model_registry.py`)
- `get_client(model)` auto-corrects provider/model mismatches at runtime
- `set_active_model()` switches BOTH model AND provider via `find_provider_for_model()`
- Never change one without the other or the LLM call goes to the wrong API endpoint
- **Provider dispatch has FOUR branches** in `get_client()` / `get_model()` /
  `get_client_by_provider()`: `google`→`GeminiProvider`, `anthropic`→
  `AnthropicProvider`, `azure`→`AzureProvider`, `bedrock`→`BedrockProvider`,
  else the generic `LLMProvider`. The generic `LLMProvider` always sends
  `Authorization: Bearer` to `/chat/completions` — it CANNOT reach
  Anthropic-native (`/messages`), Azure (`api-key` header + `api-version`),
  or Bedrock (SigV4). Adding a non-Bearer provider means a new class +
  a branch in all three sites (mirror the Google case), not just a preset.

### 2. Platform Isolation (`kazma-gateway/kazma_gateway/agent_handler.py`)
- The LangGraph state NEVER contains `chat_id`, `user_id`, or `message_id`
- These live in `SessionStore` and are restored via `_build_target_id()` on reply
- Breaking this leaks platform IDs into the graph and corrupts sessions

### 3. LLM Tool Fallback (`kazma-core/kazma_core/llm_provider.py`)
- Some providers (NVIDIA NIM) reject tool definitions with 404 "Function not found"
- The code retries without tools automatically when this is detected
- Never remove the `status_code == 404 and "function" in detail.lower()` branch

**LLM error classification — `transient` flag (do not flatten):**
- `LLMError(..., transient=True/False)` classifies every failure raised by
  `LLMProvider.chat()`. Transient = network (Connect/Timeout/**ReadError**/
  RemoteProtocol) + 429; permanent = 4xx content/schema + UnicodeEncode.
- The supervisor retry loop (`graph_builder.py:_call_llm_with_retry`) ONLY
  retries `LLMError` when `transient` is True — permanent errors fail fast.
  `httpx.ReadError` (mid-stream drops) MUST stay transient, or
  "stopped-thinking" forced-finalization returns.
- `friendly_llm_error()` prefixes all messages with `⚠️` and gives an
  actionable hint — never change it to return a bare string, or failures
  get mistaken for normal model replies again.

**Turn-failure surfacing — never synthesize over a broken turn:**
- When the supervisor's LLM call fails after retries, it sets
  `SupervisorState.turn_failed=True` + `error_message` (NOT a fake answer).
- `respond_node` checks `state.get("turn_failed")` and MUST skip its
  synthesis LLM call when True — synthesizing a plausible answer over a
  failed turn was the root cause of the "model stopped thinking" symptom.
- Keep the `turn_failed` guard in `respond_node` and the `transient` flag
  on `LLMError`; removing either reintroduces silent forced-finalization.

**Vision capability routing (`kazma-core/kazma_core/vision_capability.py`):**
- `is_text_only(model)` / `is_vision_capable(model)` classify by allow/deny
  lists (deny wins; unknown models are NOT downgraded — fail-open).
- `analyze_image` (`tools/vision_analyze.py`) uses the active model if
  vision-capable, else auto-selects a configured vision model via
  `get_vision_client(registry)` (one-off client, no active-profile change),
  else returns a clear actionable error BEFORE any API call.
- `build_user_content()` (`gateway/agent_handler/attachments.py`) takes
  `vision_capable` and, for text-only models, downgrades chat images to the
  `[Attached: … — use file_read to open: …]` stub instead of `image_url`
  (text-only providers like DeepSeek reject `image_url` with a 400).

### 4. Swarm Handoff Cycle Detection (`kazma-core/kazma_core/swarm/engine.py`)
- `_handle_handoff()` accepts `_visited: dict[str, int]` and `_depth: int`
- These thread through `_dispatch_worker_by_name_all` -> `_dispatch_worker` -> `_handle_handoff`
- Max depth is 5; removing the guard causes infinite recursion on A->B->A cycles
- Workers can be revisited up to `_MAX_VISITS=2` times (allows legitimate A->B->A return handoffs)
- Visit counts are now tracked per-worker (not just a boolean set)

### 5. Circuit Breaker Half-Open (`kazma-core/kazma_core/swarm/reliability.py`) + ReliabilityRegistry (`reliability_registry.py`)
- `_probe_in_flight` flag ensures only ONE dispatch passes through half-open state
- Both `record_success()` and `record_failure()` reset it
- Never remove this flag or concurrent calls bypass the probe semantics
- ReliabilityRegistry (P2-1 refactor) owns all breaker/retry/timeout/validator state

### 6. TaskStore WAL Mode (`kazma-core/kazma_core/swarm/task_store.py`)
- SQLite uses WAL + `busy_timeout=5000` for concurrent read/write
- Schema auto-migrates on init (ALTER TABLE for new columns on existing DBs)
- Worker filter uses `json_each()` not `LIKE` for exact matching

### 7. HITL Approval Gates (3 mechanisms — all must stay wired)
There are **three independent** HITL mechanisms. Breaking any one creates an
unattended-danger-tool security gap:

**A. Graph interrupt() — single-agent chat (Web SSE/WS + Telegram/Discord/Slack)**
- `graph_builder.py:tool_worker_node` calls LangGraph `interrupt()` for danger tools
- Gate is active ONLY when `hitl_config` is passed to `build_supervisor_graph()`
- Required build sites: `agent_runner.get_streaming_graph()`, `agent_runner._ensure_graph`,
  and `app.py` startup recompile into `_graph_holder`. Omitting HITL on any site =
  dormant gate on that path.
- Resume: `graph.ainvoke(Command(resume=…), config)` via `POST /api/approve/{thread_id}`
  (SSE), WS `approve_tool`, or gateway `/hitl approve|deny {thread_id}`
- State persists in the checkpointer — paused turns survive restarts
- Double-gating prevention: graph sets ContextVars (`_graph_hitl_gate_ctx` /
  `_hitl_approved_ctx`) so `LocalToolRegistry.execute` does **not** re-prompt the bus

**B. Swarm bus — `/swarm` + IDE `LocalToolRegistry.execute` path**
- `tool_registry.py:execute()` calls `safety.check()` (async) for danger tools
- `check_sync()` is **fail-closed** (default): blocks danger tools when no real
  bus adapter is present. `allow_headless_danger=True` is the test/dev escape hatch
- Bus adapters: `TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`
- App wiring: **one** adapter if only one platform; **`FanOutBusAdapter`** when
  multiple are configured (first approval wins). NullBus = internal-only /
  fail-closed danger
- Approval buttons resolve via `handle_callback()` on each adapter

**C. Pipeline checkpoints — swarm PIPELINE tasks** (separate from A and B)
- `engine.py:_handle_pipeline_checkpoint` + `approve_checkpoint`

**Danger tool list SoT (must stay one list):**
- **Canonical:** `kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS`
- **YAML:** `kazma.yaml` `safety.hitl.require_approval_for` (parity-tested)
- **Settings UI / ConfigStore:** `safety.require_approval_for` — consumed by
  `get_hitl_config()` (runtime override)
- **Swarm bus:** `swarm/safety.py` `_EXTENDED_DANGER` is an **alias** of CANONICAL
  (not a longer list — spawn tools only if on CANONICAL)
- **MCP tools:** `classify_mcp_tool()` name patterns (write/exec/delete → danger).
  Gate is in `UnifiedToolExecutor.execute()`

**Diagnosis map (multi-path “X relates to Y”):** `docs/docs/ops/diagnosis-map.md`

### 8. ConfigStore Singleton + Atomicity (`kazma-core/kazma_core/config_store.py`)
- Uses WAL + `busy_timeout=5000` (like all other SQLite stores)
- Process-wide singleton: `get_config_store()` — all components MUST use this, not `ConfigStore()` directly
- Multi-key writes MUST use `batch_set()` or `transaction()` for atomicity
- Never construct `ConfigStore()` in gateway/core code — use `get_config_store()`

### 9. SwarmEngine Module Structure (P2-1 refactor — 3 extractions)

The original 1878-line `engine.py` god class was split into focused modules.
SwarmEngine remains the central orchestrator with thin delegates for backward
compatibility. **All public API methods and constructors are unchanged.**

| Module | Responsibility | When to open it |
|--------|---------------|-----------------|
| `engine.py` (1573 lines) | Dispatch, handoff, task lifecycle, worker registry | Always — the orchestrator |
| `reliability_registry.py` | Circuit breakers, retries, timeouts, validators, concurrency | Configuring per-worker reliability |
| `phonebook.py` | WorkerRegistry summon + dispatch_by_name | Topology/DAG worker lookup |
| `checkpoint_manager.py` | HITL pipeline checkpoint state, timeout auto-reject, persistence | Pipeline pause/resume logic |

**Rules after refactor:**
- New reliability features go in `reliability_registry.py`, not `engine.py`.
- `engine.py` public methods are thin delegates — the real logic lives in the extracted modules.
- The de-facto public attrs (`_workers`, `_active_tasks`, `_task_handles`, `_metrics_collector`) remain on `SwarmEngine`.
- Constructor signature is unchanged — test fixtures work without modification.

### 10. IDE Subsystem (`kazma-core/kazma_core/ide/`)

The IDE is a transport-agnostic coding backend (Web, TUI, all chat platforms).
It is the **single source of truth** for file/exec/git/swarm operations on a
workspace. Three new modules; understanding their interaction is essential.

**A. Workspace root resolution — ONE ladder (binding SoT)**
- Public API: `kazma_core.workspace.binding.resolve_active_root()` (also
  `file_write._get_workspace` / `configure_workspace` for compat).
- Used by: all `file_*` tools, IdeService, workspace UI API, env_context.
- **Resolution precedence:** per-task `workspace_scope` ContextVar →
  **active WorkspaceStore row** → `configure_workspace()` pin →
  `KAZMA_WORKSPACE` env → `{data_dir}/workspace` default sandbox.
- **Binding bus:** `WorkspaceStore.set_active_workspace` →
  `notify_root_changed(root)` → pin tools + MCP rebind for
  `workspace_bound` servers (`${KAZMA_ACTIVE_WORKSPACE}` in command).
- MCP filesystem must NOT stay on a static `kazma-data/workspace` fossil
  after Switch Repo / clone.
- Path-traversal protection: `IdeService.resolve()` does a string-level
  `normpath` `..` check + containment backstop (symlink/junction-aware).

**B. HITL routing — no parallel write/exec path**
- All mutating/exec IDE operations (`write_file`, `delete_file`, `run`,
  `run_file`, `git`) delegate to `LocalToolRegistry.execute()` via
  `IdeService._call_tool()`. The HITL gate lives in `tool_registry.py:execute()`
  (§7B). Never call the underlying tool functions directly from the IDE layer.

**C. Awareness injection — `ide/env_context.py`**
- `build_env_context()` resolves workspace root, repo slug (from WorkspaceStore
  cache or `git remote`), branch, GitHub auth, and available tools into a
  markdown block.
- Injected at THREE sites: main agent init (`agent_runner.py` + `graph_builder.py`),
  per-turn in the SSE chat path (`sse_chat.py`, so workspace switches take
  effect immediately), and into every dispatched worker prompt (`worker.py`).
- `IdeService.send_to_swarm()` attaches the env block to the task `context` —
  never drop this or workers lose workspace awareness.

**D. Per-task workspace targeting — `ide/workspace_scope.py`**
- `workspace_scope(workspace_id)` is an async context manager backed by a
  `ContextVar`. `worker_dispatch.py` wraps `worker.dispatch()` in it when a
  `SwarmTask` carries `workspace_id`.
- `_get_workspace()` consults the scope FIRST, so concurrent tasks can target
  different repos. `SwarmTask.workspace_id` (None = global active workspace)
  propagates through `SwarmDispatchContext.metadata`.
- `ContextVar` propagates across `await` points within one asyncio task;
  `asyncio.create_task` copies the context (var travels with it).

**E. Repo identity — `WorkspaceStore` persistence**
- `stores/workspaces.py` has repo-identity columns (`repo_url`, `owner`,
  `repo`, `default_branch`, `is_github`) added via idempotent `ALTER TABLE`.
- `repo_for(root_path)` returns cached identity (avoids `git remote` per turn);
  `set_repo_identity()` persists it. `env_context` prefers the cache.
- Native GitHub tools (`git_github_manager/tools.py`) use the shared
  `GitHubClient` (OAuth→PAT→env token) via lazy import, with env-var fallback
  for headless mode. Don't revert to `os.getenv("GITHUB_TOKEN")`-only.

**F. Transports**
- Web: `/ide` page + `/api/ide/*` router (`ide_api.py`); file-aware AI chat
  reuses `/api/chat/stream` (no parallel path).
- TUI: `editor.py` `EditorScreen` (pushed from `files.py`).
- Chat: `/ide` slash commands in `commands.py:_try_ide_command`, wired in
  `graph.py` after the swarm intercept.

- Follow existing Kazma code style (type hints, docstrings, logging)
- Use `logger = logging.getLogger(__name__)` pattern
- Use `from __future__ import annotations` for type hints
- Keep modules focused (one concern per file)
- Python: compile-check with `py_compile` before committing
- JavaScript: syntax-check with `node --check` before committing
- Never use `&&` or `||` in PowerShell commands; use `;` and `$LASTEXITCODE`

### 11. Self-Improvement Soul Store + Prompt Fence (`kazma-core/kazma_core/skills/self_improvement.py`)

The self-improvement engine persists "Soul deltas" (LLM-generated system-prompt
refinements derived from untrusted conversation/tool output) and re-injects
them into every future system prompt. Two invariants must hold:

**A. Storage is ConfigStore-backed, NOT a free-standing JSON file.**
- The supervisor/main-agent Soul lives in `get_config_store()` under key
  `self_improvement.agent_evolution` (a dict `{"agents": {<id>: {soul, history}}}`).
- `_load_agent_evolution` / `_save_agent_evolution` go through ConfigStore —
  do NOT reintroduce a direct `path.write_text` write (the old
  `agent_evolution.json` was non-atomic and corruptible on crash/concurrency).
- A compound read-modify-write lock (`_agent_evo_lock`) serializes
  `apply_agent_mutation` within a process; ConfigStore's own lock only guards
  individual get/set, not the multi-step sequence. Both are required.
- A one-time migration (`_migrate_legacy_evolution_if_present`) moves any
  pre-existing `agent_evolution.json` into ConfigStore and renames it
  `.migrated`. Leave this in place.
- Swarm *worker* deltas live on `WorkerRegistry` (`WorkerEntry.system_prompt`),
  not here.

**B. Every injected Soul delta MUST go through the prompt fence.**
- `kazma_core/safety/prompt_fence.py` provides `is_override_delta()` (rejects
  injection markers like "ignore prior instructions") and
  `format_untrusted_block()` (wraps content in a `<kazma:data untrusted>`
  fence telling the model the text is observation data, NOT instructions).
- Deltas are checked at creation time (`_analyze_success`/`_analyze_failure`)
  AND at apply time (`_auto_apply`/`apply_agent_mutation`) — defense-in-depth.
  Never inject a delta via the old `"Apply these refinements to your behaviour:"`
  framing; always use `format_untrusted_block(evo, source="self_improvement")`.
- The 3 supervisor injection sites (`agent_runner.py`, `sse_chat.py`, gateway
  `graph.py`) all use the fence. Keep them in sync if you add a 4th.
- Kill-switch `KAZMA_SELF_IMPROVEMENT=0` is checked live (not just at init) on
  both the chat/supervisor path and the swarm worker path.

### 12. Time Travel Replay (`kazma-core/kazma_core/time_travel.py`)

The time-travel subsystem captures a snapshot of the full `SupervisorState`
after every supervisor iteration and persists it to
`kazma-data/snapshots.db` (SQLite WAL, LRU-capped per-thread at 50).

**A. The recorder must be wired into ALL graph-build sites.**
- `SnapshotRecorder` is created once per agent (`agent_runner.py` via
  `create_recorder(config=...)`) and passed to `build_supervisor_graph(...,
  snapshot_recorder=...)` at all 3 call sites: the run graph
  (`_ensure_graph`), the streaming graph (`_ensure_streaming_graph`), and
  child graphs (`build_child_graph`). The app.py post-startup recompile also
  passes it. If any site omits the recorder, that path stops capturing
  snapshots silently.

**B. Capture hook lives in the supervisor node.**
- `graph_builder.py:_supervisor` calls `snapshot_recorder.capture(merged)`
  after each iteration and stamps `snapshot_id`/`snapshot_iteration` into
  the result state. This is conditional on `snapshot_recorder is not None`.
  The SSE path reads `snapshot_id` from the terminal graph state to emit a
  `snapshot` SSE event (Part 6).

**C. Restore vs Fork — in-place vs branch.**
- `/replay <n>` (`_handle_replay` in `graph.py`): loads a snapshot via
  `ReplayEngine.replay_from(thread_id, iteration)` and writes it back to the
  SAME thread via `graph.aupdate_state(config, state)` — rewinding the live
  conversation. Same pattern as `/undo`.
- `/fork <n>` (`_handle_fork` in `graph.py`): loads the same snapshot but
  writes it under a NEW `thread_id` (mints `gw-{platform}-{sender}-{uuid}`).
  Also copies session context + creates a Web UI session. The original
  thread is NOT modified. Do NOT overwrite `active_thread.{sender}` — the
  user stays on the original; the fork appears in the Web UI sidebar.

**D. The slash-command resolver returns `None` for `/replay <n>` and `/fork`.**
- These fall through to the graph handler (like `/undo`/`/edit`) because they
  need `graph.aupdate_state`. The resolver only handles read-only subcommands
  (`list`, `compare`, `clear`) which don't need graph access.

### 13. Proxy Provider Addon (`kazma-core/kazma_core/proxy/`)

An opt-in, pluggable scraping proxy so `read_url` / `crawl_site` / `web_search`
route through a residential rotating proxy (anyip.io first). Bulletproofs
scraping against IP blocks/rate limits. **Disabled by default** — non-users see
zero change.

- **`get_proxy_provider()` (`registry.py`) re-reads `proxy.provider` LIVE on
  every fetch** (mirrors HITL's `get_hitl_config`). A Settings change takes
  effect without a restart. Default is `NullProvider` (direct, no proxy). It
  never raises — on any error it returns `NullProvider`, so scrapers stay working.
- **`get_scraping_client()` (`client.py`) is the single injection point.** The
  scraper builds its `httpx.AsyncClient` via this factory, not `httpx.AsyncClient`
  directly. It injects `proxy=` when configured + rotates UA from
  `USER_AGENT_POOL`. Adding a new fetch path = use this factory.
- **Scraping-scoped ONLY.** The proxy is never applied to LLM API calls — those
  use the separate `http_pool.py`. Never wire `get_scraping_client` into the LLM
  provider path, or provider API keys would route through a third party.
- **Adding a provider** (BrightData/Oxylabs) = one class under `proxy/` + one
  line in `registry.py::_PROVIDERS` + one Settings dropdown option. The scraper
  talks to the `ProxyProvider` interface, not to any provider directly. Don't
  hard-code a provider into `read_url.py`.
- **`proxy.password` auto-vault-encrypts** via the existing
  `is_sensitive_config_key` rule.

### 14. Swarm Autoscaler (`kazma-core/kazma_core/swarm/autoscaler.py`)

Dynamic worker creation: when a task with `workers=["auto"]` has no matching
registered worker, the autoscaler spawns one from `swarm_templates.json` so the
swarm works with zero pre-registered workers.

- **`engine.get_autoscaler()` (engine.py:163-187) is a lazy singleton.** It loads
  `swarm_templates.json` once on first access. The dispatch fallback that calls
  `maybe_scale()` is at `dispatch_inner.py:54-62` — it only fires on
  `NoCapableWorkersError`, never when a named worker is requested or when routing
  succeeds. Do not call `maybe_scale` from elsewhere.
- **`swarm_templates.json` ships production templates** (coder/researcher/generalist)
  with `model`/`provider` left EMPTY so best-model selection (`models/selection.py`)
  fires at dispatch. Do not assume `swarm_registry.json` is the only worker source.
- **`matches_task` uses word-boundary token matching** (not raw substring). When
  adding expertise tags to a template, pick whole words — the tag `code` would
  not match "barcode" (intentional). Templates are first-match-wins by file order
  (specialist → general).
- **Best-model-per-task** (`models/selection.py`): spawned workers classify their
  prompt (`models/router.py::classify_prompt`) and pick the best available model
  (user `models.defaults.<kind>` → heuristic → active). The selection never
  mutates the active profile. Env-lock (`KAZMA_MODEL`) always wins.
- **Handoff cycle guards (§4) still apply** to auto-spawned instances — they are
  regular `InProcessWorker`s once spawned. Idle-reap after 5 min;
  `record_activity` refreshes the timer.

### 15. V2 Memory Worker & Schedulers (`kazma-core/kazma_core/memory/worker_bootstrap.py`)

The V2 cognitive engine has a background maintenance tier: a durable task
queue drained by a worker, plus two fire-and-forget asyncio scheduler loops.
These were the subsystem that silently lost its backup/export runs (the
routines existed but nothing called them) — read this before touching the
background memory path.

**A. `start_memory_worker()` is the single boot entry — it starts BOTH
schedulers.** Called from `app.py:1283-1289` (wrapped in try/except so it
can't block boot). It registers handlers + calls `start_worker()` +
`_start_macro_sleep_scheduler()` + `_start_backup_export_scheduler()`. If a
new scheduler is added, register/start it HERE or it will never run (the
exact gap that previously left backups/export inert).

**B. Two scheduler loops, distinct cadences (do not collapse them):**
- **6h `macro_sleep` loop** (`_MACRO_SLEEP_INTERVAL_HOURS = 6`): enqueues a
  `macro_sleep` task every 6h → decay scoring, tier demotion/promotion,
  archival (`macro_sleep.py:run_macro_sleep`). First sweep 60s after boot.
- **24h backup/export loop** (`_BACKUP_EXPORT_INTERVAL_HOURS = 24`): enqueues
  `native_backup` + `nightly_export` tasks every 24h → native
  `sqlite3.backup()` of both memory DBs (`backup.py`) + JSONL/GraphML dumps
  (`export.py`). First sweep 120s after boot. Kept separate from the 6h
  loop so a slow disk on backup can't stall decay.
Both loops only `enqueue_task(...)`; the durable worker drains the actual
work, so a failed enqueue cannot kill the cadence and a failed handler is
retried/bounded by the queue.

**C. Handler registration is idempotent via separate module-level flags.**
`register_v2_handlers()` guards on `_registered` (macro_sleep /
entity_merge / micro_consolidation); `register_backup_export_handlers()`
guards on `_backup_export_registered` (native_backup / nightly_export). The
underlying `register_handler()` is a plain dict assignment (idempotent
overwrite), but the flags avoid re-churning on re-boot / repeated calls and
let the backup handlers register independently of the core V2 set.

**D. The durable queue lives in `memory_ops.db` (`task_queue.py`).** Bounded
retries: `max_attempts` (default 3) then dead-letter (`status='failed'`);
stuck `processing` rows past 300s are reclaimed. Per-task short-lived
SQLite connections (no WAL contention with chat reads on
`memory_state.db`). `enqueue_task` is best-effort and never raises.

**E. The split-DB design is load-bearing.** `memory_state.db` (hot reads:
beliefs, episodes, entities, procedural DAGs) is isolated from
`memory_ops.db` (cold writes: task queue, audit log) precisely so background
consolidation/backup writes don't WAL-contend with chat recall reads. Do
not merge them or route queue writes at the primary DB.

### 16. Cron Scheduler & Reminder Delivery (`kazma-core/kazma_core/cron/`)

The user-facing reminder cron (`schedule_task` native skill → `CronScheduler`
→ `kazma-data/cron.db`). Two invariants must hold:

**A. The scheduler MUST be constructed with a `graph_builder=`.**
`app.py` builds `CronScheduler(store=…, graph_builder=_cron_graph_builder,
poll_interval=…)`. Without `graph_builder=`, a job fires and `_execute()`
raises `RuntimeError("No graph builder configured")` — every reminder
silently crashes on fire. The closure returns the agent's one-shot
`build_child_graph()` (checkpointer=None), mirroring the sub-agent
graph-builder closure defined just above it.

**B. `delivery_target` is captured at schedule time, not resolved at fire time.**
`schedule_task` reads `get_current_delivery_target()` (the
`_current_delivery_target` ContextVar, bound at the gateway handler entry
AND re-set in the tool-worker node from the `_gateway` routing block —
same two-layer pattern as `_current_thread_id`) and stores it on the job.
At fire time `_deliver()` uses `job.delivery_target` as the `target_id`.
The fire-time SessionStore lookup is NOT a viable fallback — sessions
TTL-evict after 5 min (`_session_ttl_seconds=300`), so any reminder >5 min
out would miss. Legacy rows with empty `delivery_target` fall back to
`thread_id`, then `"{platform}:unknown"`. The platform-isolation invariant
(§2) is preserved — `chat_id` never enters graph state; `delivery_target`
joins `thread_id`/`platform` in the internal `_gateway` routing sub-dict.

**Multi-tenant memory flag:** `KAZMA_MEMORY_ENFORCE_TENANT=1` (off by default)
scopes `/memory` operator reads/writes by the request-scoped tenant. See
§8 ConfigStore + the env-var reference. Note: `entities.id` is a global PK,
not per-tenant.

### 17. Lifecycle Status Notifier (`kazma-core/kazma_core/lifecycle_notifier.py`)

Server lifecycle status notifications — pushes a status update to every
configured platform (Telegram/Discord/Slack) when the server starts,
restarts, shuts down, or fails to boot, so an operator can tell from chat
when something went wrong (hung boot, a crash emitting no shutdown message,
a bad bot token, etc.). Three invariants must hold:

**A. Notifications route through the SwarmMessageBus — no parallel path.**
`notify_lifecycle(event)` calls `get_message_bus().adapter.send(BusMessage(...))`.
The bus is wired during `KazmaAppBuilder.build()` (before the lifespan runs),
and `FanOutBusAdapter` already fans a single send out to every configured
platform concurrently, with each `*BusAdapter` holding its own destination
`chat_id`/`channel_id` (from `connectors.<platform>.swarm_chat_id`). Do NOT
construct new adapters or introduce new recipient config for this feature —
reuse the bus. `NullBusAdapter` (no platform configured, or under pytest via
`_skip_real_adapters`) silently drops the message; the feature self-disables.
The bus adapters are standalone `httpx` clients independent of
`gateway.start()`/`stop()`, so notifications work during early startup
(before the inbound poller is up) and late shutdown (after `gateway.stop()`,
which tears down inbound adapters, not the bus).

**B. `notify_lifecycle()` is the single entry point — called from 4 sites in `app.py`.**
- `_on_startup()` top (before MCP connect) → `starting`
- `_on_startup()` end (after the cron block) → `started`, with a `detail`
  of `Adapters: …` + `Model: <registry.active_model>`
- the gateway-start failure `except` (`[Gateway] Failed to start`) →
  `startup_failed`, with the gateway error as `detail` (highest-signal boot
  failure — bad token, network)
- `_on_shutdown()` top (before `signal_shutdown()`, before any teardown) →
  `shutting_down`
Each call site is wrapped in its own try/except (debug-level on failure) —
a notification must NEVER break boot or shutdown.

**C. Config is live-re-read; restart detection uses a ConfigStore marker.**
`get_lifecycle_config()` mirrors `get_hitl_config`/`get_proxy_provider`:
imports `get_config_store` locally inside a try, reads flat dotted keys
(`notifications.lifecycle.enabled` / `.events` /
`.restart_window_seconds`), falls back to YAML/env on any error, never
raises. Toggling via `PUT /api/settings/single` takes effect on the next
boot/shutdown. On `shutting_down`, the notifier stamps the internal key
`system.lifecycle.last_shutdown_epoch`; on `started`, if that epoch is
within `restart_window_seconds` (default 60; `0` disables detection) it
upgrades to "🔄 Restarted" instead of "🟢 Started". A hard crash leaves no
marker, so the next boot shows a plain "Started" — distinguishing
intentional restart from crash-recovery.

### 18. Migration System (`kazma-core/kazma_core/migration/`)

A portable-bundle system for moving a full Kazma installation across
machines/OSes (WSL→Windows, Linux→Mac, server→laptop) without the silent
breakage of a naive copy-paste. User surface is the `kazma migrate` CLI
(`export` / `verify` / `import`); the engine lives in
`kazma_core/migration/` so REST/UI can wrap it later.

**Three load-bearing invariants (the whole point of the tool):**

**A. `vault.db` + `KAZMA_VAULT_KEY` travel as an atomic pair.** The vault's
per-installation PBKDF2 salt lives *inside* `vault.db`, so the DB is
undecryptable without its matching key. The bundle carries both: the key in
`meta.env`, a non-reversible fingerprint in `manifest.json`. On import,
`check_vault_key()` (`migration/vault_pairing.py`) compares them: MATCH
proceeds, EMPTY writes the bundle's key, MISMATCH **aborts** unless
`--reset-vault-key` is passed (which backs up the target's existing vault.db
first, then overwrites the key). This is the #1 silent-breakage mode of a
copy-paste migration.

**B. Embedded absolute paths are rewritten to the target root.** A Linux
source (`/home/user/kazma`) has its workspace root baked into
`workspaces.root_path`, `snapshots.state_json` (full SupervisorState blobs),
`chat_sessions.messages`, memory entities/episodes, cron prompts. The
importer rewrites them all to the target path across OS separator
conventions (`migration/path_rewrite.py`). Two correctness properties: (1)
`PathMap` is ordered **longest-source-first** so `/home/u/kazma` doesn't
partially rewrite `/home/u/kazma-repos/ShipX`; (2) substitution is
byte-level substring on the column text (NOT a JSON parse — the state_json
blob is huge and paths appear anywhere), with both forward-slash and
backslash variants handled. No false positives (e.g. `barcode` ≠ `code`).
Workspace root rewrite also fires `notify_root_changed()` so MCP rebinds.

**C. Import is atomic — never corrupt the target mid-flight.** The importer
(`migration/importer.py:import_bundle`) stages to `kazma-data/.migrate-staging-<ts>/`,
verifies, path-rewrites the *staged* copies, backs up the live DBs to
`.migrate-backup-<ts>/`, then swaps staging → live one file at a time via
`rename`. Any exception before the swap leaves live data untouched; the
staging dir is preserved on failure. `verify` runs as a dry-run inside every
import and is available standalone.

**Scope — SQLite + Postgres (v2).** The bundle always carries the SQLite
files (vault, memory, snapshots, cron — these are SQLite even under a
Postgres backend). When the source is Postgres, it ALSO carries a
``data/postgres.dump`` produced by ``pg_dump -Fc`` containing the
shared-state tables (settings, chat sessions, checkpoints, swarm tasks).
The exporter reads via the backend-agnostic data-access layer for the
SQLite portion and shells out to ``pg_dump`` for the Postgres portion.

**Postgres dump/restore discovery — ``pg_bridge.py``.**
``resolve_pg_dump()`` / ``resolve_pg_restore()`` try, in order: (1) the
binary on PATH, (2) ``docker exec ${KAZMA_DB_CONTAINER:-kazma-db} <bin>``
(the common Docker-deployment default — the DB container has the client
tools even when the host doesn't), (3) raise ``PgToolNotFound`` with a
clear install hint. Override the container name with ``KAZMA_DB_CONTAINER``.

**Target-backend matching on import.** The importer checks the target
backend: if Postgres, it ``pg_restore`` the dump (``--clean --if-exists``
idempotent; schema self-recreates, target DB can be empty) into
``KAZMA_DATABASE_URL``, then proceeds with the SQLite-file restore +
path-rewrite for vault/memory/snapshots. If the target is SQLite but the
bundle has a Postgres dump, it **aborts with a clear error** rather than
silently importing partial data (the SQLite files alone lack chat history,
settings, checkpoints that live in Postgres). No SQLite↔Postgres content
translation is attempted — the bundle is source-backend-shaped.

**Bundle layout:** `manifest.json` (version, source OS/host, per-file sha256,
vault-key fingerprint, table counts, source workspace root), `meta.env`
(vault key + public url), `config.yaml` (ConfigStore.export_yaml — secrets
are `vault://` refs, not plaintext), `vault.db` (encrypted, under `data/`),
the 13 data SQLite files under `data/`, `data/postgres.dump` (only when the
source was Postgres — custom format, restored via pg_restore), `pathmap.json`,
and verbatim `assets/` (attachments/documents/exports/images/fonts — no
embedded paths).

**Key files:**
- `migration/bundle.py` — `Manifest`, `KazmaBundle`, `verify()`, `sha256_file`
- `migration/path_rewrite.py` — `PathMap`, `build_path_map`, `rewrite_paths_in_sqlite`
- `migration/vault_pairing.py` — `check_vault_key`, `sync_vault_key`
- `migration/exporter.py` — `export_bundle()`
- `migration/importer.py` — `import_bundle()`, `ImportReport`
- `kazma-cli/kazma_cli/migrate.py` — CLI dispatch (`kazma migrate export|verify|import`)
- `memory/backup.py:backup_one()` — the WAL-safe SQLite copier reused by the
  importer's pre-swap safety backup (promoted from `_backup_one`).
- Document store export/import is also wired (`documents.db` + content-addressed
  tree under the document-store root) — see §19 and `migration/exporter.py`.

### 19. Document Intelligence (`kazma-core/kazma_core/documents/`)

Secure durable document platform (phases 0–10). Docs SoT:
`docs/docs/guide/document-intelligence.md`, phases
`docs/docs/guide/document-phases.md`, security
`docs/docs/security/document-security.md`, ops
`docs/docs/ops/document-processing.md`.

**A. Two boundaries — do not invent a third path.**
- **Durable public boundary:** `DocumentIngestionService` — Web
  `/api/documents/*` (`documents_api.py`), native `document-platform` tools,
  gateway `/documents`/`/docs`, TUI `DocumentsPanel`. Tenant/actor ACL, capacity,
  audit, jobs, index.
- **Execution boundary:** `DocumentService` — sniff/parse/OCR/render inside
  isolated subprocesses. Durable workers call it; chat
  `agent_handler/attachments.py` may call it for **transient** fenced excerpts
  only (not a second durable store).
- Gateway/UI **must not** import `documents.parsers`, `documents.ocr`,
  `documents.renderers`, `parser_worker`, `mutation_worker`, etc.

**B. Job state machine (canonical).**
`received → quarantined → validating → ready_to_parse|ocr_required →
parsing|ocr_running → normalizing → indexing → verifying → ready`
(+ `retry_wait`, `rejected`, `cancelled`, `dead_letter`). Do not reintroduce
generic PENDING/ACCEPTED/PROCESSING labels in product code or docs.

**C. Config is ConfigStore-nested.** Primary keys are
`documents.intake.*`, `documents.limits.*`, `documents.ocr.*`,
`documents.workers.*`, `documents.capacity.*`, `documents.retention.*`,
`documents.gc.*`, `documents.security.*`, plus rollout
`documents.enabled` / `shadow` / `default_authoritative`. Prefer
`get_document_config()` live reads. Flat aliases exist only for a few intake
keys — do not invent `documents.max_pages` style flat keys without wiring
aliases.

**D. Multi-replica honesty.**
`jobs_pg.py` can claim jobs with `SELECT … FOR UPDATE SKIP LOCKED` when
Postgres is configured. Document **metadata** (documents/versions/blobs) is
still SQLite — readiness must report single-replica for metadata. Never claim
full multi-replica document HA until metadata is ported.

**E. Fence + security honesty.**
LLM-visible document text goes through untrusted fences
(`source="document"` or chat `document_attachment`). Auto-index is **off** by
default. Redaction UI confirm is Web-only; API/tools can redact under ACL.
**Malware:** `documents/malware.py` runs on quarantine via
`scan_if_configured` (`auto`/`on`/`off` + fail-closed). Uses system
`clamscan`/`clamdscan` only — no third-party upload of document bytes.
Sandbox: scrubbed env + resource limits; not a full network namespace.

**F. Multi-replica backends.**
Jobs: `jobs_pg.py` when Postgres. Metadata: `repository_pg.py` when
`KAZMA_DOCUMENTS_METADATA_BACKEND=postgres|auto` and pool is up. GC mark/sweep
SQL remains SQLite-shaped — collector **skips** with
`gc_postgres_metadata_sql_port_pending` when metadata is Postgres (no silent
deletes). Audit works on both backends.

**G. Certification.**
`scripts/certify_documents.py` + `tests/test_document_certification_phase10.py`
+ `hostile_corpus.py` / committed `tests/fixtures/documents/hostile_manifest.json`.
Keep CLI gates and pytest groups honest (architecture/a11y/crash matrix are
pytest; CLI has NOT RUN placeholders for soak/Postgres/external review).

### 20. Commitment Layer (`kazma-core/kazma_core/safety/commitment/`)

A policy gate between the LLM and durable mutations. Kazma resolves intent
against memory BEFORE acting — the CoPilot incident class (model invents a
date, schedules it, overwrites the user's real belief) is blocked at both the
schedule layer and the memory layer. Full plan + Phase-0 exit report:
`docs/plans/INTELLIGENT_AGENT_COMMITMENT_LAYER.md`,
`docs/plans/COMMITMENT_PHASE0_EXIT_REPORT.md`. Phases 0–2 + 5(partial) + 6 are
shipped; Phase 3 (combined-card UX), 4 (other-act resolvers), 5-swarm
(scope-token), 7 (soul) are pending.

**A. Three choke points — all mutator paths go through `authorize_effect`.**
- `agent/graph_builder.py:tool_worker_node` — the single-agent chat path; the
  gate runs BEFORE the security HITL split so it can rewrite args first.
- `agent/tool_registry.py:LocalToolRegistry.execute` — the IDE/swarm path
  (audit-only; full decisions are graph-side).
- `memory/belief_mutation.py:_mutate_functional` — the memory corruption half
  is gated here (source-trust), independent of the policy gate.

**B. The decision mapping (§3.4). `authorize_effect` returns one of:**
- `allow` (+ optional `rewritten_args`): for the remind act, the gate anchors
  the relative phrase to a memory event and **rewrites the tool args to the
  memory-correct fire_at** — whatever the model put in `timing`, the resolved
  ISO date wins. This is the schedule-path fix.
- `clarify` / `deny`: held with a clear error the model turns into a user
  question (Phase 3 will swap the error for a real card on the HITL bus).
- Audit-only: read tools, and acts without a resolver yet (memory corruption
  is gated at `mutate_belief`; `cancel_job`/`exec`/`fs`/`outbound` resolvers
  are Phase 4).

**C. Invariants — removing any reintroduces the incident class:**
- **Source-trust gate** (`_mutate_functional`): a `user_explicit` functional
  belief may NOT be superseded by a lower-trust (`llm_inferred`/`system_tool`)
  source. Kill-switch: `cfg.v2.functional_supersede_requires_user_assert`.
- **Rewrite-on-allow**: the gate's fire_at wins over the model's args — do not
  let the original (possibly wrong) `timing` reach the scheduler for remind.
- **Fail-open + kill-switch**: any layer error leaves `pending` unchanged — the
  gate is defense, not a hard dependency. `KAZMA_COMMITMENT_ENABLED=0` (or
  ConfigStore `agent.commitment.enabled=false`) disables the whole layer live.
- **Conservative auto-store** (`belief_extractor._apply_beliefs_to_v2`): low-
  confidence inferred beliefs are dropped post-turn (keeps the graph clean);
  `user_explicit` stores are never throttled. Mode: `memory.auto_store_beliefs`
  (off|conservative|aggressive, default conservative) / `KAZMA_AUTO_STORE_BELIEFS`.
- **No late approve**: `store.update_status` refuses to revive an expired
  commitment to committed/ready (§3.9 rule 2).
- **GC cadence**: `worker_bootstrap._start_commitment_gc_scheduler` runs
  `run_gc_cycle` every 15 min (sweep_expired + pending-cap + tiered retention).
  If a new scheduler is added, register it in `start_memory_worker` (§15B).

**D. Components (`kazma_core/safety/commitment/`).**
- `side_effects.py` — the single SoT registry: tool → `ToolEffectProfile`
  (effect + security_tier + semantic_tier + act). Parity-tested against
  `CANONICAL_DANGER_TOOLS`/`TOOL_TIERS`. Unregistered mutators fail-closed
  (tokenized: `widget` ≠ `get`). MCP tools (`mcp__*`) route through
  `classify_mcp_tool_effect`. New mutator tools MUST get a profile here or they
  classify fail-closed.
- `authorize.py` — `authorize_effect` (the policy gate) + `EffectDecision`.
- `relative_time.py` — `resolve_remind` (EN+AR relative-time parse, anchor to
  event vs `request_at`, ambiguity surfacing). G2-measured (0 false-allow).
- `store.py` — `Commitment` + ops-SQLite tables + TTL/GC (§3.9). On
  `memory_ops.db` (NOT a new file). `run_gc_cycle` is the scheduler entry.
- `constraints.py` — `is_commitment_enabled` (kill-switch) +
  `load_constraint_beliefs` (the §3.6 machine-readable constraint appendix).
- `config.py` — `get_commitment_config` (live reader: env → ConfigStore →
  defaults; the ONE config source for the layer).

**E. Modes** (`agent.commitment.mode`, env `KAZMA_COMMITMENT_MODE`):
`strict` (wider clarify window) | `balanced` (default) | `autonomous`
(allow-with-candidate on ambiguity) | `yolo` (semantic bypass, audit-only).
Modes modulate the ambiguous band only — a clean memory-anchored resolution is
allow+rewrite in every mode.

**Tests:** `tests/test_commitment_*.py` (corpus/G1/G2, store+GC, authorize,
tool_worker gate, scenarios, modes, config, side_effects) + the CoPilot golden.
Run: `python -m pytest tests/test_commitment_*.py tests/test_side_effects.py`.

## UI Conventions (Web)

- **Dialogs:** use the unified Promise-based helpers, never native browser
  dialogs. `window.kazmaConfirm(opts)` (→ `Promise<boolean>`),
  `window.kazmaAlert(opts)` (→ `Promise<void>`), `window.kazmaPrompt(opts)`
  (→ `Promise<string|null>`). All backed by `$store.modal`
  (`static/js/modules/stores.js`) + `components/modal.html`. Each has a
  native fallback if Alpine hasn't booted. The modal is single-instance.
- **Toasts:** use `window.showToast(msg, type, duration)` or
  `Alpine.store('toast').add(...)`. `streaming.js`'s `KazmaStream.toast`
  delegates to `$store.toast` — there is one toast system.
- **`x-cloak` is GLOBAL — do not re-introduce the blink.** The rule
  `[x-cloak] { display: none !important; }` lives once in `kazma.css`. Any
  `x-show`-gated panel MUST also carry `x-cloak`, or it flashes visible at
  first paint before Alpine evaluates its `x-show` (the "different section /
  permissions card blinks then disappears" symptom). Never put `display:flex`
  (or any `display`) in an inline `style` on an `x-show` element — the inline
  declaration wins over Alpine's `display:none` toggle; put flex layout in a
  CSS class instead (see `.system-alerts-banner`).
- **Responsive grids:** use `class="two-col-grid"` (collapses to one column
  ≤768px via `kazma.css`) on any inline `grid-template-columns:1fr 1fr;` —
  bare inline 2-col grids don't collapse and crush on mobile.

## Server Management

```powershell
# Kill existing server
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }

# Start server (background)
cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9090
```

## Testing & Validation

- **Compile check (Python):** `& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"`
- **Syntax check (JS):** `node --check "<file>"`
- **Run tests:** `& '.venv\Scripts\python.exe' -m pytest <path> -v`
- **Manual verification:** Restart server, test via Telegram and Web UI

## Key References

- `docs/docs/intro.md` — Documentation map (single SoT under `docs/docs/`)
- `docs/docs/guide/architecture.md` — Full system architecture with data flow diagram
- `docs/docs/guide/memory-and-rag.md` — Chat memory SoT (V2 cognitive engine is the single stack; the V1 4-layer RRF was removed in the V1→V2 cutover)
- `docs/docs/guide/document-intelligence.md` — Document Intelligence product guide
- `docs/docs/guide/document-phases.md` — Document phases 0–10 map
- `docs/docs/security/document-security.md` — Document threat model
- `docs/docs/ops/document-processing.md` — Document ops (metrics/GC/capacity)
- `docs/plans/DOCUMENT_DOCS_REMEDIATION_GOAL.md` — Document docs remediation goal
- `docs/plans/MEMORY_REMAINING.md` — Memory done vs later backlog
- `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` — Monorepo system map + remediation crosswalk
- `docs/docs/reference/tools-catalog.md` — Built-in + native tools
- `docs/docs/ops/production-checklist.md` — Production go-live checklist
- `docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md` — Latest production audit
- `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` — Document cert report
- `docs/DOCS_CONSOLIDATION_PLAN.md` — Docs consolidation plan
- `CHANGELOG.md` — Sprint history
- Live docs only under `docs/docs/` (Docusaurus). Do not resurrect retired `docs-v2` / loose handover trees.
