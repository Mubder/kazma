# Kazma Repo — Architecture, Test Coverage & Documentation Audit

**Generated:** 2026-06-30
**Repo:** `/home/balfaris/kazma/`
**Scope:** 357 files, ~102K LOC, 3,260 collected tests

---

## AREA 1 — ARCHITECTURE

### 1.1 ModelRouter Singleton Pattern
**Severity:** LOW (design choice, not a bug)

The `ModelRouter` in `kazma-core/kazma_core/models/router.py` does **not** implement a singleton pattern. It is a regular class instantiated via `from_config()` or `__init__()`. There is no `_instance` class variable, no `__new__` override, and no module-level singleton accessor.

**How it's used:**
- `KazmaAgent.get_streaming_graph()` passes `model_router` as a parameter to `build_supervisor_graph()`
- The graph caches the router via closure in `_supervisor()` node
- The router is rebuilt each time `get_streaming_graph()` is called (but the graph itself is cached in `_streaming_graph`)

**No bypass detected:** All LLM calls go through `LLMProvider.chat()`, which accepts an optional `model=` override from the router. No code creates local LLM instances that bypass the router.

**Recommendation:** If singleton behavior is desired (e.g., for consistent model selection across the app), add a `get_model_router()` accessor similar to `get_swarm_engine()`.

---

### 1.2 Package Boundary Violations
**Severity:** MEDIUM

kazma-ui has **49 import sites** referencing `kazma_core` internals. Key concerns:

| Import | File | Concern |
|:---|:---|:---|
| `kazma_core.agent.graph_builder.build_supervisor_graph` | `app.py:738` | Internal graph construction leaked to UI |
| `kazma_core.agent.state.initial_supervisor_state` | `sse_chat.py:407` | Internal state schema leaked to UI |
| `kazma_core.swarm.engine.get_swarm_engine` | `swarm_sse.py:27` | Module-level singleton — acceptable |
| `kazma_core.providers.PROVIDER_PRESETS` | `sse_chat.py:568` | Internal constants leaked to UI |
| `kazma_core.url_utils.*` | `sse_chat.py:555` | Utility functions — borderline |

**Positive:** Most imports use the public API surface (`KazmaAgent`, `load_config`, `ConfigStore`, `SwarmManager`).

**Recommendation:** Expose `build_supervisor_graph` and `initial_supervisor_state` through `kazma_core.agent.__init__.py` as public API, or better yet, provide a facade method on `KazmaAgent` that the UI calls instead of reaching into graph internals.

---

### 1.3 Circular Imports
**Severity:** LOW (resolved)

The codebase has a documented circular import resolution in `kazma_core/agent/__init__.py`:
```python
try:
    from kazma_core.agent_runner import AgentConfig, KazmaAgent, ...
except ImportError:
    pass
```

The old `kazma_core/agent.py` was moved to `kazma_core/agent_runner.py` to break the cycle. No other circular import patterns were detected.

---

### 1.4 Service Facade Pattern
**Severity:** 🔴 HIGH — Documentation claims a facade that doesn't exist

The README architecture tree lists:
```
kazma_ui/services.py  — Service facade layer — zero private attr access from UI
```

**`services.py` does not exist.** The file was not found at the expected path. There is no service facade layer.

**Private attribute accesses from UI code found:**

| File | Line | Access |
|:---|:---|:---|
| `kazma_ui/sse_chat.py` | 268 | `engine._finalize_task` (monkey-patched) |
| `kazma_ui/sse_chat.py` | 269 | `engine._dispatch_worker` (monkey-patched) |
| `kazma_ui/sse_chat.py` | 276-277 | `engine._active_task_id`, `engine._sse_step_counter` |
| `kazma_ui/sse_chat.py` | 377-379 | `engine._finalize_task = _patched_finalize` |

The SSE chat module **monkey-patches** private methods of `SwarmEngine` to inject SSE event emission. This is a fragile coupling that will break if internal method signatures change.

**Recommendation:** Either create the documented `services.py` facade, or refactor the SSE wiring to use the engine's public `TracingEmitter` / event system instead of monkey-patching private methods.

---

### 1.5 LangGraph Graph Construction
**Severity:** ✅ PASS

The graph topology in `graph_builder.py` is correct and matches the documented design:

```
START → CHECK_SATURATION → {SUMMARIZE, SUPERVISOR}
SUMMARIZE → SUPERVISOR
SUPERVISOR → {TOOL_WORKER, RESPOND}
TOOL_WORKER → SUPERVISOR (loop)
RESPOND → END
```

