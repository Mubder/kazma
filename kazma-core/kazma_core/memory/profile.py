"""What the user wants Kazma to know about them, in their own words (plan C1).

Recall brings back what a question is about; some things are about every
question -- who the user is, how they want to be answered. Measured before
building (2026-09-27): the live install held 6 current, user-stated,
single-valued facts about the user, all subscription reset dates and 4 of
them past, so a profile assembled from facts would have put stale dates in
front of the model on every turn. The owner chose the other way: a short
"About me" the user writes in Settings, shown to the model on every call of
every turn, never inferred.

One row per tenant in the memory database (backed up, migrated and exported
with memory). Empty text deletes the row. The supervisor places it right
after the system prompt and personality (``graph_helpers._ensure_about_user``).
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

__all__ = ["ABOUT_MAX_CHARS", "ABOUT_MARKER", "about_block", "get_about", "set_about"]

logger = logging.getLogger(__name__)

#: Long enough for a name, work, place and a few preferences; short enough
#: that it costs little on every call.
ABOUT_MAX_CHARS = 2000
#: Tags the system message so the supervisor can find and replace it.
ABOUT_MARKER = "[kazma:about-user]"


_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def _open() -> sqlite3.Connection:
    """The memory database, schema ensured once per file per process: the
    supervisor reads the text on every model call."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    path = str(primary_memory_db())
    conn = sqlite3.connect(path, timeout=30)
    with _schema_lock:
        if path not in _schema_ready:
            ensure_primary_schema(conn)
            _schema_ready.add(path)
            return conn
    apply_sqlite_pragmas(conn)
    return conn


def get_about(tenant_id: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """``{"about": text, "updated_at": epoch or None}`` for the tenant."""
    own = conn is None
    conn = conn or _open()
    try:
        row = conn.execute(
            "SELECT about, updated_at FROM memory_profile WHERE tenant_id = ?", (tenant_id or "default",)
        ).fetchone()
        return {"about": str(row[0]) if row else "", "updated_at": float(row[1]) if row else None}
    finally:
        if own:
            conn.close()


def set_about(tenant_id: str, text: str, *, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """Save the tenant's text (trimmed; empty removes it). Longer than
    :data:`ABOUT_MAX_CHARS` is refused, never cut: the user wrote all of it."""
    about = str(text or "").strip()
    if len(about) > ABOUT_MAX_CHARS:
        return {"ok": False, "error": f"About me is {len(about)} characters; the limit is {ABOUT_MAX_CHARS}."}
    own = conn is None
    conn = conn or _open()
    try:
        tenant = tenant_id or "default"
        if about:
            now = time.time()
            conn.execute(
                "INSERT INTO memory_profile (tenant_id, about, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(tenant_id) DO UPDATE SET about = excluded.about, updated_at = excluded.updated_at",
                (tenant, about, now),
            )
        else:
            conn.execute("DELETE FROM memory_profile WHERE tenant_id = ?", (tenant,))
        conn.commit()
        return {"ok": True, "about": about}
    finally:
        if own:
            conn.close()


def about_block(text: str) -> str:
    """The system message the model reads, or ``""`` when there is nothing."""
    about = str(text or "").strip()
    if not about:
        return ""
    return (
        f"{ABOUT_MARKER}\n## About the user (written by the user in Settings -> About me)\n"
        "Take this into account in every answer; it is the user's own description and "
        "preferences, and newer statements in the conversation win over it.\n\n"
        f"{about}"
    )
