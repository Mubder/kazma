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

**Strict local chat templates — system messages MUST stay at the head:**
- Local OpenAI-compatible servers with strict Jinja templates (LM Studio /
  llama.cpp Qwen3) return HTTP 400 `System message must be at the beginning`
  when any `role: system`/`developer` message appears AFTER the first user
  message. Kazma injects such notes mid-stream (INTENT ENGINE plan notes,
  iteration budget nudges, mission patches), so checkpointed history
  naturally contains them — a plain reload+send 400s on every turn.
- `hoist_system_messages()` (`llm_provider.py`) is applied to the messages
  payload inside `LLMProvider.chat()` — the single OpenAI-compatible path
  (LM Studio, Ollama, OpenAI, DeepSeek, …) shared by all transports. It
  moves system/developer messages to the head (order preserved, no-op when
  already ordered) and keeps assistant/tool adjacency intact. NEVER remove
  this call from `chat()`.
- New mid-stream system-note injection sites are covered by the hoist — but
  never add a second LLM-call path that bypasses `LLMProvider.chat()`.
- Anthropic/Gemini/Azure/Bedrock have their own `chat()` implementations
  that handle system messages natively — do NOT hoist there.

### 4. Swarm Handoff Cycle Detection (`kazma-core/kazma_core/swarm/engine.py`)
- `_handle_handoff()` accepts `_visited: dict[str, int]` and `_depth: int`
- These thread through `_dispatch_worker_by_name_all` -> `_dispatch_worker` -> `_handle_handoff`
- Max depth is 5; removing the guard causes infinite recursion on A->B->A cycles
- Workers can be revisited up to `MAX_VISITS=2` times (allows legitimate A->B->A return handoffs)
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

**A. Graph interrupt() — single-agent chat (Web SSE + Telegram/Discord/Slack)**
- `graph_builder.py:tool_worker_node` calls LangGraph `interrupt()` for danger tools
- Gate is active ONLY when `hitl_config` is passed to `build_supervisor_graph()`
- Required build sites: `agent_runner.get_streaming_graph()`, `agent_runner._ensure_graph`,
  and `app.py` startup recompile into `_graph_holder`. Omitting HITL on any site =
  dormant gate on that path.
- Resume: `graph.ainvoke(Command(resume=…), config)` via `POST /api/approve/{thread_id}`
  (SSE — the Web SoT), or gateway `/hitl approve|deny {thread_id}`. WS
  `approve_tool` is off unless `KAZMA_WS_GRAPH=1`.
- State persists in the checkpointer — paused turns survive restarts
- Double-gating prevention: graph sets ContextVars (`_graph_hitl_gate_ctx` /
  `_hitl_approved_ctx`) so `LocalToolRegistry.execute` does **not** re-prompt the bus

**B. Swarm bus — `/swarm` + IDE `LocalToolRegistry.execute` path**
- `tool_registry.py:execute()` calls `safety.check()` (async) for danger tools
- `check_sync()` is **fail-closed** (default): blocks danger tools when no real
  bus adapter is present. `allow_headless_danger=True` is the test/dev escape hatch
- Optional canonical floor (deep-audit 2026-08-19): `KAZMA_HITL_CANONICAL_FLOOR=1`
  unions CANONICAL back into the effective `require_approval_for`, so
  Settings/YAML narrowing below CANONICAL is capped back up (strict
  multi-operator deployments). The drift warning repeats every 15 min either way.
- Bus adapters: `TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`
- App wiring: **one** adapter if only one platform; **`FanOutBusAdapter`** when
  multiple are configured (first approval wins). NullBus = internal-only /
  fail-closed danger
- Approval buttons resolve via `handle_callback()` on each adapter

**C. Pipeline checkpoints — swarm PIPELINE tasks** (separate from A and B)
- `engine.py:_handle_pipeline_checkpoint` + `approve_checkpoint`

**Danger tool list SoT (must stay one list):**
- **Canonical:** `kazma_core.safety.hitl.CANONICAL_DANGER_TOOLS`
- **YAML:** `kazma.yaml` `safety.hitl.require_approval_for` (parity-tested by
  `tests/test_agent_skills.py::TestHitlWiring::test_yaml_parity` and
  `tests/test_hitl_wiring.py` — the tests compare SETS; `hitl.py` groups the
  tools thematically while the YAML list is alphabetical. Add a new danger
  tool to BOTH or the tests fail)
- **Settings UI / ConfigStore:** `safety.require_approval_for` — consumed by
  `get_hitl_config()` (runtime override)
- **Swarm bus:** `swarm/safety.py` `_EXTENDED_DANGER` is a materialized copy of
  CANONICAL with identical contents (CANONICAL is a tuple, so it cannot be the
  same object — spawn tools only if on CANONICAL)
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
| `engine.py` (1366 lines) | Dispatch, handoff, task lifecycle, worker registry | Always — the orchestrator |
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
- **Per-task scope guard (deep-audit 2026-08-19):** MCP rebind is
  PROCESS-GLOBAL — a per-task `workspace_scope` does NOT rebind servers.
  `mcp/manager.py:execute_mcp_tool` fail-closes with an actionable error
  when a per-task scope targets a different root than the bound MCP root
  (kill-switch `KAZMA_MCP_SCOPE_GUARD=0`). Per-workspace MCP instances
  remain future work.
- Path-traversal protection: `IdeService.resolve()` does a string-level
  `normpath` `..` check + containment backstop (symlink/junction-aware).

