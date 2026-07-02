# Task 3 — Prioritized Roadmap

## Priority Definitions
- **P0** — Critical (broken, missing, security)
- **P1** — High (blocks key use case)
- **P2** — Medium (nice to have, improves quality)
- **P3** — Low (future vision)

## Effort Estimates
- **S** — Small (< 2 hours)
- **M** — Medium (2-8 hours)
- **L** — Large (1-3 days)
- **XL** — Extra Large (3+ days)

---

## P0 — Critical (Fix Now)

### P0-1: Fix 36 failing tests
- **Effort:** M | **Dependencies:** None | **Quick win:** No | **Status:** ✅ Done (Sprint 14)
- **What:** Tests failing in `test_swarm_dynamic_spawning.py`, `test_swarm_engine_core.py`, `test_swarm_handoff.py` — regressions from Sprint 12/13 refactor (TelegramWorker removal, dispatch context changes, provider routing changes)
- **Why:** README claims 3,299 passing; reality is 3,315/3,364 with 36 failures
- **Resolution:** Root cause was a module-level `KAZMA_SECRET` env var leak in `test_hub_e2e.py` (23 failures), plus handoff cycle detection + workspace singleton pollution + stale tests. Reduced 36 → 3 (environmental only). Commits `5e0dda8`, `d81564c`, `eea2972`.

### P0-2: Wire HITL into WebUI + adapter paths
- **Effort:** L | **Dependencies:** None | **Quick win:** No | **Status:** ✅ Done (Sprint 14)
- **What:** Pass `hitl_config` in `get_streaming_graph()` (`agent_runner.py:484`); recompile the startup graph with HITL (`app.py:966`); route WS tool calls through the graph (`chat.py:283`); make `check_sync()` fail-closed (`safety.py:149`)
- **Why:** Currently ALL danger-tier tools (`file_write`, `shell_exec`, `code_exec`) run unattended on every UI/platform path. This is the single biggest security gap.
- **Resolution:** Full 6-phase implementation across Web/Telegram/Discord/Slack. Graph interrupt gate activated, fail-closed safety, bus adapters wired, gateway `/hitl` resolver, DiscordBusAdapter + SlackBusAdapter. Commits `13df2d5`–`e78734a`.

### P0-3: Fix Hub API auth (`_require_auth` broken)
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 13)
- **What:** Rewrite `hub/api.py:24-29` to read `X-Kazma-Secret` and `hmac.compare_digest` — currently authorizes everyone when a secret is set
- **Why:** Authentication is inverted — fails when no secret, passes everyone when secret is set
- **Resolution:** Fixed in commit `301df32` (Security quick wins).

### P0-4: Gate unauthenticated destructive routes
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 13)
- **What:** Add `/api/sessions`, `/api/session`, `/api/mcp/servers`, `/api/approve`, `/api/system/*` to auth middleware's gated paths (`auth.py:44-53`). Switch from sensitive-prefix to open-path allowlist.
- **Why:** `POST /api/sessions/clear-all` and `GET /api/system/flush` are unauthenticated destructive operations
- **Resolution:** Fixed in commit `301df32` (Security quick wins).

### P0-5: Fix Docker deployment
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 13)
- **What:** Change Dockerfile CMD to `--host 0.0.0.0` so containers are reachable
- **Why:** `docker compose up` is broken — binds loopback inside container
- **Resolution:** Dockerfile already fixed (`--host 0.0.0.0`). Confirmed accurate in Sprint 16 docs audit.

---

## P1 — High (Blocks Key Use Cases)

### P1-1: Add SSRF validation to discover/MCP endpoints
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 13)
- **What:** Call `validate_url()` in `model_registry.discover_models`, `discovery.discover_*`, and `mcp/manager._connect_sse`
- **Why:** User-controlled URLs can hit internal services / cloud metadata
- **Resolution:** Fixed in commit `301df32` (Security quick wins).

### P1-2: Fix Active Tasks tab (see Task 4 report)
- **Effort:** M | **Dependencies:** None | **Quick win:** No | **Status:** ✅ Done (Sprint 13)
- **What:** Add in-flight task tracking to engine (`_active_tasks` dict); make `/api/swarm/dispatch` non-blocking (return task_id immediately, run async); add `GET /api/swarm/tasks/active` endpoint; add `loadActiveTasks()` to swarm.js
- **Why:** Active Tasks tab is always empty — fundamental UX gap
- **Resolution:** Fixed in commit `d191a24`.

