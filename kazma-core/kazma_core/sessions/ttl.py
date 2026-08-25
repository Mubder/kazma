"""Gateway SessionStore TTL — 5 minutes. Not a durable job lookup.

Cron reminders and HITL cards older than this MUST NOT resolve
``chat_id`` / ``user_id`` from SessionStore. Capture ``delivery_target``
at schedule time (see ``cron/scheduler.py``). Looking up the store for a
job older than the TTL is a footgun: the row is gone and routing silently
fails or hits ``{platform}:unknown``.
"""

from __future__ import annotations

import logging
logger = logging.getLogger(__name__)

__all__ = [
    "SESSION_TTL_SECONDS",
    "refuse_session_lookup_for_durable_job",
    "session_store_not_for_long_jobs",
]

# Must stay in lockstep with
# ``kazma_gateway.agent_handler.graph._session_ttl_seconds``.
SESSION_TTL_SECONDS = 300


def session_store_not_for_long_jobs(reason: str = "") -> str:
    extra = f" ({reason})" if reason else ""
    return (
        "SessionStore TTL is 5 minutes. Do not look up chat_id/user_id "
        f"from SessionStore for jobs longer than that{extra}. "
        "Use delivery_target captured at schedule time."
    )


def refuse_session_lookup_for_durable_job(
    *,
    job_kind: str,
    thread_id: str = "",
) -> None:
    """Fail-closed helper: log + return None semantics for a bad lookup.

    Callers that were about to hit SessionStore for a reminder / HITL
    resume / cron fire should call this instead of ``store.get``.
    """
    msg = session_store_not_for_long_jobs(
        f"{job_kind} thread={thread_id or '?'}"
    )
    logger.error("[session-ttl] %s", msg)
    return None
