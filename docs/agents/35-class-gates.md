<!-- AGENTS.md, piece 35-class-gates. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 35. Class gates from the 2026-09-22 audit — fix the class, not the instance

Every finding in that audit was a correct fix in one sibling and missing from
the next. Each now has a gate that enumerates the siblings from the real
source, with a negative control. When one fails, fix the code; do not edit
the gate to pass. Full list with evidence: `docs/KNOWN_GAPS.md`.

- **One copy of each decision.** Admin: `kazma_ui.auth.admin_decision`
  (fail-closed; wrappers keep their response shape). Thread ownership:
  `kazma_ui.thread_ownership` (fail-closed, off the loop). `.env` loading:
  `kazma_core.env_files.load_env_files`, called by entry points, never on
  import — and a program under `scripts/` that imports Kazma is an entry
  point too (`tests/test_env_loading.py` enumerates them: load it, or be
  declared env-free or own-env with the reason; the gate once saw only the
  packages, and eleven scripts plus the guard's pager lost the vault key and
  the DSN). Untrusted XML: `kazma_core.security.safe_xml`. Child rlimits:
  `kazma_core.security.rlimits` (no `preexec_fn`). UI writes:
  `window.kazmaSave` (never discard an awaited `fetch`). A process's start
  (the pid-reuse guard of every owner record: crawls, research sessions and
  storage, origin leases, document intakes, migration folders):
  `kazma_core.process_start`. A record carries a stamp beside its start
  time: on Linux the boot id and the start's ticks after boot, matched
  exactly for any process; on macOS and the BSDs an id of this process,
  with another process's start matched within 10 s; on Windows none, the
  kernel-kept start matched within 2 ms. On Linux psutil's `create_time()`
  adds the boot time, read again on every call, and it steps with the wall
  clock: on Docker Desktop's WSL2 VM a whole second about every 30 s under
  load, and the Knowledge ingest took a live crawl over (prepush,
  2026-11-27). No other module reads a start, the boot time or
  `/proc/<pid>/stat`; the guard keeps its own copy of the Linux stamp for
  its orphan check, held to the helper's (`tests/test_process_start.py`).
- **Every thread-taking route declares its rule** in
  `tests/test_thread_route_policy.py` (owner / admin / admin+owner / session).
- **Closures scheduled from a loop bind the loop's names** as defaults or
  method arguments (`test_no_deferred_closure_reads_loop_rebound_names`).
- **`asyncio.to_thread`, never `run_in_executor`** — only `to_thread` carries
  the tenant/workspace/HITL ContextVars into the thread.
- **No blocking I/O inline in `async def`.** A route handler that never
  awaits is a plain `def` (FastAPI threadpools it, context included); one that
  does awaits `to_thread` around its store calls. An `async def` façade over a
  sync body (`KnowledgeIndex.search`) runs the body in `to_thread`. So does
  an async agent TOOL: the registry runs async tools on the loop and threads
  only sync ones. The debt ratchet counts both shapes that slipped past the
  name-based gates -- `async_inline_db_calls` (a DB-API `execute`/`commit`
  made in async code, e.g. on a connection opened in a thread) and
  `async_tools_never_await` -- and they only go down. DNS is I/O:
  `await asyncio.to_thread(validate_url, …)` — keep the name so test patches
  still apply (`test_no_blocking_dns_in_async_functions`). Route walks with
  loop-detecting fakes: `tests/test_kb_api_routes.py`, and for the chat
  session routes (list, history, rename, archive, pin, status, delete --
  the store on the loop until 2026-09-28, 150-400 ms a list on the live
  Postgres) `tests/test_chat_session_routes_off_loop.py`, which wraps the
  real store and records any call made where a loop runs. An
  `httpx.AsyncClient(...)` built in async code passes
  `verify=shared_ssl_context()` (`kazma_core.http_tls`, one context built in
  a thread at boot) -- the default loads the CA bundle in the constructor,
  on the loop (`test_async_http_clients_share_the_tls_context`).
- **Every memory route keeps to the caller's tenant**
  (`tests/test_memory_routes_tenant_scope.py`, 2026-09-27). Until then twelve
  took any tenant's ids or counted every tenant's rows -- invalidate, merge
  decisions, graph groupings, rename, unlink, hygiene, health, quality, the
  episode list -- and the beliefs list failed outright for a tenant-bound
  caller (its tenant predicate had no value bound). A new route needs a
  request in the gate's table or fails. See §16's multi-tenant note.
