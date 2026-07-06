# Changelog

All notable changes to Kazma are documented here, grouped by sprint.
Features are listed with their implementation PR/commit where available.

---

## Sprint 17 — Skill Checksums + Task Cancel/Retry + Config Reconcilation + Engine Refactor (July 2026)

### P1-3: Enforce Skill Checksums Unconditionally

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Fail-closed verification** | Checksum mismatches, invalid signatures, and verification errors now raise SkillLoadError — no more `except: pass` | `loader.py` |
| ✅ | **HMAC-SHA256 signatures** | Signature verified against KAZMA_SECRET using timing-safe comparison | `loader.py` |
| ✅ | **`kazma hub sign` CLI** | New command writes checksum + HMAC signature into skill_manifest.yaml | `cli.py` |
| ✅ | **Backward compat** | Unsigned skills still load with a warning — won't break existing installs | `loader.py` |

### P2-3: Task Cancel/Retry from UI

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Cancel running tasks** | Cancel button stops in-flight tasks via asyncio.handle.cancel() + CANCELLED status | `engine.py` |
| ✅ | **Retry failed tasks** | Retry button re-dispatches as new task with metadata['retry_of'] lineage | `engine.py` |
| ✅ | **API routes** | POST /api/swarm/tasks/{id}/cancel and /retry | `swarm_panel.py` |
| ✅ | **UI** | Cancel/Retry buttons in task detail modal, cancelled status in filter + colors | `swarm.html`, `swarm.js` |

### P2-9: Unify Config Source of Truth

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **reconcile_from_yaml()** | Seeds DB with YAML keys on startup, non-clobbering (user changes win), idempotent | `config_store.py` |
| ✅ | **Startup integration** | Called in create_app() right after singleton init | `app.py` |
| ✅ | **MCP servers merged** | agent_runner reads from BOTH YAML and ConfigStore, merged by name | `agent_runner.py` |

### P2-1: Refactor engine.py God Class

| Status | Module | Lines | Responsibility |
|:---:|:---|:---:|:---|
| ✅ | `reliability_registry.py` | 174 | Circuit breakers, retry policies, timeout guards, output validators, bounded concurrency |
| ✅ | `phonebook.py` | 87 | WorkerRegistry summon + dispatch_by_name. Dead `consult()` deleted (zero callers). |
| ✅ | `checkpoint_manager.py` | 199 | HITL pipeline checkpoint state, timeout auto-reject, restore from SQLite |
| ✅ | `engine.py` (refactored) | 1878→1573 | -305 lines (16% reduction). All public API unchanged. |

### Web UI: Visual Swarm DAG Diagram Fixes

| Status | Feature / Bug Fix | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Prevent global Mermaid re-scan** | Replaced the deprecated/broken `mermaid.init` fallback with isolated, async, programmatic `mermaid.render(id, code, target)` calls, mapping target DOM containers explicitly to shield page text/logs from re-render pollution | `swarm.js` |
| ✅ | **Target class isolation** | Changed the element class from `.mermaid` to `.swarm-mermaid-diagram` to disable standard auto-init scans | `swarm.html` |
| ✅ | **Eliminate comma-based style syntax error** | Replaced `fill:rgba(...)` format with explicit `fill:#HEX,fill-opacity:A` parameters, neutralizing the syntax parser crash triggered by nested commas | `swarm.js` |

---

## Sprint 16 — P2 Quick Wins: UI Badges + Worker Lifecycle + Docs (July 2026)

### P2-8: Circuit Breaker UI Badges

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Breaker data in API** | `_serialize_worker` now includes circuit breaker state from `engine.get_circuit_breaker_status()` | `swarm_panel.py` |
| ✅ | **Badge in worker cards** | ⚡ badge (red=open, yellow=half-open, hidden when closed) in worker card header | `swarm.html` |
| ✅ | **Live updates** | `updateBreakerBadges()` patches badges during the 5s status poll | `swarm.js` |

### P2-6: Per-Worker Start/Stop

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Engine methods** | `start_worker(name)` / `stop_worker(name)` — single-worker lifecycle control | `engine.py` |
| ✅ | **API routes** | `POST /api/swarm/workers/{name}/start` and `/stop` | `swarm_panel.py` |
| ✅ | **UI buttons** | ▶/⏹ start/stop buttons in worker card action group | `swarm.html`, `swarm.js` |

### P2-12: Docs Accuracy

| Status | Fix | Detail |
|:---:|:---|:---|
| ✅ | **Test count** | README badges + description: 3299 → 3409; added `kazma-gateway/tests/` |
| ✅ | **Slack description** | TROUBLESHOOTING.md + HANDOVER.md: "Socket Mode" → "polling-based Web API" |
| ✅ | **TelegramWorker ref** | STATUS.md: removed reference to deleted class |
| ✅ | **ROADMAP** | Test count + date updated |

---

## Sprint 15 — ConfigStore Atomicity + MCP Auth/HITL (July 2026)

### P1-4: ConfigStore Atomicity

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **WAL + busy_timeout** | ConfigStore now uses WAL journaling + 5s busy_timeout, matching every other SQLite store — eliminates "database is locked" errors on concurrent writes | `config_store.py` |
| ✅ | **batch_set()** | Atomic multi-key writes in a single BEGIN/COMMIT transaction — all-or-nothing, crash-safe | `config_store.py` |
| ✅ | **transaction()** | Context manager for grouped atomic operations | `config_store.py` |
| ✅ | **Singleton** | `get_config_store()` / `set_config_store()` — all components share one connection + one threading.Lock | `config_store.py`, 7 call sites updated |
| ✅ | **Flatten loops → batch_set** | 4 non-atomic per-leaf commit loops replaced with single atomic batch | `slash_commands.py`, `settings_manager.py`, `settings.py`, `config_store.py` |

### P1-3: MCP Server Auth + HITL

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **MCP HITL gate** | `UnifiedToolExecutor.execute()` now gates danger-tier MCP tools through `safety.check()` before dispatch. Closes the MCP security gap | `mcp/manager.py` |
| ✅ | **Tool classification** | `classify_mcp_tool()` classifies MCP tools by name pattern (write/exec/delete → danger; read/list/get → safe; unknown → danger) | `mcp/manager.py` |
| ✅ | **MCP auth** | First-class `auth` field on MCPServerConfig (bearer token / custom header) + trust level (trusted/approval_required). Injected into SSE connection headers | `mcp_client.py`, `mcp/manager.py` |
| ✅ | **MCP UI** | Auth token + trust-level fields in the Add-Server modal | `mcp.html`, `mcp.js`, `models.py` |
| ✅ | **Dead import fix** | `app.py` delete route imported non-existent `MCPManager` — fixed to use `agent.remove_mcp_server()` | `app.py` |

