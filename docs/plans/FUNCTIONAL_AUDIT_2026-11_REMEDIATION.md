# Functional audit 2026-11: remediation plan

| | |
|---|---|
| Audit | [`docs/audits/AUDIT_FUNCTIONAL_LIVE_2026-11.md`](../audits/AUDIT_FUNCTIONAL_LIVE_2026-11.md) |
| Evidence | `docs/audits/evidence/functional_2026-11/` (`findings_draft.md` holds each live candidate C<NN> = FA-2NN in full, `offline_findings.md` each FA-1NN) |
| Verdict | Works with issues |
| Findings | 128: Critical 0, High 18 (3 fixed during the audit), Medium 50, Low 60 |
| Owner decisions | FA-215, FA-218, FA-258, FA-271, FA-272, FA-277 (Stage 0) |

How to read this plan:

- **Every item lands with three things.** These are the fix, a gate that fails on the old code (the negative control), and the behaviour it locks. This follows AGENTS.md §28 and §35.
  - A class finding gets a class gate. The gate enumerates the siblings from the source the product uses, so it covers more than the one site.
- **Effort is measured with the gate.**
  - XS: under an hour.
  - S: a day or less.
  - M: two to four days.
  - L: a week or more.
- **Stages run in order.** Inside a stage, items are grouped by class. One class gate then covers its whole group.
- **Deploying:** each stage is deployed whole (pull, reload, live check), never part of one.

## Stage 0: owner actions and decisions

These need the owner's hands or the owner's call. None of them is code in this repository.

| ID | What | Why it is the owner's | Proposed action |
|---|---|---|---|
| FA-258 (High) | The live install runs 26 packages below this build's floors. 10 of them have known advisories, and the document worker image is outdated. | It is a package install and an image build on the live machine. | Run `<live python> -m kazma_cli update --reinstall -y`, then `<live python> scripts/document_worker_image.py build`. The code half is in Stage 1, Dependencies. |
| FA-218 (High) | Postgres (`kazma-db-win`), Neo4j and SearXNG publish their ports on every interface (`0.0.0.0`). | It means re-creating Docker containers that hold live data. | Take a checked dump (`scripts/pg_backup.py backup`, then `list`). Re-create each container with `127.0.0.1:` port bindings and the same volumes. Optionally give Kazma a non-superuser login. The Kazma-side gate is in Stage 1. |
| FA-215 (Medium) | The offsite copy is a plain zip that holds `.env`. | The owner chose the offsite target and its key handling. | Decide: encrypt the zip with the restic passphrase, or leave `.env` out of the offsite copy (the vault key is escrowed separately). |
| FA-271 (Medium) | The GitHub App private key sits as plaintext in Postgres beside its vault copy. | Rotating the key is a credential change. | The code half (move plaintext rows to pointers) is Stage 2. Decide whether to rotate the key after the move. |
| FA-272 (Low) | The restic passphrase file's ACL was not shown to be restricted to the owner's account. | It is a Windows ACL on the owner's file. | Decide whether `restic.pass` gets the token file's treatment (§39: `icacls /inheritance:r` plus the account's SID). The code half adds a check to the hardening report. |
| FA-277 (Medium) | First token: p50 5.3 s, p95 20.1 s, against a budget of p50 ≤3 s and p95 ≤8 s. | It is a model, cost and quality trade-off. | Choose one or more: reasoning off (or a non-reasoning model) for turns that need no plan; fewer tool definitions per turn (about 30,200 input tokens a turn today); stream the reasoning where the provider offers it. The latency benchmark gate is in Stage 2. |

## Stage 1: the High findings

### Already fixed during the audit

| ID | Finding | Fix | Gate |
|---|---|---|---|
| FA-001 | Read-tier tools read `.env`, `restic.pass` and the vault key with no card. | `cdbe9483`: `store_registry.is_kazma_store` also answers for the install's secret files and database copies, at every door. | `tests/test_kazma_secret_files_refused.py` (class gate over the tools, with its negative control) |
| FA-002 | The Docker sandbox of `python_exec` mounted the whole workspace, `.env` included. | `cdbe9483`: the workspace is mounted read-only, with Kazma's folders as empty tmpfs and its secret files as `/dev/null`. | `tests/test_sandbox_hides_kazma_files.py` (a real Docker run where the image is present) |
| FA-003 | Copies of stores (`*.db.pre_relabel`, `.bak-*`) were readable as ordinary files. | `cdbe9483`: a name that continues a database suffix, or a file under the data dir that starts with the SQLite header, is a store. | `tests/test_kazma_secret_files_refused.py` |

They were deployed on 2026-11-02 (live build `73eee4b6`) and checked on live, with every door refusing.

### Stage 1 status (2026-11-02)

Every Stage 1 item below is implemented with its gate and negative control (AGENTS.md §54 names each one), and FA-218's compose half shipped in `2b6ab511`. The class gates: `tests/test_settings_keys_have_readers.py` (dead Settings controls; it also found the Account password and session list, and four controls read by nothing), `tests/test_dependency_floors.py` (advisory floors) and `tests/test_default_voice_providers_declared.py` (a default provider's imports). FA-125 (Non-Stop per-tool timeout) stays in Stage 2: its key is read into `NonStopConfig`, and the field is what nothing uses.

