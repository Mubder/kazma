<!-- AGENTS.md, piece 71-windows-file-check. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 71. Kazma works with Windows' file check (`kazma_core/windows_trust.py`, 2026-11-26)

Live 2026-11-26, from 10:56 UTC: Windows began refusing unsigned programs
and libraries on the owner's PC (Smart App Control, `WinError 4551`; also
1260 and 5): Kazma's engine's `docker.exe`, and at the 11:30 reload
psycopg's bundled client library, so Kazma started read-only for thirty
minutes with its database answering. Smart App Control has no per-file
exceptions; a user cannot be asked to turn a protection off.

- **The owner's rule for every word users see** (binds this section): never
  tell anyone to turn off Smart App Control, SmartScreen, Defender or any
  Windows protection, nor show it as an obstacle. Say what Windows checked,
  what Kazma used instead, that nothing is wrong with the PC, and, when a
  feature truly cannot run, which feature and that an update brings a signed
  version. Smart App Control on is good news, never a finding. A fallback
  that worked is INFO with the operator's detail; only "a feature has no
  signed alternative here" is a WARNING, and it names the feature. English
  and Arabic as equals (`windows_trust.WORDS`). Gate:
  `tests/test_windows_trust.py::test_nothing_kazma_says_about_it_suggests_turning_a_protection_off`
  reads the words table, the guard's card, the fallbacks' log lines and the
  docs pages (EN and AR); a planted sentence in either language is its
  control. Text after `Detail:` quotes Windows itself and is not read.
- **One place knows, quietly** (`windows_trust`): `smart_app_control()` reads
  `HKLM\SYSTEM\CurrentControlSet\Control\CI\Policy`
  `VerifiedAndReputablePolicyState` (0 off, 1 on, 2 evaluation); `check()`
  probes each native part Kazma installs, loads or starts (`PARTS`: the
  database client library, the engine's docker CLI, the compose plugin, the
  engine's shutdown service) as `allowed`, `alternative` (a signed one in
  use), `waits` (no alternative: the feature waits) or `absent`. Readers:
  `kazma doctor` ("Windows' file check"; an alternative is `info`, a wait
  `warn`, never `FAIL`), `kazma setup system` and `kazma setup install`
  (notes, only what is news: `notes()`), the security report
  (`check_windows_file_check`: always passed, severity `info`), and the boot
  (`report_at_boot`: one INFO line; the one alert below). Every binary the
  engine setup downloads is a part or declared in `NOT_PROBED` with why
  (`test_every_binary_the_engine_setup_installs_is_a_part_or_declared`, a
  planted download as the control).
- **The fallbacks** stay where they were (§21, §63): `libpq_reach.reach_libpq`
  puts the installed PostgreSQL's signed `libpq.dll` on PATH when the bundled
  one is refused; `docker_cli._usable` passes over a CLI Windows refuses.
  Both say it at INFO now; libpq's WARNING is only "no signed PostgreSQL
  client here: reaching the database waits".
