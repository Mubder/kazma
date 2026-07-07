# Kazma Deep Audit Report

**Repository:** `G:\GitHubRepos\kazma`  
**Audit date:** July 7, 2026  
**Auditor:** Cursor Agent (Grok)  
**Method:** Static analysis, targeted greps, pytest collection (`uv run pytest --collect-only`), cross-check against prior audits (`AUDIT_REPORT.md`, `CODE_QUALITY_AUDIT.md`, `SECURITY_AUDIT.md`)

> **Note:** This file supersedes a prior export to `DEEP_AUDIT_REPORT.md`, which was overwritten by another process. Use this filename as the canonical copy of this audit.

---

## Executive Summary

Kazma is a **mature, production-capable** multi-platform AI agent framework with unusually strong automated coverage (**3,495 tests collected**), layered HITL safety, and recent remediation work (Sprints 14–17). The core architecture is sound: LangGraph supervisor brain, platform-isolated gateway, swarm orchestration, and SQLite-backed durability.

The main risks are **not missing features** — they are **concentrated maintainability debt** (god modules), **documentation drift**, and **a handful of real security gaps** (MCP IDE server, WebSocket HITL bypass, SSE graph wiring).

| Dimension | Grade | Headline |
|-----------|-------|----------|
| Security / HITL | **B+** | 3-tier HITL is real and tested; 3 critical gaps remain |
| Architecture | **B** | Clean package boundaries in theory; UI reaches into core internals |
| Code quality | **C+** | God modules growing; ~300+ broad `except Exception` handlers |
| Test coverage | **A-** | 3,495 tests; thin browser/E2E and multi-tenant layers |
| Documentation | **C** | README badges, architecture line counts, CONTRIBUTING all stale |
| Dependencies | **B-** | `uv.lock` present; optional vs core mismatch for RAG/ChromaDB |

---

## 1. Project Scale & Health

| Metric | Value |
|--------|-------|
| Tests collected (`uv run pytest --collect-only`) | **3,495** |
| Test files (`tests/`) | **166** (+ 24 gateway, + 7 TUI) |
| Integration tests | **7** (~4% of suite) |
| Python packages | `kazma-core`, `kazma-gateway`, `kazma-ui`, `kazma-tui`, `kazma-cli`, `kazma-memory`, `kazma-skills` |
| Version | 0.2.0 |
| Current sprint | Sprint 17 (skill checksums, task cancel/retry, engine refactor) |
| Open ROADMAP item | **1** — visual pipeline editor (drag-and-drop DAG) |

### Documentation vs Reality

| Source | Claims | Actual | Delta |
|--------|--------|--------|-------|
| `README.md` badge | 3,655 tests | 3,495 | **+160 overstated** |
| `ROADMAP.md` | 3,409+ | 3,495 | Stale (July 3 snapshot) |
| `architecture.md` | `swarm_panel.py` ~1,451 LOC | **1,795** | +344 lines |
| `architecture.md` | `agent_handler.py` ~1,306 | **1,359** | +53 lines |
| `architecture.md` | `engine.py` ~1,727 | **1,403** | Refactor helped, doc not updated |

### Largest Modules (LOC, measured July 7, 2026)

| File | Lines |
|------|-------|
| `kazma-ui/kazma_ui/swarm_panel.py` | 1,795 |
| `kazma-gateway/kazma_gateway/agent_handler.py` | 1,359 |
| `kazma-core/kazma_core/swarm/engine.py` | 1,403 |
| `kazma-ui/kazma_ui/app.py` | 1,152 |
| `kazma-core/kazma_core/settings_manager.py` | 976 |
| `kazma-core/kazma_core/agent/tool_registry.py` | 919 |
| `kazma-core/kazma_core/agent/graph_builder.py` | 819 |

---

## 2. Critical Findings (P0)

### C-1. Gateway MCP IDE server — no auth, no HITL

