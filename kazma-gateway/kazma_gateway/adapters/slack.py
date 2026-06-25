"""Slack adapter for Kazma Gateway.

Connects to Slack via Socket Mode (WebSocket), receives events through
the gateway's message queue, and delivers outbound messages via Slack
Web API (httpx).

No webhooks required for receiving — uses Socket Mode (Slack's preferred
method for development). Platform-specific IDs (channel_id, team_id,
user_id) live in context_metadata and NEVER enter Brain state.

Environment:
    SLACK_BOT_TOKEN — Bot token (xoxb-...)
    SLACK_APP_TOKEN — App-level token for Socket Mode (xapp-...)

context_metadata keys:
    channel_id:  str — Slack channel ID (C0123456789)
    team_id:     str — Slack workspace/team ID (T0123456789)
    user_id:     str — Slack user ID (U0123456789)
    message_ts:  str — Message timestamp (1234567890.123456)
    username:    str — Slack username or display name
    channel_name: str | None — Channel name (if available)
    team_name:   str | None — Team/workspace name (if available)
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

_SLACK_API = "https://slack.com/api"

# Rate-limit constants
_SEND_MAX_RETRIES = 3
_SEND_BASE_DELAY = 1.0


class SlackAdapter(BaseAdapter):
    """Slack bot adapter using Socket Mode + Web API.

    Receives messages via Socket Mode WebSocket (Slack's real-time messaging).
    Sends messages via Web API POST /chat.postMessage.

    Args:
        bot_token:       Slack bot token (xoxb-...).
        app_token:       Slack app-level token for Socket Mode (xapp-...).
        allowed_teams:   Optional whitelist of team IDs (empty = allow all).
        allowed_channels: Optional whitelist of channel IDs (empty = allow all).

    context_metadata keys (carried in every IncomingMessage):
        channel_id:   str
        team_id:      str
        user_id:      str
        message_ts:   str
        username:     str
        channel_name: str | None
        team_name:    str | None
    """

    name = "slack"

    def __init__(
        self,
        bot_token: str,
        app_token: str,
        allowed_teams: list[str] | None = None,
        allowed_channels: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._bot_token = bot_token
        self._app_token = app_token
        self._allowed_teams = set(allowed_teams or [])
        self._allowed_channels = set(allowed_channels or [])
        self._http: httpx.AsyncClient | None = None
        self._rate_limiter = RateLimiter(max_per_second=1)  # Slack: 1 msg/sec per channel
        self._ws = None

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Connect to Slack Socket Mode and enqueue normalized messages.

        Uses Slack's Socket Mode for real-time events. This is the preferred
        method for development and doesn't require a public URL.

        Args:
            queue:          The unified message bus.
            shutdown_event: Signals when to stop.
        """
        self._http = httpx.AsyncClient(
            base_url=_SLACK_API,
            timeout=httpx.Timeout(30.0, connect=5.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            headers={"Authorization": f"Bearer {self._bot_token}"},
        )

        try:
            logger.info("[slack] Starting Socket Mode connection")

            while not shutdown_event.is_set():
                try:
                    await self._connect_socket_mode(queue, shutdown_event)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("[slack] Socket Mode connection error")
                    if await self.jitter_sleep(shutdown_event):
                        break
                    continue

        finally:
            if self._http:
                await self._http.aclose()
                self._http = None
            logger.info("[slack] Adapter stopped")

    async def _connect_socket_mode(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Connect to Slack Socket Mode WebSocket and process events."""
        try:
            import websockets

            # Get WebSocket URL from Slack API
            resp = await self._http.post(
                "/apps.connections.open",
                headers={"Authorization": f"Bearer {self._app_token}"},
            )
            resp.raise_for_status()
            data = resp.json()

            if not data.get("ok"):
                logger.error("[slack] Failed to open Socket Mode: %s", data.get("error"))
                await asyncio.sleep(5)
                return

            ws_url = data["url"]

            async with websockets.connect(ws_url) as ws:
                self._ws = ws
                logger.info("[slack] Connected to Socket Mode")

                async for raw_msg in ws:
                    if shutdown_event.is_set():
                        break

                    try:
                        msg = json.loads(raw_msg)
                    except json.JSONDecodeError:
                        continue

                    # Slack sends different envelope types
                    envelope_type = msg.get("type")

                    if envelope_type == "hello":
                        logger.info("[slack] Socket Mode handshake complete")
                        continue

                    elif envelope_type == "disconnect":
                        reason = msg.get("reason", "unknown")
                        logger.info("[slack] Disconnect requested: %s", reason)
                        return

                    elif envelope_type == "events_api":
                        # Acknowledge the event
                        await ws.send(json.dumps({"envelope_id": msg.get("envelope_id")}))

                        # Process the event
                        event = msg.get("payload", {}).get("event", {})
                        parsed = self._parse_event(event)
                        if parsed:
                            # Team whitelist
                            if self._allowed_teams:
                                team_id = parsed.context_metadata.get("team_id")
                                if team_id and team_id not in self._allowed_teams:
                                    continue

                            # Channel whitelist
                            if self._allowed_channels:
                                channel_id = parsed.context_metadata.get("channel_id")
                                if channel_id and channel_id not in self._allowed_channels:
                                    continue

                            try:
                                queue.put_nowait(parsed)
                                logger.info(
                                    "[slack] Enqueued from %s (ch=%s): %.80s",
                                    parsed.context_metadata.get("username", "?"),
                                    parsed.context_metadata.get("channel_id", "?"),
                                    parsed.text,
                                )
                            except asyncio.QueueFull:
                                logger.warning("[slack] Queue full — dropping message")

        except ImportError:
            logger.error("[slack] websockets package not installed — run: pip install websockets")
            await asyncio.sleep(10)

    def _parse_event(self, event: dict[str, Any] | None) -> IncomingMessage | None:
        """Parse a Slack event into an IncomingMessage.

        Args:
            event: The Slack event payload.

        Returns:
            IncomingMessage or None if not a valid text message.
        """
        if not event:
            return None

        event_type = event.get("type")

        # Only process message events
        if event_type not in ("message", "app_mention"):
            return None

        # Skip bot messages, edits, deletions
        if event.get("subtype"):
            return None

        # Skip messages from bots
        if event.get("bot_id"):
            return None

        text = (event.get("text") or "").strip()
        if not text:
            return None

        channel_id = event.get("channel", "")
        if not channel_id:
            return None

        user_id = event.get("user", "")
        if not user_id:
            return None

        # Slack doesn't always include username in events — we'd need to fetch it
        # For now, use user_id as fallback
        username = event.get("username") or f"slack_{user_id}"

        return IncomingMessage(
            platform="slack",
            sender_id=f"slack:{user_id}",
            text=text,
            context_metadata={
                "channel_id": channel_id,
                "team_id": event.get("team"),
                "user_id": user_id,
                "message_ts": event.get("ts", ""),
                "username": username,
                "channel_name": event.get("channel_name"),
                "team_name": event.get("team_name"),
            },
        )

    async def send(self, outbound: OutboundMessage) -> bool:
        """Send a message to a Slack channel via Web API.

        Extracts channel_id from outbound.context_metadata or
        falls back to parsing from outbound.target_id.

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if sent successfully.
        """
        if not self._http:
            self._http = httpx.AsyncClient(
                base_url=_SLACK_API,
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
                headers={"Authorization": f"Bearer {self._bot_token}"},
            )

        # Resolve channel_id
        channel_id = outbound.context_metadata.get("channel_id")
        if not channel_id:
            if ":" in outbound.target_id:
                channel_id = outbound.target_id.split(":", 1)[1]
            if not channel_id:
                logger.error("[slack] No channel_id available for send()")
                return False

        # Send with rate-limit retry
        payload = {
            "channel": channel_id,
            "text": outbound.text[:40000],  # Slack limit: 40k chars
        }

        for attempt in range(_SEND_MAX_RETRIES):
            try:
                await self._rate_limiter.acquire()
                resp = await self._http.post("/chat.postMessage", json=payload)

                if resp.status_code == 429:
                    retry_after = float(resp.headers.get("Retry-After", _SEND_BASE_DELAY * (2**attempt)))
                    jitter = random.uniform(0.5, 1.5)
                    wait = retry_after + jitter
                    logger.warning(
                        "[slack] Rate-limited (429) — retrying in %.1fs (attempt %d/%d)",
                        wait,
                        attempt + 1,
                        _SEND_MAX_RETRIES,
                    )
                    await asyncio.sleep(wait)
                    continue

                resp.raise_for_status()
                data = resp.json()

                if data.get("ok"):
                    logger.debug("[slack] Sent to channel %s: %.80s", channel_id, outbound.text)
                    return True
                else:
                    error = data.get("error", "unknown")
                    logger.error("[slack] chat.postMessage failed: %s", error)
                    return False

            except httpx.HTTPStatusError as exc:
                logger.error(
                    "[slack] HTTP %d on send to %s: %s",
                    exc.response.status_code,
                    channel_id,
                    exc,
                )
                return False
            except Exception:
                logger.exception("[slack] Failed to send to channel %s", channel_id)
                return False

        logger.error(
            "[slack] Rate-limit exceeded after %d retries for channel=%s",
            _SEND_MAX_RETRIES,
            channel_id,
        )
        return False
