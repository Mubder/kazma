# Context Integrity Hardening — Execution Plan

Derived from the live failure of 2026-08-30 19:00–19:25 (@KazmaAI tweet batch):
the assistant proposed 8 drafts, the user approved them one turn later, and the
drafts no longer existed. Two turns were then burned trying to recover them, the
second died mid-sentence, and a third answered a question the user never asked.

**Status: EXECUTED 2026-08-30.** S1-1, S1-2, S1-3, S1-4, S2-1, S2-2, S2-3,
S3-1 landed (`tests/test_context_integrity.py`, 56 tests). S3-2
investigated, **then reproduced the same day** — two failing tests emitted
the incident string verbatim — and **fixed at all three re-stream sites**
(`tests/test_s32_stream_duplication.py`): see
`docs/plans/S3_2_DUPLICATED_STREAM_INVESTIGATION.md`.

This plan treats the incident as a *class* of defect, not an instance. The
governing requirement is the user's: this must never happen again. That rules
out probability reduction as the only strategy — one layer has to make the
failure structurally impossible, and the rest reduce how often we lean on it.

---

## The failure chain, as established

Each link verified against the source, not inferred.

| # | Link | Evidence |
|---|------|----------|
| 1 | Drafts were deleted from context, not misplaced | `trim_messages_deterministic` keeps anchors + latest user msg + last 16 tail msgs; everything prior is dropped — `turn_input.py:1163` |
| 2 | The scratchpad that was built to prevent exactly this was empty | `update_scratchpad` is soft rule #4 in the anchor; nothing forces a write — `turn_input.py:854` |
| 3 | …and would have been wiped even if written | `build_turn_working_memory` returns `scratchpad: {}`; `input_state.update(_wm)` at `ws_chat.py:1536` / `sse_chat/__init__.py:933`; `SupervisorState` is a bare `TypedDict` with **zero** `Annotated` reducers → LangGraph `LastValue` = replace |
| 4 | The summary net never fired | trim at `min(24000, window×0.6)`; `inject_summary_of_dropped` gated on `should_compact` = `window×0.8` — `graph_supervisor.py:1176`, `token_counter.py:80` |
| 5 | Recovery made it worse | every DB query searching for the lost drafts added tool output to the history whose size caused the trim |
| 6 | `what going on?` was classified as a topic change | misses all regex gates, falls through to embedding drift ≥ 0.55 — `turn_input.py:315` |
| 7 | …which disarmed the recovery subsystems | `should_suppress_memory_recall` and `should_quarantine_documents_search` both return `True` on `intent_mode == "shift"` |
| 8 | The user never learned any of this happened | trim/stub log at INFO server-side; nothing reaches the UI |

Empirically confirmed:

```
build_turn_working_memory('send the English ones now')
  → scratchpad: {}          # every turn, unconditionally
grep "Annotated\[" kazma-core/kazma_core/agent/   → (nothing)
StateGraph(SupervisorState)                        # graph_builder.py:255
```

### The dead band is not a corner case

For a 200K-context model: trim fires at **24,000** tokens, the summary net at
**160,000**. Everything between is silent deletion. That is not an edge — for
any modern model it is the *normal* operating range, so in practice the summary
net has effectively never protected a trim. This is the single widest hole.

---

## Design decisions (industrial default chosen at each fork)

