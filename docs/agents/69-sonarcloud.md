<!-- AGENTS.md, piece 69-sonarcloud. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 69. The SonarCloud review of 2026-11-16

SonarCloud reported 1,623 open issues. Every one was read against the code:
448 were real, 908 false positives (most: `sqlite3.Row` read by column name,
Alpine bindings Sonar cannot evaluate, taint "sources" that are the
operator's own command line) and 267 by design. The real ones, each fixed
with a test that fails on the old code and a gate for its class:

- **shell_exec never runs Kazma's own command line, nor `ps`.** The
  development allowlist (KAZMA_PRODUCTION unset: the default after setup)
  added `kazma`, whose `migrate export` writes the vault and its key into a
  bundle and whose `restore`/`import` replace stores, and `ps`, whose `eww`
  prints the server's environment. `shell_paths.NEVER_ALLOWED` names the
  programs no mode may allow (shells, interpreters, `env`), read against the
  allowlist's source. Gate: `tests/test_shell_exec_kazma_files.py`.
- **A model server's address has one rule**,
  `security.ssrf.check_model_server_url`: this computer or the owner's
  network (`resolves_to_own_network`), else a public address; never cloud
  metadata or link-local. `GET /api/models?base_url=` and `/v1/models` passed
  `allow_private=True`, which returns before every check, so a link the
  signed-in owner opened (a GET carries the cookie) fetched
  `169.254.169.254`; both routes are admin-only now
  (`platform_rbac._ADMIN_EXACT`, exact paths: `/api/models/options` stays
  open). The `KAZMA_ALLOW_PRIVATE_LLM` opt-in is gone. Settings sent the
  provider's key in the address; it is a header. Gate:
  `tests/test_model_server_address_rule.py` (no product code passes
  `allow_private` other than False; no page script puts a key in an
  address).
- **The release job runs only locked tools.** sigstore and cyclonedx-bom ran
  through `uvx`/`uv run --with`, pinning the tool and resolving its
  dependencies fresh in the job that holds the signing identity; they run
  from uv.lock's `sign` and `sbom` groups (`uv run --frozen --only-group`).
  Every `uv build` (release and CI) takes uv.lock's `build` group as
  hash-checked build constraints. Checkout keeps no token
  (`persist-credentials: false`); only the tag push gets it. Gates:
  `tests/test_installs_pinned_by_hash.py` (`uv_problems`: uvx, `uv tool`,
  `uv run` without `--frozen`/`--locked` or with `--with`, `uv build`
  without hashed constraints), `tests/test_release_supply_chain.py`.
- **Kazma Watch's toolchain comes from its lock.** `kazma observer setup`
  ran `npx --yes wrangler@X` (wrangler pinned, its dependencies not) with
  the owner's Cloudflare login and the Worker's secrets. It installs
  `deploy/observer/package-lock.json` (shipped in the wheel) with `npm ci
  --ignore-scripts` in its work folder, once per lock, and runs that copy
  (`npx --no-install wrangler`); Dependabot watches the lock.
  `watch_setup.WRANGLER_VERSION`, package.json and the lock name one
  wrangler (`tests/test_watch_setup.py`).
- **The image leaves out what git leaves out**: `.keys/`, `*.pem`, `*.p12`,
  `*.pfx`, migration bundles, `*.local.yaml`, a `kazma-data/` at any depth and
  the memory repair copies (`tests/test_dockerignore_secrets.py`).
- **OIDC**: the discovery document must name the configured issuer (OpenID
  Connect Discovery 4.3), the id_token is held to that issuer, and PyJWT's
  key fetch runs in a thread (`get_signing_key_from_jwt` is a loop-stall
  helper; `tests/test_oidc.py`).
- **A cancel reaches the code that asked for it** (python:S7497). The
  telemetry and swarm task streams turned a request's cancel into a normal
  end, and `kazma ask --acp` answered "cancelled" to Ctrl+C and kept serving
  while `asyncio.wait` left the turn running; the ACP prompt now cancels its
  tasks and both re-raise. Gate: `tests/test_cancel_not_swallowed.py`: every
  handler of `asyncio.CancelledError` re-raises, sits in one that does, or is
  declared (file, function) with why ending normally is what its canceller
  wants (an owned loop task, a swarm run that records its ending).
- **Correctness found with it** (`tests/test_sonar_correctness_2026_11_16.py`,
  each through the real code, every one failing on the old code): the memory
  backfill made its registry with no settings store, a TypeError at DEBUG, so
  every CLI backfill skipped belief extraction (`backfill_v2._backfill_client`;
  gate: every `initialize_model_registry` call names its store); two packages
  listed names in `__all__` they never defined, so `import *` raised (gate:
  every `__all__` names what its module defines, or a submodule); the SQLite
  graph backend called itself available whatever happened and left a
  connection open per read; the dialect detector gave text with no dialect
  marker MSA at confidence 1.0, English included (now 0.5, formal MSA phrases
  raising it to 0.8); the raw settings `transaction()` rolled back only on
  `Exception`, leaving a cancelled one open on the shared connection; the
  gateway's lifespan stopped nothing when the app ended with an error.
- **Hardening** (`tests/test_sonar_hardening_2026_11_16.py`): no product
  `cmd /c` takes a value Kazma did not write and quote (the restore cleanup's
  `rmdir` and the WAL archive's `mkdir` read an `&` in a path as a second
  command; Python removes and makes them now, taking ownership only for what
  a plain removal left); no source file holds a raw direction-control
  character (Trojan Source; vendored files declared); GitHub's naming rules
  hold every owner and repository a `/repos/` path is built from
  (`routers.github.github_names_ok`: a repository's remote URL could name
  `..`); `recall(conn=...)` gives a plain connection the row factory for the
  call and back after. Also: `--` and `--end-of-options` before the values
  two scripts pass to pg_restore and git, the images install wheels only
  (`--only-binary :all:`), the Settings restart closes its log file, and the
  lifespan's cancelled teardown is no longer logged as "completed".
- **A slow pattern on outside text is fixed, not run in a thread.** Python's
  `re` holds the GIL for the whole match, so a slow match in `to_thread`
  stalls the event loop as much as one on it; only a child process contains
  it. Measured and fixed: the fork-bomb deny pattern every `shell_exec`
  command met before its card (9 s on a 1,204-character command), the code
  span reader of a `generate_pdf` body line (`image_refs`: two single-pass
  functions now), every WhatsApp and Telegram reply's heading, link and image
  patterns, the render-resource check (`documents/resources.py`, each
  reference type scanned on its own; it also let a Markdown image through
  after an HTML `src=`), the skill-manifest and secret scans, reminder
  timings, memory notes, the web chat's Markdown, and the rest of the
  reviewer's list. Gates: `tests/test_sonar_regexes_linear.py` (each new
  pattern's answers equal to the old one's on random input, any intended
  difference tested; hostile input fast; the old pattern's growth as the
  control) and `tests/test_regex_sweep.py`: every `re.*("literal")` in the
  product packages runs in a child process against inputs built from its own
  characters, and one that grows faster than its input fails unless declared
  with why its input is bounded or contained (a planted quadratic and
  exponential pattern as controls; a declared pattern no longer in the code
  fails). The credential patterns in `errors.py` are literals on
  `re.compile` (a concatenation is invisible here) and are not declared.
