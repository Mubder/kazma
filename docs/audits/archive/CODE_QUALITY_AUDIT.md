# Kazma Code Quality & Dead Code Audit Report

**Date:** 2026-06-30  
**Scope:** All Python source files in `kazma-core/`, `kazma-gateway/`, `kazma-ui/`, `kazma-cli/`, `kazma-tui/`, `kazma-memory/`, `kazma-skills/`, `kazma-providers/`  
**Total files analyzed:** 357 Python files  

---

## Executive Summary

| Severity | Count |
|----------|-------|
| 🔴 Critical | 4 |
| 🟠 High | 12 |
| 🟡 Medium | 18 |
| 🟢 Low | 9 |
| **Total** | **43** |

---

## 🔴 Critical Findings

### C1. God Function: `create_app()` — 945 lines
- **File:** `kazma-ui/kazma_ui/app.py:29-973`
- **Issue:** The entire FastAPI application factory is a single 945-line function containing gateway initialization, adapter registration, vector memory setup, HITL endpoints, telemetry, cron scheduler, shutdown handlers, and error handlers. This is impossible to unit test, debug, or modify safely.
- **Impact:** High regression risk on any change. The function has 15+ `try/except` blocks, nested function definitions, and mutable closure state.
- **Recommendation:** Extract into a class-based factory or at minimum split into `_setup_gateway()`, `_setup_memory()`, `_setup_hitl()`, `_setup_cron()`, `_setup_lifecycle()`.

### C2. Duplicate Adapter Registration Logic (copy-paste)
- **File:** `kazma-ui/kazma_ui/app.py:367-420` and `kazma-ui/kazma_ui/app.py:489-570` (`refresh_gateway_adapters`)
- **Issue:** The adapter registration code for Telegram, Discord, and Slack is duplicated nearly verbatim between the initial setup and the `refresh_gateway_adapters()` endpoint. Both blocks resolve tokens from config_store → YAML → env, create adapters, set allowed users, and register them.
- **Impact:** Bug fixes applied to one copy but not the other. Already happened once (the Slack comment at line 409 is a leftover from a copy-paste).
- **Recommendation:** Extract into `_resolve_and_register_adapters(gateway, config_store, config)` helper.

### C3. Pervasive `except Exception:` Silencing (81 instances)
- **Files:** Across all packages — 40 in kazma-core, 25 in kazma-gateway, 16 in kazma-ui
- **Issue:** 81 instances of bare `except Exception:` (not `except Exception as e:`) that silently swallow errors. Key locations:
  - `kazma-core/kazma_core/swarm/engine.py:1000,1159,1344` — Task persistence failures silently lost
  - `kazma-core/kazma_core/agent/tool_registry.py:141,341,500` — Schema generation and tool execution errors lost
  - `kazma-core/kazma_core/time_travel.py:334,359,383` — Snapshot failures silently lost
  - `kazma-core/kazma_core/settings_manager.py:665,917,960` — Config parsing errors lost
  - `kazma-core/kazma_core/mcp_client.py:202,207,210` — MCP connection errors lost
  - `kazma-gateway/kazma_gateway/adapters/telegram.py:181,204,227,318,446,546,584,609,640,654,695,720,917,983` — Telegram API errors lost
  - `kazma-gateway/kazma_gateway/adapters/discord.py:115,234,290,354` — Discord errors lost
  - `kazma-gateway/kazma_gateway/adapters/slack.py:159` — Slack errors lost
  - `kazma-ui/kazma_ui/app.py:247,249,502,555,641,910,919,925,933` — App lifecycle errors lost
- **Impact:** Debugging production issues is nearly impossible when errors are silently swallowed. Some of these are intentional (fire-and-forget), but many are not.
- **Recommendation:** At minimum, log the exception with `logger.debug()` or `logger.warning()`. For critical paths (task persistence, MCP), propagate or re-raise.

