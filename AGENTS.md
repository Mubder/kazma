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
| `kazma-core` | `kazma-core/kazma_core/` | Agent runner, LLM provider, swarm engine, model registry, config store |
| `kazma-gateway` | `kazma-gateway/kazma_gateway/` | Platform adapters (Telegram/Discord/Slack), agent handler, slash commands |
| `kazma-ui` | `kazma-ui/kazma_ui/` | FastAPI web app, swarm panel, settings, SSE chat, static JS/CSS |
| `kazma-tui` | `kazma-tui/kazma_tui/` | Textual-based TUI dashboard (read-only ModelRegistry consumer) |

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
- `_handle_handoff()` accepts `_visited: set[str]` and `_depth: int`
- These thread through `_dispatch_worker_by_name_all` -> `_dispatch_worker` -> `_handle_handoff`
- Max depth is 5; removing the guard causes infinite recursion on A->B->A cycles

### 5. Circuit Breaker Half-Open (`kazma-core/kazma_core/swarm/reliability.py`)
- `_probe_in_flight` flag ensures only ONE dispatch passes through half-open state
- Both `record_success()` and `record_failure()` reset it
- Never remove this flag or concurrent calls bypass the probe semantics

### 6. TaskStore WAL Mode (`kazma-core/kazma_core/swarm/task_store.py`)
- SQLite uses WAL + `busy_timeout=5000` for concurrent read/write
- Schema auto-migrates on init (ALTER TABLE for new columns on existing DBs)
- Worker filter uses `json_each()` not `LIKE` for exact matching

## Coding Conventions

- Follow existing Kazma code style (type hints, docstrings, logging)
- Use `logger = logging.getLogger(__name__)` pattern
- Use `from __future__ import annotations` for type hints
- Keep modules focused (one concern per file)
- Python: compile-check with `py_compile` before committing
- JavaScript: syntax-check with `node --check` before committing
- Never use `&&` or `||` in PowerShell commands; use `;` and `$LASTEXITCODE`

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
