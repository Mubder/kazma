# Unified turn block — Phase 5: release evidence and acceptance

Date: 2026-09-20

Status: Phase 5 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
Builds on [Phase 0](UNIFIED_TURN_BLOCK_PHASE0.md),
[Phase 1](UNIFIED_TURN_BLOCK_PHASE1.md),
[Phase 2](UNIFIED_TURN_BLOCK_PHASE2.md),
[Phase 3](UNIFIED_TURN_BLOCK_PHASE3.md) and
[Phase 4](UNIFIED_TURN_BLOCK_PHASE4.md).

Plan §17 asks for an acceptance report in a specific shape:
"invariant/scenario, test or evidence link, tested build, result, and any
limitation. A green unit suite or an 'industrial' commit title is not
completion evidence." That is §7 below. Everything before it is what
Phase 5 changed to make that report possible.

---

## 1. The removal is finished

Phases 2 and 3 both closed with the same caveat: the pre-V2 progress
painter was still in `chat.js`, reachable if the projector module failed
to load. Plan §14.4 — "never run two DOM writers" — and §17 — "One state
projection and one rendering owner; removal inventory complete" — do not
leave room for it, so it is gone.

**What was removed** (322 lines deleted from `chat.js`, 35 added back as the comments explaining the removal — net −287):

| Symbol | Why it could go |
|---|---|
| `ensureProgressPanel` | One caller: the fallback below |
| the tail of `logProgress` | Everything after the projector dispatch |
| `_startProgressTimer` | 3 references → 1 (its own definition) |
| `markPlanProgress` | 2 → 1 |
| `_isUserBubble` | 2 → 1 |
| `_cotPhasesHtml` | 2 → 1 |
| `_maybeAddSourceChip` | 3 → 1 |

**What stays, and why:** `_progressEl`, `_planItems`, `setPlan`,
`_renderPlanList`, `_panelSeq`, `_stepRowHtml`, `_detailHtml`,
`_wireStepToggles`, `_localizeCotTitle`. The projector path uses all of
them — `_buildRestoredWorkbench` builds the same `.agent-progress` region
the legacy painter used to. An inventory has to say which of both.

**Why deleting the fallback was safe rather than brave.** It could only
run when `window.KazmaTurnDocument` was absent, and in that state
`_docs.live` is never created (`chat.js` ~1758), so `_answerFromDoc`
returns `""` and the turn has no answer text at all. The fallback would
have painted progress over a chat that cannot show replies. A second
writer that can only ever produce a half-rendered turn is not a safety
net; it is what invariant U03 exists to forbid.

`tests/test_chat_as_product.py::test_live_assistant_bubble_is_pinned_not_minted`
was an incident lock anchored on `ensureProgressPanel` — the 2026-09-02
CoT ladder, one bubble per status hop. It was **re-pointed, not deleted**:
`renderTurn` owns the rule now and states it the same way, *a document
with nothing to show does not get a bubble*, and the assertion follows it
there plus a new one that the painter has not come back.

Five comments still explained live behaviour by reference to the Live
Task Card, deleted in Phase 2. Those are rewritten. The tombstones —
"What stood here was the Live Task Card" — stay, and
`test_the_tombstones_survive` keeps them: a rule that only forbids is
satisfied by deleting the explanation along with the code.

---

## 2. The architecture check (§13)

> Add a narrow architecture check prohibiting retired bar markup and
> unauthorized turn-content writers. Static checks are boundary
> enforcement, not substitutes for browser tests.

`tests/test_turn_render_boundary.py`, 7 checks:

* no `live-task-card`, `live-task-*` or `thinking-indicator` hook in any
  shipped JS, CSS or template — **in code lines only**, so the tombstones
  that explain the removal stay legible;
* the tombstones are still there;
* `ensureProgressPanel` is gone and `logProgress` calls no `createElement`
  — it dispatches, it does not paint;
* `chat.js` never inserts a turn region itself; `turn_view.js` keeps the
  single ordering pass, and there is exactly one `renderTurn`;
* the renderer still reports `duplicate-region:`, `gate-outside-group:`,
  `gate-missing:`, `text-missing`, `text-blank`.

Narrow is the operative word: these say *who may write*, never *whether
it looks right*. The browser suite owns the second question and neither
file substitutes for the other.

