"""``/report`` in a chat app: a problem report, seen before it is sent (2026-11-14).

    /report <what happened>   shows the whole report, exactly as it would be sent
    /report send              sends the report shown last in this chat, unchanged
    /report cancel            drops it

The report is ``kazma_core.support.problem_report``'s, the same as the web
app's and ``kazma report``'s; it carries the install's diagnostics, so it is a
gateway admin's (``slash_commands.changes_global_config``). The chat is shown
every character that ``/report send`` sends: a summary, then the report's
text in parts of :data:`CHAT_PART_CHARS` (2026-11-15: the chat saw a summary,
and Send sent up to 30 log lines and every open problem's title nobody had
read). A report shown and not sent is forgotten after :data:`PENDING_S`; at
most :data:`MAX_PENDING` are kept.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from kazma_gateway.gateway import IncomingMessage

logger = logging.getLogger(__name__)

__all__ = ["CHAT_PART_CHARS", "try_report_command"]

PENDING_S = 600.0
MAX_PENDING = 64
#: The most of the report's text one chat message holds, inside a code block
#: (a backtick is escaped in the text, so nothing can end the block early):
#: under every platform's limit even in UTF-16 units (Telegram's 4,096).
CHAT_PART_CHARS = 2000
_pending: dict[str, tuple[float, dict[str, Any]]] = {}


def _report_parts(text: str, size: int = CHAT_PART_CHARS) -> list[str]:
    """*text* in parts of at most *size* characters, cut at line ends where it
    can be; joined in order, the parts are *text*."""
    parts: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if current and len(current) + len(line) > size:
            parts.append(current)
            current = ""
        while len(line) > size:  # one line longer than a part: cut it
            parts.append(line[:size])
            line = line[size:]
        current += line
    if current:
        parts.append(current)
    return parts


def _key(msg: IncomingMessage) -> str:
    meta = msg.context_metadata or {}
    return f"{msg.sender_id}|{meta.get('chat_id') or meta.get('channel_id') or ''}"


def _summary(report: dict[str, Any], parts: int) -> str:
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
        "addresses are taken out.",
        f"The whole report follows in {parts} part{'s' if parts != 1 else ''}, every character that would "
        "be sent, read one after another.",
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
            _pending.pop(key, None)
            parts = _report_parts(problem_report.preview(report))
            await send(_summary(report, len(parts)))
            for number, part in enumerate(parts, 1):
                await send(f"Part {number} of {len(parts)}:\n```\n{part.rstrip(chr(10))}\n```")
            # Kept only once the whole text was shown.
            _pending[key] = (now, report)
            while len(_pending) > MAX_PENDING:
                del _pending[next(iter(_pending))]
            await send("Reply /report send to send exactly this, or /report cancel.")
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        logger.warning("[report] /report failed: %s", exc, exc_info=True)
        await send(f"⚠️ The report could not be made: {type(exc).__name__}")
    return True
