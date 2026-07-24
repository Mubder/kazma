"""Slack platform adapter for the SwarmMessageBus.

Delivers structured Swarm Report cards and interactive HITL approval
requests to a Slack channel.  Uses Block Kit buttons so operators can
approve/reject directly.

Mirrors ``telegram_bus.py``: an in-process ``asyncio.Event`` per pending
approval, resolved by ``handle_callback`` when the Slack adapter
delivers an interactive action payload (button click).
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

__all__ = [
    "SlackBusAdapter",
]

_APPROVAL_TIMEOUT = 60.0  # seconds
_SLACK_API = "https://slack.com/api"


class SlackBusAdapter(BusAdapter):
    """Deliver bus messages to a Slack channel with rich formatting.

    Args:
        bot_token:   Slack bot token (xoxb-...).
        channel_id:  Target channel ID.

    Features:
        - SwarmReport cards with Block Kit formatting
        - Interactive ``actions`` block buttons for HITL approval
        - Callback routing to resolve ``asyncio.Event``
    """

    def __init__(self, bot_token: str, channel_id: str) -> None:
        self._bot_token = bot_token
        self._channel_id = channel_id
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._pending_results: dict[str, bool] = {}
        self._pending_msg_ts: dict[str, str] = {}
        self._http: Any = None  # lazy httpx client

    # ── HTTP helpers ────────────────────────────────────────────────

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bot_token}",
            "Content-Type": "application/json",
        }

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def _post_message(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """POST a message via chat.postMessage. Returns parsed JSON or None."""
        payload = {**payload, "channel": self._channel_id}
        try:
            http = await self._ensure_http()
            resp = await http.post(
                f"{_SLACK_API}/chat.postMessage",
                json=payload,
                headers=self._headers(),
            )
            data = resp.json()
            if not data.get("ok"):
                logger.warning("[SlackBus] chat.postMessage error: %s", data.get("error"))
                return None
            return data
        except Exception as exc:
            logger.warning("[SlackBus] chat.postMessage failed: %s", exc)
            return None

    async def _update_message(self, ts: str, payload: dict[str, Any]) -> None:
        """Edit an existing message via chat.update."""
        payload = {**payload, "channel": self._channel_id, "ts": ts}
        try:
            http = await self._ensure_http()
            await http.post(f"{_SLACK_API}/chat.update", json=payload, headers=self._headers())
        except Exception as exc:
            logger.debug("[SlackBus] chat.update failed: %s", exc)

    # ── BusAdapter interface ────────────────────────────────────────

    async def send(self, message: BusMessage) -> None:
        """Deliver a log/status line as a plain message."""
        icon = {"info": "ℹ️", "warning": "⚠️", "error": "🔴"}.get(message.level, "•")
        text = f"{icon} *{message.worker_name}*: {message.content[:2500]}"
        await self._post_message({"text": text[:2900], "mrkdwn": True})

    async def send_report(self, report: SwarmReport) -> None:
        """Deliver a SwarmReport as a Block Kit card."""
        status_emoji = {
            "success": "✅",
            "error": "❌",
            "timeout": "⏱️",
            "rejected": "🚫",
        }.get(report.status, "📋")
        output = (report.output or "")[:2500]
        text = (
            f"{status_emoji} *{report.worker_name}* ({report.worker_role})\n"
            f"Status: `{report.status}` · {report.duration_ms:.0f}ms\n"
            f"```\n{output}\n```"
        )
        await self._post_message({"text": text[:2900], "mrkdwn": True})

    async def send_alert(
        self,
        title: str,
        subsystem: str,
        status: str,
        reason: str,
        callback_id: str,
        button_text: str,
    ) -> None:
        """Deliver an alert card with Block Kit buttons for dependency installation."""
        callback_data = callback_id
        if callback_data and not (callback_data.startswith("sys_install:") or callback_data.startswith("install_dependency:")):
            callback_data = f"sys_install:{callback_id}"

        if callback_data and "sys_install:" in callback_data:
            # Use interactive Block Kit layout containing warning accessory image and context section
            blocks = [
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"🚨 *{title}*\n━━━━━━━━━━━━━━━━━━━━━\n*Subsystem:* {subsystem}\n*Status:* `{status}`\n*Reason:* {reason}"
                    },
                    "accessory": {
                        "type": "image",
                        "image_url": "https://api.slack.com/img/blocks/b_labs/danger.png",
                        "alt_text": "Warning Accessory"
                    }
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "Click below to trigger the remote installation safely."
                        }
                    ]
                }
            ]
        else:
            text = (
                f"🚨 *{title}*\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"*Subsystem:* {subsystem}\n"
                f"*Status:* {status}\n"
                f"*Reason:* {reason}\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                "Click below to trigger the remote installation safely."
            )
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}
            ]

        # Include button actions ONLY if we have a callback_id and status is not ACTIVE
        if callback_id and status != "ACTIVE":
            blocks.append({
                "type": "actions",
                "block_id": f"install_actions_{callback_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": button_text},
                        "style": "primary",
                        "value": callback_data,
                        "action_id": callback_data,
                    }
                ]
            })

        await self._post_message({"text": f"ALERT: {title}", "blocks": blocks})

    async def request_approval(
        self, approval: ApprovalRequest, timeout: float = _APPROVAL_TIMEOUT
    ) -> bool:
        """Post an approval card with Block Kit buttons and await the response.

        Button values carry ``swarm_approve_{task_id}`` /
        ``swarm_reject_{task_id}``.  The Slack adapter's interactive
        payload handler calls ``handle_callback`` to resolve the event.
        """
        text = (
            "⚠️ *APPROVAL REQUIRED*\n"
            f"*Worker:* {approval.worker_name}\n"
            f"*Task:* {approval.task_description[:300]}\n"
        )
        if approval.proposed_output:
            text += f"```\n{approval.proposed_output[:500]}\n```\n"
        text += f"\n_(auto-reject in {int(timeout)}s)_"

        # Block Kit: a section block + an actions block with two buttons.
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}},
            {
                "type": "actions",
                "block_id": f"hitl_actions_{approval.task_id}",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "style": "primary",
                        "value": f"swarm_approve_{approval.task_id}",
                        "action_id": f"swarm_approve_{approval.task_id}",
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Reject"},
                        "style": "danger",
                        "value": f"swarm_reject_{approval.task_id}",
                        "action_id": f"swarm_reject_{approval.task_id}",
                    },
                ],
            },
        ]

        result = await self._post_message({"text": text[:2900], "blocks": blocks})
        msg_ts = str(result.get("ts", "")) if result else ""

        event = asyncio.Event()
        self._pending_approvals[approval.task_id] = event
        if msg_ts:
            self._pending_msg_ts[approval.task_id] = msg_ts
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            approved = self._pending_results.get(approval.task_id, False)
        except TimeoutError:
            logger.warning("[SlackBus] Approval timed out for task %s", approval.task_id)
            approved = False
        finally:
            self._pending_approvals.pop(approval.task_id, None)
            self._pending_results.pop(approval.task_id, None)
            self._pending_msg_ts.pop(approval.task_id, None)

        # Edit the original message to show result (remove buttons).
        if msg_ts:
            status = "✅ *APPROVED*" if approved else "❌ *REJECTED*"
            await self._update_message(msg_ts, {"text": status, "blocks": []})

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

    def handle_callback(self, action_value: str) -> str | None:
        """Parse a Slack action value/action_id and resolve the approval.

        Returns the task_id if handled, None otherwise.

        Called from the Slack adapter's interactive payload handler::

            bus_adapter.handle_callback(action["value"])
        """
        if action_value.startswith("swarm_approve_"):
            task_id = action_value[len("swarm_approve_"):]
            self.approve(task_id)
            return task_id
        if action_value.startswith("swarm_reject_"):
            task_id = action_value[len("swarm_reject_"):]
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