---

## 3. Evidence, and the build it came from (§13)

The browser suite now records itself:

* **Traces** start for every test and are kept **only on failure**. A
  failing CI browser test used to report one sentence; it now ships the
  frames, the DOM at that moment and the console.
* **Screenshots and console logs** on failure, best-effort — a page that
  crashed cannot be screenshotted, and a missing screenshot must never
  replace the real failure.
* **A video recording of the four-gate turn**, kept whether or not it
  passes, because that recording *is* the release evidence §13 asks for
  and evidence you capture only when things break is not evidence of the
  thing working.

Measured: a passing four-gate run leaves a 936 KB `video/` and would have
left a 14 MB `trace.zip`. The trace is a failure diagnostic, so on a green
run it is deleted — 14 MB per run of a file nobody opens when nothing
broke.

Artifacts land in `test-artifacts/unified-turn/<test name>/` (gitignored),
and the CI job uploads that directory with `if: always()`, because the run
that failed is the run whose trace is worth having.

**Build identity** is written *before* the suites run, so a failed job
still says which build failed: commit, ref, run id and attempt, runner OS
and arch, Python, Node and Playwright versions, UTC timestamp, and the
installed `kazma-*` packages. It lands in the uploaded directory, so the
archive answers "which build is this?" on its own.

---

## 4. Packaging, cache-busting and rollback (§14)

### The front-end actually ships — `tests/test_turn_assets_ship.py` (15 checks)

Three ways the turn block can be right in the repository and broken in a
browser, none of which any other test in this plan would notice:

1. **A module nobody loads.** `turn_presentation.js` and
   `turn_preferences.js` are new in this plan. A module missing from the
   template is not on the page — and all of its unit tests still pass,
   because they run under bare node where there is no template.
2. **A module the server will not serve.** Checked to be inside the
   packaged `kazma_ui/static` tree, non-empty, and covered by a packaging
   directive.
3. **A cached module.** Every turn module is referenced with `?v=`, and
   the mechanism is *exercised*, not asserted: touch
   `static/js/modules/turn_view.js`, confirm the version moves, restore
   the mtime. `app.py`'s `_js_version` globs the whole JS tree —
   hand-maintained whitelists previously missed whole directories and
   served stale JS against fresh HTML (UI audit P1-2), and `modules/` is
   exactly the kind of directory a whitelist forgets.

### Rollback rehearsal — `tests/test_turn_rollback_rehearsal.py` (10 checks)

A rollback restores code, not data. So the question is never "does the
old build work" but "does the old build work **against rows the new build
already wrote**". The old reader is checked out of git at a pinned commit
(`e1367532`, immediately before Phase 1b introduced
`TURN_SCHEMA_VERSION`) rather than described from memory, because "the
old code probably ignores unknown keys" is a prediction and this is meant
to be a rehearsal.

Rehearsed, with a row built through the shipped merger so its part keys
are the ones the product mints:

* the previous build still finds the answer text;
* `schema`, `rev` and activity `id` do not make it raise — an older
  reader may ignore new keys, it may not choke on them, because the row
  it cannot parse is every row written since the upgrade;
* one gate stays one gate: not dropped (a completed turn with no record
  anything was asked) and not doubled (two decisions for one gate);
* and the forward direction, which every upgrade actually performs: a row
  with no `schema` and positional part keys — all of existing history —
  still reads on this build.

Then the same rehearsal over the whole recorded corpus in
`tests/fixtures/unified_turn/messages/` rather than one specimen: four
gates sharing a tool, a pending gate, reasoning mixed with tools and
status, tool calls carrying ids, a non-ASCII row, and a pre-parts row
with nothing but content. Any one of those could be the shape the older
reader chokes on, and those fixtures are what both language projectors
are already locked against — so they are the shape the product stores,
not a shape a test invented while holding the same assumptions as the
code.

**Not testable, therefore stated as procedure.** §14.7: *a rollback
restores a build, never approval databases or checkpoints from an older
copy that could replay completed work.* No test can enforce an operator's
procedure. It belongs in the runbook, and it is repeated in §8 below.

---

## 5. The full suite, and four locks this plan invalidated

9,596 tests (`-m "not e2e"`; the browser suites run under their own job
and are reported above). It took four runs to get a clean one:

