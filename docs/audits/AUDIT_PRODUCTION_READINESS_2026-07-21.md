# Production Readiness & Security Audit — Kazma

**Product version:** 0.6.1  
**Date:** 2026-07-21  
**Scope:** Full monorepo attack surface (core / UI / gateway / swarm / IDE / deploy)  
**Method:** Structure mapping + multi-agent deep dives + live source verification of critical claims  

---

## 1. Executive Summary

**Overall assessment score: `READY WITH CONDITIONAL FIXES`**

| Deploy profile | Decision |
|----------------|----------|
| Single-operator, `127.0.0.1`, strong secret, HITL on, YOLO off, Docker for code_exec | **Conditional GO** |
| Default CLI / `serve.py` / Docker LAN exposure with stock defaults | **NO-GO** until P0 |
| Public multi-user / multi-tenant SaaS | **NOT PRODUCTION READY** |
| Horizontal multi-replica agent fleet on SQLite | **NOT PRODUCTION READY** |

Kazma is a **serious** multi-platform agent framework (LangGraph supervisor, platform-isolated gateway, three HITL gates, swarm reliability, IDE as single mutation path). Architecture quality is high for a trusted local operator. Residual risk concentrates on:

1. **Network exposure defaults** — especially `serve.py`’s **known hardcoded admin secret** on `0.0.0.0`.
2. **Auth model** — shared secret as cookie; allowlist-based API gating; not multi-user IdP.
3. **Post-HITL host power** — shell/code after one approval (or YOLO) is near-host RCE by design.
4. **Process lifecycle** — incomplete shutdown drain; swarm/cron orphan/race paths under load.
5. **SSRF residual** on model discovery; optional vault → secrets at rest.

**What is strong (do not regress):** platform isolation (no `chat_id` in graph), graph `interrupt()` + bus fail-closed + pipeline checkpoints, MCP `force_danger=True`, shell via `shlex` + `create_subprocess_exec` (no interpreters), JWT tenant requires `KAZMA_JWT_SECRET`, IDE mutations only via `LocalToolRegistry`, handoff depth/visit caps, TaskStore/ConfigStore WAL + busy_timeout, large regression suite.

---

## 2. Prioritized Vulnerability & Defect Report

---

### CRITICAL

---

#### C1 — Hardcoded known admin secret on public bind (`serve.py`)

| | |
|--|--|
| **Severity** | `CRITICAL` |
| **Category** | Auth / Secrets / Network Exposure |
| **Location** | `serve.py` L15–23 |

**Root cause & impact:**  
When `KAZMA_HOST` defaults to `0.0.0.0` and `KAZMA_SECRET` is unset, `serve.py` sets `KAZMA_SECRET=kazma-local-dev-secret` (public in the repo). Anyone who can reach port 9090 is full admin (IDE write/exec, settings, HITL approve, chat). CLI `kazma serve` **rejects** this string and generates a random secret — this alternate entrypoint reopens Jul-18/Jul-21 C1.

**Remediation:**

```python
# serve.py — replace the secret block entirely
host = os.environ.get("KAZMA_HOST", "127.0.0.1")
_KNOWN_BAD = "kazma-local-dev-secret"
existing = (os.environ.get("KAZMA_SECRET") or "").strip()
if existing == _KNOWN_BAD:
    sys.exit("Refusing known default KAZMA_SECRET — set a strong random secret")
if not existing:
    import secrets
    generated = secrets.token_urlsafe(32)
    os.environ["KAZMA_SECRET"] = generated
    print(f"[SECURITY] Generated ephemeral KAZMA_SECRET: {generated}")
if host not in ("127.0.0.1", "::1", "localhost") and not os.environ.get("KAZMA_SECRET"):
    sys.exit("Non-loopback bind requires KAZMA_SECRET")
```

---

#### C2 — Default bind `0.0.0.0` (CLI + Docker + compose)

