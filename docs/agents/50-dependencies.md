<!-- AGENTS.md, piece 50-dependencies. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 50. Dependency floors reach every install, and the server says when one is behind (`kazma_core/install_requirements.py`, 2026-10-02)

The rewritten report (§49) found what OSV knows about the live install's
packages: 51 advisories in 14 packages (PyJWT, pypdf, urllib3, aiohttp,
cryptography...). `uv.lock` still pinned kazma 0.10.0 and the `aiogram` removed
on 2026-09-30, and CI's `uv export --frozen` exported it without asking
whether it matched `pyproject.toml`. Dependabot watched the `pip` ecosystem,
which never touches `uv.lock` and finds nothing to raise in `>=` minimums, so
no Python update ever arrived.

- **A minimum in `pyproject.toml` is a security floor.** It sits at the
  release that fixes the advisory, and also covers packages Kazma runs on
  without importing them by name. Base: urllib3, anyio, pyasn1. Per extra:
  aiohttp (push, rag), torch, transformers, oauthlib and setuptools (rag,
  docling), and virtualenv (dev). `kazma update` installs additively
  (`uv pip install -e`) and keeps any installed version that satisfies the
  range, so only a minimum makes an existing install upgrade. The lock changes
  only fresh installs.
- **The lock must match:** CI and the release SBOM run `uv export --locked`.
- **An update installs the locked versions** (2026-11-14). A checkout's
  install (`update._reinstall_local` -> `_install_editable`) passes `-c` with
  its `uv.lock` exported (`update._locked_versions`: `--locked`, every extra;
  a lock that no longer matches is not used, and the update says so). A wheel
  carries `LOCKED_VERSIONS_MEMBER` (`kazma_cli/locked_versions.txt`), written
  by the release workflow before `uv build` and kept in the wheel by hatch's
  `artifacts` although git ignores it, so the wheel's Sigstore signature
  covers it; `do_pip_update` reads it from the verified wheel
  (`_locked_versions_in_wheel`) and installs with it. A file that is not
  exact pins (`_LOCKED_LINE`: an option line could name another index) stops
  the update; a wheel with none (0.21.2 and older) installs unpinned, said.
  The release workflow fails a wheel that carries none. Gate:
  `tests/test_update_installs_tested_versions.py` (a stale lock and hostile
  files as negative controls).
