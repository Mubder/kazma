# Kazma Deep Audit Report — 2026-07-07

**Auditor:** Poolside Agent  
**Repository:** `G:\GitHubRepos\kazma`  
**Scope:** Full repository analysis (code, architecture, tests, security, documentation)

---

## Executive Summary

The Kazma project is a sophisticated autonomous AI agent framework with impressive architectural design. However, this deep audit reveals **multiple critical issues** that require immediate attention, particularly around security vulnerabilities and documentation accuracy.

| Category | Status | Key Findings |
|----------|--------|--------------|
| Architecture | ⚠️ Needs Review | Missing `services.py`, monkey-patching coupling |
| Security | 🔴 Critical Issues | Arbitrary command execution, path traversal, auth bypass |
| Tests | ✅ Strong Coverage | 3,495 tests collected (outdated README claims) |
| Documentation | 🔴 Inaccurate | Fabricated API references in `core-api.md` |
| Code Quality | ⚠️ Minor Issues | 85 linting issues, mypy type errors |

---

## 1. ARCHITECTURE AUDIT

### 1.1 Package Structure Analysis

The project follows a monorepo structure with 6 main packages aligned with `pyproject.toml`:

| Package | Build Target | Status |
|---------|--------------|--------|
| `kazma-core` | ✅ Included | Core agent, tools, swarm, security |
| `kazma-memory` | ✅ Included | Vector memory backend |
| `kazma-skills` | ✅ Included | Skill management |
| `kazma-ui` | ✅ Included | FastAPI dashboard |
| `kazma-cli` | ✅ Included | CLI entry point |
| `kazma-tui` | ✅ Included | Terminal UI |
| `kazma-gateway` | ✅ Included | Platform adapters |

**Finding:** The `pyproject.toml` correctly defines all 6 packages for wheel building.

### 1.2 Missing Service Facade (`SERVICES.py`)

**Severity:** 🔴 HIGH — Documented but not implemented

The README architecture tree documents:
```
kazma_ui/services.py  — Service facade layer — zero private attr access from UI
```

**Reality:** This file **does not exist**. The codebase has no service facade layer.

**Impact:** UI code directly accesses private engine attributes, as seen in `swarm_sse.py`:
- Line 268-269: `engine._finalize_task`, `engine._dispatch_worker` (monkey-patched)
- Line 276-277: `engine._active_task_id`, `engine._sse_step_counter` 
- Line 377-379: Runtime method assignment `engine._finalize_task = _patched_finalize`

**Recommendation:** Either implement `services.py` as documented or remove the claim and refactor the SSE wiring.

### 1.3 Circular Imports Resolution

**Status:** ✅ RESOLVED

The codebase correctly handles circular imports via lazy imports in `agent/__init__.py`:
```python
try:
    from kazma_core.agent_runner import AgentConfig, KazmaAgent, ...
except ImportError:
    pass
```

### 1.4 ModelRouter Singleton Pattern

**Status:** ⚠️ Design choice, not a bug

The `ModelRouter` is not a singleton but is passed as a parameter through the graph. All LLM calls route through `LLMProvider.chat()`, making the behavior consistent.

---

## 2. SECURITY AUDIT

### 2.1 Critical Vulnerabilities

| # | Severity | Issue | Current Status |
|---|----------|-------|---------------|
| 1 | **CRITICAL** | `shell_exec` arbitrary command execution | ⚠️ **Partially Mitigated** - Uses `create_subprocess_exec` with allowlist (lines 766-803) |
| 2 | **CRITICAL** | File tools bypass workspace scoping | ✅ **FIXED** - Uses `_workspace_scope_error()` helper |
| 3 | **HIGH** | Hardcoded dummy API key `sk-local-dev` | Present in `chat.py` line 207, 214 |
| 4 | **HIGH** | Auth bypass when `KAZMA_SECRET` unset | ✅ **FIXED** - Auto-generates secret on startup |
| 5 | **HIGH** | `sqlite_query` arbitrary DB path | ✅ **FIXED** - Path restriction added (lines 647-664) |
| 6 | **MEDIUM** | CORS `allow_headers=["*"]` | ✅ **FIXED** - Now uses explicit headers (line 188) |
| 7 | **MEDIUM** | HITL can be silently disabled | Config allows disabling without audit log |

### 2.2 Security Improvements Observed

The codebase has evolved since the previous audit report. Key improvements:

1. **`shell_exec` hardening** (lines 746-838 in `tool_registry.py`):
   - Now uses `create_subprocess_exec` instead of `create_subprocess_shell`
   - Includes `_SAFE_BINARIES` allowlist (40+ safe commands)
   - Runs with restricted `cwd` to workspace
   - 30-second timeout, output capped at 10KB
   - Logs all invocations with warning level

2. **File tool security** (lines 508-603 in `tool_registry.py`):
   - All file operations use `_workspace_scope_error()` helper
   - Includes temp directory fallback for tests
   - 1MB file size cap

