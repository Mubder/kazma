"""``/report`` in a chat app: a problem report, seen before it is sent (2026-11-14).

    /report <what happened>   shows what the report holds (and nothing private)
    /report send              sends the report shown last in this chat
    /report cancel            drops it

The report is ``kazma_core.support.problem_report``'s, the same as the web
app's and ``kazma report``'s; it carries the install's diagnostics, so it is a
gateway admin's (``slash_commands.changes_global_config``). A report shown and
not sent is forgotten after :data:`PENDING_S`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_gateway.gateway import IncomingMessage

logger = logging.getLogger(__name__)

__all__ = ["try_report_command"]

PENDING_S = 600.0
_pending: dict[str, tuple[float, dict[str, Any]]] = {}


def _key(msg: IncomingMessage) -> str:
    meta = msg.context_metadata or {}
    return f"{msg.sender_id}|{meta.get('chat_id') or meta.get('channel_id') or ''}"


def _summary(report: dict[str, Any]) -> str:
    readiness = report.get("readiness") or {}
    install = report.get("install") or {}
    lines = [
        "🐞 Problem report: this is what would be sent to Kazma's makers.",
        f"• Your words: {report.get('note') or '(none)'}",
        f"• Kazma {report.get('version')} ({report.get('commit') or 'no commit'}) on {report['system']['os']}, "
        f"Python {report['system']['python']}",
        f"• Install: {install.get('mode') or 'unknown'}, {len(install.get('parts') or {})} part(s) recorded",
        f"• Readiness: {'answered ' + str(readiness.get('status')) if readiness.get('answered') else 'no answer'}",
        f"• Open problems: {len(report.get('open_problems') or [])}; recent error lines: "
        f"{len(report.get('recent_errors') or [])}",
        "Nothing private: no chats, memories, documents, settings or keys; home folders, emails and IP "
        "addresses are taken out. The whole report is in Kazma's web app: ? beside the bell.",
        "Reply /report send to send it, or /report cancel.",
    ]
    return "\n".join(lines)


async def try_report_command(msg: IncomingMessage, send: Any) -> bool:
    """Handle ``/report``; *send(text)* replies in the chat. True when handled."""
    text = (msg.text or "").strip()
    head = text.split(maxsplit=1)[0].lower().split("@", 1)[0] if text else ""
    if head != "/report":
        return False
    from kazma_core.support import problem_report

    rest = text.split(maxsplit=1)[1].strip() if " " in text else ""
    key = _key(msg)
    now = time.monotonic()
    for old in [k for k, (at, _r) in _pending.items() if now - at > PENDING_S]:
        del _pending[old]
    try:
        if rest.lower() == "cancel":
            _pending.pop(key, None)
            await send("Dropped: nothing was sent.")
        elif rest.lower() == "send":
            held = _pending.pop(key, None)
            if held is None:
                await send("Nothing to send: write /report <what happened> first.")
            else:
                result = await asyncio.to_thread(problem_report.send, held[1])
                await send(("✅ " if result.sent else "⚠️ ") + problem_report.describe(result))
        elif not rest:
            await send("Usage: /report <what happened>. Kazma shows what the report holds before it is sent.")
        else:
            report = await asyncio.to_thread(problem_report.collect, rest, where=f"chat:{msg.platform}")
            _pending[key] = (now, report)
            await send(_summary(report))
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("[report] /report failed: %s", exc, exc_info=True)
        await send(f"⚠️ The report could not be made: {type(exc).__name__}")
    return True
