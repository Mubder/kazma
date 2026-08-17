"""Live Majlis orchestrator — per-sender phase machine for chat mouths.

Greeting/farewell short-circuits use :class:`MajlisProtocol` (not just the
pacing/tone building blocks). Real work still reaches the supervisor: long
messages and transaction/social turns are never canned.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict

from kazma_core.majlis import ConversationPhase, MajlisProtocol
from kazma_core.pacing import Intent

__all__ = ["maybe_majlis_short_circuit", "reset_majlis_runtime"]

logger = logging.getLogger(__name__)

# Same cap as the gateway F7 fast-path.
_FAST_PATH_MAX_LEN = 60
_MAX_SESSIONS = 64

_lock = threading.Lock()
_sessions: OrderedDict[str, MajlisProtocol] = OrderedDict()


def reset_majlis_runtime() -> None:
    with _lock:
        _sessions.clear()


def _protocol_for(sender_id: str) -> MajlisProtocol:
    key = (sender_id or "default").strip() or "default"
    with _lock:
        proto = _sessions.get(key)
        if proto is not None:
            _sessions.move_to_end(key)
            return proto
        proto = MajlisProtocol()
        _sessions[key] = proto
        while len(_sessions) > _MAX_SESSIONS:
            _sessions.popitem(last=False)
        return proto


async def maybe_majlis_short_circuit(text: str, *, sender_id: str = "") -> str | None:
    """Return a cultural greeting/farewell, or None to run the supervisor.

    Fail-open: any error returns None.
    """
    raw = (text or "").strip()
    if not raw or len(raw) > _FAST_PATH_MAX_LEN:
        return None
    try:
        proto = _protocol_for(sender_id)
        intent = proto.pacing.detect_intent(raw)
        if intent not in (Intent.GREETING, Intent.FAREWELL):
            return None
        # A new greeting after farewell starts a new sitting.
        if intent == Intent.GREETING:
            proto.conversation_state.transition_to(ConversationPhase.GREETING)
        try:
            await proto.process_input(raw)
        except Exception:
            logger.debug("[majlis] process_input failed", exc_info=True)
        if intent == Intent.FAREWELL:
            return "في أمان الله 👋"
        return proto.pacing.get_greeting_reply(
            raw,
            is_ramadan=proto.cultural_context.state.is_ramadan,
            is_eid=proto.cultural_context.state.is_eid,
        )
    except Exception:
        logger.debug("[majlis] orchestrator skipped", exc_info=True)
        return None