| | |
|--|--|
| **Severity** | `CRITICAL` (when combined with weak/mis-set auth) / `HIGH` alone with random secret |
| **Category** | Network Exposure / Operational Readiness |
| **Location** | `kazma-cli/kazma_cli/main.py` L117–119; `Dockerfile` L30; `docker-compose.yml` L19 |

**Root cause & impact:**  
Product defaults expose the full agent UI on all interfaces “for webhooks/WSL.” CLI generates a random secret (good) but still LAN-exposes shell/file/LLM surfaces. Compose healthcheck hits `/api/gateway/status` (auth-gated under secret) and may flap. Vector volume mounts `/root/.kazma/...` while process runs as `kazma` user → silent path mismatch.

**Remediation:**

```python
# kazma-cli: default loopback; opt-in public
host = _os_cli.environ.get("KAZMA_HOST", "127.0.0.1")
```

```yaml
# docker-compose.yml
environment:
  KAZMA_HOST: "0.0.0.0"
  KAZMA_TRUST_LAN: "0"
  KAZMA_PRODUCTION: "1"
  KAZMA_CODE_EXEC_DOCKER: "force"
volumes:
  - kazma_vectors:/home/kazma/.kazma/vector_memory   # match USER kazma
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
```

---

#### C3 — Incomplete graceful shutdown (cron / swarm / vector not drained)

| | |
|--|--|
| **Severity** | `CRITICAL` (ops / data integrity under restart) |
| **Category** | Resource Management / Error Handling |
| **Location** | `kazma-ui/kazma_ui/app.py` `_on_shutdown` ~L1064–1120 vs cron start ~L1045–1051 |

**Root cause & impact:**  
Shutdown calls `signal_shutdown()`, agent, checkpointer, model registry, HTTP pool, gateway — but **does not** stop `CronScheduler`, cancel swarm `_task_handles` / `stop_all()`, or close TaskStore / VectorMemory / FTS. In-flight swarm work and cron LangGraph jobs continue during uvicorn teardown; SQLite/Chroma can corrupt on hard kill.

**Remediation:**

```python
async def _on_shutdown(self) -> None:
    from kazma_core.shutdown import signal_shutdown
    signal_shutdown()
    # 1) Stop cron first (no new jobs)
    try:
        from kazma_core.cron.scheduler import get_cron_scheduler
        sched = get_cron_scheduler()
        if sched:
            await sched.stop()
    except Exception as e:
        logger.warning("[app] cron stop failed: %s", e)
    # 2) Drain swarm
    try:
        engine = getattr(self, "swarm_engine", None) or getattr(self, "_swarm", None)
        if engine is not None:
            for tid, handle in list(getattr(engine, "_task_handles", {}).items()):
                if handle and not handle.done():
                    handle.cancel()
            if hasattr(engine, "stop_all"):
                await engine.stop_all()
    except Exception as e:
        logger.warning("[app] swarm drain failed: %s", e)
    # 3) Existing agent / checkpointer / registry / http / gateway close...
```

---

### HIGH

---

#### H1 — Session cookie stores the raw shared admin secret

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Auth / Session Design |
| **Location** | `kazma-ui/kazma_ui/auth.py` `_should_auto_issue_cookie`, `create_auth_middleware` ~L450–509 |

**Root cause & impact:**  
`Set-Cookie: kazma-secret=<KAZMA_SECRET>`. Cookie theft = permanent admin until env secret rotation for all clients. No session table, no revocation, no per-user identity.

**Remediation:** Opaque server-side sessions (`kazma-session` random id → hashed row + expiry); never put `KAZMA_SECRET` in cookies. Keep secret only for machine-to-machine header auth.

---

#### H2 — SSRF via model discovery (`_discover_openai_compatible`)

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Injection / SSRF |
| **Location** | `kazma-core/kazma_core/models/discovery.py` `_discover_openai_compatible` ~L270–328 |

