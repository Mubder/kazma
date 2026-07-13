# Unwired / Library-Only Inventory

**Date:** 2026-07-13  
**Purpose:** Track modules that are fully implemented and tested but **not
wired into the production runtime** (agent runner, swarm engine, gateway
dispatch, or web/TUI live paths). They are **kept on purpose** so future
features can land without rewrites.

Do **not** delete these without an explicit product decision. Import paths
and public exports remain stable.

---

## Production-critical (must not remove)

These are live; listed only for contrast:

| Area | Path |
|------|------|
| Swarm orchestration | `kazma_core/swarm/*` |
| Agent / LangGraph | `kazma_core/agent/*`, `agent_runner.py` |
| HITL (graph + bus + pipeline) | `safety/hitl.py`, `swarm/safety.py`, gateway buses |
| Platform adapters | `kazma_gateway/adapters/*` |
| SSE chat | `kazma_ui/sse_chat.py` |
| Swarm task SSE | `kazma_ui/swarm_sse.py` + `swarm_panel/` (wired 2026-07-13) |
| Tool registries | `agent/tool_registry.py` (agent), `tools/registry.py` (swarm) |
| Security used live | `security/ssrf.py`, `security/vault.py` |
| RBAC engine instance | Constructed in `mcp/manager.UnifiedToolExecutor` (minimal use) |

---

## Unwired but retained (future / library)

| Module / package | LOC (approx) | Tests | Why kept |
|------------------|-------------:|-------|----------|
| `kazma_core/delegation/*` | ~1,600 | `test_delegation_*`, `test_swarm.py` | Parallel multi-agent design; not used by SwarmEngine |
| `authorization_flow.py` | ~350 | `test_authorization_flow.py` | Enterprise cross-division approvals |
| `division_sandbox.py` | ~280 | `test_division_sandbox.py` | Division-scoped execution |
| `permissions.py` + `kazma-permissions.yaml` | ~200 | `test_permissions.py` | YAML permission manager (not enforced in runtime) |
| `tool_sandbox.py` | ~120 | `test_sandbox.py` | Alternate tool sandbox (HITL path is live instead) |
| `majlis.py` | ~350 | `test_majlis.py` | Cultural orchestrator; pieces (pacing/tone) *are* live in gateway |
| `security/certification.py` | ~340 | `test_certification.py` | Skill cert levels; hub does not call it yet |
| `security/linter.py` | ~480 | via certification tests | Skill security linter |
| `security/dependency_scanner.py` | ~880 | `test_dependency_scanner.py` | CVE-style dep scan |
| `security/disclosure.py` | ~490 | `test_disclosure.py` | Vulnerability disclosure workflow |
| `security/hardening.py` + `audit_trail.py` | ~890 | `test_hardening.py` | Offline hardening runner |
| `docs/` package | ~400 | `test_doc_generator.py` | Doc generator (Docusaurus is hand-written) |
| `kazma_gateway/swarm_notify.py` | ~370 | `test_swarm_notify.py` | Optional Telegram progress notifier; not hooked into dispatch |

### Explicit non-goals for cleanup

- Do not archive `delegation/` until product chooses: wire into SwarmEngine **or** drop the dual model.
- Do not strip `kazma_core.__init__` re-exports of Majlis/RBAC/Authorization without a deprecation cycle.
- Do not remove `RBACEngine` — it is constructed on the live tool executor path.

---

## Removed in 2026-07-13 cleanup (safe)

| Item | Reason |
|------|--------|
| `kazma_ui/swarm_panel.py` | Shadowed by package `swarm_panel/`; SSE behavior restored into package |
| `kazma_core/docs.py` | Shadowed by package `docs/` |
| `kazma_tui/widgets/circular_progress.py` | Zero importers |
| `kazma_tui/widgets/performance.py` | Zero importers |
| Orphan `__pycache__` for deleted modules (`kca/`, `auth/vault`, old gateway modules, etc.) | Bytecode without source |
| Empty dirs `kca/`, `auth/`, `panels/` | Leftover after pyc cleanup |

---

## Feature restore notes

### Swarm task SSE (fixed 2026-07-13)

When the panel was split into `swarm_panel/`, the package import won over
`swarm_panel.py`, and the old file’s SSE mount became unreachable. Live path
now:

1. `SwarmRouterBuilder` mounts `create_sse_router` at `/api/swarm/tasks/{id}/stream`
2. Registers the bus via `SwarmService.register_sse_bus`
3. `resolve_engine()` re-wires the bus when the engine appears later