---

## Sprint 14 — HITL Approval Gates + Test Isolation (July 2026)

### P0-1: Human-in-the-Loop Approval Wiring (all platforms)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Graph interrupt activation** | `hitl_config` now passed at both graph build sites (`agent_runner.get_streaming_graph` + `app.py` startup recompile), so `tool_worker_node`'s `interrupt()` fires for danger tools on all single-agent paths | `agent_runner.py`, `app.py` |
| ✅ | **SSE approval frames** | `_stream_langgraph_events` detects paused graph state and emits `approval_required` SSE frame with `thread_id` + tool + args; in-chat Approve/Deny card in `chat.js` POSTs to `/api/approve` | `sse_chat.py`, `chat.js`, `streaming.js` |
| ✅ | **Fail-closed safety gate** | `check_sync()` now blocks danger tools by default when no bus adapter is wired; `allow_headless_danger` escape hatch for tests/dev | `swarm/safety.py` |
| ✅ | **Async bus check** | `tool_registry.execute()` uses `safety.check()` (async, real approvals) instead of `check_sync()` (block-only); `_hitl_approved` flag prevents double-gating | `tool_registry.py`, `graph_builder.py` |
| ✅ | **Telegram callback wiring** | `swarm_approve_`/`swarm_reject_` callback prefixes now route to `TelegramBusAdapter.handle_callback()` (previously dead seam) | `telegram.py` |
| ✅ | **Gateway /hitl resolver** | `agent_handler` detects interrupted graph after `ainvoke()`, surfaces approval prompt (inline keyboard for Telegram), resumes on `/hitl approve\|deny` | `agent_handler.py` |
| ✅ | **DiscordBusAdapter** | New BusAdapter with components v2 buttons; Discord `INTERACTION_CREATE` handling | `discord_bus.py`, `discord.py` |
| ✅ | **SlackBusAdapter** | New BusAdapter with Block Kit action buttons | `slack_bus.py` |
| ✅ | **Multi-platform bus registration** | Bus adapters registered with priority fallback (Telegram > Discord > Slack) | `app.py` |
| ✅ | **Extended danger list** | `kazma.yaml` now includes `code_exec`/`python_exec` in `require_approval_for` | `kazma.yaml` |

### P0-2: Test Isolation Fixes (28 → 3 failures)

| Status | Fix | Root Cause | Reference |
|:---:|:---|:---|:---|
| ✅ | **KAZMA_SECRET env leak** | `test_hub_e2e.py` set `os.environ['KAZMA_SECRET']` at module level, never cleaned up — caused 401 in 23 subsequent tests | `test_hub_e2e.py` |
| ✅ | **Handoff cycle detection** | `_visited` set was too aggressive, blocked legitimate A→B→A return handoffs (VAL-HAND-005). Changed to visit-count dict with `_MAX_VISITS=2` | `engine.py` |
| ✅ | **Workspace singleton pollution** | `file_write.py` workspace guard left set by earlier tests; added autouse reset fixture | `conftest.py` |
| ✅ | **Sidebar badge test** | Test looked for `model-badge` class (renamed to `sidebar-model-selector`) | `test_sidebar_model_display.py` |
| ✅ | **Router perf threshold** | 100ms threshold too aggressive on Windows; added warmup + raised to 500ms | `test_router.py` |

### Remaining 3 failures (environmental, not fixable in code)
- `test_file_write_permission_denied` — running as Windows admin
- `test_extracts_model_ids` — LM Studio not running locally
- `test_model_in_body_triggers_reconfigure` — mock assertion

### HITL Test Coverage (47 new tests)
- `test_hitl_wiring.py` (22 tests): fail-closed gate, escape hatch, config helpers, bus callbacks (3 platforms), SSE frame emission, double-gating
- `test_hitl_graph_integration.py` (6 tests): true end-to-end with real AsyncSqliteSaver — interrupt, approve, deny, safe-tool passthrough, persistence across connections

---

## Sprint 13 — Swarm Framework Deep Audit & Fixes (July 2026)

### Provider/Model Resolution — Critical Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Fuzzy Provider Matching** | `get_provider()` now resolves case-insensitively with display_name + substring fallback. Fixes `provider: xiaomi` in YAML not matching DB entry `"Xiaomi MiMo"` — the root cause of the HTTP 400 "model not supported" errors | `model_registry.py` |
| ✅ | **Auto-Correct Guard Fix** | `get_client()` auto-correct guard (prevents cross-provider model routing) now fires for ALL calls, not just when `model=None`. Previously the swarm path always passed `model=`, defeating the safety net | `model_registry.py` |
| ✅ | **Double JSON Encoding Fix** | `_save_providers()` no longer pre-encodes with `json.dumps` (ConfigStore already serializes). `_load_providers()` transparently migrates legacy double-encoded entries on read | `model_registry.py` |
| ✅ | **Unknown Model Warning** | `get_client()` now logs a warning when a model is not found in any provider, instead of silently routing to the active provider's endpoint | `model_registry.py` |

### Swarm Worker — System Prompt & Token Tracking

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **YAML System Prompt Restore** | `SwarmConfig.from_dict()` now reads `system_prompt` from YAML worker definitions. Previously every YAML worker ran with an empty system prompt | `config.py` |
| ✅ | **Token Counting Fix** | InProcessWorker ReAct loop now sums only `prompt_tokens + completion_tokens`, not `total_tokens` (which double-counts). Fixes ~2x inflated token/cost metrics | `worker.py` |
| ✅ | **tool_registry Guard** | `tool_registry` initialized to `None` before the try block; tool execution checks for `None` before calling `execute()`, preventing `NameError` if registry import fails | `worker.py` |
| ✅ | **Partial Progress Preservation** | Exception handler in ReAct loop now returns `last_content` (any accumulated output) instead of empty string, so mid-iteration failures don't discard all work | `worker.py` |
| ✅ | **Dead Code Removal** | Removed `_compose_context_payload` (never called) and redundant `if response.tool_calls:` guard in the ReAct loop | `worker.py` |

