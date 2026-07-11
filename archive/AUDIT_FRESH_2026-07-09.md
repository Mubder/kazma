# Kazma Fresh Audit — Bugs & Gaps

**Date:** 2026-07-09  
**HEAD:** `ac33257` (`docs: Update architecture.md, ROADMAP.md, README.md with Phase 3 features`)  
**Method:** `git pull`, static analysis, auth path probes, targeted pytest, docs sync  

**Prior baseline:** `AUDIT_REAUDIT_2026-07-08.md` @ `c46ae9b`  
**Delta since then:** Sprint A+B security + test rename; OTel; Phase 3 chaos/loadtests/migrate UI/adapter extract

---

## Executive Summary

| Dimension | Grade | Notes |
|-----------|-------|-------|
| Security (core HITL / shell / MCP IDE danger) | **A-** | Prior P0s largely fixed |
| Security (new API surface) | **C** | Chaos + migrate + workspace APIs **outside** auth prefixes |
| Architecture | **B+** | Adapter extract, services facade, package test rename |
| Test / CI integrity | **D+** | **~3,544 root tests not in CI**; badge says 108 |
| Code quality / debt | **B-** | Engine still large; WS dead code; race residual |
| Docs accuracy | **C** | Conflicting version/test counts |

**Ship posture:** Fine for **local single-operator**.  
**Not ready for multi-user / exposed network** until auth prefix gaps and ownership bypass are closed.

---

## What improved since 2026-07-08 re-audit

| Prior finding | Status now | Evidence |
|---------------|------------|----------|
| NEW-P0-1 MCP IDE name mismatch + secret fail-open | **FIXED** | `MCP_TOOL_TO_SAFETY`, fail-closed when secret missing for danger tools; `run_tests` in `_EXTENDED_DANGER` |
| NEW-P0-2 Web approve ownership log-only | **MOSTLY FIXED** | Returns **403** on owner/caller mismatch (`routes_direct.py`) — residual bypass if `session_id` omitted |
| FallbackChain unit tests API drift | **FIXED** | `kazma_core_tests/unit/test_reliability.py` → **22 passed** |
| Conftest collision (core/gateway/ui) | **FIXED** | Renamed to `kazma_core_tests`, `kazma_gateway_tests`, `kazma_ui_tests` |
| Service facade private-access test | **FIXED** | `test_service_facade` all green in sample run |
| Multi-platform integration suite | **PASSING** | **14/14** `kazma_core_tests/integration` |
| Chaos + loadtests + migrate UI + swarm_output | **SHIPPED** | Sprint 19 / Phase 3 |
| Docs sync script | **GREEN** | 10/10 checks |

---

## Scale (measured)

| Suite | Collected | Notes |
|-------|----------:|-------|
| Root `tests/` | **3,544** | Still the real regression suite |
| `kazma_core_tests` | 50 | CI runs this |
| `kazma_gateway_tests` | 37 | CI |
| `kazma_ui_tests` | 21 | CI |
| `kazma-tui/tests` | 216 | **Not** in `pyproject` testpaths; collides with root `tests.conftest` if combined |
| **CI effective** | **~108** | Matches README badge “108” — **omits root suite** |

### Targeted runs this audit

| Suite | Result |
|-------|--------|
| Reliability unit | **22 passed** |
| HITL gates wired | **14 passed** |
| Multi-platform integration | **14 passed** |
| Auth middleware + facade + hitl_wiring | **182 passed, 1 failed** |
| Swarm handoff / reliability / circuit / MCP HITL / config race / SSRF-CORS sample | **151 passed** |
| Docs sync | **All passed** |

---

## 🔴 Critical / High — Bugs & Gaps

### P0-1. Auth prefix list misses new privileged APIs

**Verified:** `is_sensitive_path(...)` returns **False** for:

| Path | Risk |
|------|------|
| `/api/chaos/*` | Start fault injections (DoS / break LLM path when hooks land) |
| `/api/config/migrate/*` | Run/rollback schema migrations, export config |
| `/api/git/*` | Repo status / ops |
| `/api/github/*` | External integration |
| `/api/bookmarks/*` | Data mutation |
| `/api/pipelines/*` | Pipeline sandbox |
| `/api/workspaces/*` | Multi-workspace management |

