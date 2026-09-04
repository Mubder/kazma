# GOAL: Guard / ops alerting — cause quality + flap control

**Status:** P0 (503 body) and P1 (flap collapse + recovery card) shipped 2026-09-04. P2 backup summary / `native_pg_backup` ops wiring remains deferred.  
**Created:** 2026-09-02  
**Trigger:** live Telegram `[guard] Kazma stopped: unhealthy (unreachable: Service Unavailable). Restarting in 15s (attempt 1).` after a Docker Desktop update took Postgres down.  
**Rule:** Do **not** start/restart the Kazma server. After Python lands: operator reload via `kazma_guard.py --reload`.

This is **not** “Kazma does not alert.” The page fired. The gap is **cause quality** (the 503 JSON already named `database`; the guard threw the body away) plus **flap control** (attempt N on every Docker bounce) plus a few **silent scheduled-job failures**. It is explicitly **not** “page every background success.”

---

## Mission

Make operator Telegram (and already-configured Discord/Slack via the in-app bus) name **which critical dependency or job failed, and why**, without turning the channel into a status feed that gets muted.

Success for this sprint:

1. The Docker Desktop / Postgres 503 pages as `database: <error>` (or timeout), not `unreachable: Service Unavailable`.
2. The same outage does not produce a thread of `attempt 1, 2, 3…` — one page per cooldown window, every attempt still in `guard.log`.
3. Recovery is one line when probes go healthy after a kill.
4. Backup **failures** keep paging with a reason; a run that fully succeeds may send **one** summary, never per-file.
5. `native_pg_backup` failure uses `ops_alerts` the same way universal/restic already do.
6. Scheduler heartbeats, degraded-but-serving MCP, and healthy starts stay silent (lifecycle already announces start).

---

## Live incident (failure chain)

Verified against source, 2026-09-02.

| # | Link | Evidence |
|---|------|----------|
| 1 | Docker Desktop update stopped the Postgres container | Operator action; Kazma Postgres is typically `127.0.0.1:5433` |
| 2 | `/health/ready` pinged the DB (3s cap) and marked `checks.database` failed | `kazma_ui/health.py` `readiness()` + `check_database()` |
| 3 | config_store + database are **critical** → HTTP **503** + JSON `{status: not_ready, checks: …}` | `health.py` `critical_failed` → `http_status = 503` |
| 4 | Guard `probe()` uses `urllib.request.urlopen` | `scripts/service/kazma_guard.py` `probe()` |
| 5 | 503 raises `HTTPError` (subclass of `URLError`) | stdlib urllib |
| 6 | Catch-all formats `unreachable: {exc.reason}` | `probe()` `except urllib.error.URLError` — `reason` is the phrase **Service Unavailable** |
| 7 | The `status == "not_ready"` branch that would list failing checks **never runs** | That parse only happens on HTTP 200 bodies |
| 8 | `_supervise` kills after consecutive failures and Telegram is `unhealthy ({detail})` | `kazma_guard.py` `_supervise` + `notify.send` |
| 9 | Restarting Python does not restart Docker | Attempt N continues until Postgres answers again |

The app was **alive**. `/health/live` would have been 200. Readiness failed because **shared-state Postgres** was gone.

The guard cannot honestly say “you updated Docker Desktop.” It **can** say `database: connection refused` / `ping timed out (3s)`.

---

## What already exists (do not invent a fourth notifier)

Three delivery paths, on purpose. Keep the split.

| Path | Who | When | Channels |
|------|-----|------|----------|
| Guard `Notifier` | Supervisor process, stdlib urllib | Child dead, unhealthy, crash-loop, pause | **Telegram only** — must work when the app cannot |
| `kazma_core.observability.ops_alerts.alert()` | Inside Kazma | Backup/offsite/restic/MCP/persist/turn-fail | Fan-out bus (Telegram/Discord/Slack) + Telegram-direct fallback |
| `lifecycle_notifier` | App boot/shutdown | starting / started / restarted / shutting_down | Same bus |

