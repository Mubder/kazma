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

## When the server says its packages are behind

A `git pull` brings new code but installs nothing. When a commit raises a minimum version in `pyproject.toml` (a security floor, or a release a new feature needs), the server checks at boot and logs a WARNING naming each package and the version the build requires. It also raises the ops alert `install.requirements_unmet`, and the security report's dependency check lists the same packages. The packages cannot be replaced while the server has them loaded: on Windows the reinstall would fail half way and leave some packages new and some old. So `kazma update` refuses while the server answers, and says what to run instead.

On an install the guard supervises (the `KazmaAgent` task), run these from the install folder, with the install's own `kazma`:

```bash
python scripts/service/kazma_guard.py --pause --stop --when-idle --reason "package update"
kazma update --reinstall -y
python scripts/service/kazma_guard.py --resume
```

The first command waits until no chat turn is running; then the guard stops the server gracefully and keeps it stopped. If you forget the third command, the pause lifts itself after two hours. Without the guard: stop the server, run `kazma update --reinstall -y`, and start the server again.

`--reinstall` is the packages-only path: it keeps your optional extras and touches no git state.

## Repair after a broken reinstall

A reinstall that fails half way can leave Kazma's own package half removed: `~azma*` folders in `site-packages`, and `import kazma_cli` fails, so the server cannot start. Until 2026-10-02 every reinstall run from `kazma.exe` on Windows ended this way ("failed to remove file ... kazma.exe"): the update has to replace the launcher it runs from, and Windows locks a running program. The update now renames its launchers aside first and puts back any it did not replace.

To repair, with the server stopped, in the install folder:

```powershell
# PowerShell (Windows)
Remove-Item -Recurse -Force .\.venv\Lib\site-packages\~azma* -ErrorAction SilentlyContinue
uv pip install --python .\.venv\Scripts\python.exe -e ".[rag,tui,document-platform]"
.\.venv\Scripts\python.exe -c "import kazma_cli; print('ok')"
```

Put your install's own extras in the brackets: the ones `kazma update` prints under "Preserving optional extras". Then start the server again: on a guarded install `python scripts/service/kazma_guard.py --resume`, otherwise `kazma serve`.

Or: `kazma update --reinstall -y` once the CLI is importable enough to run, or
after fixing the venv with `uv pip install` as above.

## Developer clones

Use normal git on feature branches. Do not rely on hard-reset update while
mid-feature. When you want production main: `git checkout main` then
`kazma update`, or `kazma update --sync-main`.
