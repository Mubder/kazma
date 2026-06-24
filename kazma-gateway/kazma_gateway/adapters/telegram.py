"""Telegram Adapter — Manual getUpdates polling with jitter + webhook ingress.

Headless: pure polling against the Telegram Bot API.
No webhooks required, no aiogram Dispatcher, no public IP needed.
Full control over poll timing, jitter, and rate-limit handling.

An optional webhook ingress router is available via create_webhook_router()
for testing or deployments with a public URL. Both polling and webhook
feed into the same asyncio.Queue — the Brain sees no difference.

All incoming messages are normalized to IncomingMessage with context_metadata
carrying raw Telegram IDs so the Brain never imports Telegram-specific code.

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
import random
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kazma_gateway.gateway import (
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Rate-limit constants
_SEND_MAX_RETRIES = 3
_SEND_BASE_DELAY = 1.0  # base delay for exponential backoff on 429


class TelegramAdapter(BaseAdapter):
    """Telegram Bot API adapter using manual getUpdates polling.

    Headless: direct HTTP polling — no webhooks, no aiogram Dispatcher,
    no ngrok, no public IP required.

    Every poll cycle includes a 1-3 second randomized jitter delay
    to prevent rate-limiting and API hammering (mandate requirement).

    Optionally exposes a webhook ingress router via create_webhook_router()
    for testing or when a public URL is available.

    Args:
        token:          Telegram Bot API token.
        allowed_users:  Optional whitelist of user IDs (empty = allow all).
        parse_mode:     Default parse_mode for outbound messages.
        poll_timeout:   Long-poll timeout in seconds for getUpdates (default 5).

    context_metadata keys (carried in every IncomingMessage):
        chat_id:    int — Telegram chat ID (group, private, channel)
        user_id:    int — Sender's user ID
        username:   str — Sender's username or first_name
        message_id: int — Telegram message ID (for reply threading)
        chat_type:  str — "private", "group", "supergroup", "channel"
    """

    name = "telegram"

    def __init__(
        self,
        token: str,
        allowed_users: list[int] | None = None,
        parse_mode: str = "Markdown",
        poll_timeout: int = 5,
    ) -> None:
        super().__init__()
        self._token = token
        self._api_base = _TELEGRAM_API.format(token=token)
        self._allowed_users = set(allowed_users or [])
        self._parse_mode = parse_mode or ""
        self._poll_timeout = poll_timeout
        self._offset: int = 0
        self._http: httpx.AsyncClient | None = None
        # Queue ref stored for webhook ingress (set by start() in BaseAdapter)
        self._queue: asyncio.Queue[IncomingMessage] | None = None

    async def start(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Start the adapter and store queue reference for webhook ingress."""
        self._queue = queue
        await super().start(queue, shutdown_event)

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Poll Telegram getUpdates in a loop with 1-3s jitter.

        Manual polling gives us full control over timing:
          - getUpdates with long-poll (5s timeout) reduces idle requests
          - Offset tracking ensures no message is processed twice
          - 1-3s randomized jitter between cycles (mandate requirement)
          - Clean exit on shutdown_event

        Args:
            queue:          The unified message bus.
            shutdown_event: Signals when to stop.
        """
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

        try:
            logger.info("[telegram] Starting getUpdates polling loop")

            while not shutdown_event.is_set():
                # ── Poll ────────────────────────────────────────────
                try:
                    updates = await self._poll()
                except asyncio.CancelledError:
                    raise
                except httpx.TimeoutException:
                    # Normal for long-poll — just jitter and retry
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue
                except httpx.ConnectError:
                    logger.warning("[telegram] Connection failed — retrying after jitter")
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue
                except Exception:
                    logger.exception("[telegram] Poll error")
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue

                # ── Process updates ─────────────────────────────────
                for update in updates:
                    if shutdown_event.is_set():
                        break

                    msg = self._parse_update(update)
                    if msg is None:
                        continue

                    # User whitelist
                    if self._allowed_users:
                        user_id = msg.context_metadata.get("user_id", 0)
                        if user_id not in self._allowed_users:
                            logger.debug(
                                "[telegram] Ignoring user %d (not whitelisted)",
                                user_id,
                            )
                            continue

                    try:
                        queue.put_nowait(msg)
                        logger.info(
                            "[telegram] Enqueued from %s (chat=%d): %.80s",
                            msg.context_metadata.get("username", "?"),
                            msg.context_metadata.get("chat_id", 0),
                            msg.text,
                        )
                    except asyncio.QueueFull:
                        logger.warning(
                            "[telegram] Queue full — dropping message from chat=%d",
                            msg.context_metadata.get("chat_id", 0),
                        )

                # ── Jitter (mandatory 1-3s delay) ───────────────────
                if await self.jitter_sleep(shutdown_event):
                    break

        finally:
            if self._http:
                await self._http.aclose()
                self._http = None
            logger.info("[telegram] Polling stopped")

    async def _poll(self) -> list[dict[str, Any]]:
        """Execute a single getUpdates call with long-poll.

        Uses Telegram's built-in long-polling (timeout parameter)
        to reduce idle requests. Advances the offset after each batch.

        Returns:
            List of Telegram Update objects.
        """
        assert self._http is not None

        params: dict[str, Any] = {"timeout": self._poll_timeout}
        if self._offset:
            params["offset"] = self._offset

        resp = await self._http.get(
            f"{self._api_base}/getUpdates",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            logger.error("[telegram] getUpdates returned ok=false: %s", data)
            return []

        updates = data.get("result", [])

        # Advance offset to avoid re-processing
        if updates:
            self._offset = max(u["update_id"] for u in updates) + 1

        return updates

    def _parse_update(self, update: dict[str, Any]) -> IncomingMessage | None:
        """Parse a Telegram Update into an IncomingMessage.

        Extracts the message from various update types (message,
        channel_post, edited_message) and normalizes into IncomingMessage
        with all raw platform data in context_metadata.

        Args:
            update: Raw Telegram Update object.

        Returns:
            IncomingMessage or None if not a text message.
        """
        # Extract message from various update shapes
        message = update.get("message") or update.get("channel_post") or update.get("edited_message")
        if not message:
            return None

        chat_id = message.get("chat", {}).get("id")
        if not chat_id:
            return None

        # Extract text (prefer text, fall back to caption for media)
        text = (message.get("text") or message.get("caption") or "").strip()
        if not text:
            return None

        # Build sender info
        from_user = message.get("from", {})
        user_id = from_user.get("id", 0)
        username = from_user.get("username", "") or from_user.get("first_name", "") or f"tg_{user_id}"

        return IncomingMessage(
            platform="telegram",
            sender_id=f"telegram:{chat_id}",
            text=text,
            context_metadata={
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "message_id": message.get("message_id", 0),
                "chat_type": message.get("chat", {}).get("type", "private"),
                "update_id": update.get("update_id", 0),
            },
        )

    def create_webhook_router(self) -> Any:
        """Create a FastAPI router for optional webhook ingress.

        This is NOT the primary message path — polling is. The webhook
        endpoint exists for testing (curl) and deployments with a public URL.
        Both paths feed into the same asyncio.Queue.

        Returns:
            FastAPI APIRouter with POST /telegram endpoint.

        Usage:
            adapter = TelegramAdapter(token="...")
            router = adapter.create_webhook_router()
            app.include_router(router, prefix="/api/webhooks/telegram")
        """
        router = APIRouter(tags=["telegram-webhook"])

        @router.post("")
        async def handle_update(request: Request) -> JSONResponse:
            """Accept a Telegram update via webhook POST.

            Parses the update using the same _parse_update() as polling,
            and enqueues it on the unified message bus.
            """
            try:
                update = await request.json()
            except Exception:
                return JSONResponse({"error": "Invalid JSON"}, status_code=400)

            msg = self._parse_update(update)
            if msg is None:
                return JSONResponse({"status": "ignored", "reason": "no_text"})

            # User whitelist
            if self._allowed_users:
                user_id = msg.context_metadata.get("user_id", 0)
                if user_id not in self._allowed_users:
                    return JSONResponse({"status": "ignored", "reason": "not_whitelisted"})

            if self._queue is None:
                logger.error("[telegram-webhook] Queue not initialized — adapter not started")
                return JSONResponse({"error": "Gateway not ready"}, status_code=503)

            try:
                self._queue.put_nowait(msg)
                logger.info(
                    "[telegram-webhook] Enqueued from %s (chat=%d): %.80s",
                    msg.context_metadata.get("username", "?"),
                    msg.context_metadata.get("chat_id", 0),
                    msg.text,
                )
                return JSONResponse(
                    {
                        "status": "accepted",
                        "sender_id": msg.sender_id,
                    }
                )
            except asyncio.QueueFull:
                logger.warning("[telegram-webhook] Queue full — dropping message")
                return JSONResponse({"error": "Queue full"}, status_code=503)

        @router.get("/health")
        async def webhook_health() -> dict[str, Any]:
            """Health check for the webhook endpoint."""
            return {
                "status": "ok",
                "adapter": self.name,
                "queue_initialized": self._queue is not None,
                "queue_size": self._queue.qsize() if self._queue else 0,
            }

        return router

    async def send(self, outbound: OutboundMessage) -> bool:
        """Send a message back to Telegram with 429 retry.

        Extracts chat_id from outbound.context_metadata (preferred)
        or falls back to parsing from outbound.target_id.

        Handles Telegram rate-limits (HTTP 429) with exponential backoff.

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if sent successfully.
        """
        if not self._http:
            self._http = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0))

        # Resolve chat_id
        chat_id = outbound.context_metadata.get("chat_id")
        if not chat_id:
            if ":" in outbound.target_id:
                try:
                    chat_id = int(outbound.target_id.split(":", 1)[1])
                except (ValueError, IndexError):
                    logger.error(
                        "[telegram] Cannot parse chat_id from target_id: %s",
                        outbound.target_id,
                    )
                    return False
            else:
                logger.error("[telegram] No chat_id available for send()")
                return False

        # Send with 429 retry (exponential backoff)
        payload = {
            "chat_id": chat_id,
            "text": outbound.text[:4096],
        }
        if self._parse_mode:
            payload["parse_mode"] = self._parse_mode

        for attempt in range(_SEND_MAX_RETRIES):
            try:
                resp = await self._http.post(
                    f"{self._api_base}/sendMessage",
                    json=payload,
                )

                if resp.status_code == 429:
                    # Rate-limited — extract retry_after or use backoff
                    try:
                        body = resp.json()
                        retry_after = body.get("parameters", {}).get(
                            "retry_after",
                            _SEND_BASE_DELAY * (2**attempt),
                        )
                    except Exception:
                        retry_after = _SEND_BASE_DELAY * (2**attempt)

                    jitter = random.uniform(0.5, 1.5)
                    wait = retry_after + jitter
                    logger.warning(
                        "[telegram] Rate-limited (429) on send to %d — retrying in %.1fs (attempt %d/%d)",
                        chat_id,
                        wait,
                        attempt + 1,
                        _SEND_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                result = resp.json()

                if result.get("ok"):
                    logger.debug("[telegram] Sent to %d: %.80s", chat_id, outbound.text)
                    return True
                else:
                    logger.error("[telegram] sendMessage not ok: %s", result)
                    return False

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[telegram] HTTP %d on send to %d: %s",
                    exc.response.status_code,
                    chat_id,
                    exc,
                )
                return False
            except Exception:
                logger.exception("[telegram] Failed to send to %d", chat_id)
                return False

        logger.error(
            "[telegram] Rate-limit exceeded after %d retries for chat=%d",
            _SEND_MAX_RETRIES,
            chat_id,
        )
        return False
