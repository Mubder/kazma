# Task 2 — Deep Comprehensive Audit Report

## Executive Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Security (Attack Chains) | 3 | 5 | 1 | 2 |
| Docs-vs-Reality | 0 | 3 | 2 | 0 |
| Dead Code / Phantom | 0 | 0 | 3 | 2 |
| Architecture | 0 | 1 | 2 | 0 |

**Architecture Health Score: C+** — Functional but has critical security gaps in the HITL system, unauthenticated destructive routes, and several docs-vs-reality mismatches. The core engine and tool infrastructure are solid; the security posture is the weakest link.

---

## Step 1 — Metrics Verification

| Metric | README Claims | Actual | Verdict |
|--------|--------------|--------|---------|
| Test count | 3,299 passing (badge) | 3,364 collected, **3,315 passed**, 36 failed, 13 skipped | **STALE** — number wrong + 36 failures |
| Python files | ~206 | **393** | **Understated** |
| Source LOC | ~57K | **117,553** | **Understated ~2x** |
| Slash commands | 12 | **12** (help, reset, status, model, memory, cost, replay, config, personality, context, undo, edit) + `/swarm` | ✅ Accurate (swarm is separate) |
| Python version | 3.11+ | `requires-python = ">=3.11,<3.14"` | ✅ Accurate |

**36 failing tests** — primarily in `test_swarm_dynamic_spawning.py` (spawn→dispatch API after the TelegramWorker removal and dispatch changes), `test_swarm_engine_core.py`, and `test_swarm_handoff.py`. These are regressions from the recent Sprint 12/13 refactor work.

---

## Step 2 — Attack-Chain Security Audit

### [CRITICAL] B. HITL (Human-in-the-Loop) is NOT enforced on most paths

**Root cause:** There are two HITL layers, but only one is wired in production, and it fail-opens.

**Layer 1 — Graph-level `interrupt()`:**
- `graph_builder.py:355-436`: when `hitl_config` is passed, danger tools call LangGraph `interrupt()` and block until `/api/approve` resumes.
- `agent.run()` CLI path wires it: `agent_runner.py:518` reads `safety.hitl` and passes `hitl_config=` at line 529. ✅
- **`get_streaming_graph()` (WebUI SSE) does NOT pass `hitl_config`** — `agent_runner.py:484-492`. Every tool becomes "safe" → `interrupt()` is never called.
- **All platform adapters (Telegram/Discord/Slack) bypass graph HITL** — they receive the no-HITL `_sse_graph_ref` from `app.py:988`.

**Layer 2 — `SafetyMiddleware.check_sync()`** (`safety.py:149-174`):
- Invoked by `tool_registry.execute()` (`tool_registry.py:346-360`)
- **Fail-opens in 3 of 4 branches:** disabled → `return True`; not danger → `return True`; `NullBusAdapter` (default!) → `return True`; any exception → `return True`
- **WebSocket chat path bypasses the graph entirely** — `chat.py:283` calls `agent.tools.execute()` directly, with safety check wrapped in `except Exception: pass` (`chat.py:280`).

**Impact:** In any deployment without a live Telegram bus adapter, `file_write`, `file_delete`, `shell_exec`, `python_exec`, `code_exec`, `spawn_agent` all execute with **zero approval** on every path except `agent.run()` CLI.

**Files:** `agent_runner.py:484`, `app.py:966`, `chat.py:283`, `safety.py:149`, `worker.py:329`

**Fix direction:** Pass `hitl_config` in `get_streaming_graph()`; route WS tool calls through the graph; make `check_sync()` fail-closed; remove `except: pass` in `chat.py:280`.

---

### [CRITICAL] E. Hub API `_require_auth` is completely broken

**File:** `kazma-core/kazma_core/hub/api.py:24-29`

```python
def _require_auth(request: Request) -> None:
    expected = _os_hub.environ.get("KAZMA_SECRET", "")
    if not expected:
        raise HTTPException(401, "KAZMA_SECRET not configured — auth required")
        raise HTTPException(401, "Unauthorized — provide X-Kazma-Secret header")  # dead code
```

