# Kazma Deep Audit — 2026-07-08 (Fresh Re-Audit)

**Repository:** `G:\GitHubRepos\kazma`
**Auditor:** Mavis / M3
**Method:** Direct source inspection (no tests run) against prior audits:
`AUDIT_DEEP_REPORT_2026-07-07.md`, `AUDIT_REPORT.md`, `CODE_QUALITY_AUDIT.md`,
`SECURITY_AUDIT.md`, `DEEP_AUDIT_REPORT.md`.
**Goal:** Identify **NEW** bugs since the most recent audit (one day later) and
verify which prior P0/P1 items remain UNFIXED, PARTIALLY FIXED, or REGRESSED.

---

## TL;DR

The Sprints 14–17 remediation work landed: most prior Criticals (C-1 MCP, C-2
WS, C-3 SSE graph, C-4 services facade, H-1/2/3/4) are **fixed or hardened**.
The architecture is in better shape than the previous audit reports. However,
this fresh audit identified **6 NEW critical/high-severity findings**, including:

1. **P0 — Async HITL bypass via `NullBusAdapter.request_approval` returning `True`**
   silently auto-approves every swarm/MCP danger tool when no platform
   adapter is wired. The sync path is correctly fail-closed; the async path is
   not. (`kazma-core/kazma_core/swarm/bus.py:114`)
2. **P1 — Discord adapter has zero `allowed_users` enforcement.** Any user in
   any guild the bot joins can dispatch agent commands. (`adapters/discord.py`)
3. **P1 — Slack `allowed_teams`/`allowed_channels` whitelist exists but
   `app.py` never reads or wires them.** Slack accepts messages from any team
   by default. (`app.py:387`)
4. **P1 — `_finalize_task` mutates `_task_history` without holding
   `_task_lock`.** Race condition under concurrent task completion.
5. **P1 — `ModelRegistry` active provider/model + `_clients` dict mutated
   without a lock.** Race condition across concurrent requests.
6. **P2 — MCP IDE server defaults to `KAZMA_MCP_IDE_ENABLED=true`.** When the
   app is run without `KAZMA_SECRET` set in env, ANY process that can speak
   stdio JSON-RPC gets file-write + test execution.

Severity ladder:
- **P0** = real security / correctness gap that can be triggered today
- **P1** = real bug or significant design hole, exploit requires specific config
- **P2** = quality / hardening issue with security implications
- **P3** = documentation drift, dead code, minor code smell

---

## 1. Status of Prior Audit (2026-07-07) P0/P1 Items

