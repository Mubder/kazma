# Versioning & Releases

Kazma uses a **fixed public base** plus a **live git commit id**.

## Format

| Piece | Example | Meaning |
|-------|---------|---------|
| Public base (files) | `0.9.4` | Root `pyproject.toml` / `kazma.yaml` / package versions |
| Display version | `0.9.4+g4d37b2c` | What CLI, banner, FastAPI, and UI show |
| `+g……` | PEP 440 **local** segment | Short git SHA (`g` = git) |

Why not bump `0.9.4` → `0.9.5` on every merge? That turned into
`0.10 → 0.11 → 0.12` noise in one day. The **SHA** is the accurate
build identity; the base is a human milestone.

## Source of truth

| Location | Role |
|----------|------|
| Root `pyproject.toml` → `project.version` | **Public base only** (`0.9.4`) |
| `kazma.yaml` → `agent.version` | Same public base |
| `kazma_core.version.get_version()` | **Runtime display**: `base+gSHA` |
| Git tag `v0.9.4` | Optional milestone marker (no `+`) |

```python
from kazma_core.version import get_version, get_base_version

get_base_version()  # "0.9.4"
get_version()       # "0.9.4+g4d37b2c"  (when git / CI SHA available)
```

### SHA resolution order

1. `KAZMA_GIT_SHA` (optional override)
2. `GITHUB_SHA` (Actions)
3. `git rev-parse --short=7 HEAD`
4. No SHA → show base alone (installed wheel without `.git`)

## What is NOT automated

| Former behaviour | Now |
|------------------|-----|
| Push to `main` → auto patch bump | **Disabled** (`.github/workflows/version-bump.yml` is a no-op) |
| `feat:` → minor leap | **Gone** |
| CI rewrites `pyproject.toml` | **Never** |

## When to change the base (`0.9.4` → `0.9.5` / `0.10.0`)

Only for a deliberate product milestone:

1. Edit **all** base sites to the new public version (no `+g…` in files):
   - `pyproject.toml`
   - `kazma.yaml` → `agent.version`
   - `kazma-gateway/pyproject.toml`
   - `kazma-tui` falls through to `kazma_core.version` (no hardcode needed)
2. Commit: `chore(version): base 0.9.4 → 0.9.5`
3. Optionally run **Actions → Release** to create tag `v0.9.5` + GitHub Release

Do **not** put `+gSHA` into committed version files. Display code adds it.

## Manual Release workflow

`.github/workflows/release.yml` (workflow_dispatch only):

* Resolves `base` from `pyproject.toml` + short SHA from `HEAD`
* Optionally creates annotated tag `v{base}` if missing
* Creates/updates a GitHub Release noting the full `base+gSHA`
* **Does not** edit version files

## Local check

```bash
python -c "from kazma_core.version import get_version; print(get_version())"
# → 0.9.4+g4d37b2c
```

## What not to do

- Do **not** re-enable auto bump on push to `main`
- Do **not** store `0.9.4+g…` in `pyproject.toml` (packaging noise; SHA goes stale)
- Do **not** map conventional-commit types to SemVer bumps in CI
- Do **not** set `allow_zero_version = false` (forced a fake 1.0.0 once)