**Root cause & impact:**  
Custom/LM Studio paths call `validate_url`; the general OpenAI-compatible path does **not**. Authenticated (or open-mode) client can force server-side GET to cloud metadata / RFC1918 / loopback services; error bodies may leak response fragments.

**Remediation:**

```python
async def _discover_openai_compatible(base_url: str, api_key: str | None, provider: str) -> ProviderInfo:
    from kazma_core.security.ssrf import SSRFError, validate_url
    url = f"{base_url.rstrip('/')}/models"
    try:
        # Production: allow_private only behind explicit env opt-in
        allow_priv = os.environ.get("KAZMA_ALLOW_PRIVATE_LLM", "").lower() in ("1", "true")
        validate_url(url, block_unresolved=True, allow_private=allow_priv)
    except (SSRFError, ValueError) as exc:
        return ProviderInfo(name=provider, label=provider.title(), base_url=base_url, error=str(exc))
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
        resp = await client.get(url, headers=headers)
        # ...
```

---

#### H3 — Local `python_exec` is not a jail (host RCE after one HITL approve)

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Sandbox Escape / Execution |
| **Location** | `kazma-core/kazma_core/tools/code_exec.py` `_BLOCKED_IMPORT_ROOTS` ~L49–55; `python_exec` ~L358–413 |

**Root cause & impact:**  
Import blocklist omits `os`, `pathlib`, `shutil`, `io`. Local mode can `os.system(...)`, read host FS. Docker preferred but falls back to local unless `KAZMA_PRODUCTION` / force Docker. Default non-prod path remains host-level.

**Remediation:** Expand blocklist (`os`, `sys`, `pathlib`, `shutil`, `io`, `importlib`, `tempfile`); in production **always** force Docker with no fallback (already partially wired); disable `python_exec` if Docker missing.

---

#### H4 — `shell_exec` post-HITL remains a powerful host surface

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Command Abuse / Secret Exposure |
| **Location** | `kazma-core/kazma_core/agent/tool_registry.py` `shell_exec` ~L813–918 |

**Root cause & impact:**  
Interpreters removed (good). Allowlist still includes `git`, `cp`, `mv`, `tar`, `cat`, `find`, `ps`, `kazma`. Binary-only check; **full parent env** inherited (API keys); absolute paths outside workspace allowed for many tools.

**Remediation:**

```python
child_env = {
    "PATH": restricted_path,
    "LANG": "C.UTF-8",
    "HOME": str(cwd),
    "TMPDIR": str(cwd),
    "TEMP": str(cwd),
}
# Reject absolute paths outside cwd for cat/cp/mv/tar/find
# git subcommand denylist: push, credential, config --global, clean -fdx
# Drop ps, kazma from prod allowlist
proc = await asyncio.create_subprocess_exec(
    *args, stdout=..., stderr=..., cwd=cwd, env=child_env,
)
```

---

#### H5 — YOLO + tool grants remain full HITL bypasses (no production hard-disable)

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | HITL / AuthZ |
| **Location** | `kazma-core/kazma_core/safety/yolo.py`; `safety/hitl.py`; `routes_direct.py` approve `scope=yolo` |

**Root cause & impact:**  
TTL + audit exist, but YOLO is still enableable via `/yolo` or UI with no `KAZMA_PRODUCTION` refuse. After weak auth (C1/C2) or any session, unlimited danger tools for hours.

**Remediation:**

```python
def enable_yolo(thread_id: str, *, actor: str = "unknown") -> dict[str, Any]:
    if (os.environ.get("KAZMA_PRODUCTION") or "").lower() in ("1", "true", "on"):
        raise PermissionError("YOLO is disabled in production")
    # existing logic...
```

---

#### H6 — HITL reject leaves tasks in `_active_tasks` forever

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Memory Leak / Task Lifecycle |
| **Location** | `swarm/engine.py` `reject_checkpoint` ~L996–1032; `_finalize_task` keeps PAUSED in active maps ~L784–788 |

