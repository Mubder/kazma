<!-- AGENTS.md, piece 70a-spaces-foundation. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 70. Spaces: the foundation (`kazma_core/spaces/`, 2026-11-17)

Several people on one Kazma (`docs/plans/SPACES_PLAN.md`; its "State of the
items" table says what is done). A space's id is the stores' `tenant_id`.
Stage 1's foundation is in; Spaces is NOT enforced, and the one switch
that turns it on (S1-33, below) is refused until its preconditions hold, so
an install with one account behaves as before.

- **The scope is space and asker, bound together** (`spaces/scope.py`):
  `bind_scope(space_id, asker)` binds both and the tenant ContextVar;
  `install_work(reason)` declares work of the install, which reads no
  space. `require_scope` / `require_space_id` answer the bound scope; with
  nothing bound they answer the owner's scope only while the install does
  not enforce Spaces, and raise `NoSpaceBound` once it does.
  `tenant_isolation.require_tenant_id` asks them. Never catch `NoSpaceBound`
  to fall back to `default`, and never write a new `or "default"` for a
  tenant: the ratchet in `tests/test_no_space_no_data.py` counts the ones
  left and only goes down (the four left are in `memory/recall.py`, which
  the memory session holds, since 2026-11-22). A stored record that names
  no space is read with `scope.space_of_record` (written before Spaces: the
  owner's, N19). A parameter some caller leaves out resolves with
  `scope.space_named_or_bound`: the owner's while Spaces is off -- what
  such code wrote before, so nothing moves -- and the bound space once it
  is on. Not `require_space_id` there: while Spaces is off it answers the
  ambient tenant and refuses inside `install_work`, which would move rows
  today. Work of the install or the machine that needs the owner's space
  names `OWNER_SPACE_ID` and says why. Enforcement is one stored switch
  (`identity.ENFORCED_KEY`) kept in memory (`scope.spaces_enforced`): asking
  for a space reads no settings.
- **Work that knows one row's space says it** (`spaces/row_work.py`, S4-01
  step 0a). Code no request binds -- a job's stages, a recovery, an ending, a
  spool drain -- ran unbound, or in the space of whoever started it. Real work
  for a row runs in `work_of(space, asker)` (`work_of_async` in a coroutine:
  the whole scope, the asker while a member, else `acting_account`; a row of
  no space, or of one being erased, is refused); a person's own act in another
  space of theirs (a move) runs in `resolve_act_of`, as them or refused
  (`AskerLeft`), asked after any wait, never the fallback; only
  statements that name the row's space run in `statements_of(space)`, which
  changes no one's scope; a row Kazma keeps for itself (a memory's mirror
  copy, its vector) is written in `row_statements(space)`: the row's own space
  where Kazma does the work (nothing bound, or the install's door said), the
  bound (or declared) space where a request or a turn does -- a store never
  moves a request into another space (S4-01 0b); a row of another space
  there keeps that door, said as `cross`, which step 1's report counts as a
  finding and `door_hides` tells a delete not to trust (0b review M1, L3);
  the install's own (about every space, or none) run
  in `install_statements(reason)`, declared in `INSTALL` of
  `tests/test_row_work_gates.py`, both ways, and only where no request comes
  with an id of its own; every use of the others is declared there too, with
  where its space comes from and as it is written (`ROWS`), every caller of
  `acting_account` (`ACTS_FOR_A_SPACE`) and every file that binds a scope
  itself (`BINDERS`; none from a request's own words); a loop for every space starts in `install_loops`. A
  loop over rows of several spaces enters one per row. Never held across a
  `yield`. With Spaces off they bind nothing. Report mode (below) asks
  `Door.now` at every checkout; nothing decides by it yet, and
  `tests/_space_door.py` asks at a store's own methods.
- **Every checkout of Kazma's own Postgres is counted by its door** (S4-01
  step 1, report mode, `db/space_doors.py`, 2026-11-26). The shared pool's
  `connection` (and so `execute`), the checkpointer's pool
  (`checkpoints_pg.ReportedCheckpointPool`) and the memory mirror's and
  pgvector's connections, when their address is Kazma's own database, call
  `space_doors.checkout(pool)` first: it asks `Door.now` and counts the
  checkout by door (`space`: `scope` or `row`; `install`: `statements` or
  `work`, with the reason; `none`: a tenant only, or nothing), pool and
  caller (the nearest function of Kazma's own, six for `none`; no space id,
  no row). Nothing is sent and nothing refused, Spaces on or off; a failure
  in the counter is counted and said once. One INFO per caller with no door
  that the report does not hold yet. The report is `space_doors.json` in the
  data folder, added to by the maintenance runner (`space door report`) and
  at a stop; `scripts/space_doors_report.py` prints it against the lists
  below (the file never forgets: `--since` the deploy that fixed a caller).
  The settings table is the install's (`ConfigStore._pg_access`,
  decision 4). **A new connection or pool is a door that counts, or one of
  the install's own sign-ins, declared with why** (`MADE`; read through the
  file's own imports, every spelling psycopg allows); a helper that
  only passes a statement on goes in `space_doors.DOOR_FUNCTIONS`, so the
  report names who wanted it; every function that checks a connection out is
  in `CHECKOUTS`; a caller known to go through no door is in `KNOWN_OPEN`,
  with the step that gives it one and the functions that check out on its
  way (a new one inside it is new), and leaves when that step lands
  (`tests/_space_door_lists.py`; gate `tests/test_space_doors_gates.py`, both
  ways, planted connections, checkouts and a door that stops counting as
  controls; behaviour `tests/test_space_doors.py`, real pools
  `tests/test_space_doors_postgres.py`). `KAZMA_SPACE_DOOR_REPORT=0` turns
  the counter off.
