<!-- AGENTS.md, piece 26-default-deny. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 26. Default-Deny Boundaries (2026-08-29 security audit)

Full report and reproductions: the audit artifact and
`tests/test_audit_2026_08_29_regressions.py`. Four boundaries were
default-OPEN; they are now default-CLOSED, and CI keeps them that way.

**A. Peer address is not a credential behind a proxy.**
- `_should_auto_issue_cookie()` mints an admin session for a loopback client
  with no credential — that is what makes localhost use work with no login.
  Behind a same-host nginx/Caddy, `request.client.host` is `127.0.0.1` for
  EVERY internet visitor, so all of them inherited it (finding F-01).
- `KAZMA_TRUSTED_PROXIES` declares the proxy. `X-Forwarded-For` and
  `X-Forwarded-Proto` are honoured from those addresses ONLY. With a proxy
  declared, a request that arrives THROUGH it never gets peer trust
  (`auth._peer_trust_allowed`); a peer that is not the proxy (a connection
  that bypassed it) keeps it, and `KAZMA_LOOPBACK_AUTOLOGIN=1` is the
  explicit opt-in that keeps it for all. Same rule in
  `websocket_is_authenticated()` — its Origin guard passes when the header
  is absent, so a curl client used to get the agent.
- Anything keying state per client (rate limit, login throttle, audit log)
  must call `auth.client_address(request)`, never `request.client.host`.
- The variable is the operator's CLAIM about the topology, and a wrong claim
  used to fail open (under Docker the proxy is the bridge IP, so `127.0.0.1`
  is the natural guess and it is wrong). `_note_forwarded_headers()` settles
  it from the traffic: an `X-Forwarded-*` header from an undeclared peer
  latches `undeclared_proxy_detected()` and revokes peer trust for the
  process. `proxy_health()` surfaces the state on `/api/auth/status`.
  The latch never clears at runtime — it may only close doors, never open
  them, which is also why spoofing the header gains an attacker nothing.
- **The app applies forwarded headers, not uvicorn**
  (`kazma_ui/proxy_headers.py`, the OUTERMOST middleware): it records the
  TCP peer (`auth.TCP_PEER_SCOPE_KEY`, read by `_peer_host`), then runs
  uvicorn's own `ProxyHeadersMiddleware` for the declared proxies, so routes
  see the same client and scheme as before. Every launcher passes
  `proxy_headers=False`. With uvicorn rewriting first, `_peer_host` read the
  forwarded client as the peer: behind Cloudflare Tunnel (declared
  `127.0.0.1`) the detector flagged the tunnel's own visitors on every boot
  (2026-09-23 to 2026-09-25) and advised trusting a visitor's IP, and
  `_peer_trust_allowed` saw the visitor instead of the proxy. Gate:
  `tests/test_forwarded_headers_peer.py` (real ASGI layers — fake requests
  whose `client` is the peer are why the unit tests never saw it).
  Entries may be CIDR ranges; `auth._is_trusted_proxy` is the ONE answer to
  "is this peer a declared proxy" (every check asks it, never set
  membership), and `*` is dropped so neither layer trusts every peer.
- **One body limit per request** (`kazma_ui/body_limits.py`, 2026-11-15, the
  defensive check before the independent review: the login route read a
  body of any size before sign-in). Outside every gate, a declared length
  past the route's limit is 413 unread and a chunked body stops at it; 64 KiB
  before sign-in, 2 MiB signed in, larger limits declared per route
  (`_route_body_limits`). `upload_limits.UploadSizeLimitMiddleware` is its
  old name. Gate: `tests/test_request_body_limits.py` (the built app's
  routes both ways).
- **A client that went away is not a server error**
  (`kazma_ui/client_gone.py`, 2026-11-25): a page closed while it was sending
  its request was a WARNING, an ERROR with a traceback and uvicorn's
  "Exception in ASGI application". `ClientGoneMiddleware`, outside everything
  but the forwarded headers, ends it with one DEBUG line, only when the
  request's own disconnect message arrived before its answer was complete AND
  the error is `ClientDisconnect` alone: a group with another member, one
  raised over a real failure, and `CancelledError` go on as before. A request
  cut off mid-body is answered nothing, and the activity and memory trails
  note its failure as 499 (what a route did all the same stays done). A `try`
  that reads a body and catches everything re-raises `ClientDisconnect` first
  (the document upload logged it as a failed upload); a body written to disk
  as it arrives is removed when it stops (the history import kept half an
  export). Gate: `tests/test_client_gone.py`.
