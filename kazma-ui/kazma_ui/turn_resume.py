"""A turn a reload cut off continues (resilience plan C1).

Since 2 November a turn a reload cuts off ends with an honest notice
(``stopped_for_shutdown``, FA-262), and the owner had to send the request
again. Its checkpoint still holds the turn half way: a next step and no
approval waiting. At start (``resume_cut_turns``, once the chat apps have had
time to connect) Kazma finds those turns (the newest checkpoint of each chat,
written in the last 30 minutes) and:

* continues a **safe** one by itself -- its last step was a model call, or
  tools whose effects are all on record (``agent.effect_journal``: a tool
  started and finished; the receipt answers the repeat) -- a web turn into
  its journal (every open tab sees it), a chat app's turn back to its chat
  with a line saying it continues after a restart;
* leaves an **unsafe** one -- a tool that changes something was started and
  its receipt has no result, so its effect is unknown -- to the owner: it is
  listed as a run (``runs_chat_turn``, native state ``cut``) with **Continue**
  and **Close**, and the run supervisor's card says which tool's effect is
  unknown. Continue resumes it; the effect journal still refuses to repeat the
  unknown call, so the turn says so instead of doing it twice.

Never a turn paused for an approval (its card is the action), never one that
already ended (``turn_failed`` or no next step), never one older than 30
minutes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["continue_turn", "cut_turns", "forget_cut_turn", "register_chat_app_continuer", "resume_cut_turns"]

#: A turn cut longer ago than this is not continued (its moment has passed).
RESUME_WINDOW_S = 30 * 60.0
#: Wait this long after start before continuing: the chat apps connect first.
START_DELAY_S = 30.0
#: At most this many checkpoints are read looking for cut turns.
_SCAN_LIMIT = 400
_CHAT_APPS = frozenset({"telegram", "discord", "slack"})

#: thread_id -> the cut turn left to the owner (the runs adapter lists them).
_left: dict[str, CutTurn] = {}
_continuer: Any = None


@dataclass
class CutTurn:
    thread_id: str
    at: float
    next: tuple[str, ...]
    platform: str = "web"
    target: str = ""
    user_text: str = ""
    unknown: list[str] = field(default_factory=list)   # tools whose effect is unknown

    @property
    def safe(self) -> bool:
        return not self.unknown


def register_chat_app_continuer(continuer: Any) -> None:
    """The gateway's way to continue a chat app's turn and deliver it."""
    global _continuer
    _continuer = continuer


def cut_turns() -> dict[str, CutTurn]:
    """The cut turns left to the owner (a copy)."""
    return dict(_left)


def forget_cut_turn(thread_id: str) -> None:
    """The owner closed it: no longer offered."""
    _left.pop(thread_id, None)


def _created_epoch(value: Any) -> float:
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except ValueError:
        return 0.0


def _open_effects(thread_id: str, since: float) -> list[str]:
    """The tools of this turn whose receipt has no result (effect unknown)."""
    from kazma_core.agent.effect_journal import _EffectJournal
    from kazma_core.paths import data_dir

    unknown = []
    for row in _EffectJournal(data_dir() / "tool_effects.db").inspect(thread_id):
        if float(row.get("started_at") or 0) < since - 1:
            continue
        if row.get("state") != "completed" or row.get("result_uncertain"):
            unknown.append(str(row.get("tool") or "a tool"))
    return unknown


def _last_user_text(messages: list[Any]) -> str:
    for msg in reversed(messages or []):
        if getattr(msg, "type", "") == "human":
            content = getattr(msg, "content", "")
            return content if isinstance(content, str) else ""
    return ""


async def _find_cut_turns(checkpointer: Any, graph: Any, now: float | None = None) -> list[CutTurn]:
    """Every chat whose newest checkpoint, written in the last 30 minutes,
    stops half way through a turn with no approval waiting."""
    from kazma_core.checkpoint_retention import checkpoint_time

    now = time.time() if now is None else now
    # Every chat's newest checkpoint first, then each one read: a saver may
    # hold its connection while it lists (SQLite's does), so reading a state
    # inside the listing waits on the listing for good.
    recent: list[tuple[str, float]] = []
    seen: set[str] = set()
    async for tup in checkpointer.alist(None, limit=_SCAN_LIMIT):
        cfg = (getattr(tup, "config", None) or {}).get("configurable") or {}
        thread_id, ns = str(cfg.get("thread_id") or ""), str(cfg.get("checkpoint_ns") or "")
        if not thread_id or ns or thread_id in seen:
            continue
        seen.add(thread_id)
        at = checkpoint_time(cfg.get("checkpoint_id"))
        if at is None:
            continue
        if now - at > RESUME_WINDOW_S:
            break                         # newest first: everything after is older
        recent.append((thread_id, at))
    found: list[CutTurn] = []
    for thread_id, at in recent:
        snapshot = await graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})
        values = getattr(snapshot, "values", None) or {}
        if not getattr(snapshot, "next", None) or values.get("turn_failed"):
            continue
        if any(getattr(task, "interrupts", None) for task in getattr(snapshot, "tasks", ()) or ()):
            continue                      # paused for an approval: its card is the action
        routing = values.get("_gateway") or {}
        platform = str(routing.get("platform") or "web").lower()
        unknown = await asyncio.to_thread(_open_effects, thread_id, _created_epoch(values.get("created_at")))
        found.append(CutTurn(thread_id=thread_id, at=at, next=tuple(snapshot.next), platform=platform,
                             target=str(routing.get("delivery_target") or ""),
                             user_text=_last_user_text(values.get("messages") or []), unknown=unknown))
    return found


async def continue_turn(graph: Any, turn: CutTurn, *, drive: Any = None) -> tuple[bool, str]:
    """Continue *turn* from its checkpoint. ``(started, words)``. *drive* runs
    a web turn into its journal (``_drive_graph_to_journal`` by default)."""
    from kazma_ui.active_turns import register_turn, running_turns

    if turn.thread_id in running_turns():
        return False, "The turn is already running."
    config = {"configurable": {"thread_id": turn.thread_id, "checkpoint_ns": ""}}
    if turn.platform in _CHAT_APPS:
        if _continuer is None or not turn.target.startswith(f"{turn.platform}:"):
            return False, "The chat app this turn belongs to is not connected."
        task = asyncio.create_task(_continuer(thread_id=turn.thread_id, resume_config=config,
                                              platform=turn.platform, target_id=turn.target,
                                              user_text=turn.user_text))
    else:
        from kazma_ui.reply_sink import resolve_reply_turn
        from kazma_ui.session_manager import get_session_manager
        if drive is None:
            from kazma_ui.sse_chat._streaming import _drive_graph_to_journal as drive

        session = await asyncio.to_thread(get_session_manager().get_by_thread_id, turn.thread_id)
        session_id = getattr(session, "id", "") or ""
        reply_turn = await asyncio.to_thread(resolve_reply_turn, turn.thread_id, session_id)
        task = asyncio.create_task(drive(graph, None, config, thread_id=turn.thread_id,
                                         session_id=session_id, reply_turn_id=reply_turn))
    register_turn(turn.thread_id, task)
    _left.pop(turn.thread_id, None)
    logger.warning("[turn_resume] continuing thread=%s (%s) cut off by a restart", turn.thread_id[:12],
                   turn.platform)
    return True, "The turn continues."


async def resume_cut_turns(graph_getter: Any, checkpointer_getter: Any, *, delay_s: float = START_DELAY_S) -> int:
    """At start: continue the safe cut turns, leave the rest to the owner.
    Returns how many were continued."""
    from kazma_core.shutdown import sleep_or_shutdown

    if await sleep_or_shutdown(delay_s):
        return 0
    graph, checkpointer = graph_getter(), checkpointer_getter()
    if graph is None or checkpointer is None:
        return 0
    continued = 0
    for turn in await _find_cut_turns(checkpointer, graph):
        if turn.safe:
            started, _ = await continue_turn(graph, turn)
            continued += started
        else:
            _left[turn.thread_id] = turn
            logger.warning("[turn_resume] thread=%s was cut off by a restart while %s ran; its effect is "
                           "unknown, so it waits for the owner (Continue or Close)",
                           turn.thread_id[:12], ", ".join(turn.unknown))
    return continued
