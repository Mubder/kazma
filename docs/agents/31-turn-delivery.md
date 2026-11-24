<!-- AGENTS.md, piece 31-turn-delivery. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 31. Turn Delivery V2 — journal is SoT, client projects

Web/SSE/WS chat is **event-sourced delivery**, not a second brain.
Plan: `docs/plans/TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md` (P0–P4 shipped).
HITL sits on this as one projection (§30).

**A. `close_turn` is the only closer.** `kazma_ui/turn_runtime.py:close_turn`
decides whether a turn is open, waiting on HITL, or done. A pending
registry row keeps the turn **open**. Absence of a row is an unregistered
pending gate (backfill), never an inferred Approved stamp. Do not add a
client-side `forceEndTurn` wall-clock that marks CoT Done while the server
is still working. Its reply text is the RUNNING turn's
(`_helpers._this_turn_assistant_text`: after the last user message): a turn
cancelled before it said anything was saved with the previous turn's answer
(live 2026-10-20, `/abort` on a history request stored the swarm summary
before it). Gate: `test_an_aborted_turn_is_never_saved_with_the_previous_answer`
(`tests/test_turn_runtime.py`, the whole chat's last text as the control).
**An abort closes its turn** (`turn_runtime.close_aborted_turn`, both abort
paths): the cancelled run saved its row open and nothing closed it, so the
liveness watchdog paged `turn.stuck_open` ten minutes later (2026-10-20). A
partial answer is kept; an empty turn gets the cancellation note.
**An aborted request is answered "cancelled"** (`steer.abort_messages`, both
abort paths): the abort's system note alone is hoisted to the head by the
OpenAI-compatible path, the request was left unanswered, and the NEXT turn
wrote the aborted 2,000-word history (live 2026-10-20). Gate:
`tests/test_abort_leaves_no_pending_request.py` (the turn rebuild and the
hoist; the note alone as the negative control).

**B. The client projects; it does not author.** `chat.js` paints from the
TurnDocument / `_serverGateViews`. Token deltas APPEND; only `turn_complete`
has replace semantics (§29F duplicated-prefix invariant). Do not restore
`tokenAccum` dual-paint (T-4).

**C. Catch-up is resume, not replay of deltas.** Refresh / reconnect reads
the journal + gate status. Do not re-stream content the bubble already
appended.

**D. Transports are mouths.** SSE (`sse_chat/`) and the gateway graph
(`agent_handler/graph.py`) run turns and close through the same completion
contract. A new mouth that invents its own “Done” is a delivery bug.
**The WebSocket (`ws_chat.py`) runs no turn and takes no turn control**
(2026-09-30, AUD-026): send and approve are SSE, stop / steer / abort are
HTTP, and the socket answers those five actions with a refusal naming the
route (`_HTTP_ROUTE_FOR`). It carried a second copy of each -- a graph client
behind `KAZMA_WS_GRAPH` and stop/steer/abort twins -- that no shipped client
sent to, and the copies drifted (its approve lacked the gate-identity check
and dropped `approved_ids`, which fails open). What it still does: the
pending HITL card on connect, the cursor resume, `ping`, and clearing a
watched turn's orphan clock. The chat store sends nothing on it. Gate:
`tests/test_ws_chat_is_telemetry_only.py` (behaviour through the router with
a graph double; no graph-running call in the module; nothing reads the old
switch; the store sends no frame).

**Both browser mouths paint every journal frame.** The turn broker stamps
one frame and fans it to the SSE stream AND the WebSocket, and a tab that
only watches a turn (another window, a phone) may have the socket alone. So
every frame type `streaming.js` `dispatch` handles needs a case in
`agentStore.handleSocketMessage`, painted by the same functions
(`chat.applyJournalFrame`, `chat.ingestGateViews`; the attach callbacks use
them too). Live 2026-09-26 the socket had no case for `hitl` -- the approve
route's approved/denied -- nor the tool frames, and dropped `done`'s gate
views: a watching tab never saw the other tab's approval settle, and its
block stayed on "Approval required" under the answer. Gate:
`tests/test_journal_frame_parity.py` (reads both dispatchers); the
browser net is `test_a_watching_tab_shows_each_turn_in_its_own_block
[socket-only]`.

**E. Presentation is governed by one plan.**
`docs/plans/UNIFIED_TURN_BLOCK.md` is the binding contract for how a Web
chat turn is presented: one persistent block per turn, an integrated status
header, one collapsed thoughts disclosure whose expansion events never
touch, ONE approval group with a keyed row per gate, and one answer region.
It supersedes in part `COT_AND_THOUGHTS.md` (separate live bar, auto-open
thoughts), `TURN_RENDER_V2_KEYED_SLOTS.md` (flat per-gate top-level slots)
and `HITL_VIEW_MODEL.md` (layouts that require separate cards). Delivery
ordering (§31) and gate authority (§30) are preserved unchanged — any
protocol extension must update those contracts explicitly.

Baseline, evidence and known gaps: `UNIFIED_TURN_BLOCK_PHASE0.md` through
`_PHASE5.md` in the same directory. Each phase report states what it does
NOT claim, and `_PHASE5.md` §6 lists what the release does not have; read
those before claiming any of it.

Shipped: durable tool activity and a document revision (P1), the in-block
header, reader-owned thoughts fold and one answer authority (P2), one
approval group with keyed rows (P3), gate-identity enforcement on the
approval route and the full acceptance matrix (P4), the removal of the
last second DOM writer plus release evidence (P5).

**The lifecycle job is green on Linux and is a required check.** It was
RED from Phase 3 to Phase 5 and nobody noticed, because every phase report
was written from Windows runs by hand (`_PHASE5.md` §6). Since 2026-09-27
`main` is protected by the ruleset `KazmaLatestRule`
(`docs/docs/ops/branch-protection.md`): every CI job, this one included, is
a required check (21 checks since 2026-10-15), deletions and force pushes are blocked, and
the repository admin bypasses -- the owner's direct pushes, and the
agent's with the owner's credentials, still land. So a red required check
on `main` is not a blocked push: it is a failing build, fixed at once. The
metrics bot no longer commits to `main` (GitHub will not let the Actions
app bypass on a personal-account repository); refresh `METRICS.md` with
`scripts/generate_metrics.py --write` before a push. The website gets its
numbers from `metrics.json`, through the ONE open pull request
`scripts/sync_site_metrics.py` keeps on `Mubder/KazmaAI`, daily
(`docs/docs/ops/website-metrics.md`). It is read back, never assumed: the
old workflow "updated" a merged pull request for two months, all green.

Load-bearing rules:

- **One status surface per turn, inside the turn.** `#live-task-card` is
  deleted. A page-level status element is what forced a second phase
  machine, a second clock and a second recovery loop. The header derives
  from `modules/turn_presentation.js`, a pure function — do not tell it a
  phase.
- **The fold belongs to the reader.** `modules/turn_preferences.js` is the
  only writer of disclosure state, and only from a gesture. Any map keyed
  by turn id must be promoted when the turn is renamed off `'live'`, like
  `turn_view.js:promote` — the preference store was not, and the fold shut
  itself a second into every turn.
- **Gates are ROWS in one region above the answer**, in ask order. Never
  order gates by state: that is what moved the answer when one settled.
- **One count.** `turn_presentation.gateRows()` is what both the header
  and the group count, so the two cannot contradict each other.
- **One writer, and it is enforced.** `turn_view.js` performs the single
  ordering pass; `chat.js` builds regions and hands them over, it never
  inserts them. The pre-V2 fallback painter (`ensureProgressPanel`) is
  deleted — it could only run when the projector module was missing, and
  in that state there is no answer text to render at all, so it could
  only ever produce a half-rendered turn.
  `tests/test_turn_render_boundary.py` fails if either the retired bar
  markup or a second turn-content writer comes back.
- **A decision names its gate.** See §30 B2: `/api/approve` verifies the
  body's `interrupt_id` against the registry before resuming. A retried
  Approve used to decide whichever question the graph had reached by
  then.
- **Every journaled frame names its turn.** `TurnBroker.emit` stamps
  `data.turn_id` (`delivery._with_turn_id`: the emitting task's bound turn,
  else the thread's open reply turn; an existing id is kept, a frame between
  turns stays unnamed). The client's first named frame of a turn painted
  under `'live'` ADOPTS that document (`applyTurnEvent` -> `_retagDoc`) --
  never a fresh one, which would repaint the bubble from one frame.
  `tests/test_frames_name_their_turn.py`.
- **The stream's frames are documented as they are built** (2026-10-01).
  `docs/docs/guide/api-and-extension-points.md` §3 lists every `event:` with
  when it is sent and its fields, and the `sse_chat` package docstring the
  same list. `tests/test_sse_event_contract.py` reads the code (every
  `emit_j` / `_sse_frame` / `_journal_fast_path` call and every journal
  write, with its literal payload keys) and fails on a frame or field
  either is missing, a row no code sends, or a frame the browser's
  `dispatch` does not handle. The table had kept `tool`/`args`/`is_error`,
  `cost_usd` and an error `message` that nothing sent.
- **A `done` frame carries the answer** (2026-09-28). The client closes a
  turn from `done.content`; the instant replies (`/replay`, `/research`
  usage, `/reset`, `/compact`, `/swarm` usage) sent their text in a token
  frame only, and the header said "thinking" until the reconciler's next
  read, 6-10 s later. Every literal `done` frame in `sse_chat/` carries
  `content` (`tests/test_instant_reply_frames.py`; browser:
  `tests/e2e/test_instant_reply_closes.py`). A journaled instant reply also
  carries `capacity`: it is stored as its own turn, so it is never replayed,
  and a `done` with content would otherwise push to every device.
- **A stored row field is named once, beside its writer.** The history
  route's two serializers (the row list and the checkpoint-hydrate merge)
  are whitelists; they pass `reply_sink.CLIENT_ROW_FIELDS`, and a field kept
  for the server goes in `SERVER_ROW_FIELDS` with its reason.
  `tests/test_history_row_fields.py` enumerates every field `reply_sink`
  stores from its source and reads a full row back through the real route
  (the revision, the usage and the close time were each dropped by a
  whitelist nobody updated). The meta line under a bubble has one writer,
  `_paintMetaTail` in `chat.js`: callers set fields, never `textContent`
  (that flattened the `<time>` element). A terminal frame's stats reach it
  through `_paintTurnStatsFor` from BOTH mouths (the socket bridge
  `applyTurnStats` updated only the badges, so a watching tab never showed
  the line). A reply shows `closed_at` — its delivery time, set once by
  `_write_lifecycle` on the closing write.
- **The memory a turn was shown is a stored row** (2026-09-27). The
  supervisor's `memory_explain` payload becomes the turn's `memory` part
  (`turn_document.memory_part` / `memoryPartOf`, shared fixture
  `memory_used.json`), committed before the frame is published
  (`_streaming._note_memory`, persist-then-publish like tool rows) and drawn
  by `_activityRowsHtml` through `_memoryRowHtml` (never running) from both
  mouths (`applyJournalFrame('memory_explain')`). The "Memory context" panel
  it replaces had drawn nothing since Phase 5 deleted its markup (2026-09-20);
  its frame was never stored either. Gates: `tests/test_turn_memory_row.py`,
  `tests/js/test_memory_row.js`.
- **A step spins only when it says `running`.** Gate rows carry `info`;
  `_activityRowsHtml` drew every status row without running/failed/done as
  running, so decided gates spun forever (`test_sequential_allow_tool_in_one_bubble`
  asserts nothing spins on a finished four-gate turn, live and reloaded).

Sequential approval is proven at the lifecycle level by
`tests/e2e/test_unified_turn_app_graph.py` and in a browser by
`tests/e2e/test_unified_turn_browser.py`, which claims HITL_VIEW_MODEL.md
Playwright 1 and 4 — open since the 2026-09-19 F0 spike. Both run in the
`unified-turn-lifecycle` CI job.

**A turn's numbers are the turn's, not the segment's.** A turn that pauses
for approval runs as several graph segments. The done frame's tokens and
cost are the sum of the per-call ledger for the turn id
(`llm_ledger.turn_usage`; every row carries `turn_id`, bound per turn in
`_stream_langgraph_events`), its duration runs from the question
(`kazma_ui/turn_usage.turn_started_epoch`), and the session is charged only
the segment's calls. A new LLM call site inside a turn records into the
ledger (the respond synthesis did not, 2026-09-26). Gate:
`tests/test_turn_usage.py` (through the real streamer).

**A turn's time limit is one rule on every surface**
(`kazma_core/agent/turn_budget.py`, 2026-10-15; full audit 2026-09-30: the
chat apps had `KAZMA_TURN_TIMEOUT_SECONDS`, the web none, the agent runner a
600 s copy, an approval's resume no limit anywhere). `resolve_turn_budget` is
the one answer: a chat with `/long` active has none (`long_task` defines no
wall-clock budget; its step budget and the mission's hard wall still bound
it), else the variable when set, else `agent.turn_timeout_seconds` (Settings
-> System), else none; `0` is none. It bounds each RUNNING segment: a new
message or an approval's resume, until it answers or pauses -- the wait for
an approval is never counted, and the non-stop envelope's recovery runs
inside it. Every graph run sits in `turn_segment_budget` (`run_agent_turn`,
`turn_runtime.invoke_turn`, the SSE pump, in the task that reads the graph);
past it the run is cancelled as Stop cancels it, the checkpoint gets the
honest reply as the turn's last message (`turn_failed`, nothing pending, as
`respond`), so `close_turn`, the `done` frame's `content`, the gateway's
reply and a reload all agree, and `turn.timed_out` is paged once. Only
`/compact` runs without it (`budget=False`, declared). Gates:
`tests/test_turn_budget.py` (one reader of the variable and the setting;
every graph run inside the budget; each with its negative control),
`tests/e2e/test_turn_time_limit.py` (the real route; the old pump as the
control).

**A turn's tokens are bounded too, and so is a day's spend**
(`kazma_core/agent/turn_token_budget.py`, plan E1, 2026-11-05; the owner's
choice: 500,000 tokens, no daily limit). `agent.turn_token_budget` (Settings
-> System; 0 is none; `/long` active is none) counts every model call of the
turn -- the supervisor's, its failover, the empty-answer nudge -- into
`SupervisorState.turn_tokens`; from 70 % each call carries a call-local note
(turn-note kind `budget_check`, never in the checkpoint), and at the limit the
supervisor goes to RESPOND with `turn_budget_stopped` and says what it has and
how to go on, in the user's language; `respond_node` skips its synthesis then,
as on `turn_failed`. A continuation reply (`long_task.is_continuation_reply`,
which reads Arabic without its vowel marks: the stop asks for "كمل" and only
the shadda spelling matched) grants half the budget again
(`turn_token_budget_granted`); any other message starts a fresh budget.
`agent.daily_cost_limit_usd` (off by default) refuses a turn before any model
call once today's ledger spend (`llm_ledger.usage_since`, from the operator's
local midnight) reaches it, and raises `cost.daily_limit` once a day at 80 %;
a spend the ledger cannot measure refuses nothing and says so once. Both
reads run off the loop and ride out a store that cannot answer
(`store_errors()`). Settings shows both values (`GET
/api/settings/turn-spending`), the Dashboard today's spend against the limit
(admin only). Gate: `tests/test_turn_token_budget.py` (real graphs: the note,
the stop, a continuation, a fresh message, the refusal; the supervisor without
the limit and the old grant as negative controls).

