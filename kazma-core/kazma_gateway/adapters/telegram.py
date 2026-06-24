"""Telegram Adapter — Long-polling Telegram client using getUpdates.

No webhooks, no tunnels, no public IP required. Just a bot token.
The adapter runs a background asyncio task that polls getUpdates
every 1 second and pushes normalized Messages onto the gateway queue.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

from kazma_gateway.base import AdapterStatus, BaseAdapter, Message

logger = logging.getLogger(__name__)

TELEGRAM_API_BASE = "https://api.telegram.org"
POLL_INTERVAL = 1.0  # seconds between getUpdates calls
MAX_TIMEOUT = 30  # long-polling timeout (Telegram will hold the connection)


class TelegramAdapter(BaseAdapter):
    """Poll-based Telegram adapter.  No tunnels, no webhooks.

    Args:
        token: Bot token. If None, reads TELEGRAM_BOT_TOKEN from env.
        allowed_updates: List of update types to process.
            Default: ["message", "channel_post"].
    """

    def __init__(
        self,
        token: str | None = None,
        allowed_updates: list[str] | None = None,
    ) -> None:
        super().__init__(name="telegram", platform="telegram")

        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
        if not self.token:
            logger.warning("[Telegram] No bot token — adapter will stay STOPPED")

        self.allowed_updates = allowed_updates or ["message", "channel_post"]
        self._client: httpx.AsyncClient | None = None
        self._offset: int = 0  # Telegram update_id tracking

    # ── Client ───────────────────────────────────────────────────────

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=f"{TELEGRAM_API_BASE}/bot{self.token}",
                timeout=httpx.Timeout(MAX_TIMEOUT + 5, connect=10.0),
            )
        return self._client

    async def _close_client(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # ── Send (outbound) ──────────────────────────────────────────────

    async def send(self, target_id: str, content: str, **kwargs: Any) -> str:
        """Send a message to a Telegram chat.

        Args:
            target_id: Chat ID (with or without 'telegram:' prefix).
            content: Message text. Supports HTML parse_mode by default.
        """
        chat_id = target_id.replace("telegram:", "") if ":" in target_id else target_id

        client = self._get_client()
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": content,
            "parse_mode": kwargs.get("parse_mode", "HTML"),
            "disable_web_page_preview": True,
        }

        try:
            resp = await client.post("/sendMessage", json=payload)
            data = resp.json()
            if data.get("ok"):
                msg_id = data.get("result", {}).get("message_id", "?")
                logger.info("[Telegram] Sent to chat_id=%s (msg_id=%s)", chat_id, msg_id)
                self.message_count += 1
                return str(msg_id)
            else:
                err = data.get("description", f"HTTP {resp.status_code}")
                logger.error("[Telegram] Send failed (chat_id=%s): %s", chat_id, err)
                self.error_count += 1
                return f"Error: {err}"
        except httpx.ConnectError:
            self.error_count += 1
            return "Error: Cannot connect to api.telegram.org"
        except Exception as exc:
            self.error_count += 1
            logger.exception("[Telegram] Send exception (chat_id=%s)", chat_id)
            return f"Error: {exc}"

    # ── Polling loop (inbound) ───────────────────────────────────────

    async def _poll(self) -> None:
        """Long-poll getUpdates, emit Messages onto the gateway queue."""
        if not self.token:
            self.status = AdapterStatus.ERROR
            self.last_error = "No bot token configured"
            logger.error("[Telegram] Polling aborted — no token")
            return

        client = self._get_client()
        logger.info("[Telegram] Polling started (offset=%d)", self._offset)

        while not self._stop_event.is_set():
            try:
                params: dict[str, Any] = {
                    "offset": self._offset,
                    "timeout": MAX_TIMEOUT,
                    "allowed_updates": self.allowed_updates,
                }

                resp = await client.get("/getUpdates", params=params)
                data = resp.json()

                if not data.get("ok"):
                    desc = data.get("description", "unknown")
                    logger.warning("[Telegram] getUpdates error: %s", desc)
                    await asyncio.sleep(POLL_INTERVAL)
                    continue

                updates = data.get("result", [])
                for update in updates:
                    await self._handle_update(update)

                # Update offset (last update_id + 1)
                if updates:
                    self._offset = updates[-1]["update_id"] + 1

            except asyncio.CancelledError:
                break
            except httpx.TimeoutException:
                # Long-poll timeout is normal — just loop again
                continue
            except httpx.ConnectError:
                logger.warning("[Telegram] Connection lost, retrying in 5s...")
                await asyncio.sleep(5)
            except Exception as exc:
                logger.exception("[Telegram] Poll error: %s", exc)
                self.last_error = str(exc)[:120]
                await asyncio.sleep(POLL_INTERVAL)

        await self._close_client()
        logger.info("[Telegram] Polling stopped")

    async def _handle_update(self, raw: dict[str, Any]) -> None:
        """Parse a single Telegram update and emit a normalized Message."""
        # Extract message from any update shape
        message = raw.get("message") or raw.get("channel_post") or raw.get("edited_message")
        if not message:
            return

        chat = message.get("chat", {})
        chat_id = chat.get("id")
        text = message.get("text") or message.get("caption", "")
        if not chat_id or not text:
            return

        from_user = message.get("from", {})
        username = from_user.get("username") or from_user.get("first_name", f"tg_{chat_id}")

        msg = Message(
            sender_id=f"telegram:{chat_id}",
            content=text.strip(),
            platform="telegram",
            platform_message_id=str(message.get("message_id", "")),
            metadata={
                "chat_type": chat.get("type", "private"),
                "username": username,
                "message_id": message.get("message_id"),
                "raw_update_id": raw.get("update_id"),
            },
        )

        await self.emit(msg)
        logger.debug("[Telegram] ← msg from %s: %.80s", username, msg.content)

    # ── Lifecycle helpers ────────────────────────────────────────────

    async def start(self) -> None:
        """Override start with a connectivity check first."""
        if not self.token:
            self.status = AdapterStatus.ERROR
            self.last_error = "No bot token configured"
            logger.error("[Telegram] Cannot start — no token")
            return

        # Check connectivity
        client = self._get_client()
        try:
            resp = await client.get("/getMe")
            data = resp.json()
            if data.get("ok"):
                bot = data["result"]
                logger.info("[Telegram] Connected as @%s (%s)", bot["username"], bot["first_name"])
            else:
                self.status = AdapterStatus.ERROR
                self.last_error = data.get("description", "getMe failed")
                return
        except Exception as exc:
            self.status = AdapterStatus.ERROR
            self.last_error = str(exc)[:120]
            return

        await super().start()

    def status_info(self) -> dict[str, Any]:
        info = super().status_info()
        info["token_configured"] = bool(self.token)
        info["offset"] = self._offset
        return info
