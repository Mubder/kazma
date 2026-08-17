# Multi-Session Architecture Plan

**Status:** Phase 4 list/switch + take-over shipped 2026-08-17 (`/sessions`,
`/session n`, directory in `kazma_core/sessions/`). Native Telegram topics /
Discord threads (Phase 1 platform mapping) still later. Per-session model /
cost / workspace (Phases 2–3) still later.
**Date:** 2026-08-15 (deep audit session); take-over commands 2026-08-17
**Trigger:** User requested industry-level non-conflictable multi-session design.

---

## Executive Summary

Kazma is already ~80% multi-session capable. The checkpointer, memory engine,
HITL approval system, turn management, and web session store are all
thread-keyed and would survive N concurrent sessions today. The single
load-bearing invariant blocking multi-session is the **`active_thread.{sender_id}`
pointer** — one ConfigStore key plus an in-memory mirror that forces every
inbound message from a user into exactly one thread.

The redesign is NOT a rewrite — it's removing one bottleneck and adding a
thread-selection layer at message intake, plus scoping three pieces of
process-global state to threads.

---

## What Already Works with N Sessions (No Changes Needed)

| Subsystem | Why It's Safe | Evidence |
|-----------|---------------|----------|
| Checkpointer | Per-thread checkpoint chains, per-thread asyncio.Lock (LRU 10k), per-tenant SQLite/Postgres | `CheckpointManager._locks` |
| Memory (V2) | Episodes carry session_id provenance; recall biases same-session (+0.35 RRF); working tier per-session | `recall.py:1237-1276` |
| HITL approvals | Thread-scoped interrupts; cards embed thread_id; cross-thread resume fail-closed; watchdog independent per thread | `hitl.py:265-303` |
| Turn management | `_thread_locks` per-thread; `active_turns._turns` per-thread; YOLO/steer/long-task all thread-keyed | `graph.py:321-355` |
| Web session sidebar | Full CRUD: list/switch/new/search/pin/archive/rename/delete; gw-* threads appear in sidebar | `chat.js:2864-3695` |
| SessionManager store | SQLite/Postgres, PK tenant+session, LRU 10k, per-session mutation locks | `session_manager.py:172-234` |

---

## The Four Things That Break with N Sessions

### 1. The `active_thread` pointer (PRIMARY BOTTLENECK)

**Where:** `store.py:74-83` (ConfigStore `active_thread.{sender_id}`) +
`graph.py:311` (`_sessions: OrderedDict[sender_id, thread_id]`)

Every inbound message resolves through this single pointer. `/new` mints a
new thread and repoints it, but there's no way to go back to a previous
thread or choose which thread a message belongs to. `/fork` creates a thread
orphaned from the platform (only reachable via Web UI).

Discord is worse: identity is per-channel (`discord:{channel_id}`), so every
user in a channel shares one thread, one HITL history, one lock.

### 2. Process-global model selector

**Where:** `app.py:1473-1493` `_recompile_holder_graph()` — one graph, one
LLM binding, process-wide. Switching models in session A changes session B's.

### 3. Process-global cost breaker + IDE workspace

**Where:** `agent_runner.py:191` (cost_breaker at `graph.py:432-450`) and
IDE workspace root (`workspace.binding.resolve_active_root()`). The IDE has
`workspace_scope.py` for swarm tasks but NOT for chat sessions.

### 4. Typing indicator + swarm attribution

**Where:** `graph.py:379-391` typing keyed by chat target (not thread);
`swarm_dispatch.py` records `source_thread_id` in metadata but nothing reads
it — results go to the chat unlabeled, interleaved across sessions.

---

## The Industry-Level Non-Conflictable Solution

### Architecture principle: "Thread selection at intake, everything else already works"

Industry pattern (ChatGPT conversations, Claude projects, Slack threads):
separate **session identity** from **delivery channel**. A user has N
sessions; each message explicitly or implicitly selects one; results are
delivered to the chat but attributed to the session.

### Phase 1 — Thread selection at intake (core change)

**Telegram:** Map each Kazma session to a Telegram **forum topic**.
`/new` creates a new topic; messages in that topic route to that thread;
bot replies sent as topic messages. Fallback for non-forum chats:
**reply-to threading** (reply to a bot message → route to that thread;
bare messages → active thread).