**B. HITL routing — no parallel write/exec path**
- All mutating/exec IDE operations (`write_file`, `apply_patch`, `delete_file`,
  `run`, `run_file`, `git`) delegate to `LocalToolRegistry.execute()` via
  `IdeService._call_tool()`. The HITL gate lives in `tool_registry.py:execute()`
  (§7B). Never call the underlying tool functions directly from the IDE layer.
  Prefer `file_apply_patch` for edits to existing files (not whole-file write).

**C. Awareness injection — `ide/env_context.py`**
- `build_env_context()` resolves workspace root, repo slug (from WorkspaceStore
  cache or `git remote`), branch, GitHub auth, and available tools into a
  markdown block.
- Injected at THREE sites: main agent init (`agent_runner.py` — NOT
  `graph_builder.py`, which has no env_context reference), per-turn in the SSE
  chat path (`sse_chat.py`, so workspace switches take effect immediately),
  and into every dispatched worker prompt (`worker.py`).
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
queue drained by a worker, plus FOUR fire-and-forget asyncio scheduler loops.
These were the subsystem that silently lost its backup/export runs (the
routines existed but nothing called them) — read this before touching the
background memory path.

**A. `start_memory_worker()` is the single boot entry — it starts ALL FOUR
schedulers.** Called from `app.py` startup (wrapped in try/except so it
can't block boot). It registers handlers + calls `start_worker()` +
`_start_macro_sleep_scheduler()` + `_start_backup_export_scheduler()` +
`_start_reconsolidation_scheduler()` + `_start_commitment_gc_scheduler()`.
If a new scheduler is added, register/start it HERE or it will never run (the
exact gap that previously left backups/export inert).

**B. Four scheduler loops, distinct cadences (do not collapse them):**
- **6h `macro_sleep` loop** (`_MACRO_SLEEP_INTERVAL_HOURS = 6`): enqueues a
  `macro_sleep` task every 6h → decay scoring, tier demotion/promotion,
  archival (`macro_sleep.py:run_macro_sleep`). First sweep 60s after boot.
- **24h backup/export loop** (`_BACKUP_EXPORT_INTERVAL_HOURS = 24`): enqueues
  `native_backup` + `nightly_export` + `native_pg_backup` tasks every 24h →
  native `sqlite3.backup()` of both memory DBs (`backup.py`) + JSONL/GraphML
  dumps (`export.py`) + a filtered `pg_dump` of Kazma's Postgres
  shared-state tables (§21). First sweep 120s after boot. Kept separate
  from the 6h loop so a slow disk on backup can't stall decay.
- **24h reconsolidation loop** (`_RECONSOLIDATION_INTERVAL_HOURS = 24`):
  enqueues `global_reconsolidation` (dedupe + re-embed of beliefs,
  subject-hash partitioned).
- **15-min commitment GC loop** (`_COMMITMENT_GC_INTERVAL_MINUTES = 15`):
  enqueues the commitment-store sweep (TTL expiry + tiered retention, §20).
All loops only `enqueue_task(...)`; the durable worker drains the actual
work, so a failed enqueue cannot kill the cadence and a failed handler is
retried/bounded by the queue.

**C. Handler registration is idempotent via separate module-level flags.**
`register_v2_handlers()` guards on `_registered` (macro_sleep /
entity_merge / micro_consolidation); `register_backup_export_handlers()`
guards on `_backup_export_registered` (native_backup / nightly_export /
native_pg_backup). The underlying `register_handler()` is a plain dict
assignment (idempotent overwrite), but the flags avoid re-churning on
re-boot / repeated calls and let the backup handlers register
independently of the core V2 set.

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
`docs/plans/COMMITMENT_PHASE0_EXIT_REPORT.md`. **All phases shipped**: 0–2
(core + gate + store + TTL/GC), 3 (semantic clarify/confirm interrupt card),
4 (remind + cancel_job + exec/outbound/config resolvers), 5 (swarm scope-token
default + MCP classification), 6 (autonomy modes), 7 (soul confirm gate), 8
(docs + metrics). Default-off kill-switches on every enforcement layer.
**Fail posture (deep-audit 2026-08-19):** authorization-engine EXCEPTIONS on
semantic tools fail CLOSED at both chokes (a broken policy engine must not
free-fire the remind/exec classes); the layer kill-switch and
import-unavailable degradation stay fail-open (treated as layer-off).

**A. Two `authorize_effect` choke points + an independent memory-side gate.**
- `agent/graph_builder.py:tool_worker_node` — the single-agent chat path; the
  gate runs BEFORE the security HITL split so it can rewrite args first.
- `agent/tool_registry.py:LocalToolRegistry.execute` — the IDE/swarm path
  (mostly audit-only — remind/cancel_job decisions need graph context — but
  the exec denylist / outbound allowlist / config protected-key resolvers DO
  enforce here).
- `memory/belief_mutation.py:_mutate_functional` — the memory corruption half
  is gated by its OWN source-trust check (a `user_explicit` functional belief
  cannot be superseded by `llm_inferred`/`system_tool`), NOT by an
  `authorize_effect` call — two independent defenses, by design.

**B. The decision mapping (§3.4). `authorize_effect` returns one of:**
- `allow` (+ optional `rewritten_args`): for the remind act, the gate anchors
  the relative phrase to a memory event and **rewrites the tool args to the
  memory-correct fire_at**. For exec, the denylist blocks catastrophes before
  the HITL card. For config, protected keys are denied. For outbound, the
  target allowlist is checked.
- `clarify` / `confirm`: a real interrupt card fires on the unified HITL bus
  (kind=semantic_clarify/confirm) with discrete options. Per-option buttons
  render on Web (chat.js + sidebar), Telegram, Discord, and Slack. Resume
  applies the chosen `slots_patch`. The existing Approve/Deny buttons map to
  best-option / cancel via `build_resume_value`.
- `deny`: blocked with a clear error; no card.
- Audit-only: read tools, `mutate_fs` (containment in `IdeService.resolve`),
  `delegate` (HMAC trust at skill-load).

**C. Invariants — removing any reintroduces the incident class:**
- **Source-trust gate** (`_mutate_functional`): a `user_explicit` functional
  belief may NOT be superseded by a lower-trust (`llm_inferred`/`system_tool`)
  source. Kill-switch: `cfg.v2.functional_supersede_requires_user_assert`.
- **Rewrite-on-allow**: the gate's fire_at wins over the model's args — do not
  let the original (possibly wrong) `timing` reach the scheduler for remind.
- **Exec denylist**: catastrophic commands (`rm -rf /`, fork bombs, `curl|sh`,
  `dd of=/dev/`, `mkfs`, shutdown, `chmod 777 /`) are denied BEFORE the HITL
  card. Safe commands pass through (HITL still applies).
- **Config protected keys**: `safety.*`, `agent.commitment.*`,
  `notifications.lifecycle.*` cannot be mutated by the agent (self-protection).
- **Outbound allowlist**: when `agent.commitment.outbound_allowed_targets` is
  configured, unknown targets → clarify with the allowlist.
- **Swarm scope default** (`worker_dispatch._do_dispatch`): when
  `swarm_scope_enforce` is on, dispatched workers are capped at semantic_tier
  HIGH (deny exec/outbound/config/identity CRITICAL) + denied_acts=
  {soul_delta, identity, config_change}. Default ON since 2026-08-15 (intent-engine auto-dispatch) — opt-OUT via the env/ConfigStore kill-switch.
- **Soul confirm gate** (`apply_agent_mutation`/`_auto_apply`): when
  `soul_requires_confirm` is on, soul deltas are held until confirmed via
  `POST /api/commitment/soul/{cid}/confirm`. Mint-wired at both apply callers.
  Default OFF.
- **Fail-open + kill-switch**: `KAZMA_COMMITMENT_ENABLED=0` disables the whole
  layer. Every enforcement layer has its own default-OFF flag. (Engine
  *exceptions* on semantic tools fail closed at both chokes — see the §20
  header; only the kill-switch/import-degradation paths fail open.)
- **Conservative auto-store**, **No late approve**, **GC cadence** — as before.

**D. Components (`kazma_core/safety/commitment/`).**
- `side_effects.py` — the single SoT registry: tool → `ToolEffectProfile`.
  Parity-tested. Unregistered mutators fail-closed (tokenized). MCP tools
  (`mcp__*`) route through `classify_mcp_tool_effect`.
- `authorize.py` — `authorize_effect` (the policy gate) + `EffectDecision` +
  the act resolvers: `_resolve_remind_act`, `_resolve_cancel_job_act`,
  `_resolve_exec_act` (denylist+cwd), `_resolve_send_outbound_act` (allowlist),
  `_resolve_config_change_act` (protected keys).
- `relative_time.py` — `resolve_remind` (EN+AR). G2-measured (0 false-allow).
- `store.py` — `Commitment` + ops-SQLite tables + TTL/GC + tiered retention +
  `list_pending_soul()` (the confirm queue).
- `constraints.py` — `is_commitment_enabled` + `load_constraint_beliefs` +
  `cron_pending_jobs` (for the cancel_job resolver).
- `config.py` — `get_commitment_config` (the ONE config reader).
- `scope.py` — `ScopeToken` + `swarm_scope` (ContextVar) +
  `default_worker_scope()` + `is_act_within_scope()` (the privilege guard).
- `resume.py` — `build_resume_value()` + `is_semantic_kind()` (maps
  Approve/Deny → option/cancel for semantic interrupts on every platform).
- `_commitment_resolve_gate()` in `graph_builder.py` — the extracted gate
  (Phase 2.5 SRP). Called from `tool_worker_node`.
- **Operator API**: `kazma_ui/commitment_api.py` —
  `GET /api/commitment/soul/pending`, `POST .../{cid}/confirm`, `POST .../{cid}/reject`.
- **Metrics**: `kazma_ui/metrics.py` exposes
  `kazma_commitment_decisions_total{decision=...}` + `kazma_commitment_pending`.

**E. Modes + kill-switches.**
- Modes (`agent.commitment.mode` / `KAZMA_COMMITMENT_MODE`): strict |
  balanced (default) | autonomous | yolo.
- Kill-switches (all default OFF / layer default ON):
  `KAZMA_COMMITMENT_ENABLED` (layer, default on),
  `KAZMA_COMMITMENT_SWARM_SCOPE_ENFORCE` (default ON since 2026-08-15),
  `KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM` (default off),
  `KAZMA_AUTO_STORE_BELIEFS` (default conservative).

**Tests:** 15+ test files (`tests/test_commitment_*.py` +
`tests/test_side_effects.py`) — corpus/G1/G2, store+GC, authorize, clarify-card
interrupt+resume, tool_worker gate, scenarios (7), act resolvers, modes, scope,
soul, cancel_job, config. Run:
`python -m pytest tests/test_commitment_*.py tests/test_side_effects.py -m "not slow"`.

### 21. Postgres Shared-State Backup & Schema Assurance (`kazma-core/kazma_core/db/pg_backup.py`)

Scheduled `pg_dump` backups + boot-time schema verification for the tables
Kazma owns in Postgres. Built after the 2026-08-14 incident where a second
app pointed at the shared `kazma` database dropped Kazma's tables
(checkpoints / settings / chat sessions / document jobs) and there was no
scheduled PG backup to restore from.

**A. `KAZMA_PG_TABLES` is the single SoT of which tables Kazma owns.**
The list (LangGraph `checkpoints*`, `kazma_settings`, `kazma_chat_sessions`,
`kazma_swarm_tasks`, `kazma_swarm_worker_metrics`, `kazma_platform_users`,
`kazma_web_sessions`, `document_jobs`, `document_job_events`) drives BOTH
the nightly dump's `-t` filter AND the boot verification. A new
shared-state PG table MUST be added here or it silently stops being backed
up. The dump is deliberately table-filtered — never a whole-DB dump — so a
shared database neither leaks foreign-app data into Kazma's backups nor
restores over another app's tables.

**B. Nightly dump pipeline (24h loop, `worker_bootstrap.py`).**
The 24h backup/export loop enqueues `native_pg_backup` when
`pg_backup_enabled()` (live-checked: Postgres backend + `backups.pg.enabled`
config + `KAZMA_PG_BACKUP_ENABLED` env kill-switch). The handler
(`_handle_native_pg_backup`) runs `perform_pg_backup()` in a worker thread:
dump via `migration/pg_bridge.dump_database(tables=KAZMA_PG_TABLES)` to a
`.tmp` file, validate the `PGDMP` magic, atomically rename into
`{kazma-data}/backups/pg/pg_shared_<epoch>.dump`, then prune to
`backups.pg.retention` (default 7, env `KAZMA_PG_BACKUP_RETENTION`).
Failures return False so the durable queue retries (max 3) then
dead-letters; a failed dump never leaves a valid-looking file behind.

**C. Boot-time schema verification (fail-open, never blocks boot).**
`app.py:_on_startup` calls `verify_required_pg_tables(pool)` when PG is
active. Missing tables → CRITICAL log naming the missing tables + the
restore command (`python scripts/pg_backup.py restore --latest`); the
server still boots (SQLite-side features work) but chat/settings/document
history is broken until restored. A pool failure returns None = "unknown",
not "all present".

**D. Operator CLI (`scripts/pg_backup.py`).**
`backup` (one-shot dump now), `restore --latest|--file <name> [--dry-run]`
(via `pg_bridge.restore_database`, `--clean --if-exists` — only the dumped
tables are touched), `list`. Loads `.env` from CWD; never prints the DSN
userinfo.

**E. Config is live-read, never raises** (mirrors `get_hitl_config`):
ConfigStore keys `backups.pg.enabled` / `backups.pg.retention`; env
kill-switch `KAZMA_PG_BACKUP_ENABLED=0`. Tests:
`python -m pytest tests/test_pg_backup.py`.

**F. Universal backup — "never left anything behind"**
(`kazma_core/backup/universal.py`). One unified backup that backs up
literally everything: every `*.db` in `kazma-data/` (WAL-safe via
`sqlite3.backup()` API), every non-DB dir (attachments, document-store,
workspace, exports, vectors — via `_robust_copytree` that skips ephemeral
files like LibreOffice cache), and the Postgres dump (delegates to
`pg_backup.perform_pg_backup`). Produces `manifest.json` + retention-capped
(default 7). Wired into the 24h `native_backup` handler (auto) +
`POST /api/backup/now` (manual, background task) + Settings → Backup tab
(animated progress bar polling `GET /api/backup/status` every 2s).
Delete/archive/download: `DELETE /api/backup/{name}`,
`POST …/archive` (zip), `GET …/download`. The per-file copy MUST be robust
(`_robust_copytree`) — `shutil.copytree` aborts on one vanishing file and
loses the entire `document-store`. Do NOT revert to `copytree`.

### 22. Agent Skills Ecosystem (`kazma-core/kazma_core/agent_skills/`)

Kazma is a first-class citizen of the open **agentskills.io** `SKILL.md`
ecosystem — it can install, run, and publish skills that also work in Claude
Code, Cursor, Codex, etc. Four modules; understanding them is essential before
touching skill loading.

**A. Parser + spec (`parser.py`).** `SKILL.md` = YAML frontmatter + markdown
body. Required fields per the spec: **`name`** + **`description`** only.
`version` is OPTIONAL — do not re-introduce a "missing required field: version"
warning (it spam-listed every ecosystem skill). `validate_manifest` enforces
name+description and is the single gate.

**B. Discovery scopes (`discovery.py`).** `skill_base_dirs()` returns
`(scope, path)` pairs, lowest → highest precedence:
`bundled` (shipped with Kazma) → `user` (`~/.agents/skills` + Kazma/Claude/
Cursor compat dirs) → `project` (`<root>/.agents/skills`, `skills/`, …).
Project overrides user overrides bundled on name collision. The `bundled`
scope is `kazma_core/agent_skills/bundled/` — 3 Kazma-native starter skills.

**C. Integrity — two paths, do not flatten.**
- **User/project skills** (`integrity.py`): HMAC-SHA256 signed at install time
  (keyed by `KAZMA_SECRET`); activation verifies checksum+signature, **fail-closed** on tamper, warn-only on unsigned.
- **Bundled skills** (`catalog._verify_bundled_skill`): verified against the
  committed `bundled/checksums.json` (SHA-256 per skill); a bundled skill NOT
  listed in the manifest fails closed. Adding a bundled skill ⇒ regenerate
  `checksums.json` (see the generator at the bottom of the bundled dir's git
  history) or it won't activate.

**D. Activation always fences the body** (`catalog.format_skill_activation`):
the SKILL.md body is wrapped in `format_untrusted_block(source="agent_skill:…")`
— it is GitHub-sourced text, data-not-instructions. Never inject a skill body
raw into the system prompt.

**E. Install with no Node/npm** (`installer.py`). `install_from_any(source)`
handles `owner/repo`, full GitHub URLs, `tree/branch/path`, git URLs, local
paths, and `npx skills add …` strings — downloads the GitHub zipball via httpx.
`rglob("SKILL.md")` so multi-skill repos install all skills. One HITL approval
covers an install (the user is the gate for what enters the system).

**F. Marketplace search** (`tools.search_agent_skills` + `/api/skills/marketplace/search`):
GitHub `topic:agent-skills` repository search (GITHUB_TOKEN-aware for rate
limits). The `/skills` page has a Marketplace tab (debounced search + one-click
install). Do not build a parallel registry — the GitHub topic IS the index.

### 23. Windows asyncio.subprocess trap (`SelectorEventLoop`)

The Kazma server runs a **`SelectorEventLoop`** (`kazma_core/eventloop.py` —
forced because psycopg async refuses the Proactor loop, and Postgres-backed
checkpoints must persist on Windows). On Windows the selector loop **does not
implement subprocess transports**: `asyncio.create_subprocess_exec` /
`create_subprocess_shell` raise `NotImplementedError`. This is a recurring
footgun — every tool that spawns a subprocess must avoid the bare asyncio API.

**The rule:** on the server loop, spawn subprocesses via
**`asyncio.to_thread(subprocess.run, …)`** (blocking, bounded) or
**`asyncio.to_thread(subprocess.Popen, …)`** (non-blocking start). The MCP
manager (`mcp/manager.py`) already has an explicit `NotImplementedError` →
Popen fallback — mirror that.

**Known-correct sites (keep them):**
- `system/installer.py`, `system/runtime_manager.py`, `telemetry.py`,
  `models/discovery.py`, `agent/tool_registry.py:shell_exec` — all use
  `to_thread` + `subprocess`.
- **Playwright**: the browser tools (`kazma_skills/native/browser_automation`)
  run on the **sync API inside `to_thread`**; the heavier crawl/fetch paths
  (`knowledge_ingest.py`, `read_url.py`) route through
  `kazma_core/playwright_loop.py` (a dedicated ProactorEventLoop daemon thread).
  Do NOT revert these to the async Playwright API on the server loop.

**Symptom of a regression:** a tool reports `error=False` in 0 ms while doing
nothing, and the log shows `Task exception was never retrieved …
NotImplementedError` from `playwright/_impl/_transport.py` or
`asyncio/base_events.py`. That means someone re-introduced
`create_subprocess_exec` on the server loop.

### 24. Import Integrity + Web Acquisition SoT + CSRF/Rate-Limit (2026-08-14 audit round)

**A. Import-integrity gates — `tests/test_imports.py` (deletion SOP).**
- Two tests: `test_every_product_module_imports` (imports every module of
  every `kazma-*/kazma_*` package) and `test_no_dangling_kazma_import_references`
  (AST scan: every `kazma_*` import reference must resolve to a real file;
  imports inside try/except are exempt as deliberate degradation paths).
- Born from the crawl.py incident: a module deletion left a dangling import
  in `web_acquire/__init__` — py_compile passed (syntax-only), no test
  imported the package, production research broke at first use
  (`ModuleNotFoundError`). **Rule: deleting a module requires green
  `tests/test_imports.py` in the SAME commit, and importers removed in the
  same change.** Optional pre-commit hook: `.pre-commit-config.yaml`.
- `tools/read_url.fetch_full_text` is the PUBLIC ladder entry point
  (alias of `_fetch_full_text`); the `web_acquire.fetch` façade and KB
  ingest fallback use the public name — do not import the underscored one.

**B. Web acquisition — ONE egress stack.**
- SoT ladder: `tools/read_url._fetch_full_text` (SSRF-validate → Firecrawl if
  key configured → Jina only when explicitly opted in via
  `KAZMA_JINA_READER=1` → httpx via scraping client → hard-page recovery
  Firecrawl→Jina→Playwright) built on `proxy.client.get_scraping_client`
  (proxy provider + rotating UA pool); `web_acquire` is the façade
  (`fetch_text`/`search`/`rank_urls`/profiles) used by research pipeline, KB
  ingest, and readiness.
- `crawl_site(profile=...)` accepts named cap presets (`research_brief` |
  `research_deep` | `kb_site` | `single_page`); explicit args win; hard env
  ceilings (`KAZMA_CRAWL_MAX_PAGES` etc.) still clamp.
- Deliberate direct-API exceptions (never route through the scraping proxy):
  Jina Reader, Firecrawl, loopback SearXNG. The gateway attachment URL fetch
  DOES route through `get_scraping_client_sync` (SSRF-per-redirect kept).

**C. CSRF + rate limiting (`kazma_ui`).**
- `csrf.py` middleware: non-GET `/api/*` with a mismatched Origin/Referer
  host → 403. `Authorization`-header requests and origin-less clients
  (curl/CLI/webhooks) are exempt; `X-Forwarded-Host` honored for proxies.
  **Use `request.url.hostname` — Starlette's URL has no `.host`** (the
  2026-08-14 every-browser-POST-500 crash). Tests must build REAL ASGI-scope
  Requests (`tests/test_csrf.py`) — MagicMock auto-attributes and hides
  exactly that bug class.
