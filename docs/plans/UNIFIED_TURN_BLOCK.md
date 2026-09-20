# Unified turn block: production architecture and delivery plan

Date: 2026-09-20

Status: Proposed implementation contract. No implementation or verification is claimed by this document.

Scope: Kazma Web chat turn presentation, state projection, persistence/recovery, approval integration, and release enforcement. Preserve other transports and existing safety boundaries.

## 1. Outcome and user requirements

Each assistant turn has exactly one persistent block in the transcript. Its header contains status and controls. Its body contains collapsed thoughts/activity, one grouped approval section, and the streaming answer. Completion updates this block in place. Refresh reconstructs the same content and authoritative state.

Required behavior:

1. Merge the separate live status bar into the assistant turn header. Remove the bottom bar and its independent controller.
2. Thoughts/activity start collapsed during live execution as well as history loading. Incoming events never override the user's expansion choice.
3. Thoughts remain available after the final answer arrives, session switching, reconnect, and refresh. Restart recovery requires durable storage, not DOM preservation.
4. Four approval requests in a turn appear as four identifiable rows inside one approval group, not four independent cards or bubbles.
5. Sequential approvals and resume cycles keep the same turn identity, block, and approval group.
6. The answer streams into one answer region. Collapsing thoughts never hides the answer or required approval controls.
7. Completion, failure, cancellation, and interrupted recovery are distinguishable. The UI never fabricates success.
8. Behavior is enforced by tests against the real application lifecycle and the distributable application.

“Production-grade” is an acceptance standard, not a commit adjective. This plan cannot guarantee no future bugs; it must make these failure classes difficult to introduce, detectable before release, and diagnosable in operation.

## 2. Verified baseline and limits of this review

Repository inspection, not live incident reproduction, established the following:

| Evidence | Consequence |
|---|---|
| `f7992e2f`: sequential cards keep one bubble | One bubble did not mean one grouped approval container. |
| `c3ab62fd`: thoughts stay in bubble; task card becomes bar | The two presentation surfaces were deliberately retained. |
| `afbd22dd`: live CoT fold opens | Live updates explicitly remove the collapsed class. |
| `static/js/modules/turn_view.js:slotPlan` creates an entry per gate | Individual approval cards are part of the existing layout contract. |
| `static/js/chat.js:_paintWorkbenchSlot` opens live thoughts | Expansion is currently affected by execution updates. |
| `templates/chat.html` contains `live-task-card` and its controls | The separate bar has structural ownership outside the turn. |
| `static/js/chat.js` still uses `tokenAccum` at multiple sites | Removal requires a call-site audit; changing one paint function is insufficient. |
| `delivery.py` describes the journal as process-local memory | Restart recovery cannot rely on journal replay. Durable state must be inspected and completed. |
| `tests/e2e/test_hitl_view_model.py` explicitly leaves incidents 1 and 4 unclaimed | Existing browser coverage does not prove sequential approval/resume through the app graph. |

Do not claim all thought-loss paths are diagnosed yet. Phase 0 records actual event/persistence paths and reproduces the reported behavior before assigning further root causes.

### Relationship to prior plans

Upon implementation adoption, this document supersedes the presentation rules in:

- `COT_AND_THOUGHTS.md`: separate live header bar and any automatic opening of thoughts.
- `TURN_RENDER_V2_KEYED_SLOTS.md`: flat individual HITL slots around the answer. Stable identity and keyed reconciliation remain required.
- `HITL_VIEW_MODEL.md`: incident-specific layout expectations that require separate cards. Server-owned gate views and decision/execution separation remain required.

It preserves `TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md` delivery ordering/cursor rules and `HITL_GATE_REGISTRY_PLAN.md` approval authority. Any protocol extension must explicitly update those contracts.

During Phase 0, add supersession notices to the old plans, update their conflicting tests, and update relevant AGENTS.md guidance. Do not leave multiple documents marked binding with contradictory layout rules. Retain historical incident records as history.

