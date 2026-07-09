# FULL REMEDIATION & FIXING PLAN

**Sources:**  
- Phases 0–5 remediation history  
- `AUDIT_REAUDIT_2026-07-08.md`  
- `AUDIT_FRESH_2026-07-09.md` @ `ac33257`  
- Code-quality findings (resource leak, dual registries, large files, coverage gaps) — 2026-07-09  

**Status:** Phases 0–5 + Sprint A/B partial complete | **Active fixing plan = Sprints S0–S5 below**  
**Last Updated:** 2026-07-09  

---

## Executive summary (current)

| Area | Status |
|------|--------|
| HITL 3 mechanisms / MCP classify / fail-closed bus / workspace scope | ✅ Verified PASS |
| MCP IDE danger map + secret fail-closed | ✅ Fixed (Sprint A) |
| Web approve hard 403 on mismatch | ⚠️ Partial (omit `session_id` still bypasses) |
| Package conftest rename / FallbackChain tests / multi-platform integration | ✅ Fixed |
| Auth prefixes for chaos/migrate/workspace APIs | ❌ Open (P0) |
| Root `tests/` (~3,544) in CI | ❌ Open (P0) |
| GeminiProvider `close()` explicit lifecycle | ❌ Open (leak risk) |
| Dual tool registries / shell naming | ❌ Open (architecture) |
| Large-module split (>500 LOC) | 📋 Planned |
| Module-level unit coverage gaps | 📋 Planned |

**Residual risk:** Low–moderate on localhost single-op; **high** if LAN/public without auth-prefix fix.

---

## Already done (do not re-do)

| Item | Notes |
|------|--------|
| Phases 0–5 | ConfigStore, HITL wiring, WS 410, constants/exceptions/schema/migrations |
| MCP IDE `MCP_TOOL_TO_SAFETY` + secret required for danger | `mcp_server.py` |
| Web approve 403 on ownership **mismatch** | Still need require-session when owner exists |
| `kazma_*_tests` package rename | Core/gateway/ui |
| FallbackChain unit tests aligned | 22 passed |
| Multi-platform integration | 14 passed |
| Dead code: tools/registry methods-outside-class; engine empty SSE task_id | Previously fixed |
| Docs sync script green | 10/10 |

---

# ACTIVE FIXING PLAN

Priority order: **S0 Security → S1 Correctness/leaks → S2 CI/tests → S3 Architecture → S4 Coverage → S5 Size/debt**

---

## Sprint S0 — Security (½–1 day) — **DO FIRST**

| ID | Issue | File(s) | Fix | Effort |
|----|-------|---------|-----|--------|
| **S0-1** | Auth misses privileged APIs | `kazma-ui/kazma_ui/auth.py` | Add to `SENSITIVE_PREFIXES`: `/api/chaos`, `/api/config`, `/api/git`, `/api/github`, `/api/bookmarks`, `/api/pipelines`, `/api/workspaces`. Prefer default-deny all `/api/*` except allowlist (`/api/status`, `/api/telemetry` read). | S |
| **S0-2** | Chaos always mounted, no kill-switch | `chaos/__init__.py`, `routes_direct.py` | Require `KAZMA_CHAOS_ENABLED=true` (default **off**); 404 otherwise; never mount in prod configs | S |
| **S0-3** | Web HITL ownership bypass if `session_id` omitted | `routes_direct.py` | If owner context exists → **require** matching `session_id` or 403 | S |
| **S0-4** | Disclosure HMAC guessable fallback | `security/disclosure.py` | `secrets.token_hex(32)` + persist via ConfigStore when `KAZMA_DISCLOSURE_KEY` unset | S |
| **S0-5** | Migration error detail leak | `routes_direct.py` migrate handlers | Return generic 500; log full exception server-side | S |
| **S0-6** | DISC / secret unification residual | `config_store.get_kazma_secret` vs `auth.get_kazma_secret` | Single implementation; document auto-gen policy | S |

**Security already PASS (no change required):** HITL 3-tier, MCP classify, NullBus fail-closed, workspace fail-closed.

---

## Sprint S1 — Correctness & resource hygiene (½ day)

| ID | Issue | File(s) | Fix | Effort |
|----|-------|---------|-----|--------|
| **S1-1** | **Potential resource leak — GeminiProvider** | `google_llm.py`, `llm_provider.py` | Parent `close()` *does* close `self._http` (same attr Gemini uses). Still: **override `async def close()`** on `GeminiProvider` that calls `await super().close()` (and document ADC lifecycle). Audit all call sites so streaming/long-lived Gemini clients always `await close()` or use context manager. Add unit test: client closed after `close()`. | S |
| **S1-2** | **Duplicate `import yaml`** | `config_store.py` | Multiple function-local `import yaml` (~316, 320, 393, 603, 631, 662). Not a runtime bug; consolidate to **module-level** `import yaml` once (or one `_yaml()` helper) for clarity and faster import path. | XS |
| **S1-3** | Engine `_task_lock` residual | `swarm/engine.py` | Acquire lock around **all** `_task_history` mutations including `_finalize_task` | S |
| **S1-4** | HITL prompt test drift | `tests/test_hitl_wiring.py` | Assert `hitl approve` (slash-less Slack-safe form), not only `/hitl` | XS |
| **S1-5** | MCP IDE docstring stale | `mcp_server.py` header | Document fail-closed secret requirement for danger tools | XS |
| **S1-6** | Dead WS body after 410 | `kazma-ui/kazma_ui/chat.py` | Delete ~358 unreachable lines **or** move to `archive/` | S |

