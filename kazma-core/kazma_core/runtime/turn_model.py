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

from contextvars import ContextVar

__all__ = [
    "current_turn_model",
    "pin_turn_model",
    "reset_turn_model",
]

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

