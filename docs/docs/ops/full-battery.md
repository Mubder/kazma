---
id: full-battery
title: Full-system battery
sidebar_label: Full-system battery
description: Three-part diagnostic — chat tools, HTTP/ops, one HITL write — so a failure names the broken part
---

# Full-system battery

Three independent runs. **A green Part A is not a green Kazma.** Score at the bottom.

| Part | What it proves | What it cannot prove |
|------|----------------|----------------------|
| **A** Chat probe | The supervisor can still call read-only tools | Telegram, SSE, HITL buttons, backups |
| **B** HTTP / ops | Process, ConfigStore, memory recall, workspace, research stack, backup API | That a human can Approve a card |
| **C** HITL write | Graph interrupt → Approve → file actually lands | Swarm bus, pipeline checkpoints, email send, X post |

**Verdict**

- Any **FAIL** in A or B → that subsystem is broken. Stop guessing.
- **C** not run → you have **not** proven HITL or disk writes.
- All three green → chat tools + HTTP + one approved write work. Still not “Telegram works” unless C was pasted **on Telegram**.

Base URL below is `http://127.0.0.1:9090`. Change it if yours is not. Do **not** start or restart the server as part of this battery.

---

## Part A — chat probe (read-only)

**Two messages**, in order, on Web chat **or** Telegram.

1. Send only: `/long mission`  
   Wait for the `MISSION ON` ack. That slash is **not** sent to the model — if you paste the probe under it in the same bubble, the probe never runs.
2. Then paste the block below (no slash on the first line).

After a server reload that includes the 2026-09-17 `/long mission`+body fall-through, a single paste starting with `/long mission` plus the probe also works. Until then, use two messages.

The probe text ("Read-only tools only" / "Do not write") **arms `audit_only`**. That is intended. Diagnostic reads (`config_read`, `git_status`, `email_list`, …) are on that allowlist; writes, `mcp_test_server`, vault/DB internals are not. A FAIL of `tool 'X' is not on the audit_only allowlist` on a read-tier probe after this change is a real gate bug, not "the subsystem is down".

```
You are running Part A of the Kazma full-system battery. Read-only tools only. Do not write files, send email, post to X, mutate memory, dispatch a swarm task, or run shell_exec/python_exec/file_write. If a tool is missing, gated, or returns Error:/⚠️ — that row is FAIL, not a reason to invent a pass.

Continue through EVERY section even if earlier ones fail. Do not synthesize a reassuring summary over a failed probe.

For each row:
| # | Subsystem | Probe | Result | Evidence |
Result is PASS, FAIL, or SKIP (SKIP only if the feature is honestly not configured, with the config key you read). Evidence is one short quote from the tool or the error.

1. Identity — context_info (workspace root, active model, provider).
2. Clock — current_datetime.
3. ConfigStore — config_read agent.max_iterations and safety.hitl.enabled.
4. Memory — memory_search q="kazma" (or memory_list_beliefs). Empty beliefs = PASS "empty", not FAIL.
5. Workspace — file_list "."
6. Git — git_status. SKIP only if not a repo.
7. Web search — web_search q="Kazma AI agent".
8. Fetch — read_url of ONE https URL from step 7. FAIL if SSRF/timeout/empty. Confirm body is fenced (kazma:data).
9. Research stack — research_readiness.
10. Swarm — do NOT dispatch. Confirm check_swarm_task (or equivalent) is registered and answers. FAIL on import/"No swarm".
11. HITL config — config_read safety.require_approval_for. Do not call a danger tool.
12. MCP — mcp_list_resources (not mcp_test_server; that is write-tier). SKIP if no servers.
13. Documents — document_status with **no** document_id/job_id (platform overview: enabled, workers, catalog). Empty catalog = PASS. SKIP if documents.enabled is false.
14. Email — email_list folder=INBOX limit=1. Do not send.
15. Calendar — list_events. FAIL if the user has Google connected and the tool returns silent sandbox.
16. Voice — config_read voice.stt_provider and voice.tts_provider. SKIP if voice disabled.
17. X — x_status if the tool exists. SKIP if not configured. Do not post.
18. Cron — list_scheduled if the tool exists. Empty list = PASS.
19. Host — get_system_stats.
20. Vision — analyze_image only if an image is in this thread; else SKIP.
21. Model routing — active model from (1) plus whether a second provider is configured (names only, never keys). FAIL if provider/model mismatch is obvious.

## Score
- PASS n
- FAIL n ← names
- SKIP n ← names

## What is really broken
One sentence per FAIL: subsystem, tool, exact error. Zero FAIL → "No functional break in Part A" and list SKIPs as unconfigured, not healthy.

Do not claim Telegram/Discord/Slack, backups, Postgres dumps, restic, or HITL buttons work. Do not call this production-ready.
```

