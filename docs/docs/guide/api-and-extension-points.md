---
id: api-and-extension-points
title: API & Extension Points
sidebar_label: API & Extension Points
description: Kazma API & Extension Points — code-audited reference (unified docs, v0.9+)
---
> The HTTP/SSE surface of the Kazma Web UI, the SSE event contract, and the concrete places to extend the framework (tools, providers, adapters, skills, MCP).

---

## 1. HTTP API surface

All endpoints are mounted by `KazmaAppBuilder` in `kazma-ui/kazma_ui/app.py:615-709`. Routers:

| Router | Prefix/area | Source |
|---|---|---|
| `health_router` | `/health/*` | `health.py` |
| `chat_router` | page routes (`/chat`, …) | `chat.py` |
| `settings_router` | `/settings` | `settings.py` |
| `skills_router` | skills | skills routes |
| `mcp_router` | MCP | mcp routes |
| `agents_router` | agents | agents routes |
| `providers_router` | `/api/providers` | providers routes |
| `sse_router` | `/api/chat/*` | `sse_chat/` |
| `telemetry_router` | telemetry | telemetry routes |
| `dashboard_router` | `/api/dashboard/*` | `dashboard.py` |
| `models_router` | models | models routes |
| `workspace_router` | workspace | workspace routes |
| `swarm_router` | `/api/swarm/*` | `swarm_panel/` |
| `monitor_router` | monitor | monitor routes |
| `metrics_router` | metrics | metrics routes |

Plus direct routes in `routes_direct/` and a conditional Telegram webhook at `/api/webhooks/telegram` (`app.py`).

---

## 2. Key endpoints (verified)

### 2.1 Chat (SSE)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat/stream` | Primary chat transport. Body `\{message, session_id, model\}`. Returns `text/event-stream`. (`sse_chat/__init__.py`) |
| `GET` | `/api/chat/sessions` | List sessions. (line 547) |
| `POST` | `/api/chat/sessions` | Bind an empty session shell to the authenticated caller before opening its telemetry socket. Requires CSRF protection. |
| `DELETE` | `/api/chat/sessions/\{session_id\}` | Delete session. (line 555) |
| `GET` | `/api/chat/sessions/\{session_id\}/messages` | Session history. (line 561) |

> **Graph transport:** `POST /api/chat/stream`. `/ws/chat/{session_id}` is the telemetry / cursor bus: it runs no turn and takes no turn control (send, approve, stop, steer and abort are HTTP/SSE; the socket refuses them, naming the route).

