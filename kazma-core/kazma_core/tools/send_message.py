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

from collections.abc import Callable

_message_backends: dict[str, Callable] = {}


def register_message_backend(name: str, handler: Callable) -> None:
    """Register a platform send handler.

    Args:
        name:    Backend identifier (e.g. "telegram", "discord").
        handler: Async callable(target_id: str, text: str) -> str.
    """
    _message_backends[name] = handler


async def send_message(target_id: str, text: str, *, backend: str = "telegram") -> str:
    """Send a message through the specified backend.

    Args:
        target_id: Platform-prefixed target (e.g. "telegram:12345").
        text:      Message body.
        backend:   Which registered backend to use (default "telegram").

    Returns:
        Backend response string, or error message if backend not found.
    """
    handler = _message_backends.get(backend)
    if handler is None:
        return f"Error: no backend '{backend}'"
    return await handler(target_id, text)
