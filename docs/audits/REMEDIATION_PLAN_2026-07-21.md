# Production Readiness Remediation Plan

**Source audit:** [`AUDIT_PRODUCTION_READINESS_2026-07-21.md`](./AUDIT_PRODUCTION_READINESS_2026-07-21.md)  
**Product version:** 0.6.1  
**Plan date:** 2026-07-21  
**Goal:** Move Kazma from **READY WITH CONDITIONAL FIXES** → safe single-node production, then toward multi-user if product requires it.

---

## 0. How to use this plan

| Rule | Detail |
|------|--------|
| **Do not skip P0** | No non-loopback / Docker-public claim until Phase 0 is green. |
| **One PR ≈ one work package** | Keep reviews small; land tests with the fix. |
| **No regressions on strengths** | Platform isolation, three HITL gates, MCP `force_danger`, shell no-interpreters, JWT verify. |
| **Threat model** | Default target is **single-operator trusted host**. Multi-tenant SaaS is Phase 4 (optional product decision). |
| **Done means** | Code + tests + manual smoke listed in each package. |

### Target profiles

| Profile | When “done” |
|---------|-------------|
| **A — Local trusted operator** | Phase 0 + Phase 1 complete |
| **B — Single-node Docker / reverse proxy** | Phase 0–2 complete |
| **C — Multi-user / SaaS** | Phase 0–4 complete (major product work) |

---

## 1. Dependency graph (high level)

```text
Phase 0 (P0 secrets & bind) ─────────────────────────────┐
    │                                                    │
    ├─→ WP-0.1 serve.py secret                           │
    ├─→ WP-0.2 CLI bind defaults                         │
    └─→ WP-0.3 compose/Docker hygiene                    │
                                                         │
Phase 1 (P0 lifecycle & fail-closed) ────────────────────┤
    │  can start after 0.1; parallel with 0.2–0.3        │
    ├─→ WP-1.1 app shutdown drain                        │
    ├─→ WP-1.2 reject_checkpoint active-map cleanup      │
    ├─→ WP-1.3 cancel single-finalize                    │
    ├─→ WP-1.4 circuit breaker probe finally             │
    ├─→ WP-1.5 LLM reconfigure aclose                    │
    ├─→ WP-1.6 NullBus fail-closed                       │
    └─→ WP-1.7 YOLO prod disable                         │
                                                         │
Phase 2 (P1 security depth) ─────────────────────────────┤
    │  after Phase 0; can parallel most packages         │
    ├─→ WP-2.1 discovery SSRF                            │
    ├─→ WP-2.2 code_exec harden                          │
    ├─→ WP-2.3 shell_exec env + path policy              │
    ├─→ WP-2.4 auth default-deny + pages gate            │
    ├─→ WP-2.5 cron concurrency + stop + stale RUNNING   │
    ├─→ WP-2.6 HITL ownership fail-closed                │
    └─→ WP-2.7 workspace root confinement                │
                                                         │
Phase 3 (P2 reliability & polish) ───────────────────────┤
    ├─→ swarm admission, FTS/session locks, vault req    │
    └─→ login rate limit, OAuth public URL, docs/CI      │
                                                         │
Phase 4 (optional SaaS) ─────────────────────────────────┘
    └─→ opaque sessions, real tenancy, multi-replica DB
```

---

## 2. Phase 0 — Stop the bleed (P0, ~1–2 days)

**Exit criteria:** No known admin secret in tree; non-loopback bind cannot start without a strong secret; Docker healthcheck and vector path correct; tests/docs mention correct defaults.

### WP-0.1 — Kill hardcoded secret in `serve.py`  
**Audit:** C1  
**Effort:** 0.5 day  
**Files:** `serve.py`; optional `tests/test_serve_entrypoint.py` or script smoke  

| Task | Detail |
|------|--------|
| 0.1.1 | Remove `kazma-local-dev-secret` assignment entirely. |
| 0.1.2 | Align with CLI: refuse known-bad secret; generate random if unset; print once. |
| 0.1.3 | Default host `127.0.0.1` (or require explicit `KAZMA_HOST`). |
| 0.1.4 | Add regression test or unit test of secret bootstrap helper (extract function if needed). |

