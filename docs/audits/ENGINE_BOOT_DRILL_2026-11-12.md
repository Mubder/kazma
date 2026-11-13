# Engine boot drill, 2026-11-12

The gate of Stage 2 in `docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`:
a Full install on the clean drill VM, a restart with nobody signed in, every
container part ready within five minutes. Run by
`scripts/engine_boot_drill.py` from the host PC against the Hyper-V VM
`KazmaVMs`, reverted each time to `clean Windows + SSH + nested
virtualization (2026-11-12)` (Windows 11, 8 GB, nested virtualization on).

## What the drill does

1. Reverts the VM, copies one commit of the checkout (`git archive`) and
   `uv`, syncs the base packages.
2. Runs `kazma setup install --mode full -y` (or `--docker desktop`) detached
   from the SSH session, restarts Windows when setup asks (WSL), runs it
   again, and once more after a round that left something undone.
3. Sets the strict code profile (`security.execution.container_required`,
   the live install's), so Kazma's readiness probes the code sandbox's
   engine and says which one it waited for.
4. Restarts Windows. Nobody signs in; every reading checks `query user`.
5. Every 15 s reads Kazma's own readiness (`/health/ready`),
   `kazma_guard.py --status` and SearXNG's `/healthz` on the VM.

A part counts as ready when: the engine, `--status` says `docker : answers`;
the code sandbox, readiness's `code_execution` check is `ok` on the engine
the drill installed; the document worker, `--status` says `doc broker :
running`; private search, `/healthz` answers 200.

## The result

Commit `caa8620a8`, Kazma's own engine (`--docker kazma`):
**pass**, nobody signed in at any of 26 readings.

| Install | |
|---|---|
| Round 1 | exit 1 after 1,946 s: everything but the three container parts in place; Windows must restart (WSL) |
| Round 2 | exit 0 after 690 s: the engine, the document worker image, SearXNG |

| Part, after the restart | Ready at (s after boot) |
|---|---|
| Private search (SearXNG) | 37 |
| Code sandbox (readiness, on Kazma's engine) | 57 |
| Container engine (`--status`) | 74 |
| Document worker (`--status`) | 90 |

Kazma itself answered readiness 57 s after boot, its code sandbox check
saying `"engine": "kazma"`; every part stayed up to the last reading, 429 s
after boot. Evidence:
`docs/audits/evidence/engine_boot_drill_kazma_20261112T2252Z.md` (and its
`.json`, every reading).

## The negative control

Commit `41b4e0905`, the same drill with Docker Desktop (`--docker
desktop`): **not_up**, as it must be, nobody signed in at any of 26
readings. Docker Desktop was installed (round 2), but it starts only in a
signed-in session: it never ran during the install, so the document worker
image and SearXNG could not be set up (rounds 2 and 3 end with exit 1,
"failed to connect to the docker API"), and after the restart no part came
up in 427 s. Kazma answered readiness at 54 s and said what it waited for,
by name: its code sandbox check read "No available isolated execution
backend: Docker Desktop is not running" (`"engine": "desktop"`), and
`kazma_guard.py --status` quoted it ("up, waiting for docker"). Evidence:
`docs/audits/evidence/engine_boot_drill_desktop_20261113T0007Z.md`.

The first attempt at this control (on `caa8620a8`) never got Docker Desktop
at all: its path installed WSL with `wsl --install`, which Windows refuses
outside a signed-in console. Fixed in `41b4e0905` (finding 4 below).

## What the drill found

Two earlier runs on `6e667cdaa`, and the first control, found four faults;
each is fixed with a test that fails on the old code.

1. **Setup's WSL checks waited on its console.** `container_engine_setup._out`
   passed `input=None`, so `wsl.exe` inherited setup's console. On a Windows
   without WSL, the stub answers `wsl.exe --version` with "press any key to
   install WSL" and waited until the 60 s timeout, at every WSL call setup
   made; a key pressed then would have installed WSL from the Store. Fixed in
   `9f044c037` (no input of the step's own is no input). The terminal gate in
   `tests/test_setup_system.py` had missed it: its module list predated
   `container_engine_setup.py`, and it counted an `input=` that can be None
   as saying the input.
2. **A first Full install ended "not installed" for a SearXNG that was
   starting.** Setup asked SearXNG 2 s after `docker compose up`; it answered
   49 s later. The install ended with exit 1 and "Run setup again to finish".
   Fixed in `caa8620a8`: a step waits for SearXNG to answer, within 120 s
   (`tests/test_install_modes.py`, a SearXNG that never answers as the
   control). In the final run, round 2 ends with exit 0.
3. **The drill set the strict profile in the wrong store.** It ran the write
   from the VM account's home folder: no `.env` loaded, so the setting went
   to a local store of its own and read back True, while the server's
   Postgres never had it. Readiness then had no code sandbox check, and the
   first boot read it as never ready. The drill now writes from the install
   folder and refuses unless the install's `.env` is the one loaded
   (`tests/test_engine_boot_drill.py`). Re-measured on that install: every
   part ready 60 s after boot.
4. **An unattended `--docker desktop` install never got Docker Desktop.** It
   installed WSL with `wsl --install --no-distribution`, which Windows
   refuses outside a signed-in console ("The Windows Subsystem for Linux is
   not installed"). Fixed in `41b4e0905`: both paths get WSL from one step,
   the engine's (the two Windows features and WSL's own MSI, pinned by
   SHA-256), and the container steps wait for the restart on either path
   (`tests/test_install_modes.py`).

## Not measured

A Windows update restart (rather than a plain one), sleep and resume, and
WSL updating itself, as in the plan's earlier measurements. The drill VM has
no Docker Desktop sign-in, so the control shows what an unattended restart
leaves, not what a later sign-in brings back.
