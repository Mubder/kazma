"""Telegram Adapter — aiogram-based long-polling, zero tunnels.

Uses aiogram's Dispatcher with polling (no webhooks, no public IP needed).
All incoming messages are normalized to IncomingMessage with context_metadata
carrying raw Telegram IDs (chat_id, user_id, message_id) so the Brain
never imports anything Telegram-specific.

Configuration (kazma.yaml):
    connectors:
      telegram:
        token: "123456:ABC-DEF..."
        allowed_users: []       # optional whitelist of Telegram user IDs
        parse_mode: "Markdown"  # default parse_mode for replies
"""

from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.types import Message

from kazma_gateway.gateway import (
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)


class TelegramAdapter(BaseAdapter):
    """Telegram Bot API adapter using aiogram long-polling.

    Headless: uses aiogram's built-in polling — no webhooks,
    no ngrok, no public IP required.

    Args:
        token:          Telegram Bot API token.
        allowed_users:  Optional whitelist of user IDs (empty = allow all).
        parse_mode:     Default parse_mode for outbound messages.

    context_metadata keys (carried in every IncomingMessage):
        chat_id:    int — Telegram chat ID (group, private, channel)
        user_id:    int — Sender's user ID
        username:   str — Sender's username or first_name
        message_id: int — Telegram message ID (for reply threading)
    """

    name = "telegram"

    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        parse_mode: str = "Markdown",
    ) -> None:
        super().__init__()
        self._token = token
        self._allowed_users = set(allowed_users or [])
        self._parse_mode = ParseMode(parse_mode) if parse_mode else None
        self._bot: Bot | None = None
        self._dp: Dispatcher | None = None

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Start aiogram polling and enqueue normalized messages.

        Runs until shutdown_event is set, then cleanly stops polling.

        Args:
            queue:          The unified message bus.
            shutdown_event: Signals when to stop.
        """
        self._bot = Bot(token=self._token)
        self._dp = Dispatcher()

        # Register the message handler on the dispatcher
        @self._dp.message()
        async def handle_message(message: Message) -> None:
            """Normalize and enqueue every incoming text message."""
            # Skip empty messages
            text = message.text or message.caption
            if not text or not text.strip():
                return

            # Extract sender info
            from_user = message.from_user
            user_id = from_user.id if from_user else 0
            username = (
                (from_user.username if from_user else "")
                or (from_user.first_name if from_user else "")
                or f"tg_{user_id}"
            )

            # User whitelist
            if self._allowed_users and user_id not in self._allowed_users:
                logger.debug(
                    "[telegram] Ignoring user %d (not whitelisted)",
                    user_id,
                )
                return

            chat_id = message.chat.id
            message_id = message.message_id

            # Build the normalized message — context_metadata carries
            # everything the adapter's send() needs to route the reply
            msg = IncomingMessage(
                platform="telegram",
                sender_id=f"telegram:{chat_id}",
                text=text.strip(),
                context_metadata={
                    "chat_id": chat_id,
                    "user_id": user_id,
                    "username": username,
                    "message_id": message_id,
                    "chat_type": message.chat.type,
                },
            )

            try:
                queue.put_nowait(msg)
                logger.info(
                    "[telegram] Enqueued from %s (chat=%d): %.80s",
                    username,
                    chat_id,
                    text,
                )
            except asyncio.QueueFull:
                logger.warning(
                    "[telegram] Queue full — dropping message from chat=%d",
                    chat_id,
                )

        # Start polling. aiogram's start_polling blocks until stopped,
        # so we wrap it and watch the shutdown event.
        logger.info("[telegram] Starting aiogram long-polling...")

        # Create a task for the polling loop
        polling_task = asyncio.create_task(
            self._dp.start_polling(
                self._bot,
                handle_signals=False,  # we handle shutdown ourselves
            ),
            name="telegram-polling",
        )

        # Wait until shutdown is signalled
        await shutdown_event.wait()

        # Stop polling gracefully
        logger.info("[telegram] Shutdown signalled — stopping polling...")
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

        # Close the bot session
        if self._bot:
            await self._bot.session.close()

        logger.info("[telegram] Polling stopped")

    async def send(self, outbound: OutboundMessage) -> bool:
        """Send a message back to Telegram.

        Extracts chat_id from outbound.context_metadata (carried
        verbatim from the original IncomingMessage).

        Args:
            outbound: The OutboundMessage with context_metadata["chat_id"].

        Returns:
            True if sent successfully.
        """
        if not self._bot:
            logger.error("[telegram] Bot not initialized — cannot send")
            return False

        chat_id = outbound.context_metadata.get("chat_id")
        if not chat_id:
            # Fallback: try parsing from target_id
            if ":" in outbound.target_id:
                try:
                    chat_id = int(outbound.target_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    logger.error(
                        "[telegram] No chat_id in context_metadata and cannot parse target_id: %s",
                        outbound.target_id,
                    )
                    return False
            else:
                logger.error("[telegram] No chat_id available for send()")
                return False

        try:
            await self._bot.send_message(
                chat_id=chat_id,
                text=outbound.text[:4096],
                parse_mode=self._parse_mode,
            )
            logger.debug("[telegram] Sent to %d: %.80s", chat_id, outbound.text)
            return True
        except Exception:
            logger.exception("[telegram] Failed to send to %d", chat_id)
            return False
