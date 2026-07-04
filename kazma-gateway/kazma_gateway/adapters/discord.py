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
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
    RateLimiter,
)

logger = logging.getLogger(__name__)

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
    ) -> None:
        super().__init__()
        self._token = token
        self._allowed_guilds = set(allowed_guilds or [])
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(max_per_second=5)
        self._ws = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._sequence: int | None = None
        self._session_id: str | None = None

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

        Args:
            data: The MESSAGE_CREATE event data.

        Returns:
            IncomingMessage or None if not a valid text message.
        """
        if not data:
            return None

        # Skip bot messages
        author = data.get("author", {})
        if author.get("bot"):
            return None

        content = (data.get("content") or "").strip()
        if not content:
            return None

        channel_id = str(data.get("channel_id", ""))
        if not channel_id:
            return None

        guild_id = data.get("guild_id")
        user_id = str(author.get("id", ""))
        username = author.get("username", "") or author.get("global_name", "") or f"discord_{user_id}"
        message_id = str(data.get("id", ""))

        return IncomingMessage(
            platform="discord",
            sender_id=f"discord:{channel_id}",
            text=content,
            context_metadata={
                "channel_id": channel_id,
                "guild_id": str(guild_id) if guild_id else None,
                "user_id": user_id,
                "message_id": message_id,
                "username": username,
                "guild_name": data.get("guild_name"),
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
        return all_sent