- **Every credential but the shared secret carries a role** (API tokens: the
  role chosen at creation, operator by default), enforced in every mode, and
  `admin_decision` gives the same answer. A token handed to another agent
  for A2A could add an MCP server on a single-operator install
  (`tests/test_api_token_roles.py`).
- **Install-wide writes are admin** (`platform_rbac._ADMIN_WRITE_PREFIXES`:
  GitHub credentials, the active workspace and its folders, the chat apps,
  division requests, model profiles; `tests/test_install_wide_writes.py`
  finds every write route whose code reaches a credential, workspace or
  process sink). A stored key stays with its address: a masked key posted
  with a changed address is refused for profiles, providers and checks
  (`url_credentials.same_address`).
- **Gates judge `request_path.judged_path`**, the path the router serves,
  never `request.url.path` (a decoded `%3F`/`%23` cut it short); a decoded
  `?` or `#` in a path is refused (`tests/test_gates_judge_the_routed_path.py`).
  The login throttle groups IPv6 by /64 and keeps 4,096 keys at most; every
  refused local sign-in costs one full PBKDF2. `.local`, `.lan` and `.test`
  names are this machine only when `KAZMA_AUTOLOGIN_HOSTS` lists them.

**B. HITL default-denies.**
- `requires_approval()` ends on the `TOOL_TIERS` classification, not on a
  name list. An unclassified tool is GATED. **Every tool you register needs
  a tier** — `read` / `write` / `danger`; anything destructive, outbound, or
  credential-touching is `danger` and also belongs in
  `CANONICAL_DANGER_TOOLS` + `kazma.yaml`.
- A configured `require_approval_for` list ADDS to that floor. It can no
  longer un-gate `shell_exec` by omission.
- CI: `test_every_registered_tool_has_a_tier`, `test_danger_tools_are_gated`.

**C. Allowlisting a binary is not allowlisting what it runs.**
- `shell_exec` vets `argv[0]` and rejects shell metacharacters, but a bare
  program name is not path-shaped and `find`'s `+` terminator sidesteps the
  `;` rejection — `find . -exec whoami +` walked past the allowlist (F-03).
- `_EXEC_CAPABLE_ARGS` rejects per-binary flags that execute another
  program (`find -exec`, `git --upload-pack`/`-c`, `tar
  --use-compress-program`, …). Add an entry when you add a binary.
