"""A chat that goes away ends what it has running or waiting.

Live 2026-11-27: a private chat's turn waited on an approval (``python_exec``)
and the chat was deleted. The card left the bell -- the chat was nobody's any
more -- but the turn's reply turn stayed open (``reply_sink``) with nothing
running it, and ten minutes later the run supervisor paged "Chat turn is
stuck: nothing is running it": an incident and a red Ops alert for a chat the
person had deleted. Nothing that removed a chat ended its turn.

Every way a chat goes away ends what it had, here:

* deleted -- the chat page, the Dashboard's thread delete
  (``chat_deleted``); moved to another space (``chat_moved``); its space
  erased (``space_erased``, :func:`end_space_runs`, before any of its rows
  go); removed by the chat store's limit (``chat_store_full``);
* archived (``chat_archived``): only what WAITS ends -- a turn still running
  finishes and saves its answer in the archived chat, as before;
* cleared -- ``/reset`` on the web and in the chat apps, the Dashboard's
  clear-all (``chat_cleared``); its waiting approvals cleared
  (``approvals_cleared``).

What it had ends the same way each time:

* a turn running is cancelled and waited for with a bound
  (``bounded_cancel.cancel_and_wait``, AGENTS.md section 69) -- not on archive;
* its chat's gates (mechanisms :data:`CHAT_MECHANISMS`; a swarm's or the MCP
  bridge's are not the chat's) end in one compare-and-set each
  (``hitl_gates.end_gate``): a ``pending`` one is settled ``not_needed``
  (nobody decided it), a ``claimed`` one keeps the decision a person recorded,
  and a ``resuming`` one whose drive was cut is ``error`` with its decision
  kept (its call may have run) -- never a person's decision rewritten (D2);
  in a chat that stays its card is stamped settled; the commitments it held
  are dropped;
* its steers, its paused mark and an active ``/long`` go, its reply turn is
  closed, a cut turn is no longer offered;
* a run that was there -- a turn running, an open reply turn, a cut turn --
  is recorded as ended with the reason (``runs_chat_turn.record_ended``):
  Running work lists it cancelled, "The chat was deleted" in the reader's
  language (the catalog's ``runs.ended.<reason>``), and no incident is opened
  for it. Each thing is taken in one step (the cancel, the reply turn, the cut
  turn, each gate), so of two ends at once one reports and logs it.

:func:`end_chat_runs` is the way from the event loop, before the chat's state
is removed (the cancel is awaited); :func:`chat_went_away` the same from a
thread (the chat store's own removals), with the cancel handed to the turn's
loop. ``tests/test_chat_gone_ends_runs.py`` enumerates every way a chat goes
away from the source.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CHAT_MECHANISMS",
    "GATE_OUTCOME",
    "REASONS",
    "ChatRunsEnded",
    "chat_went_away",
    "end_chat_runs",
    "end_every_chat_run",
    "end_space_runs",
]

logger = logging.getLogger(__name__)

#: Why a chat's work ended; each is worded in the catalog
#: (``runs.ended.<reason>``, kazma_ui/i18n/catalog/runs.py).
REASONS = (
    "chat_deleted",
    "chat_archived",
    "chat_cleared",
    "approvals_cleared",
    "chat_moved",
    "space_erased",
    "chat_store_full",
)

#: How the gates of a chat that went away are settled: nobody decided them,
#: and they are no longer needed.
GATE_OUTCOME = "not_needed"

#: The gate mechanisms that are a chat turn's own (the graph's interrupt and
#: its semantic cards). A swarm bus request, a pipeline checkpoint and the
#: ``kazma mcp`` bridge's question are their own work's, never ended here.
CHAT_MECHANISMS = ("graph", "semantic")

#: How long a cancelled turn is waited for before it is left to end on its
#: own (logged at WARNING by ``cancel_and_wait``).
CANCEL_WAIT_S = 10.0

#: Why a resuming gate whose drive was cut is an error (its call may have run).
_CUT_MID_RESUME = "the chat went away while its approved call ran; whether it ran is not known"


@dataclass(frozen=True)
class ChatRunsEnded:
    """What ending a chat's work found and did (only what THIS call took)."""

    thread_id: str
    reason: str
    cancelled: bool = False
    gates: int = 0
    turn: bool = False
    recorded: bool = False


