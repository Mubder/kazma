# Versioning & Releases

Kazma uses **industrial automatic versioning**. You never choose a version number
or release date by hand — **conventional commits** drive everything.

## Current product line

| Version | Notes |
|---------|--------|
| **0.6.0** | Restored product line (2026-07-21). After mistaken `0.1–0.2` auto-releases. |
| 0.5.0 | Last intentional product version before automation glitch |
| 0.2.x / 0.1.0 | **Invalid line** — created when semantic-release had no `v0.5.0` tag and restarted from zero. Tags may still exist for history; **do not ship from them**. |

Baseline tag for automation: **`v0.6.0`**. Next releases: `0.6.1` (patch), `0.7.0` (minor), etc.

## Config vs updates (no dirty-yaml hell)

| Layer | Where | Git? | Day-to-day edits? |
|-------|--------|------|-------------------|
| Shipped defaults | `kazma.yaml` | **tracked** | **No** — product defaults only |
| Local file overrides | `kazma.local.yaml` | **ignored** | Optional (ports, flags) |
| Runtime / UI | `kazma-data/settings.db` | ignored | **Yes** (Settings, `/config`) |

**Rule for operators:** never put machine secrets or ports only in tracked `kazma.yaml`.
Use the Web Settings UI, or copy `kazma.local.yaml.example` → `kazma.local.yaml`.

`kazma update` auto-stashes accidental dirty files, but the structural fix above means
users should rarely need that.

## How it works

| Commit type | Version bump | Example |
|-------------|--------------|---------|
| `feat:` | **minor** (`0.5.0` → `0.6.0`) | New Agent Skills install |
| `fix:` / `perf:` / `refactor:` | **patch** (`0.5.0` → `0.5.1`) | Typing keepalive bugfix |
| `feat!:` or footer `BREAKING CHANGE:` | **major** (`0.5.0` → `1.0.0`) | Breaking API |
| `chore:` / `docs:` / `test:` / `ci:` | no release | CI, docs only |

On every push to `main`, GitHub Actions runs **python-semantic-release**:

1. Reads commits since the last `v*` tag  
2. Bumps `pyproject.toml` + `kazma.yaml` `agent.version`  
3. Updates `CHANGELOG.md`  
4. Creates tag `vX.Y.Z` + GitHub Release  

Workflow: `.github/workflows/release.yml`

## Commit message format (required)

```
feat(skills): install Agent Skills without Node

fix(gateway): keep Telegram typing alive during agent runs

docs: explain versioning policy
```

Scope in parentheses is optional but recommended.

## Source of truth

| File | Role |
|------|------|
| `pyproject.toml` → `project.version` | Canonical package version |
| `kazma.yaml` → `agent.version` | Runtime/agent banner (kept in sync by release job) |
| `CHANGELOG.md` | Human-readable history (auto-appended) |
| Git tags `v*` | Immutable release markers |

## Manual override

Actions → **Release** → Run workflow → set `force_level` to `patch`, `minor`, or `major`.

## Local dry-run

```bash
pip install python-semantic-release==9.21.1
semantic-release version --print   # show next version without writing
```

## What not to do

- Do **not** hand-edit version numbers for product releases  
- Do **not** use vague commits like `update stuff` if you want a release  
- Use `feat` / `fix` when the change should ship a version bump  
