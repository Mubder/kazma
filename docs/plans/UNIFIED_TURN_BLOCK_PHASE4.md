# Unified turn block — Phase 4 report: recovery, integration, hardening

Date: 2026-09-20

Status: Phase 4 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
Builds on [Phase 0](UNIFIED_TURN_BLOCK_PHASE0.md),
[Phase 1](UNIFIED_TURN_BLOCK_PHASE1.md),
[Phase 2](UNIFIED_TURN_BLOCK_PHASE2.md) and
[Phase 3](UNIFIED_TURN_BLOCK_PHASE3.md).

---

## 1. The first defect this phase found

Phase 3 closed with concurrent-client tests unwritten. Writing them found
three defects: one in the approval route every UI shares (below), one in
the harness itself (§3), and one in the tool registry (§4). This is the
first.

`POST /api/approve/{thread_id}` asked one question before resuming: *is
this thread paused?* It read the pending interrupt straight off the
checkpoint (`read_pending_interrupt`) and resumed whatever it found. The
`interrupt_id` in the request body was used to stamp the response view
and for nothing else.

Measured by
`tests/e2e/test_unified_turn_concurrency.py::test_a_lost_response_does_not_execute_twice`:

```
POST /api/approve/<thread> {interrupt_id: G1}   -> 200 {running: true}
      ... graph resumes, executes G1's file_write, pauses again at G2
POST /api/approve/<thread> {interrupt_id: G1}   -> 200 {running: true}
                                                   view.tool = shell_exec
```

The second POST is what a double-clicked Approve, a lost 200 or a stale
tab produces. It authorized a shell command the human had never been
shown. Plan §7 says a lost acknowledgement must trigger status recovery,
"not an unconditional new approval command"; invariant U11 says transport
loss is not authorization. One missing comparison broke both.

The authority to answer already existed. AGENTS.md §30 makes
`hitl_gates.db` the decision authority with CAS, `hitl_gate_bridge`
registers every pause under its real `interrupt_id`, and a new pause
settles the thread's older rows. Only the graph path of this route never
asked — the bus-bridge path in the same file always did.

### The fix

`_gate_not_pending(thread_id, gate_id)` in
[`routes_direct/misc.py`](../../kazma-ui/kazma_ui/routes_direct/misc.py),
consulted before `read_pending_interrupt`:

| Registry says | Route does |
|---|---|
| `pending` | no objection — resume, exactly as before |
| `claimed` / `resuming` | 409, `reason: not_pending`, the server's actual view |
| terminal (`settled`, `denied`, `expired`, `failed`, `superseded`) | 409, same |
| row names another thread | 409, `foreign` |
| no row / no id / registry off / registry unreadable | no objection |

The last line is deliberate. The dashboard, the TUI and the gateway all
reach this route with ids the registry may not carry, and a human is
waiting behind every one of these calls: fail open on plumbing, never on
a recorded decision.

### The refusal had to be legible

`chat.js` (~5853) splits an approve 409 on
`running || hitl_state === 'inflight' || hitl_state === 'approved'` —
true converges and re-attaches, false paints the row **errored** and
throws the delivery away. The registry's own words are "claimed" and
"resuming", which match neither arm, so a correctly-approved row would
have flashed red on every double-click. The 409 therefore translates:
`claimed` and `resuming` leave as `inflight`, terminal states as
`settled`, and the raw `registry_state` rides along for operators.
Both ends of that agreement are locked in
`tests/test_approve_decides_one_gate.py`.

---

## 2. Why the harness changed scope

Every unified-turn e2e suite now boots one app **per test**, not per
module.

The root `conftest.py` gives each test its own ConfigStore,
SessionManager and gate registry. A module-scoped app outlives those
swaps and writes through stores the next test has already replaced. Two
symptoms, one cause:

* `KeyError: 'session not found: utb-…'` from `reply_sink.transact`;
* `Model 'harness-model' not found in any configured provider. Falling
  back to active provider` → the shipped OpenAI profile → the page
  showing "No API key configured for https://api.openai.com/v1".

The second only ever hit the browser, and that is the instructive part.
The HTTP harness pins no model, so the chat route uses the agent's own
provider and skips the registry entirely. A browser pins the model on
every send, which routes through `get_client(model)`. Same harness, same
app, different branch — which is why four green HTTP tests said nothing
about the browser.

`tests/e2e/conftest.py` also re-seeds the harness provider per test,
scoped by `KAZMA_UTB_HARNESS` so no other e2e test has its provider
rewritten underneath it.