| Run | Result |
|---|---|
| 1 | 4 failed, 9,558 passed, 36:49 |
| 2 | **crashed at 19%** — native access violation, see below |
| 3 | 1 failed, 9,568 passed, 37:00 |
| 4 | **9,569 passed, 24 skipped, 3 xfailed, 0 failed, 36:26** |

None of the failures was a behaviour regression, and none was deleted.
Plan §13: "Update obsolete layout assertions rather than disabling the
suites."


| Test | Cause | Phase |
|---|---|---|
| `test_setplan_and_memory_explain_never_create_panels` | stale source-grep lock | 5 |
| `test_file_write_workspace_not_drive_root` | leaked `KAZMA_DATA_DIR` | 1 |
| `test_stream_silence_journals_turn_heartbeats` | stale source-grep lock | 1 |
| `test_live_voice_mints_the_user_row_not_the_assistant` | stale source-grep lock | 2 |

Three of the four had been red since earlier phases of this plan shipped
without a full-suite run. All four are this plan's to fix regardless of
which phase broke them.

**The attribution above is a correction.** The first pass compared
against a worktree at the Phase 4 tip and ran the four tests *in
isolation* there. Two passed, so I recorded them as Phase 5 regressions.
An isolated run cannot show an ordering bug, so that evidence never
supported the conclusion — and one of the two, the workspace test, was
not a Phase 5 regression at all. What follows is what the third full run
actually measured.

**One was leaked global state, from Phase 1.**
`test_file_write_workspace_not_drive_root` failed in the full run and
passed in isolation — the signature of state left behind by something
earlier. The third run's assertion named it:

```
assert ws.parent.name == "kazma-data"
E  AssertionError: assert 'test_upsert_...the_revision0' == 'kazma-data'
```

`tests/test_turn_document.py::test_upsert_bumps_the_revision_on_every_write`
set `os.environ["KAZMA_DATA_DIR"] = str(tmp_path)` **raw**, with no
restore, so every test after it in the session inherited a data
directory pointing inside that test's `tmp_path`. Now `monkeypatch`.

*A wrong diagnosis, recorded because it was acted on.* I first blamed a
workspace **pin**: `resolve_active_root()` does not only read the ladder,
it memoises it — rung 2 assigns the active WorkspaceStore row into
`binding._WORKSPACE_ROOT`, so merely *asking* where the workspace is
installs a process pin at rung 3, and Phase 4's isolation dropped the
store on teardown while leaving the pin. That memoisation is real and
the teardown now clears it
(`test_the_harness_teardown_clears_the_workspace_pin` fails if it stops).
But it cannot have caused **this** failure: the failing test's first
statement is `configure_workspace(workspace=None, …)`, which clears any
pin before it reads anything. The fix was hygiene, not the cure, and the
test went on failing with it in place.

**Three were source-grep locks pointing at code this plan moved**, each
re-pointed to where the rule lives now — plan §13: "Update obsolete
layout assertions rather than disabling the suites.":

* *setPlan must never mint a panel* — `ensureProgressPanel` no longer
  exists, so the rule is now unrepresentable rather than obeyed. The
  assertion says that, and adds "no `createElement` in `setPlan`" so it
  still guards the 2026-09-03 phantom-workbench incident by any route.
* *voice mints the user row, not the assistant* — `tokenAccum` was
  deleted in Phase 2 when the document became the single answer
  authority. Re-pointed to the surviving latch (`currentMsgEl = null`,
  `_turnPainted = false`) plus a check that the accumulator has not come
  back.
* *the resumed graph heartbeats* — the assertion read a fixed 900
  characters after an anchor comment, and Phase 1's delta-queue work grew
  that comment past the window. The behaviour never changed. Re-sliced to
  the next `emit_j("turn_heartbeat"` rather than a character count.

A theme worth naming, because it cost four separate diagnoses in this
session: **source-grep assertions and comments interact badly**, in two
different ways.

*Matching the comment that explains the removal.* You delete a function,
you write a comment saying why, and the assertion `"foo()" not in js`
now matches your own explanation. That is what broke
`test_setplan_and_memory_explain_never_create_panels`, and it caught two
of the tests written in this phase before they were committed.

