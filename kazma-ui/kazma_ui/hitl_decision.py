"""One way to write down a gate decision: registry, transcript, journal.

A HITL gate is decided by a person (the web card, a Telegram/Discord/Slack
button), by the approval-timeout watchdog, or by the WS debug path. Each of
them used to record the decision its own way, and each missed something:

* the watchdog claimed the registry row but never stamped the transcript
  part, so the chat kept the card ``pending`` forever -- a turn that renders
  with a dead Approve row and no answer, ``text-missing`` on every reload
  (live 2026-09-26, turn 9bdd89fd93cb);
* the platform buttons claimed the registry row but neither stamped the
  transcript nor told the journal, so a browser watching that thread never
  saw the decision.

:func:`record_gate_decision` is the one writer. The order is load-bearing:
transcript stamp, registry CAS, THEN the journal frame -- the broker stamps
the gate's view onto the frame from the registry, and emitting before the
CAS painted a pending view on a decided frame ("No longer pending",
2026-09-20). Every step is best-effort and logged: recording must never
stop the resume that follows it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["DECISIONS", "record_gate_decision"]

#: decision -> (transcript/journal state, registry decision)
DECISIONS: dict[str, tuple[str, str]] = {
    "approved": ("approved", "approve"),
    "denied": ("denied", "deny"),
    "timeout": ("timeout", "deny"),
}


def _resolve_turn(thread_id: str, session_id: str, turn_id: str) -> tuple[str, str]:
    """The session and EXISTING turn the decision belongs to, or blanks.

    ``resolve_reply_turn`` mints a fresh id when no turn is open; stamping
    under that would add an empty bubble holding nothing but the card (a
    platform button on a thread no browser has a turn for). So a resolved id
    counts only when the session already has an assistant row under it.
    """
    if session_id and turn_id:
        return session_id, turn_id
    try:
        from kazma_ui.reply_sink import resolve_reply_turn
        from kazma_ui.session_manager import get_session_manager

        owner = get_session_manager().get_by_thread_id(thread_id)
        if owner is None:
            return session_id, turn_id
        session_id = session_id or str(getattr(owner, "session_id", "") or "")
        if session_id and not turn_id:
            candidate = str(resolve_reply_turn(thread_id, session_id) or "")
            rows = getattr(owner, "messages", None) or []
            if candidate and any(
                isinstance(m, dict)
                and str(m.get("role") or "") == "assistant"
                and str(m.get("turn_id") or "") == candidate
                for m in rows
            ):
                turn_id = candidate
    except Exception:  # noqa: BLE001 -- no turn found is "do not stamp", never an error
        logger.warning("[gate-decision] turn lookup failed", exc_info=True)
    return session_id, turn_id


async def _step(label: str, run: Callable[[], Awaitable[None]]) -> None:
    """Run one recording step; a failure is logged and the next step runs.

    One handler for all three steps: recording must never stop the resume
    that follows it, and a writer that failed is worth a warning -- a gate
    whose transcript and registry disagree is the ghost-card class.
    """
    try:
        await run()
    except Exception:  # noqa: BLE001 -- see the docstring
        logger.warning("[gate-decision] %s failed", label, exc_info=True)


async def record_gate_decision(
    thread_id: str,
    *,
    decision: str,
    actor: str,
    tool: str = "",
    payload: dict[str, Any] | None = None,
    interrupt_id: str = "",
    session_id: str = "",
    turn_id: str = "",
) -> None:
    """Write one gate decision everywhere it is read.

    A failed step is logged and the next still runs; an unknown ``decision``
    is a programmer error and raises ValueError.
    """
    if decision not in DECISIONS:
        raise ValueError(f"unknown gate decision {decision!r}")
    if not thread_id:
        return
    state, registry_decision = DECISIONS[decision]
    body = dict(payload) if isinstance(payload, dict) else {}
    iid = str(interrupt_id or body.get("interrupt_id") or "")
    tool = str(tool or body.get("tool") or "")

    session_id, turn_id = await asyncio.to_thread(_resolve_turn, thread_id, session_id, turn_id)

    async def _stamp() -> None:
        # 1. The transcript: what every reload paints.
        if not (session_id and turn_id):
            return
        from kazma_ui.sse_chat._streaming import stamp_hitl_part_state

        await asyncio.to_thread(
            stamp_hitl_part_state,
            session_id,
            turn_id,
            state=state,
            thread_id=thread_id,
            tool=tool,
            payload=body,
            interrupt_id=iid,
        )

    async def _claim() -> None:
        # 2. The registry: decision truth (AGENTS.md §30).
        from kazma_ui.hitl_gate_bridge import (
            gate_claimed,
            gate_claimed_for_thread,
            gate_resuming,
        )

        if iid:
            await gate_claimed(thread_id, iid, registry_decision, actor,
                               tool=tool, payload=body or None)
            await gate_resuming(iid)
        else:
            # Platform cards carry no interrupt id: the oldest pending gate
            # on the thread is the one they asked about.
            await gate_claimed_for_thread(thread_id, registry_decision, actor,
                                          tool=tool, payload=body or None)

    async def _tell() -> None:
        # 3. The journal: every open tab, whichever tab (or platform) decided.
        from kazma_ui.delivery import get_turn_broker

        await get_turn_broker().emit(thread_id, {
            "type": "hitl",
            "data": {
                "state": state,
                "interrupt_id": iid,
                "tool": tool,
                "thread_id": thread_id,
                "turn_id": turn_id,
                "actor": actor,
            },
        })

    await _step("transcript stamp", _stamp)
    await _step("registry claim", _claim)
    await _step("journal frame", _tell)
    logger.info("[gate-decision] %s thread=%s gate=%s by %s",
                state, thread_id[:12], iid[:12] or "?", actor)