---

## Sprint S2 — CI / test integrity (½–1 day)

| ID | Issue | Fix | Effort |
|----|-------|-----|--------|
| **S2-1** | Root suite (~3,544) not in CI | Add CI job: `pytest tests/ -q --tb=line`; keep package jobs | M |
| **S2-2** | `pyproject` `testpaths` omit root | Add `"tests"` **or** document two suites; badge must match | S |
| **S2-3** | TUI conftest collides with root | Rename `kazma-tui/tests` → `kazma_tui_tests` (same pattern as core/gateway/ui) | S |
| **S2-4** | Health private attrs break facade contract | `health.py`: use `list_workers()` / public registry APIs; no `engine._workers` | S |
| **S2-5** | Badge / STATUS / version drift | Align README, STATUS, `pyproject` version + honest test counts (package vs root) | S |

---

## Sprint S3 — Architecture gaps (1–2 days)

### S3-1. Dual tool registries (high confusion)

| Registry | Path | Role today |
|----------|------|------------|
| Agent / graph tools | `agent/tool_registry.py` | LangGraph ReAct builtins incl. **`shell_exec`** |
| Swarm / worker tools | `tools/registry.py` | Permissioned `BaseTool` set; shell as **`ShellTool` registered as `"shell"`** |

| ID | Issue | Fix |
|----|-------|-----|
| **S3-1a** | Overlapping purposes | Document canonical: **agent path = `LocalToolRegistry`**, **swarm worker RBAC = `tools.registry.ToolRegistry`**. Longer term: facade `UnifiedToolSurface` that aliases both. |
| **S3-1b** | **`shell_exec` vs `shell` naming** | Safety `_EXTENDED_DANGER` lists `shell_exec` (via DEFAULT + docs). Swarm registry exposes `"shell"`, not `shell_exec`. **Register alias** `shell_exec` → same `ShellTool`, or rename ShellTool to `shell_exec` and keep `shell` as alias. Ensure danger gate keys match registration names. |
| **S3-1c** | Swarm registry missing tools agent has | Either register `code_exec`/`python_exec` with danger flags, or explicitly document swarm workers must not get them | 

### S3-2. Streaming client lifecycle

| ID | Issue | Fix |
|----|-------|-----|
| **S3-2** | `streaming.stream_chat` — no explicit `close()` | **No change required** if `async with` owns the client (confirmed pattern). Add one-line docstring: “caller must not hold client past stream.” Optional: accept injected client for tests. |

### S3-3. Large files — split plan (>500 LOC)

Do **not** big-bang rewrite. Extract leaf modules; keep public APIs stable.

| File | ~LOC | Split plan | Priority |
|------|-----:|------------|----------|
| `swarm/engine.py` | ~1.5–1.7k | History/finalize → `task_lifecycle.py`; SSE emit already thin; cancel/retry helpers | P1 |
| `settings_manager.py` | ~1.1k | Import/export YAML; category routers; validation | P2 |
| `model_registry.py` | ~1.0k | Discovery I/O; provider persistence; client cache | P2 |
| `agent/tool_registry.py` | ~0.9k | Builtins → `agent/builtin_tools.py`; execute/retry stays | P1 |
| `swarm/reliability.py` | ~0.9k | Already partial extract to `reliability_registry.py`; move Timeout/Retry/Fallback to submodules if still growing | P3 |
| `adapters/telegram.py` | ~1.1k | Poll / send / callbacks (separate sprint) | P2 |
| `routes_direct.py` | ~0.8k | Chaos routes + migrate routes → `routes_chaos.py`, `routes_migrate.py` | P1 (after S0) |

### S3-4. Facade / health

| ID | Fix |
|----|-----|
| **S3-4** | Finish removing private attr access from UI (`health`, `services` fallbacks for `_task_handles` once public register/get APIs are universal) |

---

## Sprint S4 — Test coverage gaps (2–4 days, incremental)

Modules called out with **little/no dedicated tests**. Prefer thin unit tests over full E2E.

