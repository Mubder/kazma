"""What /undo and /edit do to a chat's messages (Memory audit M09).

The gateway's ``/undo`` and ``/edit`` rewrote the checkpoint inline; the
transforms live here so the rewrite and its memory side
(``memory.forget.rewind_memory``) can be exercised through a real graph.
"""

from __future__ import annotations

from typing import Any

__all__ = ["drop_last_answer", "replace_last_answer"]


def _role(message: Any) -> str | None:
    return message.get("role") if isinstance(message, dict) else None


def drop_last_answer(messages: list[Any]) -> list[Any] | None:
    """*messages* without the last assistant answer, and the tool calls and
    results of that turn just before it; None when there is no answer."""
    out = list(messages)
    i = len(out) - 1
    while i >= 0:
        if _role(out[i]) == "assistant":
            out.pop(i)
            # The tool results of this turn, then the assistant message whose
            # tool_calls produced them.
            j = i - 1
            while j >= 0:
                r = _role(out[j])
                if r == "tool":
                    out.pop(j)
                    j -= 1
                    continue
                if r == "assistant" and out[j].get("tool_calls"):
                    out.pop(j)
                break
            return out
        i -= 1
    return None


def replace_last_answer(messages: list[Any], corrected: str) -> tuple[list[Any], int | None]:
    """*messages* with the last plain assistant answer replaced by
    *corrected* (its tool calls dropped), and that answer's index; with no
    answer to replace, *corrected* appended and None."""
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        msg = out[i]
        if _role(msg) == "assistant":
            # Keep tool_calls-only intermediate assistants out of "last reply".
            if msg.get("tool_calls") and not (msg.get("content") or "").strip():
                continue
            out[i] = {**msg, "role": "assistant", "content": corrected}
            out[i].pop("tool_calls", None)
            return out, i
    out.append({"role": "assistant", "content": corrected})
    return out, None
