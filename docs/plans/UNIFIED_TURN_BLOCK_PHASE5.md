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
completion evidence." That is §5 below. Everything before it is what
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
procedure. It belongs in the runbook, and it is repeated in §6 below.

---

## 5. Acceptance report (§17)

**Tested build:** `104823e2` plus this phase's working tree, Windows 11,
Python 3.12.9, Node v24.20.0, Chromium via Playwright.
**Method:** every figure below is from a run on that tree; none is
carried over from an earlier phase's report.

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
| 3 | Thoughts survive final answer, refresh, session switch, restart | `…browser.py`, `…recovery.py`, `…restart.py` | **pass** | Restart recovery is single-process; §15 excludes multi-replica |
| 4 | Exactly one approval group, four identified requests | `…app_graph.py`, `…browser.py::test_sequential_allow_tool_in_one_bubble` | **pass** | — |
| 5 | Real approval/resume through the app graph; no endpoint-only substitute | `…app_graph.py` (6 tests, real `interrupt()`/`POST /api/approve`) | **pass** | — |
| 6 | Single answer region throughout | `tests/js/test_turn_view.js`, `test_unified_turn_a11y.py` | **pass** | — |
| 7 | One projection, one rendering owner; removal inventory complete | §1, `test_turn_render_boundary.py` | **pass** | — |
| 8 | Server-authoritative decision/execution/completion/timeout semantics | `test_approve_decides_one_gate.py`, `test_hitl_gates.py`, `…concurrency.py` | **pass** | WS approve path unfixed — §6 |
| 9 | Live / reconnect / history / restart convergence | `tests/js/test_turn_convergence.js`, `…recovery.py`, `…restart.py` | **pass** | "Mid-token" is staged with a scripted stream, not a split packet |
| 10 | Existing HITL paths and cross-surface decisions still correct | 373 compatibility tests (Phase 4 §7) | **pass** | — |
| 11 | Performance, focus, keyboard, mobile, RTL, scroll | `tests/js/test_turn_performance.js`, `test_unified_turn_a11y.py`, `…browser.py::test_the_answer_survives_a_phone_in_rtl` | **pass** | — |
| 12 | Required CI tests run without skips; branch enforcement verified **or reported** | §6 | **reported, not met** | `main` has no branch protection at all — §6 |
| 13 | Packaged-build smoke, build identity, browser evidence | §3, §4, `test_turn_assets_ship.py` | **partial** | Build identity and browser evidence are done; the "smoke" is static + served-tree + cache-bust checks, and never boots a built wheel |
| 14 | Migration/rollback tested without reverting execution or approval history | §4, `test_turn_rollback_rehearsal.py` | **pass** | The "never restore an older database" half is procedure, not test |
| 15 | Conflicting documentation superseded; no dual-renderer path left | §1 | **pass** | — |

---

## 6. Limitations, stated rather than buried

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

**The plain-string failure convention was not audited exhaustively.**
Phase 4 added `Safety:` to the two prefixes `tool_registry` already
recognised. A tool reporting failure in a fourth shape would still be
recorded as a success.

**Rollback procedure (not enforceable by test).** Restore the build.
Do **not** restore `hitl_gates.db`, `checkpoints.db` or the session store
from an older copy: an older approval database can replay work that has
already completed (§14.7). If a future schema change makes the previous
build unable to read current rows, the answer is forward recovery, not a
downgrade (§14.6).

---

## 7. What this plan delivered

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
