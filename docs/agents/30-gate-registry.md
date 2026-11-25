<!-- AGENTS.md, piece 30-gate-registry. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 30. HITL Gate Registry (`kazma_core/safety/hitl_gates.py`) — one gate, one row, one truth

Born from the 2026-09-01 chat-card incident chain (ghost cards, pre-approved
stamps, second question hidden on the dashboard). Plan SoT:
`docs/plans/HITL_GATE_REGISTRY_PLAN.md`. Every HITL approval across ALL FOUR
mechanisms (graph interrupt, swarm bus, pipeline checkpoints, semantic cards)
is one row in `kazma-data/hitl_gates.db` with a strict CAS state machine:
`pending → claimed → resuming → settled` (+ `timeout`/`superseded`/`error`).
Kill-switch `KAZMA_GATE_REGISTRY=0` degrades to a **thin execution
fallback**: a live checkpoint interrupt is pending (live card), never an
inferred Approved stamp. That is not a second decision author.

**A. Decision truth vs execution truth (the split is load-bearing).**
The registry owns the DECISION (was this gate answered, by whom); the
LangGraph checkpoint owns EXECUTION (is the graph actually paused). Readers
consult the registry (`hitl_thread_status`, `close_turn`,
`/api/pending-approvals`, chat.js `_serverGateViews` while
`_serverGatesAuth`). Paused + no covering
row is an **unregistered pending gate**: backfill from the snapshot and
keep the turn open — never treat absence as "no question". Auto-deny
uses the registry for *which* is pending and the checkpoint for *how*
(stale row must not resume nothing).

**B. Every transition is a single CAS UPDATE.** Zero rows affected ⇒
`TransitionConflict` carrying the row's actual state — that IS the 409 body.
Idempotent same-decision re-claim returns the row (200 semantics). `pending`
is the ONLY state a card renders live buttons for; the client never infers a
claim.

**B2. A decision names its gate, and the server checks.** `/api/approve`
verifies the body's `interrupt_id` against the registry BEFORE resuming
(`_gate_not_pending`, `routes_direct/misc.py`). Only a `pending` row may
be resumed; `claimed`/`resuming`/terminal answer 409 `not_pending` with
the server's actual view, and a row owned by another thread answers 409
`foreign`. The route resumes only a LIVE approval interrupt
(`_live_hitl_interrupt`: a checkpoint with a next step and no interrupt is
not paused, 409), keyed by that interrupt's id, and a gate the registry
knows that is not the live interrupt answers 409 (`gate_names_interrupt`);
the gate-less "security resume" fallback is gone on the web and the
gateway (2026-10-14, Time Travel audit TT-02: after a restore wrote a
mid-turn step, a still-live card resumed it and ran a call the human had
denied). A missing row or id can still reach the live interrupt
for legacy dashboard/TUI/platform clients, but execution now requires
`record_gate_decision(..., require_durable=True)` (2026-10-05). It backfills
the verified gate, strictly claims it once and records resuming before any
projection or drive. Registry off/unreadable answers 503 and stays paused;
a repeated identical claim answers 409. Legacy `claim_gate` idempotency is
retained only for projection callers (`allow_reclaim=True`). **Why (2026-09-20,
`tests/e2e/test_unified_turn_concurrency.py`):** the route's only test
was "is this THREAD paused". A resume pauses again at the next gate, so
a retried Approve — a double-click, a lost 200, a stale tab — decided
the question the human had never seen (approved `file_write`, authorized
`shell_exec`). The 409 translates registry words into client words:
`claimed`/`resuming` leave as `inflight`, because chat.js converges on
that and paints an ERROR on anything else.

