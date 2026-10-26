# Full code audit — 2026-10-26

HEAD `20dc1797`. A read-only audit: nothing in the repository or the live
install was changed. Nine auditors each took one slice in parallel, and every
High finding (and most Medium ones) was then checked again against the source
before it went in this report.

**Verification column** in every table:
- **V**: re-read in the source at HEAD for this report.
- **R**: the auditor reproduced it with a scratch script (private data dir,
  no network).
- **A**: reported by the slice auditor from reading the code, not re-checked
  separately.

Confidence is the auditor's, lowered where the re-check found a narrower
condition.

---

## 1. Executive summary

**Overall health: Medium risk.**

The core safety architecture holds up under audit:
- HITL gates and the gate registry
- the commitment layer
- the SSRF/DNS-pinned acquisition stack
- the cron and X outboxes
- the document sandbox
- memory's forget machinery

These match AGENTS.md almost everywhere, and the auditors found no SQL
injection, no XSS sink and no path-traversal hole in the main surfaces.

The risk sits at the edges of those systems, in two kinds of place:
- **Side doors that bypass the hardened path:**
  - a t.co resolver that is a raw `httpx.get`;
  - a tool executor that strips tool arguments;
  - a `config_read` tool that returns nested secrets.
- **Failure paths that report success:**
  - a migration rollback that deletes live databases;
  - sends and backups whose return values are ignored;
  - recall failures that read as "nothing remembered".

A third, systemic class repeats across six slices: blocking settings or
chat-store I/O on the event loop, reached through wrapper functions. The
name-based loop-stall gate cannot see those calls. This is the same failure
class as the 2026-09-28 outage restart.

**Scope.** 1,204 product files, about 459,000 lines.

| Package | Files | Lines |
|---|---|---|
| kazma-core | 743 | 240,816 |
| kazma-ui | 210 | 133,302 (incl. 20,740 i18n catalog, 13,026 templates) |
| kazma-gateway | 73 | 26,081 |
| kazma-skills | 59 | 12,257 |
| kazma-tui | 33 | 9,332 |
| kazma-cli | 13 | 6,263 |
| scripts/ | 73 | 31,158 |

Stack: Python 3.11 (FastAPI, LangGraph, Textual), Alpine.js front end, SQLite
and PostgreSQL, Chroma, Docker document workers.

There are no TODO/FIXME/XXX/HACK markers anywhere in product code.

**Coverage, honestly.** This is not a zero-skip line-by-line read. Each
auditor read the riskiest 20–35% of its slice in full and covered the rest by
targeted grep or AST scans: route handlers, `subprocess`, f-string SQL,
`innerHTML`/`x-html` sinks, `except` sites, `httpx` calls and lock sites.
Section 4 lists what was not read. The un-read code is mostly where earlier
area audits (2026-10-13/15) already went deep: much of `documents/ocr`,
`web_acquire`, `x_api` drafting, `memory` benchmarks and the TUI screens.

**Top 3 risks needing immediate intervention**

1. **AUD-001: the chat tool executor deletes tool arguments named `action`,
   `resource`, `user_id` or `division`.** This affects every tool on every
   chat surface. `email_send(action="draft")` is approved as a draft and sent
   as an email, and memory admin is broken from chat.
2. **AUD-002: any X user who @mentions the account makes the server fetch a
   URL of their choice.** It is a blind SSRF through a t.co redirect: no
   public-address check, no body cap, no total deadline. It runs before the
   summoner allowlist, and it can stall the whole mention poller.
3. **AUD-003 and AUD-004: `kazma migrate import` can destroy the target's
   data on a failure path.** A failed pre-swap backup is ignored, then the
   rollback unlinks the live database. Separately, the vault key is rewritten
   and `vault.db` moved aside before checks that can still abort the import.

Close behind: **AUD-005** (`config_read` returns provider keys and the
database password to the model with no approval) and **AUD-006/007** (chat
streaming and every HTTP request do settings or chat-store writes on the
event loop: a Postgres outage again becomes a guard restart).

---

## 2. Detailed audit findings

Where a slice ID is given in parentheses, it is the auditor's own ID.
Critical/High/Medium findings get the full write-up. Low findings are listed in
compact tables at the end of each category.

### 🐛 1. Bugs & functional flaws

**[AUD-001] [Severity: High] [Confidence: High] [V] `kazma-core/kazma_core/mcp/manager.py:2305-2308`** (SEC-01)
- **Evidence:**
  ```python
  user_id = arguments.pop("user_id", arguments.pop("_user_id", None))
  division = arguments.pop("division", arguments.pop("_division", None))
  resource = arguments.pop("resource", arguments.pop("_resource", None))
  action = arguments.pop("action", arguments.pop("_action", None))
  ```
  `UnifiedToolExecutor.execute` is the executor of the chat graph:
  - `agent_runner.py:260`: `self.tools = UnifiedToolExecutor(...)`
  - `agent_runner.py:934, 1109`: `tool_executor=self.tools`
  - `app.py:1963`: `tool_executor=self.agent.tools`

  These pops run before routing to the local registry or MCP.
- **Root cause:** RBAC context keys reuse ordinary parameter names, and the
  RBAC block (see AUD-064) only acts when the *model* supplies `user_id`.
- **Impact:**
  - `email_send` (`email_manager/tools.py:206`, `action: str = "send"`) asked
    with `action="draft"`: the HITL card shows `draft`, the human approves,
    and the tool receives no `action` and **sends**. `action="reply"` becomes
    a fresh send. Both are irreversible.
  - `memory_admin(action=...)` always falls back to its help text.
  - MCP tools with an `action` or `resource` parameter lose it.
- **Remediation:**
  - Pop only underscore-prefixed internal keys, and better, take the
    principal from a ContextVar.
  - Never remove a key named in the tool's JSON schema.
  - Add a test: `email_send(action="draft")` through `UnifiedToolExecutor`
    reaches the tool unchanged. Use the old pop as the negative control.

**[AUD-003] [Severity: High] [Confidence: High] [V] `kazma-core/kazma_core/migration/importer.py:401-406, 639-666`** (INF-01)
- **Evidence:** In the swap, `if dest.exists(): _backup_one(dest, backup_dir / arc_name)` discards the return value.
  `memory/backup.py:41-62` returns `False` on failure and unlinks its
  half-written output. In `_rollback_sqlite_swap`:
  ```python
  if bak.exists(): ...; continue
  # File did not exist live before the swap — drop the new one.
  dest.unlink(missing_ok=True)
  ```
- **Root cause:** A failed backup does not stop the import, and the rollback
  reads "no backup file" as "the file did not exist before".
