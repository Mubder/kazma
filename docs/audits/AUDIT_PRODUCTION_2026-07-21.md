# Kazma Production-Readiness Audit — 2026-07-21

**Product version:** 0.6.1 (`pyproject.toml` / tag `v0.6.1`)  
**Scope:** Security · Architecture · Reliability · Tests/CI · Ops/Deploy · Docs truth  
**Method:** Multi-agent deep inspection + critical path live verification + regression sample  
**Corpus:** ~614 Python modules (excl. venv/docs node_modules), ~205 root `test_*.py` files, ~3.7k+ tests  
**Prior baseline:** `docs/audits/AUDIT_FULL_2026-07-18.md`  

**Honesty note:** A monorepo of this size cannot be literally “every line read once” in a single pass. This audit prioritizes **attack surface, failure modes, production wiring, and known dual-system traps**, with file:line evidence. Soft library code and generated docs trees are sampled, not line-certified.

---

## Executive verdict

| Deploy profile | Production ready? |
|----------------|-------------------|
| **Single-operator, `127.0.0.1`, strong secret, HITL on, YOLO unused** | **Yes — conditional** (best-in-class agent core) |
| **LAN / Docker `0.0.0.0` with defaults** | **No** (hardcoded fallback secret + LAN cookie trust) |
| **Public multi-user / multi-tenant SaaS** | **No** (shared secret admin, tenant spoof, SQLite scale) |
| **Horizontal K8s agent fleet** | **No** (SQLite single-writer) |

**Overall grade: B− for local production · D for public multi-tenant**

Kazma is a **serious multi-platform agent framework**, not a toy. The brain (LangGraph supervisor), mouths (gateway isolation), three HITL gates, swarm reliability, and checkpointer-first history are production-quality for a trusted operator. Gaps cluster on **network exposure**, **DR**, **shutdown cleanliness**, and **ops honesty**.

---

## Scorecard

| Dimension | Grade | Notes |
|-----------|:-----:|-------|
| Core architecture (brain / mouths / IDE) | **A−** | Clear monorepo; IDE + swarm + turn_input solid |
| HITL design (graph + bus + pipeline) | **A−** | MCP `force_danger` fixed; YOLO is a footgun |
| Platform isolation | **A** | Graph never holds `chat_id` / `user_id` |
| Auth model | **D+** | Shared secret; LAN auto-cookie; CLI hardcoded secret |
| Shell / exec residual risk | **C+** | Interpreters out; post-HITL RCE still real |
| Session / checkpointer continuity | **A−** | `turn_input` + `hitl_supersede`; dual DBs still confuse |
| Swarm reliability | **A−** | Handoff depth/visits; half-open probe; WAL TaskStore |
| Config layering | **B+** | ConfigStore + `kazma.local.yaml` (2026-07-21) |
| Shutdown / lifecycle | **C+** | Lifespan exists; `signal_shutdown` unused; checkpointer not closed |
| Test corpus | **A−** | ~3.7k tests; HITL/auth/swarm heavy; Playwright not in CI |
| CI / supply chain | **B−** | Root suite in CI; soft Codecov; no Dependabot |
| Backup / DR | **C** | Memory-only hot backup; no full `kazma-data/` DR |
| Docs truth | **B−** | docs-v2 mostly candid; Prometheus/versioning drift |
| Multi-tenant product | **C** | Columns + tests; not SaaS |

---

## What is strong (protect these)

1. **Platform isolation** — LangGraph state does not hold `chat_id` / `user_id`; `SessionStore` restores targets (`agent_handler/store.py`).
2. **Three HITL mechanisms** — Graph `interrupt()`, swarm bus fail-closed on NullBus, pipeline checkpoints; `_hitl_approved` only via ContextVar (not LLM args).
3. **MCP danger path** — `force_danger=True` when tier is danger/unknown (`mcp/manager.py` ~771–780); Jul-18 C1 largely fixed for write/exec names.
4. **Checkpointer-as-SoT** — `build_turn_messages` + `cancel_pending_hitl` on gateway and SSE; post-permission amnesia largely addressed.
5. **Shell** — `shlex.split` + `create_subprocess_exec`; interpreters removed from allowlist.
6. **SSRF module** — multi-A/AAAA checks + redirect re-validation on `read_url`.
7. **JWT tenant extraction** — requires `KAZMA_JWT_SECRET` + HS256 (unverified decode disabled).
8. **IDE mutations** — through `LocalToolRegistry` / HITL (no parallel ungated path).
9. **Swarm** — handoff max depth 5, visits 2; circuit breaker single half-open probe; TaskStore WAL + `json_each`.
10. **ConfigStore** — process singleton, WAL, `batch_set` / `transaction`; Settings write through SQLite not yaml.
11. **Provider/model coupling** — `set_active_model` / `get_client` auto-correct mismatches under lock.
12. **Large regression suite** — 276/276 passed in sample (`test_hitl_wiring`, `test_auth_middleware`, `test_ssrf_cors`).
13. **Honest dead-code inventory** — `docs/audits/UNWIRED_INVENTORY.md`.
14. **Config separation** — `kazma.local.yaml` + ConfigStore so updates need not fight user settings (`config_loader.py`).