Cost: ~25s per test. Bought: the app's singletons are the test's
singletons. A shared server that silently diverges from the test's own
state is a harness reporting on something other than the product.

---

## 3. The harness was never actually isolated

Per-test app scope turned `test_approved_tools_actually_execute` red,
and the reason was not the change that exposed it.

The worker had been logging success for every approved write:

```
[ToolWorker] HITL approved: file_write
[ToolWorker] exec name=file_write tier=danger args=path=<tmp>\README.md
Tool 'file_write' executed in 0ms      Tool file_write [OK]
[ToolWorker] file_write → 32ms (error=False)
```

and `<tmp>/README.md` did not exist. Nor did it exist anywhere under the
isolated tree. `kazma_core/tools/file_write.py` reports a refusal by
**returning** `"Error: …"` rather than raising, so a refused write is an
ordinary return value: `error=False`, turn continues, nothing written.

Probing the workspace ladder directly, in a process whose
`KAZMA_DATA_DIR` and `KAZMA_WORKSPACE` both pointed at a fresh temp
directory:

```
ROOT:   G:\GitHubRepos\kazma
ACTIVE: {'name': 'kazma', 'root_path': 'G:\GitHubRepos\kazma', 'is_active': True}
ACCESS: allowed=False, reason='outside workspace; no grant',
        workspace='G:\GitHubRepos\kazma'
```

Two compounding causes. `stores/workspaces.py` computes its default
database path at **import** time, so the singleton was already aimed at
the operator's real `kazma-data/workspaces.db` before any fixture set a
variable. And an empty store is not a quiet one: given no rows it
registers and activates the current working directory — a fresh row id
and today's timestamp, pointing at the checkout. That active row is
rung 2 of `resolve_active_root()`, which outranks the `KAZMA_WORKSPACE`
at rung 4.

**So the harness's agent had the operator's repository as its
workspace.** Nothing was written to it, but only by geometry: the
fixture's paths are absolute and point into the temp directory, so
`check_path_access` refused them for being *outside* the workspace. A
fixture written the obvious way — a relative `"README.md"` — would have
had an approved `file_write` overwrite the repo's own README. Plan
§14.1: "Never use the operator's active approvals as test fixtures."

The fix is in `unified_turn_server`: set `KAZMA_WORKSPACE`, install a
WorkspaceStore on the isolated database, and **name** the workspace in
it rather than hoping an empty store stays quiet. `reset_workspace_store`
joins the teardown so the operator's own store comes back.

`tests/test_harness_isolation.py` locks the property directly, in a
third of a second, because the end-to-end proof costs a minute and a
real graph. The test that found it was passing before per-test isolation
because six tests shared one data directory and an earlier turn had left
the file there. It now also carries the tree listing in its failure message,
which is what turned "no README.md" into a diagnosis in one run rather
than three.

---

## 4. ...and a refused tool was reported as a completed one

Chasing the silent write turned up a second defect, this one in the
product and squarely in the acceptance matrix:

> | Deny / expiry / tool execution failure | Correct distinct
> | decision/execution labels; **no false success**. |

`tool_registry.py` states the convention in its own comment — "Plain-string
tools report failures by returning an `Error: …` string" — and checks two
prefixes, `Error:` and `⚠️`. `workspace.path_policy.denied_message()`
never adopted it. It opens with:

```
Safety: write/modify outside the active workspace is not allowed.
  path: …
  workspace: …
To proceed, request a path grant (user must approve): …
```

So a refusal classified as `is_error=False`, the worker logged
`Tool file_write [OK]`, and the turn recorded a **completed execution**
for a write that never happened. Five tools return that message —
`file_write`, `file_read`, `file_apply_patch`, `tool_scope`,
`ide/service` — and all five were doing it.

On the unified turn block this is the worst-shaped bug available: the
approval row exists to keep the decision and the execution apart, and it
was telling the reader that the thing they approved had succeeded.

The fix is the third prefix in the same expression. Rewording
`denied_message` was the alternative and is worse: that text is written
for the model, and it explains how to request a grant.

`tests/test_refused_tool_is_not_a_success.py` pins the message's prefix
as well as the classifier, because a reworded denial would otherwise
restore the false success in silence.

---

## 5. Three measurements that corrected the tests, not the product

Worth recording, because in each case the first red was the test being
wrong about the system.

**A turn to the first gate journals four frames.** The shared script
narrates 19 characters and `scripted_provider` chunks at 24, so the
whole leg is one delta. A "disconnect after 6 frames" therefore read the
*entire* leg and re-attached with nothing to replay. The recovery suite
now supplies its own script with a first step long enough to stream.

