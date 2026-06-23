"""Telegram Tools — Agent tools for interacting with the Telegram Bot API.

Tools in this module allow Kazma agents to send messages back to
Telegram users. These are registered as standard local tools that
the LangGraph tool_worker node can execute.

Usage:
    from kazma_core.tools.telegram_tools import send_telegram_message

    # Direct call
    result = await send_telegram_message(chat_id="123456", text="مرحباً")

    # Via LocalToolRegistry
    registry = LocalToolRegistry(include_builtins=True)
    registry.register_function(
        name="send_telegram_message",
        func=send_telegram_message,
        description="Send a message to a Telegram chat.",
        category="communication",
    )
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_SEND_TIMEOUT = 15.0  # seconds

# ── Error types ────────────────────────────────────────────────────────


class TelegramError(Exception):
    """Base exception for Telegram API errors."""

    def __init__(self, message: str, code: int = 0) -> None:
        super().__init__(message)
        self.code = code


class TelegramRateLimitError(TelegramError):
    """Raised when Telegram returns 429 (rate limited)."""


class TelegramBlockedError(TelegramError):
    """Raised when the user has blocked the bot (403 Forbidden)."""


# ── Helpers ────────────────────────────────────────────────────────────


def _resolve_token(token: str | None = None) -> str:
    """Resolve the bot token from argument or environment.

    Args:
        token: Explicit bot token. If None, reads TELEGRAM_BOT_TOKEN env var.

    Returns:
        The bot token string.

    Raises:
        ValueError: If no token is provided and env var is not set.
    """
    import os

    if token:
        return token
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not env_token:
        raise ValueError(
            "No Telegram bot token provided. Set TELEGRAM_BOT_TOKEN environment variable "
            "or pass `token=...` explicitly."
        )
    return env_token


async def _post_telegram(
    token: str,
    method: str,
    payload: dict[str, Any],
    *,
    timeout: float = TELEGRAM_SEND_TIMEOUT,
) -> dict[str, Any]:
    """Send a POST request to the Telegram Bot API.

    Args:
        token: Bot token.
        method: API method name (e.g. 'sendMessage').
        payload: JSON payload for the request.
        timeout: Request timeout in seconds.

    Returns:
        Parsed JSON response from Telegram.

    Raises:
        TelegramRateLimitError: On 429 responses.
        TelegramBlockedError: On 403 (bot blocked) responses.
        TelegramError: On other non-2xx responses.
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/{method}"

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(url, json=payload)
        data = response.json() if response.text else {}

    if not data.get("ok", False):
        error_code = response.status_code
        description = data.get("description", f"HTTP {error_code}")

        if error_code == 429:
            retry_after = response.headers.get("Retry-After", "unknown")
            raise TelegramRateLimitError(
                f"Telegram rate limit hit (429). Retry after {retry_after}s. {description}",
                code=error_code,
            )
        if error_code == 403 and "blocked" in description.lower():
            raise TelegramBlockedError(
                f"Telegram bot blocked by user: {description}",
                code=error_code,
            )
        raise TelegramError(f"Telegram API error ({error_code}): {description}", code=error_code)

    return data


# ── Core tool ──────────────────────────────────────────────────────────


async def send_telegram_message(
    chat_id: str,
    text: str,
    *,
    token: str | None = None,
    parse_mode: str = "HTML",
    disable_web_page_preview: bool = True,
) -> str:
    """Send a message to a Telegram chat via the Bot API.

    This is a standard Kazma tool — compatible with LocalToolRegistry
    and the LangGraph tool_worker node.  The tool handles rate limits,
    blocked chats, and other Telegram errors gracefully.

    Args:
        chat_id: Target Telegram chat ID (string, e.g. '123456789').
        text: Message text to send. Supports HTML formatting when
              parse_mode='HTML'.
        token: Optional bot token override. If omitted, reads
               TELEGRAM_BOT_TOKEN from the environment.
        parse_mode: Telegram parse mode ('HTML', 'MarkdownV2', or '').
        disable_web_page_preview: Whether to disable link previews.

    Returns:
        JSON string with the Telegram API response or error details.

    Example:
        >>> result = await send_telegram_message(
        ...     chat_id="123456789",
        ...     text="مرحباً بك في كاظمه!",
        ... )
        >>> print(result)
        '{"ok": true, "result": {"message_id": 42, ...}}'
    """
    resolved_token: str
    try:
        resolved_token = _resolve_token(token)
    except ValueError as exc:
        logger.error("[TelegramTool] Token resolution failed: %s", exc)
        return f"Error: {exc}"

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": disable_web_page_preview,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        data = await _post_telegram(resolved_token, "sendMessage", payload)
        logger.info(
            "[TelegramTool] Message sent to chat_id=%s (msg_id=%s)",
            chat_id,
            data.get("result", {}).get("message_id", "?"),
        )
        import json

        return json.dumps(data, ensure_ascii=False, indent=2)
    except TelegramRateLimitError as exc:
        logger.warning("[TelegramTool] Rate limited for chat_id=%s: %s", chat_id, exc)
        return f"Error: Telegram rate limit hit. Retry later. ({exc})"
    except TelegramBlockedError as exc:
        logger.warning("[TelegramTool] Bot blocked by chat_id=%s: %s", chat_id, exc)
        return f"Error: Bot blocked by user. Cannot send message. ({exc})"
    except TelegramError as exc:
        logger.error("[TelegramTool] API error for chat_id=%s: %s", chat_id, exc)
        return f"Error: Telegram API error (code={exc.code}): {exc}"
    except Exception as exc:
        logger.exception("[TelegramTool] Unexpected error sending to chat_id=%s", chat_id)
        return f"Error: Unexpected failure sending Telegram message: {exc}"
