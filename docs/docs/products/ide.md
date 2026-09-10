---
id: ide
title: IDE
sidebar_label: IDE
description: Transport-agnostic coding IDE — Web, TUI, and /ide slash commands
---

# IDE

Kazma’s IDE is a **transport-agnostic coding backend**: one service for Web, TUI, chat `/ide` commands, and the in-process CLI (`kazma ask` / `kazma acp`). Mutations always go through **`LocalToolRegistry`** so HITL cannot be bypassed.

**Web editor (Hands 0.11):** CodeMirror 5 `fromTextArea` on `/ide` (nord
theme, `--bg-deep`). File bytes are written into a `<textarea>` first, then
CodeMirror wraps it for line numbers and syntax. If the CDN is blocked, the
file is still visible as plain text. Monaco+Alpine was a dead end (browser
hang / empty tabs) and is gone. Agent edits to existing files should use
**`file_apply_patch`** or **`file_apply_patch_set`** (unique
`old_string`/`new_string` or a unified diff), not a whole-file `file_write`.

**Codebase index:** `codebase_search` (and `GET /api/ide/codebase?q=`) finds
definitions via a per-workspace SQLite symbol index (tree-sitter if you
`pip install 'kazma[index]'`, else regex) plus live ripgrep. Install `rg`
for faster text hits. Kill-switch `KAZMA_CODE_INDEX=0`.

**Language intelligence:** the Web editor is **syntax-only** (CodeMirror
modes). `POST /api/ide/lsp` still exists for hover/complete/definition/
diagnostics (Python/JSON in-process; symbols reuse the code index) but is
not bound in the CodeMirror UI. Kill-switch `KAZMA_IDE_LSP=0`. For an
industrial editor loop, use `kazma acp` in Zed.

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

### Path grants (outside-workspace access)

Access outside the active workspace is **denied by default**, but can be opened
with permission. Source of truth: `kazma_core/workspace/path_policy.py` +
`workspace/path_grants.py`, wired through `IdeService.resolve`, `file_read` /
`file_write`, file list/search/delete/append, and the shell path checks.

| How | Effect |
|-----|--------|
| **Chat (smooth)** | When a file tool fails on an outside-workspace path, the agent calls `request_path_access` (a danger-tier HITL card). On approval a **session grant** (~1h TTL) is created and the tool retries. |
| **Settings / API** | Durable extra roots via `workspace.extra_roots` + `GET/PUT /api/workspace/extra_roots` (`path`, `mode`: `read` \| `write`, `label`). Persist until removed. |

Read grants never allow writes. Denial messages tell the agent how to request a
grant, so the loop is smooth rather than a hard failure.

## Operations

| Action | HITL | Notes |
|--------|------|-------|
| Read / list / search | Usually safe | Workspace-scoped |
| Codebase search | Safe | `codebase_search` / `GET /api/ide/codebase` — symbols + ripgrep |
| Language intelligence | Safe | `/api/ide/lsp` backend exists; Web editor is syntax-only CodeMirror |
| Write / delete | Danger tools | Graph or bus approval |
| Apply patch | Danger (`file_apply_patch` / `file_apply_patch_set`) | Search-replace or unified diff; one HITL card for a set; same HITL as write |
| Run / run_file / shell | Danger | `shell_exec` / `python_exec` policy; Docker `force` blocks host shell unless `KAZMA_HOST_SHELL=1` |
| Git status / diff / log | Safe | Read-only git is a workspace subprocess (not HITL `shell_exec`) |
| Git commit / push / clean | Danger | Native git skill tools |
| Send to swarm | — | Attaches env context for workers |

## AI chat from IDE

File-aware chat reuses **`/api/chat/stream`** (no parallel agent path). Env context is injected so the model sees branch, repo, and available tools.

## Related

- [Tools catalog](../reference/tools-catalog)  
- [Web UI](web-ui) · [Slash commands](../reference/slash-commands)  
- [Security](../guide/security-and-safety)  
