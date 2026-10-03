"""How clean the memory graph is: counts, with examples, for memory health.

Read-only. Nothing here changes a row: an operator reads the report (memory
health, ``/api/memory/v2/health`` -> ``graph``) and decides, with the
existing controls, what to merge or delete.

- ``empty``: entities with no live fact (``purge_empty_entities`` takes
  them; new ones are removed as they empty, ``entity_retire``).
- ``isolated``: entities with facts but no link to another entity -- only
  literal values hang off them.
- ``work_items``: entity ids shaped like a step of work, not a thing
  (``ego_anchor.subject_should_mint_entity``): phases, tickets, versions,
  paths, sentences. New ones are no longer minted.
- ``duplicates``: ids that are the same words (order, separators, a plural
  "s" aside) -- merge candidates.

Counts use the materialized ``belief_count`` / ``graph_degree`` columns;
a row still at the stale sentinel (-1) is not counted.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["graph_hygiene_report"]

_EXAMPLES = 10
_HUB_IDS = ("user", "assistant")


def _same_words_key(entity_id: str) -> str:
    """Key under which ids made of the same words collide: the rule predicate
    names follow (``predicates._predicate_key``: order, filler words and a
    plural "s" aside), so "the same words" means one thing everywhere."""
    from kazma_core.memory.predicates import _predicate_key

    return " ".join(sorted(_predicate_key(re.sub(r"[^a-z0-9]+", "_", (entity_id or "").lower()))))


def graph_hygiene_report(conn: sqlite3.Connection, *, tenant_id: str | None = None) -> dict[str, Any]:
    """Counts and example ids of each kind of clutter in *tenant_id*'s graph.

    ``None`` / ``"default"`` read the whole install, like the rest of health.
    """
    from kazma_core.memory.ego_anchor import subject_should_mint_entity

    scoped = tenant_id not in (None, "", "default")
    # A merged-away entity is a redirect (metadata.merged_into), not clutter.
    rows = conn.execute(
        "SELECT id, belief_count, graph_degree FROM entities "
        "WHERE COALESCE(json_extract(CASE WHEN json_valid(metadata_json) "
        "THEN metadata_json END, '$.merged_into'), '') = ''"
        + (" AND tenant_id = ?" if scoped else "")
        + " ORDER BY id",
        (tenant_id,) if scoped else (),
    ).fetchall()

    empty: list[str] = []
    isolated: list[str] = []
    work_items: list[str] = []
    by_words: dict[str, list[str]] = {}
    for rid, count, degree in (tuple(r) for r in rows):
        eid = str(rid or "")
        if not eid or eid.lower() in _HUB_IDS:
            continue
        count = int(count if count is not None else -1)
        degree = int(degree if degree is not None else -1)
        if count == 0:
            empty.append(eid)
        elif count > 0 and degree == 0:
            isolated.append(eid)
        if not subject_should_mint_entity(eid):
            work_items.append(eid)
        key = _same_words_key(eid)
        if key:
            by_words.setdefault(key, []).append(eid)
    duplicates = [ids for ids in by_words.values() if len(ids) > 1]

    return {
        "entities": len(rows),
        "empty": {"count": len(empty), "examples": empty[:_EXAMPLES]},
        "isolated": {"count": len(isolated), "examples": isolated[:_EXAMPLES]},
        "work_items": {"count": len(work_items), "examples": work_items[:_EXAMPLES]},
        "duplicates": {"count": len(duplicates), "examples": duplicates[:_EXAMPLES]},
    }