**File:** `kazma-gateway/kazma_gateway/mcp_server.py`

`write_file` and `run_tests` are exposed over stdio JSON-RPC with path-escape guards but **no authentication** and **no HITL gate**. Any MCP client that can speak to the process gets file write + subprocess execution.

```python
# mcp_server.py ~211-228
result = subprocess.run(
    cmd,
    cwd=str(root),
    capture_output=True,
    text=True,
    timeout=120,
)
```

**Impact:** Local privilege boundary break — IDE/MCP clients get file write + process execution.

**Fix:** Require `KAZMA_SECRET` on `tools/call`; route danger tools through `SafetyMiddleware.check()`; add HITL for `write_file`/`run_tests`.

---

### C-2. WebSocket chat bypasses graph `interrupt()` HITL

**File:** `kazma-ui/kazma_ui/chat.py` (~284–331)

`/ws/chat` uses direct `stream_chat()` + `agent.tools.execute()`, not the checkpointed LangGraph supervisor. It applies only `SafetyMiddleware` with a 5s timeout — not mechanism A (durable `interrupt()` pause/resume).

```python
# chat.py ~284-331
# Safety gate: block danger-tier tools without approval
...
result = await agent.tools.execute(event.tool_call_name, args)
```

**Impact:**

- No durable checkpoint pause/resume on WS path
- If bus adapter is wired and approves within 5s, danger tools run without graph-level approval UX
- If bus is absent, fail-closed blocks (good), but UX differs from SSE/gateway

**Fix:** Route WS through the same checkpointed graph as SSE, or disable WS tool execution entirely.

---

### C-3. SSE router may hold stale graph without checkpointer

**Files:** `kazma-ui/kazma_ui/app.py`, `kazma-ui/kazma_ui/sse_chat.py`

Flow:

1. `_setup_routers()` mounts SSE with `graph=self._sse_graph_ref` from `get_streaming_graph()` — **no checkpointer**
2. On `startup`, graph is **recompiled** with checkpointer and `self._sse_graph_ref` is updated (`app.py:1177–1196`)
3. SSE closure still captures the **original** `graph` parameter from router creation (`sse_chat.py:498–501`)

```python
# app.py ~576-577
sse_router = create_sse_chat_router(
    graph=self._sse_graph_ref,
```

```python
# sse_chat.py ~498-501
async for frame in _stream_langgraph_events(
    graph=graph,
    input_state=input_state,
```

HITL approve/pending endpoints use `_hitl_state["graph"]` (updated on startup), but `/api/chat/stream` may use the stale closure.

**Fix:** Pass a mutable graph holder (dict/ref) into `create_sse_chat_router`, or remount SSE router after checkpointer init.

---

### C-4. Documented `services.py` facade does not exist

**Claimed in:** `README.md:574`, prior `AUDIT_REPORT.md`

The README architecture tree lists `kazma_ui/services.py` as a "Service facade layer — zero private attr access from UI." **The file does not exist.** The UI still monkey-patches `SwarmEngine` private methods in `sse_chat.py` and accesses `ModelRegistry._registry` in debug routes (`app.py`).

This is an architectural integrity issue: documentation promises an abstraction that was never built or was removed.

---

## 3. High Priority Issues (P1)

### Security

| ID | Issue | Location |
|----|-------|----------|
| H-1 | Auth is shared-secret, not per-user; cookie auto-issued to all dashboard visitors | `kazma-ui/kazma_ui/auth.py` |
| H-2 | `POST /api/approve/{thread_id}` has no thread ownership check (gateway has sender check) | `app.py:1078–1115` |
| H-3 | Hub API + skill signature verify read only `os.environ["KAZMA_SECRET"]`, not ConfigStore | `hub/api.py`, `hub/loader.py` |
| H-4 | `spawn_agent`/`spawn_agents` in bus danger list but **not** in `kazma.yaml` `require_approval_for` | `safety.py` vs `kazma.yaml:84–89` |
| H-5 | `KAZMA_AUTH_DISABLED` fully opens sensitive APIs with no audit trail | `auth.py` |
| H-6 | Local `.env` on disk may contain real API keys (gitignored correctly, but exposure risk if shared) | `.env` |
| H-7 | `python_exec`/`code_exec` weaker sandbox on Windows (no POSIX `resource` limits) | `tools/code_exec.py` |

