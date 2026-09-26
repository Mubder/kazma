# Mission Guidance: Kazma Agent Framework

## Project Overview

Kazma is a multi-platform AI agent framework with a LangGraph supervisor brain,
swarm orchestration, cross-platform dispatch (Telegram/Discord/Slack/Web/TUI),
and an OpenAI-compatible LLM provider layer. See `docs/docs/guide/architecture.md`
(Docusaurus docs under `docs/docs/`) for the full system architecture and
`CHANGELOG.md` for recent work. Binding industrial audit (waves 0–8 shipped):
`docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md`. Historical production audit:
`docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md`. System map:
`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`.

## Package Scope

All packages are in scope. There is **no** `kazma-memory` package (retired;
V2 memory lives in `kazma_core.memory`).

| Package | Path | Purpose |
|---------|------|---------|
| `kazma-core` | `kazma-core/kazma_core/` | Agent runner, LLM provider, swarm engine, model registry, config store, IDE service |
| `kazma-gateway` | `kazma-gateway/kazma_gateway/` | Platform adapters (Telegram/Discord/Slack), agent handler, slash commands, `/ide` commands |
| `kazma-ui` | `kazma-ui/kazma_ui/` | FastAPI web app, IDE page, swarm panel, settings, SSE chat, static JS/CSS |
| `kazma-tui` | `kazma-tui/kazma_tui/` | Textual-based TUI dashboard + IDE editor screen |
| `kazma-cli` | `kazma-cli/kazma_cli/` | `kazma` CLI (`serve`, `migrate`, `ask`, ACP) |
| `kazma-skills` | `kazma-skills/kazma_skills/` | Native skill manifests + implementations |

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
- A provider with no usable key is replaced by one that has a key in
  `get_client()` — and that is announced, never silent; provider keys are
  install-scoped so it does not happen for a key that is there. Both in §38.

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
- The supervisor retry loop (`graph_supervisor.py:_call_llm_with_retry`) ONLY
  retries `LLMError` when `transient` is True — permanent errors fail fast.
  `httpx.ReadError` (mid-stream drops) MUST stay transient, or
  "stopped-thinking" forced-finalization returns. The graph is split:
  `graph_builder.py` wires nodes; `graph_supervisor.py` is the supervisor
  LLM call; `graph_tool_worker.py` is HITL + commitment; `graph_respond.py`
  is `respond_node`. Do not "fix retry" inside `graph_builder.py`.
- `friendly_llm_error()` prefixes all messages with `⚠️` and gives an
  actionable hint — never change it to return a bare string, or failures
  get mistaken for normal model replies again.

**Turn-failure surfacing — never synthesize over a broken turn:**
- When the supervisor's LLM call fails after retries, it sets
  `SupervisorState.turn_failed=True` + `error_message` (NOT a fake answer).
- `respond_node` (`graph_respond.py`) checks `state.get("turn_failed")` and
  MUST skip its synthesis LLM call when True — synthesizing a plausible
  answer over a failed turn was the root cause of the "model stopped thinking"
  symptom.
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

### 7. HITL Approval Gates (3 execution paths + 1 registry — all must stay wired)
There are **three independent HITL execution paths** and **one decision
registry** (§30). Breaking any execution path creates an unattended-danger-tool
gap. Minting a second gate from `LocalToolRegistry.execute` on the web/chat
path (H-8) reopens ghost cards. Decision truth = `hitl_gates.db`; execution
truth = LangGraph checkpoint. Surfaces render; they never infer Approved.

**A. Graph interrupt() — single-agent chat (Web SSE + Telegram/Discord/Slack)**
- `graph_tool_worker.py:tool_worker_node` calls LangGraph `interrupt()` for
  danger tools (wired from `graph_builder.py` closures — do not look for the
  gate body in `graph_builder.py`)
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
- Canonical floor, ON by default since audit 2026-09-16 F-5: CANONICAL is always
  unioned into the effective `require_approval_for`, so Settings/YAML cannot
  narrow below it. `KAZMA_HITL_CANONICAL_FLOOR=0` is the warned opt-out. (It
  was an opt-in `=1` from 2026-08-19; docs describing it that way are stale.)
  The drift warning repeats every 15 min either way.
- Bus adapters: `TelegramBusAdapter`, `DiscordBusAdapter`, `SlackBusAdapter`
- App wiring: **one** adapter if only one platform; **`FanOutBusAdapter`** when
  multiple are configured. Swarm fan-out is **tri-state** (Wave 6 H-12): `True`
  settles immediately; `False` is a vote until `expected_voters` or the deadline.
  This is **not** web `claim_gate` (first claim 200, second 409). Do not restore
  "first boolean / first approval wins" — a Discord Deny used to kill a Telegram
  Approve. NullBus = internal-only / fail-closed danger
- H-9: `is_danger_tool()` must call `requires_approval()` (tier floor), not a
  name list that can un-gate by omission
- Approval buttons resolve via `handle_callback()` on each adapter

**C. Pipeline checkpoints — swarm PIPELINE tasks** (separate from A and B)
- `engine.py:_handle_pipeline_checkpoint` + `approve_checkpoint`
- Every ending of a task goes through `SwarmEngine._finalize_task`: it saves
  the task, tells the panel, and (`_close_open_checkpoint`) closes a paused
  pipeline's checkpoint — entry, auto-reject timer, gate row. Cancel skipped
  the close and left Approve on cancelled tasks. Code that sets a terminal
  status itself must be declared in `tests/test_swarm_paused_task_endings.py`
  with how it covers those three.
- A pipeline paused before a restart is restored into history only, never
  `_active_tasks`; reject and cancel reach it there. `reject_checkpoint`
  decides "already finished?" BEFORE the handler runs — the handler marks the
  shared task failed, and judging after it left restored rejects unsaved
  (2026-09-25: four `200`s, still paused at the next boot).

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
- **`self._lock` covers the live statement, and `close()` must take it too.**
  Every read/write holds the lock across `_get_conn()` *and* the statement
  that follows. `close()` did not, so it freed sqlite's native handle under a
  thread already inside `sqlite3_step` — a use-after-free, which on Windows
  is a bare `access violation` with no traceback that no `try/except` can
  catch. It killed a full pytest run at 19% on 2026-09-20: the memory
  worker's session-purge cadence read the `auth` category on a pool thread
  while the per-test fixture closed the same store. **Any new method that
  touches `_conn` holds `_lock` for as long as the statement is live.**
  A read after a close is fine — `_get_conn()` reopens lazily.
  Repro: `tests/test_config_store_close_race.py`.
- Background cadences do not run under pytest:
  `worker_bootstrap.background_schedulers_enabled()` is False there (override
  with `KAZMA_TEST_BACKGROUND_SCHEDULERS=1`). One switch for all eight
  schedulers — the purge was simply the one that got caught.
- **GET nested vault walk is resolve-only.** `_resolve_vault_value` decrypts
  `vault://` pointers inside dicts/lists. Lazy-migrate of plaintext secrets
  is **only** for the exact string key `get()` was called with. Nested
  `api_key` fields inside `providers.list` must not share
  `cfg:providers.list.api_key` — that ping-ponged `vault.store` on every
  registry read and stalled SSE (2026-09-08 `_No response received._`).

### 9. SwarmEngine Module Structure (P2-1 refactor — 3 extractions)

The original 1878-line `engine.py` god class was split into focused modules.
SwarmEngine remains the central orchestrator with thin delegates for backward
compatibility. **All public API methods and constructors are unchanged.**

| Module | Responsibility | When to open it |
|--------|---------------|-----------------|
| `engine.py` | Dispatch, handoff, task lifecycle, worker registry | Always — the orchestrator |
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
- **A relative path an agent tool is given means the active workspace**
  (`binding.resolve_tool_path`), the directory `shell_exec` runs in — never
  the server process's CWD. Every file tool, `check_path_access` and the
  skill tools resolved against the CWD until 2026-09-26; it only looked
  right because the live workspace IS the install folder. After a Switch
  Repo, `file_read("README.md")` read the install's README. Gate:
  `tests/test_tool_paths.py` (every tool that builds a path from its own
  parameter, behavioural runs from a CWD holding a same-named decoy).
  `file_delete` refuses the workspace root and its ancestors.
- **File tools keep a file's own line endings**
  (`tools/text_newlines.py`): read exact, write `newline=""` in the file's
  existing style; a new file takes the platform default. Text mode turned
  every patched LF file into CRLF on Windows, and the checkpoint rollback
  wrote CRLF files back as `\r\r\n`. Tests: `tests/test_text_newlines.py`
  (bytes, both styles).

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
  chat path (`sse_chat/` package, so workspace switches take effect immediately),
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
- The 3 supervisor injection sites (`agent_runner.py`, `sse_chat/` package,
  gateway `graph.py`) all use the fence. Keep them in sync if you add a 4th.
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

- **`engine.get_autoscaler()` is a lazy singleton.** It loads
  `swarm_templates.json` once on first access. The dispatch fallback that calls
  `maybe_scale()` is in `dispatch_inner.py` — it only fires on
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
queue drained by a worker, plus **eight** fire-and-forget asyncio scheduler
loops started from `start_memory_worker()`. These were the subsystem that
silently lost its backup/export runs (the routines existed but nothing called
them) — read this before touching the background memory path.

