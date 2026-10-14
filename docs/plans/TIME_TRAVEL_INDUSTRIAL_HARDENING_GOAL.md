# Time Travel industrial hardening — continuous execution

Status: OPEN -- 2026-10-15. Remediation merged to `main` (AGENTS §12, §18 B and §30 B2 updated with it); the long soak's harness is built (`scripts/time_travel_soak.py`); open: its run of hours or days (TT-G3, D3) and an independent review.

Objective: execute the complete Time Travel audit remediation and
qualification plan, with current evidence for each requirement. Industrial
readiness is not reached by fixing only the witnessed defects or by a green
unit suite: the existing Time Travel suite was green with every defect
present. Restore must become a coordinated, audited operation that cannot
run a call the human did not see, and every restore, fork and approve test
must run on a real LangGraph graph and saver.

Baseline: commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the
uncommitted working tree of 2026-10-13. The auditor found no uncommitted
change in the Time Travel files; of the eleven cited files the merge checked,
only `kazma-ui/kazma_ui/settings.py` was modified (+2 lines of document OCR
settings, unrelated to TT-08).

- Audit: `session-artifacts/audits-2026-10-13/time-travel.md`; final form
  `docs/audits/AUDIT_TIME_TRAVEL_2026-10-13.md`.
  Auditor's scores: 4/10 capability and architecture, about 2-3/10
  demonstrated industrial readiness. 12 findings: P1 2, P2 5, P3 5.
- Witnesses: `session-artifacts/audits-2026-10-13/time_travel_probes_2026_10_13.py`,
  **17 passed in 0.53 s** (`time-travel-probes.log`). Each witness asserts
  that a defect is present. Witnesses must never become permanent tests that
  require the defects. Each is inverted into a regression that asserts the
  fixed behaviour, with the original defect as its negative control. Six are
  wholly or partly source-text checks (TT-03 route body, TT-04 route text,
  TT-05 delete/reset/forget/scan, TT-07 gateway text, TT-08 settings text,
  TT-10); their replacements measure behaviour (§28). The TT-02 witness
  mirrors the approve route's branches; its replacement drives the real
  route.
- Existing suite: **77 passed in 19.78 s** across 11 files
  (`time-travel-regression.log`): `tests/test_time_travel.py`,
  `test_snapshot_rewind_payload.py`, `test_time_travel_concurrency.py`,
  `test_replay_router_getters.py`, `test_thread_ownership_routes.py`,
  `test_web_time_travel_command.py`, `test_replay_thread_titles.py`,
  `test_snapshot_retention_actually_prunes.py`, `test_replay_command.py`,
  `test_path_rewrite.py`, `test_thread_route_policy.py`. These are incident
  locks: re-point an assertion a fix changes, never delete it.

## Requirements and completion evidence

Rows are ordered by stage. S0 is the test foundation the Stage 1 exit gate
depends on (the report does not place TT-12 in a stage). G rows are the
report's stage exit gates; Q1 comes from the report's "not performed" list.