**Root cause & impact:**  
Reject updates history/store but never pops `_active_tasks` / `_task_handles`. Timeout auto-reject shares path. Unbounded growth under repeated pipeline HITL denials; `list_active_tasks()` lies.

**Remediation:**

```python
async def reject_checkpoint(self, task_id: str, reason: str = "...") -> TaskResult | None:
    result = await self._checkpoint_handler.reject(task_id, reason=reason)
    if result is not None:
        task = self._active_tasks.get(task_id)
        if task is not None:
            self._finalize_task(
                task, status="failed", worker_results=[],
                error=reason, duration_seconds=0.0,
            )
        # existing history update as fallback if not in active map
    return result
```

---

#### H7 — Cancel double-finalize race

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Concurrency / Race |
| **Location** | `swarm/task_control.py` `cancel_active_task` ~L17–49; `engine.py` dispatch `CancelledError` branch |

**Root cause & impact:**  
Cancel both `handle.cancel()` **and** immediate `finalize(...)`. Running dispatch also finalizes on `CancelledError` → double SSE, double SQLite persist, metric skew.

**Remediation:** Cancel handle only; let `CancelledError` path finalize once — **or** atomic terminal CAS flag on task status.

---

#### H8 — Circuit breaker half-open probe can stick permanently

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Concurrency / Resilience |
| **Location** | `swarm/reliability.py` `allow_probe` / `record_*` ~L280–345 |

**Root cause & impact:**  
`allow_probe()` sets `_probe_in_flight=True`; cancel/timeout paths that skip both `record_success` and `record_failure` leave the breaker half-open forever until `reset()`/restart.

**Remediation:**

```python
# worker_dispatch.py probe path
try:
    breaker.check_or_raise(name)
    result = await do_dispatch(...)
    breaker.record_success()
except Exception:
    breaker.record_failure()
    raise
finally:
    # belt-and-suspenders if record_* not reached
    if getattr(breaker, "_probe_in_flight", False) and breaker.state == CircuitState.HALF_OPEN:
        breaker._probe_in_flight = False  # or breaker.release_probe()
```

Prefer `async with breaker.probe(): ...` context manager.

---

#### H9 — `LLMProvider.reconfigure` drops AsyncClient without `aclose`

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Connection Leak |
| **Location** | `llm_provider.py` `reconfigure` ~L545–580 |

**Root cause & impact:**  
`self._http = None` with “GC’d” comment. httpx connections/FDs leak under frequent Settings provider switches.

**Remediation:**

```python
if changed and self._http is not None:
    old = self._http
    self._http = None
    try:
        # schedule if sync context:
        asyncio.get_running_loop().create_task(old.aclose())
    except RuntimeError:
        pass  # no loop — best-effort
```

---

#### H10 — Cron: unbounded fan-out + incomplete stop + stale RUNNING

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Background Workers / Resilience |
| **Location** | `kazma-core/kazma_core/cron/scheduler.py` |

**Root cause & impact:**  
Due jobs `create_task` with no global semaphore; no `is_shutting_down()` in poll loop; crash leaves DB rows `RUNNING` which re-fire; never stopped from app lifespan.

**Remediation:** Semaphore cap; on start reset stale RUNNING → FAILED/PENDING; honor shutdown; `stop()` from lifespan.

---

#### H11 — Spoofable `X-Tenant-ID` (not SaaS tenancy)

| | |
|--|--|
| **Severity** | `HIGH` for multi-tenant use / `MEDIUM` single-tenant |
| **Category** | AuthZ / Multi-tenant Isolation |
| **Location** | `auth.py` `create_tenant_middleware` ~L541+ |

**Root cause & impact:**  
Any client can set tenant header/cookie. Vault/memory may scope by that value without cryptographic binding to identity.

**Remediation:** Ignore client-supplied tenant unless it matches verified JWT claims; single-tenant default that hardcodes `default`.

