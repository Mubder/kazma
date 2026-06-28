"""Telegram Adapter — Manual getUpdates polling with jitter + webhook ingress.

Headless: pure polling against the Telegram Bot API.
No webhooks required, no aiogram Dispatcher, no public IP needed.
Full control over poll timing, jitter, and rate-limit handling.

An optional webhook ingress router is available via create_webhook_router()
for testing or deployments with a public URL. Both polling and webhook
feed into the same asyncio.Queue — the Brain sees no difference.

All incoming messages are normalized to IncomingMessage with context_metadata
carrying raw Telegram IDs so the Brain never imports Telegram-specific code.

Voice messages are transcribed via a configurable STT provider (openai, local,
groq) and injected into the agent pipeline as text. If STT is not configured,
the user receives a fallback message.

Configuration (kazma.yaml):
    connectors:
      telegram:
        token: "123456:ABC-DEF..."
        allowed_users: []       # optional whitelist of Telegram user IDs
        parse_mode: "Markdown"  # default parse_mode for replies
    gateway:
      voice:
        enabled: false          # enable voice transcription
        provider: openai        # openai | local | groq
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
    RateLimiter,
)

logger = logging.getLogger(__name__)

_TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Voice download size cap
MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB

# Rate-limit constants
_SEND_MAX_RETRIES = 3
_SEND_BASE_DELAY = 1.0  # base delay for exponential backoff on 429

# Emoji reaction map for setMessageReaction API
_EMOJI_MAP: dict[str, list[dict[str, str]]] = {
    "👀": [{"type": "emoji", "emoji": "👀"}],
    "✅": [{"type": "emoji", "emoji": "✅"}],
    "🎯": [{"type": "emoji", "emoji": "🎯"}],
    "❌": [{"type": "emoji", "emoji": "❌"}],
    "⏳": [{"type": "emoji", "emoji": "⏳"}],
    "": [],  # clear reaction
}


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
        voice_enabled:  Whether to transcribe voice messages (default False).
        voice_provider: STT provider name ("openai", "local", "groq").
        stt_api_key:    API key for the STT provider (default from env).

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
        voice_enabled: bool = False,
        voice_provider: str = "openai",
        stt_api_key: str | None = None,
    ) -> None:
        super().__init__()
        self._token = token
        self._api_base = _TELEGRAM_API.format(token=token)
        self._allowed_users = set(allowed_users or [])
        self._parse_mode = parse_mode or ""
        self._poll_timeout = poll_timeout
        self._offset: int = 0
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(max_per_second=30)
        # Queue ref stored for webhook ingress (set by start() in BaseAdapter)
        self._queue: asyncio.Queue[IncomingMessage] | None = None
        # Voice transcription config
        self._voice_enabled = voice_enabled
        self._voice_provider = voice_provider
        self._stt_api_key = stt_api_key

    def set_allowed_users(self, user_ids: list[int] | set[int]) -> None:
        """Set the whitelist of allowed Telegram user IDs (public setter).

        This replaces direct assignment to the private ``_allowed_users``
        attribute from UI or configuration code.
        """
        self._allowed_users = set(user_ids)

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
        self._http = httpx.AsyncClient(
            base_url=_TELEGRAM_API.format(token=self._token),
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

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

                    # Handle inline keyboard callbacks
                    if "callback_query" in update:
                        asyncio.create_task(
                            self._handle_callback_query(
                                update["callback_query"],
                            )
                        )
                        continue

                    # Handle voice messages (async download + transcribe)
                    msg = self._parse_update(update)
                    if msg is None:
                        message = (
                            update.get("message")
                            or update.get("channel_post")
                            or update.get("edited_message")
                        )
                        if message and self.detect_voice_message(message):
                            voice_result = await self._handle_voice_message(message)
                            if voice_result is None:
                                continue
                            from_user = message.get("from", {})
                            user_id = from_user.get("id", 0)
                            chat_id = message.get("chat", {}).get("id", 0)
                            username = (
                                from_user.get("username", "")
                                or from_user.get("first_name", "")
                                or f"tg_{user_id}"
                            )
                            sender_id = f"telegram:{user_id}" if user_id else f"telegram:{chat_id}"
                            msg = IncomingMessage(
                                platform="telegram",
                                sender_id=sender_id,
                                text=voice_result,
                                context_metadata={
                                    "chat_id": chat_id,
                                    "user_id": user_id,
                                    "username": username,
                                    "message_id": message.get("message_id", 0),
                                    "chat_type": message.get("chat", {}).get("type", "private"),
                                    "update_id": update.get("update_id", 0),
                                    "voice_transcribed": True,
                                },
                            )
                        else:
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
                        # Fire 👀 reaction on the user's message
                        if msg.context_metadata.get("message_id"):
                            asyncio.create_task(
                                self._set_reaction(
                                    msg.context_metadata["chat_id"],
                                    msg.context_metadata["message_id"],
                                    "👀",
                                )
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
            f"/bot{self._token}/getUpdates",
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

        # sender_id uses user_id for unique per-user sessions.
        # Fallback to chat_id for channel posts (no 'from' field).
        sender_id = f"telegram:{user_id}" if user_id else f"telegram:{chat_id}"

        return IncomingMessage(
            platform="telegram",
            sender_id=sender_id,
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

    # --- Voice message transcription ---------------------------------

    @staticmethod
    def detect_voice_message(message: dict[str, Any]) -> bool:
        """Check if a Telegram message contains a voice or audio file."""
        return "voice" in message or "audio" in message

    async def download_voice_file(self, file_id: str) -> bytes | None:
        """Download a voice/audio file from Telegram using getFile."""
        assert self._http is not None, "HTTP client not initialized -- adapter not started"
        try:
            resp = await self._http.get(
                f"/bot{self._token}/getFile",
                params={"file_id": file_id},
            )
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("[telegram] getFile returned ok=false: %s", data)
                return None
            file_path = data["result"].get("file_path")
            if not file_path:
                logger.error("[telegram] No file_path in getFile response")
                return None
            file_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            dl_resp = await self._http.get(file_url)
            dl_resp.raise_for_status()

            # Check Content-Length header first (gw-064)
            content_length = int(dl_resp.headers.get("content-length", 0))
            if content_length > MAX_VOICE_BYTES:
                logger.warning(
                    "[telegram] Voice file too large (Content-Length): %d bytes exceeds limit %d",
                    content_length,
                    MAX_VOICE_BYTES,
                )
                return None

            # Stream-based fallback: check actual downloaded bytes
            # (protects against servers that omit Content-Length)
            if len(dl_resp.content) > MAX_VOICE_BYTES:
                logger.warning(
                    "[telegram] Voice file too large (downloaded): %d bytes exceeds limit %d",
                    len(dl_resp.content),
                    MAX_VOICE_BYTES,
                )
                return None

            logger.info("[telegram] Downloaded voice file: %s (%d bytes)", file_path, len(dl_resp.content))
            return dl_resp.content
        except httpx.HTTPStatusError as exc:
            logger.error("[telegram] HTTP %d downloading voice file: %s", exc.response.status_code, exc)
            return None
        except Exception:
            logger.exception("[telegram] Failed to download voice file")
            return None

    async def transcribe_voice(self, audio_bytes: bytes) -> str | None:
        """Transcribe audio bytes using the configured STT provider."""
        if self._voice_provider == "openai":
            return await self._transcribe_openai(audio_bytes)
        elif self._voice_provider == "groq":
            return await self._transcribe_groq(audio_bytes)
        elif self._voice_provider == "local":
            logger.warning("[telegram] Local STT provider not yet implemented")
            return None
        else:
            logger.error("[telegram] Unknown STT provider: %s", self._voice_provider)
            return None

    async def _transcribe_openai(self, audio_bytes: bytes) -> str | None:
        """Transcribe via OpenAI Whisper API."""
        import os
        api_key = self._stt_api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.error("[telegram] No OpenAI API key for STT")
            return None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.openai.com/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                    data={"model": "whisper-1"},
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                if text:
                    logger.info("[telegram] OpenAI STT transcription: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[telegram] OpenAI STT transcription failed")
            return None

    async def _transcribe_groq(self, audio_bytes: bytes) -> str | None:
        """Transcribe via Groq Whisper API."""
        import os
        api_key = self._stt_api_key or os.environ.get("GROQ_API_KEY")
        if not api_key:
            logger.error("[telegram] No Groq API key for STT")
            return None
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/audio/transcriptions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": ("voice.ogg", audio_bytes, "audio/ogg")},
                    data={"model": "whisper-large-v3"},
                )
                resp.raise_for_status()
                result = resp.json()
                text = result.get("text", "").strip()
                if text:
                    logger.info("[telegram] Groq STT transcription: %.100s", text)
                return text or None
        except Exception:
            logger.exception("[telegram] Groq STT transcription failed")
            return None

    async def _handle_voice_message(self, message: dict[str, Any]) -> str | None:
        """Full voice pipeline: detect -> download -> transcribe -> return text."""
        voice_obj = message.get("voice") or message.get("audio")
        if not voice_obj:
            return None
        if not self._voice_enabled:
            return None
        file_id = voice_obj.get("file_id")
        if not file_id:
            logger.warning("[telegram] Voice message has no file_id")
            return None
        audio_bytes = await self.download_voice_file(file_id)
        if not audio_bytes:
            return None
        transcription = await self.transcribe_voice(audio_bytes)
        if not transcription:
            chat_id = message.get("chat", {}).get("id")
            if chat_id:
                try:
                    assert self._http is not None
                    await self._http.post(
                        f"/bot{self._token}/sendMessage",
                        json={
                            "chat_id": chat_id,
                            "text": "Voice received but transcription is unavailable. Please configure an STT provider (openai/groq) or type your message.",
                        },
                    )
                except Exception:
                    logger.debug("[telegram] Failed to send voice fallback message")
            return None
        return transcription

    # ── Typing indicator (fire-and-forget) ──────────────────────────

    async def _trigger_typing(self, target_id: str) -> None:
        """Send a 'typing…' chat action to the user (fire-and-forget)."""
        chat_id = target_id.split(":", 1)[1] if ":" in target_id else target_id
        try:
            url = f"{_TELEGRAM_API.format(token=self._token)}/sendChatAction"
            async with httpx.AsyncClient(timeout=5.0) as c:
                await c.post(url, json={"chat_id": chat_id, "action": "typing"})
        except Exception:
            pass  # fire-and-forget — never block

    # ── Emoji reactions ────────────────────────────────────────────

    async def _set_reaction(
        self,
        chat_id: int | str,
        message_id: int,
        emoji: str,
    ) -> None:
        """Set an emoji reaction on a Telegram message (fire-and-forget).

        Uses the setMessageReaction Bot API endpoint. Errors are logged
        at debug level but never propagate — reactions are cosmetic.

        Args:
            chat_id:    Telegram chat ID.
            message_id: Message ID to react to.
            emoji:      One of 👀, ✅, 🎯, ❌, ⏳, or "" to clear.
        """
        reaction = _EMOJI_MAP.get(emoji, [{"type": "emoji", "emoji": emoji}])
        try:
            url = f"{_TELEGRAM_API.format(token=self._token)}/setMessageReaction"
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.post(
                    url,
                    json={
                        "chat_id": chat_id,
                        "message_id": message_id,
                        "reaction": reaction,
                        "is_big": False,
                    },
                )
                if resp.status_code != 200:
                    logger.debug(
                        "[telegram] setMessageReaction %s failed (%d): %s",
                        emoji,
                        resp.status_code,
                        resp.text[:200],
                    )
        except Exception:
            logger.debug("[telegram] setMessageReaction error (fire-and-forget)")

    # ── Callback query handling ────────────────────────────────────

    async def _answer_callback_query(
        self,
        callback_query_id: str,
        text: str = "",
    ) -> None:
        """Answer a Telegram callback query (fire-and-forget).

        Dismisses the loading indicator on the client side.

        Args:
            callback_query_id: The callback query ID from the update.
            text:              Optional notification text to show.
        """
        try:
            url = f"{_TELEGRAM_API.format(token=self._token)}/answerCallbackQuery"
            async with httpx.AsyncClient(timeout=5.0) as c:
                payload: dict[str, Any] = {"callback_query_id": callback_query_id}
                if text:
                    payload["text"] = text
                await c.post(url, json=payload)
        except Exception:
            logger.debug("[telegram] answerCallbackQuery error (fire-and-forget)")

    async def _handle_callback_query(self, callback_query: dict[str, Any]) -> None:
        """Handle an inline keyboard callback query.

        Parses callback_data, answers the query, and enqueues a synthetic
        IncomingMessage so the agent can process the user's button press.

        Supported callback_data formats:
            - hitl:approve:<request_id>
            - hitl:deny:<request_id>
            - personality:<name>

        Args:
            callback_query: Raw Telegram CallbackQuery object.
        """
        cb_id = callback_query.get("id", "")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        from_user = callback_query.get("from", {})

        # Dismiss loading indicator
        asyncio.create_task(self._answer_callback_query(cb_id))

        # Parse callback_data into a synthetic command text
        text = ""
        if data.startswith("hitl:"):
            parts = data.split(":", 2)
            if len(parts) == 3:
                action, request_id = parts[1], parts[2]
                text = f"/hitl {action} {request_id}"
        elif data.startswith("personality:"):
            name = data.split(":", 1)[1]
            text = f"/personality {name}"

        if not text:
            logger.debug("[telegram] Unknown callback_data: %s", data)
            return

        # Build synthetic IncomingMessage
        chat_id = message.get("chat", {}).get("id", 0)
        user_id = from_user.get("id", 0)
        username = (
            from_user.get("username", "")
            or from_user.get("first_name", "")
            or f"tg_{user_id}"
        )

        msg = IncomingMessage(
            platform="telegram",
            sender_id=f"telegram:{user_id}" if user_id else f"telegram:{chat_id}",
            text=text,
            context_metadata={
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "message_id": message.get("message_id", 0),
                "chat_type": message.get("chat", {}).get("type", "private"),
                "callback_query_id": cb_id,
            },
        )

        if self._queue is not None:
            try:
                self._queue.put_nowait(msg)
                logger.info(
                    "[telegram] Callback enqueued from %s: %.80s",
                    username,
                    text,
                )
            except asyncio.QueueFull:
                logger.warning(
                    "[telegram] Queue full — dropping callback from chat=%d",
                    chat_id,
                )

    # ── Inline keyboard builders ──────────────────────────────────

    @staticmethod
    def build_approval_keyboard(request_id: str) -> dict[str, Any]:
        """Build an inline keyboard for HITL approval prompts.

        Args:
            request_id: Unique identifier for the approval request.

        Returns:
            Telegram InlineKeyboardMarkup dict.
        """
        return {
            "inline_keyboard": [
                [
                    {
                        "text": "✅ Approve",
                        "callback_data": f"hitl:approve:{request_id}",
                    },
                    {
                        "text": "❌ Deny",
                        "callback_data": f"hitl:deny:{request_id}",
                    },
                ]
            ]
        }

    @staticmethod
    def build_personality_keyboard(
        personalities: list[str],
    ) -> dict[str, Any]:
        """Build an inline keyboard for personality selection (top 3).

        Args:
            personalities: List of personality names.

        Returns:
            Telegram InlineKeyboardMarkup dict with up to 3 buttons.
        """
        return {
            "inline_keyboard": [
                [{"text": name, "callback_data": f"personality:{name}"}]
                for name in personalities[:3]
            ]
        }

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
        # Fire typing indicator before sending
        asyncio.create_task(self._trigger_typing(outbound.target_id))

        if not self._http:
            self._http = httpx.AsyncClient(
                base_url=_TELEGRAM_API.format(token=self._token),
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )

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
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": outbound.text[:4096],
        }
        if self._parse_mode:
            payload["parse_mode"] = self._parse_mode

        # Include inline keyboard if present
        reply_markup = outbound.context_metadata.get("reply_markup")
        if reply_markup:
            payload["reply_markup"] = reply_markup

        for attempt in range(_SEND_MAX_RETRIES):
            try:
                await self._rate_limiter.acquire()
                resp = await self._http.post(
                    f"/bot{self._token}/sendMessage",
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
                    # React with ✅ (or 🎯 if a tool was used)
                    original_msg_id = outbound.context_metadata.get("message_id")
                    if original_msg_id:
                        emoji = "🎯" if outbound.context_metadata.get("tool_used") else "✅"
                        asyncio.create_task(
                            self._set_reaction(chat_id, original_msg_id, emoji)
                        )
                    return True
                else:
                    logger.error("[telegram] sendMessage not ok: %s", result)
                    # React with ❌ on failure
                    original_msg_id = outbound.context_metadata.get("message_id")
                    if original_msg_id:
                        asyncio.create_task(
                            self._set_reaction(chat_id, original_msg_id, "❌")
                        )
                    return False

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[telegram] HTTP %d on send to %d: %s",
                    exc.response.status_code,
                    chat_id,
                    exc,
                )
                # React with ❌ on error
                original_msg_id = outbound.context_metadata.get("message_id")
                if original_msg_id:
                    asyncio.create_task(
                        self._set_reaction(chat_id, original_msg_id, "❌")
                    )
                return False
            except Exception:
                logger.exception("[telegram] Failed to send to %d", chat_id)
                # React with ❌ on error
                original_msg_id = outbound.context_metadata.get("message_id")
                if original_msg_id:
                    asyncio.create_task(
                        self._set_reaction(chat_id, original_msg_id, "❌")
                    )
                return False

        logger.error(
            "[telegram] Rate-limit exceeded after %d retries for chat=%d",
            _SEND_MAX_RETRIES,
            chat_id,
        )
        # React with ❌ on exhausted retries
        original_msg_id = outbound.context_metadata.get("message_id")
        if original_msg_id:
            asyncio.create_task(
                self._set_reaction(chat_id, original_msg_id, "❌")
            )
        return False
