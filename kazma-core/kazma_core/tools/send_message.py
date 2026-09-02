"""Generic message dispatch — platform-agnostic send_message tool.

Provides a backend registry so any platform adapter can register its
send handler. The Brain calls send_message(target_id, text) without
knowing which platform handles delivery.

Usage:
    from kazma_core.tools.send_message import send_message, register_message_backend

    # Register a backend (done by adapters at startup)
    register_message_backend("telegram", my_telegram_send_func)

    # Send (Brain calls this — platform-agnostic)
    result = await send_message("telegram:12345", "Hello!")
"""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from typing import Any

__all__ = [
    "register_message_backend",
    "send_message",
    "get_current_delivery_target",
    "set_current_delivery_target",
    "reset_current_delivery_target",
    "is_valid_delivery_target",
    "operator_telegram_target",
    "resolve_delivery_target",
    "web_gateway_block",
]

_message_backends: dict[str, Callable] = {}


# ══════════════════════════════════════════════════════════════════════════
# Current delivery target ContextVar
# ══════════════════════════════════════════════════════════════════════════
# Mirrors safety/hitl.py::_current_thread_id. Holds the platform-prefixed
# delivery target (e.g. "telegram:<chat_id>") of the conversation that is
# *currently* executing, so tools (notably schedule_task) can capture it for
# later async delivery (cron reminders) without leaking the raw chat_id into
# graph state — preserving the platform-isolation invariant (AGENTS.md §2).
#
# Like _current_thread_id, this is bound at two layers:
#   1. Transport layer (gateway handler entry) — best-effort.
#   2. Tool-worker node (graph_builder.py) — authoritative, re-set from the
#      state's `_gateway` routing block on every iteration, because ContextVars
#      set at the transport layer do not reliably cross into LangGraph node
#      execution (see the existing note + _current_thread_id fallback).
_current_delivery_target: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_delivery_target", default=None
)


def set_current_delivery_target(
    target: str | None,
) -> contextvars.Token[str | None]:
    """Bind the current delivery target for this async context.

    Args:
        target: Platform-prefixed target (e.g. "telegram:12345"), or None.

    Returns:
        Token to pass to :func:`reset_current_delivery_target`.
    """
    return _current_delivery_target.set(target)


def reset_current_delivery_target(
    token: contextvars.Token[str | None],
) -> None:
    """Restore the prior delivery-target ContextVar value."""
    _current_delivery_target.reset(token)


def get_current_delivery_target() -> str | None:
    """Return the platform-prefixed delivery target of the running turn, if any.

    Returns None when no conversation is active (e.g. headless cron execution).
    """
    return _current_delivery_target.get()


def is_valid_delivery_target(target: str | None) -> bool:
    """True for a well-formed ``platform:id`` address (not a bare thread UUID)."""
    t = str(target or "").strip()
    if not t or ":" not in t or t.endswith(":"):
        return False
    plat, ident = t.split(":", 1)
    return bool(plat.strip()) and bool(ident.strip())


def operator_telegram_target() -> str:
    """The operator's Telegram chat from ConfigStore, or ``""``.

    Web-booked reminders have no inbound ``telegram:<chat_id>``. Lifecycle
    notifications already use ``connectors.telegram.swarm_chat_id`` (else
    the first allowed user). Same address so a reminder booked in the
    Web UI actually rings Telegram instead of being stored targetless.
    """
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        tg_id = store.get("connectors.telegram.swarm_chat_id")
        if not tg_id:
            allowed = store.get("connectors.telegram.allowed_users")
            if isinstance(allowed, str):
                allowed = [u.strip() for u in allowed.replace(",", " ").split() if u.strip()]
            elif not isinstance(allowed, list):
                allowed = []
            if allowed:
                tg_id = str(allowed[0])
        tg_id = str(tg_id or "").strip()
        if tg_id:
            return f"telegram:{tg_id}"
    except Exception:
        pass
    return ""


def resolve_delivery_target() -> str:
    """Best delivery address for a reminder booked on this turn.

    Order: live ContextVar (gateway/Telegram chat) → cron-parent job →
    operator Telegram. Empty means the booking must fail closed rather
    than persist ``target=(none)`` and drop the fire-time alert.
    """
    cur = str(get_current_delivery_target() or "").strip()
    if is_valid_delivery_target(cur):
        return cur
    try:
        from kazma_core.cron.scheduler import get_cron_parent

        parent = get_cron_parent()
        if parent:
            inherited = str(parent.get("delivery_target") or "").strip()
            if is_valid_delivery_target(inherited):
                return inherited
    except Exception:
        pass
    return operator_telegram_target()


def web_gateway_block(thread_id: str) -> dict[str, str]:
    """Internal ``_gateway`` routing block for Web SSE/WS turns.

    Web has no inbound ``telegram:<chat_id>``. Stamp the operator Telegram
    address (same one lifecycle notifications use) so ``schedule_task``
    captures a deliverable target. ``platform`` stays ``web`` — the
    conversation is the Web UI; only the reminder alert goes to Telegram.
    Does not put a raw ``chat_id`` on SupervisorState (AGENTS.md §2).
    """
    return {
        "thread_id": str(thread_id or ""),
        "display_name": "web",
        "platform": "web",
        "delivery_target": resolve_delivery_target(),
    }


def register_message_backend(name: str, handler: Callable) -> None:
    """Register a platform send handler.

    Args:
        name:    Backend identifier (e.g. "telegram", "discord").
        handler: Async callable(target_id: str, text: str, **kwargs) -> str.
                 The **kwargs allow passing attachments= for file delivery
                 without breaking text-only callers.
    """
    _message_backends[name] = handler


async def send_message(target_id: str, text: str, *, backend: str = "telegram", **kwargs: Any) -> str:
    """Send a message through the specified backend.

    Args:
        target_id: Platform-prefixed target (e.g. "telegram:12345").
        text:      Message body.
        backend:   Which registered backend to use (default "telegram").
        **kwargs:  Passed through to the backend handler (e.g. attachments=).

    Returns:
        Backend response string, or error message if backend not found.
    """
    handler = _message_backends.get(backend)
    if handler is None:
        return f"Error: no backend '{backend}'"
    return await handler(target_id, text, **kwargs)


async def send_file_message(
    target_id: str,
    text: str,
    *,
    file_path: str,
    backend: str = "telegram",
) -> str:
    """Send a file attachment through the specified backend.

    Reads the file at ``file_path``, wraps it as an attachment, and dispatches
    via the backend handler's ``attachments=`` kwarg. The backend (registered
    by the gateway) constructs the platform-native send-document call.

    Args:
        target_id: Platform-prefixed target (e.g. "telegram:12345").
        text:      Accompanying message text (caption).
        file_path: Absolute or workspace-relative path to the file.
        backend:   Which registered backend to use (default "telegram").

    Returns:
        Backend response string, or error message.
    """
    from pathlib import Path
    import mimetypes

    p = Path(file_path).expanduser().resolve()
    if not p.exists():
        return f"Error: file not found: {file_path}"
    if not p.is_file():
        return f"Error: not a file: {file_path}"
    if p.stat().st_size > 50 * 1024 * 1024:  # 50 MB Telegram limit
        return f"Error: file too large ({p.stat().st_size // 1024 // 1024} MB; max 50 MB)"

    data = p.read_bytes()
    mime, _ = mimetypes.guess_type(str(p))
    attachment = {
        "kind": "file",
        "filename": p.name,
        "mime": mime or "application/octet-stream",
        "data": data,
    }
    return await send_message(target_id, text, backend=backend, attachments=[attachment])
