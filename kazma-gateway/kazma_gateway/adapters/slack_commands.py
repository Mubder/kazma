"""Slack native /kazma commands over Socket Mode, with private delayed replies."""

from __future__ import annotations

import asyncio
import contextlib
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


def safe_response_url(url: str, *, interaction: bool = False) -> bool:
    """Continuation endpoints are Slack-owned HTTPS URLs, never arbitrary hosts."""
    try:
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and parts.hostname in ("hooks.slack.com", "hooks.slack-gov.com")
            and not parts.username
            and not parts.password
            and parts.port in (None, 443)
            and parts.path.startswith("/actions/" if interaction else "/commands/")
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


def prepare_callback(adapter: Any, payload: dict[str, Any], key: str, user: str, channel: str) -> str | None:
    """Keep private button responses private using the fresh interaction URL."""
    url = str(payload.get("response_url") or "")
    if not key or not user or not channel or not safe_response_url(url, interaction=True):
        return None
    if adapter._native_replies.get(key):
        return None
    team = str((payload.get("team") or {}).get("id") or "")
    adapter._native_replies.put(key, url, team, user, channel, original=False)
    return key


async def settle_callback(adapter: Any, payload: dict[str, Any], reply_id: str | None = None) -> bool:
    """Clear clicked buttons without claiming that a gate has been approved."""
    url = str(payload.get("response_url") or "")
    if not safe_response_url(url, interaction=True):
        return False
    entry = adapter._native_replies.get(reply_id) if reply_id else None
    if reply_id and (
        not entry or entry.credential != url
        or entry.user != str((payload.get("user") or {}).get("id") or "")
        or entry.channel != str((payload.get("channel") or {}).get("id") or "")
    ):
        return False
    async with (entry.lock if entry else contextlib.AsyncExitStack()):
        if entry and entry.sent >= 5:
            return False
        body: dict[str, Any] = {
            "text": "Selection received; checking the current request.",
            "blocks": [],
            "replace_original": True,
        }
        if entry:
            body["response_type"] = "ephemeral"
        try:
            response = await adapter._http.post(url, json=body, timeout=2.5)
            response.raise_for_status()
            if response.text and response.text != "ok" and response.json().get("ok") is False:
                return False
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("[slack] callback card update failed (%s)", type(exc).__name__)
            return False
        if entry:
            entry.sent += 1
    return True


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