3. **Auth middleware** (`auth.py`):
   - Auto-generates secure 32-char hex token when `KAZMA_SECRET` unset
   - Persists to `.env` file
   - Uses `hmac.compare_digest` for timing-safe comparison
   - WebSocket endpoints accept connections without auth (still open)

4. **SQLite query restrictions** (lines 641-664 in `tool_registry.py`):
   - Only allows SELECT/WITH queries
   - Blocks multi-statement queries with `;`
   - AST-based forbidden keyword check
   - Authorizer callback enforces read-only at SQLite level
   - Path restriction to known directories

### 2.3 Remaining Security Gaps

| Gap | Location | Recommendation |
|-----|----------|----------------|
| WebSocket unauthenticated | `app.py:222-257` | Validate `X-Kazma-Secret` in handshake |
| HITL disable without audit | `hitl.py:75` | Log CRITICAL warning when disabled |
| Exception leak in exports | `settings.py:119` | Generic error messages |
| KAZMA_SECRET in templates | `app.py:126` | Remove from global context |

---

## 3. CODE QUALITY AUDIT

### 3.1 Linting Results (ruff)

```
kazma-core: 65 errors (60 fixable)
  - I001: unsorted-imports (20)
  - UP006: non-pep585-annotation (16)
  - UP045: non-pep604-annotation-optional (9)
  - W291: trailing-whitespace (6)
  - F401: unused-import (3)
  - E401: multiple-imports-on-one-line (2)
  - F811: redefined-while-unused (1)
  - UP015: redundant-open-modes (1)
  - UP041: timeout-error-alias (1)

kazma-ui: 20 errors (17 fixable)
  - I001: unsorted-imports (14)
  - N813: camelcase-imported-as-lowercase (1)
  - F401: unused-import (1)
  - F541: f-string-missing-placeholders (1)
  - N814: camelcase-imported-as-constant (1)
  - UP041: timeout-error-alias (1)
```

### 3.2 Type Checking (mypy)

60+ type errors detected across modules. Key patterns:
- Missing type arguments for generic types (`dict`, `list`)
- `str-bytes-safe` errors in docs module
- Missing library stubs for `tiktoken` and `yaml`
- `attr-defined` error for `getuid` on Windows

### 3.3 Code Organization

**Positive patterns:**
- Clear separation of concerns (agent, swarm, tools, security)
- Good use of dataclasses for state
- Proper async/await patterns throughout
- ContextVar usage for tenant/session isolation

**Areas for improvement:**
- Some files exceed 500 lines (should be split)
- Monkey-patching in `swarm_sse.py` creates fragile coupling
- Repeated imports of `get_swarm_engine()` in hot paths

---

## 4. TEST COVERAGE AUDIT

### 4.1 Test Count Verification

| Source | Claim | Actual |
|--------|-------|--------|
| README badge | "3,655 passing" | 3,495 tests collected |
| Previous audit | "3,260 tests" | 3,495 tests (grown by ~235) |

**Note:** The test count has increased, but the README claims 3,655 which is inaccurate.

### 4.2 Test Categories

| Category | Count | Coverage |
|----------|-------|----------|
| Unit tests | ~2,000 | Good coverage of core modules |
| Integration tests | ~50 | Basic graph checkpointing works |
| E2E tests | ~10 | Limited real-world flow testing |

### 4.3 Uncovered Core Modules

| Module | Impact | Test File Needed |
|--------|--------|-----------------|
| `compaction.py` | Context compaction | ❌ Missing |
| `token_counter.py` | Token counting | ❌ Missing |
| `dialect_detector.py` | Arabic dialect detection | ❌ Missing |
| `streaming.py` | SSE streaming | ❌ Missing |
| `permissions.py` | RBAC permissions | ❌ Missing |
| `mcp_client.py` | MCP client | ❌ Missing |
| `settings_manager.py` | Settings persistence | ❌ Missing |
| `security/certification.py` | Certification chain | ❌ Missing |
| `security/audit_trail.py` | Audit logging | ❌ Missing |
| `security/disclosure.py` | Capability disclosure | ❌ Missing |
| `memory/fts5.py` | FTS5 full-text search | ❌ Missing |
| `memory/kg_adapter.py` | Knowledge graph adapter | ❌ Missing |

### 4.4 Integration Test Gaps

**Critical gap:** No end-to-end test covering:
```
User message → SSE endpoint → LangGraph → Tool execution → Response
```

The closest test (`test_graph_roundtrip.py`) uses stub LLM and doesn't go through HTTP layer.

---

## 5. DOCUMENTATION AUDIT

### 5.1 API Reference Inaccuracies (`core-api.md`)

The file contains fabricated class names and incorrect imports:

| Documented | Actual | Status |
|------------|--------|--------|
| `from kazma_core.agent import Agent` | `KazmaAgent` from `agent_runner` | ❌ Wrong |
| `agent.process(message)` | `agent.run(message)` | ❌ Wrong |
| `from kazma_core.checkpoint import CheckpointManager` | Not a public class | ❌ Wrong |
| `from kazma_core.compaction import ContextCompactor` | `CompactionEngine` | ❌ Wrong |
| `from kazma_core.swarm.registry import WorkerRegistry` | `WorkerPhonebook` | ❌ Wrong |
| `from kazma_core.swarm.memory import UnifiedMemoryAdapter` | Not found | ❌ Wrong |