| Module | Min viable tests | Priority |
|--------|------------------|----------|
| `agent_runner.py` | Graph build + HITL config pass-through; streaming graph cache | P1 |
| `compaction.py` | Compaction threshold / no-throw on empty | P2 |
| `token_counter.py` | Count bounds for sample strings | P3 |
| `dialect_detector.py` | MSA vs Gulf smoke | P3 |
| `tracing.py` | No-op when OTEL unset; setup when endpoint set (mock) | P2 |
| `streaming.py` | Token events with mock HTTP | P1 |
| `permissions.py` | Allow/deny matrix | P2 |
| `providers.py` | Config load smoke | P3 |
| `mcp_client.py` | Auth header injection; connect fail soft | P1 |
| `settings_manager.py` | get/set/batch round-trip | P1 |
| `security/certification.py` | Badge verify true/false | P2 |
| `security/audit_trail.py` | Append + list | P2 |
| `kazma_ui/metrics.py` | Serialize workers without private crash | P2 |
| `kazma_ui/dashboard.py` | Route returns 200 (TestClient) | P3 |
| `kazma_ui/chat.py` | WS closes 410 | P1 |
| `kazma_gateway/gateway.py` | Start/stop queue smoke with mocks | P1 |

**Also keep green:** existing HITL, MCP HITL, config race, SSRF/CORS, swarm reliability samples in root `tests/`.

---

## Sprint S5 — Polish / debt (ongoing)

| ID | Item |
|----|------|
| **S5-1** | Reduce broad `except Exception` / silent pass |
| **S5-2** | FastAPI lifespan instead of deprecated `on_event` |
| **S5-3** | Wire chaos hooks only under env + decorator (if product wants real chaos) |
| **S5-4** | Archive stale audit kanbans; point to this plan + `AUDIT_FRESH_2026-07-09.md` |
| **S5-5** | Windows code_exec Job Object coverage tests |

---

## Suggested execution order (DAG)

```
S0-1 auth prefixes ─┬─► S0-2 chaos gate ─► S3 routes split (chaos/migrate)
S0-3 ownership ─────┤
S0-4 disclosure ────┤
S0-5 migrate errors ┘
         │
         ▼
S1-1 Gemini close ──► S1-2 yaml imports ──► S1-3 task_lock ──► S1-6 delete dead WS
         │
         ▼
S2-1 CI root suite ──► S2-3 TUI tests rename ──► S2-4 health public API ──► S2-5 badges
         │
         ▼
S3-1 shell_exec alias + registry doc ──► S3-3 extract builtins / engine lifecycle
         │
         ▼
S4 coverage waves (agent_runner, streaming, gateway, settings, mcp_client first)
```

---

## Verification checklist (after each sprint)

```powershell
# Docs / architecture invariants
uv run python scripts/check_docs_sync.py

# Package suites
uv run --extra dev pytest kazma-core/kazma_core_tests kazma-gateway/kazma_gateway_tests kazma-ui/kazma_ui_tests -q

# Root corpus (must be CI after S2-1)
uv run --extra dev pytest tests/test_hitl_wiring.py tests/test_auth_middleware.py tests/test_mcp_hitl.py -q

# Compile touch points
uv run python -c "import py_compile; py_compile.compile(r'kazma-core/kazma_core/google_llm.py', doraise=True); print('OK')"
```

Auth probe after S0-1:

```python
from kazma_ui.auth import is_sensitive_path
assert is_sensitive_path("/api/chaos/experiments")
assert is_sensitive_path("/api/config/migrate/run")
assert is_sensitive_path("/api/workspaces")
```

---

## Tracking board

| Sprint | Focus | Status |
|--------|-------|--------|
| Phases 0–5 | Foundation remediation | ✅ Complete |
| Sprint A/B (old) | MCP/HITL/conftest/reliability | ✅ Mostly complete |
| **S0** | Auth prefixes, chaos, ownership, disclosure | ✅ **Done 2026-07-09** |
| **S1** | Gemini close, yaml, task_lock, dead WS, HITL test | ✅ **Done 2026-07-09** |
| **S2** | CI root suite, TUI rename, badges/version | ✅ **Done 2026-07-09** |
| **S3** | Dual registries, routes, task_lifecycle, sse_bridge | ✅ Partial (history + SSE extracted; dispatch still large) |
| **S4** | Coverage gaps | ✅ Wave-1–3 (+ providers, sse_bridge tests) |
| **S5** | Debt / polish | 🟡 model_registry_store + telegram_stt/keyboards + settings splits; engine dispatch remains |

---

## Notes on reported items (verification)

| Finding | Verdict |
|---------|---------|
| GeminiProvider missing `close()` | **Valid as hardening:** inherits parent `close()` on same `_http`, but no override/tests; long-lived ADC clients may never be closed by callers → **S1-1** |
| Duplicate yaml imports in config_store | **Valid quality:** many local `import yaml` → **S1-2** |
| Large files >500 LOC | **Valid** → **S3-3** |
| Dead code previously fixed | **Ack** — keep regression tests only |
| shell_exec in danger list but swarm registry uses `"shell"` | **Valid naming gap** → **S3-1b** |
| Two tool registries | **Valid** → **S3-1a** |
| streaming no close | **OK as designed** with `async with` → document only **S3-2** |
| Coverage gaps list | **Valid backlog** → **S4** |
| Security verification PASS items | **Ack** — preserve in CI |

---

**Generated:** 2026-07-09  
**Base HEAD for fresh findings:** `ac33257`  
**Next action:** Execute **Sprint S0** (auth prefixes + chaos gate + ownership).
