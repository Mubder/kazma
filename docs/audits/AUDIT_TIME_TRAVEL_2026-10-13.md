# Time Travel audit — final, 2026-10-13

> Source reports, witness files and logs: `session-artifacts/audits-2026-10-13/` (local working evidence, not committed). A file named below without a folder is there.

Source report: `session-artifacts/audits-2026-10-13/time-travel.md` (one
auditor, findings TT-01 to TT-12). Witness file
`time_travel_probes_2026_10_13.py` and logs `time-travel-probes.log`,
`time-travel-regression.log` (both `.err` files empty), all in
`session-artifacts/audits-2026-10-13/`. This document merges that report
into final form. It adds no finding. Where the merge re-read the code, the
current `file:line` is given; other line references are the auditor's.

Reviewed state: commit `d7e27c194d3f170092a9c405709b9197a2c00161` plus the
uncommitted working tree of 2026-10-13. The auditor found no uncommitted
change in the Time Travel files (`time_travel.py`, `replay_routes.py`,
`sse_chat/_time_travel.py`, `replay.html`, `replay.js`, the gateway
handlers). During the merge, `git status` on eleven of the cited files
(`time_travel.py`, `replay_routes.py`, gateway `graph.py`, `misc.py`,
`settings.py`, `state.py`, `turn_input.py`, `turn_runtime.py`,
`agent_runner.py`, `app.py`, `cli/ask.py`) showed one modified:
`kazma-ui/kazma_ui/settings.py` (+2 lines of document OCR settings, at lines
1254 and 1278). The TT-08 attribute read is unchanged by it: it is
at `settings.py:1010` in both HEAD and the working tree (the report cites the
surrounding `try` body as `1008-1009`).

## 1. Verdict

Time Travel works on one path: one turn per chat, restored on the surface
that recorded it, with no approval pending and no turn running. The
defensive work around it is real (section 3).

Auditor's scores (engineering judgments, not benchmark results):

| Dimension | Score |
|---|---|
| Capability and architecture | 4/10 |
| Demonstrated industrial readiness | about 2-3/10 |

Two structural problems carry most of the findings:

1. **Snapshot identity.** A snapshot is addressed by `(thread_id,
   iteration)`, and `iteration` restarts at 0 on every turn
   (`agent/state.py:424`). In a chat with more than one turn, "iteration N",
   what retention keeps, and what a restore writes are not well defined
   (TT-01).
2. **Restore is a raw `aupdate_state`** (`time_travel.py:841-854`). It is not
   a turn operation: it writes nothing to the gate registry, the transcript,
   the turn journal or the scratchpad, and it does not coordinate with a
   running turn. On a thread paused at a gate, an Approve on the still-live
   card can then run a different call, one the human had denied earlier
   (TT-02).

The auditor's condition for calling Time Travel industrial, or relying on it
as an operator recovery tool: TT-01 and TT-02 fixed, restore rebuilt as a
coordinated and audited operation, and its tests run on a real `StateGraph`
instead of a fake that records writes.

Finding count: **12** — P1: 2 (TT-01, TT-02); P2: 5 (TT-03, TT-04, TT-05,
TT-06, TT-12); P3: 5 (TT-07, TT-08, TT-09, TT-10, TT-11).

