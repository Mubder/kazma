<!-- AGENTS.md, piece 70c-spaces-swarm-accounts. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **The swarm follows the space (S1-15, 2026-11-18).** Guards act only where
  Spaces is enforced; the older multi-user mode keeps its refusal
  (`swarm.single_operator.swarm_refusal`), and work bound to no space is
  refused once enforced (`NO_SPACE_REFUSAL`).
  - A task carries its space and the person who asked (`metadata`
    `tenant_id` and `asker`, stamped at admission: once enforced the
    admitting scope overwrites what the task arrived with,
    `SwarmEngine._stamp_whose`). **Whose task it is is Kazma's to record**:
    every key the swarm reads from a task's metadata is declared a request's
    or Kazma's own (`swarm.task_metadata`: `REQUEST_KEYS`, `KAZMA_KEYS` --
    whose task it is, where it came from and where its result goes, what its
    worker may do, how a run ended), and the dispatch route refuses a request
    naming a Kazma key (400 `metadata_set_by_kazma`, nothing recorded or
    run). A new key the swarm reads is declared in one of the two or the
    gate fails (`tests/test_swarm_task_metadata.py`). Every list, task,
    stream, action and the export keep to the caller's space
    (`single_operator.task_of_this_space`; the task store's list filter
    names the bound space, `"*"` is Kazma's own work); another space's task
    answers as an unknown one.
  - **A worker runs as its task's person**: `worker_dispatch._worker_scope`
    binds `identity.work_scope(space, asker)` (the asker while still a
    member; a task whose asker left is refused, `identity.AskerLeft`, never
    run as the space's acting account, the S2-12 review; a task of no space
    runs nothing). A worker usually inherits the dispatching request's scope;
    the binding is what holds for a task run with nothing bound (a pipeline
    resumed after a restart, the durable path), a hand-off target and its
    approval's resume. A sub-agent runs in its spawner's scope. The scope is
    resolved once, before the worker's breaker is asked
    (`worker_dispatch._scope_or_refusal`): a task of no space, or an identity
    store that cannot answer (reported to the database watch), is refused
    there and never counts against the breaker every space shares.
  - **A worker serves every space**: its log lines and last task carry
    their space (`mark_dispatched(space=)`, `logs_for`) and are shown in it
    only (`services._kept_to_space`). A worker's lessons are the task's
    space's (`self_improvement.worker_lessons`, kept under
    `worker:<name>` in that space's Soul, never in the registry prompt every
    space shares); the registry prompt's own lessons were learned before
    Spaces and are shown in the owner's space only
    (`worker._prompt_in_this_space`).
  - A bus approval records the space and the person that asked
    (`shared_approvals` `space` and `asker`, the gate row's tenant); the Web
    page shows it only in that space. **Only the person who asked answers**
    (decision N4, `single_operator.answered_by_caller`): a task's approve,
    reject, cancel and retry and its worker's approval are refused to anyone
    else (403 `not_yours`); another member of a shared space sees them. The
    check is the shared action's, `task_actions.task_refusal` (S1-16
    follow-up): the page's routes and the run supervisor's buttons ask the
    same one, so a retry never takes a member's task into the caller's space
    through the admission stamp, and work bound to no space acts on no
    task's (`no_space`). **Who may act on a task is decided by its kind, in
    one place** (`task_actions.caller_may_act_on`, S1-17 follow-up): a
    research task (`metadata.kind` `research`) follows N32 on every route
    -- its author or the space's owner -- and any other task N4, on the
    Research page's routes too. The Swarm page held every task to N4 and the
    Research page every task to N32.
    **Someone else's card and result stay off the owner's chat apps** (O1,
    `single_operator.for_the_owner`): until people link their own chat apps
    every one is the owner's, so a request that does not act for the owner
    is never posted to the bus (`safety._kept_in_its_space`: it waits on its
    row, answered in its space) and a chat app's button never decides it;
    its result goes to neither the output target nor a chat
    (`chat_delivery`). Delivering a request to its asker (the bell, Web
    Push) is S2-05's. An identity store that cannot say whose a result is
    holds it (`single_operator.wait_for_owner_answer`, through an outage, up
    to `OWNER_ANSWER_WAIT_S`): the owner's is never dropped; an approval
    whose person cannot be read answers 503 and stays pending. That wait is
    never the request's: once Spaces is enforced a foreground dispatch
    answers once its task is done and hands the result over in the
    background (`routes_tasks._hand_over_result`, S1-17 follow-up: the
    page's request hung through an outage); not enforced, the hand-over
    stays in the request, as before. A failure of the dispatch or of the
    result's routing sends its safe message either way
    (`routes_tasks._reported`, one handler for both).
  - **The swarm's writes stay the admin's in every mode** (dispatch, a
    task's actions, an approval's decision) until feature grants give the
    swarm to others (S2-11); the install's swarm, read -- workers' totals and
    breakers, the output target -- is the admin's once enforced
    (`route_roles.INSTALL_READ`). The agent's `dispatch_swarm` follows the
    routes' rule once enforced (`single_operator.swarm_write_refusal`: open
    with sign-in off, the owner only with it on).
  - Gates: `tests/_swarm_space_cases.py` (the matrix cases),
    `tests/test_swarm_spaces.py` (each guard taken out as a control; the
    unscoped dispatch as the control for a task run with nothing bound; the
    review's forged dispatch with both layers out; a shared space of two;
    a real worker's real tool call), `tests/test_swarm_task_metadata.py`.
- **Research follows the space (S1-16, 2026-11-18).** Guards act only where
  Spaces is enforced.
  - A research session carries its space (`ResearchSession.tenant_id`,
    always stamped from the bound scope). A space's list holds its own
    sessions, the owner's space also those from before Spaces (no tenant).
    Every route and tool that names a session asks
    `research_session.session_here` (another space's answers as an unknown
    one); never `get_session` where a request names the id. Once enforced,
    work bound to no space makes no session (`NoSpaceBound`: it became the
    owner's), and a session records its author (`asker`, always stamped) and
    the chats it was made in (`thread_ids`, a column never shown; one
    question is one row, whatever chats asked it). A chat taken out of a
    shared space takes its web searches (`forget_chat_searches`, through
    `chat_moves.remove_what_it_left`): one another chat made too stays, a
    row from before stays (`""`: a chat not known), a report stays (a file
    of the space). Gate: `tests/test_chat_move_leftovers.py`.
  - **Shared research is read by every member and changed by two**
    (decision N32, the S1-16 follow-up): its author and the space's owner
    archive, restore, delete, cancel or resume it; another member reads it
    (403 `not_yours`). One rule, `spaces.shared_work.may_change`, for a
    session (`research_session.session_to_change`, every route and tool
    that changes one) and a research task (`_task_to_change`, archive and
    delete alike). A task's rule is its kind's on every page
    (`task_actions.caller_may_act_on`): a swarm task reached through the
    Research routes follows N4.
  - **A chat's web search is one row per question, per space, per person**
    (`_chat_search_key`): the owner's keeps the ids it had (12 hex digits of
    the question); every other key starts with `s` and hashes the space, the
    person and the question together, so no question of the owner's shares
    an id with another space's. A row of another space under an id is a
    miss, never written (`_row_is_this_spaces`).
  - A research task is a swarm task of kind research: it keeps to its space
    as every swarm task does (S1-15; the research routes' `_find_task`).
  - The research files (reports, papers, extracts, digests, a saved page, a
    crawl's pages, the pipeline's files, the acquisition journal) are the
    workspace folder's and wait for S1-19, the space folder.
  - Gates: `tests/_research_space_cases.py` (the matrix cases),
    `tests/test_research_spaces.py` (the unfiltered list as the control,
    and each other guard taken out; a shared space of two, any member
    changing shared research as the control; the review's crafted
    collision; a session for no one as the owner's as the control).

- **A mailbox and a calendar belong to a space (S1-17, part a,
  2026-11-18).** Guards act only where Spaces is enforced; one account
  behaves as before. Parts b (X) and c (GitHub) follow.
  - **One rule for a connected account's names**
    (`kazma_core/spaces/connected_accounts.py`): the owner's space keeps
    today's vault names and the process environment, so the owner's existing
    connections stay where they are (N26); every other space keeps its own
    under `space:<id>:<name>` (install-scoped in the vault, so a refresh with
    nothing bound reads the space it was told), and its environment values in
    the vault too (`space:<id>:env.<VARIABLE>`). `connected_space()` is the
    bound space once enforced, `NoSpaceBound` with none: a connection is never
    looked up for no one, nor the owner's lent to it.
  - **Every mail and calendar secret and environment value goes through
    `email_manager.credentials`** (`vault_retrieve`, `vault_store`,
    `vault_delete`, `cred`, `account_env`, `set_account_env`). The install's
    app registration -- the Google and Microsoft client ids and secrets, the
    Microsoft tenant, Kazma's own address -- is the install's in every space
    (`_INSTALL_NAMES`, `_INSTALL_VARIABLES`). The extra-accounts list is
    `email.accounts` for the owner's space and `email.accounts_by_space`
    (CREDENTIAL in `settings_restore`: S1-28 carries it) for the others. The
    mail presets and the two app-registration routes are `install` routes.
  - **A member connects their space's own accounts** (the review of
    S1-17c, N26): once Spaces is enforced, the mail and calendar routes,
    GitHub's token, sign-in and disconnect, and X's credentials (the OAuth
    1.0a keys, the app's Bearer token and OAuth 2.0 client), Sign in with X
    and Disconnect (the owner, 2026-11-18: "Yes, per space") need membership,
    not admin (`platform_rbac._connects_a_spaces_own_account`, checked before
    the X rule and the admin prefixes; a member is an operator); the
    install's pieces stay the admin's -- the mail presets and app
    registrations (held equal to `route_scopes`' `install` routes), the
    GitHub App and a clone -- and so do X's reply bot policy and resuming X
    after a stop (continuing authority). What publishes on X is unchanged:
    the always-ask card, N4, O1, a draft seen before it is published. Not
    enforced, every one is the admin's, as before. The matrix's mail, GitHub
    and X cases put the auth middleware's role check (`auth._role_refusal`,
    the one rule the middleware applies; `Matrix.role_checked`) in front of
    the routers: they mounted the tenant middleware only, and no case met an
    admin-only route a member could not reach. The page a member connects
    mail, calendar and X from is S1-23's (Settings is the admin's today).
  - **A sign-in finishes in the space it started in**: `oauth_attempts.begin`
    records the bound space and person, and a callback binds them back
    (`kazma_ui.oauth_sign_in.attempt_scope`); an attempt of no space, or of a
    person no longer a member, is refused (`no_space`, worded in both
    languages); one whose membership cannot be read (the database away) is
    the refusal `identity_unavailable`, never a 500 (S1-17 follow-up). X's
    and GitHub's callbacks bind theirs (parts b and c).
  - **The practice mailbox and calendar are each space's** (S1-17
    follow-up): `sandbox_emails.db` keys a message by space and id
    (`backends/sandbox.py`; a file from before is rebuilt once, every
    message the owner's), and the in-memory practice calendar is one per
    space (`calendar.router._sandbox_instances`). One mailbox for every
    space put member A's sent mail in member B's Sent folder.
  - **The background connector check goes space by space**
    (`connector_health.check_connectors`): each space's grants are refreshed
    in that space; only the owner's space raises the owner's alerts.
  - Gates: `tests/test_connected_accounts_spaces.py` (two spaces, two
    mailboxes; a refresh writes only its own; the source gate -- no mail or
    calendar module reaches the process environment or the vault itself, and
    no other module names their secrets but the declared offsite backup and
    settings restore; each with its control), `tests/_mail_space_cases.py`
    (19 routes and 12 tools; with the space names taken out, the cases fail).

- **An X account belongs to a space (S1-17, part b, 2026-11-18).** Every
  X key and row was already kept per tenant (`x_api.ownership.x_config_key`,
  `tenant.<t>.connectors.x.*`; the stores "apart"), and a space is a tenant.
  Guards act only where Spaces is enforced; one account as before.
  - **The tenant X work is under is the bound space**
    (`ownership.x_tenant_id`, a tenant bound by older code naming its space
    as `require_space_id` reads it): work bound to no space reads no X
    account (`NoSpaceBound`) -- it read the owner's.
  - **The owner's environment and legacy names are the owner's space's**:
    the OAuth 1.0a keys from the environment, the legacy vault names
    (`x_api_key` ...: the same names in every space, and the vault's read
    still falls back from a space to a global row, for any name -- so a
    global copy of one answered every space; read now for the owner's space
    only. A space's own names, `tenant.<space>.connectors.x.*`, are its
    alone: a global row under one could only come from its own write) and
    `X_BEARER_TOKEN` (`auth_tokens.bearer_from_env`: another space spent the
    owner's X API budget). Source gate: no other X code reads the
    environment but Kazma's own switches.
  - **Background X work runs in its row's whole scope**
    (`ownership.x_work_scope`, resolved off the loop: the space and the
    account its own work acts for, as every queued work): a booked post, its
    receipt, a projection repair, the legacy schedule's migration and fire,
    the mention poller (the owner's space: it answers the owner's account).
    A row of no space does not run -- with the tenant alone bound it was
    sent: a booked post is held for review, a legacy post marked failed.
    Whose row it is that cannot be read for now (the database away, a
    locked file: `ownership.identity_failure_passes`) waits for a later
    pass, nothing held or failed -- the legacy fire's catch-all would have
    failed it for good. The run supervisor's X Cancel with nothing bound is
    refused (`no_space`).
  - **Sign in with X finishes in the space it started in**
    (`kazma_ui.oauth_sign_in.attempt_scope`; the callback is an `account`
    route: behind sign-in, it names no space -- classed `space`, it was
    refused for anyone in two spaces), and `x_draft_posts` saves under the
    X account's space.
  - **Another space's X notice stays off the owner's channels** (O1,
    `ownership.kept_in_its_space`, the one rule): a health or publication
    notice is held undelivered (`KEPT_IN_ITS_SPACE`), and the old
    schedule's outcome notice -- carrying the post's text -- goes neither to
    the address its booking captured nor to the chat apps
    (`scheduled_fire._deliver`). Delivering it to its space's people is
    S2-05's.
  - Gates: `tests/_x_space_cases.py` (every X route, the X page, the
    Scheduled page's X booking and the 13 X tools: two spaces each with
    their own X account and its rows; X and the X model stood in for, every
    call to X carrying the asker's keys), `tests/test_x_account_spaces.py`
    (each rule with its control; the source gate with a planted read).
- **A GitHub sign-in belongs to a space (S1-17, part c, 2026-11-18;
  decision N26).** Guards act only where Spaces is enforced; one account as
  before.
  - **The owner's space keeps today's sign-in**: its stored tokens, the
    install's GitHub App (its installation token acts on the owner's
    repositories) and `GITHUB_TOKEN` / `GITHUB_PAT` in the process
    environment. Every other space keeps only its own OAuth token and PAT,
    under `tenant.<space>.connectors.github.*`
    (`github_client.github_key` -> `connected_accounts.space_setting_key`),
    and reads nothing else: `get_github_token` gives the App and the
    environment (`connected_accounts.owners_environment`) to the owner's
    space only. With nothing connected a space reads no token at all -- it
    read the owner's environment, App, or a global vault row an older
    install keeps.
  - **A friend's push never acts as the owner**: `_git_token` takes the App
    first in the owner's space only, and so does the retry of a push GitHub
    refused (the review of S1-17c: it minted the App's token whatever the
    space). One function hands out the App's token in the git tools,
    `_owners_app_token`, and every caller of `get_app_installation_token` /
    `mint_app_installation_token` asks the space first, or is declared (a
    gate, the old retry as its control). The GitHub tools refuse before any
    request in a space with no account (`_no_account_here`; the owner's
    space as before). A token saved in another space never reaches the
    process environment (`bind_github_token_to_process`, owner's only), the
    GitHub App's settings are the owner's space's (`POST
    /api/github/app/config` answers 409 `owners_space_only` elsewhere), and
    the turn's note, the Skills marketplace search and the agent's skill
    search read the environment's token for the owner's space only.
  - **Sign in with GitHub finishes in the space it started in**: the
    callback is open before sign-in, so with no binding it ran in the owner's
    space and a member's account became the owner's sign-in; it binds the
    attempt's space (`oauth_sign_in.attempt_scope`).
  - **Disconnect deletes** (`clear_oauth_token`, `oauth_revoke`): it wrote
    the keys empty, the settings store refuses to blank a stored secret, and
    Disconnect cleared nothing (the bug X's Disconnect had; found on the
    way). It deletes every copy the vault keeps (`forget_github_keys`,
    `SecretVault.delete(every_scope=True)`: the GitHub names are not
    install-scoped, and the settings store's delete reached the request's
    scope only -- live keeps two copies of the OAuth token), and a delete
    that fails answers 503 `disconnect_failed`, never "disconnected" (the
    review of S1-17c). The bot identity's switch has a settings rule
    (`git.bot_identity.`).
  - **The GitHub client reads its token off the loop**: `GitHubClient()`
    with no token is a loop-stall helper; async code makes it with
    `await GitHubClient.of_this_space()` and the tools in a thread.
  - Left: the repository the routes and tools act on is still the install's
    active workspace (`resolve_repo`, S1-19 and S1-20); the commit author
    (the bot's name and email) is the install's.
  - Gates: `tests/_github_space_cases.py` (every GitHub route and the five
    tools: A with nothing connected sends no token, the tools refuse; then A
    connects its own and every request carries it alone; B and the owner
    keep theirs -- 18 of 23 fail with the space rule taken out),
    `tests/test_github_account_spaces.py` (each rule with its control; a
    source gate on the environment -- any shape naming the variable but the
    one reader's and the owner's binding -- and on the sign-in's keys --
    each key constant handed to `github_key` -- each with planted shapes).
