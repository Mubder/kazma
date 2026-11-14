---
id: kazma-update
title: Kazma Update (operator upgrade)
sidebar_label: Kazma Update
description: Primary operator path to upgrade a git install — stash, reset main, reinstall, verify.
---

# Kazma Update

> This page is published at [kazma.ai/docs/ops/kazma-update/](https://kazma.ai/docs/ops/kazma-update/) ([Arabic](https://kazma.ai/ar/docs/ops/kazma-update/)).

**`kazma update` is the supported operator upgrade path** for git / monorepo
installs. Prefer it over a manual `git pull` so local edits, optional extras,
and CLI health are handled consistently.

## The short version

From the install folder, with the install's own Python
(`.venv\Scripts\python.exe` on Windows, `.venv/bin/python` elsewhere):

```bash
python -m kazma_cli update
```

That is the whole update. Then, only when it applies:

| When | Run |
|---|---|
| You pulled with `git pull` yourself, or the server reports its packages are behind (`install.requirements_unmet`) | `python -m kazma_cli update --reinstall -y` |
| Documents run in the container profile | `python scripts/document_worker_image.py build` ([below](#documents-after-an-update)) |

Three things break an install, and the update does none of them:

- **A bare `uv sync`.** uv makes the environment match exactly the extras
  named on its command line and removes every other package: run without
  `--extra`, it takes away the optional extras (vector memory, the TUI,
  OCR, the document platform...) and every package installed from Settings
  → Packages. The update installs additively (`uv pip install -e
  ".[your extras]"` with the locked versions), never with `uv sync`. If you run uv yourself, use
  `uv sync --inexact --extra rag --extra tui`. If a sync already removed
  packages, `python -m kazma_cli update --reinstall -y` puts your extras back
  (it remembers them in `system.installed_extras`).
- **An install that fails partway.** On Windows a file another program holds
  open can stop an install after it has removed packages. The update then
  rewrites every package (`uv pip install --reinstall`), at most twice with a
  short pause, and counts the update done only when Kazma imports in a fresh
  Python. Every step's whole output is in `.kazma/update.log`. If it still
  fails, Kazma stays stopped and the update names the error.
- **New packages need something outside Python.** After the packages, the
  update downloads the Chromium build the installed Playwright launches
  (only where the browser tools were set up), and puts each container setup
  made from this install's compose files (SearXNG, Neo4j) on the image the
  file now pins. A container of yours under another name is left alone.
- **`kazma update` on Windows.** The update replaces `kazma.exe`, and Windows
  will not replace a running launcher; started from it, the update refuses
  and names `python -m kazma_cli update`.
- **Installing Kazma from PyPI by name.** The Kazma names there are held
  placeholders with no code; Kazma installs from this repository and its
  GitHub releases only.

### What versions it installs

The update installs the versions Kazma's tests ran with: the ones `uv.lock`
names (since 2026-11-14).

- **A git install** installs with its own `uv.lock` as constraints (`uv
  export --locked`, every extra's pins; an install takes only the packages it
  needs). A package installed at another version is moved to the locked one.
  If the lock no longer matches `pyproject.toml` (an edited checkout), the
  update says so and installs the newest versions `pyproject.toml` allows.
- **A wheel install** reads the locked versions from inside the release's
  wheel (`kazma_cli/locked_versions.txt`, written by the release workflow
  before it builds, so the wheel's signature covers them) and installs with
  them. A release from 0.21.2 or older carries none: it installs as before,
  and the update says so. A file inside a wheel that is not exact pins stops
  the update.

When a security advisory is fixed, the fixed release becomes Kazma's minimum
in `pyproject.toml` and the lock moves with it, so the next update installs
it; until then the server says at boot which installed packages are below
what the build requires (see
[below](#when-the-server-says-its-packages-are-behind)).

### Every release is tested as an upgrade {#upgrade-drill}

Before a release is published, the release workflow upgrades the release
before it to the new one and checks that Kazma still runs on its data
(`scripts/upgrade_drill.py`, since 2026-11-14):

1. the previous release's wheel, checked as `kazma update` checks one,
   installed in a fresh environment with the versions it was tested with;
2. it starts on a data folder of its own, answers, stops, and a setting is
   written into that data;
3. the new wheel is installed over it with the versions inside it, as
   `kazma update` installs a wheel;
4. it must report the new version, start on the same data, answer as ready
   (or as waiting only for a model to be set up), keep the setting and every
   database the old release made, and log no error.

A release that fails it is not published. CI runs the same drill on every
push, on Linux and Windows, from the newest release to a wheel built from that
commit. Nothing of an install on the machine running it is read or written.

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

## Documents after an update

The document tables are brought up to date when the server next starts, on
SQLite and on Postgres. On Postgres the setup takes one advisory lock shared by
every Kazma schema setup and sends a statement only for a table, index or
column the catalog says is missing (`kazma_core/db/pg_schema.py`), so a start
does not wait behind a running backup's dump or deadlock with another start.

The update does **not** rebuild the document worker image. With the container
profile on a native install, run `python scripts/document_worker_image.py
build` right after the update; the guard's broker serves the older image
until then and switches by itself, and Kazma raises one ops alert naming the
build. Updating by hand avoids that alert: pull, build, then reload --

```bash
git pull
python scripts/document_worker_image.py build
python scripts/service/kazma_guard.py --reload --when-idle
```

-- so Kazma starts on the image for its code (the image is named after the
code it was built from, so a reload before the build finds only the old one).
Under docker compose, rebuild the image from `deploy/document-worker/Dockerfile`
and restart `document-broker`: the broker pins the image it started with. Until then readiness
reports `worker_isolation.image_code_matches: false` and
`document_worker_image_outdated` when the document code changed (the check
covers `kazma_core/documents` only, while the image holds all of
`kazma_core`). See
[Document processing → Isolated document workers](./document-processing#isolated-document-workers-container-profile).

## Open browser tabs after an update

A tab opened before the update keeps the page code it loaded. It shows
"Kazma was updated" with a **Refresh** button within 5 minutes, or as soon
as you return to it; refresh to load the new version.

## Developer clones

Use normal git on feature branches. Do not rely on hard-reset update while
mid-feature. When you want production main: `git checkout main` then
`kazma update`, or `kazma update --sync-main`.

## An update window for the machine's own updates

Updating the machine's other programs (Docker Desktop, the database, Windows
itself) stops what Kazma runs on for a while. Outside a window the guard
treats that as an outage: it restarts Kazma and pages you. Put the work
inside one:

```bash
kazma maintenance begin --reason "machine updates" --hours 2
```

It waits until no chat turn runs and pauses the guard under a maintenance
reason. Kazma keeps running; the guard restarts and pages nothing, and the
outside watch is told it is planned. The window closes by itself after
`--hours`, so a forgotten one cannot become a silent outage. When the
updates are done:

```bash
kazma maintenance end
```

It resumes the guard and waits up to five minutes for Kazma to be healthy
(`/health/deep`); if it is not, it pages you.

**topgrade** can open and close the window itself. In `topgrade.toml`:

```toml
[pre_commands]
"Kazma maintenance" = "kazma maintenance begin --reason topgrade --hours 2"

[post_commands]
"Kazma maintenance" = "kazma maintenance end"
```

Two machine settings keep updates inside the window (set them once):

- **Docker Desktop:** Settings → Software updates → turn off "Automatically
  check for updates", so it updates through topgrade inside the window.
- **Windows Update:** set active hours to your day, and turn on "Notify me
  when a restart is required". After a restart, the boot task and the
  database service bring everything back by themselves.

## Deploying to a live install from a checkout

`scripts/deploy_live.py --live <install>` pulls, runs the package update and
the document image build when their files changed, reloads when idle, and
checks the result: the new commit answering, ready and deep health, one test
turn kept out of memory, and no error in the log since the boot. When a check
fails it reverts the deployed commits on `main`, pulls the revert into the
install, reloads it and pages you with the check that failed.