Priority meanings (auditor's): **P1** fix before relying on Time Travel or
making any multi-user or high-assurance claim; **P2** significant
correctness, privacy, availability or qualification work; **P3**
truthfulness, cleanup and smaller defects.

## 2. Scope and evidence

### 2.1 Reviewed

- Engine: `kazma-core/kazma_core/time_travel.py`.
- Capture hook in `agent/graph_builder.py` (`:161-184`) and every
  `build_supervisor_graph` call site.
- HTTP API: `kazma-ui/kazma_ui/replay_routes.py`.
- Web page: `templates/replay.html`, `static/js/replay.js`.
- Web chat commands: `sse_chat/_time_travel.py` and its intercept in
  `sse_chat/__init__.py`.
- Gateway: `agent_handler/graph.py` (`_handle_replay`, `_handle_fork`) and
  `slash_commands.py` (`/replay list|compare|clear`); the TUI `/replay`.
- Settings (`settings.py`, `settings_agent.js`), the maintenance loop and
  route.
- Migration (`importer.py`, `path_rewrite.py`), universal backup, store
  registry.
- Surfaces Time Travel touches: HITL approve route, gate bridge,
  `close_turn`, `build_turn_messages`, effect journal, chat delete, `/reset`,
  memory forget, `scripts/scan_old_sessions.py`.

### 2.2 Witness run

- **17 witnesses passed in 0.53 s** (`time-travel-probes.log`, Python 3.12.9,
  pytest 9.1.1, Windows). Each witness asserts that a defect is **present**.
  A pass confirms a finding; it is not a health signal.
- Inert: temporary SQLite files, LangGraph `InMemorySaver`, fake session
  stores, fake recorders/graphs. No server, network, LLM or live install.
- The restore witnesses use a real `StateGraph` on the real
  `SupervisorState` schema (real scratchpad merge reducer), the product's
  node names and routing shape, and the real `SnapshotRecorder`,
  `apply_snapshot_to_thread`, `has_pending_hitl`, `read_pending_interrupt`,
  `build_resume_command`, `build_turn_messages`, `rewrite_paths_in_sqlite`
  and `/api/replay/*` router.
- The TT-02 witness **mirrors** the approve route's branches
  (`misc.py:638-640`, `:773-779`) instead of calling the route; the real
  `tool_worker_node` and approve route were not executed end to end.
- Six witnesses are wholly or partly source-text checks rather than
  behaviour: the route-body half of `test_tt03_web_restore_is_undone_by_the_next_message`,
  the route-text half of `test_tt04_restore_during_a_running_turn_is_silently_lost`,
  `test_tt05_delete_reset_forget_never_touch_snapshots`, the gateway half of
  `test_tt07_web_fork_writes_system_and_tool_messages_into_the_chat`, the
  settings half of `test_tt08_settings_effective_cap_is_never_the_live_recorder`,
  and `test_tt10_small_surface_defects`.

| Witness | Finding | Logged observation |
|---|---|---|
| `test_tt01_iterations_restart_each_turn_and_collide` | TT-01 | rows=4 listed=2; web=`'second turn'`, gateway=`'first turn'` |
| `test_tt01_retention_deletes_the_newest_snapshot_not_the_oldest` | TT-01 | stored = three rows of `'old long turn'`; newest turn's row gone |
| `test_tt02_restore_under_a_pending_gate_lets_an_old_approval_run_another_call` | TT-02 | `executed=['rm -rf build']` |
| `test_tt03_rewind_keeps_later_scratchpad_entries` | TT-03 | scratchpad = `['note alpha', 'note beta']` |
| `test_tt03_web_restore_is_undone_by_the_next_message` | TT-03 | next-turn context includes `A2`, `A3 secret` |
| `test_tt04_restore_during_a_running_turn_is_silently_lost` | TT-04 | restore ok; final = `['u1', 'u2', 'late answer']` |
| `test_tt04_restore_route_accepts_a_thread_with_a_running_turn` | TT-04 | status 200 while `is_turn_running` |
| `test_tt05_gateway_clear_leaves_the_agent_cache_restorable` | TT-05 | cleared=1; agent cache still serves it |
| `test_tt05_delete_reset_forget_never_touch_snapshots` | TT-05 | source witness: delete/reset/forget/scan script |
| `test_tt06_global_cap_does_not_bound_the_database` | TT-06 | memory=2, SQLite=6 |
| `test_tt06_snapshot_bytes_grow_quadratically_and_are_uncapped` | TT-06 | 1,669,451 bytes stored for an 81,380-byte final state (ratio 20.5); a 5 MB snapshot accepted |
| `test_tt07_web_fork_writes_system_and_tool_messages_into_the_chat` | TT-07 | fork chat roles = `system, user, assistant, tool` |
| `test_tt08_settings_effective_cap_is_never_the_live_recorder` | TT-08 | "effective" falls back to yaml/default |
| `test_tt09_windows_paths_inside_snapshot_json_are_not_rewritten` | TT-09 | `n == 0`, `changed == 0` |
| `test_tt10_small_surface_defects` | TT-10, TT-07 | `/fork 0` refused; gateway fork writes on the loop; stale docstring; English-only web reply; docs claim web `/replay <N>` |
| `test_tt11_a_failed_snapshot_write_is_still_announced` | TT-11 | failed write, record returned, `snapshot_id` stamped |
| `test_tt11_an_unreadable_store_reads_as_no_saved_steps` | TT-11 | corrupt store answers "No steps of this chat have been saved yet" |

### 2.3 Existing Time Travel suite

**77 passed in 19.78 s** (`time-travel-regression.log`) across eleven files:
`tests/test_time_travel.py` (28), `test_snapshot_rewind_payload.py` (2),
`test_time_travel_concurrency.py` (2), `test_replay_router_getters.py` (3),
`test_thread_ownership_routes.py` (9), `test_web_time_travel_command.py`
(14), `test_replay_thread_titles.py` (3),
`test_snapshot_retention_actually_prunes.py` (6), `test_replay_command.py`
(6), `test_path_rewrite.py` (2), `test_thread_route_policy.py` (2). All are
green while every defect in section 4 is present (TT-12).

### 2.4 One extra check (not kept as a witness)

A fork of a mid-turn snapshot onto a fresh thread leaves
`next == ('supervisor',)`; a restore onto an idle thread leaves `next == ()`.
TT-02 is therefore specific to a restore made while an approval is pending
or a turn is running.

### 2.5 Not run

- Live browser tour of `/replay` in either language; Telegram, Discord or
  Slack session; TUI session.
- Restore semantics on `AsyncPostgresSaver` or the product's SQLite saver
  (demonstrated on LangGraph 1.2.6 `InMemorySaver`; the auditor states these
  are core Pregel behaviour, not saver-specific).
- The real `tool_worker_node` and approve route end to end (mirrored, see
  2.2). Gate A's registry state is inferred from code, not read from a
  `hitl_gates.db`.
- A restore against a real effect-journal receipt store (the "completed
  calls are fenced" claim rests on reading `effect_journal.py:45-130`).
- Measurement of the live `snapshots.db` (live install out of bounds).
- Load or soak run.
- `/undo`, `/edit`, `/compact` (they mutate checkpoints the same way; not
  audited).
- The report cites no full-suite run.

### 2.6 Re-checked during this merge (code read only, no execution)

`time_travel.py:229-242` (`get` without `ORDER BY`), `:277-296`
(`evict_beyond` orders by `iteration DESC`), `:385-429` (`capture`: memory
key `(thread_id, iteration)`, SQLite failure logged at WARNING and the record
still returned), `:841-854` (`apply_snapshot_to_thread` = one
`aupdate_state`); `agent/state.py:273` (scratchpad merge reducer), `:424`
(`iteration=0`); `agent/graph_builder.py:161-184`;
`agent/turn_input.py:1584-1592`; `agent/graph_tool_worker.py:1314-1324`;
`turn_runtime.py:94-103` (`_snapshot_paused`: any non-empty `next` is
paused); `routes_direct/misc.py:638-640`, `:773-779`;
`replay_routes.py:13-16`, `:203-236`, `:263`, `:270-285`; gateway
`agent_handler/graph.py:930-935`, `:2270`, `:2286`, `:2300-2301`, `:2314-2315`,
`:2326`, `:2353`, `:2362`, `:2365-2367`; `sessions/directory.py:126-142`;
`slash_commands.py:145-168`, `:555`; `settings.py:1004-1012`;
`i18n/catalog/common.py:518-525`, `:1114-1117`, `:1162-1165`;
`docs/docs/guide/time-travel-and-replay.md:24`, `:29`, `:70-71`;
`docs/docs/reference/slash-commands.md:107`, `:205`;
`kazma-tui/kazma_tui/chat.py:1106`; `store_registry.py:116`;
`docs/KNOWN_GAPS.md:1498-1513`; `AGENTS.md:611`. Each matched the report.

## 3. What is already good

| Area | Assessment |
|---|---|
| Ownership | Every `/api/replay/*` route calls `thread_ownership.require_thread_owned`: 404 for another caller's thread, 403 when ownership cannot be determined (fail closed). The routes are declared in `tests/test_thread_route_policy.py`, which has a negative control. No route or command was found that lists, reads, restores or forks another tenant's thread. |
| Event loop | Recorder, engine and compare work in the routes runs in `asyncio.to_thread`. The gateway loads restore/fork state off the loop. The web `/replay` reply reads the store off the loop. (Exception: TT-07.) |
| Turn safety | A capture failure cannot fail the turn (`graph_builder.py:161-184`, tested); capture runs off the loop. The recorder's cache and SQLite connection are lock-guarded (`test_time_travel_concurrency.py`). |
| Retention honesty | `maintain_snapshots` commits, re-counts and logs `prune did NOT stick` (`time_travel.py:710-799`; the message at `:775`). This fixed the 2026-09-14 incident; it has a negative control. |
| Paths | DB path anchored to the data dir, not the CWD (H19). `snapshots.db` is declared `bundle` in `store_registry.py:116`; the universal backup copies it WAL-safe in one step. |
| Payload hygiene | One builder, `checkpoint_payload_from_snapshot`, serves all four restore/fork sites; it rewrites `thread_id` and strips `chat_id`/`user_id`/`message_id` (AGENTS §2). |
| Serializer | Snapshots are JSON loaded with `json.loads` (constructs no classes); the restored payload is written through the checkpointer's strict serde (§40). |
| Effect fence | Graph effect identity (`effect_journal.py:45-61`) includes `created_at`, `iteration` and the call id; a snapshot restores all three, so a call that **completed** in the original timeline returns its recorded result. It does not cover calls never dispatched (TT-02). |
| Wiring | Recorder passed at every production build site: `agent_runner.py:922`, `:982`, `:1088`; `app.py:1914`; `kazma_core/cli/ask.py:539`. The evaluation graph is deliberately without one. The router is mounted at app build and reads graph and recorder per request (§12 F). |
| UX basics | The web chat answers `/replay` and `/fork` without the model. Page strings come from the catalog in both languages, with plural forms for the "restored" toast. |

## 4. Ranked findings

Overlap markers: **[x MEM-R10]**, **[x MEM-L03]**, **[x MEM-S02]**,
**[x MEM-L07]**, **[x migration]** name the other 2026-10-13 audit that
covers part of the same defect class (section 5).

### TT-01 — P1 — Snapshot identity collides across turns; retention deletes the newest step

**Evidence.** Every turn starts at `iteration=0` (`agent/state.py:424`; the
SSE path calls `initial_supervisor_state` per turn at
`sse_chat/__init__.py:1014`), and the supervisor increments it within the
turn (`graph_supervisor.py:476`, `:1873`). Snapshots are keyed by
`(thread_id, iteration)`: the memory key (`time_travel.py:391-405`),
`SnapshotStore.get` with no `ORDER BY` (`:229-242`), and `list_snapshots`,
which dedups by that key (`:452-478`). `evict_beyond` keeps the highest
iteration numbers (`ORDER BY iteration DESC`, `:284-286`), not the newest
rows; the memory cache evicts by insertion order (`:402-411`), a different
rule.

**Failure.** In a multi-turn chat SQLite keeps one row per step, but the page,
`/replay list` and the compare dropdowns show one card per iteration number.
"Iteration 1" means the newest turn's step on the agent's cache (web page,
web restore) and the oldest surviving row on a fresh recorder (gateway
`/replay 1` builds one per command, `graph.py:2270`; the web after a
restart). The same action restores different conversations by surface and
time, and the inspected detail may not be the state written. After a turn
with high iteration numbers (mission mode, long tool loops), each later
short turn's snapshot is deleted from SQLite as it is written; memory shows
it until a restart.

**Witnesses.** `test_tt01_iterations_restart_each_turn_and_collide`,
`test_tt01_retention_deletes_the_newest_snapshot_not_the_oldest`.

**Fix direction.** Stable identity: snapshot id (already a uuid) + turn
identity (`created_at` / user-turn index / reply `turn_id`) + iteration.
Restore, fork, detail and compare by snapshot id. Evict by capture order
(rowid/timestamp). List turns, then steps within a turn. One source of truth
(the database, or a write-through cache keyed by id).

**Gate.** Real multi-turn graph run: every step listed once; restore-by-id
writes the inspected state on every surface, before and after a restart;
eviction keeps the newest N. Negative control: `(thread, iteration)` keying
fails all three.

### TT-02 — P1 — A restore under a pending approval lets that card's Approve run a different, previously denied call

**Evidence.**
1. Restore is `aupdate_state(config, payload)` and nothing else: no registry
   settlement, lock, `is_turn_running` check or journal frame
   (`time_travel.py:841-854`; `replay_routes.py:203-236`;
   `graph.py:2258-2293`).
2. On a thread paused at a gate, LangGraph applies the update as the last
   writer (`supervisor`); the snapshot's `next_node="tool_worker"` yields
   `next == ('tool_worker',)`, no interrupt, and the snapshot's
   `tool_calls_pending` and `workspace_root`.
3. `close_turn` treats any non-empty `next` as paused
   (`turn_runtime.py:94-103`, `:320-330`), so the old gate row stays
   `pending` and its card stays live until the timeout watchdog orphan-settles
   it after `approval_timeout_seconds` (default 300 s,
   `hitl_timeout.py:244-300`).
4. The approve route checks only that the named gate's registry row is
   pending (`hitl_gate_bridge.py:68-102`); it treats `next` without an
   interrupt as resumable (`misc.py:638-640`), falls back to a gate-less
   `Command(resume={"approved": True, ...})` (`misc.py:773-779`), records
   the decision under the old gate (`:932-944`) and drives the graph.
5. The tool worker's `interrupt()` consumes that resume and approves every
   pending call (`graph_tool_worker.py:1317-1324`, `:1364`).

**Failure.** Turn 1 asks for `shell_exec rm -rf build`; the human denies.
Turn 2 asks for `file_write notes.md` and waits at gate A. The user rewinds to
turn 1's step. Gate A's card is still pending; the user clicks Approve.
`rm -rf build` runs in the snapshot's workspace root; `notes.md` is never
written; no card was shown for the command that ran. The effect journal does
not stop it (a denied call has no receipt). This defeats §30 B2.

**Witness.** `test_tt02_restore_under_a_pending_gate_lets_an_old_approval_run_another_call`.

**Fix direction.** Restore refuses, or first settles, every live gate on the
thread (`record_gate_decision(..., "superseded")` or a dedicated `rewound`
state, with the `hitl` journal frame). Restore only turn-boundary states
(snapshot after `respond`, or write `as_node="respond"` with
`tool_calls_pending=[]` and `next_node="end"`). Independently, the approve
route requires a live interrupt whose id equals the decided gate; `next`
without an interrupt answers 409 and never resumes; `build_resume_command`
keys security resumes by interrupt id.

**Gate.** Real-graph restore under a pending gate, then Approve and Deny: each
refuses or decides only the gate shown. Approve-route test with `next` and
no interrupt: 409. Negative control: today's route and restore execute the
denied call.

### TT-03 — P2 — "Restore" is not a rewind: the web reverts it, and the transcript, memory and scratchpad keep later turns **[x MEM-R10]**

**Evidence.** The restore route rewrites only the checkpoint
(`replay_routes.py:203-236`); chat store, turn journal and reply rows are
untouched. `build_turn_messages` prefers session history when it has at
least two more contentful turns than the checkpoint
(`agent/turn_input.py:1584-1592`); the SSE path passes
`fallback_history=session.messages` (`sse_chat/__init__.py:954-960`); the
gateway passes `None` (`graph.py:370-376`) and is not affected by this part.
`scratchpad` is a merge reducer (`state.py:273`), so an older scratchpad
merges into the newer one. Memory (episodes, facts, weekly summaries of the
rewound turns) is untouched and recalled. Contradicted claims:
`time-travel-and-replay.md:29` ("Later turns are gone"), catalog
`replay.about_restore` (`common.py:522-525`, "as if the later turns never
happened"), `replay.confirm_restore` (`common.py:1162-1165`, "Later turns
will be lost"), TUI "Next send continues from here"
(`kazma-tui/kazma_tui/chat.py:1106`), and the gateway reply "The
conversation has been rewound to that point" (`graph.py:2286`).

**Failure.** A web user rewinds three turns and gets a success toast; the page
still shows those turns and the next message re-feeds them to the model. A
one-turn rewind holds, so behaviour is inconsistent.

**Witnesses.** `test_tt03_web_restore_is_undone_by_the_next_message`,
`test_tt03_rewind_keeps_later_scratchpad_entries`.

**Fix direction.** Make restore a transcript operation too: mark or truncate
chat-store rows after the restored turn, emit a journal frame so open tabs
repaint, clear the scratchpad with `SCRATCHPAD_CLEAR` before applying, and
state in UI and docs that memory, files and messages already sent are not
rewound. Alternative: rename to "restore agent context" and drop the claim
that later turns are gone. Memory: decide together with MEM-R10 (forget the
rewound turns by provenance, or say plainly that memory is kept).

**Gate.** SSE-level: restore at least two turns back, send, assert the model
input lacks the later turns and the transcript shows the rewind. Real-graph
scratchpad test. Negative control: today's route.

### TT-04 — P2 — Restore and fork are not serialized with running turns or with each other **[x MEM-R10]**

**Evidence.** The web routes take no per-thread lock and never call
`active_turns.is_turn_running` (`replay_routes.py`). The approve route has
both (`misc.py:791-852`). The gateway's per-thread `cmd_lock`
(`graph.py:930-935`) is an in-process asyncio lock in the gateway handler;
it serializes gateway turns and commands but not web turns, and web restores
do not take it.

**Failure.** A restore during a running turn answers `{"ok": true}`; the
running Pregel loop then checkpoints its in-memory channels over it, so the
restore is silently lost. A restore between two node writes interleaves
with them.

**Witnesses.** `test_tt04_restore_during_a_running_turn_is_silently_lost`,
`test_tt04_restore_route_accepts_a_thread_with_a_running_turn`.

**Fix direction.** One per-thread checkpoint-mutation lock shared by turns,
approve, restore, fork, `/undo`, `/edit` and `/compact` on every transport;
409 `turn_running` (and `approval_pending`, TT-02) instead of writing.

**Gate.** Route test with a registered running turn expecting 409; real-graph
race test. Negative control: today's routes.

### TT-05 — P2 — Deleted and forgotten conversations live on in snapshots; "clear" is incomplete **[x MEM-L03, MEM-S02, MEM-L07]**

**Evidence.** Only the replay page's Delete and `/replay clear` delete
snapshots. Not chat delete (`sse_chat/__init__.py:1475-1496`), web or
gateway `/reset` (`:547-560`; `graph.py:938-986`), or memory forget / "Don't
remember this chat" (`memory/forget.py`). `/reset` keeping them is
documented (`slash-commands.md:107`); delete and forget keeping them is not.
Gateway `/replay clear` uses its own module-global recorder
(`slash_commands.py:145-168`, clear at `:555`): it clears SQLite but not the
agent recorder's cache, which the web page and web restore read. On the web
chat `/replay clear` clears nothing; it answers with a count and a link
(`sse_chat/_time_travel.py:35-63`). `scripts/scan_old_sessions.py` reads the
latest snapshot of every thread by default (`--source all`, `:340`;
`load_snapshots_sessions` at `:78`, used at `:379`) and re-extracts beliefs
under tenant `default` without the forget ledger (no `refuses_write`).
Snapshots are a full plaintext copy of agent state (system prompt, "About
me", recall blocks, tool results including file contents and secrets),
carried in universal backups (about two thirds of a backup by volume, per
the auditor) and in migration bundles.

**Failure.** A deleted or forgotten chat stays in `snapshots.db`, its backups
and export bundles for up to `retention_days`, or indefinitely with
`auto_maintain` off. On a gateway thread `/replay N` restores deleted content
into the live checkpoint, and the gateway projects it back into a web chat
row. A run of the scan script turns forgotten content back into facts.
`/replay clear` reports "Cleared N snapshot(s)" while the web page can still
show and restore them until a restart.

**Witnesses.** `test_tt05_gateway_clear_leaves_the_agent_cache_restorable`,
`test_tt05_delete_reset_forget_never_touch_snapshots` (source witness).

**Fix direction.** One `forget_thread_snapshots(thread_keys)` that purges the
store and every recorder cache, called by chat delete, forget/kept-out and
`/replay clear` on every surface; one recorder per process. The scan script
skips snapshots or checks `refuses_write` for every chat key. Consider
treating snapshots as observability data (short default retention,
`rebuilt`/not-backed-up disposition) or encrypting them. Say in the delete
dialog what is kept.

**Gate.** Delete, forget, or `/replay clear`, then no surface (web, gateway,
TUI, scan script) can list or restore the thread's steps. Negative control:
today's code.

### TT-06 — P2 — Snapshots have no byte bounds; growth is quadratic and the caps count entries only

**Evidence.** Every capture serializes the whole state (`time_travel.py:395-400`);
no per-snapshot, per-thread or total byte cap. `max_global_snapshots` bounds
only the in-memory dict (`:413-415`), while the docs call it "Newest
snapshots kept in total" (`time-travel-and-replay.md:71`). The cache holds up
to 2,000 full states by count. Mission mode allows up to 2,000 iterations per
turn (`state.py:416`); each is serialized and written, then all but 50
deleted. The listing route decodes every state to count messages
(`replay_routes.py:157-169`). `VACUUM` runs on every boot and daily
(`time_travel.py:744-754`, `:882-919`); `docs/KNOWN_GAPS.md:1498-1513`
measured 0.0% reclaimable on the live 288.1 MB file. A capture during
`VACUUM` waits up to the 5 s busy timeout, then is lost with a warning.

**Witnesses.** `test_tt06_snapshot_bytes_grow_quadratically_and_are_uncapped`,
`test_tt06_global_cap_does_not_bound_the_database`.

**Fix direction.** Store deltas or a LangGraph checkpoint reference plus a
summary; per-snapshot byte ceiling with explicit truncation; per-thread and
total byte budgets with oldest-first eviction; cap captures per turn (first,
last, every K-th); count messages at write time; `VACUUM` only when the
freelist ratio justifies it.

**Gate.** Capacity test (mission-length turn, many threads) asserting declared
byte budgets and stable RAM. Negative control: count-only caps.

### TT-12 — P2 — The tests cannot see restore semantics

**Evidence.** Every restore and fork test uses a fake graph whose
`aupdate_state` records its arguments (`test_snapshot_rewind_payload.py:32-38`,
`test_replay_router_getters.py:37-44`, `test_time_travel.py` for the
recorder). None builds a real `StateGraph`, so reducer merging, `next`/`as_node`
consequences, HITL interplay and transcript divergence never meet the code.
No test captures more than one turn on a thread. The global-cap test asserts
only on the memory dict (`test_audit_2026_09_04_wave5.py:123-133`).
`test_path_rewrite.py` has no JSON-encoded input. 77 tests pass with every
defect present.

**Fix direction.** A shared real-graph fixture (real `SupervisorState`,
product node names, a stub tool worker with `interrupt()`, a saver of each
supported kind) used by every restore, fork and approve test; run on SQLite
and Postgres savers in the existing Postgres job; each gate with a negative
control (§28). The TT witnesses, inverted, are the starting set.

### TT-07 — P3 — Fork defects

**Evidence.** The web fork writes the snapshot's raw graph messages into the
fork's chat row (`replay_routes.py:270-278`, `messages=` at `:277`): system
prompt, "About me", recall blocks, assistant tool-call shells, tool payloads.
The gateway fork projects them through `_convert_messages_to_dicts`
(`graph.py:2358`). Fork titles are English only (`replay_routes.py:276`
`"Fork (iter N)"`; `graph.py:2362` `"Fork of … (iter N)"`). The page's fork
gives no confirmation or link, only a toast with the raw thread id. The
gateway fork calls `record_thread_owner` (a settings write, `graph.py:2326`)
and `get_session_manager()` / `.put` (a chat-store write, `:2353`, `:2365`)
on the event loop, against §35; no current gate catches it. A failed owner
record is swallowed at DEBUG inside `record_thread_owner`
(`sessions/directory.py:141-142`), leaving an unowned fork on which gateway
approvals then refuse. `/fork 0` answers with the usage text
(`graph.py:2300-2301`).

**Witnesses.** `test_tt07_web_fork_writes_system_and_tool_messages_into_the_chat`,
`test_tt10_small_surface_defects`.

**Fix direction.** One fork routine for both mouths: projected transcript,
catalog title, owner recorded off the loop with failure surfaced; accept
iteration 0, or fork by snapshot id.

**Gate.** Fork route tests on stored roles and title language;
`record_thread_owner` added to `_LOOP_STALL_HELPERS`. Negative control: the
current web fork.

### TT-08 — P3 — Settings' "effective" snapshot cap reads an attribute that does not exist

**Evidence.** `settings.py:1010` reads `int(rec.max_snapshots)` (report: `1008-1009`);
`SnapshotRecorder` has only `_max_snapshots`. The `AttributeError` is
swallowed at DEBUG (`:1011-1012`) and the route falls back to kazma.yaml or
50. `settings_agent.js:741` compares the saved value with that, so after a
saved change and a restart "Restart required" never clears and Restart stays
enabled.

**Witness.** `test_tt08_settings_effective_cap_is_never_the_live_recorder`.

**Fix direction.** A `max_snapshots` property (and the global cap) on the
recorder; report effective retention and auto-maintain too.

**Gate.** Real route with a recorder built from a stored override. Negative
control: an attribute-less recorder.

### TT-09 — P3 — Migration does not rewrite Windows paths inside snapshot JSON **[x migration]**

**Evidence.** `rewrite_text` (`migration/path_rewrite.py:135-166`) matches
`C:\Users\…` with single backslashes; `state_json` stores the JSON-escaped
`C:\\Users\\…`. The importer applies the same byte-level rewrite to
`snapshots.state_json` and other JSON columns (`importer.py:46-75`: chat
messages, spool payloads, `metadata_json`, …).

**Failure.** After a Windows-source migration, snapshots keep the old
machine's paths, `workspace_root` included; a restore writes a root that does
not exist and resumes of the restored state fail closed. The report counts
zero changed rows and does not warn.

**Witness.** `test_tt09_windows_paths_inside_snapshot_json_are_not_rewritten`.

**Fix direction.** Also rewrite the JSON-escaped form of each source and
target, or parse JSON columns, and verify each JSON column still parses after
the rewrite. The non-snapshot JSON columns have the same gap (out of this
audit's scope).

**Gate.** Round trip in both directions with a JSON-encoded Windows source.
Negative control: today's `rewrite_text`.

### TT-10 — P3 — Documentation and UI claims that do not match behaviour

| Claim (location) | What the code does |
|---|---|
| `/replay list`, `<N>`, `compare`, `clear` and `/fork <N>` work "In any chat (web, Telegram, Discord, Slack)" (`time-travel-and-replay.md:24-32`; `slash-commands.md:205-207`) | The web chat answers every `/replay` and `/fork` with a count and a link; it does not restore, compare or clear. |
| "LRU-capped per-thread at 50" (`AGENTS.md:611`); "Newest snapshots kept per conversation" (`time-travel-and-replay.md:70`) | Retention keeps the highest iteration numbers (TT-01). |
| `max_global_snapshots` = "Newest snapshots kept in total" (`time-travel-and-replay.md:71`) | Bounds memory only (TT-06). |
| "The in-memory store is the source of truth for the current session" (`replay.about_note`, `common.py:518-521`) | The gateway never reads it, and the two can disagree (TT-01, TT-05). |
| "Snapshots are captured after each agent turn" (`replay.no_snapshots`, `common.py:1114-1117`) | Captured after each supervisor iteration (`graph_builder.py:161-184`). |
| `replay_routes.py:13-16` docstring cites `tests/test_replay_route_ownership.py` | That file does not exist; the gates are `test_thread_route_policy.py` and `test_thread_ownership_routes.py`. |
| The web `/replay` reply | English only on an Arabic UI (`sse_chat/_time_travel.py:35-63`, no `current_language`, unlike `discovery_reply`); "step(s)" instead of a plural form. |
| `replay.js:160-161` | A 403 or 500 is shown as "No snapshots for this thread yet". |

**Witness.** `test_tt10_small_surface_defects`.

**Fix.** Correct docs, catalog and AGENTS §12 together with TT-01/TT-03;
localize the web reply; show errors as errors. **Gate.** Existing docs/i18n
gates plus a page test where the snapshots route answers 500 and an error
state appears.

### TT-11 — P3 — Observability: failures read as success or as "nothing saved"; restores leave no audit trail

**Evidence.** A failed SQLite write in `capture()` logs a WARNING and still
returns the record (`time_travel.py:417-429`); the supervisor stamps
`snapshot_id` (`graph_builder.py:181-183`) and the stream announces a
`snapshot` event for a step never stored. `get_snapshot`, `list_snapshots`
and `list_distinct_threads` swallow SQLite errors at DEBUG (`:444-450`,
`:469-476`, `:512-516`), so an unreadable or corrupt store shows as "No steps
of this chat have been saved yet" or a 404, and the SSE handler's
`except sqlite3.Error` never fires. Restore and fork write INFO logs only: no
durable record of actor, source thread, snapshot, target or prior checkpoint
id. No metric, no ops alert (including for "prune did NOT stick", logged at
ERROR), no firing-ledger signature.

**Witnesses.** `test_tt11_a_failed_snapshot_write_is_still_announced`,
`test_tt11_an_unreadable_store_reads_as_no_saved_steps`.

**Fix direction.** Return `None` (no announcement) when the durable write
fails; count failures (`kazma_time_travel_capture_total{result}`); raise
store errors to the routes as 503; one audit row per restore or fork (and per
gate it settles); an ops alert for a prune that did not stick; a ledger
signature for the daily maintenance.

**Gate.** Fault injection (locked DB, corrupt DB): honest 503, no `snapshot`
frame. Negative control: today's swallow-at-DEBUG.

## 5. Cross-area overlaps

| TT finding | Other audit (same folder) | What overlaps | Handling |
|---|---|---|---|
| TT-03 | `memory-recall.md` MEM-R10 (P2) | MEM-R10: `_handle_undo` (`graph.py:2118`), `_handle_edit` (`:2177`), `slash_commands._cmd_replay` and the web restore (`replay_routes`, `apply_snapshot_to_thread`) never touch `memory.forget`; its witness enumerates seven state rewriters, none touches memory. TT-03 covers the transcript and scratchpad half. | Decide the memory half once: forget the rewound turns by provenance (MEM-R10's fix), or state in the UI that memory is kept (TT-03's alternative). One test can serve both gates. |
| TT-04 | `memory-recall.md` MEM-R10 | MEM-R10's list of seven state rewriters is the set the TT-04 lock must cover (`/undo`, `/edit`, `/compact` were not audited here). | Use that list as the gate's enumeration source. |
| TT-05 | `memory-lifecycle.md` MEM-L03 (P1) | MEM-L03 lists "LangGraph checkpoints and time-travel snapshots holding earlier recall blocks" among copies forget does not reach, "not verified in depth". TT-05 verifies the snapshot half. | Snapshots belong in MEM-L03's forget fan-out registry. |
| TT-05 | `memory-surfaces.md` MEM-S02 (P1) | MEM-S02 proposes a forget-contract store registry; its list does not name `snapshots.db` or checkpoints. | Add `snapshots.db` (purge or declared retention) to that registry. |
| TT-05 | `memory-lifecycle.md` MEM-L07 (P2) | MEM-L07: restoring `memory_state.db` undoes forgets; it proposes a forget journal replayed after any restore or migration import. Merge note (not stated in either report): once TT-05 purges snapshots, a restored or imported `snapshots.db` raises the same question. | Have the journal replay cover snapshots, or state the backup/bundle disposition. |
| TT-05 (scan script) | memory audits | The auditor marks the `scan_old_sessions.py` forget-ledger gap as belonging to the memory audits. | Fix once; TT-05's gate includes the script. |
| TT-09 | migration (no migration report in this folder) | The JSON-escaping gap also affects chat messages, spool payloads and memory metadata columns. | Fix the class in `path_rewrite.py`; TT-09's gate covers snapshots, the class gate covers every JSON column `importer.py` rewrites. |

## 6. AGENTS.md claims that do not match the code

| Section (line) | Claim | What the code does |
|---|---|---|
| §12 header (`AGENTS.md:611`) | "LRU-capped per-thread at 50" | SQLite keeps the 50 highest iteration numbers (`time_travel.py:284-286`); the memory cache evicts by insertion order (`:402-411`). Neither keeps the newest steps of a multi-turn thread (TT-01). |
| §12 A (`AGENTS.md:613-620`) | `SnapshotRecorder` "created once per agent" and passed to the build sites | True for the agent, but the gateway builds a fresh recorder per `/replay` and `/fork` command (`graph.py:2270`, `:2315`) and the slash resolver keeps a module-global one (`slash_commands.py:145-168`). Their caches disagree (TT-01, TT-05). The CLI site `kazma_core/cli/ask.py:539` passes the recorder but is not listed. |
| §12 C (`AGENTS.md:629-633`) | `/replay <n>` writes the snapshot back "rewinding the live conversation. Same pattern as `/undo`." | Only the checkpoint changes. The web transcript is re-fed on the next turn (`turn_input.py:1584-1592`), the scratchpad merges (`state.py:273`), memory is untouched (TT-03, MEM-R10); on a paused thread runnable work is left behind (TT-02). |
| §12 D (`AGENTS.md:640-643`) | The resolver handles "read-only subcommands (`list`, `compare`, `clear`)" | `clear` deletes rows (`slash_commands.py:555`) through the resolver's own recorder, and the agent cache keeps serving them (TT-05). |
| §12 F (`AGENTS.md:652-658`) | "A fork's chat is saved in a thread, and a failure to save it is a WARNING." | True for the web route (`replay_routes.py:280-285`). The gateway fork saves on the loop and logs the failure at DEBUG (`graph.py:2365-2367`); its owner record fails at DEBUG (`sessions/directory.py:141-142`) (TT-07). |
| §18 B (`AGENTS.md:1495-1505`) | `snapshots.state_json` rewritten "with both forward-slash and backslash variants handled" | JSON-escaped backslashes are not matched (TT-09 witness: `n == 0`, `changed == 0`). |
| §30 B2 (`AGENTS.md:3213-3231`) | "A decision names its gate, and the server checks." | The check is against the registry row. With `next` and no interrupt the route resumes (`misc.py:638-640`) with a gate-less security command (`misc.py:773-779`); after a restore, pending gate A decides a different call (TT-02). |
| §30 E (`AGENTS.md:3276-3279`) | "Every crash window has one behavior"; `close_turn` orphans pending rows "whose checkpoint is NOT paused" in seconds | A restore produces `next` without an interrupt, which `_snapshot_paused` counts as paused (`turn_runtime.py:94-103`); the stale row stays live until the 300 s timeout watchdog (TT-02). |
| §31 A/C/D (`AGENTS.md:3363`, `:3375`, `:3379`) | The journal is the source of truth; `close_turn` is the only closer; transports are mouths | Restore changes a thread's turn state with no journal frame or close, so open tabs are not repainted (TT-03, TT-11). |
| §15 I (`AGENTS.md:1102-1104`) | "every path that rebuilds memory must respect" a forget | `scan_old_sessions.py` re-extracts beliefs from snapshots without the ledger, and forget does not reach snapshots (TT-05). |
| §35 (`AGENTS.md:3811`) | No blocking I/O inline in `async def`; loop-stall helpers gated | Gateway fork's settings and chat-store writes run on the loop (`graph.py:2326`, `:2353`, `:2365`); no gate sees them (TT-07). |

Docs and catalog mismatches are in TT-10.

## 7. Staged path to industrial readiness

The report defines three stages. TT-12 is not placed in a stage by the
report; its real-graph fixture is a precondition of the Stage 1 exit gate
("proven on a real graph with SQLite and Postgres savers"), so the tracker
puts it first.

### Stage 1 — Make restore safe (TT-02, TT-04)

One `rewind_thread(thread, snapshot_id, actor)` service called by every
surface. Under one per-thread mutation lock it:

1. refuses while a turn is running;
2. refuses while any gate is pending, or supersedes it through
   `record_gate_decision`;
3. writes a turn-boundary state only, so nothing is left runnable;
4. clears and replaces the scratchpad;
5. emits a journal frame;
6. writes an audit row.

Separately, the approve route matches a **live** interrupt id to the decided
gate.

**Exit gate.** No Approve or Deny on any transport after a restore can run a
call the human did not see. Restore during a run or a pause is refused
deterministically. Proven on a real graph with SQLite and Postgres savers,
with negative controls.

### Stage 2 — Make it correct (TT-01, TT-03, TT-05)

- Re-key snapshots by snapshot id and turn identity; one recorder per
  process; the database as source of truth.
- Consider basing restore on LangGraph's checkpoint history
  (`aget_state_history` + `checkpoint_id`), already bounded by §41
  retention, keeping `snapshots.db` as an optional index or observability
  view.
- The transcript follows the rewind; the UI states what is not rewound
  (memory, files, messages already sent).
- Purge snapshots on delete, forget and clear across every surface and
  script.

**Exit gate.** A multi-turn, multi-surface, restart-spanning test shows the
same snapshot id yields the same state everywhere. The next turn's model
input matches the rewound transcript. A deleted or forgotten chat cannot be
listed, restored or re-ingested.

### Stage 3 — Bound, observe and document (TT-06 to TT-11)

- Byte budgets per snapshot, per thread and in total, with eviction; capped
  captures per turn.
- Metrics and alerts; store failures surface as 503s.
- Fix the Settings readout and the migration JSON rewrite.
- Docs, catalog and AGENTS §12 match measured behaviour, in both languages.

**Exit gate.** A mission-length soak (2,000 iterations, many threads) stays
within the declared disk and RAM budgets. Fault injection shows honest
errors. Each docs claim is checked by a test.

## 8. Limits

- One auditor; scores are that auditor's judgments.
- Postgres saver: restore and `next` semantics shown on `InMemorySaver`
  only.
- TT-02 mirrors the approve route and tool worker by cited branches; the
  registry state of gate A is inferred from code.
- No live surfaces, no live `snapshots.db` measurement, no load or soak.
- Effect-journal fencing of completed calls after a restore rests on code
  reading.
- Out of scope, reported only where they touch Time Travel: migration's
  JSON-escaping gap in non-snapshot columns; the scan script's forget-ledger
  gap.
- `/undo`, `/edit` and `/compact` not audited; the TT-04 lock should cover
  them.
- This merge ran nothing. It re-read the code at the lines listed in 2.6 on
  the 2026-10-13 working tree; line numbers can drift with later edits.