- `rate_limit.py`: per-principal sliding window (cookie > Authorization > IP)
  on chat stream / voice / research sessions / swarm dispatch / system flush.
  Active ONLY when auth is enabled (never demo mode); live ConfigStore
  `api.rate_limit.<bucket>_per_minute`; env `KAZMA_RATE_LIMIT_ENABLED=0`.

**D. Research sessions.**
- `suppress_chat_recording()` (ContextVar, research_session.py): the deep
  pipeline wraps BOTH its search gathers in it — sub-queries must NOT mint
  standalone `record_chat_research` rows (the panel flood: one deep run
  showed 10+ "1 sources" rows). Chat-initiated searches still record.
- Sessions support delete + archive (idempotent `archived` column via ALTER
  TABLE; `list_sessions` EXCLUDES archived by default — pass
  `archived=True`/`None` explicitly for the Archived tab / everything).
  The panel routes `session:`-prefixed ids to `/api/research/sessions/*`
  mutation endpoints — sessions live in `research_sessions.db`, NOT the
  swarm TaskStore.
- Chat-tool snapshots persist up to `_CHAT_RESULT_MAX` (200K — the old
  [:500] cap discarded full output at write time); a longer re-query
  refreshes the stored summary, a shorter one never clobbers it.

**E. Deep canary + CI gate.**
- `GET /health/deep` (kazma_ui/health.py): one REAL roundtrip per critical
  path — ConfigStore write→read→delete, a real `recall()`, workspace
  binding, research-stack readiness, brain entry-point imports, DB ping.
  503 when any check fails; TTL-cached 30s (aggressive polling is free).
  Poll it in ops dashboards — it exists to catch SILENT no-ops (the
  recall-NameError class), which structural checks cannot see.