### P1-3: Enforce skill checksums unconditionally
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 16)
- **What:** Require manifest+checksum before exec_module; fail-closed on exception; add HMAC signatures
- **Why:** Current verification is advisory — exceptions swallowed, execution proceeds
- **Resolution:** Checksum verification is now fail-closed (no more `except: pass`). Tampered files, invalid signatures, and verification errors all raise SkillLoadError. Added HMAC-SHA256 signature verification against KAZMA_SECRET. New `kazma hub sign <dir>` CLI command writes checksum + signature. Unsigned skills still load (backward compat, with warning). 9 new tests.

### P1-4: Add MCP server auth + HITL
- **Effort:** M | **Dependencies:** P0-2 | **Quick win:** No | **Status:** ✅ Done (Sprint 15)
- **What:** Add auth to MCP JSON-RPC `tools/call`; gate `run_tests`/`write_file` behind HITL
- **Why:** MCP server allows unauthenticated file writes and subprocess execution
- **Resolution:** HITL gate in UnifiedToolExecutor, classify_mcp_tool() pattern matching, auth field (bearer/header) + trust levels, UI modal. Commit `00d0f2c`.

### P1-5: Fix ConfigStore atomicity
- **Effort:** M | **Dependencies:** None | **Quick win:** No | **Status:** ✅ Done (Sprint 15)
- **What:** Wrap multi-key mutations in single transactions; load YAML under lock
- **Why:** Concurrent config writes can corrupt state
- **Resolution:** WAL + busy_timeout, batch_set() transactions, singleton, 4 flatten loops → batch_set. Commit `2121e2c`.

---

## P2 — Medium (Quality Improvements)

### P2-1: Refactor engine.py (split god class)
- **Effort:** XL | **Dependencies:** None | **Quick win:** No
- **What:** Split 1,742-line SwarmEngine into: dispatch coordinator, persistence layer, handoff handler, metrics/tracing, autoscaler integration

### P2-2: Per-worker start/stop endpoints
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 16)
- **What:** Add `POST /api/swarm/workers/{name}/start` and `/stop`
- **Resolution:** Engine methods + API routes + UI buttons. Commit `8f0a97e`.

### P2-3: Task cancel/retry from UI
- **Effort:** M | **Dependencies:** None | **Quick win:** No | **Status:** Open
- **What:** Add cancel (interrupt running dispatch) and retry (re-dispatch failed task) buttons

### P2-4: Circuit breaker UI badges
- **Effort:** S | **Dependencies:** None | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 16)
- **What:** Show circuit breaker state (closed/open/half-open) as colored badges on worker cards; manual reset button
- **Resolution:** Breaker data in _serialize_worker, ⚡ badges in worker cards, live polling updates. Commit `8f0a97e`.

### P2-5: Semantic routing (embeddings)
- **Effort:** L | **Dependencies:** None | **Quick win:** No
- **What:** Replace keyword-overlap CapabilityRouter with embedding-based semantic matching

### P2-6: Unify routing algorithms
- **Effort:** M | **Dependencies:** P2-5 | **Quick win:** No
- **What:** Merge CapabilityRouter (keyword), WorkerRegistry.find_best (embedding+keyword), and semantic_router into one

### P2-7: Visual pipeline editor
- **Effort:** XL | **Dependencies:** None | **Quick win:** No
- **What:** Drag-and-drop DAG editor for pipeline stages

### P2-8: Fix README/docs accuracy
- **Effort:** S | **Dependencies:** P0-1 | **Quick win:** ✅ Yes | **Status:** ✅ Done (Sprint 16)
- **What:** Update test count badge (3,315 passing), fix "Socket Mode" claim, document SSE durability gap, add production-readiness caveats
- **Resolution:** Test count 3299→3409, Slack "Socket Mode"→"polling-based", TelegramWorker ref removed, ROADMAP date+count. Commit `8f0a97e`.

