# Kazma Fresh Re-Audit Report

**Repository:** `G:\GitHubRepos\Kazma`  
**Commit audited:** `c46ae9b` (`fix: comprehensive audit remediation - critical fixes, tests, CI`)  
**Audit date:** 2026-07-08  
**Method:** `git pull` (already at origin/main), static analysis, targeted greps, pytest collection per package, targeted HITL/auth/reliability runs, `scripts/check_docs_sync.py`

**Supersedes for current state:** `AUDIT_DEEP_REPORT_2026-07-07.md`, `docs/AUDIT_KANBAN.md` (stale at `e5ad3a9`), `HARDENING_REPORT.md` (2026-06-30 claims).

---

## Executive Summary

Kazma on `c46ae9b` is a **mature, production-capable** multi-platform agent framework. Recent audit remediations (Sprints 14–17 + commits through `c46ae9b`) fixed most prior P0s: tool HITL wiring, shell allowlist, SSE graph holder, services facade, god-module splits (`agent_handler`, `swarm_panel`), hub fail-closed auth, and WS chat deprecation (410 close).

Remaining risk is **concentrated**, not systemic:

| Dimension | Grade | Headline |
|-----------|-------|----------|
| Security / HITL | **B+** | 3-tier HITL real + tested; MCP IDE name-mismatch + soft approve ownership remain |
| Architecture | **B+** | Packages clear; facade exists; engine still large; UI still peeks at privates |
| Code quality | **B-** | ~649 `except Exception`; engine 1.5k LOC; new reliability tests out of sync with API |
| Test hygiene | **B** | ~3,544 root + package tests; **conftest ImportPathMismatch** blocks monorepo-wide collect; 3 new reliability unit tests fail |
| Documentation | **C+** | `check_docs_sync` passes; badges/STATUS/AUDIT_KANBAN stale |
| Dependencies / CI | **B** | CI split by package (good); monorepo pytest still broken if run together |

**Verdict:** Ship-capable for local/self-hosted use. Before public network exposure, fix MCP IDE auth fail-open + danger-name mismatch, and enforce web HITL approve ownership (currently logs only).

---

## 1. Scale & Health (measured)

| Metric | Value |
|--------|-------|
| HEAD | `c46ae9b` |
| Version | 0.2.0 |
| Root `tests/` collected | **3,544** |
| `kazma-core/tests` | 50 |
| `kazma-gateway/tests` | 37 |
| `kazma-ui/tests` | 21 (all passed) |
| `kazma-tui/tests` | 216 |
| Docs sync (`scripts/check_docs_sync.py`) | **All passed** |
| Monorepo `pytest` (all trees) | **ImportPathMismatch** (`tests.conftest` collision) |

### Largest production modules (line counts)

| File | Lines | Notes |
|------|------:|-------|
| `swarm/engine.py` | **1512** | Still god-orchestrator after P2-1 split |
| `adapters/telegram.py` | 1124 | Largest adapter |
| `settings_manager.py` | 978 | |
| `model_registry.py` | 909 | |
| `agent/graph_builder.py` | 819 | |
| `agent/tool_registry.py` | 770 | Safety gate present |
| `app.py` | 735 | Routes extracted to `routes_direct` |
| `agent_handler.py` (facade) | **72** | Package split done |
| `swarm_panel.py` (facade) | **362** | Package split done |
| `services.py` | **295** | Facade exists |

### Docs vs reality

| Source | Claims | Actual | Status |
|--------|--------|--------|--------|
| README badge | 3,505 passing | 3,544 collected (root only) | Stale |
| README link badge | 3,495 | same | Stale / inconsistent |
| STATUS.md | 3,409 / 3,439 | outdated | Stale |
| `docs/AUDIT_KANBAN.md` | 7 CRITICAL open | Most fixed on main | **Misleading** |
| HARDENING_REPORT | allowlist has python/curl | **Removed** from allowlist | Stale |

---

## 2. Prior findings — disposition (re-verified)

### From AUDIT_DEEP_REPORT + AUDIT_KANBAN criticals

