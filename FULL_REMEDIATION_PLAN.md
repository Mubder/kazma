# FULL REMEDIATION & MAINTENANCE PLAN

**Source:** Consolidated from Phase 0-5 Remediation + AUDIT_REAUDIT_2026-07-08.md  
**Status:** Phases 0-5 Complete | Sprints A-C Ready | Ongoing Debt Tracked  
**Last Updated:** 2026-07-08 (commit c46ae9b)

---

## 📊 EXECUTIVE SUMMARY

| Metric | Status |
|--------|--------|
| **Critical Fixes (P0)** | ✅ 5/5 Complete |
| **Core Tests Passing** | ✅ 14/14 HITL gates, 37/37 gateway, 21/21 UI |
| **Docs Sync** | ✅ 10/10 checks pass |
| **CI/CD** | ✅ GitHub Actions pipeline active |
| **Integration Tests** | ⚠️ 11/16 failing (mock API mismatches) |
| **Reliability Tests** | ⚠️ 3/6 FallbackChain failing (API drift) |
| **Conftest Collision** | ❌ Blocks monorepo test collection |

**Residual Risk:** Moderate-low for localhost single-operator; High for LAN/public without reverse-proxy auth until P0-1/P0-2 resolved.

---

## 🎯 PHASES 0-5: COMPLETED (Audit Remediation)

### Phase 0: Test Infrastructure ✅
- Created `kazma-core/tests/`, `kazma-gateway/tests/`, `kazma-ui/tests/`
- Added `pyproject.toml` test config with coverage, markers
- Shared fixtures in `conftest.py` per package

### Phase 1: Critical Fixes ✅
| Fix | File | Impact |
|-----|------|--------|
| ConfigStore silent failure → in-memory fallback | `kazma-core/kazma_core/config_store.py` | Prevents AttributeError downstream |
| `engine.dispatch()` 5-min timeout | `kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py` | Prevents indefinite hangs |
| `_InMemoryStore` thread-safe locking | `kazma-core/kazma_core/config_store.py` | Prevents dict corruption under concurrency |
| Error message sanitization | `kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py` | No internal details leaked to users |
| Input validation `_parse_output_target_suffix` | `kazma-gateway/kazma_gateway/agent_handler/swarm_dispatch.py` | Validates platform + Telegram chat_id ranges |
| WebSocket `/chat` deprecated (410 Gone) | `kazma-ui/kazma_ui/chat.py` | Redirects to SSE `/api/chat/stream` for full HITL |

### Phase 2: Test Coverage ✅
| Test File | Scope | Tests |
|-----------|-------|-------|
| `kazma-core/tests/test_hitl_gates_wired.py` | 3 HITL mechanisms verified at runtime | 14 |
| `kazma-gateway/tests/test_swarm_approval_callbacks.py` | Sprint 14 regression (dead seam fix) | 13 |
| `kazma-ui/tests/test_unit.py` | Unit tests for UI components | 21 |
| `kazma-core/tests/unit/test_reliability.py` | Circuit breaker, retry, timeout, concurrency | 16/19 pass |

### Phase 3: Code Quality ✅
| Module | Purpose |
|--------|---------|
| `kazma-core/kazma_core/constants.py` | 50+ centralized constants (timeouts, limits, IDs) |
| `kazma-core/kazma_core/exceptions.py` | `KazmaError` hierarchy + `sanitize_error()` |
| `kazma-core/kazma_core/config_schema.py` | Pydantic models with cross-section validation |
| `kazma-core/kazma_core/migrations.py` | Versioned SQLite migration runner |
| `kazma-core/kazma_core/tracing.py` | OpenTelemetry setup (env-gated) |

### Phase 4: Config & Reliability ✅
| Feature | File |
|---------|------|
| Pydantic config schema with cross-validation | `config_schema.py` |
| Health endpoints `/health/live`, `/health/ready` | `kazma-ui/kazma_ui/health.py` |
| SQLite migration framework | `migrations.py` + `config_store.py` integration |

### Phase 5: WebSocket Deprecation ✅
- `/chat` WebSocket returns 410 Gone with redirect to SSE
- Explicit security warning in docstring about missing HITL Mechanism A

### Infrastructure ✅
| Item | Status |
|------|--------|
| `.github/workflows/ci.yml` | Multi-job pipeline (lint, typecheck, test, coverage, security, docker) |
| `scripts/check_docs_sync.py` | 10/10 checks pass (CI gate) |
| `FULL_REMEDIATION_PLAN.md` | This document |

