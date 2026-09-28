"""``/replay`` and ``/fork`` typed in the web chat.

They are chat commands on Telegram, Discord and Slack (the gateway's
``_handle_replay`` / ``_handle_fork``). The web chat had no branch for them,
so the text went to the model, which searched the codebase for what
"replay" means and improvised an answer (live 2026-09-28). The web answers
at once, without the model: how many steps of this chat were saved, and a
link that opens the Time Travel page on it -- where each snapshot can be
read before it is restored or branched from, which a bare command cannot
show.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

__all__ = ["is_time_travel_command", "time_travel_reply"]

_COMMAND = re.compile(r"^/(replay|fork)(?:\s|$)", re.IGNORECASE)


def is_time_travel_command(text: str) -> bool:
    """True for ``/replay`` or ``/fork`` with any arguments."""
    return bool(_COMMAND.match((text or "").strip()))


def _when(timestamp: str) -> str:
    """``2026-09-28 06:05 UTC`` from a snapshot's ISO timestamp."""
    stamp = str(timestamp or "")
    return f"{stamp[:10]} {stamp[11:16]} UTC" if len(stamp) >= 16 else stamp


def time_travel_reply(text: str, thread_id: str, recorder: Any) -> str:
    """The web chat's answer to a ``/replay`` or ``/fork`` command.

    Reads the snapshot store (blocking): call it off the event loop.
    """
    link = f"/replay?thread={quote(thread_id, safe='')}"
    command = _COMMAND.match(text.strip()).group(1).lower()
    platforms = (
        f"`/{command} <n>` as a chat command works on Telegram, Discord and "
        "Slack; here, the Time Travel page does it."
    )
    if recorder is None:
        return (
            "Time travel is unavailable on this install: the snapshot recorder "
            "did not start (the server log says why)."
        )
    snapshots = recorder.list_snapshots(thread_id)
    if not snapshots:
        return (
            "No steps of this chat have been saved yet: a snapshot is taken "
            f"each time the agent works through a step. {platforms}"
        )
    first, last = snapshots[0], snapshots[-1]
    return (
        f"This chat has {len(snapshots)} saved step(s), iterations "
        f"{first.iteration} to {last.iteration}, the latest from {_when(last.timestamp)}.\n\n"
        f"[Open this chat in Time Travel]({link}) to read any step, then "
        f"restore the chat to it or branch a new chat from it.\n\n{platforms}"
    )
