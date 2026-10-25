"""Chat turns as runs (web, Telegram, Discord, Slack, voice).

Sources of truth: the turn registry (``active_turns``: what is running, on
every transport), the open reply turns (``reply_sink``) and the turn's
checkpoint. Progress is the graph's own heartbeat: every supervisor and tool
step stamps it (``supervisor_watchdog.record_heartbeat``), so a turn whose
model call or tool hangs stops stamping.

A turn that is open with nothing running it and no approval waiting is the
turn-liveness invariant's case (``turn_liveness``): its owner is gone. This
adapter reports it so the run supervisor pages it, with a Close button --
the healing the liveness watchdog keeps switched off by default, here decided
by a person. A turn waiting for an approval is waiting on a human: its card
is the action, and it is never "stalled".

Cancel is the web chat's Abort (``turn_abort.abort_turn``); Close is the
watchdog's heal (``reply_sink.close_reply_turn``). The chat's address comes
from the checkpoint's routing block, only when an incident needs it
(:meth:`origin_for`): never from the session store, whose rows expire.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from kazma_core.runs.contract import ActionResult, Run, RunState

__all__ = ["ADAPTER", "set_graph_getter"]

logger = logging.getLogger(__name__)

_graph_getter: Callable[[], Any] | None = None

#: thread_id -> when this adapter first saw the turn open with nothing running it.
_abandoned_since: dict[str, float] = {}


def set_graph_getter(getter: Callable[[], Any]) -> None:
    """The live compiled graph (app startup), for Cancel and the chat address."""
    global _graph_getter
    _graph_getter = getter


def _graph() -> Any:
    return _graph_getter() if _graph_getter is not None else None


class _ChatTurnAdapter:
    STATE_MAP: dict[str, str] = {
        "running": RunState.RUNNING,
        "awaiting_approval": RunState.WAITING_HUMAN,
        "abandoned": RunState.RUNNING,
    }

    async def list_runs(self, now: float, *, window_s: float) -> list[Run]:
        from kazma_core.agent.supervisor_watchdog import get_heartbeat

        from kazma_ui.active_turns import running_turns
        from kazma_ui.reply_sink import open_turns_snapshot

        running = running_turns()
        open_turns = open_turns_snapshot()
        mono_now = time.monotonic()
        beats = {tid: get_heartbeat(tid) for tid in set(running) | set(open_turns)}
        idle = [tid for tid in open_turns if tid not in running]
        waiting, titles = await asyncio.to_thread(self._read, idle, list(set(running) | set(open_turns)))
        for gone in set(_abandoned_since) - set(idle):
            _abandoned_since.pop(gone, None)

        runs: list[Run] = []
        for tid in sorted(set(running) | set(open_turns)):
            beat = beats.get(tid)
            beat_at = now - (mono_now - beat) if beat is not None else None
            if tid in running:
                native = "running"
                started = running[tid]
                progress_at = max(started, beat_at or 0.0)
                owner: bool | None = True
            elif tid in waiting:
                native = "awaiting_approval"
                started = beat_at or now
                progress_at = beat_at
                owner = None
                _abandoned_since.pop(tid, None)
            else:
                native = "abandoned"
                started = _abandoned_since.setdefault(tid, now)
                progress_at = started
                owner = False
            runs.append(Run(
                run_id=f"chat_turn:{tid}", kind="chat_turn", native_id=tid,
                title=titles.get(tid) or tid[:12], state=RunState(self.STATE_MAP[native]),
                native_state=native, started_at=started, progress_at=progress_at,
                progress_note="", owner_alive=owner,
                version=str(open_turns.get(tid, "")),
                actions=("cancel",) if native == "running" else (("close",) if native == "abandoned" else ()),
                link="/chat",
            ))
        return runs

    @staticmethod
    def _read(idle: list[str], threads: list[str]) -> tuple[set[str], dict[str, str]]:
        """(threads waiting on an approval, chat titles): store reads, in a thread."""
        from kazma_ui.turn_liveness import _is_awaiting_human

        waiting = {tid for tid in idle if _is_awaiting_human(tid)}
        titles: dict[str, str] = {}
        try:
            from kazma_ui.session_manager import peek_session_manager

            manager = peek_session_manager()
        except ImportError:
            manager = None
        if manager is not None:
            for tid in threads:
                session = manager.get_by_thread_id(tid)
                title = getattr(session, "title", "") if session is not None else ""
                if title:
                    titles[tid] = " ".join(str(title).split())[:90]
        return waiting, titles

    async def origin_for(self, native_id: str) -> dict[str, str]:
        """The chat's address from its checkpoint (``_gateway``), or ``{}``."""
        graph = _graph()
        if graph is None:
            return {}
        from kazma_core.runs.adapters._common import origin_from

        snapshot = await graph.aget_state({"configurable": {"thread_id": native_id, "checkpoint_ns": ""}})
        routing = (getattr(snapshot, "values", None) or {}).get("_gateway") or {}
        return origin_from(routing.get("delivery_target"), native_id)

    async def act(self, native_id: str, action: str, *, actor: str) -> ActionResult:
        if action == "cancel":
            graph = _graph()
            if graph is None:
                return ActionResult(False, "graph_unavailable", "The agent is not ready.")
            from kazma_ui.turn_abort import abort_turn

            outcome = await abort_turn(native_id, graph)
            return ActionResult(bool(outcome.get("ok")), "aborted" if outcome.get("ok") else "abort_failed",
                                "The turn was stopped." if outcome.get("ok") else str(outcome.get("warning") or ""))
        if action == "close":
            from kazma_ui.reply_sink import close_reply_turn

            await asyncio.to_thread(close_reply_turn, native_id)
            _abandoned_since.pop(native_id, None)
            logger.warning("[runs] closed an abandoned chat turn thread=%s", native_id[:12])
            return ActionResult(True, "closed", "The turn was closed; the chat can go on.")
        return ActionResult(False, "not_offered", f"Chat turns do not take {action}.")


ADAPTER = _ChatTurnAdapter()