**Discord:** Map each Kazma session to a **Discord thread**. `/new` creates
a thread in the channel; messages in that thread route to that thread.

**Slack:** Map to **Slack threads** (reply threading).

**Web:** Already works — the sidebar IS the session selector.

**Implementation sketch:**
```python
# New field in IncomingMessage context_metadata:
msg.context_metadata["thread_hint"] = topic_id | reply_to_id | discord_thread_id

# In graph.py handler, BEFORE the active_thread lookup:
thread_id = resolve_thread_from_hint(msg) or _sessions.get(sender_id) or active_thread...
```

The `active_thread` pointer stays as the **default** when no hint is present
— backward compatible.

### Phase 2 — Per-session model & cost

- **Model per session:** Store `model` in the ChatSession row (metadata
  field already exists). Pass per-invocation:
  `graph.ainvoke(..., config={"configurable": {"model": session.model}})`.
  The graph already supports per-call LLM injection via `state.get("_llm")`.
- **Cost breaker per session:** Change from process-global to
  `dict[thread_id, CostCircuitBreaker]` with per-thread budgets + global
  ceiling. The `should_halt()` check (`graph.py:432-450`) reads the
  thread's breaker.

### Phase 3 — Per-session workspace & result attribution

- **IDE workspace per session:** Reuse existing `workspace_scope.py`
  ContextVar — wrap the gateway handler in `workspace_scope(session.workspace_id)`
  when the ChatSession carries one.
- **Swarm result attribution:** Add thread_id to reply prefix
  (`[Session: <name>]`); read `source_thread_id` from task metadata to
  route to the correct platform thread/topic.
- **Typing indicator per thread:** Key by thread_id instead of target_id.

### Phase 4 — Session management UX on platforms

- `/sessions` — list active sessions with names and status
- `/switch <n>` or topic-selection — select active session for bare messages
- `/new <name>` — named sessions (stored in ChatSession.title)
- Session names in `/status` and swarm result attribution

---

## Why This Is Non-Conflictable

| Risk | Why It's Safe |
|------|---------------|
| Two sessions writing same checkpointer | Per-thread locks already exist |
| HITL approval confusion | Already thread-scoped; cards embed thread_id |
| Memory corruption | Memory is per-tenant (intended); working tier per-session |
| Cost overrun across N sessions | Phase 2 scopes breaker per-thread + global ceiling |
| Model confusion | Phase 2 scopes model per-session; graph supports per-call LLM |
| Platform API conflicts | Uses each platform's NATIVE threading (topics/threads) |
| Backward compatibility | active_thread stays as default when no hint present |
| Web UI | Already multi-session; no Phase 1 changes needed |

---

## Effort Estimate

| Phase | Scope | Estimate |
|-------|-------|----------|
| 1 | Thread hints + Telegram topics + Discord threads + /sessions /switch | ~2-3 days |
| 2 | Per-session model + cost breaker | ~1 day |
| 3 | Per-session workspace + swarm attribution + typing | ~1 day |
| 4 | Polish: session names, status display, edge cases | ~1 day |

**Total: ~5-6 focused days.** No rewrite, no schema migration (ChatSession
already has the fields), backward compatible from day one.

---

## Key Files to Touch (When Implementing)

| File | Change |
|------|--------|
| `kazma-gateway/.../store.py` | Add thread_hint resolution before active_thread fallback |
| `kazma-gateway/.../graph.py` | Read thread_hint from msg context; add /sessions /switch commands |
| `kazma-gateway/.../gateway.py` | Pass platform-native thread/topic ID as thread_hint |
| `kazma-gateway/.../adapters/telegram.py` | Topic creation on /new; topic-aware routing; reply-to resolution |
| `kazma-gateway/.../adapters/discord.py` | Thread creation on /new; thread-aware routing |
| `kazma-gateway/.../adapters/slack.py` | Thread-timestamp routing |
| `kazma-ui/.../app.py` | Per-session model injection (remove global recompile) |
| `kazma-core/.../agent_runner.py` | Cost breaker → per-thread dict |
| `kazma-gateway/.../swarm_dispatch.py` | Read source_thread_id for attribution |
| `kazma-core/.../workspace/workspace_scope.py` | Wrap gateway handler per-session |
