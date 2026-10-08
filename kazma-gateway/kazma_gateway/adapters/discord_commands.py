"""Discord application commands: fast ACK, common dispatch, ephemeral replies."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
from kazma_core.agent.command_catalog import BY_NAME, command_name, native_command_text

from kazma_gateway.adapters.discord_send import chunk_message, sanitize_outbound
from kazma_gateway.gateway import IncomingMessage, OutboundMessage

logger = logging.getLogger(__name__)

COMMAND = {
    "name": "kazma",
    "type": 1,
    "description": "Run a Kazma command (help, x help, ide help…)",
    "options": [
        {"name": "command", "type": 3, "description": "Command and arguments, for example: x help", "required": False}
    ],
}


async def register(adapter: Any, application: str) -> None:
    """Upsert only /kazma; never bulk replace an owner's other app commands."""
    status: dict[str, str] = {}
    scopes = sorted(adapter._allowed_guilds) or [""]
    for guild in scopes:
        key = guild or "global"
        endpoint = f"/applications/{application}" + (f"/guilds/{guild}" if guild else "") + "/commands"
        try:
            response = await adapter._http.post(endpoint, json=COMMAND)
            response.raise_for_status()
            actual = await adapter._http.get(endpoint)
            actual.raise_for_status()
            found = next((row for row in actual.json() if row.get("name") == "kazma"), None)
            options = found.get("options", []) if found else []
            matches = (
                found
                and found.get("type", 1) == 1
                and len(options) == 1
                and all(
                    options[0].get(field, False if field == "required" else None) == value
                    for field, value in COMMAND["options"][0].items()
                )
            )
            status[key] = "verified" if matches else "mismatch"
        except (httpx.HTTPError, ValueError) as exc:
            status[key] = f"failed ({type(exc).__name__})"
        if status[key] != "verified":
            logger.warning("[discord] /kazma registration %s: %s", key, status[key])
    adapter._receive.extra["native_commands"] = status


async def handle(adapter: Any, data: dict[str, Any]) -> None:
    """Authorize before dispatch and defer before any work or model call."""
    uid = str(((data.get("member") or {}).get("user") or data.get("user") or {}).get("id") or "")
    cid = str(data.get("channel_id") or "")
    iid, token = str(data.get("id") or ""), str(data.get("token") or "")
    app = str(data.get("application_id") or "")
    guild = str(data.get("guild_id") or "")
    raw = {"id": iid, "author": {"id": uid}, "channel_id": cid, "guild_id": guild}
    adapter._receive.message(iid, author_id=uid, where=f"server {guild}" if guild else "a direct message")

    async def ack(payload: dict[str, Any]) -> bool:
        try:
            response = await adapter._http.post(f"/interactions/{iid}/{token}/callback", json=payload, timeout=2.5)
            response.raise_for_status()
            return True
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("[discord] native command ACK failed (%s)", type(exc).__name__)
            adapter._receive.drop_event("processing_failed", raw)
            return False

    if adapter._native_replies.get(iid):
        adapter._receive.drop_event("duplicate", raw)
        return
    reason = None
    if not uid or not adapter.actor_allowed(uid):
        reason = "user_not_allowed" if adapter._allowed_users or adapter._allow_all else "no_allowlist"
    elif guild and adapter._allowed_guilds and guild not in adapter._allowed_guilds:
        reason = "server_not_allowed"
    elif not cid:
        reason = "no_channel"
    if reason:
        adapter._receive.drop_event(reason, raw)
        await ack(
            {
                "type": 4,
                "data": {"content": "Kazma refused this command: check the configured allowlists.", "flags": 64},
            }
        )
        return
    command = data.get("data") or {}
    options = command.get("options") or []
    name = str(command.get("name") or "")
    arguments = next((str(o.get("value") or "") for o in options if o.get("name") in ("command", "arguments")), "")
    text = native_command_text(name, arguments)
    if name != "kazma" and name not in BY_NAME:
        await ack({"type": 4, "data": {"content": "Unknown command. Use /kazma help.", "flags": 64}})
        adapter._receive.drop_event("unsupported_command", raw)
        return
    if (command_name(text) or "").rstrip("!") not in BY_NAME:
        await ack({"type": 4, "data": {"content": "Unknown command. Use /kazma help.", "flags": 64}})
        adapter._receive.drop_event("unsupported_command", raw)
        return
    if not iid or not app or not token or not iid.isdecimal() or not app.isdecimal() or "/" in token:
        adapter._receive.drop_event("processing_failed", raw)
        return
    if not await ack({"type": 5, "data": {"flags": 64}}):
        return
    adapter._native_replies.put(iid, token, app, uid, cid)
    metadata = {
        "user_id": uid,
        "channel_id": cid,
        "guild_id": guild or None,
        "message_id": iid,
        "native_reply_id": iid,
        "receive_key": iid,
    }
    incoming = IncomingMessage(
        sender_id=f"discord:{uid}:{cid}", text=text, platform="discord", context_metadata=metadata
    )
    try:
        queue = getattr(adapter, "_queue", None)
        if queue is None:
            raise asyncio.QueueFull
        queue.put_nowait(incoming)
        adapter._receive.note_passed_on(iid)
    except asyncio.QueueFull:
        adapter._receive.drop_event("queue_full", raw)
        await send(
            adapter,
            OutboundMessage(
                target_id=f"discord:{cid}", text="Kazma is busy. Please retry shortly.", context_metadata=metadata
            ),
        )