### C4. Global Singleton Proliferation (18 module-level singletons)
- **Files:** Multiple modules across kazma-core and kazma-gateway
- **Issue:** 18 `global` statements managing module-level singletons:
  - `kazma_core/swarm/engine.py:61` — `_swarm_engine`
  - `kazma_core/agent/tool_registry.py:66` — `_vector_memory`
  - `kazma_core/agent/sub_agent.py:30` — `_sub_agent_manager`
  - `kazma_core/shutdown.py:25` — `_shutdown_event`
  - `kazma_core/personalities.py:170,184` — `_runtime_override`
  - `kazma_core/cron/scheduler.py:34` — `_cron_scheduler`
  - `kazma_core/swarm/handoff.py:48` — `_handoff_callback`
  - `kazma_core/tools/file_write.py:28` — `_WORKSPACE_ROOT`, `_ALLOW_ABSOLUTE`
  - `kazma_core/hub/api.py:166` — `_api`
  - `kazma_core/retry.py:51` — `_HTTPX_RETRYABLE`
  - `kazma_gateway/slash_commands.py:47,151` — `_CONFIG_PATH`, `_ReplayEngine`
  - `kazma_ui/dashboard.py:42,56` — `templates`, `_tracer`, `_cost_breaker`
  - `kazma_ui/session_manager.py:163,171` — `_session_manager`
- **Impact:** Makes testing difficult (test isolation requires manual reset), creates hidden coupling between modules, and makes dependency injection impossible.
- **Recommendation:** Migrate to dependency injection or a service locator pattern. At minimum, provide `reset_*()` functions for testing.

---

## 🟠 High Findings

### H1. God Function: `create_swarm_router()` — 656 lines
- **File:** `kazma-ui/kazma_ui/swarm_panel.py:251-906`
- **Issue:** Second-largest function in the codebase. Contains 15+ route definitions, SSE event wiring, and HTML fallback generation.
- **Recommendation:** Split into `_register_dispatch_routes()`, `_register_monitoring_routes()`, `_register_sse_routes()`.

### H2. God Function: `create_settings_router()` — 426 lines
- **File:** `kazma-ui/kazma_ui/settings.py:42-467`
- **Issue:** 426-line function with 20+ route definitions for all settings tabs.
- **Recommendation:** Extract per-tab route groups.

### H3. God Function: `_register_builtins()` — 387 lines
- **File:** `kazma-core/kazma_core/agent/tool_registry.py:429-815`
- **Issue:** Registers 12+ built-in tools as nested function definitions. Each tool is a closure that's hard to test independently.
- **Recommendation:** Move each tool to a separate function/module and register declaratively.

### H4. God Function: `create_sse_chat_router()` — 362 lines
- **File:** `kazma-ui/kazma_ui/sse_chat.py:253-614`
- **Issue:** SSE streaming router with complex event handling.
- **Recommendation:** Extract stream handler and error recovery logic.

### H5. God Function: `dispatch()` — 263 lines
- **File:** `kazma-core/kazma_core/swarm/engine.py:222-484`
- **Issue:** The main dispatch method handles 6 different task types (PIPELINE, FAN_OUT, CONSULT, CONDITIONAL, BROADCAST, single) in a single method with repetitive error-handling blocks.
- **Recommendation:** Use a strategy pattern or dispatch table to route task types.

### H6. God Function: `chat_websocket_handler()` — 271 lines
- **File:** `kazma-ui/kazma_ui/chat.py:108-378`
- **Issue:** WebSocket handler with message processing, tool execution, and response formatting.
- **Recommendation:** Extract message processor and response formatter.

### H7. `SettingsManager` Class — 1,048 lines with 72 methods
- **File:** `kazma-core/kazma_core/settings_manager.py`
- **Issue:** Single class managing providers, models, agents, connectors, MCP, skills, appearance, shortcuts, account, tools, system, and import/export. Violates Single Responsibility Principle.
- **Recommendation:** Split into `ProviderSettings`, `ModelSettings`, `ConnectorSettings`, `AccountSettings`, etc.

### H8. Repeated `json.loads` with try/except Pattern — 23 instances
- **Files:** `settings_manager.py` (8), `llm_provider.py`, `streaming.py`, `swarm/reliability.py`, `config_store.py`, `rbac.py`, etc.
- **Issue:** The pattern `if isinstance(x, str): try: x = json.loads(x) except: x = []` is repeated 23 times across the codebase.
- **Recommendation:** Create a `_safe_json_parse(value, default=[])` utility function.

### H9. Repeated `httpx.AsyncClient` Instantiation — 22 instances
- **Files:** `settings_manager.py` (4), `models/discovery.py` (5), `tools/vision_analyze.py`, `tools/read_url.py`, `tools/web_search.py`, `mcp_client.py`, etc.
- **Issue:** Each HTTP call creates a new `httpx.AsyncClient` context manager instead of reusing a shared client. This wastes TCP connections and prevents connection pooling.
- **Recommendation:** Inject or share a single `httpx.AsyncClient` instance per module/service.

