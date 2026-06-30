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

### Methods

| Method | Description |
|---|---|
| run(message) | Process a message and return a response string |
| shutdown() | Clean shutdown — close LLM client, checkpointer, MCP |
| connect_mcp_servers() | Connect configured MCP servers, returns tool count |

## Token Counter

```python
from kazma_core.token_counter import TokenCounter

counter = TokenCounter(max_tokens=4096)
count = counter.count("my message")
```

## Dialect Detector

```python
from kazma_core.dialect_detector import DialectDetector

detector = DialectDetector()
result = detector.detect("مرحبا")
# result.dialect, result.confidence, result.script
```

## CheckpointManager

```python
from kazma_gateway.stores.checkpoint import create_checkpoint_manager

manager = await create_checkpoint_manager(path="kazma-data/checkpoints.db")
# Use with LangGraph: graph.compile(checkpointer=manager)
# List checkpointed threads:
threads = await manager.list_checkpoints(limit=50)
await manager.close()
```

## CompactionEngine

```python
from kazma_core.compaction import CompactionEngine

engine = CompactionEngine(max_tokens=4096)
compacted = await engine.compact(messages)
```

## Tool Sandbox

```python
from kazma_core.tool_sandbox import ToolSandbox

sandbox = ToolSandbox()
result = await sandbox.execute(tool_name, params)
```
