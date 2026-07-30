# 360° Audit — HITL Safety & Swarm Engine

**Date:** 2026-07-29
**Scope:** The three HITL gates, danger-tool classification, swarm handoff
cycle detection, circuit breakers, YOLO mode, MCP classification, TaskStore,
and approval-flow concurrency.
**Method:** Invariants from AGENTS.md §4/§5/§6/§7 verified against live source.
Each gate classified BYPASSABLE / SAFE / CONDITIONAL.

---

## Executive Summary

**The safety core is sound.** No production single-agent or swarm danger-tool
bypass was found. All three gates are fail-closed; the danger list is a genuine
single source of truth with a CI parity test; cycle detection, breaker probe,
YOLO TTL, and TaskStore WAL are correctly implemented.

One real defect (M): the **Web HTTP/WS approve paths lack the per-thread resume
lock** the gateway path has (TOCTOU on double-resume — but the danger tool
does *not* double-execute). Several CONDITIONAL items are dev-mode-only or
multi-replica seams.

**Overall: 84/100** — this is the strongest subsystem audited so far.

---

## Findings

### ✅ The Three HITL Gates — all SAFE

| Gate | Verdict | Evidence |
|------|---------|----------|
| **A** graph `interrupt()` | SAFE | Wired on ALL build sites (`agent_runner.py:713,816`, `app.py:1193`). ContextVars (`_graph_hitl_gate_ctx`/`_hitl_approved_ctx`) set ONLY inside `tool_worker_node` post-interrupt; no tool/worker/MCP code sets them. `_hitl_approved` from LLM args is stripped and never trusted (`tool_registry.py:405`). |
| **B** `tool_registry.execute()` + `safety.check()` | SAFE (fail-closed) | `check_sync()` blocks danger on NullBus + `allow_headless_danger=False`. `check()` (async) blocks on NullBus. Exception path returns `is_error=True` "blocked — SafetyMiddleware unavailable" (`tool_registry.py:472-478`). |
| **C** pipeline checkpoints | SAFE for PIPELINE; CONDITIONAL otherwise | Enforced structurally in `patterns.py:368-394, 498-519` (pause after step before next). Checkpoints honored only when `task.type == TaskType.PIPELINE` (`dispatch_inner.py:74`) — a non-PIPELINE task carrying `hitl_checkpoints` silently ignores them (documented design). Individual danger tools inside any worker still hit Gate B. |

**IDE path has no parallel write/exec route** — `IdeService._call_tool` (`ide/service.py:166-175`) delegates to `get_tool_registry().execute()` → Gate B. Grep confirms no direct `file_write()` calls outside the registry.

**Sub-agents fail-closed** — `spawn_agents` hardcodes `safety_mode="auto_deny"` (`sub_agent.py:215`); `spawn_agent` does not expose `safety_mode` to the LLM. `SafetyMiddleware.enabled` cannot be flipped by an attacker (set only by app wiring).

### ✅ CANONICAL_DANGER_TOOLS — single source of truth — SAFE
- Canonical: `safety/hitl.py:101-125` (24 entries). `_EXTENDED_DANGER` (`swarm/safety.py:29`) is an **alias**, not a longer list.
- `kazma.yaml` `require_approval_for` matches exactly (24 entries).
- **CI parity test** (`tests/test_hitl_wiring.py:179-194`) loads the real `kazma.yaml` and asserts equality with the canonical list — drift fails the build.

### ✅ Swarm Handoff Cycle Detection — SAFE
`handoff_guards.py`: `MAX_HANDOFF_DEPTH=5`, `MAX_VISITS=2`. Traced A→B→A→B→A: guard fires at visit 2 (`handoff_guards.py:56`) before depth-5 backstop. Every recursive handoff goes through `_handle_handoff` → guard; no bypass path. Fallback chain threads `_visited`/`_depth` too.

### ✅ Circuit Breaker `_probe_in_flight` — SAFE (single process); CONDITIONAL (multi-replica)
`reliability.py:280-296`: check-then-set with no `await` between → atomic under asyncio. Reset on BOTH success (`:324`) and failure (`:339`). All state mutations go through methods. **Multi-replica seam:** `from_dict` (`:388-414`) rebuilds with `_probe_in_flight=False`; a replica hydrating from ConfigStore could double-probe (documented best-effort under `KAZMA_SHARED_BREAKERS=1`).

### ✅ YOLO Mode — SAFE
`safety/yolo.py`: TTL enforced (`expires_at`, lazy expiry on read). Production guard (`KAZMA_PRODUCTION=1` blocks unless `KAZMA_ALLOW_YOLO=1`). Per-thread keying. **No untrusted enable path**: all `enable_yolo` callers are operator-triggered slash/HTTP/WS handlers; no tool exposes it; `spawn_agent` doesn't expose `safety_mode`; `config_save` blocks the `yolo.` prefix (`tool_registry.py:1067`).

### 🟡 MCP Danger Classification — CONDITIONAL (dev-only evasion)
`classify_mcp_tool` (`mcp/manager.py:101-120`): `unknown` → treated as danger (`hitl.py:240`). Gate in `UnifiedToolExecutor.execute()` IS reached for all MCP calls. **Dev-mode evasion:** a malicious MCP server naming a destructive tool `get_data`/`read_config` matches `_SAFE_KEYWORDS` → classified `safe` → skips HITL in default mode. **Production closes it:** prod mode (`manager.py:1184-1185`) requires HITL for anything not on `KAZMA_MCP_SAFE_ALLOWLIST`. Mitigated by explicit `trust: trusted` opt-in.

### ✅ TaskStore WAL — SAFE
`swarm/task_store.py`: WAL + `busy_timeout=5000` via `apply_sqlite_pragmas` (`:112`). Worker filter uses `json_each` exact match (`:408-410`), not `LIKE`. All queries parameterized — no SQL injection.

### 🟧 Approval Flow Concurrency — CONDITIONAL (Web UI TOCTOU)
- **Gateway path (`/hitl approve`)** — SAFE: per-thread `asyncio.Lock` (`graph.py:249-279` `_get_thread_lock`) + stale-card guard.
- **Web HTTP (`POST /api/approve`)** — CONDITIONAL: `routes_direct.py:1294-1586` has the stale check but **no lock** between `aget_state` (`:1410`) and `Command(resume)` (`:1569`).
- **WS (`approve_tool`)** — CONDITIONAL: same pattern (`ws_chat.py:647-845`).

**Harm is bounded:** LangGraph serializes checkpoint writes; the first resume consumes the interrupt and the danger tool runs once. The second resume is a no-op/409. Realistic worst case: confusing UX, not a double execution. **Fix:** backport `_get_thread_lock` to the HTTP and WS approve paths.

---

## Roadmap

### ⚡ Phase 1 (immediate)
1. **Backport the per-thread resume lock** to `routes_direct.py:1294` and `ws_chat.py:647` (the gateway path already has it).

### 🏗️ Phase 2 (short-term)
2. **Document/enforce** that MCP `safe`-name classification is only relied upon in production mode; warn in dev when auto-connecting untrusted servers.
3. **Multi-replica breaker probe**: share `_probe_in_flight` in ConfigStore (or document the seam explicitly).

### 🟢 Strengths to preserve
- The ContextVar double-gating design is correct and not bypassable — do not let a future refactor add a second `.set()` site outside `tool_worker_node`.
- The YAML/code parity test is the model for keeping a single source of truth — replicate this pattern elsewhere.