*A comment growing past a fixed window.* `test_stream_silence_journals_turn_heartbeats`
read 900 characters after an anchor comment; Phase 1's delta-queue work
made that comment longer and the window stopped reaching the code.

The fixes are different. For the first, strip comment lines before
searching — which `test_turn_render_boundary.py`,
`test_chat_steer_composer.py` and `test_voice_ws_pipeline.py` now do. For
the second, slice to a structural landmark instead of a character count.
Neither is served by making the assertion vaguer.

Re-run after the fixes: **9,569 passed, 24 skipped, 3 xfailed, 0 failed, 36:26** — the fourth full run of this session and the first clean one.

### A native crash, found on the way

The first re-run did not finish. At 19% it died with

```
Windows fatal exception: access violation

Current thread (most recent call first):
  kazma_core/config_store.py line 1694 in get_category
  kazma_core/security/web_sessions.py line 160 in purge_expired_sessions
  concurrent/futures/thread.py line 59 in run
```

Not a test failure — a hard interpreter crash, in a **background
thread**, with no test on the stack.

`worker_bootstrap._start_session_purge_scheduler` sleeps 120 seconds
after the memory worker boots and then runs `purge_expired_sessions` via
`asyncio.to_thread`. In a long run, some test boots that worker and two
minutes later the purge reads the ConfigStore on a pool thread — while a
per-test fixture is closing that store's SQLite connection. Use after
close, at the C level. `purge_expired_sessions` wraps its read in
`try/except` and logs "purge skipped", but an access violation is not an
exception and nothing catches it.

This is not this plan's code and not this plan's to fix — nothing in
Phases 0–5 touches `web_sessions`, `worker_bootstrap` or the ConfigStore
connection lifecycle. It is recorded because:

* a full suite that can die at 19% makes "the suite is green" a
  statement about luck, and this plan's exit criteria depend on that
  statement;
* the same shape is reachable outside tests. Any runtime path that
  closes or swaps the ConfigStore while the 6-hourly purge is in flight
  has the same race, and it would take the process down rather than log
  a warning.

The run before it completed all 9,588 tests, and the one after is
reported above, so the crash is intermittent — which is what a timing
race looks like, not evidence that it is harmless.

---

## 6. The CI gate had been red since Phase 3

Checked only at the very end of Phase 5, which is three phases too late.

| Phase | `Unified turn lifecycle (GATE)` |
|---|---|
| 0 – 2d | success |
| **3** | **failure** — the browser step |
| **4** | **failure** — the app-graph step |
| **5** | **failure** — the app-graph step |

Both failures are Linux-only. Every one of them ran green on Windows, by
hand, which is what the phase reports were written from.

**Phase 3's** red step was "What the operator sees (HITL_VIEW_MODEL
Playwright 1 and 4)" — the provider-configuration bug Phase 4 §2
describes, where a pinned model resolved to the shipped OpenAI profile.
Phase 4 fixed it, and Phase 4's own report explains the mechanism
without mentioning that CI had been reporting it for a week.

**Phase 4's and 5's** red step is "Four sequential gates through the real
app graph", running `.FFFFF` — the first test passes and the other five
die with `sqlite3.OperationalError: unable to open database file` out of
`SessionManager._upsert_db`.

That one was introduced by Phase 4's move to a per-test app.
`reset_session_manager()` does not merely drop the singleton: it
**creates a replacement**, at `data_dir()/chat_sessions_test_<pid>.db`.
The harness teardown called it while `KAZMA_DATA_DIR` still pointed at
the harness's own temporary directory, so the process-wide singleton
ended up rooted inside a directory the enclosing `with` block then
deleted. Every later test in the file inherited it — hence one pass and
five failures rather than six failures.

Windows never showed it, and the reason is worth stating because it will
recur: `TemporaryDirectory` cannot delete a directory whose SQLite
handles are still open, `ignore_cleanup_errors=True` swallows that
failure, and the stale path therefore still exists when the next test
looks for it. Linux deletes it and the next test dies.

The fix is an ordering one — restore the environment *before* resetting
the singletons, both before the directory goes. It is two lines. The fix
was never the expensive part.

### What this says about the evidence

