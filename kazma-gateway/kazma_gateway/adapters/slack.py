"""Slack adapter for Kazma Gateway.

Connects to Slack via bot token using Slack's Web API (httpx).
When an app-level token (xapp-...) is provided, uses Socket Mode for
real-time event delivery (recommended). Falls back to polling the
conversations.list + conversations.history pattern when no app token
is available (requires channels:read scope).

Socket Mode receives app_mention and message events in real-time
without needing channels:read scope or public tunneling.

Delivers outbound messages via chat.postMessage REST API with
429 rate-limit retry.

Environment:
    SLACK_BOT_TOKEN — Slack bot token (xoxb-...)
    SLACK_APP_TOKEN — Slack app-level token (xapp-...) for Socket Mode

context_metadata keys:
    channel_id:  str — Slack channel ID
    user_id:     str — Slack user ID
    team_id:     str | None — Workspace ID
    thread_ts:   str | None — Thread timestamp for replies
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx

from kazma_gateway.gateway import (
    Attachment,
    BaseAdapter,
    IncomingMessage,
    OutboundMessage,
)

logger = logging.getLogger(__name__)

__all__ = [
    "SlackAdapter",
]

_SLACK_API = "https://slack.com/api"
_POLL_INTERVAL = 2.0
_MAX_TIMEOUT = 15.0
_MAX_RETRIES = 3
_SOCKET_RECONNECT_DELAY = 2.0
_SOCKET_MAX_RECONNECT_DELAY = 30.0


class SlackAdapter(BaseAdapter):
    """Slack adapter supporting Socket Mode and polling.

    When an app_token (xapp-...) is provided, uses Socket Mode for
    real-time event delivery. This is the recommended mode as it
    receives app_mention events without requiring channels:read scope.

    Without an app_token, falls back to polling conversations.history
    (requires channels:read, groups:read, im:read scopes).

    Args:
        bot_token: Slack bot token (xoxb-...). If None, reads SLACK_BOT_TOKEN
                   from the environment.
        app_token: Slack app-level token (xapp-...) for Socket Mode.
                   If None, reads SLACK_APP_TOKEN from the environment.
        allowed_teams: Optional iterable of team IDs to whitelist.
        allowed_channels: Optional iterable of channel IDs to whitelist.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        app_token: str | None = None,
        allowed_teams: list[str] | None = None,
        allowed_channels: list[str] | None = None,
        allowed_users: list[str] | None = None,
    ) -> None:
        import os

        super().__init__()
        self.name = "slack"

        self._bot_token = bot_token or os.environ.get("SLACK_BOT_TOKEN", "")
        self._app_token = app_token or os.environ.get("SLACK_APP_TOKEN", "")
        self._allowed_teams: set[str] = set(allowed_teams or [])
        self._allowed_channels: set[str] = set(allowed_channels or [])
        # Audit G2c: per-user allowlist (parity with Telegram/Discord). Empty
        # = allow all members of allowed channels; populated = drop non-listed.
        self._allowed_users: set[str] = set(allowed_users or [])

        if not self._bot_token:
            logger.warning("[Slack] No bot token — adapter will stay STOPPED")

        self._http: httpx.AsyncClient | None = None
        self._known_channels: list[dict[str, Any]] = []
        self._last_ts: dict[str, str] = {}  # channel_id → last seen ts
        self._seen_events: set[tuple[str, str]] = set()  # (channel_id, ts) — deduplicates app_mention+message

    # ── Helpers ─────────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json",
        }

    # ── Event Parsing (public for testing) ──────────────────────────

    def _parse_event(self, event: dict[str, Any] | None) -> IncomingMessage | None:
        """Parse a raw Slack event dict into an IncomingMessage.

        Delegates to :mod:`slack_parse` (Telegram-style pure helper).
        """
        from kazma_gateway.adapters.slack_parse import parse_message_event

        return parse_message_event(event)

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
        except Exception as exc:
            logger.debug("Slack typing indicator failed: %s", exc)

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

        from kazma_gateway.adapters.slack_send import resolve_channel_id, sanitize_outbound

        channel_id = resolve_channel_id(outbound.context_metadata, outbound.target_id) or channel_id
        # Audit G9b: sanitize <!everyone>/<!here>/<!channel> and <@user> mentions
        # so untrusted agent/tool output can't broadcast/ping.
        payload: dict[str, Any] = {
            "channel": channel_id,
            "text": sanitize_outbound(outbound.text or ""),
            "mrkdwn": True,
        }
        thread_ts = outbound.context_metadata.get("thread_ts")
        if thread_ts:
            payload["thread_ts"] = thread_ts
        blocks = outbound.context_metadata.get("blocks")
        if blocks:
            payload["blocks"] = blocks

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
                    # Deliver any media attachments after the text.
                    for att in outbound.attachments:
                        await self._send_attachment(channel_id, att, thread_ts)
                    # Voice reply: if the inbound was transcribed audio, reply
                    # with synthesized speech.
                    if outbound.context_metadata.get("voice_transcribed") and outbound.text:
                        asyncio.create_task(
                            self._send_voice_reply(channel_id, outbound.text, thread_ts)
                        )
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

    async def _maybe_transcribe_audio(self, msg: IncomingMessage) -> IncomingMessage:
        """Telegram-depth STT: size caps, language, provider, metadata tags."""
        from kazma_gateway.adapters.slack_stt import transcribe_message

        return await transcribe_message(
            msg, http=self._http, bot_token=self._bot_token or ""
        )

    async def _send_voice_reply(
        self, channel_id: str, text: str, thread_ts: str | None = None
    ) -> bool:
        """Synthesize *text* and upload as audio (TG-depth shared TTS path)."""
        if not self._http:
            return False
        from kazma_gateway.adapters.slack_stt import send_voice_reply

        return await send_voice_reply(
            http=self._http,
            bot_token=self._bot_token or "",
            channel_id=channel_id,
            text=text,
            thread_ts=thread_ts,
            headers_fn=self._headers,
        )

    async def _send_attachment(
        self,
        channel_id: str,
        att: Attachment,
        thread_ts: str | None = None,
    ) -> bool:
        """Upload a file to Slack via the modern async upload flow.

        Slack deprecated ``files.upload``; the current flow is:
        1. ``files.getUploadURLExternal`` → returns a one-time upload URL.
        2. POST the bytes to that URL (no auth header).
        3. ``files.completeUploadExternal`` → registers the file + shares it
           to the channel.

        Bytes come from ``att.data`` or are fetched from ``att.url``.
        """
        if self._http is None:
            return False

        data = att.data
        if data is None and att.url:
            try:
                resp = await self._http.get(
                    att.url,
                    timeout=30.0,
                    headers={"Authorization": f"Bearer {self._bot_token}"},
                )
                resp.raise_for_status()
                data = resp.content
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Slack] attachment fetch failed: %s", exc)
                return False
        if not data:
            return False

        safe_name = att.filename or f"kazma_{att.kind}"
        try:
            # 1. Get upload URL
            resp = await self._http.post(
                f"{_SLACK_API}/files.getUploadURLExternal",
                params={"filename": safe_name, "length": str(len(data))},
                headers=self._headers(),
            )
            resp.raise_for_status()
            up = resp.json()
            if not up.get("ok"):
                logger.error("[Slack] getUploadURLExternal failed: %s", up.get("error"))
                return False
            upload_url = up["upload_url"]
            file_id = up["file_id"]

            # 2. POST bytes to the upload URL (multipart, NO auth header).
            ul_resp = await self._http.post(
                upload_url,
                files={"file": (safe_name, data, att.mime or "application/octet-stream")},
            )
            ul_resp.raise_for_status()

            # 3. Complete upload + share to the channel.
            complete_body: dict[str, Any] = {
                "files": [{"id": file_id, "title": safe_name}],
                "channel_id": channel_id,
            }
            if thread_ts:
                complete_body["initial_comment"] = ""
            cp = await self._http.post(
                f"{_SLACK_API}/files.completeUploadExternal",
                json=complete_body,
                headers=self._headers(),
            )
            cp.raise_for_status()
            cd = cp.json()
            if not cd.get("ok"):
                logger.error("[Slack] completeUploadExternal failed: %s", cd.get("error"))
                return False
            logger.info("[Slack] uploaded %s (%d bytes) to %s", safe_name, len(data), channel_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Slack] attachment send failed: %s", type(exc).__name__)
            return False

    # ── Listen (abstract method) ────────────────────────────────────

    async def listen(
        self,
        queue: asyncio.Queue[IncomingMessage],
        shutdown_event: asyncio.Event,
    ) -> None:
        """Receive messages from Slack and enqueue them.

        Uses Socket Mode when an app_token is available (real-time,
        no channels:read scope needed). Falls back to polling otherwise.
        """
        if not self._bot_token:
            return

        self._http = httpx.AsyncClient(timeout=httpx.Timeout(_MAX_TIMEOUT + 5, connect=10.0))
        self._queue = queue
        self._shutdown = shutdown_event

        if self._app_token:
            logger.info("[Slack] Socket Mode enabled — using real-time event delivery")
            await self._listen_socket_mode()
        else:
            logger.info("[Slack] No app token — falling back to polling mode")
            await self._listen_polling()

        await self._http.aclose()

    # ── Socket Mode ─────────────────────────────────────────────────

    async def _listen_socket_mode(self) -> None:
        """Connect to Slack Socket Mode and receive events in real-time."""
        import websockets

        reconnect_delay = _SOCKET_RECONNECT_DELAY

        while not self._shutdown.is_set():
            try:
                # Get WSS URL from Slack
                resp = await self._http.post(
                    f"{_SLACK_API}/apps.connections.open",
                    headers={"Authorization": f"Bearer {self._app_token}"},
                )
                data = resp.json()
                if not data.get("ok"):
                    logger.error("[Slack] Socket Mode connection failed: %s", data.get("error", "unknown"))
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, _SOCKET_MAX_RECONNECT_DELAY)
                    continue

                wss_url = data.get("url", "")
                if not wss_url:
                    logger.error("[Slack] Socket Mode: no WSS URL returned")
                    await asyncio.sleep(reconnect_delay)
                    continue

                logger.info("[Slack] Socket Mode connecting to WSS endpoint")
                reconnect_delay = _SOCKET_RECONNECT_DELAY  # reset on successful connection

                async with websockets.connect(wss_url) as ws:
                    logger.info("[Slack] Socket Mode connected — listening for events")

                    while not self._shutdown.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                        except TimeoutError:
                            # No event in 30s — send ping to keep alive
                            try:
                                await ws.ping()
                            except Exception:
                                logger.debug("[Slack] Ping failed, connection may be stale")
                                break
                            continue

                        try:
                            msg = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            logger.debug("[Slack] Socket Mode: invalid JSON received")
                            continue

                        msg_type = msg.get("type", "")

                        if msg_type == "hello":
                            logger.info("[Slack] Socket Mode handshake confirmed")
                            continue

                        if msg_type == "disconnect":
                            logger.info("[Slack] Socket Mode disconnect received — reconnecting")
                            break

                        if msg_type == "interactive":
                            envelope_id = msg.get("envelope_id", "")
                            if envelope_id:
                                try:
                                    await ws.send(json.dumps({"envelope_id": envelope_id}))
                                except Exception:
                                    logger.debug("[Slack] Failed to ACK interactive envelope")

                            payload = msg.get("payload", {})
                            payload_type = payload.get("type", "")

                            if payload_type == "block_actions":
                                from kazma_gateway.adapters.slack_callbacks import (
                                    iter_block_actions,
                                    route_swarm_bus,
                                )

                                for value, action in iter_block_actions(payload):
                                    if action.kind in ("sys_install", "install_dep"):
                                        package_name = action.package_name
                                        from kazma_core.system.runtime_manager import (
                                            trigger_package_promotion,
                                        )

                                        await trigger_package_promotion(package_name)
                                        response_url = payload.get("response_url", "")
                                        if response_url:
                                            try:
                                                if action.kind == "sys_install":
                                                    updated_text = (
                                                        "[⏳ Installing package... please wait]"
                                                    )
                                                else:
                                                    updated_text = (
                                                        "⏳ *Installing ML dependencies "
                                                        "in the background...*"
                                                    )
                                                updated_blocks = [
                                                    {
                                                        "type": "section",
                                                        "text": {
                                                            "type": "mrkdwn",
                                                            "text": updated_text,
                                                        },
                                                    }
                                                ]
                                                async with httpx.AsyncClient() as client:
                                                    await client.post(
                                                        response_url,
                                                        json={
                                                            "text": updated_text,
                                                            "blocks": updated_blocks,
                                                            "replace_original": True,
                                                        },
                                                    )
                                            except Exception as exc:
                                                logger.warning(
                                                    "[Slack] Failed to update interactive card: %s",
                                                    exc,
                                                )
                                    elif action.kind == "swarm":
                                        try:
                                            route_swarm_bus(value)
                                        except Exception as exc:
                                            logger.warning(
                                                "[Slack] Swarm approval callback failed: %s",
                                                exc,
                                            )
                                    elif action.kind in (
                                        "hitl",
                                        "personality",
                                        "model_provider",
                                        "model_select",
                                    ) and action.text:
                                        # Graph HITL / pickers → enqueue synthetic command
                                        try:
                                            user = payload.get("user", {})
                                            channel = (
                                                payload.get("channel", {}) or {}
                                            ).get("id", "")
                                            user_id = user.get("id", "")
                                            # Audit G2b: interactive payloads (HITL approvals,
                                            # pickers) MUST respect the channel + user allowlists,
                                            # same as events. Previously any member who could see
                                            # an approval card could click Approve unchecked.
                                            if self._allowed_channels and channel and channel not in self._allowed_channels:
                                                logger.info(
                                                    "[Slack] Ignoring interaction from non-allowed channel %s",
                                                    channel,
                                                )
                                                continue
                                            if getattr(self, "_allowed_users", None) and (not user_id or user_id not in self._allowed_users):
                                                logger.info(
                                                    "[Slack] Ignoring interaction from non-allowed user %s",
                                                    user_id,
                                                )
                                                continue
                                            incoming = IncomingMessage(
                                                platform="slack",
                                                sender_id=f"slack:{user.get('id', '')}",
                                                text=action.text,
                                                context_metadata={
                                                    "channel_id": channel,
                                                    "user_id": user.get("id", ""),
                                                    "interaction": True,
                                                },
                                            )
                                            self._queue.put_nowait(incoming)
                                        except Exception as exc:
                                            logger.warning(
                                                "[Slack] Failed to enqueue interaction: %s",
                                                exc,
                                            )
                            continue

                        if msg_type == "events_api":
                            envelope_id = msg.get("envelope_id", "")
                            # ACK the event immediately
                            if envelope_id:
                                try:
                                    await ws.send(json.dumps({"envelope_id": envelope_id}))
                                except Exception:
                                    logger.debug("[Slack] Failed to ACK envelope")

                            # Parse the Slack event
                            payload = msg.get("payload", {})
                            event = payload.get("event", {})
                            incoming = self._parse_event(event)
                            if incoming is not None:
                                # Deduplicate: Slack sends both app_mention AND message
                                # events for the same mention. The underlying message
                                # shares the same ts (timestamp), so we skip duplicates.
                                cid = incoming.context_metadata.get("channel_id", "")
                                msg_ts = incoming.context_metadata.get("message_ts", "")
                                if msg_ts:
                                    key = (cid, msg_ts)
                                    if key in self._seen_events:
                                        continue
                                    self._seen_events.add(key)
                                    # Prune old entries periodically (keep last 500)
                                    if len(self._seen_events) > 500:
                                        self._seen_events = set(list(self._seen_events)[-250:])
                                # Enforce channel whitelist if configured
                                if self._allowed_channels and cid not in self._allowed_channels:
                                    logger.debug("[Slack] Event from non-whitelisted channel %s — skipping", cid)
                                    continue
                                # Audit G2c: enforce user allowlist if configured
                                if self._allowed_users:
                                    _uid = incoming.context_metadata.get("user_id", "")
                                    if not _uid or _uid not in self._allowed_users:
                                        logger.info("[Slack] Dropping event from non-allowed user %s — skipping", _uid)
                                        continue
                                # Voice: transcribe any audio attachment.
                                incoming = await self._maybe_transcribe_audio(incoming)
                                try:
                                    self._queue.put_nowait(incoming)
                                    logger.debug("[Slack] ← event: type=%s user=%s text=%.80s",
                                                 event.get("type", "?"),
                                                 event.get("user", "?"),
                                                 event.get("text", ""))
                                except asyncio.QueueFull:
                                    logger.warning("[Slack] Queue full — dropping event")
                            continue

                        logger.debug("[Slack] Socket Mode: unhandled message type: %s", msg_type)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if not self._shutdown.is_set():
                    logger.warning("[Slack] Socket Mode error: %s — reconnecting in %.1fs", exc, reconnect_delay)
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 2, _SOCKET_MAX_RECONNECT_DELAY)

    # ── Polling fallback ────────────────────────────────────────────

    async def _listen_polling(self) -> None:
        """Poll Slack for new messages (fallback when no app_token)."""
        # Fetch channel list on first poll
        await self._refresh_channels()

        while not self._shutdown.is_set():
            try:
                await self._poll_channels()
                should_exit = await self.jitter_sleep(self._shutdown)
                if should_exit:
                    break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("[Slack] Poll error: %s", exc)
                await asyncio.sleep(5)

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
            else:
                error = data.get("error", "unknown")
                if error == "missing_scope":
                    needed = data.get("needed", "")
                    logger.error(
                        "[Slack] Missing scopes for polling: needed=%s. "
                        "Add an app-level token (SLACK_APP_TOKEN) to use Socket Mode instead, "
                        "or add the required scopes in your Slack app settings.",
                        needed,
                    )
                else:
                    logger.error("[Slack] conversations.list failed: %s", error)
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
        raw_files = msg.get("files") or []
        # Polling (conversations.history) rarely includes files inline; Socket
        # Mode is the primary media path. Still honor any that are present.
        if not text and not raw_files:
            return

        user_id = msg.get("user", "")
        username = f"slack_{user_id}" if user_id else "slack_unknown"

        attachments: list[Attachment] = []
        for f in raw_files:
            mime = (f.get("mimetype") or "").lower()
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
                    filename=f.get("name", "") or f"slack_{f.get('id', 'file')}",
                    url=f.get("url_private_download") or f.get("url_private"),
                    meta={"file_id": f.get("id"), "source": "slack"},
                )
            )

        msg_text = text or (f"[{attachments[0].kind}]" if attachments else "")
        incoming = IncomingMessage(
            platform="slack",
            sender_id=f"slack:{channel_id}",
            text=msg_text,
            attachments=attachments,
            context_metadata={
                "channel_id": channel_id,
                "user_id": user_id,
                "thread_ts": msg.get("thread_ts"),
                "message_ts": msg.get("ts"),
                "username": username,
                "media": bool(attachments),
            },
        )
        # Voice: transcribe any audio attachment (polling path).
        incoming = await self._maybe_transcribe_audio(incoming)
        try:
            self._queue.put_nowait(incoming)
        except asyncio.QueueFull:
            logger.warning("[Slack] Queue full — dropping message from %s", user_id)
            return
        logger.debug("[Slack] ← from %s: %.80s", user_id, incoming.text)

    # ── Interactive builders (Telegram-parity static API) ───────────

    @staticmethod
    def build_approval_keyboard(request_id: str) -> list[dict[str, Any]]:
        """Slack Block Kit for graph HITL (shared callback IDs with Telegram)."""
        from kazma_gateway.adapters.slack_blocks import build_approval_blocks

        return build_approval_blocks(request_id)

    @staticmethod
    def build_personality_keyboard(personalities: list[str]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.slack_blocks import build_personality_blocks

        return build_personality_blocks(personalities)

    @staticmethod
    def build_provider_keyboard(providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.slack_blocks import build_provider_blocks

        return build_provider_blocks(providers)

    @staticmethod
    def build_model_keyboard(provider_name: str, models: list[str]) -> list[dict[str, Any]]:
        from kazma_gateway.adapters.slack_blocks import build_model_blocks

        return build_model_blocks(provider_name, models)

    # ── Lifecycle ───────────────────────────────────────────────────

    async def start(self, queue: asyncio.Queue[IncomingMessage], shutdown_event: asyncio.Event) -> None:
        """Override start with token check."""
        if not self._bot_token:
            logger.error("[Slack] Cannot start — no bot token")
            return
        self._queue = queue
        self._shutdown = shutdown_event
        await super().start(queue, shutdown_event)