### Recovery: an outage, a restart or a restore leaves Kazma wrong

Class gate for this group: **every recovery path the docs promise has a drill in the test suite that runs the real thing** (a real Postgres outage, a real restore, a real export from the wrong folder), with the old code as the negative control. The drills join the `@pytest.mark.postgres` job where they need Postgres.

**FA-259 (High, M): a two-minute Postgres outage gets Kazma killed by its guard.**
- **Fix, in four places:**
  - `ConfigStore` holds no lock across the pool checkout (`config_store.py:1644`, `:1867-1884`). A reader must not wait behind a writer that is waiting for a connection.
  - `/health` skips the auth store's database read (`auth.py:1380-1433`). The probe must answer while the database is away.
  - Readiness checks are cancelled at their cap (`health.py:432-446`), not left running behind the answer.
  - A boot that finds no database waits for it, saying so, instead of exiting (`app.py:272`). One run in twelve kept Kazma down for 15 minutes.
- **Gate:** a real-Postgres drill. Stop the database for 2 minutes while the guard's probe runs every 10 s, and expect no restart and an honest 503 that names the database. Then a boot drill with the database away.
- **Negative control:** the current lock order and boot exit.

**FA-263 (High, S): a crawl cut by a graceful reload never resumes.**
- **Fix:** `kb_api.py:565-573` `release_unclaimed` records `interrupted_by: process_ended` on the journal row. Auto-resume (`crawl_jobs.py:839-847`) and `_recover` (`:503`) look for exactly that.
- **Gate:** start a crawl in a child process, stop it gracefully mid-crawl, start again, and expect the crawl to resume once.
- **Negative control:** today's release.

**FA-268 (High, S, or M with a boot drill): the documented restic restore produces a layout Kazma does not read, and reports success.**
- **Fix:** `restore.py:263` flattens the snapshot's `dbs/` and `assets/` into the data dir's top level, the layout `store_registry` and `paths.py` read.
- **Gate:** restore a real snapshot into a scratch data dir. Then `store_registry` must find every store, and recall must read the restored memory.
  - Today `tests/test_restore.py:162` checks only that the restore ran. Re-point it.
- **Negative control:** today's restore.
- **Related:**
  - FA-267: the universal backup copies nested live SQLite stores raw, without their WAL.
  - FA-269: the docs name the wrong passphrase path.
  - FA-270: a restore can leave a stale `-wal`/`-shm` beside the restored file.

**FA-252 (High, S): an export run from the wrong folder exports an empty new install, and `verify` passes it.**
- **Fix:**
  - The exporter refuses a data dir that holds no store. `paths.py:138-149` creates `<cwd>/kazma-data` on first use, so an export outside the install made an empty one and exported that.
  - `verify` refuses a bundle with no `vault.db` and no settings.
- **Gate:** an export from an empty folder fails with a message naming the data dir it looked in, and `verify` refuses a hand-made empty bundle.
- **Negative control:** today's exporter and `verify`.

**FA-255 (High, S): an import revives revoked "Allow tool" grants and signed-out web sessions.**
- **Fix:** the import applies §47's key classes, as the Settings restore does. Runtime state and credentials (`hitl_grants`, `web_session.`, `account.`) are never written from a bundle.
  - The places: importer step 7, `config_store.py:2475-2527`, `hitl_grants.py:188-207`.
- **Gate:** export, revoke a grant and sign out, import. The grant must not be effective and the session must not authenticate.
  - Class: the import path asks `settings_restore.classify` for every key, the same as the restore.
- **Negative control:** today's import.

**FA-101 (High, S): `kazma update` reports success over a conflicting stash pop.**
- **Fix:** when the pop fails:
  - reset the tree to HEAD;
  - say the local edits were not reapplied, and name the stash;
  - return a distinct status.

  Also, `_postflight_ok` refuses unmerged paths (`git diff --name-only --diff-filter=U`).
- **Gate:** the repro as a test (a local edit that conflicts with the incoming commit). Also add `update.py` to `tests/test_failure_results_are_used.py`.
- **Negative control:** the current pop handling.

### Privacy: work the owner kept out of memory reaches memory anyway

Class gate for this group: **every path that writes a memory, a fact or a lesson asks the forget ledger first.** The ledger is `forget.refuses_write`, under every key of the chat. The gate enumerates the writers from the source: forks, the import, the export's restore, `/new` promotion, self-improvement, the swarm bridge. Every writer must call the ledger or be declared.

**FA-246 (High, S): a fork of a chat kept out of memory is remembered.**
- **Fix:** `thread_rewind.py:385-447` `fork_thread` copies the source chat's whole-chat ledger row under the fork's keys before the fork's first turn.
- **Gate:** keep a chat out, fork it, run a turn through the real route. Nothing is in recall or the episode table.
- **Negative control:** today's fork.

