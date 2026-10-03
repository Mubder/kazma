"""Which entities no clean-up, delete or merge may take away.

Every path that removes an entity or merges one away asks here: the Memory
page's Delete, Merge and Hygiene purge, the agent's memory tools, and the
automatic retire (:mod:`kazma_core.memory.entity_retire`). Each kept its own
list until 2026-10-03, five copies that did not agree, and four of them named
the author of Kazma as the owner every install protects.

- ``core``: ``user`` (the memory hub), ``assistant`` and ``kazma`` (the agent).
- ``self``: the hub's own names -- an entity the hub's aliases name, or one
  labelled you / me / user (:func:`self_hub.collect_self_entity_ids`). The
  owner's node is known by what the hub records, never by a name written into
  the code: each install has its own owner.
- ``flag``: an entity the operator protected (``entities.is_protected``).

A ``self`` entity may still be merged INTO the hub: that is how a stray person
shell for the owner goes back where it belongs.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Collection

__all__ = ["is_core_entity", "merge_refusal", "protection"]

_CORE_IDS = frozenset({"user", "assistant", "kazma"})


def is_core_entity(entity_id: str) -> bool:
    """True for the hub and the agent's own ids -- policy, no row needed."""
    return str(entity_id or "").strip().lower() in _CORE_IDS


def protection(
    conn: sqlite3.Connection,
    entity_id: str,
    *,
    tenant_id: str | None = None,
    self_ids: Collection[str] | None = None,
) -> str | None:
    """``"core"``, ``"self"`` or ``"flag"`` when *entity_id* is protected, else None.

    The hub's names are read for *tenant_id*, else the entity row's own tenant.
    A caller judging many entities of one tenant passes *self_ids* once. Raises
    ``sqlite3.Error`` when the store cannot answer: each caller decides, and
    none of them deletes on a guess.
    """
    eid = str(entity_id or "").strip()
    if not eid:
        return None
    if is_core_entity(eid):
        return "core"
    row = conn.execute(
        "SELECT tenant_id, is_protected FROM entities WHERE id = ?", (eid,)
    ).fetchone()
    row_tenant, flag = tuple(row) if row is not None else (None, 0)
    if self_ids is None:
        from kazma_core.memory.self_hub import collect_self_entity_ids

        self_ids = collect_self_entity_ids(conn, str(tenant_id or row_tenant or "default"))
    if eid in self_ids:
        return "self"
    if int(flag or 0) == 1:
        return "flag"
    return None


def merge_refusal(
    conn: sqlite3.Connection,
    source_id: str,
    target_id: str,
    *,
    tenant_id: str | None = None,
) -> str | None:
    """Why *source_id* may not be merged into *target_id* (its protection), or None."""
    from kazma_core.memory.self_hub import HUB_ID

    kind = protection(conn, source_id, tenant_id=tenant_id)
    if kind == "self" and str(target_id or "").strip() == HUB_ID:
        return None
    return kind