**A. `start_memory_worker()` is the single boot entry — it starts EVERY
scheduler.** Called from `app.py` startup (wrapped in try/except so it
can't block boot). It registers handlers + `start_worker()` + all
`_start_*_scheduler()` calls in that function. If a new scheduler is added,
register/start it HERE or it will never run (the exact gap that previously
left backups/export inert). Current boot list:

- `_start_macro_sleep_scheduler()`
- `_start_backup_export_scheduler()`
- `_start_reconsolidation_scheduler()`
- `_start_commitment_gc_scheduler()`
- `_start_session_purge_scheduler()`
- `_start_daily_digest_scheduler()`
- `_start_firing_ledger_scheduler()`
- `_start_restore_drill_scheduler()`

**B. Distinct cadences (do not collapse them):**
- **6h `macro_sleep`:** rule-based tier demotion/promotion (TTLs,
  importance, access) and archival (`macro_sleep.py:run_macro_sleep`).
  First sweep 60s after boot. **Archival is cold storage, never deletion**
  (2026-09-26): `_ARCHIVE_EPISODE_SQL` moves the tier and fills an empty
  summary with a stub; the question, the answer and the vector stay, and
  no statement may set episode text to NULL (gate in
  `tests/test_memory_nothing_lost.py`). Only rows stale on BOTH clocks are
  archived (created past the TTL *and* not recalled within it — every chat
  turn is importance 1 and can never be promoted, so the recall clock is
  what keeps an in-use memory). Recall searches every tier in
  `vector_engine.RECALLABLE_TIERS`; an archived hit ranks about one place
  behind an equally matching active one (`memory.v2.archived_recall_weight`,
  0.98 -- fused scores are reciprocal ranks 1.6 % apart, so 0.7 would be ~25
  places, i.e. never found), and a recalled archived memory returns to
  episodic at the next sweep. Moves reach the state mirror; a remote vector
  index is re-tagged, never told to delete. There is no decay score:
  `compute_retention` was removed 2026-09-23 (per-second λ, no reader).
- **6h backup/export** (`_BACKUP_EXPORT_INTERVAL_HOURS = 6`, not 24):
  enqueues `native_backup` + `nightly_export` + `native_pg_backup` →
  native `sqlite3.backup()` of both memory DBs (`backup.py`) + JSONL/GraphML
  dumps (`export.py`) + a filtered `pg_dump` of Kazma's Postgres
  shared-state tables (§21). First sweep 120s after boot. Kept separate
  from macro_sleep so a slow disk on backup can't stall decay.
- **24h reconsolidation:** `global_reconsolidation` (dedupe + re-embed of
  beliefs, subject-hash partitioned).
- **15-min commitment GC:** TTL expiry + tiered retention (§20). Every
  sweep on this cadence is one entry of `_MAINTENANCE_SWEEPS` (commitment GC,
  artifact GC, HITL-gate TTL, memory task-queue purge, swarm task retention —
  `swarm.task_retention_days`, default 30, 0 keeps all — the supervisor
  watch, §39, memory vector repair, memory recovery and memory turn
  reconcile, §15F), run by ONE isolated
  runner so a failing sweep never stops the rest. A new periodic cleanup is a
  new entry there, never a new loop (`tests/test_swarm_task_retention.py`).
- **Session purge, daily digest, weekly firing ledger, restore drill:**
  started here too; do not assume "the four original loops" is the set.
All `enqueue_task(...)` loops: the durable worker drains the actual work, so
a failed enqueue cannot kill the cadence and a failed handler is
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

**F. Every memory findable (2026-09-26, `docs/plans/MEMORY_NOTHING_LOST_PLAN.md`).**
Measured on live that day: meaning search compared episodes inside an
unordered `LIMIT` slice (the 60 newest of 300 were never searched), facts
inside the 400 "most important", fact meaning search ran only when keywords
came up short, and archiving had erased 76 memories.
- **Exact meaning search, every row** (`vector_engine.py`):
  `vec_distance_cosine` in SQL over every comparable row (same size, same
  model; NULL/'' version = legacy same model), NumPy chunked fallback, zero
  vectors excluded, read-only (no temp tables). Gate: no `LIMIT` in a
  candidate fetch unless it orders by distance.
- **Facts** (`recall._recall_beliefs`): meaning search always runs; the score
  is weighted RRF (`_BELIEF_CHANNEL_WEIGHTS`: meaning 2, keyword/bridge 1,
  graph walk 0.5 and capped to its top results -- it is seeded by the same
  keywords and reaches every fact about "user") times a standing band of at
  most +5 % (`_STANDING_BAND`). A multiplier on RRF is worth ranks, not
  percent: keep bands small.
- **Vector repair** (`reembed.run_vector_repair_pass`, 15 min, ~20 s): no
  vector, wrong size or another model's vector -> re-encoded in place,
  newest first; with nothing to repair it never loads the model.
  `global_reconsolidation` calls the same function.
- **Recovery of erased rows** (`rehydrate.run_rehydrate_pass`, 15 min):
  sources are earlier backups of the memory DB (a same-row copy whose text
  reproduces the stub), the chat store + spool and checkpoint history
  (`chat_history.py`, both backends), `noted` beliefs, `memory_store` tool
  calls (resumable scan), the knowledge library. Anything but a backup must
  reproduce the episode id (`dual_write._episode_id` /
  `swarm_bridge.bridge_episode_id`) AND the stub (`_archive_stub`, checked
  against the old SQL). Sources must agree or nothing is restored; text is
  never overwritten; an unreadable source leaves the row pending (an outage
  is never a verdict). Verdicts live in `metadata.rehydrated` /
  `metadata.rehydrate`; memory health shows them (`memory_findability`).
- **Every turn reaches memory** (item H): `kazma_ui.turn_runtime.close_turn`
  -- the closer every transport runs -- hands a finished turn (graph
  terminal, not paused, and the `_post_turn_memory` record's turn index
  equal to the state's) to `consolidator.remember_turn`, once per turn.
  Nothing else calls it or `_schedule_post_turn_memory` (gate in
  `tests/test_memory_every_turn.py`). Until 2026-09-26 only the gateway
  handler did, so no web chat turn reached memory from 2026-08-08.
  `extract_turn_texts` pairs a question with ITS answer (it used to take the
  previous turn's when there was none); the episode `turn_number` is the
  turn index (`user_turn_index`), not the iteration count that made a
  repeated question collide into one memory.
- **Turn reconcile** (`turn_reconcile.run_turn_reconcile_pass`, 15 min, ~60
  s): every chat-store turn older than 10 minutes without an episode gets
  one, with its own time, through `mirror_episode`. Episodes only: facts are
  not re-extracted from old turns (functional supersede orders by ingestion,
  so an old statement would overwrite a newer fact). Repeated questions are
  counted, erased-row stubs count as present, and the cursor passes only
  settled sessions. On live it found 1,004 of 1,174 chat turns missing.
- **Nothing repoints live memory** (item I): no product code rebinds a
  `kazma_core.paths` function or resets the shared `dual_write` writer
  (`_reset_mirror` is a test helper; gate in `tests/test_memory_every_turn.py`).
  The Dashboard's golden eval did both inside the live server, so any turn
  written during -- and, through the writer left on its temp file, after --
  a run was stored in a file nobody read. It now seeds and recalls on a
  private database (`recall(conn=...)`), refuses the live one, and runs off
  the event loop.
- **Conversation history has one reader** (`memory/chat_history.py`): the
  past-chats fallback and the recovery read Postgres `kazma_chat_sessions` or
  SQLite `sessions`, overlaid with the save spool (`paths.chat_spool_db`, the
  web UI's rule too), tenant-filtered, scored in SQL over every session. The
  fallback used to open only the SQLite file -- on Postgres a July leftover.

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

Server lifecycle status notifications — pushes a status update when the
server starts, restarts, shuts down, or fails to boot, so an operator can
tell from chat when something went wrong (hung boot, a crash emitting no
shutdown message, a bad bot token, etc.). Three invariants must hold:

**A. Notifications route through the SwarmMessageBus — no parallel path.**
`notify_lifecycle(event)` sends a `BusMessage` through the adapters
selected by `notifications.ops.channels` (same Settings checkboxes as
ops alerts; empty = every configured platform). `bus_send_targets()`
filters a `FanOutBusAdapter`; `telegram-group` is not a bus adapter and
uses the ops-alerts group route. Do NOT construct new adapters or
introduce a second recipient config. `NullBusAdapter` (no platform
configured, or under pytest via `_skip_real_adapters`) silently drops
the message; the feature self-disables.
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
Postgres is configured. Document **metadata** (documents/versions/blobs)
defaults to SQLite and moves to `repository_pg.py` when
`KAZMA_DOCUMENTS_METADATA_BACKEND=postgres|auto` and the pool is up. Readiness
must report single-replica for metadata **whenever the SQLite backend is
active** — never claim multi-replica HA from the availability of the Postgres
path alone, only from the backend actually in use.

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
`KAZMA_DOCUMENTS_METADATA_BACKEND=postgres|auto` and pool is up. GC is
backend-agnostic: `retention._mark` dispatches to `repository.gc_mark`, which
both backends implement, so mark/sweep runs on Postgres metadata. The old
`gc_postgres_metadata_sql_port_pending` skip is gone and
`tests/test_leftovers_except_g.py` asserts it stays gone. Audit works on both
backends.

**G. Certification.**
`scripts/certify_documents.py` + `tests/test_document_certification_phase10.py`
+ `hostile_corpus.py` / committed `tests/fixtures/documents/hostile_manifest.json`.
Keep CLI gates and pytest groups honest (architecture/a11y/crash matrix are
pytest; CLI has NOT RUN placeholders for soak/Postgres/external review).

**H. Arabic / RTL — one module, one shaping pass, one fold.**
`documents/arabic.py` is the ONLY home for Arabic text policy. Do not
reintroduce a codepoint-block regex, a second shaping routine, or a
call-site-local normalization.

- **Direction** is decided by Unicode **bidi class** (`R`/`AL`), never by a
  block regex — the regex missed Hebrew/Syriac/Thaana/N'Ko and Arabic
  Extended-B, and counted harakat and Arabic-Indic digits as letters.
  `is_rtl_dominant` has **no** "any RTL char near the start" escape hatch; that
  clause made the threshold dead code and flipped English reports to RTL.
- **`shape_arabic` vs `rtl` are different questions.** `rtl` is "lay the page
  out right-to-left"; `shape_arabic` is "this contains complex script, a visual
  engine must shape it". `PdfEngine.render` gates the DOCX→LibreOffice route on
  **`shape_arabic`** — gating it on `rtl` stranded every mixed-language
  document on the degraded reportlab path.
- **Shape once per paragraph.** `arabic.shape_spans()` resolves bidi embedding
  levels over the whole paragraph, splits styled spans at level boundaries,
  applies UBA rule L2, and only then reshapes. Never call the shaper per
  markdown span: that reorders each fragment internally, emits them in logical
  order, and renders the sentence inside-out with the spaces eaten.
- **Search folding is applied on BOTH sides.** `fold_for_search` feeds the
  `folded` column of `knowledge_chunks_fts` at index time AND the query at
  search time (`knowledge.py:fts_search`). A fold on one side only is worse
  than none. SQLite's `unicode61` treats harakat (`Mn`) as separators, so
  vocalized Arabic indexes as single letters without this.
- **`to_logical()` runs in `parsers/common.IRBuilder.add_page`**, before limits,
  quality assessment and chunking. It NFKC-folds Arabic presentation forms back
  to base letters so a legacy visual-dump PDF is recovered without OCR. Pages
  whose character *order* is also reversed still fail the quality gate and
  still escalate — that is the correct remaining OCR case.
- **Fonts are chosen by verified coverage** (`documents/fonts.py`), not by first
  path that exists. An RTL job requires presentation-form cmap coverage
  (`U+FB50-FDFF` / `U+FE70-FEFF`); base-block coverage renders tofu.
  `_setup_fonts` raises `DocumentRenderError` rather than emitting a blank
  Arabic PDF behind a warning string.
- **IBM Plex Sans Arabic (OFL) is vendored** in `documents/assets/fonts/`
  (Regular + Bold + `OFL-IBM-Plex.txt`) so generated documents match the web
  UI and the Docusaurus docs on Windows, macOS and the container. Amiri
  remains a naskh fallback if Plex is removed. `KAZMA_DOCUMENT_FONT_DIR`
  overrides the directory. Precedence: **Plex wins both directions** when
  present; Amiri only for Arabic jobs if Plex is absent; system fonts last.
  Do not revert to "bundle-for-Arabic, system-for-Latin" — that split
  restyled English documents away from the product face. The OFL text must
  keep travelling with the fonts.
- **HTML exports inline the pinned font** as a data URI
  (`documents.render.embed_html_fonts`, default on) so an Arabic export renders
  the same wherever it is opened. That costs ~850 KB per Arabic file; the flag
  exists for deployments that serve these over the wire. English exports are
  never affected. DOCX names `THEME["font_latin"]` / `THEME["font_arabic"]`
  (both IBM Plex Sans Arabic); LibreOffice needs the family, or the PDF
  route copies the TTFs into the soffice user profile.
- **The image and CI carry the system deps.** `fonts-noto-naskh-arabic`,
  `libreoffice-writer`, `tesseract-ocr(-ara)`. Removing them from either does
  not fail a test — it silently returns the platform to blank Arabic PDFs, and
  makes `tests/test_docx_rtl_visual.py` skip instead of run.

**I. Direction is per BLOCK, not per document.**
`DocProfile.direction` is the *document* base direction — margins, section,
chrome, gutter. `DocProfile.block_direction(text)` resolves each block on its
own, and every engine must use it for content: `w:bidi` per paragraph (DOCX),
per-block style alignment (PDF `_styles_for`), a `dir` wrapper on the blocks
that differ (HTML).

A document is not required to be all one language. A bilingual archive, a
report quoting Arabic sources, minutes with two languages — resolving direction
once for the whole document means whichever language loses the ratio has every
one of its blocks aligned backwards. This was reported from a real generated
tweet archive: 35% Arabic, so the document resolved LTR and every Arabic tweet
was left-aligned with no `w:bidi` at all. Nudging the ratio would simply have
inverted the bug onto the English blocks.

Blocks with no strong directional character — a divider rule, a bare number, a
date — inherit the document direction rather than defaulting to LTR. The DOCX
LTR branch must stamp `w:bidi val="0"` explicitly: in an RTL document the
Normal style and the section both carry bidi, so silence is not neutral.

**J. One type scale, enforced by measurement.**
`theme_cs_size()` is the complex-script optical size and **every** engine must
apply it: DOCX via `w:szCs`, HTML via `_css`, PDF via `_build_styles`. The PDF
engine was the odd one out for a long time — it used the Latin `body_size` for
Arabic, so the same document was set at 11pt one way and 16pt the other and
paginated to 6 pages versus 13. Same rule for `line_height` vs `line_height_ar`
and for table cells (`theme_cs_size(10)`).

RTL tables must read right-to-left in every format: `w:bidiVisual` (DOCX),
`dir="rtl"` (HTML), and an explicit column reversal in the PDF engine, which
has no bidi table model of its own.

Headings carry keep-with-next (`w:keepNext`/`w:keepLines`/`w:widowControl` in
DOCX, `_keep_headings_with_body` → `KeepTogether` in PDF, `break-after: avoid`
in HTML) so a heading is never the last thing on a page. Paragraph widows and
orphans are off (`allowWidows=0`/`allowOrphans=0`, `orphans: 3`/`widows: 3`).

`tests/test_document_layout.py` measures all of this against a rendered PDF
with PyMuPDF rather than grepping the source — every defect it covers was
invisible at the code level and obvious on the page. Do not weaken it to source
assertions.

**K. Third-party parse egress is opt-in and audited.**
`extract_salvage.try_remote_parse` uploads the ORIGINAL document bytes to
LlamaParse/Reducto. It requires `documents.security.remote_parse` (default
**off**); an API key in the environment is not consent. Local Docling salvage
is `documents.security.local_salvage` (default on). Every remote call records
an `egress`/`remote_parse` audit row with provider and byte count via the hook
`DocumentIngestionService._install_salvage_audit` installs. The legacy
`KAZMA_REMOTE_PARSE=0` / `KAZMA_DOCLING=0` env switches remain as an additional
veto only — they can turn a tier off, never on.

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
- `agent/graph_tool_worker.py:tool_worker_node` — the single-agent chat path;
  the gate runs BEFORE the security HITL split so it can rewrite args first.
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
  Config default is off; **auto-ON in production / multi-user** unless the
  operator set the key explicitly (`get_commitment_config`).
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
- `_commitment_resolve_gate()` in `graph_tool_worker.py` — the extracted gate
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
  `KAZMA_COMMITMENT_SOUL_REQUIRES_CONFIRM` (default off; auto-ON in
  production / multi-user unless explicitly set),
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
The list lives in `kazma_core/db/pg_backup.py` (not here — this file will
drift). It includes LangGraph `checkpoints*`, shared-state
(`kazma_settings`, `kazma_chat_sessions`, `kazma_swarm_tasks`,
`kazma_swarm_worker_metrics`, `kazma_platform_users`, `kazma_web_sessions`),
document jobs (`document_jobs`, `document_job_events`), **and** the document
catalog from H-13 (`documents`, `document_blobs`, `document_versions`,
`document_artifacts`, `document_acl`, `document_tombstones`,
`document_chunks`, `document_audit_events`). A new shared-state PG table
MUST be added to that Python list or it silently stops being backed up.
The dump is deliberately table-filtered — never a whole-DB dump — so a
shared database neither leaks foreign-app data into Kazma's backups nor
restores over another app's tables. `hitl_gates.db` is SQLite (single-process
like the turn journal), not this list.

**B. Dump pipeline (6h loop, `worker_bootstrap.py`).**
The backup/export loop enqueues `native_pg_backup` when
`pg_backup_enabled()` (live-checked: Postgres backend + `backups.pg.enabled`
config + `KAZMA_PG_BACKUP_ENABLED` env kill-switch). The handler
(`_handle_native_pg_backup`) runs `perform_pg_backup()` in a worker thread:
dump via `migration/pg_bridge.dump_database(tables=KAZMA_PG_TABLES)` to a
`.tmp` file, validate the `PGDMP` magic, atomically rename into
`{kazma-data}/backups/pg/pg_shared_<epoch>.dump`, then prune to
`backups.pg.retention` (default **3** local staging dumps; restic keeps
history. Env `KAZMA_PG_BACKUP_RETENTION`).
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
tables are touched), `list`. Loads the `.env` python-dotenv finds walking up
from the script's folder (not the CWD); never prints the DSN userinfo.

**E. Config is live-read, never raises** (mirrors `get_hitl_config`):
ConfigStore keys `backups.pg.enabled` / `backups.pg.retention`; env
kill-switch `KAZMA_PG_BACKUP_ENABLED=0`. Tests:
`python -m pytest tests/test_pg_backup.py`.

**E2. The dump must find its tools without PATH's help.** `pg_dump` usually
runs inside the DB container through `docker exec`, and a Docker Desktop
update dropped the CLI from the system PATH on 2026-09-25 — every dump after
it failed while Docker and the container were fine. The docker CLI is found
by `kazma_core.docker_cli.find_docker_cli()` (`KAZMA_DOCKER_BIN`, PATH,
Docker's install folders), never `shutil.which("docker")` alone
(`tests/test_docker_cli_discovery.py` gates it). Boot checks the tool
(`pg_dump_tool_problem`, ops alert `backup.pg_tools`), and a failed dump's
alert carries its reason (`last_pg_backup_failure`), redacted. Putting the
folder back on PATH then needs only `kazma_guard.py --reload`: the server
adopts PATH entries the OS settings gained since the guard started (§38).

**F. Universal backup — "never left anything behind"**
(`kazma_core/backup/universal.py`). One unified backup that backs up
literally everything: every `*.db` in `kazma-data/` (WAL-safe via
`sqlite3.backup()` API), every non-DB dir (attachments, document-store,
workspace, exports, vectors — via `_robust_copytree` that skips ephemeral
files like LibreOffice cache). It does **not** run a second `pg_dump`.
It **records whether `native_pg_backup` is fresh** (`_pg_dump_state`); a
stale/missing dump is a failed item in the manifest, not `"ok": true`.
Produces `manifest.json` + retention-capped (default 7). Wired into the
6h `native_backup` handler (auto) +
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
- **Every product module also imports ON ITS OWN**, in a fresh interpreter
  (`scripts/check_fresh_imports.py`, CI job "Every module imports on its
  own"). The one-process gate above cannot see an import cycle that works
  only when entered at the right module: each import finds the earlier ones
  loaded. `kazma_core.routing_engine` was one until 2026-09-26 -- the swarm
  package's `__init__` imports it, and it imported `kazma_core.swarm.task`
  back at module level. A module that a package's `__init__` imports must
  not import that package at module level; take annotation-only types under
  `TYPE_CHECKING` and the rest at call time. Both gates walk one list
  (the script's `iter_modules`).
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
  ingest, and readiness. Direct (no-proxy) hops pin the validated IP
  (`PinHostAsyncTransport`) and abort if the peer is private (§32). Do not
  pin through `proxy=` — CONNECT would break scraping.
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
  `.[test]`-only install left `importorskip`ing/degrading. Playwright is a
  separate CI job: smoke (`tests/e2e/test_smoke.py`, polls `/health/live`)
  plus a named GATE step for HITL view-model incidents 2 and 3
  (`tests/e2e/test_hitl_view_model.py`). Remaining `tests/e2e` files run in
  the same job. Incidents 1 and 4 stay unclaimed (no app-graph pause
  harness). Torch-bearing `rag` extra is still too heavy for CI.

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
- **The app applies forwarded headers, not uvicorn**
  (`kazma_ui/proxy_headers.py`, the OUTERMOST middleware): it records the
  TCP peer (`auth.TCP_PEER_SCOPE_KEY`, read by `_peer_host`), then runs
  uvicorn's own `ProxyHeadersMiddleware` for the declared proxies, so routes
  see the same client and scheme as before. Every launcher passes
  `proxy_headers=False`. With uvicorn rewriting first, `_peer_host` read the
  forwarded client as the peer: behind Cloudflare Tunnel (declared
  `127.0.0.1`) the detector flagged the tunnel's own visitors on every boot
  (2026-09-23 to 2026-09-25) and advised trusting a visitor's IP, and
  `_peer_trust_allowed` saw the visitor instead of the proxy. Gate:
  `tests/test_forwarded_headers_peer.py` (real ASGI layers — fake requests
  whose `client` is the peer are why the unit tests never saw it).
  Entries may be CIDR ranges; `auth._is_trusted_proxy` is the ONE answer to
  "is this peer a declared proxy" (every check asks it, never set
  membership), and `*` is dropped so neither layer trusts every peer.

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
- **Masking is by key AND by value.** A password inside a URL is a secret
  whatever the key is called (`kazma_core/security/url_credentials.py`, the
  one home): `memory.backends.state.url` held the live Postgres DSN in
  plaintext and the Settings API returned it (2026-09-25), because every
  masker decided by key name. Every function named mask*/redact* that decides
  by key is found from the source and fed a URL password by
  `tests/test_url_credentials.py::test_every_key_name_masker_also_masks_url_passwords`;
  a new one needs a probe there. The mask is `****`, which
  `is_masked_secret_placeholder` refuses to write back, so a form can round-trip
  it. Provider, profile and connector saves go further
  (`url_credentials.restore_masked_url`, 2026-09-26): a URL posted back
  exactly as shown keeps its stored password; stars with any other change
  (host, user, path) are refused with a 400 — a stored password is never
  moved to a URL someone just typed (`tests/test_url_password_round_trip.py`).

**E. Nothing blocking on the event loop; nothing fire-and-forget.**
- A sync `sqlite3.connect` inside `async def` pins the loop that serves
  every SSE and WebSocket stream. Drop `async` (FastAPI threadpools sync
  handlers) or wrap in `asyncio.to_thread`.
- `asyncio` holds only a WEAK reference to a task, so a discarded
  `create_task(...)` can be garbage-collected mid-run, silently. Use
  `kazma_core.background.spawn_background(coro, name=…)`. Bare
  `loop.create_task` in product code (including `ops_alerts._dispatch`)
  is the same class of bug — prefer `spawn_background`.
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
- A thread-per-tool-call used to crash Windows (`AccessViolation` in FTS /
  schema / ConfigStore — three faces of one race). **One worker thread**
  now drains a bounded queue; schema is ensured once per process; the queue
  drops rather than blocks; `atexit` drains it. Do not reintroduce a thread
  per call. Repro: `tests/test_truncation_retry.py`.
- `reset_config_store()` must not close the sqlite handle out from under a
  background reader. Closing is **opt-in** (`close=False` is the default).
  The recorder reads `read_memory_cfg()` on the caller thread so the worker
  never touches ConfigStore. **A default every caller must override to be
  safe is the wrong default.**
- The *explicit* `ConfigStore.close()` was the remaining way to free that
  handle under a reader, and it did — see §8. Repro:
  `tests/test_config_store_close_race.py`.

**I. A process a tool starts gets no secrets (2026-09-26).**
- `shell_exec` / `python_exec` always used a minimal environment. pytest
  (`run_unit_tests`, the patch-set verify run), `pip`/`npm` installs, `ruff`,
  git and the IDE's read-only git passed the server's whole environment --
  `KAZMA_VAULT_KEY`, the database password, API keys -- to code nobody
  reviewed: a repository's conftest and tests, install scripts, hooks,
  `core.fsmonitor`.
- `security/child_env.tool_child_env()` is the builder: the server's
  environment minus every `KAZMA_*` name, credential-named variables and URLs
  carrying a password (git keeps HOME, its credential helper, SSH_AUTH_SOCK).
  `run_off_loop` (the skills' runner) fills `env=` with it when the caller
  does not; a caller that builds its own env starts from it
  (`get_commit_env`, `_git_sync`). `KAZMA_CHILD_ENV_ALLOW` lets named
  variables through, never `KAZMA_*` ones.
- Gate: `tests/test_child_env.py` finds every process started under the
  tool, IDE and skill folders and checks where its env came from (closures
  included); behavioural half runs real pytest and a real git hook.




### 27. Backups that report success (2026-08-29)

A backup that logs "complete" while the offsite copy cannot write is the
same as no backup. Reproductions: `tests/test_backup_silent_failures.py`.

**A. Prove the write.** `restic_repo.remote_writable()` PUTs a probe before
any restic call. `rclone:` and `s3:` are both probed (`s3:` = stdlib SigV4
PUT+DELETE under `locks/` — the prefix an append-only key must still be
allowed to delete). A read-only probe is worthless. `_run` refuses restic
when the probe fails (`backup.restic_remote_read_only`). Tests:
`tests/test_restic_s3_write_probe.py`.

**B. A missing passphrase is not a config note.** `alert_missing_password()`
is critical, and silent only when no repository exists yet.

**C. Mechanisms that only speak when they break cannot be told from
mechanisms that never run.** Successful restic snapshot + restore each log
one line. `observability/firing_ledger.py` (`run_weekly_sweep`, started
from `start_memory_worker`) counts them. `_log_paths()` must read
**guard.log and kazma.log, and their rotated siblings** (the app log rotates
at midnight; reading only the live file made a weekly report of one day and
called six days of backups "silent", 2026-09-23). Signatures are checked
against the code: `test_every_ledger_signature_matches_a_line_the_code_emits`
derives the emitted lines from logger format strings, ops-alert keys and the
result `summary()` methods -- a signature no code emits fails the build. A
mechanism logs on SUCCESS too (restic maintenance did not, and its signature
matched only the failure line). A health-gated restart is a
`guard.restarting` whose reason is `unhealthy (...)`, not a failed probe. The
sweep runs in a thread (it reads ~4M lines) and must stay scheduled (it
shipped unscheduled once).

**Chaos injection is only real where it lands.** `InjectionTarget.LLM_PROVIDER`
is injected INSIDE `resilient_chat`'s attempt loop. `ChaosInjectionError`
carries `.transient` (408/429/5xx) so retry/failover actually run.


### 28. A guard nobody has seen fail (2026-08-30)

Every guard test needs a **negative control**: assert it FAILS on a
synthetic violation in the same file, or "passing" means nothing. Enumerate
inputs from the **same SoT the system uses** (CSS gate parses `base.html`
stylesheet list — `kazma.v5.css` loads second and last-wins). Prefer
measuring behaviour to grepping source. A guard can be wrong in the
direction of blocking good work (`ast.walk` flagged a correct lazy
`import fitz`). Runtime corollary: `start_stall_watchdog` must be
stoppable — cancelling the heartbeat must not leave a thread reporting
stalls forever. HITL card CSS: do not duplicate selectors in `kazma.css`
and `kazma.v5.css` (Wave 8 L-2: standalone `.metric-card` lives in v5).


### 29. Context Integrity (2026-08-30 incident hardening)

Born from the @KazmaAI tweet-batch incident: 8 approved drafts vanished
between proposal and approval because deterministic trim deleted them, the
scratchpad built to prevent it was clobbered every turn, the summary net
never fired (24K→160K dead band), and a misread `what going on?` disarmed
recall. Plan + execution report:
`docs/plans/CONTEXT_INTEGRITY_HARDENING_PLAN.md`; the duplicated-stream
reproduction + fix: `docs/plans/S3_2_DUPLICATED_STREAM_INVESTIGATION.md`.
Tests: `tests/test_context_integrity.py` (56),
`tests/test_s32_stream_duplication.py` (3).

**A. The scratchpad channel is a merge reducer — never re-introduce a
replacing write path.** `SupervisorState.scratchpad` is
`Annotated[dict[str, str], merge_scratchpad]` (24 keys × 4000 chars,
oldest evicted, `SCRATCHPAD_CLEAR` sentinel for deliberate resets). The
transports must NEVER contribute a `scratchpad` key:
`build_turn_working_memory()` deliberately omits it, and the old
`scratchpad: {}` transport payload is precisely what wiped the
checkpointed value every user turn (LastValue replace). Adding any new
write path that puts a full replacement dict into the graph input
reopens the incident.

**B. Durable artifacts live in `agent/artifacts.py`, not in graph state.**
The SQLite store keyed `(tenant_id, thread_id, key)` is the source of
truth for scratchpad findings and proposals; graph state is a read-through
cache. House patterns are load-bearing: open through
`apply_sqlite_pragmas()`, DB under `kazma-data/` (universal backup for
free), GC rides the commitment-GC cadence (`worker_bootstrap.py` — no new
sweeper loop). Drafts are a row; the model reads the row. Context loss can
no longer destroy approvable content.

**C. The proposal chokepoint is the commitment resolver — the supervisor
nudge is NOT the guarantee.** `x_post` / `x_schedule_post` / `book_x_post`
refuse without a resolvable `proposal_id`
(`safety/commitment/authorize.py:_resolve_proposal_backed_post`, inside
`_resolve_send_outbound_act`); a broken artifact store denies fail-closed.
When the id resolves, the gate REWRITES `text` to the STORED text — the id
wins over whatever the model holds in context. The iteration-0 nudge in
`graph_supervisor.py` is advisory only and must never be the thing
standing between a draft and the wire. A scheduled post
(`x_schedule_post`) is the same incident with a delay — it is on the
required list for exactly that reason. The tool worker's proposal check
(`_commitment_resolve_gate`) passes ONLY the publish calls the resolver
verified in the same pass and refuses the rest (commitment layer off). It
must never refuse a verified call: from 2026-09-17 to 2026-09-24 it refused
every one, no chat post went out, and an eval test had locked that in as a
"property". `tests/test_x_post_proposal_gate.py` +
`test_a_verified_publish_stops_at_the_approval_card` pin both halves. The HITL card (Web `renderHitlCard`
+ gateway `_build_approval_prompt`) renders the stored proposal text, so
what the user approves is what publishes.

**D. The summary net fires on EVERY trim that drops turns.** Never gate
`inject_summary_of_dropped` on a percentage-of-window again — trim fires at
`min(24K, window×0.6)` and the old 80% gate meant the net had effectively
never protected a trim. The injected note NAMES what was dropped ("4
assistant turns including 8 enumerated draft items"); under ~2K dropped
tokens the heuristic summarizer runs (zero extra LLM cost). Trims are
counted: `kazma_context_trims_total{summary=fired|missed}` +
`kazma_context_trim_dropped_messages_total` — a growing `missed` is a bug
report. Re-tuning the 24K budget is a SETTINGS change
(`agent.trim.token_budget`, clamped to [4000, window×0.95]) decided after
reading those counters for real traffic — the default is deliberately
unchanged.

**E. `shift` is split — recall is disarmed only by an explicit pivot.**
`shift_explicit` (regex — the user verifiably said so; legacy `"shift"`
from old checkpoints counts) suppresses recall and supersedes the task.
`shift_inferred` (embedding drift) re-ranks only: recall stays ON and
`stub_prior_tool_chains(..., keep_assistant_prose=True)` collapses tool
payloads but keeps assistant prose IN FULL — a misread pivot must not
erase the thing being asked about. Interrogative check-ins
(`is_interrogative_checkin`, EN + Arabic شنو/وش/ليش/شفيه/وين/متى/شلون/شصار)
are gated BEFORE the embedder and can never classify as drift;
`_MIN_CHARS` is 25 plus a content-word requirement (length alone was never
the right signal).

**F. A recovery attempt never re-streams content (the duplicated-prefix
invariant).** The SSE/WS bubble APPENDS token deltas; only `turn_complete`
has replace semantics. Once any delta of a user-visible LLM call has been
emitted, no recovery attempt of that same call may emit content deltas
again — the authoritative text arrives via the final response +
`turn_complete` backfill. Three sites enforce it: supervisor retries pass
`emit_deltas=(attempt == 1)` to `invoke_llm_chat`, the failover chain
passes `emit_deltas=False`, and `LLMProvider.chat_stream` tracks
`_emitted_any` so its blocking fallbacks yield only the final
`StreamDelta(response=…)`. Removing any one of these brings back the
`The proposal turn is The proposal turn is` incident string (locked by
`tests/test_s32_stream_duplication.py`).

**G. Recovery spirals get an honest exit.** ≥3 turn-cumulative queries
against session/checkpoint/audit stores hunting the assistant's own prior
output force RESPOND with "what's missing + one concrete question"
(`tool_loop_breaker.count_recovery_probes`); digging only grew the history
that caused the trim.


### 30. HITL Gate Registry (`kazma_core/safety/hitl_gates.py`) — one gate, one row, one truth

Born from the 2026-09-01 chat-card incident chain (ghost cards, pre-approved
stamps, second question hidden on the dashboard). Plan SoT:
`docs/plans/HITL_GATE_REGISTRY_PLAN.md`. Every HITL approval across ALL FOUR
mechanisms (graph interrupt, swarm bus, pipeline checkpoints, semantic cards)
is one row in `kazma-data/hitl_gates.db` with a strict CAS state machine:
`pending → claimed → resuming → settled` (+ `timeout`/`superseded`/`error`).
Kill-switch `KAZMA_GATE_REGISTRY=0` degrades to a **thin execution
fallback**: a live checkpoint interrupt is pending (live card), never an
inferred Approved stamp. That is not a second decision author.

**A. Decision truth vs execution truth (the split is load-bearing).**
The registry owns the DECISION (was this gate answered, by whom); the
LangGraph checkpoint owns EXECUTION (is the graph actually paused). Readers
consult the registry (`hitl_thread_status`, `close_turn`,
`/api/pending-approvals`, chat.js `_serverGates`). Paused + no covering
row is an **unregistered pending gate**: backfill from the snapshot and
keep the turn open — never treat absence as "no question". Auto-deny
uses the registry for *which* is pending and the checkpoint for *how*
(stale row must not resume nothing).

**B. Every transition is a single CAS UPDATE.** Zero rows affected ⇒
`TransitionConflict` carrying the row's actual state — that IS the 409 body.
Idempotent same-decision re-claim returns the row (200 semantics). `pending`
is the ONLY state a card renders live buttons for; the client never infers a
claim.

**B2. A decision names its gate, and the server checks.** `/api/approve`
verifies the body's `interrupt_id` against the registry BEFORE resuming
(`_gate_not_pending`, `routes_direct/misc.py`). Only a `pending` row may
be resumed; `claimed`/`resuming`/terminal answer 409 `not_pending` with
the server's actual view, and a row owned by another thread answers 409
`foreign`. Absence is not an objection — no row, no id, registry off or
registry unreadable all fall through to the route's existing liveness
check, because the dashboard, TUI and gateway reach this route with ids
the registry may not carry and a human is waiting behind every call.
Fail open on plumbing, never on a recorded decision. **Why (2026-09-20,
`tests/e2e/test_unified_turn_concurrency.py`):** the route's only test
was "is this THREAD paused". A resume pauses again at the next gate, so
a retried Approve — a double-click, a lost 200, a stale tab — decided
the question the human had never seen (approved `file_write`, authorized
`shell_exec`). The 409 translates registry words into client words:
`claimed`/`resuming` leave as `inflight`, because chat.js converges on
that and paints an ERROR on anything else.

**C. Two-id rule.** `register_gate` is idempotent on both `gate_id`
(LangGraph `intr.id` preferred) and `alias_id` (the deterministic
`make_gate_id` hash) — one pause can never mint two cards. A terminal row
under a HASH id does not eat a NEW ask for the same tool+args (fresh row,
uniquified suffix); a native-id repeat returns the terminal row.

**D. Surfaces render; they never mint.** Gate transitions publish through
`GateEvents` into the EXISTING turn journal (`hitl` parts of the same
TurnDocument) — never a parallel event stream. Writer sites: SSE post-stream
scan (primary register, has `intr.id`), `/api/approve` (claim + resuming),
drive terminal (`settle_thread_gates` — settles claimed/resuming, NEVER
pending: a second live question keeps the turn open), gateway pause/resume
(`gate_claimed_for_thread` — platform cards are per-thread), swarm
`safety.check()` (register→bus→claim+settle), pipeline
`checkpoint_manager._gate_register_pipeline`/`_gate_settle_pipeline`.

**D2. A decision has ONE writer:** `kazma_ui/hitl_decision.record_gate_decision`
-- transcript part stamp, registry CAS, THEN the journal `hitl` frame (the
broker stamps the frame's view from the registry; emit-before-CAS painted "No
longer pending", 2026-09-20). The web approve route, the approval-timeout
watchdog (`decision="timeout"`), platform buttons and the WS approve path all
call it. Each used to write its own subset: the watchdog never stamped the
transcript, so an auto-denied card stayed `pending` in the chat forever and
the turn reloaded broken (2026-09-26); platform buttons never told the journal,
so a browser watching the thread never saw the decision. It stamps only a turn
the session already has (a minted id would add an empty bubble). Gate:
`tests/test_gate_decision_recorder.py` (every function that builds a resume
with `approved=` calls it; `kazma ask` and `hitl_supersede` are exempt with
reasons).

**E. Reconciler — every crash window has one behavior.**
Approve-on-missing-row backfills (`created_missing`); `close_turn` settles
pending rows whose checkpoint is NOT paused as `orphaned` (in seconds);
`boot_sweep()` (app startup) orphans stale claimed/resuming rows past grace
and NEVER touches pending (the card must survive a restart); TTL sweep rides
the 15-min commitment GC cadence in `worker_bootstrap.py` (no new scheduler
loop). Metrics: `kazma_hitl_gates_total{state,mechanism}`,
`kazma_hitl_gate_parity_mismatch_total{site}` (must trend to zero),
`kazma_hitl_gate_reconciled_total{action}`.

**F. Honesty limits.** `hitl_gates.db` is single-process truth (like the
turn journal) — no multi-replica claims; a Postgres backend goes next to §21
when needed. The registry cannot stop the model narrating while paused —
that is handled by close_turn keeping the turn open on any pending row.

Tests: `tests/test_hitl_gates.py`, `test_hitl_gate_bridge.py`,
`test_hitl_gate_read_cutover.py`, `test_hitl_gate_swarm_pipeline.py`,
`test_hitl_gate_reconciler.py`.

**Collision recipes** (also in `docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md` —
keep both in sync):

1. **T-4 / `chat.js`:** scrub stays inside `renderTurn`. No second painter.
2. **H-8 / `tool_registry.execute`:** apply `rewritten_args`; `clarify`/`confirm`
   fail closed (“run from chat”). Never mint a second gate row on the web path.
3. **T-2 pipeline timeout:** finalize the task **and** `settle_gate`.
4. **H-9 bus:** `is_danger_tool()` → `requires_approval()`. Not FanOut first-wins.
5. **H-12:** swarm bus tri-state only. Must not retarget web `claim_gate`.

Protected files: `chat.js` (`_paintHitlFromDoc`, `renderTurn`,
`_hitlAlreadyClaimed`, `_serverGates`), `turn_document.js`,
`turn_runtime.py` (`close_turn`), `hitl_gates.py`, `hitl_status.py`.


### 31. Turn Delivery V2 — journal is SoT, client projects

Web/SSE/WS chat is **event-sourced delivery**, not a second brain.
Plan: `docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md` (P0–P4 shipped).
HITL sits on this as one projection (§30).

**A. `close_turn` is the only closer.** `kazma_ui/turn_runtime.py:close_turn`
decides whether a turn is open, waiting on HITL, or done. A pending
registry row keeps the turn **open**. Absence of a row is an unregistered
pending gate (backfill), never an inferred Approved stamp. Do not add a
client-side `forceEndTurn` wall-clock that marks CoT Done while the server
is still working.

**B. The client projects; it does not author.** `chat.js` paints from the
TurnDocument / `_serverGates`. Token deltas APPEND; only `turn_complete`
has replace semantics (§29F duplicated-prefix invariant). Do not restore
`tokenAccum` dual-paint (T-4).

**C. Catch-up is resume, not replay of deltas.** Refresh / reconnect reads
the journal + gate status. Do not re-stream content the bubble already
appended.

**D. Transports are mouths.** SSE (`sse_chat/`), WS (`ws_chat.py`), gateway
graph (`agent_handler/graph.py`) all close through the same completion
contract. A new mouth that invents its own “Done” is a delivery bug.

**Both browser mouths paint every journal frame.** The turn broker stamps
one frame and fans it to the SSE stream AND the WebSocket, and a tab that
only watches a turn (another window, a phone) may have the socket alone. So
every frame type `streaming.js` `dispatch` handles needs a case in
`agentStore.handleSocketMessage`, painted by the same functions
(`chat.applyJournalFrame`, `chat.ingestGateViews`; the attach callbacks use
them too). Live 2026-09-26 the socket had no case for `hitl` -- the approve
route's approved/denied -- nor the tool frames, and dropped `done`'s gate
views: a watching tab never saw the other tab's approval settle, and its
block stayed on "Approval required" under the answer. Gate:
`tests/test_journal_frame_parity.py` (reads both dispatchers); the
browser net is `test_a_watching_tab_shows_each_turn_in_its_own_block
[socket-only]`.

**E. Presentation is governed by one plan.**
`docs/plans/UNIFIED_TURN_BLOCK.md` is the binding contract for how a Web
chat turn is presented: one persistent block per turn, an integrated status
header, one collapsed thoughts disclosure whose expansion events never
touch, ONE approval group with a keyed row per gate, and one answer region.
It supersedes in part `COT_AND_THOUGHTS.md` (separate live bar, auto-open
thoughts), `TURN_RENDER_V2_KEYED_SLOTS.md` (flat per-gate top-level slots)
and `HITL_VIEW_MODEL.md` (layouts that require separate cards). Delivery
ordering (§31) and gate authority (§30) are preserved unchanged — any
protocol extension must update those contracts explicitly.

Baseline, evidence and known gaps: `UNIFIED_TURN_BLOCK_PHASE0.md` through
`_PHASE5.md` in the same directory. Each phase report states what it does
NOT claim, and `_PHASE5.md` §6 lists what the release does not have; read
those before claiming any of it.

Shipped: durable tool activity and a document revision (P1), the in-block
header, reader-owned thoughts fold and one answer authority (P2), one
approval group with keyed rows (P3), gate-identity enforcement on the
approval route and the full acceptance matrix (P4), the removal of the
last second DOM writer plus release evidence (P5).

**The lifecycle job is green on Linux and blocks nothing.** It was RED
from Phase 3 to Phase 5 and nobody noticed, because every phase report
was written from Windows runs by hand (`_PHASE5.md` §6). It is green now
— but `main` has no branch protection at all
(`gh api repos/Mubder/kazma/branches/main/protection` → 404). Enabling it
would reject the direct pushes this repository works by, so the trade is
the owner's to make. Do not describe this job as a required check.

Load-bearing rules:

- **One status surface per turn, inside the turn.** `#live-task-card` is
  deleted. A page-level status element is what forced a second phase
  machine, a second clock and a second recovery loop. The header derives
  from `modules/turn_presentation.js`, a pure function — do not tell it a
  phase.
- **The fold belongs to the reader.** `modules/turn_preferences.js` is the
  only writer of disclosure state, and only from a gesture. Any map keyed
  by turn id must be promoted when the turn is renamed off `'live'`, like
  `turn_view.js:promote` — the preference store was not, and the fold shut
  itself a second into every turn.
- **Gates are ROWS in one region above the answer**, in ask order. Never
  order gates by state: that is what moved the answer when one settled.
- **One count.** `turn_presentation.gateRows()` is what both the header
  and the group count, so the two cannot contradict each other.
- **One writer, and it is enforced.** `turn_view.js` performs the single
  ordering pass; `chat.js` builds regions and hands them over, it never
  inserts them. The pre-V2 fallback painter (`ensureProgressPanel`) is
  deleted — it could only run when the projector module was missing, and
  in that state there is no answer text to render at all, so it could
  only ever produce a half-rendered turn.
  `tests/test_turn_render_boundary.py` fails if either the retired bar
  markup or a second turn-content writer comes back.
- **A decision names its gate.** See §30 B2: `/api/approve` verifies the
  body's `interrupt_id` against the registry before resuming. A retried
  Approve used to decide whichever question the graph had reached by
  then.
- **Every journaled frame names its turn.** `TurnBroker.emit` stamps
  `data.turn_id` (`delivery._with_turn_id`: the emitting task's bound turn,
  else the thread's open reply turn; an existing id is kept, a frame between
  turns stays unnamed). The client's first named frame of a turn painted
  under `'live'` ADOPTS that document (`applyTurnEvent` -> `_retagDoc`) --
  never a fresh one, which would repaint the bubble from one frame.
  `tests/test_frames_name_their_turn.py`.
- **A stored row field is named once, beside its writer.** The history
  route's two serializers (the row list and the checkpoint-hydrate merge)
  are whitelists; they pass `reply_sink.CLIENT_ROW_FIELDS`, and a field kept
  for the server goes in `SERVER_ROW_FIELDS` with its reason.
  `tests/test_history_row_fields.py` enumerates every field `reply_sink`
  stores from its source and reads a full row back through the real route
  (the revision, the usage and the close time were each dropped by a
  whitelist nobody updated). The meta line under a bubble has one writer,
  `_paintMetaTail` in `chat.js`: callers set fields, never `textContent`
  (that flattened the `<time>` element). A terminal frame's stats reach it
  through `_paintTurnStatsFor` from BOTH mouths (the socket bridge
  `applyTurnStats` updated only the badges, so a watching tab never showed
  the line). A reply shows `closed_at` — its delivery time, set once by
  `_write_lifecycle` on the closing write.
- **A step spins only when it says `running`.** Gate rows carry `info`;
  `_activityRowsHtml` drew every status row without running/failed/done as
  running, so decided gates spun forever (`test_sequential_allow_tool_in_one_bubble`
  asserts nothing spins on a finished four-gate turn, live and reloaded).

Sequential approval is proven at the lifecycle level by
`tests/e2e/test_unified_turn_app_graph.py` and in a browser by
`tests/e2e/test_unified_turn_browser.py`, which claims HITL_VIEW_MODEL.md
Playwright 1 and 4 — open since the 2026-09-19 F0 spike. Both run in the
`unified-turn-lifecycle` CI job.

**A turn's numbers are the turn's, not the segment's.** A turn that pauses
for approval runs as several graph segments. The done frame's tokens and
cost are the sum of the per-call ledger for the turn id
(`llm_ledger.turn_usage`; every row carries `turn_id`, bound per turn in
`_stream_langgraph_events`), its duration runs from the question
(`kazma_ui/turn_usage.turn_started_epoch`), and the session is charged only
the segment's calls. A new LLM call site inside a turn records into the
ledger (the respond synthesis did not, 2026-09-26). Gate:
`tests/test_turn_usage.py` (through the real streamer).

**A plan feeds only the running turn.** `_paintTextSlot` paints every turn,
finished ones on every reload; plan ingestion there logged progress on the
live turn and painted a "Kazma is thinking…" header under a finished answer
(`_isRunningDoc`; `tests/e2e/test_plan_fence_reload.py`).

Cross-language turn projection is locked by shared fixtures under
`tests/fixtures/unified_turn/` — do NOT add a Python example and a
JavaScript example for the same rule; that is how `legacy_turn_id` came to
mint a different id in each language with both suites green.

The same job carries the release evidence: Playwright traces and
screenshots on failure, a recording of the four-gate turn either way, and
a build-identity file written before anything runs so a failed job still
says which build failed. It uploads `test-artifacts/unified-turn`.

**Delivery over a proxy that re-chunks (2026-09-26).** Behind Cloudflare
Tunnel the live chat dropped tool rows and approval cards, reverted Stop to
Send in seconds, and threw `Cannot read properties of null (reading
'tool_name')`; loopback delivers frames whole, so every test passed. Rules:

- **One SSE reader, state per STREAM.** `KazmaStream.createSseParser`
  (`streaming.js`) keeps a half-read frame until its blank line, whatever
  the network cut; it held event/data/id per network READ, lost cut frames
  and glued their orphaned data onto the next frame. A handler that throws or
  data that is not JSON costs that frame only (`onFrameError`; chat.js
  resyncs once) -- it used to stop the reader for good. Gates:
  `tests/js/test_sse_parser.js` (every cut position, CR/LF/CRLF, a copy of
  the old reader as the negative control) and
  `tests/e2e/test_chunked_stream_browser.py`, which wraps the app in
  `RechunkedStreams` (1-40 byte pieces). A browser test that only ever runs
  on loopback does not test delivery.
- **`replay` means history, stamped per call site.**
  `_frame_from_journaled(frame, replay=...)` and
  `_sse_attach_stream(..., replay_is_history=...)` have no default. The stamp
  was unconditional, so the live tail -- which is how a sent turn reaches its
  own tab since the journal drive -- labelled a new approval history, the
  client refused it (correctly), and the card waited for the reconciler.
- **Other tabs learn about a question.** The send journals a `user_message`
  frame (content, turn_id, `client_msg_id`) BEFORE capturing the head, so
  the sender's stream never carries it and its WebSocket drops it by id;
  `chat.beginObservedTurn` adds the user row in the other tabs. Without it a
  watching tab painted each new turn into the previous turn's block. It is in
  `REPLAY_SKIP_TYPES`: a reload reads the row from the store.
- **A page attaches from what it has READ**, on either mouth
  (`_pageDeliveryCursor`: the WebSocket tracker, else the persisted cursor).
  `_lastSeqSeen` follows SSE only and no terminal callback records a seq, so
  attaches replayed from 0 or from before the previous turn's end and flipped
  finished blocks back to "working".
- **Only an ENDED turn may be healed from the checkpoint.**
  `_checkpoint_backfill_unanswered` returns early while the thread's turn
  runs, is paused, or its snapshot has a `next` node; it had written a paused
  turn's narration as a finished reply on the page's own `/messages` poll.
- **Token, tool and status frames name their turn** since 2026-09-26 (the
  load-bearing rule "Every journaled frame names its turn" above). Before
  that only done/turn_complete/hitl did, and every client filed the rest
  under "the current turn"; the rules in this list are what kept that guess
  right, and they still hold.


### 32. SSRF pin-IP (Wave 8 H-7)

`validate_url` returns the public IPs it resolved. Direct scraping (no
`proxy=`) uses `PinHostAsyncTransport` (`kazma_core/security/ssrf_pin.py`)
to connect to the pin while keeping Host + SNI. After every `read_url`
hop, `assert_peer_public` / `peer_ip_from_response` abort if the peer is
private (fail closed).

**Do not pin through `proxy=`.** `get_scraping_client` skips the pin
transport when a proxy is configured — CONNECT would break scraping.
Peer-private abort still runs. Tests: `tests/test_audit_wave8.py`.
httpx `>=0.27`.


### 33. Ops alerting — three paths, no fourth notifier

| Path | Who | When | Channel |
|------|-----|------|---------|
| Guard `Notifier` | Supervisor process, stdlib urllib | Child dead / unhealthy / crash-loop / pause | Telegram-direct — must work when the app cannot |
| `observability/ops_alerts.alert()` | Inside Kazma | Backup/offsite/restic/MCP/persist/turn-fail | Fan-out bus + Telegram-direct fallback |
| `lifecycle_notifier` | App boot/shutdown | starting / started / restarted / shutting_down | Same bus, filtered by `notifications.ops.channels` |

Model fallbacks (§38) ride the second row plus the web banner
(`AlertDispatcher.post_banner`, banner only) — not a fourth notifier.

**The guard's credentials come from a child process.** `Notifier` reads
env first, then Kazma's settings — through `_NOTIFY_LOOKUP`, run with the
server's interpreter in the install folder, which loads the install's `.env`
the way the server does. It used to import kazma_core in-process; once
importing stopped loading `.env` (2026-09-22) that lookup had no vault key
and no DSN, and the pager skipped every page for a day (from 2026-09-24
22:30). Never import the app into the guard, never load `.env` into the
guard's own environment (every server inherits it), and never let a test
run the real lookup (`_settings_lookup_allowed` is False under pytest). The
daily digest counts `notify.skipped`/`notify.failed`, so a silent pager shows
up through the app's channel.

Cooldown default 900s per key (`KAZMA_OPS_ALERT_COOLDOWN_S`). Never raises.
Kill-switch `KAZMA_OPS_ALERTS=0` (does not mute lifecycle). Mute theorem:
60 identical messages = the channel is ignored.

Guard `probe()` reads JSON 503 `checks` and names the failing
dependency (P0). Same-detail restart pages collapse on a cooldown; a
recovery card fires once after an unhealthy kill (P1). Remaining backup
success-summary / `native_pg_backup` ops wiring is still deferred:
`docs/plans/GUARD_OPS_ALERTING_CAUSE_QUALITY.md`. Do not invent a fourth
notifier, mix ops pages into HITL cards, or page every backup success.

### 34. Calendar / Gmail OAuth — vault is SoT, no silent sandbox

Live 2026-09-08: `list_events(provider=google)` returned
`Calendar: sandbox, No events found` after a successful Gmail reconnect.
The calendar router only read `GOOGLE_CALENDAR_TOKEN` from env; its
`_vault_get` stub always returned `""`. Gmail OAuth stores `email.gmail.*`
in the vault and does **not** automatically feed Calendar.

**Invariants:**
- Calendar tokens live in the vault (`calendar.google.*` /
  `calendar.microsoft.*`) via `kazma_skills.native.calendar.credentials`.
  Env vars are an override. Never reintroduce a vault stub that returns `""`.
- A Gmail-only token (`gmail.modify` without `auth/calendar`) must **not**
  be sent to Calendar. `google_access_token()` reuses the Gmail grant only
  when `email.gmail.scopes` includes Calendar.
- Explicit `provider=google` / `outlook` **fails closed**
  (`CalendarNotConnectedError`) — never silent sandbox — whether it is named
  in the call or by `KAZMA_CALENDAR_PROVIDER` (the env value was judged
  non-explicit until 2026-09-25). Sandbox is auto fallback only when no
  account is connected.
- **Email fails closed the same way** (`EmailNotConnectedError`,
  `email_manager/router.py`, since 2026-09-26). A provider named in the call
  or by `EMAIL_DEFAULT_PROVIDER`, or an account alias, that is not connected
  is refused with the Settings step to fix it; an unknown provider or a
  typo'd alias is refused too (both used to default to the sandbox, which
  answered "Sandbox sent to …" for a send that never happened). The sandbox
  answers only `auto` with nothing connected, `sandbox` itself, or an
  account whose TYPE is `sandbox`. Every email tool returns the refusal as
  its answer (`tests/test_email_fail_closed.py` enumerates the tools).
- Connect with Google requests Calendar as a **soft** extra (like
  `drive.file`): Gmail connect still succeeds if Calendar API is off.
  Settings → Email → **Connect Calendar** is the dedicated grant (same
  OAuth client, same `/api/email/oauth/gmail/callback` — dispatch on
  `state.provider == google_calendar`).
- Microsoft mail OAuth requests `Calendars.ReadWrite` and copies tokens
  to `calendar.microsoft.*`. Device-flow client id reads vault via `cred()`,
  not env-only.
- Connector health (`check_connectors`) probes Gmail **and** Calendar
  independently (Testing-mode 7-day expiry). Do not probe Drive as a
  stand-in for Gmail.

Tests: `tests/test_calendar_connector.py`, `tests/test_connector_health.py`.

### 35. Class gates from the 2026-09-22 audit — fix the class, not the instance

Every finding in that audit was a correct fix in one sibling and missing from
the next. Each now has a gate that enumerates the siblings from the real
source, with a negative control. When one fails, fix the code; do not edit
the gate to pass. Full list with evidence: `docs/KNOWN_GAPS.md`.

- **One copy of each decision.** Admin: `kazma_ui.auth.admin_decision`
  (fail-closed; wrappers keep their response shape). Thread ownership:
  `kazma_ui.thread_ownership` (fail-closed, off the loop). `.env` loading:
  `kazma_core.env_files.load_env_files`, called by entry points, never on
  import — and a program under `scripts/` that imports Kazma is an entry
  point too (`tests/test_env_loading.py` enumerates them: load it, or be
  declared env-free or own-env with the reason; the gate once saw only the
  packages, and eleven scripts plus the guard's pager lost the vault key and
  the DSN). Untrusted XML: `kazma_core.security.safe_xml`. Child rlimits:
  `kazma_core.security.rlimits` (no `preexec_fn`). UI writes:
  `window.kazmaSave` (never discard an awaited `fetch`).
- **Every thread-taking route declares its rule** in
  `tests/test_thread_route_policy.py` (owner / admin / admin+owner / session).
- **Closures scheduled from a loop bind the loop's names** as defaults or
  method arguments (`test_no_deferred_closure_reads_loop_rebound_names`).
- **`asyncio.to_thread`, never `run_in_executor`** — only `to_thread` carries
  the tenant/workspace/HITL ContextVars into the thread.
- **No blocking I/O inline in `async def`.** A route handler that never
  awaits is a plain `def` (FastAPI threadpools it, context included); one that
  does awaits `to_thread` around its store calls. An `async def` façade over a
  sync body (`KnowledgeIndex.search`) runs the body in `to_thread`. DNS is I/O:
  `await asyncio.to_thread(validate_url, …)` — keep the name so test patches
  still apply (`test_no_blocking_dns_in_async_functions`). Route walks with
  loop-detecting fakes: `tests/test_kb_api_routes.py`. An
  `httpx.AsyncClient(...)` built in async code passes
  `verify=shared_ssl_context()` (`kazma_core.http_tls`, one context built in
  a thread at boot) -- the default loads the CA bundle in the constructor,
  on the loop (`test_async_http_clients_share_the_tls_context`).
- **Every product module is reached** (`tests/test_orphan_modules.py`); a
  module only its own tests import fails, unless allowlisted with a reason.
- **Debt ratchet:** `tests/test_debt_ratchet.py` holds the blind/silent
  exception-handler counts; they may only go down, and lowering them means
  updating the baseline in the same change. It counts untracked files too:
  listing only committed files let a local run pass that CI then failed on a
  new module (2026-09-26).
- **Loop-stall dumps name the next gate entry.** `kazma_core.observability.
  loop_stall` writes every thread's stack to `.kazma/stall-*.txt` when the
  loop is unresponsive for 15s, and the weekly ledger counts them. The frame
  on the loop thread is a sync helper doing I/O from async code; add it to
  `_LOOP_STALL_HELPERS` in `tests/test_static_gates.py` and every async call
  site must then use `to_thread` (`test_loop_stall_helpers_are_not_called_on_the_loop`).
  The first pass (2026-09-23) found 12 helpers from 70 dumps, and 17 more
  call sites of them nobody had caught yet. The second (2026-09-26, 50
  dumps) was led by the auth middleware -- 8 dumps of a per-request
  user-store/session read on the loop, one AFTER the first pass -- then the
  MCP reconnect sweeper (49.7 s) and the readiness probe; 18 names added,
  5 more callers found by the gate itself. Rank the dumps by the first frame
  ABOVE the storage layer (the caller that ran it on the loop is the news,
  not `postgres_pool.execute`). Every dump now leads with the loop thread's
  stack: faulthandler stops at 100 threads, and the eleven dumps of the
  2026-09-25 database hang (one a 318 s stall) had none.

### 36. A chat save the database refuses is spooled, never held only in memory

Live 2026-09-24: a NUL in one tool result made Postgres refuse every save of
one chat ("A reply was produced but NOT saved to the transcript"). The reply
existed only in `SessionManager`'s cache, and the restart that picked up the
fix discarded a finished answer. A cache eviction or `_refresh_from_db` (the
Web UI calls it for every gateway session) would have done the same without
a restart. The NUL is fixed (`pg_helpers.json_dumps`, §35 gate); this section
is the class.

- **`SessionManager._write_durably` is the only way in.** It calls
  `_upsert_db`; if the primary store raises for ANY reason, the whole session
  goes to `kazma_ui/session_spool.py` (SQLite `chat_sessions_spool.db` next to
  the sessions DB, SQLite even under Postgres so it does not share the
  primary's failure mode) and the save counts as durable. It raises only
  when both refuse, and then the callers' "NOT saved" alert fires.
- **Every read overlays the spool** in `_session_from_row`, and spool-only
  sessions are served by `_load_one_from_db` / `list_all` /
  `get_by_thread_id`. The next accepted save of the session clears its entry;
  boot (`_drain_spool`) retries the rest. Delete / clear / evict discard it,
  or a deleted chat returns.
- **Gate:** `test_chat_saves_go_through_the_spool` — `_upsert_db` is called
  only by `_write_durably` and `_drain_spool`, and nothing outside
  `session_manager.py` writes `kazma_chat_sessions`. Behaviour:
  `tests/test_session_spool.py` (its genuine-refusal test runs in the CI
  Postgres job). The migration bundle carries the spool file.
- **Not covered:** a hard crash between the answer finishing and the save
  call. Streaming persists incrementally, so that window is the tail of one
  reply, and the answer is still in the checkpoint.

### 37. Kazma's own stores: declared once, read back by a tool, refused raw, carried whole (`kazma_core/store_registry.py`)

Live 2026-09-25: asked "list me all remaining posts", the agent spent 67 tool
calls and an operator-approved `python_exec` byte-dumping
`agent_artifacts.db`. It could SAVE drafts (`save_proposal`) but had no tool
to READ them; the SQL tool refused with "pass a workspace SQLite file",
`file_read` returned raw SQLite bytes, and `python_exec`'s refusal list had
never heard of the file. Underneath: posting ONE draft stamped its whole set
`proposal_posted`, so 7 of 11 drafts vanished from X Studio and sat on the
14-day age-out meant for spent sets. And "is this one of Kazma's stores?"
had five hand-kept answers (SQL tool, path-policy names, path-policy
location rule, migration exporter, importer) — the bundle silently left
`agent_artifacts.db`, `x_scheduled.db`, `x_posts.db`, `hitl_gates.db`,
`task_ledgers.db`, `rbac.db`, `audit.db` and `llm_calls.db` behind.

- **`STORES` is the one declaration** of every database Kazma keeps: what it
  holds and how it crosses machines (`bundle` / `settings` / `documents` /
  `rebuilt` / `machine` / `legacy`, with a reason when not `bundle`).
  Runtime-named files are `STORE_FAMILIES` + `DYNAMIC_NAME_SITES`. The
  migration exporter and importer derive their lists from it.
- **`TOOL_WRITES`: every tool that can change anything declares where the
  write lands and which tool reads it back** — per WRITER, not per store
  (`agent_artifacts.db` always had a reader: the scratchpad context feed; the
  drafts half had none). A write to a Kazma store must name a read-tier tool
  or a `CONTEXT_FEEDERS` entry; no note excuses it. Adding a write-tier or
  danger tool without an entry fails CI.
- **One predicate, every door, and the refusal names the reader.**
  `is_kazma_store()` (any SQLite file under the data dir except the
  `workspace/` sandbox; `vault.db` / `hitl_gates.db` by name anywhere) is
  what the SQL tools, the file tools (reads AND writes — reads were open
  until this), `python_exec`, `shell_exec`, the commitment exec resolver
  (before the approval card) and the card disclosure all ask;
  `store_refusal()` says what the store holds and which tool reads it. A
  path grant cannot open a store, so the file refusal never offers one.
- **Drafts carry per-item state.** `used_at` / `used_via` / `used_ref` on the
  item; the row's `kind` is derived (`proposal_posted` only when every item
  is used). Marking requires the posting tool's own `"ok": true`. A startup
  heal re-derives sets the old whole-set rule stamped, from Kazma's X
  records (post ledger, schedule) — and leaves a set with no evidence alone.
  `list_proposals` is the reader; `stored_text_for` answers only a ref that
  names ONE draft (the chat gate's rule, now X Studio's too).
- **`{"ok": false}` is a failed tool call** (`tool_registry._json_reports_failure`),
  not only `Error:` / `⚠️` / `Safety:` prefixes.
- **Gates:** `tests/test_store_registry.py` (every DB name in the code
  declared; every writer declares its readback; store writes have a
  no-approval reader; context feeders wired; every store's migration
  disposition, both directions and a real export→import round trip; every
  door refuses every store and names the reader, the user's sandbox DB passes
  every door; no store path built from the process CWD; no
  `with sqlite3.connect()` — it commits and never closes, which is why
  `kazma migrate import` failed on Windows at its first swap until
  2026-09-25). Behaviour: `tests/test_saved_drafts_readback.py`.
- **Not covered:** a store named only by a variable (a command that builds
  the path at runtime passes the text checks — the tools' own path rules and
  the operator's card remain the control); per-tenant isolation of a store
  inside one SQLite file is the store's own job, not this registry's.

### 38. The 2026-09-25 hardening batch — each rule with the gate that holds it

One pass after §37, each item a class with its own gate and negative control.
Read the named test before changing the code it guards.

- **Drafts are retired, never deleted; quoting beats the language lock.**
  `discard_proposal` (tool, `write` tier) and X Studio Dismiss/Restore set
  per-item `used_via="discarded"`; posted/scheduled items are never touched,
  and the commitment gate refuses a discarded `proposal_id` with the restore
  call to use. `language_lock` carries `QUOTED MATERIAL IS EXEMPT` in every
  variant — the lock governs the words the model writes, not stored drafts it
  reproduces (an English lock had hidden 11 Arabic drafts). Gate:
  `tests/test_language_lock_quoting.py` (no prompt constant bans a script
  outright); `tests/test_draft_discard.py`.
- **A SQLite connection used as a context manager commits AND closes.**
  `with sqlite3.connect()` commits and leaves the handle open (it sits in a
  reference cycle), which blocks rename/delete on Windows. Store `_connect()`
  helpers return `db.sqlite_session.committed_and_closed(conn)`. Gate:
  `test_store_registry.py::test_no_raw_connection_opener_is_used_as_a_context`.
- **All Arabic shaping and direction go through `documents/arabic.py`** (§19H),
  with ONE shared reshaper (`functools.lru_cache`): a reshaper per call was
  96% of a layout test's runtime and took a CI chunk down on its timeout. Gate:
  `tests/test_arabic_single_home.py` (only arabic.py imports
  `arabic_reshaper`/`bidi`).
- **A vault miss that is really a missing tenant says so, once.**
  `SecretVault.retrieve` with NO tenant bound that misses a name stored under
  a tenant logs one WARNING per name (tenants named, never the value). A
  caller with its own tenant missing another's key is isolation, and silent.
  Gate: `tests/test_vault_scoped_miss_tripwire.py`.
- **Every install sharing one Postgres settings store is named.** Each server
  boot records `system.installs.<id>` (the id lives in the install's own data
  dir, `<data_dir>/install_id`) and names installs booted in the last 14 days;
  replicas go under `database.shared_store.acknowledged_peers` (INFO, not
  WARNING). `kazma doctor` shows the same, read-only. The data-dir warning
  cannot see a second checkout that relocates nothing — the 2026-09-16 shape.
  Gate: `tests/test_shared_store_peers.py`.
- **No except-branch re-derives the data dir from the CWD.** Sixteen did
  (`Path.cwd() / "kazma-data" / ...`): a second settings.db, a document store
  no backup copies, an IDE sandbox the chat tools did not use, a CWD root
  ADDED to the database client's path allowlist. They raise, deny, or use a
  non-store location now; only the updater keeps one (it runs while its own
  package may not import). Gate 8 in `tests/test_store_registry.py`.
- **Diagnostics are read-only, by scope** (`kazma_core/diagnostic_scope.py`).
  `read_only_diagnostic(name, allow=...)` is a ContextVar (follows
  `to_thread`): every ConfigStore mutator and vault store/delete raises
  `DiagnosticWriteRefused` unless allowed, and read side effects are skipped
  (recall's access bump — `/health/deep` had been keeping one memory "in use"
  forever — and the lazy plaintext→vault migration). Every `/health`,
  readiness and diagnostics route and `kazma doctor` open a scope;
  `/health/deep` allows only its canary key. Gate:
  `tests/test_diagnostics_are_read_only.py` (routes enumerated from source).
- **A write veto is a `_Veto`, never `None`.** It cannot be serialized, so a
  caller that forgets to test it raises instead of writing. With `None`,
  `atomic_update(secret, lambda _: None)` logged "refused to blank" and wrote
  null over the pointer, on both backends. `_write_db_value` is
  backend-aware (the lazy migration never landed on Postgres).
- **Tests stub modules with `tests._module_stubs.stub_modules`**, never
  `patch.dict(sys.modules)` (it evicts every module imported inside it).
  **Tests patch attributes with `monkeypatch.setattr`, never `mod.attr =
  fake`**: nothing restores the assignment, and a later test in the same
  process inherits it (`rs._db_path = ...` hid another file's check once a
  new file shifted fast_test's chunks, 2026-09-26). The debt ratchet holds
  them (`bare_module_attr_assignments`) at zero since all 138 were converted.
  **A test starts from the same state whatever ran before it**: the root
  conftest imports every product module before collection, so no module is
  first imported under a test's patches or environment, and restores the
  environment after every test (a bare `os.environ` write no longer reaches
  the next test). `fast_test.py` deals files to processes round-robin, so
  anything a test inherits from its neighbours changes whenever a test file
  is added. Gate: `tests/test_order_independence.py` (each guard's negative
  control runs the same tests with it off: `KAZMA_TEST_ISOLATION=0`,
  `KAZMA_TEST_PREIMPORT=0`).
  **No test reads a real `.env`** — two Postgres tests did, one the LIVE
  install's by hard-coded path. **`@pytest.mark.postgres` is the Postgres
  job's list** (`scripts/postgres_suite.py`), per test, verified on a real
  Postgres before marking. Gates: `tests/test_module_stubs.py`,
  `tests/test_postgres_suite.py`.
- **Every `KAZMA_*` variable is inventoried** in the generated
  `docs/docs/reference/environment-variables-index.md`
  (`scripts/generate_env_reference.py`) **and described** on the curated page:
  the ratchet in `tests/test_env_reference.py` is at 0, so a new variable
  means a row, written from its call site. A backticked default on that page
  must be the literal the code falls back to
  (`test_documented_defaults_match_the_code` — the page had two caps 25× and
  12× too low). A variable any value of which turns a protection off also
  goes in the security table and `.env.example`, and into
  `SECURITY_ENV_NAMES` (`tests/test_static_gates.py`) unless its name carries
  a marker; the name net alone had missed twenty-one.
- **Bandit's HIGH gate covers `tests/` and `scripts/`; every `# nosec` names
  its rule and a reason** (`tests/test_security_scan_scope.py`).
- **`python_exec`/`code_exec` get the exec denylist's deny-before-card
  floor** from the code's AST (`safety/commitment/python_denylist.py`),
  judged by the shell denylist's own rm/chmod patterns. Computed paths still
  go to the card. Gate: `tests/test_python_exec_denylist.py`.
- **The python_exec sandbox restricts the SNIPPET, not the process**
  (`code_exec._build_sandbox_script`): the snippet runs with its own builtins
  (guarded `__import__`, no exec/eval/compile/breakpoint) and the modules it
  imports run normally. Patching the builtins module broke Python's own
  import system -- 17 of 20 ordinary snippets (`datetime`, `json`, `re`...)
  died with "exec() is disabled" and the operator approved one date sum three
  times. Never patch `builtins` process-wide again. What the runner refuses
  is refused before the card by `code_exec.sandbox_refusal` (commitment exec
  resolver step 1c; not under E2B, which runs code raw). Gates:
  `tests/test_code_exec.py` (the stdlib runs; the snippet's escapes do not;
  negative control) and `tests/test_python_exec_denylist.py`.
- **Restore rehearsal: opt-in, scratch-only** (`backup/restore_rehearsal.py`,
  `backups.pg.restore_rehearsal` / `KAZMA_PG_RESTORE_REHEARSAL`): the weekly
  pass restores the newest dump into `kazma_restore_rehearsal_<epoch>`,
  checks it, drops it; every CREATE/DROP re-checks that exact pattern and
  refuses the live database. Gate: `tests/test_restore_rehearsal.py`.
- **The date guard matches a subject by whole words** (Latin script;
  Arabic stays substring because of clitics), so three-letter heads are safe
  (`tests/test_date_guard_word_match.py`).
- **Async routes that never await are plain `def`s** (§35); the debt ratchet
  counts the rest (`async_route_never_awaits`), and it only goes down.
- **A reload adopts the PATH the OS settings have now**
  (`kazma_core/path_refresh.py`). Windows gives a process its parent's
  environment, and the guard lives from boot to boot, so the operator fixed
  PATH, reloaded, and the server still could not find Docker. The server
  appends the entries it is missing from the machine + user PATH,
  in `KazmaAppBuilder._adopt_process_environment`, straight after
  `setup_logging` (before any tool can spawn; the line lands in kazma.log,
  as does the `.env` ladder line that used to be logged before logging
  existed). Append only: never reorder (a venv stays first), never remove.
  Other OS-level variables still need a `KazmaAgent` restart; Kazma's own
  belong in `.env`, re-read every boot. Gate: `tests/test_path_refresh.py`
  (runs the real app factory; order check with a negative control).
- **Some secrets belong to the install** (`security/vault.py:
  INSTALL_SCOPED_SECRETS`: provider keys, `email.*`, `calendar.*`,
  `cfg:memory.backends.*` — and a credential URL moves into the vault only
  under such a name, or its background readers lose it), and the
  VAULT enforces it: `SecretVault.store` writes those names globally and
  rewrites every tenant copy to match, whatever tenant the caller names
  (`store_install_scoped`, never deletes); `delete` removes every copy.
  ConfigStore has no tenant, but `vault.store` took the request's, so a
  provider key saved in Settings sat under tenant `default`; the registry
  reads with no tenant bound, the `default` rung is closed under
  `KAZMA_PRODUCTION=1`, and every live boot from 2026-09-16 to 2026-09-25
  built the agent on Z.AI with the DeepSeek key right there. Mail had the
  same split from writers that bypassed `email_manager.credentials`
  (`backup/cloud_sync._write_vault`, the agent's secret tool):
  `email.gmail.scopes` held one value for chat and another for background
  work, flagged every boot. Boot runs `consolidate_install_scoped_secrets()`
  before `initialize_model_registry`: provider keys take the NEWEST copy
  (the operator's saves), mail/calendar the GLOBAL one (what mail code
  reads). Do NOT fix a context-less miss by opening the posture gate: it is
  closed in production because an OIDC install can have other tenants. X
  credentials (`cfg:connectors.x.*`) stay per tenant — an account the agent
  posts as is an authorization question. Gates:
  `tests/test_provider_key_install_scope.py`,
  `tests/test_mail_secrets_install_scope.py` (live shapes, negative
  controls, every writer).
- **No model fallback is silent** (`observability/model_fallback.py`). The
  registry's substitution (reported by `get_client` after the lock is
  released) and both failover chains (supervisor, `resilient_chat`) report
  there: an `ops_alerts` page (substitution: twice a day while it lasts;
  failover: 15 min) and `AlertDispatcher.post_banner` (web banner only, with
  a local-path link, e.g. Open Providers). The configured model serving
  again clears the banner; an announced substitution also says "resolved".
  Quiet inside a read-only diagnostic. Gate:
  `tests/test_model_fallback_notice.py::test_every_model_swap_is_reported`
  (every swap log line's class/function must report; negative control), and
  `tests/js/test_alert_banner_link.js` (off-site links are dropped).
- **A remote vector store is used, and reported, from a probe of what it can
  hold** (`memory/backends.py`). pgvector's probe reads the catalog (extension
  installed / installable, may the role create the table, the existing
  table's vector size), never `SELECT 1` — a Postgres without pgvector passes
  that, and the live one did for weeks, refusing every vector statement at
  DEBUG. The result is cached per target in `_REMOTE_VECTOR_STATE` (60 s,
  shared by every instance); search/upsert/delete stop at it;
  `vector_capability` only READS it (routes call it on the loop); boot fills
  it (`probe_vector_backend`). Auto-selected pgvector on a server without the
  extension is the expected fallback (INFO, "full (local)"); a chosen one is a
  WARNING. The table is sized by the embedder (`get_embedding_dim`, the
  sqlite-vec source), and each DDL statement commits alone. Gate:
  `tests/test_vector_store_probe.py` (fakes for every state; a real Postgres
  with and without pgvector).

### 39. The guard carries out reloads, keeps a heartbeat, never dies silently (`scripts/service/kazma_guard.py`)

Live 2026-09-26, three failures in one reload. `--reload` from an operator
shell could not stop the server -- the `KazmaAgent` task runs the guard
elevated, in its own logon session, and the shell got "Access is denied" --
so the old build kept serving. The request file it left behind was the kind
that, from 2026-09-20 to 09-22, woke the guard on every sleep: 3.8 million
health probes in 47 hours, 53 a second. And the guard itself had died twenty
minutes earlier, exit code 1 and nothing in guard.log; the task sat "Ready"
(Task Scheduler does not restart a process that ran and exited, whatever its
code) and Kazma ran with nobody supervising it.

- **The running guard does the stop.** `--reload` writes a dated request;
  the guard handles it within a second, in its sleep loop and during a
  boot. A request older than the running child is already satisfied and is
  cleared; a newer one stops the child and respawns at once. The CLI waits
  for the acknowledgement (`reload_ack`, or a spawn after the request, in
  the state file), starts the task when no guard is alive, and stops the
  server itself only for a guard from before this change -- never a server
  that booted after the request -- and withdraws a request it could not
  apply.
- **Deliberate stops are graceful.** Reload, pause and the guard's own
  shutdown send CTRL_BREAK (Windows) / SIGTERM (POSIX) to the child's
  process group and wait `KAZMA_GUARD_GRACEFUL_STOP_S` (60) before the
  kill; uvicorn drains and the app announces its own stop, so the guard pages
  a reload only when the app could not. An unhealthy child gets 15 s. Never
  signal process group 0: that is every process on the console.
- **One wake-up per request; probes keep their interval.** A handled
  request is remembered by its on-disk signature (a file the guard cannot
  delete is handled once), and `_supervise` never probes more often than
  `KAZMA_GUARD_INTERVAL`, whatever wakes it.
- **The heartbeat answers "is a guard running?"** The guard writes
  `heartbeat` into its state file at least every 10 s in every state it waits
  in; `--status` and `--reload` read it, because an operator shell cannot
  open the elevated processes to ask. The guard hands the server that file's
  path in `KAZMA_GUARD_STATE_FILE`.
- **Never silent.** The whole body of the supervision loop sits in a try
  whose handler logs `guard.internal_error` with the traceback, pages once
  per kind, and keeps supervising the SAME child (a second server next to it
  is the one outcome worse than the error). `_run_supervisor` logs
  `guard.crashed` for anything that still escapes; native crashes go to
  `guard.fault.log`. The sleep loop reads the clock once per pass (a negative
  `time.sleep` raises), and the logger never raises.
- **A recorded PID is verified before it is reaped:** creation time recorded
  at spawn, a python image otherwise. A PID left by a dead guard can belong
  to anything by now.
- **The server says when its guard is gone.** The maintenance cadence runs
  `observability/supervisor_watch.check_supervisor`: with
  `KAZMA_GUARD_STATE_FILE` set (the guard started this server) and no
  heartbeat for 5 minutes, it pages `guard.gone` (critical, every 6 hours
  while it lasts) and logs once when the guard is back. A server started
  without the guard is not watched.
- **A reload's stop is fast because nothing waits out a timeout.** The
  gateway stops its adapters together, and the Discord and Slack readers
  close their websocket the moment shutdown is set
  (`kazma_gateway/adapters/ws_shutdown.closes_on_shutdown`); they used to sit
  in `recv()` until the platform next spoke, costing 5 s each, in sequence, on
  every reload (`tests/test_gateway_prompt_shutdown.py`, real websockets).
  A new socket-reading adapter uses the same helper.
- **The OS brings a dead guard back:** `install_service.py` registers a
  5-minute repeating trigger with `MultipleInstances IgnoreNew`. An existing
  task gets it only when re-registered from an elevated shell (owner action).

Gates: `tests/test_guard_owns_reload.py` (each with a negative control),
`tests/test_guard_integration.py` (the real guard against a fake server:
graceful reload, an ignored stop, a leftover request, the heartbeat),
`tests/test_idle_reload.py`.

### 40. One checkpoint serializer, strict (`kazma_core/checkpoint_serde.py`)

LangGraph's `JsonPlusSerializer` rebuilds typed values from a checkpoint by
calling the class the checkpoint names, and its default is PERMISSIVE (any
class, with a warning). Given an allowlist it is strict: LangGraph's safe
types plus `KAZMA_MSGPACK_TYPES`, anything else back as raw data.

- **Every saver takes `serde=kazma_checkpoint_serde()`** — the gateway's
  CheckpointManager and per-tenant savers, the Postgres saver, KazmaAgent's
  own savers, the shared SQLite saver (`checkpoints_shared` no longer takes a
  serializer: it used to keep whichever its FIRST caller passed, so the
  process's posture depended on boot order), and the CLI's MemorySaver.
- **Every Enum the graph-state modules define is on the list**
  (`kazma_core.agent.state`, `kazma_core.agent.intent.types`). `TaskStatus`
  and `RouteKind` were not: on SQLite they came back as plain strings with
  a warning (Postgres stores `str` inline, so the live install never showed
  it). A new state enum goes on the list.
- Gate: `tests/test_checkpoint_serde.py` — every saver construction in the
  product source, the one construction site, every state enum round-tripped
  as itself, and a class off the list never constructed (negative control:
  LangGraph's permissive mode builds it).

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
  `[x-cloak] { display: none !important; }` lives once in `kazma.css`
  (`base.html` then loads `kazma.v5.css` — last-wins for overlapping
  selectors; do not duplicate HITL/card rules). Any `x-show`-gated panel
  MUST also carry `x-cloak`. Never put `display:flex` (or any `display`) in
  an inline `style` on an `x-show` element — the inline declaration wins
  over Alpine's `display:none` toggle; put flex layout in a CSS class
  instead (see `.system-alerts-banner`).
- **Responsive grids:** use `class="two-col-grid"` (collapses to one column
  ≤768px via `kazma.css`) on any inline `grid-template-columns:1fr 1fr;` —
  bare inline 2-col grids don't collapse and crush on mobile.
- **Every page loads clean — four did not (2026-09-26).** Touring the live
  install page by page found the IDE, workspace, knowledge and settings pages
  throwing on every load; no test had opened them. The rules that broke:
  - **No markup inside a double-quoted directive.** `x-text="a ? '<span
    class="ki">' : b"` ends the attribute at `class=` (a SyntaxError per
    row). Build markup in a component method and bind it with `x-html`.
  - **Icons inserted after load come from `KazmaIcons.span(name)`**, which
    returns them filled. `icons.js` hydrates `[data-icon]` only on DOM ready,
    so a bare `<span data-icon>` inserted later stays empty.
  - **A `<template x-for>` carries only `x-for` and `:key`.** Any other
    binding on it runs OUTSIDE the loop ("s is not defined").
  - **Declare every field a template reads in the component's initial
    data**, and expose a closure constant the template uses (`kb.js`'s `S`).
    A field set only by a loader throws until the loader runs.
  - **Never `x-init="init()"` beside `x-data`.** Alpine 3 calls the
    component's `init()` itself; eleven templates called it again, the app
    shell in `base.html` among them, so every page ran its init twice —
    every load fetched twice and every `setInterval` poller ran twice.
  - **One poller per endpoint per page.** The system-alerts banner reads
    `$store.notifications` (which polls `/api/alerts/recent`) instead of a
    10 s poller of its own (`tests/js/test_alert_banner_link.js`).
  - **Settings mixins compose by descriptor** (`settings.js`):
    `Object.assign` evaluated every getter once and froze it, so the
    Packages, Skills and Tools filters never worked.
  - **The CodeMirror bundle checks its own load order**
    (`scripts/vendor_codemirror.py`: every `require()` bundled earlier, or
    the build fails). One missing addon stopped nine editor modes loading.
  Gates: `tests/e2e/test_pages_load_clean.py` (every nav page in its own
  tab: no uncaught error, every directive compiles, every bundled mode
  registered), `tests/test_alpine_templates.py` (every directive in every
  template compiles in node; `<template x-for>` scope; no second `init()`),
  `tests/js/test_settings_mixins.js`, `tests/test_vendor_codemirror.py` —
  each with a negative control.
- **A polled route never calls a rate-limited API per poll.**
  `/api/github/status` spent ~2,000 GitHub calls an hour per open Workspace
  tab (10 s poll, fetched twice per tick, three calls each). It now keeps one
  status per (owner, repo, token fingerprint) for 30 s, fetched once at a time
  (`_status_once`: single-flight), reads its stores in a thread, and the page
  refreshes on a new connection only. `tests/test_github_status_cache.py`.

## Server Management

> **RULE (user directive, 2026-08-15, amended 2026-09-26): never start the
> Kazma server and never kill it by hand.** Code reaches the running server
> only through the guard's reload, below.

> **RULE (user directive, 2026-09-02): NEVER edit the live install at
> `C:\Users\balfa\kazma`.** The dev repo (`G:\GitHubRepos\kazma`) is where all
> work happens. Do not write, copy, patch, or revert ANY file there (not even
> static assets that the server would pick up "without a restart", and not
> even to verify a fix), and do not change its git config or history.

> **Deploys (operator's standing order, 2026-09-26).** The agent may deploy
> the live install itself: push to `origin/main`, then
> `git -C C:/Users/balfa/kazma pull` (a plain merge -- the clone keeps a
> local `kazma.yaml` delta, so it is never a fast-forward; pull only a tree
> with no modified tracked files), then reload with the LIVE install's guard,
> then verify the new commit is serving and test through the UI.

```powershell
# The RUNNING GUARD carries out the reload: it stops the server gracefully
# (the app's shutdown hooks run) and starts the code on disk (§39). Run the
# guard script OF THE INSTALL BEING RELOADED -- the request lands in
# <that install>/.kazma. --when-idle waits until no chat turn is running
# (GET /health/activity, local callers only); exit 3 = still busy at
# --idle-timeout, nothing was touched.
& 'C:\Users\balfa\kazma\.venv\Scripts\python.exe' 'C:\Users\balfa\kazma\scripts\service\kazma_guard.py' --reload --when-idle
& 'C:\Users\balfa\kazma\.venv\Scripts\python.exe' 'C:\Users\balfa\kazma\scripts\service\kazma_guard.py' --status
```

`--status` says `guard : running` or `NOT RUNNING`, from the guard's
heartbeat. With no guard running, `--reload` starts the `KazmaAgent` task and
the new guard clears the old server with its own rights, so a reload never
needs an elevated shell.

`--reload` also picks up a tool added to PATH while Kazma ran (§38). A
new *guard* (its own code, or other OS-level variables) still needs the
`KazmaAgent` task restarted.

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
- `docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md` — Binding industrial audit (waves 0–8 shipped). Do **not** follow dump `AUDIT_DEEP_2026-09-01.md` Part 6 order.
- `docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md` — Deep-structure audit (22 findings, change-impact map, CI recovery, Telegram desync §20)
- `docs/audits/AUDIT_PRODUCTION_READINESS_2026-07-21.md` — Historical production audit (2026-07-21)
- `docs/audits/AUDIT_DOCUMENT_CERTIFICATION.md` — Document cert report
- `docs/plans/HITL_GATE_REGISTRY_PLAN.md` — Gate registry (P6)
- `docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md` — Turn Delivery V2
- `docs/plans/CONTEXT_INTEGRITY_HARDENING_PLAN.md` — Context integrity
- `docs/plans/GUARD_OPS_ALERTING_CAUSE_QUALITY.md` — Deferred Guard/ops alerting sprint
- `docs/plans/done/DOCS_CONSOLIDATION_PLAN.md` — Docs consolidation plan (completed)
- `CHANGELOG.md` — Sprint history
- Live docs only under `docs/docs/` (Docusaurus). Do not resurrect retired `docs-v2` / loose handover trees.
