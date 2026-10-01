"""Memory admin API — beliefs, entities, merge/link, hygiene.

Backs the ``/memory`` operator UI. Prefer these routes over raw SQL and
over hoping the chat agent discovers the right tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from kazma_core.errors import safe_error

logger = logging.getLogger(__name__)

_PROTECTED_ENTITIES = frozenset({"user", "assistant", "kazma", "mubder"})

router = APIRouter(tags=["memory-admin"])


def _is_protected(conn: sqlite3.Connection, eid: str) -> bool:
    """True if `eid` is protected from deletion / merge-as-source.

    The hardcoded floor (_PROTECTED_ENTITIES: user/assistant/kazma/mubder) is
    always protected. F3 adds a per-row `is_protected` flag so an operator can
    extend protection to any entity (e.g. mark `shipx` protected).
    """
    if str(eid or "").lower() in _PROTECTED_ENTITIES:
        return True
    try:
        row = conn.execute(
            "SELECT is_protected FROM entities WHERE id=?", (eid,)
        ).fetchone()
        return bool(row and int(row["is_protected"] or 0) == 1)
    except Exception:
        return False


def _would_orphan(
    conn: sqlite3.Connection, belief_ids: list[str], *, tenant_id: str | None = None
) -> list[str]:
    """F3: return entity ids that would have ZERO live edges if the given
    beliefs were invalidated. Used to warn the operator before an
    unlink/invalidate/repoint strands a node. A node is "orphaned" only if it
    currently has live edges AND all of them are in `belief_ids`.

    Pass only the caller's own belief ids, and its ``tenant_id``: the answer
    names the beliefs' endpoints, and an endpoint may be a fact's text.
    """
    if not belief_ids:
        return []
    try:
        placeholders = ",".join("?" * len(belief_ids))
        scoped = tenant_id not in (None, "", "default")
        scope = " AND tenant_id = ?" if scoped else ""
        params = (*belief_ids, tenant_id) if scoped else tuple(belief_ids)
        # Active beliefs NOT in the to-remove set — endpoints here stay anchored.
        surviving = conn.execute(
            f"""SELECT DISTINCT subject AS eid FROM beliefs
                WHERE invalidated_at IS NULL AND valid_until IS NULL
                  AND id NOT IN ({placeholders}){scope}
                UNION
                SELECT DISTINCT object AS eid FROM beliefs
                WHERE invalidated_at IS NULL AND valid_until IS NULL
                  AND id NOT IN ({placeholders}){scope}""",
            params * 2,
        ).fetchall()
        surviving_ids = {r["eid"] for r in surviving if r["eid"]}
        # Endpoints touched by the to-remove beliefs — candidates for orphaning.
        touched = conn.execute(
            f"""SELECT DISTINCT subject AS eid FROM beliefs
                WHERE invalidated_at IS NULL AND valid_until IS NULL
                  AND id IN ({placeholders}){scope}
                UNION
                SELECT DISTINCT object AS eid FROM beliefs
                WHERE invalidated_at IS NULL AND valid_until IS NULL
                  AND id IN ({placeholders}){scope}""",
            params * 2,
        ).fetchall()
        return sorted(
            r["eid"] for r in touched
            if r["eid"] and r["eid"] not in surviving_ids
        )
    except Exception:
        logger.debug("[memory_api] _would_orphan failed", exc_info=True)
        return []


# ══════════════════════════════════════════════════════════════════════════
# Undo store — short-window reversal for reversible memory mutations
# ══════════════════════════════════════════════════════════════════════════
# In-process LRU mapping an opaque token → an async restore closure. Covers
# invalidate-batch, link, unlink, edit, delete-entity. NOT durability: the
# window is 60s, the cap is 50, and entries are lost on restart. The point is
# the "Undo" affordance on the toast — for true safety, restore from a backup
# (Maintenance deck). Merge is intentionally NOT undoable (an identity rewrite
# across many beliefs is not reliably reversible); it returns a full receipt
# instead so the operator can see exactly what moved.
_UNDO_TTL_SECONDS = 60.0
_UNDO_CAP = 50
_undo_store: dict[str, dict[str, Any]] = {}


def register_undo(restore: Any, *, label: str, kind: str) -> str:
    """Register a restore closure and return an undo token.

    Args:
        restore: an ``async def``/coroutine factory that replays the inverse
                 mutation. Called with no args by ``POST /undo/{token}``.
        label:   human-readable summary for logs ("invalidated 3 beliefs").
        kind:    op category (invalidate / link / edit / delete-entity).

    Returns:
        Opaque token string.
    """
    token = f"undo-{uuid.uuid4().hex[:16]}"
    _undo_store[token] = {
        "restore": restore,
        "label": label,
        "kind": kind,
        "tenant": _memory_tenant_id(),  # M-05: bind to the originating tenant
        "expires_at": time.time() + _UNDO_TTL_SECONDS,
    }
    # LRU evict (dict preserves insertion order in Py3.7+).
    while len(_undo_store) > _UNDO_CAP:
        _undo_store.pop(next(iter(_undo_store)))
    return token


def _consume_undo(token: str) -> dict[str, Any] | None:
    """Pop a non-expired undo entry, or None if missing/expired.

    M-05: cross-tenant redemption is refused (token is bound to the tenant
    that created it)."""
    entry = _undo_store.pop(token, None)
    if entry is None:
        return None
    if time.time() > float(entry.get("expires_at") or 0):
        return None
    if str(entry.get("tenant") or "default") != _memory_tenant_id():
        return None
    return entry


def _fts_match_expr(text: str) -> str:
    """Build a safe FTS5 MATCH expression (alnum tokens OR-joined).

    Mirrors ``kazma_core.memory.recall._fts_match_query`` but kept local to
    the UI layer so it doesn't couple to recall's private helpers. Returns
    "" when no usable tokens remain (caller then falls back to LIKE).
    """
    toks: list[str] = []
    for part in (text or "").lower().replace("-", " ").split():
        cleaned = "".join(c for c in part if c.isalnum())
        if len(cleaned) >= 2 and cleaned not in toks:
            toks.append(cleaned)
    return " OR ".join(toks)


def _conn() -> sqlite3.Connection:
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    c = sqlite3.connect(primary_memory_db(), check_same_thread=False, timeout=30)
    c.row_factory = sqlite3.Row
    ensure_primary_schema(c)
    return c


#: What :func:`_read_json` answers for a body that is not JSON; each route
#: decides what that means, as it did when it parsed the body itself.
_INVALID_JSON = object()


async def _read_json(request: Request) -> Any:
    """The request's JSON body, or ``_INVALID_JSON``.

    The routes below read the body here, on the loop, and do every
    database step in a thread (AGENTS §35).
    """
    try:
        return await request.json()
    except ValueError:
        return _INVALID_JSON


def _memory_tenant_id() -> str:
    """Active tenant id for memory reads/writes.

    Phase 4 tenant correctness. When ``KAZMA_MEMORY_ENFORCE_TENANT`` is unset
    (default), returns ``"default"`` so existing single-tenant deployments
    behave exactly as before — every memory row is in the ``default`` tenant
    and no query is narrowed. When the flag is on (``1``/``true``), returns
    the request-scoped tenant from ``require_tenant_id()`` (set by the auth
    middleware from verified JWT/opaque-session claims), so tenant A's memory
    is isolated from tenant B's.

    Read paths use this in ``WHERE tenant_id = ?``; write paths use it on
    every INSERT. The flag is read live so a Settings/env change takes effect
    without a restart.
    """
    import os

    enforce = str(os.environ.get("KAZMA_MEMORY_ENFORCE_TENANT", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )
    if not enforce:
        try:
            from kazma_core.tenant_isolation import multi_user_or_production

            enforce = bool(multi_user_or_production())
        except Exception:
            # Audit F-05: a failed probe used to silently disable isolation.
            # Treat "cannot tell" as "enforce" — a single-tenant install is
            # unaffected (its rows are all in `default`), while a multi-tenant
            # one stays scoped.
            logger.warning(
                "[tenant] multi-user probe failed — enforcing isolation",
                exc_info=True,
            )
            enforce = True
    if enforce:
        try:
            from kazma_core.tenant_isolation import require_tenant_id

            return require_tenant_id()
        except Exception:
            # Do not fall back to the shared "default" tenant — that would
            # leak another tenant's rows when isolation is required.
            return "__unscoped__"
    return "default"


#: The id-keyed memory tables a route may be handed an id for, and the query
#: that names the row's tenant.
_OWNER_SQL = {
    "entities": "SELECT tenant_id FROM entities WHERE id=?",
    "beliefs": "SELECT tenant_id FROM beliefs WHERE id=?",
    "entity_merges": "SELECT tenant_id FROM entity_merges WHERE id=?",
    "graph_associations": "SELECT tenant_id FROM graph_associations WHERE id=?",
}


def _row_tenant_ok(conn: sqlite3.Connection, table: str, row_id: str) -> bool:
    """Fail-closed tenant gate for an id-keyed memory route (audit M-05).

    The install's own tenant ('default') always passes -- single-user
    behaviour. Under enforcement, a row of another tenant, a missing row and a
    failed lookup all read as not found: ids are global, rows are not.
    ``tests/test_memory_routes_tenant_scope.py`` calls every memory route with
    another tenant's ids.
    """
    tid = _memory_tenant_id()
    if tid == "default":
        return True
    try:
        row = conn.execute(_OWNER_SQL[table], (row_id,)).fetchone()
    except sqlite3.Error:
        return False
    return bool(row) and str(row[0] or "default") == tid


def _entity_tenant_ok(conn: sqlite3.Connection, eid: str) -> bool:
    """:func:`_row_tenant_ok` for an entity id."""
    return _row_tenant_ok(conn, "entities", eid)


def _belief_tenant_ok(conn: sqlite3.Connection, bid: str) -> bool:
    """:func:`_row_tenant_ok` for a belief id."""
    return _row_tenant_ok(conn, "beliefs", bid)


def _belief_count_sql() -> str:
    """Canonical active-belief count for an ``entities e`` row.

    Single source of truth lives in kazma_core.memory.entity_counts — the
    materialized-column recompute must never drift from this read path.
    """
    from kazma_core.memory.entity_counts import belief_count_sql

    return belief_count_sql()


def _entity_degree_sql() -> str:
    """Canonical entity-only graph degree for an ``entities e`` row.

    Single source of truth lives in kazma_core.memory.entity_counts (the
    2026-08-24 drift incident: two handwritten copies diverged when the
    payload-object fix landed in only one of them).
    """
    from kazma_core.memory.entity_counts import entity_degree_sql

    return entity_degree_sql()


# ── Page ─────────────────────────────────────────────────────────────────


def register_memory_page(app: Any, templates: Any, agent: Any) -> None:
    """Mount HTML page on the main FastAPI app."""

    @app.get("/memory", response_class=HTMLResponse)
    def memory_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "memory.html",
            {
                "config": getattr(agent, "config", None),
                "active_page": "memory",
            },
        )


# ── Health snapshot (compact for page header) ────────────────────────────


@router.get("/api/memory/v2/vocab")
async def memory_vocab() -> dict[str, Any]:
    """F4: existing vocabulary for the chip-based link dialog.

    Returns the predicates and entities already in the store so the Link UI
    can offer them as clickable chips instead of forcing free-text (the root
    cause of the reset-explosion mess: each new link invented a new predicate
    name). Predicates are ordered by frequency so the common ones surface
    first; entities by belief_count. Tenant-scoped like every other route.
    """
    return await asyncio.to_thread(_memory_vocab_sync)


def _memory_vocab_sync() -> dict[str, Any]:
    """Blocking half of :func:`memory_vocab` -- runs off the event loop."""
    try:
        conn = _conn()
        tid = _memory_tenant_id()
        tfilter = " AND tenant_id = ?" if tid != "default" else ""
        tparam: tuple = (tid,) if tid != "default" else ()
        # Predicates: DISTINCT name + type + count, frequency-sorted. Only
        # counts active beliefs so stale predicates don't dominate the chips.
        preds = [
            dict(r)
            for r in conn.execute(
                f"""SELECT predicate AS name, predicate_type AS type,
                           COUNT(*) AS cnt
                    FROM beliefs
                    WHERE invalidated_at IS NULL AND valid_until IS NULL
                    {tfilter}
                    GROUP BY predicate, predicate_type
                    ORDER BY cnt DESC, predicate ASC
                    LIMIT 200""",
                tparam,
            ).fetchall()
        ]
        # Entities: id + name + type + belief_count, count-sorted. The hub
        # (user) is pinned at the top by its high belief_count naturally.
        ents = [
            dict(r)
            for r in conn.execute(
                f"""SELECT e.id, e.name, e.type,
                           COALESCE(e.belief_count,
                             (SELECT COUNT(*) FROM beliefs b
                              WHERE b.invalidated_at IS NULL AND b.valid_until IS NULL
                                AND (b.subject=e.id OR b.object=e.id))
                           ) AS belief_count
                    FROM entities e
                    WHERE 1=1 {('AND e.tenant_id = ?' if tid != 'default' else '')}
                    ORDER BY belief_count DESC, e.name ASC
                    LIMIT 200""",
                tparam,
            ).fetchall()
        ]
        conn.close()
        return {"ok": True, "predicates": preds, "entities": ents}
    except Exception as exc:
        logger.exception("[memory_api] vocab failed")
        return {"ok": False, "error": safe_error(exc), "predicates": [], "entities": []}


@router.get("/api/memory/v2/admin/summary")
async def memory_admin_summary() -> dict[str, Any]:
    return await asyncio.to_thread(_memory_admin_summary_sync)


def _memory_admin_summary_sync() -> dict[str, Any]:
    """Blocking half of :func:`memory_admin_summary` -- runs off the event loop."""
    try:
        conn = _conn()
        # Phase 4: scope every count by the active tenant when enforcement is
        # on; unscoped (today's behavior) otherwise. The tenant filter uses
        # the tenant_id-leading indexes so it stays fast either way.
        tid = _memory_tenant_id()
        scoped = tid != "default"
        tfilter = " AND tenant_id = ?" if scoped else ""
        tparam: tuple = (tid,) if scoped else ()

        # Phase 3: prefer the materialized columns for the empty/isolated
        # counts when no entity is stale; fall back to the live correlated
        # subqueries otherwise (correctness over speed).
        try:
            stale_n = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE belief_count = -1" + tfilter,
                tparam,
            ).fetchone()[0]
        except Exception:
            stale_n = 1
        count_expr = "e.belief_count" if stale_n == 0 else _belief_count_sql()
        degree_expr = "e.graph_degree" if stale_n == 0 else _entity_degree_sql()

        live = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL" + tfilter,
            tparam,
        ).fetchone()[0]
        dead = conn.execute(
            "SELECT COUNT(*) FROM beliefs WHERE invalidated_at IS NOT NULL OR valid_until IS NOT NULL" + tfilter,
            tparam,
        ).fetchone()[0]
        ents = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE 1=1" + tfilter, tparam
        ).fetchone()[0]
        empty = conn.execute(
            f"SELECT COUNT(*) FROM entities e WHERE {count_expr} = 0" + tfilter,
            tparam,
        ).fetchone()[0]
        isolated = conn.execute(
            "SELECT COUNT(*) FROM entities e "
            "WHERE " + count_expr + " > 0 AND " + degree_expr + " = 0 "
            "AND LOWER(e.id) NOT IN ('user','assistant')"
            + tfilter,
            tparam,
        ).fetchone()[0]
        try:
            eps = conn.execute(
                "SELECT COUNT(*) FROM episodes WHERE 1=1" + tfilter, tparam
            ).fetchone()[0]
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
        return {"ok": False, "error": safe_error(exc)}


# ── Undo ─────────────────────────────────────────────────────────────────


@router.post("/api/memory/v2/undo/{token}")
async def undo_action(token: str) -> dict[str, Any]:
    """Replay the inverse of a recent reversible mutation (60s window).

    Tokens are single-use and expire. Returns the restore closure's result.
    """
    entry = _consume_undo(token.strip())
    if entry is None:
        return {"ok": False, "error": "undo token not found or expired"}
    try:
        restore = entry["restore"]
        result = await restore() if callable(restore) else None
        logger.info(
            "[memory_api] undo %s (%s): %s",
            entry.get("kind"),
            entry.get("label"),
            "restored",
        )
        return {
            "ok": True,
            "kind": entry.get("kind"),
            "label": entry.get("label"),
            "result": result,
        }
    except Exception as exc:
        logger.exception("[memory_api] undo %s failed", entry.get("kind"))
        return {"ok": False, "error": safe_error(exc)}


# ── Entities ─────────────────────────────────────────────────────────────


@router.get("/api/memory/v2/entities")
async def list_entities(
    q: str = "",
    limit: int = 100,
    offset: int = 0,
    empty_only: bool = False,
    isolated_only: bool = False,
) -> dict[str, Any]:
    return await asyncio.to_thread(_list_entities_sync, q, limit, offset, empty_only, isolated_only)


def _list_entities_sync(q: str, limit: int, offset: int, empty_only: bool, isolated_only: bool) -> dict[str, Any]:
    """Blocking half of :func:`list_entities` -- runs off the event loop."""
    try:
        conn = _conn()
        lim = max(1, min(int(limit or 100), 300))
        off = max(0, int(offset or 0))
        # Phase 3: use the materialized belief_count / graph_degree columns
        # when no entity is stale (-1). If any are stale (first boot after
        # upgrade, or after a bulk clear/delete), fall back to the live
        # correlated subqueries for correctness. The backfill + write-path
        # hooks keep stale rows rare in steady state.
        try:
            stale_n = conn.execute(
                "SELECT COUNT(*) FROM entities WHERE belief_count = -1"
            ).fetchone()[0]
        except Exception:
            stale_n = 1  # column missing → treat as stale (use live subquery)
        if stale_n == 0:
            count_expr = "e.belief_count"
            degree_expr = "e.graph_degree"
        else:
            count_expr = _belief_count_sql()
            degree_expr = _entity_degree_sql()
        # Phase 4: scope by tenant when enforcement is on. The base WHERE
        # carries the filter; FTS/LIKE/empty/isolated clauses append to it.
        tid = _memory_tenant_id()
        where = " FROM entities e WHERE 1=1"
        params: list[Any] = []
        if tid != "default":
            where += " AND e.tenant_id = ?"
            params.append(tid)
        matched_via: str | None = None
        query = (q or "").strip()
        if query:
            # Prefer entities_fts (name + type + aliases_json) for diacritic-
            # insensitive, alias-aware search; fall back to LIKE if FTS is
            # unavailable or the query has no usable tokens.
            match_q = _fts_match_expr(query)
            fts_rowids: list[int] | None = None
            if match_q:
                try:
                    fts_rows = conn.execute(
                        "SELECT e.rowid FROM entities_fts "
                        "JOIN entities e ON e.rowid = entities_fts.rowid "
                        "WHERE entities_fts MATCH ? LIMIT 2000",
                        (match_q,),
                    ).fetchall()
                    fts_rowids = [int(r["rowid"]) for r in fts_rows]
                except Exception:
                    fts_rowids = None  # FTS unavailable → LIKE fallback
            if fts_rowids is not None:
                matched_via = "fts"
                if not fts_rowids:
                    # FTS matched nothing — return empty.
                    conn.close()
                    return {
                        "ok": True, "count": 0, "total": 0, "offset": off,
                        "limit": lim, "entities": [], "matched_via": "fts",
                    }
                ph = ",".join("?" for _ in fts_rowids)
                # Also allow an exact id match alongside FTS (ids aren't in FTS).
                where += f" AND (e.rowid IN ({ph}) OR LOWER(e.id) LIKE ?)"
                params.extend(fts_rowids)
                params.append(f"%{query.lower()}%")
            else:
                # FTS unavailable or no usable tokens → LIKE on id/name/type.
                ql = f"%{query.lower()}%"
                where += " AND (LOWER(e.id) LIKE ? OR LOWER(e.name) LIKE ? OR LOWER(e.type) LIKE ?)"
                params.extend([ql, ql, ql])
                matched_via = "like"
        if empty_only:
            where += f" AND {count_expr} = 0"
        if isolated_only:
            where += (
                f" AND {count_expr} > 0 AND {degree_expr} = 0"
                " AND LOWER(e.id) NOT IN ('user','assistant')"
            )
        # Total count for the pager (same WHERE, no ORDER/LIMIT). Uses the
        # tenant_id-leading indexes; cheap relative to the row query.
        total = conn.execute(f"SELECT COUNT(*){where}", params).fetchone()[0]

        sql = (
            f"SELECT e.id, e.type, e.name, e.is_high_stakes, e.is_protected,"
            f" e.aliases_json, e.metadata_json, {count_expr} AS belief_count,"
            f" {degree_expr} AS linked_others{where}"
            " ORDER BY belief_count DESC, linked_others ASC, e.name ASC LIMIT ? OFFSET ?"
        )
        rows = [dict(r) for r in conn.execute(sql, [*params, lim, off]).fetchall()]
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
            # ``core``: always protected, never unprotectable (the page shows
            # no switch for it); ``protected``: core or the owner's flag.
            r["core"] = str(r.get("id") or "").lower() in _PROTECTED_ENTITIES
            r["protected"] = r["core"] or int(r.get("is_protected") or 0) == 1
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
        return {
            "ok": True,
            "count": len(rows),
            "total": int(total),
            "offset": off,
            "limit": lim,
            "entities": rows,
            "matched_via": matched_via,
        }
    except Exception as exc:
        logger.exception("[memory_api] list entities failed")
        return {"ok": False, "entities": [], "error": safe_error(exc)}


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
    return await asyncio.to_thread(_rename_entity_sync, entity_id, await _read_json(request))


def _rename_entity_sync(entity_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`rename_entity` -- runs off the event loop."""
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    if len(eid) > 200:
        return {"ok": False, "error": "entity_id too long"}
    body = payload
    if body is _INVALID_JSON:
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
        if row and not _entity_tenant_ok(conn, eid):
            # Another tenant's entity: ids are global, rows are not.
            conn.close()
            return {"ok": False, "error": "not_found"}
        created = False
        if not row:
            # Promote a graph node / hub id into a real entity so the
            # display name survives reloads (virtual facts, user hub).
            etype = "person" if eid.lower() in ("user", "you", "assistant") else "concept"
            conn.execute(
                """INSERT INTO entities
                   (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
                   VALUES (?, ?, ?, ?, '[]', ?, '{}')""",
                (eid, _memory_tenant_id(), etype, new_name, 1 if etype == "person" else 0),
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
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/entities/{entity_id}/protect")
async def protect_entity(entity_id: str, request: Request) -> dict[str, Any]:
    """F3: toggle the per-entity `is_protected` flag.

    A protected entity cannot be deleted or used as a merge source. The
    hardcoded floor (user/assistant/kazma/mubder) is always protected and
    cannot be unprotected here. Body: ``{"protected": true|false}``.
    """
    return await asyncio.to_thread(_protect_entity_sync, entity_id, await _read_json(request))


def _protect_entity_sync(entity_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`protect_entity` -- runs off the event loop."""
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    body = {} if payload is _INVALID_JSON else payload
    want = bool(body.get("protected"))
    # The hardcoded floor is always protected — reject attempts to clear it
    # via this route so the operator can't accidentally unprotect the hub.
    # Checked before the row lookup so it holds even if the core entity has no
    # row yet (the core set is policy, not data-dependent).
    if not want and str(eid).lower() in _PROTECTED_ENTITIES:
        return {"ok": False, "error": f"cannot unprotect core entity: {eid}"}
    try:
        conn = _conn()
        if not _entity_tenant_ok(conn, eid):
            conn.close()
            return {"ok": False, "error": "not_found"}
        row = conn.execute(
            "SELECT id, is_protected FROM entities WHERE id=?", (eid,)
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "not_found"}
        conn.execute(
            "UPDATE entities SET is_protected=? WHERE id=?", (1 if want else 0, eid)
        )
        conn.commit()
        conn.close()
        return {"ok": True, "id": eid, "protected": want}
    except Exception as exc:
        logger.exception("[memory_api] protect entity failed")
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/entities/{entity_id}/major")
async def set_major_entity(entity_id: str, request: Request) -> dict[str, Any]:
    """Mark/unmark an entity as a MAJOR node (bigger + distinct color on canvas).

    The operator's mental model: Mubder is the master node; big projects
    (kazma, shipx, kca) are MAJOR. This flag makes them render bigger and
    with a distinct color, and grouped sub-nodes attach to them visually.
    Body: ``{"major": true|false}``.
    """
    return await asyncio.to_thread(_set_major_entity_sync, entity_id, await _read_json(request))


def _set_major_entity_sync(entity_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`set_major_entity` -- runs off the event loop."""
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    body = {} if payload is _INVALID_JSON else payload
    want = bool(body.get("major"))
    try:
        conn = _conn()
        if not _entity_tenant_ok(conn, eid):
            conn.close()
            return {"ok": False, "error": "not_found"}
        row = conn.execute("SELECT id FROM entities WHERE id=?", (eid,)).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "not_found"}
        conn.execute("UPDATE entities SET is_major=? WHERE id=?", (1 if want else 0, eid))
        conn.commit()
        conn.close()
        return {"ok": True, "id": eid, "major": want}
    except Exception as exc:
        logger.exception("[memory_api] set major failed")
        return {"ok": False, "error": safe_error(exc)}


@router.delete("/api/memory/v2/entities/{entity_id}")
async def delete_entity(entity_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_delete_entity_sync, entity_id)


def _delete_entity_sync(entity_id: str) -> dict[str, Any]:
    """Blocking half of :func:`delete_entity` -- runs off the event loop."""
    eid = (entity_id or "").strip()
    if not eid:
        return {"ok": False, "error": "entity_id required"}
    conn = _conn()
    # F3: protection covers the hardcoded floor AND the per-row is_protected
    # flag. Resolved against a connection so the per-row flag is read.
    if not _entity_tenant_ok(conn, eid):
        conn.close()
        return {"ok": False, "error": "not_found"}
    if _is_protected(conn, eid):
        conn.close()
        return {"ok": False, "error": f"protected entity: {eid}"}
    try:
        row = conn.execute(
            "SELECT id, type, name, aliases_json, metadata_json, is_high_stakes, is_protected "
            "FROM entities WHERE id=?",
            (eid,),
        ).fetchone()
        if not row:
            conn.close()
            return {"ok": False, "error": "not_found"}
        # Snapshot for undo (restore the exact row).
        snap = {
            "id": row["id"],
            "type": row["type"],
            "name": row["name"],
            "aliases_json": row["aliases_json"] or "[]",
            "metadata_json": row["metadata_json"] or "{}",
            "is_high_stakes": int(row["is_high_stakes"] or 0),
            "is_protected": int(row["is_protected"] or 0),
        }
        try:
            from kazma_core.memory.entity_resolution import preserve_merge_ledger

            preserve_merge_ledger(conn, eid, reason="entity_delete")
        except Exception:
            logger.debug("[memory_api] merge-ledger archive skipped", exc_info=True)
        conn.execute(
            "DELETE FROM entity_merges WHERE source_entity_id=? OR target_entity_id=?",
            (eid, eid),
        )
        # Phase 3: entities that co-occurred with this one lose a degree once
        # it's gone. Mark them stale (-1) so the read path recomputes them on
        # next access, rather than tracking the delta precisely here. (Beliefs
        # referencing eid by subject/object are left in place — only the
        # entity shell is removed.)
        try:
            ename = row["name"]
            co = conn.execute(
                "SELECT DISTINCT CASE WHEN subject=? THEN object ELSE subject END AS other "
                "FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL "
                "AND (subject=? OR object=? OR subject=? OR object=?)",
                (eid, eid, eid, ename, ename),
            ).fetchall()
            other_ids = [str(r["other"]) for r in co if r["other"]]
            if other_ids:
                placeholders = ",".join("?" for _ in other_ids)
                conn.execute(
                    f"UPDATE entities SET belief_count=-1 WHERE id IN ({placeholders})",
                    other_ids,
                )
        except Exception:
            logger.debug("[memory_api] delete co-occur stale-mark failed", exc_info=True)
        conn.execute("DELETE FROM entities WHERE id=?", (eid,))
        conn.commit()
        conn.close()

        def _restore_entity_sync() -> dict[str, Any]:
            c = _conn()
            c.execute(
                "INSERT OR IGNORE INTO entities "
                "(id, tenant_id, type, name, aliases_json, metadata_json, is_high_stakes, is_protected) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snap["id"],
                    _memory_tenant_id(),
                    snap["type"],
                    snap["name"],
                    snap["aliases_json"],
                    snap["metadata_json"],
                    snap["is_high_stakes"],
                    snap["is_protected"],
                ),
            )
            c.commit()
            c.close()
            return {"restored": snap["id"]}

        async def _restore_entity() -> dict[str, Any]:
            return await asyncio.to_thread(_restore_entity_sync)

        undo_token = register_undo(
            _restore_entity,
            label=f"deleted entity {eid}",
            kind="delete-entity",
        )
        return {
            "ok": True,
            "deleted": eid,
            "type": row["type"],
            "name": row["name"],
            "undo_token": undo_token,
            "receipt": {"entities_deleted": 1},
        }
    except Exception as exc:
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/entities/merge")
async def merge_entities(request: Request) -> dict[str, Any]:
    """Merge *source_id* into *target_id* (beliefs rewired, source retired)."""
    return await asyncio.to_thread(_merge_entities_sync, await _read_json(request))


