<!-- AGENTS.md, piece 70d-spaces-vault-files-admin. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **The vault has a space scope (S1-18, 2026-11-19).** Guards act only
  where Spaces is enforced; not enforced, the vault is exactly as before.
  - `security.vault.secret_scope(name)` is the one answer to whose a name
    is: the install's (`INSTALL_SCOPED_SECRETS`, which stays; the offsite
    backup's passwords; every `cfg:` settings secret that is not a space's
    connected account, until S1-23 classes settings) or a space's (a name
    that carries its space, `space:<id>:` / `cfg:tenant.<id>.`; the X and
    GitHub settings of the owner's space, `_SPACE_ACCOUNT_SETTINGS`; anything
    the agent stored).
    The owner's mailbox and calendar (`email.*`, `calendar.*`) are the
    owner's space's, though the install list still stores them as one row;
    only the app registration is the install's
    (`connected_accounts.INSTALL_ACCOUNT_SECRETS`, the one list). Work with
    no space that uses the owner's grant binds the owner's space
    (`cloud_sync._as_the_owners_space`).
  - A space's secret is returned only to work in that space
    (`SecretVault._scopes_to_read`): another space, and work with no space
    bound, get None. No `default` rung (`retrieve_scoped` asks the same
    rule). A row with no tenant is the owner's space's (N19). The caller's
    space is the bound scope's first (`_callers_space`: the mail code pins
    the tenant to none while its scope stays bound).
  - A secret is never written for no one (`NoSpaceBound`) nor for a space
    its name does not carry (`VaultScopeError`), and the settings store
    refuses such a save: it never keeps the secret as plaintext instead
    (`config_store._vault_scope_refusals`).
  - A list, a delete (`every_scope` too) and the divergent-duplicate scan
    keep to the caller's space; a drill that must judge every row uses
    `sample_opens`. No product code passes a tenant to a vault call
    (`SecretVault.for_space(id)` is the declared way).
  - A new vault name read by the install's own work with no space bound goes
    on the install list, or it reads None once enforced.
  - Gates: `tests/test_vault_space_scope.py` (the old read as each control),
    `tests/_vault_space_cases.py`.
- **A space's files live in its own folder (S1-19, 2026-11-19).** Guards
  act only where Spaces is enforced; not enforced, every folder and the
  machine's workspace are as before.
  - `kazma_core/spaces/files.py` is the one home: `space_dir(id)` is
    `<data dir>/spaces/<id>/`, with `files/` (the working folder),
    `attachments/`, `images/`, `documents/`, `exports/`
    (`space_folder(kind, install=)`). The owner's space keeps the install's
    folders and the machine's workspace (a mapping, not a move). A new
    writer of a file asks `space_folder`, never `data_dir() / "<folder>"`.
  - **A sandboxed space cannot leave its folder.** `resolve_active_root`'s
    first rung is `sandboxed_files_root()`, and `check_path_access` allows
    only paths inside the space folder (`path_policy._space_home`): no
    durable root, session grant, absolute-path switch or skill folder.
    Enforced with no space bound, the policy opens no file; a space whose
    kind cannot be read raises `SpaceKindUnknown` (never "host").
  - **Nobody opens another space's folder, the owner included** (O1):
    `store_registry.in_another_spaces_folder` is part of `is_kazma_store`,
    so every door refuses a path inside `spaces/<other>/` (the machine's
    workspace can hold the data folder: on live it does). A database or
    `.env` in a person's `files/` is theirs (`_a_persons_files`).
  - An upload or shared-file id resolves only in the space that holds the
    file. `python_exec` never uses the machine's own Python in a sandboxed
    space (`local_exec_forbidden`). The install's one browser page belongs
    to the space that opened it: another space's call closes it first
    (`browser_automation.tools._page_space`).
  - Research files follow the working folder; the acquisition status shows a
    space its own journal jobs only (`research_panel.routes._jobs_of_this_space`).
  - Gates: `tests/_files_space_cases.py` (every tool that takes a path or
    writes a file, with B's file, the owner's file and climbing paths),
    `tests/test_space_files.py` (each rule taken out as a control).
