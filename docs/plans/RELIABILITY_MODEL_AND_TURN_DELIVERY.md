# Plan: Model Stickiness + Long-Horizon Delivery + Industry-Grade Agent Reliability

**Plan ID:** `4b025a6e`  
**Date:** 2026-08-03  
**Audit SoT:** `docs/audits/AUDIT_MODEL_AND_TURN_DELIVERY_2026-08-03.md`  
**Goal:** One structural reliability pass that (1) makes model selection and turn delivery correct on every transport, and (2) raises the web/gateway chat stack to industry-grade reliability (SoT, completion contract, reconnect, observability, tests).

---

## Problem statement

### Bug 1 — Model dropdown lies
UI shows the new model (e.g. DeepSeek v4 Flash) while turns still call the old one (e.g. Pro). Root: multiple writers + multiple live LLM/graph handles that do not rebind together. Preferred web path is WebSocket, which **ignores** `payload.model`; Gateway closes over a graph snapshot; SSE `llm_provider` is a mount-time orphan after rebind; Settings `/api/provider/switch` can wipe the live key to `"***"`.

### Bug 2 — Long-horizon “Done” without a reply
CoT shows **Done ~3 minutes** while the server is still working; reply often only appears after the user types/interacts. Root: client wall-clock `TURN_WATCHDOG_MS = 3 * 60 * 1000` → `forceEndTurn()` (false success, no poller); WS reconnect does not push completion; final text is SessionStore-durable but not always live-delivered.

### Industry gap (in scope for this plan)
Not “every AI feature in existence,” but every **reliability foundation** adjacent to these bugs that an industry-grade multi-transport agent needs: single model-switch pipeline, turn completion contract, reconnect/catch-up, turn telemetry, honest errors, regression tests.

**Out of scope (later sprints):** multi-tenant per-user model isolation, full LangChain BaseChatModel streaming migration, new model providers, memory/swarm feature work.

---

## Design principles (Key Decisions)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| **KD-1 Model SoT** | `ModelRegistry` (`active_provider` + `active_model`) is the only runtime SoT. All switch entry points call one service. | Stops dual keys and half-applied switches. |
| **KD-2 Live graph/LLM** | Always resolve via **holder/getter**, never mount-time object snapshots. Gateway handler reads holder each turn (or is re-registered on recompile). | Closes orphan `llm_provider` and stale gateway graph. |
| **KD-3 Switch = rebind** | `set_active_*` → `agent.sync_active_model()` → recompile holder → rebind gateway → mirror `registry.active_chat_model`. Prefer `get_client()` over `reconfigure` when provider class may change. Never pass masked `"***"` keys. | Provider class branches (Anthropic/Gemini/Azure/Bedrock) cannot be reconfigured in place. |
| **KD-4 Per-request model (Web)** | Global active model is authoritative after successful switch. WS/SSE may accept body `model` only as **ensure-active** (if differs, call switch service before turn, or reject with clear error). Single-operator default: await switch before send; no silent global hijack mid-turn for multi-user later. | Matches current product (one process-wide agent) without encoding multi-tenant lies. |
| **KD-5 Turn terminal** | Client marks CoT **Done** only on server terminal (`idle` / `stream_end` / `done` / explicit Stop / hard error). Never on wall-clock alone. | Fixes 3‑minute false Done. |
| **KD-6 Durable + live projection** | SessionStore + checkpointer are durable truth; live socket is projection. Any detach (watchdog unlock, WS close, refresh) starts catch-up (poll or reconnect replay). | Industry pattern: Slack/OpenAI-style durable turn + reconnect. |
| **KD-7 Custom LLM** | Keep httpx `LLMProvider`. End-of-turn **always** checkpoint-backfill full final text (even if partial deltas). Prefer `ainvoke`+heartbeat for HITL (already); keep `astream_events` for main path but harden backfill. Full stream adapter is optional later. | Minimal risk; fixes delivery without rewriting LLM stack. |
| **KD-8 Env lock honesty** | If `KAZMA_MODEL`/`KAZMA_PROVIDER` set, switch APIs return **error**, UI reverts. | Demo/prod pins must not look successful. |

---

## Architecture

### A. Model switch service (new)

**Location (preferred):** `kazma-core/kazma_core/runtime/model_switch.py`  
(or `kazma_ui` façade that calls core + app hooks if core must stay free of gateway)

```
switch_active_model(model: str, *, provider: str | None = None) -> SwitchResult
switch_active_provider(...) -> SwitchResult

SwitchResult:
  ok: bool
  model: str
  provider: str
  error: str | None   # e.g. env_locked, not_found, recompile_failed
```

**Pipeline (atomic best-effort, single process):**