**An attach to a paused thread never closes.** That is deliberate —
`_sse_attach_stream` holds it open because approve will journal into the
same tail — so a reader waiting for a terminal frame waits for a human.
`sse_frames` gained an opt-in `keepalives=True`, and a keepalive after
the replay on a turn the handshake said is not running is read as the
end of transmission. The suite went from 189s (three deadline waits) to
68s.

**Crossing a retention bound by one frame is not a gap.** The first
attempt set the bound to 3, ran a 4-frame turn and attached at cursor 1;
`replay()` correctly answered `gap=False`, because the retained window
started at seq 2 and the client had missed nothing. The test now derives
its stale cursor from the window the journal actually kept, and asserts
that eviction really happened before asserting anything about gaps.

---

## 6. Acceptance matrix

Every row of plan §10 with the test that answers it. A phase number
means the row was already answered there and is unchanged.

| Scenario | Answered by | Phase |
|---|---|---|
| Normal streaming | `tests/e2e/test_unified_turn_browser.py`, `tests/js/test_turn_view.js` | 2 |
| Open/collapse during streaming | `…browser.py::test_the_fold_starts_collapsed_and_stays_where_the_reader_puts_it`, `tests/js/test_turn_preferences.js` | 2 |
| Final arrives | `tests/js/test_turn_convergence.js`, `tests/test_turn_durable_presentation.py` | 1 |
| Four sequential gates | `tests/e2e/test_unified_turn_app_graph.py`, `…browser.py::test_sequential_allow_tool_in_one_bubble` | 3 |
| Multiple pending gates | `tests/js/test_turn_view.js`, `…app_graph.py` (two distinct `file_write` gates) | 3 |
| Repeated identical tool/arguments | `…app_graph.py`, `tests/js/test_turn_document.js` | 3 |
| Approve before next status poll | `…browser.py::test_the_row_settles_before_any_poll` | 3 |
| Deny / expiry / execution failure | `…app_graph.py::…denial_does_not_end_the_turn`, `tests/test_hitl_gates.py` | 3 |
| **Two tabs approve concurrently** | `…concurrency.py::test_two_tabs_approving_at_once_produce_one_decision` | **4** |
| **Lost approval HTTP response** | `…concurrency.py::test_a_lost_response_does_not_execute_twice`, `tests/test_approve_decides_one_gate.py` | **4** |
| **Disconnect mid-token** | `…recovery.py::test_a_stream_abandoned_mid_turn_reconnects_without_gap_or_repeat` | **4** |
| **Journal retention gap** | `…recovery.py::test_a_cursor_outside_retention_is_told_to_resync_not_served_a_hole`, `::test_the_durable_store_still_answers_after_a_gapped_attach` | **4** |
| Delayed/duplicate frames | `tests/js/test_turn_convergence.js` (recorded seeds) | 1 |
| Refresh mid-pause | `…browser.py::test_refresh_mid_pause_rebuilds_the_group` | 3 |
| Refresh after completion | `tests/e2e/test_hitl_view_model.py`, `tests/js/test_turn_convergence.js` | 2 |
| Restart mid-stream / mid-pause / after decision | `tests/e2e/test_unified_turn_restart.py` | 1 |
| **Session switch during updates** | server: `…recovery.py::test_a_second_session_never_receives_the_first_sessions_frames`; client: `…browser.py::test_a_new_session_mid_pause_does_not_inherit_the_open_question` | **4** |
| Stop during stream or resume | `_stopRequested` in `chat.js`, `tests/test_chat_as_product.py` | 2 |
| Failed model turn | `tests/test_delivery_reconciles.py`; `turn_failed` path unchanged | — |
| Retry / next turn | `tests/js/test_turn_document.js`, `tests/js/test_turn_preferences.js` | 1 |
| Legacy history | `tests/test_unified_turn_fixtures.py`, `tests/js/test_unified_turn_fixtures.js` | 1 |
| **Long activity / code / RTL / mobile** | `…browser.py::test_the_answer_survives_a_phone_in_rtl`, `::test_a_completed_four_gate_turn_keeps_its_answer_out_of_the_fold`, `tests/test_unified_turn_a11y.py` | **4** |

---

## 7. Compatibility

Plan §12 asks for checks on "dashboard decisions, gateway approval,
SSE/WS delivery, attachments, voice, composer steering, and existing HITL
paths". The gate guard sits in the route all of them share and the
classifier fix sits in the registry every tool goes through, so these are
the ones that matter this phase. Run against the working tree:

