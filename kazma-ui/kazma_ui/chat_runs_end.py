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
  (``hitl_gates.end_gate``), read again and ended from where they are when a
  person's answer moved them meanwhile: a ``pending`` one is settled
  ``not_needed`` (nobody decided it); a decided one keeps the decision a
  person recorded (D2) and says what became of its call
  (``hitl_gates.chat_gone_fate``, worded per decision in the catalog,
  ``runs.gate_fate.<fate>``): approved and taken before its drive started
  (``not_run``: the drive never starts, :func:`begin_drive`), approved with
  its drive started (``cut``, ``error``: its call may have run), denied, or
  expired; in a chat that stays its card is stamped settled; the
  commitments it held are dropped;
* its steers, its paused mark and an active ``/long`` go, its reply turn is
  closed, a cut turn is no longer offered -- archived, it is ended in its
  checkpoint (the abandonment marker) and the chat says why, in the
  reader's language (``chat.cut_turn_archived``), so un-archiving shows why
  it is gone;
* a run that was there -- a turn running, an open reply turn, a cut turn --
  is recorded as ended with the reason (``runs_chat_turn.record_ended``):
  Running work lists it cancelled, "The chat was deleted" in the reader's
  language (the catalog's ``runs.ended.<reason>``) with what became of its
  approval, and no incident is opened for it. Each thing is taken in one step (the cancel, the reply turn, the cut
  turn, each gate), and two ends of one thread take what is left one at a
  time (``_one_end_at_a_time``), so of two ends of a waiting chat at once one
  reports and logs it. Before, the end whose gate write lost could take the
  reply turn first, and both reported (Linux, 2026-11-28).

:func:`end_chat_runs` is the way from the event loop, before the chat's state
is removed (the cancel is awaited); :func:`chat_went_away` the same from a
thread (the chat store's own removals), with the cancel handed to the turn's
loop. ``tests/test_chat_gone_ends_runs.py`` enumerates every way a chat goes
away from the source.

**The drive token** (review of ec538d779, L1). A decision is carried out by
a drive the decider starts after recording it (the approve route, the
approval watchdog, a chat app's button). The decider asks
:func:`begin_drive` in the same step as it starts the drive; an end marks
its thread going away first and takes each gate's token before ending it.
One lock decides each gate: either its drive started first (the end cancels
a registered drive and records ``cut``) or the end took it first (no drive
starts, the decider says the chat went away, and the record says the call
did not run). Before, an Approve that won the race ran its call on a
deleted chat, and the gate was recorded ``approve`` though its call never
ran.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CHAT_MECHANISMS",
    "GATE_OUTCOME",
    "REASONS",
    "ChatRunsEnded",
    "begin_drive",
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

#: Which of the gates a chat's going away ended tells most about its turn,
#: first (the run's ``ended_gate``, Running work's line).
_TELLING = ("cut", "not_run", "denied", "expired", "not_needed")


@dataclass(frozen=True)
class ChatRunsEnded:
    """What ending a chat's work found and did (only what THIS call took)."""

    thread_id: str
    reason: str
    cancelled: bool = False
    gates: int = 0
    turn: bool = False
    recorded: bool = False
    #: What became of the approval it waited on (``hitl_gates.CHAT_GONE_FATES``).
    approval: str = ""


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


# ── the drive token ─────────────────────────────────────────────────────

_token_lock = threading.Lock()
#: gate id -> "driven" (the drive carrying its decision out started) or
#: "taken" (its chat went away first: no drive starts). Keyed by the
#: registry's own gate id, never its alias: the alias is a hash of the
#: thread, tool and arguments, which the next identical question on the same
#: thread shares (review of e916a8f96, L2). Oldest first, bounded: a token
#: matters for the moments between a decision and its drive.
_tokens: OrderedDict[str, str] = OrderedDict()
_TOKENS_KEPT = 10_000
#: thread id -> how many ends of its chat are under way.
_going: dict[str, int] = {}
#: thread id -> when (``time.perf_counter``, :func:`drive_clock`) an end of its chat last finished.
#: A decision recorded before then is never carried out after: its gate may
#: have left the live states meanwhile (an expiry), so the end took no token
#: for it (review of e916a8f96, L1). Bounded like the tokens.
_ended_at: OrderedDict[str, float] = OrderedDict()
#: thread id -> why its chat last went away (a :data:`REASONS` code), for a
#: refused drive's gate (:func:`refused_drive`). Bounded like the tokens.
_end_reasons: OrderedDict[str, str] = OrderedDict()


def _mark(gate_id: str, what: str) -> None:
    """Under ``_token_lock``."""
    _tokens[gate_id] = what
    _tokens.move_to_end(gate_id)
    while len(_tokens) > _TOKENS_KEPT:
        _tokens.popitem(last=False)


def drive_clock() -> float:
    """The clock *since* of :func:`begin_drive` is read on: take it before
    the decision is recorded."""
    return time.perf_counter()


def real_gate_id(gate_id: str) -> str:
    """The registry's own id of the gate *gate_id* names (it may name it by
    its alias), for :func:`begin_drive`; *gate_id* itself when the registry
    holds no such gate or is off. Blocking (the gate registry)."""
    from kazma_ui.hitl_gate_bridge import registry_on

    if not gate_id or not registry_on():
        return gate_id
    from kazma_core.safety.hitl_gates import gate_for

    row = gate_for(gate_id)
    return row.gate_id if row is not None else gate_id


def begin_drive(thread_id: str, gate_id: str, *, since: float) -> bool:
    """Before a drive that carries out a recorded decision starts (the
    approve route, the approval watchdog, a chat app's button), in the same
    step as the caller starts AND registers it (``register_turn``, so an end
    can cancel it): True when it may start, and from now on a chat that goes
    away counts its call as started (``cut``). False when its chat is going
    away or went away -- the thread's chat is being ended, was deleted, or
    was ended after *since* (:func:`drive_clock`, read before the decision
    was recorded), or an end took the gate (*gate_id*: the registry's own
    id, :func:`real_gate_id`) -- and nothing may start: the caller says the
    chat went away and ends its gate and stamps its card (:func:`refused_drive`).
    Memory only, never blocks."""
    from kazma_core.spaces.threads import thread_ended

    with _token_lock:
        if _going.get(thread_id) or (gate_id and _tokens.get(gate_id) == "taken"):
            return False
        if _ended_at.get(thread_id, float("-inf")) >= since or thread_ended(thread_id):
            return False
        if gate_id:
            _mark(gate_id, "driven")
    return True


def _take(gate_id: str) -> bool:
    """Take a gate's token for an end: True when no drive carrying its
    decision started -- and none will (:func:`begin_drive` refuses it from
    now on); False when one did."""
    with _token_lock:
        if _tokens.get(gate_id) == "driven":
            return False
        _mark(gate_id, "taken")
    return True


@contextmanager
def _going_away(thread_id: str, reason: str = "") -> Iterator[None]:
    """The thread's chat is being ended (for *reason*): no drive starts on it
    meanwhile, nor after for a decision recorded before the end finished."""
    with _token_lock:
        _going[thread_id] = _going.get(thread_id, 0) + 1
        if reason:
            _end_reasons[thread_id] = reason
            _end_reasons.move_to_end(thread_id)
            while len(_end_reasons) > _TOKENS_KEPT:
                _end_reasons.popitem(last=False)
    try:
        yield
    finally:
        with _token_lock:
            left = _going.get(thread_id, 1) - 1
            if left > 0:
                _going[thread_id] = left
            else:
                _going.pop(thread_id, None)
            _ended_at[thread_id] = time.perf_counter()
            _ended_at.move_to_end(thread_id)
            while len(_ended_at) > _TOKENS_KEPT:
                _ended_at.popitem(last=False)


def refused_drive(thread_id: str, gate_id: str, session_id: str = "", turn_id: str = "") -> str:
    """After :func:`begin_drive` refused a drive: its gate, still live, is
    ended as its chat's going away ends a gate (the decision kept, its call
    not run, ``ended_reason`` the end's reason) -- an end that touched no
    gate (an archive that left a running turn to finish, a failed step, the
    registry off) left it ``resuming`` until the next boot sweep (review of
    974f2ae85, N2) -- and its card is stamped settled
    (:func:`settle_refused_card`). The fate ("" when another writer ended
    it). Blocking; never raises."""
    fate = _step("ending its refused gate", thread_id, lambda: _end_refused_gate(thread_id, gate_id), "")
    settle_refused_card(thread_id, session_id, turn_id)
    return fate


def _end_refused_gate(thread_id: str, gate_id: str) -> str:
    """:func:`refused_drive`'s gate, ended when still live. Blocking."""
    from kazma_ui.hitl_gate_bridge import registry_on

    if not gate_id or not registry_on():
        return ""
    from kazma_core.safety.hitl_gates import gate_for

    row = gate_for(gate_id)
    if row is None or not row.is_live:
        return ""
    with _token_lock:
        reason = _end_reasons.get(thread_id, "chat_deleted")
    return _end_one(row, GATE_OUTCOME, reason, waiting_only=False)


def settle_refused_card(thread_id: str, session_id: str = "", turn_id: str = "") -> bool:
    """The card of a decision whose drive :func:`begin_drive` refused,
    stamped ``settled`` in its chat's transcript: the decision's own stamp
    may have landed after the end stamped it (``record_gate_decision``
    claims, then stamps), and an un-archived card read "Approved" for a call
    that never ran (review of e916a8f96, L3). *session_id* / *turn_id* are
    found when not given. Blocking; False when nothing was written."""
    if not (session_id and turn_id):
        from kazma_ui.hitl_decision import resolve_decision_turn

        session_id, turn_id = resolve_decision_turn(thread_id, session_id, turn_id)
    from kazma_ui.hitl_gate_bridge import stamp_waiting_card

    return _step("stamping its refused card", thread_id,
                 lambda: stamp_waiting_card(thread_id, session_id, turn_id, "settled"), False)


def _fate_words() -> dict[str, str]:
    """Each fate in English and Arabic, from the catalog
    (``runs.gate_fate.<fate>``): the gate row's message, read by whoever
    reads the row."""
    from kazma_core.safety.hitl_gates import CHAT_GONE_FATES

    from kazma_ui.i18n import t

    return {fate: "\n".join(t(f"runs.gate_fate.{fate}", lang) for lang in ("en", "ar"))
            for fate in CHAT_GONE_FATES}


def _end_one(row: Any, outcome: str, reason: str, *, waiting_only: bool) -> str:
    """End one chat gate (``hitl_gates.end_gate``) from the state it is in,
    reading it again when a person's answer moved it meanwhile. The fate of
    its call ("" when another writer ended it first, or an archive left a
    call that runs to finish). Blocking."""
    from kazma_core.safety.hitl_gates import LIVE_STATES, chat_gone_fate, end_gate, gate_for

    held = row
    for _ in range(len(LIVE_STATES) + 1):     # a gate only moves forward
        drove = not _take(held.gate_id)
        if drove and waiting_only:
            # An archive leaves what runs to finish: its drive settles it.
            logger.info("[chat] gate %s of thread=%s is being carried out; the archive leaves it",
                        held.gate_id[:16], held.thread_id[:12])
            return ""
        fate = chat_gone_fate(held.state, held.decision, held.actor, drove=drove)
        if end_gate(held.gate_id, held.state, outcome=outcome, reason=reason, drove=drove,
                    words=_fate_words()) is not None:
            return fate
        again = gate_for(held.gate_id)
        if again is None or not again.is_live:
            logger.info("[chat] gate %s of thread=%s was ended meanwhile; what was written stands",
                        held.gate_id[:16], held.thread_id[:12])
            return ""
        logger.info("[chat] gate %s of thread=%s was answered meanwhile (%s); ended from there",
                    held.gate_id[:16], held.thread_id[:12], again.state)
        held = again
    logger.warning("[chat] gate %s of thread=%s kept moving; left as it is", held.gate_id[:16],
                   held.thread_id[:12])
    return ""


def _end_gates(thread_id: str, outcome: str, reason: str = "", *,
               waiting_only: bool = False) -> list[str]:
    """End the thread's chat gates, each in one compare-and-set
    (``hitl_gates.end_gate``, :func:`_end_one`): a pending one settled
    *outcome*, a decided one keeping the decision a person recorded and
    saying what became of its call. The fates of the gates THIS call ended;
    one another writer ended first keeps what it wrote. Blocking (the gate
    registry)."""
    from kazma_ui.hitl_gate_bridge import registry_on

    if not registry_on():
        return []
    from kazma_core.safety.hitl_gates import live_gates

    fates = []
    for row in live_gates(thread_id):
        if row.mechanism not in CHAT_MECHANISMS:
            continue
        fate = _end_one(row, outcome, reason, waiting_only=waiting_only)
        if fate:
            fates.append(fate)
    return fates


def _telling(fates: list[str]) -> str:
    """The fate that tells most about the turn (:data:`_TELLING`)."""
    return next((fate for fate in _TELLING if fate in fates), "")


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


#: How long an end waits for another end of the same thread to finish what
#: is left before it goes on (said at WARNING): a stalled store never holds
#: a chat's going away for good.
REST_WAIT_S = 30.0
_rest_lock = threading.Lock()
#: thread id -> [its lock, how many ends hold or wait for it].
_rest_held: dict[str, list[Any]] = {}


@contextmanager
def _one_end_at_a_time(thread_id: str) -> Iterator[None]:
    """Two ends of one thread take what is left one after the other: the
    first takes it all (its gates, its reply turn, its cut turn) and
    reports; the second finds nothing. Bounded (:data:`REST_WAIT_S`).
    Blocking; never on the event loop."""
    with _rest_lock:
        held = _rest_held.setdefault(thread_id, [threading.Lock(), 0])
        held[1] += 1
    got = held[0].acquire(timeout=REST_WAIT_S)
    if not got:
        logger.warning("[chat] another end of thread=%s held on for %.0f s; this one goes on beside it",
                       thread_id[:12], REST_WAIT_S)
    try:
        yield
    finally:
        if got:
            held[0].release()
        with _rest_lock:
            held[1] -= 1
            if held[1] <= 0 and _rest_held.get(thread_id) is held:
                _rest_held.pop(thread_id, None)


def _end_the_rest(thread_id: str, reason: str, *, session_id: str, title: str | None,
                  gate_outcome: str, cancelled: bool, space: str | None = None,
                  waiting_only: bool = False) -> ChatRunsEnded:
    """Everything but the cancel, one end of the thread at a time
    (:func:`_one_end_at_a_time`). Blocking: the gate registry, the chat
    store, the settings, the commitments. With *waiting_only* (an archive) a
    turn still running is left to finish, with its reply turn."""
    with _one_end_at_a_time(thread_id):
        return _end_what_is_left(thread_id, reason, session_id=session_id, title=title,
                                 gate_outcome=gate_outcome, cancelled=cancelled, space=space,
                                 waiting_only=waiting_only)


def _end_what_is_left(thread_id: str, reason: str, *, session_id: str, title: str | None,
                      gate_outcome: str, cancelled: bool, space: str | None,
                      waiting_only: bool) -> ChatRunsEnded:
    """:func:`_end_the_rest`'s steps, under the thread's end lock."""
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
    fates = _step("ending its approvals", thread_id,
                  lambda: _end_gates(thread_id, gate_outcome, reason, waiting_only=waiting_only), [])
    gates = len(fates)
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
    if cut and session_id:
        # A chat that stays (archived): its cut turn ended, and the chat says
        # why -- un-archived, nothing else would (review L3).
        _step("saying why its cut turn ended", thread_id,
              lambda: _say_cut_turn_ended(thread_id, session_id), False)
    approval = _telling(fates)
    if ran:
        # A run the adapter listed (a turn running, open or cut) is recorded
        # ended; gates alone were no chat turn's run.
        if title is None:
            title = _step("reading its title", thread_id, lambda: _title_of(thread_id), "")
        home, asker = _step("reading its space", thread_id, lambda: _home(thread_id), ("", ""))
        from kazma_ui.runs_chat_turn import record_ended

        record_ended(thread_id, reason=reason, title=title or "", space=space if space is not None else home,
                     asker=asker, approval=approval)
    logger.info("[chat] thread=%s went away (%s): turn cancelled=%s, approvals ended=%d (%s), reply turn=%s, "
                "cut turn=%s", thread_id[:12], reason, cancelled, gates, ", ".join(fates) or "-", bool(turn_id),
                cut)
    return ChatRunsEnded(thread_id, reason, cancelled=cancelled, gates=gates, turn=bool(turn_id),
                         recorded=ran, approval=approval)


def _open_row_turn(held: Any) -> str:
    """The turn id of the chat's trailing assistant row still marked open
    (a turn a restart cut), or "". Never adopts it (``resolve_reply_turn``
    would open it again)."""
    for message in reversed(list(getattr(held, "messages", None) or [])):
        if not isinstance(message, dict) or str(message.get("role") or "") != "assistant":
            return ""
        if message.get("open") and message.get("turn_id"):
            return str(message["turn_id"])
        return ""
    return ""


def _say_cut_turn_ended(thread_id: str, session_id: str) -> bool:
    """The chat *session_id* says, in the reader's language (the request's,
    else the install's: ``chat.cut_turn_archived``), that the turn a restart
    cut ended when the chat was archived; the cut turn's row is closed. So a
    chat un-archived later says why its turn is gone. Blocking (the chat
    store); False when nothing was written."""
    from uuid import uuid4

    from kazma_ui.i18n import current_language, t
    from kazma_ui.reply_sink import close_reply_turn
    from kazma_ui.session_manager import get_session_manager
    from kazma_ui.turn_runtime import persist_reply

    held = get_session_manager().get(session_id)
    if held is None:
        return False
    open_turn = _open_row_turn(held)
    if open_turn:
        close_reply_turn(thread_id, session_id, open_turn)
    return persist_reply(session_id, uuid4().hex, t("chat.cut_turn_archived", current_language()),
                         interrupted=False, thread_id=thread_id, parts=[])


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
    with _going_away(thread_id, reason):
        return await _end_on_loop(thread_id, reason, session_id=session_id, title=title,
                                  gate_outcome=gate_outcome, graph=graph, waiting_only=waiting_only)


async def _end_on_loop(thread_id: str, reason: str, *, session_id: str, title: str | None,
                       gate_outcome: str, graph: Any, waiting_only: bool) -> ChatRunsEnded:
    """:func:`end_chat_runs`, its thread marked going away (no drive starts)."""
    from kazma_ui.reply_sink import current_reply_turn
    from kazma_ui.turn_resume import cut_turns

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
    if graph is not None and (cancelled or current_reply_turn(thread_id) or thread_id in cut_turns()):
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
    with _going_away(thread_id, reason):
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