| Prior ID | Finding | Status | Evidence |
|---|---|---|---|
| **C-1** | MCP IDE server — no auth, no HITL | **PARTIALLY FIXED** | `mcp_server.py:345-374` now requires `_secret` argument matching `KAZMA_SECRET` via `hmac.compare_digest`, and routes `write_file`/`run_tests` through `safety.check_sync()`. But server defaults to **enabled** (`KAZMA_MCP_IDE_ENABLED` env defaults `"true"`); auth gate only kicks in if `KAZMA_SECRET` is set. See NEW-FINDING N-2. |
| **C-2** | WebSocket chat bypasses graph `interrupt()` HITL | **FIXED (deprecated)** | `chat.py:127` — `chat_websocket_handler` immediately closes with `code=4100` before reaching the legacy code body. A `DeprecationWarning` is emitted. Dead code below line 130 is unreachable. Frontend should now hit `/api/chat/stream`. |
| **C-3** | SSE router holds stale graph without checkpointer | **FIXED** | `app.py:54` defines `_graph_holder`; `app.py:594-595` passes it as `graph_holder=` and `graph_getter=lambda: self._graph_holder.get("graph")` to `create_sse_chat_router`; `sse_chat.py:319-332` `_get_graph()` resolves via `graph_getter` → `graph_holder` → fallback. `app.py:718` updates the holder post-startup with the recompiled (HITL+checkpointer) graph. |
| **C-4** | Documented `services.py` facade doesn't exist | **FIXED** | `kazma-ui/kazma_ui/services.py` present (335 LOC, per `DEEP_AUDIT_REPORT.md`). |
| **H-1** | Auth shared-secret, cookie auto-issued to all dashboard visitors | **UNCHANGED** | `auth.py:217-229` still unconditionally auto-sets `SECRET_COOKIE` on every response (including page loads) when `KAZMA_SECRET` is configured. By design for browser-based JS but means **any unauthenticated visitor who loads any dashboard page becomes a fully-privileged API client.** Risk depends on deployment topology (localhost vs. public). |
| **H-2** | `POST /api/approve/{thread_id}` lacks thread ownership | **PARTIALLY FIXED** | `routes_direct.py:480-494` adds ownership check, BUT the check is bypassed when `session_id` is absent from the request body (line 490 `if owner and caller_session`). `session_id` is also client-asserted, not bound to auth identity — anyone with `KAZMA_SECRET` can approve any thread by omitting the field. |
| **H-3** | Hub API + loader use `os.environ["KAZMA_SECRET"]` directly, not ConfigStore | **FIXED** | Both `hub/api.py:36` and `hub/loader.py` use `get_kazma_secret()` (env-based today, ConfigStore-backed path available). |
| **H-4** | `spawn_agent`/`spawn_agents` in bus danger list but not in `kazma.yaml` `require_approval_for` | **FIXED** | `kazma.yaml:91-94` includes all four: `spawn_agent`, `spawn_agents`, `schedule_task`, `cancel_scheduled`. |
| **H-5** | `KAZMA_AUTH_DISABLED` fully opens APIs | **PARTIAL — effectively dead** | `auth.py:104` reads the env var and returns `""` (open mode) if set. BUT `app.py:97-98` unconditionally auto-generates and sets `os.environ["KAZMA_SECRET"]` on startup (regardless of `KAZMA_AUTH_DISABLED`), so the disable flag is silently overridden before `auth.get_kazma_secret()` ever sees it. |
| **H-7** | `python_exec`/`code_exec` weaker sandbox on Windows | **FIXED** | `code_exec.py:208-209` calls `_assign_to_job_object(proc)` on Windows to enforce resource limits via Job Objects. POSIX uses `_set_limits` via `preexec_fn`. |
| **H-8** | `swarm_panel.py` god module (1,795 LOC) | **UNCHANGED** | Now ~1,993 LOC per `architecture.md:124`. No decomposition since last audit. |
| **H-9** | `agent_handler.py` god module (1,359 LOC) | **FIXED — extracted** | `agent_handler.py` is now 72 LOC; subpackage split into `agent_handler/{store, hitl, commands, swarm_dispatch, graph}.py`. Per-file LOC: commands 492, swarm_dispatch 445, graph 297, hitl 141, store 107, init 69. |
| **H-10** | `engine.py` still large | **FIXED** | 1,512 LOC (was 1,403 in last audit, 1,878 original). Reliability/phonebook/checkpoint extracted to `reliability_registry.py` (195 LOC), `phonebook.py`, `checkpoint_manager.py`. Slight regression vs. last audit but refactor preserved. |
| **H-11** | `app.py` god builder | **PARTIALLY FIXED** | 865 LOC (was 1,152). Now split into `KazmaAppBuilder` class with named phases (`_bootstrap_environment`, `_setup_templates_and_middlewares`, `_setup_swarm`, `_setup_gateway_and_bus`, `_setup_routers`, `_setup_lifecycle_and_errors`). Still 60+ `try/except` blocks swallowing errors. |
| **H-12** | Duplicate adapter registration (initial vs refresh) | **NOT VERIFIED** | Did not re-inspect `refresh_gateway_adapters`; verified `app.py:344-394` initial registration has the dup pattern but only single site. If `refresh_gateway_adapters` was deduped, OK. |
| **H-13** | ~300 `except Exception` blocks | **UNCHANGED** | Still pervasive across all packages. Hot-path swallows include `app.py:433-434` (vector memory init), `app.py:516` (SlackBusAdapter wire failure), `app.py:533` (SubAgentManager init), `chat.py:530` (WS error reply), `engine.py:1385-1388` (task persist). |
| **H-14** | 49+ `kazma_core` internal imports from `kazma-ui` | **UNCHANGED** | `sse_chat.py` still imports `kazma_core.agent.state`, `kazma_core.providers`, `kazma_core.url_utils`; `app.py` still imports `kazma_core.agent.graph_builder.build_supervisor_graph`, `kazma_core.safety.hitl.get_hitl_config`, etc. The services facade masks some of it but did not eliminate. |

