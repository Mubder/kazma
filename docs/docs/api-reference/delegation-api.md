---
sidebar_position: 3
---

# Delegation API Reference

## DelegationOrchestrator

```python
from kazma_core.delegation.orchestrator import DelegationOrchestrator

orchestrator = DelegationOrchestrator()
agents = await orchestrator.find_agents(required_capabilities=["analysis"])
```

## DelegationProtocol

```python
from kazma_core.delegation.protocol import DelegationProtocol

protocol = DelegationProtocol()
result = await protocol.delegate(
    task="Analyze this data",
    target_agent=agent,
    timeout=60,
)
```

## SwarmCoordinator

```python
from kazma_core.delegation.swarm import SwarmCoordinator

swarm = SwarmCoordinator(max_agents=5)
results = await swarm.execute_parallel(tasks)
```

## SwarmEngine fan-out

```python
from kazma_core.swarm import SwarmConfig, SwarmEngine, SwarmTask, TaskType

engine = SwarmEngine(SwarmConfig(enabled=True, max_concurrent=5, workers=[]))
result = await engine.dispatch(
    SwarmTask(
        prompt="Compare these implementation options",
        workers=["builder", "reviewer", "researcher"],
        type=TaskType.FAN_OUT,
        aggregation="vote",
        metadata={"max_concurrent": 2},
    )
)
```

Fan-out returns per-worker `worker_results` plus an `aggregated_output`.
Supported aggregation strategies are `first_valid`, `merge_all`, `vote`,
`synthesize`, and `collect`.

## Security Module

```python
from kazma_core.delegation.security import DelegationSecurity

security = DelegationSecurity()
allowed = await security.check_permissions(agent, required_permissions)
```
