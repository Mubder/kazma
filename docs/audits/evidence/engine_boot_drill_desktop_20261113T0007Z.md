# Engine boot drill: desktop

Run 2026-11-13 00:07 UTC by `scripts/engine_boot_drill.py` (`docs/plans/CONTAINERS_WITHOUT_A_SIGN_IN_PLAN.md`, Stage 2).

| | |
|---|---|
| Commit | `41b4e09051571c37152bf80c9f4798f8c45a4dc0` |
| VM | `KazmaVMs`, reverted to `clean Windows + SSH + nested virtualization (2026-11-12)` |
| Engine installed | Docker Desktop (`--docker desktop`) |
| Kind | negative control: nothing may come up |
| Budget | every container part ready within 300 s of boot, nobody signed in |
| Readings | 26, every 15 s |
| Kazma answered readiness | at 54 s |
| Verdict | **not_up** (expected not_up: held) |

- Install round 1: exit 1 after 1692 s; Windows restarted for WSL.
- Install round 2: exit 1 after 132 s.
- Install round 3: exit 1 after 49 s.
- Strict code profile set on the VM: container_required = True from ['C:\\kazma-drill\\.env']

| Part | Ready at (s after boot) | In budget | Ready at the end | Last reading |
|---|---|---|---|---|
| Container engine (kazma_guard.py --status: docker) | never | no | no | not checked (idle) |
| Code sandbox (readiness: code_execution) | never | no | no | No available isolated execution backend: Docker Desktop is not running |
| Document worker (kazma_guard.py --status: doc broker) | never | no | no | idle (not_requested), tcp://127.0.0.1:29504 |
| Private search (SearXNG /healthz) | never | no | no | KAZMA_SEARXNG_URL not set |

Every reading: `docs/audits/evidence/engine_boot_drill_desktop_20261113T0007Z.json`.
