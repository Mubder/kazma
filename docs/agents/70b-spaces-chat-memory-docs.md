<!-- AGENTS.md, piece 70b-spaces-chat-memory-docs. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Chat state follows the space (S1-08, 2026-11-18).** A guard refuses
  only where Spaces is enforced; a stamp (a row's space, a message's author)
  is always written. Never refuse, or move a stored value, on an install
  that does not enforce Spaces.
  - **The thread's record decides for everything kept by thread**
    (`spaces/threads.py`): `thread_home` is the record's space, and the
    owner's for a thread from before Spaces (N19); `thread_refusal` and
    `require_thread` refuse work bound to another space. Work that bound
    nothing (a sweep, the start pass) acts for the thread and is not
    refused. A new store or reader keyed by thread asks them.
  - **Checkpoints** (`kazma_gateway/stores/checkpoint.py`, `_open`): once
    enforced the saver is the THREAD's, whoever asks: a read of another
    space's thread is "no checkpoints", a write raises
    `ThreadInAnotherSpace`. On Postgres every space then uses the one
    table, and a listing or a clear goes thread by thread
    (`_filters_by_thread`). Not enforced: the bound tenant's saver, as
    before.
    On one shared table a listing is held to the space's threads by its own
    statement, before its limit, and a listing or a clear asks the record
    store once (`threads.space_threads`: the owner's space is every thread
    no other space recorded, N19).
  - **Time Travel** steps are kept by id alone. Restore and fork ask
    `thread_rewind._in_this_space`, and a fork's thread is recorded
    (`threads.record_new_thread`) before anything is written under it. A
    record store that fails is the refusal `thread_record_unavailable`
    (503, a WARNING), and a fork bound to no space once enforced is
    `no_space`; each refusal code is worded on the chat apps and the Time
    Travel page. A chat delete of an id the space's store does not hold
    deletes nothing more (`_not_this_spaces_chat` in `sse_chat`). A deleted
    chat's record is removed last, and only when nothing of the chat is
    left: not when its checkpoints, its steps or (the Dashboard's delete)
    its chat could not be deleted, or no checkpoint store was there to ask
    (`threads.forget_thread`).
  - **A step's space** is `scope.space_of_state(state)`: the state's own
    tenant while Spaces is not enforced (a receipt's identity is hashed
    from it, so it must not move), the bound scope's once it is. Drafts,
    the scratchpad and receipts use it; a plan (`task_ledger.py`) is read
    and made only through a thread of its own space. Every read of a
    thread's record remembers its space (`threads.record_of`), so a guard
    a turn's own code asks on the loop reads no store.
  - **Sockets** name their space like any request:
    `space_binding.space_for_socket` checks membership at the handshake of
    `/ws/chat/{session_id}` and `/ws/voice` (close 4003 with the refusal's
    code) and `space_binding.bound_to` binds it. A live-voice room is its
    space's (`routes_voice._livekit_room`: the owner's keeps its name), and
    so is an A2A task or conversation (`a2a_api.py`).
  - **Messages name their author.** `reply_sink.authored` stamps `author`
    (the bound account) on a user's row; history and the `user_message`
    frame add `author_name` for another member only where Spaces is
    enforced (`space_binding.other_authors`, off the loop), and the page
    shows it (`_otherAuthorName` in chat.js; the page's own account is
    `<meta name="kazma-account">`).
  - Gates: `tests/_chat_space_cases.py` (the matrix cases: the product's
    routers behind the real tenant middleware), `tests/test_chat_state_spaces.py`
    (each guard taken out must fail its case on its own assertion, never a
    stand-in's error; the checkpoint store on one shared table, "the shared
    table reads across" as the control, and on real Postgres; Time Travel
    through the product's own `CheckpointManager`; drafts, notes and
    receipts against the tenant an older checkpoint left), `tests/js/test_message_author.js`,
    `tests/e2e/test_two_tabs_two_spaces.py` (two tabs in two spaces, by the
    sidebar, EN and AR).
