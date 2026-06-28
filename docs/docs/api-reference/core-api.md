---
sidebar_position: 1
---

# Core API Reference

## Agent

```python
from kazma_core.agent import Agent

agent = Agent(config=my_config)
response = await agent.process(message="Hello")
```

### Methods

| Method | Description |
|---|---|
| process(message) | Process a message and return a response |
| reset() | Reset agent state |
| get_state() | Get current agent state |

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

## Checkpoint Manager

```python
from kazma_core.checkpoint import CheckpointManager

manager = CheckpointManager(db_path="checkpoints.db")
await manager.save(state)
state = await manager.load(agent_id="my-agent")
```

## Context Compactor

```python
from kazma_core.compaction import ContextCompactor

compactor = ContextCompactor(max_tokens=4096)
compacted = await compactor.compact(messages)
```

## Tool Sandbox

```python
from kazma_core.tool_sandbox import ToolSandbox

sandbox = ToolSandbox()
result = await sandbox.execute(tool_name, params)
```