---

## 🚨 SPRINT A: SECURITY (½–1 DAY) — From Re-audit

### NEW-P0-1: MCP IDE Danger Tool Gate
**File:** `kazma-gateway/kazma_gateway/mcp_ide_server.py` (or similar)
**Issue:** MCP IDE server allows danger tools without proper secret validation
**Fix:** Force-gate `DANGER_MCP_TOOLS` env var; require `KAZMA_SECRET` for any danger tool execution; disable if secret not set

### NEW-P0-2: Web Approve Ownership Check
**File:** `kazma-ui/kazma_ui/routes_direct.py` (or `/api/approve/`)
**Issue:** Approval endpoint doesn't verify requester owns the thread
**Fix:** Hard 403 on ownership mismatch; verify `thread_id` belongs to requesting session/user

### DISC-01: Disclosure Key Generation
**File:** `kazma-core/kazma_core/config_store.py` or auth module
**Issue:** No persistent disclosure key when unset
**Fix:** Generate `secrets.token_hex(32)` on first run; persist in ConfigStore under `security.disclosure_key`

### MCP Tool Name Alignment
**File:** `kazma-core/kazma_core/swarm/safety.py` + `kazma-gateway/kazma_gateway/mcp_ide_server.py`
**Issue:** MCP tool names don't match bus danger list (`_EXTENDED_DANGER`)
**Fix:** Add alias table or normalize names before `is_danger_tool()` check

---

## 🧪 SPRINT B: TEST / CI HYGIENE (½ DAY) — From Re-audit

### B1: Conftest Collision Fix
**Problem:** `kazma-core/tests/conftest.py` and `kazma-gateway/tests/conftest.py` both define `tests` package → collection blocked
**Fix Options:**
1. Rename to `kazma_core_tests/conftest.py`, `kazma_gateway_tests/conftest.py` + update `pyproject.toml` `testpaths`
2. Use `pytest.ini` `importmode=importlib` + explicit `pythonpath`
3. Single root `tests/conftest.py` with package-scoped fixtures

**Recommended:** Option 1 — minimal change, explicit namespaces

### B2: Fix `TestFallbackChain` API Mismatches
**File:** `kazma-core/tests/unit/test_reliability.py`
**Current API (real):**
```python
chain = FallbackChain(fallback_workers=["worker2", "worker3"])
result = await chain.execute(primary_result, dispatch_worker=dispatch_fn)
```
**Fix:** Update 3 failing tests to match real constructor + `execute()` signature

### B3: Fix `TestNoPrivateAccessInUI`
**File:** `kazma-ui/tests/test_service_facade.py` (or similar)
**Problem:** Health check uses private `SwarmEngine._workers`
**Fix:** Route through `list_workers()` public method on registry

### B4: Enable CI Integration Job
**File:** `.github/workflows/ci.yml`
**Action:** Change `test-integration: if: false` → `if: always()` once B1-B3 pass

### B5: Badge Refresh
**Files:** `README.md`, `STATUS.md`
**Update:** Dynamic badges showing per-package test counts + coverage

---

## 🏗 SPRINT C: TECH DEBT (ONGOING)

| ID | Area | Description | Effort |
|----|------|-------------|--------|
| **C1** | `engine.py` | Acquire `_task_lock` on ALL `_task_history` mutations (race residual) | S |
| **C2** | WebSocket | Delete dead WS implementation after 410 deprecation period | S |
| **C3** | `engine.py` | Further split: `dispatch.py`, `handoff.py`, `patterns.py` | M |
| **C4** | `telegram.py` | Split: `polling.py`, `callback.py`, `send.py` | M |
| **C5** | Secrets | Unify `get_kazma_secret()` — single impl with documented auto-gen policy | S |
| **C6** | Silent fails | Audit ~31 remaining `except Exception: pass` sites; add logging or narrow | M |
| **C3** | RACE-ENG | Verify `_task_lock` acquired on all `_task_history` writes (dispatch, complete, cancel) | S |
| **DISC-01** | Auth | Persist disclosure key in ConfigStore (see Sprint A) | S |
| **ORCH** | Delegation | Re-verify delegation chain items not fully re-verified | M |

---

## 📋 INTEGRATION TEST FIXES (Post Sprint B)

