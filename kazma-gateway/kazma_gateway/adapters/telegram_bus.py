"""Telegram platform adapter for the SwarmMessageBus.

Delivers structured Swarm Report cards and interactive HITL approval
requests to a Telegram chat.  Uses MarkdownV2 formatting and inline
keyboard buttons so operators can approve/reject directly without
manual reaction monitoring.

Extracted from ``kazma_core.swarm.bus`` to keep platform-specific
code out of kazma-core — maintaining platform neutrality.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from kazma_core.swarm.bus import (
    ApprovalRequest,
    BusAdapter,
    BusMessage,
    SwarmReport,
)
from kazma_core.http_tls import shared_ssl_context

logger = logging.getLogger(__name__)

__all__ = [
    "TelegramBusAdapter",
]

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


def _safe_slice_md(text: str, limit: int = 4096) -> str:
    """Safely slice markdown text at limit without leaving dangling escape slashes."""
    sliced = text[:limit]
    slashes = 0
    for char in reversed(sliced):
        if char == "\\":
            slashes += 1
        else:
            break
    if slashes % 2 != 0:
        sliced = sliced[:-1]
    return sliced


def _format_operator_card_md(content: str) -> str:
    """MarkdownV2: bold the first line (``[Guard]`` / ``[Ops]`` / ``[System]``)."""
    lines = (content or "")[:4096].split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        esc = _escape_md(line)
        out.append(f"*{esc}*" if i == 0 else esc)
    return "\n".join(out)


# ── Adapter ─────────────────────────────────────────────────────────────


class TelegramBusAdapter(BusAdapter):
    """Deliver bus messages to a Telegram chat with rich formatting.

    Args:
        bot_token:  Telegram bot token (e.g. ``123:abc``).
        chat_id:    Target chat (group or DM).

    Features:
    - SwarmReport cards with monospace output blocks
    - Inline keyboard buttons ``[👍 Approve] [👎 Reject]`` for HITL
    - Callback query routing to decide the shared approval
    - Mobile-friendly card width
    """

    def __init__(self, bot_token: str, chat_id: int | str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id
        self._pending_approvals: set[str] = set()
        self._http: Any = None  # lazy httpx client

    @property
    def name(self) -> str:
        """Routing key for platform selection (ops-alert channels)."""
        return "telegram"

    # ── HTTP helpers ────────────────────────────────────────────────

    async def _ensure_http(self) -> Any:
        if self._http is None:
            import httpx
            self._http = httpx.AsyncClient(timeout=15.0, verify=shared_ssl_context())
        return self._http

    async def _post(self, payload: dict[str, Any], method: str = "sendMessage") -> dict[str, Any] | None:
        """Send a request to the Telegram API. Returns parsed JSON or None."""
        try:
            http = await self._ensure_http()
            resp = await http.post(
                f"{_TELEGRAM_API}/bot{self._bot_token}/{method}",
                json=payload,
            )
            data = resp.json()
            if isinstance(data, dict):
                if data.get("ok"):
                    return data
                # Check for Markdown parse failure to attempt fallback
                desc = str(data.get("description", ""))
                err_code = data.get("error_code")
                logger.warning(
                    "[TelegramBus] %s returned ok=false (code=%s, desc=%s)",
                    method, err_code, desc,
                )
                if payload.get("parse_mode") and ("parse" in desc.lower() or err_code == 400 or "entities" in desc.lower()):
                    logger.info("[TelegramBus] Retrying %s with plain text fallback", method)
                    fallback_payload = dict(payload)
                    fallback_payload.pop("parse_mode", None)
                    raw_text = fallback_payload.get("text", "")
                    if isinstance(raw_text, str):
                        # Strip Markdown escape slashes if present
                        fallback_payload["text"] = re.sub(r"\\([_*\[\]()~`>#+\-=|{}.!])", r"\1", raw_text)[:4096]
                    fallback_resp = await http.post(
                        f"{_TELEGRAM_API}/bot{self._bot_token}/{method}",
                        json=fallback_payload,
                    )
                    fallback_data = fallback_resp.json()
                    if isinstance(fallback_data, dict) and fallback_data.get("ok"):
                        return fallback_data
                    logger.warning("[TelegramBus] Plain text fallback also failed for %s", method)
            return data
        except Exception as exc:
            # httpx exception strings embed the full request URL — including
            # bot<token>. Log the exception class only so a connect/timeout
            # error can never write the bot token to log files (the main
            # Telegram adapter already follows this practice).
            logger.warning("[TelegramBus] %s failed: %s", method, type(exc).__name__)
            return None

    async def _edit_message(
        self, message_id: int, text: str, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Edit an existing message (for removing buttons after action)."""
        payload = {
            "chat_id": self._chat_id,
            "message_id": message_id,
            "text": _safe_slice_md(text, 4096),
            "parse_mode": "MarkdownV2",
            **(reply_markup or {}),
        }
        return await self._post(payload, method="editMessageText")

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
            # Truncate for mobile readability and escape codeblock specials (\ and `)
            output = report.output[:400].replace("\\", "\\\\").replace("`", "\\`")
            lines.append(f"```\n{output}\n```")

        return "\n".join(lines)

    # ── BusAdapter interface ────────────────────────────────────────

    async def send(self, message: BusMessage) -> bool:
        # Operator cards are finished copy. Do not clip at 300 chars and
        # do not wrap them in a second worker header. Bold the header
        # line and the source line (Guard / Ops / System).
        from kazma_core.observability.alert_card import is_operator_card

        raw = message.content or ""
        if is_operator_card(raw):
            text = _format_operator_card_md(raw)
        else:
            text = _escape_md(raw[:3500])
        result = await self._post({
            "chat_id": self._chat_id,
            "text": _safe_slice_md(text, 4096),
            "parse_mode": "MarkdownV2",
        })
        return bool(result and result.get("ok"))

    async def send_report(self, report: SwarmReport) -> None:
        """Send a formatted Swarm Report card."""
        card = self._format_report_card(report)
        await self._post({
            "chat_id": self._chat_id,
            "text": _safe_slice_md(card, 4096),
            "parse_mode": "MarkdownV2",
        })

    async def send_alert(
        self,
        title: str,
        subsystem: str,
        status: str,
        reason: str,
        callback_id: str,
        button_text: str,
    ) -> None:
        """Deliver an alert card, with an install button only if one applies.

        A plain status alert (RAM pressure, disk pressure) has no package to
        install and gets no button. The card used to promise one either way --
        "Click below to trigger the remote installation safely" over an empty
        keyboard -- which is how an operator ends up clicking "resolve" on a
        memory warning and starting an install.
        """
        callback_data = callback_id
        if callback_data and not (callback_data.startswith("sys_install:") or callback_data.startswith("install_dependency:")):
            callback_data = f"sys_install:{callback_id}"
        has_button = bool(callback_id) and status != "ACTIVE"

        # If it's a sys_install callback data, use the requested HTML-formatted text block
        if callback_data and "sys_install:" in callback_data:
            text = (
                f"🚨 <b>{title}</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"<b>Subsystem:</b> {subsystem}\n"
                f"<b>Status:</b> {status}\n"
                f"<b>Reason:</b> {reason}\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                + ("Click below to trigger the remote installation safely."
                   if has_button else "")
            )
            parse_mode = "HTML"
        else:
            safe_title = _escape_md(title)
            safe_subsystem = _escape_md(subsystem)
            safe_status = _escape_md(status)
            safe_reason = _escape_md(reason)
            text = (
                f"🚨 *{safe_title}*\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                f"*Subsystem:* {safe_subsystem}\n"
                f"*Status:* {safe_status}\n"
                f"*Reason:* {safe_reason}\n"
                "━━━━━━━━━━━━━━━━━━━━━\n"
                + ("Click below to trigger the remote installation safely\\."
                   if has_button else "")
            )
            parse_mode = "MarkdownV2"

        payload: dict[str, Any] = {
            "chat_id": self._chat_id,
            "text": text[:4096],
            "parse_mode": parse_mode,
        }

        # Include inline keyboard ONLY if we have a callback_id and status is not ACTIVE
        if callback_id and status != "ACTIVE":
            from kazma_gateway.adapters.callback_store import encode_callback_data

            payload["reply_markup"] = {
                "inline_keyboard": [[
                    {
                        "text": button_text,
                        "callback_data": encode_callback_data(callback_data),
                    }
                ]]
            }

        await self._post(payload)

    async def request_approval(
        self, approval: ApprovalRequest, timeout: float | None = None
    ) -> bool:
        """Post an approval card with inline keyboard buttons.

        Buttons: ``[👍 Approve]`` ``[👎 Reject]``

        Waits for the callback query handler to call ``approve()``
        or ``reject()``, or times out after configurable timeout.
        """
        if timeout is None:
            timeout = _APPROVAL_TIMEOUT
        safe_name = _escape_md(approval.worker_name)
        safe_task = _escape_md(approval.task_description[:200])
        # Inside a MarkdownV2 ``` code block only `` ` `` and `` \ `` need
        # escaping. _escape_md escapes the full spec (_*[]()~>#+=|{}.!), so
        # every other char rendered with a literal backslash — making the HITL
        # approval card hard to read (e.g. `result=\{a:1\}`). Escape only the
        # two characters Telegram requires inside preformatted blocks (audit).
        _raw_output = approval.proposed_output or ""
        safe_output = _raw_output.replace("\\", "\\\\").replace("`", "\\`")

        text = (
            "⚠️ *APPROVAL REQUIRED*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            f"*Worker:* {safe_name}\n"
            f"*Task:* {safe_task}\n"
        )
        if approval.web_link:
            # A MarkdownV2 link: inside its (...) only ")" and "\" are escaped,
            # inside `...` only "`" and "\" (the same two as the block below).
            link = approval.web_link.replace("\\", "\\\\")
            if link.startswith("http"):
                target = link.replace(")", "\\)")
                text += f"*Full call:* [open in Kazma]({target})\n"
            else:
                shown = link.replace("`", "\\`")
                text += f"*Full call:* `{shown}`\n"
        if safe_output:
            text += f"```\n{safe_output}\n```\n"
        text += "━━━━━━━━━━━━━━━━━━━━━\n"
        text += f"\\(times out in {int(timeout)}s; the tool does not run unless approved\\)"

        # Inline keyboard
        from kazma_gateway.adapters.callback_store import encode_callback_data

        reply_markup = {
            "inline_keyboard": [[
                {
                    "text": "👍 Approve",
                    "callback_data": encode_callback_data(f"swarm_approve_{approval.task_id}"),
                },
                {
                    "text": "👎 Reject",
                    "callback_data": encode_callback_data(f"swarm_reject_{approval.task_id}"),
                },
            ]]
        }

        result = await self._post({
            "chat_id": self._chat_id,
            "text": _safe_slice_md(text, 4096),
            "parse_mode": "MarkdownV2",
            "reply_markup": reply_markup,
        })

        if not result or not result.get("ok"):
            logger.warning(
                "[TelegramBus] Failed to deliver approval prompt for task %s",
                approval.task_id,
            )
            return False

        # The request's one outcome, whatever platform decides it (SW-S04,
        # SW-S05): shared_approvals waits, and this card shows the outcome.
        from kazma_core.swarm import shared_approvals

        msg_id = result.get("result", {}).get("message_id")
        self._pending_approvals.add(approval.task_id)

        async def _edit(row: dict) -> None:
            if msg_id:
                header = _escape_md(shared_approvals.outcome_text(row))
                body = text.rsplit(chr(10), 1)[0]  # the card without its "times out" line
                await self._edit_message(msg_id, body.replace("⚠️ *APPROVAL REQUIRED*", f"*{header}*"))

        try:
            return await shared_approvals.await_card(approval.task_id, _edit)
        finally:
            self._pending_approvals.discard(approval.task_id)

    # ── Callback handlers (called from telegram.py) ─────────────────

    def approve(self, task_id: str, *, actor: str = "") -> None:
        """A human's Approve: decides the request if it is still pending."""
        from kazma_core.swarm.shared_approvals import resolve_from_callback

        resolve_from_callback(task_id, True, actor=actor, platform="telegram")

    def reject(self, task_id: str, *, actor: str = "") -> None:
        """A human's Deny: decides the request if it is still pending (final)."""
        from kazma_core.swarm.shared_approvals import resolve_from_callback

        resolve_from_callback(task_id, False, actor=actor, platform="telegram")

    def handle_callback(self, callback_data: str, *, actor: str = "") -> str | None:
        """Parse a callback query and resolve the pending approval.

        Returns the task_id if handled, None otherwise.

        Called from the Telegram adapter's callback_query handler::

            bus_adapter.handle_callback(callback_query["data"])
        """
        from kazma_gateway.adapters.callback_store import decode_callback_data

        callback_data = decode_callback_data(callback_data)

        from kazma_core.swarm.shared_approvals import decide_from_button

        return decide_from_button(callback_data, actor=actor, platform="telegram")

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
            # Exception class only — the string form embeds bot<token> (see _post).
            logger.debug("[TelegramBus] setReaction failed: %s", type(exc).__name__)

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