- **Host tools exist only in a host space (S1-20, 2026-11-20).** Guards act
  only where Spaces is enforced; not enforced, every tool is offered and runs
  as before.
  - `kazma_core/spaces/tools.py` holds every tool's group: `DEVELOPMENT`,
    `ADMIN`, `SPACE`, `OUTSIDE`. A new tool goes in one of them or
    `tests/test_host_tools_absent.py` fails; a tool in no group (an MCP
    server's) is `development`.
  - `host_allowance()` is the one rule: a sandboxed space has no host tool;
    the owner's space has the admin tools, and Development's while
    `identity.holds_development` (N28, on at first); a shared space with a
    project folder has Development for each person who holds it and never
    the admin tools (S2-12, piece 3); no space bound, none.
  - Applied at the offer (`skills.switches.offered`, a swarm worker's list,
    both through `offered_here`) and at the call
    (`LocalToolRegistry._execute_inner`, `UnifiedToolExecutor`'s MCP
    branch). A new place that offers or runs tools asks it too: hiding alone
    is never enough.
  - The Development routes (`route_scopes.DEVELOPMENT_ROUTE_RULES`) are
    refused by the tenant middleware where Development is not (403
    `host_only`); the sidebar's Workspace and IDE entries sit inside
    `{% if development_here() %}` (no read while drawing). A new Development
    page or route goes in the rules. A page that is not Workspace or the IDE
    asks a Development route only after reading the flag its page was drawn
    with (the chat page's `#chat-root` `data-development`): the chat page
    asked `/api/workspaces` on every load, two or three 403s in a sandboxed
    space, and main's browser job failed on Linux from 2026-11-20
    (`tests/test_development_routes_asked_where_offered.py`, the old chat
    page as the control; `tests/e2e/test_two_tabs_two_spaces.py`).
  - A clone builds its environment with `security.git_auth.clone_env`:
    outside the owner's space, none of the machine's credentials.
  - **Work on the machine acts as the admin** (`kazma_core/spaces/machine.py`,
    2026-11-20): once enforced `kazma ask`/`acp` and `kazma mcp` bind the
    owner's account in the owner's personal space, or in a space they name
    (`--space`) that the owner is a member of (`machine_space`, read off the
    loop in async code); any other space is refused before anything runs.
    The terminal app's and the CLI's calls name the owner's personal space
    (`local_api.auth_headers`). A new entry point that runs work on the
    machine binds the same way.
  - Gates: `tests/test_host_tools_absent.py` (controls: a policy that only
    hides the tool, the route rule taken out, the clone environment as it
    was), `tests/_host_tools_space_cases.py`,
    `tests/test_machine_work_acts_as_the_admin.py` (nothing bound and a call
    naming no space as controls).
- **Running work and approvals carry their space (S1-21, 2026-11-20).**
  Guards act only where Spaces is enforced; not enforced, every surface lists
  and acts as before (the older multi-user mode keeps its refusal).
  - `kazma_core/runs/spaces.py` holds the rules: `visible_here` (a run or
    incident of the space bound now; none with no space bound),
    `kept_in_its_space` (O1: news of a space other than the owner's stays
    off the install's channels), `may_act` (its asker or an owner of its
    space, `spaces.shared_work.may_change`), `runs_refusal`.
  - **Every run carries `space` and `asker`** (`contract.Run`), stamped by
    its adapter from the kind's own record: a thread record, a swarm task's
    metadata, a job's tenant and creator, a document job's tenant and actor,
    a research session's columns, a crawl's working folder
    (`spaces.files.space_of_folder`) or library, an X operation's tenant, a
    memory task's payload. Empty is the install's own work and work from
    before Spaces: the owner's space's (N19). A new adapter names `space=`
    on every `Run(` (gate; `maintenance.py` is declared install work).
    Adapters read every space's runs (the swarm with the task store's `"*"`
    once enforced; a chat turn's title inside its thread's own space).
  - **The registry keeps a surface to the bound space**
    (`list_runs(everyone=False)`); the supervisor alone reads `everyone`.
    `perform` refuses another space's run ("gone") and anyone but its asker
    or an owner of its space (`not_yours`, 403); a kind's own control may be
    stricter (a swarm task answers only its asker, N4).
  - **Incidents carry `space` and `asker`** (columns added to an older
    file); `recent(space=)` and `live(space=)` keep a surface to its space,
    a row with none being the owner's. The supervisor opens another space's
    incident with `notify = suppressed` (`KEPT_IN_ITS_SPACE`), `deliver` and
    the recovery and diagnosis notes refuse it as a second line, the bell
    shows the bound space's, and the runbooks step only on the owner's
    work, as the owner. Delivering a member's incident to them is S2-05's.
  - `/api/runs` answers in the bound space; a member decides on their own
    space's incidents (the admin rule stays while not enforced). Chat apps'
    `/runs` and a card's button refuse until their chats are bound to a
    space (stage 3).
  - **An approval gate names whose work asked**: `GateRow.asker` (column
    added to an older file), stamped by `register_gate` from the bound
    scope, and its tenant from the bound space when the writer named none.
    The pending list was already per space (the chat store, a bridge gate's
    tenant); an MCP server's sampling request is shown only where
    Development is. Only the asker answering is S2-05's.
  - Gates: `tests/test_gates_and_runs_spaces.py` (the unfiltered listing,
    today's supervisor paging a member's stuck X post with its words, and
    the decision path without its space checks as controls),
    `tests/_runs_space_cases.py`.
- **Model calls name their space and asker (S1-22, 2026-11-20).**
  - The per-call ledger stamps each row with the scope bound where the
    call was made (`llm_ledger._whose`, read in the caller's own context by
    `arecord_llm_call`): a turn's, a scheduled fire's, a swarm worker's.
    Kazma's own work and a call with nothing bound name nobody. Columns
    `space` and `asker` are added to an older file; rows from before name
    nobody. A new model call site needs no argument: it is made inside the
    work's scope, or it is Kazma's own.
  - `llm_ledger.spend_by_person(since)` is the admin's view (N11: spend and
    counts, never a text): `GET /api/dashboard/spend-by-person` (admin), the
    Dashboard's "Spent today, per person" card, shown once two people have
    spent or Spaces is enforced (an install with one account sees the page as
    before).
  - **The trace feed keeps to its space**: a `TraceEntry` carries `space`
    and `asker` (stamped by `TraceStore.add` from the bound scope; a trace's
    details are the start of a turn's prompt); `TraceStore.recent` and
    `stats` take the space shown (`tracing.shown_space`, `tracing.view`:
    every one, asked the old way, while Spaces is not enforced), per-space
    totals are kept, and a socket registered for a space is sent that
    space's entries and totals only. The Agents page and API, the Dashboard's
    trace list and totals, and `/ws/dashboard` (membership checked at the
    handshake, `space_binding.space_for_socket`) show the bound space's; the
    Agents page counts the running turns of its space.
  - Gates: `tests/test_ledger_names_the_asker.py` (every call of a real
    turn, a scheduled fire and a swarm task; the ledger without the stamp
    and a call site with nothing bound as controls; a socket registered as
    before as the feed's control), `tests/_traces_space_cases.py`.
- **Settings in three areas (S1-23, 2026-11-20).** Guards act only where
  Spaces is enforced; not enforced, Settings and its saves are as before.
  - `kazma_core/settings_scope.py` holds every settings key's scope:
    `install` (the admin's), `space` (a space's own: its connected
    accounts, its chats' state, its Soul) or `account` (a person's sign-in
    and sessions). `_scope_of_key` is the one answer (the longest pattern
    decides; a key under `tenant.<id>.` is that space's). A new key goes
    under a rule there, or `tests/test_settings_scope.py` fails.
  - The install's own writers write install keys only
    (`install_writer_refusal`): Settings' generic and batch saves answer
    400 and `config_save` raises `RefusedBeforeEffect` for a space's or a
    person's key. Such a key is changed through its own route, in its space.
  - Settings is three areas, My account, This space and Install
    (`#settings-root`'s `data-areas`, `settings._settings_areas`). A member
    reaches `/settings`, their password and their sessions
    (`platform_rbac._reaches_own_settings`) and is shown My account and This
    space; Install, the API tokens and the platform users are the admin's
    and are not on a member's page at all. A new Settings tab is placed in
    an area (`settings_core.js` `_tabArea`).
  - Gates: `tests/test_settings_scope.py` (a planted key, the
    not-enforced save and a page built for everyone as controls),
    `tests/e2e/test_settings_areas_browser.py` (EN and AR, by the sidebar).
- **What reaches the admin carries no member content (S1-24, 2026-11-20).**
  Guards act only where Spaces is enforced, except the finished-turn line's
  `asker=`, which is always written.
  - Every log line at INFO or above that quotes someone's words goes through
    `kazma_core/observability/log_words.py` (`words_for_log`,
    `args_for_log`): once enforced it says how long they are, never the
    words. A new such line uses it.
  - The finished-turn line names the person by account id (`asker=`, read
    by `turn_line.read_turn_asker`). Once enforced the daily digest shows
    the turns by person, counts what ran without asking in a chat another
    person opened under that person (`daily_digest._others_chats`: never
    the chat's id or its tools), and counts other people's open problems by
    person; its own list is the owner's space's.
  - Langfuse gets only the owner's space's work once enforced
    (`KazmaTracer._to_langfuse`); the rest gets the console line.
  - Gate: `tests/test_admin_signals_hold_no_content.py` (a member's real
    turn, every INFO line, alert, the digest and the weekly report read for
    a planted word; the log's words as before, today's digest and every
    call sent to Langfuse as controls).
- **The admin boundary (S1-25, 2026-11-20).** Guards act only where Spaces
  is enforced; not enforced, the older user routes answer and there is no
  People tab.
  - The admin opens no space they are not a member of: the membership check
    (`space_binding.space_for_request`), never a role check, on every route
    and socket. A new way into a space's data asks membership too.
  - Settings' own writers write settings only
    (`settings_scope.install_writer_refusal` asks `settings_restore.classify`):
    the identity store and every other record of Kazma's are written by
    their own code, or the admin could add themselves to any space.
  - People (`kazma_core/spaces/people.py`, `kazma_ui/people_api.py`, Settings
    -> Install -> People): add (an account and its sandboxed personal space),
    reset (sessions end), delete (sessions end, every membership goes, the
    personal space stays closed, `account_deleted_at`, for S1-27). Every
    change is an event of the identity store's record, in the same step; the
    record is append-only (`identity.update_registry`) and hash-chained
    (`people.broken_at`). A reset is told to the person at their next page
    load, once (`/api/account/notices`, `account_notices.js`). The older
    mode's user routes refuse once enforced (`use_people_page`).
  - Gates: `tests/test_admin_cannot_open_a_space.py` (a role check alone
    and an untouched record as controls), `tests/e2e/test_people_page_browser.py`
    (EN and AR, by the sidebar; no tab without Spaces as the control).
- **A key per space; a space's secrets sealed with it (S1-26, 2026-11-21).**
  Sealing happens only where Spaces is enforced; an install with one account
  writes the rows it always wrote (no key, no sealed row, the same
  encryption).
  - **The keys live in the vault's file**, table `space_keys` (not the
    identity store, whose registry no longer holds the empty field): the
    salt, the wrapped keys and the rows they seal are one file, so a backup,
    a migration bundle and a restore carry them from one moment, and a
    Postgres restore of another date cannot part a space's key from its
    rows. A key is 32 random bytes named by a random id, wrapped with
    AES-256-GCM under a key derived (HKDF) from the vault's for wrapping
    alone, bound to its space and id; the `wrap` column says how, for the
    version where members hold it.
  - **Made when the space is made**: `identity.update_registry` keys every
    space a change added (`identity._key_new_spaces` ->
    `space_keys.ensure_keys`), best effort; a space without one gets it in
    the same transaction as its first sealed secret. **The owner's space has
    none** (decision N22: its secrets are not sealed again until stage 4).
  - **Which rows**: `security.vault.sealing_space` -- another space's
    secrets (a name that carries the space, or the row's tenant); the
    install's and the owner's keep the vault key. A sealed row names its
    key (the `key_id` column; NULL is the vault key) and **a read follows
    the id the row names**, enforced or not; a row whose key is gone, does
    not unwrap or is another space's raises `SpaceKeyMissing`, never opens
    with another key. In `vault.py` only `_open` opens a row and only
    `_seal` / `_reseal` seal one; a new statement that inserts a secret
    names `key_id`, one that selects a sealed value selects it too.
  - **Moves are explicit, checked and reversible**: `SecretVault.seal_space`
    (rows written before the space had a key), `unseal_space` (its inverse),
    `rotate_space_key` -- one transaction each, every value read back before
    it is written. Never on a read. `space_keys.report` is what the
    switch-on check (S1-33) reads.
  - **Erase** takes the space's keys with its rows
    (`SecretVault.erase_space`); the vault's connection zeroes deleted data
    (`secure_delete`) and the erase empties the write-ahead log. The restore
    drill's `vault:space_keys` check fails a backup whose sealed rows name a
    key it does not hold. The export is unchanged (N10's password seal;
    secret names only, never a key).
  - **Key care (S4-07, 2026-11-23).** A space's key can be changed
    (`space_keys.change_key`; `kazma spaces keys change SPACE` / `--all`,
    and `kazma spaces keys` lists key ids, never a key): its sealed secrets
    are sealed again with the new key, read back first, in one transaction
    (`SecretVault.rotate_space_key`), and its old keys are destroyed --
    deleted there, zeroed, and the write-ahead log emptied
    (`SecretVault._empty_the_log`, the erase's too; a reader that holds the
    log is said at WARNING). **A change never loses a secret** (the review
    of S4-07): it is refused, naming the rows (`SpaceKeyStillNamed`,
    `rows_elsewhere`), while a row that is not the space's own -- a name a
    later build classes elsewhere -- is sealed with one of its keys, and a
    key some row still names is never deleted; a row of the space that does
    not open refuses it too (`rows_damaged`), and `--all` reports that space
    and goes on. Recorded (`space_key_changed`). Refused for the owner's
    space, a space not held or erasing, the vault off. A deleted
    space's key is destroyed: the erase takes it, and the maintenance sweep
    "erased space keys" (`space_keys.destroy_erased_keys`) destroys again
    what an older vault file restored over the identity store brings back,
    for a space the record says was erased (`space_erased`) only. A running
    Kazma keeps an old key it unwrapped in memory until it reloads. Gate:
    `tests/test_space_key_rotation.py` (a change under the old key, a change
    that leaves the log, the change of before losing a real secret under a
    drifted name, and no sweep as controls).
  - What it does not do: the vault key wraps every space's key, so this
    keeps one space's secret from another's key, not from the person who
    runs the machine. Rotating the install's vault key is stage 4.
  - Gates: `tests/test_space_keys.py` (the single vault key opening both, a
    space made with no key, a read by the newest key, an unbound wrap, a
    build that seals without enforcement, a row-by-row move, the erase and
    the drill of before as controls; two source gates with planted shapes),
    `tests/test_backup_restore_spaces.py` (a real backup and a real
    migration: each space reads its secret back, sealed; a restored vault
    without its keys as the control).
- **Before a second person is let in (S1-33, 2026-11-21).** Spaces is still
  not on anywhere; this is the check and the refusal.
  - `kazma_core/spaces/preconditions.py` is the one table (`PRECONDITIONS`):
    work with no space is refused (`PROBES`, each called with nothing bound
    under `scope.as_if_enforced`, plus `TENANT_FALLBACKS_LEFT`, held equal
    to the S1-02 ratchet's baseline), one value per secret
    (`SecretVault.copies_that_disagree`), a daily limit per person and
    features per person (missing until the modules `_BUILT_BY` names exist
    and their own `precondition()` holds -- a module alone is not the rule in
    force, `preconditions._module_answer`: S2-02, S2-11), no plain copy of the
    backup leaving the machine (`universal.plain_copy_configured`, N10), the
    owner's API token for the deploy check (N15), a key per space
    (`space_keys.report`), and for the switch alone no account left by the
    older multi-user mode. A check that cannot be made is missing. A new
    precondition is a row there and a break in the gate's `BREAKS`.
  - **Who asks**: `people.create_person` for every new account (409
    `preconditions_missing`, each one named) and `preconditions.switch_on`,
    the only product code that writes `identity.ENFORCED_KEY`. A new writer
    of an account or of the switch fails the source gates.
  - **A function that answers "which space"** refuses with nothing bound
    once enforced and is added to `PROBES`. `safety.hitl.get_current_tenant_id`
    asks `require_space_id` (it answered `default`).
  - **The command** is `kazma spaces migrate`: `--check` and `--dry-run`
    run inside `read_only_diagnostic`; the move (`preconditions.apply`)
    copies the vault first, records each step in `system.spaces.migrate`,
    settles copies (`SecretVault.settle_copies`: the newest copy's value, no
    row deleted, each row kept under its key), keys and seals spaces, and
    makes the owner's token (`<install>/.kazma/owner_api.token`, one of
    Kazma's secret files; `scripts/deploy_live.py` signs in with it and
    falls back to the shared secret). `--switch-on` is refused while
    anything is missing. **Never run the move or the switch on the live
    install without the owner's word.**
  - **Tests take the preconditions as met** (the root conftest's
    `_second_account_preconditions_hold`); a test of them is marked
    `real_preconditions`.
  - Gate: `tests/test_second_account_preconditions.py` (each precondition
    missing alone; the switch stored by hand, an account made with no check
    and the old fallback as controls; planted writers for the source gates).
