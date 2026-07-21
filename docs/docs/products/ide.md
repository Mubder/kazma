---
id: ide
title: IDE
sidebar_label: IDE
description: Transport-agnostic coding IDE — Web, TUI, and /ide slash commands
---

# IDE

Kazma’s IDE is a **transport-agnostic coding backend**: one service for Web, TUI, and chat `/ide` commands. Mutations always go through **`LocalToolRegistry`** so HITL cannot be bypassed.

## Components

| Piece | Module |
|-------|--------|
| Service | `kazma_core/ide/service.py` |
| Env context injection | `ide/env_context.py` |
| Per-task workspace | `ide/workspace_scope.py` |
| Web API | `kazma_ui/ide_api.py` + `/ide` page |
| TUI | `kazma_tui` editor screen |
| Chat | Gateway `/ide` slash commands |

## Workspace resolution (must stay consistent)

Both `file_write._get_workspace()` and `IdeService._resolve_workspace_root()` use:

1. Per-task `workspace_scope` ContextVar  
2. `configure_workspace()` global  
3. `KAZMA_WORKSPACE` env  
4. Active **WorkspaceStore** row  
5. Default `cwd/kazma-data/workspace`  

Production may require an explicit workspace root. Path traversal is blocked with `normpath` + containment checks.

## Operations

| Action | HITL | Notes |
|--------|------|-------|
| Read / list / search | Usually safe | Workspace-scoped |
| Write / delete | Danger tools | Graph or bus approval |
| Run / run_file / shell | Danger | `shell_exec` / `python_exec` policy |
| Git commit / push / PR | Danger | Native git skill tools |
| Send to swarm | — | Attaches env context for workers |

## AI chat from IDE

File-aware chat reuses **`/api/chat/stream`** (no parallel agent path). Env context is injected so the model sees branch, repo, and available tools.

## Related

- [Tools catalog](../reference/tools-catalog)  
- [Web UI](web-ui) · [Slash commands](../reference/slash-commands)  
- [Security](../guide/security-and-safety)  
