---
sidebar_position: 1
---

# Kazma Hub Overview

Kazma Hub is the **local skill registry and CLI** for discovering, validating, signing, and installing agent skills.  
A remote marketplace API may be configured (`hub_url`); many flows work fully offline against the local SQLite registry.

## What exists today (verified)

| Capability | Status | CLI |
|------------|--------|-----|
| Search registry | **Live** | `kazma hub search` |
| Install / uninstall | **Live** (registry) | `kazma hub install` / `uninstall` |
| List / info | **Live** | `kazma hub list` / `info` |
| Register local path | **Live** | `kazma hub register` |
| Validate manifest | **Live** | `kazma hub validate` |
| Sign skill (HMAC) | **Live** | `kazma hub sign` |
| Certification check (local) | **Live** | `kazma hub check-certification` |
| Submit to remote hub API | **Live only if hub API is up** | `kazma hub submit` / `status` |
| `kazma hub publish` | **Does not exist** | Use `register` + optional `submit` |
| Interactive wizard | **Live** | `kazma wizard` |
| Built-in native skills | **Live** | Auto-loaded from `kazma-skills/…/native/` |

Full command list (from `kazma_core/hub/cli.py`):

```text
search, install, list, uninstall, info, register, validate, sign,
submit, status, stats, badge, certified, check-certification
```

## Quick commands

```bash
kazma hub search "vault"
kazma hub install author/skill-name
kazma hub list
kazma hub info author/skill-name
kazma hub validate ./my-skill
kazma hub sign ./my-skill          # requires KAZMA_SECRET
kazma hub check-certification ./my-skill
kazma wizard
```

## Two skill shapes

1. **Native tools skills** (recommended for product tools) — `skill_manifest.yaml` with a `tools:` map + `tools.py` callables, loaded by `NativeSkillLoader` into `LocalToolRegistry`. See [Creating skills](../skill-development/creating-skills).  
2. **Hub package skills** — directory with manifest (+ optional entry point) registered via the hub CLI.

## Honesty notes

- Offline security scanners under `kazma_core/security/*` exist as libraries; hub `validate` runs the wired validator path — not every scanner module is invoked on install.  
- Certification **levels / badges** in older docs were aspirational UI; use `check-certification` for local requirement scoring. Remote “premium marketplace” review is only as real as your hub API deployment.  
- Prefer [Tools catalog](../reference/tools-catalog) for the complete list of built-in + native tools.

## Next steps

- [Finding skills](./finding-skills)  
- [Publishing / registering skills](./publishing-skills)  
- [Security auditing](./security-auditing)  
- [Creating skills](../skill-development/creating-skills)  
