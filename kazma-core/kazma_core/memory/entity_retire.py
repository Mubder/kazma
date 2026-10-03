"""Remove an entity row when its last fact goes, if the row holds nothing else.

A fact invalidated or superseded leaves its entities behind. One that held
only that fact becomes an "empty" entity: no facts, no links, listed by the
entity page and counted by memory health until someone runs
``purge_empty_entities`` by hand. :func:`retire_empty_entities` removes it
at the moment it empties -- but only a plain shell: a concept whose row
carries nothing but its own name.

Kept, whatever the count: protected entities (the hub, the agent, the
owner's own names, and those the operator protected --
:mod:`kazma_core.memory.entity_protection`), major and high-stakes entities,
any type but ``concept`` (someone chose it), a row with metadata (a merge
redirect, operator data), aliases other than its own name (a merge folded
another name into it), a graph grouping, a merge-ledger row, and an entity
another entity's merge points at. The facts themselves are never touched:
invalidated facts stay as history and still name the id, and the next fact
that names it mints the entity again.

Every purge removes exactly what this rule accepts (:func:`plain_empty_shells`):
the Memory page's Hygiene and the agent's ``memory_purge_empty_entities``
deleted every entity with no live fact, merge redirects included -- a
redirect is how a merged-away name keeps reaching its target
(``entity_resolution.canonical_entity_id``).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Collection, Iterable

logger = logging.getLogger(__name__)

__all__ = ["plain_empty_shells", "retire_empty_entities"]


def _own_aliases(eid: str, name: str) -> set[str]:
    from kazma_core.memory.entity_resolution import alias_hash

    names = {eid, name, eid.replace("_", " ")}
    return names | {alias_hash(n) for n in names if n}


def _is_plain_empty_shell(
    conn: sqlite3.Connection, eid: str, *, tenant_id: str, self_ids: Collection[str]
) -> bool:
    """True when entity *eid* has no live fact and carries nothing but its name."""
    from kazma_core.memory.entity_counts import belief_count_sql
    from kazma_core.memory.entity_protection import protection

    if not eid or protection(conn, eid, tenant_id=tenant_id, self_ids=self_ids):
        return False
    row = conn.execute(
        f"SELECT e.id, e.type, e.name, e.aliases_json, e.is_high_stakes, "
        f"e.is_major, e.metadata_json, {belief_count_sql()} AS live "
        "FROM entities e WHERE e.id = ? AND e.tenant_id = ?",
        (eid, tenant_id),
    ).fetchone()
    if row is None:
        return False
    # By position: callers' connections may not use sqlite3.Row.
    rid, rtype, rname, raliases, stakes, major, meta, live = tuple(row)
    if int(live or 0) != 0:
        return False
    if str(rtype or "") != "concept":
        return False
    if int(stakes or 0) or int(major or 0):
        return False
    if (meta or "").strip() not in ("", "{}"):
        return False
    try:
        aliases = set(json.loads(raliases or "[]"))
    except (TypeError, ValueError):
        return False
    if not aliases <= _own_aliases(str(rid), str(rname or "")):
        return False
    held = conn.execute(
        "SELECT 1 FROM graph_associations WHERE tenant_id = ? AND (member = ? OR group_root = ?) "
        "UNION ALL SELECT 1 FROM entity_merges WHERE source_entity_id = ? OR target_entity_id = ? "
        "UNION ALL SELECT 1 FROM entities WHERE CASE WHEN json_valid(metadata_json) "
        "THEN json_extract(metadata_json, '$.merged_into') END = ? "
        "LIMIT 1",
        (tenant_id, eid, eid, eid, eid, eid),
    ).fetchone()
    return held is None


def plain_empty_shells(
    conn: sqlite3.Connection, *, tenant_id: str | None = None, limit: int | None = None
) -> list[tuple[str, str]]:
    """``(tenant_id, entity_id)`` of each entity :func:`retire_empty_entities`
    would remove now, by tenant and id.

    ``None`` reads every tenant: the install's own view, as every memory list
    gives the install's tenant. The purge controls list and remove exactly
    these, and memory health counts them.
    """
    from kazma_core.memory.entity_counts import belief_count_sql
    from kazma_core.memory.self_hub import collect_self_entity_ids

    scoped = tenant_id is not None
    rows = conn.execute(
        f"SELECT e.tenant_id, e.id FROM entities e WHERE e.type = 'concept' "
        f"AND {belief_count_sql()} = 0"
        + (" AND e.tenant_id = ?" if scoped else "")
        + " ORDER BY e.tenant_id, e.id",
        (tenant_id,) if scoped else (),
    ).fetchall()
    found: list[tuple[str, str]] = []
    self_ids_of: dict[str, set[str]] = {}
    for rtenant, rid in (tuple(r) for r in rows):
        tenant = str(rtenant or "default")
        if tenant not in self_ids_of:
            self_ids_of[tenant] = collect_self_entity_ids(conn, tenant)
        if _is_plain_empty_shell(conn, str(rid), tenant_id=tenant, self_ids=self_ids_of[tenant]):
            found.append((tenant, str(rid)))
            if limit is not None and len(found) >= limit:
                break
    return found


def retire_empty_entities(
    conn: sqlite3.Connection, entity_ids: Iterable[str], *, tenant_id: str = "default"
) -> list[str]:
    """Delete each of *entity_ids* that :func:`_is_plain_empty_shell` accepts.

    Returns the removed ids. The caller owns the commit. Never raises: a
    failure leaves the row, which ``purge_empty_entities`` can still take.
    """
    from kazma_core.memory.self_hub import collect_self_entity_ids

    removed: list[str] = []
    self_ids: set[str] | None = None
    for eid in dict.fromkeys(str(e) for e in entity_ids if e):
        try:
            if self_ids is None:
                self_ids = collect_self_entity_ids(conn, tenant_id)
            if _is_plain_empty_shell(conn, eid, tenant_id=tenant_id, self_ids=self_ids):
                conn.execute(
                    "DELETE FROM entities WHERE id = ? AND tenant_id = ?", (eid, tenant_id)
                )
                removed.append(eid)
        except sqlite3.Error:
            logger.warning("[entity_retire] could not check entity %r", eid[:60], exc_info=True)
    if removed:
        logger.info("[entity_retire] removed %d empty entit(ies): %s", len(removed), removed[:10])
    return removed
