# HANDOFF: Kazma Swarm Framework - Phase 5 (Output Routing to Telegram Group)

## Repository State
- **Branch:** `main`
- **Latest commit:** `751fe61` (pushed to `origin/main`)
- **Working tree:** Clean (no uncommitted changes)
- **Server:** Running on `http://127.0.0.1:8090` (PID 43152)
- **Active model:** `deepseek-v4-flash` (provider: `deepseek`)
- **Python venv:** `G:\GitHubRepos\kazma\.venv`

## How to Restart the Server
```powershell
# Kill existing
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }
# Start
cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 8090
```

## How to Compile-Check
```powershell
& 'G:\GitHubRepos\kazma\.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"
node --check "<js-file>"
```

---

## What Was Done (Session History)

### Session 1 (commits up to `bb79d66`)
1. **Font-size persistence bug fix** - Manual localStorage + `$watch` replacing broken Alpine.$persist()
2. **Design-B modern overhaul** - Glassmorphism, bento cards, gradient text, theme-aware tints
3. **Discovery/routing crisis fix** - Removed LM Studio ghost fallback, added `find_provider_for_model()`, SSE chat re-resolves provider
4. **Add Provider modal** - Removed Preset field, fixed Name field binding
5. **Model selection checkboxes** - Per-provider checkboxes in Settings; only checked models appear in dropdowns
6. **Swarm worker routing fix** - `InProcessWorker.dispatch()` resolves correct provider via `find_provider_for_model()`
7. **Cross-platform dispatch** - Added `/swarm` slash commands to `agent_handler.py`
8. **Remove Dynamic Spawn** - Merged into unified Add Worker form
9. **Auto-scaling** - Created `autoscaler.py` with `WorkerTemplate` + `AutoScaler`; 4 API endpoints
10. **UX fixes** - SSE lifecycle fix, replaced `location.reload()` with `refreshStatus()`, wired dead metrics

### Session 2 (commits `bf6f1b8` through `6866b04`)
1. **Provider/model mismatch fix** - `set_active_model()` auto-switches provider; `get_client()` safety reconciliation
2. **LLM error handling** - Captures HTTP response body on 400/404 errors
3. **NVIDIA NIM tool-call fallback** - Retries without tools on 404 "Function not found"
4. **Slash commands wired** - `resolve_slash_command()` connected to `agent_handler.py` handler
5. **Interactive /models** - Telegram inline keyboards (provider -> model -> select)
6. **Natural language swarm dispatch** - Both `/swarm <task>` and bare "use the swarm to..." patterns
7. **Auto-routing** - CapabilityRouter matches best workers for NL dispatch
8. **Type dropdown removed** - Hardcoded to `in_process`

### Session 3 (commits `64a5ed4` through `751fe61`) - PRO GRADE OVERHAUL

#### Phase 1: Critical Bug Fixes

**`kazma-core/kazma_core/swarm/engine.py`:**
- Handoff cycle detection: `_handle_handoff()` now accepts `_visited: set[str]` and `_depth: int` params; aborts at max depth 5 or on cycle
- Catch-all try/except in `dispatch()`: wraps `_dispatch_inner()` so unexpected exceptions finalize as failed + close tracing spans
- LRU cap: `_task_history` limited to 500 entries (`_max_history = 500`)
- `reject_checkpoint`: removed double-store (was storing twice before/after setting result)
- `start_all`/`stop_all`: use `return_exceptions=True` so one failure doesn't abort others
- `_create_worker`: passes `system_prompt` from `WorkerConfig` to `InProcessWorker`

**`kazma-core/kazma_core/swarm/reliability.py`:**
- Half-open circuit breaker: added `_probe_in_flight` flag; only ONE probe allowed
- Retryable predicate: `_is_retryable_exception()` checks against `_NON_RETRYABLE_PATTERNS` (auth, config, rate limit)
- Removed dead `TimeoutGuardError` class

**`kazma-core/kazma_core/swarm/task_store.py`:**
- Schema: added columns (context, dependencies, fallback_chain, validation_schema, aggregation, timeout)
- Auto-migration: `ALTER TABLE` for existing DBs
- WAL mode + busy_timeout=5000 on connection
- Worker filter: `LIKE '%"...%'` replaced with `json_each()` for exact match
- Ordering: `COALESCE(completed_at, created_at) DESC` for NULL-safe
- Paused tasks: ordered by `created_at` instead of `completed_at`
- `_row_to_task`: restores all new fields from DB

**`kazma-core/kazma_core/swarm/registry.py`:**
- Added `threading.Lock` to all mutations (_load, _save, register, update, delete)
- `_save_unlocked()` for caller-held-lock path
- True cached singleton: `get_worker_registry()` function with module-level cache