- **`shell_exec` git: the card is the gate, and it names what the repository
  runs** (2026-11-11, the owner's decision). Every git command it runs gets
  `-c core.fsmonitor=false` before its subcommand
  (`git_repo_programs.shell_git_argv`, added after the policy that refuses a
  `-c` the call names). The approval card (chat graph `card_notes`, chat
  apps, swarm bus) names the hooks present (not `.sample`) and every program
  the repository's own `local`/`worktree` configuration sets -- filter, diff
  and merge drivers, `core.sshCommand`, `core.pager`, a shell alias that is
  the command (`_repository_programs`, read with `git config --show-scope` and
  `git rev-parse`, which run none of them, with `tool_child_env`, off the
  loop). Nothing else is switched off: LFS and the owner's hooks keep
  working. One splitter for the command's words
  (`shell_arguments.split_command_line`). Gate:
  `tests/test_shell_git_programs.py` (real repositories, a global
  configuration of the test's own; the argv without the override as the
  control).

**D. Secret masking recurses.**
- `settings.mask_deep()` walks dicts, lists, and JSON-encoded strings.
  The old two-level version skipped lists, so `providers.list` shipped six
  live API keys in the clear (F-02). Key matching is on `.`/`_` token
  boundaries — a substring test made `pat` match `selected_path`.
- New API surfaces that echo config must go through `mask_deep`.
- **A provider row goes out through one masker**
  (`security/provider_mask.mask_provider_entry`, 2026-11-15): the key, extra
  keys, headers, AWS credentials and the address's password. The unified
  model options (`ModelRegistry.list_unified_options`: `GET
  /api/settings/models/options`, and `GET /api/swarm/models`, open to any
  signed-in user) returned the rows raw, and the Agents page showed the
  address with its password. Gate: `tests/test_no_get_route_answers_a_provider_key.py`
  walks every GET `/api` route with a provider whose every secret is a
  sentinel (the raw options as the control).
- **Masking is by key AND by value.** A password inside a URL is a secret
  whatever the key is called (`kazma_core/security/url_credentials.py`, the
  one home): `memory.backends.state.url` held the live Postgres DSN in
  plaintext and the Settings API returned it (2026-09-25), because every
  masker decided by key name. Every function named mask*/redact* that decides
  by key is found from the source and fed a URL password by
  `tests/test_url_credentials.py::test_every_key_name_masker_also_masks_url_passwords`;
  a new one needs a probe there. The mask is `****`, which
  `is_masked_secret_placeholder` refuses to write back, so a form can round-trip
  it. Provider, profile and connector saves go further
  (`url_credentials.restore_masked_url`, 2026-09-26): a URL posted back
  exactly as shown keeps its stored password; stars with any other change
  (host, user, path) are refused with a 400 — a stored password is never
  moved to a URL someone just typed (`tests/test_url_password_round_trip.py`).

**E. Nothing blocking on the event loop; nothing fire-and-forget.**
- A sync `sqlite3.connect` inside `async def` pins the loop that serves
  every SSE and WebSocket stream. Drop `async` (FastAPI threadpools sync
  handlers) or wrap in `asyncio.to_thread`.
- `asyncio` holds only a WEAK reference to a task, so a discarded
  `create_task(...)` can be garbage-collected mid-run, silently. Use
  `kazma_core.background.spawn_background(coro, name=…)`. Bare
  `loop.create_task` in product code (including `ops_alerts._dispatch`)
  is the same class of bug — prefer `spawn_background`.
- CI: `test_no_blocking_db_driver_in_async`, `test_no_bare_create_task`.
- **A cancelled task is waited for with a bound** (`kazma_core/bounded_cancel.py`,
  2026-10-23). A cancel that lands while anyio reads an httpx socket is
  sometimes absorbed (3.11 + selector loop, 3 in 4,000), and a loop that
  catches errors then runs on: the typing indicator's `stop()` awaited one
  under its lock, and every later message on every chat app waited on it
  before its turn began -- two hours of silence on Telegram, no error. Use
  `cancel_and_wait` / `wait_then_cancel` / `wait_for_bounded`; never
  `X.cancel()` then `await X`, nor `asyncio.wait_for(<task>)` (on timeout it
  cancels and waits unbounded), and never await a task's end while holding a
  lock others need. A loop that must stop also checks a stop flag. Gate:
  `tests/test_bounded_cancel.py` (the old shapes and the old indicator as
  negative controls).
- **The LLM ledger row is written off the loop** (2026-10-15). Async code
  awaits `observability.llm_ledger.arecord_llm_call`: the SQLite insert and
  the ledger's switch (a settings read) run in a thread, the caller waits
  for its row (a turn's totals, `turn_usage`, read after its calls returned,
  include them, in the order made), and the write is shielded, so a
  cancelled caller loses no row and waits for it, bounded. `resilient_chat`
  (every swarm worker and research model call) wrote it on the loop until
  then. `record_llm_call` and the ledger's readers are in
  `_LOOP_STALL_HELPERS`. Gate: `tests/test_llm_ledger_off_the_loop.py`
  (SQLite traced on the ledger's connection; the inline, unawaited and
  unshielded writes as negative controls). The non-stop settings the
  failover chains and the turn's envelope read (`get_nonstop_config`:
  kazma.yaml, then the settings store) are a loop-stall helper too; the
  supervisor, `resilient_chat`, `run_agent_turn` and the runner await it in
  a thread.
