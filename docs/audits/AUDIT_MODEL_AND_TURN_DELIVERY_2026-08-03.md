# Audit: Model Selection + Long-Horizon Turn Delivery

**Date:** 2026-08-03  
**Scope:** Everything that can make (1) the UI model disagree with the LLM that actually runs, or (2) CoT show “Done” while the reply is missing until the user interacts.  
**Status:** Implementation in progress (2026-08-03 reliability sprint).  
**Plan:** `docs/plans/RELIABILITY_MODEL_AND_TURN_DELIVERY.md`

---

## Executive summary

These are **system-wide binding and lifecycle** problems, not a single dropdown bug.

| Theme | Smoking gun | Blast radius |
|-------|-------------|--------------|
| **Model stickiness** | Multiple writers + multiple live LLM/graph handles that do not rebind together | Web WS/SSE, Settings, Gateway (Telegram/Discord/Slack), env lock |
| **Turn delivery** | Client wall-clock 3‑minute `forceEndTurn` + WS reconnect does not push final text | Web chat (WS preferred path); poll/reload as accidental recovery |

Industry-standard fix = **one model-switch pipeline** + **one turn completion contract** + regression tests. Band-aids (raise 3m timer only, fix SSE only) will regress.

---

## Part A — Model selection

### A.1 Canonical vs actual sources of truth

| Store / handle | Role | Writers |
|----------------|------|---------|
| `ModelRegistry._active_model` / `_active_provider` | **Intended runtime SoT** | `set_active_model`, `set_active_provider`, env deserialize |
| ConfigStore `registry.active_model` | Persist registry | registry |
| ConfigStore `registry.active_chat_model` | UI/Telegram bus alias | **only** `PUT /api/settings/active_model` |
| ConfigStore / YAML `llm.model` | Legacy | Settings save, `/config model` |
| localStorage `kazma.selectedModel` | Optimistic UI | chat.js |
| Graph closed-over `llm` | **What the turn actually calls** | Only after recompile / `get_client` into graph |
| SSE mount-time `llm_provider` | Stale after first rebind | `reconfigure` only |
| Gateway handler closed-over `graph` | Platform turns | Boot / checkpointer re-register only |

### A.2 Entry points inventory

| Entry | Persist registry | `sync_active_model` | Recompile `_graph_holder` | Rebind gateway | Notes |
|-------|------------------|---------------------|---------------------------|----------------|-------|
| Chat / sidebar dropdown → `PUT …/active_model` | Yes | Yes | Yes (callback) | **No** | Fire-and-forget PUT; always returns ok |
| Settings save model → `/api/provider/switch` | Provider path | **No** | **No** | **No** | Can reconfigure with masked `***` key |
| SSE body `model` | No (intentional) | No | No | No | Mutates **orphan** `llm_provider` after rebind |
| WS body `model` | — | — | — | — | **Ignored** (primary web path) |
| Gateway `/_models_select` | Yes | **No** | **No** | **No** | User told “switched”; graph unchanged |
| Gateway `/config model` | YAML only | No | No | No | Says restart; no registry |
| Env `KAZMA_MODEL` | Boot pin | N/A | N/A | N/A | Later switches no-op |
| TUI `/model set` | Yes | N/A | N/A | N/A | Per-turn `get_client()` — OK |

### A.3 Chat turn model resolution (actual)

```
Web WS (preferred if connected)
  → graph from _graph_holder
  → llm baked into last recompile
  → payload.model IGNORED

Web SSE
  → graph from _graph_holder
  → optional reconfigure(mount-time llm_provider)  # often NOT the graph llm
  → graph still uses closed-over llm

Gateway
  → graph fixed at create_graph_handler(graph=snapshot)
  → never sees holder updates after model switch

TUI
  → registry.get_client() each turn  # healthiest path
```

### A.4 Confirmed model bugs

