# Containers that come back with nobody signed in

Asked for by the owner on 2026-11-12, after a Windows update restarted the
PC at 06:29 and again at 06:31 and nobody signed in until 12:30. Docker
Desktop starts only in a signed-in session, so every container on the PC
stayed down for six hours: Kazma's (private search, the memory graph, the
document worker, the code sandbox) and the owner's other projects. The
owner's words: "we are not talking only about me on this matter, how to make
it smooth for every end user? maybe some of them can't handle the WSL or so,
so what is our variants?"

Written 2026-11-12. Stage 0 shipped the same day (commit 76aee4e8).

---

## Who this affects

| What | Windows | Linux | macOS |
|---|---|---|---|
| Kazma itself | a boot task (S4U): starts with nobody signed in | a user service with linger: starts at boot | a LaunchAgent: waits for a sign-in (a LaunchDaemon is documented for an always-on Mac) |
| Postgres | an OS service (section 21 G) | an OS service | an OS service |
| Containers (Full installs only) | Docker Desktop: **waits for a sign-in** | the `docker.io` / `moby-engine` service: starts at boot, but setup never gives Kazma's user access to its socket (found while writing this plan) | Docker Desktop: waits for a sign-in, as Kazma does |

Express installs (the default) use no container: private search, the
document container and the container sandbox are Full parts
(`install_components`: `docker`, `document_container`, `private_search`).
Without Docker, code runs in Kazma's local sandbox, documents in the
`process` profile, and search through the remote chain. So a restart with
nobody signed in already loses nothing on an Express install.

