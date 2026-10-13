# Time Travel industrial hardening — continuous execution

Status: OPEN -- 2026-10-13

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
| TT-12 | **S0.** One shared real-graph fixture — real `SupervisorState` (scratchpad reducer included), product node names and routing, a stub tool worker that calls `interrupt()`, and each supported saver (the product SQLite saver and the Postgres saver) — drives every restore, fork and approve test across more than one turn. The fake `aupdate_state` recorders (`test_snapshot_rewind_payload.py:32-38`, `test_replay_router_getters.py:37-44`) no longer stand in for restore semantics; the global-cap test asserts on SQLite, not only the memory dict (`test_audit_2026_09_04_wave5.py:123-133`); `test_path_rewrite.py` has JSON-encoded input | List of fixture-backed tests with their negative controls; SQLite runs locally; Postgres variants marked `@pytest.mark.postgres` and green in the Postgres CI job; all 17 witnesses inverted, each with the defect as control | Open |
| TT-02 | **S1.** Restore refuses (409 `approval_pending`) or first settles every live gate on the thread through `record_gate_decision` (`superseded` or a dedicated `rewound` state) and emits the `hitl` journal frame. Restore writes a turn-boundary state only (`tool_calls_pending=[]`, nothing runnable). The approve route (web) and the gateway approval path resume only when the checkpoint holds a live interrupt whose id equals the decided gate: `next` without an interrupt answers 409 and never resumes (`routes_direct/misc.py:638-640`), the gate-less fallback (`misc.py:773-779`) cannot execute a call, and `build_resume_command` keys security resumes by interrupt id | Real-graph test: restore under a pending gate, then Approve and Deny on web and gateway; each refuses or decides only the gate shown. Approve-route test that calls the real route with `next` and no interrupt → 409. Negative control: today's route and restore execute the denied call (`executed == ['rm -rf build']`) | Open |
| TT-04 | **S1.** One per-thread checkpoint-mutation lock shared by turns, approve, restore, fork, `/undo`, `/edit` and `/compact` on every transport (the gateway's `cmd_lock`, `agent_handler/graph.py:930-935`, covers gateway paths only). Restore and fork answer 409 `turn_running` while a turn runs or is mid-segment (`active_turns.is_turn_running`) | Route test with a registered running turn → 409; real-graph race test (restore during a run is refused, not silently lost); a gate enumerating the checkpoint-mutating entry points (MEM-R10's list of seven state rewriters as the starting set) that fails on one without the lock, with its control. Negative control: today's route answers 200 | Open |
| TT-G1 | **S1 exit.** One `rewind_thread(thread, snapshot_id, actor)` service called by every surface: under the TT-04 lock it refuses while a turn runs, refuses or supersedes pending gates, writes turn-boundary state only, clears and replaces the scratchpad, emits a journal frame and writes an audit row. No Approve or Deny on any transport after a restore can run a call the human did not see; restore during a run or a pause is refused deterministically | The TT-02 and TT-04 tests on the real graph with SQLite and Postgres savers, with negative controls; a gate that no surface writes a restore except through the service (control: a direct `apply_snapshot_to_thread` call from a route) | Open |
| TT-01 | **S2.** Snapshots addressed by a unique id carrying turn identity (snapshot uuid + turn identity such as `created_at` / user-turn index / reply `turn_id` + iteration). Restore, fork, detail and compare by snapshot id, never a bare iteration. Listing shows turns, then steps, each step once. Retention evicts oldest-first by capture order (today `ORDER BY iteration DESC`, `time_travel.py:284-286`). One recorder per process, the database as source of truth (or a write-through cache by id). Identical restore result on web, gateway and TUI, before and after a restart | Real multi-turn graph test: listing, restore-by-id across surfaces and a restart, eviction keeps the newest N. Negative control: the `(thread, iteration)` keying fails all three | Open |
| TT-03 | **S2.** Restore rewinds the transcript (chat-store rows after the restored turn marked or truncated) and the scratchpad (`SCRATCHPAD_CLEAR` before applying), and emits a journal frame so open tabs repaint; the UI and docs state what is not rewound (memory, files, messages already sent). Or the feature is renamed "restore agent context" and every claim that later turns are gone is removed (`time-travel-and-replay.md:29`, `replay.about_restore`, `replay.confirm_restore`, TUI `chat.py:1106`, gateway reply `graph.py:2286`). The next message's model input excludes rewound turns. The memory half is decided with MEM-R10 | SSE-level test restoring ≥2 turns back, then sending: model input lacks the later turns and the transcript shows the rewind; real-graph scratchpad test; EN/AR UI text test; if memory is rewound, MEM-R10's recall-after-rewind test. Negative control: today's route | Open |
| TT-05 | **S2.** Delete, forget/kept-out ("Don't remember this chat", `/memory off`) and `/replay clear` on every surface purge the thread's snapshots, under every chat key, from the store and every recorder cache through one `forget_thread_snapshots(thread_keys)`. The web chat's `/replay clear` clears or says plainly that it does not. `scripts/scan_old_sessions.py` skips snapshots or checks `refuses_write` for every chat key. The plain chat-delete disposition is decided consistently with AGENTS §15I (deleting a chat keeps its memories unless "Also forget" is ticked) and stated in the delete dialog. The backup and bundle disposition of `snapshots.db` (today `bundle`, `store_registry.py:116`) is decided and recorded; snapshots are entered in the memory forget-contract registry (MEM-L03, MEM-S02) | Behavioural tests: after delete, forget and `/replay clear`, no surface (web page and API, web chat, gateway, TUI, scan script) can list or restore the thread's steps; store-registry disposition test. Negative control: today's code | Open |
| TT-G2 | **S2 exit.** The same snapshot id yields the same state on every surface, across turns and a restart; the next turn's model input matches the rewound transcript; a deleted or forgotten chat cannot be listed, restored or re-ingested | One multi-turn, multi-surface, restart-spanning test on the TT-12 fixture, SQLite and Postgres savers | Open |
| TT-06 | **S3.** Declared per-snapshot, per-thread and total byte budgets enforced in SQLite (not only in memory), oldest-first eviction, a per-snapshot ceiling with explicit truncation, captures per turn capped (for example first, last, every K-th), message counts stored at write time (the listing route stops decoding every state, `replay_routes.py:157-169`), `VACUUM` only when the freelist ratio justifies it | Capacity/soak test: a mission-length turn (2,000 iterations) and many threads, with disk and RAM measurements against the declared budgets. Negative control: today's count-only caps (1,669,451 bytes stored for an 81,380-byte final state; memory 2 vs SQLite 6) | Open |
| TT-07 | **S3.** One fork routine for web and gateway: projected transcript (no system, recall or raw tool messages in the chat row), catalog title in EN and AR, owner and chat row written off the event loop with failure reported (today DEBUG: `sessions/directory.py:141-142`, `graph.py:2366-2367`), `/fork 0` or fork by snapshot id accepted, and the page's fork confirms and links the new chat | Fork route tests (stored roles, title language); `record_thread_owner` and the chat-store write added to `_LOOP_STALL_HELPERS` (`tests/test_static_gates.py:886`) with a negative control. Negative control: the current web fork | Open |
| TT-08 | **S3.** Settings reports the live recorder's caps through a public property (`max_snapshots`, the global cap; also effective retention and auto-maintain); the restart banner clears after a restart (today `settings.py:1010` reads a missing attribute) | Real `/api/settings/time_travel` route test with a recorder built from a stored override. Negative control: an attribute-less recorder | Open |
| TT-09 | **S3.** Migration rewrites JSON-escaped paths in both directions in `snapshots.state_json` and every JSON column `migration/importer.py:46-75` rewrites, verifies each JSON column still parses after the rewrite, and reports changed rows truthfully; AGENTS §18 B matches | Round-trip test Windows↔Linux with a JSON-encoded Windows source on `snapshots.state_json` and the other JSON columns. Negative control: today's `rewrite_text` | Open |
| TT-10 | **S3.** Docs (`time-travel-and-replay.md`, `slash-commands.md`), catalog (`replay.about_note`, `replay.no_snapshots`, `replay.about_restore`, `replay.confirm_restore`) and AGENTS §12 (plus §18 B, §30 B2/E where TT-02/TT-09 change behaviour) match measured behaviour in EN and AR; the web `/replay` reply is localized with plural forms; the `replay_routes.py:13-16` docstring names the real gates; `replay.js` shows a 403/500 as an error, not "No snapshots" | Existing gates (`tests/test_i18n_keys_exist.py`, `tests/test_count_labels.py`, `tests/test_scripts_have_no_english.py`, `tests/test_docs_file_refs.py`); a page test where `/api/replay/snapshots/*` answers 500 and an error state appears; one test per docs claim (TT-G3) | Open |
| TT-11 | **S3.** A capture whose durable write failed returns `None` (no `snapshot_id`, no `snapshot` frame); store read errors reach the routes as 503 and the web chat says the store is unreadable; one audit row per restore or fork (actor, source thread, snapshot id, target, prior checkpoint id) and per gate it settles, in a store declared in `store_registry` (§37); `kazma_time_travel_capture_total{result}`; an ops alert for "prune did NOT stick"; a firing-ledger signature for daily maintenance | Fault-injection tests (locked DB, corrupt DB): honest 503, no `snapshot` frame; metric and audit-row assertions; ledger signature passes `test_every_ledger_signature_matches_a_line_the_code_emits`. Negative control: today's swallow-at-DEBUG | Open |
| TT-G3 | **S3 exit.** A mission-length soak (2,000 iterations, many threads) stays within the declared disk and RAM budgets; fault injection shows honest errors; each docs claim is checked by a test | Soak measurement log with budgets and observed peaks; fault-injection results; docs-claim test list | Open |
| TT-Q1 | **Qualification.** The surfaces the audit did not exercise are exercised end to end on a test harness, never the live install: the Time Travel page and a restored chat in a browser, arriving by sidebar soft navigation, in EN and AR; gateway `/replay` and `/fork` through the real handler; TUI `/replay` | `tests/e2e` runs (run explicitly; `scripts/fast_test.py` skips `tests/e2e`) and gateway handler tests on the TT-12 fixture | Open |

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