1. If env locked → return error (do not mutate).
2. `registry.set_active_model` / `set_active_provider` (real keys only from provider store).
3. Mirror `registry.active_chat_model` = active model.
4. `agent.sync_active_model()` (new client, clear agent graph caches, fire callback).
5. Callback / app hook:
   - `build_supervisor_graph(llm=agent.llm, …, checkpointer, hitl, recorder)`
   - `_graph_holder["graph"] = recompiled`
   - `_hitl_state["graph"] = recompiled`
   - **Rebind gateway:** either `create_graph_handler` with **getter** `lambda: holder["graph"]`, or re-register handler each recompile.
6. Log: `provider`, `model` from `agent.llm.config.model` (fix M-P2-3).

**Call sites migrate to this service:**

| Current entry | Change |
|---------------|--------|
| `PUT /api/settings/active_model` | Call service; return non-ok on failure |
| `POST /api/provider/switch` | Call service (no masked reconfigure) |
| Gateway `/_models_select` | Call service (or HTTP internal equivalent via shared app hook) |
| Gateway `/config model` | Prefer service + registry (YAML optional secondary) |
| TUI `/model set` | Call service if agent in-process; else registry + note |
| Chat/sidebar UI | `await` PUT; revert on error |

**SSE LLM binding:**

```python
# app.py mount
create_sse_chat_router(
  ...
  llm_provider_getter=lambda: self.agent.llm,  # not self.agent.llm snapshot
  graph_holder=self._graph_holder,
)
```

Inside SSE: resolve `llm = llm_provider_getter()` each request before reconfigure/validation. Prefer: if body model differs from active, call switch service (single-op) or reconfigure **only the current agent.llm** then ensure graph was rebuilt.

**WS model field:**

- Before `ainvoke`/`astream_events`: if `payload.model` and differs from registry active → call switch service (or 409 “switch model first”). Preferred for UX: switch then run (same as dropdown await).
- Always log `active_model` on turn start.

**Gateway graph:**

```python
# Prefer holder read per turn
def create_graph_handler(graph_getter: Callable[[], Any], ...):
    async def handler(msg):
        graph = graph_getter()
        await graph.ainvoke(...)
```

Re-register once at boot with `graph_getter=lambda: self._graph_holder.get("graph")`.

### B. Turn completion contract

**Server terminal envelope (both WS and SSE):**

```json
{
  "type": "turn_complete",
  "thread_id": "...",
  "session_id": "...",
  "content": "<final assistant text or empty>",
  "interrupted": false,
  "empty": false,
  "model": "deepseek-v4-flash",
  "tokens": 0,
  "cost": 0.0,
  "duration_ms": 0
}
```

- Emit **after** persist; include content so client does not race 0.1s sleeps.
- Keep legacy `idle` / `stream_end` / `done` for compatibility; agentStore/chat treat `turn_complete` as authoritative for content + unlock.

**Client rules (`chat.js` + `agentStore.js`):**

1. Remove wall-clock success path: either delete 3‑min `forceEndTurn` success, or convert to **idle-progress watchdog** (no tool/status/token for N minutes → banner “still working?” + start poller, **do not** `finalizeProgress(true)`).
2. On any unlock without terminal content: `_pollBackgroundTurn` while `GET …/status.generating` or last assistant `pending`.
3. Poller: backoff 2s→10s; while `generating` true, **no hard 90s death** (or cap ≥ DETACHED_TTL); when generating false + empty local → `loadSession`.
4. On `turn_complete` / final `llm_delta`+idle: upsert assistant bubble, then `finalizeProgress`, then unlock.
5. WS `onclose` mid-turn: unlock Stop with “reconnecting / catching up” + poller (not silent Done).

**WS reconnect (`ws_chat.py` connect):**

1. Existing HITL scan (keep).
2. If `get_active_turn(thread_id)` running → emit `status: thinking` + optional pending content from SessionStore.
3. If not generating and last assistant pending/empty in store → emit `turn_complete` with persisted content or instruct client `loadSession`.

**Backfill harden:**

- Always run checkpoint backfill at end; if checkpoint text longer than accum, emit full text once.
- Do not skip backfill solely because `tokens_emitted` was true with empty/partial content.

### C. Industry-grade additions (this plan)