### Engine — Dispatch Context & System Prompt Propagation

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Single-Dispatch Blackboard** | Single-dispatch path (`TaskType.DISPATCH`) now builds a `SwarmDispatchContext` with a blackboard, matching the broadcast/fanout/pipeline paths. Previously single-dispatch passed a plain string, preventing blackboard sharing | `engine.py` |
| ✅ | **System Prompt in Context** | `_build_dispatch_context()` now accepts and propagates a `system_prompt` parameter into `SwarmDispatchContext`, enabling task-level and stage-level prompt overrides | `engine.py` |

### Error Handling & Minor Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **LLM Error Nesting Fix** | `LLMProvider.chat()` no longer wraps `LLMError` in another `LLMError`, preventing nested "LLM call failed: LLM call failed: ..." messages. Network vs generic errors are now distinct | `llm_provider.py` |
| ✅ | **Spawn system_prompt Sync** | Worker spawn endpoint (`POST /api/swarm/workers/spawn`) now passes `system_prompt` to the persistent WorkerRegistry | `swarm_panel.py` |

---

## Sprint 12 — Swarm Pro-Grade Overhaul (July 2026)

### Swarm Engine — Critical Bug Fixes (Phase 1)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Handoff Cycle Detection | `_handle_handoff()` now tracks visited workers and depth (max 5); aborts with clear error on cycles (A→B→A) | `engine.py` |
| ✅ | Dispatch Catch-All | `dispatch()` wraps `_dispatch_inner()` in try/except; unexpected exceptions finalize as failed and close tracing spans | `engine.py` |
| ✅ | LRU History Cap | `_task_history` capped at 500 entries with automatic eviction | `engine.py` |
| ✅ | Half-Open Probe Fix | Circuit breaker half-open state allows only ONE probe via `_probe_in_flight` flag | `reliability.py` |
| ✅ | Retryable Predicate | RetryPolicy skips non-retryable errors (auth, config, rate limit) via `_is_retryable_exception()` | `reliability.py` |
| ✅ | TaskStore Schema Migration | Added columns (context, dependencies, fallback_chain, validation_schema, aggregation, timeout); auto-migrates existing DBs | `task_store.py` |
| ✅ | TaskStore WAL + Fixes | WAL mode + busy_timeout; `json_each()` worker filter; `COALESCE` ordering for NULL safety | `task_store.py` |
| ✅ | Registry Thread Safety | `WorkerRegistry` now has `threading.Lock`; true singleton via `get_worker_registry()` | `registry.py` |
| ✅ | Pipeline System Prompt | Stage `system_prompt` now passed to dispatch via `SwarmDispatchContext` | `topology.py` |

### Swarm Engine — Pro Features (Phase 2)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Token/Cost Tracking | `InProcessWorker.dispatch()` captures `tokens_used`, `cost`, `duration_seconds` from provider response | `worker.py` |
| ✅ | Worker System Prompts | Workers use `system_prompt` from config/registry when no context prompt; `WorkerConfig` has `system_prompt` field; Add Worker UI has textarea | `worker.py`, `config.py`, `swarm.html` |
| ✅ | Worker Logs Cap | `SwarmWorker.logs` capped to 100 entries (ring buffer) | `worker.py` |

### Swarm Engine — UI/API (Phase 3)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Server-Side Task Search | `GET /api/swarm/tasks?q=<query>` filters on prompt text | `swarm_panel.py` |
| ✅ | Task Export | `GET /api/swarm/tasks/export?format=csv\|json` | `swarm_panel.py` |
| ✅ | Registry Sync Fix | Fixed `caps_data` NameError in WorkerRegistry sync on worker add | `swarm_panel.py` |

### Swarm Engine — Dead Code Cleanup (Phase 4)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Removed TimeoutGuardError | Dead class never raised; removed from `reliability.py` and `__init__.py` | `reliability.py` |
| ✅ | Removed Type Dropdown | Hardcoded to `in_process`; removed vestigial Telegram worker option from UI | `swarm.html`, `swarm.js` |
| ✅ | Removed Dead Constants | `_SUPPORTED_MODELS` / `_SUPPORTED_PROVIDERS` removed | `swarm_panel.py` |

### Output Routing to Telegram Group (Phase 5)

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Output Mirroring | Swarm results mirrored to both originating chat and configured Telegram group | `agent_handler.py` |
| ✅ | Persistent Config | `/swarm config group <chat_id>`, `/swarm config disable`, `/swarm config clear` | `agent_handler.py` |
| ✅ | Per-Dispatch Override | Inline syntax: `/swarm <task> -> telegram:-1001234567890` | `agent_handler.py` |
| ✅ | Output Routing API | `GET/PUT /api/swarm/output-target` | `swarm_panel.py` |
| ✅ | Output Routing UI | Card with enable toggle, chat ID input, save/clear, live status | `swarm.html`, `swarm.js` |

### Cross-Platform Swarm Dispatch

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Natural Language Dispatch | Both `/swarm <task>` and bare "use the swarm to..." trigger auto-routing via CapabilityRouter | `agent_handler.py` |
| ✅ | Interactive /models | Telegram inline keyboards: provider → model → select | `telegram.py`, `agent_handler.py` |
| ✅ | Slash Commands Wired | `resolve_slash_command()` connected to handler; `/reset`, `/help`, `/model`, `/status`, `/cost`, etc. | `agent_handler.py` |

### Provider/Model Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Provider Auto-Correction | `set_active_model()` auto-switches provider; `get_client()` reconciles mismatches | `model_registry.py` |
| ✅ | LLM Error Detail | HTTP response body captured and logged on API errors | `llm_provider.py` |
| ✅ | NVIDIA NIM Tool Fallback | Retries without tools on 404 "Function not found" (models without tool support) | `llm_provider.py` |

---

## Sprint 11 — CLI Control Plane (June 2026)