def _what(thread_id: str) -> str:
    return f"the turn of a chat that went away (thread {thread_id[:12]})"


def _checked(reason: str) -> str:
    if reason not in REASONS:
        raise ValueError(f"not a reason a chat goes away: {reason!r}")
    return reason


def _running(thread_id: str) -> bool:
    from kazma_ui.active_turns import is_turn_running

    return is_turn_running(thread_id)


# ── the running turn ────────────────────────────────────────────────────


async def _cancel_and_wait(thread_id: str) -> bool:
    """Cancel the thread's running turn and wait for it, bounded. True when
    one was running. On the loop."""
    from kazma_ui.active_turns import cancel_turn, get_active_turn

    task = get_active_turn(thread_id)
    if task is None or not isinstance(task, asyncio.Future):
        return False
    if task.done():
        return False
    if task is asyncio.current_task():
        # The turn removes its own chat: it ends when it returns.
        logger.warning("[chat] a turn removed its own chat (thread=%s); it ends by itself", thread_id[:12])
        return False
    if task.get_loop() is not asyncio.get_running_loop():
        return _cancel_where_it_runs(thread_id)
    from kazma_core.bounded_cancel import cancel_and_wait

    cancel_turn(thread_id)
    await cancel_and_wait(task, what=_what(thread_id), timeout=CANCEL_WAIT_S)
    return True


def _cancel_where_it_runs(thread_id: str) -> bool:
    """From a thread: hand the cancel of the thread's running turn to the
    loop it runs on, where it is waited for with the same bound in the
    background. True when one was running."""
    from kazma_ui.active_turns import get_active_turn

    task = get_active_turn(thread_id)
    if task is None or not isinstance(task, asyncio.Future) or task.done():
        return False
    try:
        task.get_loop().call_soon_threadsafe(_cancel_on_its_loop, thread_id, task)
    except RuntimeError:
        logger.warning("[chat] the loop of thread=%s's turn is closed; its turn was not cancelled",
                       thread_id[:12])
        return False
    return True


def _cancel_on_its_loop(thread_id: str, task: asyncio.Future[Any]) -> None:
    """On the turn's loop: unregister and cancel it, and wait for it, bounded,
    in the background."""
    from kazma_core.background import spawn_background
    from kazma_core.bounded_cancel import cancel_and_wait

    from kazma_ui.active_turns import get_active_turn, unregister_turn

    if get_active_turn(thread_id) is task:
        unregister_turn(thread_id, task)
    spawn_background(cancel_and_wait(task, what=_what(thread_id), timeout=CANCEL_WAIT_S),
                     name=f"chat-gone-cancel:{thread_id[:12]}")


# ── what is left once nothing runs ──────────────────────────────────────


def _end_gates(thread_id: str, outcome: str) -> int:
    """End the thread's chat gates, each in one compare-and-set
    (``hitl_gates.end_gate``): a pending one settled *outcome*, a claimed one
    with the decision a person recorded, a resuming one an error with it.
    Returns how many THIS call moved; one another writer moved first keeps
    what it wrote. Blocking (the gate registry)."""
    from kazma_ui.hitl_gate_bridge import registry_on

    if not registry_on():
        return 0
    from kazma_core.safety.hitl_gates import end_gate, live_gates

    moved = 0
    for row in live_gates(thread_id):
        if row.mechanism not in CHAT_MECHANISMS:
            continue
        ended = end_gate(row.gate_id, row.state, outcome=outcome,
                         message=_CUT_MID_RESUME if row.state == "resuming" else "")
        if ended is None:
            logger.info("[chat] gate %s of thread=%s was decided meanwhile; that decision stands",
                        row.gate_id[:16], thread_id[:12])
            continue
        moved += 1
    return moved


def _title_of(thread_id: str) -> str:
    """The chat's title, while its row is there. Blocking (the chat store)."""
    from kazma_ui.session_manager import get_session_manager

    held = get_session_manager().get_by_thread_id(thread_id)
    return str(getattr(held, "title", "") or "") if held is not None else ""


def _home(thread_id: str) -> tuple[str, str]:
    """(space, opener) of the thread from its record; ("", "") for one from
    before Spaces. Blocking."""
    from kazma_core.spaces.threads import record_of

    record = record_of(thread_id)
    return (record.space_id, record.opener) if record is not None else ("", "")