## 3. User-visible layout

```text
Assistant turn
┌─────────────────────────────────────────────────────────┐
│ Working · 12s · 3 tools                           [Stop] │
│ ▸ Thoughts & activity                                  │
│                                                         │
│ Approvals · 4 requests · 1 awaiting your decision        │
│   ▸ file_write     Approved · execution completed       │
│   ▸ shell_exec     Denied                               │
│   ▸ file_delete    Approved · execution completed       │
│   Current request: tool, arguments/diff, scope           │
│   [Approve once] [Allow tool (session)] [Deny]            │
│                                                         │
│ The answer streams here…                               │
└─────────────────────────────────────────────────────────┘
```

The example is a layout sketch, not a change to danger-tool classification. Actual rows correspond only to server-issued gates.

### Header

- Exists from the first acknowledged turn state, including before the first token.
- Owns phase, elapsed duration, useful aggregate counts, Stop, and applicable retry/recovery actions.
- On completion becomes “Completed”; on failure “Failed”; on cancellation “Cancelled”; on pending HITL “Approval required”.
- A disconnected indicator describes the connection separately from execution. Disconnection is not completion or failure.
- Elapsed duration derives from server timestamps. Define execution duration and paused time explicitly; proposed default is total wall duration including approval wait. Never restart the clock on reconnect.
- A local timer may update elapsed display. It cannot mark a gate expired or a turn complete.
- No second fixed or floating status bar. A sticky header is out of initial scope; it can be considered later without adding a second rendered copy.

### Thoughts and activity

- One disclosure section, collapsed by default. Empty turns can show a compact activity label until content exists; do not render an empty expanded log.
- Opening exposes emitted reasoning summaries/text, tool activity, and relevant progress. Do not manufacture unavailable model reasoning.
- Preserve the user's choice through updates and completion. A new turn starts collapsed.
- Proposed preference behavior: persist expansion locally by session/turn within the browser session; when no preference exists, default collapsed. Preferences never travel as execution facts.
- Tool rows use stable identities. Expanded row details and keyboard focus survive unrelated updates.
- No duplicated approval decision log inside activity; link to the approval group when a timeline reference is useful.
- Long content may be loaded progressively, but must remain retrievable. Visual limits must not silently discard stored content.

### Approval group

- Zero groups when there are no gates; exactly one when at least one exists.
- One row per actual gate ID. Settled rows are compact and individually expandable.
- Pending rows expose the request details and controls even when thoughts are collapsed. Never hide an actionable request behind an unrelated disclosure.
- Multiple simultaneous pending gates each retain explicit controls; do not assume at most one pending gate in the schema.
- No aggregate “approve future requests” behavior. Existing explicit session/tool grants retain their existing scope and labels.
- Decision and execution labels are separate: “Approved” does not imply “Execution completed”.
- Request submission may temporarily disable its own buttons and show “Submitting”. Only a server response/view can establish a decision.
- Approval group location stays stable above the answer. Rows update in request order without moving the answer between containers.
- Preserve current argument/diff preview, scope explanation, timeout information, and semantic clarification controls where applicable.

### Answer

- One always-independent answer region inside the block, outside the thoughts disclosure.
- Streaming updates and final replacement target the same region. No copy from a temporary widget.
- Thoughts are never an answer fallback. Missing final content has an explicit server-backed failure/incomplete outcome where applicable.
- Preserve markdown, code blocks, attachments, citations, bidi/RTL handling, copy actions, and existing voice integrations.

## 4. Non-negotiable invariants

Assign these IDs to tests and review evidence:

