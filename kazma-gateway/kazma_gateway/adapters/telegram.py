"""Telegram polling adapter — getUpdates-based, no webhooks.

Polls the Telegram Bot API getUpdates endpoint with offset tracking
to ensure no messages are lost. Normalizes each update into a
UniversalMessage on the unified bus.

Configuration (from kazma.yaml):
    connectors:
      telegram:
        token: "123456:ABC-DEF..."
        poll_interval: 1.0        # seconds between polls
        allowed_users: []         # optional user ID whitelist
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from kazma_gateway.base import BaseAdapter
from kazma_gateway.schemas import UniversalMessage

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramAdapter(BaseAdapter):
    """Telegram Bot API polling adapter.

    Uses getUpdates with offset tracking for reliable message delivery.
    No webhooks, no tunnels — pure polling.

    Args:
        token: Telegram Bot API token.
        poll_interval: Seconds between poll requests (default 1.0).
        allowed_users: Optional whitelist of user IDs. Empty = allow all.
        timeout: HTTP timeout for Telegram API calls (default 10.0).

    Target ID format:
        send("telegram:12345", "hello") — sends to chat_id 12345.
    """

    name = "telegram"

    def __init__(
        self,
        token: str,
        poll_interval: float = 1.0,
        allowed_users: list[int] | None = None,
        timeout: float = 10.0,
    ) -> None:
        super().__init__()
        self._token = token
        self._api_base = _TELEGRAM_API.format(token=token)
        self._poll_interval = poll_interval
        self._allowed_users = set(allowed_users or [])
        self._timeout = timeout
        self._offset: int = 0
        self._http: httpx.AsyncClient | None = None

    async def listen(self, queue: asyncio.Queue[UniversalMessage]) -> None:
        """Poll Telegram getUpdates in a loop.

        Runs indefinitely until cancelled. Each update is parsed,
        normalized to UniversalMessage, and enqueued.

        Args:
            queue: The unified message bus.
        """
        self._http = httpx.AsyncClient(timeout=self._timeout)

        try:
            # Clear any pending updates by fetching with a short timeout
            # on first run, then switch to long polling
            logger.info("[telegram] Starting poll loop (interval=%.1fs)", self._poll_interval)

            while self._running:
                try:
                    updates = await self._poll()
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException:
                    # Normal for long polling — just retry
                    continue
                except httpx.ConnectError:
                    logger.warning("[telegram] Connection failed, retrying in 5s")
                    await asyncio.sleep(5)
                    continue
                except Exception:
                    logger.exception("[telegram] Poll error")
                    await asyncio.sleep(3)
                    continue

                for update in updates:
                    msg = self._parse_update(update)
                    if msg:
                        # User whitelist check
                        if self._allowed_users:
                            user_id = msg.metadata.get("user_id")
                            if user_id and user_id not in self._allowed_users:
                                logger.debug("[telegram] Ignoring user %d (not whitelisted)", user_id)
                                continue

                        await queue.put(msg)
                        logger.info(
                            "[telegram] Enqueued message from %s: %.80s",
                            msg.sender_id,
                            msg.content,
                        )

                # Short sleep to prevent tight-looping when no updates
                await asyncio.sleep(self._poll_interval)

        finally:
            if self._http:
                await self._http.aclose()
                self._http = None

    async def _poll(self) -> list[dict[str, Any]]:
        """Execute a single getUpdates call.

        Uses long polling with a timeout to reduce idle requests.

        Returns:
            List of Telegram Update objects.
        """
        assert self._http is not None

        params: dict[str, Any] = {
            "timeout": 5,  # long-poll timeout (seconds)
        }
        if self._offset:
            params["offset"] = self._offset

        resp = await self._http.get(
            f"{self._api_base}/getUpdates",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            logger.error("[telegram] getUpdates not ok: %s", data)
            return []

        updates = data.get("result", [])

        # Advance offset to avoid re-processing
        if updates:
            self._offset = max(u["update_id"] for u in updates) + 1

        return updates

    def _parse_update(self, update: dict[str, Any]) -> UniversalMessage | None:
        """Parse a Telegram Update into a UniversalMessage.

        Args:
            update: Raw Telegram Update object.

        Returns:
            UniversalMessage or None if the update is not a text message.
        """
        # Extract message from various update types
        message = update.get("message") or update.get("channel_post") or update.get("edited_message")
        if not message:
            return None

        chat_id = message.get("chat", {}).get("id")
        if not chat_id:
            return None

        # Extract text (prefer text, fall back to caption)
        text = (message.get("text") or message.get("caption") or "").strip()
        if not text:
            return None

        # Build sender info
        from_user = message.get("from", {})
        user_id = from_user.get("id", 0)
        username = from_user.get("username", "") or from_user.get("first_name", "") or f"tg_{user_id}"

        return UniversalMessage(
            platform="telegram",
            sender_id=f"telegram:{chat_id}",
            content=text,
            reply_to=f"telegram:{chat_id}",
            metadata={
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "message_id": message.get("message_id", 0),
                "chat_type": message.get("chat", {}).get("type", "private"),
                "update_id": update.get("update_id", 0),
            },
        )

    async def send(self, target_id: str, content: str) -> bool:
        """Send a message to a Telegram chat.

        Args:
            target_id: Must be "telegram:<chat_id>".
            content: Message text (max 4096 chars for Telegram).

        Returns:
            True if sent successfully.
        """
        # Parse target_id
        if not target_id.startswith("telegram:"):
            logger.error("[telegram] Invalid target_id format: %s", target_id)
            return False

        try:
            chat_id = int(target_id.split(":", 1)[1])
        except (ValueError, IndexError):
            logger.error("[telegram] Cannot parse chat_id from: %s", target_id)
            return False

        if not self._http:
            self._http = httpx.AsyncClient(timeout=self._timeout)

        try:
            resp = await self._http.post(
                f"{self._api_base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": content[:4096],
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()
            result = resp.json()

            if result.get("ok"):
                logger.debug("[telegram] Sent to %d: %.80s", chat_id, content)
                return True
            else:
                logger.error("[telegram] sendMessage failed: %s", result)
                return False

        except Exception:
            logger.exception("[telegram] Failed to send to %d", chat_id)
            return False