def _failures() -> tuple[type[BaseException], ...]:
    """What a step's store can raise (``store_errors``), and a chat the
    store says is gone (``ChatDeleted``, a ``KeyError``)."""
    from kazma_core.db.pg_helpers import store_errors

    return (*store_errors(), LookupError)


def _step(what: str, thread_id: str, call: Any, default: Any) -> Any:
    """Run one step; a failure is said at WARNING and the rest goes on."""
    try:
        return call()
    except _failures():
        logger.warning("[chat] a chat went away, but %s of thread=%s failed", what, thread_id[:12],
                       exc_info=True)
        return default


def _end_the_rest(thread_id: str, reason: str, *, session_id: str, title: str | None,
                  gate_outcome: str, cancelled: bool, space: str | None = None,
                  waiting_only: bool = False) -> ChatRunsEnded:
    """Everything but the cancel. Blocking: the gate registry, the chat
    store, the settings, the commitments. With *waiting_only* (an archive) a
    turn still running is left to finish, with its reply turn."""
    from kazma_core.agent.steer import clear_all_steers

    from kazma_ui.reply_sink import close_reply_turn, take_reply_turn
    from kazma_ui.turn_resume import forget_cut_turn

    if waiting_only and _running(thread_id):
        logger.info("[chat] thread=%s %s while its turn runs: the turn finishes and saves its answer",
                    thread_id[:12], reason)
        return ChatRunsEnded(thread_id, reason)
    clear_all_steers(thread_id)

    def unpause() -> None:
        from kazma_ui.sse_chat._streaming import mark_thread_unpaused

        mark_thread_unpaused(thread_id)

    _step("its paused mark", thread_id, unpause, None)
    gates = _step("ending its approvals", thread_id, lambda: _end_gates(thread_id, gate_outcome), 0)
    turn_id = take_reply_turn(thread_id)
    cut = forget_cut_turn(thread_id)
    ran = bool(cancelled or turn_id or cut)
    if not (ran or gates):
        return ChatRunsEnded(thread_id, reason)
    if session_id and gates and turn_id:
        # A chat that stays (archived): its card never shows live again.
        def stamp() -> bool:
            from kazma_ui.hitl_gate_bridge import stamp_waiting_card

            return stamp_waiting_card(thread_id, session_id, turn_id, "settled")

        _step("stamping its card", thread_id, stamp, False)
    if gates:
        def drop_commitments() -> int:
            from kazma_core.safety.commitment.store import abort_pending_for_thread

            return abort_pending_for_thread(thread_id, reason=f"chat_gone:{reason}")

        _step("dropping its commitments", thread_id, drop_commitments, 0)

    def pause_long() -> None:
        from kazma_core.agent.long_task import pause_long_task

        pause_long_task(thread_id, reason=reason)

    _step("pausing its long task", thread_id, pause_long, None)
    if turn_id and session_id:
        _step("closing its reply row", thread_id, lambda: close_reply_turn(thread_id, session_id, turn_id), None)
    if ran:
        # A run the adapter listed (a turn running, open or cut) is recorded
        # ended; gates alone were no chat turn's run.
        if title is None:
            title = _step("reading its title", thread_id, lambda: _title_of(thread_id), "")
        home, asker = _step("reading its space", thread_id, lambda: _home(thread_id), ("", ""))
        from kazma_ui.runs_chat_turn import record_ended

        record_ended(thread_id, reason=reason, title=title or "", space=space if space is not None else home,
                     asker=asker)
    logger.info("[chat] thread=%s went away (%s): turn cancelled=%s, approvals ended=%d, reply turn=%s",
                thread_id[:12], reason, cancelled, gates, bool(turn_id))
    return ChatRunsEnded(thread_id, reason, cancelled=cancelled, gates=int(gates), turn=bool(turn_id),
                         recorded=ran)


# ── the ways in ──────────────────────────────────────────────────────────


