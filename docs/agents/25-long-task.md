<!-- AGENTS.md, piece 25-long-task. The index is in AGENTS.md; scripts/agents_md.py joins the pieces. -->
### 25. Long-Task Continue Protocol & Partial Pause (`agent/long_task.py`)

Born from the 2026-08-19 Telegram desync ("Saved. Ready…" acks instead of
executing commands after a mission ended Partial) — full diagnosis in
`docs/audits/AUDIT_DEEP_STRUCTURE_2026-08-19.md` §20.

**A. The continue-context injection is GATED by reply shape.**
- `consume_continue_context(thread_id, user_text=…)` returns the stored
  salvage ONLY when `is_continuation_reply(user_text)` is true: ≤8-word
  replies matching proceed/continue/yes/ok/go-on/keep-going/wrap-up
  (Arabic كمّل/اكمل/تابع/نعم/يلا/زين included).
- The stored `long_task.continue.{thread}` context is cleared on EVERY
  consume call — gated or not — so a stale "do not re-do / final report"
  directive can never leak into a later turn. The salvage itself is
  already in the conversation history (the user saw the Partial reply).
- The injection header carries an explicit escape clause ("if the user's
  latest message is a NEW task, ignore this directive") as defense in
  depth. Only injection site: gateway `agent_handler/store.py` — keep the
  `user_text=` argument if a second site is ever added. It reaches the model
  through `agent_handler/graph.py:_rebuild_turn_messages` (§43); until
  2026-09-29 the history rebuild replaced the state and dropped it whenever
  the chat had history.

**B. A Partial PAUSES the long task.**
- The gateway's recursion-Partial handler calls `pause_long_task()`:
  `long_task_status()` then reports `active: False` with baseline budgets
  (no mission framing/ceilings), `is_mission_mode()` defuses, and
  `consume_long_task_turn()` stops eating follow-up turns while paused.
- The paused record survives for `/long status` until TTL expiry; a fresh
  `/long` re-enable always works. The Partial reply tells the user the
  state machine ("reply **Proceed** to wrap up, or send a new task and it
  runs fresh").
- `/long off` (or `/abort`) remains the immediate manual clear on any
  build.
- An active `/long` (budget, mission or `/unrestricted`) lifts the turn's
  wall-clock limit for its chat on every surface; a paused one does not
  (§31, `agent/turn_budget.py`).
- **A budget preset never lowers the Settings baseline** (2026-11-15):
  `/long on` set the research preset's 40 tool rounds where the live chat had
  100 (`agent.max_iterations`); `enable_long_task` and the "long task by
  default" setting now take the higher of the two (an explicit number stays).
  Gate: `tests/test_long_task.py` (`test_long_on_never_lowers_the_installs_baseline`).
- **The gateway does both through `_settle_recursion_partial`**
  (`agent_handler/graph.py`), and a failure there is a WARNING. Inline, it
  imported a name removed 2026-09-04 inside `except Exception: pass`, so
  from then until 2026-09-26 no Partial was stored and no long task paused
  on Telegram/Discord/Slack. `tests/test_gateway_recursion_partial.py`.

