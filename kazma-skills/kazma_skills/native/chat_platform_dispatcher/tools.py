"""Chat Platform Dispatcher Native Skill — tools for cross-channel notifications and HITL."""

from __future__ import annotations

import logging
from kazma_core.tools.send_message import send_message as _core_send_message

logger = logging.getLogger(__name__)


async def dispatch_notification(channel: str, recipient_id: str, text: str) -> str:
    """Send a notification message to a specific recipient or channel on Telegram, Discord, or Slack.

    Args:
        channel: The platform backend to use ('telegram', 'discord', 'slack').
        recipient_id: Platform-prefixed recipient ID (e.g. 'telegram:12345').
        text: The message body to deliver.

    Returns:
        The delivery status response.
    """
    if not recipient_id or not text:
        return "Error: Recipient ID and text must be provided."

    # Validate channels
    allowed_channels = {"telegram", "discord", "slack"}
    chan = channel.lower().strip()
    if chan not in allowed_channels:
        return f"Error: Channel '{channel}' not supported. Supported: {', '.join(allowed_channels)}"

    try:
        res = await _core_send_message(target_id=recipient_id, text=text, backend=chan)
        return f"Message dispatch status: {res}"
    except Exception as e:
        logger.error("Error dispatching message to %s: %s", recipient_id, e)
        return f"Error dispatching notification: {e}"


async def send_approval_request(
    channel: str,
    recipient_id: str,
    title: str,
    actions: list[str],
) -> str:
    """Dispatch an interactive approval card with actions/buttons for human verification (HITL).

    Formats a robust markdown button card block compatible across Telegram, Discord, and Slack.

    Args:
        channel: Platform backend ('telegram', 'discord', 'slack').
        recipient_id: Recipient target ID.
        title: The heading or prompt description requiring human review.
        actions: List of actions/buttons, e.g. ['Approve', 'Deny'].

    Returns:
        The dispatch status.
    """
    if not recipient_id or not title:
        return "Error: Recipient ID and title must be specified."

    if not actions:
        actions = ["Approve", "Deny"]

    # Construct premium interactive text representation
    card = [
        "🔔 *KAZMA INTERACTIVE HITL CARD*",
        "==================================",
        f"📝 *Request:* {title}",
        "",
        "👇 *Please select an action below:*",
    ]

    for act in actions:
        card.append(f"• [ {act.upper()} ]")
    card.append("==================================")

    formatted_card = "\n".join(card)

    try:
        res = await _core_send_message(target_id=recipient_id, text=formatted_card, backend=channel.lower().strip())
        return f"Approval request dispatched. Status: {res}"
    except Exception as e:
        logger.error("Error sending approval request card: %s", e)
        return f"Error sending approval card: {e}"


async def send_message(
    target_id: str,
    text: str,
    backend: str = "telegram",
) -> str:
    """Send a text message to the current conversation thread.

    Use this to reply to the user. The platform and delivery channel are handled automatically.

    Args:
        target_id: Platform-prefixed recipient ID.
        text: The message body to deliver.
        backend: The platform backend to use (default: 'telegram').

    Returns:
        The delivery status response.
    """
    try:
        return await _core_send_message(target_id=target_id, text=text, backend=backend)
    except Exception as e:
        logger.error("Error sending message to %s: %s", target_id, e)
        return f"Error sending message: {e}"
