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
  erased (``space_erased``); removed by the chat store's limit
  (``chat_store_full``);
* archived (``chat_archived``);
* cleared -- ``/reset`` on the web and in the chat apps, the Dashboard's
  clear-all (``chat_cleared``); its waiting approvals cleared
  (``approvals_cleared``).

What it had ends the same way each time:

* a turn running is cancelled and waited for with a bound
  (``bounded_cancel.cancel_and_wait``, AGENTS.md section 69);
* a turn waiting on an approval has its gates settled ``not_needed``
  (nobody decided them; they are no longer needed), the card stamped settled
  in a chat that stays, the commitments it held dropped;
* its steers, its paused mark and an active ``/long`` go, its reply turn is
  closed, a cut turn is no longer offered;
* the run is recorded as ended with the reason (``runs_chat_turn.record_ended``):
  Running work lists it cancelled, "The chat was deleted" in the reader's
  language (the catalog's ``runs.ended.<reason>``), and no incident is
  opened for it.

:func:`end_chat_runs` is the way from the event loop, before the chat's state
is removed (the cancel is awaited); :func:`chat_went_away` the same from a
thread (the chat store's own removals), with the cancel handed to the turn's
loop. ``tests/test_chat_gone_ends_runs.py`` enumerates every way a chat goes
away from the source.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

__all__ = [
    "GATE_OUTCOME",
    "REASONS",
    "ChatRunsEnded",
    "chat_went_away",
    "chats_went_away",
    "end_chat_runs",
    "end_every_chat_run",
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

#: How long a cancelled turn is waited for before it is left to end on its
#: own (logged at WARNING by ``cancel_and_wait``).
CANCEL_WAIT_S = 10.0


@dataclass(frozen=True)
class ChatRunsEnded:
    """What ending a chat's work found and did."""

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


def _settle_gates(thread_id: str, outcome: str) -> int:
    """Every live gate of the thread settled *outcome*. Blocking (the gate
    registry). A gate decided meanwhile keeps its decision."""
    from kazma_ui.hitl_gate_bridge import registry_on

    if not registry_on():
        return 0
    from kazma_core.safety.hitl_gates import TransitionConflict, live_gates, settle_gate

    settled = 0
    for row in live_gates(thread_id):
        try:
            settle_gate(row.gate_id, outcome)
            settled += 1
        except TransitionConflict as conflict:
            logger.info("[chat] gate %s of thread=%s was decided meanwhile (%s); that decision stands",
                        row.gate_id[:16], thread_id[:12], conflict.actual)
    return settled


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
                  gate_outcome: str, cancelled: bool) -> ChatRunsEnded:
    """Everything but the cancel. Blocking: the gate registry, the chat
    store, the settings, the commitments."""
    from kazma_core.agent.steer import clear_all_steers

    from kazma_ui.reply_sink import close_reply_turn, current_reply_turn
    from kazma_ui.turn_resume import cut_turns, forget_cut_turn

    clear_all_steers(thread_id)

    def unpause() -> None:
        from kazma_ui.sse_chat._streaming import mark_thread_unpaused

        mark_thread_unpaused(thread_id)

    _step("its paused mark", thread_id, unpause, None)
    gates = _step("settling its approvals", thread_id, lambda: _settle_gates(thread_id, gate_outcome), 0)
    turn_id = current_reply_turn(thread_id)
    cut = thread_id in cut_turns()
    had_work = bool(cancelled or gates or turn_id or cut)
    if not had_work:
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
    _step("closing its reply turn", thread_id,
          lambda: close_reply_turn(thread_id, session_id, turn_id), None)
    forget_cut_turn(thread_id)
    if title is None:
        title = _step("reading its title", thread_id, lambda: _title_of(thread_id), "")
    space, asker = _step("reading its space", thread_id, lambda: _home(thread_id), ("", ""))
    from kazma_ui.runs_chat_turn import record_ended

    record_ended(thread_id, reason=reason, title=title or "", space=space, asker=asker)
    logger.info("[chat] thread=%s went away (%s): turn cancelled=%s, approvals settled=%d, reply turn=%s",
                thread_id[:12], reason, cancelled, gates, bool(turn_id))
    return ChatRunsEnded(thread_id, reason, cancelled=cancelled, gates=int(gates), turn=bool(turn_id),
                         recorded=True)


# ── the two ways in ─────────────────────────────────────────────────────


async def end_chat_runs(thread_id: str, *, reason: str, session_id: str = "", title: str | None = None,
                        gate_outcome: str = GATE_OUTCOME, graph: Any = None) -> ChatRunsEnded:
    """End what the chat on *thread_id* has running or waiting: its chat
    goes away for *reason* (:data:`REASONS`). From the event loop, BEFORE the
    chat's state is removed: a running turn is cancelled and waited for
    (bounded) first. *session_id*: the chat's row, when it stays (archived:
    its card is stamped, its reply row closed). *graph*, when its checkpoint
    stays: the turn ends in it as Stop ends it (``turn_abort.mark_turn_abandoned``:
    the marker, so its next turn does not answer the request that was cut,
    and the memory rewind past that request). Never raises."""
    _checked(reason)
    if not thread_id:
        return ChatRunsEnded("", reason)
    from kazma_ui.reply_sink import current_reply_turn

    try:
        cancelled = await _cancel_and_wait(thread_id)
    except RuntimeError:  # a loop that is gone; the rest still ends
        logger.warning("[chat] the running turn of thread=%s was not cancelled", thread_id[:12], exc_info=True)
        cancelled = False
    if graph is not None and (cancelled or current_reply_turn(thread_id)):
        from kazma_ui.turn_abort import mark_turn_abandoned

        await mark_turn_abandoned(thread_id, graph)
    return await asyncio.to_thread(_end_the_rest, thread_id, reason, session_id=session_id, title=title,
                                   gate_outcome=gate_outcome, cancelled=cancelled)


def chat_went_away(thread_id: str, *, reason: str, session_id: str = "", title: str | None = None,
                   gate_outcome: str = GATE_OUTCOME) -> ChatRunsEnded:
    """:func:`end_chat_runs` from a thread -- the chat store's own removals
    (its limit, a space erased). Blocking; never on the event loop. A running
    turn's cancel is handed to its loop and waited for there, bounded.
    Never raises."""
    _checked(reason)
    if not thread_id:
        return ChatRunsEnded("", reason)
    cancelled = _step("cancelling its turn", thread_id, lambda: _cancel_where_it_runs(thread_id), False)
    return _end_the_rest(thread_id, reason, session_id=session_id, title=title, gate_outcome=gate_outcome,
                         cancelled=bool(cancelled))


def _threads_with_work() -> set[str]:
    """Every thread with a chat turn that runs, is open, was cut, or waits on
    an approval. Blocking (the gate registry)."""
    from kazma_ui.active_turns import running_turns
    from kazma_ui.reply_sink import open_turns_snapshot
    from kazma_ui.turn_resume import cut_turns

    found = set(running_turns()) | set(open_turns_snapshot()) | set(cut_turns())
    from kazma_ui.hitl_gate_bridge import registry_on

    if registry_on():
        from kazma_core.safety.hitl_gates import pending_gates

        found |= {row.thread_id for row in pending_gates(limit=5000) if row.thread_id}
    return found


def chats_went_away(thread_ids: Iterable[str], *, reason: str) -> int:
    """:func:`chat_went_away` for every thread of *thread_ids* that has
    work, their chats gone already (no title is read). Blocking. Returns how
    many had."""
    _checked(reason)
    wanted = {str(t) for t in thread_ids if t}
    if not wanted:
        return 0
    busy = _step("listing what runs", "-", _threads_with_work, set())
    ended = 0
    for thread_id in sorted(wanted & busy):
        ended += chat_went_away(thread_id, reason=reason, title="").recorded
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