### H10. Type Hint Coverage Gaps — 30+ public functions missing annotations
- **Files:** `hub/api.py` (8 endpoints), `hub/cli.py` (15 commands), `slash_commands.py` (4 functions), `i18n.py`, `app.py`
- **Issue:** Many public API endpoints and CLI commands lack return type annotations or parameter type hints:
  - `hub/api.py`: `health()`, `list_skills()`, `search_skills()`, `submit_skill()`, `get_certification_status()`, `download_skill()`, `get_skill()`, `get_stats()` — all missing return types
  - `hub/cli.py`: 15 Click commands missing parameter types for `ctx`
  - `slash_commands.py`: `_replay_list`, `_replay_iteration`, `_replay_compare`, `_replay_clear` missing types for `engine_cls`
- **Impact:** Reduces IDE support, makes static analysis less effective, increases onboarding friction.

### H11. `TelegramAdapter.listen()` — 191 lines with deep nesting
- **File:** `kazma-gateway/kazma_gateway/adapters/telegram.py:144-334`
- **Issue:** The polling loop has 5+ levels of nesting (while → for → try → if → try) making control flow hard to follow.
- **Recommendation:** Extract `_process_update()` and `_handle_voice_update()` helper methods.

### H12. `TelegramAdapter.send()` — 162 lines with complex retry logic
- **File:** `kazma-gateway/kazma_gateway/adapters/telegram.py:843-1004`
- **Issue:** The send method handles 429 retry, parse_mode fallback, reaction emoji setting, and error handling all in one method.
- **Recommendation:** Extract `_send_with_retry()` and `_handle_send_reaction()`.

---

## 🟡 Medium Findings

### M1. TODO/FIXME Comments Indicating Unfinished Work
- **File:** `kazma-ui/kazma_ui/dashboard.py:254-255`
  ```python
  "platform": None,  # TODO: Extract from session store
  "display_name": None,  # TODO: Extract from session store
  ```
- **Issue:** Two TODO markers indicating incomplete session metadata extraction in the dashboard.

### M2. Dead Code: Duplicate Comment in `app.py`
- **File:** `kazma-ui/kazma_ui/app.py:409-410`
  ```python
  # Slack adapter (optional, via env vars)
  # Slack adapter (from config store → env)
  ```
- **Issue:** Leftover comment from copy-paste. The first comment is stale.

### M3. Dead Code: Mock Telemetry Endpoint
- **File:** `kazma-ui/kazma_ui/app.py:815-851`
- **Issue:** A mock `/api/telemetry` endpoint generates random telemetry data alongside the real telemetry SSE route. The comment at line 848 acknowledges the duplicate was removed, but the mock endpoint itself still exists and could confuse consumers.
- **Recommendation:** Remove or rename to `/api/telemetry/mock` with a deprecation notice.

### M4. Duplicate `import hashlib` in `settings_manager.py`
- **File:** `kazma-core/kazma_core/settings_manager.py:763,769`
- **Issue:** `import hashlib` is imported twice in `change_password()` — once inside an `if` block and once after it.
- **Recommendation:** Move to top-level import.

### M5. Inconsistent Import Style: Lazy vs Top-Level
- **Files:** `settings_manager.py` (lazy imports of `httpx`, `yaml`, `hashlib`, `psutil`, `platform`), `app.py` (lazy imports everywhere)
- **Issue:** Some modules use lazy imports inside functions (e.g., `import httpx` inside `test_provider()`) while others import at module level. This is inconsistent and can cause performance issues on repeated calls.
- **Recommendation:** Standardize: use top-level imports for required dependencies, lazy imports only for optional dependencies.

### M6. `_run()` Helper Uses Deprecated `get_event_loop()`
- **File:** `kazma-core/kazma_core/hub/cli.py:20-32`
- **Issue:** Uses `asyncio.get_event_loop()` which is deprecated in Python 3.12+. The `nest_asyncio` fallback is fragile.
- **Recommendation:** Use `asyncio.run()` directly or `loop.run_until_complete()` with explicit loop creation.

### M7. `SwarmEngine.approve_checkpoint()` Accesses Private State
- **File:** `kazma-core/kazma_core/swarm/engine.py:1257,1280`
- **Issue:** Directly accesses `self._checkpoint_handler._paused` (private dict) instead of using a public API.
- **Impact:** Breaks encapsulation; if `HITLCheckpointHandler` changes its internal storage, this breaks.
- **Recommendation:** Add `get_paused_entry()` and `remove_paused_entry()` methods to `HITLCheckpointHandler`.