---

#### H12 — Authenticated workspace can be any absolute path

| | |
|--|--|
| **Severity** | `HIGH` |
| **Category** | Path / API Integrity |
| **Location** | Gateway workspaces router `create_workspace` (confinement only if `KAZMA_WORKSPACE_ROOT` set) |

**Root cause & impact:**  
Traversal guard is relative to **chosen** root. Compromised secret can re-root IDE to `/` or user home.

**Remediation:** Require `KAZMA_WORKSPACE_ROOT` when `KAZMA_PRODUCTION=1`; refuse absolute paths outside it.

---

### MEDIUM

| ID | Severity | Category | Location | Root cause & impact | Remediation summary |
|----|----------|----------|----------|---------------------|---------------------|
| **M1** | MEDIUM | Auth architecture | `auth.py` `SENSITIVE_PREFIXES` | Allowlist gating — new routes ship open by omission (`/api/settings/voice` GET exempt). | Default-deny all `/api/*` except explicit open set. |
| **M2** | MEDIUM | Info disclosure | `/settings` not in sensitive prefixes | Remote unauth loads settings shell; last-4 key fragments possible. | Gate admin HTML pages; mask secrets as constant `***`. |
| **M3** | MEDIUM | Auth brute force | `/api/auth/login` | No rate limit/lockout. | Per-IP throttle + backoff + audit. |
| **M4** | MEDIUM | OAuth / Host injection | GitHub OAuth redirect from `Host` | Host spoof / concurrent state race. | `KAZMA_PUBLIC_URL`; auth-gate `oauth/start`. |
| **M5** | MEDIUM | Secrets at rest | `config_store.py` vault optional | Without vault key, API keys plaintext in `settings.db`. | Require `KAZMA_VAULT_KEY` in prod. |
| **M6** | MEDIUM | HITL AuthZ | Gateway `hitl.py` empty `sender_id` | Ownership skipped when `sender_id` empty. | Fail-closed: require non-empty owner match. |
| **M7** | MEDIUM | HITL AuthZ | `routes_direct.py` approve | Ownership exceptions soft-continue; web threads loosely bound. | Fail-closed on check errors; bind principal. |
| **M8** | MEDIUM | Path isolation | `tool_registry._workspace_scope_error` | Temp dirs allowed inconsistently vs `file_write`. | Remove temp exception in prod. |
| **M9** | MEDIUM | HITL integrity | `swarm/bus.py` `NullBusAdapter.request_approval` returns `True` | Latent fail-open if middleware bypassed. | Return `False`; only middleware `allow_headless_danger` escapes. |
| **M10** | MEDIUM | MCP classification | `mcp/manager.py` `classify_mcp_tool` | `"safe"` name patterns skip HITL; `trust: trusted` full skip. | Default MCP danger; allowlist per server. |
| **M11** | MEDIUM | Unbounded work | `SwarmEngine.dispatch` | Per-fanout bounds only; no global admission control. | Global semaphore / 429 when full. |
| **M12** | MEDIUM | Concurrency design | `ReliabilityRegistry.get_bounded_concurrency` | New semaphore every call — shared limit is a no-op. | Cache per key. |
| **M13** | MEDIUM | Cycle guards | Fallback paths drop `_visited`/`_depth` | Longer chains via fallback+handoff. | Thread hop budget across fallbacks. |
| **M14** | MEDIUM | Timeouts | `agent_runner.run` | Iteration cap only; no wall-clock turn budget. | `asyncio.wait_for` per turn. |
| **M15** | MEDIUM | Session store | `session_manager.py` | Full-DB load; `check_same_thread=False` without lock. | Lazy LRU + lock. |
| **M16** | MEDIUM | SQLite concurrency | `memory/fts5.py` | Bare connection, no lock. | WAL + lock or writer queue. |
| **M17** | MEDIUM | Resource leak | VectorMemory/Chroma | No `close()` / shutdown hook. | Implement + call on shutdown. |
| **M18** | MEDIUM | Disk bloat | semantic cache | No TTL/eviction when enabled. | Max rows + TTL. |
| **M19** | MEDIUM | Sub-agent HITL | `app.py` sub-agent graph lambda | Ignores child `hitl_config`/tools. | Dedicated child graph with auto-deny. |
| **M20** | MEDIUM | SSRF residual | `ssrf.py` + discovery private allow | DNS TOCTOU; private LLM intentional SSRF into LAN. | Pin IP; opt-in private only. |

