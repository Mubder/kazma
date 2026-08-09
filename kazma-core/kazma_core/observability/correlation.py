"""Turn correlation IDs.

One UUID per agent turn, carried in a :class:`ContextVar` so every log record
emitted during the turn (across ``await`` points and nested tool calls) can be
tagged without threading an argument through forty call sites. Closes the
audit observability gap: "no correlation IDs in logs or SSE frames".

Usage::

    from kazma_core.observability.correlation import (
        TurnIdFilter, bind_turn_id, current_turn_id, new_turn_id,
    )

    token = bind_turn_id(new_turn_id())     # turn entry (agent_runner / SSE)
    try:
        ...
    finally:
        reset_turn_id(token)

Attach ``TurnIdFilter`` to handlers to get ``%(turn_id)s`` in format strings
(empty string when outside a turn, so existing formats keep working).
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar, Token

__all__ = [
    "TurnIdFilter",
    "bind_turn_id",
    "current_turn_id",
    "new_turn_id",
    "reset_turn_id",
]

_current_turn_id: ContextVar[str] = ContextVar("kazma_turn_id", default="")


def new_turn_id() -> str:
    """Mint a new turn id (short uuid4 hex — enough for log correlation)."""
    return uuid.uuid4().hex[:12]


def bind_turn_id(turn_id: str) -> Token:
    """Bind *turn_id* to the current async context. Returns the reset token."""
    return _current_turn_id.set(turn_id)


def reset_turn_id(token: Token) -> None:
    """Restore the previous turn id (pass the token from :func:`bind_turn_id`)."""
    try:
        _current_turn_id.reset(token)
    except Exception:  # noqa: BLE001 — token misuse must never break a turn
        pass


def current_turn_id() -> str:
    """Return the current turn id, or ``""`` outside a turn."""
    return _current_turn_id.get()


class TurnIdFilter(logging.Filter):
    """Logging filter injecting ``turn_id`` into every record.

    Records get ``record.turn_id`` (``""`` when no turn is bound), so format
    strings can safely include ``%(turn_id)s`` everywhere.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.turn_id = current_turn_id()
        except Exception:  # noqa: BLE001
            record.turn_id = ""
        return True
