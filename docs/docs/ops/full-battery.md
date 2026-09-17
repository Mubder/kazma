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
12. MCP — mcp_test_server on the first configured server, else mcp_list_resources. SKIP if no servers.
13. Documents — document_status or list. SKIP if documents.enabled is false.
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

PowerShell, from any directory. Loopback autologin may apply in a **browser**; this shell needs a secret header if `/api/*` is gated.

```powershell
$base = 'http://127.0.0.1:9090'
$h = @{}
if ($env:KAZMA_SECRET) { $h['X-Kazma-Secret'] = $env:KAZMA_SECRET }

function Probe($name, $path, $expect = 200) {
  try {
    $r = Invoke-WebRequest -Uri ($base + $path) -Headers $h -UseBasicParsing -TimeoutSec 30
    $ok = $r.StatusCode -eq $expect
    $snip = if ($r.Content.Length -gt 180) { $r.Content.Substring(0, 180) } else { $r.Content }
    [pscustomobject]@{ Name = $name; Code = [int]$r.StatusCode; Ok = $ok; Evidence = $snip }
  } catch {
    $code = 0
    if ($_.Exception.Response) { $code = [int]$_.Exception.Response.StatusCode }
    [pscustomobject]@{ Name = $name; Code = $code; Ok = $false; Evidence = $_.Exception.Message }
  }
}

$rows = @(
  (Probe 'live' '/health/live'),
  (Probe 'ready' '/health/ready'),
  (Probe 'deep' '/health/deep'),
  (Probe 'auth' '/api/auth/status'),
  (Probe 'app-status' '/api/status'),
  (Probe 'research-ready' '/api/research/ready'),
  (Probe 'backup-list' '/api/backup/list'),
  (Probe 'backup-status' '/api/backup/status'),
  (Probe 'pending-hitl' '/api/pending-approvals'),
  (Probe 'health-details' '/health/details')
)
$rows | Format-Table -AutoSize
$failed = @($rows | Where-Object { -not $_.Ok })
if ($failed.Count) { Write-Host "PART B FAIL:" ($failed.Name -join ', ') } else { Write-Host 'PART B: all HTTP probes returned expected codes' }
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
| `/api/research/ready` | JSON with backends | 500 |
| `/api/backup/list` | JSON `backups` (empty list is PASS) | 401 without a secret when you expected open; 500 |
| `/api/pending-approvals` | JSON list (empty is PASS) | 500 |
| `/health/details` | 200 **with** auth; 401 without a secret on a locked install is PASS | 200 **without** auth (leak) |
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

If a card never appears, Result=FAIL Evidence="no interrupt / no pending approval". Do not invent a success. Do not send email, post to X, or dispatch swarm.
```

**How to score C**

| What you saw | Meaning |
|--------------|---------|
| Card on Web (or Telegram) → Approve → file exists → second card → file gone | Graph HITL path works on **that mouth** |
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

## Still not in this battery

These need a dedicated drill, not this pack:

- `kazma migrate export/verify/import` and vault-key pairing
- restic/S3 write probe and restore
- Postgres `pg_dump` / `verify_required_pg_tables`
- Multi-replica sticky sessions
- OIDC login
- Document Arabic/OCR visual
- Browser/Playwright computer-use
- Swarm A→B→A handoff cycles
- Time-travel `/replay` `/fork`

See [Production checklist](./production-checklist), [Smoke matrix](./smoke-matrix), [Disaster recovery](./disaster-recovery), [Migration](./migration).