def _merge_entities_sync(payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`merge_entities` -- runs off the event loop."""
    body = payload
    if body is _INVALID_JSON:
        return {"ok": False, "error": "invalid JSON"}
    source_id = str((body or {}).get("source_id") or "").strip()
    target_id = str((body or {}).get("target_id") or "").strip()
    if not source_id or not target_id:
        return {"ok": False, "error": "source_id and target_id required"}
    if source_id == target_id:
        return {"ok": False, "error": "source and target must differ"}
    conn = _conn()
    # F3: protection covers the hardcoded floor AND the per-row is_protected flag.
    if _is_protected(conn, source_id):
        conn.close()
        return {"ok": False, "error": f"cannot merge protected source {source_id}"}
    try:
        src = conn.execute(
            "SELECT id, tenant_id, name, aliases_json FROM entities WHERE id=?", (source_id,)
        ).fetchone()
        tgt = conn.execute(
            "SELECT id, tenant_id, name, aliases_json FROM entities WHERE id=?", (target_id,)
        ).fetchone()
        if not src or not tgt:
            conn.close()
            return {"ok": False, "error": "source or target not found"}
        # M-05/M-07: cross-tenant merges are refused; the belief rewire below
        # is scoped to the source entity's tenant so it can never touch
        # another tenant's rows.
        src_tid = str(src["tenant_id"] or "default")
        tgt_tid = str(tgt["tenant_id"] or "default")
        active_tid = _memory_tenant_id()
        if active_tid != "default" and (src_tid != active_tid or tgt_tid != active_tid):
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
        # Rewire beliefs (slug + display name) and count how many moved so the
        # receipt can show "N beliefs rewired" (undo is intentionally NOT
        # offered — an identity rewrite across many beliefs is not reliably
        # reversible; restore from a backup if needed).
        beliefs_rewired = 0
        for old in {source_id, src["name"]}:
            if not old:
                continue
            cur = conn.execute(
                "UPDATE beliefs SET subject=? WHERE subject=? AND tenant_id=?",
                (target_id, old, src_tid),
            )
            beliefs_rewired += int(cur.rowcount or 0)
            cur = conn.execute(
                "UPDATE beliefs SET object=? WHERE object=? AND tenant_id=?",
                (target_id, old, src_tid),
            )
            beliefs_rewired += int(cur.rowcount or 0)
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
               VALUES (?, ?, ?, ?, 'approved', 'ui_manual', 1.0, ?, ?, ?)""",
            (
                mid,
                _memory_tenant_id(),
                source_id,
                target_id,
                now,
                now,
                json.dumps({"via": "memory_ui"}),
            ),
        )
        # Phase 3: the merge rewired beliefs (source → target), so recompute
        # the materialized counts for BOTH entities. Source's count drops to
        # 0 (its beliefs were reassigned); target's rises by the rewired count.
        try:
            from kazma_core.memory.entity_counts import recompute_entity_counts

            recompute_entity_counts(conn, [source_id, target_id])
        except Exception:
            logger.debug("[memory_api] merge count recompute failed", exc_info=True)
        conn.commit()
        conn.close()
        return {
            "ok": True,
            "merge_id": mid,
            "source_id": source_id,
            "target_id": target_id,
            "status": "approved",
            # No undo_token — identity rewrite is not reliably reversible.
            "receipt": {
                "beliefs_rewired": beliefs_rewired,
                "note": "merge is not reversible; restore from a backup if needed",
            },
        }
    except Exception as exc:
        logger.exception("[memory_api] merge failed")
        return {"ok": False, "error": safe_error(exc)}


