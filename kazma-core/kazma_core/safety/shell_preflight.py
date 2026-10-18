"""Refuse a ``shell_exec`` call that cannot run, before anyone approves it.

Live 2026-10-18 (Telegram): the approval card for a ``shell_exec`` said
"BLOCKED: host shell is disabled by the execution policy" and still asked
the owner to approve it; the command also held ``|`` and ``2>/dev/null``,
which the tool refuses on every install. The owner approved a call that
could never run, and the refusal that followed ended the turn.

:func:`shell_refusal` is what the tool refuses from the policy and the
command's text alone. The chat tool worker and the swarm/IDE registry ask it
before an approval, and ``shell_exec`` asks it first, so the three say the
same thing. Refusals that need the filesystem (an unresolved binary, a path
outside the workspace) stay in the tool, which raises
:class:`~kazma_core.agent.tool_refusal.RefusedBeforeEffect` for them.
"""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)

__all__ = [
    "HOST_SHELL_OFF",
    "METACHARACTERS",
    "shell_refusal",
]

HOST_SHELL_OFF = (
    "host shell_exec is disabled by the execution policy. Use python_exec or the "
    "native file/git tools. An administrator must change the policy to enable host shell."
)
#: Rejected, never passed to a shell: they would bypass the binary allowlist.
METACHARACTERS = ("|", "&&", ";", ">", "<")
_METACHAR_REFUSAL = (
    "Shell metacharacters (|, &&, ;, >, <) are blocked for security — they bypass "
    "the safe-binary allowlist. Use python_exec for multi-step operations, or chain "
    "native tools (file_read, file_write, git_*) individually."
)


def _command_refusal(command: str) -> str | None:
    """What ``shell_exec`` refuses from the command's text alone, or None."""
    text = str(command or "")
    if not text.strip():
        return "Empty command"
    if any(op in text for op in METACHARACTERS):
        return _METACHAR_REFUSAL
    return None


def _shell_refusal_sync(command: str) -> str | None:
    """The policy (a settings read: blocking), then the command's text."""
    from kazma_core.safety.post_hitl import host_shell_allowed

    if not host_shell_allowed():
        return HOST_SHELL_OFF
    return _command_refusal(command)


async def shell_refusal(command: str) -> str | None:
    """:func:`_shell_refusal_sync` off the event loop."""
    return await asyncio.to_thread(_shell_refusal_sync, command)
