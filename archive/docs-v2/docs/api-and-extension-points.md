# API & Extension Points

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
| `sse_router` | `/api/chat/*` | `sse_chat.py` |
| `telemetry_router` | telemetry | telemetry routes |
| `dashboard_router` | `/api/dashboard/*` | `dashboard.py` |
| `models_router` | models | models routes |
| `workspace_router` | workspace | workspace routes |
| `swarm_router` | `/api/swarm/*` | `swarm_panel/` |
| `monitor_router` | monitor | monitor routes |
| `metrics_router` | metrics | metrics routes |

Plus direct routes in `routes_direct.py` and a conditional Telegram webhook at `/api/webhooks/telegram` (`app.py:365`).

---

## 2. Key endpoints (verified)

### 2.1 Chat (SSE)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/chat/stream` | Primary chat transport. Body `{message, session_id, model}`. Returns `text/event-stream`. (`sse_chat.py:353`) |
| `GET` | `/api/chat/sessions` | List sessions. (line 547) |
| `DELETE` | `/api/chat/sessions/{session_id}` | Delete session. (line 555) |
| `GET` | `/api/chat/sessions/{session_id}/messages` | Session history. (line 561) |

> **Legacy:** `GET /ws/chat` returns **410 Gone** (`chat.py:4`). Do not use.

### 2.2 Providers

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/provider/active` | Active provider/model. (line 583) |
| `GET` | `/api/providers` | List providers. (line 601) |
| `POST` | `/api/provider/switch` | Switch active provider/model. (line 607) |

### 2.3 HITL approval

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/pending-approvals` | Pending HITL approvals. (`hitl_approval.py:146`) |
| `POST` | `/api/approve/{thread_id}` | Approve/deny a paused tool. Body `{action: "approve"|"deny", reason?}`. Protected by `KAZMA_SECRET`. (`routes_direct.py:454`) |

### 2.4 Dashboard

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/dashboard/status` | Dashboard overview. (`dashboard.py:177`) |
| `GET` | `/api/sessions` | Sessions list. (line 221) |
| `POST` | `/api/sessions/clear-all` | Clear sessions. (line 330) |

### 2.5 Swarm

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/swarm/status` | Swarm status. |
| `GET`/`POST`/`DELETE` | `/api/swarm/workers[/{name}]` | Worker CRUD. |
| `POST` | `/api/swarm/dispatch` | Dispatch a task (all patterns via `type`). |
| `GET` | `/api/swarm/tasks[/{id}]` | Task list / detail. |
| `POST` | `/api/swarm/tasks/{id}/approve` | Approve pipeline checkpoint. (`routes_tasks.py:612`) |
| `POST` | `/api/swarm/tasks/{id}/reject` | Reject pipeline checkpoint. (line 657) |
| `GET` | `/api/swarm/workers/{name}/metrics` | Worker metrics. |
| `GET` | `/api/swarm/circuit-breakers` | Breaker states. |

### 2.6 Health

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health/live` | Liveness. (`health.py:94`) |
| `GET` | `/health/ready` | Readiness. (line 104) |
| `GET` | `/health/details` | Detailed health. (line 148) |
| `GET` | `/api/gateway/status` | Gateway/adapter status. |

---

## 3. SSE event contract

`POST /api/chat/stream` returns a stream of Server-Sent Events. Each event has a typed `event:` line and a JSON `data:` payload (`sse_chat.py:8-13`).

| `event:` | Meaning | Key payload fields |
|---|---|---|
| `token` | An LLM streaming chunk. | `content` |
| `tool_call` | A tool is starting. | `tool`, `args` |
| `tool_result` | A tool finished. | `tool`, `result`, `is_error` |
| `approval_required` | A HITL pause surfaced — frontend should call `POST /api/approve/{thread_id}`. (line 199-207) | `thread_id`, `tool`, `args` |
| `done` | Turn complete. | `tokens`, `cost_usd`, `duration_ms` |
| `error` | Fatal error. | `message` |

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
    case 'token':            appendToken(data.content); break;
    case 'tool_call':        showToolCall(data.tool, data.args); break;
    case 'tool_result':      showToolResult(data.tool, data.result); break;
    case 'approval_required': promptApproval(data.thread_id, data.tool); break;
    case 'done':             finishTurn(data.tokens, data.cost_usd); break;
    case 'error':            showError(data.message); break;
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

Register during startup (or via a skill entry point). The supervisor exposes it to the LLM automatically.

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

See [Skills, MCP & Tools → Adding a custom skill](skills-mcp-and-tools.md#34-adding-a-custom-skill-minimal-example). Sign it with `kazma hub sign`.

### 4.5 Add an MCP server

See [Skills, MCP & Tools → Configuring an MCP server](skills-mcp-and-tools.md#53-configuring-an-mcp-server). Tools are discovered at runtime and classified by `classify_mcp_tool`.

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

### 4.7 Tap the 4-layer memory adapter (advanced)

The `UnifiedMemoryAdapter` (`swarm/memory/adapter.py`) is only used by `self_improvement.py` and `phonebook.py`. To use it elsewhere:

```python
from kazma_core.swarm.memory.adapter import get_adapter

adapter = get_adapter()
results = await adapter.query("your query", top_k=5)   # RRF-blended across 4 layers
```

Note: this is **not** wired into the chat agent by default — see [Memory & RAG](memory-and-rag.md).

---

## 5. Telemetry & observability endpoints

- `/api/telemetry/*` (telemetry_router) — runtime telemetry.
- `/api/dashboard/status` — overview for the dashboard.
- Swarm metrics at `/api/swarm/workers/{name}/metrics`.

> **Prometheus `/metrics` does not exist.** OTel packages are declared but Kazma's tracing is an in-house span emitter. See [Architecture → Observability](architecture.md#9-observability-current-state).

---

## Documentation Audit Notes

- **The WebSocket chat endpoint is dead** (410 Gone). All API consumers should use SSE.
- **The SSE `approval_required` event** is the canonical way for frontends to surface HITL pauses; pair it with `POST /api/approve/{thread_id}`.
- **`/api/approve` ownership enforcement** (403 on cross-user) means approval tokens are per-user — an admin can't approve another user's task without matching identity fields.
- **The 4-layer memory adapter is an extension point, not a default** — calling `get_adapter()` from custom code is supported, but don't assume the chat agent already uses it.