**B3. A click on a pending card waits for the segment that showed it**
(2026-10-01). A segment journals its approval card and THEN finishes (the
turn's totals, the reply's save); until its task ends the thread counts as
running, and the route refused an Approve sent in that window as "no longer
pending" over a gate that was pending -- the button did nothing. When the
registry has the named gate on record as `pending` (`gate_is_pending`, the
positive question `gate_not_pending` does not answer), the route waits for
that task (`_await_segment_tail`, 10 s), checks the gate again and resumes;
no id, no row or a timeout keeps the old refusal. The lifecycle job caught
it once the route's own checks became faster than that tail.
`test_an_approve_sent_as_the_card_appears_is_taken` holds the tail open for
a second (negative control: the same turn without the wait is refused).

**C. Two-id rule.** `register_gate` is idempotent on both `gate_id`
(LangGraph `intr.id` preferred) and `alias_id` (the deterministic
`make_gate_id` hash) — one pause can never mint two cards. A terminal row
under a HASH id does not eat a NEW ask for the same tool+args (fresh row,
uniquified suffix); a native-id repeat returns the terminal row.

**C2. A row keeps no credential** (2026-11-11). `register_gate` is the one
writer of a row's call (`_record_view`): `args_json` as an approval card
shows it (`security.approval_args`), `payload_json` with every call's
arguments masked (`redacted_gate_payload`), `message` through `redact_text`,
and `args_digest` = `arguments_digest` of the REAL arguments -- the swarm
card's footer and the Swarm page's `describe` show it. A writer passes the
whole real JSON (a text cut before masking no longer parses, and its secrets
stay); the row is bounded after. Twins are matched by `args_digest`, never by
the stored text (two calls differing only in a hidden secret are two calls),
and only between graph rows (the one mechanism registered twice). Rows an
older build wrote are masked once at schema open (`_redact_stored_rows`,
digest from what they held). Execution never reads the row (A). Gate:
`tests/test_gate_records_keep_no_credentials.py` (every `GateRow(` writer
driven with a planted secret and the raw row read; the old storage and the
text twin rule as controls).

**D. Surfaces render; they never mint.** Gate transitions publish through
`GateEvents` into the EXISTING turn journal (`hitl` parts of the same
TurnDocument) — never a parallel event stream. Writer sites: SSE post-stream
scan (primary register, has `intr.id`), `/api/approve` (claim + resuming),
drive terminal (`settle_thread_gates` — settles claimed/resuming, NEVER
pending: a second live question keeps the turn open), gateway pause/resume
(`gate_claimed_for_thread` — platform cards are per-thread), swarm
`safety.check()` (register→bus→claim+settle), pipeline
`checkpoint_manager._gate_register_pipeline`/`_gate_settle_pipeline`.

