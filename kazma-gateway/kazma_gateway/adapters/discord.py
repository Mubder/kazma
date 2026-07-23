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
    ) -> None:
        super().__init__()
        self._token = token
        self._allowed_guilds = set(allowed_guilds or [])
        self._allowed_users = set(allowed_users or [])
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
                except Exception:
                    logger.exception("[discord] Gateway connection error")
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
                                "intents": (1 << 0) | (1 << 9),  # GUILDS + MESSAGE_CONTENT
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

                            # User-level allowlist (mirrors Telegram). Empty
                            # list = allow all; populated = drop non-listed.
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
        except Exception:
            logger.exception("[discord] Heartbeat error")

    async def _handle_interaction(self, data: dict[str, Any]) -> None:
        """Route a Discord component interaction (button press) to the bus.

        When a HITL approval button is clicked, resolve the pending
        asyncio.Event on the active DiscordBusAdapter and acknowledge
        the interaction so Discord doesn't show "interaction failed".
        """
        interaction_id = data.get("id", "")
        interaction_token = data.get("token", "")
        component_data = data.get("data", {})
        custom_id = component_data.get("custom_id", "")

        if custom_id.startswith("install_dependency:") or custom_id.startswith("sys_install:"):
            package_name = custom_id.split(":", 1)[1]
            from kazma_core.system.runtime_manager import trigger_package_promotion
            asyncio.create_task(trigger_package_promotion(package_name))
            
            try:
                if not self._http:
                    self._http = httpx.AsyncClient(
                        base_url=_DISCORD_API,
                        timeout=15.0,
                        headers={"Authorization": f"Bot {self._token}"},
                    )
                
                content = "[⏳ Installing package... please wait]" if custom_id.startswith("sys_install:") else "⏳ Installing ML dependencies in the background..."
                
                await self._http.post(
                    f"/interactions/{interaction_id}/{interaction_token}/callback",
                    json={
                        "type": 7,
                        "data": {
                            "content": content,
                            "embeds": [],
                            "components": []
                        }
                    },
                )
            except Exception as exc:
                logger.warning("[discord] Failed to respond to dependency interaction: %s", exc)
            return

        if not custom_id.startswith(("swarm_approve_", "swarm_reject_")):
            return  # not a swarm approval button

        task_id = None
        try:
            from kazma_core.swarm.bus import get_message_bus

            from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

            adapter = get_message_bus().adapter
            if isinstance(adapter, DiscordBusAdapter):
                task_id = adapter.handle_callback(custom_id)
        except Exception as exc:
            logger.warning("[discord] Swarm approval interaction failed: %s", exc)

        if task_id is not None:
            logger.info("[discord] Swarm approval resolved: %s", task_id)

        # Acknowledge the interaction (type 6 = deferred update) so
        # Discord doesn't mark it as failed. Best-effort — ignore errors.
        try:
            if not self._http:
                self._http = httpx.AsyncClient(
                    base_url=_DISCORD_API,
                    timeout=15.0,
                    headers={"Authorization": f"Bot {self._token}"},
                )
            await self._http.post(
                f"/interactions/{interaction_id}/{interaction_token}/callback",
                json={"type": 6},
            )
        except Exception as exc:
            logger.debug("[discord] Interaction ack failed: %s", exc)

    def _parse_message(self, data: dict[str, Any] | None) -> IncomingMessage | None:
        """Parse a Discord MESSAGE_CREATE event into an IncomingMessage.

        Captures any attachments (images/files) alongside the text. A message
        with attachments but no text is still accepted so media-only uploads
        reach the agent.

        Args:
            data: The MESSAGE_CREATE event data.

        Returns:
            IncomingMessage or None if not a valid message.
        """
        if not data:
            return None

        # Skip bot messages
        author = data.get("author", {})
        if author.get("bot"):
            return None

        content = (data.get("content") or "").strip()
        raw_attachments = data.get("attachments") or []
        embeds = data.get("embeds") or []

        # Accept if there is text OR any attachment. Media-only messages are
        # common (e.g. uploading a screenshot), so don't require content.
        if not content and not raw_attachments:
            return None

        channel_id = str(data.get("channel_id", ""))
        if not channel_id:
            return None

        guild_id = data.get("guild_id")
        user_id = str(author.get("id", ""))
        username = author.get("username", "") or author.get("global_name", "") or f"discord_{user_id}"
        message_id = str(data.get("id", ""))

        attachments: list[Attachment] = []
        for a in raw_attachments:
            mime = (a.get("content_type") or "").lower()
            if mime.startswith("image/"):
                kind = "image"
            elif mime.startswith("video/"):
                kind = "video"
            elif mime.startswith("audio/"):
                kind = "audio"
            else:
                kind = "file"
            attachments.append(
                Attachment(
                    kind=kind,
                    mime=mime or "application/octet-stream",
                    filename=a.get("filename", "") or f"discord_{a.get('id', 'file')}",
                    url=a.get("url"),
                    meta={
                        "attachment_id": a.get("id"),
                        "source": "discord",
                        "width": a.get("width"),
                        "height": a.get("height"),
                    },
                )
            )
        # Embeds with a single image (e.g. link previews) are treated as
        # image attachments too, when the embed carries an image url.
        for e in embeds:
            img = e.get("image") or {}
            url = img.get("url")
            if url:
                attachments.append(
                    Attachment(
                        kind="image",
                        mime="image/png",
                        filename="embed.png",
                        url=url,
                        meta={"source": "discord_embed"},
                    )
                )

        text = content or (f"[{attachments[0].kind}]" if attachments else "")
        return IncomingMessage(
            platform="discord",
            sender_id=f"discord:{channel_id}",
            text=text,
            attachments=attachments,
            context_metadata={
                "channel_id": channel_id,
                "guild_id": str(guild_id) if guild_id else None,
                "user_id": user_id,
                "message_id": message_id,
                "username": username,
                "guild_name": data.get("guild_name"),
                "media": bool(attachments),
            },
        )

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
        # Fire typing indicator before sending
        asyncio.create_task(self._trigger_typing(outbound.target_id))
        if not self._http:
            self._http = httpx.AsyncClient(
                base_url=_DISCORD_API,
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"Authorization": f"Bot {self._token}"},
            )

        # Resolve channel_id
        channel_id = outbound.context_metadata.get("channel_id")
        if not channel_id:
            if ":" in outbound.target_id:
                channel_id = outbound.target_id.split(":", 1)[1]
            if not channel_id:
                logger.error("[discord] No channel_id available for send()")
                return False

        # Send with 429 retry — chunk long messages into 2000-char pieces
        text = outbound.text
        chunks = [text[i:i + 2000] for i in range(0, len(text), 2000)] if text else [""]
        all_sent = True
        for chunk in chunks:
            for attempt in range(_SEND_MAX_RETRIES):
                try:
                    await self._rate_limiter.acquire()
                    resp = await self._http.post(
                        f"/channels/{channel_id}/messages",
                        json={"content": chunk},
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
                asyncio.create_task(self._send_voice_reply(channel_id, outbound.text))
        return all_sent

    async def _send_voice_reply(self, channel_id: str, text: str) -> bool:
        """Synthesize *text* and upload it as an audio attachment."""
        try:
            from kazma_gateway.adapters.voice_helpers import (
                live_voice_settings,
                synthesize_speech,
            )

            if not live_voice_settings().get("enabled") or not self._http:
                return False
            audio = await synthesize_speech(text)
            if not audio:
                return False
            await self._rate_limiter.acquire()
            resp = await self._http.post(
                f"/channels/{channel_id}/messages",
                data={"payload_json": json.dumps({"content": ""})},
                files={"files[0]": ("reply.mp3", audio, "audio/mpeg")},
            )
            resp.raise_for_status()
            logger.info("[discord] voice reply sent to %s (%d bytes)", channel_id, len(audio))
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[discord] voice reply failed: %s", type(exc).__name__)
            return False

    async def _maybe_transcribe_audio(self, msg: IncomingMessage) -> IncomingMessage:
        """When voice is enabled, transcribe any audio attachment to text.

        Downloads the audio (Discord exposes a URL) and replaces ``msg.text``
        with the transcript, tagging ``voice_transcribed`` so the outbound
        path can reply with TTS. No-op when voice is disabled or no audio is
        present.
        """
        audio = next((a for a in msg.attachments if a.kind == "audio"), None)
        if audio is None:
            return msg
        try:
            from kazma_gateway.adapters.voice_helpers import (
                live_voice_settings,
                transcribe_audio,
            )

            if not live_voice_settings().get("enabled"):
                return msg
            data = audio.data
            if data is None and audio.url:
                resp = await self._http.get(audio.url, timeout=30.0)
                resp.raise_for_status()
                data = resp.content
            if not data:
                return msg
            transcript = await transcribe_audio(data)
            if transcript:
                msg.text = transcript
                msg.context_metadata["voice_transcribed"] = True
                logger.info("[discord] transcribed audio (%d bytes) from %s",
                            len(data), msg.context_metadata.get("channel_id"))
        except Exception as exc:  # noqa: BLE001 — never drop a turn over STT
            logger.warning("[discord] audio transcription failed: %s", type(exc).__name__)
        return msg

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
