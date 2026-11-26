<!-- AGENTS.md, piece 21c-install-parts. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 21c. Express, Full or Custom: one table of parts (`kazma_core/install_components.py`)

The owner's three installs (2026-11-07, after the drill VM needed a dozen
manual steps and met about ten fresh-install bugs one at a time). `COMPONENTS`
is the one table: each part's extras, system dependencies (21b), the parts it
`needs`, what it `gives` and what Kazma `lacks` without it, whether Express
holds it. **Express** (the default) is the parts marked `express`, on the
systems a part's `express_on` names (`express_ids(os_name)`,
`components_for(..., os_name=)`; with no system named, only
the parts in Express everywhere). **Full** is the whole table (`full_ids`), never a list
kept by hand, so a part added joins Full the day it is added. **Custom** is a
checklist from Express (`toggle`: on brings what a part needs, off takes what
needs it). Every extra in pyproject.toml belongs to a part or is declared in
`EXTRA_ALIASES` with why. **A part installs what it promises, nothing for
working on Kazma** (2026-11-26, §71): the tests part takes the `test` extra
(pytest and its plugins), not `dev`, whose mypy, locust and pre-commit no
test and no running Kazma imports -- measured, 340 of the 699 unsigned native
files an Express install on Windows carried. `dev` is a checkout's (setup.ps1,
setup.sh); `kazma update` keys it on `mypy` only (`_EXTRA_MARKERS`), so an
install with pytest alone does not gain it.