**FA-212 (High, S): self-improvement writes lessons from failed turns, from chats kept out of memory, and from fallback templates.**
- **Fix:**
  - No lesson from a turn with `turn_failed`, from a chat the ledger keeps out, or from a fallback template reply (`self_improvement.py:327`).
  - The Soul's size cap keeps whole blocks: it must not cut a lesson in half.
- **Gate:** each of the three, through the real post-turn path, leaves the Soul unchanged. A successful remembered turn changes it once.
- **Negative control:** today's analyzer.

### Provider and model calls

**FA-214 (High, S): one streaming 429 ends the turn.**
- **Fix:** honour `Retry-After` on the stream path, bounded, as the non-stream path does (`llm_provider.py`, around line 1683). Otherwise mark the stream's 429 retryable (`kind="rate_limit"`), so the supervisor's backoff applies (`graph_supervisor.py:1607`).
  - Word the reply from the attempts actually made.
- **Gate:** a scripted provider that answers 429 then 200 on the stream must answer the turn. A provider that answers 429 N times must say how many attempts were made.
- **Negative control:** today's stream path.
- **Related Mediums:** FA-211 and FA-213 (Stage 2, Provider honesty).

### Dead controls: a switch that changes nothing

Class gate: **every key a Settings control writes has a reader outside its own page** (`settings_manager` and the page's routes do not count), or the key is declared with its reason. Enumerate the keys from the Settings routes' writes. The same gate covers FA-125 and FA-249.

**FA-250 (High, S): the Settings → Tools switch changes nothing.**
- **Fix:** the registry (`tool_registry.py:445-466`, `:513-538`) leaves a tool switched off by `tools.<name>.enabled` out of `get_tool_definitions`, and `_execute_inner` refuses it. That key is written at `settings_manager.py:1016-1018`.
  - Read the switches once per store, as §22G's skill switches are read.
- **Gate:** switch a tool off through the route. It is absent from the next model call's tools, and a direct call is refused. The class gate above covers the rest.
- **Negative control:** today's registry.

### Correctness

**FA-004 (High, M): Kazma misreads its own Arabic-page PDF: lines repeated, cells glued.**
- **Fix:**
  - `pdf_rtl.py:379` takes the glyphs whose baseline lies inside the block, not every glyph the rectangle touches.
  - The word-gap rule applies on left-to-right lines too (`pdf_rtl.py:310`).
- **Gate:**
  - The audit's minimal PDF reads the same with and without one Arabic word.
  - A DOCX → PDF round trip of a two-column table reads `Q1 | 120`.
- **Negative control:** the current `text_in_rect`.
- **Related:** FA-230 (a year at the end of an Arabic sentence moves to the front).

**FA-112 (High, S): `find_free_slots` ignores multi-day and all-day events and the operator's zone.**
- **Fix:** one overlap finder in `backends/base.py`. It works in the operator's zone (`cron.timing`'s zone), parses all-day events, and treats an event as busy when it overlaps the slot. Backends supply events only.
  - Today each backend has its own loop: `sandbox.py:89-114`, `google_calendar.py:167-194`, `outlook_calendar.py:209-239`.
- **Gate:** three cases, run against every backend:
  - an event spanning midnight;
  - an all-day event;
  - an event in another zone.
- **Negative control:** the current per-backend loops.

### Dependencies

Class gate: **every advisory OSV knows for an installed package has a floor at or above its fix in `pyproject.toml`**, or a `REVIEWED_ADVISORIES` entry with its reason. The same goes for every third-party import that a default provider reaches.

**FA-258, code half (High, S):**
- **Fix:**
  - Floors at the fix for the five advisory packages that have no floor, and raise pillow and pypdf to their fix.
  - The deploy recipe (AGENTS.md Server Management, the deploy memory) gains the update step after the pull whenever `pyproject.toml` changed.
  - The boot check (`install_requirements`) already names the gap.
- **Gate:** the class gate above, run against a recorded OSV answer (`tests/fixtures/`).
- **Negative control:** today's floors.

**FA-248 (High, S): read aloud and voice-note replies fail, because Microsoft refuses the default TTS provider.**
- **Fix:**
  - Declare `edge-tts>=7.3.1`, the release that speaks Microsoft's current protocol.
  - A voice-reply failure is logged at WARNING (`tts.py:273`, `:303`, `:364`). The Telegram path logs it at DEBUG today, so it fails silently there.
- **Gate:**
  - Every third-party import a default provider reaches is declared, with a floor.
  - A TTS failure on a chat app produces a WARNING and a text reply.
- **Negative control:** today's undeclared import and DEBUG line.

### Infrastructure, Kazma-side half of FA-218

- **Fix:** the hardening report (§49) and the boot check flag:
  - a Docker port of the database, Neo4j or SearXNG that is published on a non-loopback address;
  - a Kazma database role that is a superuser.