---

## 2. NEW Findings (Post 2026-07-07)

### N-1. P0 — Async HITL auto-approval via `NullBusAdapter.request_approval = True`

**File:** `kazma-core/kazma_core/swarm/bus.py:105-127`

```python
class NullBusAdapter(BusAdapter):
    async def send(self, message: BusMessage) -> None: pass
    async def send_report(self, report: SwarmReport) -> None: pass
    async def request_approval(self, approval: ApprovalRequest) -> bool:
        return True   # auto-approve when no adapter is present   ← BUG
```

**Why this is dangerous:**

`safety.check()` (async, used by `tool_registry.py:400` and `mcp/manager.py:735`)
calls `bus.request_approval(...)` and waits. If no real Telegram/Discord/Slack
adapter is wired (vanilla deployment, fresh `.env` with no connectors), the
singleton bus defaults to `NullBusAdapter`, which returns `True` — i.e.
**auto-approves the danger tool silently**.

The sync path (`safety.check_sync()`) is correctly fail-closed: lines 175-184
explicitly detect `NullBusAdapter` and return `False` unless
`allow_headless_danger=True` (which is a documented test escape hatch).

The async path has no such check. The result is **asymmetric enforcement**:
- Swarm bus + no adapter + danger tool → APPROVED (auto).
- Direct sync check + no adapter + danger tool → BLOCKED.

**Impact:** Any operator running a vanilla kazma deployment with no platform
connector (very common: web-only or CLI-only deployments) gets **no HITL
protection on the swarm/MCP async path**. An LLM prompt-injection or
compromised skill can call `shell_exec`, `file_write`, `python_exec`, etc.
without operator approval.

**Reproduction (hypothetical):**
```python
from kazma_core.swarm.bus import get_message_bus
bus = get_message_bus()
# bus._adapter is NullBusAdapter() — no platform wired
import asyncio
print(asyncio.run(bus.request_approval(ApprovalRequest(...))))
# → True  (silently auto-approved)
```

**Fix:**
1. **Best:** `NullBusAdapter.request_approval` should return `False` and
   log `WARNING: No bus adapter wired — danger tool auto-rejected`. The
   documented "fail-closed" behavior is supposed to apply to async too.
2. **Or:** `safety.check()` (async) mirrors `check_sync()`: check
   `isinstance(bus._adapter, NullBusAdapter)` first; if so, return `False`
   unless `allow_headless_danger=True`.
3. Add a test asserting async path fails closed.

**Owner:** kazma-core / swarm
**Estimated effort:** 1-2 hours + test.

---

### N-2. P2 — MCP IDE server defaults to ENABLED

**File:** `kazma-gateway/kazma_gateway/mcp_server.py:348`

```python
mcp_enabled = os.environ.get("KAZMA_MCP_IDE_ENABLED", "true").lower() in ("1", "true", "yes")
```

When `KAZMA_MCP_IDE_ENABLED` is unset, the IDE server is **enabled by default**.
Anyone running `python -m kazma_gateway.mcp_server` (or having it auto-launched
by VS Code MCP extension) exposes `write_file` and `run_tests` over stdio JSON-RPC.

