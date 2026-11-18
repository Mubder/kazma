"""A message the person forwarded is someone else's words.

Telegram and WhatsApp tell a bot when a message was forwarded. Until 2026-11
its text reached the agent as the person's own message, and memory filed
"My name is Dana and I live in Oslo" -- Dana's words -- as the user's name
and home at the user's own trust (the review of the memory framework audit's
F3). A forwarded message's text now reaches the agent inside a data fence
(``prompt_fence.format_untrusted_block``, source ``forwarded_message``) under
a "[Forwarded message from <name>]" line: the model reads it as data, never
as instructions, and ``memory.own_words`` leaves both out of the user's
words. A forwarded voice note's transcript is fenced the same way, after it
is transcribed (Telegram's update path, ``whatsapp.WhatsAppAdapter``; the
review of F3, 2026-11-18: WhatsApp fenced only a caption, and a voice note
has none). Discord and Slack hand the bot a forwarded or shared message's text
in fields the adapters do not read, so nothing of it reaches the agent there.
"""

from __future__ import annotations

from typing import Any

from kazma_core.agent.attached_text import file_label

__all__ = ["forwarded_text", "telegram_forward_origin", "whatsapp_forwarded"]


def telegram_forward_origin(message: dict[str, Any]) -> str | None:
    """Who a Telegram *message* was forwarded from ("" when Telegram does not
    say), or None when it was not forwarded. Reads ``forward_origin`` (Bot
    API 7) and the older ``forward_*`` fields."""
    origin = message.get("forward_origin")
    if isinstance(origin, dict):
        kind = origin.get("type")
        if kind == "user":
            user = origin.get("sender_user") or {}
            return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or ""
        if kind == "hidden_user":
            return str(origin.get("sender_user_name") or "")
        chat = origin.get("sender_chat") or origin.get("chat") or {}
        return str(chat.get("title") or "")
    if message.get("forward_from"):
        user = message.get("forward_from") or {}
        return " ".join(p for p in (user.get("first_name"), user.get("last_name")) if p) or ""
    if message.get("forward_from_chat"):
        return str((message.get("forward_from_chat") or {}).get("title") or "")
    if message.get("forward_sender_name"):
        return str(message.get("forward_sender_name"))
    if message.get("forward_date"):
        return ""
    return None


def whatsapp_forwarded(raw: dict[str, Any]) -> bool:
    """Whether a WhatsApp Cloud API message says it was forwarded."""
    context = raw.get("context") or {}
    return isinstance(context, dict) and bool(context.get("forwarded") or context.get("frequently_forwarded"))


def forwarded_text(text: str, origin: str | None = "") -> str:
    """*text* as the agent is given a forwarded message: a line saying it was
    forwarded (and from whom, when the platform says), then the text inside
    a data fence."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    who = file_label(origin) if origin else ""
    stub = f"[Forwarded message from {who}]" if who else "[Forwarded message]"
    return f"{stub}\n{format_untrusted_block(text, source='forwarded_message')}"
