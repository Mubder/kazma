"""Reopen list entries a single-valued write closed.

Live 2026-10-19: the deep pass stored ``user noted "test codeword is ..."``
labelled single-valued, and the write treated the 58 current entries of the
owner's ``noted`` list as one fact's stray values -- it superseded the newest
and closed the other 57 (``belief_mutation._established_kind`` now keeps a
list a list). Nothing was deleted: each entry's row and text stayed, closed.

A list entry (``predicate_type = 'set'``) is closed by such a write when the
write is a single-valued row (``functional``/``state``) of the same tenant,
subject and predicate, and the entry is the one it superseded
(``supersedes_id``) or was closed in the same instant it was written
(``invalidated_at`` equal to its ``ingested_at``), and Kazma does not itself
classify the predicate single-valued (``kazma_classifies``). An entry closed any other
way -- the Memory page, a Forget, the user's own correction -- is not read.

:func:`find_closed_entries` reads only and names entries by id, never by
value. :func:`reopen_entries` reopens them in one transaction (valid again,
not invalidated) and writes one ``memory_audit_log`` row each
(``list_entry_reopened``). The mirror outbox's triggers carry the change to the
Postgres mirror. ``scripts/memory_reopen_lists.py`` runs both; the live run is
the owner's.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from kazma_core.memory.belief_mutation import kazma_classifies

logger = logging.getLogger(__name__)

__all__ = ["find_closed_entries", "reopen_entries"]

#: Two timestamps of one write: ``recorded`` is written to both columns.
_SAME_INSTANT_S = 0.001

#: An entry the user forgot is never reopened: Forget empties the value
#: (``object = ''``) and stamps ``metadata.forgotten``
#: (``forget._forget_fact_rows``). A forgotten entry is still a closed
#: ``set`` row, so the superseded-id match found it and the reopen made a
#: blank fact current again (audit 2026-10-26 AUD-047). Both reads and the
#: reopen's own ``UPDATE`` hold this condition.
_NOT_FORGOTTEN_SQL = (
    "COALESCE(object, '') != '' AND NOT (json_valid(COALESCE(metadata_json, '{}')) "
    "AND json_extract(COALESCE(metadata_json, '{}'), '$.forgotten') IS NOT NULL)"
)


def find_closed_entries(conn: sqlite3.Connection, *, written_after: float = 0.0) -> list[dict[str, Any]]:
    """Every list entry a single-valued write closed, grouped by that write
    (written after *written_after*, epoch seconds). Ids and times only."""
    groups: list[dict[str, Any]] = []
    writes = conn.execute(
        "SELECT id, tenant_id, subject, predicate, supersedes_id, ingested_at FROM beliefs "
        "WHERE COALESCE(predicate_type, '') IN ('functional', 'state') AND ingested_at > ? "
        "ORDER BY ingested_at",
        (float(written_after),),
    ).fetchall()
    for wid, tenant, subject, predicate, supersedes, at in writes:
        if kazma_classifies(str(predicate)):
            continue  # single-valued by Kazma's own rule: a supersede is right there
        closed = [str(r[0]) for r in conn.execute(
            "SELECT id FROM beliefs WHERE tenant_id = ? AND subject = ? AND predicate = ? "
            "AND predicate_type = 'set' AND invalidated_at IS NOT NULL "
            f"AND {_NOT_FORGOTTEN_SQL} "
            "AND (id = ? OR ABS(invalidated_at - ?) < ?) ORDER BY ingested_at",
            (tenant, subject, predicate, supersedes or "", float(at or 0.0), _SAME_INSTANT_S),
        ).fetchall()]
        if closed:
            groups.append({
                "write": str(wid), "tenant": str(tenant or "default"), "subject": str(subject),
                "predicate": str(predicate), "written_at": float(at or 0.0), "closed": closed,
            })
    return groups


def reopen_entries(
    conn: sqlite3.Connection, ops_conn: sqlite3.Connection, groups: list[dict[str, Any]],
    *, actor: str = "operator:memory_reopen_lists",
) -> dict[str, Any]:
    """Reopen every entry of *groups* (from :func:`find_closed_entries`) in one
    transaction; one audit row each. Idempotent: a reopened entry is current
    and not found again."""
    ts = time.time()
    done: list[tuple[str, str, str]] = []
    conn.execute("BEGIN IMMEDIATE")
    try:
        for group in groups:
            for bid in group["closed"]:
                cur = conn.execute(
                    "UPDATE beliefs SET valid_until = NULL, invalidated_at = NULL "
                    "WHERE id = ? AND predicate_type = 'set' AND invalidated_at IS NOT NULL "
                    f"AND {_NOT_FORGOTTEN_SQL}",
                    (bid,),
                )
                if cur.rowcount:
                    done.append((bid, group["tenant"], group["write"]))
        conn.execute("COMMIT")
    except sqlite3.Error:
        conn.execute("ROLLBACK")
        raise
    for bid, tenant, write in done:
        ops_conn.execute(
            "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id, actor, "
            "reason, state_before_json, state_after_json) "
            "VALUES (?, ?, ?, 'list_entry_reopened', 'beliefs', ?, ?, ?, ?, ?)",
            ("lr_" + uuid.uuid4().hex[:20], tenant, ts, bid, actor,
             f"closed by the single-valued write {write} (2026-10-19 fix)",
             json.dumps({"current": False}), json.dumps({"current": True})),
        )
    ops_conn.commit()
    if done:
        logger.info("[list_reopen] reopened %d list entr%s: %s", len(done),
                    "y" if len(done) == 1 else "ies", [d[0] for d in done[:10]])
    return {"reopened": len(done), "ids": [d[0] for d in done]}