**D2. A decision has ONE writer:** `kazma_ui/hitl_decision.record_gate_decision`
-- transcript part stamp, registry CAS, THEN the journal `hitl` frame (the
broker stamps the frame's view from the registry; emit-before-CAS painted "No
longer pending", 2026-09-20). The web approve route, the approval-timeout
watchdog (`decision="timeout"`) and platform buttons all call it (the WS
approve path did too, until the socket's graph client was removed, §31 D). Each used to write its own subset: the watchdog never stamped the
transcript, so an auto-denied card stayed `pending` in the chat forever and
the turn reloaded broken (2026-09-26); platform buttons never told the journal,
so a browser watching the thread never saw the decision. It stamps only a turn
the session already has (a minted id would add an empty bubble). Gate:
`tests/test_gate_decision_recorder.py` (every function that builds a resume
with `approved=` calls it; `kazma ask` and `hitl_supersede` are exempt with
reasons).

**E. Reconciler — every crash window has one behavior.**
Approve-on-missing-row backfills (`created_missing`); `close_turn` settles
pending rows whose checkpoint is NOT paused as `orphaned` (in seconds);
`boot_sweep()` holds interrupted claimed/resuming rows as `error`, preserving
the human's decision and actor. The exclusive runtime owner sweeps with zero
grace at startup, so a fresh claim cannot remain stuck forever. It NEVER
touches pending (the card must survive a restart). `display_gates` includes
error rows to override stale Approved transcript stamps; `live_gates` remains
the lifecycle reader. A terminal registry row for the exact checkpoint
interrupt cannot become pending through the thin fallback. TTL sweep rides
the 15-min commitment GC cadence in `worker_bootstrap.py` (no new scheduler
loop). Metrics: `kazma_hitl_gates_total{state,mechanism}`,
`kazma_hitl_gate_parity_mismatch_total{site}` (must trend to zero),
`kazma_hitl_gate_reconciled_total{action}`.

**E2. An approval that expires is said for what it is, in the chat it was
asked in** (2026-10-18). Live on Telegram: the watchdog auto-denied a
`file_apply_patch` card nobody had answered in 300 s; the model read "denied by
user", asked for `send_file`, and that card and the turn's answer went to the
web journal only (`_drive_graph_to_journal`): never seen, it expired too, and
the owner was told twice that they had refused. Twelve minutes later a tap on
the first card answered "Already handled -- this approval was applied
earlier". Now: (1) the tool worker gives a timed-out answer (`timed_out` in the
resume) its own result -- "expired unanswered ... the user did not decide",
the watchdog's instruction, outcome `terminal` (`_denied_result`); (2) a chat
app's turn (the checkpoint's `_gateway` routing: platform and
`delivery_target`, never the session store) is resumed by the gateway
(`hitl_timeout.register_chat_app_resumer`, registered by
`create_graph_handler`; `agent_handler.hitl.make_timeout_resumer`) under its
thread lock, and the chat gets a notice, the answer and any next card through
the same `_run_resume` / `_deliver_resume` a tap uses; a turn with no address
stays in the journal; (3) a late tap reads the card's own gate
(`_decided_card_message`): expired, denied, or the approve path. Gate:
`tests/test_approval_timeout_reaches_the_chat.py` (each with its control). **After an approval the chat gets what the resume
added** (`hitl._new_assistant_text`, 2026-10-20): the newest assistant text of
the running turn that the turn had not said before the resume. "The latest
assistant text" over the whole history re-sent the previous turn's failure
after every approval of a step that only called tools.
`tests/test_resume_sends_only_new_text.py` (the old search as the control).

**F. Honesty limits.** `hitl_gates.db` is single-process truth (like the
turn journal) — no multi-replica claims; a Postgres backend goes next to §21
when needed. The registry cannot stop the model narrating while paused —
that is handled by close_turn keeping the turn open on any pending row.

**G. Graph tool receipts (2026-10-06).** `agent/effect_journal.py` reserves a
mutating call before dispatch and commits its result before the graph's next
checkpoint, on `tool_effects.db` (WAL, synchronous FULL; carried in migration
bundles and on the complete state volume). Identity binds tenant, thread,
turn timestamp, iteration, provider call ID and tool; the request hash also
binds arguments and workspace. A completed replay returns the recorded result;
a started receipt, timeout or result-commit failure stops the turn with an
honest assistant warning and no synthesis. Never reset an unknown receipt
or enable whole-agent retries on this basis. Reads are not cached. Legacy
states without thread/turn scope have no graph receipt. Direct IDE tool calls
and in-process swarm tool loops now use the same receipt engine. The IDE HTTP
boundary binds an `Idempotency-Key` to principal, tenant, payload and captured
workspace; its saved response never dispatches or opens another gate. The browser
retains unresolved IDs in session storage across reload, without storing payloads.
Unkeyed clients receive a generated ID but cannot recover a lost response without
having retained their own key. `tests/test_execution_recovery_scope.py` covers
concurrent requests, changed payload/workspace, denied gates and failed writes.
`tests/test_effect_journal.py`
includes real child-process death, concurrent admission and visible warnings.

