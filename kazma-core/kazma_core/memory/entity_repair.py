"""Repair facts the old id rule collapsed into the entity ``entity``.

Until 2026-10-13 an entity's id kept ``a-z0-9`` only and fell back to the
constant ``"entity"`` (Memory audit M02): every all-Arabic, Persian or CJK
subject became ``entity``, so unrelated facts superseded each other, and once
one such object existed, extraction rewrote later non-Latin objects to the id
``entity`` -- "user works_at أرامكو" was stored as ``user works_at entity``.
The id rule is fixed (``entity_resolution.slug``); this module repairs what it
wrote.

A collapsed fact's real subject or object is not stored anywhere (the audit
reason and the state JSON carry the collapsed id too), so nothing is guessed:

- :func:`size_collapsed` counts, read-only: the owner sizes the live store
  first.
- :func:`repair_collapsed` quarantines each collapsed fact -- no longer
  current, ``metadata.quarantined`` saying why, its row kept -- and queues the
  turn it came from (``source_session`` / ``source_turn``) for the deep pass
  (``micro_consolidation``), which extracts it again under the fixed ids at
  the turn's own time. A fact whose turn is no longer held stays quarantined.
  The ``entity`` node goes with its last fact when it holds nothing else.
  A fact whose own turn says "entity" in Latin letters may be a real one and
  is left alone, counted as ``ambiguous``.

Idempotent: a quarantined fact is not touched again, and a turn is queued once
per run. ``scripts/memory_entity_repair.py`` runs both against an install's
memory database; the live run is the owner's.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["COLLAPSED_ID", "repair_collapsed", "size_collapsed"]

#: The id the old rule gave every label it could not spell.
COLLAPSED_ID = "entity"
_REASON = "collapsed_entity_id"
_SAYS_ENTITY = re.compile(r"\bentit(?:y|ies)\b", re.I)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Rows by name, whatever the caller's connection returns."""
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(sql, params).fetchall()


def _collapsed_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return _rows(
        conn,
        "SELECT id, tenant_id, subject, object, source_session, source_turn, invalidated_at, "
        "valid_until, metadata_json FROM beliefs WHERE subject = ? OR object = ?",
        (COLLAPSED_ID, COLLAPSED_ID),
    )


def _quarantined(meta_json: str | None) -> bool:
    try:
        meta = json.loads(meta_json or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(meta, dict) and (meta.get("quarantined") or {}).get("reason") == _REASON


def _source_episode(conn: sqlite3.Connection, row: sqlite3.Row) -> sqlite3.Row | None:
    """The memory of the turn the fact came from, still held (not forgotten)."""
    if not row["source_session"] or row["source_turn"] is None:
        return None
    found = _rows(
        conn,
        "SELECT id, user_text, assistant_text FROM episodes WHERE tenant_id = ? AND session_id = ? "
        "AND turn_number = ? AND tier != 'forgotten' ORDER BY created_at LIMIT 1",
        (row["tenant_id"], row["source_session"], int(row["source_turn"])),
    )
    return found[0] if found else None


def _classify(conn: sqlite3.Connection) -> dict[str, list[tuple[sqlite3.Row, sqlite3.Row | None]]]:
    out: dict[str, list[tuple[sqlite3.Row, sqlite3.Row | None]]] = {
        "done": [], "ambiguous": [], "rederivable": [], "unrecoverable": [],
    }
    for row in _collapsed_rows(conn):
        if _quarantined(row["metadata_json"]):
            out["done"].append((row, None))
            continue
        episode = _source_episode(conn, row)
        said = " ".join(str(episode[k] or "") for k in ("user_text", "assistant_text")) if episode else ""
        if _SAYS_ENTITY.search(said):
            out["ambiguous"].append((row, episode))
        elif episode is not None:
            out["rederivable"].append((row, episode))
        else:
            out["unrecoverable"].append((row, None))
    return out


def size_collapsed(conn: sqlite3.Connection) -> dict[str, Any]:
    """What a repair would do, by tenant. Reads only."""
    groups = _classify(conn)
    tenants: dict[str, dict[str, int]] = {}
    for kind, items in groups.items():
        for row, _ep in items:
            t = tenants.setdefault(str(row["tenant_id"] or "default"), {k: 0 for k in groups})
            t[kind] += 1
    node = conn.execute("SELECT tenant_id FROM entities WHERE id = ?", (COLLAPSED_ID,)).fetchall()
    return {
        "facts": sum(len(v) for v in groups.values()),
        **{kind: len(items) for kind, items in groups.items()},
        "turns_to_extract_again": len({ep["id"] for _r, ep in groups["rederivable"] if ep is not None}),
        "by_tenant": tenants,
        "entity_node": [str(r[0] or "default") for r in node],
    }


def repair_collapsed(
    conn: sqlite3.Connection, *, now: float | None = None, queue: bool = True, enqueue: Any = None
) -> dict[str, Any]:
    """Quarantine every collapsed fact and queue its turn for the deep pass.

    *queue* False queues nothing (a repair of a copy); *enqueue* defaults to
    ``task_queue.enqueue_task``. The caller's connection is committed."""
    from kazma_core.memory.entity_retire import retire_empty_entities
    from kazma_core.memory.hygiene import invalidate_belief

    if enqueue is None:
        from kazma_core.memory.task_queue import enqueue_task as enqueue
    ts = float(now if now is not None else time.time())
    groups = _classify(conn)
    quarantined = 0
    for row, _episode in groups["rederivable"] + groups["unrecoverable"]:
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["quarantined"] = {"reason": _REASON, "at": ts}
        if row["invalidated_at"] is None and row["valid_until"] is None:
            invalidate_belief(str(row["id"]), conn=conn, now=ts, tenant_id=str(row["tenant_id"] or "default"))
        conn.execute("UPDATE beliefs SET metadata_json = ? WHERE id = ?",
                     (json.dumps(meta, ensure_ascii=False), row["id"]))
        quarantined += 1
    tenants = {str(r["tenant_id"] or "default") for r, _e in groups["rederivable"] + groups["unrecoverable"]}
    retired = [eid for t in sorted(tenants) for eid in retire_empty_entities(conn, [COLLAPSED_ID], tenant_id=t)]
    conn.commit()
    queued = 0
    turns = list(dict.fromkeys(str(ep["id"]) for _r, ep in groups["rederivable"] if ep is not None))
    for episode_id in turns if queue else []:
        if enqueue("micro_consolidation", {"episode_id": episode_id}):
            queued += 1
        else:
            logger.warning("[entity_repair] the memory queue refused turn %s; run the repair again", episode_id)
    return {
        "quarantined": quarantined,
        "turns_queued": queued,
        "turns_to_extract_again": len(turns),
        "ambiguous_left": len(groups["ambiguous"]),
        "already_done": len(groups["done"]),
        "entity_node_retired": bool(retired),
    }