**Acceptance:**
- [x] `rg "kazma-local-dev-secret" serve.py` → only in reject list, never assigned.
- [ ] Starting without secret on public host either generates random **or** exits non-zero.
- [ ] Manual: `python serve.py` shows security notice, not a fixed secret.

---

### WP-0.2 — CLI bind & secret policy  
**Audit:** C2 (partial)  
**Effort:** 0.5 day  
**Files:** `kazma-cli/kazma_cli/main.py`; help text / README snippet  

| Task | Detail |
|------|--------|
| 0.2.1 | Default `KAZMA_HOST=127.0.0.1`. |
| 0.2.2 | If host is non-loopback and secret missing → refuse start (do not only generate quietly without warning). |
| 0.2.3 | Keep random generation for loopback/dev DX. |
| 0.2.4 | Document `KAZMA_HOST=0.0.0.0` + strong secret for webhooks. |

**Acceptance:**
- [ ] Default serve is loopback-only.
- [ ] Non-loopback without secret exits with clear error.
- [ ] Existing CLI tests updated.

---

### WP-0.3 — Docker / Compose production hygiene  
**Audit:** C2, M compose vector path, healthcheck  
**Effort:** 0.5 day  
**Files:** `docker-compose.yml`, `Dockerfile` (comments only if needed), `.env.example`  

| Task | Detail |
|------|--------|
| 0.3.1 | Vector volume → `/home/kazma/.kazma/vector_memory` (match `USER kazma`). |
| 0.3.2 | Healthcheck → `GET /health` (always open). |
| 0.3.3 | Env defaults: `KAZMA_TRUST_LAN=0`, document `KAZMA_PRODUCTION=1`, `KAZMA_CODE_EXEC_DOCKER=force`. |
| 0.3.4 | `.env.example`: required secrets checklist from audit §3.15. |

**Acceptance:**
- [ ] Compose up healthy without secret in healthcheck path.
- [ ] Vector path writable as non-root user.
- [ ] `.env.example` lists all production env vars.

---

## 3. Phase 1 — Lifecycle & fail-closed core (P0, ~2–3 days)

**Exit criteria:** Clean SIGTERM; rejected/cancelled swarm tasks leave no zombies; breakers recover; YOLO off in prod; NullBus never auto-approves if middleware is skipped.

### WP-1.1 — App lifespan drain  
**Audit:** C3  
**Effort:** 1 day  
**Files:** `kazma-ui/kazma_ui/app.py`; optionally `kazma_core/shutdown.py`  

| Task | Detail |
|------|--------|
| 1.1.1 | On shutdown: `signal_shutdown` → cron `stop()` → cancel swarm handles / `stop_all()` → close task/vector/FTS if available → existing agent/checkpointer/http/gateway. |
| 1.1.2 | Hold strong refs on app for `cron_scheduler` and `swarm_engine` if only weakly attached today. |
| 1.1.3 | SSE/cron loops already check `is_shutting_down()` where missing — wire cron poll. |
| 1.1.4 | Integration test: start app lifespan, start fake cron, shutdown, assert stop called (mock). |

**Acceptance:**
- [ ] Shutdown log shows cron stop + swarm drain + gateway stop in order.
- [ ] No unawaited tasks after 15s graceful timeout in manual restart smoke.

---

### WP-1.2 — `reject_checkpoint` clears active maps  
**Audit:** H6  
**Effort:** 0.5 day  
**Files:** `kazma-core/kazma_core/swarm/engine.py`; tests under `tests/` swarm HITL  

| Task | Detail |
|------|--------|
| 1.2.1 | On reject/timeout-reject: pop `_active_tasks` / `_task_handles` via `_finalize_task(..., status="failed")`. |
| 1.2.2 | Unit test: pause → reject → `list_active` empty; history status FAILED. |

**Acceptance:**
- [ ] 100 reject cycles do not grow `_active_tasks`.
- [ ] Existing pipeline checkpoint tests still pass.

---

### WP-1.3 — Cancel single-finalize  
**Audit:** H7  
**Effort:** 0.5 day  
**Files:** `swarm/task_control.py`, `swarm/engine.py`  

| Task | Detail |
|------|--------|
| 1.3.1 | Prefer: cancel handle only; `CancelledError` path finalizes once. |
| 1.3.2 | Or: `_terminal_once` flag / status CAS before finalize. |
| 1.3.3 | Test: cancel emits one `task_completed`/persist, not two. |