---

### LOW

| ID | Finding | Location | Fix |
|----|---------|----------|-----|
| **L1** | Dead `auth_middleware` always-set-cookie body (regression risk) | `auth.py` ~414–434 | Delete dead function |
| **L2** | Docstring says LAN trust “default on” while code defaults off | `auth.py` `_trust_lan_enabled` | Fix docstring |
| **L3** | Exception `str(exc)` unless `KAZMA_ENV=production` | `app.py` catch-all | Default redacted errors |
| **L4** | `google_llm.py` `shell=True` on fixed argv | `google_llm.py` ~378–384 | Drop `shell=True` |
| **L5** | Stale `constants.GRAPH_HITL_DANGER_TOOLS` vs `CANONICAL_DANGER_TOOLS` | `constants.py` | Single SoT re-export |
| **L6** | `IdeService.run_file` builds python/node/bash cmds that shell_exec rejects | `ide/service.py` | Route `.py` → `python_exec` |
| **L7** | Loopback auto-cookie any local process gets admin | `auth.py` | Opt-out `KAZMA_LOOPBACK_AUTO_COOKIE=0` |
| **L8** | Port matrix 9090/8000/8090 drift | CLI, Docker, loadtests | Single source of truth |
| **L9** | Dual memory/delegation unwired packages | monorepo | Archive or wire one path |

---

## 3. Quick-Win Production Polish

1. **Delete or neutralize `serve.py` hardcoded secret** today (5 min; C1).
2. **Delete dead `auth_middleware`** cookie always-set path in `auth.py` (regression bomb).
3. **Align healthcheck** to `/health` (always open).
4. **Fix compose vector volume** path to `/home/kazma/.kazma/vector_memory`.
5. **`NullBusAdapter.request_approval` → `False`** (one-line latent fail-open kill).
6. **Block YOLO when `KAZMA_PRODUCTION=1`** (one guard).
7. **`LLMProvider.reconfigure`**: `aclose` old client (connection hygiene).
8. **Circuit breaker probe `finally`** (capacity silent death).
9. **`reject_checkpoint` → `_finalize_task`** (active-map leak).
10. **SSRF guard on `_discover_openai_compatible`** (copy pattern from Ollama path).
11. **Shell child `env=` scrub** (stop leaking API keys to subprocesses).
12. **Rate-limit `/api/auth/login`** (slowloris of shared secret).
13. **Default-deny `/api/*`** instead of growing `SENSITIVE_PREFIXES`.
14. **Document threat model** in `SECURITY.md`: single-operator shared secret ≠ multi-tenant SaaS; update supported version (0.6.x).
15. **Production env checklist** (compose / `.env.example`):

    ```
    KAZMA_HOST=127.0.0.1          # or 0.0.0.0 only behind reverse proxy + strong secret
    KAZMA_SECRET=<64+ random>
    KAZMA_TRUST_LAN=0
    KAZMA_PRODUCTION=1
    KAZMA_CODE_EXEC_DOCKER=force
    KAZMA_VAULT_KEY=<fernet key>
    KAZMA_WORKSPACE_ROOT=/data/workspaces
    KAZMA_CORS_ORIGINS=https://your.domain
    KAZMA_YOLO_TTL_SECONDS=0     # or disable YOLO entirely
    ```