- **Every Kazma table on Postgres has a second-lock rule** (S4-01 step 2,
  `db/row_security.py`, 2026-11-26; nothing arms it yet). `RULES` gives each
  table of `pg_backup.KAZMA_PG_TABLES` -- and pgvector's under its configured
  name (M9) -- one rule: a space's (policy `kazma_space_rows`, `TO PUBLIC`,
  enabled never forced, a row's space `COALESCE(NULLIF(x, ''), 'default')`
  for every table, M6), the intake mutex's narrow grant, or the install's (no
  grant, no policy). The rights of the role `kazma_space` are per table
  (M12: audit and job events SELECT, INSERT; reservations SELECT; blobs no
  DELETE). The policy compares with `kazma_space_id()`
  (`KAZMA_PG_FUNCTIONS`: raises 42501 when no space or `''` is set; every
  restore makes it). **A new Postgres table needs its rule**, in `RULES` and
  in the rights block of `deploy/postgres/kazma_app_role.sql`, which is
  generated from it (`row_security._role_sql_rights()`); the role, its
  membership (`kazma_app` holds it with INHERIT FALSE, SET TRUE; it is a
  member of nothing) and those rights live in that file (M11), granted on a
  space's table only where its policy is on. `apply(url)` puts what is
  missing in place as the tables' owner only (a login that owns nothing would
  blind the install), one table per transaction, 300 ms lock wait, a few
  tries, one budget (M15), and adds the checkpoints' `space_id` column only
  with no space set (its default is the transaction's space). `check(url)`
  compares the catalog with the rules both ways (role, membership, function,
  row security, policies, rights incl. PUBLIC's, schema CREATE, default
  privileges, views, SECURITY DEFINER functions); `space_rows` lists rows
  whose space the registry does not know (M6) and `prove` adds a connection
  as the role (`space_conninfo`: the address's own options kept, the role
  last, M10). A table made later gets its rule as it is made
  (`ensure_schema` -> `apply_made_tables`; the checkpointer after `setup()`),
  only once `apply` succeeded in this process (`in_place`) and only in a
  database that holds the rules: with Spaces off nothing more is sent.
  `kazma doctor` and `kazma spaces migrate --check` show it (not a
  precondition, decision 3); `scripts/row_security.py` checks, lists, proves
  and applies by hand. Gates: `tests/test_row_security_rules.py` (rules both
  ways, every schema maker's `CREATE TABLE`, the role file's rights, the
  function, the address, nothing more sent; controls planted), real Postgres
  `tests/test_pg_row_security.py` (the role file and its repairs, apply
  once, each planted difference, a space's own rows only, no space raises,
  M6, the refusals, a dump's lock left, tables made later, dump, restore,
  the upgrade's copy, the rehearsal, the remove file). Step 2 review: how
  Kazma's login holds the role has one answer, `_membership`, read from
  `pg_auth_members` (`pg_has_role` says yes for a superuser and for a
  PostgreSQL 16 creator's own grant, which has no SET); `apply` grants it
  explicitly and is not `complete` while a role, function or schema finding
  remains (`Applied.unresolved`); a grant or function it may not touch is
  said, never raised. The policies call `public.kazma_space_id()`, which
  PUBLIC may not run (a reader set up to run no function stays one), nor
  any function of `KAZMA_PG_FUNCTIONS`: each DDL revokes it, `apply` does,
  and every whole restore (the upgrade's and the move's copies, the compose
  upgrade, the migration bundle's import) calls
  `pg_backup.revoke_public_execute` after, which fails when PUBLIC still may
  (delta review N1, N4). `rows`
  and `prove` read a database the rules never reached (a missing rule column
  is the owner's rows) and count every space's table in full: on live, when
  it is idle. `scripts/row_security.py` takes another address only from an
  environment variable it is named (`--url-from`), never its command line
  (§26 D); `kazma doctor` gives it one line while nothing is armed.
- **Once armed, the shared pool has a space half** (S4-01 step 3,
  `db/postgres_pool.py`, `db/row_security.py`; nothing arms it yet but tests:
  `row_security.armed()`, step 6 arms at start). `PostgresPool.connection`
  asks `row_security.checkout_space()`: nothing said is `NoSpaceBound` before
  anything is sent; the install's door is the owner's pool as today; a
  space's goes to a second pool on the same address that signs in as
  `kazma_space` (`space_conninfo`; every new connection checked to be that
  role, M10), as large as the install's (M2), and opens the transaction with
  `BEGIN; SELECT set_config('kazma.space', ...)` in one message, its result
  read (M3). After that transaction ends the checkout refuses a second one
  (M4); an install checkout inside a space one is refused unless declared
  (`install_inside_a_space`, M13); a space half that cannot sign in while
  the owner can is its own problem, said, not §44's outage (M1). Those
  refusals are `NoSpaceBound`s: no other store answers for them (H2). The
  step 3 review: the checkout's one message also reads `current_user`, and
  a connection that is not `kazma_space` is closed and refused
  (`_NotTheSpaceRole`); the pool's reset (`end_space`) sends `CLOSE ALL;
  UNLISTEN *; SELECT pg_advisory_unlock_all(); RESET ALL; RESET ROLE;
  DISCARD TEMP` -- a session `SET ROLE NONE` a checkout left made the next
  checkout, in another space, the owner (`RESET ALL` leaves the role), and a
  cursor held past its commit read the first space's rows into the next
  (M1, delta N1); not `DISCARD ALL`, which drops psycopg's prepared
  statements. Settings statements are declared inside a space checkout (the
  space role holds none of the settings table's locks), and only
  `ConfigStore._pg_access` may declare one (a gate, delta N2); the mirror
  reads its region and conflict policy before its checkout (M2, N2). Armed, a refusal is one whether or not
  Spaces is enforced (`pg_helpers._space_refusal`, M3). A cursor of the
  ended transaction is refused too (L1); a held connection rolls back what
  its store left open (L2); a failed space checkout is judged once a minute
  and said as a refused sign-in, connections of another role or a full
  pool (L3); a row of no space's shape is refused before a connection
  (`_NotASpace`, L4); a space checkout inside another is refused unless
  declared (`space_inside_a_space`, L5); while not armed, a workload's
  statements reach the server and nothing more, recorded on the wire by a
  proxy (L7, delta N3). After a deploy,
  read `scripts/space_doors_report.py --since <the deploy's time>`: older
  entries say `none` and are listed as new (L6). Not
  armed, the pool takes today's path and opens no space half. The workers'
  metrics table, which has no space, is the install's
  (`TaskStore.record_worker_metric` and its two readers); so is the chat
  store's whole read into its one cache (`_load_all_from_primary`):
  `KNOWN_OPEN` is empty. The memory mirror and pgvector on Kazma's own
  database take the shared door once armed (`PostgresPool.held_connection`,
  a checkout held until `close()`, tuple rows); elsewhere they stay outside
  the lock. Gates: `tests/test_pg_space_pool.py` (the doors, real Postgres,
  each with its control) and `tests/test_pg_armed_flows.py` (documents,
  swarm, chats and the mirror armed and enforced, seeded as requests bind:
  each space reads its own; not armed as the control).
  `KAZMA_TEST_SECOND_LOCK=armed` runs any Postgres suite armed
  (`tests/_second_lock_armed.py`); a test there that reaches the database
  with nothing bound fails, and binding every test is step 5's.
- **The checkpointer's door** (S4-01 step 4, `checkpoints_pg.py`,
  `db/row_security.py`, the gateway's `stores/checkpoint.py`; nothing arms
  it). The checkpointer's pool asks `row_security.checkpoint_door()` at each
  checkout. Spaces off and not armed: today's path, nothing more sent.
  Spaces enforced: a space's checkout opens its transaction in the space
  (`BEGIN` and `set_config` in one message, `await conn.execute(...,
  prepare=False)`: never `pgconn.exec_` on the loop, review H4, a gate) and
  ends it with `COMMIT` or `ROLLBACK`, so the rows it writes are stamped
  with the space, armed or not (M7); the install's door and nothing said take
  today's path until armed. Armed, a space's checkout goes to the space half
  (an async pool signed in as `kazma_space`, opened on the loop at its first
  use, every connection role-checked and given back reset, as the shared
  pool's), nothing said is refused. `CheckpointManager` runs each saver call
  in the thread's own space (`_door`: its record, or the bound space for a
  call that names no thread); its listing takes each step in the door and
  hands the row on outside it (M5: a door held across a `yield` stays in the
  consumer). `aprune` keeping the latest is refused in a space's checkout
  (L1). A whole `apply` ends with the backfill (design 7.3, at every whole
  pass, not at each checkpointer open): every checkpoint row gets its
  thread's space from `thread_scopes.db`, NULL for the owner's, a wrong
  stamp back -- set-based, the records loaded once into a temporary table,
  two statements per table under the schema lock with a statement timeout
  (step 4 review L6). It trusts this install's records only on this
  install's own database (`availability.is_kazma_database`; elsewhere a
  note: Spaces on replicas is not covered, review M2), reads them read-only,
  and a missing record store, an empty one while rows carry a space, or one
  that cannot be read keeps `apply` from being complete, changing nothing
  (M1: a missing store used to be made empty and read as "no threads").
  The checkpointer's space checkout does not mark `holding(space)` as the
  shared pool's does: a context variable held across LangGraph's yields
  would leak into the consumer (M5), so step 3's nested refusals do not
  cover it (L2). Its pools' connection check keeps a cancel a cancel
  (`checkpoints_pg._check_kept_cancel`: psycopg_pool 3.3.1 counts
  `CancelledError` among a client's errors and goes on; for upstream, L1);
  a closed pool makes no space half (L3). Gates:
  `tests/test_pg_checkpoint_spaces.py` (real Postgres, the saver called
  directly: wire, stamp, a space's own threads, refused, backfill, the
  backfill's trust, prune; each with its control) and
  `tests/test_checkpoint_doors.py` (the door's answers, the manager, M5 and
  its control, the H4 gate over `exec_` and the sync senders, the check's
  cancel, a closed pool). No flow moves a thread between spaces: the chat
  move forks, its copy written under the target's scope.
- **The identity store** (`spaces/identity.py`): accounts, spaces and
  memberships in one document (`identity.REGISTRY_KEY`) changed only by
  `update_registry`, one `atomic_update` per change. The owner
  (`scope.OWNER_ACCOUNT_ID`, the one admin) and the owner's space
  (`scope.OWNER_SPACE_ID`, `default`) are always in it; an install with one
  account stores nothing. The older multi-user mode's users are accounts:
  `platform_rbac._load_users_from_store` reads them and every change goes
  through `platform_rbac._update_users` (one atomic step). Nothing outside
  `platform_rbac` calls `_save_users_to_store`. A personal space has one
  member forever; an unreadable store raises `IdentityUnavailable`, never
  "no accounts". Its functions are loop-stall helpers.
- **Every store declares its space rule** (`store_registry.SPACE_RULES`:
  `row`, `folder`, `install`, `derived`, `host`, what it does today, its
  content columns), and **every route its scope** (`kazma_ui/route_scopes.py`:
  `install`, `space`, `account`, `open`). A new store or route with neither
  fails `tests/test_store_space_rules.py` / `tests/test_route_scopes.py`.
- **A sign-in is an account, or none** (`kazma_ui/space_binding.account_of`):
  the shared secret, an API token and the loopback auto-login are the
  owner's account; a local user's session is that user's; an OIDC session
  is none (refused once enforced).
- **The page names its space on every call** (`static/js/space_scope.js`:
  header `X-Kazma-Space`, or a `space` query value), and the tenant
  middleware checks membership every time, before the handler
  (`space_binding.space_for_request`; 403 `not_a_member`, one answer for a
  space that does not exist and one the caller is not in). The space is the
  tab's, never the session's. **Only on an install that enforces Spaces**:
  elsewhere the page names nothing, reloads nothing and remembers nothing
  (it reads `<meta name="kazma-spaces-enforced">`), and the server runs a
  request that names exactly the tenant it would run in anyway as before
  Spaces; any other named space still needs membership. The older
  multi-user mode's users and OIDC sign-ins are members of no space: as
  first written, a page named the space it was rendered for and every call
  after its load was refused (found in review, before any merge). A test of
  a request rule drives the page's own flow: page load, then a call naming
  the rendered space. **A page that names no space is drawn, an API call
  is not** (`space_binding.must_name_its_space`, 2026-11-18, the first run
  of the two-tab browser test): a GET outside `/api/` and the sockets is
  drawn for the person's own first space, and `space_scope.js` loads it
  again for the space the tab remembers. The sidebar's soft navigation
  drops `?space=` from the address, so a reload refused with 400 left the
  tab unable to get back to its space. **With no sign-in at all** (sign-in
  off) there is no account to check: once enforced, a space route that
  names another space is refused, never answered from the owner's space
  (S4-03 refuses sign-in off at boot once a second account exists). Gates:
  `tests/test_web_request_space.py` (the middleware, and `create_app()`'s
  `/chat`; the old rules as controls).
- **A turn runs in its thread's space** (`spaces/threads.py`,
  `thread_scopes.db`): a thread is recorded once with its space and opener,
  and `turn_segment_budget` binds the turn's scope from the record. Every
  graph run sits in that block (`tests/test_turn_scope_bound.py`). A turn
  whose entry point bound no tenant keeps none bound (the vault tells "no
  tenant" from `default`).
- **The matrix** (`tests/_space_matrix.py`, `tests/test_space_matrix.py`):
  every `space` route and every agent tool has a case, or names the plan
  item that adds it. A new one with neither fails. An item registers its
  cases in a module named in `CASE_MODULES` (Documents:
  `tests/_document_space_cases.py`, run with Spaces enforced, the real router
  behind the real tenant middleware, engines stubbed) and lowers
  `PENDING_BASELINE` in the same change.
- Gates: `tests/test_identity_store.py`, `tests/test_no_space_no_data.py`,
  `tests/test_sign_in_is_an_account.py`, `tests/test_web_request_space.py`,
  `tests/test_turn_scope_bound.py`, `tests/js/test_space_scope.js`, each
  with the old behaviour as its negative control.