| ID | Sev | Bug | Evidence |
|----|-----|-----|----------|
| M-P0-1 | **P0** | SSE `llm_provider` is mount-time object; after `sync_active_model` it is orphaned | `app.py` mount; `agent_runner.sync_active_model`; `sse_chat` reconfigure |
| M-P0-2 | **P0** | Gateway graph snapshot never follows model switch | `create_graph_handler(graph=…)`; callback only updates holder |
| M-P0-3 | **P0** | `/api/provider/switch` can set live `api_key` to `"***"` | `get_active_profile` masks key → `reconfigure(api_key="***")` |
| M-P0-4 | **P0** | Env lock / switch failures still return `"status":"ok"` | `settings.py` `api_set_active_model` |
| M-P1-1 | **P1** | WS ignores `payload.model` while client always sends it | `agentStore.sendPrompt` vs `ws_chat.py` |
| M-P1-2 | **P1** | `/provider/switch` does not `sync_active_model` / recompile | `sse_chat.switch_provider` |
| M-P1-3 | **P1** | Dual keys: `active_model` vs `active_chat_model` vs `llm.model` | writers diverge |
| M-P1-4 | **P1** | Provider class change cannot be done via `reconfigure` alone | Anthropic/Gemini/Azure/Bedrock need new client class |
| M-P2-1 | **P2** | Optimistic UI + empty PUT error handling | `chat.js` `onModelChange` |
| M-P2-2 | **P2** | WS `graph_getter=lambda: self.graph` invalid; falls through by accident | `app.py` |
| M-P2-3 | **P2** | Sync log uses `getattr(llm, "model")` (always wrong) | `agent_runner.sync_active_model` |

---

## Part B — Long-horizon turn delivery

### B.1 Transport lifecycle (web)

**WS (preferred):** tools stream → optional `llm_delta` (rarely for custom LLM) → backfill if no tokens → persist → `idle` + `stream_end`.

**SSE:** pump `astream_events` + 10s keepalive → token/tool frames → post-stream backfill → `done`. Detach on disconnect; pump continues + done_callback persists.

**Gateway:** `ainvoke` then one outbound message (no 3‑min UI Done).

### B.2 Timeouts / watchdogs inventory

| Value | Where | Behavior |
|------:|-------|----------|
| **3 min** | `chat.js` `TURN_WATCHDOG_MS` | **`forceEndTurn`** → CoT **Done**, clear bubble state, **no** server cancel, **no** poller |
| 1.5 s | desync healer | Only heals chat-generating + Alpine-idle |
| 90 s | `_pollBackgroundTurn` | Gives up; “took too long” |
| 10 s | SSE queue | Keepalive comments |
| 300 s | `DETACHED_TTL_S` | Reap **orphaned** pumps only |
| 60 s | `LLMConfig.timeout` | Per HTTP LLM call |
| 100 | graph `recursion_limit` | Multi-hop ceiling |
| 600 s | agent_runner turn_timeout | Non-web runner path |

### B.3 Confirmed delivery bugs

| ID | Sev | Bug | Evidence |
|----|-----|-----|----------|
| D-P0-1 | **P0** | 3‑min wall-clock false “Done”; not reset on tool/activity | `chat.js` `TURN_WATCHDOG_MS` + `forceEndTurn` |
| D-P0-2 | **P0** | After false Done / WS drop, final text often **persist-only**; reconnect does not push completion | `ws_chat` `is_lost`; connect only HITL scan; no poller on that path |
| D-P1-1 | **P1** | Poller 90s << real long turns / 300s detached TTL | `_pollBackgroundTurn` maxAttempts=18 |
| D-P1-2 | **P1** | Custom LLM: no `on_chat_model_stream`; final answer is backfill; partial `tokens_emitted` can skip backfill | `EventBridge`, `ws_chat` `if not tokens_emitted` |
| D-P1-3 | **P1** | HITL resume uses `ainvoke` (correct); main prompt still `astream_events` (hang risk) | `ws_chat` / `sse_chat` comments |
| D-P2-1 | **P2** | `lastActivityTs` SSE-only; WS visibility recovery weaker | `chat.js` |
| D-P2-2 | **P2** | After watchdog, late frames don’t re-arm Stop / `_isGenerating` | agentStore vs chat.js desync |

### B.4 When UI is guaranteed vs only disk