### P2-9: Unify config source of truth
- **Effort:** M | **Dependencies:** None | **Quick win:** No
- **What:** Document that ConfigStore SQLite is authoritative; auto-reconcile kazma.yaml on startup

---

## P3 — Low (Future Vision)

### P3-1: Multi-user session isolation
- **Effort:** XL | **Quick win:** No
- **What:** Per-user thread_id namespacing, separate checkpoints per user

### P3-2: Agent memory persistence across restarts (SSE path)
- **Effort:** L | **Quick win:** No
- **What:** Persist SSE session dict to SQLite; survive restarts

### P3-3: Rate limit dashboard
- **Effort:** M | **Quick win:** No

### P3-4: Observability: log aggregation, alerting
- **Effort:** XL | **Quick win:** No

### P3-5: RBAC — verify it actually gates
- **Effort:** L | **Quick win:** No
- **What:** RBAC exists in code but audit found no enforcement point on tool execution

### P3-6: WebSocket streaming for real-time agent output
- **Effort:** L | **Quick win:** No

### P3-7: v2.0 Cultural Switching (ROADMAP.md)
- **Effort:** XL | **Quick win:** No

### P3-8: Mobile app / PWA
- **Effort:** XL | **Quick win:** No

### P3-9: Voice output (beyond transcription)
- **Effort:** L | **Quick win:** No

### P3-10: Multi-tenant SaaS
- **Effort:** XL | **Quick win:** No

### P3-11: Plugin/extension system
- **Effort:** XL | **Quick win:** No

### P3-12: Model benchmarking suite
- **Effort:** M | **Quick win:** No

---

## Quick Wins (Ship Today, < 1 hour each)

| # | Item | Effort | Status |
|---|------|--------|--------|
| 1 | Fix Hub `_require_auth` (P0-3) | S | ✅ `301df32` |
| 2 | Gate destructive routes (P0-4) | S | ✅ `301df32` |
| 3 | Fix Docker bind (P0-5) | S | ✅ Done |
| 4 | Add SSRF validation (P1-1) | S | ✅ `301df32` |
| 5 | Enforce skill checksums (P1-3) | S | ⬜ Open |
| 6 | Per-worker start/stop (P2-2) | S | ✅ `8f0a97e` |
| 7 | Circuit breaker badges (P2-4) | S | ✅ `8f0a97e` |
| 8 | Fix README accuracy (P2-8) | S | ✅ `8f0a97e` |

---

## Already Completed (from prior sessions)

| Item | Status |
|------|--------|
| Telegram setMyCommands (menu button) | ✅ Commit `db85c36` |
| TelegramWorker removal | ✅ Commit `94205bb` |
| SummaryWorker removal | ✅ Commit `94205bb` |
| Provider routing fix (fuzzy matching) | ✅ Commit `52c1bc7` |
| Token double-counting fix | ✅ Commit `52c1bc7` |
| YAML system_prompt restore | ✅ Commit `52c1bc7` |
| ReAct tool-calling loop in workers | ✅ Commit `ba2c0a2` |
| Dedicated swarm bot output routing | ✅ Commit `521bab2` |
| Hub auth fix (P0-3) | ✅ Commit `301df32` |
| Route gating (P0-4) | ✅ Commit `301df32` |
| SSRF validation (P1-1) | ✅ Commit `301df32` |
| Active Tasks tab (P1-2) | ✅ Commit `d191a24` |
| Docker bind fix (P0-5) | ✅ Done |
| HITL approval gates all platforms (P0-2) | ✅ Commits `13df2d5`–`e78734a` (Sprint 14) |
| Test isolation fix (P0-1) | ✅ Commits `5e0dda8`, `d81564c`, `eea2972` (Sprint 14) |
| ConfigStore atomicity (P1-5) | ✅ Commit `2121e2c` (Sprint 15) |
| MCP auth + HITL (P1-4) | ✅ Commit `00d0f2c` (Sprint 15) |
| Circuit breaker badges (P2-4) | ✅ Commit `8f0a97e` (Sprint 16) |
| Per-worker start/stop (P2-2) | ✅ Commit `8f0a97e` (Sprint 16) |
| Docs accuracy (P2-8) | ✅ Commit `8f0a97e` (Sprint 16) |