**A turn a restart cut off continues** (`kazma_ui/turn_resume.py`,
resilience plan C1). At start (30 s in, once the chat apps connect), every
chat whose newest checkpoint, written in the last 30 minutes, stops half way
with no approval waiting is a cut turn. A safe one (no tool of the turn
whose receipt has no result: `agent.effect_journal`) continues by itself: a
web turn into its journal (`_drive_graph_to_journal`), a chat app's back to
its chat with a line saying so (`make_continue_resumer`, in their language).
One whose tool effect is unknown waits for the owner: listed by
`runs_chat_turn` (native `cut`) with **Continue** and **Close**; Continue
resumes it, and the effect journal still refuses to repeat the unknown call.
The listing reads every checkpoint first and each state after (SQLite's
saver holds its connection while it lists: reading a state inside the
listing waited for good). It lists through the `CheckpointManager` the app
hands it, which delegated no `alist` until 2026-11-06: the pass failed with
NotImplementedError on every live boot (its tests used the bare saver).
Every product `BaseCheckpointSaver` subclass implements the async calls a
graph, a history, a listing and a delete make
(`tests/test_checkpointer_wrappers.py`). Gate: `tests/test_turn_resume.py`
(a real graph and SQLite saver, and the manager over it; no start pass, and
the manager without its listing, as negative controls).