- **Every model call is in the ledger, with the cache's share** (2026-11-22,
  the memory plan's findings 6 and 9). Memory's own calls -- the deep pass,
  the weekly summaries and roll-ups, the lesson refiner, the V1 backfill --
  were in no ledger, so no spend report, product measure or person's daily
  limit counted them; they go through `llm_ledger.ledgered_chat` (one call
  and its row, thread `memory:<work>` when none is bound, whose call from
  the bound scope; a failed call an `error` row). And no row counted the
  prompt tokens a provider served from its cache: an answered call's row
  hands its usage over (`usage=`) and the ledger reads it with
  `kazma_core.token_usage.token_counts`, whatever the shape (DeepSeek's
  `prompt_cache_hit_tokens`, OpenAI's and Qwen's
  `prompt_tokens_details.cached_tokens`, Anthropic's
  `cache_read_input_tokens` beside an `input_tokens` that leaves it out --
  read raw, an Anthropic row's prompt counted none) into `cached_tokens`;
  every sum gives `prompt_tokens` and `cached_tokens` beside each other.
  Entity resolution's tier 3 (a model deciding a merge) is removed: its
  `client.chat(` was a coroutine never awaited, so it never reached a
  provider; no product code calls a client's chat without awaiting it
  (`tests/test_coroutines_are_awaited.py`). Gate:
  `tests/test_memory_calls_ledgered.py` (the deep pass EN/AR, the summaries'
  chat and the refiner through the real code; today's calls, today's rows
  and planted sites as the controls).

**F. Errors do not carry internals.**
- API handlers return `kazma_core.errors.safe_error(exc)` — a stable code
  plus a correlation id, with the real exception logged under that id.
- 4xx **validation** paths use `validation_error(exc)` instead: the message
  is the caller's answer, and replacing it with a code makes the API
  unusable. Redaction applies either way. A typed refusal of Kazma's own
  (`PeopleRefused`, `SpaceMoveRefused`...) keeps its sentence in a `message`
  field, and a route answers it with `refusal_message(exc)` (or the codes it
  carries): the field, redacted, never the exception's text (2026-11-26,
  section 66). One with no field answers the catalog's
  `common.request_refused`, in the reader's language. Gates
  (`tests/test_codeql_triage_2026_11_26.py`): a route handler that catches
  a class named `...Refused` and hands it to `validation_error`, `str` or an
  f-string fails; every class `refusal_message` is handed keeps the field,
  and nothing else is handed to it (a built-in caught beside a refusal, an
  expression).
