# Engine shutdown drill (2026-11-13)

Kazma's container engine (Docker Engine in the `kazma-engine` WSL distro,
AGENTS.md section 63) must stop its containers cleanly when Windows shuts
down or restarts, so nothing they wrote in their last seconds is lost. The
engine-move drill found it did not: Neo4j logged "Stopping..." and never
"Stopped.", and a config line its start script had just appended came back
as 30 NUL bytes after Windows Update restarted the VM.

Everything here ran on the drill VM `KazmaVMs` (Hyper-V on the owner's PC,
Windows 11 build 26300, nested virtualization, 8 GB), from its checkpoint
`clean Windows + SSH + nested virtualization (2026-11-12)`, reached over SSH
with nobody signed in, as after an unattended Windows Update restart. The
writer is a container that appends a short numbered line every 100 ms with
no fsync, as most programs do, and on SIGTERM writes `TERM`, takes 8 s to
stop (as Neo4j did) and writes `DONE`.

## Before: what Windows does to WSL

Measured with the engine as setup installed it (commit 220fbd5df), a probe in
the distro (a heartbeat written and fsync'd every 100 ms, a mark when systemd
begins its stop, a mark when Docker has stopped, persistent journald), the
writer, and Windows' own events.

| | |
|---|---|
| Restart began (event 1074) | 02:07:13.818 UTC |
| Event log stopped (6006) | 02:07:14.096 |
| Kernel shutting down (event 13) | 02:07:15.616 |
| Distro's heartbeat | ran to about 02:07:15.9, then stopped: no SIGTERM, no systemd stop, Docker never began stopping |
| Writer | no `TERM`; its file came back with **760 NUL bytes** (about 4 s of lines: the size written, the data lost) |

Why, from WSL's source (`microsoft/WSL`) and the service manager:
- `WSLService` and `vmcompute` are `IGNORES_SHUTDOWN`: Windows sends them no
  shutdown notice, and they hold no shutdown up. The VM ends when Windows
  ends their processes.
- WSL shuts a distro down gracefully (`systemctl poweroff`, then a hard
  poweroff after `boot.initTimeout`, 10 s) only for `wsl --terminate`,
  `wsl --shutdown`, an idle distro, or a sign-out of the Windows session that
  started it (`WslService::OnSessionChanged`). The `KazmaEngine` boot task's
  session is session 0, never signed out.

What a fix may not use, measured:
- **Task Scheduler** ends every running task, and every process it started,
  60 ms into a shutdown (Task Scheduler operational log, event 111,
  "Task Scheduler terminated ... instance of the \KazmaEngine task"; return
  code `0x8007042B`), before any service hears of the shutdown. It starts no
  task during one: an on-demand run answered `0x8007045B`,
  ERROR_SHUTDOWN_IN_PROGRESS.
- **LocalSystem** cannot run anything in the distro: WSL refuses it
  (`WSL_E_LOCAL_SYSTEM_NOT_SUPPORTED`). A GPO shutdown script runs as SYSTEM.

## The fix

`KazmaEngineShutdown`, a Windows service (AGENTS.md section 63, "A Windows
shutdown stops the engine cleanly"): it takes the preshutdown notice and
holds Windows, within 120 s, while it stops the engine through a channel
opened before the shutdown. `keep-engine.ps1` starts the distro's holder
through WMI, outside Task Scheduler's job, with its stdin and stdout on the
service's named pipes. At preshutdown the service writes `stop`; the distro
stops Docker then containerd (every container its stop signal and its own
stop timeout, restart policies kept), writes its own disks, and answers;
then `poweroff`.

The measurements found four faults on the way to it, each now held by a test
in `tests/test_engine_clean_shutdown.py`:

1. A stop task run by the service at preshutdown never ran:
   ERROR_SHUTDOWN_IN_PROGRESS.
2. With keep-engine.ps1 making the stop on a named event, it was ended by
   Task Scheduler before the stop's answer came back (in one run it won the
   race by 50 ms and still died before reading the answer).