**A plan feeds only the running turn.** `_paintTextSlot` paints every turn,
finished ones on every reload; plan ingestion there logged progress on the
live turn and painted a "Kazma is thinking…" header under a finished answer
(`_isRunningDoc`; `tests/e2e/test_plan_fence_reload.py`).

Cross-language turn projection is locked by shared fixtures under
`tests/fixtures/unified_turn/` — do NOT add a Python example and a
JavaScript example for the same rule; that is how `legacy_turn_id` came to
mint a different id in each language with both suites green.

The same job carries the release evidence: Playwright traces and
screenshots on failure, a recording of the four-gate turn either way, and
a build-identity file written before anything runs so a failed job still
says which build failed. It uploads `test-artifacts/unified-turn`.

**Delivery over a proxy that re-chunks (2026-09-26).** Behind Cloudflare
Tunnel the live chat dropped tool rows and approval cards, reverted Stop to
Send in seconds, and threw `Cannot read properties of null (reading
'tool_name')`; loopback delivers frames whole, so every test passed. Rules:

- **One SSE reader, state per STREAM.** `KazmaStream.createSseParser`
  (`streaming.js`) keeps a half-read frame until its blank line, whatever
  the network cut; it held event/data/id per network READ, lost cut frames
  and glued their orphaned data onto the next frame. A handler that throws or
  data that is not JSON costs that frame only (`onFrameError`; chat.js
  resyncs once) -- it used to stop the reader for good. Gates:
  `tests/js/test_sse_parser.js` (every cut position, CR/LF/CRLF, a copy of
  the old reader as the negative control) and
  `tests/e2e/test_chunked_stream_browser.py`, which wraps the app in
  `RechunkedStreams` (1-40 byte pieces). A browser test that only ever runs
  on loopback does not test delivery.
