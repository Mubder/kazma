"""Shared booking + publish logic for X posts.

``book_x_post`` is used by BOTH the chat tool (``x_schedule_post``) and the
Web Scheduled page / X Studio so the ToU policy is applied identically no
matter where a post is booked. HITL approval happens on the chat/agent path;
the human authoring a draft in the Web UI is the approval for that path.

``publish_x_post`` is the single immediate-post choke (chat ``x_post`` and
X Studio Post now). Policy first, then one ``POST /2/tweets``, then ledger.

Policy applied at booking:
  * connector enabled + configured (``can_post``) and scheduling not killed
  * ``evaluate_post`` fail-safes (length, mentions, cashtags, hashtags, dedupe,
    caps on already-fired posts)
  * future-only fire time
  * quota reservation — pending scheduled posts count toward the daily/monthly
    caps so the schedule cannot be used to exceed them
  * dedupe against pending drafts
"""

from __future__ import annotations

import re
import time
from typing import Any

__all__ = ["book_x_post", "delete_x_post", "publish_x_post"]


# The shared timing parser accepts one RECURRING form ("daily at 9am") and
# collapses it to the next single occurrence. That is correct for cron,
# which reschedules itself after each run, and wrong for scheduled X posts,
# where the fire loop is deliberately one-shot.
_RECURRING_TIMING = re.compile(r"^\s*(daily|hourly|weekly|every)\b", re.IGNORECASE)


def _parse_when(when: str) -> float:
    """Parse a timing expression into a future epoch. Raises ValueError.

    Recurring expressions are refused rather than silently degraded. The
    parser would happily turn "daily at 9am" into tomorrow at 09:00, the
    post would fire once, and the operator would believe they had scheduled
    a daily tweet -- a silent single-shot dressed as a recurrence.

    Refusing is also the right answer on the merits, not just a limitation
    of the fire loop. The post ledger enforces a duplicate window
    (``connectors.x.duplicate_window_days``, 30 by default) and X itself
    rejects identical tweets, so an honestly-implemented daily repeat of the
    same text would start failing on its second run. Recurring identical
    posts are also the pattern automation rules flag.

    Recurring work that genuinely needs to repeat belongs on the cron path,
    which reschedules itself correctly after every fire.

    Everything else is the one time model cron uses
    (:func:`kazma_core.cron.timing.resolve_timing`, Scheduled audit SCH-E09):
    the same string is the same instant on both paths -- RFC 3339 with ``Z``
    or an offset, a time without one read in the operator's zone, future and
    finite, a local time the clock skips or repeats refused with the zone
    named. Blocking (a settings read for the zone).
    """
    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.cron.timing import TimingError, looks_recurring, resolve_timing

    raw = (when or "").strip()
    if _RECURRING_TIMING.match(raw) or looks_recurring(raw):
        raise ValueError(
            f"'{raw}' is a recurring time, and scheduled X posts fire once. "
            "Give a one-off time instead ('2h', '2026-09-01T09:00', or an ISO "
            "timestamp). For something that must repeat, schedule a recurring "
            "task rather than a post -- X rejects identical tweets, so the "
            "same text cannot be republished on a schedule anyway."
        )
    zone, zone_name = resolve_cron_zone()
    try:
        resolved = resolve_timing(raw, zone=zone, zone_name=zone_name)
    except TimingError as exc:
        raise ValueError(str(exc)) from None
    epoch = resolved.first.timestamp()
    if epoch <= time.time():
        raise ValueError("The requested time is in the past. Pick a future time.")
    return epoch


def book_x_post(
    *,
    text: str,
    when: str,
    reply_to_id: str = "",
    tenant_id: str = "default",
    thread_id: str = "",
    delivery_target: str = "",
    idempotency_key: str = "",
    proposal_id: str = "",
) -> tuple[bool, dict[str, Any]]:
    """Validate + store a scheduled X post. Returns ``(ok, payload)``.

    ``payload`` is a JSON-ready dict: on success it carries ``scheduled``,
    ``id``, ``text``, ``fire_at``, ``tz``; on failure a single ``error``.
    """
    from kazma_core.cron.scheduler import resolve_cron_zone
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.publication_service import schedule
    from kazma_core.x_api.schedule import x_schedule_enabled

    if not x_schedule_enabled():
        return False, {"error": "X post scheduling is disabled (KAZMA_X_SCHEDULE / KAZMA_X_POST)."}
    if tenant_id != x_tenant_id():
        return False, {"error": "Scheduled publication tenant does not match authenticated context."}
    try:
        fire_at = _parse_when(when)
    except ValueError as exc:
        return False, {"error": str(exc)}
    _zone, zone_name = resolve_cron_zone()
    return schedule(text=text, fire_at=fire_at, reply_to_id=reply_to_id or "", idempotency_key=idempotency_key,
                    metadata={"thread_id": thread_id, "delivery_target": delivery_target,
                              "tz": zone_name, "proposal_id": proposal_id})


async def publish_x_post(
    *,
    text: str,
    reply_to_id: str = "",
    idempotency_key: str = "",
    origin: str = "immediate",
    metadata: dict[str, Any] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Validate + POST /2/tweets once. Returns ``(ok, payload)``.

    Writes are never retried (a dropped 201 plus a retry would double-post).
    The caller is responsible for HITL on the agent path; the Web studio
    treats the operator click as the approval, matching ``book_x_post``.
    """
    from kazma_core.x_api.publication_service import publish

    return await publish(text=text, reply_to_id=reply_to_id, idempotency_key=idempotency_key, origin=origin, metadata=metadata)


async def delete_x_post(*, tweet_id: str) -> tuple[bool, dict[str, Any]]:
    """DELETE /2/tweets/:id once. Returns ``(ok, payload)``.

    Same no-retry contract as publish. Web Studio treats the operator click
    as approval; the chat tool stays always-HITL outside this function.
    """
    from kazma_core.x_api.publication_service import delete

    return await delete(tweet_id=tweet_id)
