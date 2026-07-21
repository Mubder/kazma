---
id: system-map
title: System Map
sidebar_label: System Map
description: Pointer to the full monorepo architecture system map
---

# System map

The **full monorepo architecture map** (data-flow diagram, package catalogs, subsystem deep-dives, remediation crosswalk) is maintained as:

**[`docs/ARCHITECTURE_AND_SYSTEM_MAP.md`](https://github.com/kazma-ai/kazma/blob/main/docs/ARCHITECTURE_AND_SYSTEM_MAP.md)**

That file is the engineering single source of truth for *how packages wire together*. This docs site focuses on operator and developer guides.

## Quick package map

| Package | Role |
|---------|------|
| `kazma-core` | Agent brain, swarm, tools, model registry, vault, IDE service, ConfigStore |
| `kazma-gateway` | Telegram / Discord / Slack adapters + agent handler |
| `kazma-ui` | FastAPI web UI, SSE chat, settings, IDE, swarm panel |
| `kazma-tui` | Textual dashboard / editor |
| `kazma-cli` | `kazma` CLI entrypoints |
| `kazma-skills` | Skill manifests + native skills |
| `kazma-memory` | Arabic tokenizer / search helpers |

## Critical runtime rules

1. Model + provider always switch together (`model_registry.py`).  
2. Platform IDs never enter LangGraph state.  
3. Three HITL gates: graph interrupt, swarm bus, pipeline checkpoints.  
4. IDE mutations go through `LocalToolRegistry` (shared HITL).  
5. ConfigStore via `get_config_store()` only.

See also [Architecture](../guide/architecture) and repo root [`AGENTS.md`](https://github.com/kazma-ai/kazma/blob/main/AGENTS.md).