async def end_chat_runs(thread_id: str, *, reason: str, session_id: str = "", title: str | None = None,
                        gate_outcome: str = GATE_OUTCOME, graph: Any = None,
                        waiting_only: bool = False) -> ChatRunsEnded:
    """End what the chat on *thread_id* has running or waiting: its chat
    goes away for *reason* (:data:`REASONS`). From the event loop, BEFORE the
    chat's state is removed: a running turn is cancelled and waited for
    (bounded) first -- unless *waiting_only* (an archive), which leaves a
    running turn to finish and ends only what waits. *session_id*: the chat's
    row, when it stays (archived: its card is stamped, its reply row closed).
    *graph*, when its checkpoint stays: the turn ends in it as Stop ends it
    (``turn_abort.mark_turn_abandoned``: the marker, so its next turn does not
    answer the request that was cut and the start pass does not continue it,
    and the memory rewind past that request). Never raises."""
    _checked(reason)
    if not thread_id:
        return ChatRunsEnded("", reason)
    from kazma_ui.reply_sink import current_reply_turn

    if waiting_only and _running(thread_id):
        logger.info("[chat] thread=%s %s while its turn runs: the turn finishes and saves its answer",
                    thread_id[:12], reason)
        return ChatRunsEnded(thread_id, reason)
    cancelled = False
    if not waiting_only:
        try:
            cancelled = await _cancel_and_wait(thread_id)
        except RuntimeError:  # a loop that is gone; the rest still ends
            logger.warning("[chat] the running turn of thread=%s was not cancelled", thread_id[:12],
                           exc_info=True)
    if graph is not None and (cancelled or current_reply_turn(thread_id)):
        from kazma_ui.turn_abort import mark_turn_abandoned

        await mark_turn_abandoned(thread_id, graph)
    return await asyncio.to_thread(_end_the_rest, thread_id, reason, session_id=session_id, title=title,
                                   gate_outcome=gate_outcome, cancelled=cancelled, waiting_only=waiting_only)


def chat_went_away(thread_id: str, *, reason: str, session_id: str = "", title: str | None = None,
                   gate_outcome: str = GATE_OUTCOME, space: str | None = None) -> ChatRunsEnded:
    """:func:`end_chat_runs` from a thread -- the chat store's own removals
    (its limit, a space erased). Blocking; never on the event loop. A running
    turn's cancel is handed to its loop and waited for there, bounded.
    *space*: the space the run is recorded in when its thread record may be
    gone. Never raises."""
    _checked(reason)
    if not thread_id:
        return ChatRunsEnded("", reason)
    cancelled = _step("cancelling its turn", thread_id, lambda: _cancel_where_it_runs(thread_id), False)
    return _end_the_rest(thread_id, reason, session_id=session_id, title=title, gate_outcome=gate_outcome,
                         cancelled=bool(cancelled), space=space)


def _threads_with_work() -> set[str]:
    """Every thread with a chat turn that runs, is open, was cut, or waits on
    one of its chat's approvals (:data:`CHAT_MECHANISMS`). Blocking (the gate
    registry)."""
    from kazma_ui.active_turns import running_turns
    from kazma_ui.reply_sink import open_turns_snapshot
    from kazma_ui.turn_resume import cut_turns

    found = set(running_turns()) | set(open_turns_snapshot()) | set(cut_turns())
    from kazma_ui.hitl_gate_bridge import registry_on

    if registry_on():
        from kazma_core.safety.hitl_gates import pending_gates

        found |= {row.thread_id for row in pending_gates(limit=5000)
                  if row.thread_id and row.mechanism in CHAT_MECHANISMS}
    return found


def end_space_runs(space: str) -> int:
    """End what the chats of *space* run or wait for, its space being erased
    (``spaces.space_data.erase_space``): BEFORE any of its rows go, while its
    thread records still say whose each thread is -- the run is recorded in
    *space*, never the owner's. Blocking. Returns how many runs it ended."""
    from kazma_core.spaces.threads import thread_home

    busy = _step("listing what runs", "-", _threads_with_work, set())
    ended = 0
    for thread_id in sorted(busy):
        if _step("reading its space", thread_id, lambda t=thread_id: thread_home(t), "") != space:
            continue
        ended += chat_went_away(thread_id, reason="space_erased", space=space).recorded
    return ended


async def end_every_chat_run(*, reason: str, mine: Any = None) -> int:
    """:func:`end_chat_runs` for every thread with work that *mine* (a
    blocking test, thread id -> bool; None: every one) says goes. For a clear
    of every chat at once (the Dashboard's clear-all). Returns how many."""
    _checked(reason)

    def busy() -> list[str]:
        return sorted(t for t in _threads_with_work() if mine is None or mine(t))

    ended = 0
    for thread_id in await asyncio.to_thread(busy):
        ended += (await end_chat_runs(thread_id, reason=reason)).recorded
    return ended
