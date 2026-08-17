"""Per-turn model pin — does not mutate the process-wide ModelRegistry.

Chat mouths send ``model`` on each SSE/WS turn. That used to call
``ensure_active_model``, which rewrote ``registry.active_model`` (and the
bound agent LLM) for every concurrent season. Two seasons on different
models would clobber each other.

Pin a ContextVar instead. ``ModelRegistry.get_client()`` and the supervisor
honor it as a one-off override; Settings / ``switch_active_model`` remain
the only process-wide switch.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from typing import Any

__all__ = [
    "current_turn_model",
    "pin_turn_model",
    "reset_turn_model",
    "resolve_turn_client",
]

logger = logging.getLogger(__name__)

_turn_model: ContextVar[str | None] = ContextVar("kazma_turn_model", default=None)


def current_turn_model() -> str | None:
    """Return the model pinned for this async task, or None."""
    try:
        raw = _turn_model.get()
    except LookupError:
        return None
    clean = (raw or "").strip()
    return clean or None


def pin_turn_model(model: str | None):
    """Sync pin for SSE/WS turns. Returns a reset token or None."""
    clean = (model or "").strip()
    if not clean:
        return None
    return _turn_model.set(clean)


def reset_turn_model(token) -> None:
    if token is None:
        return
    try:
        _turn_model.reset(token)
    except Exception:
        pass


def resolve_turn_client(default_llm: Any) -> tuple[Any, str | None]:
    """One-off client for the pinned turn model (no registry persist).

    Returns ``(client, pinned_model)``. When nothing is pinned, returns
    ``(default_llm, None)``.
    """
    pinned = current_turn_model()
    if not pinned:
        return default_llm, None
    try:
        from kazma_core.model_registry import get_model_registry

        return get_model_registry().get_client(pinned), pinned
    except Exception:
        logger.debug("[turn_model] get_client(%s) failed", pinned, exc_info=True)
        return default_llm, pinned