- **Docker through wsl.exe: one decision for every docker call**
  (`docker_cli.docker_command` / `docker_argv`, 2026-11-26). Kazma's own
  engine (context `kazma-engine`, its distro registered) is reached as
  `wsl.exe -d kazma-engine -u root --exec docker ...`: Microsoft's signed
  `wsl.exe`, the distro's own docker, compose and buildx, no Linux shell
  (`--exec`). Paths on this machine are given as the distro reads them
  (`_cross`: a whole argument, `--opt=<path>`, a `-v` source, a `--mount`
  `src=`/`source=`; drives at `/<letter>`); a network share or a removable
  drive takes the Windows CLI. A variable taken by name (`-e NAME`, and
  compose's interpolated names through `carry_env(names=)`) is carried in
  `WSLENV`, never on a command line. Docker Desktop, `DOCKER_HOST`, an
  explicit `KAZMA_DOCKER_BIN` and `KAZMA_DOCKER_ROUTE=cli` take the Windows
  CLI (`find_docker_cli`). A call through wsl.exe starts the distro when it
  is stopped, as the KazmaEngine task does. Measured (medians, the Windows CLI
  first): version 145/145 ms, exec 281/152 ms, run --rm 699/425 ms, 50 MB out
  551/316 ms, 20 MB in 334/198 ms, bytes intact; nothing is cached. Callers:
  readiness, code execution, `pg_bridge` (`docker_args_start` places
  `-e PGPASSWORD`), `pg_move`, `container_services` (a `Docker` is a CLI path
  or the route; `_made_from` accepts the compose label's distro path),
  install, uninstall, update's image refresh, setup, the compose upgrade,
  `engine_move`, the worker image build (absolute paths), the document broker
  (`_ROUTE`), the engine setup's `_docker`, prepush (`docker_route`), the
  guard's Docker watch (`_docker_route_argv`; the Windows CLI and its
  refusal handling only for Docker Desktop) and its reload preflight. Gate:
  `tests/test_docker_route.py` (each route's command, the controls, and no
  module that looks the CLI up itself unless declared); the boot drill reads
  the route (`docker_route` part, `scripts/engine_boot_drill.py`).
- **Signed first while Smart App Control is on** (`kazma_core/windows_policy.py`,
  the one reader of the setting, standard library; the guard loads it by
  path through `docker_cli`): measured with `Get-AuthenticodeSignature`,
  psycopg_binary's five files and the engine's static-zip `docker.exe` carry
  no signature, PostgreSQL 18's `libpq.dll` and every DLL it needs are
  signed by EnterpriseDB, Docker Desktop's `docker.exe` by Docker Inc. With
  the setting `on`, `reach_libpq` sets `PSYCOPG_IMPL=python` and puts the
  signed library on PATH without loading the bundled one (an operator's own
  `PSYCOPG_IMPL` is kept), and `find_docker_cli` takes Docker Desktop's CLI
  before the engine's (it reaches Kazma's engine through the same context);
  the unsigned file is never started. Off or in evaluation, nothing changes.
  `windows_trust` says it as `preferred`. Docker publishes no signed CLI
  outside Docker Desktop: the engine keeps the static one (it must work
  without Docker Desktop); signing it with Kazma's certificate is the
  owner's decision.
- **One alert**: `windows.feature_waits` (warn, Settings -> System), raised
  by `report_at_boot` only when a part `waits`; a part with an alternative is
  not in it. The guard's card for a reload kept back is `guard.reload_waits`
  (info, §39).
- **A reload never swaps a working server for one that cannot start here**
  (§39, `kazma_guard.run_reload_preflight`): before `_take_reload_request`
  stops the server, a child with the server's python, in its folder, loads
  `.env` (not under pytest), imports `kazma_core` (which runs
  `reach_libpq`), psycopg (`pq.__impl__`), `kazma_ui.app`, and starts the
  docker CLI `find_docker_cli` picks (`docker_cli.py` loaded by path: no
  settings store opened). Failing or past `KAZMA_GUARD_PREFLIGHT_TIMEOUT_S`
  (180 s), the server is kept: state `reload_action` `kept_running` and
  `reload_preflight`, `guard.reload_preflight_failed` in guard.log, one card
  per reason, and `--reload` exits `RELOAD_WAITS_EXIT` (4) saying the update
  waits. The heartbeat and the watches tick while it runs. `--reload
  --skip-check` switches without it. The running guard keeps its old code
  until the KazmaAgent task restarts (`guard_features` lists
  `reload_preflight`, so `--status` says so). Gates:
  `tests/test_guard_reload_preflight.py` (a real child with Windows' refusal
  planted as a `psycopg` in its folder; the clean folder as the control),
  `tests/test_guard_integration.py::test_a_reload_whose_new_code_cannot_start_keeps_the_server_running`
  (the real guard: kept, then switched once the plant is gone).
