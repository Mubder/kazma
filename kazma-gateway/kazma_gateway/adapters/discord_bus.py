"""Discord platform adapter for the SwarmMessageBus.

Delivers structured Swarm Report cards and interactive HITL approval
requests to a Discord channel.  Uses Discord components v2 buttons so
operators can approve/reject directly.

An approval's outcome lives in ``kazma_core.swarm.shared_approvals``: one
row per request, decided once, whichever platform's button is pressed
first; ``handle_callback`` decides it and every card shows the outcome.
"""

from __future__ import annotations

import logging
from typing import Any

from kazma_core.swarm.bus import (
    ApprovalRequest,
    BusAdapter,
    BusMessage,
    SwarmReport,
)
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)


def _card_body(text: str) -> str:
    """An approval card without its "APPROVAL REQUIRED" header and its
    "times out" line: what a decided card keeps under its outcome."""
    return text.split(chr(10), 1)[-1].rsplit(chr(10), 1)[0]

__all__ = [
    "DiscordBusAdapter",
]


def _mention_safe(payload: dict[str, Any]) -> dict[str, Any]:
    """*payload* with every text it shows de-pinged and no mention parsed."""
    from kazma_gateway.adapters.discord_send import sanitize_outbound

    def walk(value: Any, key: str = "") -> Any:
        if isinstance(value, str):
            return sanitize_outbound(value) if key in _SHOWN_TEXT else value
        if isinstance(value, dict):
            return {k: walk(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, key) for v in value]
        return value

    safe = walk(payload)
    safe["allowed_mentions"] = {"parse": []}
    return safe


#: The payload fields Discord shows as message text (content, embeds, fields).
_SHOWN_TEXT = frozenset({"content", "title", "description", "name", "value", "text", "label"})

_APPROVAL_TIMEOUT = 60.0  # seconds
_DISCORD_API = "https://discord.com/api/v10"