| ID | Invariant |
|---|---|
| U01 | One turn ID maps to one mounted assistant block in the active transcript. |
| U02 | One turn block owns one header, at most one activity disclosure, one approval group, and one answer region. |
| U03 | Only the turn renderer mutates these content regions. |
| U04 | Applying an already-applied event never duplicates text, activity, or gates. |
| U05 | Old snapshots/events cannot regress authoritative turn or gate revisions. |
| U06 | Streamed answer and final answer share one region; final replacement preserves non-answer parts. |
| U07 | Completion does not delete persisted thoughts or gate history. |
| U08 | Event processing never changes disclosure preferences. |
| U09 | One gate ID maps to one row; distinct gates remain distinct even for identical tool/arguments. |
| U10 | Pending/approved/resuming/settled meanings come from the server's canonical view. |
| U11 | Transport loss, timer expiry, and DOM shape never imply authorization or completion. |
| U12 | Live, recovered, and historical rendering converge on equivalent content and authoritative status at the same revision. |
| U13 | A session switch prevents stale callbacks from painting into another session. |
| U14 | Terminal turns release subscriptions/timers without destroying their transcript. |
| U15 | Unsupported or incomplete recovery is shown honestly; it never becomes a fabricated successful reply. |

## 5. Architecture and ownership

```text
Graph/checkpoint          HITL registry
execution authority       decision authority
          \                /
           server turn serialization / canonical gate view
                         |
              durable presentation state
                         |
             existing journal and broker
                         |
        transport/cursor adapter + snapshot recovery
                         |
                 TurnDocument projector
                         |
                  derived turn view
                         |
                  TurnView renderer
                         |
               one persistent turn block

User expansion preferences --------> renderer only
User actions ---------------------> existing command APIs
```

“One source of truth” means one authority per fact, not one database for everything. Do not replace the gate registry or infer execution from a presentation record.

### Proposed module boundaries

Names below are proposed; prefer extending an existing focused module over creating an equivalent duplicate.

| Responsibility | Owner / proposed location |
|---|---|
| Sequence, cursor scope, transport attachment | Existing `streaming.js`, `modules/delivery_cursor.js`; audit attachment ownership before extracting anything |
| Pure event reduction and normalized parts | Existing `modules/turn_document.js` |
| Pure derived header/activity/approval/answer model | Proposed `modules/turn_presentation.js` |
| DOM tree, keyed regions/rows, content painting | Existing `modules/turn_view.js` plus focused renderer helpers |
| Expansion state | Proposed `modules/turn_preferences.js`, or a small private store within the renderer if sufficient |
| Commands and transient submission state | Existing approval client plus focused turn actions adapter |
| Canonical turn completion | Existing `turn_runtime.py:close_turn` |
| Persistence/serialization | Existing `turn_document.py`, `turn_runtime.py`, `sse_chat/_persistence.py`; extend one durable owner |
| Page orchestration | `chat.js`: mount, session selection, commands, and wiring only for this subsystem |

The renderer may read owned node references for reconciliation and focus. It may not query arbitrary transcript DOM to discover approval truth, turn identity, or whether work finished.

The page may request a repaint. It may not append/move approval cards or rewrite answer text itself. Formatters may return safe markup; ownership of insertion stays with the renderer.

### Keyed containment

Use stable child regions such as `header`, `activity`, `approvals`, and `answer`. Inside `approvals`, key rows by gate ID. Inside `activity`, key by stable part ID.

The answer remains a sibling of the activity and approval regions, never their descendant. This replaces the old flat-per-gate slot rule while preserving its protection against swallowing the answer in a collapsed panel.

Do not keep stale unplanned DOM indefinitely as a recovery mechanism. Preserve valid history in state, then render it. Missing data triggers explicit incomplete-state handling and recovery; authoritative removals need explicit semantics.

## 6. Data and protocol contract

Phase 0 inventories actual fields before introducing new ones. Required semantics, whether already represented or newly added:

- Session/thread ID, stable turn ID, stable message identity, and stream epoch/cursor scope.
- Ordered delivery sequence and authoritative document revision. Do not compare independent counters as if they were the same revision.
- Explicit snapshot completeness and covered revision/cursor. Partial gate updates must never masquerade as a complete gate inventory.
- Server lifecycle state and timestamps.
- Stable reasoning/activity IDs, tool-call IDs, and gate IDs.
- Canonical gate view including decision, execution phase, allowed actions, and known deadline.
- Explicit event kind: delta, upsert, snapshot, terminal replacement, or intentional removal.
- Schema version when storage/wire changes require compatibility handling.