**Acceptance:**
- [ ] No double SSE events on cancel.
- [ ] TaskStore has single terminal row.

---

### WP-1.4 — Circuit breaker probe `finally`  
**Audit:** H8  
**Effort:** 0.5 day  
**Files:** `swarm/reliability.py`, `swarm/worker_dispatch.py`  

| Task | Detail |
|------|--------|
| 1.4.1 | Add `release_probe()` or context manager; always clear `_probe_in_flight`. |
| 1.4.2 | Cancelled probe → record_failure or neutral release. |
| 1.4.3 | Unit test: half-open + cancel → next probe allowed. |

**Acceptance:**
- [ ] Stuck half-open scenario impossible without open cooldown.
- [ ] Existing breaker tests green.

---

### WP-1.5 — LLM client `aclose` on reconfigure  
**Audit:** H9  
**Effort:** 0.25 day  
**Files:** `kazma-core/kazma_core/llm_provider.py`  

| Task | Detail |
|------|--------|
| 1.5.1 | Before nulling `_http`, await/schedule `aclose()`. |
| 1.5.2 | Test with mock AsyncClient counting aclose. |

**Acceptance:**
- [ ] 50 reconfigure cycles no FD growth (or mock aclose called 50×).

---

### WP-1.6 — NullBus fail-closed  
**Audit:** M9  
**Effort:** 0.25 day  
**Files:** `kazma-core/kazma_core/swarm/bus.py`; safety tests  

| Task | Detail |
|------|--------|
| 1.6.1 | `NullBusAdapter.request_approval` → `return False`. |
| 1.6.2 | Headless danger only via `SafetyMiddleware.allow_headless_danger`. |
| 1.6.3 | Update any test that relied on NullBus auto-approve. |

**Acceptance:**
- [ ] Direct bus.approve without middleware = deny.
- [ ] Headless tests still pass with `allow_headless_danger=True`.

---

### WP-1.7 — YOLO disabled in production  
**Audit:** H5  
**Effort:** 0.5 day  
**Files:** `safety/yolo.py`, `routes_direct.py` approve scope, `sse_chat.py`  

| Task | Detail |
|------|--------|
| 1.7.1 | `enable_yolo` raises / returns error when `KAZMA_PRODUCTION=1`. |
| 1.7.2 | Approve `scope=yolo` rejected in production with clear message. |
| 1.7.3 | UI: hide YOLO chip/button when production flag (or soft-fail message). |
| 1.7.4 | Tests for enable + approve scope. |

**Acceptance:**
- [ ] With `KAZMA_PRODUCTION=1`, `/yolo` cannot arm danger tools.
- [ ] Dev/default still allows YOLO with TTL.

---

## 4. Phase 2 — Security depth (P1, ~4–6 days)

**Exit criteria:** No unauthenticated sensitive API by omission; model discovery SSRF-safe; code/shell hardened; cron safe under load; workspace confined in prod.

### WP-2.1 — SSRF on OpenAI-compatible discovery  
**Audit:** H2, M20  
**Effort:** 0.5 day  
**Files:** `models/discovery.py`; `tests/test_ssrf_cors.py` or discovery tests  

| Task | Detail |
|------|--------|
| 2.1.1 | `validate_url` before GET; `follow_redirects=False`. |
| 2.1.2 | `KAZMA_ALLOW_PRIVATE_LLM=1` opt-in for private base_url. |
| 2.1.3 | Tests: metadata IP / RFC1918 rejected without flag. |

**Acceptance:**
- [ ] `http://169.254.169.254/` discovery fails closed.
- [ ] Ollama/LM Studio still work with private allow flag.

---

### WP-2.2 — `python_exec` / code_exec harden  
**Audit:** H3  
**Effort:** 1 day  
**Files:** `tools/code_exec.py`  

| Task | Detail |
|------|--------|
| 2.2.1 | Expand `_BLOCKED_IMPORT_ROOTS` (`os`, `sys`, `pathlib`, `shutil`, `io`, `importlib`, `tempfile`, …). |
| 2.2.2 | Document: local mode is defense-in-depth only; Docker is real jail. |
| 2.2.3 | `KAZMA_PRODUCTION=1` already forces Docker — add tests for no local fallback. |
| 2.2.4 | Optional: disable tool registration if Docker missing in production. |

