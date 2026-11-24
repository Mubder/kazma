<!-- AGENTS.md, piece 12-time-travel. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 12. Time Travel Replay (`kazma-core/kazma_core/time_travel.py`)

The time-travel subsystem saves the `SupervisorState` after supervisor
iterations to `<data dir>/snapshots.db` (SQLite WAL). Hardened to the
2026-10-13 audit (TT-01..TT-12, tracker
`docs/plans/TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md`). Every restore, fork
and approve test runs on the real graph and saver of `tests/_tt_graph.py`
(the product SQLite saver; Postgres when a throwaway database is set).

**A. The recorder must be wired into ALL graph-build sites.**
- `SnapshotRecorder` is created once per agent (`agent_runner.py` via
  `create_recorder(config=...)`) and passed to `build_supervisor_graph(...,
  snapshot_recorder=...)` at all 3 call sites: the run graph
  (`_ensure_graph`), the streaming graph (`get_streaming_graph`), and
  child graphs (`build_child_graph`); also the app.py post-startup recompile
  and `kazma_core/cli/ask.py`. If any site omits the recorder, that path stops
  capturing snapshots silently. The server registers it as the process's
  recorder (`set_process_recorder`); the chat apps, the slash resolver and the
  forget fan-out use `process_recorder()` (they each built their own).

**B. One source of truth, one identity (TT-01).** The database only: no
in-memory copy (it keyed `(thread_id, iteration)`, disagreed with SQLite and
kept serving cleared steps). A step is addressed by its snapshot id;
`iteration` restarts every turn and is a step number inside a turn. Rows carry
`turn_key` (the turn's `created_at`), `turn_index` (user messages held, the
memory turn number), `seq` (capture order), `message_count`, `nbytes`,
`truncated`; an older file is backfilled from its own JSON at open. Restore,
fork, detail and compare take the id on every surface (the chat apps accept
its first 8+ characters, `resolve_ref`); listings group turns then steps
(`replay_routes.group_steps`). Retention evicts by capture order.

**C. Capture (TT-06, TT-11).** `graph_builder._supervisor` calls
`time_travel.capture_after_supervisor` (one function: the test graph runs the
same path): off the loop, never failing the turn, thinned in a long turn
(`_capture_due`: every step to 20, every 10th after, always the last), and
stamping `snapshot_id` only when the durable write succeeded (`capture`
returns None on failure; `kazma_time_travel_capture_total{result}`). The
step's insert and its eviction are ONE write transaction
(`SnapshotStore.enforce_bounds(record, insert=True)`, the only call
`capture` makes): a crash or an error leaves the step stored with its
eviction, or not at all, and None means nothing was stored. As two
transactions (`save`, then `enforce_bounds`) a kill between them left the
turn one step over its cap until its next capture (31 of 30, found by the
TT-G3 soak, 2026-10-15); `save` alone is for seeding a store in tests.
Gate: `tests/test_time_travel_capture_atomic.py` (a real child killed inside
and after the transaction; the two transactions as the negative control; no
product code writes a step any other way). Budgets
are enforced in SQLite on every capture, oldest first: per step
(`max_snapshot_bytes`, 2 MB: a larger state is stored cut with
`_time_travel_truncated` and can be read, never restored or forked), per turn
(`max_per_turn`, first and newest), per chat (`max_snapshots`,
`max_thread_bytes`) and in total (`max_global_snapshots`, `max_total_bytes`).
Read failures raise `SnapshotStoreError`: 503 `store_unavailable` on the
routes, a plain sentence in the chats -- never "no steps saved".
`maintain_snapshots` VACUUMs only when a quarter of the file and 8 MB are
free, then truncates the WAL the VACUUM wrote through (`_truncate_wal`), and
reports sizes as the file plus its WAL and shared memory (`_disk_bytes`):
with the recorder's connection open the WAL kept the rewritten file's size,
and the "reclaimed" it reported was not the disk's (found by the TT-G3 soak,
2026-10-15). A prune that did not stick is an ops alert
(`time_travel.prune_not_sticking`); the run has a firing-ledger signature.
The long soak (TT-G3) is `scripts/time_travel_soak.py`: a restart per cycle
on private stores, 2,000-iteration turns, every restore/fork/forget path,
injected faults and kills, these budgets checked after every cycle
(`tests/test_time_travel_soak_harness.py`, with planted violations). A soak
reads a process's peak memory through `soak_report.peak_rss_bytes` (its own
peak: `VmHWM` on Linux), never `ru_maxrss`, which on Linux keeps the parent's
size from before the child's exec -- a cycle child of a 1 GiB pytest process
read 1 GiB on Linux CI (2026-11-10; gate in `tests/test_soak_report.py`).

**D. Restore and fork are one service (`kazma_ui/thread_rewind.py`, TT-02,
TT-04, TT-G1).** Every surface calls `rewind_thread` / `fork_thread`: the
page's routes (the TUI uses them), the chat apps' `/replay <step>` and
`/fork <step>` (`_handle_replay` / `_handle_fork` in gateway `graph.py`; the
slash resolver answers `list`, `compare`, `clear` and returns None for a
step). Under the thread's mutation lock a restore refuses 409 `turn_running`
(a registered turn, or the lock held by another task) and 409
`approval_pending` naming the cards (registry or live interrupt; decision
D11: nothing is settled behind the human), writes a turn-boundary state
(`turn_boundary_payload`: nothing pending, tool chains sanitized,
`SCRATCHPAD_CLEAR` first, written `as_node="respond"` so `next == ()`), takes
the later turns back from memory (`rewind_memory`, D2), from the transcript
(`_transcript_cut`: the chat-store rows after the step) and from Time Travel
(the steps saved after it), bumps the thread's rewind generation, journals
`thread_rewound` (every tab reloads; never replayed) and writes an audit row
(`kazma_core/time_travel_audit.py`, `time_travel_audit.db`, refusals too). A
fork records a chat app's owner first, off the loop (`record_thread_owner`
returns False and logs WARNING; a failure refuses the fork), writes the
boundary state under the new id, and saves a projected chat row (questions
and answers only) with a catalog title (`replay.fork_title`). Do NOT
overwrite `active_thread.{sender}` on a fork. Gates in
`tests/test_time_travel_rewind.py`: only `thread_rewind` calls
`apply_snapshot_to_thread`; every product function that calls
`aupdate_state`/`adelete_thread` holds `thread_locks.thread_mutation` or is
declared.