### 2.2 Providers

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/provider/active` | Active provider/model. |
| `POST` | `/api/provider/switch` | Switch active provider/model. |
| `GET` | `/api/providers` | List providers, with masked keys, discovered models and declared `capabilities`. |
| `POST` | `/api/providers` | Add or update a provider. A masked `****` key means "keep the stored one". |
| `DELETE` | `/api/providers/{name}` | Remove a provider. |
| `POST` | `/api/providers/{name}/toggle` | Enable or disable. |
| `POST` | `/api/providers/{name}/test` | Health check — see below. |
| `POST` | `/api/providers/{name}/discover` | Fetch the provider's model list. |

`/api/providers/*` is the **only** provider surface. A parallel
`/api/settings/providers/*` existed with the same six operations and was
deleted: two routes for one concept is how they drift, and this pair drifted
far enough that a fix shipped into the one nothing called.

**What `…/test` returns.** Reachable and working are different claims, so the
response carries both:

| field | meaning |
|---|---|
| `reachable` | the model list answered. Not a claim that the provider works. |
| `chat_ok` | a real completion came back on `POST /chat/completions`. |
| `chat_ms`, `chat_model` | that completion's latency, and the model the provider says it served. |
| `success` | both of the above. |

The check resolves its key through `ModelRegistry.resolve_provider_credentials`
and its model through `ModelRegistry.probe_model_for` — the same paths a real
message uses. It resolves nothing by hand, and it does not write.

Probe failures report the HTTP status and an actionable hint. Remote response
bodies and credential-bearing request URLs are excluded from diagnostics.

### 2.3 HITL approval

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/pending-approvals` | Pending HITL approvals. (`hitl_approval.py:146`) |
| `POST` | `/api/approve/\{thread_id\}` | Approve/deny a paused tool. Body `\{action: "approve"|"deny", reason?\}`. Protected by `KAZMA_SECRET`. (`routes_direct/misc.py`) |

### 2.4 Dashboard

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dashboard/status` | Dashboard overview. (`dashboard.py:177`) |
| `GET` | `/api/sessions` | Sessions list — **admin only** (it lists every user's threads). (line 221) |
| `POST` | `/api/sessions/clear-all` | Clear sessions — **admin only**. (line 330) |

### 2.5 Swarm

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/swarm/status` | Swarm status. |
| `GET`/`POST`/`DELETE` | `/api/swarm/workers[/\{name\}]` | Worker CRUD. |
| `POST` | `/api/swarm/dispatch` | Dispatch a task (all patterns via `type`). |
| `GET` | `/api/swarm/tasks[/\{id\}]` | Task list / detail. |
| `POST` | `/api/swarm/tasks/\{id\}/approve` | Approve pipeline checkpoint. (`routes_tasks.py:612`) |
| `POST` | `/api/swarm/tasks/\{id\}/reject` | Reject pipeline checkpoint. (line 657) |
| `GET` | `/api/swarm/workers/\{name\}/metrics` | Worker metrics. |
| `GET` | `/api/swarm/circuit-breakers` | Breaker states. |

### 2.6 Health

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Liveness. (`health.py:94`) |
| `GET` | `/health/ready` | Readiness. (line 104) |
| `GET` | `/health/details` | Detailed health. (line 148) |
| `GET` | `/api/gateway/status` | Gateway/adapter status. |

### 2.7 Memory compatibility and operator interfaces

| Method | Path | Current contract |
|---|---|---|
| `GET` | `/api/memory/graph` | HTTP 410; use `/api/memory/v2/graph`. |
| `GET` | `/api/memory/graph/stats` | HTTP 410; use `/api/memory/v2/health`. |
| `GET` | `/api/memory/graph/export` | HTTP 410; use the V2 graph surface. |
| `GET` | `/api/memory/graph/search` | Compatibility search backed by V2 beliefs. |
| `POST` | `/api/memory/graph/clear` | Requires confirmation and tenant authorization; invalidates current beliefs while preserving history and episodes. |

Operator diagnostics and public integration endpoints can intentionally have no
browser caller. Their maintained inventory and caller reasons are in
`tests/test_api_route_callers.py`; absence from the frontend is not a deletion
criterion. Restore remains available through
`python -m kazma_core.backup.restore`; see [Disaster recovery](../ops/disaster-recovery).

---

## 3. SSE event contract {#sse-event-contract}

`POST /api/chat/stream` answers with Server-Sent Events: an `event:` line naming the frame and one `data:` line holding a JSON object. Source: `kazma-ui/kazma_ui/sse_chat/` (the route and the turn streamer `_streaming.py`) and the turn journal `kazma-ui/kazma_ui/delivery.py`; the browser reads it with `dispatch` in `static/js/streaming.js`.

**The turn's frames are journaled first.** Each one is written to the thread's journal before it is sent, so it also carries an `id:` line, and its data carries `seq` (the same number) and `turn_id`. A client that loses the stream sends the request again with `last_event_id` (or `last_seq`) in the body instead of a message: it gets a `resumed` frame, then every frame it missed, then the live turn. A frame replayed as history carries `replay: true`; a pending approval in it is shown only after the gate registry confirms it is still open. `error`, `capacity` and `user_message` frames, and any frame whose data says `capacity: true`, are never replayed. During a silence the stream sends `: keepalive` comment lines.

| `event:` | When | `data` fields |
|---|---|---|
| `resumed` | The first frame of every stream that follows a turn: a new prompt, a reconnect, and the wait after an approval. Not journaled. | `from`, `to` (the journal's newest `seq`), `count` (frames replayed), `gap`, `running`, `session_id`, `thread_id` |
| `token` | A piece of the answer, appended in order: a streamed chunk, a paragraph break between two model calls, the final text read from the checkpoint when nothing streamed, or a notice (the turn paused without an approval card, ended with no text, or failed before replying). | `content` |
| `tool_call` | A tool starts. | `tool_name`, `tool_call_id` (the same on its `tool_result`), `inputs` (JSON text, at most 2,000 characters) |
| `tool_result` | The tool finished. | `tool_name`, `tool_call_id`, `result` (at most 5,000 characters) |
| `memory_explain` | The supervisor was given memories for this question; the turn stores the same record. | `query`, `empty`, `detail`, `beliefs`, `episodes`, `weekly_summaries`, `knowledge` (each item: `id`, `kind`, `content`, `score`, `sources`), `summary` (the counts) |
| `turn_heartbeat` | About every 10 seconds with no other frame (every 8 while a turn resumes after an approval). | `phase` (`llm`, `supervisor`, `tool` or `resuming`), `current` (the tool), `detail`, `step`, `elapsed_s` |
| `status_update` | The graph reached its end and the answer is being written (`status: "synthesizing"`, `active_node: "Respond"`). Or the client's cursor is older than the journal keeps (`status: "resync"`, `seq`): the stream ends there, and the client reloads the chat from the store. | `status`, then `active_node` or `seq` |
| `status` | A second resume reached a turn that is already running; this stream follows that turn. | `content`, `status` (`thinking`) |
| `context_compacted` | This turn trimmed the model's context or collapsed old tool output. | `detail`, with counts such as `stubbed_segments`, `dropped_user`, `dropped_assistant` |
| `approval_required` | The graph paused for an approval. No `done` follows: the stream stays open, and the turn continues in it after `POST /api/approve/{thread_id}`. | `thread_id`, `interrupt_id`, `kind`, `tool`, `args`, `tools`, `items`, `message`, `yolo_allowed`, `approval_deadline`, and the gate registry's `view` and `gate_views` |
| `hitl` | An approval was decided, in this tab, another tab or a chat app. | `state` (`approved`, `denied` or `timeout`), `interrupt_id`, `tool`, `thread_id`, `turn_id`, `actor`, `view`, `gate_views` |
| `approval_timeout` | Nobody answered in time: the tool is skipped and the turn goes on. | `thread_id`, `interrupt_id`, `tool`, `turn_id`, `message` |
| `capacity` | `/long`, `/plan` or `/yolo`, answered without the model; a `done` with `capacity: true` follows. | `action`, `reply`, and `long_active` and `yolo_active`, or `plan_active` |
| `error` | The request or the turn failed. Before a turn starts (a body that is not JSON, a message over 512,000 characters, an empty message, no API key for a cloud provider, the session budget used up) it is the only frame. | `content` (internals removed) |
| `done` | The turn ended. `content` is the answer and replaces what the `token` frames built. | `content`, `tokens`, `cost`, `duration_ms`, `interrupted`, `empty`, `model`, `turn_id`, `session_tokens`, `session_cost`, `gate_views` |
| `turn_complete` | Right after `done`, with the same data. | as `done` |
| `snapshot` | After `done`, when Time Travel saved this step. | `snapshot_id`, `iteration`, `model` |
| `user_message` | Another tab started a turn on this thread. Only on streams already attached to the thread, never on the sender's own and never replayed; the chat page takes it from its WebSocket, which carries the same journal. | `content`, `turn_id`, `client_msg_id`, `session_id`, `ts` |

**Replies without the model.** `/reset`, `/compact` and the bare `/swarm` usage answer with a `token` and a `done` carrying `content`, `tokens`, `cost` and `duration_ms`; they are not journaled. The `/research` usage, `/replay` and `/fork` send the same two frames with `capacity: true`, journaled so the thread's other tabs see them. An attached stream ends after `done` or `turn_complete` (unless `interrupted`), or after `error`.

**HITL approval expiry**: if the user clicks Approve/Deny on a card that
has already timed out or been resumed, `POST /api/approve/{thread_id}`
returns **HTTP 409** with `{"status": "expired", "error": "No pending
approval for this thread (already resumed or expired)."}`. The frontend
(`hitl_approval.js`) detects this and transitions the card to
"Expired or already resumed" then removes it. A decision that names a gate
(`interrupt_id`) already decided, or a gate of another thread, is also a
409: `reason: "not_pending"`, with `hitl_state` (`inflight`, `settled` or
`foreign`), `registry_state` and the gate's `interrupt_id`.

### 3.1 Client-side example (JavaScript)

```javascript
// chat.js uses KS.sse('/api/chat/stream', {...}); the raw shape is:
const resp = await fetch('/api/chat/stream', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ message: 'Hello', session_id: sess, model: 'gpt-4o-mini' }),
});

const reader = resp.body.getReader();
const decoder = new TextDecoder();
let buffer = '';

while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buffer += decoder.decode(value, { stream: true });

  // SSE events are separated by blank lines
  let idx;
  while ((idx = buffer.indexOf('\n\n')) !== -1) {
    const block = buffer.slice(0, idx);
    buffer = buffer.slice(idx + 2);
    const eventType = (block.match(/^event: (.+)$/m) || [])[1];
    const data = JSON.parse(((block.match(/^data: (.+)$/m) || [])[1]) || '{}');
    handleEvent(eventType, data);
  }
}

function handleEvent(type, data) {
  switch (type) {
    case 'token':             appendToken(data.content); break;
    case 'tool_call':         showToolCall(data.tool_name, data.inputs); break;
    case 'tool_result':       showToolResult(data.tool_name, data.result); break;
    case 'approval_required': promptApproval(data.thread_id, data.interrupt_id, data.tool); break;
    case 'done':              finishTurn(data.content, data.tokens, data.cost); break;
    case 'error':             showError(data.content); break;
  }
}
```

### 3.2 Approving via the API (Python)

```python
import httpx

resp = httpx.post(
    "http://127.0.0.1:8000/api/approve/<thread_id>",
    headers={"X-Kazma-Secret": KAZMA_SECRET},   # required if KAZMA_SECRET is set
    json={"action": "approve", "reason": "looks safe"},
)
print(resp.status_code, resp.json())
```

---

## 4. Extension points

### 4.1 Add a tool

Register a function with the `ToolRegistry`:

```python
from kazma_core.agent.tool_registry import register_tool

@register_tool(
    name="weather_lookup",
    description="Look up current weather for a city.",
    danger=False,            # True → triggers HITL
)
async def weather_lookup(city: str) -> str:
    ...
    return f"Weather in {city}: sunny, 25C"
```

Register during startup (or via a skill entry point). The supervisor exposes it to the LLM automatically. Schemas are generated from type hints with `additionalProperties: false`. Optional parameters stay optional unless `KAZMA_STRICT_TOOLS=1`. For a one-off JSON reply (not a tool call), pass `response_format=` to `LLMProvider.chat` — do not put it on every supervisor turn.

### 4.1b Tool hooks (PreToolUse / PostToolUse)

```python
from kazma_core.agent.tool_hooks import register_pre_tool_hook, ToolHookDecision

def block_shell(event):
    cmd = str((event.tool_input or {}).get("command") or "")
    if "rm -rf" in cmd:
        return ToolHookDecision(decision="deny", reason="blocked pattern")
    return None

register_pre_tool_hook(block_shell, matcher="shell_exec")
```

Or YAML (`agent.hooks.pre_tool` / `post_tool`): each entry is `{matcher, command, timeout_seconds?}`. The command receives JSON on stdin (`hook_event_name`, `tool_name`, `tool_input`, and for post `tool_response`) and may print JSON (`decision`, `tool_input` / `updatedInput`, `reason`, `extra`) or exit `2` to deny. **Hooks cannot auto-approve HITL.** Kill-switch: `KAZMA_TOOL_HOOKS=0`.

### 4.2 Add a provider

Providers are ConfigStore entries under `providers.list`. The 10 built-in presets are in `kazma_core/providers.py:13-84`. To add a custom OpenAI-compatible endpoint:

```python
from kazma_core.config_store import get_config_store
from kazma_core.model_registry import get_model_registry

store = get_config_store()
reg = get_model_registry()

# Option A: use the 'custom' preset shape
reg.upsert_provider(
    name="my-endpoint",
    display_name="My Inference Server",
    base_url="https://infer.example.com/v1",
    api_key="sk-...",
    enabled=True,
)

# Option B: switch active provider/model
reg.set_active_provider("my-endpoint")
reg.set_active_model("my-model-id")
```

Any OpenAI-compatible endpoint works (vLLM, Together, Groq, Fireworks, …). For non-OpenAI auth schemes, note that `LLMProvider.chat()` always sends `Authorization: Bearer` — route through an OpenAI-compatible proxy if the upstream needs a different header.

### 4.3 Add a platform adapter

Subclass `BaseAdapter` (`kazma-gateway/kazma_gateway/gateway.py:239`), implement receive/send, produce `IncomingMessage`, and register it. For swarm HITL on the new platform, also subclass `BusAdapter` (`kazma_core/swarm/bus.py:66`) and wire it in `app.py`'s bus-singleton block.

### 4.4 Add a skill

See [Skills, MCP & Tools → Adding a custom skill](skills-mcp-and-tools#34-adding-a-custom-skill-minimal-example). Sign it with `kazma hub sign`.

### 4.5 Add an MCP server

See [Skills, MCP & Tools → Configuring an MCP server](skills-mcp-and-tools#53-configuring-an-mcp-server). Tools are discovered at runtime and classified by `classify_mcp_tool`.

### 4.6 Add a swarm worker

```bash
kazma swarm worker add researcher --model deepseek-chat --provider deepseek --type in_process --role researcher
```

Or via the API:

```python
import httpx
httpx.post("http://127.0.0.1:8000/api/swarm/workers", json={
    "name": "researcher",
    "model": "deepseek-chat",
    "provider": "deepseek",
    "worker_type": "in_process",
    "roles": ["researcher"],
})
```

### 4.7 Tap the V2 memory stack

The **V2 Cognitive Engine** is the **chat default** (per-turn recall, tools, auto-store, compaction) and is also used by self-improvement / phonebook. (The V1 `UnifiedMemoryAdapter` was removed in the V1→V2 cutover.) Custom code:

```python
from kazma_core.memory.recall import recall
from kazma_core.paths import primary_memory_db
import sqlite3

conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
conn.row_factory = sqlite3.Row

result = recall("what does the user prefer?", conn=conn, limit=5)
# result.beliefs  -> list[RecallHit] of currently-valid beliefs
# result.episodes -> list[RecallHit], ranked by evidence (meaning above the question's
#   background + content words covered); empty when nothing clears the threshold
```

Writing a belief (functional predicates supersede; set predicates append):

```python
from kazma_core.memory.belief_mutation import mutate_belief
from kazma_core.paths import primary_memory_db, ops_memory_db

primary = sqlite3.connect(primary_memory_db(), check_same_thread=False)
ops = sqlite3.connect(ops_memory_db(), check_same_thread=False)

mutate_belief(
    primary, "user", "prefers", "dark mode",
    ops_conn=ops, predicate_type="set",
    extraction_method="custom", source_session="my-integration",
)
```

See [Memory & RAG](memory-and-rag).

---

## 5. Telemetry & observability endpoints

- `/api/telemetry/*` (telemetry_router) — runtime telemetry.
- `/api/dashboard/status` — overview for the dashboard.
- Swarm metrics at `/api/swarm/workers/\{name\}/metrics`.

> **Prometheus `/metrics` does not exist.** OTel packages are declared but Kazma's tracing is an in-house span emitter. See [Architecture → Observability](architecture#9-observability-current-state).

---

## Documentation Audit Notes

- **Graph transport is SSE** (`POST /api/chat/stream`). `/ws/chat/{session_id}` is the telemetry / cursor bus: it runs no turn and takes no turn control (send, approve, stop, steer and abort are HTTP/SSE; the socket refuses them, naming the route).
- **The SSE `approval_required` event** is the canonical way for frontends to surface HITL pauses; pair it with `POST /api/approve/\{thread_id\}`.
- **`/api/approve` ownership enforcement** (403 on cross-user) means approval tokens are per-user — an admin can't approve another user's task without matching identity fields.
- **V2 is the single memory stack** — per-turn recall, tools, auto-store, and compaction all use `recall()` from `memory/recall.py`. The V1 4-layer adapter (`get_adapter()`) was removed in the V1→V2 cutover.
