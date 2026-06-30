---
sidebar_position: 1
---

# Core API Reference

## KazmaAgent

```python
from kazma_core.agent_runner import KazmaAgent, AgentConfig, load_config

config = load_config()
agent = KazmaAgent(config)
response = await agent.run(message="Hello")
await agent.shutdown()
```

| Method | Description |
|---|---|
| `run(message)` | Process a message and return a response string |
| `shutdown()` | Clean shutdown — close LLM client, checkpointer, MCP |
| `connect_mcp_servers()` | Connect configured MCP servers, returns tool count |

## ModelRegistry

```python
from kazma_core.model_registry import get_model_registry, initialize_model_registry

initialize_model_registry()
registry = get_model_registry()
provider = registry.get_client()               # LLMProvider
response = await provider.chat([{"role": "user", "content": "Hello"}])
```

| Method | Description |
|---|---|
| `get_active_profile()` | Return current provider/model dict |
| `get_client(model=None)` | Return LLMProvider for the active model |
| `get_model(model_id)` | Return LLMProvider for a specific model |
| `list_providers()` | List all configured providers |
| `list_model_profiles()` | List all saved model profiles |
| `save_model_profile(name, data)` | Save a model profile |
| `get_discovered_models(provider)` | List models auto-discovered from a provider |

## WorkerRegistry (Swarm)

```python
from kazma_core.swarm.registry import WorkerRegistry, WorkerEntry

registry = WorkerRegistry()
registry.register(WorkerEntry(
    name="core",
    expertise=["code", "security"],
    roles=["orchestrator"],
    model="deepseek-v4-pro",
    provider="deepseek",
    system_prompt="You are the core engineer...",
))
```

| Method | Description |
|---|---|
| `register(entry)` | Add a worker (persists to JSON) |
| `update(name, **kwargs)` | Update worker fields (model, provider, expertise, etc.) |
| `delete(name)` | Remove a worker |
| `get(name)` | Get worker by name |
| `list_all()` | List all workers |
| `find_by_expertise(tag)` | Find workers matching an expertise tag |
| `find_best(task_description)` | Smart routing: semantic → keyword → generalist fallback |
| `find_generalists()` | Return all generalist workers (no expertise/Soul) |

## SwarmEngine

```python
from kazma_core.swarm.engine import get_swarm_engine

engine = get_swarm_engine()
result = await engine.consult("code", "Fix auth bug")
```

| Method | Description |
|---|---|
| `dispatch(task)` | Dispatch a task to a specific worker |
| `broadcast(task)` | Broadcast to all workers |
| `consult(expertise, task)` | Consult workers by expertise, aggregate results |
| `summon(worker_name)` | Instantiate a worker from the Registry |
| `pipeline(task, workers)` | Run sequential pipeline stages |

## UnifiedMemoryAdapter (4-Layer RAG)

```python
from kazma_core.swarm.memory import UnifiedMemoryAdapter, MemoryHit

adapter = UnifiedMemoryAdapter(vector_store, graph, fts5_store, sqlite_vec)
hits: list[MemoryHit] = await adapter.query("auth vulnerability", limit=10)
# Each hit has: id, content, score, source_layer, metadata

await adapter.index("task output here", metadata={"worker": "core"})
```

| Layer | Backend | Purpose |
|---|---|---|
| L1 | ChromaDB | Global semantic similarity |
| L2 | NetworkX | Knowledge graph (dependencies, lineage) |
| L3 | SQLite FTS5 | Lexical keyword + BM25 + Arabic |
| L4 | sqlite-vec | Local per-worker embeddings |

## Refiner (Pipeline Middleman)

After pipeline completion, the Refiner synthesizes all stage outputs into a Markdown report card delivered to the triggering platform (Telegram, Web UI, TUI).

## SafetyMiddleware

```python
from kazma_core.swarm.safety import get_safety

safety = get_safety()
# Danger-tier tools are gated behind bus.request_approval()
# Danger tools: shell_exec, file_write, file_delete, python_exec,
#               code_exec, spawn_agent, spawn_agents, schedule_task
if await safety.check("shell_exec", "rm -rf /tmp/old-logs", task_id):
    # Approved — execute
```

## Pipeline Logger

```python
from kazma_core.swarm.memory.pipeline_logger import get_pipeline_logger

plog = get_pipeline_logger()
plog.log_step(cid, worker, stage, "info", "step_start", "Starting research")
plog.log_tool_exec(cid, worker, "shell_exec", "ls -la", output)
logs = plog.query_by_correlation(cid)
```
