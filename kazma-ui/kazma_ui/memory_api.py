"""Memory admin API — beliefs, entities, merge/link, hygiene.

Backs the ``/memory`` operator UI. Prefer these routes over raw SQL and
over hoping the chat agent discovers the right tools.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

logger = logging.getLogger(__name__)

_PROTECTED_ENTITIES = frozenset({"user", "assistant", "kazma", "mubder"})

router = APIRouter(tags=["memory-admin"])


def _conn() -> sqlite3.Connection:
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db(), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    return c


def _belief_count_sql() -> str:
    return """
        (
          SELECT COUNT(*) FROM beliefs b
          WHERE b.tenant_id = e.tenant_id
            AND b.valid_until IS NULL AND b.invalidated_at IS NULL
            AND (b.subject = e.id OR b.object = e.name
                 OR b.object = e.id OR b.subject = e.name)
        )
    """


def _entity_degree_sql() -> str:
    """How many *other* entity ids this entity co-occurs with in active beliefs."""
    return """
        (
          SELECT COUNT(DISTINCT other_id) FROM (
            SELECT CASE
              WHEN b.subject = e.id THEN b.object
              WHEN b.object = e.id THEN b.subject
              WHEN b.subject = e.name THEN b.object
              WHEN b.object = e.name THEN b.subject
              ELSE NULL
            END AS other_id
            FROM beliefs b
            WHERE b.tenant_id = e.tenant_id
              AND b.valid_until IS NULL AND b.invalidated_at IS NULL
              AND (b.subject = e.id OR b.object = e.id
                   OR b.subject = e.name OR b.object = e.name)
          )
          WHERE other_id IS NOT NULL
            AND other_id != e.id
            AND other_id != e.name
            AND other_id NOT IN ('', 'true', 'false', 'null')
        )
    """


# ── Page ─────────────────────────────────────────────────────────────────


def register_memory_page(app: Any, templates: Any, agent: Any) -> None:
    """Mount HTML page on the main FastAPI app."""

    @app.get("/memory", response_class=HTMLResponse)
    async def memory_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "memory.html",
            {
                "config": getattr(agent, "config", None),
                "active_page": "memory",
            },
        )


# ── Health snapshot (compact for page header) ────────────────────────────


@router.get("/api/memory/v2/admin/summary")
async def memory_admin_summary() -> dict[str, Any]:
    try:
        conn = _conn()
        live = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL"
        ).fetchone()[0]
        ents = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        empty = conn.execute(
            f"""
            SELECT COUNT(*) FROM entities e
            WHERE {_belief_count_sql()} = 0
            """
        ).fetchone()[0]
        isolated = conn.execute(
            f"""
            SELECT COUNT(*) FROM entities e
            WHERE {_belief_count_sql()} > 0
              AND {_entity_degree_sql()} = 0
              AND LOWER(e.id) NOT IN ('user','assistant')
            """
        ).fetchone()[0]
        try:
            eps = conn.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
        except Exception:
            eps = 0
        conn.close()
        return {
            "ok": True,
            "beliefs_live": live,
            "beliefs_invalidated": dead,
            "entities": ents,
            "entities_empty": empty,
            "entities_isolated": isolated,
            "episodes": eps,
        }
    except Exception as exc:
        logger.exception("[memory_api] summary failed")
        return {"ok": False, "error": str(exc)[:300]}


# ── Entities ─────────────────────────────────────────────────────────────


@router.get("/api/memory/v2/entities")
async def list_entities(
    q: str = "",
    limit: int = 100,
    empty_only: bool = False,
    isolated_only: bool = False,
) -> dict[str, Any]:
    try:
        conn = _conn()
        lim = max(1, min(int(limit or 100), 300))
        sql = f"""
            SELECT e.id, e.type, e.name, e.is_high_stakes, e.aliases_json,
                   e.metadata_json,
                   {_belief_count_sql()} AS belief_count,
                   {_entity_degree_sql()} AS linked_others
            FROM entities e
            WHERE 1=1
        """
        params: list[Any] = []
        if q and q.strip():
            ql = f"%{q.strip().lower()}%"
            sql += " AND (LOWER(e.id) LIKE ? OR LOWER(e.name) LIKE ? OR LOWER(e.type) LIKE ?)"
            params.extend([ql, ql, ql])
        if empty_only:
            sql += f" AND {_belief_count_sql()} = 0"
        if isolated_only:
            sql += (
                f" AND {_belief_count_sql()} > 0 AND {_entity_degree_sql()} = 0"
                " AND LOWER(e.id) NOT IN ('user','assistant')"
            )
        sql += " ORDER BY belief_count DESC, linked_others ASC, e.name ASC LIMIT ?"
        params.append(lim)
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
        from kazma_core.memory.self_hub import (
            graph_focus_id,
            is_self_entity,
            parse_aliases,
        )

        for r in rows:
            r["empty"] = int(r.get("belief_count") or 0) == 0
            r["isolated"] = (
                int(r.get("belief_count") or 0) > 0
                and int(r.get("linked_others") or 0) == 0
                and str(r.get("id") or "").lower() not in ("user", "assistant")
            )
            r["protected"] = str(r.get("id") or "").lower() in _PROTECTED_ENTITIES
            aliases = parse_aliases(r.get("aliases_json"))
            r["aliases"] = aliases
            r["is_self"] = is_self_entity(
                entity_id=str(r.get("id") or ""),
                name=str(r.get("name") or ""),
                aliases=aliases,
                entity_type=str(r.get("type") or ""),
            )
            # Canvas hub is always id=user — self person shells focus there.
            r["graph_id"] = graph_focus_id(
                str(r.get("id") or ""),
                name=str(r.get("name") or ""),
                aliases=aliases,
                entity_type=str(r.get("type") or ""),
            )
        conn.close()
        return {"ok": True, "count": len(rows), "entities": rows}
    except Exception as exc:
        logger.exception("[memory_api] list entities failed")
        return {"ok": False, "entities": [], "error": str(exc)[:300]}


@router.post("/api/memory/v2/entities/{entity_id}/rename")
async def rename_entity(entity_id: str, request: Request) -> dict[str, Any]:
    """Change an entity's *display* name without rewiring belief ids.

    Canonical ``id`` stays stable (links, subjects, objects keep working).
    Previous labels are kept in ``aliases_json`` so resolution still maps
    nicknames (e.g. Mubder / You) onto the same node. Protected hub ids
    like ``user`` may be renamed for the canvas label ("You" → "Mubder")
    but cannot be deleted or merged away.

    Missing rows are upserted so graph virtual nodes (and the hardcoded
    ``user`` hub) can receive a durable label on first rename.
    """
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    if len(eid) > 200:
        return {"ok": False, "error": "entity_id too long"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    new_name = str((body or {}).get("name") or "").strip()
    if not new_name:
        return {"ok": False, "error": "name required"}
    if len(new_name) > 120:
        return {"ok": False, "error": "name too long (max 120)"}

    try:
        from kazma_core.memory.self_hub import (
            ensure_user_hub,
            is_self_entity,
            is_self_label,
            parse_aliases,
        )

        conn = _conn()
        row = conn.execute(
            "SELECT id, type, name, aliases_json FROM entities WHERE id=?",
            (eid,),
        ).fetchone()
        created = False
        if not row:
            # Promote a graph node / hub id into a real entity so the
            # display name survives reloads (virtual facts, user hub).
            etype = "person" if eid.lower() in ("user", "you", "assistant") else "concept"
            conn.execute(
                """INSERT INTO entities
                   (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
                   VALUES (?, 'default', ?, ?, '[]', ?, '{}')""",
                (eid, etype, new_name, 1 if etype == "person" else 0),
            )
            created = True
            old_name = eid
            aliases: list[Any] = []
            etype_out = etype
        else:
            old_name = str(row["name"] or eid)
            etype_out = str(row["type"] or "concept")
            aliases = parse_aliases(row["aliases_json"])

        # Preserve identity surface: old display, bare id, and defaults.
        for a in (old_name, eid, new_name, "You" if eid.lower() == "user" else None):
            if a and a not in aliases:
                aliases.append(a)
        # Cap alias list growth (keep most recent tail)
        if len(aliases) > 40:
            aliases = aliases[-40:]

        conn.execute(
            "UPDATE entities SET name=?, aliases_json=? WHERE id=?",
            (new_name, json.dumps(aliases, ensure_ascii=False), eid),
        )

        # Self person shells (User / You / ent_* backfill) drive the canvas hub
        # label. Keep entities.id=user in sync so the graph shows Mubder not You.
        was_self = is_self_entity(
            entity_id=eid,
            name=old_name,
            aliases=aliases,
            entity_type=etype_out,
        ) or is_self_label(old_name)
        hub_synced = False
        if was_self or eid.lower() == "user":
            # Keep User/You in aliases so is_self stays true after rename
            for keep in ("User", "You", "user"):
                if keep not in aliases:
                    aliases.append(keep)
            conn.execute(
                "UPDATE entities SET aliases_json=? WHERE id=?",
                (json.dumps(aliases, ensure_ascii=False), eid),
            )
            ensure_user_hub(
                conn,
                new_name,
                extra_aliases=list(aliases) + [eid, old_name],
            )
            hub_synced = True

        conn.commit()
        conn.close()
        return {
            "ok": True,
            "id": eid,
            "name": new_name,
            "previous_name": old_name,
            "aliases": aliases,
            "type": etype_out,
            "created": created,
            "hub_synced": hub_synced,
            "graph_id": "user" if (was_self or eid.lower() == "user") else eid,
        }
    except Exception as exc:
        logger.exception("[memory_api] rename entity failed")
        return {"ok": False, "error": str(exc)[:300]}


@router.delete("/api/memory/v2/entities/{entity_id}")
async def delete_entity(entity_id: str) -> dict[str, Any]:
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    if eid.lower() in _PROTECTED_ENTITIES:
        return {"ok": False, "error": f"protected entity: {eid}"}
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id, type, name FROM entities WHERE id=?", (eid,)
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "not_found"}
        conn.execute(
            "DELETE FROM entity_merges WHERE source_entity_id=? OR target_entity_id=?",
            (eid, eid),
        )
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        conn.commit()
        conn.close()
        return {
            "ok": True,
            "deleted": eid,
            "type": row["type"],
            "name": row["name"],
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/memory/v2/entities/merge")
async def merge_entities(request: Request) -> dict[str, Any]:
    """Merge *source_id* into *target_id* (beliefs rewired, source retired)."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    source_id = str((body or {}).get("source_id") or "").strip()
    target_id = str((body or {}).get("target_id") or "").strip()
    if not source_id or not target_id:
        return {"ok": False, "error": "source_id and target_id required"}
    if source_id == target_id:
        return {"ok": False, "error": "source and target must differ"}
    if source_id.lower() in _PROTECTED_ENTITIES:
        return {"ok": False, "error": f"cannot merge protected source {source_id}"}
    try:
        conn = _conn()
        src = conn.execute(
            "SELECT id, name, aliases_json FROM entities WHERE id=?", (source_id,)
        ).fetchone()
        tgt = conn.execute(
            "SELECT id, name, aliases_json FROM entities WHERE id=?", (target_id,)
        ).fetchone()
        if not src or not tgt:
            conn.close()
            return {"ok": False, "error": "source or target not found"}

        try:
            src_aliases = json.loads(src["aliases_json"] or "[]")
        except Exception:
            src_aliases = []
        try:
            tgt_aliases = json.loads(tgt["aliases_json"] or "[]")
        except Exception:
            tgt_aliases = []
        for a in list(src_aliases) + [src["name"], source_id]:
            if a and a not in tgt_aliases:
                tgt_aliases.append(a)
        conn.execute(
            "UPDATE entities SET aliases_json=? WHERE id=?",
            (json.dumps(tgt_aliases, ensure_ascii=False), target_id),
        )
        # Rewire beliefs (slug + display name)
        for old in {source_id, src["name"]}:
            if not old:
                continue
            conn.execute(
                "UPDATE beliefs SET subject=? WHERE subject=?", (target_id, old)
            )
            conn.execute(
                "UPDATE beliefs SET object=? WHERE object=?", (target_id, old)
            )
        conn.execute(
            """UPDATE entities
               SET metadata_json = json_set(
                 COALESCE(NULLIF(metadata_json,''), '{}'),
                 '$.merged_into', ?
               )
               WHERE id = ?""",
            (target_id, source_id),
        )
        mid = "m_" + uuid.uuid4().hex[:16]
        now = time.time()
        conn.execute(
            """INSERT OR IGNORE INTO entity_merges
               (id, tenant_id, source_entity_id, target_entity_id, status,
                merge_tier, confidence, requested_at, resolved_at, metadata_json)
               VALUES (?, 'default', ?, ?, 'approved', 'ui_manual', 1.0, ?, ?, ?)""",
            (
                mid,
                source_id,
                target_id,
                now,
                now,
                json.dumps({"via": "memory_ui"}),
            ),
        )
        conn.commit()
        conn.close()
        return {
            "ok": True,
            "merge_id": mid,
            "source_id": source_id,
            "target_id": target_id,
            "status": "approved",
        }
    except Exception as exc:
        logger.exception("[memory_api] merge failed")
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/memory/v2/entities/link")
async def link_entities(request: Request) -> dict[str, Any]:
    """Create a belief edge subject --predicate--> object (links two nodes)."""
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    subject = str((body or {}).get("subject") or "").strip()
    predicate = str((body or {}).get("predicate") or "related_to").strip()
    obj = str((body or {}).get("object") or "").strip()
    if not subject or not obj:
        return {"ok": False, "error": "subject and object required"}
    if not predicate:
        predicate = "related_to"
    try:
        from kazma_core.memory.belief_mutation import mutate_belief
        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        primary = _conn()
        ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        ensure_ops_schema(ops)
        # Ensure both entities exist
        for eid, etype in ((subject, "concept"), (obj, "concept")):
            row = primary.execute(
                "SELECT id FROM entities WHERE id=?", (eid,)
            ).fetchone()
            if not row:
                primary.execute(
                    """INSERT OR IGNORE INTO entities
                       (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
                       VALUES (?, 'default', ?, ?, '[]', 0, '{}')""",
                    (eid, etype, eid.replace("_", " ")),
                )
        primary.commit()
        result = mutate_belief(
            primary,
            subject,
            predicate,
            obj,
            ops_conn=ops,
            predicate_type="set",
            confidence=0.9,
            importance=3,
            extraction_method="user_explicit",
            tenant_id="default",
        )
        ops.close()
        primary.close()
        return {"ok": True, "link": result, "subject": subject, "predicate": predicate, "object": obj}
    except Exception as exc:
        logger.exception("[memory_api] link failed")
        return {"ok": False, "error": str(exc)[:300]}


# ── Beliefs (edit + batch invalidate) ────────────────────────────────────


@router.patch("/api/memory/v2/beliefs/{belief_id}")
async def edit_belief(belief_id: str, request: Request) -> dict[str, Any]:
    """Operator correction of an active belief's triple (subject/predicate/object).

    In-place update of a currently-valid row so bad extractions can be fixed
    without losing the belief id. Clears the embedding when the object text
    changes (recall will re-embed on next path that needs it). FTS sync is
    handled by the beliefs_fts UPDATE trigger.
    """
    bid = (belief_id or "").strip()
    if not bid:
        return {"ok": False, "error": "belief_id required"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    body = body or {}

    def _opt(key: str) -> str | None:
        if key not in body or body[key] is None:
            return None
        s = str(body[key]).strip()
        return s if s else None

    new_subject = _opt("subject")
    new_predicate = _opt("predicate")
    new_object = _opt("object")
    new_ptype = _opt("predicate_type")
    if not any((new_subject, new_predicate, new_object, new_ptype)):
        return {
            "ok": False,
            "error": "provide subject, predicate, object, and/or predicate_type",
        }
    if new_ptype and new_ptype not in ("functional", "set", "state"):
        return {"ok": False, "error": "predicate_type must be functional|set|state"}

    try:
        conn = _conn()
        row = conn.execute(
            "SELECT id, subject, predicate, predicate_type, object, "
            "valid_until, invalidated_at, metadata_json "
            "FROM beliefs WHERE id=?",
            (bid,),
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "not_found"}
        if row["invalidated_at"] is not None or row["valid_until"] is not None:
            conn.close()
            return {"ok": False, "error": "belief is not active (invalidated or superseded)"}

        subject = new_subject if new_subject is not None else str(row["subject"])
        predicate = new_predicate if new_predicate is not None else str(row["predicate"])
        obj = new_object if new_object is not None else str(row["object"])
        ptype = new_ptype if new_ptype is not None else str(row["predicate_type"] or "set")
        if len(subject) > 200 or len(predicate) > 120 or len(obj) > 4000:
            conn.close()
            return {"ok": False, "error": "field too long"}

        object_changed = obj != str(row["object"])
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:
            meta = {}
        if not isinstance(meta, dict):
            meta = {}
        meta["operator_edit"] = {
            "at": time.time(),
            "previous": {
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "predicate_type": row["predicate_type"],
            },
        }

        # Null embedding when payload text changes so stale vectors don't rank.
        if object_changed:
            conn.execute(
                """UPDATE beliefs
                   SET subject=?, predicate=?, object=?, predicate_type=?,
                       extraction_method='user_explicit',
                       metadata_json=?,
                       embedding=NULL
                   WHERE id=?""",
                (
                    subject,
                    predicate,
                    obj,
                    ptype,
                    json.dumps(meta, ensure_ascii=False),
                    bid,
                ),
            )
        else:
            conn.execute(
                """UPDATE beliefs
                   SET subject=?, predicate=?, object=?, predicate_type=?,
                       extraction_method='user_explicit',
                       metadata_json=?
                   WHERE id=?""",
                (
                    subject,
                    predicate,
                    obj,
                    ptype,
                    json.dumps(meta, ensure_ascii=False),
                    bid,
                ),
            )
        conn.commit()
        conn.close()

        # Best-effort Neo4j edge cleanup (stale dual-write after operator edit)
        try:
            from kazma_core.memory.graph_backend import delete_belief_edge

            delete_belief_edge(
                belief_id=bid,
                subject=str(row["subject"]),
                predicate=str(row["predicate"]),
                obj=str(row["object"]),
            )
        except Exception:
            pass

        return {
            "ok": True,
            "id": bid,
            "belief": {
                "id": bid,
                "subject": subject,
                "predicate": predicate,
                "object": obj,
                "predicate_type": ptype,
            },
            "object_changed": object_changed,
        }
    except Exception as exc:
        logger.exception("[memory_api] edit belief failed")
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/memory/v2/beliefs/invalidate-batch")
async def invalidate_batch(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    ids = (body or {}).get("ids") or (body or {}).get("belief_ids") or []
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "ids[] required"}
    from kazma_core.memory.hygiene import invalidate_belief

    results = []
    ok_n = 0
    for bid in ids[:200]:
        r = invalidate_belief(str(bid), remove_graph=True)
        results.append(r)
        if r.get("ok"):
            ok_n += 1
    return {"ok": True, "invalidated": ok_n, "results": results}


# ── Hygiene ──────────────────────────────────────────────────────────────


@router.get("/api/memory/v2/hygiene/preview")
async def hygiene_preview() -> dict[str, Any]:
    """Preview safe cleanup candidates (no writes)."""
    try:
        conn = _conn()
        empty = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT e.id, e.type, e.name, {_belief_count_sql()} AS belief_count
                FROM entities e
                WHERE {_belief_count_sql()} = 0
                  AND LOWER(e.id) NOT IN ('user','assistant','kazma','mubder')
                ORDER BY e.name
                LIMIT 200
                """
            ).fetchall()
        ]
        isolated = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT e.id, e.type, e.name,
                       {_belief_count_sql()} AS belief_count,
                       {_entity_degree_sql()} AS linked_others
                FROM entities e
                WHERE {_belief_count_sql()} > 0
                  AND {_entity_degree_sql()} = 0
                  AND LOWER(e.id) NOT IN ('user','assistant')
                ORDER BY belief_count DESC
                LIMIT 100
                """
            ).fetchall()
        ]
        # Near-dup noted (same first 160 normalized chars)
        noted = conn.execute(
            """
            SELECT id, object, valid_from FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
              AND predicate = 'noted'
            ORDER BY valid_from DESC
            LIMIT 500
            """
        ).fetchall()
        groups: dict[str, list[sqlite3.Row]] = {}
        import re

        for r in noted:
            key = re.sub(r"\s+", " ", (r["object"] or "").strip().lower())[:160]
            if not key:
                continue
            groups.setdefault(key, []).append(r)
        near_dups = []
        for key, members in groups.items():
            if len(members) <= 1:
                continue
            members = sorted(
                members, key=lambda x: float(x["valid_from"] or 0), reverse=True
            )
            near_dups.append(
                {
                    "keep_id": members[0]["id"],
                    "drop_ids": [m["id"] for m in members[1:]],
                    "preview": key[:80],
                    "count": len(members),
                }
            )
        dead = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL"
        ).fetchone()[0]
        conn.close()
        return {
            "ok": True,
            "empty_entities": empty,
            "isolated_entities": isolated,
            "near_dup_noted": near_dups[:50],
            "invalidated_belief_count": dead,
        }
    except Exception as exc:
        logger.exception("[memory_api] hygiene preview failed")
        return {"ok": False, "error": str(exc)[:300]}


