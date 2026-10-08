"""Slack native /kazma commands over Socket Mode, with private delayed replies."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlsplit

import httpx
from kazma_core.agent.command_catalog import BY_NAME, command_name, native_command_text
from websockets.exceptions import ConnectionClosed

from kazma_gateway.adapters.slack_send import chunk_message, sanitize_outbound
from kazma_gateway.gateway import IncomingMessage, OutboundMessage

logger = logging.getLogger(__name__)


def safe_response_url(url: str) -> bool:
    """Continuation endpoints are Slack-owned HTTPS URLs, never arbitrary hosts."""
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname in ("hooks.slack.com", "hooks.slack-gov.com")
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and parts.path.startswith("/commands/")
            and not parts.fragment
        )
    except ValueError:
        return False


async def handle(adapter: Any, ws: Any, envelope: dict[str, Any]) -> None:
    """ACK within the reader; all actual command execution remains on the queue."""
    payload = envelope.get("payload") or {}
    iid = str(envelope.get("envelope_id") or "")
    uid, cid = str(payload.get("user_id") or ""), str(payload.get("channel_id") or "")
    team = str(payload.get("team_id") or "")
    adapter._receive.event("slash_commands")
    adapter._receive.message(iid, author_id=uid, where=f"channel {cid}")

    async def ack(text: str | None = None) -> bool:
        response: dict[str, Any] = {"envelope_id": iid}
        if text and envelope.get("accepts_response_payload", True):
            response["payload"] = {"text": text, "response_type": "ephemeral"}
        try:
            await asyncio.wait_for(ws.send(json.dumps(response)), timeout=2.5)
            return True
        except (TimeoutError, ConnectionClosed, OSError, ValueError) as exc:
            logger.warning("[slack] native command ACK failed (%s)", type(exc).__name__)
            adapter._receive.drop("processing_failed", message_id=iid, author_id=uid, channel_id=cid)
            return False

    if adapter._native_replies.get(iid):
        await ack()
        adapter._receive.drop("duplicate", message_id=iid, author_id=uid, channel_id=cid)
        return
    reason = None
    if adapter._allowed_teams and team not in adapter._allowed_teams:
        reason = "team_not_allowed"
    elif not cid:
        reason = "no_channel"
    elif adapter._allowed_channels and cid not in adapter._allowed_channels:
        reason = "channel_not_allowed"
    elif not uid or not adapter.actor_allowed(uid):
        reason = "user_not_allowed" if adapter._allowed_users or adapter._allow_all else "no_allowlist"
    if reason:
        adapter._receive.drop(reason, message_id=iid, author_id=uid, channel_id=cid)
        await ack("Kazma refused this command: check the configured allowlists.")
        return
    name = str(payload.get("command") or "").lstrip("/")
    text = native_command_text(name, str(payload.get("text") or ""))
    if (name != "kazma" and name not in BY_NAME) or (command_name(text) or "").rstrip("!") not in BY_NAME:
        adapter._receive.drop("unsupported_command", message_id=iid, author_id=uid, channel_id=cid)
        await ack("Unknown command. Use /kazma help.")
        return
    url = str(payload.get("response_url") or "")
    if not iid or not safe_response_url(url):
        adapter._receive.drop("processing_failed", message_id=iid, author_id=uid, channel_id=cid)
        await ack("Kazma could not validate the private reply endpoint. Check the Slack app configuration.")
        return
    if not await ack("Kazma accepted the command. Working…"):
        return
    adapter._native_replies.put(iid, url, team, uid, cid, original=bool(envelope.get("accepts_response_payload", True)))
    metadata = {
        "user_id": uid,
        "channel_id": cid,
        "team_id": team,
        "native_reply_id": iid,
        "receive_key": iid,
        "message_ts": iid,
    }
    incoming = IncomingMessage(sender_id=f"slack:{uid}", text=text, platform="slack", context_metadata=metadata)
    try:
        queue = getattr(adapter, "_queue", None)
        if queue is None:
            raise asyncio.QueueFull
        queue.put_nowait(incoming)
        adapter._receive.note_passed_on(iid)
    except asyncio.QueueFull:
        adapter._receive.drop("queue_full", message_id=iid, author_id=uid, channel_id=cid)
        await send(
            adapter,
            OutboundMessage(
                target_id=f"slack:{cid}", text="Kazma is busy. Please retry shortly.", context_metadata=metadata
            ),
        )


async def send(adapter: Any, outbound: OutboundMessage) -> bool:
    """Slack permits five response-url messages for thirty minutes."""
    entry = adapter._native_replies.get(str(outbound.context_metadata.get("native_reply_id") or ""))
    if (
        not entry
        or entry.user != str(outbound.context_metadata.get("user_id"))
        or entry.channel != str(outbound.context_metadata.get("channel_id"))
    ):
        logger.warning("[slack] native reply expired or recipient changed; nothing sent")
        return False
    text = outbound.text or ""
    if outbound.attachments:
        text += "\n\nFiles cannot be delivered privately through Slack slash commands. Open this chat in Kazma web, or request the file using an ordinary message command."
    chunks = chunk_message(sanitize_outbound(text))
    async with entry.lock:
        if entry.sent + len(chunks) > 5:
            logger.warning("[slack] native reply exceeds Slack's five-message limit; nothing sent")
            return False
        for chunk_index, chunk in enumerate(chunks):
            payload: dict[str, Any] = {
                "text": chunk,
                "response_type": "ephemeral",
                "replace_original": entry.sent == 0 and entry.original,
            }
            if chunk_index == 0 and outbound.context_metadata.get("blocks"):
                payload["blocks"] = outbound.context_metadata["blocks"]
            try:
                response = await adapter._http.post(entry.credential, json=payload)
                response.raise_for_status()
                if response.text and response.text != "ok":
                    data = response.json()
                    if data.get("ok") is False:
                        return False
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("[slack] native reply failed (%s)", type(exc).__name__)
                return False
            entry.sent += 1
    return not bool(outbound.attachments)