- **A tool's structured argument names its keys** (2026-11-12). A tool's
  schema is generated from its signature (`kazma_core.agent.tool_schema`),
  and `list[dict]` told the model no key: it guessed, and the engines
  dropped what they did not read (live, every slide title of a deck, sent as
  `title` to an engine reading `heading`). A list of records is a TypedDict
  (keys, types, `Annotated` descriptions; the document records are
  `kazma_core.documents.payloads`), and a mapping whose keys are data (a
  form's fields) is described in a `name: description` docstring line.
  Gates: `tests/test_tool_parameters_name_their_keys.py` (every registered
  tool; the old signatures as the negative control),
  `tests/test_document_tool_records.py` (every key of every record reaches
  the rendered document; the engine that read only `heading` as control).
- **Every product module is reached** (`tests/test_orphan_modules.py`); a
  module only its own tests import fails, unless allowlisted with a reason.
- **Every PowerShell script Kazma ships can start**
  (`tests/test_powershell_scripts_start.py`, 2026-11-06). setup.ps1 declared
  its own `-Debug` under `[CmdletBinding()]`, which gives an advanced script
  PowerShell's common parameters, so PowerShell (5.1 and 7) refused to start
  it: from 2026-06-27 the Windows install's first step could not run (found
  setting up the drill VM; parsing and `Get-Command` both succeed, only an
  invocation fails). An advanced script declares no common parameter, read
  by PowerShell's own parser and without one; a script with non-ASCII text
  starts with a UTF-8 BOM (5.1 reads a BOM-less file in the ANSI code page,
  and `industry_smoke.ps1`'s em dash ended a string); a script that sets
  `$ErrorActionPreference = "Stop"` redirects a program's stderr only inside
  its `Invoke-Native` helper (5.1 makes each redirected stderr line an error
  record, and under "Stop" uv's "Using CPython ..." ended setup at `uv
  sync`); setup.ps1 is run in an empty folder and, on Windows PowerShell,
  past `uv sync` with a stand-in uv that writes to stderr.
- **No function reads a name it imported on another path**
  (`tests/test_unbound_local_imports.py`, 2026-10-02). Any import in a
  function makes the name local to the whole function, so a read on a path
  that skipped the import raises UnboundLocalError; ruff's undefined-name
  check cannot see it. `send_file` imported `asyncio` in its web branch and
  called it on every other path (two days of "No active chat channel" from
  reminders and the CLI, hidden by a DEBUG line), and `discover_models`
  named `SSRFError` in an `except` the import had not reached. The gate is
  definite-assignment over every function in the packages and `scripts/`; an
  import of Kazma's own modules or the standard library counts as unable to
  raise, a third-party one can. Import at the top of the function (or the
  module), never inside the branch that happens to need it first.
- **A SQLite connection kept across calls cannot sit on the write lock**
  (`tests/test_sqlite_kept_connections.py`, 2026-09-27). In Python's default
  mode every INSERT/UPDATE/DELETE opens a transaction -- also one that changes
  nothing (the cron store, §16 C) and one that raises (fourteen more stores:
  the memory writer, the LLM ledger, the semantic cache...). Every
  `sqlite3.connect`/`aiosqlite.connect` product code keeps on an attribute or
  a global is found from the source (also one opened into a local and
  stored later), and it is one of: autocommit
  (`isolation_level=None`; a group of writes is an explicit `BEGIN
  IMMEDIATE` -- `db.sqlite_session.write_transaction` on aiosqlite, which
  runs the groups on one connection one at a time: a second `BEGIN` on an
  open group fails); a
  `sqlite3` connection every write on which runs inside `with conn:` (commit,
  or rollback when the block raises); or LangGraph's saver connection, on
  which Kazma writes nothing of its own. An aiosqlite connection kept in the
  default mode is refused: `async with` closes it, it is no transaction.
  Every explicit `BEGIN` in product code rolls back on its error path (same
  file). Behaviour: `tests/test_kept_connection_error_paths.py` makes each
  store's write fail inside the statement and takes the lock from another
  connection.
- **Debt ratchet:** `tests/test_debt_ratchet.py` holds the blind/silent
  exception-handler counts; they may only go down, and lowering them means
  updating the baseline in the same change. It counts untracked files too:
  listing only committed files let a local run pass that CI then failed on a
  new module (2026-09-26).
- **Loop-stall dumps name the next gate entry.** `kazma_core.observability.
  loop_stall` writes every thread's stack to `.kazma/stall-*.txt` when the
  loop is unresponsive for 15s, and the weekly ledger counts them. The frame
  on the loop thread is a sync helper doing I/O from async code; add it to
  `_LOOP_STALL_HELPERS` in `tests/test_static_gates.py` and every async call
  site must then use `to_thread` (`test_loop_stall_helpers_are_not_called_on_the_loop`).
  The first pass (2026-09-23) found 12 helpers from 70 dumps, and 17 more
  call sites of them nobody had caught yet. The second (2026-09-26, 50
  dumps) was led by the auth middleware -- 8 dumps of a per-request
  user-store/session read on the loop, one AFTER the first pass -- then the
  MCP reconnect sweeper (49.7 s) and the readiness probe; 18 names added,
  5 more callers found by the gate itself. Rank the dumps by the first frame
  ABOVE the storage layer (the caller that ran it on the loop is the news,
  not `postgres_pool.execute`). Every dump now leads with the loop thread's
  stack: faulthandler stops at 100 threads, and the eleven dumps of the
  2026-09-25 database hang (one a 318 s stall) had none. The weekly report
  names the blockers of the week's dumps itself (§27C). Third pass
  (2026-09-30): the restic maintenance handler's repo/password reads, the
  workspace ladder (`resolve_active_root`, six async tools) and the GitHub
  token helpers (a token lookup can mint a GitHub App token over the network).
  Fourth (2026-10-01): switching the model or provider (`switch_active_model`,
  `switch_active_provider`, `ensure_active_model`,
  `maybe_activate_provider_for_chat`: settings and vault writes, then the
  agent rebinds and the graph compiles again) and the provider list's writes
  (`set_provider_health`, `upsert_provider`) ran on the loop from the
  Settings route, the chat's provider switch, setup, the provider Test and
  the `/model` menu. Twelve provider and setup routes that never awaited are
  plain `def`s now. Fifth (2026-10-01): the chat store on the turn's own
  path. `get_by_thread_id` is a Postgres round trip under the store's lock
  when the session is not cached, and `persist_reply` (-> `upsert_reply` ->
  `store.transact`) takes the session's thread lock and writes the whole
  session before releasing it -- `close_turn`, the SSE stream's terminal and
  crash writes, the approve and clear-approvals routes, the approval timeout,
  hard steer and reminder delivery ran them on the loop, each up to the pool's
  5 s while the database is away. With them: the sync helpers that wrap the
  lookup (`resolve_session_id`, `ensure_session_for_thread`,
  `resolve_reply_turn`, `persisted_hitl_for_thread`), and the MCP server list
  behind `/mcp` and `/api/mcp/servers` (`get_mcp_servers`, `oauth_status`).
  No stall dump had caught them: a normal write takes milliseconds; an
  outage is when they stall.
  Sixth (2026-10-02): the agent's file tools. `resolve_tool_path`,
  `check_path_access` and `_workspace_scope_error` read the workspace store,
  the durable roots and the session grants, and every async file tool ran
  them on the loop; so did 14 tools through `file_write._get_workspace` (an
  alias of `resolve_active_root` the list never named), `file_write`'s
  re-index (`notify_file_changed`: settings, the workspace, SQLite, a parse),
  and `request_path_access`'s settings writes. Async tools take
  `path_policy.resolve_and_check` or `tool_scope.resolve_in_scope` (one
  thread for the lookup and the check); a tool whose body never awaited
  (`file_apply_patch`, `request_path_access`) runs the body in a thread.
  **The gate follows private wrappers now**: a private module-level sync
  function that reaches a listed helper, directly or through another such
  function in the module, counts as one (`_module_stall_wrappers`, public
  names never inferred). That found 39 more calls through 16 wrappers: the
  research pipeline's path helpers and its ConfigStore write, `read_url`'s
  research loaders, the MCP manager's path gate, the gateway's web-session
  sync (four per turn, chat-store writes), the document tools'
  `_resolve_input` (8), `x_test`'s status payload.
- **On the memory path the inference follows imports** (2026-10-13, Memory
  audit M34): `test_an_area_reaches_no_helper_through_another_module`
  resolves calls through each module's own `from kazma_... import` bindings,
  public functions included, to a fixed point (`_qualified_stall_helpers`),
  and holds the memory modules to it. Undo resolved its tenant on the loop
  through three hops in two modules (`_consume_undo` -> `_memory_tenant_id`
  -> `multi_user_or_production` -> a settings read); the memory worker raised
  ops alerts and queued retries on the loop. **Since 2026-10-27 it covers the
  whole product** (audit 2026-10-26 AUD-111): every product module is in an
  area (`test_every_product_module_is_in_an_area`), calls resolve through a
  module's own bindings, `self`/`cls`, a class's methods and modules imported
  whole (`_StallModule`), and a direct settings-store use seeds a stall like
  a listed helper. A nested def handed to a thread is not its parent's call.
  A function that keeps its read off the loop when it runs on one is declared
  in `_OFF_THE_LOOP_WHEN_ON_IT` with the test that proves it; a call that
  reads nothing at that site in `_CALLS_THAT_READ_NOTHING_HERE`. About 230
  sites were converted. Not held: chat-store methods on a receiver the gate
  cannot resolve (`get_session_manager().get`). The memory GET
  routes write nothing (`test_a_memory_get_route_writes_nothing`, every GET
  traced): `memory_api._conn()` makes the schema ready once per process
  (it ran the entity-count backfill on every request), and the operator's
  graph search is a read-only diagnostic (no access bump).
- **The settings store is never used on the loop** (2026-09-30,
  `test_the_settings_store_is_not_used_on_the_loop`, negative control beside
  it). On Postgres a settings read that misses the cache, and every write, is a
  round trip made with the store's lock held -- up to the pool timeout (5 s)
  per call while the database is away. The heartbeat's `atomic_update` and
  the restic handler's read sat on the loop during a two-minute Postgres
  outage and the guard restarted Kazma (§44). 93 direct calls in async code
  were converted: `await asyncio.to_thread(store.get, key)`, or a plain `def`
  (a route or tool that never awaited). The gate sees `get_config_store().x()`,
  a name bound from it, and any `*.config_store.x()` receiver.