When `KAZMA_SECRET` **IS** set, the `if not expected` block is skipped and the function returns `None` — no header read, no `compare_digest`. The second `raise` is unreachable dead code. So `submit_skill` and `download_skill` are **completely unauthenticated whenever a secret is configured** (and hard-deny when it isn't). This is backwards in both directions.

**Fix:** Rewrite to read `X-Kazma-Secret` header and `hmac.compare_digest` against env secret.

---

### [CRITICAL] A. Unauthenticated destructive routes

**File:** `kazma-ui/kazma_ui/auth.py:44-53`

Auth middleware only gates paths matching `SENSITIVE_PREFIXES`. These routes are **NOT** in the list and are reachable without `X-Kazma-Secret` even when a secret IS configured:

| Route | File:Line | Impact |
|-------|-----------|--------|
| `POST /api/sessions/clear-all` | `dashboard.py:281` | Deletes all sessions + checkpoints |
| `DELETE /api/sessions/{thread_id}` | `dashboard.py:320` | Deletes individual session |
| `POST /api/approve/{thread_id}` | `app.py:816` | HITL approve/deny any paused tool (no secret check if default) |
| `DELETE /api/mcp/servers/{name}` | `app.py:149` | Delete MCP server config |
| `GET /api/system/flush` | `app.py:103` | DoS — nulls model/tool/worker singletons |
| `GET /api/system/debug/registry` | `app.py:83` | Info disclosure — dumps full provider/model inventory |
| `GET /api/pending-approvals` | auth.py:63 (ALWAYS_OPEN) | Approval queue world-readable |

**Fix:** Switch auth from sensitive-prefix to open-path allowlist; add `/api/sessions`, `/api/session`, `/api/mcp/servers`, `/api/approve`, `/api/system/*` to gated set.

---

### [HIGH] D. SSRF — unvalidated URL fetches

`validate_url()` (`security/ssrf.py`) is solid and correctly applied in providers `/test`, `read_url`, and `vision_analyze`. But missing from:

| Function | File:Line | Risk |
|----------|-----------|------|
| `model_registry.discover_models` | `model_registry.py:395` | Server fetches arbitrary base_url from ConfigStore |
| `discover_lm_studio_models` | `discovery.py:137` | Arbitrary base_url, no validation |
| `discover_custom_models` | `discovery.py:183` | Arbitrary base_url, no validation |
| `MCP SSE connect` | `mcp/manager.py:413` | `httpx.AsyncClient(base_url=cfg["url"])` |

**Impact:** User who can set provider base_url or MCP server URL can make server fetch `http://169.254.169.254/...` or internal hosts.

**Fix:** Call `validate_url(base_url, block_unresolved=True)` at top of each function.

---

### [HIGH] C. Skill loader — integrity is advisory, not enforced

**File:** `kazma-core/kazma_core/hub/loader.py:200-228`

- Checksum is computed **before** `exec_module` (correct ordering) ✅
- **But verification only runs if** `skill_manifest.yaml` exists (line 202), only blocks if `stored_hash` non-empty (line 209), and **any non-`SkillLoadError` exception is swallowed** (`except Exception: pass`, line 217-218) → execution proceeds.
- It's a plain SHA-256 checksum, **not a signature**. No HMAC/asymmetric verification. An attacker who can write the file can recompute the hash.

**Fix:** Require manifest+checksum unconditionally; fail-closed on exception; add HMAC/detached signatures.

---

### [HIGH] MCP IDE server — no auth, subprocess execution

**File:** `kazma-gateway/kazma_gateway/mcp_server.py:317-330`

`tools/call` dispatches `write_file` and `run_tests` (subprocess, line 197-210) with no HITL and no auth on the JSON-RPC call. Only a path-escape guard exists. Anything reaching the stdio/SSE MCP server can write files and spawn processes.

---

### [HIGH] Docker deployment broken

**File:** `Dockerfile` CMD

Binds `127.0.0.1` inside the container. With `ports: 8000:8000`, the service listens on container loopback only → **host cannot reach it**. `docker compose up` is broken out of the box.

**Fix:** Change CMD to `--host 0.0.0.0` (Docker provides network isolation).

---

### [MEDIUM] H. ConfigStore — non-atomic multi-key writes

**File:** `kazma-core/kazma_core/config_store.py`

- `threading.Lock` serializes **individual** calls, not multi-key operations
- `import_yaml` (line 192) calls `self.set` once per leaf key — each acquires/releases lock separately
- A crash mid-import leaves partial config; concurrent readers see half-imported state
- `get()` reads YAML fallback **outside** the lock (line 119)

**Fix:** Wrap multi-key mutations in a single transaction (`with self._lock:` + one `BEGIN/COMMIT`).

---

### [LOW] G. Secret masking edge case

**File:** `kazma-ui/kazma_ui/providers.py:50-57`

A real API key containing `****` as a substring would be misidentified as a placeholder on save, silently keeping the old key. Rare but possible with generated tokens.

---

## Step 3 — Docs-vs-Reality Gap Analysis

| README Claim | Reality | Verdict |
|---|---|---|
| `tests-3299_passing` badge | 3,364 collected, 3,315 passed, **36 failed** | **STALE + misleading** |
| `production_ready` badge | Default bind `127.0.0.1` ✅, but auth **default-empty** (fail-open), HITL unenforced on UI/platforms | **Misleading** |
| "Durable Execution … resume after SIGKILL" | True only for `agent.run()` CLI path. **WebUI SSE path** uses in-memory `session_id→thread_id` dict (`sse_chat.py:299`) — **lost on restart** | **Overclaimed** for primary UI |
| "Socket Mode" for Slack | `adapters/slack.py` uses **polling** (`conversations.list`/`history`). `SLACK_APP_TOKEN` collected but **unused**. | **Inaccurate** |
| "Docker deployable" | Dockerfile binds `127.0.0.1` — host can't reach container | **Broken** |
| Sub-Agent Spawning | `agent/sub_agent.py` is real: `spawn`/`spawn_parallel` with `hitl_config` | ✅ Accurate |
| RAG Memory / ChromaDB | `memory/vector_store.py` imports `chromadb.PersistentClient`, wired into 4-layer adapter | ✅ Accurate |
| Knowledge Graph / NetworkX | `kg_engine.py` uses `nx.MultiDiGraph`. Neo4j backend raises `NotImplementedError` | ✅ for NetworkX; Neo4j stubbed |
| "12 slash commands" | Exactly 12 in `resolve_slash_command()` | ✅ Accurate |

---

## Step 4 — Dead Code & Phantom Features

| Item | Status | Notes |
|------|--------|-------|
| TelegramWorker | ✅ **Removed** (commit `94205bb`) | Only historical comment remains in `engine.py:129` |
| SummaryWorker | ✅ **Removed** (commit `94205bb`) | Fully clean |
| `_fallback_html` in swarm_panel.py | ✅ **NOT dead** | Reachable template-missing fallback at line 363 |
| `_require_auth` dead code | ❌ **Bug** | Second `raise` is unreachable (`hub/api.py:29`) |
| Slack `app_token`/Socket Mode | ⚠️ **Phantom** | Collected in config but unused; adapter uses polling |
| Neo4j KG backend | ⚠️ **Stub** | Raises `NotImplementedError` (`kg_adapter.py:58`) |
| Telegram local STT | ⚠️ **Stub** | Logs "not yet implemented" (`telegram.py:576`) |
| Hub skill download | ⚠️ **Manifest only** | Ships only JSON manifest, not executable code |
| `discover_*_models` duplicates | ⚠️ **Overlapping** | Near-duplicate functions in `discovery.py` |
| Discord/Slack adapters | ✅ **Functional** | Real WebSocket/polling implementations with valid API calls |

---

## Step 5 — Architecture Assessment

### Critical Refactor Candidates (>1000 lines)

| Lines | File | Issue |
|-------|------|-------|
| 1,742 | `swarm/engine.py` | Monolithic SwarmEngine: dispatch, persistence, handoff, autoscaling, metrics, tracing |
| 1,462 | `ui/swarm_panel.py` | REST API + HTML serving + SSE wiring |
| 1,353 | `gateway/agent_handler.py` | Message handler + slash commands + swarm dispatch + model selector |
| 1,226 | `ui/app.py` | App factory wiring everything |
| 1,137 | `adapters/telegram.py` | Full bot adapter + keyboards + voice + callbacks |

### God Classes (500-1000 lines)
22 additional files, notably: `settings_manager.py` (983), `tool_registry.py` (965), `reliability.py` (890), `graph_builder.py` (876), `model_registry.py` (840).

### Coupling
- 6 non-test modules import directly from `engine.py`; 17 including tests
- Runtime mutual dependency between `agent` and `swarm` packages (resolved by lazy imports, but a refactor boundary smell)
- `swarm/engine.py` should be split into: dispatch coordinator, persistence layer, handoff handler, metrics/tracing

### Config Source of Truth
**ConfigStore (SQLite) is the runtime source of truth; `kazma.yaml` is the fallback/base.** DB overrides always win. `export_yaml`/`import_yaml` exist to reconcile but are not auto-run. This is a common confusion source.

### Test Quality
- Heavy mock usage (most tests mock providers, registries, stores)
- Integration tests exist (`test_swarm_cross_flows.py`) but are limited
- 36 failing tests indicate regressions from recent refactoring