### Merge semantics

1. Token deltas append exactly once under their existing scoped sequence rules.
2. Snapshot content is applied at its covered revision, then buffered newer events are applied in order.
3. A gap triggers snapshot recovery; do not blindly drop out-of-order missing content by advancing the cursor past it.
4. Final replacement affects answer text only unless explicit additional parts are present. It cannot imply deletion of activity or gates.
5. Gate updates merge by identity and authoritative ordering, using the existing canonical server view. Do not create a client rank table that guesses authorization transitions.
6. Partial snapshots merge only their declared coverage. Full snapshots may replace covered state under a documented revision rule.
7. Legacy records pass through one normalization adapter into the current model. Never introduce a second legacy renderer.
8. Unknown event kinds are safely ignored or trigger compatible resync according to protocol policy; they must not clear content.

Use shared JSON fixtures to prove Python normalization/serialization and JavaScript projection agree. Do not rely on two implementations with independently written examples.

## 7. Lifecycle and command semantics

These are presentation states mapped from server facts; do not add a second execution state machine in the browser.

| Situation | Header / actions | Required state behavior |
|---|---|---|
| Request acknowledged | Queued/Starting; Stop if supported | Establish stable turn identity before content. |
| Executing | Working; Stop | Append content/activity through projector. |
| Pending gate | Approval required | Same block; actionable gate row; server dictates allowed actions. |
| Approval submission | Approval required; row submitting | Local pending request only; no optimistic Approved. |
| Approval accepted | Resuming/Working | Same turn; retain decision history. |
| Denied request | Server-derived continued/terminal phase | Denial does not automatically mean the whole turn failed. |
| Completed | Completed | Final answer update; retain activity/gates; remove active controls. |
| Failed | Failed; actionable recovery if supported | Preserve partial answer and activity; no synthesized success. |
| Cancellation requested | Stopping | Await server acknowledgement; do not prematurely mark cancelled. |
| Cancelled | Cancelled | Preserve history and final server gate states. |
| Transport disconnected | Connection indicator alongside last known phase | Reattach/recover; never close on a client timeout. |
| Restart with incomplete execution | Recovering, then authoritative phase or Interrupted | Never rerun dangerous work automatically merely to restore UI. |

Retry of a failed turn must have explicit identity semantics: proposed default is a new turn linked to the prior one. Approval resume remains the same turn. Follow existing execution APIs and document any needed changes before implementation.

Concurrent approval clients retain registry CAS behavior. A 409 resolves to the actual server view and cannot create a duplicate row. A lost acknowledgement triggers status recovery, not an unconditional new approval command.

## 8. Persistence and restart recovery

The existing journal is memory-only. A durable presentation snapshot or durable event record is required for content that must survive restart. A process-local replay buffer cannot meet this requirement.

Phase 0 must determine whether current assistant-row/checkpoint persistence captures emitted reasoning and partial text during execution, at pauses, and before termination. Record actual write boundaries and crash windows.

Preferred implementation: extend the existing durable turn/message persistence with versioned incremental presentation state, avoiding a parallel competing store. If it cannot support required atomicity or cadence, document the evidence and design a dedicated durable presentation record keyed by the same turn ID, with one writer and an explicit message-materialization contract.

Required guarantees:

- Server-acknowledged presentation events must be recoverable after restart. Persist their recoverable state before publishing them, or implement a durable outbox that couples publication to committed state.
- Do not claim this guarantee if persistence is only periodic. If grouping token writes for throughput, commit each group before publishing that group.
- Preserve registry/checkpoint authority. Cross-store operations are not magically atomic: document ordering, reconciliation, and crash behavior between gate transitions, checkpoint progress, and presentation publication.
- Durably capture pause, decision visibility, terminal state, and complete activity content.
- Recovery snapshots include the revision boundary required to resume without duplication.
- Explicitly distinguish full restart from same-process reconnect; reset or re-scope volatile journal cursors safely.
- Old finished turns normalize once and render through the same path. Absent historical thoughts are labelled unavailable when relevant; do not fabricate them.
- Storage retention and deletion follow session policy. If complete activity is paged or archived, the disclosure must retrieve it. No silent permanent truncation.
- Use existing WAL/transaction conventions and migrations. Measure write amplification and chat latency before accepting the persistence design.

A persistence failure must surface as a recoverability/delivery failure and enter bounded recovery; it must not silently publish content promised as durable. Preserve already committed content and avoid a retry loop that re-executes tools.

## 9. Migration and removal inventory

Before removal, enumerate each caller and mark it as migrated, retained with narrow responsibility, or deleted.

| Current surface/path | Target disposition |
|---|---|
| `templates/chat.html:#live-task-card` and body | Remove after header controls move into turn block. |
| Separate task-card event/controller flow | Remove independent phase/content ownership; adapt commands to turn header. |
| Hidden legacy thinking indicator | Remove only after all references are retired. |
| `_paintWorkbenchSlot` automatic opening | Replace with preference-only expansion behavior. |
| Per-gate top-level slots | Replace with keyed rows inside one approval region. |
| `tokenAccum` and related fallback reads | Replace content decisions with document selectors; throttle rendering without a second text authority. |
| Direct live/history/recovery content insertion in `chat.js` | Route through the renderer; delete retired branches. |
| Independent gate overlays / imperative decision labels | Limit to transient command status; authoritative labels come from server views. |
| Restored-workbench-only markup | Use common region construction for live and restored turns. |
| Old CSS/selectors/imports/localization/tests | Remove or update alongside the owning change. |

Preserve unrelated composer, upload, voice, slash-command, and dashboard behavior. Review every changed shared function's callers. Do not rewrite all of `chat.js` in a single unrelated cleanup.

No framework migration is required. No new broker, approval API, or graph execution path is justified by this UI change.

## 10. Verification strategy

### Test layers

1. Pure model tests: identity, ordering, revision coverage, delta/snapshot merging, terminal preservation, and legacy normalization.
2. Renderer behavior tests: actual nodes, group counts, stable references, independent answer visibility, expansion, and focus retention.
3. Server integration: persistence round trips, canonical gate joins, terminal semantics, crash/restart recovery, and CAS races.
4. Application browser tests: real page, real application routes, real graph interrupt/resume, isolated storage, deterministic model provider.
5. Packaged application smoke: run the built distribution with its shipped static assets and verify the representative lifecycle.

Mock the external model/network at a supported provider boundary to make the graph deterministic. Do not mock the approval endpoint or manually change the UI to make sequential approval tests pass. The real graph must emit the first request, resume after the actual approval command, emit the next request, and finish.

The existing failed app-graph harness is a prerequisite to solve, not an accepted permanent exception. Missing browser dependencies or skipped required scenarios must fail the release gate.

### Acceptance matrix