- **Log lines cannot be forged** (113 issues, one class). Every record passes
  `_LogQueueHandler.prepare`, which now makes it one line: CR, LF, the other
  control characters, DEL, U+0085 and U+2028/2029 are written as visible
  escapes and traceback lines after the first are indented, in every format
  (`logging_config.FORMATTERS`, the file following `logging.format`, the
  console always plain). The readers -- the daily digest, the weekly targets,
  the weekly report, the finished-turn line -- read through one module,
  `observability/log_lines.py` (`read_app_line`, `read_guard_line`,
  `opens_with`), and match a marker only at the start of the parsed message;
  a guard signature reads only the guard record's `event`. A logged value
  could fake a failed turn, a "ran without asking" entry, a day of fresh
  backups or the firings of the backup and restore-drill jobs, in the default
  JSON format too; `/ws/chat/{id}` logged its decoded id before refusing an
  unsigned handshake (it logs `%r`, cut). Gates:
  `tests/test_log_lines_cannot_be_forged.py` (hostile values through the real
  handler in every format, the unsigned handshake as witness, a plain
  formatter as control), `tests/test_log_readers_read_markers_at_the_start.py`
  (every marker after a prefix counts nothing; the readers of the commit
  before as control; no `.search`/`in` on a message in the reader modules).