- **Impact:** A full disk, or a locked live database during `--force`, makes
  the `memory_state.db` backup fail. The swap replaces the live file anyway.
  If a later step fails, the rollback deletes it: the original is gone both
  live and in the backup. Without a rollback, the old data is overwritten with
  no backup kept. This contradicts AGENTS §18C ("never corrupt the target
  mid-flight").
- **Remediation:**
  - Abort before the swap when any pre-swap backup returns `False`.
  - Record the set of destinations that existed, and unlink only those that
    did not.
  - Add a test that makes `_backup_one` fail.

**[AUD-004] [Severity: High] [Confidence: High] [V] `migration/importer.py:335-388`, `migration/vault_pairing.py:142-157`** (INF-02)
- **Evidence:** Step 5, `sync_vault_key(...)`, moves the target `vault.db`
  aside and writes the bundle's `KAZMA_VAULT_KEY` into `.env`. Step 6 can then
  return early on each of these, each saying "Live SQLite files were not
  modified":
  - `_stage_legacy_knowledge` failing;
  - "bundle contains a Postgres dump but the target backend is SQLite";
  - "KAZMA_DATABASE_URL is not set".
- **Root cause:** An irreversible step runs before the preflight checks and
  before the backup.
- **Impact:** A `--reset-vault-key` or empty-key import that aborts in step 6
  leaves the target with the bundle's key and its own vault moved away. Every
  secret is unreadable at the next boot, and the report says nothing changed.
  This breaks §18A (the key and the vault "travel as an atomic pair").
- **Remediation:**
  - Run the vault sync after every preflight check and after a successful
    swap.
  - Otherwise, record the original `.env` line and the vault location, and
    restore them on every early return and rollback.

**[AUD-008] [Severity: High] [Confidence: High] [V, R] `kazma-core/kazma_core/tools/file_apply_patch.py:192-239`** (AGT-02)
- **Evidence:** Refusals made before any write are *returned* as strings:
  - `"Error: No path provided."`
  - `denied_message(...)`
  - `"Error: File not found: ..."`
  - `f"Error: {exc}"` for `PatchError`
  - `"No changes applied ..."`

  The registry marks any error from a non-read tool as `effect_uncertain`
  (`tool_registry.py:755-758`). `execute_effect` then raises `EffectUncertain`
  and the tool worker sets `turn_failed`.
- **Root cause:** AGENTS §30 G requires `RefusedBeforeEffect`.
  `tests/test_tools_refuse_before_effect.py::DELEGATES` lists `file_write` and
  `file_apply_patch_set` but not `file_apply_patch`, and the refusals sit two
  calls down from the registered body.
- **Impact:** The most common patch mistake, an `old_string` that does not
  match, ends the whole turn as "effects unknown, must not be repeated"
  instead of letting the model correct the patch. The auditor reproduced it.
- **Remediation:**
  - Raise `RefusedBeforeEffect` for every pre-write refusal.
  - Add `"file_apply_patch": ["kazma_core.tools.file_apply_patch:_file_apply_patch_sync"]` to `DELEGATES`.
  - Make the gate follow delegates transitively.

**[AUD-009] [Severity: High] [Confidence: High] [V] `kazma-core/kazma_core/documents/ingestion.py:1868-1889`** (DOC-02)
- **Evidence:** The OCR-text branch of `convert_document` calls
  `self.service.generate(..., export_dir=None)` with no `document_id` or
  `version_id`, although `service.generate` accepts both (`service.py:264-265`).
  `operations.py:482` persists an artifact only
  `if scope.document_id is not None and scope.version_id is not None`.
- **Impact:**
  - Converting a scanned document to Word or Markdown returns an
    `artifact_id` that was never stored.
  - The Web download answers 404.
  - The agent tool `_saved_copy` gets "Artifact is unavailable" and hands the
    model an id and no file. That is the exact 2026-10-22 failure §19 G5 says
    is fixed.
  - GC later deletes the orphaned blob.
- **Remediation:** Pass `document_id=doc_id, version_id=version.id`. Add an
  end-to-end test: OCR'd document → convert docx → `resolve_artifact_blob`
  succeeds.

**[AUD-010] [Severity: High] [Confidence: High] [V, R] `kazma-core/kazma_core/memory/consolidator.py:421-446`, `belief_extractor.py:707-760`** (MEM-01)
- **Evidence:** `_run_turn_memory` checks only the whole-chat keep-out:
  ```python
  if not chat_remembered(session_id, ...): return True
  ```
  It then runs `_v2_extract_sync` on the turn. Neither module consults the
  per-turn forget ledger: there is no `refuses_write` or `forgotten_turns`
  call in either file.
- **Root cause:** `forget.keep_turn_out` writes a per-turn ledger row, but
  only the episode writer reads it.
- **Impact:** The user says "Please forget that I live in Paris".
  `memory_invalidate(forget_source_turn=true)` forgets the fact and keeps the
  turn out. The post-turn heuristic pass then stores `lives_in Paris` again
  at `user_stated` trust, so it blocks later corrections, and
  `forget.enforce_ledger` sweeps only episodes. This contradicts §15 I.
  Reproduced.
- **Remediation:**
  - Skip extraction when the ledger refuses the turn, and check again inside
    the mutation.
  - Extend `enforce_ledger` to the facts of ledgered turns.
  - Add a gate: a ledgered turn leaves no current belief with its
    `source_session` and `source_turn`.

**[AUD-011] [Severity: High] [Confidence: High] [V] `kazma-ui/kazma_ui/static/js/voice.js:308-310, 495-520`** (UICLI-01)
- **Evidence:** The only teardown hook is
  `window.addEventListener('pagehide', function() { stopTTS(); })`.
  `voice.js` is a page script: `nav.js:52-58`, `isSoftNavPageScript`, re-runs
  it on each visit with a new closure. `destroyChatMouth` does not stop
  streaming.
- **Impact:** After you turn on Live voice and leave Chat by the sidebar:
  - The microphone keeps streaming to `/ws/voice`.
  - Its transcripts keep starting agent turns while you are on other pages.
  - Coming back shows the button off, and only a full reload stops the mic.
- **Remediation:** Push a teardown onto `kazmaOnSoftNavLeave` that calls
  `stopStreaming()`, `stopRecording()` and `stopTTS()`. Add an e2e test that
  arrives by the sidebar, as AGENTS UI conventions require.

**[AUD-012] [Severity: High] [Confidence: High] [V] `kazma-ui/kazma_ui/static/js/swarm.js:129-160`** (UICLI-02)
- **Evidence:** `init()` adds two `document` click listeners and one keydown
  listener with no guard. `init()` runs on every page-script run (line 3119).
  `destroySwarmMouth` (3099-3113) removes none of them.
- **Impact:** Swarm → another page → Swarm, then press Retry. Two
  `POST /api/swarm/tasks/{id}/retry` go out, creating and running **two** new
  tasks with duplicate side effects. Approve, Reject, Cancel, Start and Stop
  fire twice: a success toast, then a 409 error toast.
- **Remediation:** Bind once per document with a window-level flag whose
  handler calls the current `window.KazmaSwarm`, or pass an `AbortController`
  signal and abort it in `destroySwarmMouth`. The same pattern is in AUD-030.

**[AUD-013] [Severity: High] [Confidence: High] [V] `scripts/service/kazma_guard.py:3379-3425`** (GW-01)
- **Evidence:** `_stop_recorded_child` reads `child_pid` from the state file,
  checks only `_pid_alive(pid)`, then runs `taskkill /PID <pid> /T /F`.
  The other two reapers check `_recorded_child_is_ours` first (lines 1045,
  1884).
- **Impact:** After a reboot or a hard guard stop, `child_pid` stays in the
  state file and Windows reuses the PID. An operator then runs
  `--pause --stop` or `--reload` with no live guard, and an unrelated process
  tree (an editor, a browser) is force-killed. This contradicts §39 ("A
  recorded PID is verified before it is reaped").
- **Remediation:** Return 0 unless `_recorded_child_is_ours(pid, state, log)`
  is true. Add a reused-PID test.

**[AUD-014] [Severity: Medium] [Confidence: High] [V, R] `kazma-core/kazma_core/llm_provider.py:1167-1181`** (SEC-03)
- **Evidence:** The final `else` of the non-streaming `chat()` error handling
  raises `LLMError(..., transient=False)` for every status. That includes
  500/502/503. `_http_status_is_transient` (line 45) says 5xx is transient
  "(AGENTS.md §3)", and `chat_stream` and `AnthropicProvider` agree with it.
- **Impact:** A provider 503 inside `resilient_chat` fails fast, with no retry
  and no failover. That covers swarm workers, research, X model calls and
  task-model calls. The chaos experiment passes because it injects a
  transient error that the real provider path never raises.
- **Remediation:** `transient=_http_status_is_transient(status_code)`, plus a
  MockTransport 503 test.

**[AUD-015] [Severity: Medium] [Confidence: High] [A] `kazma-core/kazma_core/model_registry.py:658-665`** (SEC-06)
- **Evidence:**
  ```python
  effective_model = model or entry.get("model") or self._active_model or default_model_for(provider_name)
  ```
  Provider entries store `models` (a list), never `model`, so this falls
  through to the *active* model, whoever's it is.
- **Impact:** A swarm worker pinned to `provider="anthropic"` with no model,
  while `deepseek-chat` is active, sends `deepseek-chat` to Anthropic. The
  call gets a 404 classified as permanent. This contradicts §1.
- **Remediation:** Use `_active_model` only when
  `find_provider_for_model(_active_model) == provider_name`. Otherwise use the
  provider's first visible model.

**[AUD-016] [Severity: Medium] [Confidence: High] [V, R] `kazma-core/kazma_core/swarm/task_control.py:171`, `runs/adapters/swarm.py:100`** (SWX-02)
- **Evidence:** `new_metadata["retry_of"] = task_id` records the parent, not
  the root. The lineage is built as `"swarm_task:" + (retry_of or id)`.
- **Impact:** With a runbook on, the "one automatic step per lineage a day"
  limit (§52) resets on every human retry of a retry. During a provider
  outage that becomes an unbounded automatic retry loop. Reproduced: in A → B
  → C, C's lineage is not A.
- **Remediation:**
  `retry_of = original.metadata.get("retry_of") or task_id`. Add a three-hop
  test.

**[AUD-017] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/cron/store.py:305`, `cron/scheduler.py:872`** (SWX-03)
- **Evidence:** `DELIVERY_LEASE_S = 60.0` is never renewed. Each poll's
  `recover_deliveries(owner=self._owner)` selects
  `state='sending' AND (lease_owner != ? OR lease_until < ?)`, which also
  matches this scheduler's own live sends.
- **Impact:** A Telegram send that retries past 60 s (30 s read timeout × 3,
  plus 429 waits) is marked `unknown`. The job becomes `delivery_unknown` and
  an alert says it "may not have arrived" when it did. When the send really
  failed, its retries are lost.
- **Remediation:** Track in-flight delivery ids, as `_in_flight` does for
  fires, and exclude them from recovery when not booting. Alternatively,
  renew the lease around `_send`.

**[AUD-018] [Severity: Medium] [Confidence: High] [A] `runs/decide.py:82`, `runs/notify.py:127,175`, `runs/diagnose.py:107`** (SWX-04)
- **Evidence:** `except (RuntimeError, OSError, ValueError)` around claimed
  work. `sqlite3.Error`, `httpx.HTTPError`, `KeyError` and `TypeError` all
  escape, leaving rows in `acting`, `notify='sending'` or
  `diagnosis_state='running'`.
- **Impact:** One "database is locked" on a decision or a card send leaves an
  incident that can neither be acted on nor expire. A new incident for the
  same run is blocked by the unique index. At boot, `recover()` falsely
  records "Kazma stopped while the action ran".
- **Remediation:** Catch `Exception` (re-raising `CancelledError`) and record
  the failure with `finish_*(ok=False)`.

**[AUD-019] [Severity: Medium] [Confidence: High] [A] `cron/scheduler.py:883`, `runs/adapters/cron.py:58-63`** (SWX-05)
- **Evidence:** A fire's lease is fixed at 600 s and never renewed, while
  `cron.task_budget_seconds` allows up to 3600. The adapter judges "owner" by
  `lease_until > now`, and `dead_after_s=120`.
- **Impact:** A healthy 15-minute task fire gets a "stuck: nothing is
  running it" incident card with a Cancel button.
- **Remediation:** Set the lease to the budget plus a margin, or renew it.
  Judge a running job by its claim time.

**[AUD-020] [Severity: Medium] [Confidence: High] [V] `kazma-core/kazma_core/backup/universal.py:888-922`** (INF-04)
- **Evidence:** Inside `_backup_lock`, the loop that deletes every folder
  without `manifest.json` runs *before* the "already running" check.
- **Impact:** A manual "Back Up Now" is mid-copy when the 6-hour queued
  backup arrives. The queued run deletes the running backup's folder, then
  returns "already running". The first run finishes and records databases as
  ok whose files were deleted.
- **Remediation:** Decide "running or crashed" first, and clean up only when
  no live run exists.

**[AUD-021] [Severity: Medium] [Confidence: High] [A] `backup/universal.py:519-546, 1036, 1048`** (INF-07)
- **Evidence:** `_robust_copytree` logs `OSError` (including
  `PermissionError`) at DEBUG and does not count it. The summary is always
  `"ok": True`, and `_prune` counts failed backups toward retention.
- **Impact:** A locked document blob is silently missing from every backup.
  Seven consecutive partial backups rotate out the last complete one. This
  contradicts §27 ("A backup that logs 'complete' … is the same as no
  backup").
- **Remediation:** Count failures other than `FileNotFoundError` into the
  manifest and the gap alert. Set `ok=False` on a failed database or file.
  Have `_prune` always keep the newest complete backup.

**[AUD-022] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/documents/ingestion.py:506-540`, `jobs.py:296-313`** (DOC-03)
- **Evidence:** In `recover_interrupted_intake`:
  - It selects every `received`, `quarantined` or `validating` job, with no
    owner or lease filter.
  - It catches only `InvalidJobTransitionError`, but losing the
    compare-and-set raises `StaleJobUpdateError`. That is a sibling class:
    both subclass `JobConflictError`.
- **Impact:** A second process starting `start_workers()` (the CLI, the
  agent tool's lazy service, another replica) while the server validates an
  upload can do two things:
  - re-run validation, so one side raises;
  - fail `start_workers`, so that process has no worker pool, or fail the
    upload although the document was ingested.

  This contradicts §19 B2.
- **Remediation:** Catch `JobConflictError`, and skip jobs whose intake
  reservation is held by a live owner.

**[AUD-023] [Severity: Medium] [Confidence: Medium] [A] `documents/retention.py:311-349`, `ingestion.py:1286-1293`** (DOC-04)
- **Evidence:** `_sweep_scratch` deletes `.stage/<dir>` by mtime, older than
  `gc_grace_seconds` (3600). A long durable OCR run holds one staged copy and
  never touches it.
- **Impact:** A scan of more than about 110 pages runs past an hour. A GC
  pass deletes its source mid-job, the next batch's `sha256_path` raises, and
  the job restarts from page 1, possibly ending in dead letter.
- **Remediation:** Touch the stage between batches, or register live stage
  directories with GC.

**[AUD-024] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/documents/worker.py:428-435`** (DOC-05)
- **Evidence:** `_heartbeat`:
  `except Exception: logger.error(...); cancellation.set(); return`.
- **Impact:** One "database is locked" stops lease renewal, and the stage
  keeps running. The lease lapses mid-OCR, another worker re-claims the job,
  and two parsers run on the same document. Alternatively, the next batch
  cancels and all OCR progress is lost.
- **Remediation:** Retry on store errors until the lease is actually at risk.
  Stop only on `JobConflictError` or an expired lease.

**[AUD-025] [Severity: Medium] [Confidence: High] [A] `documents/engines/docx.py:149-153`, `engines/html.py:73-80`, `engines/pdf.py:235-241`** (DOC-09)
- **Evidence:**
  ```python
  for block in model.blocks:
      try: self._render_block(document, block)
      except Exception: logger.debug(...)
  ```
- **Impact:** A table or paragraph that hits a render bug is missing from the
  generated document, with no warning and `artifact_ready`. This contradicts
  §19's `DocumentRenderError` rule ("never a document that looks successful
  but is wrong").
- **Remediation:** At least add a warning per dropped block. Better, raise
  `DocumentRenderError` for body blocks.

**[AUD-026] [Severity: Medium] [Confidence: High] [V, R] `kazma-core/kazma_core/memory/recall.py:318-329`** (MEM-02)
- **Evidence:** `except Exception: logger.warning(...); return RecallResult([], [])` returns no `degraded` flag.
- **Impact:** With a locked or corrupt memory database, or a Postgres-primary
  outage, `memory_search` answers "No relevant memories found." and the model
  tells the user it remembers nothing. The existing test
  (`test_memory_tools_fenced.py::test_a_recall_failure_is_not_an_empty_result`)
  patches `recall` to raise, which the real function never does. This
  contradicts §26 G and M11.
- **Remediation:** Return `degraded="recall_failed:<code>"` from every failure
  branch. Rewrite the test to break `_recall_episodes` instead.

**[AUD-027] [Severity: Medium] [Confidence: High] [V, R] `kazma-core/kazma_core/memory/belief_mutation.py:567-569`** (MEM-03)
- **Evidence:**
  ```python
  except Exception:
      logger.debug("[belief_mutate] mutation failed", exc_info=True)
      return {"action": "noop", ...}
  ```
- **Impact:** With a locked database, facts are lost silently:
  - Turn reconcile's cursor moves past turns whose facts were never written,
    against M27's "a locked database is never a verdict".
  - The post-turn pass reports ok, so nothing is retried, against M12.
- **Remediation:** Re-raise `sqlite3.OperationalError`, or return
  `{"action": "failed"}` and have callers raise.

**[AUD-028] [Severity: Medium] [Confidence: Medium-High] [A] `memory/reembed.py:228-300, 451-472`** (MEM-04)
- **Evidence:** The repair and rebuild passes embed rows and write the
  `UPDATE ... SET embedding` inside an open batch transaction. They do not use
  `write_local_vector(..., still_sql=...)`.
- **Impact:** A forget that lands between the SELECT and the write gets the
  forgotten text's vector written back onto its tombstone and re-sent to the
  remote index. The batch also holds the memory write lock while the model
  runs. This contradicts §15 D2.
- **Remediation:** Route both passes through `encode_vector`,
  `write_local_vector(..., still_sql="tier != 'forgotten' AND <text unchanged>")`
  and `index_remotely(still_held=...)`.

**[AUD-029] [Severity: Medium] [Confidence: High] [V, R] `kazma-ui/kazma_ui/chat_attachments.py:41, 51`** (UISRV-04)
- **Evidence:** Stored as `f"{attachment_id}{suffix}"`, loaded with
  `root.glob(f"{attachment_id}.*")`.
- **Impact:** `Dockerfile`, `Makefile`, `README` and `LICENSE` upload with a
  200 and an id, then the turn carries `data=None`, and the model never sees
  the file. Reproduced.
- **Remediation:** Glob `f"{attachment_id}*"` with an exact stem check, or
  always store a fixed suffix.

**[AUD-030] [Severity: Medium] [Confidence: High] [A] Soft-navigation lifecycle leaks** (UICLI-03 to 09, 18, 22)
`nav.js` re-runs page scripts on every visit (`isSoftNavPageScript`). Every
listener, interval or socket created in a closure stacks up and is never torn
down. `tests/test_still_not_doing.py` only greps for `kazmaOnSoftNavLeave` in
three files.

| Site | Leak | Effect after N visits |
|---|---|---|
| `chat.js:271-405, 2292-2324, 7443` | `keydown`, `visibilitychange`, `pageshow` and `focus` listeners, plus the 1.5 s `_turnSyncTimer` | A stale closure's Escape handler can POST `/api/chat/stop` for the running turn from another page. N×2 fetches on every focus. |
| `dashboard.js:20-46` | `/ws/dashboard` socket, 10 s and 60 s pollers | Each trace row is prepended N times. Usage is fetched from every page. |
| `hitl_approval.js:17-25` | `_pollTimer` guard is local to the closure | N pollers of `/api/pending-approvals` every 5 s, forever. |
| `templates/workspace.html:849-876` | 10/30/60 s intervals, `message` listener, no `destroy()` | N × `git status` subprocesses every 10 s, and N × uncached GitHub GraphQL calls a minute, even in hidden tabs. |
| `memory_console.js:1553, 1757` | Two raw `setInterval`s that bypass `_memEvery` | `/api/memory/v2/health` polled every 5 s from every page. |
| `swarm.js:294, 918, 2603` | `_activeTasksPoll`, `timerInterval` and `pipelineInterval` not cleared | A 2 s fetch keeps running after leaving. |
| `research.js:128-160` | `_delegatedBound` guard is local to the closure | Archive and Resume POST twice. Delete opens two confirms, and one promise never resolves. |
| `stores/agentStore.js:237-252` | `_livenessWorker` never terminated | One Web Worker leaked per Chat visit. |
| `documents.js:365-383` | Job `_poll` with no `destroy()` | Keeps polling until the job finishes. |

- **Remediation (class):**
  - Give every page script one `AbortController`, and pass its `signal` to
    every `addEventListener`.
  - Keep interval and socket handles on `window` or the component.
  - Abort, clear and close everything in the leave hook. Use the
    `kazmaOnSoftNavLeave` array form: `dashboard.js` and `swarm.js`
    *overwrite* it.
  - Add a gate that boots each page script twice in a jsdom/node harness,
    navigates away, and counts live listeners, intervals and sockets.

**[AUD-031] [Severity: Medium] [Confidence: High] [A] `kazma-ui/kazma_ui/static/js/dashboard.js:618`, `dash_lists.js:60`** (UICLI-10)
- **Evidence:** `'/chat?session=' + id`. `chat.js:360` reads only `?s=`, and
  `dashboard.js:206` uses `?s=` for the same purpose.
- **Impact:** A session title in the Dashboard's table or mobile cards opens
  the last chat, not the one clicked.
- **Remediation:** Use `/chat?s=`, and assert the link target in
  `tests/e2e/test_dashboard_session_table.py`.

**[AUD-032] [Severity: Medium] [Confidence: High] [A] Sends whose failure is reported as delivered** (GW-02, GW-03)
- **Evidence:** Two places ignore a send result:
  - `adapters/telegram.py:1659-1661`, `discord.py:846-847`,
    `slack.py:421-424, 470-471`: `await self._send_attachment(...)` discards
    its `bool`, then `return True`.
  - `agent_handler/swarm_output.py:172-183, 262-269, 311-318` and
    `swarm_dispatch.py:640` discard `manager.send(...)`, which returns `False`
    on failure.
- **Impact:** `send_file` answers "File sent" when the upload got a 413. A
  swarm result routed to a dead adapter is logged as "routed", and the direct
  Telegram fallback never runs. This contradicts §51.
- **Remediation:** Fold each attachment's result into `all_sent`, and
  `return ok` from the swarm routes. Extend `tests/test_send_results_read.py`
  to adapter `send()` and `manager.send` callers.

**[AUD-033] [Severity: Medium] [Confidence: High] [A, R] `kazma-gateway/kazma_gateway/rate_feedback.py:120-131`, `gateway.py:876-973`** (GW-05)
- **Evidence:** A rate limit of `0` divides by zero (`refill_rate = 0`) in
  `get_feedback_message`. The `_consume` loop catches only `TimeoutError` and
  `CancelledError`.
- **Impact:** An operator sets a limit to 0 meaning "off". The next message
  kills `gateway-consumer`, and every chat app stops answering. Only asyncio's
  "Task exception was never retrieved" says why.
- **Remediation:** Treat `<= 0` as disabled, or validate it in
  `settings_validation`. Wrap the per-message body in
  `except Exception: logger.exception(...); continue`.

**[AUD-034] [Severity: Medium] [Confidence: Medium] [A] `kazma-gateway/kazma_gateway/adapters/discord.py:200-221`** (GW-07)
- **Evidence:** Only close codes 4013 and 4014 stop the loop. Codes 4004
  (bad token) and 4010–4012 retry every 1–3 s forever, and a clean close
  reconnects with no delay at all.
- **Impact:** A revoked token sends Identify thousands of times a day, which
  risks Discord's identify limit and an IP ban. The Test shows only
  "connecting".
- **Remediation:**
  - Treat 4004 and 4010–4012 as fatal, recording
    `self._receive.disconnected(...)`.
  - Use exponential backoff.
  - Add a minimum delay after a clean close.

**[AUD-035] [Severity: Medium] [Confidence: High] [A] `kazma-tui/kazma_tui/settings_panel.py:147-165`** (GW-08)
- **Evidence:** Any exception except `LocalApiRefused` falls back to a local
  `get_config_store().set(...)`. 401, 403 and 5xx raise a plain
  `RuntimeError`.
- **Impact:** A mismatched secret or a slow server makes the TUI write
  straight into the shared store, skipping the server's admin check, change
  listeners and cache. This contradicts §47 ("only when no server answers").
- **Remediation:** Fall back only on connection-refused or no-listener.
  Surface every other error.

**[AUD-036] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/voice/stt.py:660-701`, `voice/tts.py:502-581`** (INF-09)
- **Evidence:** The faster-whisper, Kokoro and Coqui providers load the model
  and run inference synchronously inside `async def`.
  `import TTS.api.TTS as coqui_tts` always fails, because `TTS` is a class.
  `kokoro.create` is not the `kokoro>=0.8` API.
- **Impact:** Picking a local voice provider either freezes the server for
  the duration of a model load or always fails.
- **Remediation:**
  - Run the bodies in `asyncio.to_thread` and cache the models.
  - Fix the imports and the API calls.
  - Use `compute_type="int8"` on CPU.

  [Kokoro and whisper behaviour: NEEDS RUNTIME VERIFICATION]

**[AUD-037] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/models/discovery.py:480-492`** (INF-10)
- **Evidence:** `ollama pull` is started with `stdout=PIPE, stderr=PIPE` that
  nobody reads, and nobody waits on the process.
- **Impact:** Once the pipe buffer fills, the pull blocks for good, and the UI
  shows "pulling" forever.
- **Remediation:** Send output to `DEVNULL` or a log, and track completion in
  a thread that waits on the process.

**[AUD-038] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/mcp_client.py:389-434`** (INF-11)
- **Evidence:** The Settings MCP Test takes the first stdout line as the
  reply, with no JSON-RPC `id` match, and drains stderr only after EOF.
- **Impact:**
  - A server that logs a notification first reports 0 tools.
  - An `npx` server that writes a lot of warnings blocks, and the Test times
    out after 90 s.
- **Remediation:** Read until the matching `id` arrives, and drain stderr into
  a bounded tail in a thread.

**[AUD-039] [Severity: Medium] [Confidence: High] [A] `kazma-core/kazma_core/agent/tool_builtins/knowledge.py:234-235`** (AGT-04)
- **Evidence:** The admission refusal of a private or unresolvable seed
  (`AcquisitionError`, nothing fetched) propagates out of the tool, and the
  effect journal records it as uncertain.
- **Impact:** A crawl refused at admission (§24 G) fails the whole turn.
  `tests/test_knowledge_crawl_admission.py` calls the tool directly, so it
  never sees the journal wrapper.
- **Remediation:** Raise `RefusedBeforeEffect` for admission refusals, and
  test the refusal through `execute_effect`.

**Low (category 1)**

| ID | Src | V | Location | Issue | Fix |
|---|---|---|---|---|---|
| AUD-040 | DOC-08 | V | `documents/ingestion.py:1276` | `getattr(version, "byte_size", 0)`: `VersionRecord` (`repository.py:205-215`, slots) has no such field, so the free-space headroom is always 0. | Use the blob's size. |
| AUD-041 | DOC-07 | A | `documents/ingestion.py:2857-2868` | Transient-artifact cleanup checks references per tenant, but the CAS path is shared across tenants, so it can unlink another tenant's blob. | Leave it to GC, which checks every tenant. |
| AUD-042 | SWX-09 | A | `runs/incidents.py:202` | A pending card is still sent after its incident resolved during the restart tick. | Filter `state IN ('open','acting')`. |
| AUD-043 | SWX-10 | A | `swarm/sse_bridge.py:40` | A long single-worker task is judged "stalled" at 600 s while working, because progress is stamped only per engine event. | Stamp progress per tool round. |
| AUD-044 | INF-14 | A | `backup/restore.py:91-106` | restic `snapshots` return code is not checked: a bad password reads as "no restore points". | Raise on a nonzero return code. |
| AUD-045 | INF-18 | A | `backup/universal.py:333-389` | An rclone `TimeoutExpired` is caught and rclone runs a second time (another 30 min). | Return `ok=False` on timeout. |
| AUD-046 | MEM-08 | R | `memory/macro_sleep.py:301-309` | A failed sweep's partial writes are committed by `anchor_orphan_leaf_concepts`. | Roll back in the except, and skip the later steps. |
| AUD-047 | MEM-10 | A | `memory/list_reopen.py:45-92` | The reopen script can bring back a forgotten (blanked) fact as current. | Require `object != ''` and not forgotten. |
| AUD-048 | MEM-12 | A | `memory/task_queue.py:348-393, 464` | A reclaim's `attempts+1` is overwritten by the ack's `task["attempts"]+1`, so up to about twice `max_attempts`. | `attempts = attempts + 1` in SQL. |
| AUD-049 | MEM-15 | A | `memory/graph_backend.py:199-201` | A predicate starting with a digit is not a valid Cypher type, and the Neo4j edge is silently lost. | Backtick-quote it. |
| AUD-050 | GW-14 | A | `adapters/telegram.py:1221` | `m.get("is_bot")`: adding *any* bot to a group posts Kazma's setup text and chat id, before the allowlist. | Match Kazma's own id only. |
| AUD-051 | GW-16 | A | `kazma_gateway/telegram_format.py:28,81` | A `"` in a link URL breaks the HTML, and the whole message falls back to plain text. | `html.escape(url, quote=True)`. |
| AUD-052 | UISRV-08 | A | `sse_chat/__init__.py:841-845, 592-595, 638-640, 2169-2176` | Read-modify-write of a session outside `SessionManager.transact`, so it can overwrite a concurrent update. | Use `transact` (in a thread). |
| AUD-053 | UICLI-12 | V | `static/js/voice.js:308-310` | Read-aloud keeps playing after a soft navigation. | Same fix as AUD-011. |

### 🕳️ 2. Architectural & implementation gaps

**[AUD-054] [Severity: Medium] [Confidence: High] [V] `kazma-gateway/kazma_gateway/agent_handler/graph.py:836-840` vs `:1452`** (GW-04)
- **Evidence:** `rewrite_work_slash` replaces `msg.text` at line 838 before
  `changes_global_config(msg.text)` at line 1452.
  `kazma_core/agent/slash_turns.py:27-29` sends
  `/skill install|uninstall|activate` to the model as text.
- **Impact:**
  - §46's admin-only rule for installing or removing code never runs on the
    chat path, though a HITL card still applies.
  - The admin-gated deterministic branches of `_try_skill_command` are
    unreachable.
  - `tests/test_slash_admin_gate.py` calls `_try_skill_command` directly, so
    it passes.
- **Remediation:** Evaluate `changes_global_config` on the original text
  before the rewrite, or drop install and uninstall from `_SKILL_WORK`. Add a
  test through `create_graph_handler`.

**[AUD-055] [Severity: Medium] [Confidence: High] [A] `kazma-ui/kazma_ui/dashboard.py:479-535`** (UISRV-06)
- **Evidence:** The Dashboard chat delete removes checkpoints, the session
  store and chats, but never calls `forget_thread_snapshots`. The SSE delete
  does (`sse_chat:1545-1549`).
- **Impact:** A deleted chat's states stay in `snapshots.db`, against §12 G
  (TT-05).
- **Remediation:** Call `forget_thread_snapshots([thread_id, *session_ids])`
  through `to_thread`.

**[AUD-056] [Severity: Medium] [Confidence: High] [A] `kazma-ui/kazma_ui/research_panel/routes.py:150-165`** (UISRV-07)
- **Evidence:** `_export_reply` returns `{"ok": True, "message": msg}` even
  when the generator returned `"Error: ..."`.
- **Impact:** A failed PDF export shows a success toast and no file.
- **Remediation:** Return `ok: False` with a 500 when there is no `Saved to:`
  line. URL-encode the filename in `download_url`.

**[AUD-057] [Severity: Medium] [Confidence: High] [A] `kazma-core/kazma_core/memory/federated_search.py:123-184`** (MEM-07)
- **Evidence:** A memory search exception is logged at DEBUG and the response
  is still `ok: True`. `result.degraded` is never passed on.
- **Impact:** "Search both stores" shows an authoritative "No hits in either
  store." over a failed search.
- **Remediation:** Pass `degraded` and an error code through, log at WARNING,
  and render the banner.

**[AUD-058] [Severity: Medium] [Confidence: High] [A] `kazma-ui/kazma_ui/static/js/documents.js:110-123`** (UICLI-11)
- **Evidence:** `if (j.ok) {...}` has no else branch.
- **Impact:** A service-unavailable or 500 body paints a blank list: no
  loading, empty or error state. This contradicts "A failed read says it
  failed".
- **Remediation:** Add an error state that shows `j.error`. Extend
  `tests/test_empty_states_after_load.py` to `ok:false` bodies.

**[AUD-059] [Severity: Medium] [Confidence: Medium] [A] `kazma-core/kazma_core/mcp/manager.py:218-219, 268-325`** (SEC-07)
- **Evidence:** The MCP workspace path gate only inspects a fixed list of
  argument names. `file_path`, `repo_path`, `uri`, `file`, `filename`, `dir`,
  `root` and `cwd` all pass unchecked, and the gate is documented as "the
  ONLY filesystem-containment check for MCP tools".
- **Impact:** A trusted or allowlisted MCP call, or one under YOLO or a
  grant, can reach outside the workspace through such a key.
- **Remediation:** Derive path arguments from the tool's `inputSchema`, or
  scan every string argument for an absolute path or a `file:` URI.

**[AUD-060] [Severity: Medium] [Confidence: Medium] [A] `kazma-skills/kazma_skills/native/chat_platform_dispatcher/tools.py:86-109`** (GW-09)
- **Evidence:** `send_approval_request` is write tier with no approval. Its
  card is bound to no interrupt: its buttons are always "stale", and its text
  says "Reply: hitl approve <thread>".
- **Impact:** A prompt-injected turn posts "Approve reading the weather?" and
  then pauses on `shell_exec`. The operator types the card's reply and
  approves the real `shell_exec`.
- **Remediation:** Bind the card to a real interrupt, or render it with no
  HITL instruction. Consider retiring the tool.

**Low (category 2)**

| ID | Src | V | Location | Issue | Fix |
|---|---|---|---|---|---|
| AUD-061 | DOC-06 | A | `documents/knowledge.py:157-168, 295-306`, `service.py:1095-1222` | Index, unindex and delete-unindex check only `read`. A read-only grantee could unindex everywhere. Latent: no read-only grants exist today. | Require `write`. |
| AUD-062 | SEC-11 | A | `safety/task_grants.py:12, 28-33` | `KAZMA_TASK_GRANT_TTL_SECONDS` is documented but never read. | Read it, or drop the doc line. |
| AUD-063 | MEM-11 | A | `memory/graph_backend.py:204-263` | Forget removes the Neo4j edge but leaves the value node, so a codeword stays as `Entity.name`. [NEEDS RUNTIME VERIFICATION] | Delete the orphaned node. |
| AUD-065 | GW-11 | A | `adapters/telegram.py:1790-1857` | The webhook path skips dedupe, `/cmd@otherbot` normalization, receive-log records and callback queries, so HITL buttons do nothing in webhook mode. | Route it through `_process_update`. |
| AUD-066 | GW-15 | A | `adapters/discord.py:598-612` | Button interactions ignore the guild allowlist. | Apply `_allowed_guilds`. |
| AUD-067 | UICLI-13 | A | `static/js/x_studio.js:346-350` | `destroy()` cancels the pending composer autosave, so up to 600 ms of typing is lost on a soft navigation. | Flush in `destroy()`. |
| AUD-068 | UICLI-14 | A | `static/js/hitl_approval.js:276-280` | A semantic-option decision ignores the response status. | Check `r.ok`. |
| AUD-069 | UICLI-19 | A | `static/js/modules/nav.js:585-590` | A link answering 403 does nothing, silently. | Fall back to `location.href`. |
| AUD-070 | UISRV-12 | A | `swarm_panel/routes_tasks.py:430-441` | "The task was recorded" when nothing was recorded. | Fix the message. |
| AUD-071 | INF-13 | A | `observability/ops_alerts.py:375-378`, `lifecycle_notifier.py:465-468` | `asyncio.wait_for(asyncio.gather(<httpx sends>))` is the unbounded-cancel shape §26E forbids. The gate checks only Name/Attribute arguments. | `wait_for_bounded`, and extend the gate to Call arguments. |
| AUD-072 | AGT-10 | A | `tools/export_session.py:131-132` | The Markdown export drops system messages without saying so. | Note them, or include them. |
| AUD-073 | GW-12 | A | `kazma-cli/kazma_cli/gateway.py:94-118` | CLI gateway commands print success for any HTTP status and send no credentials. | Check the status and send `auth_headers()`. |

### 🔌 3. Un-wired components

| ID | Src | Sev | V | Location | Finding | Recommendation |
|---|---|---|---|---|---|---|
| AUD-064 | SEC-10 | Low | V | `mcp/manager.py:2319-2333` | The RBAC check runs only when the *model* supplies `user_id`. No product code injects one, so it protects nothing, and it is the cause of AUD-001. | Remove it, or take the principal from a ContextVar. |
| AUD-074 | INF-17 | Low | A | `hub/registry.py:280-296`, `hub/cli.py:216-238` | `kazma hub install` says "Installed X" and copies no files (a stub). Its name regex allows `..`. | Fail with "not implemented", consistent with §22G, and validate the name as one path segment. |
| AUD-075 | AGT-09 | Low | A (conf. Low) | `agent/pipelines/document.py`, `pipeline_registry.py`, `intent_router.py` | Possibly unreachable. Two different `get_registry` functions share a name. `test_orphan_modules` passes, so it is reached through *some* import. | Trace the live intent flow, and rename one registry. [NEEDS RUNTIME VERIFICATION] |

Frontend calls with no backend: the UI-client auditor matched every client
`/api/...` string against the route decorators and found none unmatched
(medium confidence, loose matching). `tests/test_api_route_callers.py` already
enforces the reverse direction.

### 👻 4. Dead code & obsolete assets [Recommendation: remove]

| ID | Src | V | Location | What |
|---|---|---|---|---|
| AUD-076 | SWX-08 | A | `cron/scheduler.py:223, 821`; `cron/store.py:509, 544, 597, 1108` | Callers in tests only: `_is_due`, `_checkpointer`, `parse_timing` (still in `__all__`), `update_result`, `job_exists`, `record_event`, `list_active`. |
| AUD-077 | INF-16 | A | `backup/cloud_sync.py:151-183` | `upload_directory` and `_upload_all_files` have no caller, and report `ok` when 999 of 1000 uploads fail. |
| AUD-078 | MEM-13 | A | `memory/task_queue.py:615-617` | `async_purge_completed_tasks` has no caller. |
| AUD-079 | SEC-09 | A | `config_store.py:2306-2334` | `ConfigStore.transaction()` is SQLite-only and has no callers, yet §8 tells writers to use it. A Postgres caller would write to the local shadow. Delete it, or make it raise on Postgres, and fix §8. |
| AUD-080 | GW-19 | A | `adapters/telegram.py:861`, `rate_feedback.py:143` | `create_webhook_router` and `record_feedback` are used only by tests. |
| AUD-081 | UICLI-17 | A | `dashboard.js`, `hitl_approval.js:313,331,384` | `window.KazmaDashboard`, `KazmaHITL` and `__hitl_approval__` have no readers. `KazmaApp.setIsThinking` is never defined, so those branches never run. |
| AUD-082 | UISRV-13 | A | `setup_api.py:132`, `routes_direct/backup.py:76`, `routes_direct/system.py:113-130`, `telemetry_route.py:213` | An impossible `api_key` check, a backup task named "memory-maintenance", a connection closed three times, and a swallowed `CancelledError`. |
| AUD-083 | SEC-04 (part) | V | `safety/yolo.py:71-80` + `routes_direct/misc.py` | `downgraded` is always `False`, so the route's downgrade branch is dead (see AUD-090). |

### ⚠️ 5. Weaknesses & technical debt

#### Security

**[AUD-002] [Severity: High] [Confidence: High] [V] `kazma-core/kazma_core/x_api/mentions_fire.py:221-237, 432`** (SWX-01) [Category: Security, CWE-918]
- **Evidence:**
  ```python
  r = httpx.get(url, follow_redirects=True, timeout=8.0)
  ```
  `url` is a `t.co` link taken from the mention text. It runs at line 432,
  **before** the "already handled" check (438) and the summoner allowlist
  (~502).
- **Root cause:** A raw client outside the single egress stack (§24B, §32):
  - no per-hop public-address validation;
  - no pin;
  - environment proxies apply;
  - no body cap;
  - only a per-read timeout, so no total deadline.
- **Exploit:** Anyone posts "@KazmaAI <link>". t.co redirects to an attacker
  server, which redirects to `http://169.254.169.254/...`,
  `http://10.x/...` or `http://127.0.0.1:9090/...`. That is a blind GET from
  the operator's network. A drip-feed redirect stalls the sequential mention
  poller, so auto-reply stops. A large body is read whole into memory.
- **Remediation:**
  - Make no network call. Request `entities` in `tweet.fields` and read
    `entities.urls[].expanded_url`.
  - If a resolution is still needed, use a single HEAD with no redirects,
    only to t.co → x.com/twitter.com, through
    `dns_worker.validate_public_url`, with `trust_env=False` and a total
    deadline.
  - Move it after the summoner and seen checks.
  - Add a static gate: no raw `httpx.get/post` in `x_api/`.

**[AUD-005] [Severity: High] [Confidence: High] [V, R] `kazma-core/kazma_core/agent/tool_builtins/system.py:215-251`** (AGT-01) [Category: Security, CWE-200]
- **Evidence:**
  ```python
  is_secret_key = any(m in key_l for m in secret_markers)  # key name only
  ...
  display = val if isinstance(val, (str, int, float, bool)) else str(val)
  ```
  `ConfigStore.get` resolves `vault://` pointers inside dicts and lists (§8).
- **Exploit:** `config_read` is read tier, so it shows no approval card.
  - `config_read("providers.list")` returned the nested `api_key` in
    plaintext.
  - `config_read("memory.backends.state.url")` returned the Postgres DSN with
    its password.

  Both were reproduced. A prompt injection asks the agent to read them and
  quote them in a post, an email or a memory.
- **Remediation:** Pass the value through `settings.mask_deep()` and URL
  credential masking, or refuse any value holding a vault pointer or a
  credential URL. Add `config_read` to the masker gate in
  `tests/test_url_credentials.py`, as §26D requires for every surface that
  echoes config.

**[AUD-084] [Severity: High] [Confidence: High] [V, R] `kazma-core/kazma_core/env_files.py:65, 102`, `kazma-cli/kazma_cli/main.py:51`** (SEC-02) [Category: Security, CWE-15]
- **Evidence:** `ladder.append(Path.cwd() / ".env")`, loaded with
  `override=True` for every CLI subcommand, including `kazma mcp`. That
  command is started by editors and MCP clients from whatever folder is open
  (`mcp/server.py:99-110`).
- **Exploit:** A cloned untrusted repo carries a `.env` with any of these, and
  it overrides the operator's real environment:
  - `KAZMA_MCP_ALLOW_UNGATED=1`
  - `KAZMA_HITL_CANONICAL_FLOOR=0`
  - `KAZMA_COMMITMENT_ENABLED=0`
  - `KAZMA_HOST_SHELL=1`
  - `KAZMA_DATA_DIR=<attacker store>`

  Reproduced: the planted value beat an explicit `=0` in the process
  environment, and danger tools were exposed with no approval path.
- **Remediation:**
  - Do not load the CWD `.env` for `kazma mcp`, `kazma ask` or any command
    run inside a workspace. Load only `<user_home>/.env` and
    `KAZMA_ENV_FILE`.
  - At minimum, load the CWD file with `override=False` and refuse
    safety-relaxing `KAZMA_*` keys from it.
  - Add a hostile-CWD test to `tests/test_env_loading.py`.

**[AUD-085] [Severity: Medium] [Confidence: High] [V] `agent/tool_builtins/system.py:355-367, 546-602`** (AGT-03) [Category: Security]
- **Evidence:** `uv` is in `_SAFE_BINARIES` with no `_EXEC_CAPABLE_ARGS`
  entry, so `uv run <program>` runs an interpreter. The `git` denylist does
  not stop `git config alias.x "!cmd"` followed by `git x`.
- **Impact:** After one approved card, an allowlisted binary becomes general
  code execution. That is the class F-03 (`find -exec`) closed (§26C).
  Reproduced.
- **Remediation:** Refuse `uv run`, `uv tool run` and `--with`, or drop `uv`
  from the shell allowlist. Refuse `git config alias.*`, and any value
  starting with `!`.

**[AUD-086] [Severity: Medium] [Confidence: High] [V, R] `tools/read_url.py:1378-1395`, `research_synthesize.py`, `research_eval.py`, `research_evidence.py`** (AGT-05) [Category: Security]
- **Evidence:** `_load_research_body` checks only `relative_to(root)`, with no
  `check_path_access` or `is_kazma_store` call.
- **Impact:** On the live install the workspace contains `kazma-data/`, so
  `read_research_chunk("kazma-data/hitl_gates.db")` returns raw store bytes
  that `file_read` refuses. The tools are read tier. This contradicts §37
  ("every door refuses every store").
- **Remediation:** Route these through the same door, and add the research
  readers to `test_every_door_refuses_every_store`.

**[AUD-087] [Severity: Medium] [Confidence: High] [V] `kazma-gateway/kazma_gateway/mcp_server.py:243`** (GW-10) [Category: Security, CWE-22]
- **Evidence:** `search_files = root.rglob(glob)` uses an unvalidated glob.
  `Path('root').rglob('../*.txt')` yields files outside the root, and the
  results are never checked with `resolve()`.
- **Impact:** An MCP client sends `glob: "../**/.env"` to the read-tier
  `search_code` and reads secrets outside the project.
- **Remediation:** Reject globs containing `..` or an absolute path, and keep
  only results whose resolved path is under `root.resolve()`.

**[AUD-088] [Severity: Medium] [Confidence: High] [V] `kazma-core/kazma_core/security/ssrf.py:64-93`** (SEC-05) [Category: Security, CWE-918]
- **Evidence:** `_is_blocked_ip(100.64.0.1)` and `(100.100.100.200)` return
  `False`, though `ip.is_global` is `False` for both.
- **Impact:** CGNAT and cloud-internal addresses pass `validate_url`, the pin
  check and the acquisition DNS worker. Those include Alibaba's metadata
  endpoint and Tailscale peers.
- **Remediation:** Block whenever `not ip.is_global`, keeping the explicit
  checks. Add test cases.

**[AUD-089] [Severity: Medium] [Confidence: High] [A] `kazma-skills/.../git_github_manager/tools.py:36-52`** (GW-06) [Category: Security]
- **Evidence:** The read-tier `git_status` runs `git status` and
  `git branch` without `-c core.fsmonitor=false`. Its sibling `_git_read`
  turns fsmonitor off.
- **Impact:** A workspace whose `.git/config` sets `core.fsmonitor` runs that
  command with no approval. This contradicts §10 E1b and §26I.
- **Remediation:** Route it through `_git_read`.

**[AUD-090] [Severity: Medium] [Confidence: High] [A] `kazma-core/kazma_core/safety/yolo.py:71-80`** (SEC-04) [Category: Security]
- **Evidence:** `try_enable_yolo` calls `enable_yolo(..., force=True)`. The
  approve route maps `scope="session"` to it with no `yolo_allowed()` check.
- **Impact:** On a `KAZMA_PRODUCTION=1` install, a crafted or stale client
  POST turns off approvals for the thread. The docs say production
  hard-blocks YOLO unless `KAZMA_ALLOW_YOLO=1`.
- **Remediation:** Downgrade to "once" when `not yolo_allowed()`. Update
  `tests/test_hitl_yolo_thread_bind.py:50`.

**[AUD-091] [Severity: Medium] [Confidence: High] [A, R] `migration/pg_bridge.py:213-214, 305-307, 367-385`, `db/pg_backup.py:287`** (INF-05) [Category: Security, CWE-532]
- **Evidence:** `-e PGPASSWORD=<pw>` is in the docker argv. A
  `TimeoutExpired`'s `str()` holds the full cmd, and it is logged with
  `exc_info=True`.
- **Impact:** A dump hung past 30 minutes writes the database password into
  `kazma.log`. While the dump runs, the password is visible in the docker
  CLI's command line.
- **Remediation:** Pass `-e PGPASSWORD` (name only) and set it in `env=`.
  Re-raise timeouts without the command.

**[AUD-092] [Severity: Medium] [Confidence: High] [A, R] `migration/exporter.py:308,334`, `backup/universal.py:531-535`** (INF-06) [Category: Security]
- **Evidence:** `is_symlink()` is `False` for NTFS junctions (verified on this
  machine), so "links are never followed" is false.
- **Impact:**
  - A junction in the workspace pointing at the user profile copies SSH keys
    and similar files into a migration bundle or an offsite backup.
  - A junction cycle crashes the export with `RecursionError`.
- **Remediation:** Skip `p.is_junction()` (3.12+), or check the
  reparse-point attribute on 3.11.

**[AUD-093] [Severity: Medium] [Confidence: High] [A] `system/runtime_manager.py:93-110`, `system/installer.py:186-189`** (INF-03) [Category: Security / supply chain]
- **Evidence:** The chat-app "promote" buttons run `uv add <pkgs>` with no
  `cwd`, no `env` and no timeout, so it inherits the server's whole
  environment. `kazma_core/system` and `models` are not in the child-env
  gate's `_SPAWN_DIRS`.
- **Impact:**
  - It edits the live checkout's tracked `pyproject.toml` and `uv.lock`, so
    the next `git pull` refuses.
  - Build scripts see `KAZMA_VAULT_KEY`.
  - A hung install holds a thread forever.
- **Remediation:** Use `uv pip install` with `env=tool_child_env()` and a
  timeout, and add both folders to `_SPAWN_DIRS`.

**Low (security)**

| ID | Src | V | Location | Issue | Fix |
|---|---|---|---|---|---|
| AUD-094 | INF-15 | A | `safety/protected_config.py` | `backups.offsite.*` and `backups.extra_paths` are not protected. One `config_save` approval redirects the offsite zip, which holds `.env` and `vault.db`. | Add them to the protected prefixes. |
| AUD-095 | INF-19 | A | `migration/exporter.py:366` | `f'SELECT COUNT(*) FROM "{name}"'` has no identifier escaping (the AUD-028 sibling). | `_quote_ident`. |
| AUD-096 | SEC-12 | A | `safety/commitment/config.py:69-96` | A stored `agent.commitment.*` value overrides the env kill switch (§20E). Latent. | Apply the env after the store. |
| AUD-097 | SEC-13 | A | `mcp/child_env.py:139-158` | MCP children inherit `HTTP(S)_PROXY` with credentials. | Drop values that carry credentials. |
| AUD-098 | SEC-14 | A | `mcp/server.py:210-218` | A tool with no tier is labelled read-only and published with no approval path. Contradicts §26B. | Use `approval_floor`. |
| AUD-099 | SEC-15 | A | `http_pool.py:35-39` | The shared client follows redirects by default and does not use the shared TLS context. | `follow_redirects=False`, `verify=shared_ssl_context()`. |
| AUD-100 | UISRV-09 | A | `routes_direct/misc.py:139` | The bus-bridge branch records a client-supplied `actor`. | Derive it from the session. |
| AUD-101 | UISRV-10 | A | `routes_direct/auth.py:125-185` | A concurrent burst bypasses the login throttle: the check runs before the await and the append after. | Reserve the slot first. |
| AUD-102 | UISRV-11 | A | `documents_api.py:338`, `saas_api.py:117`, `sse_chat:2022,2058`, `settings.py:1190,1738,1769,1771`, `providers.py:430`, `mcp_ui.py:160` | Raw exception text is returned to clients (§26F). | `safe_error(exc)`. |
| AUD-103 | UISRV-14 | A | `push.py:145-216` | Any push endpoint URL is accepted, so the server POSTs to internal hosts. The prune can lose updates. | `validate_url`, and lock the read-modify-write. |
| AUD-104 | UISRV-15 | A | `commitment_api.py:49-94` | Soul confirm is not admin-only with platform users, and its failures answer 200. Multi-user mode only. | Add it to `_ADMIN_PREFIXES`. |
| AUD-105 | UISRV-05 | A | `models_route.py:183-197` | `POST /api/models/saved` stores masked `****` URL passwords and keys verbatim. Its sibling route restores them (§26D). | Reuse the placeholder and URL-restore logic. |
| AUD-106 | UICLI-20 | A | `templates/workspace.html:849` | The OAuth `message` listener does not check `e.origin`. | Check it. |
| AUD-107 | GW-17 | A | `kazma_cli/ask.py:30-38` | "a=always" turns on YOLO for the whole thread. | Grant only that tool, or say what it does. |
| AUD-108 | GW-18 | A | `environment_bootstrapper/tools.py:33-49` | `install_python_packages` accepts option arguments (`--index-url`, `-e git+...`) and an implicit venv. Danger tier. | Refuse leading `-`, and pass `--python`. |
| AUD-109 | AGT-07/08 | A | `tools/vision_analyze.py:138`, `tools/file_read.py:273-305` | A local image is read whole up to 2× the cap. The document branch of `file_read` skips the 32 MiB budget (its own budget applies). | Consistent byte caps. |

#### Performance: event-loop stalls (systemic)

**[AUD-006] [Severity: High] [Confidence: High] [V] `kazma-ui/kazma_ui/sse_chat/__init__.py:1099, 1138-1170, 1282, 1375, 1380` (+ `:845, 595, 640, 2176`; `_persistence.py:131-152`)** (UISRV-01) [Category: Performance / availability]
- **Evidence:** `_persist_now` is a sync nested def that calls
  `persist_reply`, which does a whole-session upsert under the
  SessionManager lock. It is called inline from the async `_event_generator`
  every 50 streamed characters, on cancel and on crash. `_get_store().put()`
  and `_persist_instant_turn` (`store.transact`) are also called inline.
  §35's fifth pass lists exactly these as forbidden on the loop.
- **Root cause:** `_loop_stall_helper_calls` treats every nested `FunctionDef`
  as code run by `to_thread`.
- **Impact:** On Postgres, every web turn does one database write on the loop
  per 50 characters streamed. During an outage each one stalls every stream,
  every socket and `/health/ready` for up to 5 s. That is the §44 restart
  class.
- **Remediation:** Run every flush in `await asyncio.to_thread(...)`, or
  through one background writer. Make the gate treat a nested sync def that
  is *called* from async code as running on the loop.

**[AUD-007] [Severity: High] [Confidence: High] [A] `kazma-ui/kazma_ui/auth.py:1494`, `rate_limit.py:56-65, 143`** (UISRV-02) [Category: Performance / availability]
- **Evidence:** The tenant middleware calls `client_tenant_spoof_allowed()`
  inline. That chain ends in `get_config_store().get("platform.users")`. The
  auth middleware threads the same call (line 1401). `rate_limit._check` reads
  `get_config_store()` inline, against its own docstring.
- **Impact:** After the 15 s cache TTL, every request (static files and the
  guard's probe included) does a settings round trip on the loop. With
  Postgres away, every request stalls up to 5 s.
- **Remediation:** `to_thread` both, or reuse the auth middleware's result
  through `request.state`. Add both helpers to `_LOOP_STALL_HELPERS`.

**[AUD-110] [Severity: High] [Confidence: High] [A] `kazma-core/kazma_core/documents/ingestion.py:1841-2236`** (DOC-01) [Category: Performance]
- **Evidence:** In `generate_document`, `convert_document`,
  `redact_document`, `merge_documents` and `pdf_*`, the ingestion
  coordinator's own sync prelude and postlude run on the loop:
  - `ingest_stream`, which includes two parser-worker launches (up to 300 s
    each), a clamscan `subprocess.run(timeout=120)`, fsync and database
    transactions;
  - `_resolve_current_version`;
  - `_materialize_original`, which copies the whole original;
  - `get_document_rollout`.
- **Impact:** `POST /api/documents/generate` freezes every chat stream for
  seconds to minutes.
- **Remediation:** `to_thread` each sync body, and add the helpers to
  `_LOOP_STALL_HELPERS`.

**[AUD-111] [Severity: Medium] [Confidence: High] [A] Further wrapper-reached loop I/O (same class)**

| Src | Location | Call on the loop |
|---|---|---|
| UISRV-03 | `routes_direct/misc.py:461-468` (`GET /health`), `:1155`, `:323`; `documents_api.py:1023,1060,1084`; `skills_ui.py:327,378`; `saas_api.py:78,135,206` | `admin_decision`, which reaches `validate_session` and `get_request_principal`. |
| UISRV-03 | `sse_chat:2097`, `:457` | `snapshot_capacity`, and `stamp_last_platform` (a settings **write** on every send). |
| UISRV-03 | `turn_liveness.py:144,170,193` | `persisted_hitl_for_thread`, `close_reply_turn`. |
| UISRV-03 | `misc.py:56`, `gate_view.py:332,503`, `delivery.py:316-324` | Gate-registry SQLite reads in `view_for_interrupt` and `attach_view_to_hitl_frame`. |
| UISRV-03 | `routes_chat_upload.py:99` | A 20 MB attachment write. |
| UISRV-03 | `settings.py:417,428`; `routes_voice_ws.py:192` | Push subscribe and unsubscribe; `resolve_voice_session`. |
| INF-08 | `sessions/directory.py:359-373, 596-598` | `stamp_last_platform`, `resolve_session` (the whole chat list). |
| SEC-08 | `llm_provider.py:607,634-657`; `mcp/oauth.py:495-519` | `_known_models_for` (settings, vault and discovered models) on every model id containing `/`; MCP OAuth token load and save. |
| SWX-07 | `runs/supervisor.py:186,193`; `runs/notify.py:137,185` | `ops_alerts.alert()`, which reads routing settings. Cron already threads it. |
| GW-13 | `kazma_gateway/allowlists.py:118-126` from `telegram.py:1388`, `discord.py:622`, `slack.py:906,918`, `graph.py:1452`, `commands.py:1685`, `session_commands.py:153` | `is_gateway_admin` and its siblings, which read the settings store. |
| DOC-10 | `documents/retention.py:651-685` | `get_document_config` and `get_document_rollout` in the maintenance loop (also a bare `create_task`). |

- **Systemic remediation:** §35 already describes an import-following
  inference for the memory path (`_qualified_stall_helpers`, M34). It names
  about 130 sites product-wide, and this audit confirms them. Extend that
  gate area by area: UI routes, gateway, LLM provider, runs, documents. Add
  the helpers named above as declared entries meanwhile.

**[AUD-112] [Severity: Medium] [Confidence: High] [A, R] Memory slow I/O under the write lock** (MEM-05, MEM-06) [Category: Performance]
- **Evidence:**
  - `memory/swarm_bridge.py:109-140` embeds up to 8,000 characters between an
    `INSERT` and its commit. A probe saw "database is locked".
  - `memory/global_reconsolidation.py:240-302` makes Neo4j, Postgres-mirror
    and ops-DB calls inside an uncommitted batch of up to 50 merges.
- **Impact:** Every other memory write waits out its busy timeout and fails.
  The mirror receives state that a later rollback may undo. This contradicts
  §15 D2; neither module is in `tests/test_memory_io_outside_locks.py`.
- **Remediation:** Commit first, then embed, mirror and write the graph, and
  add both modules to the gate.

**[AUD-113] [Severity: Medium] [Confidence: Medium] [A] `backup/cloud_sync.py:592-611, 691-711, 1060-1079`** (INF-12) [Category: Performance]
- **Evidence:** OneDrive uses a single `PUT …:/content`. OneDrive, WebDAV and
  S3 all call `f.read()` on the whole archive.
- **Impact:** A universal zip of about 1.3 GB is over Graph's 250 MB
  simple-upload limit, so the OneDrive provider always fails. WebDAV and S3
  allocate the full archive in the server process.
- **Remediation:** A Graph upload session, a streamed WebDAV PUT and S3
  multipart, mirroring the Drive resumable upload (AUD-006 of the 2026-09-30
  audit).

**Low (performance)**

| ID | Src | Location | Issue |
|---|---|---|---|
| AUD-114 | DOC-11 | `documents/ingestion.py:823, 1123` | Every intake launches the parser worker twice (two containers in the container profile). |
| AUD-115 | DOC-12 | `documents/ingestion.py:1382-1448` | `list_documents` makes N+1 `has_access` and job queries, and `grant_operator_access` rescans every document every minute. |
| AUD-116 | DOC-13 | `documents/backup.py:292, 389` | `?mode=ro` on a WAL snapshot can create `-wal` and `-shm` files in a checksummed backup. Use `immutable=1`. [NEEDS RUNTIME VERIFICATION] |
| AUD-117 | MEM-09 | `memory/dual_write.py:161-184` | The process-wide writer's lazy init is not locked. Two first writers race on schema `executescript`. |
| AUD-118 | MEM-14 | `stores/bookmarks.py:184-208` | `close()` frees the connection without the lock (the §8 use-after-free class), and the singleton has no init lock. |

#### Maintainability

| ID | Src | Sev | Location | Issue |
|---|---|---|---|---|
| AUD-119 | SWX-06 | Medium | `swarm/worker.py:692-699` | The forced final answer at the iteration limit calls `provider.chat` directly. It writes no ledger row and gets no retry, failover or timeout, so its cost is missing from the per-call ledger. The SW-E15 gate exempts the whole of `worker.py`. |
| AUD-120 | UICLI-15/16 | Low | `research.js:824-829`, `dashboard.js:199`, `hitl_approval.js:40,57` | English literals ("Cost (USD)", "file(s)") and raw `toLocaleString`. The script-English gate misses literals passed through a variable. |
| AUD-121 | UICLI-21 | Low | `kazma.css:5478`, `kazma.v5.css:335` | `.hitl-panel.has-pending` is defined in both files (§28). |
| AUD-122 | AGT-11 | Low | `agent/supervisor_watchdog.py:258` | Recovery reflection notes are written into the checkpoint and hoisted back every turn. Bounded by the recovery limit. |
| AUD-123 | INF (context) | Low | `tenant_isolation.py:38-47` | `_load_users_from_store` swallows store errors and returns `[]`, so the "fail closed on RBAC store error" branch can never run. |

#### Recurring patterns (root causes behind many findings)

1. **Wrapper calls the name-based gates cannot see.** This covers the 20+
   loop-stall sites in AUD-006/007/110/111, the DELEGATES list missing
   `file_apply_patch` (AUD-008), and the swarm `.chat(` gate exempting a
   whole file (AUD-119). The fix is the import-following inference §35
   already built for memory, applied to the other areas.
2. **A return value that means "it failed" is discarded.** AUD-003, 021, 032,
   044, 056, 057, 077. Recommended gate: every product call of a function
   annotated `-> bool` (or returning `ok`) in backup, send and migration code
   must use the result.
3. **A side door around the hardened path.** AUD-002 (raw httpx beside the
   egress stack), AUD-005 (`config_read` beside `mask_deep`), AUD-086
   (research readers beside the store door), AUD-087 (an MCP glob beside
   `_resolve`), AUD-105 (a second profile route beside the masked-save
   logic). Each one is the hardened rule's sibling, not the rule itself.
4. **Soft-navigation re-runs page scripts.** AUD-011, 012, 030: one class
   gate would cover all of them.

---

## 3. Prioritized action plan

### 1. Immediate fixes (blockers and security risks), in order

**Status (2026-10-26):** fixed, each with a regression test that fails on the
old code. The rules are recorded in AGENTS.md §53.
- Item 1: AUD-001 and AUD-064.
- Items 2 to 4: AUD-002, AUD-003, AUD-004, AUD-005.
- Item 5: AUD-084, applied to `kazma mcp` and `kazma ask`.
- Item 6: AUD-006, AUD-007, AUD-110. The loop-stall gate now catches a
  nested sync function that async code calls directly.
- Items 7 to 9: AUD-008, AUD-039, AUD-010, AUD-013.
- Items 10 and 11: AUD-085 to AUD-091, AUD-009, AUD-011, AUD-012.

Not done: the short-term and long-term items below. AUD-111 remains, except
the call sites the improved gate flagged (`sessions/directory.py`,
`voice_turn.py`, the startup graph recompile), which are fixed.

1. **AUD-001:** stop popping tool arguments in `UnifiedToolExecutor`, then
   remove the dead RBAC block (AUD-064).
2. **AUD-002:** remove the t.co network call from `mentions_fire`, and gate
   raw httpx in `x_api/`.
3. **AUD-003, AUD-004:** make the importer abort on a failed backup, record
   pre-existing destinations, and move the vault sync after preflight.
4. **AUD-005:** mask `config_read` output deeply.
5. **AUD-084:** stop loading the CWD `.env` for `kazma mcp` and `ask`.
6. **AUD-006, AUD-007, AUD-110:** move the chat-stream flush, the tenant and
   rate-limit settings reads, and the document action preludes off the loop.
   Fix the nested-def blind spot in the gate.
7. **AUD-008, AUD-039:** raise `RefusedBeforeEffect` for pre-effect refusals,
   and make the DELEGATES gate transitive.
8. **AUD-010:** stop fact extraction for ledgered turns, and sweep facts in
   `enforce_ledger`.
9. **AUD-013:** verify the PID before `taskkill` in `_stop_recorded_child`.
10. **AUD-085, AUD-086, AUD-087, AUD-088, AUD-089, AUD-090, AUD-091:**
    `uv`/git alias exec, research store door, MCP glob, CGNAT range,
    `git_status` fsmonitor, YOLO in production, PGPASSWORD in logs.
11. **AUD-009, AUD-011, AUD-012:** OCR convert artifact, voice mic teardown,
    swarm double Retry.

### 2. Short-term refactoring (wiring and cleanup)

- **AUD-014 to 038:** retry classification, provider/model pairing, lineage,
  cron leases, incident exception handling, backup ordering and counting,
  document intake recovery, stage GC, heartbeat, render drops, recall and
  belief failure signalling, re-embed guard, attachment glob, chat-app send
  results, gateway consumer guard, Discord close codes, TUI fallback.
- **AUD-030:** the soft-navigation lifecycle class, with a jsdom
  double-boot gate.
- **AUD-054 to 060:** the admin gate before the slash rewrite, Dashboard
  delete and Time Travel, export and federated-search truthfulness,
  documents error state, MCP path keys, the model-authored approval card.
- **AUD-076 to 083:** dead code removal (and the §8 text about
  `transaction()`).

### 3. Long-term improvements (architecture and performance)

- Extend the import-following loop-stall inference (§35 M34) to every area
  (AUD-111), retiring name-matching gates.
- A "result must be used" gate for `bool`/`ok`-returning backup, send and
  migration helpers (pattern 2).
- Streaming and multipart offsite uploads (AUD-113). A single intake parser
  launch (AUD-114). Joined ACL and job queries for listings (AUD-115).
- Build the local voice providers properly, or remove them (AUD-036).
- Replace the hub stub with a refusal (AUD-074), and settle the document
  pipeline registry's two `get_registry` functions (AUD-075).

---

## 4. What was not read

Coverage by area: each slice auditor's per-file table is kept in the session
record. The parts this audit did **not** read with attention (grep or AST scan
only):
- **kazma-core:**
  - `documents/ocr/*`, most `parsers/*`, `worker_broker.py`,
    `worker_container.py`, `fidelity`, `certification`, `renderer_worker`;
  - `web_acquire/*` except `http.py`;
  - `x_api` drafting, stance, reply-store and the vendored `_text`;
  - swarm reliability, durable, semantic router, patterns;
  - memory benchmark, rehydrate, embedder, entity resolution, legacy tables;
  - `agent/turn_input.py`, `artifacts.py`, `intent/**`;
  - `observability` beyond `ops_alerts`;
  - `runtime/*`, `tracing/*`;
  - most of `config_loader`, `providers`, `provider_adapters`.
- **kazma-ui:**
  - `turn_document.js`, `turn_view.js`, `turn_presentation.js`,
    `components.js`, `bidi.js`, `settings_hub.js`;
  - most of `settings.html`;
  - health, kb_api, x_api, scheduled_api, email_api, memory_api,
    turn_runtime, thread_rewind, `ws_chat`.
- **Gateway:** the bus adapters, diagnose and STT modules, the GitHub
  routers.
- **TUI:** everything except the settings panel, part of the app, and the
  HITL modal.
- **CLI:** doctor, migrate, project.
- **Skills:** x_publisher, document, database, browser, the email and
  calendar backends.
- **scripts/:** everything except `scripts/service`.

Several of these areas had dedicated hardening audits on 2026-10-13/15
(Documents, Knowledge, Swarm, Memory, Scheduled, Time Travel), so their
residual risk is lower. They are not audited here.

## 5. Verification checklist (for each fix set)

- Compile: `py_compile` over changed files; `node --check` over changed JS.
- CI's ruff gate:
  `ruff check --select E9,F63,F7,F82,F841,B033`.
- Targeted tests for each fix, each with its negative control (old code
  fails, new code passes).
- Before a push, the full suite with no `--chunks`:
  `python scripts/fast_test.py`.
- For any template or JS change, the soft-nav and Arabic page e2e tests,
  which `fast_test` skips:
  - `tests/e2e/test_soft_nav_into_chat.py`
  - `tests/e2e/test_pages_load_clean.py`
  - `tests/e2e/test_pages_read_in_arabic.py`
- The bandit HIGH gate over `kazma-*`, `tests/` and `scripts/`.
- The Postgres-marked tests on a throwaway container, for AUD-006/007/111.
- For live verification after deploy: the success-line check in `kazma.log`
  (AGENTS "verify a live fix by its success line").