- **Every release is tested as an upgrade from the one before**
  (`scripts/upgrade_drill.py`, 2026-11-14): the previous release's wheel,
  checked as the updater checks one and installed with its own locked
  versions (inside it, else its tag's `uv.lock`), runs on a private data
  folder; the new wheel is installed over it with its locked versions; it must
  report the new version, answer `/health/live` and `/health/ready` (or
  `needs_setup` only), keep a setting and every database the old one made, and
  log no ERROR line or traceback. Nothing of an install on the machine is read:
  `scrubbed_env` drops every `KAZMA_*` and credential variable and the servers
  start in an empty folder. The release workflow runs it before publishing; CI
  runs it on Linux and Windows on every push (`--any-version`: `main`'s version
  moves only at a release). Gate: `tests/test_upgrade_drill.py` (the whole
  drill opt-in with `KAZMA_TEST_UPGRADE_DRILL=1`).
  Dependabot uses the `uv` ecosystem.
- **The server says when it is behind.** A deploy is a `git pull` and installs
  nothing. At boot (background thread) `report_unmet_requirements` compares
  the installed packages with what this build declares and lists every
  requirement that is not met: a base requirement must be installed in range;
  an extra binds only a package that is installed. For a checkout "declares"
  means its `pyproject.toml`, because the installed metadata goes stale with
  every pull (the dev venv's still named `aiogram`). On a finding it logs a
  WARNING and raises the ops alert `install.requirements_unmet`. The fix is
  `kazma update --reinstall -y` (`install_requirements._update_instructions`).
  **The update stops Kazma itself, through the guard** (`update._ServerHold`,
  2026-10-02): the running server holds its packages' compiled files open on
  Windows, so a reinstall under it fails half way, and the guard restarts a
  server stopped any other way within seconds. The update waits until no chat
  turn runs, pauses supervision under its own reason, and waits for the
  guard's word that it holds (`stop_for_maintenance`; the gate before every
  spawn records `pause_held`, a guard too old to is judged by no child and
  nothing answering), then installs, lifts its pause
  (`resume_after_maintenance`, which never lifts another's) and waits for
  Kazma to answer. The hold covers the package step alone: the git update's
  pull runs under the server, and a refusal there puts the checkout back. A
  failed install keeps Kazma stopped (the guard would restart, again and
  again, a server that may not boot); the repair is the same command, which
  takes the pause over. **A failed install is repaired, never left partway**
  (2026-11-13): the additive `uv pip install -e` first, then every package
  rewritten (`--reinstall`, `_REPAIR_PAUSE_S` apart, twice at most), each
  judged by importing Kazma in a child (`update._environment_problem`:
  `kazma_core, kazma_cli, kazma_gateway, kazma_ui.app, dotenv`; a
  `find_spec` passed a package whose files were gone); the whole output of
  every step in `.kazma/update.log`. No `uv sync` fallback: it moved every
  package to uv.lock's version and, failing partway on live, removed about
  200 packages, Kazma among them (18 minutes down). After the packages,
  `update._bring_along` downloads the Chromium build the installed
  Playwright launches (`browser_isolation.chromium_installed` asks for that
  revision, in `chromium-<rev>` and `chromium_headless_shell-<rev>`, not any
  build) and puts each container setup made from this install's compose
  files on its pinned image (`container_services.refresh_images`: compose's
  project, service AND config-file labels must be this install's -- a
  checkout and an install in folders both called `kazma` share a project),
  never one whose secret `.env` lacks (`container_services._missing_secrets`:
  an update writes no secret, and recreated, SearXNG took the shipped
  placeholder key -- live 2026-11-14, on an install set up before setup
  generated it; the update names `kazma setup install --with <part>`).
  After a recreate it removes the image the container ran before
  (`container_services._remove_replaced_image`: exactly that image, by id,
  never forced, nothing when the container still runs it; a refusal is said
  as `updated; old image kept: <why>`): every update left the old version,
  about 4.8 GB on the live engine by 2026-11-14.
  Gates: `tests/test_update_repairs_a_broken_environment.py`,
  `tests/test_update_brings_chromium_and_images.py` (the old check, the old
  any-build rule and the project-only rule as controls). A pause also stops a server that is still booting, and wakes the
  backoff and crash-loop cooldown. Every installer (`_reinstall_local`,
  `do_pip_update`) refuses under a running server itself
  (`test_every_installer_checks_the_server_before_it_installs`), so a caller
  that skips the hold cannot replace packages under it; the wheel update and
  the extras repair had no server check at all. The same morning's fix
  refused and printed three guard commands to type around it.
  `tests/test_package_update_on_a_guarded_install.py`, and the real guard in
  `tests/test_guard_integration.py`. **The update runs as
  `python -m kazma_cli update`, never from a launcher it must replace**
  (Windows): a reinstall replaces `kazma.exe`, and Windows lets nothing
  replace -- or rename -- a running uv launcher (Python keeps the zip
  appended to it open). Live 2026-10-02 every reinstall from `kazma.exe`
  failed half way and left Kazma's own package half removed; renaming the
  launchers aside, tried that morning with a running `ping.exe` as its test,
  failed on the real launcher the same afternoon. So the update checks
  first (`update._launchers_in_use`: opening a launcher for writing fails
  while it runs, and writes nothing), refuses before it installs or pulls
  anything (pip does the same from `pip.exe`), and names the command;
  every instruction (the refusal, the boot alert, the guard's hints, the
  docs) names `<install python> -m kazma_cli update`.
  `tests/test_update_not_from_its_launcher.py` (a copy of a real uv launcher
  held by a running test; the rename it cannot do is the negative control
  of the old fix). The report's dependency check lists the same
  packages, without OSV. `packaging` is a declared dependency for this.
- **Advisories with no fix that Kazma cannot reach are reviewed, never
  hidden** (`dependency_scanner.REVIEWED_ADVISORIES`). Each entry has its
  reason and `reachable_if`, the code that would make the advisory reachable.
  Today: chromadb's Chroma-server advisories (Kazma embeds Chroma; no server,
  no `HttpClient`) and accelerate's `load_checkpoint_in_model`. The report
  names them as reviewed. A hub skill's scan does not apply the reviews.
- Gates: `tests/test_install_requirements.py` (pyproject over stale metadata
  as the negative control, extras, markers, self-references, the boot call),
  and `tests/test_dependency_scanner.py`. That file checks that no reviewed
  advisory's `reachable_if` matches product code, with planted uses as the
  controls, and that an unreviewed advisory fails the check.