Every "proven end to end" claim in Phases 3–5 rests on runs performed on
one operating system, by hand. The lifecycle job exists precisely so
that is not the only check, and it was failing the whole time.

Plan §13 anticipated the shape of this: "Required E2E tests must fail if
dependencies/harness are absent. Do not treat `importorskip`, xfail, or
retries that conceal deterministic failure as acceptance." The job did
fail, correctly and loudly, every time. Nothing concealed it. It simply
was not read — which is the same outcome, and is why checklist item 12
(§8) matters more than it looks: a gate nobody is required to pass is a
gate nobody notices.

---

## 7. Acceptance report (§17)

**Tested build:** `104823e2` plus this phase's working tree, Windows 11,
Python 3.12.9, Node v24.20.0, Chromium via Playwright.
**Method:** every figure below is from a run on that tree; none is
carried over from an earlier phase's report.

**Platform:** all of it Windows. §6 is about what that cost, and four
rows below are marked "CI pending" because their evidence has never been
observed green on Linux. Nothing here should be read as a
platform-independent claim until the lifecycle job passes.

Re-run after the 310-line removal, on that tree:

| Suite | Result |
|---|---|
| Phase 5's new static checks (boundary, assets, rollback) | 32 passed, <2s |
| turn contract + chat source locks (13 files) | 243 passed, 1 skipped, 11.90s |
| node projector suites (8 files) | all green; 9 performance properties held |
| `tests/e2e/test_unified_turn_browser.py` | 7 passed, 146.38s |

| # | Checklist item | Evidence | Result | Limitation |
|---|---|---|---|---|
| 1 | One turn block with integrated header; bottom bar and controller removed | `test_turn_render_boundary.py`, `…browser.py`, `tests/js/test_turn_view.js` | **pass** | — |
| 2 | Thoughts collapsed by default; choice preserved during updates | `…browser.py::test_the_fold_starts_collapsed_and_stays_where_the_reader_puts_it`, `tests/js/test_turn_preferences.js` | **pass** | — |
| 3 | Thoughts survive final answer, refresh, session switch, restart | `…browser.py`, `…recovery.py`, `…restart.py` | **pass on Windows; CI pending** | Restart recovery is single-process (§15 excludes multi-replica); CI green not yet observed — §6 |
| 4 | Exactly one approval group, four identified requests | `…app_graph.py`, `…browser.py::test_sequential_allow_tool_in_one_bubble` | **pass on Windows; CI pending** | The lifecycle job was red on Linux for three phases — §6 |
| 5 | Real approval/resume through the app graph; no endpoint-only substitute | `…app_graph.py` (6 tests, real `interrupt()`/`POST /api/approve`) | **pass on Windows; CI pending** | Same: green by hand, `.FFFFF` in CI until the teardown fix — §6 |
| 6 | Single answer region throughout | `tests/js/test_turn_view.js`, `test_unified_turn_a11y.py` | **pass** | — |
| 7 | One projection, one rendering owner; removal inventory complete | §1, `test_turn_render_boundary.py` | **pass** | — |
| 8 | Server-authoritative decision/execution/completion/timeout semantics | `test_approve_decides_one_gate.py`, `test_hitl_gates.py`, `…concurrency.py` | **pass** | WS approve path unfixed — §8 |
| 9 | Live / reconnect / history / restart convergence | `tests/js/test_turn_convergence.js`, `…recovery.py`, `…restart.py` | **pass**; e2e half CI-pending | "Mid-token" is staged with a scripted stream, not a split packet; the node half is platform-independent, the e2e half is §6 |
| 10 | Existing HITL paths and cross-surface decisions still correct | 373 compatibility tests (Phase 4 §7) | **pass** | — |
| 11 | Performance, focus, keyboard, mobile, RTL, scroll | `tests/js/test_turn_performance.js`, `test_unified_turn_a11y.py`, `…browser.py::test_the_answer_survives_a_phone_in_rtl` | **pass** | — |
| 12 | Required CI tests run without skips; branch enforcement verified **or reported** | §5, §6, §8 | **reported, not met** | `main` has no branch protection at all — §8 |
| 13 | Packaged-build smoke, build identity, browser evidence | §3, §4, `test_turn_assets_ship.py` | **partial** | Build identity and browser evidence are done; the "smoke" is static + served-tree + cache-bust checks, and never boots a built wheel |
| 14 | Migration/rollback tested without reverting execution or approval history | §4, `test_turn_rollback_rehearsal.py` | **pass** | The "never restore an older database" half is procedure, not test |
| 15 | Conflicting documentation superseded; no dual-renderer path left | §1 | **pass** | — |

