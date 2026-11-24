<!-- AGENTS.md, piece 70e-spaces-data-speakers. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
- **Count, export and erase one space (S1-27, 2026-11-20).** Only once
  Spaces is enforced (409 `spaces_off`); never the owner's personal space
  (`owners_space`); not yet on Postgres or with memory's Postgres copy
  (`postgres_not_yet`).
  - `kazma_core/spaces/space_data.py` walks every table of every `row` store
    (`store_registry.SPACE_RULES`) and finds the space's rows by its rule:
    `TABLE_RULES`, else a space column (`tenant_id`, `tenant`, `space`,
    `space_id`), else an FTS index its triggers keep, else its thread. A
    table with none refuses the count, the export and the erase (500
    `unknown_table`): a new table, or one the app makes at start, gets a
    rule in the same change (`keep` says why it holds no space's words),
    and so does a table a feature makes only when it first runs: the
    approval bus's `hitl_watchers` had none, and every count, export and
    erase refused on live's `hitl_gates.db`; the test world makes each such
    table by its own code (`tests/_space_world.py`).
    Settings, secrets (`SecretVault.erase_space`, whatever scope is bound)
    and libraries (`knowledge_lifecycle.delete_library`) go through their
    own stores; the space's folders after; then it leaves the identity
    store, recorded (`space_erased`).
  - An erase first closes the space (`people.close_space`: `erasing_at`, and
    `identity.is_member` is False for everyone); one that stopped half way is
    run again. Each store file's rows go in one transaction. A table whose
    trigger refuses every delete (the SQLite document job queue's
    `document_job_events`, in `documents.db`) has that guard lifted inside
    the erase's transaction and put back before it commits
    (`space_data._lift_delete_guards`): the erase failed "document job events
    are immutable" on such an install (CI, 2026-11-20). The test world makes
    the queue's tables by their own code
    (`test_an_append_only_table_is_erased_and_keeps_its_guard`, the erase
    without the lift as the control).
  - Who may (`kazma_ui/spaces_api.py`, from the memberships): the admin or a
    member counts (numbers only); the space's owner exports; an owner deletes
    a shared space; the admin erases the closed personal space of a deleted
    person and never exports a member's. The export is sealed with a
    password the person chooses (decision N10, `security/sealed_zip.py`:
    WinZip AES-256, at least 12 characters).
  - Pages: Settings -> This space -> This space's data; People lists the
    closed spaces with Erase.
  - Gates: `tests/test_space_export_erase.py` (a word planted in every table
    of A and of B; A's export holds A's only; erasing A leaves no column or
    file holding A's word, B whole; yesterday's erase that misses a store, a
    table with no rule and a Postgres copy of memory as controls),
    `tests/test_sealed_zip.py` (7-Zip and bsdtar open it with the password
    only, ZIP64 too; a big-endian counter as the control),
    `tests/e2e/test_people_page_browser.py` (EN and AR, by the sidebar).
- **Backups carry the identity store; a restore keeps spaces apart (S1-28,
  2026-11-20).** The universal backup, the Postgres dump and a migration
  bundle carry the identity store (a settings row) and the thread records
  (`thread_scopes.db`) with the stores: a restore keeps every chat in its
  space. Guards act only where Spaces is enforced.
  - The Settings file is the admin's (`settings_restore._held_by_another_space`):
    it holds no other space's `tenant.<id>.` keys and no other space's Soul,
    and a restore of an older file writes none (`kept.other_spaces`).
  - With a second account, no plain copy of the backup leaves the machine
    (decision N10, `universal._plain_copy_refused`): restic's encrypted copy
    only.
  - Gate: `tests/test_backup_restore_spaces.py` (a real universal backup and
    a real migration of two planted spaces, judged by a child pinned to the
    restored folder; a restore without the identity store, the Settings
    file of before and the plain-copy check of before as controls).
- **The docs say what Spaces promises and what it does not (S1-30,
  2026-11-20):** the threat model's first section and section 6, the safety
  design's section 10, and `docs/docs/guide/spaces.md`. Each says Spaces is
  not switched on in any release yet, keeps nothing from the person who runs
  the machine, and has not been reviewed independently;
  `tests/test_threat_model_accuracy.py` holds them to the code. **The
  identity store and the Spaces switch are written only by their own code,
  in every mode** (`settings_scope.install_writer_refusal`): before Spaces is
  enforced Settings' generic save wrote the switch (`identity.ENFORCED_KEY`), turning Spaces
  on before S1-33's checks. Gate: `tests/test_settings_scope.py` (the rule
  of before as the control).
