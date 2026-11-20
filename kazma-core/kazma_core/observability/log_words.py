"""What a log line at INFO or above may say of someone's words (Spaces S1-24).

The log is the admin's: the daily digest, the weekly report and the guard's
pages are built from it, and the admin reads it. Once Spaces is enforced,
what reaches the admin carries no member content (decision O1), so a log line
says how long a person's words are, never the words: a question, a tool's
arguments, a scheduled job's prompt. An install that does not enforce Spaces
logs them as it always did.

Every INFO-or-above line that quotes someone's words goes through here.
``tests/test_admin_signals_hold_no_content.py`` drives a member's turn through
the real server and reads every line it logged.

No I/O: whether Spaces is enforced is held in memory.
"""

from __future__ import annotations

from typing import Any

from kazma_core.spaces.scope import spaces_enforced

__all__ = ["args_for_log", "words_for_log"]


def words_for_log(text: Any, limit: int = 80) -> str:
    """*text* cut at *limit*, as before; once Spaces is enforced, only its
    length (``<41 chars>``)."""
    value = "" if text is None else str(text)
    if spaces_enforced():
        return f"<{len(value)} chars>"
    return value[:limit]


def args_for_log(args: Any, summary: str) -> str:
    """A tool call's arguments in a log line: *summary* (the caller's own
    rendering of *args*), as before; once Spaces is enforced, each argument's
    name and length, never its value."""
    if not spaces_enforced():
        return summary
    if not isinstance(args, dict):
        return words_for_log(args)
    return ", ".join(f"{key}={words_for_log(value)}" for key, value in args.items())