The problem is a **Full install on Windows or macOS**, and, on any platform,
a Kazma whose profile *requires* containers (`security.execution.container_required`,
the live install's).

## The rule this plan makes true

1. Every part Kazma installs comes back after a restart by itself, with
   nobody signed in, on any machine that can run it at all.
2. When a part cannot (the machine has no virtualization, a policy forbids
   WSL, a Mac's FileVault), setup says so when it is installed, and Kazma
   keeps serving everything else, saying which part waits and why (Stage 0).
3. Nobody has to know what WSL, a distro or a Docker context is. Setup
   chooses, installs and repairs; Settings shows one line ("Containers:
   Kazma's engine, running"); uninstall removes it.

## The variants, and who gets which

| Machine | What setup installs for the container parts | After a restart with nobody signed in |
|---|---|---|
| Any, Express | nothing: no container parts | everything back |
| Windows, Full, virtualization available (most PCs) | **Kazma's own engine** (variant B) | everything back |
| Windows, Full, the user already runs Docker Desktop and asks to keep Kazma there (`--docker desktop`) | Docker Desktop (variant C) | Kazma up; container parts wait for a sign-in, said at setup and paged once |
| Windows without virtualization (a VM whose host does not pass it through, firmware off, a policy forbids WSL) | no container parts (variant A), with the reason | everything installed is back |
| Linux, Full | Docker Engine as a system service, with access for Kazma's user (variant D) | everything back |
| macOS, Full | Docker Desktop (variant E) | Kazma and containers wait for a sign-in, as the Mac does with FileVault |

### A. No containers

What Express does today. Each container part has a fallback that needs none:
the local code sandbox, the `process` document profile (bounded, but not
qualified for hostile documents: section 19 E), the remote search chain
(SearXNG is only the first backend). The memory graph is optional.

### B. Kazma's own container engine (Windows)

Docker Engine (the open-source daemon, Apache-2.0; not Docker Desktop) in a
WSL 2 distro of Kazma's own, `kazma-engine`, started at boot by the guard.

- **Installed by setup, never by the user.** Setup enables the two Windows
  features, installs WSL from its own MSI release pinned by SHA-256 (as it
  pins uv; `wsl --install` refused to run over SSH on a clean Windows,
  2026-11-12), imports Ubuntu's published WSL image pinned by SHA-256 against
  Ubuntu's `SHA256SUMS`, and installs Docker Engine from Docker's apt
  repository at a pinned series. One restart when the features were off.
- **Started by a boot task of its own, `KazmaEngine`** (S4U, normal rights,
  at startup and every 5 minutes), not by Kazma's guard: the engine serves
  every project on the PC, and stays up when Kazma is paused or updated. The
  task holds a keep-alive (`wsl.exe -d kazma-engine -- sleep infinity`) and
  starts it again when it ends (a `wsl --shutdown`, an update), backing off
  to a minute. The guard watches the engine and pages through the existing
  `docker_away` condition; it never launches Docker Desktop for it.
  Registering an S4U task needs administrator rights once (setup runs
  elevated; on the owner's PC it was the owner's step).
- **An ordinary Docker context.** The engine listens on `127.0.0.1:2376`
  with mutual TLS: a CA kept in the distro, a client key readable by the
  account only. `docker context create kazma-engine` points at it, so
  `docker` and `docker compose` on Windows, Kazma's included, work as they
  do with Docker Desktop. Measured: without the client key the connection is
  refused. (`wsl.exe -d kazma-engine -- docker ...` also works, binary-safe,
  about 100 ms a call: the fallback where a port is not wanted.)
- **Windows paths in a mount.** The engine mounts the drives at `/c`, `/g`
  (`[automount] root=/`), and `COMPOSE_CONVERT_WINDOWS_PATHS=1` makes compose
  send `G:\x` as `/g/x`, so compose files with relative bind mounts work
  unchanged. Kazma translates its own mounts (the code sandbox's workspace)
  by the engine's daemon labels (`io.kazma.engine.drive-root=/`); a Linux
  engine without them gets no Windows mount, with the reason said.
- **Pinned by Kazma.** Kazma names its Docker context in a setting instead of
  trusting the CLI's current one: Docker Desktop, when someone opens it, may
  switch the current context back to itself.
- **Ports published on 127.0.0.1 reach Windows 127.0.0.1** through WSL's
  localhost forwarding (measured: 200 from Windows for a container in the
  distro). Kazma's addresses do not change.
- **Beside Docker Desktop, not instead of it.** Docker Desktop's engine runs
  in a network of its own (measured: no `docker0` in the shared WSL network),
  so both run at once. The user's own containers stay where they are. The
  engine's bridge and address pools are chosen at setup outside WSL's NAT
  range and the machine's routes.
- **Kept up to date.** `kazma update` upgrades Docker Engine within its
  pinned series; Ubuntu's security updates are applied by
  `unattended-upgrades` in the distro; the engine's version is in the status.
- **Watched and shown.** Readiness, `--status`, the disk watch (the distro's
  disk) and Settings name "Kazma's container engine".
- **Licensing.** Docker Desktop needs a paid subscription in a company with
  more than 250 employees or more than $10 million a year; Docker Engine does
  not. Variant B lets such a company run a Full install without it.
- **Requirements:** a 64-bit Windows 10 21H2 or later, Windows 11 or Server
  2022 or later; virtualization on (the same as Docker Desktop: setup's
  preflight already checks it, section 21c); administrator rights once.

Measured on the clean drill VM (`KazmaVMs`, Windows 11, nested
virtualization on), 2026-11-12: a boot task with nobody signed in started
the engine and kept a container reachable from Windows ("Measurement"
below).

### C. Docker Desktop

For a user who prefers it or whose containers are already there. Kazma stays
up while Docker Desktop waits for a sign-in, says so in readiness and
`--status`, and the guard pages once (3 minutes when nobody is signed in,
5 otherwise) with "Sign in to this PC to start it" (Stage 0). Setup says
before installing that the container parts wait for a sign-in. Windows'
"sign in after an update" setting does not cover this: it was on, and signed
nobody in (2026-11-12).

### D. Linux

Docker Engine from the distribution is already a system service. Setup gives
Kazma's user access to it, preferring rootless Docker as a user service with
linger (the `docker` group is root's equal), and falls back to the group with
the reason said. To be measured on a Linux machine before it ships.

### E. macOS

Docker Desktop, as now. A Mac with FileVault does not start its system until
someone signs in, so Kazma (a LaunchAgent) and Docker wait together. For an
always-on Mac, Kazma as a LaunchDaemon and Colima as a LaunchDaemon are a
later option, on demand.

### Considered and not chosen

- **Automatic sign-in (Sysinternals Autologon).** Brings Docker Desktop back,
  but keeps the account's password in the registry, readable by any
  administrator, and leaves a session open to whoever sits at the machine.
- **A Hyper-V Linux VM for the engine.** Starts at boot as a true service,
  but Windows folders cannot be mounted into its containers (the code
  sandbox mounts the workspace), every address changes, and Windows Home has
  no Hyper-V.
- **Windows containers.** Kazma's images are Linux images.
- **Rancher Desktop, Podman Desktop.** Desktop apps: they wait for a sign-in
  as Docker Desktop does.
- **A remote engine (another machine over SSH).** No workspace mounts, other
  addresses; for an advanced user only, not planned.

## Measurement

Clean drill VM, reverted to `clean Windows + SSH (2026-11-08)`, nested
virtualization turned on, 8 GB static memory:

1. WSL 3.0.5 from its MSI (SHA-256 checked), the two Windows features, one
   restart.
2. `kazma-docker` imported from `ubuntu-noble-wsl-amd64-wsl.rootfs.tar.gz`
   (SHA-256 checked against Ubuntu's list), systemd on, Docker Engine 29.9.3
   from Docker's apt repository; a probe container `nginx:alpine` with
   `--restart unless-stopped` on `127.0.0.1:18080`.
3. A boot task like Kazma's own (S4U, highest, at startup) runs `wsl.exe -d
   kazma-docker -u root -- docker ...`, reads the port from Windows, and
   logs every minute for four minutes. The VM restarted with nobody signing
   in.

Result:

| Time after the task started | What the log says |
|---|---|
| 0 s | the task runs in session 0; `query user`: "No User exists" |
| 6 s | `docker info` through `wsl.exe` started the distro: Docker 29.9.3, the probe container running |
| 21 s | Windows cannot reach the port: the `wsl.exe` call had ended |
| 45 s | the distro is **Stopped**: WSL stops a distro about 15 s after the last `wsl.exe` leaves |
| 56 s | the keep-alive child starts (`wsl.exe ... sleep infinity`) |
| 1 to 4 min | the probe container up, Windows reads `127.0.0.1:18080`: 200 every minute |
| 2 min after the task ended | the distro still running, the port still 200: the keep-alive outlives the task that started it |

So a boot task with nobody signed in can start and keep Kazma's engine, and
**the keep-alive is required**: without a `wsl.exe` attached the engine and
its containers stop within seconds. The guard starts the keep-alive before
anything else uses the engine.

Second drill, the same day, with the engine as built for the owner's PC
(mutual TLS, `KazmaEngine` task, S4U, Limited) and a probe container on
`127.0.0.1:18080`, the VM restarted with nobody signed in:

| Check | Result |
|---|---|
| A service as **SYSTEM** reads the port (a tunnel service runs that way) | 200 from 45 s after boot, every 45 s for six minutes |
| A service as **LOCAL SERVICE** reads it | the same |
| `wsl --shutdown` (WSL and Docker Desktop updates do it) | the task's keep-alive started again after 5 s; the container answered 19 s after the shutdown |
| Someone signed in while the engine runs | on the owner's PC: the engine (session 0) and the owner's own distros (session 1) run side by side in one WSL VM, and the `kazma-engine` context answers from the signed-in session |

Not measured: a Windows update restart rather than a plain one, sleep and
resume, WSL updating itself.

## Stages

Each stage lands with its gates and their negative controls (AGENTS.md
conventions), and is deployed when complete.

**Stage 0 (shipped 2026-11-12).** Kazma stays up while Docker waits:
readiness `waits_for`, the guard never restarts Kazma for it, one page per
problem (AGENTS.md section 39).

**Stage 1: one way to call Docker.** Every Docker call in the product goes
through one helper in `docker_cli` that knows the engine (`desktop`,
`kazma`, `system`): the argv prefix, the environment, and the path
translation for mounts. The call sites the inventory found: the code sandbox
(`code_exec`), the document broker, guard dependencies, service exposure,
system dependencies, setup and install (compose, `docker port`, the database
move), `pg_bridge`/`pg_move`, the guard (engine probe, container starts,
broker checks), `scripts/document_worker_image.py`. A gate fails on a Docker
call made any other way (planted calls as its negative control). No
behaviour changes on Docker Desktop.

**Stage 2: Kazma's engine on Windows.**
- The install part `container_engine` replaces Docker Desktop in Full on
  Windows; `--docker desktop` keeps Docker Desktop.
- Pinned downloads with hashes: the WSL MSI, Ubuntu's image, Docker's apt
  key and series.
- The guard supervises the engine: keep-alive child, restart, the existing
  `docker_away` page, no Docker Desktop launch.
- Readiness, `--status`, Settings and the disk watch name the engine.
- Uninstall removes the distro, after a confirm that names what it deletes.
- Gate: the drill above, automated (`scripts/engine_boot_drill.py`): a Full
  install on the clean VM, a restart with nobody signed in, every container
  part ready within five minutes. Docker Desktop's launch rule as the
  negative control.

Status (2026-11-12): setup's half is shipped. `kazma setup install` installs
the engine on Windows whenever a container part is chosen
(`container_engine_setup.py`: WSL, the distro, Docker Engine, the CLI, the
context, the boot task, the record; pinned downloads; a preflight before
anything is installed); `--docker desktop` keeps Docker Desktop; `kazma setup
engine` installs, repairs, checks or removes it (removal refused while
another project's containers live in it). The guard's supervision and
Settings' choice shipped in Stage 1's commits. Drilled by hand on the clean
VM the same day (`kazma setup engine`: WSL, a restart, the rest in 321 s;
Docker answered 29 s after a reboot with nobody signed in; three bugs found
and fixed, AGENTS.md section 63). Open: the automated boot drill
(`scripts/engine_boot_drill.py`), and readiness and the disk watch naming the
engine.

**Stage 3: move an existing install.** `kazma setup engine
--move-from-desktop`, on the pattern of the Postgres move (section 21 G):
- Kazma held while idle.
- SearXNG recreated in the engine.
- The memory graph's volume copied between the engines and checked.
- The document worker image built there, the sandbox image pulled.
- Kazma switched and seen ready.
- The old containers stopped and kept.

Drilled on the VM with Docker Desktop installed first.

Status (2026-11-13): shipped (`kazma_core/engine_move.py`, AGENTS.md section
63). What changed from the list above, and why:
- **What moves is Kazma's record, not a fixed list.** The containers are the
  ones the server names to its guard (`guard_dependencies._match_containers`),
  read against Docker Desktop: today SearXNG and Neo4j. Another project's
  container is never named; one sharing a volume with Kazma's stops the move.
  Kazma's database in a container stops it too: it has its own move, to the
  Postgres service.
- **SearXNG is recreated from its own settings**, as every moved container
  is (`docker inspect` on Docker Desktop, `docker create` on the engine),
  rather than from `docker-compose.yml`: the settings it runs with now (its
  secret, its port) are what Kazma reaches, whether compose, setup or a
  person made it.
- **The images are prepared before Kazma is held**, not after: copying
  them, and building the document worker image when Docker Desktop has no
  build of this code, takes minutes, and none of it touches what Kazma
  uses. Kazma is held only for the copies.
- **Every volume is checked entry by entry** (owner, mode, size, SHA-256,
  links), not by a list of checksums: measured on real GNU tar (Neo4j's
  image) and BusyBox tar (SearXNG's), both carry owners, modes, hard links,
  symlinks and FIFOs exactly.
- **A failure puts everything back**, and a run that dies half way is rolled
  back by the next from its journal.
- **`kazma setup engine` alone** on a Docker Desktop install now writes
  Docker Desktop down as the choice before it records the engine (found
  while building this: recorded, the engine was Kazma's choice at its next
  start, with none of its containers).

Drilled 2026-11-13 on the clean VM (`clean Windows + SSH + nested
virtualization (2026-11-12)`):
- **Setup:** WSL from `engine:wsl`, then Docker Desktop 4.99 through
  setup's winget step. Kazma (a source zip, its service registered) ran on
  Docker Desktop with SearXNG and Neo4j from the shipped compose files, a
  marker node in the graph, and another project's container with a volume.
- **Run:** `kazma setup engine --move-from-desktop -y` ran in the signed-in
  session, as an owner would from a terminal. It took 766 s, the engine's
  install included.
- **What the move did:** it carried both Neo4j volumes (528 MB; every entry
  the same on both engines) and SearXNG's, and saw both containers healthy
  and answering from Windows. Then `containers.engine` = `kazma`, the
  guard's record said `kazma`, and Kazma was up.
- **What was left:** the Docker Desktop copies were stopped with restart
  `no`, and the other project's container ran untouched.
- **After a restart with nobody signed in,** both containers were healthy
  39 s after boot, and the graph still held its marker.

The drill found three things:
- **The hold found no guard on an install with no `.git`** (a source zip).
  It said "no guard supervises it" over a running guard, and refused. The
  move now finds the guard from the install's own folder
  (`setup._checkout_root`).
- **A Kazma waiting for a provider key read as not ready** in a rerun's
  recovery. It now reads as up, as the guard's own resume counts it.
- **248 "carried" lines for Neo4j:** its start changes the owner of its
  whole folder. The move now says one line per container.

**Open, the engine's, not the move's:** at a Windows restart the engine
gets only seconds. Neo4j logged "Stopping..." 3 s after the shutdown began,
and never "Stopped.". Windows Update then restarted the VM again 32 s after
it booted, while Neo4j's start script was appending a line to its config.
That line came back as 30 NUL bytes (the size kept, the data lost, as after
a power cut), and Neo4j refused the file at every start after. A container
copied the move's way, with and without the carried files, started three
times cleanly; so did one never moved. Stopping the engine's containers
before Windows ends the WSL VM is follow-up work for the engine.

**Stage 4: Linux access.** Rootless Docker as a user service with linger,
else the `docker` group with the reason; measured on a Linux machine.
Status (2026-11-12): the `docker` group half is shipped (`kazma setup
system` starts Docker at boot and adds the account; a socket the account may
not use is found and fixed with the group alone). Rootless is not done, and
nothing here was measured on a Linux machine yet.

**Stage 5 (separate plan): isolation without containers.** An AppContainer
sandbox on Windows and bubblewrap on Linux for code and documents, so a
strict profile no longer needs a container at all. After it, containers are
needed only for private search.

**Later, on demand:** macOS always-on (variant E).

## The owner's PC (started 2026-11-12, with the owner's approval)

The owner asked "why not just use Docker as a service?" and approved backing
up Docker Desktop and moving to it. On Windows no Windows service runs Linux
containers by itself (Docker's own Windows service runs Windows containers
only; Docker Desktop's service is a helper, its engine starts with the app
at sign-in), so "Docker as a service" for Linux containers is this engine.

Done:
- **Backup** of Docker Desktop: every container's settings, consistent
  `pg_dumpall` of the running databases, every image (one archive), every
  volume, checksums. Kept in `C:\Users\balfa\docker-desktop-backup\2026-11-12`,
  readable by the account only.
- **The engine:** the `kazma-engine` distro in
  `%LOCALAPPDATA%\Kazma\engine`, Docker Engine 29.9.3, networks in
  `10.213.0.0/16`, the `kazma-engine` context; every image loaded with the
  same image IDs.

Then, the same day:
1. The owner registered `KazmaEngine`; it alone holds the engine up.
2. Stage 1 shipped (commit c396c3d2): the explicit choice, the pin, the
   mount translation, the guard's repair.
3. **Restore test**: both ShipX database dumps loaded into a throwaway
   Postgres on the engine with no error (79 tables; the old database's
   three schemas).
4. **Moved, one container at a time** (stopped on Docker Desktop with restart
   policy `no` and kept; volumes copied while stopped, a sha256 list of every
   file equal on both sides; recreated from its saved settings; files added
   inside a container carried too -- HypertFit's Redis had its config file
   written there by compose, `shipx-web` a hand-copied script):
   HypertFit (Redis, app), ShipX (Postgres, Redis, the old `shipx-db` --
   something still queries it --, web, worker), Kazma's SearXNG and Neo4j.
   ShipX's databases dumped before and after: the same data, byte for byte
   (only `pg_dump`'s random `\restrict` key differs). `shipx.kazma.ai`
   answers through the tunnel as before; Kazma is ready, its search and
   memory graph answer from the engine.
5. Left on Docker Desktop, stopped: the old `kazma-db-win` (Kazma's database
   before its move to the Postgres service), two test databases and ShipX's
   one-off `shipx-migrate`.

Found on the way: HypertFit's app had restarted 44 times since the morning's
boot (its own start-up checks fail; `frs.kazma.ai` answers 502) -- before
and after the move alike. HypertFit's own problem, reported to the owner.

Left:
1. The owner chooses Kazma's engine in Settings -> System -> Container
   engine (a request from this PC reaches live only through the tunnel's
   sign-in), and restarts the guard so it runs the new guard code.
2. The docker CLI's default context becomes `kazma-engine`, so the owner's
   own `docker` and `docker compose` reach the engine.
3. Docker Desktop no longer starts at sign-in (the owner's choice; kept
   installed for rollback).

Catches, said to the owner: no Docker Desktop window for these containers;
ports published on `0.0.0.0` (ShipX's 5600, the old `shipx-db` on 5533) are
reachable from this PC only, no longer from the LAN (the tunnel uses
`127.0.0.1`, so the sites are unaffected); `host.docker.internal` is not
defined by a plain engine: HypertFit's `SMPLX_WORKER_URL` names it (port
8790, where nothing listens today, so the SMPL-X worker is already off);
when it runs again it belongs in a container on HypertFit's network, or the
engine is given the Windows host's address.

## Independent review (Fable, 2026-11-12) and what changed

The owner asked for an independent opinion before going on. Fable's verdict:
proceed with changes. It agreed that on Windows an engine in WSL started by
a boot task is the only "Docker as a service" for Linux containers (Docker's
own Windows service runs Windows containers only; Docker Desktop, Rancher
Desktop and Podman machine start at a sign-in; a Hyper-V VM has no Windows
mounts and no Home edition; automatic sign-in stores the password). Taken
from it:

1. **No accidental switch.** The first code pinned Kazma's engine whenever
   its context existed; on the owner's PC (engine made, nothing moved) the
   next reload would have looked for its containers on an empty engine. The
   choice is now explicit: `containers.engine` (`kazma`, `desktop`), else the
   engine setup recorded it installed, else nothing pinned.
2. **Every Docker call follows the choice**, not only the server's: the first
   `find_docker_cli` of any Kazma process applies it (the CLI, setup,
   scripts); the guard takes it from the server's record.
3. **Paths follow the choice**, never a cached `docker info` (an engine that
   was down sent `G:\...` to it for a minute). Only fixed local drives are
   mounted; a share, a mapped or a removable drive is refused with the
   reason.
4. **The guard repairs the engine itself**: it runs the task, restarts
   Docker inside the distro, and holds its own keep-alive when the task is
   missing or refused.
5. **Express stays container-free** (the owner's decision after the review):
   the engine is installed when a container part is chosen; Docker Desktop
   only when the user picks it.
6. Still to build, from the review: a preflight for another Docker inside a
   WSL distro and for `.wslconfig` networking; Docker updates only through
   `kazma update` (never at a random hour); a dated Ubuntu image; a watch on
   the engine's disk; an uninstall that refuses while other projects' data
   lives in it; the native sandboxes sooner.

## Decisions

- **Variant B is the default for Full on Windows**, Docker Desktop the
  opt-out. Decided under the owner's standing rule ("choose the right
  industrial professional decision"): it is the only Windows variant that
  comes back with nobody signed in and keeps workspace mounts and addresses.
- **The owner's PC:** Kazma's containers move to the engine in Stage 3. The
  owner's other projects on Docker Desktop (ShipX, HypertFit) went down this
  morning too; moving them is the owner's choice, and the same engine can
  take them.