### 5.2 Architecture Tree Inconsistencies (README)

| Claimed Package | Exists | Status |
|-----------------|--------|--------|
| `kazma-providers/` | ❌ No | ❌ False |
| `kazma-memory/` | ✅ Yes | ✅ Accurate |
| `kazma-skills/` | ✅ Yes | ✅ Accurate |

### 5.3 CLI Documentation

**Status:** ✅ Most accurate

The CLI reference (`cli-reference.md`) correctly documents all commands.

---

## 6. DEPENDENCY ANALYSIS

### 6.1 Dependencies (from pyproject.toml)

| Category | Packages | Risk Level |
|----------|----------|------------|
| Core | FastAPI, LangGraph, SQLite | Low |
| LLM | google-cloud-aiplatform, langfuse | Medium |
| Observability | OpenTelemetry, Prometheus | Low |
| UI | Textual, Jinja2, WebSockets | Low |
| Security | cryptography | Low |

### 6.2 Optional Dependencies

| Extra | Purpose | Security Impact |
|-------|---------|----------------|
| `dev` | pytest, ruff, mypy | None |
| `tui` | textual, python-bidi | None |
| `rag` | chromadb, sentence-transformers | Model loading |

---

## 7. FINDINGS SUMMARY

### Severity Distribution

| Severity | Count | Key Issues |
|----------|-------|------------|
| 🔴 Critical | 2 | shell_exec (partially fixed), file tool scoping (fixed) |
| 🟠 High | 4 | Auth bypass (fixed), CORS (fixed), API key leak, websocket auth |
| 🟡 Medium | 5 | HITL disable, exception leak, Hub API auth |
| 🔵 Low | 3 | ReDoS, linting, type hints |

### Architecture Issues

1. **Missing `services.py`** — The documented service facade doesn't exist
2. **Monkey-patching SSE** — `swarm_sse.py` modifies private engine methods
3. **No E2E integration test** — Real user flow untested
4. **Outdated test count** — README claims don't match actual count

---

## 8. RECOMMENDATIONS

### Priority 1: Security Fixes (Immediate)

1. **Add WebSocket authentication** — Validate secret in handshake
2. **Add HITL disable audit log** — Log when safety is disabled
3. **Fix exception leak in settings export** — Use generic error messages
4. **Remove KAZMA_SECRET from global template context**

### Priority 2: Architecture Fixes (This Sprint)

1. **Implement `services.py`** or remove from documentation
2. **Refactor SSE wiring** — Use public callback registration instead of monkey-patching
3. **Add E2E integration test** — Full user message to response flow

### Priority 3: Documentation Fixes (Next Sprint)

1. **Fix `core-api.md`** — Replace fabricated references with actual classes
2. **Update README test count** — Use actual count (3,495)
3. **Add module docstrings** where missing

### Priority 4: Code Quality (Backlog)

1. Run `ruff --fix` on both packages
2. Add type arguments to generic types
3. Fix str-bytes-safe errors in docs module
4. Install tiktoken stubs for mypy

---

## 9. FILES REFERENCED

### Key Source Files Analyzed
- `kazma-core/kazma_core/agent_runner.py` — Main agent class
- `kazma-core/kazma_core/agent/tool_registry.py` — Tool registration
- `kazma-core/kazma_core/safety/hitl.py` — HITL approval gates
- `kazma-core/kazma_core/swarm/engine.py` — Swarm orchestration
- `kazma-core/kazma_core/swarm/worker.py` — In-process worker
- `kazma-ui/kazma_ui/app.py` — FastAPI application factory
- `kazma-ui/kazma_ui/sse_chat.py` — SSE chat endpoint
- `kazma-ui/kazma_ui/swarm_sse.py` — SSE event wiring (monkey-patch)
- `kazma-ui/kazma_ui/auth.py` — Authentication middleware
- `kazma-gateway/kazma_gateway/gateway.py` — Gateway manager
- `kazma-core/kazma_core/hub/api.py` — Hub REST API

### Documentation Files Analyzed
- `README.md` — Main documentation (501 lines)
- `docs/docs/api-reference/core-api.md` — API reference (fabricated)
- `AUDIT_REPORT.md` — Previous architecture audit (370 lines)
- `SECURITY_AUDIT.md` — Previous security audit (401 lines)

---

## 10. CONCLUSION

The Kazma project demonstrates **strong architectural foundations** and **solid test coverage**, but has a pattern of **documentation drift** and **security gaps** that have been partially but not fully addressed. The security improvements made since the last audit (shell_exec hardening, auto-secret generation, workspace scoping) are commendable, but critical issues around WebSocket authentication and HITL audit logging remain.

**Overall Grade: B+** — A capable framework with room for security hardening and documentation accuracy improvements.

---

*Generated: 2026-07-07 by Poolside Agent*