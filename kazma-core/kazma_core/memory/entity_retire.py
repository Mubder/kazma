"""Remove an entity row when its last fact goes, if the row holds nothing else.

A fact invalidated or superseded leaves its entities behind. One that held
only that fact becomes an "empty" entity: no facts, no links, listed by the
entity page and counted by memory health until someone runs
``purge_empty_entities`` by hand. :func:`retire_empty_entities` removes it
at the moment it empties -- but only a plain shell: a concept whose row
carries nothing but its own name.

Kept, whatever the count: the hub and self ids, protected, major and
high-stakes entities, any type but ``concept`` (someone chose it), a row
with metadata (a merge redirect, operator data), aliases other than its own
name (a merge folded another name into it), a graph grouping, a merge-ledger
row, and an entity another entity's merge points at. The facts themselves
are never touched: invalidated facts stay as history and still name the id,
and the next fact that names it mints the entity again.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable

logger = logging.getLogger(__name__)

__all__ = ["PROTECTED_IDS", "retire_empty_entities"]

PROTECTED_IDS = frozenset({"user", "assistant", "kazma", "mubder"})


def _own_aliases(eid: str, name: str) -> set[str]:
    from kazma_core.memory.entity_resolution import alias_hash

    names = {eid, name, eid.replace("_", " ")}
    return names | {alias_hash(n) for n in names if n}


def _is_plain_empty_shell(conn: sqlite3.Connection, eid: str, *, tenant_id: str) -> bool:
    """True when entity *eid* has no live fact and carries nothing but its name."""
    from kazma_core.memory.entity_counts import belief_count_sql
    from kazma_core.memory.self_hub import collect_self_entity_ids

    if not eid or eid.lower() in PROTECTED_IDS:
        return False
    row = conn.execute(
        f"SELECT e.id, e.type, e.name, e.aliases_json, e.is_high_stakes, e.is_protected, "
        f"e.is_major, e.metadata_json, {belief_count_sql()} AS live "
        "FROM entities e WHERE e.id = ? AND e.tenant_id = ?",
        (eid, tenant_id),
    ).fetchone()
    if row is None:
        return False
    # By position: callers' connections may not use sqlite3.Row.
    rid, rtype, rname, raliases, stakes, protected, major, meta, live = tuple(row)
    if int(live or 0) != 0:
        return False
    if str(rtype or "") != "concept":
        return False
    if int(stakes or 0) or int(protected or 0) or int(major or 0):
        return False
    if (meta or "").strip() not in ("", "{}"):
        return False
    try:
        aliases = set(json.loads(raliases or "[]"))
    except (TypeError, ValueError):
        return False
    if not aliases <= _own_aliases(str(rid), str(rname or "")):
        return False
    if eid in collect_self_entity_ids(conn, tenant_id):
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


def retire_empty_entities(
    conn: sqlite3.Connection, entity_ids: Iterable[str], *, tenant_id: str = "default"
) -> list[str]:
    """Delete each of *entity_ids* that :func:`_is_plain_empty_shell` accepts.

    Returns the removed ids. The caller owns the commit. Never raises: a
    failure leaves the row, which ``purge_empty_entities`` can still take.
    """
    removed: list[str] = []
    for eid in dict.fromkeys(str(e) for e in entity_ids if e):
        try:
            if _is_plain_empty_shell(conn, eid, tenant_id=tenant_id):
                conn.execute(
                    "DELETE FROM entities WHERE id = ? AND tenant_id = ?", (eid, tenant_id)
                )
                removed.append(eid)
        except sqlite3.Error:
            logger.warning("[entity_retire] could not check entity %r", eid[:60], exc_info=True)
    if removed:
        logger.info("[entity_retire] removed %d empty entit(ies): %s", len(removed), removed[:10])
    return removed