- **No answer carries an exception's own text** (2026-11-14). A page that
  shows the error gets `described_error(sentence, exc)`: Kazma's sentence
  with the code and the reference ("Could not resolve the clone URL from
  GitHub (upstream_unavailable, ref: 1a2b3c4d)."), the text logged under it.
  The clone route, the email and X helpers (`str(exc)[:300]` whenever
  `KAZMA_PRODUCTION` was unset), the backup delete, the memory backend
  tests, the MCP add and the Knowledge page's "not stored" reasons answered
  library, OS and driver text: paths, hosts, SQL. A mail server's login
  answer, an FTP server's reply and the X API's answer stay (what the owner
  fixes). Gate: `tests/test_route_errors_carry_no_exception_text.py` -- every
  module under `kazma-ui/kazma_ui` and `kazma-gateway/kazma_gateway/routers`,
  every `except ... as e` (and parameter typed as an exception) whose text
  reaches a `return`, `raise`, `yield` or `*Response(...)`, also through a
  local value; Kazma's refusal classes and one site are declared with why,
  a declaration that excuses nothing fails, planted old shapes as the
  negative control.

**G. Fenced tool output.**
- Fetched pages, search results, saved research chunks and MCP resource
  bodies go through `prompt_fence.fence_untrusted()`. So do memory and
  Knowledge read back by a tool (Memory audit M11, Knowledge KB-S01,
  2026-10-13): `memory_search`, `memory_list_*`, `memory_admin`'s lists and
  `knowledge_search` (its citation directive stays outside the fence).
  `memory_search` shows a memory's display text and says "unavailable, NOT
  an empty result" when recall fails. The per-function gate
  (`FENCED_TOOL_FUNCTIONS`) reads tools registered inside `register_*` too.
  They are the largest source of attacker-controlled text in the system and
  used to reach the model raw. CI: `test_no_unfenced_web_tool_output`.

**H. One writer for the procedural recorder.**
- A thread-per-tool-call used to crash Windows (`AccessViolation` in FTS /
  schema / ConfigStore — three faces of one race). **One worker thread**
  now drains a bounded queue; schema is ensured once per process; the queue
  drops rather than blocks; `atexit` drains it. Do not reintroduce a thread
  per call. Repro: `tests/test_truncation_retry.py`.
- `reset_config_store()` must not close the sqlite handle out from under a
  background reader. Closing is **opt-in** (`close=False` is the default).
  The recorder reads `read_memory_cfg()` on the caller thread so the worker
  never touches ConfigStore. **A default every caller must override to be
  safe is the wrong default.**
- The *explicit* `ConfigStore.close()` was the remaining way to free that
  handle under a reader, and it did — see §8. Repro:
  `tests/test_config_store_close_race.py`.

**I. A process a tool starts gets no secrets (2026-09-26).**
- `shell_exec` / `python_exec` always used a minimal environment. pytest
  (`run_unit_tests`, the patch-set verify run), `pip`/`npm` installs, `ruff`,
  git and the IDE's read-only git passed the server's whole environment --
  `KAZMA_VAULT_KEY`, the database password, API keys -- to code nobody
  reviewed: a repository's conftest and tests, install scripts, hooks,
  `core.fsmonitor`.
- `security/child_env.tool_child_env()` is the builder: the server's
  environment minus every `KAZMA_*` name, credential-named variables and URLs
  carrying a password (git keeps HOME, its credential helper, SSH_AUTH_SOCK).
  `run_off_loop` (the skills' runner) fills `env=` with it when the caller
  does not; a caller that builds its own env starts from it
  (`get_commit_env`, `_git_sync`). `KAZMA_CHILD_ENV_ALLOW` lets named
  variables through, never `KAZMA_*` ones.
- Gate: `tests/test_child_env.py` finds every process started under the
  tool, IDE and skill folders and checks where its env came from (closures
  included); behavioural half runs real pytest and a real git hook.
- **The gateway routers, chat commands and web UI too** (2026-09-30):
  `/api/git/status` ran `git status` -- which starts a repository's own
  `core.fsmonitor` -- with the server's environment, and the web and `/ide`
  clones passed `{**os.environ}`, which the gate took for a minimal dict
  literal. Git in a workspace now runs with `tool_child_env()` and
  `-c core.fsmonitor=false`; a clone's token rides
  `github_client.clone_auth_env` (a git config header, never argv). The gate
  covers those folders, sees `asyncio.to_thread(subprocess.run, ...)`, and a
  dict that unpacks another is not minimal. Settings' server restart is the
  one exemption (it starts the server itself).
- **A minimal environment keeps Windows' `SystemDrive`** (2026-11-14). Every
  list of variables a child keeps held `SystemRoot` and dropped `SystemDrive`
  (the browser's, the page parser's, the search and DNS workers', the document
  workers' and broker's, the shell tool's, the local code runner's), and
  Windows expands `%SystemDrive%` in its own paths: the browser's font cache
  made a folder literally named `%SystemDrive%` in the live install. Gate:
  `tests/test_child_env_keeps_system_drive.py` (every literal list in the
  product that keeps SystemRoot keeps SystemDrive; a real `cmd` started with
  the old list prints `%SystemDrive%` as the control).
- **A shell command's home and temp folder are its own**
  (`post_hitl._tool_home_for`, 2026-10-02): one folder per workspace under
  the system temp folder (`kazma-tool-home/<hash>`, kept between commands so
  caches work), for HOME, USERPROFILE, APPDATA, LOCALAPPDATA and the temp
  variables. They were the workspace itself, so every tool a command ran
  kept its user-level files in the repository the agent was working in: the
  live install folder (its workspace) held 143 MB of uv cache and temp files
  where `git add -A` would commit them. Still never the operator's own home.
  `tests/test_shell_child_home.py` (a real child; the old environment as the
  negative control).




