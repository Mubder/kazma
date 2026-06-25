"""Slack adapter for Kazma Gateway.

Connects to Slack via bot token using Slack's Web API (httpx).
Receives messages via polling the conversations.list + conversations.history
pattern (no Socket Mode dependency). Delivers outbound messages via
chat.postMessage REST API.

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


class SlackAdapter(BaseAdapter):
    """Polling-based Slack adapter using Web API.

    Args:
        token: Slack bot token (xoxb-...). If None, reads SLACK_BOT_TOKEN
               from the environment.
    """

    def __init__(self, token: str | None = None) -> None:
        import os

        super().__init__(name="slack", platform="slack")
        self._token = token or os.environ.get("SLACK_BOT_TOKEN", "")
        if not self._token:
            logger.warning("[Slack] No bot token — adapter will stay STOPPED")

        self._http: httpx.AsyncClient | None = None
        self._known_channels: list[dict[str, Any]] = []
        self._last_ts: dict[str, str] = {}  # channel_id → last seen ts

    # ── Helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

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

        Args:
            outbound: The OutboundMessage to deliver.

        Returns:
            True if sent successfully.
        """
        # Fire typing indicator (fire-and-forget)
        asyncio.create_task(self._trigger_typing(outbound.target_id))

        channel_id = outbound.context_metadata.get("channel_id")
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

        try:
            if not self._http:
                self._http = httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0))
            resp = await self._http.post(
                f"{_SLACK_API}/chat.postMessage",
                json=payload,
                headers=self._headers(),
            )
            data = resp.json()
            if data.get("ok"):
                logger.info("[Slack] Sent to channel=%s (ts=%s)", channel_id, data.get("ts", "?"))
                return True
            else:
                logger.error("[Slack] Send failed: %s", data.get("error", "unknown"))
                return False
        except Exception as exc:
            logger.exception("[Slack] Send exception: %s", exc)
            return False

    # ── Polling loop ────────────────────────────────────────────────

    async def _poll(self) -> None:
        """Poll Slack for new messages across known channels."""
        if not self._token:
            return

        self._http = httpx.AsyncClient(timeout=httpx.Timeout(_MAX_TIMEOUT + 5, connect=10.0))

        # Fetch channel list on first poll
        await self._refresh_channels()

        while not self._stop_event.is_set():
            try:
                await self._poll_channels()
                await asyncio.sleep(_POLL_INTERVAL)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Slack] Poll error: %s", exc)
                await asyncio.sleep(5)

        await self._http.aclose()

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
        """Normalize a Slack message into an IncomingMessage."""
        text = msg.get("text", "").strip()
        if not text:
            return

        user_id = msg.get("user", "")
        username = msg.get("user", f"slack_{user_id}")

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
        await self._emit(incoming)
        logger.debug("[Slack] ← from %s: %.80s", user_id, text)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        """Override start with token check."""
        if not self._token:
            logger.error("[Slack] Cannot start — no bot token")
            return
        await super().start(queue, shutdown_event)