| Addition | What | Where |
|----------|------|--------|
| **Turn telemetry log line** | One structured log per turn start/end: `thread_id`, `session_id`, `model`, `provider`, `duration_ms`, `tokens`, `interrupted`, `transport` | `sse_chat`, `ws_chat`, gateway handler |
| **Turn model stamp in UI meta** | Show model used on assistant meta (from `turn_complete.model` / `last_model`) | `chat.js`, session message optional field |
| **Dashboard/health snippet** | Active model + provider from registry on health/dashboard | existing health routes |
| **Switch API contract** | Documented error codes: `env_locked`, `invalid_model`, `rebind_failed` | OpenAPI/docstring + tests |
| **E2E regression suite** | See Tests section | `tests/` |
| **CHANGELOG + audit status** | Mark audit implemented | `CHANGELOG.md`, audit header |

Optional stretch (if time after P0/P1):

- Heartbeat status every 15s on WS long turns (`status: thinking`, elapsed).
- SSE `status_update` wired into chat CoT (parity with WS).

---

## Implementation phases (PR plan)

### PR1 — Model switch service + rebind (P0 model)

**Title:** `fix(model): single switch pipeline + holder/getter rebind`

**Files:**
- NEW `kazma-core/kazma_core/runtime/model_switch.py` (or app-level `kazma_ui/model_switch.py` if gateway hooks need app)
- `kazma-core/kazma_core/agent_runner.py` — fix model log attr; ensure callback always fires
- `kazma-ui/kazma_ui/app.py` — holder recompile; gateway `graph_getter`; fix WS `graph_getter`; pass `llm_provider_getter`
- `kazma-ui/kazma_ui/settings.py` — use service; honest errors
- `kazma-ui/kazma_ui/sse_chat.py` — getter; provider/switch via service (no `***` key)
- `kazma-gateway/.../agent_handler/graph.py` — `graph_getter` per turn
- `kazma-gateway/.../agent_handler/commands.py` — `/_models_select` → service/hook
- Tests: registry switch + graph identity; no masked key; env_locked error

**Deps:** none  
**Closes:** M-P0-1, M-P0-2, M-P0-3, M-P0-4, M-P1-2, M-P1-3 (mirror), M-P1-4, M-P2-2, M-P2-3

---

### PR2 — Web UI model honesty + WS apply (P1 model)

**Title:** `fix(ui): await model switch; WS ensure-active model`

**Files:**
- `static/js/chat.js`, `modules/components.js` — await PUT; revert + toast on error
- `routes/ws_chat.py` — read `payload.model`; ensure active via service before turn
- `sse_chat.py` — body model: rebind current agent.llm **or** ensure-active service (same rules)
- Tests: static + integration where possible

**Deps:** PR1  
**Closes:** M-P1-1, M-P2-1

---

### PR3 — Turn completion + kill false Done (P0 delivery)

**Title:** `fix(chat): turn_complete contract; remove false 3m Done`

**Files:**
- `static/js/chat.js` — watchdog rewrite; poller; finalize only on terminal
- `static/js/stores/agentStore.js` — handle `turn_complete`; reconnect mid-turn behavior
- `static/js/streaming.js` — optional `turn_complete` event
- `sse_chat.py` — emit `turn_complete` (or enrich `done` with `content`+`model`); always full backfill
- `routes/ws_chat.py` — emit `turn_complete`; always backfill; reconnect catch-up
- `active_turns.py` — helpers if needed for “is generating” on connect
- Tests: JS contract via source assertions + Python backfill tests; document manual long-turn check

**Deps:** none (can parallel PR1) but ship after/with PR1 for clean logs  
**Closes:** D-P0-1, D-P0-2, D-P1-1, D-P1-2, D-P2-1, D-P2-2

---

### PR4 — Industry telemetry + polish

**Title:** `feat(reliability): turn telemetry, model stamp, reconnect UX`

**Files:**
- sse/ws/gateway structured turn logs
- assistant message `model` field in SessionStore when available
- chat meta display
- health/dashboard active model
- reconnect banner strings (i18n if needed)
- `CHANGELOG.md`, audit status update

**Deps:** PR1–PR3  
**Closes:** industry observability slice

---

### PR5 — Regression test pack (lock forever)

**Title:** `test(reliability): model bind + long-turn delivery regressions`

**Tests (minimum):**

| Test | Assert |
|------|--------|
| `test_model_switch_rebinds_graph_llm` | After switch, holder graph’s llm.config.model == new |
| `test_model_switch_rebinds_gateway_getter` | Handler invokes current holder graph |
| `test_provider_switch_never_masks_key` | reconfigure/get_client never sees `***` |
| `test_active_model_env_locked_errors` | API status error, model unchanged |
| `test_ws_payload_model_ensures_active` | Differing model triggers switch or consistent graph model |
| `test_sse_llm_getter_not_orphan` | After sync, SSE resolves same object as agent.llm |
| `test_turn_complete_includes_content` | Terminal frame has content from backfill |
| `test_backfill_even_if_partial_tokens` | Full checkpoint wins |
| `test_chat_js_no_wall_clock_force_done` | Source: no `forceEndTurn` on timer without poller / no finalize true on watchdog alone |
| `test_poller_respects_generating` | Logic/unit for poll stop conditions |

