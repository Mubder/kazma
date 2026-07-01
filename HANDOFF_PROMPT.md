# HANDOFF PROMPT FOR NEXT AGENT

Copy/paste the text below as the first message to a new Droid session:

---

I'm working on the Kazma agent framework at `G:\GitHubRepos\kazma`. Before doing anything, read these files to understand the project:

1. `G:\GitHubRepos\kazma\architecture.md` — Full system architecture (swarm engine, gateway, UI, data flow)
2. `G:\GitHubRepos\kazma\CHANGELOG.md` — Sprint 12 has everything done in the current session
3. `G:\GitHubRepos\kazma\HANDOFF_PHASE5.md` — Detailed handoff from the previous session

## What was done (Sprint 12, commits bf6f1b8 → e4c0487)

### Provider/Model fixes:
- `model_registry.py`: `set_active_model()` auto-switches provider to match model; `get_client()` reconciles mismatches
- `llm_provider.py`: captures HTTP response body on errors; retries without tools on NVIDIA NIM 404 "Function not found"

### Slash commands + Telegram:
- `agent_handler.py`: wired `resolve_slash_command()` into the handler (was never called before)
- `/models` interactive selector with Telegram inline keyboards (provider → model → select)
- `/reset` now actually skips the graph (was falling through to LLM)

### Swarm natural language dispatch:
- Both `/swarm <task>` and bare "use the swarm to..." trigger auto-routing via CapabilityRouter
- `_extract_swarm_task()` strips filler phrases ("use the swarm to", "swarm:", etc.)
- `_dispatch_auto_route()` matches workers by expertise, falls back to broadcast

### Swarm Phase 1-4 (pro-grade overhaul):
- Engine: handoff cycle detection (max depth 5), catch-all in dispatch, LRU history cap (500)
- Reliability: half-open circuit breaker single-probe, retryable predicate (no auth/config retries)
- TaskStore: WAL mode, schema migration (6 new columns), json_each worker filter, COALESCE ordering
- Registry: threading.Lock + true singleton via `get_worker_registry()`
- Topology: stage system_prompt delivered to dispatch via SwarmDispatchContext
- Worker: captures tokens_used/cost/duration_seconds; uses system_prompt from config/registry
- Dead code removed: TimeoutGuardError, _SUPPORTED_MODELS, Type dropdown

### Swarm Phase 5 (output routing):
- Swarm results mirrored to both originating chat AND configured Telegram group
- Config via: UI card, `/swarm config group <chat_id>`, or per-dispatch `-> telegram:<chat_id>`
- API: `GET/PUT /api/swarm/output-target`

## CRITICAL parts of the project (DO NOT break these)

1. **Provider/Model resolution** (`model_registry.py`): `get_client()` auto-corrects provider/model mismatches. The `set_active_model()` method switches BOTH model and provider. Never call one without the other.

2. **Platform isolation** (`agent_handler.py`): The LangGraph state NEVER contains chat_id/user_id. These live in `SessionStore`. `_build_target_id()` reconstructs the target from context_metadata. Breaking this leaks platform IDs into the graph.

3. **Swarm dispatch flow** (`agent_handler.py`): Message → slash command check → model command check → `/reset` check → swarm command check → graph invoke. Order matters. Slash commands return early; only unrecognized messages reach the graph.

4. **LLM tool fallback** (`llm_provider.py`): Some providers (NVIDIA NIM) reject tool definitions with 404. The code retries without tools automatically. Never remove the `status_code == 404 and "function" in detail.lower()` branch.

5. **TaskStore WAL mode** (`task_store.py`): The SQLite DB uses WAL + busy_timeout=5000. The schema auto-migrates on init (ALTER TABLE for new columns). Never change the connection without preserving these pragmas.

6. **Handoff cycle detection** (`engine.py`): `_handle_handoff()` accepts `_visited: set[str]` and `_depth: int`. These thread through `_dispatch_worker_by_name_all` → `_dispatch_worker` → `_handle_handoff`. Never remove these params or the recursion guard.

7. **Circuit breaker half-open** (`reliability.py`): `_probe_in_flight` flag ensures only ONE dispatch passes through half-open. `record_success()` and `record_failure()` both reset it. Never remove this flag.

## Server management

```powershell
# Kill
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }
# Start (background)
cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090
# Compile check
& 'G:\GitHubRepos\kazma\.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"
```

## Known remaining work (not yet started)

- Circuit breaker UI badges + reset per worker
- Per-worker start/stop endpoints
- Task cancel/retry from UI
- Semantic routing (embeddings instead of keyword overlap)
- Unify CapabilityRouter vs SemanticRouter
- Visual pipeline editor (drag-and-drop DAG)
- SummaryWorker module is dead code (never wired)
- TelegramWorker class is vestigial (dispatch ignores its own fields)
- swarm.js dead functions: populateModelDatalist, populateProfileSelect, spawn-* references
- _fallback_html in swarm_panel.py (~120 lines of dead HTML)