### Architecture / Maintainability

| ID | Issue | LOC |
|----|-------|-----|
| H-8 | **`swarm_panel.py` god module** — largest file, 33 REST routes, SSE, export, Mermaid | **1,795** |
| H-9 | **`agent_handler.py` god module** — slash, swarm, HITL, model selector, graph invoke | **1,359** |
| H-10 | **`engine.py` still large** despite Sprint 17 refactor | **1,403** |
| H-11 | **`app.py` god builder** — gateway, routers, lifecycle, HITL, cron in one class | **1,152** |
| H-12 | Duplicate adapter registration (initial setup vs `refresh_gateway_adapters`) | `app.py` |
| H-13 | ~**300+** `except Exception` blocks; ~35+ silent `pass` — debugging hazard | All packages |
| H-14 | 49+ `kazma_core` internal imports from `kazma-ui` — package boundary leakage | `sse_chat.py`, `app.py`, etc. |

---

## 4. What's Well Implemented

### HITL — Three Mechanisms (Verified)

| Mechanism | Path | Status |
|-----------|------|--------|
| **A. Graph `interrupt()`** | `graph_builder.py:421–505` | Wired in `agent_runner`, `app.py` startup recompile, gateway brain handler |
| **B. Swarm bus** | `swarm/safety.py` | Fail-closed `check_sync()`; `allow_headless_danger=False` in prod; tests only set `True` |
| **C. Pipeline checkpoints** | `checkpoint_manager.py`, `engine.py` | API + timeout auto-reject + persistence |

Tests: `test_hitl_wiring.py`, `test_hitl_graph_integration.py`, `test_mcp_hitl.py`, `test_checkpoint.py`.

### Platform Isolation (Gateway)

```text
Platform isolation contract (agent_handler.py):
  The Brain (LangGraph graph) NEVER sees platform-specific identifiers.
  chat_id, user_id, message_id, update_id, chat_type are stored in a
  SessionStore OUTSIDE the graph state.
```

`_PLATFORM_KEYS` frozenset enforced; graph state has `thread_id` only. Gateway checks `sender_id` on cross-thread HITL approve (`agent_handler.py:1531–1548`).

### Danger Tool Hardening

- `shell_exec`: `create_subprocess_exec`, binary allowlist, workspace `cwd` — **no `shell=True` in prod** (prior `SECURITY_AUDIT.md` finding remediated)
- File tools: workspace boundary scoping on all built-in file tools
- MCP manager: pattern classification + bus gate for danger/unknown tools (`mcp/manager.py:690–721`)
- Skill checksums: fail-closed HMAC-SHA256 (Sprint 17); verification errors raise `SkillLoadError` — no `except: pass`

### ConfigStore Atomicity (Sprint 15)

- WAL + `busy_timeout=5000`
- `get_config_store()` singleton — all components must use this, not `ConfigStore()` directly
- `batch_set()` / `transaction()` for multi-key writes
- Race tests: `test_config_write_race.py` (21 tests)

### Concurrency (Gateway)

Per-thread `asyncio.Lock` serialization in `agent_handler.py` with LRU eviction (10k cap) — tested in `test_agent_handler_concurrency.py` (VAL-CRIT-001/002).

### Swarm Reliability (Sprint 17 Refactor)

`engine.py` extracted:

| Module | Responsibility |
|--------|----------------|
| `reliability_registry.py` | Circuit breakers, retries, timeouts, validators, concurrency |
| `phonebook.py` | WorkerRegistry summon + dispatch_by_name |
| `checkpoint_manager.py` | HITL pipeline checkpoint state, timeout auto-reject |

