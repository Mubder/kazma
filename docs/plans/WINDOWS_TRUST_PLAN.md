# Windows' file check: the plan to a Kazma Windows trusts by its signatures

Status 2026-11-26. AGENTS §71; user page `docs/docs/guide/windows-smart-app-control.md`; every file
and its route: `docs/plans/WINDOWS_TRUST_INVENTORY.md`.

Smart App Control lets a program or library run when a known publisher signed
it or when Windows' cloud already knows the file well ("reputation"). On the
owner's PC on 26 Nov, with it on, the unsigned venv `python.exe` and nearly
every unsigned library still loaded by reputation; two files were refused:
psycopg's cp314 client library and the docker CLI Kazma's engine installs.
Reputation is not a promise for the next file version or the next PC, so the
plan is signatures, in this order.

## Done (26 Nov)

- The guard checks the new code before a reload swaps servers; refused
  parts are said calmly; signed PostgreSQL and Docker parts are used first
  while Smart App Control is on; Express no longer installs the developer
  tools (699 -> 359 unsigned native files, measured); a docker CLI Windows
  refuses is a wait, never an engine restart.
- Kazma never starts its own console-script launchers (`kazma.exe`,
  `kazma-web.exe`, `kazma-tui.exe`, made on the user's PC by the package
  installer, never signed): the guard, the KazmaAgent task, `kazma update`,
  setup and the hints they print use `python -m kazma_cli` (and `-m` forms);
  `tests/test_windows_trust.py` holds it.
- The outside audit's findings (26 Nov): a refused `KAZMA_DOCKER_BIN` is
  said as refused, never as missing; the PostgreSQL `kazma setup postgres`
  recorded is looked for first, and a `libpq.dll` on PATH Windows refuses is
  passed over; Windows' refusal codes have one definition
  (`windows_policy.REFUSAL_CODES`, prepush loads `docker_cli.py` by path);
  a CLI that started is asked again after 15 minutes; the guard never
  restarts the engine for a CLI Windows refuses.

- Docker through `wsl.exe` for Kazma's own engine: no Windows `docker.exe`
  needed (`docker_cli.docker_command`).
- A new Windows install runs on a signed python.org Python (`setup.ps1`
  `Find-SignedPython`, winget's `Python.Python.3.12` when none): uv's own
  builds are unsigned.

## Next

1. **Signed releases** (the SSL.com certificate, §21e's one signing step):
   the Windows installer first, as planned.
2. **The engine's shutdown helper shipped signed.** Today
   `container_engine_setup` builds `kazma-engine-shutdown.exe` on the user's
   PC with the .NET Framework's `csc.exe`: a file nothing can sign there, and
   one Smart App Control would judge by reputation it cannot have. Plan:
   - the release workflow's `windows-installer` job compiles
     `kazma_core/container_engine_assets/engine-shutdown.cs` with the
     runner's `csc.exe` (the same command `_build_shutdown_service` runs),
     signs it through `.github/actions/sign-windows-installer`, requires it
     to read Valid and timestamped, and publishes it as a release asset
     beside the installer (SHA256SUMS, attestation, Sigstore);
   - setup installs that file when the release carries it (checked against
     the release's SHA256SUMS like the source archive, `update._source_archive`),
     and builds with `csc.exe` only where no signed one exists (a checkout,
     an older release);
   - `windows_trust` reports the helper `allowed`/`waits` as today; the
     test: a release with the signed asset never runs `csc.exe`, one
     without it does (the control).
   Not done now: the release job and the signing step must exist first, and
   the certificate is pending.
3. **Docker's static CLI** (Apache-2.0, unsigned as Docker publishes it):
   sign the pinned copy with Kazma's certificate and ship it as a release
   asset, or keep preferring Docker Desktop's signed CLI where it exists.
   The owner's decision.
4. **A release bundle in which every file Kazma ships is signed**: the venv
   built in CI from `uv.lock` (python.org's signed Python; uv's standalone
   build is unsigned), every native file Kazma's own installer lays down
   signed by Kazma, shipped as one bundle. The measured scale: 689 unsigned
   files in a full install's site-packages, 359 in Express, 36 loaded at
   start (205 with the meaning-search warm-up).
5. **Kazma's Python inside its own WSL engine** stays the long-term option:
   Windows' file check then judges WSL and the engine, both signed by
   Microsoft and Docker.

## The first real test (before anything says Kazma works under it)

A fresh Windows 11 VM with Smart App Control on: the hand-over
`windows-trust-done.md` names what to install, what to try and what to
record.
