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
    """Dispatch a platform-native HITL approval card (Telegram inline buttons).

    Telegram/Discord/Slack get the same Approve / Deny / Approve-for-task
    controls as a real graph interrupt. Do not draw fake ``[ APPROVE ]``
    text — those are not buttons. This card does not replace calling a
    danger tool (``file_write``, etc.); those already pause for HITL.

    Args:
        channel: Platform backend ('telegram', 'discord', 'slack').
        recipient_id: Recipient target ID (e.g. 'telegram:12345').
        title: The heading or prompt description requiring human review.
        actions: Hint list (Approve/Deny). The platform keyboard is fixed
            to the HITL action vocabulary the adapters already handle.

    Returns:
        The dispatch status.
    """
    from kazma_core.safety.hitl import get_current_thread_id
    from kazma_core.tools.send_message import get_current_delivery_target

    if not title:
        return "Error: Title must be specified."

    recipient_id = (recipient_id or "").strip() or (get_current_delivery_target() or "")
    if not recipient_id:
        return "Error: Recipient ID and title must be specified."

    allowed_channels = {"telegram", "discord", "slack"}
    chan = (channel or "telegram").lower().strip()
    if chan not in allowed_channels:
        return f"Error: Channel '{channel}' not supported. Supported: {', '.join(sorted(allowed_channels))}"

    if not actions:
        actions = ["Approve", "Deny"]

    request_id = (get_current_thread_id() or "").strip() or recipient_id

    # Same body shape as graph HITL so Telegram looks like the Web card,
    # plus typed fallbacks if the keyboard cannot be delivered.
    formatted_card = (
        f"⚠️ Approval required\n"
        f"{title}\n\n"
        f"Reply: hitl approve {request_id}\n"
        f"   or: hitl deny {request_id}"
    )

    try:
        res = await _core_send_message(
            target_id=recipient_id,
            text=formatted_card,
            backend=chan,
            hitl_approval={
                "request_id": request_id,
                "title": title,
                "actions": list(actions),
            },
        )
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
