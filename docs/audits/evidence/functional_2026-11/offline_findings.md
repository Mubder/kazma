# AUDIT-2611 offline findings (from the hunt notes, verified against dae47783)

These findings come from eight offline hunts: async, failpaths, features, growth, sidedoors, websec, and AGENTS.md claims in two halves. The hunts read the code and ran throwaway tests, which were deleted after the run. Nothing here was reproduced on the live install. Each root cause below was re-read in the dev repo at HEAD `dae47783`, and the line numbers are the lines at that commit. Where the code differs from the hunt note, the entry says so under "Status at HEAD". Findings that repeat an entry in `findings_draft.md` are listed under Duplicates and not written again. Area numbers are given only where the audit brief named them; otherwise the area is named in words. Severity is the hunt's unless the code read gave a reason to change it.

## Summary

| ID | Title | Severity | Area | Confidence |
|---|---|---|---|---|
| FA-101 | `kazma update` reports success over a conflicting stash pop | High | A5 UI/CLI | high |
| FA-102 | A rewind with a locked memory DB journals nothing; /undo and /edit still say memory was cleared | Medium | A6 memory | high |
| FA-103 | `get_vault()` caches a failed or in-progress start as "no vault" | Medium | A19 settings/backup | high |
| FA-104 | Mail and calendar sign-ins ignore a failed vault write | Medium | A13 email/calendar | medium |
| FA-105 | Discord send returns "sent" after the 429 retries run out | Medium | A4 chat apps | high |
| FA-106 | `resilient_chat` keeps old failover clients after a provider change | Medium | LLM providers | high |
| FA-107 | A failed model discovery is shown as a count of stale models | Medium | A19 settings/backup | high |
| FA-108 | "Reply persisted" is logged when the save failed | Low | A20 ops signals | high |
| FA-109 | Orphan recovery counts a task whose write was refused | Low | Swarm | high |
| FA-110 | A failed agent rebind after a key save is logged at DEBUG | Low | LLM providers | high |
| FA-111 | The "already read this turn" note is shared across chats | Low | Agent tools | high |
| FA-112 | `find_free_slots` ignores multi-day and all-day events and the operator's zone | High | A13 email/calendar | high |
| FA-113 | `dispatch_swarm` named "auto" routes to the first worker by name | Medium | Swarm | high |
| FA-114 | The log-level hint says it applies now; it does not | Medium | A19 settings/backup | high |
| FA-115 | The proxy hint omits the acquisition policy that decides whether it is used | Medium | A19 settings/backup | high |
| FA-116 | The backup hint says 24 h, vectors and a Postgres dump | Medium | A19 settings/backup | high |
| FA-117 | The outbound allowlist does not cover `send_message`; the dispatcher manifest misstates its target | Medium | Commitment layer | high |
| FA-118 | The model's product knowledge has stale lines and an incomplete always-ask list | Medium | Agent prompt | high |
| FA-119 | Scheduled time-zone hints say the opposite of the lookup order | Low | A19 settings/backup | high |
| FA-120 | The `python_exec` description promises limits the code does not apply | Low | Agent tools | high |
| FA-121 | The built-in default system prompt is unreachable when kazma.yaml sets one | Low | Agent prompt | medium |
| FA-122 | The Knowledge auto-inject hint says "every prompt" | Low | A19 settings/backup | high |
| FA-123 | The memory-mirror and vault hints describe behaviour the code does not have | Low | A19 settings/backup | medium |
| FA-124 | `arabic_translate` (read tier, no card) sends text to MyMemory and returns its answer unfenced | Medium | Native skills / egress | high |
| FA-125 | Settings Non-Stop "Per-Tool Timeout" is a dead control | Medium | A19 settings/backup | high |
| FA-126 | `config_save`'s protected list misses keys that redirect secrets or memory | Medium | Security boundary | high |
| FA-127 | `gateway.rate_limits` is read once at boot | Low | A4 chat apps | high |
| FA-128 | Chat apps preview links in model-written replies | Medium | A4 chat apps | high |
| FA-129 | `GET /api/settings/proxy` returns the decrypted proxy password | Medium | A19 settings/backup | high |
| FA-130 | The GitHub OAuth result page puts its message into HTML unescaped | Low | Security boundary | high |
| FA-131 | `/health/ready` returns raw exception text without sign-in | Low | A20 ops signals | high |
| FA-132 | The Telegram webhook logs a WARNING per unauthenticated POST | Low | A4 chat apps | high |
| FA-133 | OIDC `state` is one global key compared with `!=` | Low | Security boundary | high |
| FA-134 | Upload size limits apply after the body is spooled to disk | Low | Security boundary | high |
| FA-135 | Every log line is written and flushed on the event loop | Medium | Logging / loop | high |
| FA-136 | A watching WebSocket that stops reading freezes its thread's stream | Medium | Web chat delivery | high |
| FA-137 | Three cancel-then-unbounded-await sites the §26E gate misses | Medium | Loop / shutdown | medium |
| FA-138 | After a fatal loop error the server does not exit | Medium | Process lifecycle | high |
| FA-139 | Small synchronous file I/O inside async tools | Low | Agent tools | high |
| FA-140 | Telegram chunks are sized in code points, not UTF-16 units | Medium | A4 chat apps | high |
| FA-141 | Four append-only stores have no retention | Low | Storage growth | high |
| FA-142 | `settings.db` free pages and a large `knowledge.db` WAL are never reclaimed | Low | Storage growth | medium |
| FA-143 | `guard.log` never rotates | Low | Guard | high |
| FA-144 | The universal backup's manifest is written in place | Low | A19 settings/backup | medium |
| FA-145 | Two process-lifetime maps are never pruned | Low | A20 ops signals | high |
| FA-146 | Uploaded attachments are stored per upload with no dedupe or retention | Low | Storage growth | medium |
| FA-147 | A graph built while HITL was off stays ungated after HITL is turned on | Medium | AGENTS.md claims (§7) | high |
| FA-148 | `/abort` leaves an armed long task and its YOLO window | Low | AGENTS.md claims (§25B) | high |
| FA-149 | Macro sleep and reconsolidation wait from boot, not from their last run | Low | AGENTS.md claims (§15B) | high |
| FA-150 | `_distinct_tenants` reads SQLite on the event loop | Low | A6 memory | high |
| FA-151 | §2 says graph state never holds a chat id; §16B puts it there | Low | AGENTS.md claims | high |
| FA-152 | Stale names, counts and paths in AGENTS.md §1-§26 (grouped) | Low | AGENTS.md claims | high |
| FA-153 | `/swarm config disable` bypasses the chat-app admin gate | Medium | A4 chat apps | high |
| FA-154 | Stale names in AGENTS.md §27 onward (grouped) | Low | AGENTS.md claims | high |

Counts: Critical 0, High 2, Medium 24, Low 28.

## Findings

### FA-101 — `kazma update` reports success over a conflicting stash pop
- Area: A5 UI/CLI (`kazma update`, git install)
- Type: silent failure
- Severity: High (kept). The trigger needs uncommitted local edits that upstream also changes. When it happens, the update says it worked and the next boot reads a file with conflict markers.
- Confidence: high — repro offline (hunt)
- Surfaces / languages: CLI, English
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. The dropped call is at `update.py:1997`. The note cited 2010, which is the exception branch of the same step.
- Reproduction: throwaway `test_zz_audit2611_failpaths_update.py` (deleted). It builds a temp repo with an origin and a local uncommitted edit to a kazma.yaml line that origin also changes. It monkeypatches `_origin_is_trusted` to return (True, "x"), stubs `_ServerHold.acquire/release`, and has `_reinstall_after_reset` return True. Output: "update returned True", kazma.yaml holds `<<<<<<< Updated upstream`, and the stash entry is still present.
- Expected: §50 says the update installs, resumes and waits for Kazma, and a failed step keeps Kazma stopped and says so. The §53 pattern says a result that says whether it worked is read where it is returned.
- Observed: success is reported, conflict markers are in the working tree, and the owner's edits sit in a stash nobody is told about.
- Root cause: `kazma-cli/kazma_cli/update.py:1997` drops the result of `_restore_stash(cwd, stash_msg)`. `_restore_stash` (1401-1441) returns False on a conflict. `_postflight_ok` (1480-1501) checks only HEAD == origin/main and the CLI import, so it passes over unmerged paths. Line 2033 returns `ok and back`.
- Class and siblings checked: an ignored bool result. `tests/test_failure_results_are_used.py` covers only backup, migration, send and delivery functions, so update.py is outside it. The timeout branch (2005) and the exception branch (2010) of the same step log their failure.
- Fix: when the pop returns False, reset the tree to HEAD, say the local edits were not reapplied and name the stash, and return a distinct status (or test the pop before `_ServerHold`). Make `_postflight_ok` refuse unmerged paths (`git diff --name-only --diff-filter=U`).
- Gate (with negative control): the repro as a test, with the current line as the negative control. Add `update.py` to the failure-results gate.
- Effort: S
- Owner decision needed: no

### FA-102 — A rewind with a locked memory DB journals nothing; /undo and /edit still say memory was cleared
- Area: A6 memory
- Type: silent failure
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: chat apps `/undo` and `/edit` (English replies); web abort; Time Travel restore
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `conn = _open()` (about forget.py:1254) runs before `forget_journal.append` (1284).
- Reproduction: patch `forget._open` to raise `sqlite3.OperationalError("database is locked")`, then call `rewind_memory` for a two-turn rewind. Output: `result {'ok': False} journal before [] after []`.
- Expected: §15I "Forget converges" says every forget is appended to the journal BEFORE it changes anything. §12 D2 says a rewind rewinds memory.
- Observed: nothing is journaled, so the maintenance pass has nothing to apply, yet the WARNING says it will. The /undo reply is "✅ Removed last assistant response, and what Kazma remembered from it." whatever the result, and its error reply carries the raw exception text.
- Root cause: `kazma-core/kazma_core/memory/forget.py` `_forget_rewound_turns` (def 1234) opens the database before it journals. `rewind_memory` (1304-1323) returns `{"ok": False}`. The gateway callers ignore that result at `kazma-gateway/kazma_gateway/agent_handler/graph.py:2207` (/undo) and 2268 (/edit). Other callers drop it silently: graph.py:1410 (abort), `turn_abort.py:53` and `time_travel.py:1434`.
- Class and siblings checked: an effect attempted before its journal entry. The other forget entry points (forget.py:218, 299, 1155) journal first, so they are clean.
- Fix: append a whole-range journal entry before `_open`. /undo and /edit read `ok` and say when memory could not be updated yet, with no exception text.
- Gate (with negative control): a locked `_open` leaves a journal entry and one convergence pass applies it. The current order is the negative control.
- Effort: M
- Owner decision needed: no