---

## Part B — HTTP / ops (you run this)

PowerShell. Loopback autologin may apply in a **browser**; this shell is not a browser. Run it from the **live install** directory so `.env` can supply `KAZMA_SECRET` (the script never prints the value).

A **401** on a gated `/api/*` when no secret was sent is the gate working, **not** a dead subsystem. `Invoke-WebRequest` follows redirects, so `/health/details` without a secret can show **200 HTML** — that is the **login page**, not a leak of model names. JSON with `active_model` and no credential **is** a leak.

```powershell
$base = 'http://127.0.0.1:9090'
Set-Location 'C:\Users\balfa\kazma'   # live install; change if yours differs

if (-not $env:KAZMA_SECRET) {
  foreach ($p in @((Join-Path (Get-Location) '.env'), 'G:\GitHubRepos\kazma\.env')) {
    if (-not (Test-Path $p)) { continue }
    $line = Select-String -Path $p -Pattern '^\s*KAZMA_SECRET\s*=' | Select-Object -First 1
    if (-not $line) { continue }
    $val = ($line.Line -replace '^\s*KAZMA_SECRET\s*=\s*', '').Trim().Trim('"').Trim("'")
    if ($val) { $env:KAZMA_SECRET = $val; break }
  }
}
$haveSecret = [bool]$env:KAZMA_SECRET
$h = @{ Accept = 'application/json' }
if ($haveSecret) { $h['X-Kazma-Secret'] = $env:KAZMA_SECRET }
Write-Host ('Auth header: ' + $(if ($haveSecret) { 'X-Kazma-Secret set (from env or .env)' } else { 'NONE — gated routes should 401 (gate PASS)' }))

function Probe($name, $path, [switch]$Gated) {
  $code = 0; $body = ''
  try {
    $r = Invoke-WebRequest -Uri ($base + $path) -Headers $h -UseBasicParsing -TimeoutSec 30 -MaximumRedirection 0
    $code = [int]$r.StatusCode; $body = [string]$r.Content
  } catch {
    $body = [string]$_.Exception.Message
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
  }
  $snip = if ($body.Length -gt 180) { $body.Substring(0, 180) } else { $body }
  $html = $body -match '<!DOCTYPE html>|<html'
  $ok = $false; $note = ''
  if ($Gated -and -not $haveSecret) {
    if ($code -eq 401) { $ok = $true; $note = 'gate-401' }
    elseif ($code -in 302, 303) { $ok = $true; $note = 'gate-redirect' }
    elseif ($code -eq 200 -and $html) { $ok = $true; $note = 'login-html (redirect followed)' }
    elseif ($code -eq 200 -and $body -match 'active_model') { $ok = $false; $note = 'LEAK details JSON unauthenticated' }
    else { $note = 'unexpected unauth response' }
  } else {
    $ok = ($code -eq 200) -and -not $html
    if ($html) { $note = 'got HTML, wanted JSON' }
  }
  [pscustomobject]@{ Name = $name; Code = $code; Ok = $ok; Note = $note; Evidence = $snip }
}

$rows = @(
  (Probe 'live' '/health/live'),
  (Probe 'ready' '/health/ready'),
  (Probe 'deep' '/health/deep'),
  (Probe 'auth' '/api/auth/status'),
  (Probe 'app-status' '/api/status'),
  (Probe 'research-ready' '/api/research/ready' -Gated),
  (Probe 'backup-list' '/api/backup/list' -Gated),
  (Probe 'backup-status' '/api/backup/status' -Gated),
  (Probe 'pending-hitl' '/api/pending-approvals' -Gated),
  (Probe 'health-details' '/health/details' -Gated)
)
$rows | Format-Table -AutoSize
$failed = @($rows | Where-Object { -not $_.Ok })
if ($failed.Count) { Write-Host "PART B FAIL:" ($failed.Name -join ', ') } else { Write-Host 'PART B: all HTTP probes returned expected codes' }
if (-not $haveSecret) { Write-Host 'Gated APIs were only checked for 401. Re-run with KAZMA_SECRET (or .env in this directory) to prove the JSON bodies.' }
```

Then, still in PowerShell, from the **repo** (or the deploy clone you actually run):

```powershell
cd 'G:\GitHubRepos\kazma'   # or C:\Users\balfa\kazma if that is the live process
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --status
```

**How to score B**

