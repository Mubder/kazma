<!-- AGENTS.md, piece 63-container-engine. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 63. Kazma's own container engine (`kazma_core/container_engine.py`, `docker_cli.py`)

2026-11-12: a Windows update restarted the PC and nobody signed in; Docker
Desktop starts only at a sign-in, so every container stayed down for six
hours. Plan, measurements and an independent review:
`docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`. On Windows the only
"Docker as a service" for Linux containers is Docker Engine in a WSL distro
started by a boot task: `kazma-engine`, kept up by the `KazmaEngine` task
(S4U, normal rights; its `keep-engine.ps1` holds a `wsl.exe` attached to the
distro, since WSL stops a distro about 15 s after the last `wsl.exe` leaves;
the holder is the channel of its clean stop at a shutdown, below), reached
as the Docker context `kazma-engine` (mutual TLS on 127.0.0.1:2376). Drilled
on a clean VM: it starts with nobody signed in, services as SYSTEM and LOCAL
SERVICE (a tunnel) reach its published ports, and it is back 19 s after a
`wsl --shutdown`.

- **The choice is explicit.** `containers.engine` is `kazma` (Kazma's
  engine), `desktop` (Docker Desktop, the owner's choice), or unset: then the
  engine setup recorded it installed (`setup_record.json`
  `container_engine`), else nothing is pinned and the docker CLI's current
  context serves, as before (`container_engine.engine_setting`). Never "the
  engine exists, so use it": on the owner's PC, engine made and nothing moved,
  that would have sent Kazma to an empty engine at the next reload.
- **Pinned in `DOCKER_CONTEXT`** for the process and every docker call it
  starts (`docker_cli.apply_engine_choice`); the operator's own
  `DOCKER_CONTEXT` or `DOCKER_HOST` wins. The server applies it at boot
  before the background loops and follows the setting
  (`watch_engine_setting`); any other Kazma process applies it at its first
  `find_docker_cli` (the CLI, setup, scripts). The guard's copy of
  `docker_cli.py` (loaded by path) never reads settings: it takes the choice
  from the server's record (`guard_dependencies`, field `engine`) and hands
  the context to the document broker (`_BROKER_ENV_KEEP`), which restarts
  when the choice changes.
- **Mount paths follow the context** (`docker_cli.engine_host_path`): Kazma's
  engine mounts fixed drive `G:` at `/g` (its distro's `[automount] root=/`);
  a share, mapped, `subst` or removable drive is refused with the reason;
  Docker Desktop takes the Windows path. Never decided from a cached
  `docker info`.
- **The guard brings Kazma's engine back, never Docker Desktop**
  (`Dependencies._start_kazma_engine`): it runs the `KazmaEngine` task,
  restarts Docker inside the distro (a running keep-alive does not mean a
  running dockerd), and holds a keep-alive of its own when the task is
  missing or refused (`engine_task_missing`, `engine_task_denied`).
- **Express holds the engine on Windows and Linux since 2026-11-13** (the
  owner's request: private search and the Neo4j memory graph install by
  default, §21c); it was container-free from 2026-11-12. Setup installs
  Kazma's engine when a container part is chosen; Docker Desktop only when
  the user picks it (`kazma setup install --docker desktop`).
- **Setup installs it** (`kazma_core/container_engine_setup.py`, assets in
  `container_engine_assets/`): `check` names what is missing (wsl, distro,
  docker, cli, context, task, shutdown), `install_steps` puts in only that, in order,
  the record last (it waits for `docker version` and writes
  `setup_record.json` `container_engine`, the setting's fallback). WSL first
  (pinned MSI, the two features); a step that needs Windows restarted
  answers `RESTART` and the container steps after it wait (Docker
  Desktop's own path keeps its order). The distro is the dated Ubuntu 24.04
  image, Docker Engine set up by `setup-engine.sh` piped over stdin (LF,
  idempotent, docker packages held, Docker's apt key kept only when its
  fingerprint is the published one, `kazma_trust_docker_key`,
  `tests/test_engine_docker_key.py`, Ubuntu's archive read over HTTPS
  before the first `apt-get update`, `kazma_https_sources`,
  `tests/test_engine_apt_over_https.py` -- over plain HTTP the network on the
  way handed back a corrupted package list on every try and the first fresh
  install after the 2026-11-19 reset could not set the engine up -- TLS keys
  made once, listening on 127.0.0.1 only with client certificates); Docker's
  Windows CLI and the
  compose plugin from Docker's own downloads. **Kazma's own CLI, not any
  CLI** (2026-11-14): the check's "cli" is
  `container_engine_setup._own_cli_installed_check` (`docker.exe` in
  `%LOCALAPPDATA%\Kazma\engine\bin` and the compose plugin), and
  `find_docker_cli` takes that copy first on Windows, **once it starts**
  (2026-11-26: Windows began refusing it, "An Application Control policy has
  blocked this file", WinError 4551; it was chosen because the file was
  there, every docker call failed, the guard restarted an engine that
  answered and code execution waited). A CLI Windows refuses by policy
  (`docker_cli._REFUSALS`) is passed over for the next, said once at
  WARNING and tried again every 5 minutes; a file that is no program is
  not a refusal (gate: `tests/test_docker_cli_discovery.py`, the old choice
  as the control). The guard keeps its copy of the module until the
  `KazmaAgent` task restarts. The check took Docker
  Desktop's CLI for Kazma's, so the owner's PC never got its own, and
  uninstalling Docker Desktop would have cut Kazma off from its engine; the
  engine is still asked through whatever CLI there is, so a Docker that
  answers is never set up again (gate: `tests/test_container_engine_setup.py`,
  the old rule as the control). Every download is pinned by
  URL, size and SHA-256 and kept only when it matches (`_fetch`). What would
  stop it is said before anything is installed (`preflight`: WSL's
  localhost forwarding off, Docker already in another distro), and stops
  only the container parts. `kazma setup engine` installs, repairs, checks
  or removes it; removing is refused while another project's containers
  live in it or it cannot be asked (`--force`). On Linux Kazma's engine is
  the system's Docker service: setup starts it at boot and adds the account
  to the `docker` group (`system_deps._docker_access_steps`; a socket this
  account may not use is `DOCKER_GROUP`, fixed with the group alone).
- **Drilled on a clean Windows VM** (2026-11-12): WSL, a restart, then the
  rest in 321 s; after a reboot with nobody signed in Docker answered 29 s
  after boot, and a container read a mounted `C:` folder. The drill found
  three bugs, each now held by a test: WSL is told by `wsl --version`
  (`_wsl_installed`; Windows' stub fails it, and a WSL with no distro fails
  `wsl -l` too, which read as "no WSL" and asked for a restart on every
  run); a download setup makes opens with `http_tls.open_download` (certifi's
  roots: a fresh Windows' store lacked GitHub's, and Python reads the store
  alone; the Tesseract models too); and a program's stdin is bytes (`_out`:
  text mode on Windows wrote `\r\n`, and bash refused the script's first
  line). Docker's CLI folder joins the user's PATH (`_on_user_path`, never
  `setx`), and Kazma's own CLI off PATH is no warning.
- **Readiness and the disk watch name the engine** (2026-11-12). Kazma's
  engine comes back by itself and Docker Desktop waits for a sign-in, so
  "Docker does not answer" asked for two different steps.
  `docker_cli.engine_in_force` is the one answer (`kazma`, `desktop`,
  `system`, `other`), read from the context in force with no process
  started, so a down engine is named too; `docker_cli.engine_label` names it.
  Readiness's code sandbox check carries `engine` and says it in its error
  ("Kazma's container engine is not running"), which the guard quotes in its
  page and `--status`. The document isolation report carries `engine`, and
  its step is `start_kazma_engine` (with `kazma setup engine` to repair it)
  or `start_docker_desktop` (sign in), in both languages. The guard's
  `DiskWatch` watches the disk of the engine in force: the `kazma-engine`
  distro's folder (`docker_cli.kazma_engine_disk_dir`, where setup imports
  it) when Kazma's engine is in use, else Docker Desktop's disk image; never
  a disk no Kazma container lives on. Gate:
  `tests/test_engine_named_where_kazma_waits.py` (22 of its 24 fail on the
  old code; the general step and the catalog are the controls).
- **Its disk can live in the data folder** (2026-11-21, section 21 G3): with
  a data folder chosen, setup imports the distro into `<folder>\engine`, a
  disk kept there from before a reset is registered in place (`wsl
  --import-in-place`), and `kazma setup data-folder` moves an installed one
  (`wsl --manage kazma-engine --move`, hashed before and after, its task
  disabled meanwhile). Where the disk is, is WSL's own registration
  (`docker_cli.wsl_distro_location`), which `kazma_engine_disk_dir` and the
  guard's disk watch read.
- **The boot drill is a script** (`scripts/engine_boot_drill.py`, Stage 2's
  gate, run from the host). It reverts the drill VM to its clean checkpoint,
  copies one commit (`git archive`) and `uv`, runs `kazma setup install
  --mode full -y` detached (an SSH drop must not end it), restarts Windows
  when setup asks (WSL) and runs it again, sets the strict code profile from
  the install folder (it refuses unless the install's `.env` is the one
  loaded: from the home folder the first drill wrote it to a store of its
  own), restarts Windows with nobody signed in, and reads Kazma's readiness,
  `kazma_guard.py --status` and SearXNG every 15 s. Every container part must
  be ready within 5 minutes of boot; a reading with a session in `query
  user` makes the verdict `invalid`. Passed on 2026-11-13: every part ready
  within 90 s (`docs/audits/ENGINE_BOOT_DRILL_2026-11-12.md`). `--docker
  desktop` is the negative control (verdict `not_up`). Changing the
  `KazmaEngine` task's trigger is no control: the guard runs that task on
  demand and holds its own keep-alive. Its reading of `--status` is held to
  the guard's own line builders: `tests/test_engine_boot_drill.py`.
- **Setup's programs get no input, and it waits for what it started** (the
  drill, 2026-11-12). `container_engine_setup._out` passed `input=None`:
  `wsl.exe` inherited setup's console, and Windows' WSL stub waited on "press
  any key to install" until the 60 s timeout at every WSL call (a key
  pressed then would have installed WSL from the Store); it passes
  `stdin=DEVNULL`, and the terminal gate (`tests/test_setup_system.py`)
  covers this module and `install.py` and counts an `input` that can be None
  as the terminal. A Full install asked SearXNG 2 s after `docker compose
  up` and ended "not installed", exit 1; the step "wait for SearXNG to
  answer" (`SEARXNG_READY_WAIT_S`, 120 s) waits for it
  (`tests/test_install_modes.py`, a SearXNG that never answers as the
  control). `--docker desktop` installed WSL with `wsl --install`, which
  Windows refuses outside a signed-in console, so an unattended Desktop
  install never got Docker Desktop; both paths get WSL from one step,
  `engine:wsl` (the pinned MSI), and the container steps wait for the
  restart on either path.
