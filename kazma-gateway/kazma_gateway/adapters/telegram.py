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
import time
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from kazma_gateway.gateway import (
    Attachment,
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
    RateLimiter,
)
from kazma_gateway.slash_commands import BOT_MENU_COMMANDS

logger = logging.getLogger(__name__)

__all__ = [
    "TelegramAdapter",
]

_TELEGRAM_API = "https://api.telegram.org/bot{token}"

# Voice download size cap
MAX_VOICE_BYTES = 10 * 1024 * 1024  # 10 MB

# Generic media (photo/document/video) download cap. Telegram Bot API limits
# downloads to 20 MB, so we cap just below that.
MAX_MEDIA_BYTES = 19 * 1024 * 1024  # 19 MB

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
        tts_provider: str = "edgetts",
        tts_voice: str = "default",
        tts_output_format: str = "mp3",
        stt_language: str = "auto",
        webhook_secret: str | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__()
        self._token = token
        self._api_base = _TELEGRAM_API.format(token=token)
        self._allowed_users = set(allowed_users or [])
        self._allow_all = allow_all
        self._parse_mode = parse_mode or ""
        self._poll_timeout = poll_timeout
        self._offset: int = 0
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(max_per_second=30)
        # Queue ref stored for webhook ingress (set by start() in BaseAdapter)
        self._queue: asyncio.Queue[IncomingMessage] | None = None
        # Voice config
        self._voice_enabled = voice_enabled
        self._voice_provider = voice_provider
        self._stt_api_key = stt_api_key
        self._tts_provider = tts_provider
        self._tts_voice = tts_voice
        self._tts_output_format = tts_output_format
        self._stt_language = stt_language
        # Webhook secret token for validating webhook ingress (optional)
        self._webhook_secret = webhook_secret or ""
        # Strong refs for fire-and-forget tasks (see _spawn).
        self._bg_tasks: set[asyncio.Task] = set()
        # Per-chat update-processing chains (deep-audit 2026-08-19, #15c):
        # voice STT + media downloads run OFF the poll loop, serialized per
        # chat so same-chat message order is preserved (the handler's
        # per-thread lock processes turns in enqueue order). Maps chat_id →
        # the chat's tail task; strong refs by construction.
        self._chat_chains: dict[int, asyncio.Task] = {}
        # (chat_id, message_id) → monotonic. Telegram often delivers the
        # original message and then an edited_message with the same id —
        # that used to start a second graph turn and a second HITL card.
        self._seen_message_ids: dict[tuple[int, int], float] = {}
        # Offset-commit tracking (at-least-once delivery): ``self._offset``
        # is the COMMITTED mark — it only advances past an update once that
        # update's chain task finishes, so a crash between dispatch and
        # completion makes Telegram redeliver (it never redelivers past a
        # confirmed offset). Same-process redelivery while the offset lags
        # behind an in-flight chain is deduped twice: by
        # ``_unacked_updates`` at dispatch and by ``_seen_message_ids`` at
        # enqueue. Cross-restart redelivery is the accepted at-least-once
        # duplicate (the dedup sets are memory-only); Telegram's 24h
        # server-side update retention bounds the window.
        self._pending_updates: set[int] = set()   # dispatched, chain not finished
        self._unacked_updates: set[int] = set()   # dispatched, not yet committed
        self._max_seen_update_id: int = 0

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
        if not self._allowed_users and not self._allow_all:
            logger.error(
                "[telegram] No allowed_users configured and allow_all is false. "
                "The bot will REJECT ALL messages. Set connectors.telegram.allowed_users "
                "in Settings, or set allow_all=true to accept from everyone."
            )
        elif not self._allowed_users and self._allow_all:
            logger.warning(
                "[telegram] allow_all is true — accepting messages from ALL users."
            )
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
            base_url=self._api_base,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )

        try:
            # ── Delete any pending webhook before polling ──────────────────
            # If a webhook was previously set (e.g. by the archived
            # telegram_bridge.py), getUpdates returns HTTP 409 Conflict for
            # every call and no messages are ever received.
            try:
                dw_resp = await self._http.post(
                    "/deleteWebhook",
                    json={"drop_pending_updates": False},
                )
                logger.info(
                    "[telegram] deleteWebhook called (status=%d)",
                    dw_resp.status_code,
                )
            except Exception as exc:
                logger.warning("[telegram] deleteWebhook failed — continuing: %s", exc)

            # ── Validate bot token via getMe ──────────────────────────────
            # If the token is wrong/expired/revoked, every getUpdates returns
            # 401. Validating up front prevents a silent "connected but dead"
            # state where the adapter reports healthy but never receives.
            try:
                me_resp = await self._http.get("/getMe")
                if me_resp.status_code != 200:
                    me_data = me_resp.json()
                    logger.error(
                        "[telegram] Bot token validation failed: %s",
                        me_data.get("description", "unknown error"),
                    )
                    self._running = False
                    return
                bot_info = me_resp.json().get("result", {})
                logger.info(
                    "[telegram] Connected as @%s (%s)",
                    bot_info.get("username", "unknown"),
                    bot_info.get("first_name", ""),
                )
            except Exception:
                logger.exception("[telegram] getMe failed — cannot validate bot token")
                self._running = False
                return

            # ── Register bot commands with Telegram (menu button) ──────
            await self._register_bot_commands()

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
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code == 409:
                        # 409 Conflict — another process is polling this bot.
                        logger.error(
                            "[telegram] 409 Conflict — another process is polling this bot. "
                            "Stopping adapter. Stop the other process or use a different bot token.",
                        )
                        self._running = False
                        break
                    # Log status + response text without the full exception
                    # string (which contains the URL with the bot token)
                    try:
                        err_body = exc.response.text[:300]
                    except Exception:
                        err_body = "<unreadable>"
                    logger.error("[telegram] HTTP %d polling error: %s", exc.response.status_code, err_body)

                    # 429: respect Telegram's retry_after parameter instead
                    # of the default 1-3s jitter (which causes double-429s).
                    if exc.response.status_code == 429:
                        retry_after = 5  # safe default
                        try:
                            body = exc.response.json()
                            retry_after = int(
                                body.get("parameters", {}).get("retry_after", 5)
                            )
                        except Exception:
                            pass
                        logger.info("[telegram] 429 rate limit — backing off %ds", retry_after)
                        try:
                            await asyncio.wait_for(
                                shutdown_event.wait(), timeout=float(retry_after)
                            )
                            break  # shutdown signaled during backoff
                        except asyncio.TimeoutError:
                            pass  # backoff elapsed, resume polling
                        continue

                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue
                except Exception as poll_exc:
                    logger.error("[telegram] Poll error: %s: %s", type(poll_exc).__name__, str(poll_exc)[:200])
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue

                # ── Process updates ─────────────────────────────────
                for update in updates:
                    if shutdown_event.is_set():
                        break

                    try:
                        try:
                            update_id = int(update.get("update_id", 0) or 0)
                        except (TypeError, ValueError):
                            update_id = 0
                        # While the committed offset lags behind an
                        # in-flight chain, Telegram redelivers every
                        # unconfirmed update on each poll — skip ids we
                        # already dispatched but have not committed yet.
                        if update_id and update_id in self._unacked_updates:
                            continue

                        # Handle inline keyboard callbacks
                        if "callback_query" in update:
                            cb_task = self._spawn(
                                self._handle_callback_query(
                                    update["callback_query"],
                                )
                            )
                            self._track_update(update_id, cb_task)
                            continue

                        # Offload per-update processing off the poll loop
                        # (deep-audit 2026-08-19, finding #15c): voice STT
                        # and media downloads can take seconds — processed
                        # inline they stalled every subsequent Telegram
                        # update. Serialized PER CHAT so same-chat message
                        # order is preserved; different chats proceed
                        # concurrently.
                        self._dispatch_update_to_chain(update, queue)
                    except Exception:
                        logger.exception(
                            "[telegram] Error dispatching update %s",
                            update.get("update_id", "?"),
                        )
                        continue

                # ── Jitter (mandatory 1-3s delay) ───────────────────
                if await self.jitter_sleep(shutdown_event):
                    break

        finally:
            if self._http:
                await self._http.aclose()
                self._http = None
            self._running = False
            logger.info("[telegram] Polling stopped")

    def _dispatch_update_to_chain(
        self, update: dict, queue: asyncio.Queue
    ) -> None:
        """Spawn update processing on its chat's serialization chain."""
        message = (
            update.get("message")
            or update.get("channel_post")
            or update.get("edited_message")
        )
        chat_id = 0
        try:
            if message:
                chat_id = int(message.get("chat", {}).get("id", 0) or 0)
        except Exception:
            chat_id = 0
        try:
            update_id = int(update.get("update_id", 0) or 0)
        except (TypeError, ValueError):
            update_id = 0
        if update_id and update_id in self._unacked_updates:
            # Redelivery of an uncommitted update (Telegram resends
            # everything ≥ committed+1 while an earlier chain is still
            # running) — already dispatched, skip.
            return
        if not chat_id:
            # No chat identity (rare/unknown update shapes) — process
            # unchained but still off the poll loop.
            self._track_update(
                update_id, self._spawn(self._process_update(update, queue))
            )
            return
        prev = self._chat_chains.get(chat_id)
        task = asyncio.create_task(
            self._process_update_chained(prev, chat_id, update, queue),
            name=f"tg-update:{chat_id}:{update.get('update_id', '?')}",
        )
        self._chat_chains[chat_id] = task
        self._track_update(update_id, task)

    def _track_update(self, update_id: int, task: asyncio.Task) -> None:
        """Register a dispatched update so its offset commits on completion.

        Both chained and unchained dispatch paths funnel through here —
        the committed offset never passes an update whose task has not
        finished.
        """
        if not update_id:
            return
        self._unacked_updates.add(update_id)
        self._pending_updates.add(update_id)
        task.add_done_callback(
            lambda t, uid=update_id: self._on_update_done(uid, t)
        )

    def _on_update_done(self, update_id: int, task: asyncio.Task) -> None:
        """Commit the getUpdates offset past finished update tasks.

        Advances ``self._offset`` to the highest update_id with no
        unfinished predecessor (contiguous completed prefix). A task that
        RAISED still commits — redelivering an already-failing handler
        would poison-loop — but is logged at warning. A still-running
        earlier update holds the mark so a crash redelivers everything
        uncommitted (at-least-once).
        """
        if not task.cancelled():
            exc = task.exception()
            if exc is not None:
                logger.warning(
                    "[telegram] Update %d processing failed (%s: %s) — "
                    "committing offset to avoid redelivery loop",
                    update_id,
                    type(exc).__name__,
                    str(exc)[:200],
                )
        self._pending_updates.discard(update_id)
        nxt = self._offset + 1
        while (
            self._offset < self._max_seen_update_id
            and nxt not in self._pending_updates
        ):
            self._offset = nxt
            self._unacked_updates.discard(nxt)
            nxt += 1

    async def _process_update_chained(
        self,
        prev: asyncio.Task | None,
        chat_id: int,
        update: dict,
        queue: asyncio.Queue,
    ) -> None:
        try:
            if prev is not None:
                try:
                    await prev
                except Exception:
                    pass  # the previous update logged its own error
            await self._process_update(update, queue)
        finally:
            if self._chat_chains.get(chat_id) is asyncio.current_task():
                self._chat_chains.pop(chat_id, None)

    async def _process_update(self, update: dict, queue: asyncio.Queue) -> None:
        """Parse, gate, and enqueue a single Telegram update.

        Extracted from the poll loop (deep-audit 2026-08-19, #15c) so it can
        run on a per-chat chain task instead of blocking getUpdates.
        """
        try:
            # Handle voice messages (async download + transcribe)
            # and generic media (photo/document/video/animation).
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
                        return
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
                elif message and self._detect_media_message(message):
                    # Photo / document / video / animation:
                    # download and attach instead of dropping.
                    msg = await self._handle_media_message(message)
                    if msg is None:
                        return
                else:
                    return

            # User whitelist (fail-closed: empty list + allow_all=false = reject all)
            if not self._allowed_users and not self._allow_all:
                logger.warning("[telegram] Rejecting message — no allowed_users and allow_all is false")
                return
            if self._allowed_users:
                user_id = msg.context_metadata.get("user_id", 0)
                if user_id not in self._allowed_users:
                    logger.debug(
                        "[telegram] Ignoring user %d (not whitelisted)",
                        user_id,
                    )
                    return

            if self._already_seen_message(msg):
                logger.info(
                    "[telegram] Skipping duplicate/edit of message_id=%s chat=%s",
                    msg.context_metadata.get("message_id"),
                    msg.context_metadata.get("chat_id"),
                )
                return

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
                    self._spawn(
                        self._set_reaction(
                            msg.context_metadata["chat_id"],
                            msg.context_metadata["message_id"],
                            "👀",
                        )
                    )
            except asyncio.QueueFull:
                chat_id = int(msg.context_metadata.get("chat_id", 0) or 0)
                logger.warning(
                    "[telegram] Queue full — dropping message from chat=%d",
                    chat_id,
                )
                # The user saw their message "delivered" — tell them it was
                # dropped instead of leaving them staring at silence. Direct
                # send (deliberately bypasses the full queue); failure to
                # notify escalates to ERROR so the drop is never silent.
                if chat_id:
                    try:
                        http = self._ensure_http()
                        await http.post(
                            "/sendMessage",
                            json={
                                "chat_id": chat_id,
                                "text": "⚠️ Busy — message dropped, please resend.",
                            },
                        )
                    except Exception:
                        logger.error(
                            "[telegram] Queue full — dropped message from "
                            "chat=%d and busy notice failed to send",
                            chat_id,
                        )
        except Exception:
            logger.exception(
                "[telegram] Error processing update %s",
                update.get("update_id", "?"),
            )

    def _already_seen_message(self, msg: IncomingMessage) -> bool:
        """True if this (chat_id, message_id) was already enqueued recently."""
        meta = msg.context_metadata or {}
        try:
            chat_id = int(meta.get("chat_id") or 0)
            message_id = int(meta.get("message_id") or 0)
        except (TypeError, ValueError):
            return False
        if not chat_id or not message_id:
            return False
        now = time.monotonic()
        cutoff = now - 900.0
        if len(self._seen_message_ids) > 400:
            self._seen_message_ids = {
                k: v for k, v in self._seen_message_ids.items() if v > cutoff
            }
        key = (chat_id, message_id)
        if key in self._seen_message_ids:
            return True
        self._seen_message_ids[key] = now
        return False

    def _spawn(self, coro: Any) -> asyncio.Task:
        """Fire-and-forget with a strong reference.

        The event loop keeps only weak refs to tasks; an unheld task can be
        garbage-collected mid-execution (intermittent missing reactions /
        unanswered callback queries). Held here until done.
        """
        task = asyncio.create_task(coro)
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    def _ensure_http(self) -> httpx.AsyncClient:
        """Return the shared API client, recreating it when missing or closed.

        Post-shutdown callbacks (fire-and-forget sends racing stop()) used
        bare ``if not self._http`` re-creation: a client that was closed but
        not None'd was reused broken, and each re-creation replaced the prior
        one without closing it. Centralizing here makes the lifecycle
        is_closed-aware and single-owner (audit finding).
        """
        if self._http is not None and not getattr(self._http, "is_closed", False):
            return self._http
        self._http = None  # drop a stale/closed client
        self._http = httpx.AsyncClient(
            base_url=self._api_base,
            timeout=httpx.Timeout(30.0, connect=5.0),
            # Preserve the connection cap from the original constructor (10/5)
            # rather than httpx's default (100/20) — Telegram polling keeps a
            # single long-lived connection; a tighter pool catches leaks
            # (audit finding: _ensure_http had silently widened the pool).
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        return self._http

    async def _poll(self) -> list[dict[str, Any]]:
        """Execute a single getUpdates call with long-poll.

        Uses Telegram's built-in long-polling (timeout parameter)
        to reduce idle requests. The offset CONFIRMS only the committed
        prefix (committed + 1): updates whose chain tasks have not
        finished are redelivered by Telegram on later polls — deduped at
        dispatch via ``_unacked_updates`` — so a crash between dispatch
        and chain completion redelivers them instead of losing them.

        Returns:
            List of Telegram Update objects.
        """
        if self._http is None:
            raise RuntimeError("HTTP client not initialized")

        params: dict[str, Any] = {"timeout": self._poll_timeout}
        if self._offset:
            params["offset"] = self._offset + 1

        resp = await self._http.get(
            "/getUpdates",
            params=params,
        )
        resp.raise_for_status()
        data = resp.json()

        if not data.get("ok"):
            logger.error("[telegram] getUpdates returned ok=false: %s", data)
            return []

        updates = data.get("result", [])
        for update in updates:
            try:
                uid = int(update.get("update_id", 0) or 0)
            except (TypeError, ValueError):
                uid = 0
            if uid > self._max_seen_update_id:
                self._max_seen_update_id = uid
        return updates

    def _parse_update(self, update: dict[str, Any]) -> IncomingMessage | None:
        """Parse a Telegram Update into an IncomingMessage (text/caption)."""
        from kazma_gateway.adapters.telegram_parse import parse_text_update

        return parse_text_update(update)

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

            When a webhook_secret is configured, validates the
            X-Telegram-Bot-Api-Secret-Token header to prevent
            unauthorized webhook posts.
            """
            # Always require a webhook secret when this route is mounted (audit H2).
            # Polling mode never hits this path.
            import hmac
            import secrets as _secrets

            if not self._webhook_secret:
                # No secret configured → Telegram never sends the
                # X-Telegram-Bot-Api-Secret-Token header, so the webhook mount
                # is INERT (every request would 401). Fail closed explicitly
                # and say so: previously an ephemeral secret was generated
                # that Telegram could never know, which made the mount dead
                # while the warning implied it was gated (audit finding).
                # To activate: set TELEGRAM_WEBHOOK_SECRET (or connector
                # webhook_secret) AND pass the same value to setWebhook.
                logger.warning(
                    "[telegram-webhook] No webhook_secret configured — webhook "
                    "mount is inactive (all requests rejected). Set "
                    "TELEGRAM_WEBHOOK_SECRET and pass it to setWebhook."
                )
                return JSONResponse(
                    {"error": "Webhook secret not configured"}, status_code=503
                )
            provided = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
            if not provided or not hmac.compare_digest(provided, self._webhook_secret):
                logger.warning("[telegram-webhook] Invalid or missing secret token")
                return JSONResponse({"error": "Unauthorized"}, status_code=401)

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
        from kazma_gateway.adapters.telegram_stt import detect_voice_message as _detect

        return _detect(message)

    async def download_voice_file(self, file_id: str) -> bytes | None:
        """Download a voice/audio file from Telegram using getFile."""
        if self._http is None:
            raise RuntimeError("HTTP client not initialized -- adapter not started")
        try:
            resp = await self._http.get(
                "/getFile",
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
            # Log status + body only — exc string contains the URL with bot token
            try:
                err_body = exc.response.text[:300]
            except Exception:
                err_body = "<unreadable>"
            logger.error("[telegram] HTTP %d downloading voice file: %s", exc.response.status_code, err_body)
            return None
        except Exception as exc:
            logger.error("[telegram] Failed to download voice file: %s", type(exc).__name__)
            return None

    # --- Generic media (photo/document/video) -------------------------

    # Telegram media key → coarse kind + the size-ordered array to pick from.
    # `photo` is a size-ordered array (smallest→largest); pick the largest.
    # `document`/`video`/`animation`/`audio` are single objects with file_id.
    _MEDIA_KEYS: dict[str, str] = {
        "photo": "image",
        "document": "file",
        "video": "video",
        "animation": "video",
        "audio": "audio",
    }

    def _detect_media_message(self, message: dict[str, Any]) -> bool:
        """Return True if *message* carries a downloadable media payload."""
        return any(key in message for key in self._MEDIA_KEYS)

    async def _download_media_file(self, file_id: str) -> bytes | None:
        """Download any Telegram file by ``file_id`` (reuses the getFile flow).

        Capped at :data:`MAX_MEDIA_BYTES`. Returns raw bytes or None on error.
        """
        if self._http is None:
            raise RuntimeError("HTTP client not initialized -- adapter not started")
        try:
            resp = await self._http.get("/getFile", params={"file_id": file_id})
            resp.raise_for_status()
            data = resp.json()
            if not data.get("ok"):
                logger.error("[telegram] getFile ok=false for media: %s", data)
                return None
            file_path = data["result"].get("file_path")
            if not file_path:
                return None
            file_url = f"https://api.telegram.org/file/bot{self._token}/{file_path}"
            dl_resp = await self._http.get(file_url)
            dl_resp.raise_for_status()
            if len(dl_resp.content) > MAX_MEDIA_BYTES:
                logger.warning(
                    "[telegram] media file too large: %d bytes", len(dl_resp.content)
                )
                return None
            logger.info(
                "[telegram] Downloaded media file: %s (%d bytes)",
                file_path, len(dl_resp.content),
            )
            return dl_resp.content
        except Exception as exc:
            logger.error("[telegram] media download failed: %s", type(exc).__name__)
            return None

    async def _handle_media_message(
        self, message: dict[str, Any]
    ) -> IncomingMessage | None:
        """Download a media payload and build an :class:`IncomingMessage`.

        Captures photo / document / video / animation / audio. Caption (if
        any) becomes the message text; otherwise a minimal ``[image]`` /
        ``[file]`` placeholder keeps downstream ``if msg.text`` guards from
        skipping the turn. Images become vision attachments; everything
        else is attached as a file (the builder persists + references it).
        """
        # Pick the first present media key by priority order.
        media_kind: str | None = None
        for key in ("photo", "document", "video", "animation", "audio"):
            if key in message:
                media_kind = self._MEDIA_KEYS[key]
                payload = message[key]
                break
        else:
            return None

        # Resolve a file_id + filename + mime.
        file_id = ""
        filename = ""
        mime = ""
        if media_kind == "image":
            # photo is an array of PhotoSize; largest is last.
            sizes = payload  # type: ignore[assignment]
            if not isinstance(sizes, list) or not sizes:
                return None
            largest = sizes[-1]
            file_id = largest.get("file_id", "")
            mime = "image/jpeg"  # Telegram photos are JPEG
            filename = f"photo_{largest.get('file_unique_id', 'tg')}.jpg"
        else:
            file_id = payload.get("file_id", "")
            mime = payload.get("mime_type", "") or "application/octet-stream"
            filename = payload.get("file_name", "") or f"{media_kind}_{file_id[:8]}"

        if not file_id:
            return None

        data = await self._download_media_file(file_id)
        if data is None:
            return None

        caption = message.get("caption") or ""
        from_user = message.get("from", {})
        user_id = from_user.get("id", 0)
        chat_id = message.get("chat", {}).get("id", 0)
        username = (
            from_user.get("username", "")
            or from_user.get("first_name", "")
            or f"tg_{user_id}"
        )
        sender_id = f"telegram:{user_id}" if user_id else f"telegram:{chat_id}"
        text = caption or f"[{media_kind}]"
        attachment = Attachment(
            kind=media_kind,
            mime=mime,
            filename=filename,
            data=data,
            meta={"file_id": file_id, "source": "telegram"},
        )
        logger.info(
            "[telegram] media attached: %s (%s, %d bytes) thread=%s",
            filename, mime, len(data), chat_id,
        )
        return IncomingMessage(
            platform="telegram",
            sender_id=sender_id,
            text=text,
            attachments=[attachment],
            context_metadata={
                "chat_id": chat_id,
                "user_id": user_id,
                "username": username,
                "message_id": message.get("message_id", 0),
                "chat_type": message.get("chat", {}).get("type", "private"),
                "update_id": message.get("update_id", 0) or 0,
                "media": True,
            },
        )

    def _live_voice_settings(self) -> dict[str, str | bool]:
        """Read voice settings from ConfigStore (Settings UI) with ctor fallbacks.

        Voice is a separate subsystem from LLM providers. Settings saved in the
        Web UI land in ConfigStore; boot-time yaml/ctor values are defaults only.
        """
        out: dict[str, str | bool] = {
            "enabled": bool(self._voice_enabled),
            "tts_reply": True,
            "stt_provider": self._voice_provider,
            "stt_language": self._stt_language,
            "tts_provider": self._tts_provider,
            "tts_voice": self._tts_voice,
            "tts_output_format": self._tts_output_format,
        }
        try:
            from kazma_core.config_store import get_config_store

            def _as_bool(val: object, default: bool = False) -> bool:
                if val is None:
                    return default
                if isinstance(val, bool):
                    return val
                return str(val).strip().lower() in ("1", "true", "yes", "on")

            cs = get_config_store()
            enabled = cs.get("voice.enabled")
            if enabled is not None:
                out["enabled"] = _as_bool(enabled, default=bool(out["enabled"]))
            tts_reply = cs.get("voice.tts_reply")
            if tts_reply is not None:
                out["tts_reply"] = _as_bool(tts_reply, default=True)
            for key, attr in (
                ("voice.stt_provider", "stt_provider"),
                ("voice.stt_language", "stt_language"),
                ("voice.tts_provider", "tts_provider"),
                ("voice.tts_voice", "tts_voice"),
                ("voice.tts_output_format", "tts_output_format"),
            ):
                val = cs.get(key)
                if val is not None and str(val).strip() and str(val).strip().lower() != "none":
                    out[attr] = str(val).strip()
        except Exception:
            logger.debug("[telegram] live voice settings unavailable", exc_info=True)
        return out

    async def transcribe_voice(self, audio_bytes: bytes) -> str | None:
        """Transcribe audio bytes using the configured STT provider."""
        from kazma_core.voice.stt import transcribe

        cfg = self._live_voice_settings()
        return await transcribe(
            audio_bytes,
            provider=str(cfg["stt_provider"]),
            language=str(cfg["stt_language"]),
            api_key=self._stt_api_key,
        )

    async def synthesize_reply(self, text: str) -> bytes | None:
        """Synthesize text to audio using the configured TTS provider."""
        from kazma_core.voice.tts import synthesize

        cfg = self._live_voice_settings()
        return await synthesize(
            text,
            provider=str(cfg["tts_provider"]),
            voice=str(cfg["tts_voice"]),
            output_format=str(cfg["tts_output_format"]),
        )

    async def send_voice_reply(self, chat_id: int, audio_bytes: bytes, reply_to: int | None = None) -> bool:
        """Send an audio voice message to a Telegram chat."""
        if not self._http:
            return False
        try:
            files = {"voice": ("reply.ogg", audio_bytes, "audio/ogg")}
            data: dict[str, Any] = {"chat_id": chat_id}
            if reply_to:
                data["reply_to_message_id"] = reply_to
            resp = await self._http.post(
                "/sendVoice",
                files=files,
                data=data,
            )
            if resp.status_code == 200:
                logger.info("[telegram] Voice reply sent to %d (%d bytes)", chat_id, len(audio_bytes))
                return True
            logger.warning("[telegram] sendVoice failed: %d", resp.status_code)
            return False
        except Exception:
            logger.exception("[telegram] sendVoice failed")
            return False

    async def _send_tts_reply(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        """Synthesize and send a voice reply after a text response.

        Strips markdown/HTML from the text before synthesis so the TTS
        engine gets clean spoken text. Gated by ``voice.enabled`` and
        ``voice.tts_reply`` (Settings → Voice).
        """
        cfg = self._live_voice_settings()
        if not text or not cfg.get("enabled") or not cfg.get("tts_reply", True):
            return
        try:
            # Strip markdown/HTML for clean TTS input
            import re
            clean = re.sub(r"`[^`]*`", "", text)  # inline code
            clean = re.sub(r"```.*?```", "", clean, flags=re.DOTALL)  # code blocks
            clean = re.sub(r"[*_~]+", "", clean)  # bold/italic/strike
            clean = re.sub(r"<[^>]+>", "", clean)  # HTML tags
            clean = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", clean)  # links → text
            clean = clean.strip()
            if not clean or len(clean) < 5:
                return
            # Truncate very long responses for TTS
            if len(clean) > 2000:
                clean = clean[:2000] + "... (truncated)"
            audio = await self.synthesize_reply(clean)
            if audio:
                await self.send_voice_reply(chat_id, audio, reply_to=reply_to)
        except Exception:
            logger.debug("[telegram] TTS reply failed (non-critical)")

    async def _handle_voice_message(self, message: dict[str, Any]) -> str | None:
        """Full voice pipeline: detect -> download -> transcribe -> return text."""
        voice_obj = message.get("voice") or message.get("audio")
        if not voice_obj:
            return None
        if not self._live_voice_settings().get("enabled"):
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
                    if self._http is not None:
                        await self._http.post(
                            "/sendMessage",
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
            self._http = self._ensure_http()
            await self._http.post("/sendChatAction", json={"chat_id": chat_id, "action": "typing"})
        except Exception as exc:
            logger.debug("[telegram] typing indicator failed (fire-and-forget): %s", exc)

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
            self._http = self._ensure_http()
            resp = await self._http.post(
                "/setMessageReaction",
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
            self._http = self._ensure_http()
            payload: dict[str, Any] = {"callback_query_id": callback_query_id}
            if text:
                payload["text"] = text
            await self._http.post("/answerCallbackQuery", json=payload)
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

        # Enforce user whitelist on callback queries too
        if self._allowed_users:
            user_id = from_user.get("id", 0)
            if user_id not in self._allowed_users:
                logger.warning("[telegram] Callback from non-whitelisted user %d", user_id)
                self._spawn(self._answer_callback_query(cb_id, "Not authorized"))
                return

        from kazma_gateway.adapters.telegram_callbacks import parse_callback_data

        action = parse_callback_data(data)
        text = action.text

        # Dismiss loading indicator with status text
        alert_text = None
        if action.kind == "hitl":
            if data.startswith("hitl:opt:"):
                # Semantic clarify/confirm option buttons — their data never
                # contains "approve", so the old substring check showed
                # "❌ Denied." for every option the user actually selected.
                alert_text = "🔘 Option selected — processing..."
            else:
                approved = "approve" in data
                alert_text = "✅ Approved — processing..." if approved else "❌ Denied."
        self._spawn(self._answer_callback_query(cb_id, alert_text))

        # Immediately deactivate the inline keyboard to prevent double-click or stale interaction
        if action.kind == "hitl":
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if chat_id and message_id:
                try:
                    self._http = self._ensure_http()
                    await self._http.post(
                        "/editMessageReplyMarkup",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "reply_markup": {"inline_keyboard": []},
                        },
                    )
                except Exception as exc:
                    logger.warning("[telegram] Failed to remove HITL keyboard: %s", exc)

        if action.kind == "swarm":
            # Swarm HITL approval — resolve bus Event in-process.
            # Fan-out safe: when 2+ platforms are configured the bus is a
            # FanOutBusAdapter, so unwrap it and dispatch to the Telegram
            # child (same pattern as discord_callbacks.route_swarm_bus —
            # previously the isinstance check failed under FanOut and the
            # approval button silently no-opped).
            task_id = None
            try:
                from kazma_core.swarm.bus import FanOutBusAdapter, get_message_bus

                from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

                adapter = get_message_bus().adapter
                if isinstance(adapter, FanOutBusAdapter):
                    for child in adapter.adapters:
                        if isinstance(child, TelegramBusAdapter):
                            task_id = child.handle_callback(action.swarm_data or data)
                            break
                elif isinstance(adapter, TelegramBusAdapter):
                    task_id = adapter.handle_callback(action.swarm_data or data)
            except Exception as exc:
                logger.warning("[telegram] Swarm approval callback failed: %s", exc)
            if task_id is not None:
                logger.info("[telegram] Swarm approval resolved: %s", task_id)
            return

        if action.kind == "sys_install":
            if self._allowed_users:
                user_id = from_user.get("id", 0)
                if user_id not in self._allowed_users:
                    logger.warning(
                        "[telegram] Non-whitelisted user %d tried to trigger installation.",
                        user_id,
                    )
                    self._spawn(
                        self._answer_callback_query(
                            cb_id, "Not authorized: Admin privilege required."
                        )
                    )
                    return
            from kazma_core.system.runtime_manager import trigger_package_promotion

            self._spawn(trigger_package_promotion(action.package_name))
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if chat_id and message_id:
                try:
                    self._http = self._ensure_http()
                    await self._http.post(
                        "/editMessageText",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "text": "[⏳ Installing package... please wait]",
                            "reply_markup": {"inline_keyboard": []},
                        },
                    )
                except Exception as exc:
                    logger.warning("[telegram] Failed to edit alert card: %s", exc)
            return

        if action.kind == "install_dep":
            package_name = action.package_name
            from kazma_core.system import asynchronous_install_package
            self._spawn(asynchronous_install_package(package_name))
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            if chat_id and message_id:
                try:
                    self._http = self._ensure_http()
                    await self._http.post(
                        "/editMessageText",
                        json={
                            "chat_id": chat_id,
                            "message_id": message_id,
                            "text": "⏳ Installing ML dependencies in the background...",
                            "reply_markup": {"inline_keyboard": []}
                        }
                    )
                except Exception as exc:
                    logger.warning("[telegram] Failed to edit alert card: %s", exc)
            return

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

    # ── Inline keyboard builders (delegates to telegram_keyboards) ──

    @staticmethod
    def build_approval_keyboard(request_id: str) -> dict[str, Any]:
        """Build an inline keyboard for HITL approval prompts."""
        from kazma_gateway.adapters.telegram_keyboards import (
            build_approval_keyboard as _build,
        )

        return _build(request_id)

    @staticmethod
    def build_personality_keyboard(personalities: list[str]) -> dict[str, Any]:
        """Build an inline keyboard for personality selection (top 3)."""
        from kazma_gateway.adapters.telegram_keyboards import (
            build_personality_keyboard as _build,
        )

        return _build(personalities)

    @staticmethod
    def build_provider_keyboard(providers: list[dict[str, Any]]) -> dict[str, Any]:
        """Build inline keyboard for model provider selection."""
        from kazma_gateway.adapters.telegram_keyboards import (
            build_provider_keyboard as _build,
        )

        return _build(providers)

    @staticmethod
    def build_model_keyboard(provider_name: str, models: list[str]) -> dict[str, Any]:
        """Build inline keyboard for model selection within a provider."""
        from kazma_gateway.adapters.telegram_keyboards import (
            build_model_keyboard as _build,
        )

        return _build(provider_name, models)

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
        # Typing is kept alive by agent_handler during the whole turn.
        # One final pulse before send keeps the indicator visible for the reply.

        self._http = self._ensure_http()

        from kazma_gateway.adapters.telegram_send import (
            chunk_message,
            resolve_chat_id,
            send_chunks_with_retry,
        )

        chat_id = resolve_chat_id(outbound.context_metadata, outbound.target_id)
        if chat_id is None:
            return False

        # Per-message override (e.g. swarm Output Routing HTML quotes)
        parse_mode = outbound.context_metadata.get("parse_mode", self._parse_mode)
        chunks = chunk_message(outbound.text, parse_mode=parse_mode)
        reply_markup = outbound.context_metadata.get("reply_markup")
        all_sent = await send_chunks_with_retry(
            http=self._http,
            chat_id=chat_id,
            chunks=chunks,
            parse_mode=parse_mode,
            reply_markup=reply_markup,
            rate_acquire=self._rate_limiter.acquire,
            max_retries=_SEND_MAX_RETRIES,
            base_delay=_SEND_BASE_DELAY,
        )

        if all_sent:
            logger.debug("[telegram] Sent %d chunk(s) to %d: %.80s", len(chunks), chat_id, outbound.text)
            # React with ✅ (or 🎯 if a tool was used)
            original_msg_id = outbound.context_metadata.get("message_id")
            if original_msg_id:
                emoji = "🎯" if outbound.context_metadata.get("tool_used") else "✅"
                self._spawn(
                    self._set_reaction(chat_id, original_msg_id, emoji)
                )
            # Send any media attachments (photos/documents/video) after text.
            for att in outbound.attachments:
                await self._send_attachment(chat_id, att)
            # Send TTS voice reply only when this turn was voice + Settings allow it
            try:
                from kazma_gateway.adapters.voice_helpers import should_send_tts_reply

                if should_send_tts_reply(outbound.context_metadata):
                    self._spawn(
                        self._send_tts_reply(chat_id, outbound.text, original_msg_id)
                    )
            except Exception:
                logger.debug("[telegram] TTS reply gate failed", exc_info=True)
            return True
        else:
            # React with ❌ on failure
            original_msg_id = outbound.context_metadata.get("message_id")
            if original_msg_id:
                self._spawn(
                    self._set_reaction(chat_id, original_msg_id, "❌")
                )
            return False

    async def _send_attachment(self, chat_id: int, att: Attachment) -> bool:
        """Deliver one media attachment via the matching Telegram API.

        Routes by ``att.kind``: image→``/sendPhoto``, video→``/sendVideo``,
        audio→``/sendAudio``, otherwise ``/sendDocument``. Bytes must be in
        ``att.data``; if absent and ``att.url`` is set, the file is fetched
        first. Failures are logged but never abort the whole send.
        """
        if self._http is None:
            return False

        data = att.data
        if data is None and att.url:
            try:
                resp = await self._http.get(att.url, timeout=30.0)
                resp.raise_for_status()
                data = resp.content
            except Exception as exc:  # noqa: BLE001
                logger.warning("[telegram] attachment fetch failed: %s", exc)
                return False
        if not data:
            return False

        if att.kind == "image":
            endpoint = "/sendPhoto"
            field = "photo"
        elif att.kind == "video":
            endpoint = "/sendVideo"
            field = "video"
        elif att.kind == "audio":
            endpoint = "/sendAudio"
            field = "audio"
        else:
            endpoint = "/sendDocument"
            field = "document"

        safe_name = att.filename or f"kazma_{att.kind}"
        try:
            await self._rate_limiter.acquire()
            resp = await self._http.post(
                endpoint,
                params={"chat_id": chat_id},
                files={field: (safe_name, data, att.mime or "application/octet-stream")},
            )
            resp.raise_for_status()
            if not resp.json().get("ok"):
                logger.error("[telegram] %s returned ok=false", endpoint)
                return False
            logger.info("[telegram] sent %s (%d bytes) to %d", endpoint, len(data), chat_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[telegram] %s failed: %s", endpoint, type(exc).__name__)
            return False

    async def _register_bot_commands(self) -> None:
        """Register slash commands with Telegram's setMyCommands API.

        Called once at startup after getMe validation. Registers all
        slash commands across 3 scopes so the command menu button
        appears next to the chat input field.

        Uses the shared ``self._http`` client (already initialized
        in ``listen()`` before this method is called).
        """
        # Telegram shows only what we register here (menu next to the input).
        # Single SoT: kazma_gateway.slash_commands.BOT_MENU_COMMANDS.
        commands = list(BOT_MENU_COMMANDS)

        scopes: list[tuple[str, str]] = [
            ("default", "default"),
            ("all_private_chats", "all_private_chats"),
            ("all_group_chats", "all_group_chats"),
        ]

        if self._http is None:
            raise RuntimeError("HTTP client not initialized")

        for scope_label, scope_type in scopes:
            try:
                payload: dict[str, Any] = {
                    "commands": commands,
                    "scope": {"type": scope_type},
                }
                resp = await self._http.post(
                    "/setMyCommands",
                    json=payload,
                )
                if resp.status_code == 200 and resp.json().get("ok"):
                    logger.info(
                        "[telegram] setMyCommands OK for scope %s (%d cmds)",
                        scope_label,
                        len(commands),
                    )
                else:
                    logger.warning(
                        "[telegram] setMyCommands failed for scope %s: %s",
                        scope_label,
                        resp.text[:200],
                    )
            except Exception as exc:
                logger.warning(
                    "[telegram] setMyCommands error for scope %s (non-fatal): %s",
                    scope_label,
                    exc,
                )