| Situation | Live UI | SessionStore |
|-----------|---------|--------------|
| WS socket alive until idle | Yes | Yes |
| WS disconnect mid-turn | No | Yes (drain+persist) |
| After 3‑min watchdog | Only if late frames arrive | Yes when task completes |
| SSE held until done | Yes | Yes |
| SSE refresh mid-turn | Poll/reload | Yes (detached) |
| Gateway | Platform send at end | Web sync helper |

---

## Part C — Unified fix backlog (do once, structural)

Implement as **one PR plan** with two workstreams that share contracts.

### Workstream 1 — Model binding SoT

1. **Single switch service** used by PUT active_model, provider/switch, gateway `/_models_select`, TUI (optional):  
   `registry.set_*` → `agent.sync_active_model()` → recompile holder → update `llm` getter → **rebind gateway handler** (or handler reads holder each turn).
2. **Never** pass masked `***` into `reconfigure`; prefer `registry.get_client()` rebind for class changes.
3. SSE/WS: `llm_provider` / graph via **getter**, not mount snapshot.
4. WS: apply `payload.model` **or** document global-only and strip client field; if apply, use same rebind service (prefer await switch before send).
5. Env lock / failure → **non-ok** response; UI reverts dropdown.
6. Collapse writers: `active_chat_model` mirrors `active_model` inside the service.
7. Fix logging to `llm.config.model`; fix `graph_getter`.

### Workstream 2 — Turn completion contract

1. **Remove false success** from 3‑min watchdog: progress-based idle only, or “still running (background)” + **start poller** — never `finalizeProgress(true)` without server terminal.
2. Terminal frame carries final content (or client always upserts from `turn_complete` / messages API).
3. WS reconnect: if turn running → re-attach status; if finished since orphan → push final or force `loadSession`.
4. Always end-of-turn checkpoint backfill even if partial deltas existed.
5. Align poller with `generating` / DETACHED_TTL (backoff, no 90s hard death while generating).
6. Optional later: stream adapter or main-path `ainvoke`+heartbeat like HITL resume.

### Workstream 3 — Regression tests (lock it in)

| Test | Asserts |
|------|---------|
| PUT active_model → next WS turn | graph/`config.model` is new id |
| PUT active_model → next SSE turn | same (via holder, not orphan reconfigure) |
| After sync, SSE reconfigure target == agent.llm | identity |
| Gateway after web model switch | handler graph/llm is new model |
| `/provider/switch` never sets api_key `***` | |
| Env lock | UI gets error, not ok |
| Turn > 3 min | CoT not “Done” until idle; reply visible without resend |
| WS disconnect mid-turn → reconnect | final message appears |
| Custom LLM no stream events | backfill before terminal |

### Explicitly out of scope (unless requested)

- Multi-tenant per-user model isolation (needs per-request graph or session-scoped client)
- Full LangChain BaseChatModel migration for true token streaming
- Swarm per-worker model selection (already separate, intentional)

---

## Recommended implementation order

1. **Model switch service + gateway rebind + llm getter** (stops wrong-model class of bugs)  
2. **Client watchdog + poller + WS reconnect catch-up** (stops 3‑min Done / poke-to-deliver)  
3. **WS/SSE model parity + provider/switch key bug**  
4. **Tests for all of the above**

---

## Related files (quick index)

- `kazma-core/kazma_core/model_registry.py`, `agent_runner.py`, `llm_provider.py`, `agent/graph_builder.py`  
- `kazma-ui/kazma_ui/app.py`, `settings.py`, `sse_chat.py`, `routes/ws_chat.py`, `active_turns.py`  
- `kazma-ui/kazma_ui/static/js/chat.js`, `stores/agentStore.js`, `streaming.js`, `modules/components.js`  
- `kazma-gateway/.../agent_handler/graph.py`, `commands.py`  
- Existing partial tests: `tests/test_model_selection_pipeline.py`, `test_model_registry.py`, `test_active_turns.py`, `test_hitl_wiring.py`

---

## Conclusion

Auditing “everything related” confirms **~12 actionable defects** in two tightly coupled systems. Fixing only the dropdown or only the 3‑minute timer would leave Gateway, Settings switch, orphaned SSE provider, and reconnect delivery broken.

A structural one-time fix is realistic if the backlog above is completed with tests; partial fixes will re-open the same symptoms under the next transport or model class change.