### M8. `app.py` Closure Variable `_sse_graph_ref` Aliased Multiple Times
- **File:** `kazma-ui/kazma_ui/app.py:462,723`
- **Issue:** `_sse_graph_ref` is set via `locals().get("sse_graph")` twice and then mutated via `nonlocal` in the startup handler. This fragile pattern relies on Python closure semantics.
- **Recommendation:** Use a mutable container dict or a class attribute.

### M9. `app.py` Has Two `@app.on_event("startup")` Handlers
- **File:** `kazma-ui/kazma_ui/app.py:725,868`
- **Issue:** Two separate startup event handlers are registered. The first (line 725) starts the gateway; the second (line 868) connects MCP servers. Execution order depends on registration order but is not explicitly controlled.
- **Recommendation:** Consolidate into a single startup handler or use lifespan context manager.

### M10. `_handle_pipeline_checkpoint()` Duplicates `_finalize_task()` Logic
- **File:** `kazma-core/kazma_core/swarm/engine.py:1090-1169`
- **Issue:** Manually constructs a `TaskResult` and updates `_task_history` instead of reusing `_finalize_task()`. This duplicates the metrics recording and task persistence logic.
- **Recommendation:** Refactor to use `_finalize_task()` with a `paused` status.

### M11. `SettingsManager.get_all_providers()` Re-parses JSON on Every Call
- **File:** `kazma-core/kazma_core/settings_manager.py:75-102`
- **Issue:** Every call to `get_all_providers()` reads from config store, attempts JSON parse, and falls back to provider presets. No caching.
- **Impact:** Called frequently from the settings UI; each call does a config store read + potential JSON parse + import.
- **Recommendation:** Add short-lived cache or memoization.

### M12. `_build_result_metadata()` Stores Duplicate Keys
- **File:** `kazma-core/kazma_core/swarm/engine.py:1054-1063`
- **Issue:** Returns `{"blackboard": snapshot, "blackboard_snapshot": snapshot}` — two keys with identical values.
- **Recommendation:** Remove one of the duplicate keys.

### M13. `import os` Used Only for `os.environ.get()` in Multiple Files
- **Files:** `personalities.py:24`, `cost_breaker.py:11`, `swarm/config.py:11`, `swarm/worker.py:15`, `delegation/security.py:13`
- **Issue:** `import os` is used solely for `os.environ.get()` calls. This could be replaced with a centralized config accessor.
- **Impact:** Minor — but creates scattered env var dependencies that are hard to audit.

### M14. `Personality` Class Inherits from `dict`
- **File:** `kazma-core/kazma_core/personalities.py:32`
- **Issue:** `class Personality(dict)` uses dict inheritance for "backwards-compat" but adds properties. This is an anti-pattern — prefer composition or a dataclass.
- **Impact:** Allows arbitrary dict mutation that bypasses the property interface.

### M15. `app.py` Shutdown Handler Uses `locals().get()` for Cross-Scope Reference
- **File:** `kazma-ui/kazma_ui/app.py:907`
- **Issue:** `store_ref = locals().get("_cron_store_ref") or globals().get("_cron_store_ref")` — this pattern is unreliable and suggests the variable scoping is broken.
- **Recommendation:** Use a mutable container or class attribute for cross-scope references.

### M16. Inconsistent Error Return Patterns in `SettingsManager`
- **File:** `kazma-core/kazma_core/settings_manager.py`
- **Issue:** Some methods return `{"error": "..."}` dicts on failure (e.g., `add_provider`, `save_model_profile`), while others raise exceptions or return None. No consistent error handling contract.
- **Recommendation:** Define a `Result` type or use exceptions consistently.

### M17. `create_app()` Creates `httpx.AsyncClient` Per-Request in `refresh_gateway_adapters`
- **File:** `kazma-ui/kazma_ui/app.py:489-570`
- **Issue:** The refresh endpoint creates new adapters but doesn't properly manage the lifecycle of old adapter HTTP clients.
- **Recommendation:** Ensure `old_adapter.stop()` is awaited before creating new ones.

### M18. `_format_table()` in `hub/cli.py` Has No UTF-8/Width Awareness
- **File:** `kazma-core/kazma_core/hub/cli.py:35-49`
- **Issue:** Uses `ljust()` which counts bytes, not display width. Arabic/CJK characters will break column alignment.
- **Impact:** Since Kazma has Arabic support, this is a user-facing bug for hub CLI output.
- **Recommendation:** Use `unicodedata.east_asian_width()` or `wcwidth` for proper alignment.