- **The environment note is the space's own (S1-31, 2026-11-20).** In a
  sandboxed space `ide/env_context._sandboxed_space_note` names the space's
  folder by relative paths only, the Python policy and the tools the space
  has; never the install's workspace, data folder, repository, branch, MCP
  root, host shell or GitHub sign-in (a task naming a workspace, or a space
  whose kind cannot be read, is told nothing of the machine). The admin's
  space and an install without Spaces keep the note they always had. Gate:
  `tests/test_environment_note_per_space.py` (a real turn through the
  supervisor graph; a sandboxed space read as a host one as the control).
- **The bell, open problems and Running work are per person (S1-32,
  2026-11-20).** The acquisition monitor records each research and crawl
  notice's space (`web_acquire/monitor._crawl_space`; unreadable: shown to
  nobody, sent nowhere) and lists it only in its space
  (`monitor._shown_here`); another space's notice stays off the install's
  channels (O1). The install's own notices -- service alerts, requirements,
  advisories, the outside watcher -- are the admin's
  (`runs.spaces.install_notices_here`). Gate:
  `tests/test_bell_and_problems_per_person.py` (words planted in one
  person's topic and chat title, read through the real routes as everyone
  else; the lists of before as the control).
- **Each host space has its own root (S1-34, 2026-11-20).** Workspace rows
  carry their space (`space_id`; existing rows the owner's) and each space
  has its own active one; a switch or clone moves only its own space's, and
  only the owner's space moves the process pin and the MCP servers
  (`notify_root_changed`). `resolve_active_root` answers the bound space's
  active workspace; a host space other than the owner's with none works in
  its own folder (`spaces.files.host_space_root`), its file tools stay in
  its workspace (`path_policy._space_home`). The IDE service, code index,
  IDE undo and the MCP guard follow the root. Gate:
  `tests/test_host_space_roots.py` (one active row for the install as the
  control).
- **Who is "I" in a shared space (S1-11, 2026-11-20; the R3 study,
  N30 to N36).** `kazma_core/memory/speakers.py` is the one module that
  knows about members; everything else asks it.
  - **Which spaces file by member** (`speakers.room`): a shared space (no
    `personal_of`) once Spaces is enforced, from its first turn. A personal
    space, and every space while Spaces is not enforced, keeps the one
    `user` entry, and its rows are stored exactly as before (no `said_by`,
    subject `user`). An unreadable identity store raises: nothing is filed
    under a guessed member.
  - **Where a fact goes** (`Room.filed`): ``user`` (what the passes write
    for "I", as a subject or an object) is the speaker's entry
    `member_<account>`; a member's name, or one they are called by
    (`identity.set_called_names`), is theirs. A model never writes an entry
    (`belief_extractor._sanitize_belief`, the memory tools). With no speaker
    a first-person fact is not filed; the turn is still remembered.
  - **The speaker travels with the turn**: `respond_node`'s record
    (`said_by`), the post-turn thread and queue, the episode
    (`metadata.said_by`), the quick and deep passes, turn reconcile (the
    chat store's `author`), history imports, the memory tools. A new writer
    from a turn passes `said_by` (or the episode's `metadata`):
    `test_every_writer_from_a_turn_passes_its_speaker`.
  - **Whose words** (`Room.label`): another member's words about a member
    are `member_stated`, which never replaces that member's own (N34).
  - **One vector text** (`speakers.fact_vector_text`): a member's facts
    embed as `user <predicate> <object>` (the study's 4.3). No fact vector is
    built by hand (`test_one_text_for_a_facts_vector`).
  - **Recall reads a shared space for its asker** (`recall(asker=)`): their
    own facts and turns as `user` / `User:`, another member's under their
    name, a note outside the fence. Whose facts a question is about is
    chosen before ranking (`recall._whose_facts`: named first, then self
    first; `speakers.refers_to_asker` reads the first person, from the
    grammar: a three-letter imperfect, an object "me" on another person's
    verb and MSA "لديّ" are named apart); chosen after, another member's
    best match pushed the asker's own out by the gap.
  - **The speakers benchmark** (`benchmark_data/speakers.json`,
    `run_speaker_benchmark`) has its own ratchet (`thresholds.json`
    `speakers`, `scripts/memory_bench.py lock`); the study's vectors are
    replayed (`speakers_vectors.npz`).
  - **About me** is each member's own in a shared space
    (`memory_member_profile`, N31), with "Copy from my personal space".
  - **Forget** (N32, N33, N36): a shared turn is its author's and the space
    owner's to forget (`speakers.may_forget_turn`), a shared chat its
    opener's and the owner's to keep out; a fact about oneself another
    member stated goes, never filed from that message again
    (`metadata.not_again`), and Forget says the message stays
    (`forget.notes_for_forgotten_facts`); `forget.forget_entry` forgets every
    fact under an entry, journaled (kind `entry`).
  - **The notice** "Memory here is shared with ..." near the composer and
    on the Memory page (`static/js/shared_memory_notice.js`, N35).
  - Gates: `tests/test_memory_speakers.py` (each rule with its control),
    `tests/e2e/test_shared_memory_notice.py` (by the sidebar, EN/AR).
- **Each person's answers, settings and copies stay theirs (2026-11-21).**
  Guards act only where Spaces is enforced, except where a line says always.
  - **Web Push** (`kazma_ui/push.py`): every subscription records the
    account that subscribed it (`_account`, always stamped, from the
    request's sign-in). Once enforced a finished turn goes only to its
    thread's opener's devices (`_recipient`: the thread's record, the
    owner's for a thread from before Spaces), while they are still a member
    of the thread's space; a turn with no thread, or a device with no
    account, reaches nobody. `TurnBroker._push_terminal` hands its thread
    over. Gate: `tests/test_push_per_person.py` (every device, whatever the
    thread, as the control; nothing else reads the stored devices).
  - **An echo of every setting** (`settings_scope.echo_view`,
    `shown_in_echo`): Settings' `GET /api/settings`, `/config show` and
    `/config export` (`slash_commands._load_config`) and `config_read` show
    settings only -- never Kazma's own records (`settings_restore.classify`)
    -- and a space's key only in that space (`tenant.<id>.` is `<id>`'s, an
    unprefixed space key the owner's), a person's only to them; with no
    space bound, the install's alone. `config_read` answers `not_here`.
    Gate: `tests/test_settings_echo_per_space.py` (every product
    `.get_all()` is an echo through `echo_view` or declared; the echo of
    before as the control).
  - **One answer to "is a plain copy configured"**
    (`universal.plain_copy_configured`): the universal backup's copy while
    it is on, or a native provider set at all -- the Postgres WAL archive's
    segments and weekly base backups go to it whatever
    `backups.offsite.enabled` says. Every path that sends a plain copy asks
    `universal._plain_copy_refused` before anything leaves
    (`wal_archive._push_offsite` too). Gate: `tests/test_plain_copies_refused.py`
    (every upload to a provider and every rclone copy in product code asks;
    the push without the refusal and the rule of before as controls).
  - **A chat app works in the owner's space** (`spaces/chat_apps.py`):
    until each person links their own chat app (stage 3), the gateway's
    handler binds the owner's scope before it reads or builds anything of
    the chat (`chat_app_scope`, `_handler_body_in_its_space`), and a chat
    recorded in another space is refused with a reply. A chat-app command
    reading the owner's accounts (`/x`) runs in the bound scope
    (`chat_app_work`; before Spaces the owner's tenant, as it was). Gate:
    `tests/test_chat_apps_once_enforced.py` (the real handler on four
    platforms; no scope bound as the control; every tenant or scope the
    gateway binds comes from this rule or is declared).
  - **Research is each space's** (`tools/research_pipeline.py`): the papers
    index is per space (`papers_index_key`: the owner's keeps
    `research_pipeline.PAPERS_INDEX_KEY`, every other space's is under `tenant.<id>.`,
    so its count, export and erase reach it); the machine's other report
    roots (`KAZMA_WORKSPACE`, the default sandbox, the process folder) are
    the owner's space's only (`_owners_space_here`), every other space reads
    under its own root; a research job freezes its space in its policy
    (its `space`, read first by `runs.adapters.research._CrawlSpaces`,
    which the acquisition monitor uses too; a resume in another space is
    refused). Gate: `tests/test_research_papers_per_space.py` (one index for
    the install, the roots of before, the folder guess as controls).
  - **Only the owner's space runs code on the machine itself**
    (`code_exec.local_exec_forbidden`): every other space, and work bound
    to no space, a container or nothing, whatever the lab switch says.
  - **A URL password in a space's setting is that space's**
    (`config_store._secret_vault_name`: a `tenant.<id>.` key's goes under
    `cfg:tenant.<id>.`); `cfg-url:tenant.<id>.` is read as the same space's
    (`vault.secret_scope`, `_install_scope_rule`), and
    `vault.space_name_prefixes` is the one list of names that carry a space
    (erase, and the count and export's `space_data._vault_rows`). Gate:
    `tests/test_space_url_secrets.py`.
  - **Who may decide on a run incident is asked before anything is claimed**
    (`runs/decide._not_theirs`, every action, a dismissal and a diagnosis
    too); the run's own control is asked again by `registry.perform`.
    Gate: `tests/test_gates_and_runs_spaces.py`.
  - **A space's swarm approval rows go with it**: `space_data._settings_keys`
    reads a settings row that records its space
    (`_SPACE_RECORDING_PREFIXES`: `swarm.approval.`) beside `tenant.<id>.`
    keys and its chats' state. Gate:
    `tests/test_space_erase_takes_its_approvals.py`.
  - **A new web chat's thread id is one rule** (`threads.new_web_thread_id`,
    the chat's stream and live voice): the session id in the owner's space,
    a fresh id in any other. Gate: `tests/test_web_thread_ids.py` (no web
    code gives a thread its session id itself, but two declared places).
