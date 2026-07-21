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

Public surface on KazmaAgent (see agent_runner.py). UI and gateway use it for streaming graph and tools.

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

## Worker Registry / Phonebook (Swarm)

Use via `get_swarm_engine()` or `WorkerPhonebook` (internal; public surface via engine).

```python
from kazma_core.swarm import get_swarm_engine

engine = get_swarm_engine()
# Workers registered via config / swarm panel or code
workers = engine.list_workers()  # or equivalent public listing
```

See `swarm/engine.py` (delegates to phonebook), `swarm/phonebook.py`, and `swarm_panel.py` for CRUD. Registry JSON persisted.

## SwarmEngine

```python
from kazma_core.swarm import get_swarm_engine

engine = get_swarm_engine()
# See public methods: dispatch, consult, etc. (delegated)
```

Core orchestration in `swarm/engine.py` (post P2-1 refactor uses phonebook/reliability_registry/checkpoint_manager). Use swarm panel or `/swarm` commands for most usage. See tests for examples.

## Memory (RAG layers)

Memory access is via `kazma_core.swarm.memory` (vector + FTS5 + kg_adapter + sqlite_vec) or higher level tools.
See `swarm/memory/adapter.py` and memory backends. RAG is optional (extras in pyproject).

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
