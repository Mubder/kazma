"""Chat Platform Dispatcher Native Skill — cross-channel notifications and review requests."""

from __future__ import annotations

import logging
from kazma_core.tools.send_message import send_failed
from kazma_core.tools.send_message import send_message as _core_send_message
from kazma_core.agent.tool_refusal import RefusedBeforeEffect

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
        raise RefusedBeforeEffect("Error: Recipient ID and text must be provided.")

    # Validate channels
    allowed_channels = {"telegram", "discord", "slack"}
    chan = channel.lower().strip()
    if chan not in allowed_channels:
        raise RefusedBeforeEffect(f"Error: Channel '{channel}' not supported. Supported: {', '.join(allowed_channels)}")

    try:
        res = await _core_send_message(target_id=recipient_id, text=text, backend=chan)
        if send_failed(res):
            return res  # "Error: ...": a failed call, never "dispatch status"
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
    """Ask the operator, on a chat platform, to review something.

    The message is a question, not an approval card: it carries no HITL
    buttons and no ``hitl approve`` instruction, and nothing waits on it.
    The operator answers in the chat, as a new message. The card used to be
    bound to no interrupt but carried the real card's buttons and its
    "Reply: hitl approve <thread>" line: a prompt-injected turn could post
    "Approve reading the weather?", then pause on ``shell_exec``, and the
    operator's typed reply approved the real ``shell_exec`` (audit
    2026-10-26 AUD-060). A danger tool (``file_write``, ``shell_exec``...)
    asks for its own approval when it is called; this does not replace it.

    Args:
        channel: Platform backend ('telegram', 'discord', 'slack').
        recipient_id: Recipient target ID (e.g. 'telegram:12345').
        title: What the operator is asked to review.
        actions: The answers to offer (default Approve / Deny), listed in
            the message as words to reply with.

    Returns:
        The dispatch status.
    """
    from kazma_core.tools.send_message import get_current_delivery_target

    if not title:
        raise RefusedBeforeEffect("Error: Title must be specified.")

    recipient_id = (recipient_id or "").strip() or (get_current_delivery_target() or "")
    if not recipient_id:
        raise RefusedBeforeEffect("Error: Recipient ID and title must be specified.")

    allowed_channels = {"telegram", "discord", "slack"}
    chan = (channel or "telegram").lower().strip()
    if chan not in allowed_channels:
        raise RefusedBeforeEffect(f"Error: Channel '{channel}' not supported. Supported: {', '.join(sorted(allowed_channels))}")

    answers = [str(a).strip() for a in (actions or []) if str(a).strip()] or ["Approve", "Deny"]

    # A plain notice: no buttons (no ``hitl_approval`` markup) and no
    # ``hitl approve|deny`` command, so nothing in it can decide a real gate.
    notice = (
        f"❓ Kazma asks for your review\n"
        f"{title}\n\n"
        f"Answer in this chat ({' / '.join(answers)}). This message approves "
        f"nothing by itself: a tool that needs your approval asks with its own card."
    )

    try:
        res = await _core_send_message(
            target_id=recipient_id,
            text=notice,
            backend=chan,
        )
        if send_failed(res):
            # It said "dispatched" before the status that said it was not.
            return res
        return (
            f"Review request sent (a question, not an approval: the answer, if any, "
            f"comes as the operator's next message). Status: {res}"
        )
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