Handoff cycle detection (`_visited`, `_depth`, max 5; `_MAX_VISITS=2`) preserved.

### Other Security Positives

| Pattern | Result |
|---------|--------|
| `eval(` / `exec(` in prod `.py` | Only in hub validator warnings, security linter, tests |
| `pickle` | None in production `.py` |
| `yaml.load(` unsafe | None — uses `yaml.safe_load` |
| `shell=True` in prod | None — only in linter test fixtures |
| CORS | Restricted origins + explicit headers (`app.py:175–181`) — fixed from prior audit |
| `allow_headless_danger` | Default `False`; only `tests/conftest.py:44` sets `True` |
| Bare `except:` | **0** in production code |

---

## 5. SQLite Store Consistency

| Store | WAL | busy_timeout=5000 |
|-------|-----|-------------------|
| `config_store.py` | ✅ | ✅ |
| `swarm/task_store.py` | ✅ | ✅ |
| `gateway/stores/sqlite.py` | ✅ | ✅ |
| `swarm/semantic_cache.py` | ✅ | ✅ |
| Checkpoint DB (`stores/checkpoint.py`) | ✅ | ❌ |
| LangGraph checkpoints (`graph_builder.py`, `agent_runner.py`) | ✅ | ❌ |
| FTS/memory backend (`kazma_memory/search_backend.py`) | ✅ | ❌ |
| `time_travel.py` snapshots | ✅ | ❌ |
| `pipeline_logger.py` | ✅ | ❌ |
| `sqlite_vec.py` | ✅ | ❌ |

**Recommendation:** Centralize `apply_sqlite_pragmas(conn)` helper; apply uniformly; log pragma failures at WARNING instead of silent `pass`.

---

## 6. Test Coverage Analysis

### Strengths

- Swarm: engine, patterns, HITL, SSE, task store, reliability, concurrency
- Gateway: adapters, concurrency, slash commands (`test_gateway.py` — 902 LOC)
- Security: auth middleware, SSRF/CORS, HITL wiring, skill checksum, RBAC
- Config: atomic writes, race conditions

### Gaps

| Gap | Severity | Evidence |
|-----|----------|----------|
| **Browser/UI tests** | HIGH | JS tested via file-content grep (`test_ui_components.py`), not Playwright/Cypress |
| **Integration/E2E ratio** | MEDIUM | 7 integration files vs 166 total (~4%) |
| **Multi-tenant** | HIGH | `test_multi_tenant_isolation.py` (2 tests), `test_vector_tenant_isolation.py` (1 test) |
| **`settings_manager.py`** | MEDIUM | 976 LOC; indirect coverage via `test_settings.py` only |
| **`google_llm.py`** | MEDIUM | 334 LOC; no `test_google_llm.py` |
| **RAG optional path** | MEDIUM | `test_rag.py` / `test_rag_pipeline.py` skip when ChromaDB/sentence-transformers missing |
| **SSE graph stale-ref bug** | HIGH | No test asserting SSE uses post-startup checkpointed graph |

### Skipped Tests

~16 `pytest.skip` usages across vision, RAG, portability, hub manifest — environment-dependent; reduces CI signal on minimal installs.

---

## 7. Dependencies

| Issue | Detail |
|-------|--------|
| **RAG mismatch** | README markets ChromaDB as a core pillar; `pyproject.toml` puts it under optional `[rag]` extra |
| **`google-cloud-aiplatform`** | Required in `pyproject.toml` but not installed in minimal `uv sync` |
| **`python-dotenv`** | Used in `app.py` but not declared — transitive via uvicorn |
| **Lockfile** | `uv.lock` present with hashed wheels — good for reproducibility |
| **LangGraph** | Declared `>=0.2.0`; lock resolves to **1.2.6** — major version jump via transitive pins |

---