| Scenario | Required assertions |
|---|---|
| Normal streaming | One block/header/answer; no bottom bar; thoughts collapsed. |
| Open/collapse during streaming | Later events preserve choice and focused control. |
| Final arrives | Answer stays visible; same activity content retrievable. |
| Four sequential gates | One block and one approval group throughout; four distinct rows after completion. |
| Multiple pending gates | Every pending request remains independently actionable. |
| Repeated identical tool/arguments | Fresh gate IDs produce separate rows; repeated same ID does not. |
| Approve before next status poll | Response view drives correct state immediately; no local Approved guess. |
| Deny / expiry / tool execution failure | Correct distinct decision/execution labels; no false success. |
| Two tabs approve concurrently | One effective decision/execution; loser reconciles actual state. |
| Lost approval HTTP response | Recovered server state; no duplicate execution. |
| Disconnect mid-token | Reconnect converges without duplicated or missing content. |
| Journal retention gap | Full snapshot plus newer events converge correctly. |
| Delayed/duplicate frames | No regression, duplicate text, or extra rows. |
| Refresh mid-pause | Same group and valid controls after authoritative hydration. |
| Refresh after completion | Answer, thoughts, and decisions persist. |
| Restart mid-stream / mid-pause / after decision | Durable content survives; authoritative execution recovery; no automatic unsafe rerun. |
| Session switch during updates | No content appears in the wrong session; return restores state. |
| Stop during stream or resume | Stopping until acknowledgement; correct final state/history. |
| Failed model turn | Existing `turn_failed` behavior preserved; no synthesized final success. |
| Retry / next turn | New identity where required; previous block retained; default collapsed. |
| Legacy history | One renderer; no invented missing content or ghost approvals. |
| Long activity / code / RTL / mobile | Readable, accessible, responsive; no answer trapped in folds. |

### Convergence oracle

For a fixture's covered final revision, compare normalized content and server-authoritative status from:

1. uninterrupted live delivery;
2. delivery with disconnect, replay, and duplicates;
3. fresh history hydration;
4. restart recovery.

Compare answer text, ordered activity IDs/content, gate IDs/views, and lifecycle. Exclude local expansion, transient connection state, and elapsed display sampled at different times. Browser assertions verify this equivalent model is actually visible.

Add randomized event schedules around supported ordering/recovery semantics using recorded seeds. Do not require arbitrary impossible event permutations to succeed silently; detect gaps and reconcile.

## 11. Performance, accessibility, and observability

### Performance

- Rendering is coalesced to animation frames; updates read the document at paint time.
- Update affected keyed rows; avoid rebuilding all activity HTML for every token.
- Collapsed activity does not require repainting its full body on each update.
- Bound mounted history/subscriptions; preserve retrieval of older content through storage/paging.
- Establish a baseline before implementation with fixtures of 100, 1,000, and 10,000 activity entries and a long answer.
- Proposed controlled-run targets: p95 visible update latency under 100 ms, no sustained token-driven main-thread stalls over 50 ms, and no material memory growth after 100 session-switch cycles once collection settles. Record browser/hardware and tune targets once from baseline with justification, not after failures to make them pass.
- Compare persistence latency/write amplification before and after durable publication changes. Correctness guarantees may not be silently weakened to improve benchmarks.

### Accessibility and interaction

- Semantic disclosure buttons with `aria-expanded` and stable accessible names.
- Announce coarse status/approval changes, not every token or timer tick.
- Preserve focus during keyed updates; restore it intentionally after a control disappears.
- Keyboard-operable gate rows and controls; readable mobile layout, RTL, reduced motion, adequate contrast.
- Keep existing scroll-pinning behavior: only auto-follow when the user is already following. Approvals must not pull a reader away from inspected content.

### Diagnostics

- Extend existing invariant reporting for duplicate blocks/groups, missing answer/activity regions, invalid revisions, resync failure, and gate-view mismatch.
- Log correlation IDs, schema/revision/cursor metadata, and error category. Avoid storing reasoning, tool arguments, secrets, or full replies in diagnostics.
- Bound and deduplicate reports. Route through existing diagnostics infrastructure; no new alerting subsystem.
- Attach build identity to evidence so a cached frontend cannot be confused with the tested build.
- Recovery is bounded with backoff. If it cannot reconcile, show a clear recoverable state and preserve content; no infinite poll/repaint loop.

## 12. Delivery phases and exit criteria

Each phase ships a coherent change with relevant behavioral tests. Dependency order is intentional.

### Phase 0 — Baseline, contract, and failing reproduction

Deliver:

- Full producer/projector/persistence/painter inventory with file and function references.
- Reproduction of the current split bar, separate gate cards, and forced-open thoughts.
- Deterministic application graph harness supporting four approval/resume cycles.
- Persistence/crash-window report and protocol compatibility inventory.
- Agreed layout fixture and supersession notices in prior plans.

