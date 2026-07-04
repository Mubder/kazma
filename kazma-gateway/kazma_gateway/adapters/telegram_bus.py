"""Telegram platform adapter for the SwarmMessageBus.

Delivers structured Swarm Report cards and interactive HITL approval
requests to a Telegram chat.  Uses MarkdownV2 formatting and inline
keyboard buttons so operators can approve/reject directly without
manual reaction monitoring.

Extracted from ``kazma_core.swarm.bus`` to keep platform-specific
code out of kazma-core — maintaining platform neutrality.
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
_TELEGRAM_API = "https://api.telegram.org"


def _get_config_store() -> Any:
    """Return the shared ``ConfigStore`` (SQLite-backed settings store).

    Lazily imported to avoid a hard telegram_bus -> core import at module load.
    Tests may monkeypatch this attribute (``telegram_bus._get_config_store``)
    to inject an isolated store.
    """
    from kazma_core.config_store import get_config_store

    return get_config_store()


# ── Escape helpers ──────────────────────────────────────────────────────


def _escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters for Telegram."""
    chars = r"_*[]()~`>#+-=|{}.!"
    result = text
    for c in chars:
        result = result.replace(c, f"\\{c}")
    return result


# ── Adapter ─────────────────────────────────────────────────────────────


class TelegramBusAdapter(BusAdapter):
    """Deliver bus messages to a Telegram chat with rich formatting.

    Args:
        bot_token:  Telegram bot token (e.g. ``123:abc``).
        chat_id:    Target chat (group or DM).

    Features:
    - SwarmReport cards with monospace output blocks
    - Inline keyboard buttons ``[👍 Approve] [👎 Reject]`` for HITL
    - Callback query routing to resolve ``asyncio.Event``
    - Mobile-friendly card width
    """

    def __init__(self, bot_token: str, chat_id: int | str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._pending_approvals: dict[str, asyncio.Event] = {}
        self._pending_results: dict[str, bool] = {}
        self._http: Any = None  # lazy httpx client

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def _post(self, payload: dict[str, Any], method: str = "sendMessage") -> dict[str, Any] | None:
        """Send a request to the Telegram API.  Returns parsed JSON or None."""
        try:
            http = await self._ensure_http()
            resp = await http.post(
                f"{_TELEGRAM_API}/bot{self._bot_token}/{method}",
                json=payload,
            )
            return resp.json()
        except Exception as exc:
            logger.warning("[TelegramBus] %s failed: %s", method, exc)
            return None

    async def _edit_message(
        self, message_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> None:
        """Edit an existing message (for removing buttons after action)."""
        try:
            http = await self._ensure_http()
            await http.post(
                f"{_TELEGRAM_API}/bot{self._bot_token}/editMessageText",
                json={
                    "chat_id": self._chat_id,
                    "message_id": message_id,
                    "text": text[:4096],
                    "parse_mode": "MarkdownV2",
                    **(reply_markup or {}),
                },
            )
        except Exception as exc:
            logger.debug("[TelegramBus] editMessage failed: %s", exc)

    # ── Formatting ──────────────────────────────────────────────────

    def _format_report_card(self, report: SwarmReport) -> str:
        """Build a MarkdownV2 Swarm Report card."""
        icon = {
            "success": "✅", "error": "❌", "timeout": "⏰", "rejected": "🚫",
        }.get(report.status, "📍")

        lines = [
            "🐝 *SWARM REPORT*",
            "━━━━━━━━━━━━━━━━━━━━━",
            f"*Worker:* {_escape_md(report.worker_name)}",
            f"*Role:* {_escape_md(report.worker_role)}",
            f"*Status:* {icon} {_escape_md(report.status)}",
        ]
        if report.duration_ms > 0:
            lines.append(f"*Duration:* {report.duration_ms / 1000:.1f}s")
        lines.append("━━━━━━━━━━━━━━━━━━━━━")

        if report.output.strip():
            # Truncate for mobile readability
            output = report.output[:400]
            lines.append(f"```\n{output}\n```")

        return "\n".join(lines)

    # ── BusAdapter interface ────────────────────────────────────────

    async def send(self, message: BusMessage) -> None:
        icon = {"info": "ℹ️", "warn": "⚠️", "error": "❌", "success": "✅"}.get(
            message.level, "📍"
        )
        safe_name = _escape_md(message.worker_name)
        safe_content = _escape_md(message.content[:300])
        text = f"{icon} *{safe_name}* \\[{message.level}\\]\n{safe_content}"
        if message.worker_role:
            text += f"\n\\_Role: {_escape_md(message.worker_role)}\\_"
        await self._post({
            "chat_id": self._chat_id,
            "text": text[:4096],
            "parse_mode": "MarkdownV2",
        })

    async def send_report(self, report: SwarmReport) -> None:
        """Send a formatted Swarm Report card."""
        card = self._format_report_card(report)
        await self._post({
            "chat_id": self._chat_id,
            "text": card[:4096],
            "parse_mode": "MarkdownV2",
        })

    async def request_approval(self, approval: ApprovalRequest) -> bool:
        """Post an approval card with inline keyboard buttons.

        Buttons: ``[👍 Approve]`` ``[👎 Reject]``

        Waits for the callback query handler to call ``approve()``
        or ``reject()``, or times out after 60s.
        """
        safe_name = _escape_md(approval.worker_name)
        safe_task = _escape_md(approval.task_description[:200])
        safe_output = _escape_md(
            approval.proposed_output[:300] if approval.proposed_output else ""
        )

        text = (
            "⚠️ *APPROVAL REQUIRED*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"*Worker:* {safe_name}\n"
            f"*Task:* {safe_task}\n"
        )
        if safe_output:
            text += f"```\n{safe_output}\n```\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"\\(auto\\-reject in {int(_APPROVAL_TIMEOUT)}s\\)"

        # Inline keyboard
        reply_markup = {
            "inline_keyboard": [[
                {
                    "text": "👍 Approve",
                    "callback_data": f"swarm_approve_{approval.task_id}",
                },
                {
                    "text": "👎 Reject",
                    "callback_data": f"swarm_reject_{approval.task_id}",
                },
            ]]
        }

        result = await self._post({
            "chat_id": self._chat_id,
            "text": text[:4096],
            "parse_mode": "MarkdownV2",
            "reply_markup": reply_markup,
        })

        # Wait for callback
        event = asyncio.Event()
        self._pending_approvals[approval.task_id] = event
        try:
            await asyncio.wait_for(event.wait(), timeout=_APPROVAL_TIMEOUT)
            approved = self._pending_results.get(approval.task_id, False)
        except TimeoutError:
            logger.warning("[TelegramBus] Approval timed out for task %s", approval.task_id)
            approved = False
        finally:
            self._pending_approvals.pop(approval.task_id, None)
            self._pending_results.pop(approval.task_id, None)

        # Edit the original message to show result (remove buttons)
        if result and result.get("ok"):
            msg_id = result["result"]["message_id"]
            result_text = text.replace("⚠️ *APPROVAL REQUIRED*",
                                       "✅ *APPROVED*" if approved else "❌ *REJECTED*")
            await self._edit_message(msg_id, result_text)

        return approved

    # ── Callback handlers (called from telegram.py) ─────────────────

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

    def handle_callback(self, callback_data: str) -> str | None:
        """Parse a callback query and resolve the pending approval.

        Returns the task_id if handled, None otherwise.

        Called from the Telegram adapter's callback_query handler::

            bus_adapter.handle_callback(callback_query["data"])
        """
        if callback_data.startswith("swarm_approve_"):
            task_id = callback_data[len("swarm_approve_"):]
            self.approve(task_id)
            return task_id

        if callback_data.startswith("swarm_reject_"):
            task_id = callback_data[len("swarm_reject_"):]
            self.reject(task_id)
            return task_id

        return None

    @property
    def pending_count(self) -> int:
        """Number of pending approval requests."""
        return len(self._pending_approvals)

    # ── Emoji reactions + Rate limit feedback ──────────────────────

    async def set_reaction(self, message_id: int, emoji: str) -> None:
        """Set an emoji reaction on a message (e.g. 👍, ✅, ❌, ⏰)."""
        try:
            await self._post({
                "chat_id": self._chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
            }, method="setMessageReaction")
            logger.debug("[TelegramBus] Reaction set: %s on msg %d", emoji, message_id)
        except Exception as exc:
            logger.debug("[TelegramBus] setReaction failed: %s", exc)

    async def send_rate_limit_feedback(self, retry_after: int) -> None:
        """Notify the user that the bot is rate-limited."""
        await self._post({
            "chat_id": self._chat_id,
            "text": f"⏳ Rate limited — retrying in {retry_after}s...",
        })

    @staticmethod
    def model_list_text() -> str:
        """Return formatted model list for Telegram /model command."""
        try:
            from kazma_core.settings.model_registry import get_model_list_text
            return get_model_list_text("telegram")
        except Exception:
            return "Model registry unavailable."

    @staticmethod
    def get_active_chat_model() -> str | None:
        """Read the globally active chat model from ConfigStore.

        Resolution order:
          1. ``registry.active_chat_model`` (UI-selected active model)
          2. ``registry.active_model`` (ModelRegistry-managed fallback)
          3. ``None`` — no hardcoded fallback.

        Returns the model name string, or None if neither key is set.
        """
        try:
            store = _get_config_store()
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("[TelegramBus] ConfigStore unavailable: %s", exc)
            return None

        model = str(store.get("registry.active_chat_model", "") or "").strip()
        if model:
            return model

        model = str(store.get("registry.active_model", "") or "").strip()
        if model:
            return model

        return None

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