- **`replay` means history, stamped per call site.**
  `_frame_from_journaled(frame, replay=...)` and
  `_sse_attach_stream(..., replay_is_history=...)` have no default. The stamp
  was unconditional, so the live tail -- which is how a sent turn reaches its
  own tab since the journal drive -- labelled a new approval history, the
  client refused it (correctly), and the card waited for the reconciler.
- **Other tabs learn about a question.** The send journals a `user_message`
  frame (content, turn_id, `client_msg_id`) BEFORE capturing the head, so
  the sender's stream never carries it and its WebSocket drops it by id;
  `chat.beginObservedTurn` adds the user row in the other tabs. Without it a
  watching tab painted each new turn into the previous turn's block. It is in
  `REPLAY_SKIP_TYPES`: a reload reads the row from the store.
- **A page attaches from what it has READ**, on either mouth
  (`_pageDeliveryCursor`: the WebSocket tracker, else the persisted cursor).
  `_lastSeqSeen` follows SSE only and no terminal callback records a seq, so
  attaches replayed from 0 or from before the previous turn's end and flipped
  finished blocks back to "working".
- **A gap resumes from the head the server names, on EVERY cursor**
  (`KazmaDeliveryCursor.adoptHead`, 2026-10-16), from both mouths: the SSE
  `resync` (`chat.js` `_adoptJournalHead`) and the socket's `resumed {gap}`
  (`agentStore.js`) save the head as the session's cursor and move the
  socket tracker to it. The journal restarts at seq 1 on every server
  restart and after an hour idle, so the cursor a browser saved is AHEAD of
  it; the gap handler cleared only `_lastSeqSeen`, the saved cursor won the
  next attach's `max()`, the same gap came back until the reopen budget ran
  out (looping while paused, where it is waived) -- live, a running turn and
  its approval showed dead after a trip to Memory, a refresh did not help,
  and the watchdog auto-denied the approval. The server logs every gap
  (`[delivery] ... cursor not in the journal`). Gates:
  `tests/test_delivery_cursor_gap.py` (the old handlers as the negative
  control), `tests/e2e/test_return_to_running_turn.py` (a stale saved
  cursor, by sidebar and by refresh, running and paused; fails on the old
  code).
- **Only an ENDED turn may be healed from the checkpoint.**
  `_checkpoint_backfill_unanswered` returns early while the thread's turn
  runs, is paused, or its snapshot has a `next` node; it had written a paused
  turn's narration as a finished reply on the page's own `/messages` poll.
- **Token, tool and status frames name their turn** since 2026-09-26 (the
  load-bearing rule "Every journaled frame names its turn" above). Before
  that only done/turn_complete/hitl did, and every client filed the rest
  under "the current turn"; the rules in this list are what kept that guess
  right, and they still hold.


