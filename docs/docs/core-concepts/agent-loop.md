---
sidebar_position: 2
---

# Agent Loop

The agent loop is the core execution cycle that processes messages, selects tools, and generates responses.

## Lifecycle

```
receive_message()
  -> count_tokens()
  -> route_to_dialect()
  -> check_checkpoint()
  -> select_tool_or_respond()
  -> execute_tool()
  -> compact_context_if_needed()
  -> save_checkpoint()
  -> return_response()
```

## Token counting

Every message goes through token counting to manage context window limits:

```python
from kazma_core.token_counter import TokenCounter

counter = TokenCounter(max_tokens=4096)
remaining = counter.count("my message here")
```

When tokens approach the limit, context compaction is triggered automatically.

## Dialect routing

The router detects the language and style of the input, then routes to the appropriate handler:

```python
from kazma_core.router import DialectRouter

router = DialectRouter()
dialect = router.detect("مرحبا، كيف حالك؟")  # Arabic/Kuwaiti
```

## Checkpointing

The agent saves state after each interaction, enabling recovery from failures:

```python
from kazma_core.checkpoint import CheckpointManager

manager = CheckpointManager(db_path="checkpoints.db")
await manager.save(state)
state = await manager.load(agent_id="my-agent")
```

## Tool selection

When the agent decides to use a tool, it goes through the sandbox:

```python
from kazma_core.tool_sandbox import ToolSandbox

sandbox = ToolSandbox()
result = await sandbox.execute("image_analyze", {"path": "/tmp/photo.jpg"})
```

Tools are sandboxed to prevent unauthorized system access.
