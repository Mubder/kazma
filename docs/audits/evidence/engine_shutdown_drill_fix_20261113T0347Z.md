# Engine shutdown drill: fix

- Commit: `3038a5b4029f42a74280f8758941ecbf2fe22489`; VM `KazmaVMs` from `clean Windows + SSH + nested virtualization (2026-11-12)`
- Finished: 2026-11-13 03:47 UTC
- The verdict must be `clean`.
- Verdict: **clean**


| Round | Writer file of the boot that ended | NUL bytes | TERM | DONE | Last line | Came back | Shutdown took |
|---|---|---|---|---|---|---|---|
| 1 | `w-10cac7b0-8ebd-4c02-a94b-bee5f6b22a02.log` (6652 bytes) | 0 | yes | yes | `DONE 1794541534` | yes | 9.4 s |
| 2 | `w-0d08ef44-b48a-42af-aaef-33a9eeffbae4.log` (5852 bytes) | 0 | yes | yes | `DONE 1794541597` | yes | 9.0 s |

What the KazmaEngineShutdown service said, each round (Application log, UTC):

- Round 1: 2026-11-13T03:45:25.7930240Z Windows is shutting down: stopping Kazma's engine; 2026-11-13T03:45:34.8768137Z the engine is stopped after 9.1 s: ENGINE_STOPPED in 8.8 s (docker stop exit 0, disks written); 2026-11-13T03:45:34.8912021Z the engine's distro is powered off
- Round 2: 2026-11-13T03:45:34.8912021Z the engine's distro is powered off; 2026-11-13T03:46:28.5398273Z Windows is shutting down: stopping Kazma's engine; 2026-11-13T03:46:37.3983351Z the engine is stopped after 8.9 s: ENGINE_STOPPED in 8.8 s (docker stop exit 0, disks written); 2026-11-13T03:46:37.4162894Z the engine's distro is powered off

Readings: `engine_shutdown_drill_fix_20261113T0347Z.json`.