```
tests/test_hitl_wiring.py  tests/test_approval_reattaches_the_stream.py
tests/test_hitl_reply_continuity.py  tests/test_delivery_reconciles.py
tests/test_reply_persistence_contract.py            89 passed, 19.58s

tests/test_hitl_gates.py  tests/test_platform_rbac.py
tests/test_audit_2026_09_04_wave4.py
tests/test_f0_hitl_app_graph_spike.py               61 passed,  5.82s

tests/test_dashboard_capabilities.py  tests/test_gateway.py
tests/test_chat_steer_composer.py  tests/test_chat_attachment_security.py
tests/test_tui_session_load.py  tests/test_ws_chat_telemetry.py
tests/test_voice_mode.py  tests/test_ui004_ui008_gateway_misc.py
                                                   159 passed, 16.20s

tests/test_tool_hooks.py  tests/test_tool_arg_validation.py
tests/test_tool_schema.py  tests/test_dedup_tool_registries.py
tests/test_commitment_tool_worker_gate.py  tests/test_tool_loop_breaker.py
tests/test_task_store_workspace.py
tests/test_document_processor_security.py            64 passed, 21.79s
```

---

## 8. Evidence

| Suite | Result |
|---|---|
| `tests/test_harness_isolation.py` | 4 passed, 0.35s |
| `tests/test_refused_tool_is_not_a_success.py` | 7 passed, 0.40s |
| `tests/test_approve_decides_one_gate.py` | 16 passed, 0.83s |
| `tests/e2e/test_unified_turn_app_graph.py` | 6 passed, 100.13s |
| `tests/e2e/test_unified_turn_restart.py` + `…concurrency.py` + `…recovery.py` | 10 passed, 153.62s |
| `tests/e2e/test_unified_turn_browser.py` | 7 passed, 143.28s |
| turn contract, durability, a11y, fixtures, xfail-strict (7 files) | 190 passed, 1 skipped, 9.28s |
| node projector suites (8 files) | all green; 9 performance properties held |
| compatibility (§7) | 373 passed |

All e2e figures are from a full re-run after the workspace-isolation
fix, not from the runs that found it.

The lifecycle CI job gained four steps — the harness-isolation lock and
the one-gate lock (placed first, because they are the cheapest failures
to report), the concurrency suite and the recovery suite — alongside the
app-graph, restart and browser steps already there. No `|| true`, and no
`importorskip` standing in for a dependency the job is supposed to
have.

---

## 9. What Phase 4 does NOT claim

* **The WS approve path is unfixed.** `routes/ws_chat.py`'s
  `approve_tool` has the same shape — `read_pending_interrupt` then
  resume — and its payload carries no `interrupt_id` to match against.
  It is off unless `KAZMA_WS_GRAPH=1`, and when off it answers
  "HITL resume uses POST /api/approve/{thread_id}". Giving it the same
  guard means giving its clients an id to send, which is a protocol
  change this plan does not own (§15: no new approval endpoint).
  Recorded, not fixed.
* **"Disconnect mid-token" is staged with a scripted stream.** The
  recovery suite's own script narrates ~450 characters so the first leg
  really streams; the disconnect lands between deltas. It is not a
  disconnect inside a single network packet, and nothing here claims
  that.
* **The retention-gap test shrinks one process's bound** rather than
  crossing a replica boundary. Plan §15 puts multi-replica correctness
  out of scope and the journal is process-local by design.
* **Randomized schedules stay restricted** to delayed and duplicated
  frames — what a seq-ordered journal can actually produce. Fully
  shuffled arrival is excluded deliberately (§10: "Do not require
  arbitrary impossible event permutations").
* **The plain-string failure convention was not audited exhaustively.**
  Three prefixes are now recognised — `Error:`, `⚠️`, `Safety:` — and
  `Safety:` was found by following one silent write, not by sweeping
  every tool that returns a string. Another tool reporting failure in a
  fourth shape would still be recorded as a success. A sweep belongs
  with the removal inventory in Phase 5.
* **The legacy progress painter is still in the file**, unchanged since
  Phase 2 §6: `ensureProgressPanel` / `logProgress` / `_progressEl` are
  reachable only if the projector module fails to load. Phase 5.
* **The lifecycle job is not merge enforcement.** It runs; branch
  protection has not been told to require it. Phase 5, plan §13, and an
  administrative dependency outside this repository.
* **No packaged-build smoke, screenshots, traces or build identity yet.**
  Phase 5.