async def send(adapter: Any, outbound: OutboundMessage) -> bool:
    """Complete the deferred reply; credentials never leave this adapter."""
    entry = adapter._native_replies.get(str(outbound.context_metadata.get("native_reply_id") or ""))
    if (
        not entry
        or entry.user != str(outbound.context_metadata.get("user_id"))
        or entry.channel != str(outbound.context_metadata.get("channel_id"))
    ):
        logger.warning("[discord] native reply expired or recipient changed; nothing sent")
        return False
    async with entry.lock:
        chunks = chunk_message(sanitize_outbound(outbound.text or ""))
        if not chunks and outbound.attachments:
            chunks = ["File attached below."]
        for chunk_index, chunk in enumerate(chunks):
            base = f"/webhooks/{entry.application}/{entry.credential}"
            payload = {"content": chunk, "allowed_mentions": {"parse": []}}
            if entry.sent:
                payload["flags"] = 64
            if chunk_index == 0 and outbound.context_metadata.get("components"):
                payload["components"] = outbound.context_metadata["components"]
            try:
                response = await (
                    adapter._http.post(base, json=payload)
                    if entry.sent
                    else adapter._http.patch(base + "/messages/@original", json=payload)
                )
                response.raise_for_status()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("[discord] native reply failed (%s)", type(exc).__name__)
                return False
            entry.sent += 1
        # Files must stay on the private interaction, never its public channel.
        for attachment in outbound.attachments:
            body = attachment.data
            if body is None and attachment.url:
                from kazma_gateway.adapters.downloads import public_attachment_download

                try:
                    body = await public_attachment_download(attachment.url)
                except (httpx.HTTPError, ValueError, OSError):
                    return False
            if not body or len(body) > 8 * 1024 * 1024:
                logger.warning("[discord] native attachment unavailable or larger than 8 MiB")
                return False
            try:
                response = await adapter._http.post(
                    f"/webhooks/{entry.application}/{entry.credential}",
                    data={"payload_json": json.dumps({"flags": 64, "allowed_mentions": {"parse": []}})},
                    files={
                        "files[0]": (
                            attachment.filename or "kazma-file",
                            body,
                            attachment.mime or "application/octet-stream",
                        )
                    },
                )
                response.raise_for_status()
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("[discord] private attachment failed (%s)", type(exc).__name__)
                return False
    return True