---

## CRITICAL findings

### C1 — Default bind `0.0.0.0` + hardcoded fallback secret

| | |
|--|--|
| **Evidence** | `kazma-cli/kazma_cli/main.py` ~115–117: if host is `0.0.0.0` and `KAZMA_SECRET` unset → `KAZMA_SECRET=kazma-local-dev-secret` |
| **Exploit** | Anyone who can reach the port authenticates with a **known** secret, then IDE / approve / settings / chat |
| **Fix** | Default host `127.0.0.1`. Never invent fixed secrets. Fail startup on non-loopback bind without strong secret |
| **Prior** | **Worse** than Jul-18 C2 narrative |

### C2 — LAN cookie auto-issue (`KAZMA_TRUST_LAN` defaults ON)

| | |
|--|--|
| **Evidence** | `auth.py` ~83–106, 447–481: loopback **and** private LAN clients get `Set-Cookie: kazma-secret=<full secret>` |
| **Exploit** | Host on same LAN/VPN/Docker bridge opens UI → admin cookie without `/login` |
| **Fix** | Default `KAZMA_TRUST_LAN=0` outside dev; never mint raw shared secret as permanent cookie; use opaque sessions |
| **Prior** | Jul-18 claimed loopback-only; **weakened** by LAN trust |

### C3 — YOLO disables all HITL for a thread

| | |
|--|--|
| **Evidence** | Chat `/yolo` → `yolo.{thread_id}` → `requires_approval` false + bus auto-approve (`safety/hitl.py`, `swarm/safety.py`) |
| **Exploit** | After C1/C2 or any auth: one message then unlimited shell/file danger tools |
| **Fix** | Disable YOLO in production profile; require elevated re-auth; audit-log every YOLO enable |

---

## HIGH findings

| ID | Finding | Evidence / fix |
|----|---------|----------------|
| **H1** | `/api/voice/*` not in `SENSITIVE_PREFIXES` | Unauthenticated STT/TTS quota burn. Add prefix or default-deny `/api/*` |
| **H2** | Telegram webhook accepts updates without secret if `_webhook_secret` empty | Forge inbound → agent runs. Require secret when webhook mounted |
| **H3** | Web HITL approve: ownership skipped if `session_id` omitted | `routes_direct.py` — fail-closed require identity |
| **H4** | `python_exec` / `code_exec` local sandbox not a jail | Docker preferred; local fallback host FS. Force Docker or disable in prod |
| **H5** | Shell allowlist still powerful post-HITL (`env`, `git`, `cp`, `tar`, `kazma`) | Arg policies; drop `env`; restrict git subcommands |
| **H6** | MCP “safe” name classifier: `get_*` / `list_*` skips HITL | `get_credentials` class patterns. Expand danger patterns |
| **H7** | Secrets plaintext in `settings.db` unless vault key set | Require `KAZMA_VAULT_KEY` in prod profile |
| **H8** | `config_save` tool can write connector/LLM secrets after one approval | Block all `is_sensitive_config_key` via tools |
| **H9** | Dashboard session delete imports missing `session_store` module | Dead cleanup path; platform sessions leak |
| **H10** | `signal_shutdown()` never called from app lifespan | Graceful drain flag is dead code |

---

## MEDIUM findings

| ID | Finding |
|----|---------|
| M1 | Tenant `X-Tenant-ID` spoofable — not SaaS tenancy |
| M2 | Cookie value = long-lived admin secret (no rotation/session table) |
| M3 | Empty secret → open mode; ConfigStore failure can open the app |
| M4 | Workspace tools allow system temp dirs for reads |
| M5 | SSRF residual (DNS rebinding TOCTOU); provider discovery allows private URLs |
| M6 | `config_read` leaks last 4 chars of secrets |
| M7 | SessionManager no process-wide lock (SSE race risk) |
| M8 | Checkpointer not closed on shutdown |
| M9 | Dual danger lists (YAML graph vs swarm extended) can drift |
| M10 | Catch-all API errors may leak raw exception text |
| M11 | Docker compose vector volume mounts `/root/.kazma/...` while user is `kazma` |
| M12 | Port matrix 9090 / 8000 / 8090 drift |
| M13 | Hub `install`/`update` still stubs |
| M14 | Docs: Prometheus claims contradict code; VERSIONING partially fixed |
| M15 | No Dependabot; Codecov `fail_ci_if_error: false`; no coverage floor |

---

## LOW / product incompleteness

| Item | Status |
|------|--------|
| `/undo`, `/edit` | Still weak / incomplete vs marketing |
| Soft-nav SPA | Disabled |
| Playwright E2E | Optional, not CI |
| `delegation/` package | Tested, unwired in production |
| TUI full agent chat | Not full SSE parity |
| SECURITY.md supported versions | Stale (says 0.5.x) |

---

## Prior audit (2026-07-18) → today

