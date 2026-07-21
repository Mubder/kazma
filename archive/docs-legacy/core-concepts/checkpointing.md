---
sidebar_position: 3
---

# Checkpointing

Checkpointing provides durable state persistence, enabling agent recovery and session continuity.

## How it works

Kazma uses SQLite-backed checkpointing with the `aiosqlite` library:

```python
from kazma_core.checkpoint import CheckpointManager

# Initialize
manager = CheckpointManager(db_path="~/.kazma/checkpoints.db")

# Save agent state
await manager.save({
    "agent_id": "my-agent",
    "messages": [],
    "tools_used": [],
    "current_step": 5,
})

# Load state
state = await manager.load(agent_id="my-agent")
```

## Checkpoint schema

| Field | Type | Description |
|---|---|---|
| `agent_id` | TEXT | Unique agent identifier |
| `messages` | JSON | Conversation history |
| `tools_used` | JSON | List of tool invocations |
| `current_step` | INTEGER | Current execution step |
| `created_at` | TIMESTAMP | When the checkpoint was saved |

## Recovery

If an agent crashes or times out, the checkpoint manager restores the last valid state:

```bash
# Check last checkpoint
kazma status --checkpoint

# Recover from checkpoint
kazma recover --agent my-agent
```

## Configuration

```yaml
# ~/.kazma/config.yaml
checkpoint:
  db_path: ~/.kazma/checkpoints.db
  interval: 10          # save every N steps
  max_snapshots: 100    # keep last N snapshots
  auto_cleanup: true    # remove old snapshots
```