**`kazma-core/kazma_core/swarm/topology.py`:**
- Pipeline stage system_prompt now passed to dispatch via `SwarmDispatchContext(system_prompt=stage.system_prompt)`

#### Phase 2: Token/Cost Tracking + Worker System Prompts

**`kazma-core/kazma_core/swarm/worker.py`:**
- `dispatch()` captures `tokens_used`, `cost`, `duration_seconds` from provider response
- Worker uses `self.system_prompt` (from config/registry) when no context prompt
- Redundant `find_provider_for_model` removed; simplified to `try: get_model() except: get_client(model=)`
- `SwarmWorker.logs` capped to 100 entries (ring buffer via `del logs[:len-100]`)
- Added `import time`
- `SwarmWorker` base dataclass: added `system_prompt: str = ""` field
- `InProcessWorker.__init__`: accepts and passes `system_prompt`

**`kazma-core/kazma_core/swarm/config.py`:**
- `WorkerConfig`: added `system_prompt: str = ""` field

**`kazma-ui/kazma_ui/templates/swarm.html`:**
- Added System Prompt textarea (`id="add-system-prompt"`, rows=3)
- Removed Type dropdown (In-Process / Telegram)

**`kazma-ui/kazma_ui/static/js/swarm.js`:**
- `addWorker()`: reads `add-system-prompt` value, sends `system_prompt` in payload
- `type` hardcoded to `'in-process'`
- Form clear on success includes `add-system-prompt`

**`kazma-ui/kazma_ui/swarm_panel.py`:**
- `_build_worker_config`: passes `system_prompt` from payload to `WorkerConfig`
- WorkerRegistry sync: uses `get_worker_registry()` singleton, fixed `caps_data` NameError

#### Phase 3: UI/API Improvements

**`kazma-ui/kazma_ui/swarm_panel.py`:**
- `GET /api/swarm/tasks`: added `q` param for server-side search on prompt text
- `GET /api/swarm/tasks/export`: new endpoint, supports `format=csv|json`
- Fixed `_task_store` vs `task_store` attribute access (uses `getattr(engine, "task_store", None) or getattr(engine, "_task_store", None)`)
- Removed dead `_SUPPORTED_MODELS` / `_SUPPORTED_PROVIDERS`
- Added `Response` import for CSV export

#### Phase 4: Dead Code Cleanup
- Removed `TimeoutGuardError` from `reliability.py` and `__init__.py` (`__all__` and import)
- Removed Type dropdown from `swarm.html`
- Removed `_SUPPORTED_MODELS` / `_SUPPORTED_PROVIDERS` from `swarm_panel.py`

---

## What Remains: Phase 5 - Output Routing to Telegram Group

### User's Requirement
> "I want an easy way to add an option to route the Output of the swarm to even a Telegram Bot which is already saved and/or to a Telegram group. I routed the output of the tasks to a telegram group by adding the set Telegram bot to a Telegram group so it output there."

> "We can make the output routing to be same output in the chat (which is the same output this dispatch started from) and the telegram group."

### Design Decisions (from user clarification)
1. **Output goes to BOTH** the originating chat AND the Telegram group (same output to both)
2. **Natural language dispatch only** via `/swarm <task>` or "use the swarm to..." keyword
3. **Auto-route via CapabilityRouter** when no specific worker mentioned

### Implementation Plan for Phase 5

#### Step 1: Add `output_target` to SwarmTask metadata

**File:** `kazma-core/kazma_core/swarm/task.py`
- No new field needed on SwarmTask - use `task.metadata["output_target"]` which already exists
- Format: `{"output_target": "telegram:-1001234567890"}` (platform:chat_id)

#### Step 2: Store Telegram group config

**File:** `kazma-core/kazma_core/config_store.py` (via ConfigStore)
- Key: `swarm.output_target` = `{"platform": "telegram", "chat_id": -1001234567890, "enabled": true}`
- Also allow per-dispatch override via `-> @GroupName` syntax in message

#### Step 3: Wire output routing into `_dispatch_swarm_from_chat`

**File:** `kazma-gateway/kazma_gateway/agent_handler.py`

The key function is `_dispatch_swarm_from_chat()` (line ~494). After `result = await engine.dispatch(swarm_task)` and after formatting `reply` text (line ~550):

```python
# After the existing reply to the originating chat:
await _send_swarm_reply(msg, store, manager, thread_id, reply)

# NEW: Also send to configured output target (Telegram group)
await _maybe_send_to_output_target(manager, reply, msg, store, thread_id)
```

New function `_maybe_send_to_output_target()`:
- Reads `swarm.output_target` from ConfigStore
- If enabled and configured, sends the same reply to the Telegram group
- Uses `OutboundMessage(target_id="telegram:<group_chat_id>", text=reply, context_metadata={})`
- Handles errors gracefully (group might not exist, bot might not be member)