- **Gate:** a recorded `docker inspect` and `pg_roles` answer with a `0.0.0.0` binding fails the check; a loopback binding passes.
- **Effort:** S.

## Stage 2: the Medium findings

Grouped by class. One gate per class where a class exists. Each finding's own repro is in the evidence files.

### Stage 2 status (2026-11-02)

Every Stage 2 item below is implemented with its gate and negative control (AGENTS.md §55 names each one). FA-267 landed with Stage 1 (`tests/test_restore_layout.py`). FA-277's Stage 2 half is the benchmark (`tests/e2e/test_first_token_latency.py`: Kazma's own share of the first token, measured p50 0.05 s); the model and reasoning choice stays the owner's (Stage 0). FA-233 takes the plan's second option: readiness says each scan pays the signature load when no clamd answers, and Kazma uses `clamdscan` only when one does; running clamd (about 1 GB of memory) is the owner's choice. FA-124's remote translation is an environment opt-in (`KAZMA_TRANSLATION_REMOTE=mymemory`), off by default.

### Secrets and error text (§26D, §26F)

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-206 | `mask_deep` masks `access_key` (the S3 access key id is returned unmasked by `GET /api/settings`). | FA-129's gate: walk every `GET /api/settings/*` route with every sensitive key seeded with a sentinel through the vault; no response holds a sentinel. Today's masker is the control. | S |
| FA-129 | `GET /api/settings/proxy` answers `"****"` when a password is stored and `""` when not. | The same walk. Today's proxy route is the control. | S |
| FA-222 | The app's catch-all handler (`swarm/middleware.py:84-93`) returns `safe_error(exc)`. Delete the `KAZMA_ENV` branch (production is `KAZMA_PRODUCTION`). | A route raising an exception whose message holds a planted DSN; the body does not contain it. The old handler is the control. | S |
| FA-271 | Move the plaintext GitHub App key rows to vault pointers, in Postgres and SQLite. The rotation is the owner's (Stage 0). | A plaintext row in each backend becomes a pointer at boot; the read still returns the key. | S |

### Provider honesty (§3, §29F, §38)

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-211 | A provider that answers 200 with a non-JSON body, or with no choices, is reported as what it is, not as "hit the tool-round limit" (`graph_respond.py:254-266`). | A scripted provider with each bad body; the reply names the provider's answer. Today's text is the control. | S |
| FA-213 | A stream that ends at EOF with neither `[DONE]` nor a `finish_reason` raises `LLMError(transient=True, kind="stream_truncated")`. With deltas already shown, §29F still holds: no re-stream, and the final reply carries an honest "cut off" note. | A close-delimited fake server. The old code returns the fragment as a complete answer. | S |
| FA-210 | The bubble names the model that answered, not the active one (`_streaming.py` `_done_model`). | A turn answered by the failover model shows that model. Today's frame is the control. | S |
| FA-106 | `resilient_chat.reset_failover_cache()`, called from `sync_active_model` and from provider saves (or key the cache by a config fingerprint). | After a provider's key changes, the next failover builds a new client. | S |
| FA-107 | Model discovery returns a typed failure; the route answers 502 `{ok: false, error, cached}`; the page shows an error toast and labels the list as cached. | Discovery against a closed port answers `ok: false`. | S |
| FA-276 | Recall waits for an embedding slot until its own deadline (`acquire(timeout=remaining)`, `recall.py:126`, `:132`). It logs "no embedding slot free" apart from "missed the deadline" (`:778-782`), and sizes the slots to the embedder. | Three concurrent recalls on a 100 ms fake embedder all get vectors. The non-blocking acquire is the control. | S |
| FA-277 | The owner's choice (Stage 0), then a latency benchmark. | N trivial turns against a recorded provider; assert the time to the first token Kazma itself sends. | M |

### Failure results read where returned (§35, §51, `tests/test_failure_results_are_used.py`)

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-104 | `vault_store` raises on failure, or every caller checks it as `accounts.py` does. | With `vault_store` returning False, every connect and refresh reports failure; a source gate against a bare `vault_store(` statement. | S |
| FA-105 | The Discord sender tracks `chunk_sent` per chunk, set only on a 2xx, and ANDs it into `all_sent`. | A parity test across the Telegram, Slack and Discord senders, against a platform that always answers 429: the send returns False. | S |
| FA-102 | Append a whole-range forget-journal entry before `_open`. `/undo` and `/edit` read `ok` and say when memory could not be updated yet, with no exception text. | A locked `_open` leaves a journal entry, and one convergence pass applies it. Today's order is the control. | M |
| FA-103 | A double-checked lock around vault construction. Mark it attempted only on success or on a missing or invalid key. Retry other failures with a backoff. | Concurrent first use returns one vault; a transient failure is retried. | S |
| FA-245 | MCP connection errors become `validation_error` with the cause, logged at WARNING without a traceback. | Each failure kind (no binary, exit, timeout, bad JSON) gives its own message. | S |
| FA-256 | A Test of an unsaved MCP server pages no alert (a quiet flag on `connect_from_config`). | No `ops_alerts.alert` from a test route. | S |

