"""A call that ran without its approval card says so, in one line.

YOLO, "allow this tool" and "approve for the task" let a call that would be
asked about run without a card. Each said so in its own words on one path
and not at all on another: the swarm bus logged a YOLO approval, the chat
graph logged nothing for a grant, and the MCP gate logged neither. The daily
digest counts this one line (``UNASKED_LINE``), so the owner reads what ran
without asking, chat by chat.

The call a card itself approved is not one of them. The approve route grants
the standing approval first and then resumes the turn, so the card's own
call runs under the grant it just made -- on 2026-10-31 a click on "Allow
all" for a ``file_write`` card was logged as a YOLO auto-approval of that
same ``file_write``. The route names the card's calls
(:func:`card_answered`), and the first run of each is not noted.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BY_TASK_GRANT",
    "BY_TOOL_GRANT",
    "BY_YOLO",
    "UNASKED_LINE",
    "card_answered",
    "note_ran_without_asking",
]

#: The standing approvals, as the line names them.
BY_YOLO = "yolo"
BY_TOOL_GRANT = "tool_grant"
BY_TASK_GRANT = "task_grant"

#: The one line the daily digest reads. Values never hold a space.
UNASKED_LINE = "[HITL] ran without asking: tool=%s thread=%s by=%s"

#: How long a card's calls wait for the resume that runs them. The route
#: resumes at once; a mark the resume never used must not hide a later call.
_CARD_MARK_S = 300.0

_lock = threading.Lock()
#: thread -> (when the card was answered, its calls by tool name).
_card_calls: dict[str, tuple[float, Counter]] = {}


def card_answered(thread_id: str, tools: Any, tool: str = "") -> None:
    """The calls a card on *thread_id* approved while granting a standing
    approval (YOLO, a tool or a task grant): the resume runs them under that
    grant, and they were asked.

    *tools* is the gate's list (``[{"name": ...}]``); a gate for one call may
    carry only *tool*.
    """
    from kazma_core.safety.hitl_grants import _is_tool_batch_label

    names = Counter(
        str(t["name"]) for t in (tools or []) if isinstance(t, dict) and t.get("name")
    )
    if not names and tool and not _is_tool_batch_label(tool):
        names[str(tool)] = 1
    if not thread_id or not names:
        return
    with _lock:
        _card_calls[str(thread_id)] = (time.monotonic(), names)


def _asked_on_its_card(tool_name: str, thread_id: str) -> bool:
    with _lock:
        entry = _card_calls.get(thread_id)
        if entry is None:
            return False
        at, names = entry
        if time.monotonic() - at > _CARD_MARK_S:
            del _card_calls[thread_id]
            return False
        if names[tool_name] <= 0:
            return False
        names[tool_name] -= 1
        if not +names:
            del _card_calls[thread_id]
        return True


def note_ran_without_asking(tool_name: str, thread_id: str, by: str) -> None:
    """Say that *tool_name* ran on *thread_id* without its card because of
    the standing approval *by*. YOLO is a WARNING (it skips every card of the
    chat), a grant INFO (the operator named the tool or the task)."""
    if _asked_on_its_card(str(tool_name), str(thread_id)):
        return
    level = logging.WARNING if by == BY_YOLO else logging.INFO
    logger.log(level, UNASKED_LINE, tool_name, thread_id, by)