- **The outside audit (2026-11-26)**: `KAZMA_DOCKER_BIN` naming a file
  Windows refuses is said as that, at INFO under its own key, never "does
  not exist" (F1); `libpq_reach` looks first in the PostgreSQL `kazma setup
  postgres` recorded (`postgres_service.installed_bin_dir`, the one reader,
  which `pg_bridge` uses too) and passes over a `libpq.dll` on PATH Windows
  refuses (F2, F3); the refusal codes are `windows_policy.REFUSAL_CODES`
  only -- `docker_cli` (`refusal_codes()`), `libpq_reach`, the guard and
  prepush read it, prepush loads `docker_cli.py` by path instead of its copy
  (F6, F7; gate `test_windows_refusal_codes_have_one_definition`, a planted
  copy as control); a CLI that started is asked again after
  `STARTED_RECHECK_S` (900 s): the policy can arrive while Kazma runs.
- **A refused docker CLI is not a stopped engine** (F12): the guard's
  `Dependencies` asks `refused_by_os(cli, fresh=True)` when `docker version`
  could not even start its CLI; it takes the next CLI Windows allows
  (`deps.cli_replaced`) or waits (`cli_refused`, `refused_clis()` when none
  is found): no KazmaEngine task, no `systemctl restart docker`, never
  `deps.engine_down`; one card `deps.docker_cli_waits` names the feature, and
  `_deps_speaks_for` keeps the readiness wait from paging it twice. Gate:
  `tests/test_guard_dependencies.py` (a planted refusal; the engine really
  down as the control; a refused CLI replaced by an allowed one).
- **Kazma never starts its own launchers** (F8-F10): `kazma.exe`,
  `kazma-web.exe`, `kazma-tui.exe` are made on the user's PC by pip and
  nothing signs them. The guard, the KazmaAgent task, `kazma update`, the
  installer's shortcuts and setup already ran `python -m`; setup's hints and
  the MCP/ACP client configs (`mcp.server._client_config`, the docs) now do
  too. Gate: `test_kazma_never_starts_its_own_launchers` (names from
  pyproject's `[project.scripts]`; .py strings outside docstrings, .ps1/.cmd/
  .iss lines outside comments; a planted start as control). The engine's
  shutdown helper, built by `csc.exe` on the PC, is planned as a signed
  release asset (`docs/plans/WINDOWS_TRUST_PLAN.md`).
- **A new Windows install runs on a signed interpreter** (2026-11-26):
  `setup.ps1` `Find-SignedPython` takes the newest python.org Python
  (>= 3.11.5, < 3.15) whose `python.exe` reads Valid, signed by the Python
  Software Foundation, wherever it was found (the `py` launcher, its probes
  with `PYTHON_MANAGER_AUTOMATIC_INSTALL=false` so a probe never installs;
  the per-user and Program Files folders): 3.14, then 3.13; 3.12 and 3.11
  only when nothing newer is there, as python.org ships no patched Windows
  build of them any more (review M-b, L-7, 2026-11-27). With none,
  `Install-SignedPython` installs the pinned `Python.Python.3.14`
  (`$PythonWingetVersion`) with winget only with the owner's yes: `-Yes`
  installs, a person at the terminal is asked once, with nobody to ask
  nothing is installed and setup says so; a failure says winget's exit code
  and last line (M-c). The interpreter goes to `uv sync --python` for a new
  `.venv` only; an environment that is there keeps its interpreter.
  Measured: python.org 3.14 has no unsigned interpreter file outside Tcl/Tk
  (never loaded); uv's 3.12 build 41 of 49. Gate:
  `tests/test_setup_signed_python.py` (the functions run in PowerShell: a
  python.org interpreter chosen, uv's build passed over; the newest wins,
  3.12 only alone; each winget rule with stand-ins). Every file and its
  route: `docs/plans/WINDOWS_TRUST_INVENTORY.md`.
- **The review's fixes (2026-11-26)**: a docker call is recognised by
  `docker_cli.is_docker_exec` (the word after the route's own prefix), never
  by `"docker"` in argv[0] -- on the wsl.exe route argv[0] is `wsl.exe`, and
  `pg_bridge`, `restore_drill` and `restore_rehearsal` dumped to a refused
  port and read a backup as corrupt (H1; gate in `test_docker_route.py`
  finds argv-0 recognition, a planted one as control; the rehearsal's env
  goes through `carry_env`). `docker_command` applies the stored engine
  choice before deciding, and takes the wsl.exe route on the choice plus the
  distro's registration alone: a fresh PC whose Windows CLI is refused never
  needs the CLI to make its context (M1, M4). `windows_trust` reports the
  docker tool and compose as `alternative` (wsl.exe and the engine's own
  Docker) when that route is taken: no false `windows.feature_waits` (M2).
  `deploy_live` reads a reload's exit 4 from the guard's state and never
  reverts code Windows held back (M3, `server-deploy-details.md`). Lows: a
  timed-out code-exec container has a name and is killed by it (L1);
  `WSL_UTF8=1` on the route (L2); the guard asks the wsl.exe route first, so
  a wsl timeout is an engine that does not answer, never a refused CLI (L3);
  `KAZMA_DOCKER_ROUTE` reaches the broker (L4; a gate compares every
  variable `docker_cli.py` reads with `_BROKER_ENV_KEEP`, L-9); an explicit
  non-Kazma context
  always takes the CLI (L5); only docker's own path options are translated
  (`_cross_args`, L6); the preflight's detail is masked by value in the
  child (URL passwords and tokens) and again in the guard (the token table,
  loaded by path; withheld when it cannot load), and `windows_check` is
  decided before the masking (L7); the company-IT section names publisher
  rules only (L9); the launcher gate also finds a start by bare name
  (`run(["kazma", ...])`, `which("kazma-tui")`, a script line `kazma serve`;
  L10). `kazma update` (L8) and `--when-idle`'s window (L11) are in the VM
  test notes.
- **The delta review (2026-11-27)**: a process that loads `docker_cli.py` by
  path and never applies the choice (the guard's document broker) takes its
  parent's from `KAZMA_DOCKER_ENGINE_BY_CHOICE` (set by
  `apply_engine_choice` when Kazma's engine is chosen and reached through
  wsl.exe with no Windows context, cleared otherwise; kept for the broker),
  never from `KAZMA_DOCKER_ROUTE=wsl`, which would send Docker Desktop's
  calls to Kazma's distro (M-a). The docker tool and compose read
  `not_needed` on that route, with their own EN/AR words, and are not
  started to be judged (L-2, L-4). Docker's own path options only, up to
  the image or the container of a run, create or exec (`_program_start`,
  L-1); readiness's probe runs with the route's entries (L-3); the page of a
  deploy that waits says what a start without the check would do (L-5); the
  installer answer depends on Smart App Control (L-6); the launcher gate
  finds `args=`, shell strings, `cmd /c`, `.cmd` `call`/`start` and `.ps1`
  blocks (L-8).
- **The second delta review (2026-11-27)**: the broker is started again when
  the pair (`DOCKER_CONTEXT`, `KAZMA_DOCKER_ENGINE_BY_CHOICE`) changes, not
  the context alone: with no Windows context a choice that arrived or
  changed never reached a running broker (M-a2, `kazma_guard._broker_engine`).
  `docker_cli._cli_forced` is the one answer to "the Windows docker program
  is forced" (`KAZMA_DOCKER_ROUTE=cli`, `KAZMA_DOCKER_BIN`, `DOCKER_HOST`):
  the route, `engine_in_force` and the engine line read it, and the line
  says the CLI's current context and why (L-a). What the wsl.exe route adds
  (`WSL_ROUTE_ENV`, `DockerCommand.route_entries()`) reaches every caller
  that loads `docker_cli.py` by path: the broker's two calls, the guard's
  routed calls (for that call only: the server inherits the guard's
  environment), prepush; a gate finds a by-path caller that drops it (L-b).
  `_program_start` reads docker's own option grammar for run, create and
  exec; an attached short value is one word, and an option the grammar does
  not name translates the whole call as before (L-c). The launcher gate
  also finds `Start-Process [-FilePath]`, `.cmd` `@` and `if exist X`,
  `["kazma"] + args` and `cmd /c start "" kazma` (L-d; it reads no `.sh`:
  no launcher is unsigned on Linux). The page says an unsigned installer
  "may not run" with Smart App Control on, until VM test 1 measures it (L-e).
- **The third delta review (2026-11-27)**: a short word is read as docker's
  flag parser reads it, one character at a time (`-itp 80:80` is `-i -t -p
  80:80`; `-eX=1` carries its value; L-c2). `Dependencies.apply_engine` asks
  `broker.engine_changed()` on every apply after the first, and the broker
  restarts only when its own pair differs: a change in the export alone
  (Kazma's distro registered while the guard runs) reaches it (L-f; 50
  applies of the same record restart nothing). An engine change waits for
  the broker's running jobs (its `status` `active`), asked again at each
  apply, at most `BROKER_ENGINE_CHANGE_WAIT_S` (660 s: one job's longest run
  and a minute), said once (`broker.engine_change_waits`); past it the
  broker restarts and `broker.engine_changed` says how many jobs it stopped.
  The route-entries gate reads each function of a by-path caller, not the
  file, with the functions that only decide declared (`_DECIDE_ONLY`).
  **The launcher gate is a heuristic**: it reads strings, argv lists and
  script lines for the shapes it knows (each planted in its controls), and
  a start written another way (a `Start-Process` with other switches first,
  `if errorlevel`, a launcher named inside a `powershell -Command` string)
  is not found; review new ways to start a program against it.
- **User page**: `docs/docs/guide/windows-smart-app-control.md` and its
  Arabic text `windows-smart-app-control-ar.md` (the site's
  `ar/windows-smart-app-control`). The plan it states: signed releases (a
  code-signing certificate), then a release bundle where every file Kazma
  ships is signed, and fewer native files in the default install.