@router.post("/api/memory/v2/hygiene/run")
async def hygiene_run(request: Request) -> dict[str, Any]:
    """Run selected hygiene actions.

    Body::
      {
        "purge_empty_entities": true,
        "invalidate_near_dup_noted": true,
        "archive_invalidated": false
      }
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    body = body or {}
    out: dict[str, Any] = {"ok": True, "actions": {}}

    if body.get("purge_empty_entities"):
        preview = await hygiene_preview()
        deleted = []
        for e in preview.get("empty_entities") or []:
            r = await delete_entity(e["id"])
            if r.get("ok"):
                deleted.append(e["id"])
        out["actions"]["purge_empty_entities"] = {
            "deleted": deleted,
            "count": len(deleted),
        }

    if body.get("invalidate_near_dup_noted"):
        preview = await hygiene_preview()
        drop_ids: list[str] = []
        for g in preview.get("near_dup_noted") or []:
            drop_ids.extend(g.get("drop_ids") or [])
        from kazma_core.memory.hygiene import invalidate_belief

        n = 0
        for bid in drop_ids:
            r = invalidate_belief(bid, remove_graph=True)
            if r.get("ok"):
                n += 1
        out["actions"]["invalidate_near_dup_noted"] = {
            "invalidated": n,
            "ids": drop_ids[:100],
        }

    if body.get("archive_invalidated"):
        # Soft-hard: move invalidated rows to beliefs_archive then DELETE
        try:
            conn = _conn()
            now = time.time()
            rows = conn.execute(
                """
                SELECT * FROM beliefs
                WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL
                LIMIT 2000
                """
            ).fetchall()
            n = 0
            for r in rows:
                payload = {k: r[k] for k in r.keys()}
                conn.execute(
                    """INSERT OR IGNORE INTO beliefs_archive
                       (id, tenant_id, original_belief_json, archived_at)
                       VALUES (?, ?, ?, ?)""",
                    (
                        r["id"],
                        r["tenant_id"] if "tenant_id" in r.keys() else "default",
                        json.dumps(payload, default=str),
                        now,
                    ),
                )
                conn.execute("DELETE FROM beliefs WHERE id=?", (r["id"],))
                n += 1
            conn.commit()
            conn.close()
            out["actions"]["archive_invalidated"] = {"archived": n}
        except Exception as exc:
            out["actions"]["archive_invalidated"] = {"error": str(exc)[:200]}

    return out


def mount_memory_api(app: Any) -> None:
    """Include API router on the FastAPI app."""
    app.include_router(router)