| ID | Required result | Evidence required | State |
|---|---|---|---|
| TT-12 | **S0.** One shared real-graph fixture — real `SupervisorState` (scratchpad reducer included), product node names and routing, a stub tool worker that calls `interrupt()`, and each supported saver (the product SQLite saver and the Postgres saver) — drives every restore, fork and approve test across more than one turn. The fake `aupdate_state` recorders (`test_snapshot_rewind_payload.py:32-38`, `test_replay_router_getters.py:37-44`) no longer stand in for restore semantics; the global-cap test asserts on SQLite, not only the memory dict (`test_audit_2026_09_04_wave5.py:123-133`); `test_path_rewrite.py` has JSON-encoded input | List of fixture-backed tests with their negative controls; SQLite runs locally; Postgres variants marked `@pytest.mark.postgres` and green in the Postgres CI job; all 17 witnesses inverted, each with the defect as control | Implemented 2026-10-14. Fixture `tests/_tt_graph.py` (real `SupervisorState`, product node names and routing, an `interrupt()` tool worker, the product capture hook, the product SQLite saver or the Postgres saver). Fixture-backed: `tests/test_time_travel_rewind.py`, `tests/test_time_travel_identity.py`, `tests/test_time_travel_surfaces.py`, `tests/test_replay_router_getters.py` and the replay part of `tests/test_thread_ownership_routes.py` (re-pointed off the fakes). `test_snapshot_rewind_payload.py` now asserts the call sites call the service. The global cap is asserted in SQLite (`test_audit_2026_09_04_wave5.py`). JSON-encoded input: `test_json_columns_are_rewritten_both_ways_and_still_parse` (`tests/test_time_travel_bounds.py`), not `test_path_rewrite.py`. Postgres variants (`@pytest.mark.postgres`, 8 tests) green against a throwaway postgres:16 container; the CI Postgres job has not run them yet. Witnesses: an API-adapted copy of all 17 fails 15 by behaviour (the defects are gone); the other 2 test a lower layer that keeps its behaviour on purpose (TT-03: `build_turn_messages` fed a stale transcript; TT-04: `apply_snapshot_to_thread` called directly), and the service-level outcome is proven instead (`test_a_restore_two_turns_back_is_what_the_next_message_sends`, `test_a_restore_during_a_running_turn_is_refused_not_lost`). Copies deleted. |
| TT-02 | **S1.** Restore refuses (409 `approval_pending`) or first settles every live gate on the thread through `record_gate_decision` (`superseded` or a dedicated `rewound` state) and emits the `hitl` journal frame. Restore writes a turn-boundary state only (`tool_calls_pending=[]`, nothing runnable). The approve route (web) and the gateway approval path resume only when the checkpoint holds a live interrupt whose id equals the decided gate: `next` without an interrupt answers 409 and never resumes (`routes_direct/misc.py:638-640`), the gate-less fallback (`misc.py:773-779`) cannot execute a call, and `build_resume_command` keys security resumes by interrupt id | Real-graph test: restore under a pending gate, then Approve and Deny on web and gateway; each refuses or decides only the gate shown. Approve-route test that calls the real route with `next` and no interrupt → 409. Negative control: today's route and restore execute the denied call (`executed == ['rm -rf build']`) | Implemented 2026-10-14 (decision D11: refuse). `thread_rewind.rewind_thread` answers 409 `approval_pending` naming the cards (registry pending rows or a live checkpoint interrupt); a claimed/resuming decision is 409 `turn_running`. A restore writes `turn_boundary_payload` as node `respond` (nothing pending, `next == ()`). The approve route resumes only a live HITL interrupt: `next` without one is 409, a card the registry knows as another gate is 409 (`gate_names_interrupt`), no gate-less fallback; the gateway's fallback became the stale-card answer. `build_resume_command(interrupt_id=)` keys every approval/deny resume (web, gateway `/hitl`, timeout, supersede, CLI). Tests: `test_a_restore_under_a_pending_approval_is_refused_and_runs_nothing` (SQLite+Postgres), control `test_the_old_restore_and_approve_ran_the_denied_call` (`executed == ['rm -rf build']`), `test_a_restore_writes_a_turn_boundary_and_a_late_approve_runs_nothing`, the real route `test_the_route_refuses_next_without_an_interrupt`, `test_the_route_decides_the_live_card_with_a_keyed_resume`, `test_a_card_the_registry_knows_as_another_gate_is_refused`, the chat apps' handler `test_a_chat_app_restore_under_a_pending_approval_is_refused_by_the_handler`; gate `test_every_approval_resume_is_keyed_by_its_interrupt` with control `test_an_unkeyed_resume_is_caught`. |
| TT-04 | **S1.** One per-thread checkpoint-mutation lock shared by turns, approve, restore, fork, `/undo`, `/edit` and `/compact` on every transport (the gateway's `cmd_lock`, `agent_handler/graph.py:930-935`, covers gateway paths only). Restore and fork answer 409 `turn_running` while a turn runs or is mid-segment (`active_turns.is_turn_running`) | Route test with a registered running turn → 409; real-graph race test (restore during a run is refused, not silently lost); a gate enumerating the checkpoint-mutating entry points (MEM-R10's list of seven state rewriters as the starting set) that fails on one without the lock, with its control. Negative control: today's route answers 200 | Implemented 2026-10-14. `kazma_ui/thread_locks.thread_mutation`: one task-reentrant lock per thread per loop (bounded), taken by gateway turns and commands (`cmd_lock`, `/undo`, `/edit`, `/replay`, `/fork`), the approve route, web abort, `/reset`, chat delete, dashboard delete, the clear-approvals route and the start of a web turn (which also runs nothing if a restore landed after it read the checkpoint: rewind generation). Restore and fork answer 409 `turn_running` while a turn is registered or another task holds the lock. Tests: `test_a_restore_during_a_running_turn_is_refused_not_lost` (SQLite+Postgres), control `test_the_old_restore_during_a_running_turn_was_silently_lost`, route `test_the_route_answers_409_while_a_turn_runs`; gate `test_every_checkpoint_rewriter_holds_the_threads_lock` (every product function calling `aupdate_state`/`adelete_thread`; three declared exceptions with reasons) with control `test_a_rewriter_without_the_lock_is_caught`. |
| TT-G1 | **S1 exit.** One `rewind_thread(thread, snapshot_id, actor)` service called by every surface: under the TT-04 lock it refuses while a turn runs, refuses or supersedes pending gates, writes turn-boundary state only, clears and replaces the scratchpad, emits a journal frame and writes an audit row. No Approve or Deny on any transport after a restore can run a call the human did not see; restore during a run or a pause is refused deterministically | The TT-02 and TT-04 tests on the real graph with SQLite and Postgres savers, with negative controls; a gate that no surface writes a restore except through the service (control: a direct `apply_snapshot_to_thread` call from a route) | Implemented 2026-10-14. `rewind_thread` / `fork_thread` are the only callers of `apply_snapshot_to_thread` (gate `test_only_the_rewind_service_writes_a_snapshot_onto_a_thread`, control `test_a_route_writing_a_restore_itself_is_caught`); every outcome, refusals and errors included, is counted (`kazma_time_travel_rewind_total`) and audited (`time_travel_audit.db`). TT-02 and TT-04 tests above on SQLite and Postgres savers. |
| TT-01 | **S2.** Snapshots addressed by a unique id carrying turn identity (snapshot uuid + turn identity such as `created_at` / user-turn index / reply `turn_id` + iteration). Restore, fork, detail and compare by snapshot id, never a bare iteration. Listing shows turns, then steps, each step once. Retention evicts oldest-first by capture order (today `ORDER BY iteration DESC`, `time_travel.py:284-286`). One recorder per process, the database as source of truth (or a write-through cache by id). Identical restore result on web, gateway and TUI, before and after a restart | Real multi-turn graph test: listing, restore-by-id across surfaces and a restart, eviction keeps the newest N. Negative control: the `(thread, iteration)` keying fails all three | Implemented 2026-10-14. Rows carry `id`, `turn_key` (`created_at`), `turn_index`, `seq` (capture order), `message_count`, `nbytes`, `truncated`; older files are backfilled at open. Restore, fork, detail and compare take the id on every surface (chat apps: 8+ characters, `resolve_ref`); listings group turns, then steps (`group_steps`); eviction by `seq`; the database is the only copy, one process recorder. Tests: `test_every_step_is_listed_once_and_restores_the_same_state_everywhere` (SQLite+Postgres), `test_retention_keeps_the_newest_steps_by_capture_order`, `test_an_older_snapshots_db_gains_its_identity`, `test_one_step_one_state_on_every_surface_across_a_restart`; control in `test_time_travel_surfaces.py` (`/replay 1` names no step). |
| TT-03 | **S2.** Restore rewinds the transcript (chat-store rows after the restored turn marked or truncated) and the scratchpad (`SCRATCHPAD_CLEAR` before applying), and emits a journal frame so open tabs repaint; the UI and docs state what is not rewound (memory, files, messages already sent). Or the feature is renamed "restore agent context" and every claim that later turns are gone is removed (`time-travel-and-replay.md:29`, `replay.about_restore`, `replay.confirm_restore`, TUI `chat.py:1106`, gateway reply `graph.py:2286`). The next message's model input excludes rewound turns. The memory half is decided with MEM-R10 | SSE-level test restoring ≥2 turns back, then sending: model input lacks the later turns and the transcript shows the rewind; real-graph scratchpad test; EN/AR UI text test; if memory is rewound, MEM-R10's recall-after-rewind test. Negative control: today's route | Implemented 2026-10-14 (choice: rewind, not rename). The restore cuts the chat-store transcript after the restored step (`_transcript_cut`), writes `SCRATCHPAD_CLEAR` with the step's scratchpad, deletes the later steps, journals `thread_rewound` (both browser mouths reload the chat) and takes the turns back from memory (M09 `rewind_memory`). Pages, catalog (EN/AR), docs and gateway/TUI replies say what is not undone (files, commands, sent messages). Tests: `test_a_restore_two_turns_back_is_what_the_next_message_sends` (control: the old transcript), `test_a_restore_replaces_the_scratchpad` (control: the merge reducer), `test_a_restore_tells_every_open_tab`, `test_the_pages_say_what_a_restore_takes_back_and_what_it_does_not`; in a browser `tests/e2e/test_time_travel_page.py` (the next question reaches the model without the rewound turn). |
| TT-05 | **S2.** Delete, forget/kept-out ("Don't remember this chat", `/memory off`) and `/replay clear` on every surface purge the thread's snapshots, under every chat key, from the store and every recorder cache through one `forget_thread_snapshots(thread_keys)`. The web chat's `/replay clear` clears or says plainly that it does not. `scripts/scan_old_sessions.py` skips snapshots or checks `refuses_write` for every chat key. The plain chat-delete disposition is decided consistently with AGENTS §15I (deleting a chat keeps its memories unless "Also forget" is ticked) and stated in the delete dialog. The backup and bundle disposition of `snapshots.db` (today `bundle`, `store_registry.py:116`) is decided and recorded; snapshots are entered in the memory forget-contract registry (MEM-L03, MEM-S02) | Behavioural tests: after delete, forget and `/replay clear`, no surface (web page and API, web chat, gateway, TUI, scan script) can list or restore the thread's steps; store-registry disposition test. Negative control: today's code | Implemented 2026-10-14. One delete, `forget_thread_snapshots` / `forget_turn_snapshots` (every chat key, the database only, so no cache keeps a step): chat delete, `forget_chat` (Forget the chat, `/memory off` with its forget of the past), a user's Forget of a memory (steps from that turn on) and `/replay clear` on the chat apps, the web chat, the page and the TUI. Delete dialog states the disposition in EN/AR (steps go, memory stays unless ticked, §15I). `snapshots.db`: `machine` in `store_registry` and `NOT_BACKED_UP` (not in the universal backup, not in a migration bundle). Registered in `memory_stores.EXTERNAL_STORES`. `scripts/scan_old_sessions.py` reads the steps and asks the forget ledger under every chat key for each turn (Memory M08): `test_the_retroactive_scan_reextracts_nothing_forgotten` (control: the ledger ignored). Tests: `test_deleting_a_chat_deletes_its_steps`, `test_the_delete_dialog_says_the_steps_go_and_memory_stays`, `test_forgetting_a_turn_deletes_the_steps_that_hold_it`, `test_forgetting_the_chat_deletes_every_step`, control `test_without_the_fan_out_a_forgotten_turn_stays_in_the_steps`, `test_replay_clear_on_every_surface_leaves_no_surface_a_step`, `test_snapshots_are_neither_backed_up_nor_carried`, `test_the_memory_forget_contract_names_the_steps`; `tests/test_memory_store_registry.py` green. |
| TT-G2 | **S2 exit.** The same snapshot id yields the same state on every surface, across turns and a restart; the next turn's model input matches the rewound transcript; a deleted or forgotten chat cannot be listed, restored or re-ingested | One multi-turn, multi-surface, restart-spanning test on the TT-12 fixture, SQLite and Postgres savers | Implemented 2026-10-14. `test_one_step_one_state_on_every_surface_across_a_restart` (SQLite and Postgres savers: three turns, a chat-app restore by short id, a new recorder and saver, the same id, the next turn's input, a deleted chat that cannot be listed or restored); the chat apps' real handler and the TUI in `tests/test_time_travel_surfaces.py`. |
| TT-06 | **S3.** Declared per-snapshot, per-thread and total byte budgets enforced in SQLite (not only in memory), oldest-first eviction, a per-snapshot ceiling with explicit truncation, captures per turn capped (for example first, last, every K-th), message counts stored at write time (the listing route stops decoding every state, `replay_routes.py:157-169`), `VACUUM` only when the freelist ratio justifies it | Capacity/soak test: a mission-length turn (2,000 iterations) and many threads, with disk and RAM measurements against the declared budgets. Negative control: today's count-only caps (1,669,451 bytes stored for an 81,380-byte final state; memory 2 vs SQLite 6) | Implemented 2026-10-14. Budgets enforced in SQLite on every save, oldest first by capture order: per step 2 MiB (a larger state is stored cut and marked, readable, never restorable), per turn 30 (first and newest), per chat 50 steps / 32 MiB, install 2,000 steps / 512 MiB; capture thinned in a long turn (every step to 20, every 10th after, always the last); `message_count`/`nbytes` stored at write, the listing reads no state; VACUUM only at 25 % and 8 MiB free. Tests in `tests/test_time_travel_bounds.py`, control `test_without_the_budgets_the_store_grows_with_the_square_of_a_turn`. Measurement: see TT-G3. |
| TT-07 | **S3.** One fork routine for web and gateway: projected transcript (no system, recall or raw tool messages in the chat row), catalog title in EN and AR, owner and chat row written off the event loop with failure reported (today DEBUG: `sessions/directory.py:141-142`, `graph.py:2366-2367`), `/fork 0` or fork by snapshot id accepted, and the page's fork confirms and links the new chat | Fork route tests (stored roles, title language); `record_thread_owner` and the chat-store write added to `_LOOP_STALL_HELPERS` (`tests/test_static_gates.py:886`) with a negative control. Negative control: the current web fork | Implemented 2026-10-14. One `fork_thread` for web, gateway and TUI: projected transcript (questions and answers only), catalog title `replay.fork_title` (EN/AR), owner recorded first off the loop (refused 503 `owner_unavailable` when it fails; `record_thread_owner` returns False and logs WARNING), chat row written off the loop and reported, fork by snapshot id; the page confirms and links the new chat. Tests: `test_the_fork_route_writes_the_questions_and_answers_with_a_catalog_title`, `test_a_fork_whose_owner_cannot_be_recorded_writes_nothing`, `test_a_failed_owner_record_is_reported_not_swallowed`; `record_thread_owner` and `_save_fork_chat` in `_LOOP_STALL_HELPERS` with control `test_the_owner_record_and_the_fork_chat_are_loop_stall_helpers`. |
| TT-08 | **S3.** Settings reports the live recorder's caps through a public property (`max_snapshots`, the global cap; also effective retention and auto-maintain); the restart banner clears after a restart (today `settings.py:1010` reads a missing attribute) | Real `/api/settings/time_travel` route test with a recorder built from a stored override. Negative control: an attribute-less recorder | Implemented 2026-10-14. `SnapshotRecorder.max_snapshots`, `max_global_snapshots`, `limits` public; `/api/settings/time_travel` reports them with the live retention and auto-maintain (`live_maintenance_config`). Test `test_settings_reports_the_live_recorders_cap` (control: an attribute-less recorder). |
| TT-09 | **S3.** Migration rewrites JSON-escaped paths in both directions in `snapshots.state_json` and every JSON column `migration/importer.py:46-75` rewrites, verifies each JSON column still parses after the rewrite, and reports changed rows truthfully; AGENTS §18 B matches | Round-trip test Windows↔Linux with a JSON-encoded Windows source on `snapshots.state_json` and the other JSON columns. Negative control: today's `rewrite_text` | Partial 2026-10-14. Code and tests done: `path_rewrite.rewrite_text` also rewrites JSON-escaped Windows paths both ways (the rest of the path converted to the target's separators), a JSON row whose rewrite would not parse is left unwritten and counted (WARNING), and `snapshots.db` no longer travels in a bundle (TT-05). Tests: `test_json_columns_are_rewritten_both_ways_and_still_parse` (Windows to Linux and back, control `_old_rewrite_text`), `test_a_rewrite_that_would_break_the_json_is_not_written`. Open: AGENTS §18 B still names `snapshots.state_json` as a rewritten column and does not mention the JSON-escaped variant; §18 is outside the Time Travel section and was left to the coordinator. |
| TT-10 | **S3.** Docs (`time-travel-and-replay.md`, `slash-commands.md`), catalog (`replay.about_note`, `replay.no_snapshots`, `replay.about_restore`, `replay.confirm_restore`) and AGENTS §12 (plus §18 B, §30 B2/E where TT-02/TT-09 change behaviour) match measured behaviour in EN and AR; the web `/replay` reply is localized with plural forms; the `replay_routes.py:13-16` docstring names the real gates; `replay.js` shows a 403/500 as an error, not "No snapshots" | Existing gates (`tests/test_i18n_keys_exist.py`, `tests/test_count_labels.py`, `tests/test_scripts_have_no_english.py`, `tests/test_docs_file_refs.py`); a page test where `/api/replay/snapshots/*` answers 500 and an error state appears; one test per docs claim (TT-G3) | Implemented 2026-10-14, with one shared-section note. Docs (`time-travel-and-replay.md`, `slash-commands.md`, `api-and-extension-points.md`), catalog `replay.*` (EN/AR, plural forms) and AGENTS §12 rewritten to the measured behaviour; the web `/replay` reply follows the page language; the `replay_routes.py` docstring names the real gate files; `replay.js` shows a 403/500/503 as an error. Tests: `test_the_docs_claims_match_the_code`, `test_the_web_chat_reply_follows_the_page_language`, the global i18n/count/script gates, and in a browser `test_a_failed_step_list_is_shown_as_an_error` (control: an honest empty list). AGENTS §30 B2/E (approve route) and §18 B were not edited (outside §12); see TT-09. |
| TT-11 | **S3.** A capture whose durable write failed returns `None` (no `snapshot_id`, no `snapshot` frame); store read errors reach the routes as 503 and the web chat says the store is unreadable; one audit row per restore or fork (actor, source thread, snapshot id, target, prior checkpoint id) and per gate it settles, in a store declared in `store_registry` (§37); `kazma_time_travel_capture_total{result}`; an ops alert for "prune did NOT stick"; a firing-ledger signature for daily maintenance | Fault-injection tests (locked DB, corrupt DB): honest 503, no `snapshot` frame; metric and audit-row assertions; ledger signature passes `test_every_ledger_signature_matches_a_line_the_code_emits`. Negative control: today's swallow-at-DEBUG | Implemented 2026-10-14. A failed durable write returns None (no `snapshot_id`, no frame); read errors raise `SnapshotStoreError`: 503 `store_unavailable` on the routes, a plain sentence in the chats; `kazma_time_travel_capture_total{result}`; one audit row per restore, fork, clear and refusal (`time_travel_audit.db`, declared in `store_registry`); ops alert `time_travel.prune_not_sticking`; ledger signature `time travel maintenance` (ROUTINE, 7 a week). Tests: `test_a_failed_write_is_not_announced`, `test_a_corrupt_store_is_said_on_every_surface`, `test_a_prune_that_does_not_stick_pages_the_operator`, `test_the_daily_maintenance_has_a_ledger_signature`, `test_the_audit_trail_has_a_row_per_restore_fork_and_refusal`; `test_every_ledger_signature_matches_a_line_the_code_emits` green. |
| TT-G3 | **S3 exit.** A mission-length soak (2,000 iterations, many threads) stays within the declared disk and RAM budgets; fault injection shows honest errors; each docs claim is checked by a test | Soak measurement log with budgets and observed peaks; fault-injection results; docs-claim test list | Partial 2026-10-15. The one-shot measurement is `test_a_mission_length_turn_and_many_threads_stay_within_the_budgets` (`KAZMA_TEST_TT_SOAK_SCALE` scales it): scale 1 (a 2,000-iteration turn and 40 chats of 60): 990 rows, 21.1 MB stored, file 22.5 MB, peak Python allocation 1.8-2.2 MB, 7-12 s; scale 5 (200 chats, 5x messages): 1,988 rows (cap 2,000), 48.4 MB stored, file 52.4 MB, peak 7.8 MB, 62 s, the mission chat held to its 32 MiB budget (18 steps). Fault injection in TT-11; docs claims in TT-10. The harness for the long run is built (2026-10-15): `scripts/time_travel_soak.py`, a child process per cycle (every cycle a restart) on private SQLite stores, with the product's recorder (shipped budgets), SQLite saver, capture hook and a real LangGraph graph on `SupervisorState`. It runs turns on a pool of chats (approved, denied, left waiting), 2,000-iteration turns, states past the 2 MiB ceiling, steps aged past the retention, every user operation through the real services (list and `/replay`, detail, compare, `rewind_thread`, `fork_thread`, `forget_turn_snapshots`, `forget_thread_snapshots`, `/replay clear`, chat delete), maintenance on a cadence, and faults: the write lock held, every statement failing, the file not a database, a restore under a running turn, of a cut step and under a waiting approval, and kills after a step's insert, inside its eviction and at a seeded moment. After every cycle it checks from the stores: integrity; every cap and byte budget (one step over right after a kill, until its turn continues: the insert and the eviction are two transactions); the file and WAL bounds and the page model they rest on; the VACUUM rule; the cycle child's RSS (384 MiB, 128 MiB over its start); a restored or forked chat equal to its step, its transcript cut, later steps gone; deleted steps never back; each fault's honest ending. Test: `tests/test_time_travel_soak_harness.py` (5 cycles with a 300-iteration turn do every operation, fault and kill; negative controls: rows past a cap, a deleted chat's step and a deleted step id put back, a restore that is not its step, a checkpoint changed across a restart). A 4-minute run (22 cycles, ten 2,000-iteration turns, six kills, 24 restores, 13 forks, 22 faults): healthy; peaks 30 steps a turn (cap 30), 33.2 MB a chat (32 MiB), 220 steps and 87.8 MB stored, file 89 MB (bound 572 MB), WAL 54.5 MB (bound 581 MB), RSS 131 MiB (30 MiB over its start). Found by its first runs: with the recorder's connection open, as on a server, a VACUUM did not give the disk back as `maintain_snapshots` reported. A large result left a WAL the size of the new file (81.6 MB on disk became 108.2 MB, 27.5 MB reported reclaimed); a small one left the file unshrunk until a later checkpoint. Fixed 2026-10-15: the VACUUM is followed by a TRUNCATE checkpoint on its connection (`_truncate_wal`; a reader holding an old snapshot is said in the status), and the sizes it reports count the file, its WAL and its shared memory (`_disk_bytes`); `test_a_vacuum_gives_the_disk_back_and_reports_what_it_gave`, with the untruncated VACUUM as its control; the soak counts a WAL left after an `ok` VACUUM as a violation. Known and left: a kill between a step's insert and its eviction (two transactions) leaves that turn one step over its cap until the turn's next capture; a chat never captured again keeps it until the retention prunes it (making the two one transaction would change the soak's kill points; the owner's call). Open (D3): the run of hours or days, on a separate machine or in an agreed overnight slot. |
| TT-Q1 | **Qualification.** The surfaces the audit did not exercise are exercised end to end on a test harness, never the live install: the Time Travel page and a restored chat in a browser, arriving by sidebar soft navigation, in EN and AR; gateway `/replay` and `/fork` through the real handler; TUI `/replay` | `tests/e2e` runs (run explicitly; `scripts/fast_test.py` skips `tests/e2e`) and gateway handler tests on the TT-12 fixture | Implemented 2026-10-14. `tests/e2e/test_time_travel_page.py` (run explicitly): the page reached by the sidebar from another page, two turns listed as turns, a restore confirmed in the dialog, the chat reached by the sidebar showing the first turn only, the next question's model input without the rewound turn, the page and its tabs in Arabic with no English interface text, and a 500 shown as an error (control: an empty list). `tests/test_time_travel_surfaces.py`: the chat apps' `/replay list`, `/replay <step>`, `/fork <step>` and a refused restore through `create_graph_handler`, and the TUI's `/replay list`, restore and fork over the real routes, all on the fixture graph. |

## Cross-area dependencies

- **TT-03 / TT-04 with `memory-recall.md` MEM-R10:** rewinds do not touch
  long-term memory; MEM-R10's witness enumerates seven state rewriters, the
  set TT-04's lock must cover. Decide the memory half once.
- **TT-05 with `memory-lifecycle.md` MEM-L03 and `memory-surfaces.md`
  MEM-S02:** both define a forget fan-out over the stores holding turn text;
  MEM-L03 names time-travel snapshots as unverified, MEM-S02's list does not
  name `snapshots.db`. Register snapshots there.
- **TT-05 with MEM-L07:** a forget journal replayed after restore or
  migration import. Once snapshots are purged on forget, a restored
  `snapshots.db` raises the same question (merge note; neither report tests
  it).
- **TT-09:** the JSON-escaping gap also affects non-snapshot JSON columns;
  no migration report exists in this set, so the class fix lands here.

## Open choices the report leaves

Decide, record the choice and its reason in the execution log, and proceed:

1. TT-03: rewind the transcript, or rename the feature and drop the claims.
2. TT-03 / MEM-R10: forget rewound turns by provenance, or disclose that
   memory is kept.
3. Stage 2: re-keyed `snapshots.db`, or restore from LangGraph checkpoint
   history (`aget_state_history` + `checkpoint_id`, bounded by §41) with
   `snapshots.db` as an optional index.
4. TT-05: plain chat-delete disposition; `snapshots.db` backup disposition
   (`bundle`, `rebuilt`/not backed up, or encrypted).
5. TT-02: refuse versus settle a pending gate; `superseded` versus a new
   `rewound` state.

## Start here

Read first:

1. The merged audit, `time-travel.md`, and the witness file.
2. `AGENTS.md` §12 (line 607), §30 B2 and E (3213, 3276), §31 (3357), §15 I
   (1102), §28 (3074), §35 (3811), §37 (4000), §41 (4454), §18 B (1495), §2.
3. Engine and capture: `kazma-core/kazma_core/time_travel.py`;
   `kazma-core/kazma_core/agent/graph_builder.py:161-184`;
   `kazma-core/kazma_core/agent/state.py` (`iteration`, `scratchpad`).
4. Restore surfaces: `kazma-ui/kazma_ui/replay_routes.py`;
   `kazma-gateway/kazma_gateway/agent_handler/graph.py` (`cmd_lock` 930,
   `_handle_replay`, `_handle_fork` ~2250-2380);
   `kazma-gateway/kazma_gateway/slash_commands.py` (145-168, `_cmd_replay`
   438); `kazma-ui/kazma_ui/sse_chat/_time_travel.py`;
   `kazma-tui/kazma_tui/chat.py` (`/replay`);
   `kazma-ui/kazma_ui/templates/replay.html`,
   `kazma-ui/kazma_ui/static/js/replay.js`.
5. HITL path: `kazma-ui/kazma_ui/routes_direct/misc.py` (approve route);
   `kazma-ui/kazma_ui/hitl_gate_bridge.py`, `hitl_decision.py`,
   `turn_runtime.py`, `hitl_timeout.py`, `active_turns.py`;
   `kazma-core/kazma_core/agent/graph_tool_worker.py` (~1314-1364),
   `agent/hitl_supersede.py`, `agent/effect_journal.py`;
   `kazma-core/kazma_core/safety/commitment/resume.py`.
6. Transcript, privacy, migration, settings:
   `kazma-core/kazma_core/agent/turn_input.py` (`build_turn_messages`);
   `kazma-ui/kazma_ui/sse_chat/__init__.py`;
   `kazma-core/kazma_core/memory/forget.py`; `scripts/scan_old_sessions.py`;
   `kazma-core/kazma_core/store_registry.py`;
   `kazma-core/kazma_core/backup/universal.py`;
   `kazma-core/kazma_core/migration/path_rewrite.py`, `importer.py`;
   `kazma-ui/kazma_ui/settings.py` (~1005-1012),
   `kazma-ui/kazma_ui/static/js/settings_agent.js` (~741).
7. Docs and catalog: `docs/docs/guide/time-travel-and-replay.md`,
   `docs/docs/reference/slash-commands.md`,
   `kazma-ui/kazma_ui/i18n/catalog/common.py` (`replay.*`).

Run rules:

- Run tests at below-normal priority (a shell whose priority class is
  BelowNormal; Windows children inherit it). `scripts/fast_test.py` lowers
  its own pytest processes.
- Targeted tests while working. The full suite once, at the end, with
  `& '.venv\Scripts\python.exe' scripts/fast_test.py` and no `--chunks`. Do
  not edit files while it runs. It skips `tests/e2e`: run the page and
  Arabic e2e tests explicitly when templates, scripts or catalog strings
  change.
- Never touch the live install at `C:\Users\balfa\kazma`. Never start or stop
  the Kazma server.
- Restore and fork tests use a real LangGraph graph and saver across more
  than one turn (TT-12); a fake that records `aupdate_state` arguments is
  not evidence. Postgres variants run against a throwaway database only.
- Fix the class, not the instance: each fix ships with a behavioural test, a
  sibling gate where the class can recur, and a negative control that fails
  on the old code (§28, §35).
- No commits or pushes unless the user asks.

## Execution log

- 2026-10-13: tracker opened from the merged audit. No remediation started.
- 2026-10-13 (from the Memory tracker, M09/M10, shared defects fixed once):
  the TT-03 memory question is decided -- memory is rewound. A restore
  (`apply_snapshot_to_thread`), `/undo`, `/edit` and abort call
  `memory.forget.rewind_memory`, which forgets the rewound turns, journaled
  (`tests/test_memory_rewind.py`). Every memory writer and rebuilder asks the
  forget ledger, and a restored `memory_state.db` replays the forget journal
  (`tests/test_memory_forget_converges.py`), so the memory side of TT-05 is
  covered. Still open here: the transcript and scratchpad after a restore
  (TT-03), the rewriters' shared lock (TT-04), and the copies `snapshots.db`
  itself keeps of forgotten and deleted chats (TT-05).

- 2026-10-14: remediation implemented (rows above). Choices and reasons:
  1. TT-03: the transcript is rewound (not renamed). The feature is called
     restore everywhere and its pages promised the later turns were gone;
     the cut makes that true and the dialogs say what is not undone.
  2. TT-03 / MEM-R10: memory is rewound (decided with Memory M09; built on
     `rewind_memory`, not duplicated).
  3. Stage 2: a re-keyed `snapshots.db` stays the source of truth.
     Checkpoint history cannot be: §41 retention keeps 200 checkpoints a
     chat (10 when idle), so older steps would vanish, and a restore would
     depend on checkpoint ids the retention prune deletes.
  4. TT-05: deleting a chat deletes its steps (they are copies of the chat,
     like its checkpoints, which the delete already removed); memory follows
     §15I (kept unless "Also forget" is ticked); the dialog says both.
     `snapshots.db` is not backed up and not carried (`machine`,
     `NOT_BACKED_UP`): a copy elsewhere would bring a deleted or forgotten
     chat back, and the steps are a convenience a fresh install can do
     without. This also settles the MEM-L07 question for snapshots: there is
     no restored copy to replay a forget into. A chat kept out of memory
     (`/memory off`, "Don't remember") loses its steps when its past is
     forgotten; later turns still save steps, like its checkpoints.
  5. TT-02: refuse (D11). No `superseded`/`rewound` gate state is needed:
     a restore never happens while a gate is live, and a late answer to a
     card whose interrupt is gone resumes nothing (keyed resume).
  6. A restore of any step, mid-turn included, writes a turn-boundary state
     (the step's unfinished tool calls are dropped, tool chains sanitized),
     and deletes the steps captured after it. A user's Forget deletes the
     chat's steps from that turn on; a rewind's own `forget_episode(by=
     "rewind")` leaves that to the restore. `/reset` keeps the steps
     (documented). One recorder per process (`process_recorder`).
  Debt ratchet: `blind_except` 3,444 (baseline 3,448) and
  `module_local_public_symbols` 544 (baseline 545) went down; the baseline
  file was not edited (the coordinator locks it in).
- 2026-10-14: merged to `main` by the coordinator. AGENTS §18 B now says
  snapshots are not carried and names the JSON-escaped Windows path and the
  kept-when-unparseable rule (TT-09); §30 B2 says the approve route resumes
  only the live approval interrupt, keyed by its id, with no gate-less
  fallback (TT-02). Debt ratchet locked at `blind_except` 3,444 and
  `module_local_public_symbols` 543. The browser test joined the CI
  Playwright job.
- 2026-10-15: the TT-G3 soak harness built (`scripts/time_travel_soak.py`,
  its test `tests/test_time_travel_soak_harness.py`; the row above says what
  it runs and checks). No product code changed. Its first 4-minute run was
  healthy and found that a VACUUM with the recorder's connection open does
  not return the disk `maintain_snapshots` reports (row TT-G3); deciding a
  fix (a checkpoint with TRUNCATE after the VACUUM, or a
  `journal_size_limit`) is left to the coordinator. The run of hours or days
  is open (D3).