`kazma setup install` (`kazma_cli/install.py`) checks every part -- the ones
not chosen too (`_check_every_part`: one already here is "Here, not chosen",
never "Left out" with what Kazma lacks; only the chosen are recorded) -- then
lists every problem it can see before anything is done (`_preflight`: disk space,
the network, an administrator terminal, and `system_deps.docker_readiness`:
virtualization off in the firmware refuses Docker and what needs it; a
Windows virtual machine is told what its host must pass through; WSL 2
missing is installed first and a restart asked for), shows one plan, asks
once (`-y` asks nothing, the mode included), and runs it: a step is tried
again, a part that still fails stops only what needs it, and the end says
what Kazma lacks for each part not installed. The extras step is `uv sync
--inexact --project <checkout>` in a checkout (the lock's versions) and each
extra's own requirements with `uv pip` elsewhere -- never `kazma[...]` by
name (AUD-029). The choice and each part's outcome are recorded
(`setup_record.json` "install"): a second run takes the recorded mode, and
a recorded Custom choice, and continues where the first stopped. Every line
said and every program's output go to `<install>/.kazma/install.log`. With
the service part installed it waits for Kazma and opens the browser (the
chat page asks for a provider key). `setup.ps1` opens its own administrator
window in a terminal (`-NoElevate`), lets uv download a Python when the
machine has none, syncs the base and hands over (`-Mode`, `-Yes`;
`setup.sh`: `--mode`, `-y`); without a terminal it passes `--packages-only`.
Gates: `tests/test_install_modes.py` (a fake Windows machine: Express end to
end and a second run that does nothing, problems said first, Full where
Docker cannot run, WSL before Docker, a failing part and what needs it, an
administrator terminal, Custom kept on a re-run; every extra covered with a
planted one as the control; Express never fails for want of Docker; the
quickstart's table equal to `COMPONENTS`).

**A problem report is one report, seen before it is sent** (2026-11-14,
REMAINING 3.1; `kazma_core/support/problem_report.py`). **Send sends the
report shown** (2026-11-15): the web holds the previewed report by id for its
asker (`problem_report.hold`/`take`, ten minutes, 409 `report_expired`), and a
chat shows every part of it before `/report send`; both collected again on
Send before. `collect` gathers the
note, where it was asked from, version/commit, system, install parts,
readiness check results, open problems' titles and the last ERROR lines of
the log; everything passes `redacted` (credentials, the home folder, emails,
IPs); no memory, document, setting or key is read, and a chat only when the
person ticks "include this chat" (`chat_session`: its last `MAX_CHAT_MESSAGES`
user and assistant messages, each cut at `MAX_CHAT_CHARS`, `_chat_excerpt`).
Every surface shows
`preview` first: the `?` beside the bell and "Report this" (`Alpine.store
('report')`, `components/report_dialog.html`, `/api/support/*`, admin),
`kazma report`, `/report` then `/report send` in a chat app (an admin's:
`changes_global_config`). `send` posts to `support.report_url` (default
`https://status.kazma.ai/v1/report`, the Kazma Watch Worker's
`src/reports.js`: size-capped, rate-limited, D1, the owner told, listed behind
Access at `/reports`), at most `MAX_PER_HOUR` an hour; anything it cannot send
is saved in `<data dir>/problem_reports/` and the person told where. It is
offered where something went wrong: a turn that failed or ended without an
answer (`turn-header-report`), stuck or failed work (the Running work card),
an error toast (its action), `/report` in the web chat (opens the dialog), and
`kazma doctor`, an unfinished setup and the guard's `--status` (when Kazma is
not healthy, `REPORT_HINT`) name `kazma report`; the dialog's Save
to file keeps the report shown. Gates:
`tests/test_problem_report.py`, `tests/e2e/test_problem_report_browser.py`,
`deploy/observer/test/reports.test.mjs`.

**`kazma uninstall` takes off what setup put on, and keeps data unless
asked** (2026-11-14, REMAINING 2.5; `kazma_cli/uninstall.py`). One plan
(`build_plan`), shown and accepted before anything runs: Kazma stopped through
its guard; the service (`install_service.py --uninstall`, the `KazmaAgent`
task ended first); `KazmaFreshclam` / the macOS signature job; the containers
setup made (`container_services._image_state` says which: never another's,
volumes only with `--remove-data`); Kazma's engine
(`container_engine_setup.remove`, with its own refusal); `.venv` (Windows:
deleted by a detached `cmd` once Python has exited, started through
`Machine.spawn` so a test starts nothing). Data and Kazma's home are
kept; `--remove-data` MOVES them to `<home>/Kazma-uninstalled-<date>` -- first
backing up Kazma's database on the PostgreSQL setup installed (`pg_dump -Fc`
into the data folder) and then dropping it and its role (`_database`; a backup
that fails drops nothing) -- and only
`--purge` deletes (typed `delete`). PostgreSQL, system programs, fonts, uv and
the source folder are kept and named with their removal commands. **A service
that starts another folder is never taken** (`_service_folder`: the task XML's
`WorkingDirectory`, the systemd unit's or plist's guard path), nor the pieces
the computer shares while it exists: live 2026-11-14 a dry run from the dev
checkout planned to end and delete the live install's `KazmaAgent`. Gate:
`tests/test_uninstall.py` (that witness, admin refusals, nothing before the
plan is accepted, data moved not deleted).

**The setup scripts pass install options through, and CI installs from
scratch** (2026-11-14, REMAINING 2.2): `./setup.sh ... -- <options>` and
`setup.cmd ... -InstallOptions "<options>"` (split at spaces: `-File` hands
every argument over as one string; not `-InstallArgs`, which PowerShell would
read as the script's own `$installArgs`) reach `kazma setup install`. The CI
job `fresh-install` runs them on clean Ubuntu and Windows runners (Express
without `meaning_search` and `neural_ocr`; Windows also without the container
parts, no virtualization there), then `setup install --check` and Kazma's
`/health/live` and `/health/ready` on its port. **setup.ps1's administrator
window gets every parameter** (2026-11-19: it reopened itself with `-Mode`,
`-Yes`, `-Port` and `-Debug` and dropped `-InstallOptions`, so the owner's
Full install with three `--without` installed all three): the options go
single-quoted inside the window's one double-quoted `-Command`. Gate:
`tests/test_setup_passes_install_options.py` (each script's own parsing run;
every parameter in the elevation block, the old block as the control; the
built command run as Start-Process runs it).
**A clean Linux machine is drilled end to end** (REMAINING 2.3,
`scripts/linux_drill.py`, run from Windows): a fresh Ubuntu 24.04 WSL distro
of its own (`kazma-linux-drill`, Ubuntu's image checked against its
`SHA256SUMS`, systemd and lingering on), `./setup.sh --mode express -y` at a
given commit, a reboot (Kazma back by itself), `kazma update` to `main`,
`kazma uninstall` (service and `.venv` gone, port closed, data kept). It
leaves out the model downloads and the container parts: every WSL distro
shares one network with Kazma's engine. Passed 2026-11-14 (fc01c83a7 to
de5d671ce). Gate: `tests/test_linux_drill.py` (its parts and options exist).

