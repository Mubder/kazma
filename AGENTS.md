# Mission Guidance: Kazma Agent Framework

## Project Overview

Kazma is a multi-platform AI agent framework with a LangGraph supervisor brain,
swarm orchestration, cross-platform dispatch (Telegram/Discord/Slack/Web/TUI),
and an OpenAI-compatible LLM provider layer. See `architecture.md` for the full
system architecture and `CHANGELOG.md` for recent work.

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

### 2. Platform Isolation (`kazma-gateway/kazma_gateway/agent_handler.py`)
- The LangGraph state NEVER contains `chat_id`, `user_id`, or `message_id`
- These live in `SessionStore` and are restored via `_build_target_id()` on reply
- Breaking this leaks platform IDs into the graph and corrupts sessions

### 3. LLM Tool Fallback (`kazma-core/kazma_core/llm_provider.py`)
- Some providers (NVIDIA NIM) reject tool definitions with 404 "Function not found"
- The code retries without tools automatically when this is detected
- Never remove the `status_code == 404 and "function" in detail.lower()` branch

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

**A. Graph interrupt() — single-agent chat (Web/Telegram/Discord/Slack)**
- `graph_builder.py:tool_worker_node` calls LangGraph `interrupt()` for danger tools
- Gate is active ONLY when `hitl_config` is passed to `build_supervisor_graph()`
- Two build sites must pass it: `agent_runner.get_streaming_graph()` AND
  `app.py` startup recompile (~line 966). Omitting either = dormant gate.
- Resume: `graph.ainvoke(Command(resume={"approved": bool}), config)` via
  `POST /api/approve/{thread_id}` (web) or `/hitl approve|deny {thread_id}` (gateway)
- State persists in the checkpointer — paused turns survive restarts
- Double-gating prevention: `_hitl_approved=True` flag in tool args skips the
  bus check when the graph already approved via interrupt

**B. Swarm bus — `/swarm` dispatch path**
- `tool_registry.py:execute()` calls `safety.check()` (async) for danger tools
- `check_sync()` is **fail-closed** (default): blocks danger tools when no real
  bus adapter is present. `allow_headless_danger=True` is the test/dev escape hatch
- Bus adapters: `TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`
- Only ONE adapter active at a time (bus singleton); priority Telegram > Discord > Slack
- Approval buttons resolve via `handle_callback()` — called from each adapter's
  inbound callback/interaction handler

**C. Pipeline checkpoints — swarm PIPELINE tasks** (separate from A and B)
- `engine.py:_handle_pipeline_checkpoint` + `approve_checkpoint`

**Danger tool lists differ by mechanism:**
- Graph path: `kazma.yaml` `safety.hitl.require_approval_for` (file_write, file_delete, shell_exec, code_exec, python_exec)
- Swarm bus: `safety.py:_EXTENDED_DANGER` (adds spawn_agent, spawn_agents, schedule_task, cancel_scheduled)
- MCP tools: `classify_mcp_tool()` in `mcp/manager.py` — name-pattern matching (write/exec/delete → danger, read/list/get → safe, unknown → danger). Gate is in `UnifiedToolExecutor.execute()`.

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

**A. Workspace root resolution — TWO resolvers that MUST agree**
- `file_write._get_workspace()` (`tools/file_write.py`) is used by ALL file
  tools (`file_read`, `file_write`, `file_delete`, `file_list`, `file_search`).
- `IdeService._resolve_workspace_root()` (`ide/service.py`) is used by the IDE
  API + TUI.
- **Resolution precedence (both must follow this):** per-task `workspace_scope`
  ContextVar → `configure_workspace()` global → `KAZMA_WORKSPACE` env →
  **active WorkspaceStore row** → `cwd/kazma-data/workspace` default.
- `app.py` boot config (~line 250) calls `configure_workspace()` — it must
  consult WorkspaceStore's active workspace, NOT just default to
  `kazma-data/workspace`. Breaking this reintroduces the "reads outside
  workspace" bug where repo files get rejected.
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
cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090
```

## Testing & Validation

- **Compile check (Python):** `& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"`
- **Syntax check (JS):** `node --check "<file>"`
- **Run tests:** `& '.venv\Scripts\python.exe' -m pytest <path> -v`
- **Manual verification:** Restart server, test via Telegram and Web UI

## Key References

- `architecture.md` — Full system architecture with data flow diagram
- `CHANGELOG.md` — Sprint history (Sprint 12 = current swarm pro-grade work)
- `HANDOFF_PHASE5.md` — Detailed file-by-file handoff from the swarm overhaul
- `HANDOFF_PROMPT.md` — Ready-to-paste onboarding prompt for new agents
