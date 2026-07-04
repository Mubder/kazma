"""Slack adapter for Kazma Gateway.

Connects to Slack via bot token using Slack's Web API (httpx).
Receives messages via polling the conversations.list + conversations.history
pattern (no Socket Mode dependency). Delivers outbound messages via
chat.postMessage REST API with 429 rate-limit retry.

No webhooks, no tunnels, no Socket Mode required.
Platform-specific IDs (channel_id, user_id, team_id) live in
context_metadata and NEVER enter Brain state.

Environment:
    SLACK_BOT_TOKEN — Slack bot token (xoxb-...)
    SLACK_APP_TOKEN — Slack app-level token (optional, for Socket Mode)

context_metadata keys:
    channel_id:  str — Slack channel ID
    user_id:     str — Slack user ID
    team_id:     str | None — Workspace ID
    thread_ts:   str | None — Thread timestamp for replies
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from kazma_gateway.gateway import BaseAdapter, IncomingMessage, OutboundMessage

logger = logging.getLogger(__name__)

_SLACK_API = "https://slack.com/api"
_POLL_INTERVAL = 2.0
_MAX_TIMEOUT = 15.0
_MAX_RETRIES = 3

# Slack message subtypes that should be filtered out at the adapter level
_SKIP_SUBTYPES = frozenset({"message_changed", "message_deleted", "channel_join", "channel_leave"})


class SlackAdapter(BaseAdapter):
    """Polling-based Slack adapter using Web API.

    Args:
        bot_token: Slack bot token (xoxb-...). If None, reads SLACK_BOT_TOKEN
                   from the environment.
        app_token: Slack app-level token (xapp-...) for Socket Mode.
                   Optional — not used by the polling adapter.
        allowed_teams: Optional iterable of team IDs to whitelist.
        allowed_channels: Optional iterable of channel IDs to whitelist.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        app_token: str | None = None,
        allowed_teams: list[str] | None = None,
        allowed_channels: list[str] | None = None,
    ) -> None:
        import os

        super().__init__()
        self.name = "slack"

        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self._allowed_teams: set[str] = set(allowed_teams or [])
        self._allowed_channels: set[str] = set(allowed_channels or [])

        if not self._bot_token:
            logger.warning("[Slack] No bot token — adapter will stay STOPPED")

        self._http: httpx.AsyncClient | None = None
        self._known_channels: list[dict[str, Any]] = []
        self._last_ts: dict[str, str] = {}  # channel_id → last seen ts

    # ── Helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json",
        }

    # ── Event Parsing (public for testing) ──────────────────────────

    def _parse_event(self, event: dict[str, Any] | None) -> IncomingMessage | None:
        """Parse a raw Slack event dict into an IncomingMessage.

        Returns None for events that should be skipped (bot messages,
        edits, empty text, non-message types, missing fields).
        """
        if event is None:
            return None

        event_type = event.get("type", "")

        # Only handle message and app_mention events
        if event_type not in ("message", "app_mention"):
            return None

        # Skip bot messages
        if "bot_id" in event:
            return None

        # Skip edit / delete etc.
        subtype = event.get("subtype", "")
        if subtype and subtype != "bot_message":
            return None

        # Require channel
        channel_id = event.get("channel")
        if not channel_id:
            return None

        # Require user
        user_id = event.get("user", "")
        if not user_id:
            return None

        text = event.get("text", "")
        if not text:
            return None

        ts = event.get("ts", "")
        team_id = event.get("team", "")
        username = event.get("username") or f"slack_{user_id}"

        return IncomingMessage(
            platform="slack",
            sender_id=f"slack:{user_id}",
            text=text,
            context_metadata={
                "channel_id": channel_id,
                "user_id": user_id,
                "team_id": team_id,
                "thread_ts": event.get("thread_ts"),
                "message_ts": ts,
                "username": username,
            },
        )

    # ── Typing indicator (fire-and-forget) ──────────────────────────

    async def _trigger_typing(self, channel_id: str) -> None:
        """Fire a typing indicator on Slack (fire-and-forget)."""
        cid = channel_id.split(":", 1)[1] if ":" in channel_id else channel_id
        try:
            if not self._http:
                return
            await self._http.post(
                f"{_SLACK_API}/typing",
                json={"channel": cid},
                headers=self._headers(),
            )
        except Exception:
            pass

    # ── Send ────────────────────────────────────────────────────────

    async def send(self, outbound: OutboundMessage) -> bool:
        """Send a message to a Slack channel via chat.postMessage.

        Handles 429 rate-limit responses with up to 3 retries.

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if sent successfully.
        """
        # Fire typing indicator (fire-and-forget)
        asyncio.create_task(self._trigger_typing(outbound.target_id))

        # Resolve channel_id
        channel_id: str | None = outbound.context_metadata.get("channel_id")
        if not channel_id and ":" in outbound.target_id:
            channel_id = outbound.target_id.split(":", 1)[1]

        if not channel_id:
            logger.error("[Slack] No channel_id in target: %s", outbound.target_id)
            return False

        payload: dict[str, Any] = {
            "channel": channel_id,
            "text": outbound.text,
            "mrkdwn": True,
        }
        thread_ts = outbound.context_metadata.get("thread_ts")
        if thread_ts:
            payload["thread_ts"] = thread_ts

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                if not self._http:
                    self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))

                resp = await self._http.post(
                    f"{_SLACK_API}/chat.postMessage",
                    json=payload,
                    headers=self._headers(),
                )

                if resp.status_code == 429:
                    retry_after = resp.headers.get("Retry-After", "1")
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = 1.0
                    logger.warning(
                        "[Slack] Rate-limited (attempt %d/%d), retrying in %.1fs",
                        attempt, _MAX_RETRIES, delay,
                    )
                    if attempt < _MAX_RETRIES:
                        await asyncio.sleep(delay)
                        continue
                    return False

                data = resp.json()
                if data.get("ok"):
                    logger.info("[Slack] Sent to channel=%s (ts=%s)", channel_id, data.get("ts", "?"))
                    return True
                else:
                    logger.error("[Slack] Send failed: %s", data.get("error", "unknown"))
                    return False

            except httpx.HTTPStatusError:
                logger.exception("[Slack] HTTP error sending to channel=%s", channel_id)
                return False
            except Exception as exc:
                logger.exception("[Slack] Send exception: %s", exc)
                return False

        return False

    # ── Listen (abstract method) ────────────────────────────────────

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Poll Slack for new messages and enqueue them.

        Implements the BaseAdapter abstract method.
        """
        if not self._bot_token:
            return

        self._http = httpx.AsyncClient(timeout=httpx.Timeout(_MAX_TIMEOUT + 5, connect=10.0))
        self._queue = queue
        self._shutdown = shutdown_event

        # Fetch channel list on first poll
        await self._refresh_channels()

        while not shutdown_event.is_set():
            try:
                await self._poll_channels()
                should_exit = await self.jitter_sleep(shutdown_event)
                if should_exit:
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Slack] Poll error: %s", exc)
                await asyncio.sleep(5)

        await self._http.aclose()

    # ── Polling internals ───────────────────────────────────────────

    async def _refresh_channels(self) -> None:
        """Fetch list of channels the bot has access to."""
        try:
            resp = await self._http.post(
                f"{_SLACK_API}/conversations.list",
                json={"types": "public_channel,private_channel,im,mpim", "limit": 100},
                headers=self._headers(),
            )
            data = resp.json()
            if data.get("ok"):
                self._known_channels = data.get("channels", [])
                logger.info("[Slack] Found %d channels", len(self._known_channels))
        except Exception as exc:
            logger.warning("[Slack] Failed to list channels: %s", exc)

    async def _poll_channels(self) -> None:
        """Check each channel for new messages."""
        for channel in self._known_channels:
            try:
                cid = channel["id"]
                latest = self._last_ts.get(cid, "")
                params: dict[str, Any] = {
                    "channel": cid,
                    "limit": 5,
                    "inclusive": False,
                }
                if latest:
                    params["oldest"] = latest

                resp = await self._http.post(
                    f"{_SLACK_API}/conversations.history",
                    json=params,
                    headers=self._headers(),
                )
                data = resp.json()
                if not data.get("ok"):
                    continue

                messages = data.get("messages", [])
                # Process in reverse to maintain chronological order
                for msg in reversed(messages):
                    # Skip bot messages and subtype messages
                    if msg.get("bot_id") or msg.get("subtype"):
                        continue
                    if msg.get("user") == "USLACKBOT":
                        continue

                    ts = msg.get("ts", "")
                    if ts and ts <= latest:
                        continue

                    await self._handle_message(cid, msg)
                    self._last_ts[cid] = ts

            except Exception as exc:
                logger.debug("[Slack] Channel %s poll error: %s", channel.get("id", "?"), exc)

    async def _handle_message(self, channel_id: str, msg: dict[str, Any]) -> None:
        """Normalize a Slack message into an IncomingMessage and enqueue it."""
        # Enforce channel whitelist if configured
        if self._allowed_channels and channel_id not in self._allowed_channels:
            logger.debug("[Slack] Message from non-whitelisted channel %s — skipping", channel_id)
            return

        text = msg.get("text", "").strip()
        if not text:
            return

        user_id = msg.get("user", "")
        username = f"slack_{user_id}" if user_id else "slack_unknown"

        incoming = IncomingMessage(
            platform="slack",
            sender_id=f"slack:{channel_id}",
            text=text,
            context_metadata={
                "channel_id": channel_id,
                "user_id": user_id,
                "thread_ts": msg.get("thread_ts"),
                "message_ts": msg.get("ts"),
                "username": username,
            },
        )
        try:
            self._queue.put_nowait(incoming)
        except asyncio.QueueFull:
            logger.warning("[Slack] Queue full — dropping message from %s", user_id)
            return
        logger.debug("[Slack] ← from %s: %.80s", user_id, text)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        """Override start with token check."""
        if not self._bot_token:
            logger.error("[Slack] Cannot start — no bot token")
            return
        self._queue = queue
        self._shutdown = shutdown_event
        await super().start(queue, shutdown_event)
