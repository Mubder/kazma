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

**A shared space** (Spaces S1-11, decision N31) holds one text per member
(``memory_member_profile``): each member's own, shown only in that member's
turns and edited only by them. :func:`copy_from_personal` copies a member's
personal-space text in once; the two are independent after that. A personal
space keeps its one row, as before.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from typing import Any

from kazma_core.spaces.scope import space_named_or_bound

__all__ = ["ABOUT_MAX_CHARS", "ABOUT_MARKER", "about_block", "about_for_turn", "copy_from_personal",
           "get_about", "set_about"]

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


def _shared(tenant_id: str) -> bool:
    from kazma_core.memory.speakers import room

    return room(tenant_id).shared


def get_about(tenant_id: str, *, conn: sqlite3.Connection | None = None,
              account: str | None = None) -> dict[str, Any]:
    """``{"about": text, "updated_at": epoch or None}`` for the tenant: in a
    shared space *account*'s own text (none for no account), else the
    tenant's."""
    tenant = space_named_or_bound(tenant_id)
    shared = _shared(tenant)
    if shared and not account:
        return {"about": "", "updated_at": None}
    own = conn is None
    conn = conn or _open()
    try:
        if shared:
            row = conn.execute(
                "SELECT about, updated_at FROM memory_member_profile WHERE tenant_id = ? AND account = ?",
                (tenant, account),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT about, updated_at FROM memory_profile WHERE tenant_id = ?", (tenant,)
            ).fetchone()
        return {"about": str(row[0]) if row else "", "updated_at": float(row[1]) if row else None}
    finally:
        if own:
            conn.close()


def about_for_turn(tenant_id: str, asker: str | None = None) -> dict[str, Any]:
    """What a turn of *tenant_id* is shown (Memory audit M07).

    Its own text. With tenants not enforced, a turn of a per-platform or
    per-user tenant ("web", "web:sess-1") shows the install's own About me
    when it has none -- the Memory page saves it there, and the turns never
    read it (the page still said "Kazma reads this at the start of every
    reply"). Under enforcement a tenant sees only its own."""
    from kazma_core.memory.config import memory_tenant_enforced

    tid = space_named_or_bound(tenant_id)
    if _shared(tid):
        # Only the asker's own text, in their own turns (N31).
        return get_about(tid, account=asker)
    out = get_about(tid)
    if not out["about"] and tid != "default" and not memory_tenant_enforced():
        return get_about("default")
    return out


def set_about(tenant_id: str, text: str, *, conn: sqlite3.Connection | None = None,
              account: str | None = None) -> dict[str, Any]:
    """Save the tenant's text (trimmed; empty removes it); in a shared space
    *account*'s own. Longer than :data:`ABOUT_MAX_CHARS` is refused, never
    cut: the user wrote all of it."""
    about = str(text or "").strip()
    if len(about) > ABOUT_MAX_CHARS:
        # A code the page words in its own language (Memory audit M32).
        return {"ok": False, "error": "about_too_long", "code": "about_too_long",
                "length": len(about), "max": ABOUT_MAX_CHARS}
    tenant = space_named_or_bound(tenant_id)
    if _shared(tenant):
        return _set_member_about(tenant, account, about, conn=conn)
    own = conn is None
    conn = conn or _open()
    try:
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


def _set_member_about(tenant: str, account: str | None, about: str, *,
                      conn: sqlite3.Connection | None) -> dict[str, Any]:
    if not account:
        # Nobody's text is written for no one.
        return {"ok": False, "error": "no_account", "code": "no_account"}
    own = conn is None
    conn = conn or _open()
    try:
        if about:
            conn.execute(
                "INSERT INTO memory_member_profile (tenant_id, account, about, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, account) DO UPDATE SET about = excluded.about, "
                "updated_at = excluded.updated_at",
                (tenant, account, about, time.time()),
            )
        else:
            conn.execute("DELETE FROM memory_member_profile WHERE tenant_id = ? AND account = ?",
                         (tenant, account))
        conn.commit()
        return {"ok": True, "about": about}
    finally:
        if own:
            conn.close()


def copy_from_personal(tenant_id: str, account: str | None) -> dict[str, Any]:
    """Copy *account*'s personal-space About me into their own text in the
    shared space *tenant_id*, once (N31): the two are independent after that.
    Refused with a code: ``not_shared`` (a personal space has one text),
    ``no_account``, ``no_personal_space``, ``nothing_to_copy``."""
    tenant = tenant_id
    if not tenant or not _shared(tenant):
        return {"ok": False, "error": "not_shared", "code": "not_shared"}
    if not account:
        return {"ok": False, "error": "no_account", "code": "no_account"}
    from kazma_core.spaces import identity

    personal = next((s["id"] for s in identity.spaces_of(account) if s.get("personal_of") == account), None)
    if not personal:
        return {"ok": False, "error": "no_personal_space", "code": "no_personal_space"}
    text = get_about(personal)["about"]
    if not text:
        return {"ok": False, "error": "nothing_to_copy", "code": "nothing_to_copy"}
    return _set_member_about(tenant, account, text, conn=None)


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