### Multi-Platform Integration Tests
**File:** `kazma-core/tests/integration/test_multi_platform.py`

| Failure | Failure | Root Cause | Fix |
|--------|------------|-----|
| `test_swarm_dispatch_routes_correctly` | `target_override` param not in signature | Match actual `_dispatch_swarm_from_chat(manager, engine, msg, thread_id, store)` |
| `test_swarm_dispatch_timeout_handling` | Same + mock engine dispatch | Align mock to real signature |
| `test_graph_interrupt_approve_resume` | Graph interrupt check logic | Use proper LangGraph `interrupt()` detection |
| `test_swarm_bus_approval_flow` | Safety check expectation | Align to real `safety.check()` return semantics |
| `test_pattern_execution` | `TaskType.DISPATCH` vs `TaskType.dispatch` | Use enum values from `TaskType` |
| `SwarmTaskResult` import | Class is `TaskResult` in `task.py` | Fix import |

### Reliability Test Fixes
**File:** `kazma-core/tests/unit/test_reliability.py`

| Test | Real API | Fix |
|------|----------|-----|
| `TestFallbackChain` | `FallbackChain(fallback_workers=[...])` + `execute(primary_result, dispatch_worker=fn)` | Update 3 tests |
| `_is_retryable_exception` | Uses `_NON_RETRYABLE_PATTERNS` tuple | Test against actual patterns |

---

## 🔧 CONFTES COLLISION FIX — DETAILED

### Option 1: Package-Specific Conftest (Recommended)
```bash
# 1. Rename directories
mv kazma-core/tests/conftest.py kazma-core/kazma_core_tests/conftest.py
mv kazma-gateway/tests/conftest.py kazma-gateway/kazma_gateway_tests/conftest.py

# 2. Update pyproject.toml
[tool.pytest.ini_options]
testpaths = [
    "kazma-core/kazma_core_tests",
    "kazma-gateway/kazma_gateway_tests",
    "kazma-ui/tests",
    "kazma-tui/tests"
]
python_files = ["test_*.py"]
```

### Option 2: Importlib Mode (If Pytest 7+)
```ini
[tool.pytest.ini_options]
importmode = "importlib"
pythonpath = ["kazma-core", "kazma-gateway", "kazma-ui", "kazma-tui"]
```

---

## 📦 ARCHITECTURE MAP (Reference)

| Package | Role | Key Modules |
|---------|------|-------------|
| `kazma-core` | Agent, LLM, swarm, config, hub, security, tools | `agent/`, `swarm/`, `config_store.py`, `model_registry.py` |
| `kazma-gateway` | Telegram/Discord/Slack, agent_handler, MCP IDE server | `adapters/`, `agent_handler/`, `mcp_ide_server.py` |
| `kazma-ui` | FastAPI, SSE chat, swarm panel, auth, workspace | `app.py`, `sse_chat.py`, `chat.py`, `auth.py` |
| `kazma-tui` | Textual dashboard (read-mostly registry) | `app.py` |
| `kazma-cli` | CLI entrypoints | `main.py` |
| `kazma-skills` / `kazma-memory` | Skills + memory helpers | — |

---

## 📋 QUICK COMMANDS

```bash
# Run all tests (after conftest fix)
python -m pytest kazma-core/kazma_core_tests kazma-gateway/kazma_gateway_tests kazma-ui/tests -v

# Core only
python -m pytest kazma-core/kazma_core_tests -v

# Gateway only
python -m pytest kazma-gateway/kazma_gateway_tests -v

# UI only
python -m pytest kazma-ui/tests -v

# Docs sync check
python scripts/check_docs_sync.py

# Lint + typecheck
ruff check .
mypy kazma-core/kazma_core kazma-gateway/kazma_gateway kazma-ui/kazma_ui --ignore-missing-imports

# Security scan
bandit -r kazma-core/kazma_core kazma-gateway/kazma_gateway kazma-ui/kazma_ui -f json -o bandit-report.json || true
```

---

## 📈 TRACKING

| Sprint | Target Date | Owner | Status |
|--------|-------------|-------|--------|
| A (Security) | 2026-07-10 | — | ⏳ Ready |
| B (Test/CI) | 2026-07-12 | — | ⏳ Ready |
| C (Debt) | Ongoing | — | 📋 Backlog |

---

**Generated:** 2026-07-08  
**Base Commit:** `c46ae9b`  
**Next Review:** After Sprint A complete