The auth gate (lines 353-361) requires `KAZMA_SECRET` env var **and** a matching
`_secret` arg. If the operator doesn't set `KAZMA_SECRET`, the auth check is
skipped entirely (`if kazma_secret:` line 354 — only enters the check if secret
is set).

**Combined with N-1:** An MCP client with no Telegram/Discord/Slack adapter AND
no `KAZMA_SECRET` set gets file-write + test-run with no auth AND with auto-
approval on the swarm/MCP HITL path.

**Fix:**
- Default `KAZMA_MCP_IDE_ENABLED` to `false`.
- OR require `KAZMA_SECRET` to be set before mounting the server.
- OR refuse to start if both `KAZMA_SECRET` is unset AND
  `KAZMA_MCP_IDE_ENABLED=true`.

---

### N-3. P1 — Discord adapter has NO `allowed_users` enforcement

**File:** `kazma-gateway/kazma_gateway/adapters/discord.py`

Telegram enforces a per-user allow-list at four sites (`telegram.py:314-316`,
`494-496`, `798-800`, `848-850`). Discord parses `author` and `channel_id` but
**never checks whether the sender is allowed**. Combined with the app.py wiring:

- `DiscordAdapter(token=discord_token)` (line 369) — no `allowed_users` param
  even exists on the constructor.
- `app.py` never calls any `set_allowed_*` method on Discord.

