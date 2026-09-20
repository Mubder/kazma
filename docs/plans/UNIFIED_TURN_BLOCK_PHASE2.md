# Unified turn block — Phase 2 report: unified renderer and header

Date: 2026-09-20

Status: Phase 2 deliverable of [`UNIFIED_TURN_BLOCK.md`](UNIFIED_TURN_BLOCK.md).
Builds on [Phase 0](UNIFIED_TURN_BLOCK_PHASE0.md) and
[Phase 1](UNIFIED_TURN_BLOCK_PHASE1.md).

This is the phase the user could see. Net effect on the page: one status
line per turn instead of one per page, a thoughts fold that stays where the
reader put it, and one answer authority.

---

## 1. Delivered

| Plan §12 deliverable | State |
|---|---|
| One turn shell and integrated header | Done — §2 |
| Common live/history renderer | Already true since Turn Render V2; the header joined it — §2 |
| Preference-controlled activity disclosure | Done — §3 |
| Single answer painter; remove competing content buffers | Done for `tokenAccum` — §4. One legacy fallback remains, scoped and reported — §6 |
| Remove the separate live task bar after commands are migrated | Done — §5 |

---

## 2. The header (§3 of the plan)

`modules/turn_presentation.js` derives the header as a **pure function** of
the document plus the server facts the page already holds. Plan §7 requires
a mapping, not a second execution state machine, so the module has no
timers, no storage, no DOM and no memory.

What it refuses to say is the part that matters, because each refusal is an
incident:

| Refusal | Incident it closes |
|---|---|
| A stop request reads "Stopping", never "Cancelled" | Only the server says a cancellation took (plan §7). |
| A dropped socket reads "Working" plus a separate connection indicator | Disconnection is not completion or failure (plan §3). |
| A gate part stamped `pending` does not outrank the registry's view | "Approved — running…" under a finished reply, 2026-09-19 live. |
| Elapsed is the server's stamp or absent | "Done 0s" while the graph was still working — a client wall clock answering a server question. |
| Nothing marks a turn complete | `close_turn` is the only closer (AGENTS.md §31A). |

The header is slot 0 of every turn and unconditional once a turn exists —
"including before the first token" (§3). A header that appears only once
there is content is exactly the gap the bottom bar was invented to fill.

Elapsed now flows: `turn_heartbeat.elapsed_s` and `turn_complete.duration_ms`
→ `doc.elapsedS` / `doc.elapsedAtMs` (monotone; a replayed older heartbeat
cannot rewind it) → a 1-second display ticker that freezes at a terminal
phase. Heartbeats did not reach `applyTurnEvent` at all before this; they
only fed the bar.

---

## 3. The fold (invariant U08)

Nothing owned it. `_paintWorkbenchSlot` recomputed the fold from execution
state on every pass, so a reader who collapsed the thoughts panel had it
reopened by the next token. Commit `afbd22dd` made that deliberate after the
opposite bug — collapsing at the terminal frame yanked the answer out of
view — and the two decisions kept trading places because neither separated
*what the reader asked for* from *what the turn is doing*.

`modules/turn_preferences.js` owns the first. The document owns the second.
Exactly one writer exists (a reader gesture) and
`test_turn_ledger_abc.py` counts them.

Scope is per session and per turn, `sessionStorage` only, never sent to the
server and never written into a turn part: a preference riding on an
execution record can be replayed onto someone else's screen and makes every
snapshot comparison depend on who was looking (plan §3).

The behavioral test is not "a class is present" — it re-renders 50 times in
every execution state and requires that nothing the reader chose moves, in
both directions.

---

## 4. One answer authority (invariant U06)

`tokenAccum` was a module-level string that every paint wrote and four
decisions read. AGENTS.md §31B already forbade the dual-**paint**; what
remained was the same defect one level down — a second answer to "has this
turn produced anything", in a variable whose lifetime nobody owned.

It was zeroed in eleven places, and every zeroing was somebody defending
against a stale read. The empty-string guard in `_paintLiveTextNow` exists
because one transport's terminal frame flushed after the other's `endTurn`
had blanked it and painted `""` over a finished reply (2026-09-02).

Replaced by `_liveAnswerText()`, a selector over the document. The document
cannot go stale that way: it is keyed by turn, a retired turn's document is
simply no longer the live one. The guard stays anyway — an empty read is
never an instruction to erase an answer, whatever produced it.

---

## 5. The bar is gone (§9 removal inventory)

515 lines of controller, 33 lines of markup, 40 CSS rules, 23 call sites,
the WS store bridge, and the hidden `#thinking-indicator` it had itself
replaced. Net −1,259 lines across the commit.

Being a surface per **page** rather than per **turn** is what forced it to
own a phase machine, a wall clock, a hide timer, a step list and a retry
budget — all so it could guess about a turn it could not see.

