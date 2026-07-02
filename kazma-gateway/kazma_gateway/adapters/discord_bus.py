"""Discord platform adapter for the SwarmMessageBus.

Delivers structured Swarm Report cards and interactive HITL approval
requests to a Discord channel.  Uses Discord components v2 buttons so
operators can approve/reject directly.

Mirrors ``telegram_bus.py``: an in-process ``asyncio.Event`` per pending
approval, resolved by ``handle_callback`` when the Discord adapter
delivers an interaction (button press).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from kazma_core.swarm.bus import (
    ApprovalRequest,
    BusAdapter,
    BusMessage,
    SwarmReport,
)

logger = logging.getLogger(__name__)

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
        - Callback routing to resolve ``asyncio.Event``
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._pending_results: dict[str, bool] = {}
        self._pending_msg_ids: dict[str, str] = {}
        self._http: Any = None  # lazy httpx client

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(
                base_url=_DISCORD_API,
                timeout=15.0,
                headers={"Authorization": f"Bot {self._bot_token}"},
            )
        return self._http

    async def _post_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST a message to the target channel. Returns parsed JSON or None."""
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

    async def _patch_message(self, message_id: str, payload: dict[str, Any]) -> None:
        """Edit an existing message (e.g. to show APPROVED/REJECTED)."""
        try:
            http = await self._ensure_http()
            await http.patch(
                f"/channels/{self._channel_id}/messages/{message_id}",
                json=payload,
            )
        except Exception as exc:
            logger.debug("[DiscordBus] editMessage failed: %s", exc)

    # ── BusAdapter interface ────────────────────────────────────────

    async def send(self, message: BusMessage) -> None:
        """Deliver a log/status line as a plain message."""
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(message.level, "•")
        text = f"{icon} **{message.worker_name}**: {message.content[:1500]}"
        await self._post_message({"content": text[:2000]})

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

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        """Post an approval card with buttons and await the response.

        Buttons carry ``custom_id`` = ``swarm_approve_{task_id}`` /
        ``swarm_reject_{task_id}``.  The Discord adapter's interaction
        handler calls ``handle_callback`` to resolve the event.
        """
        text = (
            "⚠️ **APPROVAL REQUIRED**\n"
            f"**Worker:** {approval.worker_name}\n"
            f"**Task:** {approval.task_description[:300]}\n"
        )
        if approval.proposed_output:
            text += f"```\n{approval.proposed_output[:500]}\n```\n"
        text += f"\n_(auto-reject in {int(_APPROVAL_TIMEOUT)}s)_"

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

        event = asyncio.Event()
        self._pending_approvals[approval.task_id] = event
        if msg_id:
            self._pending_msg_ids[approval.task_id] = msg_id
        try:
            await asyncio.wait_for(event.wait(), timeout=_APPROVAL_TIMEOUT)
            approved = self._pending_results.get(approval.task_id, False)
        except TimeoutError:
            logger.warning("[DiscordBus] Approval timed out for task %s", approval.task_id)
            approved = False
        finally:
            self._pending_approvals.pop(approval.task_id, None)
            self._pending_results.pop(approval.task_id, None)

        # Edit the original message to show result (remove buttons).
        if msg_id:
            status = "✅ **APPROVED**" if approved else "❌ **REJECTED**"
            await self._patch_message(msg_id, {"content": status, "components": []})

        return approved

    # ── Callback resolution ─────────────────────────────────────────

    def approve(self, task_id: str) -> None:
        """Signal approval for a pending task."""
        if task_id in self._pending_approvals:
            self._pending_results[task_id] = True
            self._pending_approvals[task_id].set()

    def reject(self, task_id: str) -> None:
        """Signal rejection for a pending task."""
        if task_id in self._pending_approvals:
            self._pending_results[task_id] = False
            self._pending_approvals[task_id].set()

    def handle_callback(self, custom_id: str) -> str | None:
        """Parse a Discord component custom_id and resolve the approval.

        Returns the task_id if handled, None otherwise.

        Called from the Discord adapter's interaction handler::

            bus_adapter.handle_callback(interaction["data"]["custom_id"])
        """
        if custom_id.startswith("swarm_approve_"):
            task_id = custom_id[len("swarm_approve_"):]
            self.approve(task_id)
            return task_id
        if custom_id.startswith("swarm_reject_"):
            task_id = custom_id[len("swarm_reject_"):]
            self.reject(task_id)
            return task_id
        return None

    @property
    def pending_count(self) -> int:
        """Number of pending approval requests."""
        return len(self._pending_approvals)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None