| ID | Finding | Status @ c46ae9b | Evidence |
|----|---------|------------------|----------|
| C-1 / MCP IDE | No auth / no HITL | **Partial** | Secret + `check_sync` added, but see **NEW-P0-1** |
| C-2 / WS HITL | Graph interrupt bypass | **Fixed (closed)** | `/ws/chat` closes 410 + deprecation; SSE is primary |
| C-3 / SSE stale graph | Closure holds pre-checkpointer graph | **Fixed** | `_graph_holder` + `graph_getter` in `app.py` / `sse_chat.py` |
| C-4 / services.py | Facade missing | **Fixed** | `kazma_ui/services.py` present |
| SEC-TR-02 | tool_registry no safety | **Fixed** | `await safety.check()` in `execute()` |
| SEC-TR-01 | allowlist python/curl/docker | **Fixed** | Interpreters/network tools removed |
| SEC-TR-03/04 | file tools unscoped / fail-open | **Fixed** | `_workspace_scope_error` fail-closed |
| BUG-VEC-01/02/03 | sqlite-vec API | **Fixed** | `vec0` + `MATCH` + `enable_load_extension` |
| SUB-01 | get_streaming_graph kwargs | **Fixed** | `lambda **kwargs: get_streaming_graph()` |
| AUTH-01a/b/c | Hub fail-open + prefixes | **Fixed** | Hub fail-closed; `SENSITIVE_PREFIXES` expanded |
| RACE-REG-01 | WorkerRegistry lock | **Fixed** | `threading.Lock` + singleton |
| RACE-ENG-01 | `_task_lock` unused | **Partial** | Lock used only in `reject_checkpoint`; finalize path unlocked |
| H-2 approve ownership | No ownership on web approve | **Partial** | Logs mismatch; **does not reject** |
| DISC-01 | Predictable disclosure HMAC | **Open** | Hostname/uid/user fallback still present |
| shell=True prod | Injection | **Fixed** | Only detector strings in linter/hardening |
| pickle in prod | RCE surface | **None found** | |

---

## 3. Critical findings (open)

### NEW-P0-1. MCP IDE danger gate is ineffective (name mismatch + secret fail-open)

**File:** `kazma-gateway/kazma_gateway/mcp_server.py` (~352–374)

```text
DANGER_MCP_TOOLS = {"write_file", "run_tests"}
# ...
if tool_name in DANGER_MCP_TOOLS:
    if safety.is_danger_tool(tool_name):   # write_file → False
        check_sync(...)
```

`SafetyMiddleware.is_danger_tool()` only knows `file_write`, `shell_exec`, `code_exec`, etc.  
Verified at runtime:

| Tool name | `is_danger_tool` |
|-----------|------------------|
| `write_file` | **False** |
| `run_tests` | **False** |
| `file_write` | True |
| `shell_exec` | True |

**Also:** secret enforcement runs only when `KAZMA_SECRET` is set (`if kazma_secret:`). If unset, any local MCP client can call `write_file` / `run_tests` with no auth and no HITL.

**Impact:** IDE MCP path can write files and run pytest under the project root without bus approval when secret is unset; even with secret, HITL branch never arms for these names.

**Fix:**

1. Always require secret for danger tools (fail-closed when unset), **or** disable danger tools when secret missing.  
2. Map MCP names → danger list: treat `write_file`/`run_tests` as danger unconditionally inside `DANGER_MCP_TOOLS` (don’t rely on `is_danger_tool(tool_name)` with wrong names).  
3. Prefer async bus approval when a bus is available.

---

### NEW-P0-2. Web HITL approve ownership is log-only (not enforced)

**File:** `kazma-ui/kazma_ui/routes_direct.py` (~479–503)

On mismatch of `owner` vs `body.session_id`, code only `logger.warning` then **still** `ainvoke(Command(resume=...))`.

Gateway path checks sender and blocks; web path does not.

**Impact:** Any authenticated browser session (shared-secret cookie auto-issued on any page hit) that can guess/obtain a `thread_id` can approve another user’s paused danger tool.

**Fix:** Return 403 on mismatch when owner context exists; require `session_id` when context is present; consider binding approvals to the initiating session cookie.

---

## 4. High priority