| Job | Where it went |
|---|---|
| Phase, elapsed, counts, Stop | The turn header. The label hysteresis went with it: a derived phase cannot flicker between two readings of one state. |
| Open/closed body | The activity disclosure, as a reader preference. |
| Step list | The workbench, which already had one. The bar's second list is what blanked on an empty read. |
| Stall detection + resync | `_reconcileTick`, which was **already** resyncing every 6 s for as long as a turn might be undelivered. The bar ran a second recovery loop beside it on a 30 s backoff with a 3-try budget; all it added was the words "not responding". The header now reports the silence and its duration. |
| Approval countdown | The card's own `data-approval-deadline` — the right level, because two gates can be waiting at once. |

`tests/js/test_live_task_card.js` was deleted with its subject. Its
incidents were re-pointed onto the header rather than dropped: the vanishing
card became "discard never deletes, and the only timer is a repaint that
stops itself"; the two recovery loops became "there is one"; "one surface,
one owner" became "the WS store owns none".

Ten further source-grep locks across five files were re-pointed. Every one
of them was protecting a real incident whose mechanism moved; none was
deleted for being inconvenient.

---

## 6. What Phase 2 does NOT claim

**A legacy painter is still in the file.** `ensureProgressPanel` and the
140-line tail of `logProgress` are the pre-V2 progress panel. They are
reachable only when `window.KazmaTurnDocument.applyEvent` is absent — i.e.
when the projector module failed to load — because `logProgress` returns
early in every other case. That is a competing turn-content writer under
invariant U03, and it is **explained but not removed**.

Why not now: `_progressEl` is still threaded through plan rendering
(`_renderPlanList`), the elapsed ticker and `finalizeProgress`, so removing
the panel is a separate refactor with its own regression surface, not a
deletion. It is carried into the Phase 5 removal inventory with this
reachability condition stated, rather than counted as done here.

**Browser evidence is a manual look, not a test yet.** The page was opened
against the harness server and driven through a real four-gate turn. What
was observed: one assistant bubble, one `.turn-header` inside it reading
"Approval required · 1 awaiting your decision" with Stop, a collapsed
thoughts fold on a LIVE turn, no bottom bar, and slot order
`turn-header, agent-progress, message-text, hitl-approval-card`.

That look immediately found a defect every unit test had passed:

> A turn opens under the `'live'` placeholder and is renamed on the first
> stamped frame. The reader's fold preference was written under `'live'`
> and read back under the real turn id, so the fold shut again about a
> second into every turn. `turn_view.js:promote` already existed for this
> exact event (2026-09-03 crossed bubbles) — the rule it encodes is that
> *any* map keyed by the turn id must hear about the rename, and the new
> one did not. The unit tests missed it because they drove a single
> constant id and never promoted: a fixture that could not express the
> defect.

Fixed by promoting the preference store alongside the bubble registry and
by resolving the turn id at click time instead of capturing it at build
time. Re-verified in the browser: open → 25 repaints → still open; close →
25 repaints → still closed; the preference is keyed by the real turn id.
Locked by `tests/js/test_turn_preferences.js` (promotion cases) and
`test_unified_turn_block_phase0.py::test_every_map_keyed_by_turn_id_hears_about_a_promotion`.

An automated browser test is still owed. The acceptance matrix rows for
layout, focus, keyboard, mobile and RTL are untouched, and they are Phase
3/4 with Playwright.

**U01–U08 are not all claimed.** Held by this phase:

| ID | State |
|---|---|
| U01, U02 | Renderer tests: one header, one activity disclosure, one answer region per turn. |
| U03 | Held for the renderer path; the legacy fallback above is the stated exception. |
| U04, U05 | Phase 1. |
| U06 | Held — one answer authority. |
| U07 | Phase 1 (durable), unchanged here. |
| U08 | Held — 50 repaints move nothing the reader chose. |

Approvals are still one top-level slot per gate. That is Phase 3, and its
four reproductions are still red on purpose.

---

## 7. Evidence

| Claim | Test |
|---|---|
| The fold survives 50 repaints, both directions | `tests/js/test_turn_preferences.js` |
| Preferences never reach the server | `test_unified_turn_block_phase0.py::test_expansion_preference_has_an_owner` |
| One writer of the preference | `test_turn_ledger_abc.py::test_terminal_cot_swap_preserves_expansion` |
| The header refuses each of the five claims in §2 | `tests/js/test_turn_presentation.js` (36 checks) |
| The header is slot 0, exactly one per turn | `tests/js/test_turn_view.js` |
| The header is inside the turn, derived not told | `test_unified_turn_block_phase0.py::test_the_turn_header_is_inside_the_turn` |
| No page-level status surface, markup or CSS | `::test_no_separate_live_task_card_markup`, `::test_no_independent_task_card_controller`, `::test_no_live_task_card_styles` |
| The header cannot vanish from a live turn | `test_chat_steer_composer.py::test_the_header_cannot_vanish_from_a_live_turn` |
| One recovery loop, not two | `::test_recovery_has_one_loop_not_two` |
| One answer authority | `::test_no_token_accum_content_decisions`, `test_turn_ledger_abc.py::test_duplicate_terminal_flush_never_wipes_the_reply` |

All four Phase 2 strict-xfails XPASSed as the behavior landed and had their
markers removed with the change — which is what they were written for.