### Settings that reach nothing, or reach too far (§45, §47)

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-125 | Point the tool-timeout field at `agent.tool_timeout_seconds` and show it whether Non-Stop is on or off. Copy a stored value once. Delete the two unread fields. | The dead-control class gate (Stage 1). This key is the control. | S |
| FA-249 | The Skills page's Validate uses `skill_base_dirs` and `activation_integrity`, or the tab goes. It resolves against the CWD today (§10A) and scores a missing folder 70/100. | Validate a bundled skill and a missing folder through the route. | S |
| FA-114 | Apply the log level to Kazma's four loggers and the root handlers, or change both strings to "after a restart". | Set the level through `settings_manager`; a `kazma_core` DEBUG record reaches the file handler, and an INFO record does not at WARNING. | S |
| FA-115 | The proxy hint and the Test both name the two acquisition-mode variables; the card shows the live policy state. | With the policy at `local_only`, `test_proxy`'s answer names both variables. | S |
| FA-126 | Protect `embedding.`, `memory.backends.`, `providers.`, `connectors.*.allowed_*`, `documents.security.`, `documents.storage_root`, `proxy.` and `registry.active_*` from the agent. Better: invert to an allowlist owned by `settings_restore.KEY_RULES`. Refuse `KAZMA_*` names in `embedding.api_key_env`. | In `tests/test_protected_config.py`, every key whose value is a URL, host, variable name or allowlist is protected or declared. `embedding.api_key_env` is the control. | M |
| FA-147 | Pass the HITL config dict at both cached graph-build sites and let the worker's live re-read decide. | Build the holder graph with HITL off, turn it on in the settings store, run `file_write`: an `interrupt()`. | S |
| FA-153 | `/swarm config` with any argument changes global config: `return sub == "config" and bool(arg)`. | An AST walk of the handler finds every `cs.set`/`cs.delete` branch and asserts the admin gate for its word. Today's `("group", "clear")` misses `disable`. | S |

### Language parity (Arabic and English as equals)

Class gate: every user-facing string that a model or a tool produces for a turn is in the turn's language. Run each path in the test below in both languages, with the English-only behaviour as the control.

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-228 | `provenance._holds` judges the stored VALUE the user's message holds, not the predicate the model chose. | An Arabic and an English "remember" request storing an English-named predicate both come out `user_explicit`. | S |
| FA-238 | Deep research detects the topic's language (`arabic.is_rtl_dominant`) when none is given, and passes it to the map, reduce and assemble prompts. The report's headings come from the catalog. | A scripted model sees the language instruction for an Arabic topic. | S |
| FA-230 | The PDF reader prefers the W2 reading when several reproduce a line: numbers stay beside the words they were typed with. | The audit's sentence round-tripped. | S |

### Restart and shutdown

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-261 | One shutdown deadline shared by every stop step, under the guard's 60 s grace. A reload while work ran used 58-60 s, and one drill ended in a kill and a page. | A shutdown with a slow turn, a crawl and a document job finishes inside its deadline. | M |
| FA-262 | A turn stopped for shutdown ends its stream as `stopped_for_shutdown`, not as a normal end. | A reload mid-stream; the client sees the flag. | S |
| FA-264 | A graceful stop releases document-job leases without spending an attempt. | A reload mid-job; the job's attempt count is unchanged. | S |
| FA-138 | On the server's fatal-error path: dump every thread's stack, flush, then `os._exit(1)`. | A child with a planted non-daemon thread exits within 10 s (`sys.exit(1)` hangs). | S |
| FA-137 | `cancel_and_wait(task, timeout=...)` at the three sites. The voice route re-raises its own cancellation. Widen the gate to the whole function. | The widened `tests/test_bounded_cancel.py` fails on the three sites and on a planted cancel-then-gather. | S |
| FA-136 | The turn broker feeds the SSE queue first, then sends to each socket through a bounded call that drops a socket that times out. | A stuck socket does not delay the SSE queue or another socket; no `await <x>.send*(` inside the broker's lock. | S |

### Documents, research and runs

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-232 | Readiness reports the malware-signature date and degrades past 7 days with an ops alert. The setup docs name a freshclam schedule. (The live signatures are 41 days old.) | A stale database file in a test scanner path degrades readiness. | S |
| FA-233 | Run clamd under the guard, as the document broker is run, or say in readiness that each scan pays the 6.5 s load. | Re-measure idle; a scan through clamd under 1 s. | M |
| FA-247 | A document refused by policy is a terminal, non-paging state with no Retry, in the run-kind map. | `tests/test_run_kinds_declared.py` maps a policy `rejected` to no Retry. | S |
| FA-266 | Research admission and storage are sized together, so an admitted run cannot be refused for storage later. | Admit at the storage limit; the run is refused at admission, not mid-run. | S |
| FA-267 | `_copy_assets` skips nested `.db`, `-wal` and `-shm` files (the database copy already has them). | A backup of a data dir with a nested live store holds one consistent copy. | S |
| FA-254 | Declare the `.pre_*` repair copies as `machine` in `store_registry`, and add `.pre_reopen` to the erasure horizon. | Every suffix a repair script writes is declared. | S |
| FA-113 | `search_research` dispatches with `workers=["auto"]` and stops registering a hard-coded `researcher`. | Dispatch through the real tool with zero and two registered workers; the registry is unchanged. | S |