---

## 8. Limitations, stated rather than buried

**Branch protection does not exist on this repository.** Plan §13:
"Ensure the required job is actually required by repository branch
protection/rulesets; a workflow file alone is not merge enforcement.
Record any administrative dependency honestly." Measured:

```
$ gh api repos/Mubder/kazma/branches/main/protection
{"message": "Branch not protected", "status": "404"}
```

So this is not a matter of adding one context to an existing ruleset —
there is no ruleset. And enabling one is not a neutral act here: this
repository's working practice is to commit and push straight to `main`,
which a protected branch with required status checks would reject.
Turning it on would trade an unenforced gate for a blocked workflow, and
that trade is the repository owner's to make, not this plan's. **The
lifecycle job runs on every push; it does not block a merge. Item 12 of
the checklist is not met, and is recorded rather than quietly rounded
up.**

**The WS approve path still has Phase 4's defect.**
`routes/ws_chat.py`'s `approve_tool` reads the pending interrupt and
resumes it without matching the requested gate id — its payload carries
no id to match against. It is off unless `KAZMA_WS_GRAPH=1`, and when off
it answers "HITL resume uses POST /api/approve/{thread_id}". Giving it
the same guard means giving its clients an id to send, which is a
protocol change §15 puts out of scope. Recorded, not fixed.

**The packaged-build smoke does not build a wheel.** It checks that every
turn module is inside the packaged static tree, that packaging declares
static assets, and that cache-busting actually moves. It does not `pip
install` a built wheel and boot it. That is a real gap between "the files
are in the right place" and "the installed build serves them".

**"Disconnect mid-token" is staged.** The recovery suite's script
narrates ~450 characters so the first leg really streams and the
disconnect lands between deltas. It is not a disconnect inside a single
network packet.

**A background thread can crash the process, and it is not fixed here.**
See §5: `purge_expired_sessions` reads the ConfigStore from a pool
thread on a 6-hourly cadence, and a concurrent close of that store's
SQLite connection is a native access violation rather than a catchable
exception. Out of scope for this plan — it predates it and touches none
of its code — but it is the kind of thing that turns "the suite is
green" into a statement about timing.

**The plain-string failure convention was not audited exhaustively.**
Phase 4 added `Safety:` to the two prefixes `tool_registry` already
recognised. A tool reporting failure in a fourth shape would still be
recorded as a success.

**The manual lifecycle smoke on the installed build has not been done,
and is not this session's to do.** §14.9: "Conduct a controlled manual
lifecycle smoke on the installed build after automated verification.
Observation after rollout supplements tests; it is not a substitute for
them." Everything above ran against the repository working tree. The
installed build is the operator's, with its own data directory and its
own vault, and nothing here has touched it. The smoke is one turn with
four approvals on that build, watched: one block, one header, the fold
closed, four rows in ask order, the answer below them, and the same
after a refresh.

**Rollback procedure (not enforceable by test).** Restore the build.
Do **not** restore `hitl_gates.db`, `checkpoints.db` or the session store
from an older copy: an older approval database can replay work that has
already completed (§14.7). If a future schema change makes the previous
build unable to read current rows, the answer is forward recovery, not a
downgrade (§14.6).

---

## 9. What this plan delivered

Three defects in shipped code, each found by writing the test the plan
asked for rather than by reading:

1. **A decision did not name its gate.** A retried Approve decided
   whichever question the graph had reached — approving a file write
   authorized a shell command (Phase 4 §1).
2. **A refused tool was reported as completed.** Five file tools returned
   a `Safety:` refusal that the registry classified as success, so the
   approval row told the reader their approved write had succeeded
   (Phase 4 §4).
3. **The harness had the operator's repository as its workspace.**
   Nothing was written to it, but only by geometry (Phase 4 §3).

And two in the test suite that were passing for the wrong reason: the
execution assertion that survived on a file an earlier test left behind,
and the four-gate layout that had never been checked at phone width in
RTL.