| Probe | PASS | FAIL |
|-------|------|------|
| `/health/live` | `"status":"alive"` and a `build.commit` | No process / wrong port |
| `/health/ready` | 200 | 503 — read `checks` for which dependency |
| `/health/deep` | 200, `"ok": true` | 503 — the `failed` array **is** the broken part (config / recall / workspace / research / brain / database) |
| `/api/auth/status` | 200; if you are behind nginx, `undeclared_proxy` must not be latched | 401 unexpected; or proxy latch while you thought you were direct |
| `/api/status` | `"status":"ok"` (or `"degraded"` with named `init_errors`) | 500 / empty |
| `/api/research/ready` | **With secret:** JSON backends. **No secret:** 401 (gate PASS — body not verified) | 500; 200 without a secret (should be gated) |
| `/api/backup/list` | **With secret:** JSON `backups` (empty list is PASS). **No secret:** 401 | 500; 200 without a secret |
| `/api/backup/status` | Same as backup-list | 500; 200 without a secret |
| `/api/pending-approvals` | **With secret:** JSON list (empty is PASS). **No secret:** 401 | 500; 200 without a secret |
| `/health/details` | **With secret:** JSON (`checks`, not HTML). **No secret:** 401 JSON, or 303 `/login`, or 200 login HTML if redirects were followed | **200 JSON** with `active_model` and no credential (leak). 500 |
| `kazma_guard.py --status` | Guard sees the same `build.commit` as `/health/live` | Guard down, or commit mismatch (process did not pick up the pull) |

**Human clicks (one each, 30 seconds)**

1. Web: open `/`, send `ping`. Tokens stream; the turn ends with a real reply (not a stuck CoT).
2. Settings → Providers: page renders; no unstyled raw inputs (Voice/Email too).
3. If you use Telegram daily: send `ping` to the bot. Reply comes back. **If you skip this, Telegram is untested.**

Do **not** `POST /api/backup/now` as part of the default battery (slow, writes). List/status is enough to prove the API is alive.

---

## Part C — HITL write (chat, danger tools on purpose)

Run **after** A and B on the same mouth you actually use (Web SSE if that is daily; **Telegram if that is daily**). You will get an Approve card. Approve it. If no card appears, C has already failed.

If mission is already on from Part A, paste the block below as-is. If not: send `/long on` **alone**, wait for the ack, **then** paste the block (a leading `/long` in the same bubble is not sent to the model).

```
You are running Part C of the Kazma full-system battery: one real HITL write.

Create a unique probe file in the active workspace:
  path: kazma-battery-probe.txt
  contents: one line "kazma-battery <UTC ISO timestamp from current_datetime> <thread id if you have it>"

Use file_write (not python_exec, not shell_exec). Stop and wait for HITL. Do not retry the write, do not YOLO, do not use a different tool to bypass the card.

After the write is approved and succeeds:
1. file_read kazma-battery-probe.txt — FAIL if missing or wrong contents.
2. file_delete kazma-battery-probe.txt — that is a second HITL card; wait again.
3. file_list . — FAIL if the probe file is still there after the delete is approved.

Report:
| Step | Result | Evidence |
| HITL card appeared for file_write | PASS/FAIL | |
| Write landed | PASS/FAIL | |
| HITL card appeared for file_delete | PASS/FAIL | |
| File gone | PASS/FAIL | |

Do not invent a success. Do not send email, post to X, or dispatch swarm.

HITL-card rows: you cannot see the operator's screen. After graph interrupt() + resume, the tool result is ordinary success ("Wrote N lines") — that is the **post-approve payload**, not proof the gate was skipped. Mark those two rows **OPERATOR** (not PASS, not FAIL) and quote the tool result. The operator scores PASS/FAIL from whether ⚠️ Approval required / Approve actually appeared.

Same `hitl approve <uuid>` on write and delete is expected: that uuid is the **thread_id**, not a per-gate id.
```

**How to score C**

**You** score the card rows from the UI, not from the model's table.

After resume, the model only sees `Wrote N lines` / `Deleted: …`. That is normal. Telegram `hitl approve <uuid>` is the **thread_id** (same uuid for write and delete on one turn is expected).

| What you saw | Meaning |
|--------------|---------|
| Card on Web (or Telegram) → Approve → file exists → second card → file gone | Graph HITL path works on **that mouth** |
| ⚠️ Approval required on Telegram, then you `hitl approve` / tap Approve | Same PASS — the model may still have written FAIL; ignore that |
| Write succeeds with **no** card | HITL is off, YOLO is on, or the tool is not danger-tier. Treat as FAIL unless you intended YOLO |
| Card on Web but you sent C on Telegram | You proved the wrong mouth |
| `file_write` error “outside workspace” | Binding, not HITL |