---

## 🟢 Low Findings

### L1. `_TELEGRAM_API` Template String Used Inline in Multiple Methods
- **File:** `kazma-gateway/kazma_gateway/adapters/telegram.py:50,651,677,714`
- **Issue:** `_TELEGRAM_API.format(token=self._token)` is called in `_trigger_typing`, `_set_reaction`, and `_answer_callback_query` instead of using `self._api_base`.
- **Recommendation:** Use `self._api_base` consistently (it's already set in `__init__`).

### L2. Magic Numbers Without Constants
- **Files:**
  - `telegram.py:442` — `st_size > 1_000_000` (1MB cap, should be a constant)
  - `telegram.py:886` — `outbound.text[:4096]` (Telegram limit, should be a constant)
  - `tool_registry.py:442-443` — `st_size > 1_000_000` (duplicated cap)
  - `tool_registry.py:469` — `[:200]` entry cap
  - `swarm/engine.py:775` — `str(exc)[:500]` error truncation
- **Recommendation:** Define named constants.

### L3. `assert` Used for Runtime Validation
- **File:** `kazma-gateway/kazma_gateway/adapters/telegram.py:345,502,632`
- **Issue:** `assert self._http is not None` is used for runtime validation. Asserts are stripped in optimized mode (`python -O`).
- **Recommendation:** Use `if self._http is None: raise RuntimeError(...)`.

### L4. `format` Parameter Shadows Built-in
- **File:** `kazma-core/kazma_core/settings_manager.py:971,978`
- **Issue:** `def export_config(self, format: str = "yaml")` — `format` shadows the Python built-in.
- **Recommendation:** Rename to `fmt` or `output_format`.

### L5. Inconsistent `__init__.py` Exports
- **Files:** `kazma-core/kazma_core/__init__.py`, `kazma-gateway/kazma_gateway/__init__.py`
- **Issue:** Some `__init__.py` files re-export symbols, others are empty. No consistent pattern.
- **Recommendation:** Standardize on explicit `__all__` exports.

### L6. `load_retry_config()` Creates a New `ConfigStore` on Every Call
- **File:** `kazma-core/kazma_core/retry.py:69-85`
- **Issue:** Every retry attempt creates a new `ConfigStore()` instance to read retry config. This is called inside `_log_retry()` which fires on every retry.
- **Recommendation:** Cache the config or pass it as a parameter.

### L7. `_get_retryable()` Uses Global Mutable State for Caching
- **File:** `kazma-core/kazma_core/retry.py:49-63`
- **Issue:** Uses `global _HTTPX_RETRYABLE` to cache the httpx exception tuple. Not thread-safe.
- **Recommendation:** Use `functools.lru_cache` or make it a module constant.

### L8. `hub/cli.py:20` — `_run()` Missing Return Type Annotation
- **File:** `kazma-core/kazma_core/hub/cli.py:20`
- **Issue:** `def _run(coro):` — no type annotation on `coro` parameter or return type.
- **Recommendation:** Add `def _run(coro: Coroutine) -> Any:`.

### L9. Redundant `else` After `return`/`continue`
- **Files:** Multiple locations in `telegram.py`, `engine.py`, `patterns.py`
- **Issue:** Patterns like `if x: return ... else: ...` where the `else` is unnecessary after `return`.
- **Impact:** Minor readability issue.

---

## Summary of Top Recommendations

1. **Break up `create_app()`** (945 lines) — highest impact refactoring target
2. **Extract adapter registration** into a shared helper to eliminate copy-paste
3. **Add `logger.debug()`/`logger.warning()` to silent `except Exception:` blocks** — 81 instances
4. **Replace global singletons** with dependency injection for testability
5. **Create `_safe_json_parse()` utility** to deduplicate 23 instances
6. **Share `httpx.AsyncClient` instances** instead of creating 22 separate clients
7. **Add type annotations** to `hub/api.py` endpoints and `hub/cli.py` commands
8. **Split `SettingsManager`** (1,048 lines, 72 methods) into focused classes
9. **Remove mock `/api/telemetry` endpoint** or clearly mark as deprecated
10. **Fix `_format_table()` for Arabic/CJK** display width handling

---

*Report generated by automated code quality audit. Line numbers and counts are based on the codebase as of 2026-06-30.*
