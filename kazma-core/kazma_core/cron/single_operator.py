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

__all__ = ["MULTI_USER_REFUSAL", "REFUSAL_CODE", "scheduled_refusal"]

REFUSAL_CODE = "scheduled_single_operator"

MULTI_USER_REFUSAL = (
    "Scheduled tasks work on a single-operator install. This install has platform "
    "users, and scheduled tasks are not separated per user yet, so scheduling is "
    "off here."
)


def scheduled_refusal() -> str | None:
    """:data:`MULTI_USER_REFUSAL` when Scheduled must refuse, else None -- the
    one rule every single-operator surface asks
    (``security.single_operator``). Blocking: async callers use
    ``asyncio.to_thread``."""
    from kazma_core.security.single_operator import single_operator_refusal

    return single_operator_refusal(MULTI_USER_REFUSAL)