**D2. A rewind rewinds memory** (Memory audit M09, 2026-10-13, decided with
TT-03: memory is rewound, not kept). `/undo`, `/edit`, a restore
(`apply_snapshot_to_thread`) and abort call `memory.forget.rewind_memory`
after they write: the turns they took back -- episodes, the agent's notes,
facts -- are forgotten under every key and tenant that holds them, journaled,
with ledger rows from the old questions (a deferred write, the deep pass and
turn reconcile from the unchanged transcript do not bring them back); a fork
touches the original chat's memory not at all. The deep pass applies facts
only while its turn is still remembered (`_apply_if_still_remembered`). The
transforms are `agent/rewind.py`. Gate: `tests/test_memory_rewind.py` (each
rewrite through a real LangGraph graph, recall after; the rewrite alone as
the negative control; every product function calling `.aupdate_state(`
calls `rewind_memory` or is declared not a rewind).

**E. One per-thread checkpoint-mutation lock (`kazma_ui/thread_locks.py`,
TT-04).** Task-reentrant, per loop, bounded. Gateway turns and commands
(`cmd_lock`), the approve route (`_approve_lock_for`), the web abort, `/reset`
and chat delete, the dashboard delete, and the start of a web turn take it.
A web turn reads the checkpoint before its stream starts; it starts the graph
under the lock only if the thread's rewind generation is unchanged (else it
answers `replay.turn_after_restore` and runs nothing).

**F. The approve route resumes only a live interrupt, keyed (TT-02,
§30 B2).** `next` without an interrupt is a 409 (it was "still try resume"
with a gate-less security command: the restored step's denied call ran); a
card the registry knows as another gate is a 409
(`hitl_gate_bridge.gate_names_interrupt`); there is no gate-less fallback.
`build_resume_command(..., interrupt_id=)` keys every approval and deny
resume by LangGraph's interrupt id (web route, gateway `/hitl`, timeout
auto-deny, supersede, CLI): a resume whose interrupt is gone resumes nothing.
Gate: `test_every_approval_resume_is_keyed_by_its_interrupt`.

**G. Delete and Forget reach the steps (TT-05).** Chat delete
(`sse_chat` delete route), forgetting a chat (`forget_chat`), a user's Forget
of a memory (`forget_episode`, by "user": the chat's steps from that turn on,
under every key) and `/replay clear` on every surface go through
`forget_thread_snapshots` / `forget_turn_snapshots`; a rewind's own
`forget_episode(by="rewind")` leaves the steps to the restore. Registered in
`memory_stores.EXTERNAL_STORES`. `snapshots.db` is `machine` in
`store_registry` (not carried in a bundle) and in `NOT_BACKED_UP` (the
universal backup leaves it out): a copy elsewhere would bring back deleted or
forgotten chats. `/reset` keeps the steps (documented).

**H. The web chat answers `/replay` and `/fork` without the model**
(`kazma_ui/sse_chat/_time_travel.py`, 2026-09-28), in the page's language:
the saved steps (plural forms) and a link, `/replay?thread=<id>`, that
`replay.js` opens on that chat's timeline; `/replay clear` deletes the steps.
Before, the text reached the model. `tests/test_web_time_travel_command.py`.

**I. `/api/replay/*` is mounted when the app is built** (2026-10-01), and
reads the recorder and the graph per request (`create_replay_router(
recorder_getter=, graph_getter=)`): 503 `time_travel_unavailable` until
startup makes a recorder. A model switch rebuilds the graph, which a router
given one at mount time never saw. `tests/test_replay_router_getters.py`.

**J. Settings reads the live recorder (TT-08):** `max_snapshots`,
`max_global_snapshots` and `limits` are public properties;
`/api/settings/time_travel` reports them and the live retention, so "restart
required" clears after a restart. **Migration (TT-09):** `path_rewrite`
rewrites JSON-escaped Windows paths (`C:\\Users\\...`) both ways, converts the
path's rest to the target's separators, and keeps a JSON row whose rewrite
would not parse. Gates: `tests/test_time_travel_bounds.py`,
`tests/test_time_travel_identity.py`.

**K. Off the loop, and every surface driven (TT-Q1).** Building a recorder
(`create_recorder` reads the settings store), the process recorder, the
fan-out deletes, the owner record, the fork's chat row, the web chat's reply
and the audit store are loop-stall helpers (`_LOOP_STALL_HELPERS`; Time Travel
is a `_CROSS_MODULE_AREAS` area). The chat apps' real handler and the TUI's
commands list, restore and fork on the fixture graph
(`tests/test_time_travel_surfaces.py`); the page, reached by the sidebar, in
English and Arabic, a restored chat, and a failed step list shown as an error
are `tests/e2e/test_time_travel_page.py` (run explicitly: `fast_test` skips
`tests/e2e`).

