"""Discord adapter for Kazma Gateway.

Connects to Discord via bot token, receives messages through
the gateway's message queue, and delivers outbound messages
via Discord REST API (httpx).

No webhooks, no tunnels — uses Discord Gateway WebSocket for receiving
and REST API for sending. Platform-specific IDs (channel_id, guild_id,
user_id) live in context_metadata and NEVER enter Brain state.

Environment:
    DISCORD_BOT_TOKEN — Discord bot token (NOT in kazma.yaml)

context_metadata keys:
    channel_id:  str — Discord channel ID
    guild_id:    str | None — Guild ID (None for DMs)
    user_id:     str — Discord user ID
    message_id:  str — Discord message ID
    username:    str — Discord username
    guild_name:  str | None — Guild name
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from kazma_core.background import spawn_background
from kazma_gateway.gateway import (
    Attachment,
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
    RateLimiter,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DiscordAdapter",
]

_DISCORD_API = "https://discord.com/api/v10"
_DISCORD_GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"

# Rate-limit constants
_SEND_MAX_RETRIES = 3
_SEND_BASE_DELAY = 1.0


class DiscordAdapter(BaseAdapter):
    """Discord bot adapter using Gateway WebSocket + REST API.

    Receives messages via Discord Gateway WebSocket (long-lived connection).
    Sends messages via REST API POST /channels/{channel_id}/messages.

    Args:
        token:           Discord bot token.
        allowed_guilds:  Optional whitelist of guild IDs (empty = allow all).
        allowed_users:   Optional whitelist of Discord user IDs (empty = allow
                         all). Stored as strings to match how Discord snowflake
                         IDs are parsed. Mirrors Telegram's allowed_users gate.

    context_metadata keys (carried in every IncomingMessage):
        channel_id:  str
        guild_id:    str | None
        user_id:     str
        message_id:  str
        username:    str
        guild_name:  str | None
    """

    name = "discord"

    def __init__(
        self,
        token: str,
        allowed_guilds: list[str] | None = None,
        allowed_users: list[str] | None = None,
        allow_all: bool = False,
    ) -> None:
        super().__init__()
        self._token = token
        self._allowed_guilds = set(allowed_guilds or [])
        self._allowed_users = set(allowed_users or [])
        self._allow_all = allow_all
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(max_per_second=5)
        self._ws = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._sequence: int | None = None
        self._session_id: str | None = None

    def set_allowed_users(self, user_ids: list[str] | set[str]) -> None:
        """Replace the user allowlist at runtime (mirrors Telegram).

        This replaces direct assignment to the private ``_allowed_users``
        attribute so callers don't reach into internals.
        """
        self._allowed_users = {str(uid) for uid in user_ids}

    def actor_allowed(self, user_id: object) -> bool:
        """Fail-closed allowlist: empty + ``allow_all=False`` rejects everyone."""
        if not self._allowed_users and not self._allow_all:
            return False
        if self._allowed_users:
            uid = str(user_id or "")
            return bool(uid) and uid in self._allowed_users
        return True

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Connect to Discord Gateway and enqueue normalized messages.

        Uses Discord Gateway WebSocket for receiving events.
        Falls back to polling if WebSocket fails.

        Args:
            queue:          The unified message bus.
            shutdown_event: Signals when to stop.
        """
        if not self._allowed_users and not self._allow_all:
            logger.error(
                "[discord] No allowed_users configured and allow_all is false. "
                "Bot will REJECT ALL messages. Set connectors.discord.allowed_users "
                "or allow_all=true."
            )
        elif not self._allowed_users and self._allow_all:
            logger.warning("[discord] allow_all is true — accepting messages from ALL users.")
        self._queue = queue  # for interaction → synthetic slash enqueue
        self._http = httpx.AsyncClient(
            base_url=_DISCORD_API,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Authorization": f"Bot {self._token}"},
        )

        try:
            logger.info("[discord] Starting Discord gateway connection")

            while not shutdown_event.is_set():
                try:
                    await self._connect_gateway(queue, shutdown_event)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    # Fatal gateway close codes: 4013 invalid intents / 4014
                    # disallowed intents. 4014 means MESSAGE_CONTENT (or
                    # another privileged intent we request) is NOT enabled in
                    # the Discord Developer Portal — reconnecting can never
                    # succeed, so stop with an actionable message instead of
                    # looping forever.
                    _close_code = getattr(getattr(exc, "rcvd", None), "code", None)
                    if _close_code in (4013, 4014):
                        logger.critical(
                            "[discord] Gateway rejected our intents (close code "
                            "%s). Enable 'MESSAGE CONTENT INTENT' under Discord "
                            "Developer Portal → Application → Bot → Privileged "
                            "Gateway Intents, then restart Kazma.",
                            _close_code,
                        )
                        break
                    err_msg = str(exc) or exc.__class__.__name__
                    logger.warning("[discord] Gateway connection dropped (%s) — reconnecting...", err_msg)
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue

        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                try:
                    await self._heartbeat_task
                except asyncio.CancelledError:
                    pass
            if self._http:
                await self._http.aclose()
                self._http = None
            logger.info("[discord] Adapter stopped")

    async def _connect_gateway(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Connect to Discord Gateway WebSocket and process events."""
        try:
            import websockets

            async with websockets.connect(_DISCORD_GATEWAY) as ws:
                self._ws = ws
                logger.info("[discord] Connected to Discord Gateway")

                # Wait for Hello
                hello = json.loads(await ws.recv())
                heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

                # Cancel previous heartbeat task before starting a new one
                if self._heartbeat_task and not self._heartbeat_task.done():
                    self._heartbeat_task.cancel()
                # Start heartbeat
                self._heartbeat_task = asyncio.create_task(self._heartbeat(ws, heartbeat_interval, shutdown_event))

                # Send Identify
                await ws.send(
                    json.dumps(
                        {
                            "op": 2,
                            "d": {
                                "token": self._token,
                                # GUILDS + GUILD_MESSAGES + MESSAGE_CONTENT
                                # (privileged — must ALSO be enabled in the
                                # Discord Developer Portal, or the gateway
                                # closes with 4014) + DIRECT_MESSAGES.
                                # Without MESSAGE_CONTENT, guild messages
                                # arrive with empty content (2023 enforcement)
                                # and DMs require DIRECT_MESSAGES.
                                "intents": (1 << 0) | (1 << 9) | (1 << 15) | (1 << 12),
                                "properties": {"os": "linux", "browser": "kazma", "device": "kazma"},
                            },
                        }
                    )
                )

                # Process events
                async for raw_msg in ws:
                    if shutdown_event.is_set():
                        break

                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    op = msg.get("op")
                    t = msg.get("t")
                    d = msg.get("d")
                    s = msg.get("s")

                    if s is not None:
                        self._sequence = s

                    # Dispatch
                    if op == 0 and t == "MESSAGE_CREATE":
                        parsed = self._parse_message(d)
                        if parsed:
                            if self._allowed_guilds:
                                gid = parsed.context_metadata.get("guild_id")
                                if gid and gid not in self._allowed_guilds:
                                    continue

                            # User-level allowlist (fail-closed when empty + !allow_all)
                            if not self._allowed_users and not self._allow_all:
                                logger.warning("[discord] Rejecting message — no allowed_users and allow_all is false")
                                continue
                            if self._allowed_users:
                                uid = parsed.context_metadata.get("user_id")
                                if not uid or uid not in self._allowed_users:
                                    logger.info(
                                        "[discord] Dropping message from "
                                        "non-allowed user %s", uid,
                                    )
                                    continue

                            # Voice: if enabled and an audio attachment is
                            # present, fetch + transcribe it into msg.text.
                            parsed = await self._maybe_transcribe_audio(parsed)

                            try:
                                queue.put_nowait(parsed)
                                logger.info(
                                    "[discord] Enqueued from %s (ch=%s): %.80s",
                                    parsed.context_metadata.get("username", "?"),
                                    parsed.context_metadata.get("channel_id", "?"),
                                    parsed.text,
                                )
                            except asyncio.QueueFull:
                                logger.warning("[discord] Queue full — dropping message")

                    elif op == 0 and t == "INTERACTION_CREATE":
                        # HITL approval button press — route to the active
                        # DiscordBusAdapter so it resolves the asyncio.Event
                        # the paused swarm worker is awaiting.
                        await self._handle_interaction(d)

                    elif op == 7:  # Reconnect
                        logger.info("[discord] Gateway requested reconnect")
                        return

                    elif op == 9:  # Invalid session
                        logger.warning("[discord] Invalid session — will reconnect")
                        self._session_id = None
                        return

                    elif op == 11:  # Heartbeat ACK
                        pass

        except ImportError:
            logger.error("[discord] websockets package not installed — run: pip install websockets")
            await asyncio.sleep(10)

    async def _heartbeat(
        self,
        ws: Any,
        interval: float,
        shutdown_event: asyncio.Event,
    ) -> None:
        """Send periodic heartbeats to Discord Gateway."""
        try:
            while not shutdown_event.is_set():
                await asyncio.sleep(interval * (0.8 + random.uniform(0, 0.2)))
                await ws.send(json.dumps({"op": 1, "d": self._sequence}))
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.debug("[discord] Heartbeat stopped (%s)", exc)

    async def _handle_interaction(self, data: dict[str, Any]) -> None:
        """Route Discord component interactions via shared callback schemes.

        Handles swarm HITL, dependency install, and graph-HITL button IDs
        (``hitl:approve:{id}``) using :mod:`discord_callbacks`.
        """
        from kazma_gateway.adapters.discord_callbacks import (
            is_install_action,
            package_from_install,
            parse_custom_id,
            route_swarm_bus,
        )

        interaction_id = data.get("id", "")
        interaction_token = data.get("token", "")
        component_data = data.get("data", {})
        custom_id = component_data.get("custom_id", "")
        action = parse_custom_id(custom_id)

        async def _ack(payload: dict[str, Any]) -> None:
            try:
                if not self._http:
                    self._http = httpx.AsyncClient(
                        base_url=_DISCORD_API,
                        timeout=15.0,
                        headers={"Authorization": f"Bot {self._token}"},
                    )
                await self._http.post(
                    f"/interactions/{interaction_id}/{interaction_token}/callback",
                    json=payload,
                )
            except Exception as exc:
                logger.debug("[discord] Interaction ack failed: %s", exc)

        # Audit (allowlist bypass): enforce the user allowlist on EVERY interaction
        # branch, not only the graph HITL/picker branch below. Previously the
        # swarm-approval and dependency-install branches resolved with no user
        # check, so a non-allowlisted guild member could Approve a danger tool,
        # Reject a task, or trigger a remote package install by clicking a button.
        _ia_user = data.get("member", {}).get("user") or data.get("user") or {}
        _ia_user_id = str(_ia_user.get("id", ""))
        if not self.actor_allowed(_ia_user_id):
            logger.info(
                "[discord] Ignoring interaction (allowlist fail-closed) user=%s action=%s",
                _ia_user_id, action.kind,
            )
            await _ack({"type": 6})
            return

        if is_install_action(custom_id):
            package_name = package_from_install(custom_id)
            from kazma_core.system.runtime_manager import trigger_package_promotion

            spawn_background(trigger_package_promotion(package_name), name=f"discord-promote:{package_name}")
            content = (
                "[⏳ Installing package... please wait]"
                if action.kind == "sys_install"
                else "⏳ Installing ML dependencies in the background..."
            )
            await _ack(
                {
                    "type": 7,
                    "data": {"content": content, "embeds": [], "components": []},
                }
            )
            return

        if action.kind == "swarm":
            task_id = route_swarm_bus(custom_id)
            if task_id is not None:
                logger.info("[discord] Swarm approval resolved: %s", task_id)
            await _ack({"type": 6})
            return

        # Graph HITL / personality / model pickers → synthetic slash into queue
        if action.kind in ("hitl", "personality", "model_provider", "model_select") and action.text:
            try:
                user = data.get("member", {}).get("user") or data.get("user") or {}
                channel_id = str(data.get("channel_id") or "")
                user_id = str(user.get("id", ""))
                # Top-of-handler actor_allowed already covered empty + nonempty.
                msg = IncomingMessage(
                    platform="discord",
                    sender_id=(
                        f"discord:{user_id}:{channel_id}"
                        if user_id
                        else f"discord:{channel_id}"
                    ),
                    text=action.text,
                    context_metadata={
                        "channel_id": channel_id,
                        "user_id": str(user.get("id", "")),
                        "username": user.get("username", ""),
                        "interaction": True,
                    },
                )
                queue = getattr(self, "_queue", None) or getattr(self, "queue", None)
                if queue is not None:
                    queue.put_nowait(msg)
            except Exception as exc:
                logger.warning("[discord] Failed to enqueue interaction command: %s", exc)
            await _ack({"type": 6})
            return

        # Unknown component — ignore
        return

    def _parse_message(self, data: dict[str, Any] | None) -> IncomingMessage | None:
        """Parse a Discord MESSAGE_CREATE event into an IncomingMessage.

        Delegates to :mod:`discord_parse` (Telegram-style pure helper module).
        """
        from kazma_gateway.adapters.discord_parse import parse_message_create

        return parse_message_create(data)

    # ── Typing indicator (fire-and-forget) ──────────────────────────

    async def _trigger_typing(self, channel_id: str) -> None:
        """Fire a 'typing…' indicator on a Discord channel (fire-and-forget)."""
        cid = channel_id.split(":", 1)[1] if ":" in channel_id else channel_id
        try:
            if not self._http:
                return
            await self._http.post(f"/channels/{cid}/typing")
        except Exception:
            pass  # fire-and-forget

    async def send(self, outbound: OutboundMessage) -> bool:
        """Send a message to a Discord channel via REST API.

        Extracts channel_id from outbound.context_metadata or
        falls back to parsing from outbound.target_id.

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if sent successfully.
        """
        from kazma_gateway.adapters.discord_send import chunk_message, resolve_channel_id, sanitize_outbound

        # Fire typing indicator before sending
        spawn_background(self._trigger_typing(outbound.target_id), name="discord-typing")
        if not self._http:
            self._http = httpx.AsyncClient(
                base_url=_DISCORD_API,
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"Authorization": f"Bot {self._token}"},
            )

        channel_id = resolve_channel_id(outbound.context_metadata, outbound.target_id)
        if not channel_id:
            logger.error("[discord] No channel_id available for send()")
            return False

        # Send with 429 retry — chunk long messages into Discord-safe pieces.
        # Audit G9b: sanitize @everyone/@here and raw mention markup first so
        # untrusted agent/tool output can't ping/broadcast.
        chunks = chunk_message(sanitize_outbound(outbound.text or ""))
        all_sent = True
        for chunk in chunks:
            for attempt in range(_SEND_MAX_RETRIES):
                try:
                    await self._rate_limiter.acquire()
                    payload: dict[str, Any] = {"content": chunk}
                    # Attach interactive components on the first chunk only
                    comps = outbound.context_metadata.get("components")
                    if comps and chunk is chunks[0]:
                        payload["components"] = comps
                    resp = await self._http.post(
                        f"/channels/{channel_id}/messages",
                        json=payload,
                    )

                    if resp.status_code == 429:
                        body = resp.json()
                        retry_after = body.get("retry_after", _SEND_BASE_DELAY * (2**attempt))
                        jitter = random.uniform(0.5, 1.5)
                        wait = retry_after + jitter
                        logger.warning(
                            "[discord] Rate-limited (429) — retrying in %.1fs (attempt %d/%d)",
                            wait,
                            attempt + 1,
                            _SEND_MAX_RETRIES,
                        )
                        await asyncio.sleep(wait)
                        continue

                    resp.raise_for_status()
                    break  # success — move to next chunk
                except httpx.HTTPStatusError as exc:
                    logger.error("[discord] HTTP %d on send to %s: %s", exc.response.status_code, channel_id, exc.response.text[:200])
                    all_sent = False
                    break
                except Exception as exc:
                    logger.error("[discord] Send failed: %s: %s", type(exc).__name__, str(exc)[:200])
                    all_sent = False
                    break

        if all_sent:
            logger.debug("[discord] Sent to channel %s: %.80s", channel_id, outbound.text)
            # Deliver any media attachments after the text.
            for att in outbound.attachments:
                await self._send_attachment(channel_id, att)
            # Voice reply: if the inbound turn was transcribed audio and TTS
            # is enabled, synthesize the text and send it back as audio.
            if outbound.context_metadata.get("voice_transcribed") and outbound.text:
                spawn_background(self._send_voice_reply(channel_id, outbound.text), name="discord-voice-reply")
        return all_sent

    async def _send_voice_reply(self, channel_id: str, text: str) -> bool:
        """Synthesize *text* and upload it as an audio attachment (TG-depth path)."""
        if not self._http:
            return False
        from kazma_gateway.adapters.discord_stt import send_voice_reply

        return await send_voice_reply(
            http=self._http,
            channel_id=channel_id,
            text=text,
            rate_limiter=self._rate_limiter,
        )

    async def _maybe_transcribe_audio(self, msg: IncomingMessage) -> IncomingMessage:
        """Telegram-depth STT: size caps, language, provider, metadata tags."""
        from kazma_gateway.adapters.discord_stt import transcribe_message

        return await transcribe_message(msg, http=self._http)

    async def _send_attachment(self, channel_id: str, att: Attachment) -> bool:
        """Upload one attachment to a Discord channel via multipart.

        Discord's create-message endpoint accepts ``files[N]`` parts plus a
        ``payload_json`` describing the message. Bytes come from ``att.data``
        or, failing that, are fetched from ``att.url``.
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
                logger.warning("[discord] attachment fetch failed: %s", exc)
                return False
        if not data:
            return False

        safe_name = att.filename or f"kazma_{att.kind}"
        try:
            await self._rate_limiter.acquire()
            resp = await self._http.post(
                f"/channels/{channel_id}/messages",
                data={"payload_json": json.dumps({"content": ""})},
                files={"files[0]": (safe_name, data, att.mime or "application/octet-stream")},
            )
            resp.raise_for_status()
            logger.info(
                "[discord] sent attachment %s (%d bytes) to %s",
                safe_name, len(data), channel_id,
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[discord] attachment send failed: %s", type(exc).__name__)
            return False

    # ── Interactive builders (Telegram-parity static API) ───────────

    @staticmethod
    def build_approval_keyboard(request_id: str) -> list[dict[str, Any]]:
        """Discord components for graph HITL (same IDs as Telegram)."""
        from kazma_gateway.adapters.discord_keyboards import build_approval_components

        return build_approval_components(request_id)

    @staticmethod
    def build_personality_keyboard(personalities: list[str]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.discord_keyboards import build_personality_components

        return build_personality_components(personalities)

    @staticmethod
    def build_provider_keyboard(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.discord_keyboards import build_provider_components

        return build_provider_components(providers)

    @staticmethod
    def build_model_keyboard(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.discord_keyboards import build_model_components

        return build_model_components(provider_name, models)
