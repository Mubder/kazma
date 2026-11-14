"""A swarm task started from a chat app sends its result back to that chat.

``dispatch_swarm`` records the chat a task came from (``source_delivery_target``,
``source_platform``) so the run supervisor can say when it stalls or fails
(``kazma_core.runs``). A task that FINISHED told no one: if the chat's turn
ended before the result arrived -- its waits ran out, or the model stopped
waiting -- the result sat in the Swarm panel until the owner asked (live
2026-11-14: a data-collection task from Telegram).

Now :func:`deliver_finished_task`, called by ``SwarmEngine._finalize_task``
for every ending but a pause, sends it, unless the chat's own turn already
read the result through ``check_swarm_task`` (:func:`note_result_handed`):
the delivery waits :data:`HANDOVER_GRACE_S` for a waiting call to pick it up
first. A result longer than :data:`INLINE_MAX_CHARS` goes as a Markdown file
with a short message, so a long report reaches the chat whole.

Only a task from a chat app (Telegram, Discord, Slack) is sent: a web turn's
delivery target is the operator's Telegram, kept for reminders (AGENTS
§16 B2), and the web page has the Swarm panel. A failed send is a WARNING;
the result stays in the Swarm panel either way.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "CHAT_PLATFORMS",
    "HANDOVER_GRACE_S",
    "INLINE_MAX_CHARS",
    "chat_target",
    "deliver_finished_task",
    "note_result_handed",
]

#: The platforms a result is sent back to (each has a send backend).
CHAT_PLATFORMS = frozenset({"telegram", "discord", "slack"})
#: How long a finished task waits for a ``check_swarm_task`` already waiting
#: on it to take the result first (that call polls every second).
HANDOVER_GRACE_S = 5.0
#: A result longer than this is sent as a file.
INLINE_MAX_CHARS = 3500
_HANDED_MAX = 500

_handed: OrderedDict[str, None] = OrderedDict()
_handed_lock = threading.Lock()


def note_result_handed(task_id: str) -> None:
    """The chat's turn read *task_id*'s result: it is not sent again."""
    with _handed_lock:
        _handed[task_id] = None
        _handed.move_to_end(task_id)
        while len(_handed) > _HANDED_MAX:
            _handed.popitem(last=False)


def _result_was_handed(task_id: str) -> bool:
    with _handed_lock:
        return task_id in _handed


def chat_target(meta: dict[str, Any]) -> tuple[str, str] | None:
    """(platform, target) for a task from a chat app, else None."""
    if str(meta.get("source") or "") != "chat":
        return None
    target = str(meta.get("source_delivery_target") or "")
    platform = target.split(":", 1)[0] if ":" in target else ""
    if platform not in CHAT_PLATFORMS:
        return None
    # A web turn's delivery target is the operator's Telegram (for reminders):
    # its result stays on the web page. A task recorded before the platform
    # was recorded has none, and is judged by its target alone.
    source_platform = str(meta.get("source_platform") or platform)
    if source_platform != platform:
        return None
    return platform, target


def _output_of(result: Any) -> str:
    output = (getattr(result, "aggregated_output", None)
              or getattr(result, "synthesized_output", None) or "")
    if not output:
        for wr in getattr(result, "worker_results", None) or ():
            if getattr(wr, "output", ""):
                output = wr.output
                break
    return str(output or "")


def _delivery_message(task: Any, result: Any) -> tuple[str, dict[str, Any] | None]:
    """The text to send for *task*'s ending, and the file to attach (or None)."""
    status = str(getattr(result, "status", "") or "")
    error = str(getattr(result, "error", "") or "")
    output = _output_of(result)
    minutes = float(getattr(result, "duration_seconds", 0.0) or 0.0) / 60
    head = f"Swarm task {task.id}"
    if status == "success" and output:
        head += f" finished ({minutes:.0f} min)."
    elif status in ("partial",) and output:
        head += f" finished with part of the work done ({minutes:.0f} min)."
    elif status == "cancelled":
        head += " was cancelled."
    else:
        head += f" ended without a result ({status or 'failed'})."
        if error:
            head += f"\nWhy: {error[:600]}"
    if not output:
        return head, None
    if len(output) <= INLINE_MAX_CHARS:
        return f"{head}\n\n{output}", None
    attachment = {
        "kind": "file",
        "filename": f"{task.id}-result.md",
        "mime": "text/markdown",
        "data": output.encode("utf-8"),
    }
    preview = output[:600].rstrip()
    return (f"{head} The full result ({len(output):,} characters) is in the attached file.\n\n"
            f"{preview}…"), attachment


async def _deliver(task: Any, result: Any, platform: str, target: str) -> None:
    if _result_was_handed(task.id):
        logger.info("[swarm] task %s: the chat's turn read the result; not sent again", task.id)
        return
    from kazma_core.tools.send_message import send_failed, send_message

    text, attachment = _delivery_message(task, result)
    kwargs: dict[str, Any] = {"attachments": [attachment]} if attachment else {}
    from kazma_core.runs.claims import WORK_FAILURES

    try:
        answer = await send_message(target, text, backend=platform, **kwargs)
    except WORK_FAILURES as exc:  # a platform failure must not escape a background task
        logger.warning("[swarm] task %s: sending the result to %s raised %s", task.id, platform,
                       type(exc).__name__, exc_info=exc)
        return
    if send_failed(answer):
        logger.warning("[swarm] task %s: %s did not take the result (%s); it is in the Swarm panel",
                       task.id, platform, str(answer)[:200])
        return
    note_result_handed(task.id)
    logger.info("[swarm] task %s: result sent to %s", task.id, platform)


def deliver_finished_task(task: Any, result: Any) -> bool:
    """Send *task*'s ending to the chat it came from, in the background.
    Returns whether a delivery was started. Never raises."""
    try:
        where = chat_target(dict(getattr(task, "metadata", None) or {}))
        if where is None:
            return False
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return False
        from kazma_core.background import spawn_background

        # Started after the grace: a check_swarm_task already waiting on the
        # task takes the result first. A stop during the grace sends nothing.
        spawn_background(_deliver(task, result, *where), name=f"swarm-result-to-chat:{task.id}",
                         start_after=HANDOVER_GRACE_S)
        return True
    except (RuntimeError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("[swarm] task %s: could not start sending the result: %s",
                       getattr(task, "id", "?"), type(exc).__name__, exc_info=True)
        return False