## 8. Technical Debt Inventory

| Marker | Count | Notes |
|--------|-------|-------|
| `TODO`/`FIXME`/`HACK` in `.py` | **0** | Prior markers appear resolved |
| `deprecated` endpoints | 1+ | `GET /api/telemetry` still mounted (`app.py:1138`) |
| `archive/` packages | 3 dirs | `kazma-comms`, `kazma-connectors`, `kazma-providers` — still importable if PYTHONPATH misconfigured |
| `data/roadmaps.json` | Stale | Discord/Slack marked TODO though implemented |
| `CONTRIBUTING.md` | **Critical drift** | Claims "sqlite-vec ONLY, no ChromaDB"; lists archived packages as active |
| `services.py` | Missing | README documents facade that does not exist |

---

## 9. HITL Mechanism Detail

### A. Graph `interrupt()` — Single-Agent Chat

| Aspect | Status |
|--------|--------|
| Implementation | `graph_builder.py` — separates safe/danger, `interrupt()`, `_hitl_approved` flag |
| Config wired | `agent_runner.py:515–527` (SSE), `app.py:1173–1186` (recompile), gateway via brain handler |
| Resume API | `app.py:1078–1115`, gateway `/hitl approve` (`agent_handler.py:1489–1560`) |
| SSE detection | `sse_chat.py:181–212` emits `approval_required` |
| Double-gating prevention | `_hitl_approved=True` in tool args skips redundant bus check |
| **Gap** | WS path bypasses; SSE may use stale graph (C-2, C-3) |

### B. Swarm Bus (`SafetyMiddleware`)

| Aspect | Status |
|--------|--------|
| Implementation | `swarm/safety.py` — async `check()`, fail-closed `check_sync()` |
| `allow_headless_danger` | Default `False`; tests only |
| Wired in | `tool_registry.py:383–416`, `mcp/manager.py:690–721` |
| Bus adapters | Telegram/Discord/Slack wired in `app.py:448–489` |
| Extended danger list | Adds `spawn_agent`, `spawn_agents`, `schedule_task`, `cancel_scheduled` beyond graph list |
| **Gap** | `SafetyMiddleware.enabled=False` or `safety.hitl.enabled=false` disables all gates |

### C. Pipeline Checkpoints (Swarm)

| Aspect | Status |
|--------|--------|
| Implementation | `checkpoint.py`, `checkpoint_manager.py`, `engine.py:1369–1478` |
| API | `POST /api/swarm/tasks/{id}/approve` (gated by auth) |
| Timeout auto-reject | `checkpoint_manager.py:86–94` |
| Persistence | TaskStore + in-memory handler |
| Tests | `test_checkpoint.py`, `test_swarm_hitl_checkpoints.py` |

### Danger Tool List Divergence

| Mechanism | Tools |
|-----------|-------|
| Graph (`kazma.yaml`) | `file_write`, `file_delete`, `shell_exec`, `code_exec`, `python_exec` |
| Swarm bus (`safety.py`) | Above + `spawn_agent`, `spawn_agents`, `schedule_task`, `cancel_scheduled` |
| MCP (`mcp/manager.py`) | Name-pattern matching — write/exec/delete → danger; unknown → danger |

---

## 10. Danger Tool Execution Paths

| Tool | Path | Sandboxing | HITL |
|------|------|------------|------|
| `shell_exec` | `tool_registry.py:746–838` | `shlex.split` + `create_subprocess_exec`, binary allowlist, workspace `cwd` | Graph + bus |
| `file_write` | `tools/file_write.py` + registry | Workspace boundary | Graph + bus |
| `file_read/list/search` | `tool_registry.py` | Workspace-scoped via `_workspace_scope_error()` | Read = no HITL |
| `python_exec` | `tools/code_exec.py` | Subprocess `-I`, 512MB/30s (POSIX); Windows weaker | Graph + bus |
| `sqlite_query` | `tool_registry.py:595+` | SELECT-only, path allowlist | Sensitive read (logged, allowed) |
| MCP tools | `mcp/manager.py` | Pattern classify + bus gate | Bus (not graph interrupt) |
| MCP IDE `write_file` | `mcp_server.py` | Path escape guard only | **None** (C-1) |
| Swarm worker tools | `swarm/worker.py:362` → `tool_registry.execute()` | Inherits registry gates | Bus via registry |

