---
id: command-center-swarm
title: Command Center & Swarm Panel
sidebar_label: Command Center
description: Live swarm orchestration UI, SSE events, and dispatch controls
---

# Command Center & Swarm panel

The **Swarm / Command Center** UI surfaces multi-worker orchestration: dispatch, pipelines, fan-out, live task status, and metrics.

## Surfaces

| Surface | Location |
|---------|----------|
| Web page | `/swarm` + `swarm.js` / swarm panel modules |
| SSE | Swarm event stream for live updates |
| REST | `/api/swarm/*` (workers, tasks, metrics, approve/reject) |
| CLI | `kazma swarm …` (talks to running server) |
| Gateways | `/swarm` slash + HITL bus adapters |

## Concepts

- **Patterns:** dispatch, broadcast, pipeline, fan-out, consult, conditional (see [Swarm orchestration](../guide/swarm-orchestration)).  
- **Reliability:** circuit breakers, retries, timeouts, visit/depth handoff limits.  
- **HITL:** bus approvals + pipeline checkpoints.  
- **Persistence:** TaskStore (SQLite or Postgres).

## Operator tips

1. Start the Web UI first (`kazma serve`).  
2. Add workers via UI or `kazma swarm worker add …`.  
3. Watch live tasks; approve checkpoints when paused.  
4. Use metrics/history for cost and failure trends.

## Related

- [Swarm orchestration](../guide/swarm-orchestration)  
- [API routes](../reference/api-routes)  
- [CLI reference](../guide/cli-reference)  
