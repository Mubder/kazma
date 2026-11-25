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
  the whole scope, the asker while a member, else `acting_account`); only
  statements that name the row's space run in `statements_of(space)`, which
  changes no one's scope; the install's own (about every space, or none) run
  in `install_statements(reason)`, declared in `INSTALL` of
  `tests/test_row_work_gates.py`, both ways, and only where no request comes
  with an id of its own; a loop for every space starts in `install_loops`. A
  loop over rows of several spaces enters one per row. Never held across a
  `yield`. With Spaces off they bind nothing. Nothing reads what was said yet:
  the database door (`current_door`) is S4-01 step 1, and
  `tests/_space_door.py` stands in for it.
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