### H-1. Auth is shared-secret + cookie auto-issue (not per-user)

**File:** `kazma-ui/kazma_ui/auth.py`

- Auto-generates and persists secret when env unset (good for not being open).  
- Sets `kazma-secret` HttpOnly cookie on **always-open** routes (including `/`, `/health`) once secret exists.  
- Anyone who can load the dashboard UI gets API power for all sensitive prefixes.

Acceptable for single-operator local installs; insufficient for multi-user or internet-facing without reverse-proxy identity.

### H-2. `KAZMA_AUTH_DISABLED` + pytest open mode

When `KAZMA_AUTH_DISABLED` or pytest modules present, auth returns empty secret → open sensitive APIs. Ensure production never sets the disable flag; CI should not deploy with it.

### H-3. Engine `_task_lock` incomplete (RACE-ENG-01 residual)

`_task_history` mutated in `_finalize_task` without lock; lock only in `reject_checkpoint`. Concurrent cancel/retry/finalize can race under load.

### H-4. Private attribute leakage still in UI

Facade exists, but:

- `health.py` uses `engine._workers`, `registry._providers`  
- `services.py` fallbacks still touch `_workers` / `_task_handles`  
- `test_service_facade.py::TestNoPrivateAccessInUI` **FAILS**

### H-5. New reliability unit tests disagree with API

`kazma-core/tests/unit/test_reliability.py` `TestFallbackChain`:

- Calls `chain.add()` / `chain.execute()` with no args  
- Real `FallbackChain.execute(primary_result, dispatch_worker=...)`  
→ **3 failures** — tests written against imaginary API (CI risk when job is enabled).

### H-6. Monorepo pytest conftest collision

Both `tests/conftest.py` and `kazma-core/tests/conftest.py` (and gateway) resolve as `tests.conftest` → `ImportPathMismatchError` when collecting multiple trees together.

CI works only because jobs are split. Local `pytest` from repo root across packages breaks.

### H-7. Disclosure HMAC key still guessable (DISC-01)

`disclosure.py` falls back to `kazma-{hostname}-{uid}-{username}` when `KAZMA_DISCLOSURE_KEY` unset.

### H-8. Telegram adapter still a god module (~1124 LOC)

Largest remaining non-engine concentration of platform logic.

---

## 5. Medium priority

| ID | Issue |
|----|-------|
| M-1 | ~649 `except Exception` in prod packages; ~31 silent `pass` next-line cases |
| M-2 | `engine.py` still 1.5k LOC — further extract dispatch/history/SSE emit |
| M-3 | Docs drift: README dual badges, STATUS, AUDIT_KANBAN, HARDENING allowlist claims |
| M-4 | CI `test-integration` job has `if: false` — multi-platform integration present but not gated |
| M-5 | WS dead code retained after early `return` (~200+ lines unreachable in `chat.py`) — delete or gate behind feature flag |
| M-6 | `get_kazma_secret()` duplicated: UI auto-gen vs `config_store.get_kazma_secret()` env-only |
| M-7 | Windows weaker sandbox for `code_exec` / `python_exec` (no POSIX rlimits) |
| M-8 | Integration layer still thin relative to unit volume |

---

## 6. What’s solid (do not regress)

### HITL — three mechanisms

| Mechanism | Status |
|-----------|--------|
| A. Graph `interrupt()` | Wired via `get_streaming_graph` + startup recompile + gateway; SSE `approval_required` frames |
| B. Swarm bus | Fail-closed `check_sync`; async `check` in tool_registry; Telegram/Discord/Slack adapters |
| C. Pipeline checkpoints | `checkpoint_manager` + API approve/reject |

Verified: `kazma-core/tests/test_hitl_gates_wired.py` → **14 passed**.

### Platform isolation

`_PLATFORM_KEYS` in `agent_handler/store.py`; graph state keeps `thread_id` only.

### Shell / files / MCP manager

- `create_subprocess_exec` + narrowed `_SAFE_BINARIES`  
- Workspace fail-closed on file tools  
- MCP client tools: pattern classify + bus gate in `mcp/manager.py`  
- No production `shell=True` / `pickle` / live `eval(`