- **Every control on every page has a name** (479 issues: labels written
  beside their control with nothing tying them). 172 labels gained `for` and
  their control an `id` from its `x-model` path; unnamed controls an
  `aria-label` from the catalog (EN/AR); group captions are
  `fieldset`/`legend`; toggles nested in a second label take the page's
  `toggle-row` shape; switches carry their platform's or provider's name;
  the import chips work from the keyboard; the Swarm dialogs are dialogs and
  close on Escape. Nothing looks different (full-page screenshots compared).
  Gates: `tests/test_controls_have_accessible_names.py` (every page rendered
  through the app; a control named only by its placeholder, or not at all, an
  orphan or nested label, a missing or duplicated `for` fail; planted shapes
  as controls) and the accessibility-tree check of the page tour
  (`tests/e2e/test_pages_load_clean.py`, every page and Settings tab, its own
  negative control).
- **A system program linked out of the tool path still runs** (found the same day: GitHub's
  `ubuntu-latest` became Ubuntu 26.04, where every coreutils program in `/usr/bin` links
  into `/usr/lib/cargo/bin/coreutils/`). `post_hitl.resolve_shell_binary` refused any
  program resolving outside a tool-path folder, so shell_exec ran no `ls`, `cat` or `sleep`
  there; it now also takes a target root owns and no one else may write, all the way up
  (`_root_owned`; a link to a file another user could change is still refused). Gate:
  `tests/test_shell_binary_resolution.py` (POSIX). CI's Postgres is 18 (26.04's `pg_dump`
  writes `SET transaction_timeout`, which 16 refuses; live runs 18).
- **The report says what is product code**: `.sonarcloud.properties` marks
  the suites, the fuzz targets and the Worker's tests as test code and leaves
  out vendored libraries, examples and the audits' evidence scripts. Marking
  the false positives that remain is done in SonarCloud with the owner's
  account.
- **The second pass, BLOCKER and HIGH (2026-11-21)**: 20 open issues, each
  read against the code.
  - **A ContextVar's default is never an object a caller can change**
    (python:S8508): `ContextVar(..., default={})` makes ONE dict that every
    context which never set the variable shares, so a write into what
    `.get()` answered outside a recall would reach every later recall.
    Recall's member names (`recall._ENTRY_NAMES`) default to None and are
    read through `recall._entry_names()` (an empty mapping nothing can
    write). Gate: `tests/test_context_var_defaults_immutable.py` (every
    `ContextVar(` in the product packages and `scripts/`; the old declaration
    and the old default's leak as negative controls).
  - **The backup folder's Test** (pythonsecurity:S2083 x4): the folder the
    admin types is written to by design; reading its rules found device
    paths taken for folders, a second name of the data folder taken for
    another place and a save that kept a path with an unnamed refusal code,
    all fixed (section 21 F, `tests/test_backup_folder_path_rules.py`).
  - **A return value no caller reads is dropped**: `self_improvement._update_soul`
    answers nothing (python:S3516; its three callers count in their own
    *change*). `setup-engine.sh` uses `[[ ]]` throughout: setup runs it only
    as `bash -s` (shell:S7688).
  - Judged safe, for the owner to mark: WinZip AES's fixed algorithms in
    `security/sealed_zip.py` (S4790, S5344, S5542), the operator's own
    command-line arguments in `kazma migrate` and `scripts/` (S8705, S8707),
    and two S3516 whose one returned object is built or edited before it is
    returned (`research_pipeline._candidate_report_roots`, an
    `atomic_update` updater in `space_data._erase_settings`). So is
    `scripts/prepush.py` starting itself again with its own command line under
    the virtualenv's Python (S8705, 2026-11-26).
- **The third look (2026-11-25), the new issues outside memory code, all
  false alarms**, for the owner to mark: `sqlite_snapshot.snapshot_copy`
  (S6549 x2: the path comes from a plan file a soak script writes for its own
  child, and the next line opens that same path, so the link check tells
  nobody more); `thread_rewind.rewind_thread` and
  `chat_delete.remove_chat_remains` (S5145 x3: every record passes
  `_LogQueueHandler.prepare`; each line, with a hostile id in the message and
  in the traceback, was written as one record in all three formats).

