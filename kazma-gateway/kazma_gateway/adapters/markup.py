"""Buttons on a chat app's message: one table for every platform (2026-11-14).

Each chat app carries its interactive controls in its own field of the send
context: Telegram ``reply_markup``, Discord ``components``, Slack ``blocks``,
WhatsApp ``whatsapp_buttons``. The approval card (graph interrupts, both in a
turn and after a resume) and the run supervisor's incident card used to name
the three fields at each site, so a new chat app needed every site found by
hand. They ask here:

* :func:`attach_markup` puts a built markup in the platform's field;
* :func:`approval_markup` builds Approve / Deny (/ Approve for task) for one
  approval card, with the labels the card chose (a public action names
  itself on the approving button and has no task approval);
* :func:`semantic_markup` builds one button per option of a clarify/confirm
  question.

A platform with no buttons gets None, and the card's own text (which always
says ``hitl approve <id>``) is the way to answer it.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["MARKUP_KEYS", "approval_markup", "attach_markup", "semantic_markup"]

#: The send-context field each platform reads its buttons from.
MARKUP_KEYS: dict[str, str] = {
    "telegram": "reply_markup",
    "discord": "components",
    "slack": "blocks",
    "whatsapp": "whatsapp_buttons",
}


def attach_markup(ctx: dict[str, Any], platform: str | None, markup: Any) -> dict[str, Any]:
    """*ctx* with *markup* in *platform*'s field (no markup, or no field: as it was)."""
    key = MARKUP_KEYS.get((platform or "").lower())
    if markup and key:
        ctx[key] = markup
    return ctx


def approval_markup(platform: str | None, request_id: str, *, details: str = "", **labels: Any) -> Any:
    """The approval card's buttons on *platform*, or None."""
    plat = (platform or "telegram").lower()
    try:
        if plat == "telegram":
            from kazma_gateway.adapters.telegram import TelegramAdapter

            return TelegramAdapter.build_approval_keyboard(request_id, **labels)
        if plat == "discord":
            from kazma_gateway.adapters.discord import DiscordAdapter

            return DiscordAdapter.build_approval_keyboard(request_id, **labels)
        if plat == "slack":
            from kazma_gateway.adapters.slack import SlackAdapter

            return SlackAdapter.build_approval_keyboard(request_id, details=details, **labels)
        if plat == "whatsapp":
            from kazma_gateway.adapters.whatsapp import WhatsAppAdapter

            return WhatsAppAdapter.build_approval_keyboard(request_id, **labels)
    except Exception as exc:  # noqa: BLE001 - the card's text still says how to answer
        logger.warning("Approval buttons could not be built for %s: %s", plat, exc, exc_info=True)
    return None


def semantic_markup(platform: str | None, request_id: str, question: str, options: list[dict[str, Any]]) -> Any:
    """One button per option of a clarify/confirm question on *platform*, or None."""
    plat = (platform or "telegram").lower()
    try:
        if plat == "telegram":
            from kazma_gateway.adapters.telegram_keyboards import build_semantic_keyboard

            return build_semantic_keyboard(request_id, options)
        if plat == "discord":
            from kazma_gateway.adapters.platform_keyboards import discord_semantic_components

            return discord_semantic_components(request_id, options)
        if plat == "slack":
            from kazma_gateway.adapters.platform_keyboards import slack_semantic_blocks

            return slack_semantic_blocks(request_id, question, options)
        if plat == "whatsapp":
            from kazma_gateway.adapters.whatsapp import WhatsAppAdapter

            return WhatsAppAdapter.build_semantic_markup(request_id, question, options)
    except Exception as exc:  # noqa: BLE001 - the card's text still says how to answer
        logger.warning("Option buttons could not be built for %s: %s", plat, exc, exc_info=True)
    return None