### Text the product tells about itself

| ID | Fix | Gate (negative control) | Effort |
|---|---|---|---|
| FA-116 | Say "every Kazma database, chats, settings, documents and the workspace every 6 hours; Postgres has its own dump; search vectors are rebuilt, not backed up", in both languages. | Any hours in a backup string equals `_BACKUP_EXPORT_INTERVAL_HOURS`. | S |
| FA-117 | Every message-sending tool is profiled `send_outbound` with a declared target field the resolver reads. | Enumerate the tools that send a message; each is `send_outbound` with a target field. `dispatch_notification` is the control. | M |
| FA-118 | Correct the six product-knowledge lines; derive the always-ask sentence from `ALWAYS_HITL_TOOLS`. | Every tool name in product knowledge exists in the registry. | S |
| FA-124 | Translation uses the configured model by default; MyMemory is an opt-in backend with a POST body and a length cap; the result is fenced. | An egress inventory of literal hosts in native-skill tools, each declared. | S |
| FA-128 | One helper, `outbound_flags.py`, turns link previews off on every send and edit of model or tool text, on all three chat apps. | An AST gate that every send and edit payload goes through the helper. | S |
| FA-135 | One `QueueHandler` on the root logger; file and stream handlers run on a `QueueListener` thread, flushed at shutdown. Log I/O stalls the loop today. | With a file handler that sleeps 2 s a record, a 50 ms loop heartbeat is never more than 200 ms late. | M |
| FA-140 | One `telegram_units()` length for every split and check; on "too long", split again. | A property test over text with astral characters: every chunk is at most 4096 UTF-16 units and the chunks rejoin. | S |

## Stage 3: the Low findings

Compact, each with its fix, its gate and the effort. Each gate's negative control is the current code at the site named.