`LocalToolRegistry.execute` retries only classified reads. Mutating and unknown
tools have one invocation attempt; a failure after invocation carries
`effect_uncertain=True`, which the journal persists and holds on replay.
Argument validation before invocation is still correctable. Do not turn a
post-invocation error into a retryable ordinary result.
**A refusal before anything ran is not uncertain** (2026-10-18,
`agent/tool_refusal.RefusedBeforeEffect`): a tool raises it only before it
writes, sends or starts anything, and the registry answers it as an ordinary
`Error:` result (`exc.content()`; a JSON `{"ok": false}` answer stays as it
is). Live, two turns in a row on Telegram: an approved `shell_exec` refused by
the host-shell policy, then an approved `browser_navigate` this host cannot
launch, each recorded "effects unknown; must not be repeated" and the turn
failed. **Every tool that is not read-only refuses this way**:
`tests/test_tools_refuse_before_effect.py` reads each one's own body (and the
helper it hands the work to, `DELEGATES`) and fails on an error it RETURNS
outside an exception handler -- an `"Error..."`/`"Safety:"` text, a refusal a
helper built (`denied`, `refusal`...), `{"ok": false}`, or the answer of one of
its module's own helpers that returns one, followed to any depth (2026-10-31:
`generate_docx` returned `_output(...)`, whose "Error:" the name-only rule
missed) -- and on a
`raise RefusedBeforeEffect` that the tool's own `except Exception` would turn
back into a returned error (pass it through with `except RefusedBeforeEffect:
raise`). An error returned from a handler (the action raised) stays uncertain;
one known to come after the action (`send_file`'s "was not sent") is marked
`effect_unknown(...)`. A patch set whose failed hunk was rolled back checks
it: every file hashed as it was before the set is a `KnownEffectError` ("No
file was changed"); only a file the restore did not bring back stays unknown
(2026-10-31: one letter of 19 replace-all patches did not occur, the
checkpoint put the paper back, and the turn failed "effects unknown"). A typed refusal raised
from inside an action counts too: the browser's launch refusal is
`web_acquire.result.BrowserLaunchRefused` (an `AcquisitionError`, rebuilt
whole by Playwright's `type(error)(message)`). A test that calls a tool
directly reads its answer through `tests/_tool_answer.answer`. **A failure
whose effect the tool knows, where a repeat duplicates nothing anyone sees,**
raises `tool_refusal.KnownEffectError` (a `RefusedBeforeEffect`: the registry
answers it as an ordinary error). `generate_pdf`'s check that the requested
text reached the PDF names the incomplete file and asks for a new call;
returned as `effect_unknown` it failed the turn and forbade the retry it asked
for (live 2026-10-20). Every document tool's failed operation is one too
(`document_generator._output`, `document_processor._artifact_output`): the
operation deletes its export, so nothing was written, and the message says to
call again later when the service is down (retryable) or to change the request;
live 2026-10-31 a broker outage ended a `generate_docx` turn as "effects
unknown, must not be repeated". The check also compared table cells raw against
punctuation-free text, so every table with "°C" or "Jun – Sep" failed; cells
and text are normalized alike now (`tests/test_pdf_verification.py`). Never
for a send, a post or a payment. **And a call that cannot run gets no card**: `safety/call_preflight.call_refusal` (shell:
`shell_preflight.shell_refusal`, the policy then an empty command or a shell
metacharacter; the browser tools and `computer_use`:
`browser_isolation.launch_refusal`, the strict network mode's own decision,
off the loop) is asked by the chat tool worker before the commitment gate
(`_refuse_unrunnable_calls`, `outcome="policy"`, the turn goes on) and by the
registry before the swarm bus. The cards had said "BLOCKED: host shell is
disabled" and asked to approve a navigation Windows refuses. The X tools that
publish, delete or book (`X_PUBLISH_TOOLS`) ask `x_api.policy.publishing_refusal`
there (live 2026-11-25: an approved `x_post` while publishing was paused after
a restore), and one that certainly did not happen -- `not_sent`, `rejected`,
`not_on_x`, a booking not made -- is raised as `RefusedBeforeEffect` with its
reason; an unknown outcome stays a failure (`tests/test_x_refused_says_why.py`,
which enumerates the X tools that ask). Gates:
`tests/test_shell_refused_before_card.py`, `tests/test_browser_refused_before_card.py`
(the live URL; the old browser tools give exactly the live `effect_uncertain`;
a launchable or runnable call still gets its card). So does a call whose
arguments the output limit cut off (`_refuse_unrunnable_calls(truncated=)`:
the response ended at max_tokens and the arguments are `{"raw": ...}` or
empty): live 2026-10-31 the owner was asked to approve half a JSON object
that the worker then skipped (`tests/test_truncated_tool_call.py`).
`dispatch_effects` tracks actual local/MCP mutator invocation across the worker
retry boundary, after validation and HITL. An unsuccessful dispatch after such
an invocation carries `retry_safe=False`; keep it on `WorkerResult` through
serialization and stop both retry and fallback chains.
Handoff failures retain earlier invocation evidence on every failed result,
including missing targets, so selecting the chain's final result cannot reopen
task-level fallback. IDE rate admission runs before receipt admission and once
per HTTP request; throttled requests retain HTTP 429 and can retry after expiry.
In-process workers stop
uncertain tool loops with an empty output and no synthesis. This does not qualify
whole-agent Temporal retries or custom workers' external effects outside the tool
registry. Completed effects and unknown effects both make whole-worker replay
unsafe; a successful tool receipt alone does not prove the next worker safe.
`UnifiedToolExecutor` carries the same uncertainty for a failed MCP mutator
dispatch, preserving the original failure through output hooks. Read errors
remain correctable. This does not make the external server transactional.

**A refusal counts on its own allowance, never as tool death** (2026-11-05,
plan D1). Every error the registry and `UnifiedToolExecutor` return before the
tool ran -- unknown or switched-off tool, a hook, commitment, permission or
division refusal, a preflight or approval refusal, missing arguments,
`RefusedBeforeEffect`, a read that answered with an error -- carries
`refused: True`, and `classify_tool_result` makes it `REFUSED` (a
policy-worded one keeps its class; `effect_uncertain` always wins). The
hard breaker neither counts nor resets on it; `update_breaker` counts refused
rounds apart (`REFUSAL_ALLOWANCE` = 6, reset by a success), and the same call
refused twice (`tool_signature`, kept in `SupervisorState.refused_signatures`)
ends the turn at once with the refusal and a stop note, so the model tells the
user what is missing. Live 2026-11-04 three wrong-path refusals ended a
research turn as tool death. The chat tool worker and the swarm worker use
the same breaker. Gate: `tests/test_refusals_stay_correctable.py` (every error
dict either executor builds carries `refused` or is declared post-invocation;
the unmarked results as the negative control).

Tests: `tests/test_hitl_gates.py`, `test_hitl_gate_bridge.py`,
`test_hitl_gate_read_cutover.py`, `test_hitl_gate_swarm_pipeline.py`,
`test_hitl_gate_reconciler.py`.

**Collision recipes** (also in `docs/audits/AUDIT_DEEP_2026-09-01_EXEC.md` —
keep both in sync):

1. **T-4 / `chat.js`:** scrub stays inside `renderTurn`. No second painter.
2. **H-8 / `tool_registry.execute`:** apply `rewritten_args`; `clarify`/`confirm`
   fail closed (“run from chat”). Never mint a second gate row on the web path.
3. **T-2 pipeline timeout:** finalize the task **and** `settle_gate`.
4. **H-9 bus:** `is_danger_tool()` → `approval_floor()` (the `requires_approval()` floor).
5. **H-12 (superseded 2026-10-14):** a bus request has one shared decision
   (`shared_approvals`): the first human Approve or Deny decides, every card
   shows it, timeout/cancel are their own outcomes. Must not retarget web `claim_gate`.

Protected files: `chat.js` (`_paintApprovalGroup`, `_paintHitlSlotCard`,
`renderTurn`, `_hitlAlreadyClaimed`, `_serverGateViews`, `_serverGatesAuth`),
`turn_document.js`,
`turn_runtime.py` (`close_turn`), `hitl_gates.py`, `hitl_status.py`.