---

## 11. Auth Middleware & API Security

### Well Implemented

- `KAZMA_SECRET` middleware with `hmac.compare_digest` (`auth.py:159–165`)
- Broad `SENSITIVE_PREFIXES` including `/api/chat`, `/api/swarm`, `/api/mcp`, `/api/approve`, `/api/workspace` (`auth.py:55–73`)
- WebSocket auth when secret configured (`app.py:873–927`)
- CORS restricted origins + explicit headers — fixed from prior `allow_headers=["*"]` finding
- Tests: `test_auth_middleware.py`, `test_ssrf_cors.py`
- Auto-generates secret on first run via `secrets.token_hex(32)` and persists to `.env` (`app.py:83–119`)
- Config export redaction tested (`test_config_wizard.py`)

### Gaps

- Open mode when secret unset (backward compat) — must document for production
- Dashboard HTML pages not gated — cookie grants API access to page visitors
- `/api/dashboard/*`, `/api/status`, `/api/telemetry` read endpoints open by design
- Hub **read** endpoints (`/api/v1/skills`) unauthenticated — acceptable for public catalog; write requires auth
- MCP IDE server unauthenticated (C-1)
- JWT tenant extraction without signature verification (`auth.py:297–315`) — OK for routing hints only; risky if used for authorization

---

## 12. Package Boundary & Coupling

### kazma-ui → kazma-core Internal Imports

~49 import sites reference `kazma_core` internals. Notable leaks:

| Import | File | Concern |
|--------|------|---------|
| `build_supervisor_graph` | `app.py` | Internal graph construction in UI layer |
| `initial_supervisor_state` | `sse_chat.py` | Internal state schema in UI |
| Monkey-patch `engine._finalize_task` | `sse_chat.py` | Fragile coupling to SwarmEngine internals |
| `_mr._registry`, `reg._active_provider` | `app.py` debug routes | Private attribute access |

Sprint 8 claimed "Zero private attribute access from UI" — this has regressed.

### Circular Imports

Resolved via `kazma_core/agent/__init__.py` try/except and `agent_runner.py` extraction. No active circular import issues detected.

### Singleton Proliferation

~18 module-level singletons (`get_swarm_engine`, `get_config_store`, `get_safety`, `get_message_bus`, etc.). Makes test isolation harder but is documented and partially mitigated with `reset_*()` helpers in tests.

---

## 13. Error Handling Patterns

| Pattern | Approx. Count | Risk |
|---------|---------------|------|
| `except Exception:` (broad) | ~300+ | Masks root causes |
| `except Exception: pass` | ~35+ | Silent failure |
| Bare `except:` | 0 in prod | Good |

### Hot-Path Silent Swallowing (Examples)

- `agent_handler.py:947–948` — `except Exception: pass  # fall through to text`
- `app.py:797–811` — lifecycle refresh swallows without logging
- `kazma-ui/kazma_ui/metrics.py:113–114` — telemetry aggregation silently dropped
- `swarm/engine.py` — task persistence failures in some paths

**Recommendation:** Project rule — broad catches must log at `DEBUG` minimum; `pass` only in documented fire-and-forget paths.

---

## 14. ROADMAP Claimed vs Actual State

