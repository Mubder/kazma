# Versioning & Releases

Kazma uses **industrial automatic versioning**. You never choose a version number
or release date by hand — **conventional commits** drive everything.

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
