"""One memory of a conversation, read whole or shown bounded (2026-11-19).

A memory keeps all of a turn's text since the owner's "keep all text"
(``memory/limits.py``); a list of memories shows each one bounded. These are
the two reads of one memory by its id, in the caller's tenant
(``tenant_view.narrowed_to``: another space's memory reads as not there) and
in the tiers recall searches (a forgotten memory is not there either):

* :func:`preview_episode` -- what ``memory_search`` and the Memory page's
  probe show of one memory: at most ``limits.MEMORY_SHOW_MAX`` characters,
  and how many it leaves out;
* :func:`read_episode` -- the agent's ``memory_read`` and the Memory page's
  "the whole memory": the memory's text, one page of
  ``limits.MEMORY_READ_PAGE`` characters at a time, from an offset.

The text is "User: <question>" and "Assistant: <answer>" when both are
there, as recall shows them; one side alone (a swarm result, a note) as it
is.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from kazma_core.memory.limits import MEMORY_READ_PAGE, MEMORY_SHOW_ANSWER, MEMORY_SHOW_MAX, MEMORY_SHOW_QUESTION

__all__ = ["episode_full_text", "preview_episode", "read_episode"]


def _row(conn: sqlite3.Connection, episode_id: str, tenant_id: str | None) -> Any:
    from kazma_core.memory.tenant_view import narrowed_to
    from kazma_core.memory.vector_engine import RECALLABLE_TIERS

    only = narrowed_to(tenant_id)
    tiers = ",".join("?" for _ in RECALLABLE_TIERS)
    return conn.execute(
        "SELECT id, tenant_id, session_id, turn_number, user_text, assistant_text, summary_text, "
        f"tier, created_at FROM episodes WHERE id = ? AND (? IS NULL OR tenant_id = ?) AND tier IN ({tiers})",
        (episode_id, only, only, *RECALLABLE_TIERS),
    ).fetchone()


def episode_full_text(user_text: str | None, assistant_text: str | None, summary_text: str | None = "") -> str:
    """A memory's whole text, labelled as recall labels it."""
    question, answer = (user_text or "").strip(), (assistant_text or "").strip()
    if question and answer:
        return f"User: {question}\n\nAssistant: {answer}"
    return question or answer or (summary_text or "").strip()


def _open() -> sqlite3.Connection:
    from kazma_core.paths import primary_memory_db

    conn = sqlite3.connect(primary_memory_db(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def preview_episode(episode_id: str, *, tenant_id: str | None, conn: sqlite3.Connection | None = None
                    ) -> tuple[str, int] | None:
    """``(what a list shows of the memory, characters it leaves out)``, or
    None when the caller has no such memory."""
    from kazma_core.memory.episode_text import display_parts

    own = conn is None
    conn = conn or _open()
    try:
        row = _row(conn, episode_id, tenant_id)
    finally:
        if own:
            conn.close()
    if row is None:
        return None
    return display_parts(row["user_text"], row["assistant_text"], row["summary_text"],
                         question_max=MEMORY_SHOW_QUESTION, answer_max=MEMORY_SHOW_ANSWER,
                         one_max=MEMORY_SHOW_MAX)


def read_episode(episode_id: str, *, tenant_id: str | None, offset: int = 0,
                 length: int = MEMORY_READ_PAGE, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
    """One page of a memory's whole text, or None when the caller has no such
    memory: ``{"id", "tier", "created_at", "session_id", "total", "offset",
    "end", "next_offset" (None at the end), "text"}``. An offset past the end
    gives an empty page with ``next_offset`` None."""
    own = conn is None
    conn = conn or _open()
    try:
        row = _row(conn, episode_id, tenant_id)
    finally:
        if own:
            conn.close()
    if row is None:
        return None
    whole = episode_full_text(row["user_text"], row["assistant_text"], row["summary_text"])
    total = len(whole)
    start = max(0, min(int(offset or 0), total))
    size = max(1, min(int(length or MEMORY_READ_PAGE), MEMORY_READ_PAGE))
    end = min(total, start + size)
    # A page never ends in whitespace: the model reads it inside a data fence,
    # which drops a block's trailing whitespace, so a page cut after a space
    # lost that space and the pages joined back were not the memory. The
    # whitespace opens the next page instead (the whole text ends in none).
    while start + 1 < end < total and whole[end - 1].isspace():
        end -= 1
    return {
        "id": str(row["id"]),
        "tier": str(row["tier"] or ""),
        "created_at": float(row["created_at"] or 0.0),
        "session_id": str(row["session_id"] or ""),
        "total": total,
        "offset": start,
        "end": end,
        "next_offset": end if end < total else None,
        "text": whole[start:end],
    }
