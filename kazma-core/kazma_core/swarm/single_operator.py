"""The swarm works on a single-operator install (Swarm audit SW-S16, decision D1).

Swarm tasks are not separated per user: a task read, streamed, approved,
cancelled or retried by id is the install's, the active-task list is every
task, and a chat-dispatched task carries the chat and the sender it came
from. Kazma is one operator per install (``docs/THREAT_MODEL.md``), so the
swarm stays whole there; on an install with platform users -- or
``KAZMA_MULTI_USER`` -- every swarm surface (the Web API, the chat ``/swarm``
command, the agent's swarm tools) refuses with :data:`MULTI_USER_REFUSAL`
until per-user swarm work is built. A settings store that cannot answer
refuses too: the swarm never acts for everyone by accident.
"""

from __future__ import annotations

__all__ = ["MULTI_USER_REFUSAL", "swarm_refusal"]

MULTI_USER_REFUSAL = (
    "The swarm works on a single-operator install. This install has platform "
    "users, and swarm tasks are not separated per user yet, so the swarm is "
    "off here."
)


def swarm_refusal() -> str | None:
    """:data:`MULTI_USER_REFUSAL` when the swarm must refuse, else None --
    the one rule every single-operator surface asks
    (``security.single_operator``). Blocking: async callers use
    ``asyncio.to_thread``."""
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(MULTI_USER_REFUSAL)