| ID | Fix | Gate | Effort |
|---|---|---|---|
| FA-108 | Log "persisted" only when `ok`; "NOT persisted" at WARNING otherwise. | A refused save logs no "Reply persisted". | S |
| FA-109 | Append the recovered task only when `persist_task` returns True; else WARNING and `record_swarm_failure("task_write")`. | A refused write is not counted recovered. | S |
| FA-110 | WARNING with the provider name; the model-fallback notice when the agent keeps a client for another key. | A raising `_sync_agent` logs a WARNING. | S |
| FA-111 | Key the file-read note cache by thread id; clear only that thread's entries. | Two threads reading one file both get it without the note. | S |
| FA-119 | Reword both strings to the code's zone precedence. | The precedence sentence names `cron.timezone` first. | S |
| FA-120 | State the real time bound, output cap and importable packages. | The description's numbers equal the module constants. | S |
| FA-121 | Merge the rules still needed into the shipped prompt; delete the rest. | Each rule a module documents as always in the prompt is in the prompt built from `kazma.yaml`. | S |
| FA-122 | "Relevant library text is added to a prompt when it matches the question." | String review. | S |
| FA-123 | Reword both strings after a closer read. | String review. | S |
| FA-127 | Read the rate limit per message from the cached store, or rebuild on the §48 change notice. | Set the key; the next message uses the new limit. | S |
| FA-130 | `html.escape(message)` on the page. | Render with `<script>` in the message: escaped. | S |
| FA-131 | `safe_error` per readiness check; detail in the log. | A failing check's answer holds no exception text. | S |
| FA-132 | Throttle the unauthenticated webhook WARNING, or do not mount the route in polling mode. | 100 POSTs, at most one WARNING per interval. | S |
| FA-133 | OAuth state in a signed short-lived cookie; `hmac.compare_digest`. | Two interleaved sign-ins both succeed; another browser's state is refused. | S |
| FA-134 | Refuse on `Content-Length` before parsing; stream the multipart body with a counting limit. | An oversized body is refused with no temp file. | S |
| FA-139 | Wrap the blocking file calls in async tools in `asyncio.to_thread`. | A source gate over async tool bodies for `open(`, `write_text(`, `read_bytes(`, `shutil.rmtree(` outside `to_thread`. | S |
| FA-141 | One sweep entry per append-only store with a declared age (decided rows only); an `ERASURE_HORIZON` line for `hitl_gates.db`. | `store_registry` requires a retention or a reason for every store. | M |
| FA-142 | A one-off VACUUM of `settings.db` after the Knowledge move; `journal_size_limit` in `apply_sqlite_pragmas`. | A store opened through the helper has a `journal_size_limit`. | S |
| FA-143 | Reuse the rotate helper in `GuardLog`, keeping the readers' rotated-sibling handling (§27C). | 3 MB written leaves a live file under 2 MB and one sibling. | S |
| FA-144 | One `atomic_write_text` (temp, flush, fsync, `os.replace`) for every manifest write. | No `write_text` of a `manifest.json` outside the helper. | S |
| FA-145 | An LRU cap (or a TTL past the cooldown) on both throttle maps. | 10,000 keys leave each map at its cap. | S |
| FA-146 | Content-addressed attachment names with a reference count, or a retention rule tied to the chats that name the file. | One file uploaded twice leaves one copy. | M |
| FA-148 | Correct §25B, or have both abort paths call `pause_long_task()` as a Partial does. | After `/abort` with `/unrestricted` on, the long task is not active. | S |
| FA-149 | Stamp the last run through `cadence.py` and sleep only the rest. | Both loops added to `tests/test_scheduler_cadence.py`. | S |
| FA-150 | `await asyncio.to_thread(_distinct_tenants, ...)`; add it to `_LOOP_STALL_HELPERS`. | The §35 helper gate fails on the three current calls. | S |
| FA-151 | State §2's exception: `_gateway` holds `delivery_target` for reminders and expired-approval delivery, and nothing else. | Optionally: `_gateway` holds no keys beyond the declared set. | S |
| FA-152 | Correct each AGENTS.md sentence. | A doc gate that every backticked `module.function` in AGENTS.md resolves. | S |
| FA-154 | Correct each name (the protected-file list first: `_serverGates` names nothing). | The doc-name gate of FA-152. | S |
| FA-202 | One WARNING when the pool goes away and one recovery line when it is back, not a line per call. | A simulated outage of N calls logs two lines. | S |
| FA-203 | Prune stall dumps past a count or age. | 200 dumps leave the cap. | XS |
| FA-204 | Prune old-format document-store backups with the rest. | An old-format backup past retention is removed. | XS |
| FA-205 | GC collects a blob row whose file is gone. | A planted orphan row is collected. | S |
| FA-208 | The daily digest at a fixed time of day, not a drifting interval. | A virtual clock across restarts sends at the set time. | S |
| FA-209 | Pin every GitHub Action by commit SHA. | Every `uses:` is a 40-hex SHA. | XS |
| FA-217 | `control_plane_store_targeted` does not refuse a user's own bare `*.db` in the workspace. | A workspace `app.db` passes; Kazma's stores still refuse. | S |
| FA-219 | Unauthenticated callers of the auth status see only the mode and whether sign-in is required. | An unauthenticated GET holds no other field. | S |
| FA-224 | Skip the clean-up write under `writes_suppressed()`. | A read-only diagnostic makes no write. | XS |
| FA-225 | The CLI prints UTF-8 and answers `--version`. | `kazma --version` exits 0 on a cp1252 console. | XS |
| FA-226, FA-227 | No change (recorded as by design). | — | — |
| FA-229 | The Knowledge footer from the catalog in the turn's language; `safe_error` for the tool's exception. | An Arabic turn's footer is the Arabic string. | S |
| FA-231 | Parse `<path>: <name> FOUND` from the right. | Both ClamAV name shapes and a dotted Windows path. | XS |
| FA-234, FA-241 | One error envelope for the document routes. | Every document route's error has `ok`, `error`, `code`. | S |
| FA-235 | A content rubric for the research quality score. | A scripted report scores by its content, not its length. | M |
| FA-236 | A swarm dispatch that dispatched nothing answers non-200. | A dispatch with no capable worker answers 4xx/5xx. | S |
| FA-239 | A smaller swarm worker prompt. | Its token count under a set bound. | S |
| FA-240 | Count the sources acquired, not the sources planned. | A run with a refused source counts it out. | S |
| FA-242 | One HTTP client per Gmail listing with bounded concurrency (the class: Graph, Google and Outlook Calendar too). | A mock transport counts connections for a 20-message listing. | S |
| FA-243 | Model behaviour (repeated identical `email_list` calls); no code change. | — | — |
| FA-244 | `splitlines()` without keepends in the unified diff. | The exact unified text. | XS |
| FA-251 | Rules for `agent.max_iterations` and `agent.language`; a restore refuses unknown keys. | Every `kazma.yaml` key has a rule. | S |
| FA-253 | Journal a denied call as a refused row. | A denied call leaves a refused receipt. | S |
| FA-257 | Record the transition in `hitl_gates`; the commitment pending metric becomes a gauge. | A decided card's metric falls. | S |
| FA-260 | The LLM ledger reads the thread from the run config; research passes `research:<session id>`, Diagnose its incident. | Every `resilient_chat` call names its work. | S |
| FA-265 | A `wait_or_shutdown` helper for the background loops' sleeps. | Shutdown with a loop asleep returns at once. | S |
| FA-269 | The restore docs print `password_file()`. | The docs' path equals the helper's. | XS |
| FA-270 | Restore only the backup's own folder, and leave no `-wal`/`-shm` beside it. | A restore over a file with a stale WAL reads the backup. | S |
| FA-272 | A restriction check of `restic.pass`'s ACL in the hardening report (decision in Stage 0). | An open ACL fails the check. | S |
| FA-274 | A malformed request body answers 400. | `{not json` to any JSON route answers 400. | S |
| FA-275 | Answer 404 on `not_found`. | An unknown id answers 404. | XS |
| FA-278 | Page the chat session list and read it without the store lock. | 10 concurrent list calls within twice a single call's time. | S |

