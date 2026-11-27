# Every Windows-native file Kazma runs or loads, and its route under Smart App Control

Measured 26 Nov 2026 on the owner's PC with `Get-AuthenticodeSignature`
(per-file data: 1,815 rows, kept with the session's hand-over). Smart App
Control was **off** on this PC while measuring (`VerifiedAndReputablePolicyState`
= 0), so "unsigned" here means "Windows judges it by reputation", never "refused".
On 26 Nov, with it on, Windows refused only psycopg's cp314 client library and
the engine's static `docker.exe`; everything else loaded by reputation.

A route keeps the file's feature working with Smart App Control on, **without**
Kazma's own code-signing certificate:

- **signed**: already signed by its publisher (Microsoft, PSF, EnterpriseDB, Docker, ...);
- **engine**: runs inside Kazma's engine (Linux in WSL, outside Windows' file check);
- **alternative**: a signed alternative or fallback is used;
- **dropped**: not in the default install;
- **not started**: Kazma never starts it;
- **feature waits**: no route yet; the named feature waits, calmly (`windows_trust`), until a signed version;
- **Kazma's signature**: only Kazma's own certificate (or Kazma's signed release bundle) closes it.

## The table

| File(s) | Signed? | Route | Status |
|---|---|---|---|
| Interpreter, python.org (`python.exe`, `pythonw.exe` venv launchers, `python3.dll`, `python3XX.dll`, stdlib `.pyd`/`.dll`, `vcruntime140*.dll`) | Valid (PSF; vcruntime: Microsoft catalog) | signed | closed: setup makes a new venv from the newest python.org Python there (3.14 first; installs the pinned 3.14 with winget, with the owner's yes, when none) |
| Interpreter, Tcl/Tk (`tcl90.dll`, `tcl9tk90.dll`, `libtommath.dll`, `zlib1.dll`) | NotSigned | not started (only `_tkinter` loads them; Kazma never imports tkinter) | closed |
| Interpreter, uv-managed CPython (python-build-standalone: 41 of 43 files) | NotSigned | dropped (setup no longer lets uv download one on Windows) | closed for new installs; the dev venv still uses one |
| Kazma's console-script launchers (`kazma.exe`, `kazma-web.exe`, `kazma-tui.exe`) and every other venv `Scripts\*.exe` launcher (98 unsigned) | NotSigned (made on the PC) | not started: Kazma runs `python -m ...` everywhere (gate) | closed |
| venv packages, the developer tools (`dev` extra: mypy 238, pywin32 59 via locust, gevent 16, ... = 340 files in Express) | NotSigned | dropped from Express (tests part takes `test`) | closed for new installs |
| psycopg's compiled wrapper and its libpq/libssl/libcrypto (5 files) | NotSigned | alternative: PostgreSQL's signed `libpq.dll` (EnterpriseDB), first while Smart App Control is on, as a fallback otherwise | closed |
| venv packages Kazma loads at start (34 unsigned in 21 distributions: pydantic_core, cryptography, orjson, numpy, pillow, pymupdf, lxml, ...) | NotSigned | **Kazma's signature** (the signed release bundle) or Kazma's Python inside its engine | open: loaded by reputation today |
| venv packages the meaning-search warm-up loads (scipy 81, pandas 43, scikit-learn 22, torch 8, ... = 181) | NotSigned | **Kazma's signature** (bundle); with none, the guard's reload check keeps a running Kazma and the feature waits | open |
| venv packages loaded only by a feature (40 files: aiohttp, chromadb, docling, opencv, grpcio, tree-sitter, ...) | NotSigned | **Kazma's signature** (bundle) | open |
| `kazma-engine` distro and its `docker`, `docker compose` (v5.8.0), `buildx` | Linux (WSL) | engine: every docker call reaches Kazma's engine through Microsoft's `wsl.exe` | closed |
| Kazma's engine `docker.exe` (static zip), `docker-compose.exe` | NotSigned | not needed for Kazma's engine (wsl.exe route); a fallback only where Windows allows it | closed |
| Docker Desktop's `docker.exe` and its 47 exes | Valid (Docker Inc) | signed | closed |
| `wsl.exe` and `C:\Program Files\WSL\*` (326 files) | Valid (Microsoft) | signed | closed |
| `kazma-engine-shutdown.exe` (built on the PC by `csc.exe`) | NotSigned | **Kazma's signature**: a prebuilt exe signed in the release (`WINDOWS_TRUST_PLAN.md`). No signed host measured as good: a service must answer Windows' service manager (PowerShell is no service; pywin32's host is unsigned too), and Task Scheduler starts nothing once a shutdown has begun. Until then, refused, WSL's VM ends with Windows and `windows_trust` names the feature that waits ("A clean stop of the container engine when Windows shuts down") | open: Kazma's signature |
| PostgreSQL 18 (`pg_dump`, `pg_restore`, `psql`, `pg_basebackup`, `postgres`, all 39 exes and 28 DLLs) | Valid (EnterpriseDB) | signed | closed |
| LibreOffice (`soffice.exe`, `soffice.bin`, `soffice.com`) | Valid (The Document Foundation) | signed | closed |
| Node.js (`node.exe`, system and Playwright's driver) | Valid (OpenJS) | signed | closed |
| `npx.cmd`, `npm.cmd` (MCP servers started with npx) | cannot be signed (`.cmd`) | not measurable here; the VM test checks it | open (measure) |
| Playwright's Chromium (`chrome.exe`, headless shell, ffmpeg) | NotSigned | none yet: refused, browsing pages with a browser fails until a route lands (planned: Playwright's `msedge` channel, Microsoft Edge, signed and on every Windows); `windows_trust` does not probe it yet | open |
| ClamAV (`clamscan.exe`, `freshclam.exe`, `clamd.exe`, its DLLs) | NotSigned | none yet: refused, the malware scan fails until a route lands (planned: ClamAV in a container on Kazma's engine); `windows_trust` does not probe it yet | open |
| Tesseract (`tesseract.exe` + 51 DLLs) | UnknownError (certificate expired 2023) / NotSigned | engine: the document worker image carries Linux Tesseract (Arabic, English) for the container profile; the host's copy (process profile) has no route yet | partly closed |
| restic | NotSigned | none yet: refused, the restic copies fail and are alerted as failed backups (§27) until a route lands (planned: restic's container on Kazma's engine) | open |
| ripgrep (`rg.exe`) | NotSigned | alternative: refused, the code index searches with its own Python walk (`code_index/ripgrep._rg` returns None on OSError) | closed |
| Git (`git.exe` Valid; `libiconv-2`, `libintl-8`, `libpcre2-8-0`, `zlib1`, `ssh.exe` NotSigned) | mixed | a release install updates without git (`kazma update` from signed source archives); git tools and a checkout's update need Git for Windows' DLLs (no route yet) | open |
| uv (WinGet 0.13.9: `uv.exe`, `uvx.exe`, `uvw.exe`) | Valid (OpenAI OpCo, LLC; same signer as `ruff.exe`) | signed | closed |
| uv 0.13.6 zip setup.ps1 installs into `~\.local\bin` | not measured (not on this PC) | measure on the VM | open (measure) |
| cloudflared | Valid (Cloudflare) | signed (Kazma only checks it runs) | closed |
| Windows' own tools (`sc`, `schtasks`, `icacls`, `takeown`, `whoami`, `tasklist`, `taskkill`, `netstat`, `netsh`, `msiexec`, `dism`, `reg`, `powershell`, `cmd`, `csc`, `winget`) | Valid (Microsoft) | signed | closed |
| `setup.cmd` | cannot be signed (`.cmd`) | the user starts it; the VM test checks it | open (measure) |
| `setup.ps1`, the engine's `keep-engine.ps1`, `install_windows_task.ps1`, `query.ps1` | NotSigned | **Kazma's signature** (scripts can be Authenticode-signed); how Smart App Control treats them was not observable here (it was off): the VM test checks it | open (measure) |
| inline `powershell -Command` scripts (no file) | n/a | nothing to sign; the VM test checks them under App Control | open (measure) |

## Measured to decide

- **The interpreter.** A venv made from python.org's Python is fully signed on
  the interpreter side except Tcl/Tk, which Kazma never loads (53 files, 7
  unsigned, 0 outside Tcl/Tk; 3.14.10 and 3.15.2 alike). A uv-managed Python is
  not (3.12.16: 42 of 49 unsigned). Live already runs on python.org 3.14.
- **The Python version.** The package side does not change with the version:
  cp312 and cp314 venvs carry 661 files from version-specific wheels, 646
  unsigned, in both. Reputation differed once: on 26 Nov cp314's psycopg was
  refused while cp312's loaded (one event). Recommendation for Windows
  installs: **3.14**, then 3.13 (the delta review, 27 Nov): python.org ships
  no patched Windows build of 3.12 after 3.12.10 or of 3.11 after 3.11.9
  (later fixes are source-only), so they miss the June 2025 tarfile fixes
  (CVE-2025-4517 and its siblings) that Kazma's `filter="data"` extraction
  relies on; they are taken only when nothing newer is there. Setup installs
  the pinned `Python.Python.3.14` 3.14.10 with winget, with the owner's yes.
  Live runs on 3.14.
- **psycopg's wrapper** (a throwaway PostgreSQL 18 container on the engine,
  two rounds, the compiled wrapper first): `SELECT 1` 0.47-0.50 ms against
  0.53-0.54 ms pure Python on PostgreSQL's signed libpq; a parameterised read
  0.81-0.88 against 0.87-0.89 ms; 5,000 rows fetched 4.4-4.6 against 31.7-32.5
  ms; 5,000 rows inserted 149-152 against 212-276 ms. Decision: the signed
  library first while Smart App Control checks files (no refusal to wait
  for), the compiled wrapper otherwise (a company policy that refuses it is
  met by the fallback at start, and the guard's reload check).
- **Docker through wsl.exe** (medians, the Windows CLI first): `docker
  version` 145/145 ms, `docker exec` 281/152 ms, `docker run --rm` 699/425 ms,
  50 MB out of `docker exec` 551/316 ms, 20 MB into `docker exec -i`
  334/198 ms, bytes intact both ways. No caching is needed on the hot paths
  (code execution, the guard's probes).

## Is the certificate now the only thing left?

**Not yet.** Kazma's own files (the shutdown helper, its `.ps1` scripts) and
the third-party natives in its venv (loaded by reputation today) wait for the
certificate and the signed release bundle; four tools with no signed build
(Playwright's Chromium, ClamAV, restic, Git's DLLs) still need their routes
(Edge, containers on Kazma's engine), and three script rows (`setup.cmd`,
`npx.cmd`, inline PowerShell) can only be measured on the VM with Smart App
Control on.
