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
| `kazma-core` | Agent brain, swarm, tools, model registry, vault, IDE service, ConfigStore, **document intelligence** (`documents/`) |
| `kazma-gateway` | Telegram / Discord / Slack adapters + agent handler (`/documents` slash, attachment parse) |
| `kazma-ui` | FastAPI web UI, SSE chat, settings, IDE, swarm panel, **`/documents`** + documents API |
| `kazma-tui` | Textual dashboard / editor / **Documents tab** |
| `kazma-cli` | `kazma` CLI entrypoints (incl. migrate with document store) |
| `kazma-skills` | Skill manifests + native skills (`document-platform`, `document-generator`, …) |

## Decomposed modules

Four modules that had grown past 2,700 lines are now packages behind
**unchanged public facades** (audit O5, 2026-08-29). Import paths did not
change; the route table and tool registry were verified byte-identical
before and after.

| Package | Facade | Submodules |
|---------|--------|-----------|
| `kazma_ui/routes_direct/` | `register_direct_routes` | `memory` `system` `settings` `auth` `backup` `misc` `_shared` |
| `kazma_ui/sse_chat/` | `create_sse_chat_router` | `_helpers` `_persistence` `_streaming` |
| `kazma_ui/i18n/` | `TRANSLATIONS`, `t`, `make_translator` | `catalog/` — one module per UI section |
| `kazma_core/agent/tool_builtins/` | `register_builtin_tools` | `filesystem` `memory` `system` `knowledge` `research` `mcp` `external` |

Two rules follow:

- Registration **order** is preserved inside each facade. `external` runs last
  in `tool_builtins` so a name registered twice still resolves the same way.
- Patch a seam where it is **defined**, not on the facade. `_module_graph`
  lives in `sse_chat/_helpers`; a patch applied to `kazma_ui.sse_chat` will
  not reach the `from … import` binding in `_streaming`.

## Critical runtime rules

1. Model + provider always switch together (`model_registry.py`).  
2. Platform IDs never enter LangGraph state.  
3. Three HITL gates: graph interrupt, swarm bus, pipeline checkpoints.  
4. IDE mutations go through `LocalToolRegistry` (shared HITL).  
5. ConfigStore via `get_config_store()` only.  
6. Document durable path uses `DocumentIngestionService` only — no gateway/UI imports of parser internals (`AGENTS.md` §19).  
7. **Every registered tool needs a `TOOL_TIERS` entry.** HITL default-denies what it cannot classify, so an untiered tool prompts on every call. CI gate: `test_every_registered_tool_has_a_tier`.  
8. **No blocking DB call inside `async def`, no bare `asyncio.create_task`.** Both stall or silently drop work on the loop that serves every SSE/WebSocket stream. Use `asyncio.to_thread` (or a sync handler) and `kazma_core.background.spawn_background`. CI-gated.  
9. **Peer address is not a credential behind a proxy.** `KAZMA_TRUSTED_PROXIES` must be set wherever anything fronts Kazma — see [Hardening](../security/hardening-guide.md#behind-a-reverse-proxy).

## Multi-path diagnosis

When one UI element is served by **several code paths** (SSE vs WS, three HITL gates, dual registries), use the operator-facing:

**[Diagnosis map](../ops/diagnosis-map)** — symptom tables, “X is related to Y”, invariants, and drift tests.

See also [Architecture](../guide/architecture) and repo root [`AGENTS.md`](https://github.com/Mubder/kazma/blob/main/AGENTS.md).