Exit: tests fail for the actual missing behaviors, not fixture/setup errors; every invariant has an assigned verification layer. No claim that sequential approval works until the app-graph harness proves it.

### Phase 1 — State and durable recovery contract

Deliver:

- Stable identities, explicit snapshot coverage/revisions, normalization, and shared protocol fixtures.
- Canonical gate-view coverage for live and historical rows.
- Durable publication/recovery implementation where baseline shows a gap.
- Migration and old-record compatibility tests.

Exit: model/server convergence and restart tests pass; persistence guarantees and any bounded limits are documented and measured.

### Phase 2 — Unified renderer and header

Deliver:

- One turn shell and integrated header.
- Common live/history renderer and preference-controlled activity disclosure.
- Single answer painter; remove competing content buffers and paint branches within scope.
- Remove separate live task bar/controller after all commands are migrated.

Exit: U01–U08 and relevant lifecycle/browser checks pass; source inventory contains no unexplained competing turn-content writer.

### Phase 3 — Grouped approval integration

Deliver:

- One approval region with keyed rows and canonical labels.
- Real approve/deny/resume behavior, command feedback, and preserved argument previews.
- Four sequential gates and concurrent-client integration/browser tests.

Exit: all approval scenarios pass through actual application routes and graph; no individual top-level approval cards or parallel controls remain in Web chat.

### Phase 4 — Recovery, integration, and hardening

Deliver:

- Full acceptance matrix, crash-window tests, long-history performance and accessibility checks.
- Compatibility checks for dashboard decisions, gateway approval, SSE/WS delivery, attachments, voice, composer steering, and existing HITL paths.
- Bounded invariant diagnostics and recovery behavior.

Exit: live/recovered equivalence proven; no skipped mandatory tests; no unexplained regressions in protected subsystems.

### Phase 5 — Release evidence and cleanup

Deliver:

- CI-required lifecycle job, packaged-build smoke, screenshots/recordings, traces on failure, and build identity.
- Completed removal inventory, updated architecture/diagnosis documentation, and acceptance report.
- Rollback rehearsal on copied test data and cache-busting verification.

Exit: release criteria below met. No production-grade claim before this phase passes.

## 13. CI and release enforcement

- Add a required unified-turn lifecycle job to `.github/workflows/ci.yml`; run it on all relevant core/UI/protocol/static/test changes, and on release builds regardless of path filters.
- Retain existing HITL and delivery suites. Update obsolete layout assertions rather than disabling the suites.
- Run relevant Python compile checks and `node --check` for changed JavaScript; these supplement behavioral tests.
- Required E2E tests must fail if dependencies/harness are absent. Do not treat `importorskip`, xfail, or retries that conceal deterministic failure as acceptance.
- Store screenshots and traces on failure plus a representative successful four-gate recording for the release evidence.
- Add a narrow architecture check prohibiting retired bar markup and unauthorized turn-content writers. Static checks are boundary enforcement, not substitutes for browser tests.
- Ensure the required job is actually required by repository branch protection/rulesets; a workflow file alone is not merge enforcement. Record any administrative dependency honestly.
- New event types or rendering paths must add shared fixtures and lifecycle coverage before merge.

## 14. Rollout, compatibility, and rollback

1. Build and test with an isolated data directory. Never use the operator's active approvals as test fixtures.
2. Prefer additive, versioned storage changes and one-time normalization. Avoid destructive migrations.
3. Validate the packaged frontend and backend together, including static asset versions and stale-client protocol behavior.
4. If a temporary rollout switch is needed, select exactly one renderer per session; never run two DOM writers. Define its removal phase and owner before introducing it.
5. Reconcile active turns through server snapshots at cutover. Do not resume or re-execute tools solely because the renderer changed.
6. Preserve gate/checkpoint semantics across rollback. Verify the previous compatible build can read the data; if it cannot, define forward recovery rather than suggesting an unsafe downgrade.
7. Rollback restores a build, never approval databases or checkpoints from an older copy that could replay completed work.
8. Release-blocking findings include lost content, duplicate execution, ghost actionable gates, missing active approvals, cross-session painting, failed recovery convergence, or a remaining competing painter.
9. Conduct a controlled manual lifecycle smoke on the installed build after automated verification. Observation after rollout supplements tests; it is not a substitute for them.

