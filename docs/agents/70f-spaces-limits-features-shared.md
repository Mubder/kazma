<!-- AGENTS.md, piece 70f-spaces-limits-features-shared. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **A daily spending limit per person (S2-02, 2026-11-22; decision N8).**
  Only where Spaces is enforced: an install that does not enforce it reads
  nothing more (`daily_limits.may_apply`, a check in memory).
  - `kazma_core/spaces/daily_limits.py` is the one rule. A limit is tokens
    always (every call, a model of unknown price too) and dollars where the
    price is known (0: none). Whose: a person's own
    (`identity` `policies[account]["daily_limit"]`), else the limit for
    people (`identity` `defaults["daily_limit"]`), else `SHIPPED_DEFAULT`
    (3,000,000 tokens and $1.00: about 40 ordinary turns, from a median turn
    of 71,830 tokens on the live install's ledger in November 2026); the
    admin has none unless one is set for them (the install's own
    `agent.daily_cost_limit_usd` covers the admin and is unchanged).
  - Counted from the ledger by the person the calls were for
    (`llm_ledger.usage_of`, `asker` and `ts`, index `idx_llm_calls_asker_ts`)
    since the local midnight (`cron.timezone`). A spend that cannot be
    counted (the ledger off or unreadable) refuses: a person's limit is
    never taken as unspent.
  - Asked where a model is called for a person: the supervisor refuses a
    turn at iteration 0 before any model call (`verdict`, off the loop,
    after the install's own check), and `resilient_chat` refuses a call
    outside a turn (`call_refusal`: `LLMError(kind="daily_limit")`, a swarm
    worker's, research's), and so does X Studio's `x_chat`
    (`XDailyLimitReached`, the work held), which also writes each answered
    call to the ledger the limit counts. A turn that started under the limit
    runs to its end; its own token budget bounds how far past.
  - **Past the limit, a turn still runs on the person's own key** (S2-04's
    follow-up, 2026-11-23): the supervisor resolves the client the turn's
    call will go to (`graph_supervisor._turn_client`, the one answer for the
    call and the admission) and lets the turn in when they hold a key of
    their own for its provider (`daily_limits.on_own_key`; a store that
    cannot say is no). In that turn every model call goes on their own
    keys: the state's `own_keys_only`, read by every graph node first
    (`hold_to_own_keys`: LangGraph runs a node in a copy of its caller's
    context, and only turns the rule on, so a sub-agent keeps it), and
    `LLMProvider._person_call` refuses a call that would go on the install's
    key (`LLMError` kind `daily_limit`, worded by `friendly_llm_error`) -- a
    respond synthesis on another provider, a failover. A call outside a turn
    stays refused by `call_refusal`. Gate: `tests/test_member_own_keys.py`
    (a real turn on her own key past her limit, one on the install's
    refused, today's refusal and the failover under the limit as the
    controls; every graph node holds the rule, a planted node as the
    control).
  - **A tool that pays another API with the install's key keeps off it in
    such a turn** (the review of de6874f3f, `kazma_core/spaces/install_paid.py`):
    `generate_image`'s paid backends (`INSTALL_PAID_TOOLS`; asked by name,
    refused before the card by `call_preflight` in the person's language and
    at the call; `auto` and the install's default, `KAZMA_IMAGE_PROVIDER`,
    draw with the keyless pollinations: only a paid backend the call names
    is refused), the acquisition policy's remote providers (Tavily, Jina,
    Firecrawl, the proxy: `AcquisitionPolicy.permits` answers no) and E2B
    (`e2b_enabled`). A Knowledge crawl started in such a turn records the
    rule on its journal row (`own_keys_only`), and every resume binds it
    (`kb_api.resume_job`, `daily_limits.own_keys_scope`): auto-resumed after
    a restart it lost it. A document job's remote parse (LlamaParse,
    Reducto: outside any turn) is refused for a job whose asker is past their
    daily limit (`daily_limits.call_refusal(asker)`, read in the stage's
    thread; `extract_salvage.salvage_audit_scope(remote_refusal=)`), and the
    local reading is kept. `PAID_KEY_READERS` declares every tool-reachable
    module that reads an API key from the environment -- by its literal name,
    or from a table (a read whose name is not a literal, in a module whose
    tuple, list or mapping holds a key's name) -- with how such a turn keeps
    off what it pays; a new one fails `tests/test_install_paid_tools.py` (a
    planted tool and a planted table read as controls; outside such a turn
    every path is as before). **The one
    accepted exception**: the remote embedder (`memory/embedder.py`, when the
    owner chose a remote embedding model) embeds the turn's question for
    recall on the install's key -- it costs very little. Speech in and out
    of a turn (`voice/stt.py`, `voice/tts.py`) is outside the turn and was
    never counted by a daily limit.
  - **Every model call is checked or declared**
    (`tests/test_model_calls_checked.py`): each product `.chat(`,
    `.chat_stream(`, `invoke_llm_chat(`, `ledgered_chat(` and each `.chat`
    handed on is in a function that asks `daily_limits.call_refusal` or
    `verdict`, or is declared with why (in a checked turn; Kazma's own work
    after a turn, not refused until S2-07 but in the ledger since the memory
    plan's finding 6, so counted; the admin's own; a client's own retries). A new model call asks `call_refusal` or is
    declared; a planted direct call and `x_chat` without its check are the
    controls.
  - The admin sets limits on People (`people.set_daily_limit`,
    `set_default_daily_limit`, `PUT /api/people/{account_id}/daily-limit`,
    `PUT /api/people/default-limit`), each change an event of the identity
    store's record; `GET /api/people` gives each person's limit and today's
    spend (counts and money, N11). Deleting a person takes their limit.
  - The precondition of a second account (`daily_limits.precondition`,
    S1-33) holds while the ledger counts and every person but the admin
    resolves to a limit.
  - Gates: `tests/test_person_daily_limit.py` (the real supervisor graph and
    `resilient_chat`; the install-wide sum as the control, the checks taken
    out fail seven), `tests/e2e/test_person_daily_limit_browser.py` (by the
    sidebar, EN/AR).
- **Features per person (S2-11, 2026-11-22; plan 4.8, decision N18).** Only
  where Spaces is enforced: an install that does not enforce it refuses
  nothing and reads nothing.
  - `kazma_core/spaces/features.py` is the one table: each feature's pages,
    routes (`kazma_ui.route_scopes` patterns), tools, chat commands,
    background loops and connected accounts; `ALWAYS_ON` (chat, memory, My
    account, with why) and `INSTALL` (the admin's commands, Kazma's own
    loops) declare the rest. A new page, route, tool, command or loop with
    neither fails `tests/test_feature_grants.py` (planted ones as controls),
    and so does a declaration that names nothing.
  - Who has what (`grants_of`): the admin every feature, Development theirs
    to switch off (N28, the switch `identity.holds_development` reads);
    anyone else N18's (`Feature.new_member`: documents, knowledge, research,
    web, images and voice) plus what the admin turned on
    (`identity` `policies[account]["features"]`). People sets them
    (`people.set_features`, `PUT /api/people/{account_id}/features`), an
    event of the identity record each time. A grant never passes S1-20's
    group rule.
  - Refused at every part: the tenant middleware answers a feature's route
    403 `feature_off` (`route_scopes.features_of_route`: a route's own
    feature and `features.ALSO_NEEDS`'s, one thread with the host check,
    `auth._rules_here`; a page is a page in its language,
    `auth._feature_off_response`) and remembers the page's grants for the
    sidebar (`feature_here`); a socket at its handshake
    (`space_binding.space_for_socket`, close 4403 `feature_off`); the
    command palette and shortcuts offer only what the sidebar shows
    (`search_pages.pageOffered`); tools at the offer and the call through
    `spaces.tools.tool_refusal` (host groups and features together:
    `offered_here`, the registry, the MCP branch, the environment note);
    chat commands (`features.command_refusal`, which reads a command as the
    handlers do: a bot's name and a prefix, and every chat-app handler's
    `_command_claimed`); the intent engine (`features.INTENT_HANDLERS`: a
    decision whose work needs a feature the person lacks is dropped).
    Refusals name the feature as People does (`features.NAMES`, held equal
    to the catalog) in the message's language.
  - **Checked where the work is admitted** (the S2-11 review):
    `features.ADMISSIONS` names, per feature, the functions every door to
    its work passes -- research's `research_runtime._frozen`, X's
    `book_x_post` and `x_work_scope`, a document stage, a crawl's every page
    and its resume, a swarm dispatch, a scheduled fire, a feature's socket
    -- and each asks `work_refusal` for the person the work is for (a
    booked post records its `booker`; a crawl its `asker`).
    `features.NO_ADMISSION` says why the rest have none. Work that cannot
    run waits (`FeatureOff`): a booked or legacy X post stays due, a crawl
    stops `interrupted` (`feature_off`) for a Resume, nothing held or
    failed; a document job fails with Retry, and an identity outage in its
    stage is retried as `metadata_unavailable`. A new door passes an
    admission or is a new one. A record that names no account (Kazma's
    own, "operator") is left to its space's rules. A feature off keeps the
    person's data.
  - The root conftest gives every test's people -- real accounts only --
    every feature (the admin's Development kept); a test of the features
    themselves is marked `real_features`. `create_app()` reads the stored Spaces switch into the
    process: a test that sets `scope._enforced` imports what builds the app
    at collection, never mid-test.
  - The precondition of a second account (`features.precondition`, S1-33)
    holds while every stored switch names a feature of the table.
  - Gates: `tests/test_feature_grants.py` (the table against the real app,
    the registry, the commands and the loops; X Studio on for Y and off for
    G through routes, the offer, the call, a scheduled job's executor, a
    real swarm worker, `/x` and X's background work; a grant that only hides
    the menu entry as the control), `tests/test_feature_admissions.py` (the
    admission gate with a planted admission and the document stage without
    its call as controls; the intent path through the real supervisor graph;
    research's admission; X booked through Scheduled, the booker, a post that
    waits; a crawl through the real replayed crawl; the real document
    worker; live voice's handshake; every chat-app handler; pages and
    refusals in both languages; Knowledge through Documents -- each with
    its control), `tests/test_feature_grants_in_tests.py`,
    `tests/e2e/test_feature_grants_browser.py` (by the sidebar, EN/AR).
- **Shared spaces come in two kinds (S2-12, piece 1, 2026-11-22; plan 4.11,
  N29).** Only once Spaces is enforced (`spaces_off` otherwise), and only
  the admin makes one (`people.create_shared_space`, `POST
  /api/people/spaces`, Settings -> People -> Shared spaces; recorded
  `space_created`, the admin its owner). A plain space (`sandboxed`, the
  default) never has a host tool, for anyone, the admin too. A space with a
  project folder (`host`) works in that folder (its first workspace row,
  made before the space is recorded and dropped if the record fails).
  - **The folder's rules are one module** (`kazma_core/spaces/project_folder.py`,
    codes `PROBLEMS`, worded `settings.people_err.folder_<code>`): an
    existing absolute local folder (a share or device path refused on its
    text); never Kazma's install folder, data folder or home, the owner's
    machine workspace (the owner's rows, `KAZMA_WORKSPACE`) or another
    space's root, nor inside or holding one. Compared by real path AND by
    identity (`kazma_core/workspace/folder_identity.py`, the backup folder's
    rule, now one home: `backup.folder_copy` aliases it).
  - **Checked again at use**: `resolve_active_root` asks
    `project_folder.check_at_use` for a shared host space's active row every
    time, never swallowed; a folder that became a junction or link into
    Kazma's data after the space was made raises `RefusedBeforeEffect`
    before anything reads or writes through it. The per-task rung too (the
    S2-12 review, finding 10): a swarm task's workspace row and an approval
    resume's root, so another space's root is refused there.
  - **Kazma's folders come from one list** (finding 9):
    `store_registry.kazma_owned_paths` -- the data folder, the install,
    Kazma's home, every store wherever its own variable puts it, the vector
    store, the document store, an absolute research folder, what the setup
    record names (the data folder, Postgres's data, archive and base
    backups, before and after an upgrade), the engine's disk, the backup
    folder copy and the admin's extra folders. A source that cannot be read
    makes the folder `unreachable`, never one that overlaps nothing.
  - **A create is atomic** (finding 10): one at a time from the folder check
    to the record (`people._CREATE_LOCK`), so two creates never take one
    folder; a workspace row of a space the identity store does not hold (a
    create a stop cut off) is dropped before the next create
    (`WorkspaceStore.drop_rows_of_unknown_spaces`) and never counts as a
    space's root (`project_folder._roots_of_spaces`). Gate:
    `tests/test_project_folder_owned_paths.py` (the list, the per-task rung
    and the creates of before as the controls).
  - Gates: `tests/test_shared_spaces.py` (each refusal; the junction made
    after creation, with the check taken out as the control; a loopback name
    of Kazma's data, with the identity taken out as the control; a failed
    record leaves no workspace row), `tests/e2e/test_shared_spaces_browser.py`
    (by the sidebar, EN/AR).
  - **Who is in one (piece 2).** The admin adds a person
    (`people.add_member`, `POST /api/spaces/{space_id}/members`, People);
    they are told at their next page load (`member_added`, with the space's
    name). A member leaves (`people.leave_space`, `POST .../leave`, Settings
    -> This space's data -> Who is in this space); its owner or the admin
    takes one out (`people.remove_member`, `DELETE .../members/{account_id}`;
    told, `member_removed`); the last owner stays (`last_owner`). Each is an
    event of the People record.
  - **What goes with a member (N36)**, leaving, taken out, or their account
    deleted (`delete_person`, every shared space they were in): after the
    membership is gone, `memory.forget.forget_entry` forgets every fact under
    their own entry there (journaled first; their messages stay, the others'
    entries and the space's shared facts too), and their share of its
    documents is taken back (`documents.principal.take_back_access`,
    `revoke_member_access` on both document stores, audited `revoke_member`;
    their own documents stay theirs). A forget that fails is `forget_failed`
    (503), never silent. The confirm dialogs say this first, EN/AR.
  - **The forget is never left unfinished** (the S2-12 review, finding 4).
    It is journaled BEFORE the membership changes (`people._journal_then`:
    the change tried on a copy first, so a refused one journals nothing;
    `forget_entry(journal_seq=)` marks that entry applied), so a stop or a
    failure after the change leaves an entry the forget convergence applies.
    The convergence also forgets every `member_*` entry whose account is no
    longer a member of its space (`forget._forget_departed_entries`: a forget
    never journaled, a deleted account), and a replay never forgets a current
    member's entry (`forget_journal._still_a_member`). The removed member's
    notice says the facts were forgotten only once they were
    (`notices_for` `forgotten`, `forget.entry_forgotten`; else the pending
    wording). Gate: `tests/test_member_forget_converges.py` (the flow of
    before as the control).
  - Gates: `tests/test_space_membership.py` (through the real memory store
    and recall; the flow without the forget call as the control),
    `tests/test_document_listing_queries_are_joined.py` (the take-back on
    SQLite and Postgres), `tests/e2e/test_space_membership_browser.py` (by
    the sidebar, EN/AR).
  - **A departed person's queued work is refused** (the S2-12 review,
    finding 6). `identity.work_scope` raises `identity.AskerLeft` for work a
    person asked for in a space they are no longer a member of (a deleted
    account too: `_was_a_person` reads the accounts and the record); it
    never runs as the space's acting account, which stays for work no person
    asked for and for a principal that never was a person (work from before
    Spaces). A scheduled fire is refused `asker_left` (EN/AR), a swarm
    task's worker does not run, a booked X post is refused as work of no
    space, a crawl is not resumed (`asker_left`), and a document job's stage
    fails `asker_left` (`identity.asker_left`; the owner's own space never,
    whose older jobs name principals that are no accounts). The space's own
    account may still cancel or reject such a task
    (`single_operator._acts_for`). Gate:
    `tests/test_departed_members_work_refused.py` (the rule of before, which
    ran their work as the admin, as the control).
  - **Leaving ends what is open** (the S2-12 review, finding 7). The
    identity store, the one writer of memberships, tells
    `spaces.departures` every change it writes (`departures.note`: a
    membership gone is a departure, one back clears it; in this process, in
    memory). `kazma_ui.live_connections` listens: that person's sockets in
    that space (chat, voice, the dashboard feed: `socket_watched`) are closed
    4403 `not_a_member`, and their attached chat streams (`stream_watched`,
    `sse_chat._sse_attach_stream`) are woken and end. A turn starts only for
    a member of its space (`threads.scope_for_turn`, `_AskerNotInSpace`): a
    request that slipped in before the removal, and a resume of their turn,
    get none. A new socket or stream that names the space is refused by the
    membership check every request passes. Gate:
    `tests/test_departed_connections_closed.py` (the store that does not say
    as the control).
  - **Development per person (piece 3).** In a shared space with a project
    folder `spaces.tools.host_allowance` gives Development to each person
    who holds it (`identity.holds_development`, turned on per person on
    People; the admin's own switch, N28), in that space's own folder; the
    admin's own tools are never there (`why_not_admin`). A plain shared
    space has none, for anyone. People says what turning it on means, and
    asks first (EN/AR).
  - **N21: the admin answers a holder's always-ask cards.** Until S2-05 a
    person other than the admin who holds Development was refused their
    always-ask actions and folder grants in a shared space with a project
    folder, before any card. S2-05 lifted that refusal: the card is made and
    goes to the admin (`spaces.approvals`, below). Their file and git cards
    stay their own.
  - **A person other than the admin runs no project code on the host** (the
    S2-12 review, findings 1, 2 and 8; decision 3: members' code runs only
    in a container, S4-05). `HostAllowance.member` (a holder who is not the
    admin, in a shared space) gets only `spaces.tools.MEMBER_DEVELOPMENT`,
    each tool declared with why it starts no host process that runs the
    project's code; the shell, the test runner, the linter and formatter,
    the metrics and every MCP server are refused, offered or called
    (`_host_refusal`). Their git (`security.git_auth.git_command_env`, every
    git command of the git skill) runs with an empty hooks folder,
    `--no-verify` (`git_auth.no_verify`, read from the environment), no
    fsmonitor, pager, editor, signing, `ext::` or `file://` transport, and
    is refused before anything runs when the repository's own configuration
    names a filter or merge driver, a diff program or an include
    (`git_auth._member_git_refusal`). They write nothing under `.git`
    (`path_policy.check_path_access`, `via="git"`, which no grant opens).
    Outside the owner's space every git command -- the git tools' and the
    shell's (`post_hitl.restricted_child_env`) -- gets none of the machine's
    credentials (`git_auth.drop_machine_credentials`: no agent or askpass,
    every credential helper reset, `NO_KEY_SSH`: no ssh configuration file,
    no key). The filesystem MCP server is refused in a shared space to
    anyone (N27). A new Development tool is refused to members until it is
    declared there. Gate: `tests/test_member_runs_no_project_code.py`
    (planted tools and the allowance of before as controls; real hooks on a
    real repository, the admin's hooks running as the control).
  - Gates: `tests/test_development_per_person.py` (S1-20's rule and the
    flow without the interim refusal as the controls; N21 over every
    always-ask tool, enumerated from `safety.hitl.ALWAYS_HITL_TOOLS`, at the
    preflight, the graph and the registry: the review's finding 11),
    `tests/test_host_tools_absent.py` (re-pointed), the People confirm in
    `tests/e2e/test_shared_spaces_browser.py` (EN/AR).
  - **The header says where the tab is (piece 4).** `space_scope.js`
    (`showSpace`, from the spaces it already reads) shows the space's kind
    (`#space-kind`: personal, shared, shared with a project), its name when
    there is no switcher, and in a shared space "New private chat"
    (`#new-private-chat`, `kazmaSpace.newPrivateChat`: the tab moves to the
    person's own space with an empty chat, whose thread is recorded there).
    Gates: `tests/js/test_space_scope.js`, the header test in
    `tests/e2e/test_shared_spaces_browser.py` (EN/AR).
  - **Moving one's own document (piece 5).** "Private means in my personal
    space": a person moves their own document from their own space into a
    shared space they are in, or takes it back out (`spaces.moves`: Spaces
    enforced, a member of both, one of the two their own personal space and
    the other shared; `DocumentIngestionService.move_document` checks it is
    theirs). The original goes through the target space's own intake (as an
    upload would), then the source is unindexed and tombstoned; a source
    that cannot be retired takes the copy back, so it is in exactly one
    space. Taking it out leaves the shared space a line (who, when, the
    title it had: `taken_out_documents`, tombstones of reason `taken_out`,
    which only the move writes: a delete refuses `MOVE_REASONS`). A move is
    the person's click on the Documents page (`POST
    /api/documents/{document_id}/move`), never the agent's. Files and chats
    are not moved here.
  - Gates: `tests/test_document_moves.py` (a move that does not retire the
    source as the control), the taken-out read on SQLite and Postgres
    (`tests/test_document_listing_queries_are_joined.py`), the matrix case,
    `tests/e2e/test_document_moves_browser.py` (by the sidebar, EN/AR).
  - **A move is safe at once and across a stop** (the S2-12 review,
    findings 3 and 5). One move of a document at a time, from reading it to
    retiring it (`ingestion._move_lock`); each attempt has its own key
    (`move:<uuid>`), is recorded before it starts in `ingestion.MOVES_KEY`
    (`system.documents.moves`: ids only; a second move of the same document
    is refused `move_in_progress`, 409, while one is recorded) and removed
    when it ends. A failed retire takes back only a copy this attempt made
    (not `reused`) and only while the source is still there. Every start
    settles a move a stop cut off (`DocumentIngestionService.settle_moves`,
    in `start_workers`): its copy is taken back while the source is still
    there; one whose source was retired is done. A document with more than
    one version is refused (`more_than_one_version`, 409): a move never
    leaves its earlier versions behind. Gate:
    `tests/test_document_move_safety.py` (piece 5's move, interleaved, as
    the control: in neither space).