Plus: daily digest (`daily_digest.py`), weekly firing ledger (`firing_ledger.py`).

Load-bearing constraints already in `ops_alerts.py`:

- Default cooldown **900s** per key (`KAZMA_OPS_ALERT_COOLDOWN_S`).
- Never raises, never blocks; callable from sync except handlers.
- Kill-switch `KAZMA_OPS_ALERTS=0` (does not mute lifecycle).
- Detail cap 600 chars. Telegram 4096 hard limit.
- Bus first; if `NullBusAdapter` (worker/CLI/guard-adjacent), `_telegram_direct`.
- Mute theorem (docstring, earned): MCP failed 60 times in eight days; sixty messages = the channel is muted = same as zero.

**Already paging on failure (with a reason string):** universal backup DB/offsite/degraded; restic passphrase missing / read-only remote / `restic check` failed; restore drill; MCP down/reconnect; persist fail; turn with no answer; connector health; weekly ledger report.

**Deliberately silent today:** successful backups (INFO + Settings + digest); scheduler ticks (6h macro_sleep, 15-min commitment GC, cron poll); degraded-but-serving MCP (readiness stays HTTP 200 so the guard does not kill a working agent); guard healthy start (lifecycle already says started).

**Real gap besides the 503 body:** `native_pg_backup` does not call `ops_alerts` on dump failure.

---

## Design decisions (industrial default at each fork)

| Fork | Chosen | Rejected, and why |
|------|--------|-------------------|
| More alerts vs better alerts | **Cause quality + flap control** | Paging every major-task success trains mute; this incident already paged |
| Guard FanOut vs Telegram-direct | **Keep Telegram-direct on the supervisor** | When Postgres is down the in-app bus can be sick; the guard must not import the child to say the child is down |
| In-app jobs | **Keep `ops_alerts` + existing bus** | A fourth notifier duplicates cooldown, kill-switch, and delivery bugs |
| Name Docker vs name the check | **Name the failing check + error** | Guard cannot prove Docker Desktop; it can prove `database` |
| Per-attempt Telegram vs collapse | **Collapse same `detail` on a cooldown; log every attempt** | Docker updates already produce attempt N; that is how the channel dies |
| Success: per hop vs one summary vs digest | **One backup summary optional; digest/weekly remains the heartbeat** | Per-DB/per-restic-file is a dozen messages on a quiet night |
| Recovery page | **One line after a kill when probes go healthy** | Without it, “attempt 1” has no close; with a storm of recoveries it must share the cooldown |
| HITL / swarm chat | **Ops pages stay ops** | Mixing “database down” with Approve buttons is the wrong tap |
| Alerting in backup/exception paths | **Fail-open forever** | An alert that fails the backup is worse than silence |

---

## Work packages (when this sprint opens)

Do **not** follow a “alert everything” dump. Sequence:

### P0 — Guard `probe()` reads 503 bodies

`scripts/service/kazma_guard.py` `probe()`:

- On `urllib.error.HTTPError`, read a **small** body (existing 4KB cap), parse JSON if present.
- If `status == "not_ready"` (or any 503 JSON with `checks`), surface failing keys **and** each check’s `error` when present: `unhealthy (database: ping timed out (3s))`.
- If body is missing/HTML/unparsed, keep today’s fallback `HTTP {code}` / `unreachable: {reason}` — never invent a cause.
- Reuse the existing 200 `not_ready failing=` path so 200-vs-503 does not become two formatters.

Tests: fake 503 JSON with `checks.database`; fake 503 HTML; connection refused with no body; 200 degraded MCP still healthy (do **not** kill on non-critical).

### P1 — Collapse restart Telegram + recovery line

`_supervise` / restart `notify.send`:

- Same `detail` inside a cooldown (default 900s, env override ok) → log `guard.restarting`, **do not** send another Telegram.
- Crash-loop page still fires (that is a different condition).
- When probes recover after a kill (or child becomes ready after respawn that followed unhealthy), **one** `Kazma healthy again ({check} recovered after Ns)` — not on every probe flap that never crossed `FAILURES_TO_KILL`.

