"""Unified conversation directory — one season, every mouth.

Web, Telegram, Discord, Slack, and TUI list and switch the same
SessionManager rows. LangGraph ``thread_id`` is the durable identity;
platform delivery lives in SessionStore (never in graph state).
"""

from __future__ import annotations

from kazma_core.sessions.ttl import (
    SESSION_TTL_SECONDS,
    refuse_session_lookup_for_durable_job,
    session_store_not_for_long_jobs,
)
from kazma_core.sessions.directory import (
    SessionEntry,
    bind_sender_to_thread,
    canonical_web_session,
    create_named_session,
    enrich_summary,
    find_mouth_thread,
    format_session_list,
    list_directory,
    remember_sender_thread,
    resolve_session,
    stamp_last_platform,
)

__all__ = [
    "SESSION_TTL_SECONDS",
    "refuse_session_lookup_for_durable_job",
    "session_store_not_for_long_jobs",
    "SessionEntry",
    "bind_sender_to_thread",
    "canonical_web_session",
    "create_named_session",
    "enrich_summary",
    "find_mouth_thread",
    "format_session_list",
    "list_directory",
    "remember_sender_thread",
    "resolve_session",
    "stamp_last_platform",
]