**Deps:** PR1–PR4  
**Closes:** audit “missing tests” section

---

## Detailed task checklist (implementation order)

### Phase 0 — Prep
- [ ] Re-read audit + this plan
- [ ] Branch `fix/reliability-model-and-turns` (or stacked PR branches)
- [ ] Note: PowerShell uses `;` not `&&`

### Phase 1 — Core switch + rebind (PR1)
- [ ] Add `switch_active_model` / `switch_active_provider` with `SwitchResult`
- [ ] Wire settings PUT + provider switch
- [ ] App callback: recompile + gateway getter
- [ ] SSE `llm_provider_getter`
- [ ] Fix gateway `create_graph_handler` to use getter
- [ ] Fix `/_models_select`
- [ ] Fix log `config.model`
- [ ] py_compile + unit tests

### Phase 2 — UI + WS model (PR2)
- [ ] Await model PUT in chat + sidebar; toast + revert
- [ ] WS ensure-active model
- [ ] SSE body model aligned with service
- [ ] Manual: switch Flash/Pro, send on WS, log shows new model

### Phase 3 — Delivery (PR3)
- [ ] Replace 3m false Done
- [ ] `turn_complete` server emit (WS + SSE)
- [ ] Client handlers
- [ ] Poller while generating
- [ ] WS reconnect catch-up
- [ ] Full backfill always
- [ ] Manual: multi-tool turn >3 min, reply visible without typing

### Phase 4 — Industry extras (PR4)
- [ ] Structured turn logs (model, transport, duration)
- [ ] Model on message meta
- [ ] Health/dashboard active model
- [ ] CHANGELOG + audit status

### Phase 5 — Tests (PR5)
- [ ] All regression tests green
- [ ] `pytest` targeted suites

### Phase 6 — Verify
- [ ] Restart server
- [ ] Web: model switch + chat log identity
- [ ] Web: long turn / tab background / reconnect
- [ ] Telegram (if configured): model switch from web affects gateway
- [ ] Env lock smoke if applicable

---

## Risk register

| Risk | Mitigation |
|------|------------|
| Recompile drops in-flight HITL | Do not switch mid-HITL without warning; or queue switch until idle |
| Multi-user global model | Document single-operator; future session-scoped model |
| Breaking SSE clients | Keep `done`/`idle`; add fields; additive `turn_complete` |
| Gateway handler signature change | Support `graph=` and `graph_getter=` for compat |
| Test flakiness on timing | Prefer unit/identity tests over real 3m waits |

---

## Success criteria

1. **Model:** After selecting Flash in UI, next Web WS turn, SSE turn, and Gateway turn all call Flash (logs + API response model).
2. **Honesty:** Failed/env-locked switch does not leave UI on the failed target.
3. **Delivery:** Turn >3 minutes does not show CoT “Done” until server terminal; final text appears without user resend.
4. **Reconnect:** Refresh/WS drop mid-turn → catch-up shows final reply within poll window.
5. **No key wipe:** Provider switch never sets live key to `***`.
6. **Tests:** PR5 suite green in CI/local.

---

## What “industry-grade” means after this plan (honest)

**In:** correct model binding multi-transport; durable turn completion; reconnect/poll; turn telemetry; regression suite; honest env lock.

**Still later:** per-user models (SaaS), true token streaming adapter, full product eval harness, cost SLOs UI, etc.

This plan is the **Reliability Foundation** sprint. It is the right first industry-grade tranche; not the entire product roadmap.

---

## References

- Audit: `docs/audits/AUDIT_MODEL_AND_TURN_DELIVERY_2026-08-03.md`
- Critical files: `model_registry.py`, `agent_runner.py`, `app.py`, `sse_chat.py`, `ws_chat.py`, `chat.js`, `agentStore.js`, `agent_handler/graph.py`, `commands.py`, `settings.py`, `active_turns.py`, `tracing/events.py`
- Agents.md §1 (provider/model resolution), §7 HITL (do not break gates while rebinding)

---

## Open questions (defaults if user silent)

| Q | Default for implementation |
|---|----------------------------|
| Per-request body model when it differs from active? | **Ensure-active** (switch then run) for single-operator UX |
| Keep 3m timer at all? | **Progress-idle only** + poller; no false Done |
| Where to put switch service? | Core if pure; **UI `model_bind.py` + core registry** if gateway rebind needs app holder |
| Multi-tenant later? | Document; do not implement in this sprint |