`SENSITIVE_PREFIXES` in `kazma-ui/kazma_ui/auth.py` was never updated for Sprint 19 routes.

When `KAZMA_SECRET` is set, these routes are **open** (middleware only gates listed prefixes). Combined with cookie auto-issue on public pages, this is worse than “open when no secret.”

**Fix:** Add prefixes: `/api/chaos`, `/api/config`, `/api/git`, `/api/github`, `/api/bookmarks`, `/api/pipelines`, `/api/workspaces` (and any other non-read API). Prefer default-deny: sensitive = all `/api/*` except explicit allowlist.

---

### P0-2. Chaos framework has no production kill-switch

- No `KAZMA_CHAOS_ENABLED` / env guard in `chaos/__init__.py` or routes  
- Endpoints always mounted when import succeeds  
- **Also:** `chaos_injection` decorator is **not wired** into LLM/engine/gateway hot paths (only self-references) → feature is partly a **paper tiger**, but API still allows registering injections

**Risk today:** API abuse + future footgun when someone wires `@chaos_injection` into prod paths without a gate.

**Fix:** Require explicit env enable + auth; refuse mount in production; default off.

---

### P0-3. CI abandoned the main test corpus (~3,544 tests)

| Config | What runs |
|--------|-----------|
| `pyproject.toml` `testpaths` | package tests only (no `tests/`) |
| `.github/workflows/ci.yml` | package trees only |
| Root `tests/` | **Orphaned** from CI despite being the bulk of coverage |

**Impact:** HITL wiring, swarm patterns, SSRF/CORS, config races, MCP HITL, etc. can regress without CI red.

**Fix:** Add CI job `pytest tests/ -q` (or move/split into packages). Update badge honestly: “108 package + 3544 root”.

---

### H-1. Web HITL ownership still bypassable

`routes_direct.py` blocks only when **both** `owner` and `body.session_id` are present and differ.

If client **omits** `session_id`, approve proceeds for any `thread_id` (still needs shared secret / cookie).

**Fix:** If owner context exists → require `session_id` and match; else 403. Prefer binding thread ownership server-side at interrupt time.

---

### H-2. Engine `_task_lock` still incomplete

`_finalize_task` still mutates `_task_history` **without** `async with self._task_lock` (only `reject_checkpoint` uses lock). Concurrent cancel/retry/finalize races remain.

---

### H-3. Migration API error detail leak

`run_migrations` raises `HTTPException(detail=str(e))` — can expose paths/SQL internals to clients.

---

### H-4. Shared-secret model unchanged

Cookie auto-set on open routes; single secret = full admin. Acceptable for localhost; insufficient multi-tenant.

---

### H-5. Disclosure HMAC fallback still guessable

`disclosure.py` still uses `kazma-{hostname}-{uid}-{user}` when `KAZMA_DISCLOSURE_KEY` unset.

---

## 🟠 Medium — Bugs & Gaps

### M-1. Stale / failing root test after Slack HITL copy change

`tests/test_hitl_wiring.py::TestApprovalPrompt::test_prompt_contains_tool_and_args`  
expects `'/hitl'` in prompt; production text is `hitl approve` (no slash — intentional Slack fix).  
**1 failed / 183** in that bundle. Fix test, not product, unless slash form should also appear.

### M-2. ~358 lines dead WebSocket chat code

`chat.py` early-returns 410 at line 128; large unreachable body remains (maintenance trap, confusion risk if someone deletes the return).

### M-3. Health checks still use private attrs

`health.py`: `engine._workers`, `registry._providers` — facade exists but health bypasses it.

### M-4. TUI `tests/conftest.py` still collides with root

`ImportPathMismatchError` when collecting root + `kazma-tui/tests` together. Rename to `kazma_tui_tests` like other packages.

### M-5. Docs / version inconsistency

| Source | Claim |
|--------|-------|
| README badge | tests **108**, version **0.3.0** |
| README link badge | tests **3495** |
| STATUS.md | tests **138**, version **0.2.0**, engine 1573 |
| pyproject | version **0.2.0** |
| Reality | 3544 root + ~108 package + 216 TUI |

### M-6. Chaos not integrated into runtime paths

No `get_injector` / `should_inject` in llm_provider, engine, gateway — only API + decorator utility. Loadtests/docs imply more than code delivers.