| Jul-18 claim | 2026-07-21 truth |
|--------------|------------------|
| C1 MCP HITL fail-open | **Mostly fixed** (`force_danger`); sensitive “safe” names remain |
| C2 cookie auto-issue | **Partial** — loopback OK; **LAN default re-opens risk** |
| H1 shell interpreters | **Fixed** (removed); residual powerful bins remain |
| H5 JWT unverified | **Fixed** |
| H6 cross-thread HITL | **Gateway better**; web `session_id` omit residual |
| code_exec | Docker better; local fallback still weak |
| IDE tests orphaned | **Largely fixed** (in CI testpaths) |

---

## Test / CI snapshot (this audit)

```
pytest tests/test_hitl_wiring.py tests/test_auth_middleware.py tests/test_ssrf_cors.py
→ 276 passed
```

CI strengths: multi-package + root suite, Bandit high-severity hard fail, Docker build on main.  
CI gaps: soft Codecov, no Dependabot, no hard pip-audit on main, Playwright out of CI.

---

## Production go / no-go matrix

| Profile | Decision |
|---------|----------|
| Localhost single operator, HITL on, secret set, YOLO off | **GO** |
| Docker single-node, reverse proxy TLS, `KAZMA_TRUST_LAN=0`, strong secret, vault key, vector path fixed | **CONDITIONAL GO** |
| Public multi-user SaaS | **NO-GO** |
| Multi-replica SQLite agent | **NO-GO** |

---

## Minimum hardening before any non-loopback bind

1. Remove hardcoded `kazma-local-dev-secret`; default bind `127.0.0.1`.  
2. `KAZMA_TRUST_LAN=0` in Docker/prod.  
3. Default-deny `/api/*` (include `/api/voice`).  
4. Disable YOLO in production config.  
5. Require Telegram webhook secret if webhook mounted.  
6. Force Docker for code_exec or disable local.  
7. Expand `config_save` blocklist; require vault.  
8. Wire session delete across all three stores; call `signal_shutdown()` + close checkpointer.  
9. Volume backup procedure for entire `kazma-data/`.  
10. Align docs (Prometheus, SECURITY supported versions).

---

## Remediation roadmap (priority)

| P | Work | Est. impact |
|---|------|-------------|
| **P0** | C1 secret + bind defaults | Closes LAN/WAN trivial admin |
| **P0** | C2 LAN trust default off | Stops cookie minting on private peers |
| **P0** | C3 YOLO production disable | Stops unattended RCE after auth |
| **P1** | H1–H3 auth gaps (voice, webhook, approve ownership) | API surface |
| **P1** | H9–H10 shutdown + session delete | Ops reliability |
| **P1** | Full `kazma-data` backup story | DR |
| **P2** | H4–H8 sandbox / shell / MCP / vault / config_save | Depth defense |
| **P2** | SessionManager lock; sanitize catch-all errors | Stability |
| **P3** | Docs/CI supply chain; port matrix; hub stubs honesty | Trust & maintainability |

---

## Methodology appendix

| Stream | Focus | Technique |
|--------|-------|-----------|
| Security | Auth, HITL, shell, secrets, SSRF, isolation | Code + prior audit delta |
| Architecture | Dual stores, swarm, config, concurrency | Critical path read |
| Ops | CI, Docker, metrics, backup, docs | Workflow + deploy artifacts |
| Live sample | HITL/auth/SSRF tests | 276 passed |

**Trusted operator model assumed for “GO”:** physical/network trust of host + human watching HITL.  
**Not assumed:** hostile multi-tenant internet, zero-trust LAN, or compliance (SOC2) without further work.

---

*Audit authored 2026-07-21. Re-run after P0/P1 remediations before any public exposure claim.*

---

## Remediation status (2026-07-21 follow-up)

| ID | Status | Notes |
|----|--------|-------|
| C1 secret hardcode | **Fixed** | Still binds `0.0.0.0` for webhooks; generates random secret, rejects known bad default |
| C2 LAN trust default | **Fixed** | `KAZMA_TRUST_LAN` defaults **off** (set `1` for WSL lab) |
| C3 YOLO | **Hardened** | Kept `/yolo`; TTL (4h default), audit log, status command |
| H1 voice auth | **Fixed** | `/api/voice` in SENSITIVE_PREFIXES |
| H2 webhook secret | **Fixed** | Always validate; ephemeral secret if unset |
| H3 approve ownership | **Fixed** | `session_id` required for gateway-owned threads |
| H4 code_exec | **Hardened** | `KAZMA_PRODUCTION=1` forces Docker, no local fallback |
| H5 shell `env` | **Fixed** | Removed `env` / `hermes` from allowlists |
| H6 MCP secrets | **Fixed** | Sensitive-read keywords force danger |
| H8 config_save | **Fixed** | Blocks `is_sensitive_config_key`; no value echo |
| H9 session delete | **Fixed** | Uses `_session_store` + SessionManager |
| H10 shutdown | **Fixed** | `signal_shutdown()` + checkpointer close |
| UX YOLO web | **Fixed** | Slash menu + welcome chip + `/help`; backend already handled `/yolo` |
| UX HITL flood | **Fixed** | Combined batch interrupt; **Allow tool (session)** grants (~30m); YOLO button on card |
| UX silent after approve | **Fixed** | Delta post-resume assistant text; thread_id on resume; no stale "Let me check…" replay |