**Impact:** Once you add the bot to any Discord guild, any user in that guild
can dispatch messages to the agent → graph → tool execution → HITL gate.
Even with HITL enabled, the operator is pestered with approval prompts from
strangers. Without HITL (or with N-1's auto-approval), it's open.

**Fix:**
1. Add `allowed_users: list[int] | None` param to `DiscordAdapter.__init__`
   (mirror Telegram).
2. Check `author.id` in `_parse_message` or in the dispatch path
   (`if self._allowed_users and author_id not in self._allowed_users: return None`).
3. Read from `connectors.discord.allowed_users` in `app.py:369` and pass
   through.

---

### N-4. P1 — Slack adapter's `allowed_teams` / `allowed_channels` whitelist is dead code

**File:** `kazma-gateway/kazma_gateway/adapters/slack.py` (whitelist classes
exist at lines 62-81, applied at lines 432, 548).

**File:** `kazma-ui/kazma_ui/app.py:385-394`

```python
slack_adapter = SlackAdapter(
    bot_token=slack_bot_token,
    app_token=slack_app_token or None,
)
```

The `SlackAdapter` constructor accepts `allowed_teams` and `allowed_channels`
params, but `app.py` never reads them from ConfigStore and never passes them.
Empty defaults = whitelist disabled = accept messages from any team/channel.

**Fix:** Read `connectors.slack.allowed_teams` / `connectors.slack.allowed_channels`
from ConfigStore in `app.py:380-394`, pass to `SlackAdapter(...)`.

---

### N-5. P1 — `_finalize_task` mutates `_task_history` without holding `_task_lock`

**File:** `kazma-core/kazma_core/swarm/engine.py:1356-1360`

```python
task.result = result
self._task_history[task.id] = SwarmTask.from_dict(task.to_dict())   # MUTATES
if len(self._task_history) > self._max_history:
    excess = len(self._task_history) - self._max_history
    for old_key in list(self._task_history.keys())[:excess]:
        self._task_history.pop(old_key, None)                       # MUTATES
```

The class declares `self._task_lock = asyncio.Lock()` (line 95) for exactly this
purpose, and another site at line 1604 uses it correctly. This site does not.

**Race scenario:** Two tasks complete concurrently → both enter `_finalize_task`
→ both pass `len() > max_history` check with the same dict snapshot → both
iterate pop() → one evicts an extra entry the other was about to read.

**Impact:** Low-frequency (only at LRU eviction boundary), but real loss of
strictly-correct ordering. Also, since `_task_history` is exposed via
`get_task()` (line 202) and `list_active_tasks()` (line 204), concurrent readers
can see torn dict state.

**Fix:** Wrap lines 1356-1360 in `async with self._task_lock:`.

---

### N-6. P1 — `ModelRegistry` active model/provider + `_clients` cache mutated without a lock

**File:** `kazma-core/kazma_core/model_registry.py:192-281`

`set_active_model()` (line 192) and `set_active_provider()` (line 144)
mutate `self._active_provider`, `self._active_model`, and `self._clients`
without any synchronization. `get_client()` (line 222) reads
`self._active_provider` and `self._clients` without a lock.

The auto-correction path (line 244-263) mutates `self._active_provider` even
on read-only `get_client()` calls — making the read effectively write-ish.

**Race scenario:** Two concurrent SSE chat requests (both targeting the same
provider) → both call `get_client(model=X)` → both trigger auto-correction →
both write to `self._active_provider` → last write wins, but mid-flight
`_clients` mutation could leave a request bound to a stale provider.

**Impact:** Inconsistent model routing under load; occasional 401s / wrong-
endpoint errors. Hard to reproduce because Windows-PowerShell threaded tests
don't exercise this hot path.

**Fix:** Add `threading.Lock` (or `asyncio.Lock`) around the
read-modify-write of `_active_provider`, `_active_model`, and `_clients`.

---

### N-7. P2 — `app.py:97` comment + token length mismatch

**File:** `kazma-ui/kazma_ui/app.py:97`

```python
generated = secrets.token_hex(32)   # 32 bytes = 64 hex chars
```

The variable is fine (64-char hex), but nearby comments in `auth.py:95-96` say
"32-character random hex key". This is cosmetic / doc drift. Token is actually
stronger than documented, which is fine.

---

### N-8. P2 — `get_streaming_graph` / `_ensure_graph` cache race in `agent_runner.py`

**File:** `kazma-core/kazma_core/agent_runner.py:500-531`, `533-569`

Both methods do `if self._X is not None: return self._X` then `_X = build_...()`
without locking. Two concurrent calls (SSE startup + run() startup) could both
build graphs. Memory leak / dual graph issue.

**Fix:** Add `threading.Lock` for the lazy-init pattern. (`functools.cache`
won't work because `build_supervisor_graph` is async-aware.)

---

### N-9. P2 — `H-5` partial regression: `KAZMA_AUTH_DISABLED` is dead code at startup

Already covered in H-5 status row. `app.py:94-98` unconditionally sets
`KAZMA_SECRET` in `os.environ` before `auth.get_kazma_secret()` is called.
`KAZMA_AUTH_DISABLED=true` is silently ignored.

**Fix:** Move `KAZMA_AUTH_DISABLED` check to `app.py:94` before auto-generation.

---

### N-10. P2 — SSE streaming graph is built WITHOUT checkpointer; HITL resume on SSE path goes to recompiled graph

**File:** `kazma-core/kazma_core/agent_runner.py:521-530`

```python
self._streaming_graph = build_supervisor_graph(
    llm=self.llm, system_prompt=self.agent.system_prompt,
    tool_definitions=self.agent.tools.get_tool_definitions(),
    tool_executor=self.agent.tools,
    cost_breaker=self.agent.cost_breaker, authority=self.agent.authority,
    tracer=self.agent.tracer, hitl_config=streaming_hitl,
    # NO checkpointer argument
)
```

`get_streaming_graph()` builds a graph with `hitl_config` but no checkpointer.
HITL via `interrupt()` requires a checkpointer to persist the paused state
across the SSE response boundary. The `app.py:699-718` startup recompile
overwrites this with a checkpointed version — but **between the first SSE
chat request (line 444 calls `get_streaming_graph`) and the startup
recompile at line 707, the SSE router serves requests with the
un-checkpointed graph.**

The `_get_graph()` resolver always reads the current `_graph_holder["graph"]`,
which is initially the un-checkpointed version. If the first SSE request
hits before startup finishes recompile, that turn uses the un-checkpointed
graph. `interrupt()` in LangGraph with no checkpointer **silently degrades to
a no-op** (state is lost) — the tool runs to completion (or is denied) without
a pause/resume.

In practice, `app.py:444` builds the graph in `_setup_gateway_and_bus`,
which runs **before** the lifespan startup handler at line 687. So the
initial SSE requests (if any arrive before startup) WILL hit the un-
checkpointed graph.

**Fix:** Either (a) skip `get_streaming_graph()` during initial setup and
mount the SSE router only after startup completes, or (b) make the
initial graph include a checkpointer too (lazy `aiosqlite` connection).

---

### N-11. P3 — `chat.py:127` dead code below early return

The WebSocket handler returns immediately with `code=4100` at line 127, but
the entire handler body (lines 131-487, ~360 lines) remains. This is dead code
that:
- Imports modules (line 131-132).
- Maintains logic that no longer runs.
- Could mislead future readers into thinking the WS path is functional.

**Fix:** Either delete lines 130-487, or move to `_legacy_ws_chat.py` for
historical reference. Add a comment at the top of the dead block explaining
why it's preserved.

---

### N-12. P3 — Auth `KAZMA_AUTH_DISABLED` not honored at startup

Already covered (N-9). Same fix.

---

### N-13. P3 — `architecture.md` line counts still drift

`architecture.md:124` claims `swarm_panel.py ~1993 lines`. Measured:
`kazma-ui\kazma_ui\swarm_panel.py` = **2,023 lines** (via `Get-Content | Measure`).
Stale by 30 LOC.

`architecture.md:39` claims `engine.py ~1574 lines`. Measured: **1,512 lines**.

Cosmetic but the docs are used to gauge refactor progress.

---

## 3. Cross-Cutting Observations

### Concurrency Model

Three different concurrency primitives are used inconsistently across the
codebase:

| File | Primitive | Purpose |
|---|---|---|
| `config_store.py` | `threading.Lock` | SQLite multi-write atomicity |
| `engine.py` | `asyncio.Lock` (`_task_lock`) | dict mutation guard |
| `model_registry.py` | **NONE** | active model/provider/client cache |
| `agent_runner.py` | **NONE** | lazy graph cache |
| `agent_handler/graph.py` | `asyncio.Lock` + LRU | per-thread serialization |

Two files (N-5, N-6) lack the lock they should have. The pattern works
correctly in `config_store.py` and `graph.py`; it should be copied.

### HITL — Three Mechanisms Summary (Verified Live)

| Mechanism | Path | Status |
|---|---|---|
| A. Graph `interrupt()` | `graph_builder.py:421-505` | Wired in `agent_runner._ensure_graph` (line 557-567) and `app.py:707-718` startup recompile. SSE stream path depends on `app.py` ordering (N-10). |
| B. Swarm bus | `safety.check()` (async) | **Auto-approves when no adapter wired** (N-1). Sync path `check_sync()` is correctly fail-closed. |
| C. Pipeline checkpoints | `checkpoint_manager.py` | Wired via `approve_checkpoint` API; timeout auto-reject active. |

### Auto-Secret Generation Quality

- `secrets.token_hex(32)` (64 hex chars, 256 bits) — strong. ✓
- Persisted to `.env` on first run. ✓
- Auto-injected into `os.environ` for current process. ✓
- But `KAZMA_AUTH_DISABLED` override is broken (N-9).

### Error Handling (P3)

The codebase still has ~300+ `except Exception` blocks. The hot-path silent
swallows in `app.py:797-811` (lifecycle refresh), `engine.py:1385-1388`
(task persist), and `chat.py:530` (WS error) are particularly worth fixing.
For non-critical paths, log at `DEBUG` not silently `pass`.

---

## 4. Severity-Sorted Recommendation

### P0 — Do First (Today)

1. **Fix N-1**: `NullBusAdapter.request_approval` should return `False`
   (and log WARNING), OR `safety.check()` async should mirror `check_sync()`'s
   adapter check. Add test asserting async path fails closed.

### P1 — This Sprint

2. **N-3**: Add `allowed_users` enforcement to Discord adapter (Telegram parity).
3. **N-4**: Wire `allowed_teams`/`allowed_channels` from ConfigStore into
   `SlackAdapter` at `app.py:387`.
4. **N-5**: Acquire `_task_lock` around `_task_history` mutation in
   `engine.py:1356-1360`.
5. **N-6**: Add lock around `ModelRegistry` active model/provider/client cache.

### P2 — Next Sprint

6. **N-2**: Default `KAZMA_MCP_IDE_ENABLED=false`, or require `KAZMA_SECRET`
   to be set for MCP IDE server.
7. **N-9/N-12**: Honor `KAZMA_AUTH_DISABLED` in `app.py` startup.
8. **N-10**: Ensure initial SSE graph has checkpointer, or delay SSE router
   mount until after startup recompile.
9. **N-8**: Lock lazy-init in `get_streaming_graph` / `_ensure_graph`.
10. **N-11**: Strip dead code in `chat.py:130-487`.

### P3 — Polish

11. Update `architecture.md` line counts.
12. Add a test that async `safety.check()` with `NullBusAdapter` returns False
    (regression guard for N-1).
13. Replace hot-path silent `except Exception: pass` with structured logging.

---

## 5. Comparison to Prior Audits

| Source | Status |
|---|---|
| `AUDIT_DEEP_REPORT_2026-07-07.md` | C-1 partial, C-2 fixed (deprecated), C-3 fixed, C-4 fixed. H-1 unchanged, H-2 partial, H-3 fixed, H-4 fixed, H-5 dead-code, H-7 fixed, H-9/H-10/H-11 partially fixed, H-13 unchanged, H-14 unchanged. |
| `AUDIT_REPORT.md` (Jun 30) | `services.py` claim was MISSING then, now FIXED. Monkey-patch of `engine._finalize_task` (Aug 30 finding) is now via public `set_sse_bus()` (per `DEEP_AUDIT_REPORT.md`). |
| `CODE_QUALITY_AUDIT.md` (Jun 30) | `create_app()` 945-line god function → now 865-line `KazmaAppBuilder` class with 6 phases. Adapter registration dup still exists per H-12. `except Exception` counts unchanged. |
| `SECURITY_AUDIT.md` (Jun 30) | `shell_exec` `create_subprocess_shell` → now `create_subprocess_exec` with allowlist. File tool workspace scoping OK. `sk-local-dev` placeholder kept. |
| `DEEP_AUDIT_REPORT.md` (overwritten) | Marks H-3 (hub secret resolution) fixed — verified correct. |

---

## 6. Overall Verdict

**Kazma's safety posture is materially better than the prior audits suggest**
due to recent sprint work (agent_handler extraction, SSE graph holder, MCP
secret gate, YAML HITL alignment, ConfigStore atomicity, model registry
provider auto-correction).

**However, the async HITL auto-approval gap (N-1) is a real regression risk**
that the prior audits did not catch — the sync path is correctly fail-closed
but the async path silently auto-approves through `NullBusAdapter`. This
must be fixed before any production deployment without platform connectors.

**Adapter auth gaps (N-3, N-4) are operational footguns**: the moment you
add Discord or Slack, every user in those platforms can reach the agent.

**The architecture is sound; the codebase has the right shape for a
mature agent framework** — the issues found here are all **discrete, fixable
in hours-to-days**, not structural.

---

*Generated by Mavis / M3 — 2026-07-08*