---

## Optional extras (not required for a green battery)

Only if the corresponding product is how you use Kazma:

| Extra | How |
|-------|-----|
| Deep research | `/research` → one short brief, wait for `done` ([smoke matrix](./smoke-matrix)) |
| Swarm | Swarm panel → one tiny dispatch; worker appears; result or error is visible |
| Documents | Upload one PDF on `/documents`; job leaves `received` |
| Voice | One voice note on Telegram, or Web STT if you use it |
| Email send | `email_send` to **yourself** in sandbox first; HITL on send |
| Backup write | Settings → Backup → Run now; wait until status is not a silent “complete” with a failed offsite item |

---

## Part D — restic / Postgres dump / migrate+vault

Run from the **live install** directory. Never restore onto the live tree. Never print passphrases, vault keys, or DSNs.

`/api/backup/list` `"postgres": false` means the **universal generation's dump was stale or skipped at copy time**, not “this install is SQLite”. Check `KAZMA_DB_BACKEND` and `kazma-data/backups/pg/pg_shared_*.dump` (magic `PGDMP`).

```powershell
Set-Location 'C:\Users\balfa\kazma'   # live install
$env:KAZMA_DATA_DIR = (Resolve-Path '.\kazma-data')
$env:KAZMA_USER_HOME = (Resolve-Path '.\.kazma')
if (-not $env:KAZMA_RESTIC_PASSWORD -and (Test-Path '.\.kazma\restic.pass')) {
  $env:KAZMA_RESTIC_PASSWORD = (Get-Content '.\.kazma\restic.pass' -Raw).Trim()
}

# Restore drill: 0=PASS, 1=FAIL, 2=UNVERIFIED (required check did not run).
& '.venv\Scripts\python.exe' -m kazma_core.backup.restore_drill

# restic: local + offsite snapshot lists (newest DATA, not restic latest).
& '.venv\Scripts\python.exe' -m kazma_core.backup.restore --list

# Restore rehearsal into TEMP, then delete the target. Not the live install.
$dst = Join-Path $env:TEMP 'kazma-restore-rehearsal'
if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
New-Item -ItemType Directory -Path $dst | Out-Null
& '.venv\Scripts\python.exe' -m kazma_core.backup.restore --target $dst
# Expect: env present, config present, databases readable. Then delete $dst.

# Postgres dumps (SKIP if KAZMA_DB_BACKEND is sqlite / no KAZMA_DATABASE_URL).
& '.venv\Scripts\python.exe' scripts\pg_backup.py list

# migrate: export+verify+dry-import. Writes staging under kazma-data/.migrate-export-*
# Prefer a copy of the tree, not the live install, if you do not want staging there.
# kazma migrate export --out $env:TEMP\kazma-bundle.zip --no-assets
# kazma migrate verify $env:TEMP\kazma-bundle.zip
# kazma migrate import $env:TEMP\kazma-bundle.zip --workspace $env:TEMP\kazma-dry --dry-run
```

**How to score D**

| Probe | PASS | FAIL | SKIP |
|-------|------|------|------|
| Restore drill | All checks ok, including `vault:decrypt` with stored secrets | Any FAIL; vault key opens nothing | No universal backup yet |
| restic `--list` | ≥1 restore point; local and offsite counts if a remote is set | No passphrase, repo missing, snapshots fail | restic not on PATH and no repo |
| Restore rehearsal | `.env` + `kazma.yaml` + readable DBs in the TEMP target | restic restore error; no `.env` | — |
| PG dumps | `pg_shared_*.dump` with `PGDMP` magic; not stale past 8h | Backend is postgres and no dump / bad magic | SQLite backend |
| migrate verify | Bundle valid; vault pairing `match` (or `empty` on a new target) | Hash/tamper errors; `mismatch` without `--reset-vault-key` | — |

Offsite write probe is `remote_writable()` on `s3:` / `rclone:` (PUT+DELETE under `locks/`). A remote that lists but cannot write is FAIL.

---

## Still not in this battery

These need a dedicated drill, not this pack:

- Multi-replica sticky sessions
- OIDC login
- Document Arabic/OCR visual
- Browser/Playwright computer-use
- Swarm A→B→A handoff cycles
- Time-travel `/replay` `/fork`

See [Production checklist](./production-checklist), [Smoke matrix](./smoke-matrix), [Disaster recovery](./disaster-recovery), [Migration](./migration).
