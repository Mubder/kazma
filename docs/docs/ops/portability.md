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
| **Project-local data** | Runtime DBs and workspace default under `kazma-data/` (travels with the checkout) |
| **User prefs separate** | Hub registry, installed skills, TUI themes under `~/.kazma/` (like `~/.gitconfig`) |
| **Configurable** | Paths and service URLs override via env / `kazma.yaml` — never machine-absolute homes in shipped code |

**Not claimed:** a pure “USB portable app” with *zero* host state. User prefs intentionally live in the home directory so they can be shared across checkouts.

## Data layout

| Category | Default location | Resolved by |
|----------|------------------|-------------|
| Project data (settings, checkpoints, swarm tasks, audit, RBAC, vectors, workspace) | `<project-root>/kazma-data/` | `kazma_core.paths` (`get_project_root()` walks up to `pyproject.toml`) |
| User data (hub, skills, TUI themes/state) | `~/.kazma/` | `Path.home() / ".kazma"` |
| Active coding workspace | Active WorkspaceStore → `KAZMA_WORKSPACE` → `kazma-data/workspace` | `file_write._get_workspace` / IDE service (same precedence) |

Project root is **not** “wherever you happened to `cd`” when a parent directory contains `pyproject.toml`. Launching from a subdirectory of the repo still anchors DBs under the monorepo root.

## Invariants (keep these)

1. **No hardcoded user homes** — never ship `/home/alice/...`, `/Users/...`, or `/mnt/c/Users/...` in production code. Use `Path.home()` / `expanduser("~")` / `paths.py`.
2. **No `/tmp` or `/var/log` in config defaults** — use project-relative or env-overridable paths.
3. **Prefer `pathlib`** — OS separators and drive letters stay correct on Windows.
4. **Config over code** — LLM base URLs, storage paths, skill dirs → env / YAML.
5. **Optional OS branching for security only** — e.g. `code_exec` resource limits (POSIX) vs Job Objects (Windows). Same product contract; sandbox strength may differ unless Docker jail is forced.
6. **No architecture conditionals** (`amd64` / `arm64`) in app code — Python + packaging handle that.
7. **No committed model weights** — fetch from hubs; tiny stubs only if needed.

## Deployment matrix

| Target | How |
|--------|-----|
| Linux / macOS | `setup.sh` or `uv sync` / `pip install -e ".[rag]"` then `kazma serve` |
| Windows native | `setup.ps1` + `.venv\Scripts\Activate.ps1` + `kazma serve` |
| WSL2 | Unix install inside WSL; optional [fixed Windows access](wsl-fixed-access) for host browser |
| Docker Compose | Primary production path — see [Deployment](../guide/deployment) |
| Kubernetes | Hub manifests are separate; main agent needs its own PVC for `kazma-data/` |

## Verifying

```bash
# Absolute-path lint (no /home, /Users, /root in shipped core/gateway)
./scripts/ci/lint-absolute-paths.sh

# Portability regression suite
python -m pytest tests/test_portability.py tests/test_portability_setup.py -q
```

## Related

- Path implementation: `kazma_core/paths.py`
- Deploy targets: [Deployment](../guide/deployment)
- Production go-live: [Production checklist](production-checklist)
- Env overrides: [Environment variables](../reference/environment-variables)
