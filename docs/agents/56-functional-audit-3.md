<!-- AGENTS.md, piece 56-functional-audit-3. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 56. The functional audit of 2026-11, Stage 3 (the Low findings)

Plan `docs/plans/FUNCTIONAL_AUDIT_2026-11_REMEDIATION.md`, Stage 3. Each fix
has a gate that fails on the old code.

- **A result is said as it is** (FA-108/109/110/202/236). `persist_reply`
  logs what the transcript store answered; orphan recovery counts a task the
  store refused to write as not recorded; an agent that cannot take a new
  provider key reports it as a model substitution (§38), not at DEBUG; a
  document worker logs its store's outage once and its recovery once, not
  every poll; a swarm dispatch whose workers are all unknown is 404
  `workers_not_registered` with nothing recorded, and the CLI prints the
  server's answer. Two metric families say what their names say:
  `kazma_commitment_decisions_total` is counted when the gate decides (it
  was a COUNT of a table the GC trims), `kazma_hitl_gates_total` is recorded
  by the registry once per real transition, every mechanism (FA-257). Every
  model call names its work: the ledger takes the thread from the caller's
  context, research names `research:<job id>` (FA-260). A call refused before
  it ran (a commitment deny, a refusal before its card) is a step of the turn:
  a start and an end activity carrying the refusal, a `failed` row on the
  page; the expected deny logs at INFO (FA-253). Gates:
  `tests/test_results_said_as_they_are.py`,
  `tests/test_metrics_say_what_they_name.py`,
  `tests/test_model_calls_name_their_work.py`,
  `tests/test_refused_call_is_a_step.py`, `tests/js/test_refused_tool_row.js`.
- **What answers before sign-in says no more than it must.** The GitHub
  OAuth result page escapes its message (FA-130); a failed readiness or deep
  health check shows the exception's type, a code and a reference
  (`health._check_failure`), the text in the log (FA-131); the Telegram
  webhook warns about refused POSTs once per ten minutes, not per POST
  (FA-132); `/api/auth/status` before sign-in answers `auth_enabled`,
  `authenticated: false`, `mode` and `oidc`, never the proxy list (FA-219).
  OIDC sign-in state is per attempt (`auth.oidc.pending`, ten minutes, 32 at
  most, written by `atomic_update`), bound to a cookie on `/api/auth/oidc`,
  compared with `hmac.compare_digest` and taken once; a RUNTIME key in
  `settings_restore` (FA-133). Gates: `tests/test_open_answers_say_no_more.py`,
  `tests/test_oidc_sign_in_state.py`.