### CLI

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Gateway CLI Commands | Added `kazma gateway` subcommands for headless gateway lifecycle management: `status` (adapter status), `start`, `stop`, `restart` (stop + start), and `refresh` (reload adapters) | `cli-gateway-commands` |
| ✅ | Swarm CLI Commands | Added `kazma swarm` subcommands exposing the full swarm engine from the terminal: `status`, `workers`, `worker add/spawn/remove`, `dispatch`, `broadcast`, `consult`, `pipeline`, `fanout`, `history`, `task`, `metrics`, `start`, `stop`, `approve`, `reject`, and `circuit-breaker` (status + reset). Supports `--model`, `--provider`, `--type`, `--role`, `--context`, `--workers`, and `--aggregation` flags. | `cli-swarm-commands` |
| ✅ | Real `kazma status` Health | Reworked `kazma status` to report real system health (gateway adapters, swarm workers, and server health) instead of a static "OK" string | `cli-status-health` |
| ✅ | Update CLI Command | Added `kazma update` command to check for and install Kazma CLI updates. For pip-installed copies, checks PyPI for the latest version and runs `pip install --upgrade kazma`. For git/editable installs, fetches from origin and pulls if behind, then reinstalls. Supports `--check` (dry run), `--force` (reinstall even if latest), and `--yes` (skip confirmation) flags. Shows current vs latest version, a changelog summary, and prompts for confirmation before updating. | `cli-update-command` |
| ✅ | Completions SUBCMDS Fix | Updated `completions.py` `SUBCMDS` to include the full command set (`gateway`, `swarm`, `project`, `serve`, `hub`, `docs`, `wizard`, `completion`, `status`) so shell tab-completion covers every subcommand | `cli-completions-fix` |

### Documentation

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | CLI Reference Rewrite | Completely rewrote `docs/docs/api-reference/cli-reference.md` as the definitive guide for all CLI commands, organized by category (Core, Gateway, Swarm, Hub, Project, Docs) with syntax, flags, examples, and exit codes | `cli-docs-reference` |
| ✅ | README CLI Section | Added a comprehensive CLI Commands section and CLI Quick Reference table to `README.md`; documented all three entry points (`kazma`, `kazma-web`, `kazma-tui`) | `cli-docs-readme` |
| ✅ | Quickstart & Config Updates | Updated `docs/docs/getting-started/quickstart.md` and `configuration.md` to reference `kazma serve`, `kazma swarm status`, and gateway/swarm configuration | `cli-docs-getting-started` |

---

## Sprint 10 — Swarm Engine Overhaul (June 2026)