Tests: N consecutive same-detail restarts → 1 Telegram; different detail → new page; recovery after kill → 1 line; MCP 200 degraded → 0 kill.

### P2 — Backup: keep fail pages; one success summary; PG dump onto `ops_alerts`

- Universal `_alert_on_backup_gaps` stays. Do not add per-file alerts.
- Optional **one** success `alert(..., severity="info")` per completed run: ok, DB count, size, duration. Cooldown so a manual + auto pair does not double-ping.
- `native_pg_backup` / `perform_pg_backup` failure → `ops_alerts` with the dump error (tool missing, magic fail, rename fail). Success stays in the same one-summary or digest.
- restic already pages on the dangerous cases; do not add “snapshot ok” per repo.

Tests: extend `tests/test_backup_alerting.py` / `tests/test_backup_silent_failures.py`; PG dump fail raises; success is at most one info alert.

### P3 — Policy lock (docs + negative controls)

- Document in this file + a short ops note: **fail always (named), recover once, success digest/summary, never scheduler heartbeats.**
- Negative control: a test that would fail if `probe()` goes back to `unreachable: {reason}` on a JSON 503.
- Do **not** mix ops text into HITL cards.
- Do **not** wire `get_scraping_client` or LLM HTTP into alerting.
- Guard stays stdlib-first; vault credential lookup stays lazy (existing `Notifier` invariant).

---

## Out of scope (this sprint)

| Parked | Why |
|--------|-----|
| Page every scheduler tick / every restic file / every MCP reconnect attempt | Mute theorem |
| Guard sending Discord/Slack FanOut | Supervisor must not depend on the child or extra SDKs |
| “You updated Docker Desktop” as the cause string | Unprovable from the probe |
| A fourth notifier / new Telegram bot | Three paths already; add a key, not a stack |
| Changing `/health/ready` 503 policy (critical = config_store + database) | Correct; the bug is the unread body |
| Killing the child on degraded MCP | Would restart a working agent every 90s |
| Web UI alert inbox / PagerDuty / email | Different product; not this incident |
| HITL / Turn Delivery / Gate Registry | Untouched |

---

## Cost and risks (why this stayed deferred)

**Money:** ~zero. Bot API is not an LLM call.

**Attention:** the real cost. Weekly ledger + uncollapsed restart pages already exist. Success pings on top of Docker flaps is how the channel gets muted.

**Risks if implemented carelessly:**

1. Flap storms (Docker/Postgres bounce × probe interval × backoff).
2. Mute = silence (the 60-MCP-message lesson).
3. Recursive alerting (guard using the child to say the child is down).
4. Dual-path drift (guard vs `ops_alerts` vs lifecycle vs HITL).
5. Alerting that fails the path it reports (backup `alert()` must stay fail-open).
6. Green fatigue (success looks like the nightly “ok,” then critical is ignored).
7. False cause (truncated/HTML 503 parsed as `database`). Cap the read; fall back to the reason phrase.

---

## Reload / verify (when built)

Operator only:

```powershell
cd 'G:\GitHubRepos\kazma'
& '.venv\Scripts\python.exe' scripts\service\kazma_guard.py --reload
```

Proof: stop Postgres (or pause the container) without killing Python → Telegram names `database` + error; start Postgres → one recovery line; `pytest tests/test_ops_alerts.py` plus the new probe/backup tests. Do **not** kill python/uvicorn by hand.

---

## Pickup checklist

When opening this sprint:

1. Re-read this file + `ops_alerts.py` header + `kazma_guard.py` `probe()` / `Notifier` / `_supervise`.
2. Confirm `/health/ready` still 503s only on `config_store` / `database`.
3. Do not “fix” alerting by widening readiness kills.
4. Implement P0 first — that is the live incident. P1 is the flap. P2 is the backup honesty leftover. P3 is the lock.
5. Say proceed was given; then commit + push `main` as usual.
}
