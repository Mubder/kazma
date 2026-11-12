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
- **Started by the guard, which already starts at boot.** The guard holds a
  keep-alive child (`wsl.exe -d kazma-engine -- sleep infinity`), supervised
  like the document broker: restarted when it dies (a user's `wsl --shutdown`
  stops every distro), paged once through the existing `docker_away`
  condition. Docker Desktop is never launched for this engine.
- **No socket and no certificates.** Kazma runs `wsl.exe -d kazma-engine --
  docker ...` for every Docker call: only the Windows account that owns the
  distro can use it, and nothing listens on a port. Measured on this PC:
  8 MB through stdin and stdout byte for byte, about 100 ms a call (a
  container start costs seconds).
- **Windows paths in a mount are translated** to the distro's view
  (`C:\Users\x` -> `/mnt/c/Users/x`) by one helper; compose runs inside the
  distro, so the compose files' relative paths work unchanged.
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

Not measured yet (Stage 2's drill does it): someone signing in while the
engine runs (their own distros and the engine side by side), `wsl
--shutdown` while Kazma runs, and a Windows update restart rather than a
plain one.

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

**Stage 3: move an existing install.** `kazma setup engine
--move-from-desktop`, on the pattern of the Postgres move (section 21 G):
- Kazma held while idle.
- SearXNG recreated in the engine.
- The memory graph's volume copied between the engines and checked.
- The document worker image built there, the sandbox image pulled.
- Kazma switched and seen ready.
- The old containers stopped and kept.

Drilled on the VM with Docker Desktop installed first.

**Stage 4: Linux access.** Rootless Docker as a user service with linger,
else the `docker` group with the reason; measured on a Linux machine.

**Stage 5 (separate plan): isolation without containers.** An AppContainer
sandbox on Windows and bubblewrap on Linux for code and documents, so a
strict profile no longer needs a container at all. After it, containers are
needed only for private search.

**Later, on demand:** macOS always-on (variant E).

## Decisions

- **Variant B is the default for Full on Windows**, Docker Desktop the
  opt-out. Decided under the owner's standing rule ("choose the right
  industrial professional decision"): it is the only Windows variant that
  comes back with nobody signed in and keeps workspace mounts and addresses.
- **The owner's PC:** Kazma's containers move to the engine in Stage 3. The
  owner's other projects on Docker Desktop (ShipX, HypertFit) went down this
  morning too; moving them is the owner's choice, and the same engine can
  take them.
