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
- **User page**: `docs/docs/guide/windows-smart-app-control.md` and its
  Arabic text `windows-smart-app-control-ar.md` (the site's
  `ar/windows-smart-app-control`). The plan it states: signed releases (a
  code-signing certificate), then a release bundle where every file Kazma
  ships is signed, and fewer native files in the default install.
