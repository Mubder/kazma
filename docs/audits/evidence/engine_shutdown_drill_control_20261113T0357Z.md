# Engine shutdown drill: control

- Commit: `220fbd5df48cbbd7111b0928dc33d7c8b1d94a44`; VM `KazmaVMs` from `clean Windows + SSH + nested virtualization (2026-11-12)`
- Finished: 2026-11-13 03:57 UTC
- Negative control: the verdict must be `unclean`.
- Verdict: **unclean**

  - round 1: the writer never got its stop signal (no TERM)
  - round 2: the writer never got its stop signal (no TERM)

| Round | Writer file of the boot that ended | NUL bytes | TERM | DONE | Last line | Came back | Shutdown took |
|---|---|---|---|---|---|---|---|
| 1 | `w-c4bd66e5-4c49-4db1-b2b4-424aa71308af.log` (5349 bytes) | 0 | no | no | `line 272 1794542176` | yes | 0.4 s |
| 2 | `w-9f6001ec-4f57-4eea-9c28-e60bf646f2df.log` (6369 bytes) | 0 | no | no | `line 323 1794542238` | yes | 0.3 s |

What the KazmaEngineShutdown service said, each round (Application log, UTC):

- Round 1: nothing (no service)
- Round 2: nothing (no service)

Readings: `engine_shutdown_drill_control_20261113T0357Z.json`.