- **Request bodies and refusals.** `upload_limits.UploadSizeLimitMiddleware`
  refuses a chat upload or a voice clip past its limit by its declared length
  before reading, and a streamed one as it passes the limit (FA-134). The
  Documents and Scheduled routes answer every refusal in one envelope,
  `{"ok": false, "error", "code"}` (`kazma_ui/error_envelope.py`), FastAPI's
  validation answer included, which no longer echoes the input (FA-234,
  FA-241); a library that does not exist is 404 `library_not_found`. A JSON
  body that does not parse is 400 `invalid_json` (`kazma_ui/json_body.py`;
  the chat's session bind answered 503, FA-274). A memory GET that finds
  nothing is 404 (`memory_actions` reads GET answers too, FA-275). The chat
  session list refreshes from the database at most every 10 s, one refresh at
  a time, the others reading the cache: 10 readers at once took 155 ms each,
  now 0.9 ms; the sidebar groups and searches on the page, so no paging was
  added (FA-278). Gates: `tests/test_upload_limits.py`,
  `tests/test_document_error_envelope.py`, `tests/test_scheduled_api.py`,
  `tests/test_json_body_reader.py`, `tests/test_memory_actions.py`,
  `tests/test_session_list_concurrency.py`.
- **Every store says how long it keeps what it holds** (FA-141).
  `store_registry.RETENTION` has an entry for every store and family (a rule
  and the sweep that runs it, or "kept:" and why); decided gates, completed
  tool receipts and finished task ledgers go after 90 days, model calls after
  365, the pipeline log by the swarm task retention -- each a maintenance
  sweep (§15B) that removes decided rows only. `hitl_gates.db` is on the
  erasure horizon. Gate: `tests/test_store_retention_declared.py`.
- **Files and maps that only grew have a bound** (FA-142/143/144/145/203/
  204/224). Stall dumps are pruned (200, 30 days); old-format document
  backups older than the oldest verified one kept are pruned; a read-only
  diagnostic writes nothing to the crawl journal; the guard rotates its own
  log; manifests are written whole (`kazma_core/atomic_files.py`); the alert
  throttle and the approval-card counter keep a bounded number of keys;
  SQLite WAL files are capped (`SQLITE_JOURNAL_SIZE_LIMIT`) and `settings.db`
  is compacted once after the Knowledge tables left it. A chat attachment is
  stored once, named by its content hash (FA-146). The `file_read` cache is
  per reader (a turn's thread, a swarm worker's scope), so one reader's read
  never answers another's (FA-111). File I/O in async tools runs in a thread
  (FA-139). A bare `*.db` a shell command names is the workspace's file
  unless only the data dir holds it (FA-217). Gates:
  `tests/test_files_and_maps_stay_bounded.py`,
  `tests/test_attachments_stored_once.py`,
  `tests/test_file_read_cache_per_reader.py`,
  `tests/test_async_tools_file_io_off_the_loop.py`,
  `tests/test_workspace_db_is_the_users.py`.
- **Document GC runs where the catalog is** (FA-205). The Postgres mark read
  its rows by position and failed, so GC on a Postgres catalog never removed
  anything (`repository_pg._cell` reads by name). An install whose catalog is
  SQLite also sweeps the orphan blob rows a Postgres catalog left
  (`retention._sweep_idle_catalog`, same mark and grace). Gate:
  `tests/test_document_idle_catalog_orphans.py`.
- **Schedulers keep their time, and stop.** Chat-app rate limits follow the
  setting live (`rate_feedback.follow_rate_limit_settings`, FA-127); macro
  sleep and reconsolidation stamp their last run, so a reload does not run
  them again (FA-149); `_distinct_tenants` runs in a thread (FA-150); the
  daily digest is sent at `observability.daily_digest.time` (08:00, the
  operator's zone), not 24 hours after the last boot (FA-208); every loop
  sleep is `shutdown.sleep_or_shutdown`, so a stop ends it at once (FA-265).
  A delay before background work is `spawn_background(..., start_after=)`,
  which a stop ends with the work never started; a background coroutine
  that is not `never_completes` never sleeps with `asyncio.sleep` (the crawl
  auto-resume's 20 s wait held a reload for the drain's 10 s, 2026-11-13;
  `tests/test_background_tasks_end_at_a_stop.py`).
  `/abort` pauses an active `/long` task (`pause_long_task_for_abort`,
  FA-148). Gates: `tests/test_rate_limits_follow_settings.py`,
  `tests/test_scheduler_cadence.py`, `tests/test_abort_pauses_the_long_task.py`.
- **Smaller truths.** The CLI prints UTF-8 and answers `--version` (FA-225);
  a malware name with no dot is read (`malware._signature_from`, FA-231); an
  IDE diff keeps its lines (FA-244); research reports the sources it acquired
  (FA-240) and its rubric reads the report's content -- cited paragraphs,
  sources used, citations that resolve, repetition, the topic (FA-235); a
  Gmail listing uses one connection, 8 reads at once (FA-242; the Microsoft
  and calendar readers were not N+1); each swarm template declares its tools,
  so a worker's prompt holds its own (FA-239); every shipped setting has a
  validation rule, a type rule from its shipped value at least, and a restore
  refuses a key this build does not read (FA-251); every workflow action is
  pinned by SHA (FA-209). Gates: `tests/test_cli_prints_utf8_and_version.py`,
  `tests/test_malware_signature_name.py`, `kazma-core/tests/test_ide_service.py`,
  `tests/test_research_counts_sources_acquired.py`,
  `tests/test_research_rubric_reads_content.py`,
  `tests/test_gmail_listing_one_connection.py`,
  `tests/test_swarm_worker_prompt_bounded.py`,
  `tests/test_every_shipped_setting_has_a_rule.py`,
  `tests/test_actions_pinned_by_sha.py`.
- **Secrets at rest.** The security report checks that every secret file of
  the install is readable by its owner only (`check_secret_file_access`,
  `security/file_access.readable_by_others`, FA-272): the restic password
  file, the install's `.env` family, the vault and the broker token
  (`store_registry.install_secret_files`), each other reader named
  (`file_access._account_name`). It checked `restic.pass` alone until
  2026-11-03, when the live install folder was found granting read to a
  group another program had added (`CodexSandboxUsers`), inherited by `.env`
  and `vault.db` too; removing that grant is the owner's. The offsite copy
  leaves every secret-named file out at any depth, zip and rclone alike
  (FA-215, the owner's decision; by the registry's name rule since
  2026-11-10, §62). Gates: `tests/test_secret_file_access_check.py`,
  `tests/test_offsite_copy_leaves_env_out.py`.
- **What Kazma's words say is what the code does** (FA-119/120/121/122/123/
  229). The cron zone hint names its order (`cron.timezone`, `KAZMA_TZ`,
  UTC); the memory mirror, vault and Knowledge auto-inject hints say what
  they do; `python_exec`'s description is built from its constants and its
  timeout is clamped to 120 s; the system prompt's rules are in the shipped
  `kazma.yaml` (installs on the old prompt follow, `RetiredDefault`), and the
  rule to stop all tool calls on any tool error is gone; the Knowledge footer
  is in the turn's language (`knowledge_index.knowledge_footer`) and a failed
  search hands the model `safe_error`'s code. Gates:
  `tests/test_hints_say_what_the_code_does.py`,
  `tests/test_python_exec_description_is_true.py`,
  `tests/test_prompt_rules_reach_the_model.py`,
  `tests/test_knowledge_footer_language.py`.
- **A workflow's token is its own, and a release is published last**
  (2026-11-09). Every workflow declares its permissions (CI and the skill
  review read only; each write is listed with its reason in the gate), so the
  repository's default token can be read-only: three of six declared nothing
  and took a default that could write. The release workflow creates a draft
  with every file attached, publishes last, and refuses a release that is
  already published; a re-run used to replace a published release's files,
  which the updater checks against that release's SHA256SUMS, and GitHub's
  immutable releases refuse. Gates: `tests/test_workflow_permissions.py`,
  `tests/test_release_supply_chain.py` (the old publish step as the control).
- **A tool a workflow or build script runs names one release** (2026-11,
  supply chain). `uvx`/`npx` with no version run whatever PyPI or npm serves
  that day: the release workflow's SBOM tool (`cyclonedx-bom==8.0.1`), the
  PyPI reservation workflow's `twine==7.0.0` and the CodeMirror minifier
  (`vendor_codemirror.TERSER_VERSION`, terser 5.51.2) are exact. Raise a
  version by hand, after trying it. Gate: `tests/test_actions_pinned_by_sha.py`
  (every `uvx`/`npx` package in `.github/workflows/` and `scripts/` --
  `--from`, `--with`, `--package` or the command itself, Python argv lists
  with their module constants resolved -- is `name==1.2.3` or
  `name@1.2.3`; the three old forms as negative controls).
- **AGENTS.md names what exists** (FA-151/152/154). §2 states the `_gateway`
  exception; the stale names, counts and paths of §1-§45 are corrected.
  Every backticked code name here -- a dotted path, a name after its file
  (`graph_tool_worker.py:tool_worker_node`), a page script's camelCase name,
  a bare private name, a test -- must exist; what
  only looks like code is declared with why. Gates:
  `tests/test_agents_md_names.py` (planted stale names as the control),
  `tests/test_gateway_routing_block.py`.
- **A test never writes into the checkout** (found during this stage). A
  fresh workspace store seeded its first active row from the working folder
  even with `KAZMA_WORKSPACE` set, and that row outranks the variable (§10A):
  under pytest the working folder is this repository, and a test's relative
  `file_write` landed in it. The seed takes `KAZMA_WORKSPACE` first, and the
  root conftest pins it to a temp folder. Gate:
  `tests/test_install_data_shield.py` (no variable as the control). **And
  what a test leaves in the store is put back** (2026-11-08): a research
  recovery test's child process, handed the session's `KAZMA_DATA_DIR` but
  not `KAZMA_WORKSPACE`, was first to open the session's store file and
  seeded the checkout, and a later test wrote `d2-<hex>/report.md` into the
  repository. The root conftest makes the store before the first test and,
  after every test, restores the singleton (`peek_workspace_store` /
  `set_workspace_store`) and the file's workspace rows, whoever wrote them
  (`KAZMA_TEST_WORKSPACE_GUARD=0` turns it off); a child given the session
  data folder is given the session workspace too. Gates:
  `tests/test_order_independence.py` (three planted shapes, each with the
  guard off as its control), `tests/test_install_data_shield.py`.