- CI (`ci.yml`) GATES on the full suite: `python scripts/fast_test.py --chunks
  4 --chunk-timeout 1500` (crash-tolerant chunked serial pytest over ALL
  testpaths — the package suites were previously orphaned by `pytest tests/`),
  `-m "not slow"`, per-chunk `--timeout=120`. Compile-check (py_compile over
  every repo `.py`) and `node --check` over static JS are separate GATES.
  Never reintroduce `|| true` on the test step — that single flag let every
  regression class in this audit ship silently. Lint/bandit remain advisory
  (`--exit-zero`/`|| true`) until their backlogs are triaged. Known CI blind
  spots (deep-audit 2026-08-19, patched same day): the G1 commitment-latency
  file's `slow` marker was removed (it runs in ~6s and was the ONLY slow file,
  so `-m "not slow"` silently excluded it), and CI now installs the light
  pure-wheel deps (pillow/pymupdf/sqlite-vec/pypdfium2/numpy — without numpy
  the belief-graph PPR silently runs its degraded uniform-seed path) that the
  `.[test]`-only install left `importorskip`ing/degrading. Still blind:
  Playwright **one smoke** is a separate CI job (`tests/e2e/test_smoke.py`,
  polls `/health/live`); the full e2e matrix stays out. Torch-bearing
  `rag` extra is still too heavy for CI.