### FA-103 — `get_vault()` caches a failed or in-progress start as "no vault"
- Area: A19 settings/backup (vault)
- Type: bug
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: every secret write and read during first use: Settings, provider keys, mail tokens
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `security/vault.py:861-877`.
- Reproduction: (a) Call `get_vault()` from a second and a third thread while the first is still inside the 600,000-iteration PBKDF2 constructor. Output: "first <SecretVault>, second None, third <SecretVault>". (b) Make the constructor raise once, then call again. Output: "after a transient failure: None init calls 1".
- Expected: §8 and §38 say secrets live in the vault, and a vault miss is never silent.
- Observed: a concurrent caller during startup gets None. One transient failure leaves the process without a vault until restart. ConfigStore then stores the value in plaintext (`config_store.py:1607-1616`), and its warning (1618-1625) reads as if no vault is configured.
- Root cause: `kazma-core/kazma_core/security/vault.py:861-877` sets `_vault_init_attempted = True` before construction and takes no lock.
- Class and siblings checked: a lazy singleton without a lock or retry. The email `vault_store` helper builds a fresh `SecretVault` each time, so it is clean.
- Fix: put a double-checked lock around construction. Mark the vault attempted only on success or on a missing/invalid key (ValueError). Retry other failures with a backoff.
- Gate (with negative control): concurrent first use returns the same vault in every thread, and a transient failure is retried. The current function is the negative control.
- Effort: S
- Owner decision needed: no

### FA-104 — Mail and calendar sign-ins ignore a failed vault write
- Area: A13 email/calendar
- Type: silent failure
- Severity: Medium
- Confidence: medium — code read only
- Surfaces / languages: Settings → Email and calendar connect flows (Gmail, Microsoft, IMAP/app password, Google and Outlook Calendar), token refresh
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed by reading; not run.
- Reproduction: code read. With `email_manager.credentials.vault_store` returning False (vault unavailable, see FA-103), a Gmail connect drops the token write, and the callback still reports the account connected.
- Expected: §34 says the vault is the source of truth for these tokens. The §53 pattern says a result that says whether it worked is read where it is returned.
- Observed: the account shows connected, and the next read finds no token.
- Root cause: `email_manager/credentials.py:56-75` (native skill) returns a bool. These callers drop it:
  - `oauth_gmail.py:356, 387, 449-468`
  - `oauth_ms.py:204-215`
  - `protocol_connect.py:89-91, 112-114, 150-152`
  - `refreshed_grants.py:81, 84, 86`
  - `calendar/credentials.py:76-79, 150-185`
- Class and siblings checked: an ignored bool result. `accounts.py:178` raises on False, so it is clean.
- Fix: `vault_store` raises on failure, or every caller checks the result, as `accounts.py` does.
- Gate (with negative control): with `vault_store` returning False, every connect and refresh reports failure. Add a source gate against a bare `vault_store(` statement. Today's callers are the negative control.
- Effort: S
- Owner decision needed: no

### FA-105 — Discord send returns "sent" after the 429 retries run out
- Area: A4 chat apps
- Type: silent failure
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: every Discord reply, `send_message` to Discord, reminders and the cron delivery outbox
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `_SEND_MAX_RETRIES = 3` is at `discord.py:106`, and the send loop is at 882-934.
- Reproduction: an `httpx.MockTransport` answers every message POST with 429 and `retry_after` 0. Output: "send returned True message POSTs 3".
- Expected: §51 says a send says whether it was delivered. §16E says the outbox acknowledges only on `sent:`.
- Observed: a message Discord never took is reported delivered, so a reminder is acknowledged and never retried.
- Root cause: `kazma-gateway/kazma_gateway/adapters/discord.py:882-934`. `all_sent = True` is set at the start, and the 429 branch `continue`s without marking the chunk unsent. After the last retry the loop exits with `all_sent` still True.
- Class and siblings checked: a send result not tied to each chunk. Clean: `telegram_send` (per-chunk flag), `slack.py:447-500` and the bus adapters.
- Fix: track `chunk_sent` per chunk, set only on a 2xx, and AND it into `all_sent`.
- Gate (with negative control): a parity test across the Telegram, Slack and Discord senders, where a platform that always answers 429 makes the send return False. The current Discord loop is the negative control.
- Effort: S
- Owner decision needed: no

### FA-106 — `resilient_chat` keeps old failover clients after a provider change
- Area: LLM providers (swarm worker and research model calls)
- Type: bug
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: swarm workers, the Refiner and synthesis calls, research pipeline calls
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `agent/resilient_chat.py:31-32` and 256-259.
- Reproduction: one call through `resilient_chat` fills its cache. Then the provider's config changes and `graph_supervisor.reset_failover_cache()` runs, and a second call is made. Output: "cache after reset: {'fb-model': <_Old>} old client calls: 1 registry rebuilds: 0".
- Expected: AUD-027 (§45) requires that a reconfigured provider not stay in failover until restart, with `sync_active_model` resetting the failover caches.
- Observed: swarm and research failover keep the old key and base URL, and old cooldowns, until restart.
- Root cause: `kazma-core/kazma_core/agent/resilient_chat.py:31-32`. The module dicts `_clients` and `_cooldowns` are filled at 256-259 and never cleared. `graph_supervisor.reset_failover_cache` (`graph_supervisor.py:63-77`) clears only the graph's own cache.
- Class and siblings checked: a cache with no reset. Research `frozen_failover` snapshots per pipeline by design, and `upsert_provider` drops the registry's client, so both are clean.
- Fix: add `resilient_chat.reset_failover_cache()` and call it from `sync_active_model` and provider saves. Or key the cache by a config fingerprint.
- Gate (with negative control): after a provider's key changes, the next failover builds a new client. The current module is the negative control.
- Effort: S
- Owner decision needed: no

### FA-107 — A failed model discovery is shown as a count of stale models
- Area: A19 settings/backup (Providers)
- Type: misleading text
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: Settings → Providers discovery, both languages
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: point a provider at the closed port 127.0.0.1:9 and run discovery. The call returns `['old-model-a','old-model-b']`, the cached list, and the page reports finding 2 models.
- Expected: §38 says no model fallback is silent. The UI rule says a failed read says it failed.
- Observed: the owner is told discovery worked and is shown the previous list.
- Root cause:
  - `kazma-core/kazma_core/model_registry.py:856-858` returns the cached list on error, and the Gemini path returns the static `GEMINI_MODELS`.
  - `kazma-ui/kazma_ui/providers.py:645-660` has no error field.
  - `static/js/settings_hub.js:945-961` shows the success toast (956).
- Class and siblings checked: a failure returned as stale data. Not checked further.
- Fix: discovery returns a typed failure. The route answers 502 `{ok: false, error, cached}`. The page shows an error toast and labels the list as cached.
- Gate (with negative control): discovery against a closed port answers `ok: false`. The current route is the negative control.
- Effort: S
- Owner decision needed: no

### FA-108 — "Reply persisted" is logged when the save failed
- Area: A20 ops signals
- Type: misleading text
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: `kazma.log`
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-ui/kazma_ui/turn_runtime.py:192-198`.
- Reproduction: code read. The INFO line "[turn] Reply persisted thread=... turn=..." is written after `persist_reply` whether `ok` is True or False, then `return ok`.
- Expected: §36 says a failed save raises the "NOT saved" alert. A log line names what happened.
- Observed: a reader of the log, or a script counting saves, sees a success line over a refused save. The callers' alert still fires.
- Root cause: `kazma-ui/kazma_ui/turn_runtime.py:192-198` does not branch on `ok`.
- Class and siblings checked: a log line that does not read the result. Not swept.
- Fix: log "persisted" only when `ok`, and log "NOT persisted" at WARNING otherwise.
- Gate (with negative control): a refused save produces no "Reply persisted" line. The current code is the negative control.
- Effort: S
- Owner decision needed: no

### FA-109 — Orphan recovery counts a task whose write was refused
- Area: Swarm (task store)
- Type: silent failure
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: boot recovery log, swarm history
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `persist_task` (task_store.py:358-369) returns a bool, and `requeue_orphaned_running` adds the task to `failed` at 813-815 whatever that bool says.
- Reproduction: code read. With the store refusing the write (persist_task returns False, no exception), the task is reported as recovered and failed, and the RUNNING row stays.
- Expected: §9 says a store fault never becomes a stored-looking success.
- Observed: the recovery count includes tasks whose ending was never written.
- Root cause: `kazma-core/kazma_core/swarm/task_store.py:813-815`.
- Class and siblings checked: an ignored bool. Exceptions are caught and logged at WARNING; only the False return is lost.
- Fix: append the task only when `persist_task` returns True, and otherwise log a WARNING and count it with `record_swarm_failure("task_write")`.
- Gate (with negative control): a refused write is not counted as recovered. The current loop is the negative control.
- Effort: S
- Owner decision needed: no

### FA-110 — A failed agent rebind after a key save is logged at DEBUG
- Area: LLM providers
- Type: silent failure
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: Settings → Providers key save, `kazma.log`
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-core/kazma_core/runtime/model_switch.py:68-75`.
- Reproduction: code read. `notify_credentials_changed` catches every exception from `_sync_agent` and logs "credential rebind failed" at DEBUG.
- Expected: §38 says no model fallback is silent. §35's quiet-handler rule says a failure that leaves a feature off is a WARNING.
- Observed: a saved key that never reached the running agent leaves no trace at the default log level. The agent keeps the old client until a restart or model switch.
- Root cause: `model_switch.py:68-75`.
- Class and siblings checked: a quiet handler around a state change. Not swept further.
- Fix: log at WARNING with the provider name, and raise the existing model-fallback notice if the agent keeps a client for another key.
- Gate (with negative control): a raising `_sync_agent` produces a WARNING. The current handler is the negative control.
- Effort: S
- Owner decision needed: no

### FA-111 — The "already read this turn" note is shared across chats
- Area: Agent tools (`file_read`)
- Type: misleading text
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: every surface that runs `file_read`; model-facing text
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. The cache is a module global (`file_read.py:151`), keyed at 235 by path, offset and limit, with no thread.
- Reproduction: code read. Two chats read the same unchanged file in overlapping turns. The second chat gets "[ALREADY READ THIS TURN ... IDENTICAL to what you already received. Do NOT re-read ...]" above the content, though it never read it.
- Expected: the cache's own contract is per turn. The content is returned and stamped, so only the label is wrong.
- Observed: the note tells the second chat's model something false about its own context. The cache is cleared at `graph_respond.py:79-81` and `swarm/worker.py:312-314`, which is process-wide, not per chat.
- Root cause: `kazma-core/kazma_core/tools/file_read.py:151, 235`.
- Class and siblings checked: process-global per-turn state. `turn_input._scratchpad_buffers` is keyed per thread.
- Fix: key the cache by thread id (the bound `_current_thread_id`), and clear only that thread's entries.
- Gate (with negative control): two thread ids reading one file both get the content without the note. The current key is the negative control.
- Effort: S
- Owner decision needed: no

### FA-112 — `find_free_slots` ignores multi-day and all-day events and the operator's zone
- Area: A13 email/calendar
- Type: bug
- Severity: High (kept from the hunt). The tool answers "free" over time that is booked. The lead may lower it if calendar use is rare.
- Confidence: high — repro offline (hunt)
- Surfaces / languages: the `find_free_slots` tool on every chat surface; sandbox, Google and Outlook backends
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed in all three backends.
- Reproduction: throwaway `test_zz_audit2611_features_slots.py` (deleted). A sandbox event from 11-02 to 11-05 still gives 10 free slots on 11-03. On Google, an all-day event plus an overnight event gives a first free slot at 09:00Z, inside both. Result: "3 passed in 2.05 s"; the tests assert the wrong behaviour.
- Expected: the tool manifest says it finds free time in a "date range". §16G says times are in the operator's zone.
- Observed: the working day is fixed at 09:00-17:00 UTC. An event counts only if it starts on the same date, and all-day events are not parsed.
- Root cause: each backend uses `date + "T09:00:00+00:00"`, adds 8 h and filters with `s.date() == day.date()`:
  - `calendar/backends/sandbox.py:89-114`
  - `google_calendar.py:167-194` (174-181)
  - `outlook_calendar.py:209-239` (216-223)
  - `tools.py:160-166, 200` takes one `date`, while manifest lines 44-50 promise a range.
