# Versioning & Releases

Kazma uses **light automatic versioning on the 0.x line**. Day-to-day work
should feel like small steps (`0.12.0` → `0.12.1` → `0.12.2`), **not** a hard
minor jump on every feature (`0.10` → `0.11` → `0.12`).

Four-part schemes like `0.10.1.0011` are **not** used (PEP 440 / PyPI prefer
`X.Y.Z`). The same “light” intent is expressed as a **rising patch**:
`0.12.11`, `0.12.12`, …

## Current product line

| Version | Notes |
|---------|--------|
| **0.12.x** | Active line (`pyproject.toml` is source of truth) |
| 0.10.x – 0.11.x | Recent line; minors jumped too hard when `feat` mapped to MINOR |
| 0.6.x tags | Older tags; may lag `pyproject` after dual-tool history |
| **1.0.0** | **Not** a real launch unless we ship it deliberately |

## Bump policy (0.x light)

| Commit type | Auto bump | Example |
|-------------|-----------|---------|
| `feat:` | **patch** | `0.12.0` → `0.12.1` |
| `fix:` / `perf:` / `refactor:` | **patch** | `0.12.1` → `0.12.2` |
| `feat!:` / `BREAKING CHANGE:` / `break:` | **minor** | `0.12.5` → `0.13.0` (largest auto step while major stays 0) |
| `chore:` / `docs:` / `test:` / `ci:` | no release bump | tooling only |
| Manual **major** | only via Release workflow | deliberate `1.0.0` |

**Invariant:** under 0.x, **`feat` never jumps the middle digit**. That was the
bug that produced 0.10 → 0.11 → 0.12 on ordinary feature merges.

## Config vs updates (no dirty-yaml hell)

| Layer | Where | Git? | Day-to-day edits? |
|-------|--------|------|-------------------|
| Shipped defaults | `kazma.yaml` | **tracked** | **No** — product defaults only |
| Local file overrides | `kazma.local.yaml` | **ignored** | Optional (ports, flags) |
| Runtime / UI | `kazma-data/settings.db` | ignored | **Yes** (Settings, `/config`) |

**Rule for operators:** never put machine secrets or ports only in tracked `kazma.yaml`.
Use the Web Settings UI, or copy `kazma.local.yaml.example` → `kazma.local.yaml`.

## How automation works

| Tool | When | Role |
|------|------|------|
| **Commitizen** (`version-bump.yml`) | Push to `main` | Auto tag + bump using **light** `cz_customize` map |
| **python-semantic-release** (`release.yml`) | Manual workflow only | Controlled `patch` / `minor` / `major` |

Both tools share the same intent: **patch by default**, minor only for
breakers or an intentional manual minor.

Workflows:

- `.github/workflows/version-bump.yml` — light auto bump on main  
- `.github/workflows/release.yml` — manual Release (default **patch**)

## Commit message format

```
feat(skills): install Agent Skills without Node

fix(gateway): keep Telegram typing alive during agent runs

docs: explain versioning policy
```

Scope in parentheses is optional but recommended.

## Source of truth

| File | Role |
|------|------|
| `pyproject.toml` → `project.version` | **Canonical** package version |
| `kazma.yaml` → `agent.version` | Runtime banner (kept in sync by bump tools) |
| `CHANGELOG.md` | Human-readable history |
| Git tags `v*` | Immutable release markers |

## Manual override

Actions → **Release** → Run workflow → `force_level`:

- **patch** (default) — light step  
- **minor** — notable 0.x milestone  
- **major** — only when shipping a real 1.0.0+  

## Local dry-run

```bash
pip install commitizen python-semantic-release==9.21.1
cz bump --dry-run
semantic-release version --print
```

## What not to do

- Do **not** hand-edit version numbers for routine product releases  
- Do **not** map `feat` back to **minor** in config (reintroduces hard jumps)  
- Do **not** set `allow_zero_version = false` (forced a fake 1.0.0 before)  
- Use `feat` / `fix` when the change should ship a **patch** bump  
- Use `feat!` / `BREAKING CHANGE` only when a real compatibility break needs a **minor** under 0.x  