Also handle the `-> @GroupName` syntax in `_dispatch_swarm_from_chat` or `_try_swarm_command`:
- Parse `<task> -> <target>` from the task text
- Set `swarm_task.metadata["output_target"]` accordingly
- Strip the `-> ...` suffix from the task prompt before dispatch

#### Step 4: Add `/swarm config` command for Telegram

**File:** `kazma-gateway/kazma_gateway/agent_handler.py`

Add to `_try_swarm_command()`:
```
/swarm config                    - show current output target config
/swarm config group <chat_id>    - set Telegram group chat_id for output routing
/swarm config clear              - clear output target
```

This reads/writes `swarm.output_target` in ConfigStore.

#### Step 5: Add API endpoint for output target config

**File:** `kazma-ui/kazma_ui/swarm_panel.py`

```python
@router.get("/api/swarm/output-target")
async def get_output_target() -> JSONResponse:
    # Read from ConfigStore

@router.put("/api/swarm/output-target")
async def set_output_target(payload: dict) -> JSONResponse:
    # Write to ConfigStore: {platform, chat_id, enabled}
```

#### Step 6: Add UI section in Swarm panel

**File:** `kazma-ui/kazma_ui/templates/swarm.html`

Add an "Output Routing" card in the Workers tab (or a new settings area):
- Toggle: Enable output routing (on/off)
- Input: Telegram Group Chat ID
- Help text: "Add your bot to the Telegram group first. The bot must have permission to send messages."
- Save button

**File:** `kazma-ui/kazma_ui/static/js/swarm.js`
- `loadOutputTarget()` - fetch from `/api/swarm/output-target`
- `saveOutputTarget()` - PUT to `/api/swarm/output-target`

---

## Key Files Reference

### Swarm Core (`kazma-core/kazma_core/swarm/`)
| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `engine.py` (~1727 lines) | Main swarm engine | `SwarmEngine`, `dispatch()`, `_dispatch_inner()`, `_handle_handoff()`, `_dispatch_swarm_from_chat()` |
| `worker.py` (~405 lines) | Worker implementations | `SwarmWorker` (ABC), `InProcessWorker`, `TelegramWorker` (dead) |
| `task.py` (~303 lines) | Data model | `SwarmTask`, `TaskResult`, `WorkerResult`, `WorkerCapabilities`, `SwarmDispatchContext`, `HandoffRecord` |
| `config.py` | Config dataclasses | `WorkerConfig` (has `system_prompt`), `SwarmConfig` |
| `registry.py` (~337 lines) | Persistent worker catalog | `WorkerRegistry`, `WorkerEntry`, `get_worker_registry()` |
| `task_store.py` (~427 lines) | SQLite persistence | `TaskStore`, `persist_task()`, `list_tasks()`, `_row_to_task()` |
| `reliability.py` (~860 lines) | Circuit breakers, retries | `CircuitBreaker`, `RetryPolicy`, `TimeoutGuard`, `FallbackChain` |
| `router.py` (~200 lines) | Capability-based routing | `CapabilityRouter`, `NoCapableWorkersError` |
| `patterns.py` (~570 lines) | Dispatch patterns | `execute_pipeline()`, `execute_fan_out()`, `execute_conditional()` |
| `topology.py` (~470 lines) | Pipeline stages | `PipelineEngine`, `PipelineStage`, `RefinerStage` (dead) |
| `autoscaler.py` (~306 lines) | Auto-scaling | `WorkerTemplate`, `AutoScaler` |

### Gateway (`kazma-gateway/kazma_gateway/`)
| File | Purpose | Key Functions |
|------|---------|---------------|
| `agent_handler.py` (~1091 lines) | Message handler | `create_graph_handler()`, `_try_swarm_command()`, `_dispatch_swarm_from_chat()`, `_send_swarm_reply()`, `_try_model_command()`, `_build_slash_ctx()`, `_build_target_id()` |
| `slash_commands.py` (~654 lines) | Command resolver | `resolve_slash_command()`, `_cmd_help()`, `_cmd_model()`, `_cmd_config()` |
| `adapters/telegram.py` (~1024 lines) | Telegram adapter | `TelegramAdapter`, `send()`, `build_provider_keyboard()`, `build_model_keyboard()`, `_handle_callback_query()` |
| `adapters/telegram_bus.py` (~340 lines) | Swarm bus adapter | `TelegramBusAdapter` |
| `gateway.py` (~727 lines) | Core gateway types | `IncomingMessage`, `OutboundMessage`, `SessionStore`, `BaseAdapter` |