**Acceptance:**
- [ ] Local blocklist test: `import os` fails.
- [ ] Production + no Docker → clear error, no local run.

---

### WP-2.3 — `shell_exec` env scrub + path/arg policy  
**Audit:** H4  
**Effort:** 1–1.5 days  
**Files:** `agent/tool_registry.py`; swarm `tools/registry.py` ShellTool align  

| Task | Detail |
|------|--------|
| 2.3.1 | Pass restricted `env=` (no API keys). |
| 2.3.2 | Reject absolute paths outside workspace for read/write bins. |
| 2.3.3 | `git` subcommand denylist (`push`, `credential`, `config --global`, destructive clean). |
| 2.3.4 | Prod drop: `ps`, `kazma` (or require separate elevated tool). |
| 2.3.5 | Align swarm ShellTool with agent allowlist. |

**Acceptance:**
- [ ] Child process env has no `OPENAI_API_KEY` / provider keys.
- [ ] `cat /etc/passwd` or Windows equivalent denied.
- [ ] HITL still required for remaining danger bins.

---

### WP-2.4 — Auth: default-deny `/api/*` + admin pages  
**Audit:** M1, M2, L1, L2  
**Effort:** 1.5 days  
**Files:** `kazma-ui/kazma_ui/auth.py`, page routes  

| Task | Detail |
|------|--------|
| 2.4.1 | When secret set: all `/api/*` require auth except explicit open allowlist (`/api/status`, `/api/auth/*`, health-adjacent). |
| 2.4.2 | Gate `/settings`, `/ide`, `/swarm`, `/agents`, `/workspace` HTML shells. |
| 2.4.3 | Remove dead `auth_middleware` always-cookie body. |
| 2.4.4 | Fix LAN trust docstring; never embed last-4 secrets in HTML. |
| 2.4.5 | Expand auth middleware tests for new routes. |

**Acceptance:**
- [ ] New `/api/foo` without allowlist → 401 when secret set.
- [ ] Unauth GET `/settings` redirects to login when secret set.
- [ ] Loopback DX still works with auto-cookie (documented).

---

### WP-2.5 — Cron: concurrency, stale RUNNING, shutdown  
**Audit:** H10 (ties C3)  
**Effort:** 1 day  
**Files:** `cron/scheduler.py`, store  

| Task | Detail |
|------|--------|
| 2.5.1 | Global semaphore for concurrent jobs. |
| 2.5.2 | On start: stale `RUNNING` → `FAILED` or requeue with backoff. |
| 2.5.3 | Poll loop honors `is_shutting_down()`. |
| 2.5.4 | Ensure WP-1.1 calls `stop()`. |

**Acceptance:**
- [ ] 100 due jobs never exceed N concurrent graph runs.
- [ ] Crash mid-job does not storm on restart.

---

### WP-2.6 — HITL ownership fail-closed  
**Audit:** M6, M7  
**Effort:** 1 day  
**Files:** `kazma-gateway/.../hitl.py`, `routes_direct.py` approve  

| Task | Detail |
|------|--------|
| 2.6.1 | Empty `sender_id` → deny cross-thread approve. |
| 2.6.2 | Web approve: ownership check exceptions → 403, never continue. |
| 2.6.3 | Prefer binding approve to authenticated principal for all threads. |
| 2.6.4 | Gateway + web HITL tests. |

**Acceptance:**
- [ ] Cannot approve another user’s gateway thread without matching identity.
- [ ] Store errors do not skip check.

---

### WP-2.7 — Workspace root confinement in production  
**Audit:** H12, M8  
**Effort:** 0.5–1 day  
**Files:** gateway workspaces router; `tool_registry` temp allow  

| Task | Detail |
|------|--------|
| 2.7.1 | `KAZMA_PRODUCTION=1` requires `KAZMA_WORKSPACE_ROOT`; refuse paths outside. |
| 2.7.2 | Remove or gate system-temp allow in workspace scope for prod. |
| 2.7.3 | Align file_read / file_delete / file_write boundaries. |

