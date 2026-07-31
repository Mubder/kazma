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
- `docs/docs/guide/memory-and-rag.md` — Chat memory SoT (4-layer RRF, consolidator, graph)
- `docs/plans/MEMORY_REMAINING.md` — Memory done vs later backlog
- `docs/ARCHITECTURE_AND_SYSTEM_MAP.md` — Monorepo system map + remediation crosswalk
- `docs/docs/reference/tools-catalog.md` — Built-in + native tools
- `docs/docs/ops/production-checklist.md` — Production go-live checklist
- `docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md` — Latest production audit
- `docs/DOCS_CONSOLIDATION_PLAN.md` — Docs consolidation plan
- `CHANGELOG.md` — Sprint history
- Live docs only under `docs/docs/` (Docusaurus). Do not resurrect retired `docs-v2` / loose handover trees.