**Private search and the memory graph install by default** (the owner,
2026-11-13, reversing "Express stays container-free" of 2026-11-12, decided
with Fable's review). `docker`, `private_search` and `graph_neo4j` are in
Express on Windows and Linux (`express_on`); macOS Express stays
container-free (Docker Desktop starts only at a sign-in there); the locked
document worker stays in Full. They are `best_effort`: a part the computer
cannot have (virtualization off, Docker already in another WSL distro, no
compose definition on a wheel install) is said with what Kazma lacks
(`_say_unavailable`) and the install still ends `EXIT_DONE`; a step that fails
is still `EXIT_NOT_DONE`. Before, any blocked part ended every run "stopped
short". Windows waiting for its WSL restart ends `EXIT_RESTART` (3), which
setup.ps1/setup.sh say as "restart, then run setup again". A part left out by
name is recorded (the install record's `without`) and a plain re-run keeps it out, so
Express gaining a part never installs one the owner declined.
- **Container services are one table** (`kazma_core/container_services.py`):
  each row names the part, its compose file and service, the container, the
  secrets setup generates into the install's `.env` before it starts (kept
  when set; compose reads the same file with `--env-file`), what Kazma is told
  to reach it (`kazma_env`, written into `.env`) and the address that answers
  once it is ready. `build_plan` makes the same four steps for every row
  (secret, `compose up`, point Kazma at it, wait for it) and `_check_parts`
  judges every row the same way; an install whose Kazma already reaches the
  service through its own settings (the SearXNG address setting, the memory
  graph's Neo4j provider) is judged by them, so setup never starts a second copy.
  With Kazma already running, the plan ends by asking the guard to reload it
  when idle (`.env` is read at start). A new service is a row and a compose
  definition. Neo4j runs from `deploy/docker-compose.neo4j.yml` (project
  `kazma-graph`, `KAZMA_NEO4J_PASSWORD` required, heap and page cache bounded,
  `KAZMA_NEO4J_HEAP` / `KAZMA_NEO4J_PAGECACHE`); Kazma uses it through
  `KAZMA_NEO4J_URL` and `KAZMA_NEO4J_DEFAULT=1` (`memory.backends`, SQLite
  stays the source of truth, Neo4j the dual-written copy).
- **The Neo4j setup made answers to setup's password**
  (`memory.backends._apply_neo4j_env_defaults`, 2026-11-20). For the
  container setup made -- `KAZMA_NEO4J_DEFAULT` on and the stored graph
  address the same Neo4j on this machine as `KAZMA_NEO4J_URL`
  (`_same_local_server`: loopback names, the same port) -- the password
  (and user) in `.env` win over the settings. A restore carried the old
  machine's password into the settings, the new container refused it, and
  every graph sync failed "the sign-in was refused". A Neo4j the owner
  chose elsewhere keeps the password stored for it. Gate:
  `tests/test_neo4j_setup_password_wins.py` (the old fill-empty-only rule as
  the control).
- **No service sends usage data home**: the Neo4j compose sets
  `NEO4J_dbms_usage__report_enabled=false` (Neo4j's default reports;
  `tests/test_container_services.py` holds every row to it, a Neo4j without
  the setting as the control).
- **Every image a shipped compose file runs is pinned by version and digest**
  (SearXNG, Neo4j, Postgres, nginx; a locally built image is the exception),
  and Dependabot's `docker-compose` ecosystem proposes updates.
- **Settings -> Packages names the parts Express gained** after an install
  ran (`install_overview(..., os_name)` `new_in_express`, without the parts
  left out by name) with `kazma setup install`; `kazma update` never installs
  them (administrator rights, about 1 GB, a possible Windows restart).
Gates: `tests/test_install_modes.py` (Express on Windows with the engine,
SearXNG and Neo4j; virtualization off ends done, the same table without
`best_effort` as the control; the WSL restart then a finishing run; `--without`
kept; an older Express record gaining the parts; a running Kazma reloaded),
`tests/test_container_services.py` (every row against its compose file, a
drifted row and an unpinned image as controls).

**Kazma's port is chosen at install and asked for in one place** (the owner,
2026-11-13; `kazma_core/server_port.py`, `kazma_cli/port_setup.py`). Before,
9090 was written out in some twenty places, most reading the shell's
`KAZMA_PORT` alone: a port kept in `.env` reached the server and not a
`kazma` command from another shell, and the guard probed its first port until
it was restarted.
- **One answer**: `server_port.server_port` -- the shell's `KAZMA_PORT`, else
  the install's `.env` files (`env_file_ladder(include_cwd=False)`, the most
  specific last; one key read), else `DEFAULT_PORT` (9090). The CLI's clients,
  `kazma serve`, `kazma-web`, the OAuth redirect addresses, the IDE note, the
  importer and the local API ask it; the guard keeps a standard-library copy
  (`install_port`, which now reads Kazma's home `.env` too), held equal on every
  layout.
- **`port_problem`** says why a port cannot be Kazma's: below 1024, one of
  Kazma's own services (`KAZMA_SERVICE_PORTS`), the guard's broker range
  (`BROKER_RANGE`; the broker also never takes Kazma's port), a range Windows
  keeps (`netsh ... show excludedportrange`, read only), or another listener
  (a bind and a connect). The port this install's Kazma holds is its own.
- **The install** (`--port N`; `setup.ps1 -Port`, `setup.sh --port`): a first
  install asks once after the mode (Enter keeps 9090), `-y` takes 9090 or the
  first usable port after it (said), a port already set -- or an install from
  before the record that answers on 9090 -- is kept and never asked; the choice
  is written as `KAZMA_PORT` after the go-ahead. `--port` on an installed Kazma
  moves it and reloads it through the guard when idle.
- **`kazma setup port [N] [--check]`** shows it, checks a port, or keeps a new
  one, reloads when idle, and names what still points at the old port
  (`KAZMA_PUBLIC_URL`, a cloudflared configuration, sign-in redirect addresses,
  bookmarks).
- **The guard reads the port again before every start** (`Guard._follow_port`,
  `guard.port_changed`), and a reload waits on the address the guard probes now
  (`_recorded_health`).
- `ui.port` and `ui.host` in kazma.yaml were read by nothing and are retired.
Gates: `tests/test_server_port.py` (the two copies on every layout, each refusal,
the install and the command, the guard following the port; no product code
writing 9090 outside the two definitions, a planted one as the control; the
install without its port step as the control).

**Windows setup starts from `setup.cmd`** (the clean drill VM, 2026-11-07):
a Windows client's PowerShell execution policy is Restricted until someone
changes it, so the README's `.\setup.ps1` was refused before its first line
("running scripts is disabled on this system"). `setup.cmd` is a batch file,
which the policy does not cover: it changes to its own folder and starts
Windows PowerShell with `-ExecutionPolicy Bypass` for that one process,
passing arguments and the exit code through; no setting is changed, and a
Group Policy still wins. Every published instruction (README in both
languages, the docs, setup.ps1's own messages) names `setup.cmd`, and
`*.cmd` is checked out CRLF whatever the clone's `core.autocrlf` says. Gate:
`tests/test_windows_setup_command.py` (run under a Restricted process policy;
`powershell -File setup.ps1` refused there, and a setup.cmd that does not
change folder, as the negative controls).

**Setup runs nothing it has not checked** (2026-11, supply chain). setup.ps1
piped `https://astral.sh/uv/install.ps1` into `Invoke-Expression` (in the
administrator window it opens) and setup.sh piped `install.sh` into `sh`:
each ran whatever that address served. Both install ONE uv release
(`$UvVersion` / `UV_RELEASE`, equal) from uv's GitHub release, with the
SHA-256 of each platform's archive written in the script (uv's own `.sha256`
files, which GitHub's asset digests matched), checked before anything is
unpacked; a mismatch stops setup with both hashes. setup.ps1's download,
hash and unzip are .NET calls (`Save-UvArchive`, `Get-Sha256Hex`,
`Expand-UvArchive`): 5.1 started from a PowerShell 7 terminal cannot load
`Get-FileHash`. uv goes to `~/.local/bin` (Windows: also the user PATH, as
uv's installer did). The pip and snap fallbacks are gone. A new uv release
means new hashes in both scripts. Every Dockerfile starts `FROM` a digest
(`@sha256:`), and Dependabot's `docker` ecosystem watches each folder with
one. Gate: `tests/test_setup_downloads_verified.py` (no shipped script pipes
a download into a shell, every `FROM` pinned, one uv release with a hash per
platform; the real install functions cut out of each script and run on a
local archive: a match installed, a mismatch refused before unpacking; the
old installer lines and a bare tag as negative controls).

**What the install shows** (the owner, after the clean VM's first run,
2026-11-07: some 60 lines before one question, no version, and the mode
question answered by an Enter left in the console). The setup scripts open
with the version and commit and the first sentence of
`docs/docs/guide/what-is-kazma.md`; `kazma setup install` starts with
`_header` (`get_version()`) and, when installing, says only a summary by
part name (`_say_summary`): every part's state, what each part left out
lacks and every step's command go to the install log (`--dry-run` and
`--check` print them). `setup._ask` drops what was typed ahead before it
asks a person (`_drop_typeahead`). Colours are Kazma's royal blue and darker
green, amber and red (`_TONES`, setup.sh's 256-colour codes, setup.ps1's
24-bit originals), never a terminal's bright yellow, cyan or white, and none
off a terminal or with `NO_COLOR`; the log is plain. Gates in
`tests/test_install_modes.py` (the summary, the typed-ahead keys with a
question answered by code as the control, the colours, the scripts' fading
colours with the old ones as the control, the intro read from the page).

**The install downloads the meaning-search model** (the clean VM's first
start downloaded 4.3 GB before Kazma answered at all: 5.5 minutes, nothing
on screen). The `meaning_search` part is in place only with its model on
disk (`embedder.local_model_downloaded`, local cache only), and the plan
downloads it (`embedder.download_local_model`, the server's own loader).
The end's wait for Kazma counts the time each try takes (`_finish`: a
refused connection takes about 2 s on Windows, and 240 s became 325 s).
Gates: `tests/test_install_modes.py` (the model there as the control; the
old count of 48 tries).

**The shipped embedding model is pinned to one commit** (2026-11, supply
chain): `embedder.DEFAULT_MODEL_REVISION` (`refs/main` of every install's
cache, so nothing downloads again), passed by every load, download and cache
check through `embedder._model_revision`; without it the Hugging Face libraries
follow the repository's `main`. A model the owner configured that Kazma does
not ship loads unpinned unless they give one (`KAZMA_EMBED_REVISION`,
`memory.embedding.revision`); a remote config that falls back to the local
model takes the pin, never the remote model's revision. Never change the
pinned commit without re-embedding (the vectors are that commit's). Gate:
`tests/test_embedding_model_pinned.py` (every `SentenceTransformer(` /
`snapshot_download(` call in product code and scripts names `revision=`; the
old unpinned lines as the negative control).

