# Kazma Monorepo Cleanup Plan

**Date:** 2026-07-21  
**Scope:** Full workspace hygiene (dead code, redundancy, security residue, unwired packages)  
**Method:** Import/wire analysis + prior `UNWIRED_INVENTORY.md` + live path scan of agent/UI/gateway/CLI  
**Product version:** 0.6.1+ (post production-readiness remediation)

---

## Principles

1. **Do not delete library-only packages** without a product decision (see Unwired inventory).
2. Prefer **archive** (`archive/`) over silent deletion for experimental dual systems.
3. **Never** delete HITL, platform isolation, SSRF, vault, or ConfigStore singleton paths.
4. Generated trees (`docs/build/`, `docs/node_modules/`, `__pycache__/`, `.venv/`) are build artifacts — clean locally, not product debt.

---

## Cleanup matrix

| File Path | Issue Description | Category | Proposed Action | Impact Assessment |
| :--- | :--- | :--- | :--- | :--- |
| `kazma-core/kazma_core/delegation/*` | Full multi-agent package; **zero production importers** (only tests). Overlaps SwarmEngine. | Dead Code / Redundant | **CONSOLIDATE** later: document as library-only; product chooses wire into SwarmEngine **or** `archive/delegation` | High if dual-wired; Low if left as library with clear docs |
| `kazma-core/kazma_core/authorization_flow.py` | Enterprise division approvals; not on live chat/swarm path | Dead Code | **KEEP** as library; optional **REFACTOR** into optional plugin | Low — tested, unused at runtime |
| `kazma-core/kazma_core/division_sandbox.py` | Division-scoped sandbox; not wired to UnifiedToolExecutor main path | Dead Code | **KEEP** / do not dual-gate with HITL | Low |
| `kazma-core/kazma_core/permissions.py` + `kazma-permissions.yaml` | YAML tool allowlists not enforced on agent execute path | Dead Code | **REFACTOR** wire into `LocalToolRegistry.execute` **or** mark deprecated | Medium product value if multi-user tools |
| `kazma-core/kazma_core/tool_sandbox.py` | Alternate sandbox; live path is HITL + code_exec/shell_exec | Redundant | **KEEP** library; avoid parallel gates | Low |
| `kazma-core/kazma_core/majlis.py` | Cultural orchestrator shell; pacing/tone pieces live elsewhere | Dead Code / Partial | **CONSOLIDATE** document which cultural modules are live vs shell | Low–Medium |
| `kazma-core/kazma_core/security/certification.py` | Skill certification levels not called by hub install path | Dead Code | **KEEP** until hub wires cert | Low |
| `kazma-core/kazma_core/security/linter.py` | Skill security linter; hub does not invoke on install | Dead Code | **KEEP** | Low |
| `kazma-core/kazma_core/security/dependency_scanner.py` | CVE-style scanner; offline tooling | Dead Code | **KEEP** as ops CLI candidate | Low |
| `kazma-core/kazma_core/security/disclosure.py` | Vulnerability disclosure workflow | Dead Code | **KEEP** | Low |
| `kazma-core/kazma_core/security/hardening.py` + `audit_trail.py` | Offline hardening runner | Dead Code | **KEEP** | Low |
| `kazma-core/kazma_core/docs/*` | Doc generator package; Docusaurus is hand-maintained | Redundant | **KEEP** or archive if unused | Low |
| `kazma-gateway/kazma_gateway/swarm_notify.py` | Telegram progress notifier not hooked into dispatch | Dead Code | **REFACTOR** wire into swarm SSE bus **or** archive | Medium UX if wired |
| `archive/*` | Historical packages (`kazma-comms`, providers, audits) | Dead Code | **KEEP** in archive; exclude from installs | None |
| `docs/` + `docs-v2/` dual trees | **RESOLVED 2026-07-21** — `docs-v2` → `archive/docs-v2/`; unified `docs/docs/` | Redundant (fixed) | **KEEP** archive; edit only `docs/docs/` | See `docs/DOCS_CONSOLIDATION_PLAN.md` |
| `docs/node_modules/` + `docs/build/` | Generated Docusaurus artifacts in workspace | Bloat | **DELETE** from VCS if tracked; ensure `.gitignore` | Disk only |
| `kazma-data/*.db` (+ WAL/SHM) | Local runtime state; may be committed accidentally | Security / Bloat | **DELETE** from git if tracked; never commit vault-adjacent DBs | High if secrets in settings.db |
| `demo_google_genai.py` (root) | One-off demo script at repo root | Dead Code | **MOVE** to `examples/` | Low |
| `features.json` / `data/roadmaps.json` | Product roadmap data; verify single consumer | Redundant risk | **AUDIT** consumers before delete | Low |
| Dual tool registries: `agent/tool_registry.py` vs `tools/registry.py` | Agent path vs swarm ShellTool wrapper | Redundant | **CONSOLIDATE** docs: agent registry is SoT; align ShellTool allowlist | Medium consistency |
| Dual memory: `memory/*` vs `swarm/memory/*` vs `kazma-memory` | Multiple entry points for search/embed | Redundant | **CONSOLIDATE** document single chat entry; keep layers for RAG | Medium |
| Multiple routers (`dialect_detector`, `router.py`, `routing_engine.py`, `models/router.py`) | Naming confusion; ModelRouter not always wired | Redundant | **REFACTOR** rename + wire or drop | Medium clarity |
| `constants.GRAPH_HITL_DANGER_TOOLS` vs `CANONICAL_DANGER_TOOLS` | Stale shorter list can reintroduce weak gates | Security / Redundant | **DELETE** stale re-exports; single SoT in `safety/hitl.py` | High if mis-imported |
| `serve.py` vs `kazma serve` | Two entrypoints; both hardened post-audit | Redundant | **KEEP** both; prefer CLI | Low |
| `ConfigStore()` direct construction in some UI/TUI sites | Violates singleton rule (partial) | Redundant / Concurrency | **REFACTOR** always `get_config_store()` | Medium under load |
| Hardcoded secret **residual** | Known string only as **reject list** in serve/CLI — not assigned | Security (mitigated) | **KEEP** reject list; never re-assign | Critical if reintroduced |
| Empty secret → open mode | Documented backward-compat; risky if secret unset on public bind | Security | **REFACTOR** fail-start non-loopback without secret (CLI already does) | High on misconfig |
| Cookie = shared secret (legacy path) | Opaque sessions default; legacy cookie fallback remains | Security | **REFACTOR** remove legacy secret cookie when multi-user | Medium |
| `NullBusAdapter.request_approval` → False | Fixed fail-closed | Security (resolved) | **KEEP** | — |
| Port matrix 9090 / 8000 / 8090 | CLI vs Docker vs loadtest drift | Redundant | **CONSOLIDATE** document matrix only | Low ops confusion |
| `kubernetes/` + `fly.toml` | Deploy samples; may lag app ports | Stale risk | **REFACTOR** sync with compose/docs | Low |
| `loadtests/` third port (8090) | Intentional isolation | Redundant risk | **DOCUMENT** only | Low |
| `tests/` vs package `*_tests/` | Split test layouts | Redundant | **KEEP** CI covers both; document paths | Low |
| Soft-nav SPA disabled | Product incomplete, not dead | Stale | **KEEP** flag | Low |
| Hub skill install/update stubs | Incomplete product surface | Stale | **IMPLEMENT** or hide UI | Medium product honesty |