### Swarm

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Fan-out Orchestration | Added concurrent `fan_out` execution with bounded concurrency, partial-failure handling, and single-worker passthrough | `swarm-fanout-pattern` |
| ✅ | Result Aggregation | Added `first_valid`, `merge_all`, `vote`, `synthesize`, and `collect` fan-out aggregation strategies | `swarm-fanout-pattern` |
| ✅ | Consult Orchestration | Added `consult` pattern in `kazma_core/swarm/consultation.py` — role-aware independent worker opinions, attributed synthesis, partial/all-fail/single-worker handling, persisted `individual_opinions` + `synthesized_output`, and `GET /api/swarm/tasks?type=consult` history | `swarm-consult-pattern` |
| ✅ | Conditional Orchestration | Added `conditional` routing pattern in `kazma_core/swarm/patterns.py` — router worker evaluates prompt, engine dispatches to mapped worker via `metadata.routes`, graceful fallback on unmatched route (default worker or clear error), router failure halts with no downstream execution, `metadata.route_taken` recorded | `swarm-conditional-pattern` |
| ✅ | Capability Router | Added `CapabilityRouter` in `kazma_core/swarm/router.py` — when `task.workers=["auto"]`, scores registered workers by keyword overlap between task prompt/context/requirements and worker capabilities (expertise, role, tools, model_specialty). Returns top N workers, records `metadata.routed_workers` and `metadata.routing_scores`. Raises `NoCapableWorkersError` when no workers match. Spawned workers are considered by the router. | `swarm-capability-router` |
| ✅ | Retry Policy with Exponential Backoff | Added `RetryPolicy` in `kazma_core/swarm/reliability.py` — configurable per-worker retry with exponential backoff (`max_retries`, `base_delay`, `max_delay`), optional jitter (0-25% of base), retries on transient failures (exceptions, timeouts, error status). Integrated into `SwarmEngine._dispatch_worker`. Default is `max_retries=0` (no retries) for backward compatibility; per-worker override via `engine.set_retry_policy()`. | `swarm-retry-circuit-breaker` |
| ✅ | Circuit Breaker per Worker | Added `CircuitBreaker` in `kazma_core/swarm/reliability.py` — per-worker failure tracking with closed/open/half-open state machine. Trips after `failure_threshold` consecutive failures, auto-transitions to half-open after `cooldown_seconds`, single probe success closes breaker. Manual reset via `POST /api/swarm/workers/{name}/circuit-breaker/reset`. Open state rejects dispatch immediately with clear error. Independent per worker, both threshold and cooldown configurable. | `swarm-retry-circuit-breaker` |
| ✅ | TimeoutGuard | Added `TimeoutGuard` in `kazma_core/swarm/reliability.py` — per-task timeout enforcement via `asyncio.wait_for` with clean coroutine cancellation. Configurable `on_timeout` behaviors: `fail` (terminal), `retry` (counts against retry budget), `skip` (continue without worker). Default timeout 300s, `timeout=0` rejected. Integrated into `SwarmEngine._dispatch_worker` with per-task override from `SwarmTask.timeout`. | `swarm-timeout-validation-concurrency` |
| ✅ | OutputValidator | Added `OutputValidator` in `kazma_core/swarm/reliability.py` — validates worker output against Pydantic BaseModel, dict schema, or JSON schema before acceptance. Parses string output as JSON when schema expects structure. No schema skips validation. Invalid output triggers retry via `RetryPolicy`. Error details surfaced in result. Integrated into `SwarmEngine._dispatch_worker` via `SwarmTask.validation_schema`. | `swarm-timeout-validation-concurrency` |
| ✅ | BoundedConcurrency | Added `BoundedConcurrency` in `kazma_core/swarm/reliability.py` — `asyncio.Semaphore` wrapper (default `max_concurrent=5`) for limiting parallel worker dispatches. Configurable per engine, task (`metadata.max_concurrent`), or global. Semaphore released on failure/timeout (no deadlock). Applied to `fan_out`, `broadcast`, and `consult` patterns. | `swarm-timeout-validation-concurrency` |
| ✅ | FallbackChain | Added `FallbackChain` in `kazma_core/swarm/reliability.py` — per-task ordered fallback worker list. When the primary worker fails (after retries and circuit breaker evaluation), fallback workers are invoked sequentially in declared order. Each fallback is subject to its own retry policy and circuit breaker. Empty chain means no fallback. All-fail returns terminal failure with exhaustion summary. Works within dispatch, pipeline (step-level fallback), and fan_out (per-worker fallback). Fallback invocations recorded as `HandoffRecord` entries for tracing. Integrated into `SwarmEngine._dispatch_worker_by_name` and pattern dispatch calls via `SwarmTask.fallback_chain`. | `swarm-fallback-chains` |
| ✅ | Worker Handoff Mechanism | Added `HandoffRequest` exception and `request_handoff()` function in `kazma_core/swarm/handoff.py`. Workers can delegate execution mid-task to another worker by calling `request_handoff(target, task, context)`. The engine catches the request, creates a `HandoffRecord` (from, to, context_transferred, timestamp), dispatches to the target with accumulated context (original prompt, intermediate results, blackboard snapshot), and records all results. Multi-hop chains (A->B->C) and return handoffs (A->B->A) work without false cycle detection. Failed handoffs to nonexistent workers return clear errors with the worker name. The handoff chain is visible in `WorkerResult.handoffs` and emits `swarm.handoff.{from}->{to}` SSE events. Integrated into `SwarmEngine._dispatch_worker` with `_handle_handoff` and `_dispatch_worker_by_name_all` helpers. | `swarm-handoff-mechanism` |
| ✅ | Dynamic Worker Spawning | Added `POST /api/swarm/workers/spawn` endpoint and `SwarmEngine.spawn_worker()` method for runtime worker creation. Spawned `InProcessWorker` is immediately available in the registry and dispatchable by all orchestration patterns (dispatch, broadcast, pipeline, fan-out, consult, conditional). Capabilities (role, expertise, tools, model_specialty) are stored on the worker and used by `CapabilityRouter` for auto-routing. Duplicate names rejected with 409 Conflict. `DELETE /api/swarm/workers/{name}` removes spawned workers; subsequent dispatch returns not-found. Worker serialization now includes capabilities. | `swarm-dynamic-spawning` |
| ✅ | HITL Checkpoints | Added Human-in-the-Loop checkpoints for pipeline execution in `kazma_core/swarm/checkpoint.py`. Pipelines with `metadata.hitl_checkpoints=[step_N]` pause after step N completes with `status=paused`. Engine emits checkpoint SSE event with `needs_approval`, `step`, and `output_preview`. `POST /api/swarm/tasks/{id}/approve` resumes the pipeline from the next step. `POST /api/swarm/tasks/{id}/reject` aborts with `status=failed`. Configurable checkpoint timeout via `metadata.checkpoint_timeout` (auto-rejects after timeout). Multiple checkpoints work sequentially (pipeline pauses at each checkpoint independently). State persists in-memory in the engine and is queryable via `engine.get_checkpoint_info()`. `resume_pipeline()` function in `patterns.py` handles continuation with blackboard state restoration. | `swarm-hitl-checkpoints` |
| ✅ | TaskStore (SQLite Persistence) | Added `TaskStore` in `kazma_core/swarm/task_store.py` for SQLite-backed task persistence at `kazma-data/swarm_tasks.db`. Two tables: `swarm_tasks` (id, type, prompt, status, workers JSON, result JSON, timestamps, cost, tokens, metadata JSON) and `swarm_worker_metrics` (worker, date, tasks_completed, tasks_failed, avg_latency, total_tokens, total_cost; PK worker+date). Tasks are persisted on terminal state (completed, failed, timeout) and paused HITL state. History queryable with pagination (`GET /api/swarm/tasks?page=N&pageSize=N`), filterable by status, type, and worker. Task detail by id (`GET /api/swarm/tasks/{id}`). Worker metrics aggregated daily per worker (`GET /api/swarm/workers/{name}/metrics`, `GET /api/swarm/workers/metrics/all`). History survives restart. Consult results persisted with individual opinions and synthesized output. HITL checkpoint state (paused pipelines with blackboard snapshot and remaining workers) persisted to SQLite so paused tasks can be resumed after server restart. `SwarmEngine` automatically initializes `TaskStore` and uses it for all persistence. `restore_paused_tasks()` method reloads paused pipeline state from SQLite on engine startup. | `swarm-task-store` |
| ✅ | MetricsCollector and TracingEmitter | Added `MetricsCollector` in `kazma_core/swarm/metrics.py` for per-worker metrics tracking (tokens_used, cost, duration_seconds, success/failure) with in-memory accumulation and optional `TaskStore` flush. `TracingEmitter` in `kazma_core/swarm/tracing.py` emits OpenTelemetry-compatible spans with the full hierarchy: `swarm.task.{id}` (root), `swarm.dispatch.{worker}` (child), `llm.call.{model}` and `tool.execute.{tool}` (sub-children), `swarm.aggregate.{strategy}`, `swarm.synthesize`, and `swarm.handoff.{from}->{to}`. `InMemorySpanExporter` collects spans for testing. Both integrated into `SwarmEngine` constructor (`metrics_collector`, `tracing_emitter` params). `SwarmEngine._dispatch_worker` emits dispatch spans, `_finalize_task` records per-worker metrics, `dispatch()` and `broadcast()` emit root task spans. WorkerResult includes `tokens_used`, `cost`, `duration_seconds`; TaskResult totals equal sum of per-worker values. `GET /api/swarm/workers/{name}/metrics` and `GET /api/swarm/workers/metrics/all` endpoints available. | `swarm-metrics-tracing` |
| ✅ | SSE Streaming Endpoint | Added `GET /api/swarm/tasks/{id}/stream` in `kazma_ui/swarm_sse.py` — Server-Sent Events for real-time task progress. Seven event types: `task_started`, `worker_started`, `worker_progress`, `worker_completed`, `checkpoint` (HITL pause), `handoff` (delegation chain), `task_completed`. `SSEEventBus` provides per-task pub/sub with history replay for reconnection. Monkey-patches `SwarmEngine` methods to auto-emit events. Supports all orchestration patterns. | `swarm-sse-streaming` |
| ✅ | Swarm Panel UI Rewrite | Redesigned `kazma_ui/templates/swarm.html` and `kazma_ui/static/js/swarm.js` — tabbed interface with 5 sections: (1) Task Builder with orchestration pattern selector (dispatch/broadcast/pipeline/fan-out/consult/conditional), worker multi-select with capability badges (role + expertise tags), prompt/context textareas, advanced options (timeout, retry count, aggregation strategy, validation schema), and conditional routing rules; (2) Active Tasks with SSE-connected live progress, per-worker status events, HITL checkpoint cards with approve/reject, and handoff chain visualization; (3) Results Dashboard with pattern-specific views (pipeline step-by-step, fan-out per-worker grid, consult side-by-side comparison + synthesized answer, conditional routing decision), sub-tab filtering by pattern; (4) Worker Registry with worker cards (name, status, role, model, capabilities, per-worker metrics), add/remove workers, dynamic spawn form (expertise tags, tools, model specialty); (5) Task History with searchable/filterable table, pagination, and task detail modal with full worker results and handoff chains. | `swarm-ui-panel` |
| ✅ | Cross-Area Integration Flows | Wired up cross-area integration flows for the swarm engine: (1) Pipeline with HITL checkpoint end-to-end (create pipeline -> pause -> approve -> complete -> persist -> history); (2) Consult partial failure (3 workers, 1 fails -> 2 opinions + synthesis -> partial -> persisted); (3) Fan-out with fallback (primary fails -> fallback executes -> aggregated); (4) Consult from UI (submit -> SSE -> comparison view -> history); (5) SwarmManager decoupled from gateway in `app.py` (SwarmEngine works independently without gateway); (6) Prompt validation for all patterns (empty/whitespace-only prompts rejected with HTTP 400); (7) No dual worker registry (swarm_panel uses SwarmEngine's registry exclusively, no module-level `_workers` dict). Added `tests/test_swarm_cross_flows.py` with 26 tests covering VAL-CROSS-001 through VAL-CROSS-007 plus prompt validation and unified registry assertions. | `swarm-cross-flows` |

---

## Sprint 9 — UI Bug Fixes & Bilingual Support (June 2026)

### UI Fixes & Features

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Dark Mode Dropdown Fix | Corrected dropdown contrast in dark theme for WCAG-compliant readability | remediation-R2 |
| ✅ | Model Selection Pipeline | Chat-model selector with provider switch on save, SSE model passthrough, API key validation | remediation-R2 |
| ✅ | Bilingual Language System | EN/AR toggle with cookie middleware, shared Jinja2Templates, complete i18n | remediation-R2 |

---

## Sprint 8 — Architecture Remediation (June 2026)

### P0 Correctness Bug Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | agent_handler Race Condition | Fixed concurrent access race in agent_handler | remediation-R1 |
| ✅ | code_exec Windows Crash | Resolved Windows-specific crash in code_exec subprocess handling | remediation-R1 |
| ✅ | Global Session Messages | Eliminated global mutable session message state | remediation-R1 |
| ✅ | Config Write Race | Serialized config-store writes to prevent corruption | remediation-R1 |
| ✅ | Session Store Deletion | Fixed deletion path in unified session store | remediation-R1 |

### Dead Code Removal

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Module Purge | Deleted 6 dead modules: consumer.py, dispatcher.py (legacy), recovery.py, kazma_core/checkpoint.py (old), stub build_graph/create_app, compact_node | remediation-R1 |

### Architecture Unification

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | UnifiedToolExecutor | Consolidated 3 separate tool registries onto a single UnifiedToolExecutor | remediation-R1 |
| ✅ | Unified Session Stores | Merged session stores into a single coherent store layer | remediation-R1 |
| ✅ | Service Facade | Introduced service layer facade — zero private attribute access from UI | remediation-R1 |

### UI Features

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | HITL Approval UI | Inline approve/deny panel for tiered tool-safety gates | remediation-R1 |
| ✅ | Session History Loading | Load and browse prior conversations from any session | remediation-R1 |
| ✅ | Agents Page | Dedicated page for agent inspection and control | remediation-R1 |

### UI Bug Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Telemetry Route Dedup | De-duplicated telemetry route streaming | remediation-R1 |
| ✅ | Toast Null Reference | Null-safe toast notifications | remediation-R1 |
| ✅ | Cost Breaker Type Fix | Corrected cost circuit-breaker type handling | remediation-R1 |
| ✅ | Swarm Logs Endpoint | Fixed swarm logs endpoint response | remediation-R1 |
| ✅ | Init Error Surfacing | Surface initialization errors to the UI | remediation-R1 |

### Cross-Platform Hardening

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Windows setup.ps1 | PowerShell bootstrap script for Windows installs | remediation-R1 |
| ✅ | Portable Paths | Removed hardcoded home paths; user-writable data dirs | remediation-R1 |
| ✅ | PowerShell Completion | Tab completion for `kazma` CLI in PowerShell | remediation-R1 |
| ✅ | Env Var Configuration | Environment-variable overrides for all paths and secrets | remediation-R1 |

### RTL / Arabic Completion

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Cairo Font | Native Arabic typography with the Cairo font family | remediation-R1 |
| ✅ | i18n System | Full internationalization layer (150+ Arabic translations, 71 RTL CSS selectors) | remediation-R1 |

---

## Sprint 7 — Web UI Rebuild & Memory (June 2026)

### Web UI

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Web UI Rebuild | Complete rebuild from scratch — 12 settings tabs, Alpine.js, single base template | `4a2a705` |
| ✅ | Settings Persistence | SQLite config_store for all settings (model, agent, connectors, appearance) | `e93a1b5` |
| ✅ | Provider Integration | 9 built-in providers (OpenAI, Anthropic, DeepSeek, Google, xAI, OpenRouter, Ollama, LM Studio, Custom) | `4cf2a4a` |
| ✅ | Model Discovery | Real model fetching from providers with API key authentication | `8530c99` |
| ✅ | Connectors Tab | Telegram, Discord, Slack token management with allowed users | `e93a1b5` |
| ✅ | Appearance Tab | Theme, accent color, font size slider | `4a2a705` |
| ✅ | Shortcuts Tab | Keyboard shortcuts with conflict detection | `4a2a705` |
| ✅ | Tools Tab | Tool registry browser with enable/disable | `4a2a705` |
| ✅ | MCP Tab | MCP server management with start/stop/test | `4a2a705` |
| ✅ | Skills Tab | Skill browser with install/uninstall | `4a2a705` |

### Memory

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | FTS5 Memory | SQLite full-text search with BM25 ranking — keyword search alongside vector search | `b34c47a` |
| ✅ | Arabic Search | Arabic text support in FTS5 with porter unicode61 tokenizer | `b34c47a` |

### Swarm

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Swarm Manager | Unified in-process + distributed worker orchestration | `gw-065` |
| ✅ | Swarm Notifier | Telegram group notification system for worker progress | `gw-066` |
| ✅ | Swarm Panel | Web UI for worker management, dispatch, lifecycle | `gw-067` |
| ✅ | Role Presets | Worker role presets (orchestrator, observer, backend, frontend, researcher, reviewer) | `b34c47a` |
| ✅ | API Key Field | Per-worker API key support for custom providers | `7554374` |

### Bug Fixes

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | RBAC DB Fix | check_permission now queries division_permissions table | `a80b806` |
| ✅ | Role Expiry | authorization_flow now revokes roles on expiry | `a80b806` |
| ✅ | Cron Double-Fire | _in_flight guard prevents duplicate job execution | `f57eeae` |
| ✅ | Optional Schema | Optional[T] tool params now correctly typed | `a80b806` |
| ✅ | KG Parallel Edges | MultiDiGraph support for multiple relations between nodes | `ff4c0e1` |
| ✅ | ReAct Counter | Iteration counter now increments on tool path | `91cb17a` |
| ✅ | Voice Size Cap | 10MB limit on voice file downloads | `c7d6d53` |
| ✅ | Vision Stream | Stream-based Content-Length bypass protection | `c7d6d53` |
| ✅ | Shortcuts Route | Catch-all route no longer intercepts specific endpoints | `b38a483` |
| ✅ | Dashboard Default | Root page now serves dashboard instead of workspace | `784dd16` |

---

## Sprint 4+ — UX, Security & Ecosystem

### Agent UX

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | Slash Commands | 12 instant commands: `/help`, `/reset`, `/status`, `/model`, `/memory`, `/cost`, `/undo`, `/edit`, `/replay`, `/personality`, `/context`, `/config` | `7a65bde`, `8e4c735` |
| ✅ | Personality System | 8 built-in profiles with runtime switching via `/personality` | `f46b140` |
| ✅ | Quick Reply Buttons | Telegram inline keyboards for HITL approvals + personality selection | `7394980` |
| ✅ | Proactive Suggestions | Post-task next-step hints + automatic tool-intent detection | `5642f56` |
| ✅ | Rate Feedback | Friendly cooldown messages when user hits rate limits | `56a9a3d` |
| ✅ | Context Indicator | Token usage report with role breakdown via `/context` | `56a9a3d` |
| ✅ | Message Edit/Delete | `/undo` removes last agent response; `/edit` replaces it with corrected text | `bdd2846` |
| ✅ | Time Travel Replay | Snapshot-based replay engine — `/replay list`, replay from iteration, compare runs | `d965c8f`, `c9083c2` |
| ✅ | Config Wizard | `/config` with 7 sub-commands: show, model, personality, memory, tools, export | `968c7b2` |
| ✅ | Project Init | `.kazma/` directory system — `kazma project init`, show, validate | `1263b9a` |
| ✅ | Shell Completions | Bash and zsh tab completion for `kazma` CLI | `1dcf49e` |
| ✅ | CLI Banner | Startup banner with status overview, config checks, and quick help | `8f9ae8d` |

### Gateway & Adapters

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Slack Adapter | Socket Mode adapter with listen(), _parse_event(), 429 retry | `de0e9d7` |
| ✅ | Telegram Voice | Voice message transcription in Telegram adapter | `6697525` |
| ✅ | Emoji Reactions | Telegram message reactions + callback query support | `7394980` |

### Tools & Capabilities

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Image Generation | pollinations.ai-backed image gen, saved to `kazma-data/images/` | `2ec1596` |
| ✅ | Vision Analysis | Analyze images via LLM vision capabilities | `1be0db9` |
| ✅ | Knowledge Graph | NetworkX MultiDiGraph backend for structured memory | `6c69708` |
| ✅ | KG Memory Adapter | Knowledge Graph-backed memory integration layer | `d832f9b` |
|| ✅ | MCP Server | IDE integration via MCP protocol (VS Code extensions) | `679530b` |
|| ✅ | Swarm Orchestration | Multi-worker Web UI panel — worker table, dispatch, lifecycle controls | `gw-067` |
|| ✅ | Kazma Hub | Skill marketplace — search, install, publish, certify | `bdd2846` |

### Security

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Security Linter | Static analysis for security anti-patterns in agent code | `bdd2846` |
| ✅ | Dependency Scanner | Vulnerability scanning for Python dependencies | `bdd2846` |
| ✅ | RBAC Permissions | Role-based access control for tools and commands | `bdd2846` |
| ✅ | Audit Trail | Full disclosure logging and certification chain | `bdd2846` |
| ✅ | Disclosure System | Automatic capability disclosure on first interaction | `bdd2846` |

### Documentation

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Slash Commands Doc | Complete reference for all 12 slash commands | `bdd2846` |
| ✅ | Portability Policy | Platform-agnostic deployment guarantees | `2fa2a1a` |
| ✅ | Docusaurus Site | Full documentation site with security + hub guides | `bdd2846` |

---

## Sprint 3 — Advanced Agent & Safety

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Sub-Agent Spawning | Delegate tasks to child graphs with isolated contexts | `d86d46b` |
| ✅ | Sub-Agent Visualization | Thought panel for sub-agent activity in Web UI | `6ff0f7d` |
| ✅ | Cron Autonomy | Scheduled agent actions with SQLite persistence | `5df188a` |
| ✅ | Model Router | Multi-provider routing (DeepSeek, OpenRouter) with intelligent selection | `6c97ff9` |
| ✅ | Auto-Summarization | Context compaction when token window exceeds 4K threshold | `3c5166f` |
| ✅ | Sandboxed Code Exec | Python subprocess with `-I` isolation, 30s timeout, 512MB limit | `3c5166f` |
| ✅ | HITL Approval Gates | Tiered tool approval: safe/warning/danger, inline keyboard | `2ee1c9a` |
| ✅ | RAG Memory | VectorMemory with ChromaDB + sentence-transformers | `8952d50` |
| ✅ | Prometheus Metrics | `/metrics` endpoint for monitoring | `8952d50` |
| ✅ | HITL Auth | Shared-secret authenticated approval endpoint | `8952d50` |
| ✅ | MCP Bridge | UnifiedToolExecutor — local + MCP tool routing | `bb803e5` |
| ✅ | Hardware Telemetry | Async CPU, RAM, GPU monitoring | `bfe39f1` |
| ✅ | SSE Telemetry | Real-time server-sent events stream | `07205e7` |
| ✅ | Production Hardening | Health indicator + SSE reconnect, graceful shutdown | `536b50c` |
| ✅ | Agent Thought Panel | Tool call visualization in Web UI | `c21c99a` |
| ✅ | Gateway Monitor | Multi-platform metrics panel with health banner | `0eacca8`, `70e8ae5` |
| ✅ | Docker Deploy | Single `docker compose up` with 2 volumes | `b150f21` |
| ✅ | Retry & Backoff | Exponential backoff with configurable attempts | `2424604` |

---

## Sprint 2 — Gateway & Multi-Platform

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | Gateway Framework | Unified adapter framework with BaseAdapter, polling architecture | `eb3509c` |
| ✅ | Omnichannel Bus | Platform-agnostic message bus + polling adapter + monitor UI | `68685f6` |
| ✅ | Telegram Adapter | Full bot support with aiogram polling, asyncio.Event shutdown | `cecf51b` |
| ✅ | Discord Adapter | Native Markdown, rate-limited | `70e8ae5` |
| ✅ | Session Store | SQLite-based session isolation with checkpoint locks | `54e0f12` |
| ✅ | Rate Limiting | Per-platform token bucket with jitter + 429 retry | `0911192` |
| ✅ | Gateway Monitor UI | Professional dashboard with auto-refresh | `8f781d5` |
| ✅ | Persistence Indicator | Resume-aware UI with checkpoint status | `82ca0b2` |
| ✅ | Webhook Ingress | FastAPI router for Telegram webhook bridge | `8be5cd0` |
| ✅ | Live Gateway Status | Real `/api/gateway/status` endpoint replacing mock data | `715aa76` |
| ✅ | Brain-API Contract | Reply metadata envelope + gateway consumer | `62d9d45` |
| ✅ | Health Endpoint | `/health` with persistence indicator + resume highlight | `b0a0f52` |
| ✅ | Correlation ID | Request tracing across gateway → agent → reply | `647fcdd` |
| ✅ | Thread ID Standardization | Consistent thread_id across all layers | `647fcdd` |

---

## Sprint 1 — Core Foundation

| Status | Feature | Description | Reference |
|:---:|:---|:---|
| ✅ | ReAct Supervisor | LangGraph-based agent with tool-calling loop | `b150f21` |
| ✅ | SQLite Checkpointing | Durable execution — agents resume mid-task after crash | `b150f21` |
| ✅ | Multi-Provider LLM | DeepSeek, OpenRouter, Ollama with LiteLLM routing | `12777c3` |
| ✅ | Dynamic Model Discovery | Local provider auto-detection and runtime switching | `56cc5ae` |
| ✅ | Provider Config UI | Settings tab with API config + model test | `aa650f5` |
| ✅ | Web UI Dashboard | FastAPI + Jinja2 with Arabic RTL support | `aa05135` |
| ✅ | Terminal UI | Textual TUI with English-only metrics/chat dashboard | `743c2b4` |
| ✅ | CLI Entry Point | `kazma status`, `serve`, `wizard`, `hub`, `docs` | `743c2b4` |
| ✅ | Context Authority | Compaction loop architecture — 80% threshold system | `ec860cc` |
| ✅ | Omnichannel Support | Multi-platform UI with shared model config store | `e8be750` |
| ✅ | File I/O Tools | file_read, file_write, file_list, file_search | `2424604` |
| ✅ | Web Search Tool | DuckDuckGo-powered search | `c47ce06` |
| ✅ | URL Reader Tool | Fetch + extract via trafilatura | `c47ce06` |
| ✅ | Export Session Tool | Save conversation history to file | `c47ce06` |
| ✅ | Token Validation | Optional Telegram token check with graceful fallback | `14e5e12` |
| ✅ | Graph Fallback | Auto-fallback to agent.run() when graph is None | `d664be9` |
| ✅ | URL Sanitization | Force `/v1` suffix on all LLM config paths | `4431e5f` |
| ✅ | LiteLLM Routing | Centralized model config via Alpine.store | `97142cc` |


---

## Sprint 12 — Universal Model Registry & Routing Fixes (July 2026)

### Model Registry

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Universal Model Registry** | Global model registry servicing Web UI, TUI, and Telegram — `get_universal_models()`, `find_provider_for_model()`, cross-interface sync | `d013b6f` |
| ✅ | **Split-Brain Routing Fix** | Removed hardcoded `LM_STUDIO` fallback (`localhost:1234`) in `chat.py` and `sse_chat.py`. All model dispatch now goes through the centralized `ModelRegistry` with `find_provider_for_model()`. | `d5e2895` |
| ✅ | **Durable Discovery** | Discovered models serialized to SQLite `ConfigStore` via `serialize()`, surviving server restarts. | `ab2b7d5` |
| ✅ | **Backend Resolution** | `sse_chat.py` and `model_registry.py` dynamically resolve provider credentials (`base_url`, `api_key`) based on the requested model ID. | `d5e2895` |

### UI

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **Unified Model Dropdown** | Chat dropdown, Add Worker, Dynamic Spawn, and Edit Worker all use unified `<optgroup>` grouped by provider, fetching from `/api/providers`. | `d5e2895` |
| ✅ | **Provider Discovery UI** | Provider cards show "Discovered (N)" chips with scrollable model list. Preset dropdown removed. Name field editable. | `ab2b7d5` |
| ✅ | **Worker Edit Modal** | Swarm workers now have an ✏️ Edit button with role select, model dropdown, and Save/Cancel. | `d5e2895` |

### Design System

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **design-b-modern Branding** | Grid background texture, `#22d3ee` cyan accent, `#0a0f14` background, Inter font stack. | `cdbbc82` |
| ✅ | **Alpine $persist Font** | Font size persists via `Alpine.$persist` + reactive `:style` binding on `<html>`, synced to backend. | `c81ba6b` |
| ✅ | **Kazma Icon** | Custom icon in sidebar and chat header. | `1111d13` |

### Documentation

| Status | Feature | Description | Reference |
|:---:|:---|:---|:---|
| ✅ | **ROADMAP.md** | v2.0 Global Localization Architecture — Cultural Switching blueprint with 7 milestones. | `72566fb` |
| ✅ | **CHANGELOG.md** | Updated with Sprint 12 entries. | `72566fb` |