- **Memory: the space is the tenant, always (S1-09, 2026-11-18).** As
  S1-08: a guard acts only where Spaces is enforced, a stamp is always
  written.
  - **One rule for who sees every tenant** (`memory/tenant_view.py`:
    `sees_every_tenant`, `narrowed_to`, `ledger_reach`): Kazma's own work
    (no tenant named) always; the owner's tenant `default` only while Spaces
    is not enforced; any other tenant never. Once enforced, `default` is the
    owner's personal space: its own rows, its own forget ledger, its own
    About me. Memory code never compares a tenant with the literal `default`
    (the class gate in `tests/test_memory_default_is_a_space.py`; a
    comparison that means something else is declared there).
  - **The mode setting is retired.** Only `shared` can be saved. A stored
    `per_platform` or `per_user` is honoured while Spaces is not enforced
    (`config.legacy_tenant_mode`) and ended from Settings ("Use one
    memory"); enforced, `resolve_tenant_id` answers the bound space.
  - **A turn's memory tenant is `scope.space_of_state(state)`** in the graph
    nodes, Time Travel and the rewinds; never `state.get("tenant_id") or
    "default"`.
  - **An episode's id carries its space** (the owner's decision 17: `id`
    stays the key, no table is rebuilt). `dual_write.episode_ids` and
    `swarm_bridge.bridge_episode_ids` give today's id and the other form:
    plain for the owner's tenant, salted with the tenant for every other.
    The writer picks the id inside its transaction (`_id_in_tenant`): the
    one the turn is already stored under, else one no other tenant holds.
    Code that finds a turn by id asks for both forms together with the
    row's tenant (recovery, the history import).
  - **A writer names its tenant or asks the scope**: the tool-outcome
    recorder reads `require_space_id()` on the caller's thread, the swarm
    bridge and `import_history` when none is passed; with Spaces enforced
    and nothing bound, nothing is written for the owner.
  - **The worker**: `worker_bootstrap.memory_tenants()` is every tenant that
    holds memory plus every space, the owner's first, and raises when it
    cannot be read; the cadences that visit every tenant list them through
    `worker_bootstrap._queue_per_tenant` alone (a failed listing is a
    WARNING, queues nothing and writes no stamp). A queued task's binding is
    `task_queue._task_binding`: not enforced, the payload's tenant when it is
    not the owner's, as before; enforced, the task runs whole -- its space
    (the payload's tenant, or its row's for `ROW_NAMED_TASKS`) and the
    account it acts for -- or as the install's work (`INSTALL_TASKS`). A new
    task type goes in exactly one of `TENANT_TASKS`, `ROW_NAMED_TASKS`,
    `INSTALL_TASKS`. A memory sweep names the tenant of each row it touches
    and asks for no scope. The deep pass is queued for the id the writer
    returned, never a recomputed one. **A turn's tasks run as the person the
    turn was for** (the S2-05/S2-07 second review: they ran as the space's
    maker): `post_turn_memory` and `micro_consolidation` (`_PERSON_TASKS`)
    bind `identity.work_scope(space, person)` -- the payload's `asker` (the
    deep pass carries it), else `said_by`, else the episode row's
    `metadata.said_by` (`task_queue._person_of`) -- so the person's models
    and limit apply. The deep pass (`_PERSON_MODEL_TASKS`) waits, put back
    with no attempt spent (`_MemoryWorker._put_back`), while they are
    disabled or past their daily limit, and is not run for a person who
    left the space (their message is still written, as Kazma's own work).
    Gate: `tests/test_memory_tasks_bind_their_person.py` (the maker's
    binding as the control).
  - **No row under the "no tenant" sentinel** (`schema_v2.NO_TENANT`,
    triggers on every table with a tenant column, both files). The operator
    trail records the space the request acted in (`request.scope["kazma_space"]`,
    left by the tenant middleware).
  - **Recall** holds its episode candidates to the asker's tenant
    (`recall._own_episodes`) before anything reads one by id; a database
    error there rises, and recall says it failed.
  - **A statement that picks a memory row by id alone** names its tenant in
    the same WHERE, `(? IS NULL OR tenant_id = ?)` with
    `tenant_view.narrowed_to` (nothing filtered on an install that does not
    enforce Spaces), or is declared (S1-35): `tests/test_memory_id_only_statements.py`
    is a gate, its `DECLARED` table naming each function, how many such
    statements it holds and why another tenant's row cannot be reached -- a
    row the same work chose by tenant, recall's candidates held by
    `recall._own_episodes`, a queued task that names its own row, Kazma's own
    sweeps, the mirror. A new statement fails, in a declared function too.
    The ones that take an id from a request or the model -- the Memory page's
    edit, undo, invalidate, detail and recall trail, Forget of a memory, a
    fact's source and a weekly summary -- name it themselves: the route's
    check before them was the only thing keeping another space's row out
    (`tests/test_memory_id_scoped_statements.py`, each with the check taken
    away and the statement without its tenant as the control).
  - **Memory outside every space** (a tenant of the retired modes) is
    counted in memory health (`tenant_view.tenants_outside_spaces`), never
    merged. Memory's copies outside the data folder declare a space rule
    (`memory_stores.EXTERNAL_SPACE_RULES`).
  - Gates: `tests/_memory_space_cases.py` (the matrix cases),
    `tests/test_memory_default_is_a_space.py`, `tests/test_memory_spaces.py`
    (each rule with the old behaviour as its control).
- **Lessons (the Soul) are per space (S1-10, 2026-11-18).**
  - `self_improvement.soul_space(space)` is the one answer to "whose Soul":
    the owner's, always, on an install that does not enforce Spaces (one
    Soul, whatever tenant a turn runs for); enforced, the space named or
    bound, and `NoSpaceBound` when there is none (nothing is read or learned
    for no one). The owner's space keeps `self_improvement.agent_evolution`;
    every other space's Soul is inside `self_improvement.space_evolution`
    (`{"spaces": {id: {"agents": ...}}}`). Every change of a Soul is one
    `atomic_update` with the read inside the updater
    (`self_improvement._update_soul`), under `_agent_evo_lock` for this
    process: read before it, another process's lesson written meanwhile was
    lost. Every reader and writer of a Soul takes `space`.
  - **Learning**: `learn_from_finished_turn` passes the turn's tenant
    (`_post_turn_memory.tenant_id`, which `respond_node` sets from
    `space_of_state`) down to `apply_agent_mutation(space=)`. Once Spaces is
    enforced the background task binds a whole scope: the turn's space and
    the person the turn ran for, else `spaces.identity.acting_account` --
    the one answer to "who does Kazma's own work for a space act for" (the
    queued memory tasks use it too), so the refiner's own memory search
    reads that space. Not enforced: the context `close_turn` handed it, as
    before.
  - **Reading**: on every install the supervisor adds a turn's lessons at
    its first step, one current fenced copy (§11): the one Soul, or once
    enforced the lessons of `space_of_state(state)`.
  - **Forgetting**: `forget_text` and `forget_lessons_from` take the
    tenant; with none (Kazma's own replay) they read every Soul.
  - **A held lesson** (`mint_soul_commitment(space=)`) is a commitment of
    its space; the confirm queue's routes act only on a `soul_delta`
    commitment of the request's space (`commitment_api._lesson_here`) that
    is still held: confirm and reject move it only from `needs_confirm`, in
    the same UPDATE (`update_status(..., expected=)`); a decided one answers
    409 `not_held` (an applied lesson was "rejected" while it stayed in the
    Soul, and a second confirm applied it twice).
  - Gates: `tests/test_lessons_per_space.py` (the real hand-off and the
    real supervisor graph; the single Soul as each control),
    `tests/_lesson_space_cases.py`.
- **Documents follow the space (S1-12, 2026-11-18; §19 B4b and B4c).** The
  asker in its space on every surface, the refusal of chat and the agent
  lifted where Spaces is enforced, each space's files in its own folder once
  it is (the root keeps every tenant's copy in this release, for a revert).
- **A document's work runs in the document's space (S4-01 step 0a,
  2026-11-25).** The worker binds each job's scope before its first stage
  (`DocumentWorker._process_claimed`: heartbeat, stages and outcome; an
  identity store that cannot be read hands the job back on the attempt it
  had, and so does a space being erased or brought in; a tenant that is no
  space of the install fails it, `no_space`). The recoveries at
  start (interrupted intakes, cut-off moves, stranded intakes) and the
  Knowledge reconcile enter each row's own space: one row's failure is said
  (`_Pass`, once as a WARNING) and the rest go on, an outage is raised again as
  before; three rows in a row that fail as a whole store does end the pass.
  Interrupted intakes whose people could not be read, or whose space comes or
  goes, are tried again by the intake sweep, never a job under a lease. What an
  import is writing is read before the identity store (`importing_spaces`), or
  its job is rejected as no space's. The loops start with
  no space (`install_loops`): a document tool starts them inside a turn, and
  they kept that person's space for good. The queue's fair claim, its leases
  and the intake queue are the install's. Off, nothing is bound and a failure
  is raised as before. A move's copy, and its taking back, run inside the
  target as the same person; what goes with a member who leaves runs inside
  the space they left (`people._after_leaving`); the admin's counts read each
  space as its own; the garbage collection and the totals are the install's;
  the runs list is the install's only with nothing bound (a page's stays its
  space's). An audit row that could not be written is a WARNING. Gates:
  `tests/test_documents_run_in_their_space.py`,
  `tests/test_space_rows_from_a_page.py`,
  `tests/test_space_doors_postgres.py`, and in `tests/test_row_work_gates.py`
  every loop over a cross-space listing (`LISTINGS`) enters one per row.
- **The Knowledge Library follows the space (S1-13, 2026-11-18).** Guards
  act only where Spaces is enforced; the older multi-user mode keeps its
  refusal (`knowledge_lifecycle.kb_refusal`), and work bound to no space is
  refused once enforced (`NO_SPACE_REFUSAL`).
  - **A library's id is its space's**: `knowledge._library_id_in_space` -- the
    owner's ids never change, every other space's are `slug.<8 hex>` (a slug
    never holds "."), and the Chroma collection name keeps the "." apart.
    `KnowledgeStore.own_library_id` resolves an id for a lookup and
    `library_id_for` for a new library; **every route, tool and `/kb`
    resolves once and uses the answer** (a check of `get_library(x)` then a
    read of `x` reads another space's library). A new entry point that takes
    a library id does the same. **Every name built from an id** (the Chroma
    collection, the fetched-pages folder, the soak's copy) comes from
    `knowledge.library_name_part`, which keeps the mark: as "_" it made a
    space's library and an owner's of the same text one folder.
  - Reads keep to the space (the store's tenant filter is on whenever Spaces
    is enforced); `publish_page` writes only into a library of the bound
    space (`_writing_space`). Maintenance lists every library through
    `all_library_ids` / `library_space`, never the filtered reads.
  - The crawl journal is the install's: once Spaces is enforced the routes
    show a space the crawls of its own libraries (`kb_api._own_libraries`,
    keyed on `spaces_enforced()`, never the store's tenant filter, which
    production mode turns on too); a crawl a restart cut off resumes in its
    library's space (`kb_api._in_the_crawls_space`; one whose space cannot
    be read waits for Resume, the others go on); the canary runs as the
    owner's space on its private store.
  - A quota per space (`_SPACE_QUOTA_BYTES`, `KnowledgeSpaceFull`) beside the
    install's file quota, an admission check like it. `space_used_bytes`
    reads on a connection of its own, never under the store lock, and a
    measure answers for `_SPACE_BYTES_TTL_S`.
  - Gates: `tests/_knowledge_space_cases.py` (the matrix cases),
    `tests/test_knowledge_spaces.py` (each guard taken out as a control).
- **Scheduled follows the space (S1-14, 2026-11-18).** Guards act only where
  Spaces is enforced; the older multi-user mode keeps its refusal
  (`cron.single_operator.scheduled_refusal`), and work bound to no space is
  refused once enforced (`NO_SPACE_REFUSAL`).
  - A job carries its space (`tenant_id`) and the account that booked it
    (`ScheduledJob.creator`, always stamped). Every Scheduled read and change
    keeps to the caller's space (`scheduler._tenant_scope`); another space's
    job id answers as an unknown one.
  - A fire runs as a whole scope (`scheduler._fire_binding`): the job's space
    and its creator, else `identity.acting_account`. A job whose tenant is no
    space of the install does not fire (`no_space`).
  - **Only the owner's own jobs use the owner's address.** Until people link
    their own chat apps (S3-05) every address a job can deliver to is the
    owner's, so a booking by anyone else is refused
    (`scheduler.NO_OWN_ADDRESS`, code `no_own_address`) and a job that would
    act for anyone else does not fire. S3-05 lifts it; never lift it by
    sending a member's job to the owner's chat.
  - A job takes a sibling's address only within its tenant
    (`sibling_delivery_target(thread_id, tenant_id)`).
  - **What a job says and when it runs again are its person's** (S1-14
    review): only the account the job fires as may give it a new prompt,
    Run it again, make it recurring, or change its time while it fires
    (`CronScheduler._not_theirs_to_change`: `not_yours`, 403). Anyone else in
    the space may only move a pending one-off to another one-off time. In a
    shared space a member's words, or a daily recurrence, would otherwise
    reach the owner's chat as the owner. Such a change is made only to the
    job it was allowed for (S1-16 follow-up, `CronScheduler._change_rule`):
    the write pins the version read and `update_job(pending_only=True)`
    answers `conflict` when a fire began meanwhile -- a claim keeps the
    version.
  - **The fire's binding is inside its `try`**: an identity store that
    cannot answer because the database is away gives the claim back at the
    same occurrence (`_hold_for_identity`, reported to the database watch),
    and while the database is away the poll holds reminders too once Spaces
    is enforced (who a reminder fires as is in the database). Any other
    failure to read it (a registry that is not a mapping) fails the fire
    once (`identity_unavailable`), never a claim and release every pass; a
    locked SQLite settings file is held the same way, without being an
    outage (`_sqlite_lock_passes`, S1-16 follow-up). The
    missed path asks the same fire rule: a job that would not fire is
    recorded missed and queues no notice (`mark_missed(refused=)`).
  - Gates: `tests/_scheduled_space_cases.py` (the matrix cases, a member of
    a shared space among them), `tests/test_cron_spaces.py` (each guard
    taken out as a control).
