---
id: portability
title: Portability
sidebar_label: Portability
description: Cross-platform guarantees — project data, user prefs, and path policy
---

# Portability guarantees

Kazma runs on **Windows, Linux, macOS, and WSL** from the same codebase.  
This page defines what “portable” means here — and what it does **not**.

## What we mean

| Claim | Meaning |
|-------|---------|
| **Cross-platform** | Same monorepo; no OS-specific forks for core agent / IDE / swarm / gateway |
| **Project-local data** | Runtime DBs, vectors, Kazma home (log, hub, skills, TUI) travel with the checkout |
| **Clone dir separate** | User repos cloned via Switch Repo land under `~/kazma-repos` (or `KAZMA_CLONE_DIR`) |
| **Configurable** | Paths and service URLs override via env / `kazma.yaml` — never machine-absolute homes in shipped code |

**Not claimed:** multi-process MCP rooted per concurrent swarm task (MCP is process-global).

## Data layout

| Category | Default location | Resolved by |
|----------|------------------|-------------|
| Project data (settings, checkpoints, swarm tasks, audit, RBAC, vectors, default sandbox) | `<project-root>/kazma-data/` | `paths.data_dir()` — override `KAZMA_DATA_DIR` |
| Kazma home (log, hub registry, installed skills, TUI themes/state, preferences) | `<project-root>/.kazma/` | `paths.user_home()` — override `KAZMA_USER_HOME` |
| Legacy home | `~/.kazma` | Migration source only (`migrate_legacy_user_home` / `merge_legacy_hub_if_empty`) |
| Active coding workspace | scope → WorkspaceStore → pin → `KAZMA_WORKSPACE` → `{data_dir}/workspace` | `workspace.binding.resolve_active_root` |
| Cloned user repos | `~/kazma-repos` | `KAZMA_CLONE_DIR` / gateway clone helpers |

Project root is **not** “wherever you happened to `cd`” when a parent directory contains `pyproject.toml`. Launching from a subdirectory of the repo still anchors DBs under the monorepo root.

## Invariants (keep these)

1. **No hardcoded user homes** — never ship `/home/alice/...` in production code. Use `paths.user_home()` / `data_dir()` / binding SoT.
2. **No new writes to `~/.kazma`** — legacy is migration/read fallback only.
3. **No `/tmp` or `/var/log` in config defaults** — use project-relative or env-overridable paths.
4. **Prefer `pathlib`** — OS separators and drive letters stay correct on Windows.
5. **Config over code** — LLM base URLs, storage paths, skill dirs → env / YAML.
6. **Workspace + MCP share one root** — `workspace_bound` MCP servers rebind on Switch Repo via `${KAZMA_ACTIVE_WORKSPACE}`.
7. **Optional OS branching for security only** — e.g. `code_exec` resource limits (POSIX) vs Job Objects (Windows).

## Deployment matrix

| Target | How |
|--------|-----|
| Linux / macOS | `setup.sh` or `uv sync` / `pip install -e ".[rag]"` then `kazma serve` |
| Windows native | `setup.ps1` + `.venv\Scripts\Activate.ps1` + `kazma serve` |
| WSL2 | Unix install inside WSL; optional [fixed Windows access](wsl-fixed-access) for host browser |
