"""Unified conversation directory — one season, every mouth.

Web, Telegram, Discord, Slack, and TUI list and switch the same
SessionManager rows. LangGraph ``thread_id`` is the durable identity;
platform delivery lives in SessionStore (never in graph state).
"""

from __future__ import annotations

from kazma_core.sessions.directory import (
    SessionEntry,
    bind_sender_to_thread,
    create_named_session,
    enrich_summary,
    format_session_list,
    list_directory,
    resolve_session,
    stamp_last_platform,
)

__all__ = [
    "SessionEntry",
    "bind_sender_to_thread",
    "create_named_session",
    "enrich_summary",
    "format_session_list",
    "list_directory",
    "resolve_session",
    "stamp_last_platform",
]