| Fork | Chosen | Rejected, and why |
|------|--------|-------------------|
| Prevent context loss vs. make loss survivable | **Make it survivable** — durable artifact store; loss-prevention is defence in depth on top | Any in-context scheme has a size bound; a bound that can be exceeded will be exceeded. Approval must not depend on remembering |
| Scratchpad in graph state vs. in SQLite | **SQLite, keyed `(tenant, thread, key)`**, state holds a read-through cache | A graph channel is exactly what failed. A reducer fixes today's bug; a store removes the category — survives trim, turn boundaries, restart, and checkpoint corruption |
| Merge reducer vs. drop key at transport | **Both** — reducer is structural, transport drop is belt-and-braces | Either alone leaves the other write path able to clobber |
| Auto-capture proposals vs. explicit tool | **Explicit `save_proposal` + supervisor nudge on enumerated outbound drafts** | Auto-capture on free text is a heuristic that will silently miss; an explicit tool is verifiable and testable |
| Summary net gate | **Fire whenever trim actually dropped user/assistant turns**, heuristic summarizer under ~2K dropped, LLM above | Keeping an 80%-of-window gate means the net stays theatre |
| Suppress recall on drift | **Only on *explicit* abandonment** (`shift_explicit`); inferred drift re-ranks, never disables | Turning off recall the instant context is lost is precisely backwards |
| Interrogative check-ins | **Hard allowlist gate before embedding, EN + AR** | Raising the 0.55 threshold trades one silent misclassification for another; a contentless question is categorically not a topic change |
| `_MIN_CHARS` 12 → 25 | **Raise, and require a content word** | `what going on?` cleared the existing gate by two characters. Length alone was never the right signal |
| Recovery spiral | **Breaker forces RESPOND with an honest "context was trimmed" turn** | Letting the model dig is what converted a recoverable loss into three wasted turns |

---

## Defects and fixes

Severity: **S1** = caused or hid the data loss. **S2** = disabled recovery.
**S3** = observability and correctness of the surrounding UX.

### S1-1 — `scratchpad` is wiped at every user turn

*The engine that was built for this exact scenario is inoperative across turns.*

- Add a merge reducer: `scratchpad: Annotated[dict[str, str], merge_scratchpad]`
  in `SupervisorState`, with an explicit `__clear__` sentinel so it remains
  possible to reset deliberately.
- Remove `scratchpad` from the `build_turn_working_memory` return so the
  transport never contributes an empty dict.
- Bound it: 24 keys, 4000 chars per value (matching what the anchor renders),
  evicting oldest-first, so an unbounded scratchpad cannot itself cause a trim.

Files: `agent/state.py`, `agent/turn_input.py`, `routes/ws_chat.py`,
`sse_chat/__init__.py`.

### S1-2 — Durable turn-artifact store *(the fix that makes this impossible)*

New `kazma_core/agent/artifacts.py` + SQLite table keyed
`(tenant_id, thread_id, key)`, holding value, kind, created-at, and a
content hash.

House patterns are not optional here, and are cheap to get right up front:

- Open through `apply_sqlite_pragmas()` (`config_store.py:284`) — WAL +
  `busy_timeout=5000` + `synchronous=NORMAL`. Do not hand-roll the pragmas;
  a shared helper already exists and every other store uses it.
- Live under `kazma-data/`, which buys universal WAL-safe backup coverage for
  free (`AGENTS.md:935`) rather than needing a new backup path.
- Respect the ops/state DB split rationale (`AGENTS.md:444–450`): artifact
  writes must not WAL-contend with chat recall reads on the hot path.

- `update_scratchpad` writes through to it instead of a process-local dict.
- The working-memory anchor renders from it, so entries survive trim, turn
  boundaries, process restart, and a corrupt checkpoint.
- Retention: per-thread cap plus age-out, wired into the existing GC mark/sweep
  rather than a new sweeper.

This is what changes "unlikely to recur" into "cannot recur". After it lands,
losing conversational context stops being able to destroy approvable content —
the drafts are a row, and the model reads the row.

### S1-3 — Proposals awaiting approval are not persisted

- New `save_proposal(kind, items)` tool → artifact store, returns stable IDs.
- The HITL approval card references proposal IDs, so approve resolves an ID
  rather than trusting that the text is still in context.
- The existing refusal gate stays exactly as it is — it behaved correctly and
  is the reason nothing wrong was posted. It gets a lookup path, not a loosening.

**Where enforcement actually lives.** The supervisor nudge at iteration 0 is
*best-effort only*, and must not be mistaken for the guarantee — a nudge is
ignorable prompt text, i.e. the same mechanism class as link #2 of the incident,
which is exactly what failed. The **enforced chokepoint is the outbound tool**:
`x_post` and its siblings require a resolvable `proposal_id` and refuse without
one. That makes the failure mode explicit and safe — when the nudge is missed,
the outcome degrades to today's correct behaviour ("I can't verify what you
approved") rather than to data loss. A soft rule may improve the hit rate; it
may never be the thing standing between a draft and the wire.

