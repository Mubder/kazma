<!-- AGENTS.md, piece 70h-spaces-fairness-people-keys. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Fairness between spaces and between people (S4-04, 2026-11-23).** Only
  where Spaces is enforced: an install that does not enforce it reads
  nothing more, counts nothing, and every queue keeps its order.
  `kazma_core/spaces/fairness.py` is the one rule.
  - **A person's share** (`person_share`): in a shared space with more than
    one member, each person holds at most half of any of the space's
    shares, rounded up; in a personal space, or one with a single member,
    the space's. `shares(kind, space)` answers both;
    `space_limits.running_refusal(..., person=, person_running=)` refuses
    for either, and crawls (`crawl_jobs.start`, the journal row's `asker`),
    research runs (`research_runtime._take_space_share`) and swarm tasks
    (`engine._running_ahead_of`, the task's `asker`) ask it.
  - **Turns and scheduled jobs have shares** (`SpaceLimits.turns`, 8;
    `scheduled`, 2; both on the People page), counted in this process
    (`fairness.turn_slots`, `fairness.scheduled_slots`). A new turn takes its
    slot in `turn_segment_budget`, waiting up to `TURN_WAIT_S`; past it the
    turn is refused before it starts (`TurnSegment.refused`, `expired` too,
    the honest end written in the person's language, never recorded as
    failed: `turn_failed` False, as the daily-limit refusal) and the caller
    runs no graph: every caller of the budget checks `segment.refused` (a
    gate).
    An approval's resume is let in and counted; a turn inside one that
    holds a slot -- a sub-agent's, a scheduled task's -- takes none
    (`fairness.held`). A due job takes its slot in
    `CronScheduler._execute_bounded` before the scheduler's own semaphore,
    waiting up to one poll and holding nothing while it waits; one still
    beyond its share is left pending, unclaimed. A reminder (no model call)
    takes no slot (`CronScheduler._takes_a_slot`): behind a running task
    fire it would ring minutes late, or past its catch-up window. With
    Spaces off a slot is not even asked (no thread hop).
  - **Queues take turns**: the scheduler's due list by space, then by
    person (`SQLiteCronStore.due_jobs(fair=True)`); document claims, within
    a tenant, by the person with the fewest running (`fair_claim_order`'s
    `running_actor`, both backends; with Spaces off the order is the one
    before S4-04); the memory lane by space (N37:
    `memory_task_queue.claim_space`, filled at enqueue -- `enqueue_task(
    space=)`, else the payload's tenant, else the bound scope -- read by
    `_claim_batch`; never whose data a task is, which the space walk reads
    from the payload).
  - **Uploads and paid endpoints**: a space's documents are bounded by its
    queue and rate budgets (`documents.admission`, per tenant); every rate
    window (`kazma_ui.rate_limit`) is keyed by the person the tenant
    middleware bound, wherever they sign in from, and each request also
    counts in its space's window of `SPACE_WINDOW_FACTOR` (2) people's
    worth: both admit it or neither counts it.
  - Gates: `tests/test_space_fairness.py` (turns through the real
    `run_agent_turn` and graph, the scheduler's real poll on a real store,
    real document claims, the real memory claim, the real rate dependency;
    one queue for all as each control: one space's turns, jobs, claims or
    tasks first and another space's waiting; a caller of the budget that
    runs the graph anyway as the gate's control; the People page's limits
    in both languages).
- **A member's code runs only in a container, with the network only where
  Kazma filters it (S4-05, S4-05b, 2026-11-23).** Only where Spaces is
  enforced: an install that does not enforce it reads nothing more, and the
  admin's code keeps the rules it had. `kazma_core/spaces/member_code.py`
  `code_rule()` is the one answer for the work running now: anyone but the
  admin, in any space, runs `python_exec` in the Docker jail or not at all
  -- never the host fallback, whatever `KAZMA_CODE_EXEC_ALLOW_LOCAL` says
  (`code_exec.local_exec_forbidden` asks it first), and never E2B (the
  install's paid key, its internet not this filter's;
  `sandbox.e2b.e2b_enabled`). Work no person asked for likewise, with no
  network. A chat-app turn runs as the person whose linked account sent it
  (stage 3, `chat_apps.chat_place`), so their code is held there too.
  - **The network is the public internet only, on Kazma's own engine**
    (S4-05b; the review of S4-05: the first build's `--network bridge`
    reached a host service through the LAN and WSL addresses, the router's
    admin page and cloud metadata). Setup's step `engine:egress`
    (`container_engine_setup._action_egress`, `egress-filter.sh`) puts in
    the distro `kazma-egress-rules` and the unit `kazma-egress.service`
    (before Docker, at every start of the distro), then makes the network
    `container_engine_setup.EGRESS_NETWORK` on the bridge `EGRESS_BRIDGE`
    with `EGRESS_LABEL`. A packet from the bridge goes on only to a public
    address: the private ranges, 100.64/10, link-local (169.254/16, the
    metadata address), loopback, the reserved, documentation and multicast
    ranges, this computer's own addresses (the distro's, and Windows' as
    listed at setup) and any local destination are refused at once (TCP
    reset or ICMP), in Docker's `DOCKER-USER` chain ahead of its own rules;
    a packet to the distro itself (`INPUT`) and every IPv6 packet from the
    bridge are refused. `member_code.network_refusal` says why it cannot be
    given here (`NETWORK_REFUSALS`: a public install, the cloud profile;
    `KAZMA_TRUST_LAN` on; another engine, Docker Desktop or a system Docker,
    whose rules Kazma does not control; the filter not put in, read from
    setup's stamp), else "". Refused, `allowed_network` answers False
    whatever is stored and People refuses turning it on with that code (409,
    worded EN/AR; `/api/people` gives `code_network_refusal` and the page
    shows it beside a disabled switch). Allowed (People asks first), the
    person's jail joins the filtered network -- checked as setup's
    (`egress_network_ok`) before every run, a network of the name made
    otherwise refusing the run -- and the snippet may import the network
    modules; everyone else's runs `--network none`. Never put a member's
    container on the engine's default network.
  - **The rules are checked, never assumed** (the review of S4-05b,
    2026-11-23: a stamp and a network say the filter was put in, not that
    its rules still hold -- another distro sharing the network namespace,
    `iptables -F` or a firewall backend change takes them out, and the
    bridge reached everything). Before every networked run
    `container_engine_setup.egress_rules_in_effect` runs
    `kazma-egress-rules ... check` in the distro as root: Docker's jump to
    `DOCKER-USER`, the bridge's jump to `EGRESS_CHAIN` and the `INPUT` and
    IPv6 refusals are each the FIRST rule the bridge's packets meet
    (`first_for`: a rule for another interface by its whole name is not in
    front of them -- `-i` read only among the words iptables prints before
    any match, so a comment naming one hides nothing, and a rule with no
    condition is a rule: the third review), and the chain is exactly the list the script makes
    (`chain_rules`, compared with `iptables -S`, so each rule is written as
    iptables prints it). Asked rule by rule with `iptables -C`, the first
    check answered "in effect" with an ACCEPT in front of the jump and with
    a range's UDP refusal gone (the second review). Anything else refuses
    the run, never another network, and the check names the rule in front
    (`EGRESS_IN_FRONT`). Setup's step ends with the same check: put in is not
    in effect, and a step whose rules do not hold writes no stamp and says
    which rule stands in front (`_egress_check`). Put in again, the chain is built under a
    name of its own (`<chain>-NEW`) and swapped in (jump first, the old chain
    out, then renamed), and a refusal that lost its place is put in front
    again (`put_first`): the bridge is never filtered by an empty chain.
    **Rules that cannot be put in cost the members' network, never Docker**
    (the second review: a Docker that required them took SearXNG, Neo4j and
    the document worker down with nobody given the network): the drop-in
    (`docker.service.d/kazma-egress.conf`) puts them in again before Docker
    starts and lets it start whatever that answers, and once it runs
    `kazma-egress-guard` removes the network when the check fails and says
    what `docker network rm` answered: removed, not there, still there, or
    Docker could not say (`kazma setup engine` puts both back).
    **The bridge is held shut while Docker starts** (the drill of 2026-11-25,
    `docs/audits/EGRESS_RESTART_DRILL_2026-11-25.md`): Docker takes its jump
    to `DOCKER-USER` out and puts it back behind its own rules for half a
    second, and a container it could not kill (a hung shim) got through. The
    drop-in's first `ExecStartPre` is `kazma-egress-rules ... hold` (a DROP in
    raw `PREROUTING`, both families); only `release` -- the check, the hold
    aside -- ends it, from the guard or setup. A held bridge is not in effect
    (`HELD:`). `container_engine_setup.check` asks the rules too: flushed, it
    said "ok" and `kazma setup engine` repaired nothing. A kernel with
    no IPv6 (`/proc/net/if_inet6` absent) carries none: nothing is put in for
    it. Not refused, and said on People's confirm (EN/AR) and in the
    guide: the network's own public address (hairpin NAT to the router's
    public side, its forwards and its admin page if it answers there).
  - **Only a member's network needs the filter** (the main session's rule,
    2026-11-23): until the admin gives someone's code the network
    (`member_code.network_given`: a stored `code_network` of anyone but the
    admin; an identity store that cannot be read is nobody), an engine
    without the filter is whole -- `container_engine_setup.check` lists it
    in `EngineCheck.waiting`, never `missing` -- so nothing pages, no check
    is red and `kazma setup install` repairs nothing (the live install has
    no filter). Once someone has it, it is missing and the install puts it
    in. `kazma setup engine` puts it in all the same
    (`install_steps(with_waiting=True)`), and the filter's step comes after
    the engine's record: a filter that fails never leaves the engine
    unrecorded. Nothing else (readiness, the guard, doctor) reads it.
  - S2-12's member rules (`spaces.tools.MEMBER_DEVELOPMENT`) keep a member
    off every host process that runs a project's code; this holds the one
    code runner they keep.
  - Gates: `tests/test_member_code_in_containers.py` (the rule; per-space
    runs with no Docker refused, the local fallback reading a host file as
    the control; each refusal of the network, the switch and its route
    where Kazma filters and where it cannot; the jail on the filtered
    network only for an allowed person, `--network none` for the rest, a
    network not setup's refusing the run; E2B never; a chat-app turn of a
    linked person; the source holding the jail to `none` or the filtered
    network, the first build's `bridge` as the control; a real Docker run,
    `real_docker`), `tests/test_engine_egress_filter.py` (the rules script
    run by bash against stand-in iptables: every refused range and the
    host's own addresses, the public internet on, nothing doubled, removal;
    each way the rules stop being in effect, a rule in front of a jump and a
    refusal taken out among them, `iptables -C` rule by rule as the control;
    no IPv6; the guard; without the host's addresses as the control),
    `tests/test_container_engine_setup.py` (the filter waiting until
    someone has the network, the check of before as the control; the step:
    rules before the network, a network made otherwise replaced, nothing
    made when the rules
    did not go in), `tests/test_member_code_egress_engine.py` (opt-in,
    `KAZMA_TEST_EGRESS_ENGINE=1`, on Kazma's engine with names of its own:
    the public internet reached, this computer and the metadata address
    refused at once; Docker's default network reaching this computer as the
    control; the rules on real iptables in a container's own network
    namespace), `tests/e2e/test_person_code_network_browser.py` (by the
    sidebar, EN/AR: the reason and a disabled switch where Kazma cannot
    filter; where it can, a stored allowance shown on, taken back, allowed
    again after the confirmation).
- **A space moves to another install (S4-06, 2026-11-23; reworked after its
  review the same day).** Only where Spaces is enforced: an import into an
  install that does not enforce it is refused (`spaces_off`), and so is one
  on Postgres (`postgres_not_yet`, as S1-27).
  `kazma_core/spaces/space_move.py` is the one home.
  - **"Move this space"** (This space's data, a shared space's owner only,
    `POST /api/spaces/{space_id}/move`, refused `not_the_owner` to anyone
    else) makes a bundle (`space_move.FORMAT`, `kazma-space-<id>.kazmaspace`);
    a personal space stays (`personal_space_stays`). **"Bring in a space"**
    (People -> Shared spaces, the admin, `POST /api/spaces/import`: the file,
    its password, who owns it here) takes it in.
  - **One AEAD seals the whole file** (`kazma_core/security/sealed_file.py`,
    the owner's decision): scrypt (`sealed_file.SCRYPT_N`) of a password the
    person chooses, then AES-256-GCM over chunks of `sealed_file.CHUNK` bytes,
    each nonce the file's prefix, the chunk's index and whether it is the
    last, every chunk bound to the header. A chunk moved, dropped, repeated,
    cut off or grown does not open, so neither does a renamed, added or
    dropped entry; the header's check tells a wrong password (`wrong_password`)
    from a changed file (`changed`). `SealedWriter` takes the zip as a
    stream into a `.partial` file and `SealedReader` serves it by position,
    each chunk checked again as it is read: no plain copy is written to disk.
    Inside, `bundle.json` lists every entry and its size, and the import
    takes exactly those. A JSON entry or a row is read up to
    `space_move.MAX_JSON_BYTES`, rows a line at a time; an export bigger
    than an import takes (`space_move.MAX_BUNDLE_BYTES`) is refused before it
    is written. S1-27's own export keeps `security/sealed_zip.py` (opened
    with 7-Zip).
  - **The space's key travels inside the seal, never a vault key**:
    `SecretVault.export_space_sealed` seals the space's secrets with its key
    (sealing first a row still under the vault key) and hands the keys over;
    `SecretVault.import_space_sealed` wraps them with the target's own vault
    key, in one transaction that opens every secret before it commits. The
    key that leaves is changed here as the bundle is made
    (`space_keys.change_key`, recorded): it opens nothing kept on the old
    install. A secret that does not open stops the bundle
    (`secrets_unreadable`).
  - **Only the owner arrives** (the owner's decision): the bundle maker's
    account becomes the person chosen; everyone else in the space leaves it,
    as a member who leaves does (S2-12), and is invited again there. Their
    memory entry (`member_<id>`, its facts, About me, suggestions, changes),
    their work (scheduled jobs, swarm tasks, research sessions, X posts and
    composers), their share of documents and the accounts they connected do
    not travel. A space secret records who stored it (`vault.STORED_BY` in
    its metadata): the bound asker of its FIRST write, and from then on only
    a person connecting the account again changes it
    (`spaces.connected_accounts.connecting`: the tenant middleware binds it
    for the requests in which a person gives the account, named one by one
    in `platform_rbac.CONNECT_ROUTES` and never by prefix -- a disconnect or
    a start writes too; every other request under the account routes is
    declared not one in `tests/test_space_move_third_review.py`;
    `oauth_sign_in.attempt_scope` for a sign-in callback; the agent's
    `vault_store`). Every other write -- a
    token refresh in whoever's turn, the hourly check as the space's maker
    -- keeps the record the row has, none included (the second review:
    stamped at every write, a refresh made another member's account the
    maker's, and it travelled). Another person's stays, with its
    connection's settings; one with no record travels only from a space
    nobody but its owner was ever in (`_only_ever`). A new route or tool
    that connects an account binds `connecting`. What they wrote stays the
    space's, written by `identity.DEPARTED_ACCOUNT`, which is no account:
    work naming it is refused as a departed person's (`identity.work_scope`).
    Nobody who stays is named inside a chat's saved state either
    (`_rewrite_checkpoint`: a card's `asker`, `answered_by` and its names,
    the state's `said_by`; on arrival the owner's own become the new
    owner's, and a state naming a person of this install is refused). A
    state, row or setting nested past `space_move.MAX_DEPTH`, a checkpoint
    kept in a way LangGraph does not write (`_STATE_TYPES`) and a number
    that is none (`math.isfinite`, `_a_number`; a JSON NaN) are refusals on
    the way in (`not_a_bundle`, `bad_setting`) and, for a row, left out and
    counted on the way out: never a RecursionError.
  - **Every table says how it moves** (`space_move.MOVE_RULES`, a
    `MoveRule` each: `moves`, with the column that names its person, its
    memory entry or grant; `stopped`; `out`, with why). A table a space keeps
    with no rule refuses the move. **Never moved**: ledgers (`llm_calls`,
    the forget journal, the action and audit records: a planted spend row
    counted against the daily limit), outboxes and alerts (`cron_deliveries`
    and `cron_alerts` would have sent and paged outside the space), the old
    install's own state (a chat app's routing, approval cards, Time Travel's
    steps), what is rebuilt here, and a chat's state: grants, YOLO windows
    and pointers. A setting moves only when it is NAMED
    (`_MOVED_SETTINGS`: the space's X and GitHub connection keys, each with
    what it holds), each value checked both ways (`_holds_what_it_should`;
    `foreign_setting`, `bad_setting`, `missing_secret`); what stays is in
    `_STAYING_SETTINGS` with why. A new `connectors.x.` or
    `connectors.github.` key goes in one of the two or
    `tests/test_space_move_second_review.py` fails: moved by prefix, the
    files X drafting reads (`connectors.x.drafting.reference_files`)
    travelled, and a bundle's owner read any file of the new machine. Once
    Spaces is enforced `x_api.references.load_references` also asks the
    bound space's path policy, whoever set them.
  - **Live work arrives stopped** (the owner's decision; a move is a copy
    and the old install still runs it, `_STOPS`): a scheduled job cancelled
    (Restore here, to the new owner's own chat), a swarm task cancelled, a
    research session interrupted, an X post due to publish held for approval
    (one being sent: outcome not known), an X thread cancelled, a crawl not
    moved. A chat paused at an approval card arrives with the card expired
    (`_expire_waiting_cards`: a gate row timed out, the turn not resumed).
  - **The space's checkpoints land in its own file**
    (`paths.space_checkpoints_db`, also what the gateway's checkpoint store
    opens): a bundle travels them under one name (`space_move.CHECKPOINTS`),
    from `checkpoints.db` or `checkpoints_<space>.db`, and one naming any
    other checkpoint file is refused. **One file per space, and an id Kazma
    makes** (the third review, 2026-11-24: the name lost the underscores at
    its ends, so an id `v_` had `v`'s file, and a refused import of `v_`
    deleted `v`'s chats): a space id is the file's name as it is (a tenant of
    before Spaces keeps the name it had, unless its id ends in an underscore), a bundle's id must be one Kazma
    makes (`scope.made_space_id`, `scope.new_space_id`: `s` and twelve hex
    digits), and a checkpoint file of its name that is here counts as the
    space's being here -- a retired memory mode's is someone else's -- but
    for the emptied one of a space this install erased (`_emptied_here`),
    which a space moving back is written into.
  - **Refused before anything is written**, each code worded EN/AR
    (`settings.space_data_err.<code>`): the password, a changed byte, an
    entry not listed or listed and missing, too big; anything of the space
    here already (`space_exists`); a row that is not the space's, a row of a
    table that never moves (`not_moved`), another member's entry, work or
    account (`foreign_member`, `members_work`), a person of this install
    named by a row (`foreign_person`); an id here already (`id_collides`:
    never merged; integer row ids alone are given new numbers) -- in any
    store and whoever's it is, a chat of the owner's from before Spaces, which
    has no record, among them (`_check_ids`: such a chat was recorded as the
    space's, and a take-back deleted it); an id this install makes by itself
    for another space and has not yet (`foreign_row`, the fourth review: a
    chat app's chat, `chat_apps.foreign_chat_thread` -- `gw-` with nothing
    random in it and no `--in-<space>` of its own at its end -- in any row's
    `thread_id` or a transcript's `session_id`; a library whose id
    `_library_id_in_space` would not give it: that person's own chat was
    "another space's" from then on, and the owner's "Handbook" failed); a
    file or folder at the top of the space's folder named as the import's
    own mark (`reserved_name`, as the bundle is made too: never dropped
    without a word; a mark that could not be removed is Kazma's own); a pointer to a secret not in the
    bundle (`foreign_secret`); a table or column this install lacks.
  - **A half-done import leaves the install as it was**: every row store is
    made by its own code first (`spaces/row_stores.py`); the space is
    recorded in `space_move.JOURNAL_KEY` before anything is written; its
    chats' records are written first, alone, and what the checks asked is
    asked again after them (`_claim_threads`; the entry says `claiming` until
    then: a take-back of such an import removes the records alone, since a
    row keyed by one is the chat's that was made here meanwhile; released,
    never ended -- `threads.release_thread`,
    `tests/test_space_move_ends_no_chat.py` -- so that chat runs and the
    bundle can be brought in again); a
    failure takes back what was written by the erase's own walk, by what is
    recorded here as the space's and **never by the ids the bundle names**
    (a chat or a job made here after the checks was erased by its id), and a file
    or folder only when it carries the import's own mark, which the journal
    holds (the checkpoint file's `application_id`, put in place over nothing;
    `.kazma-arriving` in a folder until the space lands): never one another
    space has -- or an empty one at the name with the import's staged file
    still beside it (`_claim_and_replace`, a disk with no hard links: a
    replace that fails gives the name up, a stop leaves both); a staged file
    a program holds is no failure of an import that landed (`_unmark` tries
    again). The checkpoint file goes last -- a program may hold it open --
    and the journal keeps it for the next start. One a stop cut off is taken
    back as Kazma starts (`settle_at_start`) or by the next import, never a
    running one (`settle_unfinished_imports` takes the import lock), nor one
    that landed: its entry is closed and said so. The space joins the
    identity store last (`people.record_moved_space`, event
    `space_imported`; its refusal is a worded code). **So what takes a space
    with no member, or none at all, for one that is gone leaves a space in
    the journal alone** (`importing_spaces`, read AFTER what is stored and
    BEFORE the identity store): the forget of departed entries (it forgot
    the new owner's facts as they arrived), the erased-key sweep (a space
    erased here once and moved back) and the document worker, which gives
    the job back on the attempt it had, due again in half a minute
    (`hand_back` on both job stores: counted as a failed attempt, it was
    dead-lettered in fifteen seconds). A new sweep of that kind asks it. A
    project folder's files stay on the old machine (the guide says so).
  - **An erase leaves no activity row**: `activity.record` writes nothing
    for a space the install no longer holds or is erasing.
  - Gates: `tests/test_space_bundle_round_trip.py` (S1-27's world exported
    and brought into a fresh install with another vault key: every row that
    moves, word, count, setting, secret, file and thread record of A, none
    of B; the key and secrets absent in the clear; a bundle carrying B's row
    refused, and landing with the row check taken out; each refusal leaving
    the install as it was; a half-done and a cut-off import taken back; the
    routes; every refusal worded; then every case of the space matrix on an
    install whose space A was moved in), `tests/test_space_move_review.py`
    (each of the four decisions and eight review findings, every one failing
    on 052dd4be3; a table with no rule as the control),
    `tests/test_space_move_second_review.py` (the second review's, failing
    on 46f200bcb), `tests/test_space_move_third_review.py` (the third's,
    failing on b55865291; the file name of before, where `v` and `v_` meet,
    as the control), `tests/test_space_move_fourth_review.py` (the fourth's,
    failing on b5d8330c7; the gateway's own ids for every chat app, made by
    its own code),
    `tests/e2e/test_space_move_browser.py` (by the sidebar, EN/AR: moved,
    the file sealed whole, deleted, a wrong password refused, brought back in
    with a person as owner).
- **Every action under the person's name (S2-09, 2026-11-22).** Only where
  Spaces is enforced; an install that does not enforce it records nothing
  more (the memory operator trail and the logs as before).
  - `kazma_ui/activity.py` `ActivityRecorder`, an ASGI middleware outside
    the tenant middleware: every request that changes something (`POST`,
    `PUT`, `PATCH`, `DELETE`) and reached a route is one row of the space's
    activity (`kazma_core/spaces/activity.py`, `activity.db`): when, the space
    it acted in, the account the tenant middleware bound (`kazma_asker` and
    `kazma_space` in the request: never a session label, a body or a header
    the client chose), the route's method and template, the ids in its path,
    the answer's status. Never a body or a query value. A request no person
    made writes nothing. One middleware: a route added later is recorded the
    day it is added.
  - A space the install no longer holds, or is erasing, gets no row
    (`activity._held`): an erase is answered after its space is gone (S4-06).
  - The memory operator trail names the account too (`account:<id>`), the
    session's label only where no account was bound.
  - **The Activity page** (`/activity`, `GET /api/activity`; sidebar and
    palette only where Spaces is enforced): the space's actions to its
    members, newest first, with each person's name; the membership check
    every request passes is its only door, the admin's included (S1-25).
    Kept 90 days, at most `activity.MAX_ROWS_PER_SPACE` a space, pruned as
    they are written; a space's rows go with its erase and are in its export
    (the `space` column).
  - Gates: `tests/test_actions_name_the_person.py` (every mutating route of
    the built app, enumerated, called through the real tenant middleware and
    the recorder, names the person; a recorder writing the session label as
    the control), `tests/e2e/test_activity_page_browser.py` (by the sidebar,
    EN/AR).
- **The People page shows each person whole (S2-10, 2026-11-22).** Only
  where Spaces is enforced (People is the admin's, S1-25). Beside each
  person's features, tools, daily limit and models (S2-11, S2-01, S2-02,
  S2-03): the spaces they are a member of with their role
  (`people._spaces_of`), how many sessions they have open and when they last
  signed in (`people._sign_ins`: the newer of the sign-in recorded at the
  login route, `people.note_sign_in`, runtime key `people.LAST_SIGN_IN_KEY`,
  and their newest session's start, `web_sessions.session_starts`; never a
  session id). The admin ends a person's sessions (`people.end_sessions`,
  `POST /api/people/{account_id}/sessions/end`, event `sessions_ended`, told
  at their next sign-in); the owner's own are ended in My account
  (`owner_in_my_account`). A sign-in that cannot be recorded is never
  refused for it. Gates: `tests/test_people_sign_ins_and_sessions.py` (the
  real login route), `tests/e2e/test_people_page_each_person_browser.py` (by
  the sidebar, EN/AR: a person signed in from a second browser, every part
  of their row, their sessions ended and the second browser signed out).
- **A member's own model keys (S2-04, 2026-11-23; plan O2, 4.9).** Only
  where Spaces is enforced, and never the admin's (the install's keys are
  theirs, under Providers); an install that does not enforce it reads
  nothing more.
  - **Kept in their personal space, sealed with its key**
    (`kazma_core/spaces/own_keys.py`): the vault name
    `space:<personal space>:model_key.<provider>` (`own_keys._secret_name`),
    a space's secret, so S1-26 seals it and S1-27's count, export and erase
    of that space reach it. Read through `SecretVault.for_space`, only for
    the person bound now (`own_keys.key_for_call`): their work in a shared
    space uses it too, nobody else's. Only a provider signed in with a plain
    key (`own_keys._takes_own_key`: a Bearer, `x-api-key` or `api-key`
    header; not a server on this network, AWS or Google's sign-in).
  - **Used at the call**: every `chat` and `chat_stream` of every provider
    class asks `LLMProvider._person_call` first -- S2-03's model check
    (`_refuse_a_model_not_allowed`), then the person's own key -- and hands
    the whole call to the client it returns
    (`LLMProvider._own_key_client`: the row's model and prices, their key,
    no switch to the install's other keys, none of the row's secret headers,
    never through the install's egress proxy: pinned to the provider's own
    address, taken as a string before the twin was built, since a
    constructor routes its config through the proxy in place -- compared
    with that same object, her key went to the install's LiteLLM proxy, the
    review of S2-04). Their key refused, the call
    fails as theirs (`_switch_key_after`), never the install's key in its
    place; a store that cannot say whether they have one refuses the call
    (`own_key_unreadable`). A client built with a typed key (the providers'
    check) is marked `_key_is_given` and never swapped. A new provider class
    asks `_person_call` and hands the call on, or the gate fails.
  - **Their spend on it is theirs, read from the answer**: the delegation
    marks the answer it was given (`LLMResponse.own_key`, `own_key_answer`,
    `own_key_delta`), every ledger writer of an answered call passes
    `own_key=llm_ledger.answered_with_own_key(response)` and names the
    client that answered (`provider_of`: its `provider_name`; the
    supervisor's routed or failover client, never the build-time one), and
    neither `llm_ledger.usage_of` (their daily limit), `usage_since` (the
    install's) nor `spend_by_person` counts it. The ledger never works it
    out from a provider's name: it named the build-time client's provider
    and asked the vault whether she had a key for it, so a call on the
    install's key was recorded as hers and the reverse (the review of
    S2-04). Gate: every ledger write passing `usage=` passes `own_key=`.
  - **The routes** (`kazma_ui/own_keys_api.py`, `/api/account/model-keys`):
    GET lists the providers they may bring a key for, each with `own`, never
    a key; PUT keeps a key only once one short message through it answered
    (`provider_check.check_model(own_key_of=(space, account))`: to the
    provider's own address with the typed key alone, never through the
    egress proxy -- behind it the proxy's key answered, a key that does not
    work was kept and the install paid -- and ledgered as theirs, `own_key`
    only when their key answered; rate limit bucket `own_keys`, 6 a
    minute); DELETE forgets it. Settings -> My account -> "Use my own key"
    (`#own-model-keys`, a member's page only), EN/AR.
  - Gates: `tests/test_member_own_keys.py` (her key in the header and never
    the install's, on the OpenAI-shaped and Anthropic classes, chat and
    stream, from a shared space too; refused as hers with nothing else
    tried; nobody else's calls changed; her own-key spend outside her limit;
    the admin's work cannot read it and no GET route answers it to the
    admin; the install-scoped row as the control; the review's cases --
    a real supervisor turn on the install's key and on hers, a provider
    moved to this network, `resilient_chat`'s rows, her key and her check
    behind the egress proxy, the route's limit -- fail on beb126dec),
    `tests/test_person_models.py` (the S2-03 gate requires `_person_call`; a
    class asking the refusal alone is the control),
    `tests/e2e/test_member_own_keys_browser.py` (by the sidebar, EN/AR,
    through the real check behind the egress proxy: a refused key keeps
    nothing, a good one is kept sealed and never shown, "Use the install's
    key" forgets it; the admin's page, and the check sent through the
    proxy keeping a refused key, as the controls).
    Past their daily limit a turn on their own key still runs, on their own
    keys only (S2-02's entry above).
- **A member's page asks nothing the server refuses (2026-11-23).** The
  page shell read the install's settings on every page -- the appearance
  (and a `PUT` on a theme switch), `/api/provider/active`, `/api/providers`
  and `/api/settings/voice` -- the admin's, so a member's page was refused
  four times a load. The tenant middleware records who views the page from
  the principal it already read (`auth._PAGE_VIEWER`), and
  `auth.install_settings_here` asks the role rule for the install's settings
  (no I/O); the page carries it (`<meta name="kazma-install-settings">`,
  `window.kazmaInstallSettings` in `auth-guard.js`), and every shell read of
  them asks first (`components.js`, `stores.js`, `voice.js`). A member's
  theme stays in their browser; the sidebar's default-model picker (it sets
  the install's default) is drawn only where the page may. This space's
  Settings tabs of a feature the person lacks (email and calendar, X Studio)
  are neither drawn nor opened (`#settings-root`'s `data-tabs-off`,
  `_tabShown`): the X panel's `x-init` asked X's routes whatever the tab.
  A shell read of an install route goes behind `kazmaInstallSettings`. Gate:
  `tests/e2e/test_member_settings_asks_nothing_refused.py` (a member with a
  new person's features, by the sidebar, EN/AR, every tab shown and a theme
  switch: no request refused; today's shell and the tabs drawn whatever the
  features as controls).
- **The cloud profile (S4-08, 2026-11-23).** An install the operator
  declares public (`KAZMA_CLOUD_PROFILE`, one of `boot_guard.SECURITY_SWITCHES`,
  read through `env_flag`) runs the checks of
  `kazma_core/security/cloud_profile.py` at boot; with it off nothing there
  runs or is read. `CHECKS` is the one table, each check declaring what a
  missing piece does: **refuse** (Kazma does not start, and prints what is
  missing and the fix) for an `https` `KAZMA_PUBLIC_URL` on a public host
  (`ssrf.public_host_problem`, ssrf's own rules with no DNS: never a private
  address, `.local`, `.internal` or a name with no dot), a proxy named in
  `KAZMA_TRUSTED_PROXIES` (`*` is no proxy; a range of prefix length 0, or
  wider than the widest private block, /8 or /7, is refused: every peer in
  it could name its own client address), nobody in without signing in (no
  open switch: `boot_guard.OPEN_SWITCHES`, the ONE list -- sign-in off,
  demo, the socket bypass, loopback and LAN auto-login, the chat apps open
  to everyone, an open season takeover -- which S4-03 reads too; no hand-set
  `KAZMA_SECRET` under `WEAK_SECRET_CHARS`) and code only in containers
  (`security.execution.container_required`, which no Settings write turns
  off while the profile is on: `cloud_profile.container_requirement_refusal`,
  asked by `settings_validation`); **warn** (said at WARNING) for
  `KAZMA_PRODUCTION`, a loopback bind, a container engine installed and a
  backup copy off the machine. A check that cannot be made is a missing
  piece. Two stages: the environment's checks in `create_app` before the app
  is built (before it waits for its database), the settings' in the builder
  once the store answers (`boot_refusal(settings=...)`). A new piece is a
  row of `CHECKS` and a way to break it in the test's `BREAKS`. `kazma
  doctor` shows every check: on, a missing refuse piece fails it; off, each
  is shown (`[info]`) and counts for nothing. Gate:
  `tests/test_cloud_profile.py` (each piece missing on its own, through the
  real `create_app` for both stages; the profile off and every piece in
  place as controls; a check with no way to break it fails the gate; the
  shipped proxy, sign-in and TLS rules as controls for the review's cases,
  and the Settings save refusing the container switch).
