# Engine boot drill: kazma

Run 2026-11-12 22:52 UTC by `scripts/engine_boot_drill.py` (`docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`, Stage 2).

| | |
|---|---|
| Commit | `caa8620a8c4a7e4f3752fd04e65e0c216098f0f7` |
| VM | `KazmaVMs`, reverted to `clean Windows + SSH + nested virtualization (2026-11-12)` |
| Engine installed | Kazma's container engine (`--docker kazma`) |
| Kind | the drill: everything must come up |
| Budget | every container part ready within 300 s of boot, nobody signed in |
| Readings | 26, every 15 s |
| Kazma answered readiness | at 57 s |
| Verdict | **pass** (expected pass: held) |

- Install round 1: exit 1 after 1946 s; Windows restarted for WSL.
- Install round 2: exit 0 after 690 s.
- Strict code profile set on the VM: container_required = True, through C:\kazma-drill\.env (restated: the drill's check required that answer; the line it kept was PowerShell's progress output)

| Part | Ready at (s after boot) | In budget | Ready at the end | Last reading |
|---|---|---|---|---|
| Container engine (kazma_guard.py --status: docker) | 73.6 | yes | yes | answers (ok) |
| Code sandbox (readiness: code_execution) | 57.2 | yes | yes | ok on engine kazma |
| Document worker (kazma_guard.py --status: doc broker) | 89.8 | yes | yes | running (requested), pid 5380, tcp://127.0.0.1:29504 |
| Private search (SearXNG /healthz) | 37.2 | yes | yes | HTTP 200 |

Every reading: `docs/audits/evidence/engine_boot_drill_kazma_20261112T2252Z.json`.
