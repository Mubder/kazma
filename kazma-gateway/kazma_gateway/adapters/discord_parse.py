"""Discord MESSAGE_CREATE → IncomingMessage (Telegram-style pure helper)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.gateway import Attachment, IncomingMessage

__all__ = ["parse_message_create"]


def parse_message_create(data: dict[str, Any] | None) -> IncomingMessage | None:
    """Parse a Discord MESSAGE_CREATE event into an IncomingMessage.

    Captures attachments (images/files) alongside text. Media-only messages
    are accepted so screenshot-only uploads still reach the agent.
    """
    if not data:
        return None

    author = data.get("author", {})
    if author.get("bot"):
        return None

    content = (data.get("content") or "").strip()
    raw_attachments = data.get("attachments") or []
    embeds = data.get("embeds") or []

    if not content and not raw_attachments:
        return None

    channel_id = str(data.get("channel_id", ""))
    if not channel_id:
        return None

    guild_id = data.get("guild_id")
    user_id = str(author.get("id", ""))
    username = (
        author.get("username", "")
        or author.get("global_name", "")
        or f"discord_{user_id}"
    )
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
        sender_id=f"discord:{user_id}:{channel_id}" if user_id else f"discord:{channel_id}",
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