### 25. Long-Task Continue Protocol & Partial Pause (`agent/long_task.py`)

Born from the 2026-08-19 Telegram desync ("Saved. Ready…" acks instead of
executing commands after a mission ended Partial) — full diagnosis in
`docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md` §20.

**A. The continue-context injection is GATED by reply shape.**
- `consume_continue_context(thread_id, user_text=…)` returns the stored
  salvage ONLY when `is_continuation_reply(user_text)` is true: ≤8-word
  replies matching proceed/continue/yes/ok/go-on/keep-going/wrap-up
  (Arabic كمّل/اكمل/تابع/نعم/يلا/زين included).
- The stored `long_task.continue.{thread}` context is cleared on EVERY
  consume call — gated or not — so a stale "do not re-do / final report"
  directive can never leak into a later turn. The salvage itself is
  already in the conversation history (the user saw the Partial reply).
- The injection header carries an explicit escape clause ("if the user's
  latest message is a NEW task, ignore this directive") as defense in
  depth. Only injection site: gateway `agent_handler/store.py` — keep the
  `user_text=` argument if a second site is ever added.

**B. A Partial PAUSES the long task.**
- The gateway's recursion-Partial handler calls `pause_long_task()`:
  `long_task_status()` then reports `active: False` with baseline budgets
  (no mission framing/ceilings), `is_mission_mode()` defuses, and
  `consume_long_task_turn()` stops eating follow-up turns while paused.
- The paused record survives for `/long status` until TTL expiry; a fresh
  `/long` re-enable always works. The Partial reply tells the user the
  state machine ("reply **Proceed** to wrap up, or send a new task and it
  runs fresh").
- `/long off` (or `/abort`) remains the immediate manual clear on any
  build.

### 26. Default-Deny Boundaries (2026-08-29 security audit)

Full report and reproductions: the audit artifact and
`tests/test_audit_2026_08_29_regressions.py`. Four boundaries were
default-OPEN; they are now default-CLOSED, and CI keeps them that way.

**A. Peer address is not a credential behind a proxy.**
- `_should_auto_issue_cookie()` mints an admin session for a loopback client
  with no credential — that is what makes localhost use work with no login.
  Behind a same-host nginx/Caddy, `request.client.host` is `127.0.0.1` for
  EVERY internet visitor, so all of them inherited it (finding F-01).
- `KAZMA_TRUSTED_PROXIES` declares the proxy. `X-Forwarded-For` and
  `X-Forwarded-Proto` are honoured from those addresses ONLY; peer trust
  switches off entirely when a proxy is declared. Same rule in
  `websocket_is_authenticated()` — its Origin guard passes when the header
  is absent, so a curl client used to get the agent.
- Anything keying state per client (rate limit, login throttle, audit log)
  must call `auth.client_address(request)`, never `request.client.host`.
- The variable is the operator's CLAIM about the topology, and a wrong claim
  used to fail open (under Docker the proxy is the bridge IP, so `127.0.0.1`
  is the natural guess and it is wrong). `_note_forwarded_headers()` settles
  it from the traffic: an `X-Forwarded-*` header from an undeclared peer
  latches `undeclared_proxy_detected()` and revokes peer trust for the
  process. `proxy_health()` surfaces the state on `/api/auth/status`.
  The latch never clears at runtime — it may only close doors, never open
  them, which is also why spoofing the header gains an attacker nothing.

**B. HITL default-denies.**
- `requires_approval()` ends on the `TOOL_TIERS` classification, not on a
  name list. An unclassified tool is GATED. **Every tool you register needs
  a tier** — `read` / `write` / `danger`; anything destructive, outbound, or
  credential-touching is `danger` and also belongs in
  `CANONICAL_DANGER_TOOLS` + `kazma.yaml`.
- A configured `require_approval_for` list ADDS to that floor. It can no
  longer un-gate `shell_exec` by omission.
- CI: `test_every_registered_tool_has_a_tier`, `test_danger_tools_are_gated`.

**C. Allowlisting a binary is not allowlisting what it runs.**
- `shell_exec` vets `argv[0]` and rejects shell metacharacters, but a bare
  program name is not path-shaped and `find`'s `+` terminator sidesteps the
  `;` rejection — `find . -exec whoami +` walked past the allowlist (F-03).
- `_EXEC_CAPABLE_ARGS` rejects per-binary flags that execute another
  program (`find -exec`, `git --upload-pack`/`-c`, `tar
  --use-compress-program`, …). Add an entry when you add a binary.

**D. Secret masking recurses.**
- `settings.mask_deep()` walks dicts, lists, and JSON-encoded strings.
  The old two-level version skipped lists, so `providers.list` shipped six
  live API keys in the clear (F-02). Key matching is on `.`/`_` token
  boundaries — a substring test made `pat` match `selected_path`.
- New API surfaces that echo config must go through `mask_deep`.

**E. Nothing blocking on the event loop; nothing fire-and-forget.**
- A sync `sqlite3.connect` inside `async def` pins the loop that serves
  every SSE and WebSocket stream. Drop `async` (FastAPI threadpools sync
  handlers) or wrap in `asyncio.to_thread`.
- `asyncio` holds only a WEAK reference to a task, so a discarded
  `create_task(...)` can be garbage-collected mid-run, silently. Use
  `kazma_core.background.spawn_background(coro, name=…)`.
- CI: `test_no_blocking_db_driver_in_async`, `test_no_bare_create_task`.

**F. Errors do not carry internals.**
- API handlers return `kazma_core.errors.safe_error(exc)` — a stable code
  plus a correlation id, with the real exception logged under that id.
- 4xx **validation** paths use `validation_error(exc)` instead: the message
  is the caller's answer, and replacing it with a code makes the API
  unusable. Redaction applies either way.

**G. Fenced tool output.**
- Fetched pages, search results, saved research chunks and MCP resource
  bodies go through `prompt_fence.fence_untrusted()`. They are the largest
  source of attacker-controlled text in the system and used to reach the
  model raw. CI: `test_no_unfenced_web_tool_output`.

**H. One writer for the procedural recorder.**
- `_record_procedural_outcome` used to spawn a `daemon=True` thread PER TOOL
  CALL, each opening its own SQLite connection and running the full
  `ensure_primary_schema` (DDL + FTS5 rebuild probes) before writing one row.
  Concurrent record threads crashed the interpreter with a Windows access
  violation whose traceback moved between `_ensure_fts5`,
  `ensure_primary_schema` and `config_store.get` — which is why it looked
  like three unrelated bugs and was first mis-diagnosed as a ConfigStore
  shared-connection problem.
- Measured on `tests/test_truncation_retry.py`: **4/10 runs crashed before,
  1/80 after**. Disabling the threads entirely gave 0/10, which is what
  identified the concurrency between them as the fault. Three narrower
  hypotheses were tested and are NOT the cause, each still crashing at
  roughly the same rate: draining the threads at exit, giving ConfigStore a
  per-thread connection, and serialising `ensure_primary_schema`. Non-daemon
  threads did not help either, so it is not a teardown race.
- Now a **single worker thread** drains a bounded queue: exactly one thread
  ever touches these databases, the schema is ensured once per process
  instead of once per tool call, the queue drops rather than blocks when
  full, and `atexit` drains it. Do not reintroduce a thread per call.
- The residual after that was the actual root cause, and the codebase had
  already found it once: `reset_config_store()` **closed** the sqlite handle
  out from under any background reader. Its own docstring named the
  procedural recorder and said harnesses should pass `close=False` — and not
  one of the 17 call sites did, so the guidance protected nothing. Closing is
  now opt-in (`close=False` is the default), and the recorder reads
  `read_memory_cfg()` on the CALLER's thread so the worker never touches
  ConfigStore at all and cannot be raced by a reset regardless.
- General rule this leaves behind: **a default that every caller must
  override to be safe is the wrong default.** If you add a background reader
  of a shared store, it must not hold that store across a lifecycle reset.
- Debugging note: in a faulthandler dump the faulting frame is the least
  useful part. The signal is which OTHER threads are alive and what they are
  inside.




### 27. Backups that report success (2026-08-29)

Three failures found the same day, all the same shape: the system kept
running, kept logging "complete", and stopped protecting the data. Each
is now detected in seconds and says what to do. `tests/test_backup_
silent_failures.py` holds the reproductions.

**A. A Drive remote that reads fast and cannot write at all.**
- A Google **service account has no Drive storage quota of its own**. It
  will list a folder shared with it in milliseconds, and fail every upload
  with `403 storageQuotaExceeded`. Only a Shared Drive escapes this, and
  Shared Drives need Workspace — a consumer account cannot have one.
- restic takes a **lock before it will even LIST**, so on such a remote
  `restic snapshots` does not fail: it retries with exponential backoff
  for about fifteen minutes and presents as a hang. Two 600-second probes
  were spent proving "still failing" before the cause was visible.
- `restic_repo.remote_writable()` now PUTs a probe object before any
  restic call against an `rclone:` repo (cached 5 min). 900s of silent
  retrying became a 3.1s error naming the credential. `_run` refuses to
  invoke restic when it fails and raises `backup.restic_remote_read_only`.
- Any offsite check that only reads is worthless. Prove the write.
- **The probe had a hole exactly where it was about to matter.**
  `remote_writable()` returned `(True, "")` for anything that was not an
  `rclone:` URL, so an `s3:` repository was assumed writable and never
  tested. Migrating off Drive to object storage would therefore have carried
  the same blind spot to the new destination on day one. `s3:` repos are now
  probed with a SigV4-signed PUT + DELETE built on the standard library — no
  boto3, because a check that runs when the backup path is already suspect
  should not depend on anything that path does not already need.
- The probe writes under the **`locks/` prefix**, and that is not arbitrary.
  An append-only backup key must still be allowed to delete locks, because
  restic writes one at the start of every run and removes it at the end; a
  key with no delete permission at all accumulates stale locks and refuses
  to back up a few runs later. Probing `locks/` exercises exactly the
  permission the policy is meant to grant and leaves nothing behind. The
  matching bucket policy is: `PutObject`/`GetObject`/`ListBucket` on the
  whole prefix, `DeleteObject` on `locks/*` only.
- `tests/test_restic_s3_write_probe.py` stands up a local HTTP server that
  **recomputes the signature the way S3 does** rather than trusting it.
  Unexercised signing code fails as a 403 against the real bucket and gets
  blamed on the bucket policy.

**B. A missing passphrase is not a config note.**
- `ensure_password()` returning empty used to log one INFO line and skip
  every snapshot. It did that for four hours while backups reported
  success, because the local dump really had been written.
- `alert_missing_password()` is critical, and deliberately silent when no
  repository exists yet — on a fresh install nothing is at stake, and an
  alert there teaches the operator to ignore the one that matters.

**C. Mechanisms that only speak when they break cannot be told from
mechanisms that never run.** A successful restic snapshot logged nothing;
a completed restore logged nothing. Both now log one line, and
`observability/firing_ledger.py` counts them.

**The firing ledger** (`run_weekly_sweep`, scheduled from the memory
worker) turns "unproven: N" from a hand-computed number into a measured
one. Two lessons from its own first day, because it made both mistakes:
- It read only `kazma.log` and reported ZERO guard restarts on an evening
  they fired. **The guard logs to its own file on purpose**, so the app's
  logging config cannot silence it — which is exactly why a ledger that
  reads one file is worse than none. `_log_paths()` reads all of them.
- Two of its patterns matched strings that appear nowhere in the codebase.
  A signature must be copied from the emitting line, not guessed from the
  mechanism name, or the dial is welded to zero.
- It shipped unscheduled: it imported, passed its tests, and nothing
  called it. That is its own finding, happening to itself.

**Chaos injection is only real where it lands.** `InjectionTarget.
LLM_PROVIDER` is injected INSIDE `resilient_chat`'s attempt loop, not
around the function: a failure raised outside skips both the retry and
the failover and proves only that exceptions propagate.
`ChaosInjectionError` carries `.transient` (408/429/5xx), because the
retry paths decide by asking, and an injected 503 that claims to be
permanent skips the very retry it was injected to exercise.


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

> **RULE (user directive, 2026-08-15): NEVER start or restart the Kazma server.**
> The user ALWAYS starts it themselves. Do not run uvicorn, do not kill the
> running server to "apply changes", do not restart it as part of any task.
> After code changes, just tell the user a restart is needed — they will do it.


```powershell
# Kill existing server
Get-Process -Name python -ErrorAction SilentlyContinue | Where-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId=' + $_.Id)).CommandLine -like '*uvicorn*kazma*' } | ForEach-Object { Stop-Process -Id $_.Id -Force }

# Start server (background) — ws-ping flags are the Turn Delivery V2
# server-side death certificate for black-holed sockets (KD-7); keep them.
cd 'G:\GitHubRepos\kazma'; & '.venv\Scripts\python.exe' -m uvicorn kazma_ui.app:create_app --factory --host 127.0.0.1 --port 9090 --ws-ping-interval 20 --ws-ping-timeout 20
```

## Testing & Validation

- **Compile check (Python):** `& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"`
- **Syntax check (JS):** `node --check "<file>"`
- **Run tests (single file):** `& '.venv\Scripts\python.exe' -m pytest <path> -v`
- **Fast FULL suite (use this — ~5 min, not 20+):**
  `python scripts/fast_test.py`
  Crash-tolerant chunked runner: file-chunks run as independent serial pytest
  processes; crashed/empty chunks are retried per-file; poison files are
  reported. It PRINTS the per-chunk FAILURES tracebacks (deep-audit
  2026-08-19 — they used to be captured and discarded, leaving CI-only
  failures undiagnosable) and treats pytest exit 5 ("no tests collected",
  i.e. module-level importorskip like the Playwright e2e suite on a
  `.[test]`-only install) as a benign skip, not POISON. Do NOT use
  pytest-xdist here — worker segfaults (native lib) make
  it silently drop ~half the suite. The serial monolithic run intermittently
  segfaults and takes 20+ min.
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
- `docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md` — Deep-structure audit (22 findings, change-impact map, CI recovery, Telegram desync §20)
- `docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md` — Latest production audit
- `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` — Document cert report
- `docs/plans/done/DOCS_CONSOLIDATION_PLAN.md` — Docs consolidation plan (completed)
- `CHANGELOG.md` — Sprint history
- Live docs only under `docs/docs/` (Docusaurus). Do not resurrect retired `docs-v2` / loose handover trees.