### M-7. `FULL_REMEDIATION_PLAN.md` / STATUS stale

Still reference old residual P0-1/P0-2 language and incomplete Sprint 19 milestones.

### M-8. Large modules remain

| File | LOC |
|------|----:|
| `engine.py` | 1512 |
| `telegram.py` | 1124 |
| `routes_direct.py` | 794 (+ chaos/migrate bulk) |
| `chaos/__init__.py` | 489 (single-file framework) |

### M-9. MCP IDE docstring stale

Module header still says “when KAZMA_SECRET is set” — code now **requires** secret for danger tools even when unset (fail-closed).

### M-10. Root suite not in default `uv run pytest`

Developers following pyproject `testpaths` only see ~108 tests and may believe coverage is complete.

---

## 🟡 Lower priority / tech debt

| ID | Item |
|----|------|
| L-1 | ~649 broad `except Exception` (prod packages); silent-pass residue |
| L-2 | Duplicate `get_kazma_secret` (UI auto-gen vs config_store env-only) |
| L-3 | Windows sandbox weaker for code_exec (Job Object exists; still not POSIX rlimits) |
| L-4 | FastAPI `on_event` deprecation warnings (lifespan migration) |
| L-5 | Pydantic `.copy()` deprecation in UI unit tests |
| L-6 | Loadtests in CI may be flaky / env-dependent (verify separately) |
| L-7 | `services.py` still has private fallbacks for `_task_handles` |
| L-8 | Kanban docs (`docs/AUDIT_KANBAN.md`) historically wrong — archive or rewrite |

---

## Solid areas (do not regress)

- Graph HITL + bus fail-closed + pipeline checkpoints  
- MCP IDE danger mapping + secret fail-closed for danger tools  
- Tool registry `safety.check()` + workspace fail-closed file tools  
- Shell allowlist without interpreters/network tools  
- ConfigStore WAL/singleton/batch_set  
- SSE `_graph_holder`  
- Platform isolation (`_PLATFORM_KEYS`)  
- Swarm output adapter extraction (`swarm_output.py`)  
- Package test rename (core/gateway/ui)  
- Reliability unit tests aligned with real API  

---

## Recommended fix order (1–2 days)

### Day 1 — Security

1. Expand `SENSITIVE_PREFIXES` (or default-deny `/api/*`) for chaos/migrate/git/github/bookmarks/pipelines/workspaces.  
2. Gate chaos: `KAZMA_CHAOS_ENABLED=true` required; else 404.  
3. Hard ownership: require session when owner exists.  
4. Sanitize migration error responses.

### Day 1–2 — CI / integrity

5. CI job: `pytest tests/ -q --tb=line` (main corpus).  
6. Rename `kazma-tui/tests` → `kazma_tui_tests`.  
7. Fix `test_prompt_contains_tool_and_args` for slash-less prompt.  
8. Align README/STATUS/pyproject version + test counts.

### Follow-on debt

9. Lock `_task_history` in `_finalize_task`.  
10. Delete dead WS body or move to `archive/`.  
11. Wire health via public APIs.  
12. Wire chaos only behind decorator + env; document non-hooked status.  
13. Further split `engine.py` / `routes_direct.py` / `chaos/__init__.py`.

---

## Residual risk statement

| Deployment | Risk |
|------------|------|
| Localhost, single op, secret set, chaos unused | **Low–moderate** |
| LAN/public, secret set | **High** — unauth chaos/migrate/workspace APIs |
| LAN/public, secret unset / auth disabled | **Critical** |
| Trusting CI green alone | **High** — 3.5k tests not run in CI |

---

## Appendix — Auth probe (this audit)

```
/api/chaos/experiments          sensitive= False   ← BUG
/api/config/migrate/run         sensitive= False   ← BUG
/api/git/status                 sensitive= False   ← BUG
/api/github                     sensitive= False   ← BUG
/api/bookmarks                  sensitive= False   ← BUG
/api/pipelines                  sensitive= False   ← BUG
/api/workspaces                 sensitive= False   ← BUG
/api/approve/x                  sensitive= True    OK
```

---

*Fresh audit @ `ac33257`. Re-check P0-1/P0-3 after any auth or CI change.*