- Class and siblings checked: three copies of one rule. No backend is clean.
- Fix: one overlap finder in `backends/base.py` that works in the operator's zone (`cron.timing`'s zone), parses all-day events and treats an event as busy when it overlaps the slot. Backends supply events only.
- Gate (with negative control): the three cases above, run against every backend. The current finders are the negative control.
- Effort: S
- Owner decision needed: no (yes only if working hours should become a setting)

### FA-113 — `dispatch_swarm` named "auto" routes to the first worker by name
- Area: Swarm (the agent's `dispatch_swarm` tool)
- Type: bug
- Severity: Medium (the Swarm page and `/swarm` route correctly; the agent's main tool does not)
- Confidence: high — code read only
- Surfaces / languages: every chat surface, through the agent's tool
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. Auto-registration is at `system.py:814-854` and the "auto" swap at 862-864.
- Reproduction: code read. Live steps for the lead: (1) with no workers registered, ask in chat to "use the swarm to write a Python function"; it runs on a newly registered `researcher`. (2) With `analyst` and `coder` registered, a coding task goes to `analyst`.
- Expected: §14 says a task with `workers=["auto"]` is routed, or spawned from a template, with the `catch_all` generalist taking what nothing matches. FEATURES.md:115-116 says workers are created on demand from templates.
- Observed: every chat "auto" dispatch goes to the alphabetically first registered worker. With none registered, a permanent `researcher` worker is added to the registry on the active model.
- Root cause: `kazma-core/kazma_core/agent/tool_builtins/system.py`. Lines 814-854 register a fixed `researcher`. Lines 862-864 replace "auto" with `engine.worker_names[0]`, and `worker_names` is sorted (`swarm/engine.py:441-443`). Routing and `maybe_scale` run only for `workers=["auto"]` (`swarm/dispatch_inner.py:49-60`), which the tool never passes.
- Class and siblings checked: one surface behind the others. The Swarm page, `/swarm` and `check_swarm_task` are clean.
- Fix: pass `workers=["auto"]` to the engine and remove the hard-coded `researcher` registration.
- Gate (with negative control): dispatch through the real tool with zero workers and with two registered. Assert the worker came from routing or a template, and the registry is unchanged. Today's tool is the negative control.
- Effort: S
- Owner decision needed: no

### FA-114 — The log-level hint says it applies now; it does not
- Area: A19 settings/backup (Settings → System → Logging)
- Type: misleading text
- Severity: Medium
- Confidence: high — code read only (logging semantics read, not run)
- Surfaces / languages: Settings page, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Live steps: set Level to DEBUG and use any feature. `kazma.log` gains no `"level": "DEBUG"` lines from `kazma_*` loggers until a restart.
- Expected: a hint that is true (UI convention "a control changes something, or is not shown"), or one that says "after a restart".
- Observed: the level is saved and takes effect at the next boot only. At WARNING, kazma INFO records still reach the handlers.
- Root cause: `kazma-ui/kazma_ui/i18n/catalog/settings.py:1223-1230` (`settings.logging_level_hint`, `settings.logging_restart_hint`) says "applies immediately". The hot-apply in `kazma-core/kazma_core/settings_manager.py:514-524` sets only the root logger's level, inside an `except: pass`. But `logging_config.py:312-315` gives `kazma_core`, `kazma_ui`, `kazma_gateway` and `kazma_skills` their own level, and lines 364, 372 and 376 give the file and console handlers that level.
- Class and siblings checked: help text that claims a live effect. Rotation and retention are correctly labelled "restart".
- Fix: apply the level to the four kazma loggers and the root handlers, or change both strings to "after a restart".
- Gate (with negative control): set the level through `settings_manager`, then assert a `kazma_core` DEBUG record reaches the file handler, and an INFO record does not at WARNING. The root-only `setLevel` is the negative control.
- Effort: S
- Owner decision needed: no

### FA-115 — The proxy hint omits the acquisition policy that decides whether it is used
- Area: A19 settings/backup (Settings → System → Proxy)
- Type: misleading text
- Severity: Medium
- Confidence: high — code read only
- Surfaces / languages: Settings → Proxy card and its Test button, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Live steps: enable the proxy and press Test. The answer is "disabled by acquisition policy" with no next step, and a `read_url` goes direct.
- Expected: §13 says stored proxy settings alone cannot authorize the route; it needs `KAZMA_ACQUISITION_MODE=local_first_with_remote_fallback` and `proxy` in `KAZMA_ACQUISITION_REMOTE_PROVIDERS`. FEATURES.md says so. The UI convention is one step that fixes it.
- Observed: the hint says scraping routes through the proxy when enabled, and the Test does not name what is missing.
- Root cause: catalog `settings.proxy_hint` (`kazma-ui/kazma_ui/i18n/catalog/settings.py:1703-1706`). The policy is in `kazma-core/kazma_core/web_acquire/policy.py:21-65`, and `settings_manager.test_proxy` (`settings_manager.py:578-594`) answers without naming the variables.
- Class and siblings checked: an opt-in that silently does nothing. The Firecrawl and Jina key fields share the gate; their hints were not checked (Unconfirmed).
- Fix: the hint and the Test both name the two variables, and the card shows the live policy state.
- Gate (with negative control): with the policy at `local_only`, `test_proxy`'s answer names both variables. Today's message is the negative control.
- Effort: S
- Owner decision needed: no

### FA-116 — The backup hint says 24 h, vectors and a Postgres dump
- Area: A19 settings/backup (Settings → Backup)
- Type: misleading text
- Severity: Medium (it invites trust in a backup that does not hold the vectors or the Postgres data)
- Confidence: high — code read only
- Surfaces / languages: Settings → Backup. EN says 24 h; AR leaves out the interval but keeps "vectors and Postgres".
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Compare the catalog text with the scheduler constant and the backup's own exclusions.
- Expected: §27F (the universal backup records whether the separate `pg_dump` is fresh and runs none itself), §24F (vectors are not backed up, they are rebuilt), and FEATURES.md "every 6 hours".
- Observed: wrong interval, and two stores claimed that this backup does not hold.
- Root cause: `kazma-ui/kazma_ui/i18n/catalog/settings.py:239-242`. Against it: `_BACKUP_EXPORT_INTERVAL_HOURS = 6` (`kazma-core/kazma_core/memory/worker_bootstrap.py:650`); `_rebuilt_dirs` (`backup/universal.py:586`); `_pg_dump_state` (`universal.py:144`, used at 1123-1141).
- Class and siblings checked: catalog text describing a cadence or scope. `backup_retention_hint` wording is in Unconfirmed. The disaster-recovery doc was not re-read.
- Fix: say "every Kazma database, chats, settings, documents and the workspace every 6 hours; Postgres has its own dump on the same cadence; search vectors are rebuilt, not backed up", in both languages.
- Gate (with negative control): any number of hours in a backup string equals `_BACKUP_EXPORT_INTERVAL_HOURS`. "24h" is the negative control.
- Effort: S
- Owner decision needed: no

### FA-117 — The outbound allowlist does not cover `send_message`; the dispatcher manifest misstates its target
- Area: Commitment layer (Settings → Safety; tool descriptions)
- Type: misleading text
- Severity: Medium (the approval card still applies, so there is no bypass)
- Confidence: high — code read only
- Surfaces / languages: Settings → Safety hint (EN and AR); the model-facing tool manifest
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Live steps: set the allowlist to one address, then ask Kazma to `dispatch_notification` to another chat id. The approval card appears, with no allowlist clarify card.
- Expected: §20C says unknown outbound targets go to clarify when the allowlist is set. The hint says it restricts "email/channel targets".
- Observed: the allowlist covers only tools profiled `send_outbound` with an argument named `to`, `target` or `recipient`.
- Root cause: `settings.outbound_allowed_targets_hint` (`kazma-ui/kazma_ui/i18n/catalog/settings.py:2501-2504`). The resolver is `kazma-core/kazma_core/safety/commitment/authorize.py:1403-1470` (argument names read at 1420). `send_message` is profiled with no outbound act (`safety/side_effects.py:192`), and `dispatch_notification` (193-200) is OUTBOUND with no act. The native manifest (`chat_platform_dispatcher/skill_manifest.yaml:20-23`) says sends go "to the current conversation", but `tools.py:114-136` requires `target_id` and defaults `backend="telegram"`.
- Class and siblings checked: one rule applied to some tools. `email_send` (`to`) is covered. `x_post` is gated by proposals instead.
- Fix: profile every message-sending tool `send_outbound` with a declared target field, and make the resolver read that field. Correct the manifest text.
- Gate (with negative control): enumerate tools whose profile or name sends a message; each must be `send_outbound` with a target field. Today's `dispatch_notification` entry is the negative control.
- Effort: M
- Owner decision needed: no

### FA-118 — The model's product knowledge has stale lines and an incomplete always-ask list
- Area: Agent prompt (product knowledge)
- Type: doc mismatch
- Severity: Medium (the model states these to the owner as facts)
- Confidence: high — code read only
- Surfaces / languages: every chat, through the system prompt
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed on all six lines.
- Reproduction: code read. Live steps: ask Kazma "where is your Soul stored?" and "how do swarm approvals reach me?".
- Expected: §11A, §15, §21, §24F, §7B, §22G and §7 (2026-11-01).
- Observed: `kazma-core/kazma_core/product_knowledge.py`:
  - line 90 says checkpoints are durable SQLite (Postgres installs keep them in Postgres);
  - line 95 calls Chroma memory (Chroma holds Knowledge vectors; memory is `memory_state.db`);
  - line 108 offers hub skills (hub install is not implemented, 501);
  - line 124 says the Soul is in `agent_evolution.json` (it is in ConfigStore);
  - line 159's always-ask list omits `request_path_access`;
  - line 168 gives a Telegram > Discord > Slack approval priority (every platform is asked at once and the first decision wins).
- Root cause: hand-written prose that nothing checks against the code.
- Class and siblings checked: lines 129-132, 141, 143, 146 and 158 are correct.
- Fix: correct the six lines. Derive the always-ask sentence from `ALWAYS_HITL_TOOLS`.
- Gate (with negative control): every tool name in product knowledge exists in the registry, and the always-ask sentence names exactly `ALWAYS_HITL_TOOLS`. Today's line 159 is the negative control.
- Effort: S
- Owner decision needed: no

### FA-119 — Scheduled time-zone hints say the opposite of the lookup order
- Area: A19 settings/backup (Settings → Scheduled)
- Type: misleading text
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: Settings → Scheduled, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read.
- Expected: §16G: a naive time means `cron.timezone`, else `KAZMA_TZ`, else UTC.
- Observed: `settings.cron_tz_source_env` says `KAZMA_TZ` overrides the setting. `settings.cron_tz_hint` says existing jobs keep their fire times, but a recurring job's next occurrence is computed in the zone in force when it advances. Only a stored one-off instant keeps its time.
- Root cause: catalog `kazma-ui/kazma_ui/i18n/catalog/settings.py:443-446, 475-478`. The code: `kazma-core/kazma_core/cron/scheduler.py:127-149` (lookup order), 924-933 and 1017-1031 (`_advancer(zone)`).
- Class and siblings checked: catalog text about precedence. Not swept further.
- Fix: reword both strings to match the code.
- Gate (with negative control): a string review is enough. Optionally, a test that the catalog's precedence sentence names `cron.timezone` first, with today's string as the negative control.
- Effort: S
- Owner decision needed: no

### FA-120 — The `python_exec` description promises limits the code does not apply
- Area: Agent tools (`python_exec`)
- Type: misleading text
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: the model-facing tool description
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read.
- Expected: a tool description the model can rely on.
- Observed: the description (`kazma-core/kazma_core/agent/tool_builtins/system.py:1007-1013`) says "Max 30s timeout, 512MB memory, isolated mode (no site-packages)". But:
  - the timeout is not clamped to 30 s (`tools/code_exec.py:565`; the Docker path uses `timeout + 15`);
  - `-I` drops the user site and the environment, not the venv's site-packages;
  - output is cut at `MAX_OUTPUT_CHARS = 4000` (`code_exec.py:53`), which the description does not say, so the model can read a cut output as complete.
  - The Windows Job Object memory cap does exist.
- Root cause: description text not kept with the runner.
- Class and siblings checked: tool descriptions; others are in Unconfirmed.
- Fix: state the real time bound, the output cap, and that the venv's packages are importable.
- Gate (with negative control): a test that the description's numbers equal the module constants. Today's text is the negative control.
- Effort: S
- Owner decision needed: no

### FA-121 — The built-in default system prompt is unreachable when kazma.yaml sets one
- Area: Agent prompt
- Type: dead control
- Severity: Low
- Confidence: medium — code read only (the live store's prompt value was not read)
- Surfaces / languages: every chat
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `agent_runner.py:308` is `self.config.system_prompt or self._default_system_prompt()`, and `kazma.yaml:54-80` sets `system_prompt`.
- Reproduction: code read.
- Expected: rules the code presents as always in the prompt reach the model.
- Observed: `_default_system_prompt()` (`kazma-core/kazma_core/agent_runner.py:428-475`), with its DOCUMENT GENERATION RULE, ITERATION EFFICIENCY and CRITICAL SYSTEM RULE, never reaches the model on a shipped install. SOURCE GROUNDING reaches it separately (`agent/source_grounding.py:25`).
- Root cause: the shipped `kazma.yaml` prompt takes precedence, and the default's rules were never merged into it.
- Class and siblings checked: prompt rules documented in code. Not swept further.
- Fix: merge the rules still needed into the shipped prompt (or the per-call base prompt), and delete the rest.
- Gate (with negative control): each rule a module documents as always in the prompt appears in the prompt built from the shipped `kazma.yaml`. Today's build is the negative control.
- Effort: S
- Owner decision needed: yes: which of the three rules should be in force.

### FA-122 — The Knowledge auto-inject hint says "every prompt"
- Area: A19 settings/backup (Knowledge page)
- Type: misleading text
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: Knowledge page, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-ui/kazma_ui/i18n/catalog/knowledge.py:255-257` (`knowledge.auto_on_msg`).
- Reproduction: code read.
- Expected: §24G (KQ01): a search or auto-inject shows only what is about the question, and nothing when nothing is.
- Observed: the hint says every prompt gets library text.
- Root cause: the text predates the evidence rule.
- Class and siblings checked: Knowledge catalog strings; not swept further.
- Fix: "Relevant library text is added to a prompt when it matches the question."
- Gate (with negative control): a string review is enough.
- Effort: S
- Owner decision needed: no

### FA-123 — The memory-mirror and vault hints describe behaviour the code does not have
- Area: A19 settings/backup
- Type: misleading text
- Severity: Low
- Confidence: medium — code read only (part of it read by a sub-reader; re-read in part)
- Surfaces / languages: Settings → Memory and Settings → System, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: `mem.mirror_hint` at `kazma-ui/kazma_ui/i18n/catalog/settings.py:3321-3324`; `sys.vault_hint` at 3813-3816. The code they describe is unchanged.
- Reproduction: code read.
- Expected: §15K: the mirror also serves recall on the Postgres-primary path, and rows only the mirror holds are counted and kept. The vault hint should match the boot path in `kazma-ui/kazma_ui/app.py:317-358`.
- Observed: the mirror hint describes a one-way copy. The vault hint's wording about where the key comes from differs from the boot path.
- Root cause: hint text not updated when `state_backend.sync_state_mirror` (`kazma-core/kazma_core/memory/state_backend.py:1410`) and the boot key handling changed.
- Class and siblings checked: grouped with FA-119 and FA-122 as catalog-versus-code text.
- Fix: reword both after a closer read.
- Gate (with negative control): a string review.
- Effort: S
- Owner decision needed: no

### FA-124 — `arabic_translate` (read tier, no card) sends text to MyMemory and returns its answer unfenced
- Area: Native skills / egress (merges sidedoors F2 and websec W4)
- Type: security
- Severity: Medium
- Confidence: high — code read only (the API was not called)
- Surfaces / languages: every chat surface that offers native skills
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Live steps for the lead: in a test chat, ask "Use arabic_translate on: audit probe 2611". Expect no card and an Arabic answer from MyMemory.
- Expected:
  - §19K: third-party egress of user content is opt-in.
  - §24B: one egress stack, local-only by default; the only direct-API exceptions are Jina, Firecrawl and loopback SearXNG.
  - §26G: remote text reaches the model fenced.
- Observed: any text the model chooses (a memory, a file excerpt) goes to a third party in a GET query string, with no card and no setting. On failure the source text is returned as if it were the translation.
- Root cause: `kazma-skills/kazma_skills/native/arabic_bilingual_nlp/tools.py`:
  - line 14 defines the tool;
  - lines 54-56 send `GET https://api.mymemory.translated.net/get` with `params={"q": text, "langpair": ...}`;
  - lines 66-75 are the failure fallback.
  - The tier is `read` (`kazma-core/kazma_core/safety/hitl.py:315`). The manifest does not name MyMemory.
  - The fence gate (`FENCED_TOOL_MODULES`, `tests/test_static_gates.py:2579`) covers only `read_url` and `web_search`.
- Class and siblings checked: a native-skill tool calling a hard-coded third-party host. `generate_image` (pollinations) is documented and explicit. `analyze_image` is SSRF-checked. The native-skill egress inventory was not exhaustive.
- Fix: translate with the configured LLM by default. Make MyMemory an opt-in backend, named in the manifest, with a POST body and a length cap. Fence the result with `fence_untrusted(source="translation")`. Return an error on failure.
- Gate (with negative control): an egress inventory of literal hosts called by native-skill tools, each declared, plus fencing for native-skill HTTP tools. The current `tools.py` is the negative control.
- Effort: S
- Owner decision needed: yes: whether a remote translation service may be offered at all.

### FA-125 — Settings Non-Stop "Per-Tool Timeout" is a dead control
- Area: A19 settings/backup (Settings → Agent → Non-Stop)
- Type: dead control
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: Settings page, EN and AR
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: `test_zz_audit2611_sidedoors_nonstop.py` (deleted) calls `SettingsManager(get_config_store()).save_nonstop_settings({"enabled": True, "tool_timeout_seconds": 7})`. Output: "page shows: 7.0 / stored nonstop: 7.0 / stored agent.tool_timeout_seconds: None / tool worker bound: 120.0".
- Expected: UI convention "a control changes something, or is not shown". §9 says every tool call is bounded at `agent.tool_timeout_seconds`.
- Observed: an owner who sets 0 or 600 still gets the 120 s cut, and the page reads back the saved value, so it looks applied.
- Root cause: `kazma-ui/kazma_ui/templates/settings.html:1161-1164`. `kazma-core/kazma_core/settings_manager.py:754-758` writes `agent.nonstop.watchdog.tool_timeout_seconds`, while the tool worker reads `agent.tool_timeout_seconds` (`agent/graph_helpers.py:110`). The `nonstop.py:12` docstring ("also read by graph_builder") is false. The field is defined at `nonstop.py:39, 186`.
- Class and siblings checked: written, never read. `failover.trigger_threshold` (`nonstop.py:54, 205`) and `context.emergency_compact_threshold` (60, 211) have no reader either, but no page control. Every other Non-Stop field on the page is read live.
- Fix: point the field at `agent.tool_timeout_seconds` and show it whether Non-Stop is on or off. Copy a stored value once. Delete the two unread fields and the false docstring.
- Gate (with negative control): every key a Settings save writes has a reader outside `settings_manager`, or is declared. This key is the negative control.
- Effort: S
- Owner decision needed: no

### FA-126 — `config_save`'s protected list misses keys that redirect secrets or memory
- Area: Security boundary (`config_save` and the commitment `config_change` resolver)
- Type: security
- Severity: Medium (the call is danger tier and carded, but the card does not show the consequence)
- Confidence: high — repro offline (hunt, admission only; nothing was sent)
- Surfaces / languages: any chat, through `config_save`
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/safety/protected_config.py` protects `backups.offsite.` (AUD-094) but none of the keys below.
- Reproduction: `test_zz_audit2611_sidedoors_cfgkeys.py` (deleted). Each key reads `protected=False sensitive=False validate=ok`:
  - `embedding.provider`, `embedding.base_url`, `embedding.api_key_env`;
  - `memory.backends.state.url`;
  - `providers.list` (the string "x" passes);
  - `connectors.*.allowed_users`;
  - `documents.security.remote_parse`, `documents.storage_root`.
- Expected: the module's own rule protects a key that could reach the network or expose a secret, with AUD-094 (§53) as the precedent.
- Observed: one approved card is enough. `kazma-core/kazma_core/memory/embedder.py:554-555` resolves `os.environ.get(<api_key_env>)` and sends it as a Bearer token to `base_url` with every memory text. With `api_key_env` set to the vault key's variable, the server's own secret leaves the machine. The card shows only the key and value.
- Root cause: a deny list with one entry per incident.
- Class and siblings checked: `workspace.selected_path` is protected. The Settings routes are admin-only, which is the operator's own choice.
- Fix: protect `embedding.`, `memory.backends.`, `providers.`, `connectors.*.allowed_*`, `documents.security.`, `documents.storage_root`, `proxy.` and `registry.active_*`. Better, invert to an allowlist owned by `settings_restore.KEY_RULES`. Refuse `KAZMA_*` names in `embedding.api_key_env` everywhere.
- Gate (with negative control): in `tests/test_protected_config.py`, every settings key whose value is a URL, host, env-variable name or allowlist is protected or declared. `embedding.api_key_env` is the negative control.
- Effort: M
- Owner decision needed: yes: deny list or allowlist.

### FA-127 — `gateway.rate_limits` is read once at boot
- Area: A4 chat apps
- Type: bug
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: Telegram, Discord and Slack flood guard; `config_save`
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-ui/kazma_ui/app.py:1150-1166`.
- Reproduction: code read. `config_save("gateway.rate_limits", ...)` answers "Setting saved" and the running limit does not change.
- Expected: §42 says the flood guard reads `gateway.rate_limits` from the settings store. §48 says a saved chat-app setting reaches the running adapter.
- Observed: the manager is built once, for the adapters present at boot. An adapter enabled later by `ChatAdapters.apply` gets the manager's default of 30, and only if some adapter existed at boot. With none at boot there is no flood guard until restart.
- Root cause: `RateFeedbackManager` is constructed at boot with the limits read then.
- Class and siblings checked: boot-read settings. `gateway.suggestions.enabled` may be the same (Unconfirmed).
- Fix: read the limit per message from the cached store, or rebuild on the §48 change notice.
- Gate (with negative control): set the key through the store, and the next message uses the new limit. Today's boot-only wiring is the negative control.
- Effort: S
- Owner decision needed: no

### FA-128 — Chat apps preview links in model-written replies
- Area: A4 chat apps
- Type: security
- Severity: Medium (an exfiltration path with no click and no card)
- Confidence: high — code read only (platform preview behaviour is documented, not observed)
- Surfaces / languages: every reply, bus message and swarm result on Telegram, Slack and Discord
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. No sender sets a preview flag.
- Reproduction: code read. Live steps for the lead: create a request-catcher URL you control. In a test chat with memory off, ask Kazma to reply with exactly `<catcher URL>?probe=2611`. The catcher should log a GET from the platform's preview fetcher within seconds. Repeat on Slack and Discord.
- Expected: the §45 rule: model output never makes the browser reach another host. The same principle applies to the chat platforms' servers.
- Observed: text an injected page persuades the model to write, such as a URL carrying a memory, is fetched by the platform as soon as the message lands.
- Root cause: no shared payload helper. Payloads:
  - `adapters/telegram_send.py:180` (`{"chat_id", "text"}`);
  - `adapters/slack.py:436-440` (`mrkdwn: True`);
  - `adapters/discord.py:887` (`{"content": chunk}`);
  - the bus senders (`telegram_bus.py:127, 178, 225, 293, 373`; `slack_bus.py:98-149`; `discord_bus.py`);
  - `agent_handler/swarm_output.py:158, 176`.
  - Only `kazma-core/kazma_core/observability/ops_alerts.py:302` disables previews.
- Class and siblings checked: a §45 rule one surface behind. Approval cards carry tool arguments, so a URL argument previews too.
- Fix: one helper, `kazma_gateway/outbound_flags.py`, for every send and edit of model or tool text: Telegram `link_preview_options.is_disabled`, Slack `unfurl_links/unfurl_media: false`, Discord `flags: 4`.
- Gate (with negative control): an AST gate that every send and edit payload goes through the helper, plus a per-adapter payload snapshot. Today's builders are the negative control.
- Effort: S
- Owner decision needed: yes: whether previews of links the owner typed may stay on.

### FA-129 — `GET /api/settings/proxy` returns the decrypted proxy password
- Area: A19 settings/backup (Settings → Proxy)
- Type: security
- Severity: Medium
- Confidence: high — code read only (live probably has no proxy password stored)
- Surfaces / languages: Settings → Proxy; the HTTP response and the page state
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed.
- Reproduction: code read. Live steps: signed in, open Settings → Proxy and read `/api/settings/proxy` in DevTools. The `password` field is plaintext when one is stored. `/api/settings/voice` answers with a mask.
- Expected: §26D: new API surfaces that echo config go through `mask_deep`. The save treats the mask as unchanged (`config_store.py:1708`).
- Observed: the password leaves the vault on every page load.
- Root cause: the route `kazma-ui/kazma_ui/settings.py:760-763` returns `get_proxy_settings()` raw. `kazma-core/kazma_core/settings_manager.py:540` reads `self._cs.get("proxy.password", "")`, which resolves the vault pointer. `static/js/settings_agent.js:497` loads it into the form (`templates/settings.html:2232`).
- Class and siblings checked: `/api/settings` (`mask_deep`), the voice route (`_mask`), the LLM key and the Slack app token are masked. The embedder, documents, diagnostics and offsite routes were not checked field by field; C6 is the offsite sibling.
- Fix: answer `"****"` when a password is stored and `""` when not.
- Gate (with negative control): walk every `GET /api/settings/*` route from the built app, with every sensitive key seeded with a sentinel through the vault. No response may contain a sentinel. The current proxy route is the negative control. This gate also catches C6.
- Effort: S
- Owner decision needed: no

### FA-130 — The GitHub OAuth result page puts its message into HTML unescaped
- Area: Security boundary (GitHub connect)
- Type: security
- Severity: Low (runs only after the state check; the detail is redacted and capped at 200 characters)
- Confidence: high — code read only
- Surfaces / languages: the GitHub OAuth callback page
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `_oauth_result_page` (`kazma-gateway/kazma_gateway/routers/github.py:715-753`) inserts `message` at 734, and line 597 builds `f"Token exchange failed: {detail}"`.
- Reproduction: code read.
- Expected: text from outside is escaped before it goes into HTML (the §45 modal rule).
- Observed: GitHub's error detail is interpolated raw.
- Root cause: an f-string HTML page with no escaping.
- Class and siblings checked: other OAuth result pages were not swept.
- Fix: `html.escape(message)`.
- Gate (with negative control): render the page with `<script>` in the message and assert it is escaped. Today's function is the negative control.
- Effort: S
- Owner decision needed: no

### FA-131 — `/health/ready` returns raw exception text without sign-in
- Area: A20 ops signals
- Type: security
- Severity: Low (no password is exposed; a psycopg error can name host, port, database and user)
- Confidence: high — code read only
- Surfaces / languages: `/health/ready`, an open route
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-ui/kazma_ui/health.py:89, 316, 348` return `"error": str(e)`, and the route is listed as open in `kazma-ui/kazma_ui/auth.py` (about line 690).
- Reproduction: code read.
- Expected: §26F: errors do not carry internals; use `safe_error`.
- Observed: failing checks return the exception text to an unauthenticated caller. Cloudflare Access probably fronts the live route; that is for the lead to check.
- Root cause: the readiness checks format exceptions directly.
- Class and siblings checked: related to C22 (catch-all exception text); not a duplicate, because C22 covers other routes.
- Fix: return a stable code and correlation id per check (`safe_error`), and keep the detail in the log.
- Gate (with negative control): a failing check's readiness answer contains no exception text. Today's `str(e)` is the negative control.
- Effort: S
- Owner decision needed: no

### FA-132 — The Telegram webhook logs a WARNING per unauthenticated POST
- Area: A4 chat apps
- Type: growth
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: `/api/webhooks/telegram` (an always-open prefix); `kazma.log`; the daily digest
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-gateway/kazma_gateway/adapters/telegram.py:1827-1842`.
- Reproduction: code read.
- Expected: an open route cannot be used to flood the log or the digest's WARNING count.
- Observed: with no `webhook_secret` set (the polling setup the live install uses), every POST logs a WARNING, and so does every bad secret.
- Root cause: an unthrottled log line on an open route.
- Class and siblings checked: other open webhook prefixes were not swept.
- Fix: throttle the line, or do not mount the route in polling mode.
- Gate (with negative control): 100 unauthenticated POSTs produce at most one WARNING per interval. The current handler is the negative control.
- Effort: S
- Owner decision needed: no

### FA-133 — OIDC `state` is one global key compared with `!=`
- Area: Security boundary (OIDC sign-in)
- Type: security
- Severity: Low (OIDC is probably not configured on live)
- Confidence: high — code read only
- Surfaces / languages: OIDC sign-in
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/security/oidc.py:124-136` stores the state in one global ConfigStore key, and line 175 compares with `!=`.
- Reproduction: code read.
- Expected: state is bound to the browser that started the sign-in and compared in constant time.
- Observed: two concurrent sign-ins overwrite each other's state, which also allows login CSRF. The comparison is not constant time.
- Root cause: global state storage instead of a per-browser cookie or session row.
- Class and siblings checked: the GitHub OAuth state check and the OIDC token checks are clean.
- Fix: store the state in a signed, short-lived cookie (or a per-request row), and compare with `hmac.compare_digest`.
- Gate (with negative control): two interleaved sign-ins both succeed with their own state, and a state from another browser is refused. Today's code is the negative control.
- Effort: S
- Owner decision needed: no

### FA-134 — Upload size limits apply after the body is spooled to disk
- Area: Security boundary (chat upload, voice)
- Type: growth
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: web chat attachment upload; voice upload
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. The size checks are at `kazma-ui/kazma_ui/routes_chat_upload.py:86` and `routes_voice.py:72-75`.
- Reproduction: code read.
- Expected: AUD-008/009 (§45) bound the voice read; the same bound should hold for disk.
- Observed: Starlette writes the whole multipart body to a temp file before the route checks its size. The bounded `file.read` caps memory, not temp disk. The only outer limit is Cloudflare's request cap.
- Root cause: the check runs inside the route, after parsing.
- Class and siblings checked: the documents route answers its budgets before reading the body (`precheck_upload`, §19 B3), so it is clean.
- Fix: refuse on `Content-Length` before parsing, and stream the multipart body with a counting limit.
- Gate (with negative control): a body over the limit is refused with no temp file written. Today's route is the negative control.
- Effort: S
- Owner decision needed: no

### FA-135 — Every log line is written and flushed on the event loop
- Area: Logging / loop
- Type: bug
- Severity: Medium
- Confidence: high — code read plus three live stall dumps read by the hunt
- Surfaces / languages: every surface (the server process)
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/logging_config.py:348-378` puts a synchronous `SafeTimedRotatingFileHandler` (line 357) and a `StreamHandler` (line 369) on the root logger. No `QueueHandler` or `QueueListener` exists in the package.
- Reproduction: the hunt read three stall dumps from the live `.kazma/` folder. `stall-20261101-000701` (15.5 s): the loop thread is inside a uvicorn access-log write and flush, under `httptools_impl.py:482`, `security_headers.py:67`, `memory_actions.py:147` and `proxy_headers.py:71`. `stall-20261031-155218` (15.5 s): `documents/worker.py:267` `logger.error` during the error flood of findings_draft C2. `stall-20260903-005704` (15.7 s): the same shape.
- Expected: §35 and §44: nothing blocking runs on the event loop, and `/health/ready` answers inside the guard's 10 s probe.
- Observed: while the disk is slow (a backup, a large write, a scan), every log call made on the loop waits for the file write. Three probes in a row that time out restart Kazma.
- Root cause: file and stream handlers attached directly to the root logger, so each `logger.*` call on the loop does the write and flush inline.
- Class and siblings checked: the §35 loop-stall gate checks named helpers and stores, never logging. No handler in the product is asynchronous. This adds evidence to findings_draft C2 and C3: an error flood multiplies the cost.
- Fix: one `QueueHandler` on the root logger, with the file and stream handlers run by a `QueueListener` thread; stop the listener at shutdown and at exit so the tail is flushed.
- Gate (with negative control): after `setup_logging()` the root logger has exactly one handler, a `QueueHandler`; with a file handler that sleeps 2 s per record, a 50 ms loop heartbeat is never more than 200 ms late. Today's configuration is the negative control.
- Effort: M
- Owner decision needed: no

### FA-136 — A watching WebSocket that stops reading freezes its thread's stream
- Area: Web chat delivery
- Type: bug
- Severity: Medium
- Confidence: high — repro offline (hunt) for the mechanism; how often it happens live is not measured
- Surfaces / languages: web chat (SSE stream and WebSocket watchers), approvals; both languages
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. In `kazma-ui/kazma_ui/delivery.py`, `emit` holds the thread's `asyncio.Lock` (line 342) while `_fan_out_sockets` (called at 360, body 404-412) awaits `socket.send_json` with no bound. The SSE queue's `put_nowait` (364) runs only after it.
- Reproduction: a throwaway test (deleted after the run) registered one socket whose send never returns and one healthy socket, then emitted frames. After 2 s: `emits_done` 0, healthy socket frames 0, SSE queue frames 0 (FAILED).
- Expected: §26E: never await a task's end without a bound while holding a lock others need. §31 D: both mouths paint every frame.
- Observed: one tab whose socket stops reading (a sleeping laptop, a dead phone connection) stops every frame of that thread. The SSE pump awaits `emit` per token (`kazma-ui/kazma_ui/sse_chat/_streaming.py:612-619`), so the answer stalls for every viewer. uvicorn 0.54's send waits on `writable` (`websockets_sansio_impl.py:432-433`), and a ping-timeout `transport.close()` defers `connection_lost` while the write buffer is not empty (`asyncio/selector_events.py:856-864`), so the wait may never end.
- Root cause: the socket fan-out is awaited inside the per-thread emit lock, ahead of the SSE queue.
- Class and siblings checked: every `emit` caller waits the same way: the approve route's `_tell` (`kazma-ui/kazma_ui/hitl_decision.py:210`), `hitl_timeout.py:181`, `thread_rewind.py:122`. Sibling of the same shape: `kazma-core/kazma_core/tracing/__init__.py:91-98` `_broadcast` (Low).
- Fix: feed the SSE queue first; send to each socket through a bounded call (`wait_for_bounded`, about 2 s) that unregisters and aborts a socket that times out. Better: a queue and a writer task per socket.
- Gate (with negative control): the repro passes (the healthy socket and the SSE queue get every frame within 1 s); a source gate finds no `await <x>.send*(` inside `async with` a lock in `delivery.py`. Today's broker is the negative control.
- Effort: S
- Owner decision needed: no

### FA-137 — Three cancel-then-unbounded-await sites the §26E gate misses
- Area: Loop / shutdown
- Type: bug
- Severity: Medium
- Confidence: medium — code read only (an absorbed cancel is rare: §26E measured 3 in 4,000)
- Surfaces / languages: server shutdown and reload, voice, browser rendering
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed; the cron lines moved from the note's 436-438. Sites: `kazma-core/kazma_core/cron/scheduler.py:437-442` (`stop()`), `kazma-ui/kazma_ui/routes_voice_ws.py:130-145` (`_cancel_utterance`), `kazma-core/kazma_core/web_acquire/browser_network.py:264-270` (`gather(*pending)` after cancelling).
- Reproduction: code read.
- Expected: §26E: use `cancel_and_wait` / `wait_then_cancel` / `wait_for_bounded`; never `X.cancel()` then `await X`.
- Observed: with an absorbed cancel, cron's `stop()` waits out a whole `poll_interval` (30 s, `kazma-ui/kazma_ui/app.py:1265`). Cron stops first in `_shutdown_services` and the guard's graceful stop is 60 s, so a slow stop can be killed. `_cancel_utterance` waits with no bound, and its `except` also swallows the caller's own cancellation. The browser network close waits on every pending handler with no bound.
- Root cause: the bare shape at three sites.
- Class and siblings checked: `tests/test_bounded_cancel.py:342-374` looks only at the two lines after a `.cancel()` (`block[i+1:i+3]`), so a later wait, or a wait through `gather`, passes. `bedrock_llm.py:439-445` was checked and is clean.
- Fix: `cancel_and_wait(task, timeout=...)` at each site; re-raise the caller's own cancellation in the voice route; widen the gate to the rest of the function.
- Gate (with negative control): the widened gate fails on the three current sites and on a planted cancel-then-gather; each site, given a task that absorbs its cancel, returns within its bound.
- Effort: S
- Owner decision needed: no

### FA-138 — After a fatal loop error the server does not exit
- Area: Process lifecycle
- Type: bug
- Severity: Medium
- Confidence: high — code read plus live stall dumps and guard.log read by the hunt
- Surfaces / languages: the server process and the guard
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `serve.py:139-145` (repository root) prints the traceback and calls `sys.exit(1)`.
- Reproduction: live evidence read by the hunt. Four stall dumps, `stall-20261008-022444`, `-022514`, `-022545`, `-022615` (15.7 s to 106.8 s), show the main thread in `threading._shutdown`. guard.log for 2026-10-07 23:25:03 to 23:28:17 UTC shows three probe timeouts, `child.graceful_timeout`, a kill, and `child.ready` at 23:28:17. The loop had died on WinError 10055 (no buffer space).
- Expected: §39: a dead server is replaced quickly. §26E: nothing left running that cannot end.
- Observed: `sys.exit` runs interpreter shutdown, which joins non-daemon threads (aiosqlite connections, anyio workers) that never finish once the loop is gone. The process stays up and silent until the guard's probes fail and its graceful timeout kills it: about 4 minutes down, about 2.5 of them in this hang.
- Root cause: `sys.exit(1)` after a fatal error in a process that holds non-daemon threads.
- Class and siblings checked: the normal shutdown path closes its stores first and exits cleanly (checked clean in the async hunt).
- Fix: after the traceback, `faulthandler.dump_traceback(all_threads=True)`, flush stdout, stderr and the log handlers, then `os._exit(1)`.
- Gate (with negative control): a child process that runs the error path with a planted non-daemon thread exits within 10 s. Today's `sys.exit(1)` is the negative control: it hangs.
- Effort: S
- Owner decision needed: no

### FA-139 — Small synchronous file I/O inside async tools
- Area: Agent tools
- Type: bug
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: agent tools on every surface
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed at `kazma-skills/kazma_skills/native/document_generator/tools.py:259, 342`, `kazma-core/kazma_core/tools/research_pipeline.py:345`, `kazma-core/kazma_core/tools/image_gen.py:151-155`, `kazma-core/kazma_core/tools/code_exec.py:808-810` (a `shutil.rmtree`) and `kazma-core/kazma_core/agent_skills/installer.py:325, 332, 369, 415, 482`.
- Reproduction: code read.
- Expected: §35: no blocking I/O inline in `async def`; an async tool's store and file work runs in `asyncio.to_thread`.
- Observed: small reads, writes and a directory delete run on the loop. Each is short on a healthy disk, and long when the disk is slow (see FA-135).
- Root cause: inline `open`/`write_text`/`rmtree` calls in async tool bodies.
- Class and siblings checked: the §35 gates check listed helpers and DB calls, not plain file I/O. The larger reads named in AUD-004/005 are already off the loop.
- Fix: wrap each call in `asyncio.to_thread`.
- Gate (with negative control): a source gate over async tool bodies for `open(`, `write_text(`, `read_bytes(`, `shutil.rmtree(` outside `to_thread`; the current files are the negative control.
- Effort: S
- Owner decision needed: no

### FA-140 — Telegram chunks are sized in code points, not UTF-16 units
- Area: A4 chat apps
- Type: bug
- Severity: Medium
- Confidence: high — repro offline (hunt)
- Surfaces / languages: Telegram replies with emoji or other characters outside the Basic Multilingual Plane; both languages
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed in `kazma-gateway/kazma_gateway/adapters/telegram_send.py`: the 4096 limit at line 27, plain text sliced by code points at 158, the HTML path's `body_limit` at 97 with its check at 133. On Telegram's "message is too long" the plain-text retry at 229-243 resends the same length and is refused again; an ERROR at 248, `False` at 261-265.
- Reproduction: a throwaway test (deleted after the run). Chunks of [4096, 3504] code points measured [4203, 3597] UTF-16 units; the HTML path [4214, 3026]. End to end: 2 chunks, 1 posted, result False. Control: markup-heavy HTML under the limit is sent.
- Expected: §42 (every message accounted for) and §51 (a send says whether it was delivered). Telegram counts its 4096 limit in UTF-16 units.
- Observed: a long reply with emoji loses its second half on Telegram. The caller `kazma-gateway/kazma_gateway/agent_handler/graph.py:2023-2030` ignores the `False` (declared `_CHAT_REPLY` in `tests/test_failure_results_are_used.py:482, 555`), so nothing tells the user.
- Root cause: `len()` and slicing in code points against a limit counted in UTF-16 units.
- Class and siblings checked: `telegram_bus._safe_slice_md` (lines 67-78) and the `[:4096]` slices at 83, 153 and 293 of `telegram_bus.py`; `kazma-core/kazma_core/swarm/bus.py:38`. Slack (3,900) and X counting are clean.
- Fix: one `telegram_units()` length for every split and check; on a "too long" answer split again instead of resending; `graph.py` reads the result and warns.
- Gate (with negative control): a property test over random text with astral characters: every chunk is at most 4096 UTF-16 units and the chunks rejoin to the text. The current chunker is the negative control.
- Effort: S
- Owner decision needed: no

### FA-141 — Four append-only stores have no retention
- Area: Storage growth
- Type: growth
- Severity: Low
- Confidence: high — code read plus live sizes read by the hunt
- Surfaces / languages: approvals, tool receipts, task ledgers, LLM ledger
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. No `DELETE FROM` in `kazma-core/kazma_core/safety/hitl_gates.py`, `agent/effect_journal.py`, `agent/task_ledger.py` or `observability/llm_ledger.py`; `store_registry.py` declares them at lines 107, 109, 151 and 171.
- Reproduction: code read; the hunt measured the live `hitl_gates.db` at 421 rows and 3.1 MB.
- Expected: §15B: a periodic cleanup is one entry of `_MAINTENANCE_SWEEPS`. §15I: Forget's erasure horizon names every copy it cannot reach.
- Observed: the four files grow for the life of the install. `hitl_gates.db` stores each call's arguments twice (`args_json` and `payload_json`), and `ERASURE_HORIZON` (`kazma-core/kazma_core/memory/memory_stores.py:291`) does not name it, so Forget's dialog does not say that approval cards keep the arguments.
- Root cause: no retention rule was written for these stores.
- Class and siblings checked: swarm tasks, checkpoints, the memory queue and research history have retention.
- Fix: one sweep entry per store with a declared age (decided terminal rows only), and an `ERASURE_HORIZON` line for `hitl_gates.db`.
- Gate (with negative control): `store_registry` requires a retention or a reason for every store; these four fail it today.
- Effort: M
- Owner decision needed: no

### FA-142 — `settings.db` free pages and a large `knowledge.db` WAL are never reclaimed
- Area: Storage growth
- Type: growth
- Severity: Low
- Confidence: medium — rests on live sizes read by the hunt
- Surfaces / languages: none visible; disk only
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed for the code: `copy_legacy_knowledge` (`kazma-core/kazma_core/stores/knowledge.py:1953`) moves the Knowledge tables out of `settings.db` with no VACUUM after, and no product connection sets `journal_size_limit`.
- Reproduction: the hunt read the live files: `settings.db` 29,446,144 bytes, of which 7,173 of 7,189 pages are free; `knowledge.db-wal` 18,020,912 bytes.
- Expected: §24G (`knowledge.db` keeps its own quota and compacts) and §21F (backups copy every `*.db`, so dead pages are copied too).
- Observed: about 29 MB of empty pages in every backup of `settings.db`, and a WAL that stays at its largest size.
- Root cause: a one-off move with no compaction, and WAL files never truncated after a checkpoint.
- Class and siblings checked: the snapshot store truncates its WAL after VACUUM (§12C); other stores do not.
- Fix: a one-off VACUUM of `settings.db` after the move, and `journal_size_limit` in `apply_sqlite_pragmas`.
- Gate (with negative control): a store opened through `apply_sqlite_pragmas` has a `journal_size_limit`; the current pragmas are the negative control.
- Effort: S
- Owner decision needed: no

### FA-143 — `guard.log` never rotates
- Area: Guard
- Type: growth
- Severity: Low
- Confidence: high — code read plus live size read by the hunt
- Surfaces / languages: the guard
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `GuardLog` in `scripts/service/kazma_guard.py:438-470` appends forever (the write is at about line 465).
- Reproduction: code read; the live `guard.log` was 1,315,962 bytes, growing since 2026-08-29.
- Expected: the guard's other logs rotate at 2 MB (`server.stderr.log`, §39; lines 1385, 1397, 1586, 2114).
- Observed: one file that only grows. The weekly report and the daily digest read it whole.
- Root cause: no rotation in `GuardLog`.
- Class and siblings checked: the stderr log and the broker log rotate.
- Fix: reuse the same rotate helper in `GuardLog`, keeping the readers' rotated-sibling handling (§27C).
- Gate (with negative control): writing 3 MB through `GuardLog` leaves a live file under 2 MB and one rotated sibling. Today's class is the negative control.
- Effort: S
- Owner decision needed: no

### FA-144 — The universal backup's manifest is written in place
- Area: A19 settings/backup
- Type: bug
- Severity: Low
- Confidence: medium — code read only
- Surfaces / languages: Settings → Backup, the 6-hour backup
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/backup/universal.py:1169-1181` writes `manifest.json` with three `write_text` calls; `_newest_complete` (732-744) trusts what it reads; the cleanup at 1059 removes only folders with no manifest.
- Reproduction: code read.
- Expected: §21F: a backup's manifest says whether it is complete, and retention keeps the newest complete one (AUD-021).
- Observed: a crash during a write leaves a truncated manifest. That folder is neither removed (it has a manifest) nor counted as complete, and a manifest cut at the wrong place can read as complete.
- Root cause: in-place writes instead of write-to-temp then rename.
- Class and siblings checked: `db/pg_backup.py:455` and the guard's `_write_json_atomic` write atomically.
- Fix: one `atomic_write_text` helper (temp file, flush, fsync, `os.replace`) for every manifest write.
- Gate (with negative control): no `write_text` of a `manifest.json` in product code outside the helper; a planted call is the negative control.
- Effort: S
- Owner decision needed: no

### FA-145 — Two process-lifetime maps are never pruned
- Area: A20 ops signals
- Type: growth
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: none visible; memory only
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/observability/ops_alerts.py:88, 153-180` keeps `_state` per alert key, and `runs/notify.py:148-149` adds a key per incident, so the map grows with every incident. `kazma-ui/kazma_ui/turn_document.py:66, 199-202` keeps `_hitl_emit_seq` per gate.
- Reproduction: code read.
- Expected: §26E and §33: alert state is bounded by keys, not by events.
- Observed: both maps grow until the process restarts. Small per entry, but unbounded on a long-running install.
- Root cause: entries are added and never removed.
- Class and siblings checked: the other module caches the hunt read are bounded (LRU or TTL).
- Fix: an LRU cap (or a TTL past the cooldown) on both maps.
- Gate (with negative control): 10,000 distinct keys leave each map at its cap; today's dicts are the negative control.
- Effort: S
- Owner decision needed: no

### FA-146 — Uploaded attachments are stored per upload with no dedupe or retention
- Area: Storage growth
- Type: growth
- Severity: Low
- Confidence: medium — code read plus a live file count read by the hunt
- Surfaces / languages: web chat uploads, chat-app attachments
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-ui/kazma_ui/chat_attachments.py:34-42` names each file `att_{uuid}{suffix}`.
- Reproduction: the hunt counted the live attachments folder: 57 files, 104 MB; the same 7.4 MB PDF was stored four times.
- Expected: §21F: every non-DB folder is copied into each universal backup, so its size multiplies by the backup count.
- Observed: every upload of the same file is a new copy, kept for good, and copied into seven backups.
- Root cause: a random name per upload and no cleanup.
- Class and siblings checked: the document store is content-addressed (§19), so it is clean.
- Fix: content-addressed names (SHA-256) with a reference count, or a retention rule tied to the chats that name the file.
- Gate (with negative control): uploading one file twice leaves one stored copy; today's naming is the negative control.
- Effort: M
- Owner decision needed: no

### FA-147 — A graph built while HITL was off stays ungated after HITL is turned on
- Area: AGENTS.md claims (§7)
- Type: bug
- Severity: Medium
- Confidence: high — code read only
- Surfaces / languages: web chat; Telegram, Discord and Slack (they read the same `_graph_holder`, `kazma-ui/kazma_ui/app.py:2063-2065`)
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-ui/kazma_ui/app.py:1956-1966` (`_recompile_holder_graph`) passes `None` when HITL is off at build time; it runs at startup (1985) and on a model change (1987). The first holder graph (`app.py:1196`) comes from `get_streaming_graph`, which does the same (`kazma-core/kazma_core/agent_runner.py:900-902`).
- Reproduction: code read. Steps for a live check: turn HITL off, reload, turn it on in Settings, ask the web chat for a `file_write`.
- Expected: §7A: the gate is active only when `hitl_config` is passed, and every build site must pass it. §7: a YOLO window is not a gate that is off.
- Observed: until a restart or model switch, `graph_tool_worker.py:885` re-reads the live config only when the graph has one; with `None`, line 919 leaves `_hitl_active=False`. Danger tools get no chat card: the gate context is not bound (1059), so `LocalToolRegistry.execute` sends them to the swarm bus (`agent/tool_registry.py:719-735`), which refuses on an install with no bus. Always-ask tools are refused with "Re-enable safety.hitl.enabled to use this tool." (1266), false while it is enabled. No approval is bypassed: the bus still gates.
- Root cause: the build sites drop the config when it is disabled, and the worker reads `None` as "never gate".
- Class and siblings checked: `_ensure_graph` (`agent_runner.py:1091-1115`) passes the dict whatever its state; `build_child_graph` builds per use, so it is current. A YOLO thread during a model switch cannot trigger this: every switch runs with no thread bound.
- Fix: pass the config dict at both cached sites and let the worker's live re-read decide.
- Gate (with negative control): build the holder graph with HITL off, turn it on in the settings store, run a `file_write` through the graph, assert an `interrupt()`. The current builder is the negative control.
- Effort: S
- Owner decision needed: no

### FA-148 — `/abort` leaves an armed long task and its YOLO window
- Area: AGENTS.md claims (§25B)
- Type: doc mismatch
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: web chat and chat apps
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. The gateway path (`kazma-gateway/kazma_gateway/agent_handler/graph.py:1382-1436`) and the web route (`kazma-ui/kazma_ui/sse_chat/__init__.py:2328-2356`, then `turn_abort.abort_turn`) cancel the turn only. `disable_long_task` is called only from `kazma-core/kazma_core/agent/capacity_commands.py:153-171` (`/long off`, `/unrestricted off`).
- Reproduction: code read.
- Expected: §25B: "`/long off` (or `/abort`) remains the immediate manual clear on any build."
- Observed: after `/abort`, a `/long mission` or an `/unrestricted` window, with its YOLO, stays armed for the next message. No user-facing doc repeats the claim.
- Root cause: the abort paths never touch the long-task record.
- Class and siblings checked: the recursion Partial pauses the long task (§25B); `/long off` clears it.
- Fix: either correct §25B, or have both abort paths call `pause_long_task()` as a Partial does.
- Gate (with negative control): after `/abort` on a chat with `/unrestricted` on, `long_task_status()` is not active and YOLO is off; today's abort is the negative control.
- Effort: S
- Owner decision needed: yes: should `/abort` also end a long task and its YOLO window, or only the turn

### FA-149 — Macro sleep and reconsolidation wait from boot, not from their last run
- Area: AGENTS.md claims (§15B)
- Type: bug
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: memory maintenance
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/memory/worker_bootstrap.py:705, 717, 1276, 1291` sleep the full interval from process start.
- Reproduction: code read.
- Expected: §15 B0: "A job's wait is counted from its last run, never from boot." The live install restarts several times a day.
- Observed: the 6-hour macro sleep and the 24-hour reconsolidation rarely or never run on an install that restarts more often than their interval (macro sleep's first sweep 60 s after boot still runs, so the effect is on reconsolidation most).
- Root cause: these two loops do not stamp and read a last-run time.
- Class and siblings checked: the digest, ledger, drill and backup loops use `observability/cadence.py`. `tests/test_scheduler_cadence.py` boots every scheduler; it does not catch these two.
- Fix: stamp the last run through `cadence.py` and sleep only the rest of the interval.
- Gate (with negative control): add both to `tests/test_scheduler_cadence.py` (job due in 30 minutes must run within 40 on a virtual clock); today's loops fail it.
- Effort: S
- Owner decision needed: no

### FA-150 — `_distinct_tenants` reads SQLite on the event loop
- Area: A6 memory
- Type: bug
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: memory maintenance
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-core/kazma_core/memory/worker_bootstrap.py:659-684`, called from async loops at 712, 809 and 1280.
- Reproduction: code read.
- Expected: §35: no blocking database work on the loop; §15G (S2): memory never runs on the event loop.
- Observed: one SQLite read per cadence pass on the loop. Small on a healthy disk.
- Root cause: a sync helper called directly from async scheduler code.
- Class and siblings checked: the other memory scheduler bodies run in threads.
- Fix: `await asyncio.to_thread(_distinct_tenants, ...)`, and add the helper to `_LOOP_STALL_HELPERS`.
- Gate (with negative control): the §35 helper gate, with the helper listed, fails on the three current calls.
- Effort: S
- Owner decision needed: no

### FA-151 — §2 says graph state never holds a chat id; §16B puts it there
- Area: AGENTS.md claims
- Type: doc mismatch
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: chat apps
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-gateway/kazma_gateway/agent_handler/store.py:256-264` puts `delivery_target` (which carries the chat id) in the `_gateway` routing block of graph state.
- Reproduction: code read.
- Expected: §2: "The LangGraph state NEVER contains `chat_id`, `user_id`, or `message_id`." §16B and §30E2 read `delivery_target` from that block on purpose.
- Observed: the two sections contradict each other; the code follows §16B.
- Root cause: §2 was not updated when §16B added the routing block.
- Class and siblings checked: no other platform id was found in graph state.
- Fix: state the exception in §2: the `_gateway` block holds `delivery_target` for reminders and expired-approval delivery, and nothing else.
- Gate (with negative control): none needed for the doc; an existing state test could assert `_gateway` holds no keys beyond the declared set.
- Effort: S
- Owner decision needed: no

### FA-152 — Stale names, counts and paths in AGENTS.md §1-§26 (grouped)
- Area: AGENTS.md claims
- Type: doc mismatch
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: none (documentation)
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed for each item below.
- Reproduction: code read.
- Expected: AGENTS.md describes the code at HEAD.
- Observed:
  - §1 says four `if` branches in three sites; dispatch is one `api_style` table (`kazma-core/kazma_core/provider_adapters.py:37-43, 76`). §2 names `agent_handler.py`; it is a package. §12A names `_ensure_streaming_graph`; the function is `get_streaming_graph` (`agent_runner.py:882`). §20D puts `side_effects.py` under `safety/commitment/`; it is `safety/side_effects.py`.
  - §17A names `_skip_real_adapters`, which does not exist (`app.py` about 1103-1116 sets the bus to `None` under pytest). §11B says three fence sites; a fourth, fenced, is `kazma-ui/kazma_ui/voice_turn.py:325-331`. §13 offers BrightData/Oxylabs as future work; both are in `proxy/registry.py:32-37`.
  - §26A says peer trust switches off entirely behind a proxy; `auth.py:520-541` keeps it for a non-proxy peer and under an explicit opt-in. §15B's sweep list misses three entries of `worker_bootstrap.py:1130-1151`.
  - §16I: a pass reads `max(50, max_concurrent*10)` due jobs (`cron/scheduler.py:787`), and "degraded" is 2 x interval + 30 s (528). §19 names `segments_around_tables`; it is `_segments_around_tables` (`pdf_layout.py:246`). §19C: only `lease_seconds` and `max_retries` are aliased (`documents/config.py:504, 529`).
  - §24C says CSRF honours `X-Forwarded-Host`; `csrf.py:15-17, 58-62` compares full origins and never trusts forwarded headers, as §45 says. §24C names four rate-limit buckets; `rate_limit.py` defines 13.
- Root cause: the doc was not updated when the code moved.
- Class and siblings checked: the rest of §1-§26 matched (see Checked and clean).
- Fix: correct each sentence.
- Gate (with negative control): a doc gate that every backticked `module.function` name in AGENTS.md resolves would catch the name items; a planted stale name is its control.
- Effort: S
- Owner decision needed: no

### FA-153 — `/swarm config disable` bypasses the chat-app admin gate
- Area: A4 chat apps
- Type: security
- Severity: Medium
- Confidence: high — code read only
- Surfaces / languages: Telegram, Discord, Slack chat commands
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed. `kazma-gateway/kazma_gateway/slash_commands.py:193-194` gates `/swarm config` only for `group` and `clear`. The `disable` branch of `agent_handler/commands.py:311-319` writes the same global `swarm.output_target` key. The gate runs at `agent_handler/graph.py:1455`.
- Reproduction: code read. Live steps: as a sender not in `KAZMA_GATEWAY_ADMINS`, send `/swarm config disable`.
- Expected: §46: every command that changes Kazma for everyone goes through `changes_global_config`, the swarm's output routing included.
- Observed: any allowed sender turns off swarm output routing for every surface ("Output routing disabled (config retained)").
- Root cause: the gate lists sub-command words by hand, and `disable` was missed.
- Class and siblings checked: `/config`, `/personality`, `/skill install|uninstall`, `/ide repo`, `/runs act` and `/kb add|crawl|refresh|delete` are gated; `/skill activate`, `/memory off|on`, `/replay clear` and `/swarm` status are correctly open. Only `disable` shares the defect. `tests/test_slash_admin_gate.py:38-39` lists only `group` and `clear`, so it cannot see the gap.
- Fix: `return sub == "config" and bool(arg)` (showing stays open), or derive the gated words from the handler's write branches.
- Gate (with negative control): an AST walk of the `/swarm config` handler finds every `cs.set`/`cs.delete` branch and asserts `changes_global_config` is True for its word. Today's `("group", "clear")` fails on `disable`.
- Effort: S
- Owner decision needed: no

### FA-154 — Stale names in AGENTS.md §27 onward (grouped)
- Area: AGENTS.md claims
- Type: doc mismatch
- Severity: Low
- Confidence: high — code read only
- Surfaces / languages: none (documentation)
- Found on: dev HEAD dae47783, offline only (not reproduced on the live install)
- Status at HEAD: confirmed for each item below.
- Reproduction: code read.
- Expected: AGENTS.md describes the code at HEAD, and the protected-file list names functions that exist.
- Observed:
  - §35 (AGENTS.md line 6532) names `test_the_memory_path_reaches_no_helper_through_another_module`; the test is `test_an_area_reaches_no_helper_through_another_module` (`tests/test_static_gates.py:1706`).
  - §38 (line 6798) names `hardening._project_files`, which does not exist; `kazma-core/kazma_core/security/hardening.py:332-336` uses `project_files.iter_project_files`, as §49 says.
  - §30 and §31 (lines 5595, 5836-5837, 5870) protect chat.js `_serverGates` and `_paintHitlFromDoc`; the code has `_serverGatesAuth` (lines 1553, 1599, 1635, 1683, 5403), and `_paintHitlFromDoc` appears only in a comment at 2221.
  - §45 (line 7457) says the `turn.timed_out` alert moved to `agent/turn.py`; it is raised in `kazma-core/kazma_core/agent/turn_budget.py:224-227`, as §31 says.
- Root cause: renames not carried into the doc.
- Class and siblings checked: the rest of §27 onward matched (see Checked and clean).
- Fix: correct each name. The protected-file list matters most: a reader looking for `_serverGates` finds nothing to protect.
- Gate (with negative control): the doc-name gate proposed in FA-152; a planted stale name is its control.
- Effort: S
- Owner decision needed: no

## Duplicates

Not written again; the existing entry in `findings_draft.md` covers them.

- hunt_features F1, hunt_sidedoors F1 and hunt_websec W1: the same finding as FA-001.
- hunt_websec W2: the same as C15.
- hunt_websec L3: the same as C19.
- Related, not re-reported: hunt_async A adds live stall evidence to C2 and C3 (now part of FA-135); hunt_websec L2 (FA-131) is close to C22 but names a different route.

## Unconfirmed

Items the hunts raised but could not settle offline. None is written as a finding.

- hunt_async: what caused the WinError 10055 behind FA-138; how often FA-136 triggers on live; whether ops-alert worker threads carry the ContextVars they need; the 2026-09-27 and 2026-09-29 stall dumps were not attributed.
- hunt_failpaths: an httpx `ReadTimeout` after the platform accepted a message returns False, so the cron outbox may send a reminder twice. Not reproduced.
- hunt_features: tool descriptions that may not match behaviour (`read_url`, `web_search`, `read_url_to_file`, `file_read`, `file_write`, `git_diff`, `email_send`, `memory_admin`, `x_delete_post`, `file_apply_patch_set`, `file_delete`, `file_list`, `run_research_pipeline`); `generate_pdf`/`generate_docx` may drop images (`tools.py:280`); the hints `backup_retention_hint` and `time_travel_max_snapshots_hint`; differences between the English and Arabic text; whether the live install overrides `system_prompt`; the F1 chain was not run end to end.
- hunt_growth: `.env` written in place (U1); the restic password file written with `write_text` (U2); the `.migrated` rename (U3); Discord length units (U4); lone surrogates in stored text (U5); restic at 11 GB and `pg_dump -Fc` growth (U6); `cultural_context` assumes UTC+3 (U7); leftovers from old backups (U8).
- hunt_sidedoors: MyMemory's 500-character limit; `gateway.suggestions.enabled` read only at boot (`app.py:1180`); `registry.active_model` set without its provider; a provider list entry named "x"; `document_import(".env")`; GitHub `oauth_scope`/`oauth_token_type`; a CWD fallback at `agent_skills/installer.py:281`.
- hunt_websec: the MCP filesystem server's `read_file` probably shares FA-001; `file_list` on `.env`; Slack text-unfurl settings; whether approval cards redact headers and `env`; the native-skill egress inventory is not exhaustive; OIDC login CSRF depends on how OIDC is configured.
- hunt_claims-a: §20A, both commitment choke points not traced end to end; §16D crash recovery read only; §15K re-mirror ordering; the numbers in §19 F3, G2, G4 and H-J; the guard side of §19 E1; the measured claims of §24B.
- hunt_claims-b: §50 names an `oauthlib` floor for `docling`, but `pyproject.toml` has it only in `rag` (wording, at most Low); a Testing & Validation paragraph is repeated; §50 abbreviates the update command that the code prints in full.

## Checked and clean

- hunt_async: `TurnJournal.append`; bounded cancellations in shared approvals, the supervisor watchdog, the task-queue lease, `gateway._consume`, telemetry, the Discord heartbeat, Slack's listener, the typing keep-alive and the checkpoint manager; Bedrock; the MCP UI stderr reader; httpx and aiosqlite clients closed; the guard does not pipe stdout; the normal shutdown path.
- hunt_failpaths: `set_chat_remembered`, `_activate_workspace`, `set_mcp_server_enabled`, `_store_config_secret`; X dispatch; the shared-approval compare-and-set; `bounded_cancel`; Telegram and Slack 429 handling; the bus adapters; task-queue fencing; the cron outbox; the `file_read` stamp; the knowledge sweep; frozen failover; `upsert_provider`; 88 quiet handlers reviewed.
- hunt_features: `/help`, the "/" menu and the slash-command reference agree; `/stop` aliases `/abort`; product knowledge lines 129-132, 141, 143, 146, 158; the FEATURES.md items checked; tool descriptions for scheduled jobs, X, email, calendar, git, knowledge, memory and swarm; an i18n spot check.
- hunt_growth: timestamps are aware UTC; X length counting; no Telegram captions over the limit; NUL bytes cleaned; Slack's 3,900 limit; the bounded caches; the atomic writers; POSIX-guarded process kills.
- hunt_sidedoors: the child graph's `safety_mode`; cron's declared tools; `config_save`'s tier; `crawl_site` SSRF checks; the HITL default-deny floor; the lint child environment; document generators confined to their folders; the store refusal on every door; `generate_image` documented; the other Non-Stop fields read live; the vision path checks.
- hunt_websec: CSRF; CORS; forwarded headers; loopback cookies; WebSocket cross-site hijack; CSP; cookie flags; login throttle and safe `next`; chat file serving; document ACL; research containment; backup names; OAuth state for GitHub; Telegram secret compared with `compare_digest`; `IdeService.resolve`; `::$DATA` paths; `shell_exec` arguments; `analyze_image`; no text in the LLM ledger; `mask_deep`; a secret scan of the live logs found nothing.
- hunt_claims-a: AGENTS.md §1-§26 matched the code apart from FA-147 to FA-152.
- hunt_claims-b: §27-§53, UI Conventions, Server Management, Testing and Key References matched the code apart from FA-153 and FA-154.