16. **CI:** keep Bandit high hard-fail; add Dependabot; coverage floor; ensure `kazma-core/tests` remains in paths.

---

## Architecture data-flow (condensed)

```
User (Web/Telegram/Discord/Slack/TUI)
  → Gateway adapters | FastAPI (auth middleware)
    → SessionStore / SessionManager  (platform IDs NEVER in LangGraph state)
    → AgentRunner / SSE stream
      → LangGraph supervisor (interrupt HITL for danger tools)
      → LocalToolRegistry + UnifiedToolExecutor (MCP force_danger)
      → SwarmEngine (dispatch / handoff / pipeline checkpoints)
        → TaskStore (SQLite WAL) + ReliabilityRegistry
      → IdeService → same tools + HITL
    → ConfigStore / optional Vault / Chroma VectorMemory
  → Response / bus adapter / SSE
```

**Primary async stack:** FastAPI + uvicorn, asyncio swarm tasks, aiogram/discord, LangGraph + aiosqlite checkpointer, httpx, ChromaDB (optional).

---

## Production go / no-go matrix (final)

| Profile | Status |
|---------|--------|
| Localhost, pinned secret, HITL on, YOLO off, Docker code_exec, vault on | **READY WITH CONDITIONAL FIXES** (apply C1 serve.py, H6–H10 lifecycle) |
| Docker single-node + reverse-proxy TLS + P0/P1 below | **Conditional after fixes** |
| Public multi-user SaaS | **NOT PRODUCTION READY** |
| Multi-replica SQLite | **NOT PRODUCTION READY** |

**Minimum before any non-loopback bind:**

1. Fix/remove `serve.py` known secret (C1).
2. Prefer loopback default; require strong secret for `0.0.0.0` (C2).
3. Drain cron+swarm on shutdown (C3).
4. SSRF on model discovery (H2).
5. Force Docker code_exec + shell env scrub (H3/H4).
6. Disable YOLO in production (H5).
7. Fix reject/cancel/breaker/LLM client leaks (H6–H9).
8. Opaque sessions or accept shared-secret threat model explicitly (H1).

---

## Related audits

| Document | Notes |
|----------|-------|
| `docs/audits/AUDIT_FULL_2026-07-18.md` | Prior full audit (MCP HITL fail-open, cookie auto-issue baseline) |
| `docs/audits/AUDIT_PRODUCTION_2026-07-21.md` | Same-day production audit + remediation status table |

**Delta note:** Several Jul-18 items (MCP `force_danger`, JWT verify, interpreter allowlist, voice auth, webhook secret, LAN trust default-off) are **fixed** in current code. The highest open risks in this pass are **`serve.py` reintroducing a known secret**, **incomplete process lifecycle**, and **post-HITL host power**.

---

*Audit authored 2026-07-21 against live source (v0.6.1). Re-audit after P0/P1 remediations before any public exposure claim.*

---

## Remediation complete (2026-07-21 follow-through)

All plan phases **0–4** and ops/polish follow-ups have been implemented in-repo. See `REMEDIATION_PLAN_2026-07-21.md` (status section).

| Area | Outcome |
|------|---------|
| C1 serve.py known secret | **Fixed** — refuse + generate random / loopback default |
| C2 bind / LAN trust | **Fixed** — loopback default; `KAZMA_TRUST_LAN=0` |
| C3 shutdown drain | **Fixed** — cron + swarm + agent + checkpointer |
| Auth / YOLO / NullBus / HITL / SSRF / shell / code_exec | **Fixed or hardened** |
| Opaque sessions + multi-user RBAC + OIDC | **Shipped** |
| Postgres cutover (config, chat, swarm, checkpoints) | **Shipped** |
| SaaS UI (login, users, tenants, header) | **Shipped** |
| DR + multi-region ops + smoke script + HA compose | **Shipped** |

**Operator-only remaining:** configure env for *your* deploy, optional migrate, run `scripts/smoke_production.py`.