def _entity_slug(text: str) -> str:
    """Match belief_mutation._slug so entity rows align with belief subjects."""
    import re

    raw = (text or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", raw)
    s = re.sub(r"_+", "_", s).strip("_")
    return (s[:80] or "entity")


def _ensure_entity_row(conn: sqlite3.Connection, eid: str, *, name: str | None = None) -> None:
    eid = (eid or "").strip()
    if not eid or len(eid) > 200:
        return
    row = conn.execute("SELECT id FROM entities WHERE id=?", (eid,)).fetchone()
    if row:
        return
    display = (name or eid).replace("_", " ").strip() or eid
    conn.execute(
        """INSERT OR IGNORE INTO entities
           (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
           VALUES (?, ?, 'concept', ?, '[]', 0, '{}')""",
        (eid, _memory_tenant_id(), display[:120]),
    )


@router.post("/api/memory/v2/entities/link")
async def link_entities(request: Request) -> dict[str, Any]:
    """Create a belief edge subject --predicate--> object (links two nodes).

    The body is parsed on the event loop; the SQLite work runs in a worker
    thread (audit F-06) so a lock wait cannot stall every other SSE and
    WebSocket stream sharing this loop.
    """
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    return await asyncio.to_thread(_link_entities_sync, body)


def _link_entities_sync(body: Any) -> dict[str, Any]:
    """Blocking half of :func:`link_entities` — runs off the event loop."""
    subject = str((body or {}).get("subject") or "").strip()
    predicate = str((body or {}).get("predicate") or "related_to").strip()
    obj = str((body or {}).get("object") or "").strip()
    if not subject or not obj:
        return {"ok": False, "error": "subject and object required"}
    if subject == obj:
        return {"ok": False, "error": "source and target must differ"}
    if not predicate:
        predicate = "related_to"
    try:
        from kazma_core.memory.belief_mutation import mutate_belief
        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        # mutate_belief slugs the *subject* by default; keep object as the
        # graph node id (entity id or virtual fact text) so the canvas
        # endpoints stay stable. BUT when the operator clicked an existing
        # graph node (real entity OR virtual-fact id) as the subject, slugifying
        # it would mint a *different* entity and detach the edge from the node
        # they clicked (the "link looks like it failed" bug). So: if the
        # subject is already a graph node id, use it verbatim.
        primary = _conn()
        ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
        ensure_ops_schema(ops)
        subject_is_node = bool(
            primary.execute(
                "SELECT 1 FROM entities WHERE id=? "
                "UNION SELECT 1 FROM beliefs WHERE subject=? AND invalidated_at IS NULL AND valid_until IS NULL "
                "UNION SELECT 1 FROM beliefs WHERE object=? AND invalidated_at IS NULL AND valid_until IS NULL "
                "LIMIT 1",
                (subject, subject, subject),
            ).fetchone()
        )
        if subject_is_node and len(subject) <= 200:
            # Promote: use the exact node id verbatim, and ensure a real entity
            # row so the canvas renders it as a connected real node (not a
            # virtual fact). The display name is the id itself when it's text.
            sub_id = subject
            _ensure_entity_row(primary, sub_id, name=subject)
        else:
            sub_id = _entity_slug(subject)
            _ensure_entity_row(primary, sub_id, name=subject)
        obj_id = obj.strip()
        # Ensure target shell when it looks like an entity id (graph node).
        # Long free-text objects still work as belief object without a row.
        if len(obj_id) <= 120:
            _ensure_entity_row(primary, obj_id, name=obj_id)
            slug_obj = _entity_slug(obj_id)
            if slug_obj != obj_id and len(slug_obj) <= 80:
                _ensure_entity_row(primary, slug_obj, name=obj_id)
        primary.commit()

        result = mutate_belief(
            primary,
            subject,
            predicate,
            obj_id,
            ops_conn=ops,
            predicate_type="set",
            confidence=0.9,
            importance=3,
            extraction_method="user_explicit",
            tenant_id=_memory_tenant_id(),
            # When the operator clicked an existing node, pin the subject id
            # verbatim so the edge attaches to that node (no slug divergence).
            subject_id=sub_id if subject_is_node else None,
        )
        ops.close()
        primary.close()

        if not isinstance(result, dict):
            return {"ok": False, "error": "link mutation returned nothing"}
        if result.get("rejected"):
            return {
                "ok": False,
                "error": f"link rejected: {result.get('rejected')}",
                "link": result,
            }
        # noop with belief_id = already linked (treat as success so UI refreshes)
        bid = str(result.get("belief_id") or "")
        if result.get("action") == "noop" and not bid:
            return {"ok": False, "error": "link was a no-op (blocked or empty)", "link": result}

        # Register undo only when we actually created a new edge (not when the
        # link already existed). Undo = invalidate that belief.
        undo_token = None
        is_new = result.get("action") != "noop" and bool(bid)
        if is_new:
            captured_bid = bid

            async def _restore_link() -> dict[str, Any]:
                from kazma_core.memory.hygiene import invalidate_belief

                return await asyncio.to_thread(invalidate_belief, captured_bid, remove_graph=True)

            undo_token = register_undo(
                _restore_link,
                label=f"linked {sub_id} —{predicate}→ {obj_id}",
                kind="link",
            )

        return {
            "ok": True,
            "link": result,
            "subject": sub_id,
            "predicate": predicate,
            "object": obj_id,
            "belief_id": bid or None,
            "already": result.get("action") == "noop",
            "undo_token": undo_token,
            "receipt": {"belief_created": 1 if is_new else 0},
        }
    except Exception as exc:
        logger.exception("[memory_api] link failed")
        return {"ok": False, "error": safe_error(exc)}
    finally:
        try:
            ops.close()
        except Exception:
            pass  # already closed / never opened


@router.post("/api/memory/v2/entities/unlink")
async def unlink_entities(request: Request) -> dict[str, Any]:
    """Invalidate a belief edge by id and/or subject–predicate–object triple.

    Graph canvas edges always have endpoints; belief_id can be missing on
    older Neo4j exports — triple match is the fallback.
    """
    return await asyncio.to_thread(_unlink_entities_sync, await _read_json(request))


def _unlink_entities_sync(payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`unlink_entities` -- runs off the event loop."""
    body = payload
    if body is _INVALID_JSON:
        return {"ok": False, "error": "invalid JSON"}
    body = body or {}
    belief_id = str(body.get("belief_id") or body.get("id") or "").strip()
    subject = str(body.get("subject") or "").strip()
    predicate = str(body.get("predicate") or body.get("label") or "").strip()
    obj = str(body.get("object") or body.get("object_text") or "").strip()

    from kazma_core.memory.hygiene import invalidate_belief

    tid = _memory_tenant_id()
    id_err: str | None = None
    # 1) Direct id path. Another tenant's id reads as not found, and the
    # triple path below is scoped the same way.
    if belief_id:
        r = invalidate_belief(belief_id, remove_graph=True, tenant_id=tid)
        if r.get("ok") or r.get("already"):
            return {
                "ok": True,
                "belief_id": belief_id,
                "via": "id",
                "updated": r.get("updated", 0),
                "graph_removed": r.get("graph_removed"),
                "already": bool(r.get("already")),
            }
        id_err = str(r.get("error") or "invalidate failed")
        # Fall through to triple if id miss (stale canvas) and triple provided

    # 2) Triple path
    if not (subject and predicate and obj):
        return {
            "ok": False,
            "error": id_err or "belief_id or subject+predicate+object required",
            "belief_id": belief_id or None,
        }

    sub_id = _entity_slug(subject)
    try:
        conn = _conn()
        tsql = " AND tenant_id=?" if tid != "default" else ""
        tparams: list = [tid] if tid != "default" else []
        row = conn.execute(
            """
            SELECT id FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
              AND predicate = ?
              AND object = ?
              AND (subject = ? OR subject = ?)
            """ + tsql + """
            ORDER BY valid_from DESC
            LIMIT 1
            """,
            (predicate, obj, subject, sub_id, *tparams),
        ).fetchone()
        # Also try slugified object for entity-like targets
        if not row:
            obj_slug = _entity_slug(obj)
            row = conn.execute(
                """
                SELECT id FROM beliefs
                WHERE valid_until IS NULL AND invalidated_at IS NULL
                  AND predicate = ?
                  AND (object = ? OR object = ?)
                  AND (subject = ? OR subject = ?)
                """ + tsql + """
                ORDER BY valid_from DESC
                LIMIT 1
                """,
                (predicate, obj, obj_slug, subject, sub_id, *tparams),
            ).fetchone()
        conn.close()
        if not row:
            return {
                "ok": False,
                "error": "no active belief matches this edge",
                "subject": subject,
                "predicate": predicate,
                "object": obj,
            }
        bid = str(row["id"] if isinstance(row, sqlite3.Row) else row[0])
        r = invalidate_belief(bid, remove_graph=True, tenant_id=tid)
        if not r.get("ok"):
            # Idempotent: already soft-deleted counts as success
            if r.get("error") == "not found":
                return {"ok": True, "belief_id": bid, "via": "triple", "already": True}
            return {"ok": False, "error": r.get("error") or "invalidate failed", "belief_id": bid}
        return {
            "ok": True,
            "belief_id": bid,
            "via": "triple",
            "updated": r.get("updated"),
            "graph_removed": r.get("graph_removed"),
        }
    except Exception as exc:
        logger.exception("[memory_api] unlink failed")
        return {"ok": False, "error": safe_error(exc)}


# ── Beliefs (edit + batch invalidate) ────────────────────────────────────


@router.patch("/api/memory/v2/beliefs/{belief_id}")
async def edit_belief(belief_id: str, request: Request) -> dict[str, Any]:
    """Operator correction of an active belief's triple (subject/predicate/object).

    In-place update of a currently-valid row so bad extractions can be fixed
    without losing the belief id. Clears the embedding when the object text
    changes (recall will re-embed on next path that needs it). FTS sync is
    handled by the beliefs_fts UPDATE trigger.
    """
    return await asyncio.to_thread(_edit_belief_sync, belief_id, await _read_json(request))


def _edit_belief_sync(belief_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`edit_belief` -- runs off the event loop."""
    bid = (belief_id or "").strip()
    if not bid:
        return {"ok": False, "error": "belief_id required"}
    body = payload
    if body is _INVALID_JSON:
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
        if not _belief_tenant_ok(conn, bid):
            conn.close()
            return {"ok": False, "error": "not_found"}
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
        # Phase 3: an edit can change subject/object, so recompute counts for
        # the union of old AND new endpoints (a flip changes two entities).
        try:
            from kazma_core.memory.entity_counts import recompute_entity_counts

            affected = {
                str(row["subject"]), str(row["object"]),
                str(subject), str(obj),
            }
            recompute_entity_counts(conn, [a for a in affected if a])
        except Exception:
            logger.debug("[memory_api] edit count recompute failed", exc_info=True)
        conn.commit()
        # Snapshot the prior triple for undo (restore in-place).
        prior = {
            "subject": str(row["subject"]),
            "predicate": str(row["predicate"]),
            "object": str(row["object"]),
            "predicate_type": str(row["predicate_type"] or "set"),
            "had_embedding_null": object_changed,
        }
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

        def _restore_edit_sync() -> dict[str, Any]:
            c = _conn()
            c.execute(
                "UPDATE beliefs SET subject=?, predicate=?, object=?, predicate_type=? "
                "WHERE id=?",
                (
                    prior["subject"],
                    prior["predicate"],
                    prior["object"],
                    prior["predicate_type"],
                    bid,
                ),
            )
            c.commit()
            c.close()
            return {"restored": bid}

        async def _restore_edit() -> dict[str, Any]:
            return await asyncio.to_thread(_restore_edit_sync)

        undo_token = register_undo(
            _restore_edit,
            label=f"edited belief {bid}",
            kind="edit",
        )
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
            "undo_token": undo_token,
            "receipt": {"beliefs_edited": 1},
        }
    except Exception as exc:
        logger.exception("[memory_api] edit belief failed")
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/beliefs/{belief_id}/repoint")
async def repoint_belief(belief_id: str, request: Request) -> dict[str, Any]:
    """F1: move a single belief's subject and/or object endpoint in place.

    This is the "move one connection" action the graph was missing — the
    alternative was destructive cut+relink (two calls, node adrift between
    them). Repoint delegates to edit_belief with only subject/object, so it
    inherits the in-place UPDATE, count recompute for old+new endpoints,
    Neo4j cleanup, the active-only guard, and the 60s undo token. The
    predicate is preserved (repoint is an endpoint move, not a relation
    change — use PATCH for predicate/type edits).
    """
    bid = (belief_id or "").strip()
    if not bid:
        return {"ok": False, "error": "belief_id required"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    body = body or {}
    new_subject = str(body.get("subject") or "").strip() or None
    new_object = str(body.get("object") or "").strip() or None
    if not new_subject and not new_object:
        return {"ok": False, "error": "provide subject and/or object to repoint"}
    # Reject predicate/type here — repoint is endpoint-only. If the operator
    # wants to change the relation, PATCH /beliefs/{id} is the right surface.
    if body.get("predicate") is not None or body.get("predicate_type") is not None:
        return {"ok": False, "error": "repoint is endpoint-only; use PATCH for predicate/type"}

    # Build the edit_belief body (subject/object only; predicate/type absent
    # so edit_belief keeps the existing values) and delegate. The request body
    # is re-serialized so edit_belief's `await request.json()` sees our shape.

    class _Req:
        def __init__(self, payload):
            self._payload = payload

        async def json(self):
            return self._payload

    payload: dict[str, Any] = {}
    if new_subject:
        payload["subject"] = new_subject
    if new_object:
        payload["object"] = new_object
    result = await edit_belief(bid, _Req(payload))
    # Tag the result so the UI can distinguish a repoint from a generic edit
    # (e.g. for the toast wording). edit_belief returns its own shape; we only
    # add the op label if the call succeeded.
    if isinstance(result, dict) and result.get("ok"):
        result["op"] = "repoint"
        # F3: surface orphan warning for the OLD subject if it lost its last edge.
        try:
            warn = await asyncio.to_thread(_repoint_orphan_warning, bid)
            if warn:
                result["warn_orphaned"] = warn
        except Exception:
            logger.debug("[memory_api] repoint orphan warning failed", exc_info=True)
    return result


def _repoint_orphan_warning(bid: str) -> list[str]:
    """Blocking half of :func:`repoint_belief`'s warning -- runs off the event loop."""
    conn = _conn()
    try:
        return _would_orphan(conn, [bid], tenant_id=_memory_tenant_id())
    finally:
        conn.close()


@router.post("/api/memory/v2/beliefs/invalidate-batch")
async def invalidate_batch(request: Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}
    ids = (body or {}).get("ids") or (body or {}).get("belief_ids") or []
    if not isinstance(ids, list) or not ids:
        return {"ok": False, "error": "ids[] required"}
    return await asyncio.to_thread(_invalidate_batch_sync, [str(b) for b in ids[:200]])


def _invalidate_batch_sync(wanted: list[str]) -> dict[str, Any]:
    """Blocking half of :func:`invalidate_batch` -- runs off the event loop."""
    from kazma_core.memory.hygiene import invalidate_belief

    tid = _memory_tenant_id()
    conn = _conn()
    try:
        # M-05: an id from another tenant is reported as not found, and it
        # never reaches the orphan warning, which names its beliefs' endpoints.
        owned = {bid for bid in wanted if _belief_tenant_ok(conn, bid)}
        # F3: the warning is computed BEFORE the invalidations -- once the rows
        # are soft-deleted the "which endpoints would be stranded" query can
        # no longer see them as active.
        warn_orphaned = _would_orphan(conn, [b for b in wanted if b in owned], tenant_id=tid)
    finally:
        conn.close()

    results = []
    invalidated_ids: list[str] = []
    for bid in wanted:
        if bid not in owned:
            results.append({"ok": False, "updated": 0, "error": "not_found", "belief_id": bid})
            continue
        r = invalidate_belief(bid, remove_graph=True, tenant_id=tid)
        results.append(r)
        # Track only rows we actually flipped (not idempotent "already" ones)
        # so the undo restores exactly what this call changed.
        if r.get("ok") and not r.get("already"):
            invalidated_ids.append(bid)

    undo_token = None
    if invalidated_ids:
        # Undo = clear the soft-invalidate timestamps so the rows are active
        # again. Re-adds the Neo4j edge best-effort via re-link is omitted
        # (edge re-sync happens on the next dual-write / reconsolidation).
        captured = list(invalidated_ids)

        def _reactivate() -> dict[str, Any]:
            conn = _conn()
            try:
                placeholders = ",".join("?" for _ in captured)
                cur = conn.execute(
                    f"UPDATE beliefs SET valid_until=NULL, invalidated_at=NULL "
                    f"WHERE id IN ({placeholders})",
                    captured,
                )
                conn.commit()
                return {"restored": int(cur.rowcount or 0)}
            finally:
                conn.close()

        async def _restore() -> dict[str, Any]:
            return await asyncio.to_thread(_reactivate)

        undo_token = register_undo(
            _restore,
            label=f"invalidated {len(invalidated_ids)} belief(s)",
            kind="invalidate",
        )

    # F3: warn_orphaned is computed above (before the invalidations).
    return {
        "ok": True,
        "invalidated": len(invalidated_ids),
        "results": results,
        "undo_token": undo_token,
        "receipt": {"beliefs_invalidated": len(invalidated_ids)},
        "warn_orphaned": warn_orphaned,
    }


# ── Graph groupings (view-only associations; never touch beliefs) ────────
# See docs/plans/MEMORY_GRAPH_GROUPING_PLAN.md. The operator clusters nodes
# into a tiered tree (main/major/sub/leaf) for canvas layout + per-tier
# colors. recall/extraction never read this table; it is purely advisory.
# Each tenant has its own tree: every read and write below names the tenant,
# because a node id (an entity id or a fact's text) can be in several trees.


def _group_tier_of(conn: sqlite3.Connection, node_id: str, tenant_id: str) -> int:
    """Return the tier of a node that is already a member somewhere, or -1."""
    row = conn.execute(
        "SELECT member_tier FROM graph_associations WHERE member=? AND tenant_id=?",
        (node_id, tenant_id),
    ).fetchone()
    return int(row["member_tier"]) if row else -1


def _group_creates_cycle(
    conn: sqlite3.Connection, member: str, new_root: str, tenant_id: str
) -> bool:
    """True if making `member` a child of `new_root` would create a cycle
    (i.e. new_root is already a descendant of member, or member == new_root).
    Walks the ancestor chain of new_root."""
    if member == new_root:
        return True
    seen: set[str] = set()
    cur = new_root
    while cur:
        if cur in seen:
            return True  # pre-existing cycle (defensive)
        seen.add(cur)
        row = conn.execute(
            "SELECT group_root FROM graph_associations WHERE member=? AND tenant_id=?",
            (cur, tenant_id),
        ).fetchone()
        cur = row["group_root"] if row else None
        if cur == member:
            return True
    return False


def _group_descendants(conn: sqlite3.Connection, root: str, tenant_id: str) -> list[str]:
    """All transitive member ids under `root` (for subtree re-tiering)."""
    out: list[str] = []
    queue = [root]
    seen: set[str] = set()
    while queue:
        cur = queue.pop()
        rows = conn.execute(
            "SELECT member FROM graph_associations WHERE group_root=? AND tenant_id=?",
            (cur, tenant_id),
        ).fetchall()
        for r in rows:
            m = r["member"]
            if m not in seen:
                seen.add(m)
                out.append(m)
                queue.append(m)
    return out


@router.get("/api/memory/v2/graph/groups")
async def graph_groups_list() -> dict[str, Any]:
    """List all graph groupings (view-only associations) for the active tenant."""
    return await asyncio.to_thread(_graph_groups_list_sync)


def _graph_groups_list_sync() -> dict[str, Any]:
    """Blocking half of :func:`graph_groups_list` -- runs off the event loop."""
    try:
        conn = _conn()
        tid = _memory_tenant_id()
        rows = [
            dict(r) for r in conn.execute(
                "SELECT id, group_root, member, member_tier, label "
                "FROM graph_associations WHERE tenant_id=? "
                "ORDER BY group_root, member_tier, member",
                (tid,),
            ).fetchall()
        ]
        conn.close()
        return {"ok": True, "groups": rows, "count": len(rows)}
    except Exception as exc:
        logger.exception("[memory_api] graph groups list failed")
        return {"ok": False, "error": safe_error(exc), "groups": []}


@router.post("/api/memory/v2/graph/groups")
async def graph_groups_create(request: Request) -> dict[str, Any]:
    """Create a view-only grouping: member under group_root, tier defaults to
    parent_tier + 1. Never touches beliefs. Rejects cycles.
    """
    return await asyncio.to_thread(_graph_groups_create_sync, await _read_json(request))


def _graph_groups_create_sync(payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`graph_groups_create` -- runs off the event loop."""
    body = payload
    if body is _INVALID_JSON:
        return {"ok": False, "error": "invalid JSON"}
    body = body or {}
    root = str(body.get("group_root") or "").strip()
    member = str(body.get("member") or "").strip()
    if not root or not member:
        return {"ok": False, "error": "group_root and member required"}
    if root == member:
        return {"ok": False, "error": "group_root and member must differ"}
    label = str(body.get("label") or "").strip() or None
    tid = _memory_tenant_id()
    try:
        conn = _conn()
        if _group_creates_cycle(conn, member, root, tid):
            conn.close()
            return {"ok": False, "error": "cycle: member is an ancestor of group_root"}
        # Existing membership? Update in place (same root: re-tier, relabel).
        existing = conn.execute(
            "SELECT id, group_root FROM graph_associations WHERE member=? AND tenant_id=?",
            (member, tid),
        ).fetchone()
        if existing and existing["group_root"] != root:
            # A second parent is a move: the move route replaces the old edge
            # and re-tiers the subtree. Reusing the old row's id here failed
            # on the primary key, so "Group under" on a grouped node always
            # failed (until 2026-10-01).
            conn.close()
            return {
                "ok": False,
                "error": (
                    f"already grouped under {existing['group_root']}: move it "
                    f"(POST /api/memory/v2/graph/groups/member/{member}/move)"
                ),
                "group_root": existing["group_root"],
            }
        # Derive tier: explicit override > parent's tier + 1 > implicit.
        # The hub ('user') is always tier 0. An ungrouped root that is NOT the
        # hub is treated as an implicit tier-1 major (so grouping kazma_app
        # under kazma — where kazma isn't yet grouped — yields tier 2, matching
        # the A/B/C/D model). This lets operators build the tree middle-out.
        parent_tier = 0 if str(root).lower() in ("user", "you", "me") else _group_tier_of(conn, root, tid)
        if parent_tier < 0:
            parent_tier = 1  # ungrouped non-hub root → implicit major
        explicit_tier = body.get("tier")
        if explicit_tier is not None:
            tier = int(explicit_tier)
        else:
            tier = parent_tier + 1
        tier = max(0, min(tier, 4))  # soft cap
        gid = existing["id"] if existing else f"assoc_{uuid.uuid4().hex[:16]}"
        now = time.time()
        conn.execute(
            """INSERT INTO graph_associations
               (id, tenant_id, group_root, member, member_tier, label, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'operator')
               ON CONFLICT(tenant_id, group_root, member) DO UPDATE SET
                 member_tier=excluded.member_tier, label=excluded.label""",
            (gid, tid, root, member, tier, label, now),
        )
        conn.commit()
        conn.close()
        return {"ok": True, "id": gid, "group_root": root, "member": member,
                "member_tier": tier, "memory_affected": False}
    except Exception as exc:
        logger.exception("[memory_api] graph group create failed")
        return {"ok": False, "error": safe_error(exc)}


@router.delete("/api/memory/v2/graph/groups/{group_id}")
async def graph_groups_delete(group_id: str) -> dict[str, Any]:
    """Remove a view-only grouping. The member's children (if any) become
    ungrouped. Never touches beliefs."""
    return await asyncio.to_thread(_graph_groups_delete_sync, group_id)


def _graph_groups_delete_sync(group_id: str) -> dict[str, Any]:
    """Blocking half of :func:`graph_groups_delete` -- runs off the event loop."""
    gid = (group_id or "").strip()
    if not gid:
        return {"ok": False, "error": "group_id required"}
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT member, tenant_id FROM graph_associations WHERE id=?", (gid,)
        ).fetchone()
        # The grouping's own tenant decides -- not its member's: another
        # tenant may group this tenant's node (ids are global, rows are not).
        if not row or not _row_tenant_ok(conn, "graph_associations", gid):
            conn.close()
            return {"ok": False, "error": "not_found"}
        cur = conn.execute("DELETE FROM graph_associations WHERE id=?", (gid,))
        conn.commit()
        conn.close()
        return {"ok": True, "id": gid, "removed": int(cur.rowcount or 0),
                "memory_affected": False}
    except Exception as exc:
        logger.exception("[memory_api] graph group delete failed")
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/graph/groups/member/{member_id}/move")
async def graph_groups_move(member_id: str, request: Request) -> dict[str, Any]:
    """Move a member (and re-tier its subtree) to a new group root. Atomic.
    Never touches beliefs."""
    return await asyncio.to_thread(_graph_groups_move_sync, member_id, await _read_json(request))


def _graph_groups_move_sync(member_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`graph_groups_move` -- runs off the event loop."""
    member = (member_id or "").strip()
    if not member:
        return {"ok": False, "error": "member_id required"}
    body = {} if payload is _INVALID_JSON else payload
    new_root = str((body or {}).get("new_root") or "").strip()
    if not new_root:
        return {"ok": False, "error": "new_root required"}
    if new_root == member:
        return {"ok": False, "error": "new_root must differ from member"}
    tid = _memory_tenant_id()
    try:
        conn = _conn()
        if not _entity_tenant_ok(conn, member) or not _entity_tenant_ok(conn, new_root):
            conn.close()
            return {"ok": False, "error": "not_found"}
        if _group_creates_cycle(conn, member, new_root, tid):
            conn.close()
            return {"ok": False, "error": "cycle: member is an ancestor of new_root"}
        # Capture the member's old tier + its subtree (member + descendants),
        # so we can shift them by the delta after the move.
        old_row = conn.execute(
            "SELECT member_tier FROM graph_associations WHERE member=? AND tenant_id=?",
            (member, tid),
        ).fetchone()
        old_tier = int(old_row["member_tier"]) if old_row else 1
        subtree = [member] + _group_descendants(conn, member, tid)
        # New tier for the moved member (same implicit-tier rule as create).
        parent_tier = 0 if str(new_root).lower() in ("user", "you", "me") else _group_tier_of(conn, new_root, tid)
        if parent_tier < 0:
            parent_tier = 1  # ungrouped non-hub root → implicit major
        explicit = body.get("tier")
        new_tier = int(explicit) if explicit is not None else parent_tier + 1
        new_tier = max(0, min(new_tier, 4))
        delta = new_tier - old_tier
        now = time.time()
        # Upsert the member's new root + tier.
        conn.execute(
            """INSERT INTO graph_associations
               (id, tenant_id, group_root, member, member_tier, created_at, created_by)
               VALUES (?, ?, ?, ?, ?, ?, 'operator')
               ON CONFLICT(tenant_id, group_root, member) DO UPDATE SET
                 member_tier=excluded.member_tier""",
            (f"assoc_{uuid.uuid4().hex[:16]}", tid, new_root, member, new_tier, now),
        )
        # But the member may have had a different root — delete the old edge.
        conn.execute(
            "DELETE FROM graph_associations WHERE member=? AND group_root!=? AND tenant_id=?",
            (member, new_root, tid),
        )
        # Re-tier descendants by the same delta (keep relative depth).
        if delta != 0:
            for desc in subtree:
                if desc == member:
                    continue
                row = conn.execute(
                    "SELECT member_tier FROM graph_associations WHERE member=? AND tenant_id=?",
                    (desc, tid),
                ).fetchone()
                if row:
                    conn.execute(
                        "UPDATE graph_associations SET member_tier=? WHERE member=? AND tenant_id=?",
                        (max(0, min(int(row["member_tier"]) + delta, 4)), desc, tid),
                    )
        conn.commit()
        conn.close()
        return {"ok": True, "member": member, "new_root": new_root,
                "new_tier": new_tier, "subtree_retiered": len(subtree) - 1,
                "memory_affected": False}
    except Exception as exc:
        logger.exception("[memory_api] graph group move failed")
        return {"ok": False, "error": safe_error(exc)}


@router.post("/api/memory/v2/graph/groups/node/{node_id}/tier")
async def graph_groups_set_tier(node_id: str, request: Request) -> dict[str, Any]:
    """Manually override a node's tier (for when parent+1 is wrong). Never
    touches beliefs."""
    return await asyncio.to_thread(_graph_groups_set_tier_sync, node_id, await _read_json(request))


def _graph_groups_set_tier_sync(node_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`graph_groups_set_tier` -- runs off the event loop."""
    node = (node_id or "").strip()
    if not node:
        return {"ok": False, "error": "node_id required"}
    body = {} if payload is _INVALID_JSON else payload
    tier = body.get("tier")
    if tier is None:
        return {"ok": False, "error": "tier required"}
    try:
        tier = max(0, min(int(tier), 4))
    except Exception:
        return {"ok": False, "error": "tier must be an integer 0-4"}
    try:
        conn = _conn()
        if not _entity_tenant_ok(conn, node):
            conn.close()
            return {"ok": False, "error": "not_found"}
        cur = conn.execute(
            "UPDATE graph_associations SET member_tier=? WHERE member=? AND tenant_id=?",
            (tier, node, _memory_tenant_id()),
        )
        conn.commit()
        conn.close()
        if cur.rowcount == 0:
            return {"ok": False, "error": "node is not a grouped member"}
        return {"ok": True, "member": node, "member_tier": tier,
                "memory_affected": False}
    except Exception as exc:
        logger.exception("[memory_api] graph group set tier failed")
        return {"ok": False, "error": safe_error(exc)}


# ── Hygiene ──────────────────────────────────────────────────────────────


@router.get("/api/memory/v2/hygiene/preview")
def hygiene_preview() -> dict[str, Any]:
    """Preview safe cleanup candidates in the caller's tenant (no writes).

    A plain ``def``: FastAPI runs it in its threadpool, and every line is
    SQLite. The install's own tenant ("default") sees the whole install, like
    every memory list; any other tenant sees its own rows only -- this listed
    every tenant's entities and notes until 2026-09-27.
    """
    tid = _memory_tenant_id()
    scoped = tid != "default"
    e_scope = " AND e.tenant_id = ?" if scoped else ""
    b_scope = " AND tenant_id = ?" if scoped else ""
    tparams: tuple = (tid,) if scoped else ()
    conn = None
    try:
        conn = _conn()
        empty = [
            dict(r)
            for r in conn.execute(
                f"""
                SELECT e.id, e.type, e.name, {_belief_count_sql()} AS belief_count
                FROM entities e
                WHERE {_belief_count_sql()} = 0
                  AND LOWER(e.id) NOT IN ('user','assistant','kazma','mubder'){e_scope}
                ORDER BY e.name
                LIMIT 200
                """,
                tparams,
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
                  AND LOWER(e.id) NOT IN ('user','assistant'){e_scope}
                ORDER BY belief_count DESC
                LIMIT 100
                """,
                tparams,
            ).fetchall()
        ]
        # Near-dup noted (same first 160 normalized chars)
        noted = conn.execute(
            f"""
            SELECT id, object, valid_from FROM beliefs
            WHERE valid_until IS NULL AND invalidated_at IS NULL
              AND predicate = 'noted'{b_scope}
            ORDER BY valid_from DESC
            LIMIT 500
            """,
            tparams,
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
            "SELECT COUNT(*) FROM beliefs "
            f"WHERE (invalidated_at IS NOT NULL OR valid_until IS NOT NULL){b_scope}",
            tparams,
        ).fetchone()[0]
        return {
            "ok": True,
            "empty_entities": empty,
            "isolated_entities": isolated,
            "near_dup_noted": near_dups[:50],
            "invalidated_belief_count": dead,
        }
    except Exception as exc:
        logger.exception("[memory_api] hygiene preview failed")
        return {"ok": False, "error": safe_error(exc)}
    finally:
        if conn is not None:
            conn.close()


def _invalidate_owned(belief_ids: list[str]) -> int:
    """Invalidate the caller's beliefs among *belief_ids*; how many flipped."""
    from kazma_core.memory.hygiene import invalidate_belief

    tid = _memory_tenant_id()
    return sum(
        1 for bid in belief_ids if invalidate_belief(bid, remove_graph=True, tenant_id=tid).get("ok")
    )


def _archive_invalidated_sync() -> dict[str, Any]:
    """Soft-hard: move the caller's invalidated beliefs to beliefs_archive, then DELETE.

    The install's own tenant archives the install's; any other tenant, its own.
    """
    tid = _memory_tenant_id()
    scope = " AND tenant_id = ?" if tid != "default" else ""
    conn = None
    try:
        conn = _conn()
        now = time.time()
        rows = conn.execute(
            "SELECT * FROM beliefs "
            f"WHERE (invalidated_at IS NOT NULL OR valid_until IS NOT NULL){scope} "
            "LIMIT 2000",
            (tid,) if scope else (),
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
        return {"archived": n}
    except Exception as exc:
        return {"error": safe_error(exc)}
    finally:
        if conn is not None:
            conn.close()


@router.post("/api/memory/v2/hygiene/run")
async def hygiene_run(request: Request) -> dict[str, Any]:
    """Run selected hygiene actions on the caller's tenant.

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
        preview = await asyncio.to_thread(hygiene_preview)
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
        preview = await asyncio.to_thread(hygiene_preview)
        drop_ids: list[str] = []
        for g in preview.get("near_dup_noted") or []:
            drop_ids.extend(g.get("drop_ids") or [])
        n = await asyncio.to_thread(_invalidate_owned, drop_ids)
        out["actions"]["invalidate_near_dup_noted"] = {
            "invalidated": n,
            "ids": drop_ids[:100],
        }

    if body.get("archive_invalidated"):
        out["actions"]["archive_invalidated"] = await asyncio.to_thread(_archive_invalidated_sync)

    return out


# ── What the user decides to keep (plan U1, memory/forget.py) ────────────
#
# A chat id here only picks among the CALLER's tenant's memories: the ledger
# rows are written under that tenant, and forgetting touches that tenant's
# episodes. Users of one tenant share its memory, as they share every other
# memory route; another tenant's chat id changes nothing of theirs.


@router.post("/api/memory/v2/episodes/{episode_id}/forget")
def forget_memory(episode_id: str) -> dict[str, Any]:
    """Forget one memory and every copy of its turn; the facts it produced
    stop being current. Another tenant's memory reads as not found.

    A plain ``def``: FastAPI runs it in its threadpool (every step is I/O).
    """
    from kazma_core.memory.forget import forget_episode

    try:
        return forget_episode(episode_id, tenant_id=_memory_tenant_id())
    except sqlite3.Error as exc:
        logger.exception("[memory_api] forget failed")
        return {"ok": False, "error": safe_error(exc)}


@router.get("/api/memory/v2/chats/{chat_id}/memory")
def chat_memory(chat_id: str) -> dict[str, Any]:
    """Whether Kazma remembers this chat, and how many memories it has left."""
    from kazma_core.memory.forget import FORGOTTEN_TIER, chat_keys, forgotten_turns

    tid = _memory_tenant_id()
    keys = chat_keys(chat_id)
    if not keys:
        return {"ok": False, "error": "chat required"}
    conn = _conn()
    try:
        marks = ",".join("?" for _ in keys)
        scope = "" if tid == "default" else " AND tenant_id = ?"
        count = conn.execute(
            f"SELECT COUNT(*) FROM episodes WHERE session_id IN ({marks}) AND tier != ?{scope}",
            (*keys, FORGOTTEN_TIER, *(() if tid == "default" else (tid,))),
        ).fetchone()[0]
        kept_out, _turns = forgotten_turns(conn, tenant_id=tid, keys=keys)
        return {"ok": True, "remembered": not kept_out, "memories": int(count)}
    finally:
        conn.close()


@router.put("/api/memory/v2/chats/{chat_id}/memory")
async def set_chat_memory(chat_id: str, request: Request) -> dict[str, Any]:
    """Keep this chat out of memory, or let it back in. Body::

        {"remember": false, "forget_past": true}

    ``forget_past`` also forgets what the chat has left in memory already.
    """
    return await asyncio.to_thread(_set_chat_memory_sync, chat_id, await _read_json(request))


def _set_chat_memory_sync(chat_id: str, payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`set_chat_memory` -- runs off the event loop."""
    from kazma_core.memory.forget import forget_chat, set_chat_remembered

    if payload is _INVALID_JSON or not isinstance(payload, dict) or "remember" not in payload:
        return {"ok": False, "error": "body must be {\"remember\": true|false}"}
    tid = _memory_tenant_id()
    out = set_chat_remembered(chat_id, bool(payload.get("remember")), tenant_id=tid)
    if out.get("ok") and payload.get("forget_past"):
        out["forgot"] = forget_chat(chat_id, tenant_id=tid)
    return out


@router.get("/api/memory/v2/summaries")
def weekly_summaries(limit: int = 30) -> dict[str, Any]:
    """The caller's tenant's weekly topic summaries, newest week first (plan
    C2), with the state of the summarizing: weeks done, queued, failed."""
    from kazma_core.memory.topic_summaries import list_summaries, summary_health

    tid = _memory_tenant_id()
    conn = _conn()
    try:
        return {
            "ok": True,
            "summaries": list_summaries(conn, tenant_id=tid, limit=limit),
            "state": summary_health(conn, tenant_id=tid),
        }
    finally:
        conn.close()


@router.post("/api/memory/v2/summaries/{summary_id}/forget")
def forget_weekly_summary(summary_id: str) -> dict[str, Any]:
    """Forget one weekly summary: its text goes and its week never writes it
    again. The turns it was written from stay. Another tenant's summary reads
    as not found."""
    from kazma_core.memory.topic_summaries import forget_summary

    try:
        return forget_summary(summary_id, tenant_id=_memory_tenant_id())
    except sqlite3.Error as exc:
        logger.exception("[memory_api] summary forget failed")
        return {"ok": False, "error": safe_error(exc)}


@router.get("/api/memory/v2/profile")
def about_me() -> dict[str, Any]:
    """The caller's "About me" (plan C1): what they want Kazma to know about
    them, shown to the model on every call."""
    from kazma_core.memory.profile import ABOUT_MAX_CHARS, get_about

    return {"ok": True, **get_about(_memory_tenant_id()), "max_chars": ABOUT_MAX_CHARS}


@router.put("/api/memory/v2/profile")
async def set_about_me(request: Request) -> dict[str, Any]:
    """Save the caller's "About me". Body: ``{"about": "..."}``; empty clears it."""
    return await asyncio.to_thread(_set_about_me_sync, await _read_json(request))


def _set_about_me_sync(payload: Any) -> dict[str, Any]:
    """Blocking half of :func:`set_about_me` -- runs off the event loop."""
    from kazma_core.memory.profile import set_about

    if payload is _INVALID_JSON or not isinstance(payload, dict) or not isinstance(payload.get("about"), str):
        return {"ok": False, "error": "body must be {\"about\": \"...\"}"}
    return set_about(_memory_tenant_id(), payload["about"])


@router.get("/api/memory/v2/export")
def export_memory() -> Any:
    """Everything the caller's tenant's memory holds, as a JSON download:
    the user's "About me", facts (current and past, with the chat and turn they came from),
    memories of conversations, weekly summaries (with the memories each was
    written from), entities, and what was forgotten (when -- never what)."""
    from datetime import UTC, datetime

    from fastapi.responses import Response
    from kazma_core.memory.forget import FORGOTTEN_TIER

    tid = _memory_tenant_id()
    scope, params = ("", ()) if tid == "default" else (" WHERE tenant_id = ?", (tid,))
    conn = _conn()
    try:
        def rows(sql: str, extra: tuple = ()) -> list[dict[str, Any]]:
            return [dict(r) for r in conn.execute(sql, (*params, *extra)).fetchall()]

        facts = rows(
            "SELECT tenant_id, subject, predicate, predicate_type, object, confidence, "
            "valid_from, valid_until, invalidated_at, ingested_at, extraction_method, "
            "source_session, source_turn, metadata_json FROM beliefs" + scope
            + " ORDER BY ingested_at"
        )
        for f in facts:
            try:
                meta = json.loads(f.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                meta = {}
            f["forgotten"] = isinstance(meta, dict) and "forgotten" in meta
        episodes = rows(
            "SELECT tenant_id, id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, created_at FROM episodes"
            + (scope + " AND" if scope else " WHERE") + " tier != ? ORDER BY created_at",
            (FORGOTTEN_TIER,),
        )
        entities = rows("SELECT tenant_id, id, type, name, aliases_json FROM entities" + scope)
        forgotten = rows(
            "SELECT tenant_id, session_key, turn_number, forgotten_at FROM memory_forgotten"
            + scope + " ORDER BY forgotten_at"
        )
        summaries = rows(
            "SELECT tenant_id, id, period_key, title, summary_text, status, turn_count, "
            "chat_count, first_turn_at, last_turn_at, created_at, updated_at FROM memory_summaries"
            + scope + " ORDER BY period_start"
        )
        for s in summaries:
            s["memories"] = [r[0] for r in conn.execute(
                "SELECT episode_id FROM memory_summary_sources WHERE summary_id = ? ORDER BY episode_id",
                (s["id"],),
            )]
        about = rows("SELECT tenant_id, about, updated_at FROM memory_profile" + scope)
    finally:
        conn.close()
    now = datetime.now(UTC)
    body = json.dumps(
        {
            "exported_at": now.isoformat(),
            "tenant": tid,
            "about_me": about,
            "facts": facts,
            "memories": episodes,
            "weekly_summaries": summaries,
            "entities": entities,
            "forgotten": forgotten,
        },
        ensure_ascii=False,
        indent=1,
        default=str,
    )
    return Response(
        content=body,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="kazma-memory-{now:%Y%m%d-%H%M}.json"'
        },
    )


def mount_memory_api(app: Any) -> None:
    """Include API router on the FastAPI app."""
    app.include_router(router)