class DiscordBusAdapter(BusAdapter):
    """Deliver bus messages to a Discord channel with rich formatting.

    Args:
        bot_token:   Discord bot token.
        channel_id:  Target channel ID (string of the numeric ID).

    Features:
        - SwarmReport cards with code-block output
        - Components v2 buttons for HITL approval
        - Callback routing to decide the shared approval
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._pending_approvals: set[str] = set()
        self._http: Any = None  # lazy httpx client

    @property
    def name(self) -> str:
        """Routing key for platform selection (ops-alert channels)."""
        return "discord"

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(
                base_url=_DISCORD_API,
                timeout=15.0,
                headers={"Authorization": f"Bot {self._bot_token}"},
                verify=shared_ssl_context(),
            )
        return self._http

    async def _post_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST a message to the target channel. Returns parsed JSON or None.

        Every post and edit of this sender goes through here or
        :meth:`_patch_message`, which neutralize mentions the way the chat
        adapter does (Swarm audit SW-S13: a worker's "@everyone" pinged the
        channel)."""
        payload = _mention_safe(payload)
        try:
            http = await self._ensure_http()
            resp = await http.post(f"/channels/{self._channel_id}/messages", json=payload)
            if resp.status_code >= 400:
                logger.warning("[DiscordBus] sendMessage %d: %s", resp.status_code, resp.text[:200])
                return None
            return resp.json()
        except Exception as exc:
            logger.warning("[DiscordBus] sendMessage failed: %s", exc)
            return None

    async def _patch_message(self, message_id: str, payload: dict[str, Any]) -> bool:
        """Edit an existing message (e.g. to show APPROVED/REJECTED). Whether
        Discord took the edit: a card left showing its buttons is a WARNING
        (SW-S15)."""
        payload = _mention_safe(payload)
        try:
            http = await self._ensure_http()
            resp = await http.patch(
                f"/channels/{self._channel_id}/messages/{message_id}",
                json=payload,
            )
            if resp.status_code >= 400:
                logger.warning("[DiscordBus] editMessage %d: %s", resp.status_code, resp.text[:200])
                return False
            return True
        except Exception as exc:
            logger.warning("[DiscordBus] editMessage failed: %s", exc)
            return False

    # ── BusAdapter interface ────────────────────────────────────────

    async def send(self, message: BusMessage) -> bool:
        """Deliver a log/status line as a plain message."""
        from kazma_core.observability.alert_card import is_operator_card

        content = message.content or ""
        if is_operator_card(content):
            text = content[:2000]
        else:
            icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(message.level, "•")
            text = f"{icon} **{message.worker_name}**: {content[:1500]}"
        return await self._post_message({"content": text[:2000]}) is not None

    async def send_report(self, report: SwarmReport) -> None:
        """Deliver a SwarmReport as a formatted card with a code block."""
        status_emoji = {
            "success": "✅",
            "error": "❌",
            "timeout": "⏱️",
            "rejected": "🚫",
        }.get(report.status, "📋")
        output = (report.output or "")[:1500]
        text = (
            f"{status_emoji} **{report.worker_name}** ({report.worker_role})\n"
            f"Status: `{report.status}` · {report.duration_ms:.0f}ms\n"
            f"```\n{output}\n```"
        )
        await self._post_message({"content": text[:2000]})

    async def send_alert(
        self,
        title: str,
        subsystem: str,
        status: str,
        reason: str,
        callback_id: str,
        button_text: str,
    ) -> None:
        """Deliver an alert card with Discord embed and buttons for dependency installation."""
        callback_data = callback_id
        if callback_data and not (callback_data.startswith("sys_install:") or callback_data.startswith("install_dependency:")):
            callback_data = f"sys_install:{callback_id}"

        description = (
            f"**Subsystem:** {subsystem}\n"
            f"**Status:** {status}\n"
            f"**Reason:** {reason}\n\n"
        )
        if callback_id and status != "ACTIVE":
            description += "Click below to trigger the remote installation safely."

        color = 0x55FF55 if status == "ACTIVE" else 0xFF5555

        embed = {
            "title": f"🚨 {title}" if status != "ACTIVE" else f"✅ {title}",
            "description": description[:2000],
            "color": color,
        }

        payload: dict[str, Any] = {"embeds": [embed]}

        if callback_id and status != "ACTIVE":
            payload["components"] = [
                {
                    "type": 1,  # ACTION_ROW
                    "components": [
                        {
                            "type": 2,  # BUTTON
                            "style": 1,  # PRIMARY (blue)
                            "label": button_text,
                            "custom_id": callback_data,
                        }
                    ],
                }
            ]

        await self._post_message(payload)

    async def request_approval(
        self, approval: ApprovalRequest, timeout: float | None = None
    ) -> bool:
        """Post an approval card with buttons and await the response.

        Buttons carry ``custom_id`` = ``swarm_approve_{task_id}`` /
        ``swarm_reject_{task_id}``.  The Discord adapter's interaction
        handler calls ``handle_callback`` to resolve the event.
        """
        if timeout is None:
            timeout = _APPROVAL_TIMEOUT
        text = (
            "⚠️ **APPROVAL REQUIRED**\n"
            f"**Worker:** {approval.worker_name}\n"
            f"**Task:** {approval.task_description[:300]}\n"
        )
        if approval.web_link:
            # Before the arguments block: the 2,000-character cut never reaches it.
            link = approval.web_link
            text += f"**Full call:** <{link}>\n" if link.startswith("http") else f"**Full call:** `{link}`\n"
        if approval.proposed_output:
            text += f"```\n{approval.proposed_output}\n```\n"
        text += f"\n_(times out in {int(timeout)}s; the tool does not run unless approved)_"

        # Discord components v2: a row with two buttons.
        components = [
            {
                "type": 1,  # ACTION_ROW
                "components": [
                    {
                        "type": 2,  # BUTTON
                        "style": 3,  # SUCCESS (green)
                        "label": "Approve",
                        "custom_id": f"swarm_approve_{approval.task_id}",
                    },
                    {
                        "type": 2,  # BUTTON
                        "style": 4,  # DANGER (red)
                        "label": "Reject",
                        "custom_id": f"swarm_reject_{approval.task_id}",
                    },
                ],
            }
        ]

        result = await self._post_message({"content": text[:2000], "components": components})
        msg_id = str(result.get("id", "")) if result else ""
        if not msg_id:
            logger.warning("[DiscordBus] Failed to deliver approval prompt for task %s", approval.task_id)
            return False

        # The request's one outcome, whatever platform decides it (SW-S04,
        # SW-S05): shared_approvals waits, and this card shows the outcome.
        from kazma_core.swarm import shared_approvals

        self._pending_approvals.add(approval.task_id)

        async def _edit(row: dict) -> None:
            await self._patch_message(msg_id, {
                "content": f"**{shared_approvals.outcome_text(row)}**\n{_card_body(text)}"[:2000],
                "components": []})

        try:
            return await shared_approvals.await_card(approval.task_id, _edit)
        finally:
            self._pending_approvals.discard(approval.task_id)

    # ── Callback resolution ─────────────────────────────────────────

    def approve(self, task_id: str, *, actor: str = "") -> None:
        """A human's Approve: decides the request if it is still pending."""
        from kazma_core.swarm.shared_approvals import resolve_from_callback

        resolve_from_callback(task_id, True, actor=actor, platform="discord")

    def reject(self, task_id: str, *, actor: str = "") -> None:
        """A human's Deny: decides the request if it is still pending (final)."""
        from kazma_core.swarm.shared_approvals import resolve_from_callback

        resolve_from_callback(task_id, False, actor=actor, platform="discord")

    def handle_callback(self, custom_id: str, *, actor: str = "") -> str | None:
        """Parse a Discord component custom_id and resolve the approval.

        Returns the task_id if handled, None otherwise.

        Called from the Discord adapter's interaction handler::

            bus_adapter.handle_callback(interaction["data"]["custom_id"])
        """
        from kazma_core.swarm.shared_approvals import decide_from_button

        return decide_from_button(custom_id, actor=actor, platform="discord")

    @property
    def pending_count(self) -> int:
        """Number of pending approval requests."""
        return len(self._pending_approvals)

    async def set_reaction(self, message_id: str, emoji: str) -> None:
        """Add a Unicode emoji reaction (Telegram parity for status feedback)."""
        if not message_id or not emoji:
            return
        try:
            http = await self._ensure_http()
            # Discord wants URL-encoded emoji for custom; unicode is fine raw.
            from urllib.parse import quote

            enc = quote(emoji)
            await http.put(
                f"/channels/{self._channel_id}/messages/{message_id}/reactions/{enc}/@me"
            )
        except Exception as exc:
            logger.debug("[DiscordBus] set_reaction failed: %s", exc)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