### S1-4 — Summary dead band (24K → 160K)

- Gate `inject_summary_of_dropped` on "did trim drop any user/assistant turn",
  computed from the `_dropped_conversation` diff already implemented in
  `semantic_compact.py`.
- Heuristic summarizer below ~2K dropped tokens, LLM above, so the common case
  costs nothing extra.
- The injected note must name what was dropped ("4 assistant turns including 8
  tweet drafts") rather than only summarizing prose.

### S2-1 — Inferred drift disarms recall

- Split `intent_mode` `shift` into `shift_explicit` (regex: the user actually
  said "never mind" / "موضوع ثاني") and `shift_inferred` (embedding).
- `should_suppress_memory_recall` and `should_quarantine_documents_search` fire
  only on `shift_explicit`.
- `stub_prior_tool_chains` on `shift_inferred` stubs tool *payloads* but keeps
  assistant prose, so a misread pivot can't erase the thing being asked about.

### S2-2 — Interrogative check-ins misread as topic changes

- Gate before embedding: a question that is a check-in on immediate prior work
  is never a shift. English `what/why/how/where/when/what's going on/status/
  wtf`; **Arabic `شنو/وش/ليش/شفيه/وين/متى/شلون/شصار`** — the product is
  Arabic-first and `شنو صار؟` fails identically today.
- Raise `_MIN_CHARS` 12 → 25 **and** require ≥1 content word outside a stopword
  list, so short contentless text fails open instead of scoring as distant.

### S2-3 — The recovery spiral

- Extend `tool_loop_breaker.py`: N≥3 tool calls in one turn against session /
  checkpoint / audit stores whose evident purpose is recovering the assistant's
  own prior output → force RESPOND with an honest "earlier context was trimmed"
  turn naming what is missing and asking one concrete question.
- Cheaper and more honest than any amount of digging, and it converts a
  three-turn spiral into one clear sentence.

### S3-1 — Context loss is invisible

- Emit a UI event when trim drops conversation turns or stubbing collapses a
  chain: a compact "earlier context compacted" chip with a hover listing what
  went. The user should never again have to ask why the assistant forgot.

### S3-2 — The truncated, duplicated stream *(investigate, do not blind-fix)*

`The proposal turn is The proposal turn is` — stream ended mid-sentence with a
repeated fragment. Distinct from the context bugs; likely turn-delivery. Scope
here is root-cause only, against `RELIABILITY_MODEL_AND_TURN_DELIVERY.md` and
`TURN_DELIVERY_V2_CURSOR_RESUME_PLAN.md`. A fix lands only once reproduced.

---

## Order of work

Dependency-ordered; each step ships green.

1. **S1-1** — reducer + transport drop + bounds. Smallest change, closes the
   active bug, unblocks honest testing of everything else.
2. **S1-4** — close the dead band. Independent, high value, no new surface.
3. **S2-2** then **S2-1** — stop misclassifying, then stop the misclassification
   from disarming recall. This order means step 3's tests fail loudly if step 2
   regressed.
4. **S1-2** — the artifact store. Largest change; lands once the surrounding
   behaviour is trustworthy.
5. **S1-3** — proposals + HITL ID binding, on top of the store.
6. **S2-3**, **S3-1** — breaker and UI signal.
7. **S3-2** — investigation, reported separately.

**Residual risk, stated plainly.** The governing principle is that one layer
must make this structurally impossible, and that layer is S1-2 — which lands
fourth. Steps 1–3 are *mitigation*: S1-1 closes the live incident path via
checkpoint persistence, but until S1-2 ships, a process restart or a corrupt
checkpoint can still destroy scratchpad content. This is a reasoned trade
(S1-2's design wants S1-1's tests to exist first), not an oversight. Steps 2–3
are independent of each other and of S1-2, so they can run in parallel and must
not be allowed to stall step 4 — everything before it is a probability
reduction, not a guarantee.

---

## Verification

House rules that apply directly to this work:

- **A read must not be trusted to prove a write.** The scratchpad fix is
  verified by running a *second turn through the graph* and asserting the value
  survives — not by reading state back inside the turn that wrote it. Reading
  it back in-turn passes today, against the broken code.
- **Every guard gets a negative control.** Each test asserts failure when the
  fix is reverted, so a guard that silently stops guarding is caught.
- **The suite is green (6355 pass, 0 fail).** Any failure is a regression until
  proven otherwise.

New coverage, one group per defect, in `tests/test_context_integrity.py`:

| Test | Asserts |
|------|---------|
| Cross-turn scratchpad survival | Write turn N, assert present turn N+1 **through the graph**; negative control on the old `LastValue` behaviour |
| **Checkpoint back-compat** | Resume a thread checkpointed **before** the schema change and assert it loads and runs. `scratchpad` is the first `Annotated` reducer in `SupervisorState`, so its channel type changes from `LastValue` to an aggregate — old checkpoints hold a plain dict under that name. Must pass on **both** savers (`AsyncSqliteSaver` and `AsyncPostgresSaver`, `agent_runner.py:961/971`), since serialization differs between them |
| Transport does not clobber | `build_turn_working_memory` returns no `scratchpad` key at all |
| Scratchpad bounds | 25th key evicts oldest; oversized value truncated; `__clear__` works |
| Dead-band closure | Trim dropping one assistant turn at 30K tokens injects a summary naming it |
| Interrogative guard (EN) | `what going on?`, `what happened?`, `status?` → not `shift`, with a mocked embedder returning maximum distance |
| Interrogative guard (AR) | `شنو صار؟`, `وش الوضع؟`, `ليش وقف؟` → not `shift` |
| Explicit shift still works | `never mind, new topic` / `موضوع ثاني` → `shift_explicit`, recall suppressed |
| Inferred shift keeps recall | `shift_inferred` → recall **on**, prose kept, tool payloads stubbed |
| Recovery breaker | 3 checkpoint/session queries hunting prior output → forced RESPOND, honest message |
| Proposal round-trip | `save_proposal` → new thread, trim everything → IDs still resolve to exact text |

End-to-end regression reproducing the incident verbatim: propose 8 drafts,
force a trim above 24K, send `post the English ones immediately`, then
`what going on?` — assert the drafts resolve, the topic holds, and the agent
answers about tweets. **This test must fail against current `main`.** If it
passes before the fixes land, the test is wrong, not the code.

---

## Explicitly not in scope

- **Loosening the approval gate.** It refused to post text it could not verify
  against what the user approved. That was correct and is the reason this
  incident cost time rather than credibility. It gains a lookup path; its
  strictness does not change.
- **Raising the drift threshold as a substitute for the interrogative gate.**
  It trades one class of silent misclassification for another.
- **Removing deterministic trim.** It is doing its job; the defect is that
  nothing durable sat behind it.

---

## Open question for the user

The 24K trim budget is aggressive for a 200K-context model — it forces trimming
long before the model is under real pressure, which is what put a routine
two-turn workflow into the dead band at all. Raising it (say `window × 0.5`,
uncapped) would make trims rarer, but costs tokens per call on every turn.

Recommendation: **land S1-1 and S1-4 first, then measure how often trim actually
fires before re-tuning the budget.** Changing the threshold now would mask
whether the real fixes work.

**Deferred items — landed 2026-08-30 (same day), the decision itself still
pending production data:**

- **The measurement now exists.** Every trim that drops messages increments
  `kazma_context_trims_total{summary="fired"|"missed"}` (the label says
  whether the S1-4 summary net actually injected a note) and
  `kazma_context_trim_dropped_messages_total` sizes the loss. Both surface
  on the existing `/metrics` endpoint via the shared prometheus registry.
- **The re-tune is a settings change, not a code change.** ConfigStore key
  `agent.trim.token_budget` overrides the 24K cap (clamped to
  [4000, window × 0.95]); the default is byte-identical to the old formula.
- **Decision rule (the plan's own):** read the counters for a week of real
  traffic BEFORE touching the budget. If trims are rare after the S1-1/S1-4
  fixes, the budget is fine as-is; a non-zero `summary="missed"` is a bug
  report, not a tuning input.
