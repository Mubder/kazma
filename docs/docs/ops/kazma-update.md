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
   A running server is stopped for this step alone, through its guard (see
   [below](#the-server-is-stopped-and-started-for-you)), and started again after.
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

## The server is stopped and started for you

Kazma's packages cannot be replaced while the server has them loaded: on Windows the install would fail half way and leave some packages new and some old. On an install the guard supervises (the `KazmaAgent` task), `kazma update` handles the server itself:

1. It waits until no chat turn is running. If one is still running after 15 minutes, it stops nothing, installs nothing, and says so.
2. It pauses the guard, which stops the server gracefully and keeps it stopped. The update installs nothing until the guard confirms it is holding.
3. It installs the packages.
4. It lifts the pause and waits until Kazma answers again, on the new packages.

The confirmation question says this before anything happens; `-y` skips the question. The pause is the same one `kazma_guard.py --pause` takes, under the update's own name. The guard sends its "supervision paused" and "resumed" messages as for any pause. A pause you took yourself is never lifted by the update.

**When the install fails**, Kazma stays stopped. Its packages may be half replaced, and the guard would keep restarting a server that may not boot. The update says so, and the repair is the same command: it takes over the pause and starts Kazma when it succeeds. To start Kazma as it is instead, run `python scripts/service/kazma_guard.py --resume`. Either way, the pause lifts itself after two hours and the guard then starts Kazma.

**Without a guard**, the update refuses a running server before anything changes: stop the server, run the update, and start the server again.

The git update stops the server for the reinstall alone. The pull runs while Kazma serves, as a deploy's pull does. If Kazma is still busy when the reinstall is due, the checkout is put back and nothing is installed.

## When the server says its packages are behind

A `git pull` brings new code but installs nothing. When a commit raises a minimum version in `pyproject.toml` (a security floor, or a release a new feature needs), the server checks at boot and logs a WARNING naming each package and the version the build requires. It also raises the ops alert `install.requirements_unmet`, and the security report's dependency check lists the same packages. The fix is one command, from the install folder:

```bash
python -m kazma_cli update --reinstall -y
```

`python` here is the install's own: `.venv\Scripts\python.exe` on Windows, `.venv/bin/python` elsewhere. A bare `python` may be another interpreter, or none; the alert prints the command with the right one. The update runs as `python -m kazma_cli update`, not `kazma update`: on Windows a reinstall must replace `kazma.exe`, and Windows lets nothing replace (or even rename) a launcher while it runs. Started from `kazma.exe` (or while the TUI runs), the update refuses before it installs anything and names the command to use.

`--reinstall` is the packages-only path: it keeps your optional extras and touches no git state. On a guarded install it stops and starts Kazma as [described above](#the-server-is-stopped-and-started-for-you).

## Repair after a broken reinstall

A reinstall that fails half way can leave Kazma's own package half removed: `~azma*` folders in `site-packages`, and `import kazma_cli` fails, so the server cannot start. Until 2026-10-02 every reinstall run from `kazma.exe` on Windows ended this way ("failed to remove file ... kazma.exe"): the update has to replace the launcher it runs from, and Windows locks a running launcher. The update now refuses to run from a launcher it must replace, before installing anything, and names the `python -m kazma_cli update` command to use instead.

To repair, with the server stopped, in the install folder:

```powershell
# PowerShell (Windows)
Remove-Item -Recurse -Force .\.venv\Lib\site-packages\~azma* -ErrorAction SilentlyContinue
uv pip install --python .\.venv\Scripts\python.exe -e ".[rag,tui,document-platform]"
.\.venv\Scripts\python.exe -c "import kazma_cli; print('ok')"
```

Put your install's own extras in the brackets: the ones `kazma update` prints under "Preserving optional extras". Then start the server again: on a guarded install `python scripts/service/kazma_guard.py --resume`, otherwise `kazma serve`.

Or: `python -m kazma_cli update --reinstall -y` once the CLI is importable enough to run, or
after fixing the venv with `uv pip install` as above.

## Developer clones

Use normal git on feature branches. Do not rely on hard-reset update while
mid-feature. When you want production main: `git checkout main` then
`kazma update`, or `kazma update --sync-main`.
