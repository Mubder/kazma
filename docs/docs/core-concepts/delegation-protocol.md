---
sidebar_position: 6
---

# Delegation Protocol

Kazma supports multi-agent delegation where a main agent can delegate tasks to specialized sub-agents.

## Overview

```
+-----------------+
|  Main Agent     |
|  (Orchestrator) |
+--------+--------+
         |
    +----+----+
    v         v
+--------+ +--------+
| Agent A| | Agent B|
|(Search) | |(Analyze)|
+--------+ +--------+
```

## Delegation flow

1. **Task decomposition** — Main agent breaks a complex task into subtasks
2. **Capability matching** — Find agents with matching capabilities
3. **Task dispatch** — Send subtasks to matched agents
4. **Result aggregation** — Combine results from sub-agents
5. **Response synthesis** — Generate final response

## Implementation

```python
from kazma_core.delegation.orchestrator import DelegationOrchestrator
from kazma_core.delegation.protocol import DelegationProtocol

orchestrator = DelegationOrchestrator()
protocol = DelegationProtocol()

# Find suitable agents
agents = await orchestrator.find_agents(
    required_capabilities=["image_analysis"]
)

# Delegate task
result = await protocol.delegate(
    task="Analyze this satellite image",
    target_agent=agents[0],
    timeout=60,
)
```

## Security

All delegated tasks go through the security module:

- Permission checks before execution
- Sandboxed execution environment
- Audit trail of all delegations
- Rate limiting per agent

## Swarm mode

For parallel execution, Kazma supports swarm mode:

```python
from kazma_core.delegation.swarm import SwarmCoordinator

swarm = SwarmCoordinator(max_agents=5)
results = await swarm.execute_parallel([
    {"task": "Analyze region A", "capabilities": ["analysis"]},
    {"task": "Analyze region B", "capabilities": ["analysis"]},
    {"task": "Collect data for region C", "capabilities": ["data_collection"]},
])
```

The newer swarm engine also supports `fan_out` orchestration, where the same
prompt is sent to multiple workers concurrently and then aggregated with one of
these strategies:

- `first_valid`
- `merge_all`
- `vote`
- `synthesize`
- `collect`

Fan-out execution is bounded by a semaphore and defaults to `max_concurrent=5`.