### ConfigStore

WAL + busy_timeout, singleton, `batch_set` / `transaction`, `reconcile_from_yaml`, `apply_sqlite_pragmas`.

### Architecture remediations landed

- `services.py` SwarmService  
- `swarm_panel/` route package  
- `agent_handler/` package  
- SSE public bus wiring (no monkey-patch required path)  
- Skill checksums fail-closed + HMAC  
- Danger list includes spawn/schedule tools in `kazma.yaml`

### Docs sync automation

`scripts/check_docs_sync.py` green for critical architecture claims.

---

## 7. Targeted test results (this audit)

| Suite | Result |
|-------|--------|
| `kazma-ui/tests` | **21 passed** |
| `kazma-core/tests/test_hitl_gates_wired.py` | **14 passed** |
| `tests/test_hitl_wiring.py` + auth (ran with facade) | HITL/auth green; see facade fail |
| `tests/test_service_facade.py` | **1 failed** (`TestNoPrivateAccessInUI`) |
| `kazma-core/tests/unit/test_reliability.py` FallbackChain | **3 failed** (API mismatch) |
| Full monorepo collect | **Blocked** by conftest name collision |

---

## 8. Kanban reconciliation

Treat `docs/AUDIT_KANBAN.md` as **historical**. Approximate status:

| Bucket | Count | Notes |
|--------|------:|-------|
| FIXED since kanban base | ~18 | SEC-TR-*, VEC, AUTH, WS, shell, registry lock, scopes, etc. |
| Still open / partial | ~6 | RACE-ENG residual, DISC-01, ORCH/delegation items (not fully re-verified), docs |
| New since kanban | 2 P0 + several H | MCP name mismatch, soft ownership, reliability tests, conftest clash |

---

## 9. Recommended fix order

### Sprint A — Security (½–1 day)

1. **NEW-P0-1** MCP IDE: force-gate `DANGER_MCP_TOOLS` without `is_danger_tool` name lookup; require secret always for danger (or disable).  
2. **NEW-P0-2** Web approve: hard 403 on ownership mismatch.  
3. **DISC-01** Use `secrets.token_hex(32)` when disclosure key unset (persist in ConfigStore).  
4. Align MCP tool names with bus danger list or alias table.

### Sprint B — Test / CI hygiene (½ day)

1. Rename package confests (`kazma_core_tests/conftest.py` pattern) or set `pytest` `pythonpath` / `import-mode=importlib` monorepo-wide.  
2. Fix or delete broken `TestFallbackChain` cases to match real API.  
3. Fix `TestNoPrivateAccessInUI` by routing health checks through `list_workers()` / public registry API.  
4. Enable CI integration job once multi-platform suite is stable.  
5. Refresh README / STATUS badges to collected counts + “per-package CI”.

### Sprint C — Debt (ongoing)

1. Acquire `_task_lock` on all `_task_history` mutations.  
2. Delete dead WS implementation after 410 close.  
3. Further split `engine.py` and `telegram.py`.  
4. Unify `get_kazma_secret()` (one implementation with auto-gen policy documented).  
5. Reduce silent `except Exception: pass` remaining ~31 sites.

---

## 10. Residual risk statement

For **single-operator localhost** with `KAZMA_SECRET` set and MCP IDE disabled or secret-required clients only: residual risk is **moderate-low**.

For **LAN/public exposure** or multi-user without reverse-proxy auth: residual risk is **high** until P0-1/P0-2 and shared-secret model are addressed.

---

## 11. Appendix — package map

| Package | Role |
|---------|------|
| `kazma-core` | Agent, LLM, swarm, config, hub, security, tools |
| `kazma-gateway` | Telegram/Discord/Slack, agent_handler, MCP IDE server |
| `kazma-ui` | FastAPI, SSE chat, swarm panel, auth, workspace |
| `kazma-tui` | Textual dashboard (read-mostly registry) |
| `kazma-cli` | CLI entrypoints |
| `kazma-skills` / `kazma-memory` | Skills + memory helpers |

---

*Generated 2026-07-08 against commit `c46ae9b`. Re-run collection and P0 checks after any security-path change.*