### UI (`kazma-ui/kazma_ui/`)
| File | Purpose |
|------|---------|
| `app.py` (~1100 lines) | FastAPI app factory, wires all routers + gateway |
| `swarm_panel.py` (~1373 lines) | Swarm REST API + HTML serving |
| `static/js/swarm.js` (~1397 lines) | Swarm panel frontend logic |
| `templates/swarm.html` (~482 lines) | Swarm panel template |

### Config (`kazma-core/kazma_core/`)
| File | Purpose |
|------|---------|
| `config_store.py` | SQLite-backed key-value config store |
| `model_registry.py` (~732 lines) | Provider/model management, `get_client()`, `set_active_model()` |
| `llm_provider.py` (~363 lines) | OpenAI-compatible LLM client, `chat()` with tool-call fallback |

---

## Critical Code Paths for Phase 5

### 1. `_dispatch_swarm_from_chat()` (agent_handler.py, line ~494)

This is where swarm tasks are dispatched from chat and results sent back. The output routing hook goes here:

```python
async def _dispatch_swarm_from_chat(msg, store, manager, thread_id, engine, workers, task, pattern):
    # ... creates SwarmTask, dispatches, formats reply ...
    
    # EXISTING: sends reply to originating chat
    await _send_swarm_reply(msg, store, manager, thread_id, reply)
    
    # PHASE 5: also send to Telegram group if configured
    # await _maybe_route_to_group(manager, reply, msg, store, thread_id)
```

### 2. `_send_swarm_reply()` (agent_handler.py, line ~560)

Platform-agnostic reply sender. Uses `_build_target_id()` to construct `"telegram:<chat_id>"`.

```python
async def _send_swarm_reply(msg, store, manager, thread_id, text):
    ctx = await store.get(thread_id)
    if not ctx:
        ctx = msg.context_metadata
    await manager.send(OutboundMessage(
        target_id=_build_target_id(msg.platform, ctx),
        text=text,
        context_metadata=ctx,
    ))
```

For Phase 5, a similar function is needed but with a custom target_id pointing to the group.

### 3. `_build_target_id()` (agent_handler.py, line ~1074)

```python
def _build_target_id(platform: str, ctx: dict[str, Any]) -> str:
    chat_id = ctx.get("chat_id")
    if chat_id is not None:
        return f"{platform}:{chat_id}"
    return f"{platform}:unknown"
```

For the group, target_id would be `f"telegram:{group_chat_id}"`.

### 4. `OutboundMessage` (gateway.py, line ~97)

```python
@dataclass(slots=True)
class OutboundMessage:
    target_id: str          # "telegram:12345"
    text: str
    context_metadata: dict[str, Any] = field(default_factory=dict)
```

The Telegram adapter's `send()` method extracts `chat_id` from `context_metadata` first, then falls back to parsing `target_id`. For group routing, set `context_metadata={"chat_id": group_chat_id}`.

### 5. Telegram `send()` (adapters/telegram.py, line ~900)

Already handles 429 retries, parse_mode fallback. Can send to any chat_id the bot is a member of.

### 6. ConfigStore access pattern

```python
from kazma_core.config_store import ConfigStore
cs = ConfigStore()
# Read
target = cs.get("swarm.output_target", None)
# Write
cs.set("swarm.output_target", {"platform": "telegram", "chat_id": -1001234567890, "enabled": True})
```

---

## Known Issues / Gotchas

1. **Telegram group chat IDs are negative** (e.g., `-1001234567890`). Supergroups start with `-100`.
2. **Bot must be added to the group** and have "Send Messages" permission.
3. **Telegram message limit:** 4096 chars. Long swarm outputs need splitting.
4. **Markdown parsing:** The Telegram adapter already has a parse_mode fallback (retries without Markdown on 400).
5. **The `swarm.js` still has dead functions** referencing removed spawn form (`populateModelDatalist`, `populateProfileSelect`, spawn-* IDs). Not blocking but should be cleaned eventually.
6. **`_fallback_html` in swarm_panel.py** (~120 lines) is dead code duplicating the template. Can be removed.
7. **Server process management:** Each Execute call is a new shell. Use `fireAndForget=true` for the server and read logs from the temp file path.

## Remaining Future Work (NOT Phase 5)
- Circuit breaker UI badges + reset per worker
- Per-worker start/stop endpoints
- Task cancel/retry from UI
- Visual pipeline editor (drag-and-drop DAG)
- Semantic routing (embeddings instead of keyword overlap)
- Unify CapabilityRouter vs SemanticRouter
- Unify topology.py PipelineEngine vs patterns.py
- Worker health monitoring dashboard
- SummaryWorker module is dead code (never wired)
- TelegramWorker class is vestigial (dispatch ignores its own fields)