Key correctness checks:
- ✅ Entry point is `CHECK_SATURATION`
- ✅ Max iterations forces `RESPOND`
- ✅ Cost breaker gate in supervisor node
- ✅ 80% context compaction check
- ✅ HITL interrupt support in tool worker
- ✅ Tool result truncation (4000 chars)
- ✅ Personality injection with marker-based replacement
- ✅ Conditional edges properly map all node transitions

---

### 1.6 SQLite Checkpointing
**Severity:** ✅ PASS

- Checkpoint files exist: `kazma-data/checkpoints.db` (+ WAL + SHM files)
- `create_supervisor_app()` creates `AsyncSqliteSaver` with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`
- Integration test `test_graph_roundtrip.py` verifies:
  - Real graph execution through SUPERVISOR → TOOL_WORKER → RESPOND
  - Checkpoint persistence to SQLite
  - Resume from checkpoint in a fresh connection
- `KazmaAgent._ensure_graph()` creates checkpoints per-thread

---

### 1.7 SwarmEngine Race Conditions
**Severity:** MEDIUM

The `SwarmEngine` uses plain `dict` for `_workers` and `_task_history` **without any locks**:

```python
self._workers: dict[str, SwarmWorker] = {}        # no lock
self._task_history: dict[str, SwarmTask] = {}      # no lock
```

In asyncio's single-threaded event loop, dict operations between `await` points are atomic. However:

1. **`add_worker()` / `remove_worker()`** mutate `_workers` without locks. If called from a REST API handler while `broadcast()` is iterating `_workers.values()` via `asyncio.gather()`, the dict could be modified mid-iteration (though this is unlikely in practice since dict iteration in CPython is safe against concurrent modification in most cases).

2. **`_finalize_task()`** writes to `_task_history` without synchronization. Multiple concurrent dispatches could theoretically interleave writes, though dict assignment is atomic in CPython.

3. **`broadcast()`** creates a `BoundedConcurrency` per call — this is correct for limiting parallel dispatches but doesn't protect the shared `_workers` dict.

4. The `BlackboardStore` correctly uses `asyncio.Lock()` for its internal state.

**Recommendation:** Add `asyncio.Lock()` around `_workers` and `_task_history` mutations if concurrent REST API calls can modify workers during dispatch. The `BoundedConcurrency` semaphore is correctly used for fan-out patterns.

---

## AREA 2 — TEST GAPS

### 2.1 Test Count
**Actual:** 3,260 tests collected
**README claim:** "2,382+ passing"
**Badge claim:** "tests-2,382+_passing"

The README is **outdated by ~878 tests**. The actual test suite has grown significantly.

---

### 2.2 Source Files with ZERO Corresponding Tests
**Severity:** HIGH

The following source modules have no dedicated test file:

**kazma-core (critical gaps):**
| Module | Impact |
|:---|:---|
| `agent_runner.py` | Core agent class — tested indirectly via integration tests |
| `compaction.py` | Context compaction engine — untested |
| `token_counter.py` | Token counting — untested |
| `dialect_detector.py` | Arabic dialect detection — untested |
| `tracing.py` | KazmaTracer — untested directly |
| `streaming.py` | SSE streaming helpers — untested |
| `pacing.py` | Rate pacing — untested |
| `tone_adapter.py` | Tone adaptation — untested |
| `state.py` | Agent state types — untested |
| `permissions.py` | RBAC permissions — untested |
| `providers.py` | Provider presets — untested |
| `mcp_client.py` | MCP client — untested |
| `settings_manager.py` | Settings persistence — untested |
| `security/certification.py` | Certification chain — untested |
| `security/audit_trail.py` | Audit logging — untested |
| `security/disclosure.py` | Capability disclosure — untested |
| `memory/fts5.py` | FTS5 full-text search — untested directly |
| `memory/kg_adapter.py` | Knowledge graph adapter — untested |

**kazma-ui (moderate gaps):**
| Module | Impact |
|:---|:---|
| `metrics.py` | Prometheus /metrics endpoint — untested |
| `dashboard.py` | Dashboard routes — untested directly |
| `chat.py` | WebSocket chat handler — untested |
| `mcp_ui.py` | MCP management UI — untested |
| `models_route.py` | Models/Ollama management — untested |
| `telemetry_route.py` | Telemetry SSE — untested |
| `gateway_monitor.py` | Gateway status endpoint — untested |
| `skills_ui.py` | Skills management UI — untested |
| `hitl_approval.py` | HITL approval endpoint — untested |

**kazma-cli:**
| Module | Impact |
|:---|:---|
| `update.py` | CLI update checker — untested |
| `project.py` | Project init/show/validate — untested |

**kazma-gateway:**
| Module | Impact |
|:---|:---|
| `gateway.py` | GatewayManager core — untested |
| `adapters/discord.py` | Discord adapter — untested |

---

### 2.3 Mock Overuse
**Severity:** MEDIUM

Heavy mock usage detected in 14+ test files. The most concerning cases:

| File | Mock Count | Concern |
|:---|:---|:---|
| `test_sse_chat.py` | 7+ MagicMock | Graph entirely mocked — no real graph execution tested |
| `test_vision_analyze.py` | 30+ Mock/AsyncMock | HTTP client entirely mocked |
| `test_swarm_dispatch_integration.py` | 50+ mock refs | **Called "integration" but SwarmManager is fully mocked** |
| `test_slack_adapter.py` | 10+ Mock | HTTP calls mocked — no real adapter behavior tested |
| `test_swarm_notify.py` | 5+ Mock | Notification HTTP mocked |

**The `test_swarm_dispatch_integration.py` file is mislabeled as an "integration" test.** It tests that the FastAPI endpoint correctly delegates to a `MagicMock()` SwarmManager — this is a unit test with a bigger fixture, not an integration test.

---

### 2.4 pytest.skip / xfail Usage
**Severity:** ✅ ACCEPTABLE

14 `pytest.skip()` calls found across the suite, all for legitimate reasons:
- Missing optional dependencies (RAG/chromadb, Pillow, duckduckgo_search)
- Missing test fixture files
- Running as root (permission tests)
- Missing kazma_core availability

No `xfail` markers found. The skip count is reasonable for a project with optional extras.

---

### 2.5 Integration Tests: Real vs Fake
**Severity:** MEDIUM

**5 files in `tests/integration/`:**

| File | Verdict | Notes |
|:---|:---|:---|
| `test_graph_roundtrip.py` | ✅ **Genuine** | Real graph, real SQLite, real tool execution, checkpoint resume |
| `test_agent_uses_graph.py` | ✅ **Good** | Real graph + checkpointer, mocked LLM only |
| `test_rag_pipeline.py` | ⚠️ Skipped | Depends on chromadb (optional) |
| `test_cron_and_adapters.py` | ❓ Need review | |
| `test_swarm_dispatch_integration.py` (in tests/) | ❌ **Fake** | SwarmManager fully mocked — unit test in disguise |

**Key gap:** No integration test for the **full end-to-end flow**: user message → SSE → graph → tool execution → response. The `test_graph_roundtrip.py` comes closest but uses a stub LLM and doesn't go through the HTTP layer.

---

## AREA 3 — DOCUMENTATION vs REALITY

### 3.1 README Claims vs Implementation
**Severity:** 🔴 HIGH (multiple inaccuracies)

| Claim | Reality | Status |
|:---|:---|:---|
| "2,382+ passing" tests | 3,260 tests collected | ⚠️ Outdated (undercount by 878) |
| `services.py` in architecture tree | File does not exist | 🔴 **False** |
| "Service Facade \| Zero private attr access from UI" | UI monkey-patches engine private methods | 🔴 **False** |
| `kazma-providers/` in architecture tree | No such package directory | 🔴 **False** |
| `kazma-memory/` in architecture tree | No such package directory | 🔴 **False** |
| `kazma-skills/` in architecture tree | No such package directory | 🔴 **False** |
| 12 slash commands documented | All 12 exist in gateway dispatcher | ✅ Pass |
| 8 personalities documented | Exist in `kazma_core/personalities.py` | ✅ Pass |
| 3 entry points (kazma, kazma-web, kazma-tui) | All exist | ✅ Pass |
| All CLI commands documented | All exist in `kazma_cli/main.py` | ✅ Pass |

---

### 3.2 CLI Commands: README vs Implementation
**Severity:** ✅ PASS

All documented CLI commands exist in `kazma_cli/main.py`:

| Command | Implementation |
|:---|:---|
| `kazma status` | `_run_status()` in main.py |
| `kazma serve [port]` | `_run_serve(port)` in main.py |
| `kazma wizard` | `_run_wizard()` → `kazma_core.cli.wizard` |
| `kazma hub <subcmd>` | `_run_hub()` → `kazma_core.hub.cli` (Click-based, 12 subcommands) |
| `kazma docs <build\|serve>` | `_run_docs()` in main.py |
| `kazma completion <shell>` | `_run_completion()` → `kazma_cli/completions.py` |
| `kazma project <subcmd>` | `_run_project()` → `kazma_cli/project.py` |
| `kazma gateway <subcmd>` | `_run_gateway()` → `kazma_cli/gateway.py` (5 subcommands) |
| `kazma swarm <subcmd>` | `_run_swarm()` → `kazma_cli/swarm.py` (17+ subcommands) |
| `kazma update` | `_run_update()` → `kazma_cli/update.py` |

---

### 3.3 API Reference Docs vs FastAPI Routes
**Severity:** 🔴 HIGH — `core-api.md` is a stub with wrong class names

`docs/docs/api-reference/core-api.md` (69 lines) contains **fabricated API references**:

| Doc Reference | Actual Implementation |
|:---|:---|
| `from kazma_core.agent import Agent` | Class is `KazmaAgent`, imported from `kazma_core.agent_runner` |
| `agent.process(message)` | Method is `agent.run(message)` |
| `from kazma_core.checkpoint import CheckpointManager` | **No such class exists** — checkpointing is via `AsyncSqliteSaver` in `graph_builder.py` |
| `from kazma_core.compaction import ContextCompactor` | Class is `CompactionEngine` |
| `from kazma_core.tool_sandbox import ToolSandbox` | Exists ✅ |
| `from kazma_core.token_counter import TokenCounter` | Exists ✅ |
| `from kazma_core.dialect_detector import DialectDetector` | Exists ✅ |

**4 out of 7 class references are wrong or nonexistent.** This file appears to have been written speculatively without verifying against the actual codebase.

---

### 3.4 Docusaurus Docs Site
**Severity:** MEDIUM

The docs site structure is present (`docs/docs/`, `docs/src/`, `docs/sidebars.js`, `docs/package.json`) with:
- Getting started guides (quickstart, configuration, installation, first-skill)
- API reference (cli-reference.md — accurate, core-api.md — **inaccurate**)
- Kazma Hub docs (overview, finding-skills, publishing-skills, security-auditing)
- Core concepts (delegation-protocol.md)
- Slash commands reference
- Portability policy
- Skill manifest spec

The CLI reference (`cli-reference.md`, 818 lines) is comprehensive and accurate — it documents all commands, subcommands, flags, and exit codes correctly.

---

### 3.5 TODO / Placeholder Content
**Severity:** LOW

| Location | Content |
|:---|:---|
| `data/roadmaps.json` | 4 tasks marked "TODO" in Phase 4 (Discord, WhatsApp, Slack adapters, auto-discovery). **Note:** Discord and Slack adapters actually exist in `kazma-gateway/adapters/` — the roadmap is stale. |
| `docs/docs/api-reference/core-api.md` | Entirely placeholder content with wrong class names |
| `docs/portability.md` line 33 | "Stubs under 1KB are acceptable as placeholders" (policy document, not a code placeholder) |

No "Coming Soon" markers found in the docs.

---

## SEVERITY SUMMARY

| Severity | Count | Key Items |
|:---|:---|:---|
| 🔴 **HIGH** | 4 | `services.py` doesn't exist (README lies), `core-api.md` has wrong class names, ~25+ source files with zero tests, UI monkey-patches engine internals |
| 🟡 **MEDIUM** | 4 | Package boundary leaks, SwarmEngine no locks on shared state, fake integration tests, test count outdated in README |
| 🟢 **LOW** | 3 | ModelRouter not a singleton (design choice), circular import resolved, stale roadmap TODOs |
| ✅ **PASS** | 5 | LangGraph construction, SQLite checkpointing, CLI commands, pytest.skip usage, slash commands |

---

## TOP 5 RECOMMENDATIONS

1. **Create `services.py` or remove the claim** — The README documents a service facade that doesn't exist. Either implement it or remove the documentation and refactor the SSE monkey-patching.

2. **Fix `core-api.md`** — Replace the fabricated API reference with accurate class/method names from the actual codebase. The current file would mislead any developer.

3. **Add tests for untested core modules** — Priority: `compaction.py`, `token_counter.py`, `dialect_detector.py`, `permissions.py`, `providers.py`. These are core functionality with zero test coverage.

4. **Update README test count** — Change "2,382+" to "3,260+" (or just "3,200+") and update the badge.

5. **Refactor SSE wiring to use public API** — Replace `engine._finalize_task = _patched_finalize` monkey-patching with a proper event hook or callback registration on the engine's public interface.