3. `systemctl mask` before `systemctl stop` reloaded the units with no
   order: containerd stopped at once under a Docker still stopping its
   containers ("Waiting for containerd", then "Force shutdown daemon" 36 s
   later). The stop now goes Docker, containerd, then the mask.
4. A plain `sync` also flushes the Windows drives mounted at `/c` and never
   returned during a shutdown. Only the distro's own disks are written now
   (`sync -f` per ext4/xfs/btrfs mount, bounded at 20 s). And .NET's
   `WriteLine` sent `stop\r`, which bash did not read as `stop`.

## After

Measured the same way with the fix installed through `kazma setup engine`
(the repair path: an engine set up before it, as on the owner's PC):

| | |
|---|---|
| Restart began (event 1074) | 03:14:58.257 UTC |
| Service heard the preshutdown notice | 03:14:59.380 (+1.1 s) |
| Engine stopped, answered | 03:15:08.358: `ENGINE_STOPPED in 8.8 s (docker stop exit 0, disks written)` |
| Distro powered off | 03:15:08.373 |
| Event log stopped (6006) | 03:15:08.442 (+10.2 s) |
| Writer | `TERM`, its 8 s stop, `DONE`; **no NUL byte**; it came back after the boot by its restart policy |

`sc.exe control KazmaEngineShutdown 128` (the drill control, no restart):
the same stop in 9.0 s, then keep-engine.ps1 started the engine again 12 s
later and the writer came back.

## The drill script

`scripts/engine_shutdown_drill.py run --ref <commit>` reverts the VM, installs
the commit's engine through setup, starts the writer and restarts Windows
`--rounds` times; `--control` on a commit before the fix must be `unclean`.
Its runs, each from the clean checkpoint, the engine installed through
`kazma setup engine` (WSL, a restart, the rest):

| Run | Commit | Round | Writer's file of the boot that ended | NUL bytes | TERM | DONE | Came back | Shutdown took (1074 to 6006) | Verdict |
|---|---|---|---|---|---|---|---|---|---|
| fix | 3038a5b40 | 1 | 6,652 bytes, last line `DONE` | 0 | yes | yes | yes | 9.4 s | **clean** |
| fix | 3038a5b40 | 2 | 5,852 bytes, last line `DONE` | 0 | yes | yes | yes | 9.0 s | |
| control | 220fbd5df | 1 | 5,349 bytes, last line `line 272` | 0 | no | no | yes | 0.4 s | **unclean** |
| control | 220fbd5df | 2 | 6,369 bytes, last line `line 323` | 0 | no | no | yes | 0.3 s | |

In the fix's rounds the service said, each time: "Windows is shutting down:
stopping Kazma's engine", then "the engine is stopped after 9.1 s"
(8.9 s): `ENGINE_STOPPED in 8.8 s (docker stop exit 0, disks written)`, then
"the engine's distro is powered off". In the control's, Windows waited for
nothing and the writer was cut off mid-run. That neither control round lost
bytes to NULs is chance: the loss depends on where ext4's last commit fell,
and the first measurement lost 760. A cut-off writer is unclean either way.
Readings: `evidence/engine_shutdown_drill_fix_20261113T0347Z.json`,
`evidence/engine_shutdown_drill_control_20261113T0357Z.json` (and their
`.md`).

## Not covered

- A power cut or a crash: Windows gives no notice at all. A shorter ext4
  commit interval and dirty-page expiry in the distro would narrow that
  window, never close it, at the cost of every container's write traffic;
  not shipped (the plan says why).
- Docker Desktop: it runs only in a signed-in session, and the drill VM is
  reached over SSH with nobody signed in. Not measured.
- The clocks: the VM's clock is stepped by Hyper-V's time sync every ~30 s
  after a checkpoint revert ("Clock change detected" in the distro's
  journal), so durations inside the distro come from `/proc/uptime`, and the
  timeline above from Windows' events.