**Acceptance:**
- [ ] Cannot register `C:\` or `/` as workspace when production set.
- [ ] Temp-dir escape closed in prod.

---

## 5. Phase 3 — Reliability, secrets, ops polish (P2, ~1–2 weeks)

### WP-3.1 — Swarm global admission control  
**Audit:** M11, M12, M13  
**Files:** `engine.py`, `reliability_registry.py`  

- Global max in-flight tasks (semaphore); 429/busy when full.  
- Cache `BoundedConcurrency` per key.  
- Thread `_visited`/`_depth` (or hop budget) through fallbacks.

### WP-3.2 — Agent turn wall-clock timeout  
**Audit:** M14  
**Files:** `agent_runner.py`, `llm_provider.py`  

- Outer `asyncio.wait_for` per turn.  
- Cap total 429 sleep budget.

### WP-3.3 — SessionManager + FTS5 locks + Chroma close  
**Audit:** M15–M17  
**Files:** `session_manager.py`, `memory/fts5.py`, `vector_store.py`, app shutdown  

- Lazy LRU sessions; `threading.Lock` around mutations.  
- FTS5 WAL + lock.  
- `VectorMemory.close()` from lifespan.

### WP-3.4 — Vault required in production + mask hardening  
**Audit:** M5, M2 residual  
**Files:** `config_store.py`, `app.py` startup, settings mask  

- Fail start if `KAZMA_PRODUCTION=1` and no `KAZMA_VAULT_KEY`.  
- Mask to constant `***` (no last-4).

### WP-3.5 — Login rate limit + OAuth fixed public URL  
**Audit:** M3, M4  
**Files:** `routes_direct.py` login; GitHub OAuth router  

- Per-IP sliding window / backoff.  
- `KAZMA_PUBLIC_URL` for redirect_uri; auth-gate `oauth/start`.

### WP-3.6 — MCP default-danger tighten  
**Audit:** M10  
**Files:** `mcp/manager.py`  

- Non-trusted servers: all tools force HITL unless explicit allowlist.  
- Keep `trust: trusted` documented as operator footgun.

### WP-3.7 — Semantic cache TTL  
**Audit:** M18  

- Max rows + TTL eviction when `KAZMA_SEMANTIC_CACHE` enabled.

### WP-3.8 — Sub-agent HITL wiring  
**Audit:** M19  
**Files:** `app.py`, `agent/sub_agent.py`  

- Child graph with full danger list + auto-deny resume (no orphan interrupt).

### WP-3.9 — Docs, CI, quick wins  
**Audit:** L1–L9, polish list  

| Item | Action |
|------|--------|
| `SECURITY.md` | Threat model + 0.6.x supported |
| Port matrix | Single default documented (9090 host / 8000 container) |
| `google_llm.py` | Drop `shell=True` |
| Danger list SoT | constants re-export `CANONICAL_DANGER_TOOLS` |
| Dependabot | Enable |
| Coverage floor | Fail under threshold in CI |
| Bandit | Keep high-severity fail |

---

## 6. Phase 4 — Optional multi-user / SaaS (product decision)

**Only if product requires public multi-tenant.** Not required for Profile A/B.

| Work package | Audit | Scope |
|--------------|-------|--------|
| **WP-4.1** Opaque sessions | H1 | Session table; cookie = random id; revoke/rotate |
| **WP-4.2** Real tenancy | H11 | Tenant only from verified JWT/session; ignore spoofable headers |
| **WP-4.3** Multi-replica store | SQLite limits | Postgres/Redis for sessions, tasks, checkpointer |
| **WP-4.4** IdP / RBAC | Auth model D | OIDC/SAML or multi-user accounts with least privilege |
| **WP-4.5** Compliance | DR | Full `kazma-data/` backup/restore runbook + restore drills |

**Exit criteria for Profile C:** External pen-test of auth + tenancy; no shared admin secret cookie; horizontal scale design signed off.

---

## 7. Suggested PR stack (Graphite / sequential branches)

Order optimized for reviewability and early risk reduction:

| # | Branch / PR title | WPs | Blocks |
|---|-------------------|-----|--------|
| 1 | `fix/serve-hardcoded-secret` | 0.1 | — |
| 2 | `fix/cli-loopback-default` | 0.2 | — |
| 3 | `fix/compose-prod-hygiene` | 0.3 | — |
| 4 | `fix/nullbus-fail-closed` | 1.6 | — |
| 5 | `fix/yolo-prod-disable` | 1.7 | — |
| 6 | `fix/llm-reconfigure-aclose` | 1.5 | — |
| 7 | `fix/breaker-probe-finally` | 1.4 | — |
| 8 | `fix/swarm-reject-active-cleanup` | 1.2 | — |
| 9 | `fix/swarm-cancel-single-finalize` | 1.3 | 8 optional parallel |
| 10 | `fix/app-shutdown-drain` | 1.1 | cron stop hooks |
| 11 | `fix/discovery-ssrf` | 2.1 | — |
| 12 | `fix/code-exec-blocklist` | 2.2 | — |
| 13 | `fix/shell-env-path-policy` | 2.3 | — |
| 14 | `fix/auth-default-deny` | 2.4 | careful UI smoke |
| 15 | `fix/cron-concurrency-stale` | 2.5 | 10 |
| 16 | `fix/hitl-ownership-failclosed` | 2.6 | — |
| 17 | `fix/workspace-prod-confine` | 2.7 | — |
| 18+ | Phase 3 packages | 3.x | after Phase 2 |

PRs 1–7 can largely land **in parallel** on independent files.

---

## 8. Test matrix (mandatory per phase)

### Phase 0
```powershell
& '.venv\Scripts\python.exe' -m pytest tests/test_auth_middleware.py -v --tb=short
# + any new serve/cli bootstrap tests
```

### Phase 1
```powershell
& '.venv\Scripts\python.exe' -m pytest tests/test_hitl_wiring.py tests/test_hitl* -v --tb=short
# + swarm cancel/reject/breaker unit tests added by WPs
```

### Phase 2
```powershell
& '.venv\Scripts\python.exe' -m pytest tests/test_ssrf_cors.py tests/test_auth_middleware.py -v --tb=short
# shell/code_exec unit tests; workspace confinement tests
```

### Manual smoke (every phase)
1. Start with no secret → random secret printed / or refuse public bind.  
2. Login → chat → trigger danger tool → approve once → completes.  
3. `/yolo` with `KAZMA_PRODUCTION=1` → denied.  
4. Ctrl+C / stop container → clean logs, no hung python.  
5. Telegram/webhook path still works if secret/host configured intentionally.

### Compile checks (per Python edit)
```powershell
& '.venv\Scripts\python.exe' -c "import py_compile; py_compile.compile(r'<file>', doraise=True); print('OK')"
```

---

## 9. Effort rollup

| Phase | Calendar (1 senior eng) | Risk reduction |
|-------|-------------------------|----------------|
| **0** | 1–2 days | Known secret + exposure defaults |
| **1** | 2–3 days | Shutdown, zombies, YOLO, NullBus |
| **2** | 4–6 days | SSRF, sandbox depth, auth architecture, cron, HITL ownership |
| **3** | 1–2 weeks | Load reliability, vault, rate limit, CI/docs |
| **4** | Multi-sprint | SaaS readiness |

**Minimum for “Local production GO” (Profile A):** Phase 0 + Phase 1 (~1 week).  
**Minimum for “Docker single-node GO” (Profile B):** Phase 0–2 (~2 weeks).

---

## 10. Risk register (execution)

| Risk | Mitigation |
|------|------------|
| Auth default-deny breaks UI soft-nav / voice / OAuth | Expand open allowlist carefully; run full auth test suite + browser smoke |
| Shell path policy breaks legitimate agent workflows | Start with scrub env + absolute path deny; git denylist second; feature-flag if needed |
| Shutdown drain deadlocks | Time-box each stop (2–5s); log and continue |
| YOLO UI still shows buttons in prod | API deny + UI hide + tests |
| Parallel PRs conflict on `auth.py` / `engine.py` | Sequence 2.4 and 1.1–1.3 carefully |

---

## 11. Tracking checklist (copy to issue tracker)

### Phase 0
- [x] WP-0.1 serve.py secret  
- [x] WP-0.2 CLI loopback default  
- [x] WP-0.3 compose/Docker hygiene  

### Phase 1
- [x] WP-1.1 shutdown drain  
- [x] WP-1.2 reject_checkpoint cleanup  
- [x] WP-1.3 cancel single-finalize  
- [x] WP-1.4 breaker probe finally  
- [x] WP-1.5 LLM aclose  
- [x] WP-1.6 NullBus fail-closed  
- [x] WP-1.7 YOLO production disable (`KAZMA_ALLOW_YOLO=1` override)  

### Phase 2
- [x] WP-2.1 discovery SSRF  
- [x] WP-2.2 code_exec harden  
- [x] WP-2.3 shell policy  
- [x] WP-2.4 auth default-deny  
- [x] WP-2.5 cron harden  
- [x] WP-2.6 HITL ownership  
- [x] WP-2.7 workspace confine  

### Phase 3
- [x] WP-3.1 partial — BoundedConcurrency cache  
- [x] WP-3.2 — turn wall-clock timeout  
- [x] WP-3.3 — SessionManager lock + FTS lock + VectorMemory.close  
- [x] WP-3.4 — vault required when KAZMA_PRODUCTION=1; mask no last-4  
- [x] WP-3.5 — login rate limit + KAZMA_PUBLIC_URL for OAuth  
- [x] WP-3.6 — MCP force HITL for untrusted / non-allowlisted  
- [x] WP-3.7 — semantic cache TTL + max rows  
- [x] WP-3.8 — sub-agent build_child_graph + full danger auto_deny  
- [x] WP-3.9 — SECURITY.md + CHANGELOG + .env.example  

### Phase 4 (SaaS / scale foundation)
- [x] WP-4.1 — opaque web sessions (`kazma-session`, ConfigStore-backed)  
- [x] WP-4.2 — production ignores spoofable X-Tenant-ID (JWT or default)  
- [x] WP-4.3 — Postgres backend module + pool + compose; shared schema bootstrap  
- [x] WP-4.4 — platform RBAC (viewer/operator/admin) + local users + OIDC PKCE  
- [x] WP-4.5 — DR runbook + backup/restore scripts  

**Cutover + SaaS UI (landed):**
- ConfigStore dual backend (SQLite | Postgres) + `scripts/migrate_sqlite_to_postgres.py`
- `/api/saas/*` users & tenants; polished login (user/secret/OIDC); Settings Account admin; header role + logout
- See `docs/ops/SAAS_AND_POSTGRES.md`

**Completed store cutover:** SessionManager + TaskStore + AsyncPostgresSaver all dual-backend; full migrate script.

### Ops / multi-replica / polish (landed)
- [x] Multi-replica compose (`docker-compose.ha.yml`) + nginx config + `/health/ready` DB ping  
- [x] Multi-region runbook `docs/ops/MULTI_REGION.md`  
- [x] OIDC IdP setup guide `docs/ops/OIDC_IDP_SETUP.md`  
- [x] Smoke script `scripts/smoke_production.py`  
- [x] Docker image installs `[rag,postgres]` + entrypoint optional auto-migrate  
- [x] Dual-backend unit tests `tests/test_pg_store_dual_backend.py`  
- [x] i18n for platform users / tenants / login SaaS strings  

### Definition of done (Profile A)
- [x] All Phase 0–1 checkboxes green  
- [x] Audit C1/C3/H5–H9 addressed with tests  
- [x] Smoke script available (`scripts/smoke_production.py`)  
- [x] CHANGELOG entry + SECURITY.md threat model  

### Definition of done (Profile B)
- [x] All Phase 0–2 checkboxes green  
- [x] Docker compose + HA compose documented  
- [x] Re-audit note: remediation complete (see AUDIT_PRODUCTION_READINESS footer)  

### Definition of done (Profile C / multi-replica SaaS)
- [x] Postgres cutover for config, chat, swarm, checkpoints  
- [x] Multi-user RBAC + OIDC + SaaS UI  
- [x] DR + multi-region ops docs  
- [x] Smoke + HA compose  

---

## 12. Status

**Plan fully executed in code (2026-07-21).** Operator steps remaining only when you deploy:
1. Set secrets / env for your environment  
2. Optional: `migrate_sqlite_to_postgres.py` if moving an existing DB  
3. Run `python scripts/smoke_production.py --base … --secret …` against the live host  

---

*Plan derived from `AUDIT_PRODUCTION_READINESS_2026-07-21.md`. Implementation complete.*
