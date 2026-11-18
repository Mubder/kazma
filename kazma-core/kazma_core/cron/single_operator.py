"""Scheduled tasks work on a single-operator install (Scheduled audit SCH-S03, decision D1).

Cron jobs are not separated per user: the operator's chat receives every
reminder, and a job id names one job whoever asks. Kazma is one operator per
install (``docs/THREAT_MODEL.md``), so scheduling stays whole there. On an
install with platform users -- or ``KAZMA_MULTI_USER`` -- every Scheduled
surface (the page, the ``/api/scheduled`` routes, the agent's scheduling
tools) refuses with :data:`MULTI_USER_REFUSAL` until per-user scheduling is
built. A settings store that cannot answer refuses too: scheduling never acts
for everyone by accident.

Reads and changes are also limited to the caller's tenant in production mode
(:func:`kazma_core.cron.scheduler.scheduled_tenant_filter`), so another
tenant's job id answers exactly like an unknown one.
"""

from __future__ import annotations

__all__ = ["MULTI_USER_REFUSAL", "NO_SPACE_REFUSAL", "REFUSAL_CODE", "scheduled_refusal"]

REFUSAL_CODE = "scheduled_single_operator"

MULTI_USER_REFUSAL = (
    "Scheduled tasks work on a single-operator install. This install has platform "
    "users, and scheduled tasks are not separated per user yet, so scheduling is "
    "off here."
)


#: Spaces enforced and the work tied to no space (a chat app's turn before
#: its chats are bound to one, stage 3): a job would belong to no one.
NO_SPACE_REFUSAL = (
    "Scheduled tasks work inside a space, and this chat is not tied to one yet."
)


def scheduled_refusal() -> str | None:
    """Why Scheduled refuses this work, or None.

    Once Spaces is enforced a job carries its space and the account that
    booked it, and fires as them (S1-14): work bound to a space is served
    there, work bound to none is refused (:data:`NO_SPACE_REFUSAL`).
    Otherwise :data:`MULTI_USER_REFUSAL` when Scheduled must refuse -- the
    one rule every single-operator surface asks
    (``security.single_operator``). Blocking: async callers use
    ``asyncio.to_thread``."""
    from kazma_core.spaces.scope import current_scope, spaces_enforced

    if spaces_enforced():
        return None if current_scope() is not None else NO_SPACE_REFUSAL
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(MULTI_USER_REFUSAL)