---

## Priority cleanup sprints (recommended order)

### Sprint C1 — Safe hygiene (1 day)
1. Ensure `kazma-data/*.db`, `__pycache__`, `docs/node_modules`, `docs/build` are gitignored and untracked.  
2. Move root demo scripts into `examples/`.  
3. Delete or re-export-only stale `constants.GRAPH_HITL_*` to `CANONICAL_DANGER_TOOLS`.  
4. Grep CI for accidental secret commits.

### Sprint C2 — Dual-system decisions (product, 1 week)
1. **delegation vs SwarmEngine** — archive or wire one.  
2. **docs vs docs-v2** — single docs tree.  
3. **permissions.yaml** — wire into tool execute or deprecate.  
4. **swarm_notify** — wire to bus or archive.

### Sprint C3 — Security polish (2–3 days)
1. Fail-start when secret empty and host non-loopback (UI factory path).  
2. Drop legacy `kazma-secret` cookie when opaque sessions on.  
3. Expand automated tests for auth default-deny new routes.

---

## Explicit non-delete list (protect)

| Path | Why |
|------|-----|
| `agent/graph_builder.py`, `tool_registry.py` | Live agent + HITL |
| `swarm/engine.py`, `safety.py`, `bus.py`, `task_store.py` | Live swarm |
| `security/ssrf.py`, `vault.py`, `web_sessions.py`, `platform_rbac.py`, `oidc.py` | Live security |
| `db/*` Postgres dual backend | Multi-replica path |
| Platform adapters + `agent_handler/*` | Live gateways |
| `sse_chat.py`, `ide_api.py`, `session_manager.py` | Live UI |
| `UNWIRED_INVENTORY.md` items until product decision | Intentional libraries |

---

## Metrics snapshot (approx)

| Metric | Value |
|--------|------:|
| Python source files (excl. venv/cache) | ~636 |
| Packages | 7 main (`core`, `ui`, `gateway`, `tui`, `cli`, `skills`, `memory`) |
| Unwired library modules (inventory) | ~12 major areas |
| Dual documentation trees | 2 (`docs/`, `docs-v2/`) |

---

*Cleanup plan generated 2026-07-21. Align deletions with product owner before archiving `delegation/` or dual docs trees.*
