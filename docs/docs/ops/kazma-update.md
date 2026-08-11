---
id: kazma-update
title: Kazma Update (operator upgrade)
sidebar_label: Kazma Update
description: Primary operator path to upgrade a git install — stash, reset main, reinstall, verify.
---

# Kazma Update

**`kazma update` is the supported operator upgrade path** for git / monorepo
installs. Prefer it over a manual `git pull` so local edits, optional extras,
and CLI health are handled consistently.

## What it does (git install)

1. **Preflight** — git available, no `.git/index.lock`, branch is `main`/`master`
   (or `--sync-main`), no local commits ahead of `origin/main` unless you
   explicitly accept discarding them.
2. **Named stash** — tracked + untracked local files (`git stash push -u` with a
   unique `kazma-update-…` message). Ignored files (`.env`, secrets) stay put.
3. **Fetch + hard reset** to `origin/main` (no merge commit prompts).
4. **Restore stash by name** (conflicts keep the stash for manual recovery).
5. **Reinstall** editable package with preserved extras (fresh Python process).
6. **Postflight** — HEAD matches `origin/main` and `kazma_cli` / `kazma_core`
   import. Failure is reported; the CLI will not claim “complete” if broken.

State while running: `kazma-data/update-state.json` (cleared on success).

## Commands

```bash
kazma update              # check + confirm + sync main + reinstall
kazma update -y           # non-interactive
kazma update --check      # dry-run only
kazma update --reinstall -y   # packages only (repair wiped venv)
kazma update --sync-main  # checkout main first (from a feature branch)
kazma update --accept-discard-local-commits
                          # allow hard-reset when you have local commits on main
```

## Safety rules

| Situation | Behavior |
|---|---|
| On `main`, behind remote, dirty untracked work | Stash → reset → restore → reinstall |
| On a feature branch | **Refuse** hard-reset; use `--sync-main` or switch yourself |
| Local commits ahead of `origin/main` | **Refuse** unless `--accept-discard-local-commits` |
| Detached HEAD / index.lock | **Refuse** with recovery hints |
| Reinstall leaves CLI broken | Update **fails** with repair commands |

## Repair after a broken reinstall

```powershell
# PowerShell (Windows)
Remove-Item -Recurse -Force .\.venv\Lib\site-packages\~azma* -ErrorAction SilentlyContinue
uv pip install --python .\.venv\Scripts\python.exe -e ".[rag,tui,document-platform]"
python -c "import kazma_cli; print('ok')"
kazma serve
```

Or: `kazma update --reinstall -y` once the CLI is importable enough to run, or
after fixing the venv with `uv pip install` as above.

## Developer clones

Use normal git on feature branches. Do not rely on hard-reset update while
mid-feature. When you want production main: `git checkout main` then
`kazma update`, or `kazma update --sync-main`.