## 15. Protected boundaries and non-goals

Preserve:

- HITL registry CAS and graph interrupt/resume, swarm bus, pipeline checkpoint, and semantic gate mechanisms.
- `close_turn` as terminal authority; `turn_failed` and permanent/transient LLM failure handling.
- Canonical danger-tool list, grant scopes, and no double-gating behavior.
- Platform isolation and existing gateway routing.
- ConfigStore singleton/transaction conventions and database migration discipline.
- Existing single-process journal/storage deployment limits; this work does not establish multi-replica correctness.

Out of scope:

- Framework rewrite, replacing LangGraph, a new approval endpoint, or redesigned agent reasoning generation.
- Automatic bulk approval of unknown future actions.
- A second status surface, side panel, or transcript copy.
- General redesign of dashboard/TUI/gateway presentation. Their existing decision synchronization must remain correct.
- Broad unrelated `chat.js` cleanup, new notification infrastructure, or indefinite parallel legacy implementations.

## 16. Risks and decisions to resolve during implementation

| Risk / decision | Required resolution |
|---|---|
| App-graph test harness currently fails to pause as intended | Inject deterministic provider behavior at the real boundary and prove interrupt/resume before UI claims. |
| Reasoning may not be durable until completion | Audit actual writes, implement durable publication semantics, test restart windows. |
| Independent stores lack cross-store transactions | Define operation ordering and reconciliation; test each crash window. |
| Current guards keep stale DOM to avoid disappearance | Move preservation to state and explicit snapshot coverage before removing guards. |
| Broad `chat.js` coupling | Inventory callers and migrate by responsibility with integration tests. |
| Old gate rows lack complete view coverage | Canonical server hydration; no client guessing from DOM or elapsed time. |
| Large thought/tool histories | Durable complete content plus measured incremental rendering/paging. |
| Stale cached assets | Build identity, cache-busting, packaged smoke, and compatibility checks. |
| Existing docs/tests encode the old UI | Explicit supersession and updated assertions in the same implementation series. |

Do not estimate a calendar completion date until Phase 0 resolves the harness and durability scope. Those are material engineering dependencies, not small CSS tasks.

## 17. Final acceptance checklist

- [ ] One assistant turn block with integrated status header; bottom bar and controller removed.
- [ ] Thoughts collapsed by default; user choice preserved during updates.
- [ ] Thoughts remain after final answer, refresh, session switch, and verified restart recovery.
- [ ] Exactly one approval group, with four correctly identified requests in the sequential fixture.
- [ ] Real approval/resume through the app graph passes; no fake endpoint-only substitute.
- [ ] Single answer region throughout streaming and finalization.
- [ ] One state projection and one rendering owner; removal inventory complete.
- [ ] Server-authoritative decision, execution, completion, and timeout semantics preserved.
- [ ] Live/reconnect/history/restart convergence passes.
- [ ] Existing HITL paths, failure surfacing, and cross-surface decisions remain correct.
- [ ] Performance, focus, keyboard, mobile, RTL, and scroll checks pass.
- [ ] Required CI tests run without skips; branch enforcement verified or dependency explicitly reported.
- [ ] Packaged-build smoke, build identity, and representative browser evidence attached.
- [ ] Migration/rollback tested without reverting execution or approval history.
- [ ] Conflicting documentation superseded; no temporary dual-renderer path left without a removal commitment.

Acceptance report format: invariant/scenario, test or evidence link, tested build, result, and any limitation. A green unit suite or an “industrial” commit title is not completion evidence.