## Weak-spot roadmap

The 128 findings cluster into ten themes; the two after them came from the owner's questions on 2026-11-02 (why no dependency notice ever arrived). Each theme below is the standard Kazma should hold, and the work to get there beyond the single findings above.

| # | Theme | Standard | Findings it would have caught | Work | Effort |
|---|---|---|---|---|---|
| 1 | A result that says whether it worked is read where it is returned | No bool, `ok` or `sent:` answer is dropped by a product caller; every refusal reaches the user as what it is. | FA-101, 104, 105, 108, 109, 245 | Extend `tests/test_failure_results_are_used.py` from backup, migration and send functions to every function that returns `ok`/bool on a write (vault, chat store, swarm store, update). | M |
| 2 | Outage and recovery are drilled, not described | Every recovery the docs promise (database outage, reload mid-work, restore, migration from any folder) has a drill that runs the real thing in CI, with the old code as the control. | FA-259, 261-264, 268, 252, 255 | A drills job: real Postgres stopped and started, a graceful reload with a crawl, a document job and a turn running, a restic restore into a scratch dir followed by a recall. | L |
| 3 | Dependency floors are security floors, and deploys install them | No installed package below a floor; every advisory with a fix has a floor at the fix; a deploy that changes `pyproject.toml` runs the update. | FA-258, 248 | The OSV floor gate on a recorded answer; the deploy recipe gains the update step; the boot alert links the command. | S |
| 4 | Every setting has a reader | A control on a page changes what it names, or it is not shown. | FA-250, 125, 249, 114, 147 | The class gate over every Settings write (Stage 1), extended to the TUI's writes. | S |
| 5 | The privacy ledger reaches every writer | Nothing derived from a chat kept out of memory (a fork, a lesson, an import, a summary) reaches memory or a prompt. | FA-246, 212, 228 | The writer enumeration gate (Stage 1); a lesson's source turn recorded so a forget takes it back. | M |
| 6 | Arabic and English as equals on every path | The same request in either language gets the same provenance, the same language back, and the same rendering. | FA-228, 238, 229, 230, 004 | A paired-language suite: each user-facing tool and pipeline run once per language through the real path, with the outputs compared. | M |
| 7 | Latency within budget | First token p50 ≤3 s and p95 ≤8 s for an ordinary turn; the session list within the page budget at 10 clients. | FA-277, 278, 276, 233, 242 | The owner's model decision; per-turn tool selection; the latency benchmark in CI on a recorded provider. | M |
| 8 | Log and file I/O off the loop | No log write, file read or sleep blocks the event loop. | FA-135, 139, 136, 137 | The `QueueHandler` logging; the async-tool I/O gate; bounded socket sends in the broker. | M |
| 9 | Append-only stores are bounded | Every store has a declared retention or a reason, and the sweep enforces it. | FA-141, 142, 145, 146, 203, 204 | `store_registry` requires a retention for every store; one sweep entry each. | M |
| 10 | CI is the first line again | Every push runs the full gate set on Linux and the Postgres job. GitHub Actions has been locked by billing since 2026-10-14. | Every finding a Linux or Postgres run would have shown first | The owner restores Actions billing; then a one-off run of every job on `main`, with the red ones fixed before Stage 1 lands. | XS (owner) |
| 11 | A dependency advisory reaches the owner the day it is published | An advisory against an installed package is told within a day, with the fixed version and the command, whether or not a commit has raised the floor yet. The boot check (`install.requirements_unmet`) speaks only at a start and only once a commit raises a floor: the owner, using Kazma daily, never saw a notice (2026-11-02). | FA-258 | A daily maintenance sweep asks OSV about the installed versions (the report's own `audit_installed`, off the loop, bounded, quiet when OSV cannot answer) and raises one ops alert per new advisory naming the package, the fixed version and `python -m kazma_cli update --reinstall -y`; reviewed advisories (`REVIEWED_ADVISORIES`) stay quiet. Gate: a recorded OSV answer with a new advisory alerts once, a repeat does not, OSV down raises nothing. | M |
| 12 | An unmet requirement stays in front of the owner until it is fixed | The daily digest names every unmet requirement ("Needs attention") until an update clears it; an alert's cooldown never hides it for good. | FA-258 | The digest reads the boot check's last result (stored, not only alerted) and lists each package with the command; cleared by the next boot that finds none. Gate: an unmet requirement appears in every digest until cleared; the old digest as control. | S |