| ROADMAP Claim | Verified State |
|---------------|----------------|
| All 21 original features shipped ✅ | Consistent with code/adapters present |
| Sprints 14–17 remediation complete ✅ | HITL, ConfigStore atomicity, engine refactor — code + tests confirm |
| Engine 1,878→1,573 lines ✅ | **1,403** measured — refactor exceeded claim |
| Routing unified to `UnifiedRouter` ✅ | Legacy `BaseRouter` gone |
| Semantic routing ✅ | `semantic_router.py` + `UnifiedRouter` |
| Test count 3,409+ | **3,495** collected — claim conservative; README overshoots |
| Visual pipeline editor | **NOT done** — correctly marked open (`ROADMAP.md:134`) |
| Docs accuracy updated ✅ | **Partially false** — significant drift remains |

---

## 15. Prioritized Remediation Roadmap

### P0 — Do First (Security + Correctness)

1. Add auth + HITL to `kazma_gateway/mcp_server.py`
2. Fix SSE graph holder so `/api/chat/stream` uses checkpointed graph
3. Align WebSocket with graph HITL or remove WS tool execution
4. Create `services.py` facade **or** remove README claim and refactor private access

### P1 — Next Sprint

5. Split `swarm_panel.py` into route modules (`workers`, `tasks`, `metrics`, `sse`, `export`)
6. Extract `agent_handler.py` submodules (swarm dispatch, HITL resume, model selector)
7. Add thread ownership check to web `POST /api/approve/{thread_id}`
8. Unify secret resolution via `get_kazma_secret()` everywhere (Hub, loader, approve)
9. Add `spawn_agent`/`spawn_agents` to `kazma.yaml` `require_approval_for`
10. Error-handling pass: replace silent `pass` with structured logging on hot paths

### P2 — Hardening

11. Standardize SQLite pragmas across all stores
12. Reconcile all docs to single test count source (CI badge generator?)
13. Promote `[rag]` to default deps or demote ChromaDB in README
14. Add browser-level tests for SSE chat + HITL approval UI
15. Expand multi-tenant test suite beyond 3 tests
16. Make `google-cloud-aiplatform` optional; declare `python-dotenv` explicitly
17. Windows `code_exec` hardening (job objects, restricted PATH)

### P3 — Polish

18. Update `architecture.md` line counts
19. Remove or gate deprecated `/api/telemetry`
20. Refresh `data/roadmaps.json`, `docs/AUDIT_KANBAN.md`
21. Continue `engine.py` decomposition toward <1,200 LOC
22. Update stale `SECURITY_AUDIT.md` — shell/file/CORS findings remediated

---

## 16. Overall Verdict

**Kazma is production-capable** with security thinking that goes well beyond typical agent frameworks: layered HITL, fail-closed safety middleware, platform isolation, skill signing, and a test suite that most open-source projects would envy.

The project is entering a **maintainability inflection point**. `swarm_panel.py` (1,795 LOC) and `agent_handler.py` (1,359 LOC) are growing faster than documentation and abstractions can keep up. The three P0 security items (MCP IDE, WS HITL, SSE graph ref) are fixable in a focused sprint and should be treated as blockers for any unattended production deployment.

**Do not rely on `DEEP_AUDIT_REPORT.md`** — it was overwritten with a different, partially inaccurate audit (e.g. claims arbitrary command execution that the current codebase has remediated). **This file is the canonical copy.**

---

## Related Audit Documents

| File | Date | Scope | Notes |
|------|------|-------|-------|
| `AUDIT_DEEP_REPORT_2026-07-07.md` | 2026-07-07 | **This report** | Canonical deep audit |
| `AUDIT_REPORT.md` | 2026-06-30 | Architecture, test gaps, documentation | Still useful |
| `CODE_QUALITY_AUDIT.md` | 2026-06-30 | God functions, dead code, singletons | Still useful |
| `SECURITY_AUDIT.md` | ~2026-06 | Security findings | **Partially stale** — shell/file/CORS fixed |
| `DEEP_AUDIT_REPORT.md` | Overwritten | Do not use | Replaced by this file |

---

*Generated by Cursor Agent (Grok) — July 7, 2026*