# Versioning & Releases

Kazma uses **light 0.x versioning** with an embedded **git commit id**.

## Format

| Piece | Example | Meaning |
|-------|---------|---------|
| Public SemVer | `0.12.1` | What tags use (`v0.12.1`) |
| Full product version | `0.12.1+g92c55af` | Stored in `pyproject.toml` + `kazma.yaml` |
| `+g……` | PEP 440 **local** segment | Short git SHA (`g` = git). Portable “commit code”. |

Why not `0.12.1.abs1234` / four dots? That is **not** PEP 440 and breaks
packaging / installers. `+g92c55af` is the standard way to attach a commit id.

## What moves automatically

| Digit | Example | Auto on push to `main`? | How to change |
|-------|---------|-------------------------|---------------|
| **patch** (last) | `0.12.**1**` | **Yes** — every light release | `version-bump.yml` / Release `patch` |
| **minor** (middle) | `0.**12**.1` | **Never** | Manual **Release** workflow + type **`CONFIRM`** |
| **major** (first) | `**0**.12.1` | **Never** | Manual **Release** + **`CONFIRM`** (real 1.0.0) |

This is why we no longer leap `0.6 → 0.10 → 0.11 → 0.12` in one day from
ordinary `feat:` merges. The middle digit is a **milestone**, not a feature
counter.

## Bump policy

| Trigger | Result |
|---------|--------|
| Push to `main` (feat/fix/…) | `0.12.N` → `0.12.N+1` **+ new `+gSHA`** |
| Release workflow `patch` | Same light step |
| Release workflow `minor` + `confirm=CONFIRM` | `0.12.x` → `0.13.0+g…` |
| Release workflow `major` + `confirm=CONFIRM` | `0.x` → `1.0.0+g…` |
| Release minor/major **without** `CONFIRM` | **Refused** |

Script SoT: `scripts/light_version_bump.py`  
Workflows:

- `.github/workflows/version-bump.yml` — auto **patch only**
- `.github/workflows/release.yml` — manual; minor/major need confirmation

## Commit messages

Still use conventional commits for humans/changelog:

```
feat(skills): …
fix(gateway): …
```

They no longer drive a **minor** jump. Auto release is always **patch**.

## Source of truth

| File | Role |
|------|------|
| `pyproject.toml` → `project.version` | Full version incl. `+gSHA` |
| `kazma.yaml` → `agent.version` | Same full string (banner) |
| Git tag `v0.12.1` | Public immutable marker (no `+`) |
| `CHANGELOG.md` | Human history |

## Local dry-run

```bash
python scripts/light_version_bump.py --dry-run
python scripts/light_version_bump.py --level patch --write   # local only
python scripts/light_version_bump.py --level minor --confirm CONFIRM --dry-run
```

## What not to do

- Do **not** map `feat` back to minor in Commitizen / semantic-release  
- Do **not** hand-edit versions for routine releases  
- Do **not** bump the middle digit without Release + `CONFIRM`  
- Do **not** set `allow_zero_version = false` (forced a fake 1.0.0 once)  