- Gates: `tests/test_container_engine_choice.py` (the first rule, "the
  context exists", as the negative control), the engine cases in
  `tests/test_guard_dependencies.py` (the same outage on Docker Desktop as
  the control), `tests/test_container_engine_setup.py` (a fake machine and
  download: a mismatched download keeps nothing, a script without its ready
  line fails its step), `tests/test_install_modes.py` (Full installs the
  engine and never Docker Desktop; `--docker desktop` as the control),
  `tests/test_setup_system.py` (the docker group; Windows as the control).
- **An install on Docker Desktop moves with one command** (Stage 3,
  `kazma setup engine --move-from-desktop`, `kazma_core/engine_move.py`, on
  the pattern of the database's move, section 21 G). It sets Kazma's engine
  up if it is not, then moves only the containers Kazma's record names: the
  rule the server tells the guard with
  (`guard_dependencies._match_containers`), read against Docker Desktop's
  containers; another project's container is never named, and a volume one
  shares refuses the move. Kazma's database in a container refuses it too,
  naming `kazma setup postgres --move-from-container`. Every refusal
  (Docker Desktop down, a name or a port already on the engine, a mount the
  engine cannot take, `containers.engine` already `kazma`) comes before
  anything changes. Images (`docker save` into `docker load`, else pulled by
  digest, held to the same layers), the sandbox image and the document
  worker image (copied when Docker Desktop holds this code's build, else
  built on the engine, only in the container profile) are put on the engine
  while Kazma runs; then Kazma is held through its guard (`_MoveHold`, its
  own pause reason), and each container is stopped on Docker Desktop with
  restart `no` and kept, seen stopped (`_seen_stopped`: never a volume read
  while its container runs), its volumes copied by tar between the engines
  and compared entry by entry (path, kind, owner, mode, size, SHA-256; a
  hard link counts as the file it names: `_manifest_of`), made again from
  its own settings (`_create_args`; Docker Desktop's Windows or
  `/run/desktop/mnt/host` bind sources as the engine mounts them,
  `_engine_bind_source`), files added or changed inside it carried, started,
  and seen healthy and answering on its ports from Windows. Then
  `containers.engine` = `kazma`, the guard's record told
  (`guard_dependencies.set_record_engine`), Kazma started and seen ready.
  A failure puts everything back: before the switch this run's objects are
  removed from the engine (they hold only the copy); after it they are
  stopped and kept (Kazma may have written to them); Docker Desktop's
  originals get their restart policy back and run; `containers.engine`
  says `desktop`. Every change is journaled before it is made
  (`setup_record.json`, `engine_move`): a run that died half way is rolled
  back by the next one, which takes the dead run's pause over
  (`_MoveHold.adopt_leftover`); Ctrl+C rolls back before it stops.
  `kazma setup engine` alone on a Docker Desktop install writes
  `containers.engine` = `desktop` before it records the engine
  (`engine_move.keep_desktop`): recorded, the engine would be Kazma's choice
  at its next start, with none of its containers. Gate:
  `tests/test_engine_move.py`, against `tests/_fake_docker_engines.py` (a
  fake docker CLI with two engines run as a real program: real argv, pipes
  and tar streams), each rule with its negative control (a mover that trusts
  the stop, a copy without the check, no journal, no Docker Desktop choice
  written). **Drilled on a clean VM** (2026-11-13, Docker Desktop 4.99,
  SearXNG and Neo4j from the shipped compose files, another project's
  container beside them). The move ran in the signed-in session in 766 s,
  the engine's install included; Neo4j's 528 MB came across entry for entry,
  and the other project's container was untouched. After a restart with
  nobody signed in, both containers were healthy 39 s after boot. The drill
  found three things:
  - The hold found its guard only through `.git`, so on an install
    unpacked from a source zip it said "no guard supervises it" and refused.
    `_MoveHold` now finds the guard from the install's own folder
    (`setup._checkout_root`).
  - `_MoveHold.ready` now counts a Kazma waiting for its provider key as
    up, as the guard's resume does.
  - The carried files are said in one line per container (Neo4j's start
    changes the owner of its whole folder: 248 lines).

  At a Windows restart its containers got only seconds (Neo4j:
  "Stopping..." and never "Stopped."), and a line Neo4j's start script had
  just appended came back as NUL bytes: the next bullet.
- **A Windows shutdown stops the engine cleanly** (2026-11-13,
  `KazmaEngineShutdown`, `container_engine_assets/engine-shutdown.cs`,
  `engine-stop.sh`, `keep-engine.ps1`). Measured on the drill VM, nobody
  signed in: WSL's VM was ended about 2 s into a restart with no signal to
  anything in it (WslService and vmcompute are `IGNORES_SHUTDOWN`; WSL shuts
  a distro down gracefully only at a sign-out of the session that started
  it, and the boot task's is session 0), and a writer container lost 760
  bytes to NULs. Task Scheduler ends every running task and what it started
  60 ms into a shutdown (event 111), before any service hears of it, and
  starts none (`0x8007045B`, ERROR_SHUTDOWN_IN_PROGRESS); WSL refuses
  LocalSystem. So the channel is opened before:
  - the `KazmaEngineShutdown` service (LocalSystem, built at setup from
    `engine-shutdown.cs` by the .NET Framework's own `csc.exe` into
    `Program Files\Kazma\engine-shutdown`, never a folder the account can
    write) takes the preshutdown notice, sets its own preshutdown timeout
    (`SHUTDOWN_BUDGET_S`, 120, plus 30) and serves two named pipes
    (`KazmaEngineHold-in`/`-out`; network denied);
  - `keep-engine.ps1` starts the distro's holder through WMI
    (`Win32_Process.Create`: outside Task Scheduler's job) as `cmd /c wsl.exe
    ... kazma-engine-hold piped <in-pipe >out-pipe`, holds the distro itself
    as before when no service listens, and starts nothing once
    `SM_SHUTTINGDOWN` is set;
  - at preshutdown the service writes `stop`: `kazma-engine-stop` stops
    Docker, then containerd (a daemon shutdown: every container gets its
    stop signal and its own stop timeout, and its restart policy brings it
    back at the next boot; a `docker stop` would keep an `unless-stopped`
    one down), then masks them at runtime (masking first reloaded them as
    empty units with no order: containerd went at once under a Docker still
    stopping, "Force shutdown daemon"), writes the distro's own disks
    (`sync -f` per ext4/xfs/btrfs mount, bounded; a plain `sync` also
    flushes the Windows drives at `/c` and never returned during a
    shutdown), answers `ENGINE_STOPPED`; then `poweroff`. Windows waits for
    it. Stop and poweroff are two lines so the answer crosses before the
    distro ends; the holder strips a CR (.NET's `WriteLine` sent `stop\r`).
  Control 128 (`sc.exe control KazmaEngineShutdown 128`) makes the same
  stop without a restart, and keep-engine starts the engine again. Setup's
  step is `engine:shutdown` (admin): check() counts the service not
  running, an older `keep-engine.ps1` or older helpers in the distro
  (`engine-stop.sha256`, read with no call into WSL) as missing, so
  `kazma setup engine` brings an existing engine (the owner's PC) up to it;
  the helpers also go in with Docker (`engine:docker`). Measured after: the
  service heard the restart 1.1 s in, the writer got TERM, finished its 8 s
  stop and wrote DONE, no NUL byte, and Windows went on at +10.2 s. The
  drill is `scripts/engine_shutdown_drill.py` (a fresh VM, the engine
  through setup, a writer, Windows restarted; `--control` on a commit
  before it must be `unclean`): on 2026-11-13 the fix was `clean` in both
  rounds (Windows took 9.4 s and 9.0 s) and the control `unclean` (the
  writer cut off mid-run, no TERM; 0.3 s), report
  `docs/audits/ENGINE_SHUTDOWN_DRILL_2026-11-13.md`. Not covered: a power cut or a crash (no
  notice at all), and Docker Desktop (it runs only in a signed-in session;
  not measured). Gates: `tests/test_engine_clean_shutdown.py` (the distro's
  scripts run under bash with stand-ins, their first versions as negative
  controls; the service built with `csc` on Windows),
  `tests/test_engine_shutdown_drill.py`.
- **A Windows shutdown stops Kazma first** (2026-11-20, `kazma_core/shutdown_notice.py`).
  The owner's restart and Windows Update's after it cut Kazma off: the start
  card said "the last run did not shut down cleanly". Task Scheduler ends the
  guard at the start of a shutdown; the server lived on, heard of the
  shutdown only at the very end (Windows' console shutdown event, 37 s in),
  after the database service had stopped, logged "Shutdown signal sent" and
  was ended in the same second, with no stop stamp written.
  - The `KazmaEngineShutdown` service serves a third pipe,
    `KazmaShutdownNotice` (16 instances: one per Kazma server on the
    machine). Each server connects from `serve.py`
    (`shutdown_notice.start`, a daemon thread; never another process: a
    test's app never connects to the machine's real service) and says
    nothing.
  - At the preshutdown notice the service writes `stop` to every connected
    server. A server stops the way the guard's CTRL_BREAK stops it
    (`signal.raise_signal(SIGBREAK)`: uvicorn's handler, then the app's own
    shutdown and `exit_after_shutdown`). It keeps its end of the pipe open
    until its process ends, and the service waits for every end within
    `SHUTDOWN_KAZMA_BUDGET_S` (75), before it stops the engine.
  - Windows stops the database service after every preshutdown service is
    done. The service's preshutdown timeout is Kazma's budget plus the
    engine's plus 30 s. Control 129 makes Kazma's stop alone (a drill; the
    guard then starts the server again). `--notice-drill N --notice-pipe
    NAME` runs the same stop with no service, for tests.
  - The guard starts no server while Windows shuts down
    (`kazma_guard.windows_shutting_down`, `SM_SHUTTINGDOWN`).
  - The service is installed with Kazma's own service, on any engine:
    `kazma setup install`'s service part counts as done only when it runs
    this build (`container_engine_setup.shutdown_service_current`: running,
    the source the build left beside the program equal to this build's, the
    registered command line equal). The part's step
    `engine:shutdown_service` (`shutdown_service_step`, administrators only)
    puts it in alone; the task is registered again only when missing. The
    engine's own shutdown step uses the same build, and either finds the
    other's work current. `kazma uninstall` takes the service off
    (`remove_shutdown_service`) unless the engine's removal does.
  - A server with no service listening says so once: a WARNING under the
    guard, naming `kazma setup install` in an administrator terminal. Lines
    said before the app sets up logging are held and said after
    (`shutdown_notice._log`).
  - Gates: `tests/test_windows_shutdown_notice.py` (the real service
    program built with `csc` and the real listener: two servers told and
    waited for, the real SIGBREAK ending a process; a server that never
    ends, and no server, as controls), the install, uninstall and engine
    tests (the task alone as the service part's control).
  - The real restart is `scripts/restart_drill.py` on the drill VM: a
    Custom install (PostgreSQL and the service only), then `shutdown /r /t 0
    /f`, judged from the stop stamp, the server's own stop line, the
    service's Application log and event 6006. Passed 2026-11-20
    (`docs/audits/evidence/restart_drill/`): the fix `clean` (the service
    told the server 1.7 s in, the stop recorded 1.9 s in, Windows stopped
    2.1 s in); the control at 9b96c2b8c `unclean` ("Shutdown signal sent"
    0.9 s in, as Windows stopped, no stamp: the live symptom). The VM has no
    TPM since the PC reset (its virtual TPM was locked to the old Windows'
    guardian certificates); its clean checkpoint is
    `engine_boot_drill.CHECKPOINT`.

