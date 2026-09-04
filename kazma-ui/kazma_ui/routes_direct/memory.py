"""Memory V2 admin endpoints (beliefs, entities, episodes, graph, queue).

Extracted from the former ``kazma_ui/routes_direct.py`` god module
(3,862 lines) — audit O5. Handler bodies are unchanged; only their
module changed. Registration order within this group is preserved.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import Depends, Request
from kazma_core.errors import safe_error

from kazma_ui.rate_limit import rate_limit
from kazma_ui.routes_direct._shared import _mem_tid, _tenant_clause, open_memory_db

logger = logging.getLogger(__name__)

__all__ = ["register_memory_routes"]


def register_memory_routes(self: Any) -> None:
    """Register the memory routes onto ``self.app``."""
    @self.app.get("/api/memory/graph")
    async def _memory_graph(q: str = "", limit: int = 80):
        """Retired — V2 belief graph superseded this. Use /api/memory/v2/graph."""
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=410,
            content={
                "detail": "The legacy property-graph endpoint was retired with the V1 "
                          "memory stack. Use GET /api/memory/v2/graph for the V2 belief "
                          "graph (bi-temporal, with ?at=/type/entity_type filters).",
                "replacement": "/api/memory/v2/graph",
            },
        )
    @self.app.get("/api/memory/graph/stats")
    async def _memory_graph_stats():
        """Retired — V2 belief counts live in /api/memory/v2/health."""
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=410,
            content={
                "detail": "Legacy graph stats retired. Use GET /api/memory/v2/health "
                          "(beliefs.active/superseded/archived).",
                "replacement": "/api/memory/v2/health",
            },
        )
    @self.app.get("/api/memory/graph/search")
    async def _memory_graph_search(q: str = "", limit: int = 20):
        """Search the V2 cognitive memory (repoints the legacy graph FTS search).

        Backed by ``recall.search`` — hybrid FTS5 + dense vector + PPR over V2
        beliefs and episodes. Returns the same ``{query, results[]}`` envelope
        the legacy handler emitted so existing callers keep working.
        """
        if not (q or "").strip():
            return {"results": [], "query": ""}
        from kazma_core.memory.recall import search

        hits = search(
            q.strip(),
            limit=max(1, min(int(limit), 50)),
            tenant_id=_mem_tid(),
        )
        return {
            "query": q.strip(),
            "results": [
                {
                    "id": h.get("id"),
                    "type": h.get("source_layer"),
                    "label": (h.get("content") or "")[:80] or h.get("id"),
                    "content": (h.get("content") or "")[:300],
                    "score": h.get("score"),
                }
                for h in hits
            ],
        }
    @self.app.post("/api/memory/graph/clear")
    def _memory_graph_clear(
        request: Request,
        tenant: str = "default",
        confirm: bool = False,
    ):
        """Invalidate all currently-active V2 beliefs (bi-temporal clear).

        Replaces the legacy destructive ``kg.clear()``. V2 is append-only /
        bi-temporal by design — rather than deleting rows, this marks every
        currently-active belief invalidated (sets ``invalidated_at`` /
        ``valid_until``) so they stop surfacing in recall while history is
        preserved for point-in-time queries. Episodes are not touched.

        Tenant-scoped (M-05): defaults to the shared ``default`` tenant;
        pass ``tenant=<id>`` to clear a specific one. There is deliberately
        NO all-tenants mode. Requires explicit confirm=true and tenant authorization.
        """
        import json
        import sqlite3
        from starlette.responses import JSONResponse

        if not confirm:
            return JSONResponse(
                {"error": "Confirmation required. Pass confirm=true to clear memory graph."},
                status_code=400,
            )

        tid = (tenant or "default").strip() or "default"

        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated

        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return JSONResponse({"error": "Unauthorized"}, status_code=401)

        principal = get_request_principal(request) or {}
        is_admin = principal.get("source") == "secret" or principal.get("role") == "admin"
        caller_tenant = principal.get("tenant") or principal.get("tenant_id")

        if not is_admin and secret:
            if not caller_tenant:
                return JSONResponse({"error": "Admin role or tenant binding required"}, status_code=403)
            if caller_tenant != tid:
                return JSONResponse(
                    {"error": f"Forbidden: cannot clear memory for tenant '{tid}'"},
                    status_code=403,
                )


        try:
            conn = open_memory_db()
            now = time.time()
            cleared_ids = [
                r[0]
                for r in conn.execute(
                    "SELECT id FROM beliefs WHERE tenant_id=? "
                    "AND valid_until IS NULL AND invalidated_at IS NULL",
                    (tid,),
                ).fetchall()
            ]
            before = len(cleared_ids)
            conn.execute(
                "UPDATE beliefs SET valid_until=?, invalidated_at=? "
                "WHERE tenant_id=? AND valid_until IS NULL AND invalidated_at IS NULL",
                (now, now, tid),
            )
            # Phase 3: a clear invalidates every active belief in the cleared
            # tenant, so those entities' belief_count/graph_degree drop to 0.
            # Mark them all stale (-1) so the read path recomputes on next
            # access rather than recomputing the whole table inline here.
            try:
                conn.execute(
                    "UPDATE entities SET belief_count=-1, graph_degree=-1 WHERE tenant_id=?",
                    (tid,),
                )
            except Exception:
                pass
            conn.commit()
            after = conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE tenant_id=? "
                "AND valid_until IS NULL AND invalidated_at IS NULL",
                (tid,),
            ).fetchone()[0]
            # Mirror tombstones (M-04): every cleared row must die in shared
            # state too. Best-effort; the nightly drift check reports gaps.
            try:
                from kazma_core.memory.state_backend import unmirror_belief_to_state

                for bid in cleared_ids:
                    unmirror_belief_to_state(str(bid))
            except Exception:
                logger.debug("[memory] graph-clear mirror tombstone skipped", exc_info=True)
            # M-15: Neo4j dual-write edges must die with the SQLite invalidate.
            neo_cleared = 0
            try:
                from kazma_core.memory.graph_backend import clear_tenant_edges

                neo = clear_tenant_edges(tenant_id=tid)
                neo_cleared = int(neo.get("cleared") or 0)
            except Exception:
                logger.debug("[memory] graph-clear neo4j cleanup skipped", exc_info=True)
            # Audit row (ops DB) so a mass invalidate is as traceable as
            # single-belief hygiene.
            try:
                import uuid as _uuid

                from kazma_core.memory.schema_v2 import ensure_ops_schema
                from kazma_core.paths import memory_ops_db

                ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
                try:
                    ensure_ops_schema(ops)
                    ops.execute(
                        """INSERT INTO memory_audit_log
                           (id, tenant_id, timestamp, event_type, target_table, target_id,
                            actor, reason, state_before_json, state_after_json)
                           VALUES (?, ?, ?, 'graph_clear', 'beliefs', ?, 'operator', ?, ?, ?)""",
                        (
                            "a_" + _uuid.uuid4().hex[:20],
                            tid,
                            now,
                            tid,
                            f"invalidated {before} active beliefs",
                            json.dumps({"count": before, "ids_sample": cleared_ids[:50]}),
                            json.dumps({"active_remaining": after, "neo4j_cleared": neo_cleared}),
                        ),
                    )
                    ops.commit()
                finally:
                    ops.close()
            except Exception:
                logger.debug("[memory] graph-clear audit skipped", exc_info=True)
            finally:
                try:
                    ops.close()
                except Exception:
                    pass  # already closed / never opened
            conn.close()
            return {
                "ok": True,
                "invalidated_beliefs": before,
                "active_remaining": after,
                "neo4j_cleared": neo_cleared,
            }
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
            try:
                ops.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/graph/export")
    async def _memory_graph_export():
        """Retired — V2 nightly JSONL/GraphML export superseded this."""
        from starlette.responses import JSONResponse

        return JSONResponse(
            status_code=410,
            content={
                "detail": "Legacy graph JSON export retired. V2 nightly JSONL + GraphML "
                          "exports run on a 24h scheduler (exports_dir); for an on-demand "
                          "graph snapshot use GET /api/memory/v2/graph.",
                "replacement": "/api/memory/v2/graph",
            },
        )
    @self.app.get("/api/memory/v2/health")
    async def _memory_v2_health():
        """V2 cognitive-engine health snapshot (beliefs, episodes, queue)."""
        from kazma_core.memory.v2_health import build_v2_health

        return build_v2_health()
    @self.app.post("/api/memory/v2/federated-search")
    async def _memory_v2_federated_search(request: Request):
        """Federated search: cognitive memory + Knowledge Library (labeled, not merged)."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        query = str((body or {}).get("query") or "").strip()
        if not query:
            return {
                "ok": False,
                "error": "query required",
                "hits": [],
                "summary": {"memory": 0, "knowledge": 0, "total": 0},
            }
        try:
            from kazma_core.memory.federated_search import federated_search
            from kazma_core.tenant_isolation import require_tenant_id

            return federated_search(
                query,
                tenant_id=require_tenant_id(),
                session_id=(body or {}).get("session_id") or None,
                limit_memory=int((body or {}).get("limit_memory") or 5),
                limit_kb=int((body or {}).get("limit_kb") or 5),
                include_memory=bool((body or {}).get("include_memory", True)),
                include_knowledge=bool((body or {}).get("include_knowledge", True)),
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": safe_error(exc),
                "hits": [],
                "summary": {"memory": 0, "knowledge": 0, "total": 0},
            }
    @self.app.post("/api/memory/v2/eval/golden")
    async def _memory_v2_eval_golden(request: Request):
        """Run the in-repo golden memory cases (no live LLM; seeds DB fixtures).

        Body optional: ``{"include_optional": false}``.
        """
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        include_optional = bool((body or {}).get("include_optional", False))
        try:
            from kazma_core.memory.eval_golden import run_golden_eval

            return run_golden_eval(include_optional=include_optional)
        except Exception as exc:
            return {
                "ok": False,
                "error": safe_error(exc),
                "passed": 0,
                "failed": 0,
                "total": 0,
                "cases": [],
            }
    @self.app.post("/api/memory/v2/probe")
    async def _memory_v2_probe(request: Request):
        """Live recall dry-run for the dashboard probe panel."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        query = str((body or {}).get("query") or "").strip()
        limit = int((body or {}).get("limit") or 5)
        session_id = (body or {}).get("session_id") or None
        if not query:
            return {"ok": False, "error": "query required", "beliefs": [], "episodes": []}
        try:
            from kazma_core.memory.recall import recall
            from kazma_core.tenant_isolation import require_tenant_id

            tenant_id = require_tenant_id()
            result = recall(
                query,
                limit=max(1, min(limit, 20)),
                session_id=session_id,
                tenant_id=tenant_id,
                explain=True,
            )
            hints: list[str] = []
            if result.empty:
                try:
                    import os
                    import sqlite3 as _sq

                    from kazma_core.memory.embedder import get_embedder
                    from kazma_core.paths import primary_memory_db

                    if get_embedder() is None:
                        hints.append("embedder unavailable — dense search offline; FTS/LIKE only")
                    dbp = primary_memory_db()
                    if not os.path.exists(dbp):
                        hints.append("memory_state.db not initialized — no rows yet")
                    else:
                        c = _sq.connect(dbp)
                        try:
                            ep_n = c.execute(
                                "SELECT COUNT(*) FROM episodes WHERE tenant_id=?",
                                (tenant_id,),
                            ).fetchone()[0]
                            bel_n = c.execute(
                                "SELECT COUNT(*) FROM beliefs WHERE tenant_id=? "
                                "AND valid_until IS NULL AND invalidated_at IS NULL",
                                (tenant_id,),
                            ).fetchone()[0]
                        finally:
                            c.close()
                        if ep_n == 0 and bel_n == 0:
                            hints.append(
                                "no episodes or beliefs for this tenant — try chat: "
                                "“Remember my favorite color is teal.”"
                            )
                        else:
                            hints.append(
                                f"store has {bel_n} beliefs / {ep_n} episodes but none matched — "
                                "try different keywords or check tenant_mode"
                            )
                except Exception:
                    hints.append("empty recall — check memory health on Dashboard")
            return {
                "ok": True,
                "query": query,
                "empty": result.empty,
                "hints": hints,
                "beliefs": [
                    {
                        "id": h.id,
                        "content": h.content,
                        "score": h.score,
                        "source": h.source,
                        "sources": (h.metadata or {}).get("sources"),
                        "metadata": h.metadata,
                    }
                    for h in result.beliefs
                ],
                "episodes": [
                    {
                        "id": h.id,
                        "content": h.content,
                        "score": h.score,
                        "source": h.source,
                        "sources": (h.metadata or {}).get("sources"),
                        "metadata": h.metadata,
                    }
                    for h in result.episodes
                ],
            }
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc), "beliefs": [], "episodes": []}
    @self.app.get("/api/memory/v2/beliefs")
    def _memory_v2_beliefs(q: str = "", limit: int = 50, offset: int = 0):
        """Active V2 beliefs (currently valid only), optional FTS filter.

        Search prefers the ``beliefs_fts`` FTS5 index (diacritic-insensitive,
        tokenized) and falls back to ``LIKE`` if FTS is unavailable or the
        query has no usable tokens — matching the recall engine's own pattern.
        """


        def _match_expr(text: str) -> str:
            """Safe FTS5 MATCH expression (alphanumeric tokens OR-joined)."""
            toks = []
            for part in text.lower().replace("-", " ").split():
                cleaned = "".join(c for c in part if c.isalnum())
                if len(cleaned) >= 2 and cleaned not in toks:
                    toks.append(cleaned)
            return " OR ".join(toks)

        try:
            conn = open_memory_db()
            tid = _mem_tid()
            tsql, tparams = _tenant_clause(tid)
            base_where = (
                " FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL" + tsql
            )

            query = (q or "").strip()
            fts_ids: list[str] | None = None  # None = unfiltered / LIKE fallback
            if query:
                match_q = _match_expr(query)
                if match_q:
                    # FTS5 MATCH — collect matching active belief ids.
                    try:
                        fts_sql = (
                            "SELECT b.id FROM beliefs_fts "
                            "JOIN beliefs b ON b.rowid = beliefs_fts.rowid "
                            "WHERE beliefs_fts MATCH ? "
                            "AND b.valid_until IS NULL AND b.invalidated_at IS NULL"
                            + (" AND b.tenant_id = ?" if tparams else "")
                            + " LIMIT 1000"
                        )
                        fts_rows = conn.execute(
                            fts_sql, [match_q, *tparams]
                        ).fetchall()
                        fts_ids = [str(r["id"]) for r in fts_rows]
                    except Exception:
                        # FTS unavailable/corrupt → fall back to LIKE.
                        fts_ids = None
                # else: no usable tokens → LIKE fallback below.

            where = base_where
            params: list = []
            if fts_ids is not None:
                if not fts_ids:
                    # FTS matched nothing — short-circuit to empty.
                    conn.close()
                    lim = max(1, min(limit, 200))
                    return {"beliefs": [], "total": 0, "offset": max(0, int(offset or 0)), "limit": lim, "matched_via": "fts"}
                ph = ",".join("?" for _ in fts_ids)
                where += f" AND id IN ({ph})"
                params = list(fts_ids)
            elif query:
                # LIKE fallback (FTS unavailable or no usable tokens).
                ql = f"%{query.lower()}%"
                where += " AND (LOWER(subject) LIKE ? OR LOWER(predicate) LIKE ? OR LOWER(object) LIKE ?)"
                params = [ql, ql, ql]

            # Total count for the pager (same WHERE).
            total = conn.execute(f"SELECT COUNT(*){where}", params).fetchone()[0]

            sql = (
                "SELECT id, subject, predicate, predicate_type, object, confidence, "
                "structural_importance, valid_from, source_trust_weight, extraction_method, "
                "access_count, last_accessed, supersedes_id"
                + where
                + " ORDER BY (structural_importance * confidence * source_trust_weight) DESC LIMIT ? OFFSET ?"
            )
            lim = max(1, min(limit, 200))
            off = max(0, int(offset or 0))
            rows = conn.execute(sql, [*params, lim, off]).fetchall()
            conn.close()
            matched_via = "fts" if (fts_ids is not None) else ("like" if query else None)
            return {
                "beliefs": [dict(r) for r in rows],
                "total": int(total),
                "offset": off,
                "limit": lim,
                "matched_via": matched_via,
            }
        except Exception as exc:
            return {"beliefs": [], "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/beliefs/{belief_id}")
    def _memory_v2_belief_detail(belief_id: str):
        """Belief detail + supersede chain.

        Strips binary ``embedding`` BLOBs — FastAPI's jsonable_encoder tries
        ``bytes.decode('utf-8')`` and crashes (UnicodeDecodeError) on float32
        vector payloads (e.g. byte 0xbc is valid float32, not UTF-8).
        """
        import sqlite3


        def _belief_json_safe(row: sqlite3.Row | dict) -> dict:
            d = dict(row)
            emb = d.pop("embedding", None)
            if emb is not None:
                try:
                    n = len(emb) if not isinstance(emb, memoryview) else len(bytes(emb))
                except Exception:
                    n = 0
                d["has_embedding"] = n > 0
                d["embedding_bytes"] = int(n)
            else:
                d["has_embedding"] = False
                d["embedding_bytes"] = 0
            # Any other non-JSON-friendly values (defensive)
            for k, v in list(d.items()):
                if isinstance(v, (bytes, bytearray, memoryview)):
                    d[k] = f"<binary {len(v)} bytes>"
            return d

        try:
            conn = open_memory_db()
            tid = _mem_tid()
            row = conn.execute(
                "SELECT * FROM beliefs WHERE id=?"
                + (" AND tenant_id=?" if tid != "default" else ""),
                (belief_id, tid) if tid != "default" else (belief_id,),
            ).fetchone()
            if not row:
                conn.close()
                return {"ok": False, "error": "not_found"}
            chain = [_belief_json_safe(row)]
            # Walk supersedes_id ancestors
            cur = row
            for _ in range(20):
                sid = cur["supersedes_id"] if "supersedes_id" in cur.keys() else None
                if not sid:
                    break
                prev = conn.execute("SELECT * FROM beliefs WHERE id=?", (sid,)).fetchone()
                if not prev:
                    break
                chain.append(_belief_json_safe(prev))
                cur = prev
            # Children that supersede this belief
            kids = conn.execute(
                "SELECT id, subject, predicate, object, valid_from, valid_until "
                "FROM beliefs WHERE supersedes_id=?",
                (belief_id,),
            ).fetchall()
            conn.close()
            return {
                "ok": True,
                "belief": _belief_json_safe(row),
                "chain": chain,
                "superseded_by": [dict(k) for k in kids],
            }
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/beliefs/{belief_id}/recall-trail")
    def _memory_v2_belief_recall_trail(belief_id: str):
        """Recall history for a belief — answers "why/how-often was this used?"

        Surfaces the access stats the engine already stamps (``access_count``,
        ``last_accessed``) plus the belief's origin (``source_session`` /
        ``source_turn`` and the originating episode preview when available).
        There is no dedicated recall-audit table today, so the trail is
        aggregate (count + last time) rather than per-recall-event; the origin
        episode gives the operator the concrete "where it came from" link.
        """


        bid = (belief_id or "").strip()
        if not bid:
            return {"ok": False, "error": "belief_id required"}
        try:
            conn = open_memory_db()
            tid = _mem_tid()
            row = conn.execute(
                "SELECT id, subject, predicate, object, source_session, source_turn, "
                "extraction_method, access_count, last_accessed, valid_from "
                "FROM beliefs WHERE id=?"
                + (" AND tenant_id=?" if tid != "default" else ""),
                (bid, tid) if tid != "default" else (bid,),
            ).fetchone()
            if not row:
                conn.close()
                return {"ok": False, "error": "not_found"}

            origin_episode = None
            src_session = row["source_session"]
            src_turn = row["source_turn"]
            if src_session:
                # Find the originating episode (same session; prefer the turn).
                ep_row = conn.execute(
                    "SELECT id, tier, user_text, assistant_text, created_at "
                    "FROM episodes WHERE session_id=? "
                    + ("AND turn_number=?" if src_turn is not None else "")
                    + " ORDER BY created_at DESC LIMIT 1",
                    (src_session, src_turn) if src_turn is not None else (src_session,),
                ).fetchone()
                if ep_row:
                    ut = (ep_row["user_text"] or "")[:160]
                    at = (ep_row["assistant_text"] or "")[:120]
                    origin_episode = {
                        "id": ep_row["id"],
                        "tier": ep_row["tier"],
                        "preview": ut or at or ep_row["id"],
                        "created_at": ep_row["created_at"],
                        "turn": src_turn,
                    }
            conn.close()
            return {
                "ok": True,
                "belief_id": bid,
                "access_count": int(row["access_count"] or 0),
                "last_accessed": row["last_accessed"],
                "extraction_method": row["extraction_method"],
                "origin": {
                    "session": src_session,
                    "turn": src_turn,
                    "episode": origin_episode,
                },
            }
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/memory/v2/beliefs/{belief_id}/invalidate")
    async def _memory_v2_belief_invalidate(belief_id: str):
        """Soft-invalidate a belief and best-effort remove its Neo4j edge."""
        from kazma_core.memory.hygiene import invalidate_belief

        return invalidate_belief(belief_id, remove_graph=True)
    @self.app.get("/api/memory/v2/entity-merges")
    def _memory_v2_entity_merges(limit: int = 50, offset: int = 0):
        """Pending entity merge quarantine list."""

        from kazma_core.memory.entity_resolution import (
            count_pending_merges,
            list_pending_merges,
        )

        try:
            conn = open_memory_db()
            lim = max(1, min(limit, 200))
            off = max(0, int(offset or 0))
            merges = list_pending_merges(conn, limit=lim, offset=off, tenant_id=_mem_tid())
            total = count_pending_merges(conn)
            conn.close()
            return {"merges": merges, "total": total, "offset": off, "limit": lim}
        except Exception as exc:
            return {"merges": [], "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/memory/v2/entity-merges/{merge_id}")
    async def _memory_v2_entity_merge_decide(merge_id: str, request: Request):
        """Approve or reject a pending entity merge. Body: {action: approve|reject}."""

        from kazma_core.memory.entity_resolution import decide_entity_merge

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        action = str((body or {}).get("action") or "approve").strip().lower()
        approve = action in ("approve", "approved", "yes", "true", "1")

        def _decide() -> dict:
            # Off the event loop (audit F-06): this takes a write lock.
            conn = None
            try:
                conn = open_memory_db()
                return decide_entity_merge(conn, merge_id, approve=approve)
            except Exception as exc:
                logger.exception("[memory] entity merge decision failed")
                return {"ok": False, "error": safe_error(exc)}
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass

        return await asyncio.to_thread(_decide)
    @self.app.get("/api/memory/v2/queue")
    def _memory_v2_queue(status: str = "", limit: int = 50, offset: int = 0):
        """Memory task queue rows for the dashboard table."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        try:
            import os

            if not os.path.exists(memory_ops_db()):
                return {"tasks": [], "total": 0, "offset": 0, "limit": 50}
            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_ops_schema(conn)
            where = " FROM memory_task_queue"
            params: list = []
            if status and status.strip():
                where += " WHERE status = ?"
                params.append(status.strip())
            total = conn.execute(f"SELECT COUNT(*){where}", params).fetchone()[0]
            lim = max(1, min(limit, 200))
            off = max(0, int(offset or 0))
            sql = (
                "SELECT id, task_type, status, attempts, max_attempts,"
                " created_at, updated_at, error_log"
                + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, [*params, lim, off]).fetchall()
            conn.close()
            return {
                "tasks": [dict(r) for r in rows],
                "total": int(total),
                "offset": off,
                "limit": lim,
            }
        except Exception as exc:
            return {"tasks": [], "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/memory/v2/queue/{task_id}/retry")
    def _memory_v2_queue_retry(task_id: str):
        """Re-queue a failed/dead-letter task as pending."""
        import sqlite3
        import time as _time

        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        try:
            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            ensure_ops_schema(conn)
            now = _time.time()
            cur = conn.execute(
                """
                UPDATE memory_task_queue
                SET status='pending', attempts=0, error_log=NULL, updated_at=?
                WHERE id=? AND status IN ('failed', 'pending')
                """,
                (now, task_id),
            )
            conn.commit()
            n = int(cur.rowcount or 0)
            conn.close()
            return {"ok": n > 0, "updated": n}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/memory/v2/queue/clear-failed")
    def _memory_v2_queue_clear_failed():
        """Delete dead-letter (failed) tasks from the durable queue."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        try:
            import os

            if not os.path.exists(memory_ops_db()):
                return {"ok": True, "deleted": 0}
            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            ensure_ops_schema(conn)
            cur = conn.execute("DELETE FROM memory_task_queue WHERE status='failed'")
            conn.commit()
            n = int(cur.rowcount or 0)
            conn.close()
            return {"ok": True, "deleted": n}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/episodes")
    def _memory_v2_episodes(limit: int = 40, tier: str = "", offset: int = 0):
        """Recent episodes for Dashboard overlay (id, tier, preview text)."""


        try:
            conn = open_memory_db()
            where = " FROM episodes WHERE 1=1"
            params: list = []
            if tier and tier.strip():
                where += " AND tier = ?"
                params.append(tier.strip())
            else:
                where += " AND tier IN ('working','episodic','recall')"
            total = conn.execute(f"SELECT COUNT(*){where}", params).fetchone()[0]
            lim = max(1, min(int(limit or 40), 100))
            off = max(0, int(offset or 0))
            sql = (
                "SELECT id, tier, user_text, assistant_text, created_at, session_id"
                + where
                + " ORDER BY created_at DESC LIMIT ? OFFSET ?"
            )
            rows = conn.execute(sql, [*params, lim, off]).fetchall()
            conn.close()
            out = []
            for r in rows:
                ut = (r["user_text"] or "")[:120]
                at = (r["assistant_text"] or "")[:80]
                preview = ut or at or r["id"]
                out.append(
                    {
                        "id": r["id"],
                        "tier": r["tier"],
                        "preview": preview,
                        "created_at": r["created_at"],
                        "session_id": r["session_id"] or "",
                    }
                )
            return {
                "ok": True,
                "episodes": out,
                "total": int(total),
                "offset": off,
                "limit": lim,
            }
        except Exception as exc:
            return {"ok": False, "episodes": [], "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.post("/api/memory/v2/reconsolidate", dependencies=[Depends(rate_limit("admin_ops", 10))])
    async def _memory_v2_reconsolidate():
        """Enqueue a global_reconsolidation task (Dashboard / Settings trigger)."""
        try:
            from kazma_core.memory.task_queue import enqueue_task

            tid = enqueue_task(
                "global_reconsolidation",
                {"tenant_id": "default", "max_merges": 50, "reembed_limit": 100},
            )
            return {"ok": True, "task_id": tid}
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
    @self.app.get("/api/memory/v2/procedural")
    def _memory_v2_procedural(limit: int = 20, q: str = ""):
        """List active procedural DAGs (skills browser)."""

        from kazma_core.memory.procedural import match_procedural_dags

        try:
            conn = open_memory_db()
            if q and q.strip():
                dags = match_procedural_dags(conn, q.strip(), limit=max(1, min(limit, 50)))
            else:
                rows = conn.execute(
                    """
                    SELECT id, name, description, confidence_score, success_count,
                           total_trials, status, dag_steps_json
                    FROM procedural_dags
                    WHERE status = 'active'
                      AND tenant_id = ?
                    ORDER BY confidence_score DESC, total_trials DESC
                    LIMIT ?
                    """,
                    (_mem_tid(), max(1, min(limit, 50))),
                ).fetchall()
                dags = []
                for r in rows:
                    import json as _json

                    try:
                        steps = _json.loads(r["dag_steps_json"] or "[]")
                    except Exception:
                        steps = []
                    dags.append(
                        {
                            "id": r["id"],
                            "name": r["name"],
                            "description": r["description"],
                            "confidence": float(r["confidence_score"] or 0),
                            "success_count": int(r["success_count"] or 0),
                            "total_trials": int(r["total_trials"] or 0),
                            "steps": steps if isinstance(steps, list) else [],
                        }
                    )
            conn.close()
            return {"dags": dags}
        except Exception as exc:
            return {"dags": [], "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/quality")
    def _memory_v2_quality():
        """Lightweight memory quality score for Dashboard (no LLM)."""
        import os
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        checks: list[dict] = []
        score = 0
        total = 0

        def _check(name: str, ok: bool, detail: str = "") -> None:
            nonlocal score, total
            total += 1
            if ok:
                score += 1
            checks.append({"name": name, "ok": ok, "detail": detail})

        try:
            dbp = primary_memory_db()
            _check("db_exists", os.path.exists(dbp), dbp)
            if os.path.exists(dbp):
                conn = sqlite3.connect(dbp, check_same_thread=False)
                ensure_primary_schema(conn)
                bel = conn.execute(
                    "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
                ).fetchone()[0]
                ep = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE tier IN ('working','episodic','recall')"
                ).fetchone()[0]
                emb = conn.execute(
                    "SELECT COUNT(*) FROM episodes WHERE embedding IS NOT NULL"
                ).fetchone()[0]
                conn.close()
                _check("has_beliefs_or_episodes", (bel + ep) > 0, f"beliefs={bel} episodes={ep}")
                _check(
                    "has_some_embeddings",
                    emb > 0 or ep == 0,
                    f"embedded={emb}/{ep}",
                )
            try:
                from kazma_core.memory.embedder import get_embedder

                _check("embedder_ready", get_embedder() is not None)
            except Exception:
                _check("embedder_ready", False, "error")
            try:
                from kazma_core.memory.backends import vector_capability

                cap = vector_capability()
                _check(
                    "vector_capability",
                    bool(cap.get("vector_search_ready")),
                    cap.get("vector_status_detail") or "",
                )
            except Exception:
                _check("vector_capability", False)
            pct = round(100.0 * score / max(1, total), 1)
            return {
                "ok": True,
                "score": pct,
                "passed": score,
                "total": total,
                "checks": checks,
                "grade": "A" if pct >= 90 else "B" if pct >= 70 else "C" if pct >= 50 else "D",
            }
        except Exception as exc:
            return {"ok": False, "score": 0, "error": safe_error(exc), "checks": checks}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/graph/export")
    def _memory_v2_graph_export(format: str = "json", limit: int = 500):
        """Export belief graph as JSON or GraphML for download."""
        import xml.sax.saxutils as xu

        from fastapi.responses import Response

        fmt = (format or "json").strip().lower()
        try:
            conn = open_memory_db()
            tid = _mem_tid()
            tsql, tparams = _tenant_clause(tid)
            rows = conn.execute(
                """
                SELECT id, subject, object, predicate, confidence, predicate_type
                FROM beliefs
                WHERE valid_until IS NULL AND invalidated_at IS NULL
                """
                + tsql
                + """
                ORDER BY structural_importance DESC, confidence DESC
                LIMIT ?
                """,
                (*tparams, max(10, min(limit, 2000))),
            ).fetchall()
            conn.close()
            nodes: dict[str, dict] = {}
            links = []
            for r in rows:
                for ent in (r["subject"], r["object"]):
                    if ent and ent not in nodes:
                        nodes[ent] = {"id": ent, "label": ent}
                links.append(
                    {
                        "source": r["subject"],
                        "target": r["object"],
                        "predicate": r["predicate"],
                        "confidence": r["confidence"],
                        "id": r["id"],
                    }
                )
            if fmt == "graphml":
                parts = [
                    '<?xml version="1.0" encoding="UTF-8"?>',
                    '<graphml xmlns="http://graphml.graphdrawing.org/xmlns">',
                    '<key id="label" for="node" attr.name="label" attr.type="string"/>',
                    '<key id="predicate" for="edge" attr.name="predicate" attr.type="string"/>',
                    '<graph id="G" edgedefault="directed">',
                ]
                for n in nodes.values():
                    parts.append(
                        f'<node id="{xu.escape(n["id"])}">'
                        f'<data key="label">{xu.escape(n["label"])}</data></node>'
                    )
                for i, e in enumerate(links):
                    parts.append(
                        f'<edge id="e{i}" source="{xu.escape(e["source"])}" '
                        f'target="{xu.escape(e["target"])}">'
                        f'<data key="predicate">{xu.escape(str(e["predicate"]))}</data></edge>'
                    )
                parts.append("</graph></graphml>")
                body = "\n".join(parts)
                return Response(
                    content=body,
                    media_type="application/graphml+xml",
                    headers={
                        "Content-Disposition": 'attachment; filename="kazma_belief_graph.graphml"'
                    },
                )
            import json as _json

            payload = _json.dumps(
                {"nodes": list(nodes.values()), "links": links},
                ensure_ascii=False,
                indent=2,
            )
            return Response(
                content=payload,
                media_type="application/json",
                headers={
                    "Content-Disposition": 'attachment; filename="kazma_belief_graph.json"'
                },
            )
        except Exception as exc:
            return {"ok": False, "error": safe_error(exc)}
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
    @self.app.get("/api/memory/v2/graph")
    def _memory_v2_graph(
        at: float = 0.0,
        type: str = "",
        entity_type: str = "",
        limit: int = 200,
        source: str = "",
    ):
        """V2 belief graph as {nodes, links, stats} for the canvas.

        Dashboard paint is always from SQLite by default (entity types,
        predicate_type, bi-temporal scrub). Neo4j remains dual-write + optional
        probe via ``source=neo4j``. Beliefs remain SoT in SQLite.

        Params:
            at: unix timestamp for bi-temporal scrubbing. When >0, returns
                beliefs valid AT that moment (valid_from <= at AND
                (valid_until IS NULL OR valid_until >= at)). When 0
                (default), returns only currently-valid beliefs. Superseded
                beliefs surfacing via ``at`` are marked ``superseded=true``
                so the canvas can render them dashed.
            type: filter by predicate_type ('functional'|'set'|'state').
            entity_type: filter entities by type (person|tool|concept|...).
            limit: max nodes (default 200).
            source: ``sqlite`` (default) | ``neo4j`` (operator probe).
        """


        def _graph_backend_meta() -> dict:
            """Neo4j/sqlite dual-write status for stats line (never raises)."""
            meta = {
                "paint_source": "sqlite",
                "graph_provider": "sqlite",
                "graph_online": True,
                "dual_write": False,
            }
            try:
                from kazma_core.memory.backends import get_backends_cfg
                from kazma_core.memory.graph_backend import get_graph_backend

                gcfg = (get_backends_cfg().get("graph") or {})
                provider = str(gcfg.get("provider") or "sqlite").lower()
                meta["graph_provider"] = provider or "sqlite"
                if provider == "neo4j":
                    meta["dual_write"] = True
                    gb = get_graph_backend()
                    meta["graph_online"] = (
                        getattr(gb, "name", "") == "neo4j"
                        and bool(getattr(gb, "available", False))
                    )
                else:
                    meta["graph_online"] = True
            except Exception:
                pass
            return meta

        # ── Optional Neo4j probe only (not default Dashboard paint) ──
        src_pref = (source or "").strip().lower()
        if src_pref == "neo4j" and float(at or 0) <= 0:
            try:
                from kazma_core.memory.graph_backend import get_graph_backend

                gb = get_graph_backend()
                if getattr(gb, "name", "") == "neo4j" and getattr(gb, "available", False):
                    if hasattr(gb, "export_topology"):
                        topo = gb.export_topology(limit=limit)
                        if topo.get("nodes") or topo.get("links"):
                            meta = _graph_backend_meta()
                            meta["paint_source"] = "neo4j"
                            stats = dict(topo.get("stats") or {})
                            stats.update(meta)
                            stats.setdefault("source", "neo4j")
                            topo["stats"] = stats
                            return topo
            except Exception:
                pass

        try:
            conn = open_memory_db()

            # ── Belief links ──
            # Tenant-scoped (M-05): the canvas renders only the requesting
            # tenant's beliefs. Single-user default behaves identically.
            _gtid = _mem_tid()
            bsql = (
                "SELECT id, subject, object, predicate, predicate_type, "
                "confidence, structural_importance, valid_from, valid_until "
                "FROM beliefs WHERE invalidated_at IS NULL"
                + (" AND tenant_id = ?" if _gtid != "default" else "")
            )
            bparams: list = []
            if _gtid != "default":
                bparams.append(_gtid)
            if at and float(at) > 0:
                atf = float(at)
                bsql += " AND valid_from <= ? AND (valid_until IS NULL OR valid_until >= ?)"
                bparams.extend([atf, atf])
            else:
                bsql += " AND valid_until IS NULL"
            if type and type.strip():
                bsql += " AND predicate_type = ?"
                bparams.append(type.strip())
            bsql += " ORDER BY (structural_importance * confidence) DESC LIMIT ?"
            bparams.append(max(10, min(limit * 4, 800)))
            brows = conn.execute(bsql, bparams).fetchall()

            # ── Object texts (belief targets) + subject entity refs ──
            # When an object text equals an entity id (e.g. user → has_project
            # → shipx where shipx is a real entity), we must emit ONE node —
            # the real entity — not a second virtual "fact" node with the same
            # id. Duplicate ids make the canvas id→index map last-write-wins
            # and orphan one of the two painted nodes (the shipx-alone bug).
            #
            # Self / hub: canvas hub is always id=user. Person shells like
            # ent_* named User/Mubder collapse onto that hub so rename and
            # list focus land on the same node (not a missing orphan).
            from kazma_core.memory.self_hub import (
                HUB_ID,
                collect_self_entity_ids,
                ensure_user_hub,
                is_self_label,
                resolve_hub_display_name,
            )
            try:
                from kazma_core.memory.hygiene import is_junk_entity_token
            except Exception:
                def is_junk_entity_token(t: str) -> bool:  # type: ignore[misc]
                    return str(t or "").strip().lower() in (
                        "true", "false", "null", "none", "yes", "no", "0", "1",
                    )

            obj_texts: set[str] = set()
            obj_belief_count: dict[str, int] = {}
            for b in brows:
                if b["object"]:
                    obj_texts.add(b["object"])
                    obj_belief_count[b["object"]] = (
                        obj_belief_count.get(b["object"], 0) + 1
                    )

            self_ids = collect_self_entity_ids(conn)
            hub_name = resolve_hub_display_name(conn)
            # Heal: if a person shell was renamed (User→Mubder) but entities.user
            # still missing/generic, upsert the hub so the label sticks.
            try:
                hub_row = conn.execute(
                    "SELECT name FROM entities WHERE id=?", (HUB_ID,)
                ).fetchone()
                hub_row_name = str(hub_row["name"] if hub_row else "") or ""
                if hub_name and hub_name != "You" and (
                    not hub_row or is_self_label(hub_row_name) or hub_row_name.lower() == "user"
                ):
                    ensure_user_hub(conn, hub_name)
                    conn.commit()
                    self_ids = collect_self_entity_ids(conn)
                    hub_name = resolve_hub_display_name(conn)
            except Exception:
                pass

            ref_ids: set[str] = set()
            for b in brows:
                sub = b["subject"]
                # Collapse self person shells onto the hub node
                ref_ids.add(HUB_ID if sub in self_ids else sub)
            ref_ids.add(HUB_ID)
            # Promote object strings that are real entity ids into the entity
            # node set so links target the entity, not a colliding virtual.
            lookup_ids = set(ref_ids) | obj_texts | self_ids
            nodes: list[dict] = []
            ent_lookup: dict[str, dict] = {}
            if lookup_ids:
                placeholders = ",".join("?" * len(lookup_ids))
                erows = conn.execute(
                    f"SELECT id, name, type, is_high_stakes, is_major FROM entities WHERE id IN ({placeholders})",
                    tuple(lookup_ids),
                ).fetchall()
                ent_lookup = {r["id"]: dict(r) for r in erows}
            for oid in obj_texts:
                if oid in self_ids:
                    # Object points at a self shell — treat as hub
                    continue
                if is_junk_entity_token(oid):
                    continue
                if oid in ent_lookup:
                    ref_ids.add(oid)

            belief_count: dict[str, int] = {}
            for b in brows:
                sub = b["subject"]
                key = HUB_ID if sub in self_ids else sub
                belief_count[key] = belief_count.get(key, 0) + 1
            # Inbound objects that name a self id count on the hub
            for oid, cnt in obj_belief_count.items():
                if oid in self_ids:
                    belief_count[HUB_ID] = belief_count.get(HUB_ID, 0) + cnt

            for eid in ref_ids:
                if eid in self_ids and eid != HUB_ID:
                    # Never emit a second self person node — hub only
                    continue
                if is_junk_entity_token(eid):
                    continue
                e = ent_lookup.get(eid)
                if eid == HUB_ID:
                    etype = "person"
                    ename = hub_name
                    stakes = True
                    if e and e.get("is_high_stakes") is not None:
                        stakes = bool(e["is_high_stakes"])
                else:
                    etype = e["type"] if e else "concept"
                    ename = (e["name"] if e and e["name"] else eid)
                    stakes = bool(e["is_high_stakes"]) if e else False
                if entity_type and entity_type.strip() and etype != entity_type.strip():
                    continue
                bc = belief_count.get(eid, 0)
                if eid != HUB_ID:
                    bc = bc + obj_belief_count.get(eid, 0)
                nodes.append({
                    "id": eid,
                    "name": ename,
                    "type": etype,
                    "isHighStakes": stakes,
                    "isMajor": bool(e and int(e.get("is_major") or 0) == 1) if eid != HUB_ID else True,
                    "beliefCount": bc,
                    "isHub": eid == HUB_ID,
                })

            # Virtual fact nodes only for pure text objects that are NOT
            # already real entity nodes (id collision → skip; keep entity).
            existing_ids = {n["id"] for n in nodes}
            for obj_text in obj_texts:
                if obj_text in existing_ids:
                    continue
                # Never promote booleans/nulls to graph nodes (orphan "true"
                # concept with 5 beliefs was pure object-payload pollution).
                if is_junk_entity_token(obj_text):
                    continue
                # Virtual nodes are type 'concept'; drop under entity_type filter.
                if entity_type and entity_type.strip() and "concept" != entity_type.strip():
                    continue
                nodes.append({
                    "id": obj_text,
                    "name": obj_text,
                    "type": "concept",
                    "isHighStakes": False,
                    "beliefCount": obj_belief_count.get(obj_text, 0),
                    "isVirtual": True,
                })
                existing_ids.add(obj_text)

            links: list[dict] = []
            node_ids = {n["id"] for n in nodes}
            for b in brows:
                # Drop a belief link if EITHER endpoint was removed by a
                # filter (e.g. entity_type). Map self shells → hub id so
                # edges attach to the single You/Mubder node.
                src = HUB_ID if b["subject"] in self_ids else b["subject"]
                tgt_raw = b["object"] or ""
                tgt = HUB_ID if tgt_raw in self_ids else tgt_raw
                if src not in node_ids:
                    continue
                if tgt not in node_ids:
                    continue
                links.append({
                    "id": b["id"],
                    "source": src,
                    "target": tgt,
                    "label": b["predicate"],
                    "object_text": b["object"],
                    "type": b["predicate_type"],
                    "confidence": b["confidence"],
                    "superseded": b["valid_until"] is not None,
                    "valid_from": b["valid_from"],
                    "valid_until": b["valid_until"],
                })

            # F: graph groupings — read BEFORE conn.close() (the block below
            # operates on a closed connection otherwise → ProgrammingError).
            # Purely advisory; recall/extraction never read this. /graph does
            # not tenant-scope beliefs today (admin overview is operator-wide),
            # so read groupings unscoped for consistency (audit C2 tracks the
            # tenant scope separately).
            groups: list[dict] = []
            member_tier: dict[str, int] = {}
            try:
                grows = conn.execute(
                    "SELECT group_root, member, member_tier, label "
                    "FROM graph_associations"
                ).fetchall()
                groups = [dict(r) for r in grows]
                member_tier = {r["member"]: int(r["member_tier"]) for r in grows}
            except Exception:
                logger.warning("[memory_v2_graph] group associations read failed", exc_info=True)

            conn.close()
            # Stamp each node with its tier (0=hub, 1-3=grouped, -1=ungrouped).
            for n in nodes:
                if n.get("isHub"):
                    n["tier"] = 0
                else:
                    n["tier"] = member_tier.get(n.get("id"), -1)
            nodes.sort(key=lambda n: n["beliefCount"], reverse=True)
            # Pre-slice totals so the UI can show "showing 200 of N" and
            # decide whether to warn the operator about truncation.
            total_nodes = len(nodes)
            total_links = len(links)
            truncated = total_nodes > limit
            kept_ids = {n["id"] for n in nodes[:limit]}
            links = [l for l in links if l["source"] in kept_ids and l["target"] in kept_ids]

            valid_froms = [l["valid_from"] for l in links if l["valid_from"]]
            type_counts: dict[str, int] = {}
            for n in nodes[:limit]:
                t = str(n.get("type") or "concept")
                type_counts[t] = type_counts.get(t, 0) + 1
            pred_counts: dict[str, int] = {}
            for l in links:
                t = str(l.get("type") or "set")
                pred_counts[t] = pred_counts.get(t, 0) + 1
            meta = _graph_backend_meta()
            meta["paint_source"] = "sqlite"
            stats = {
                "nodes": len(nodes[:limit]),
                "links": len(links),
                "total_nodes": total_nodes,
                "total_links": total_links,
                "truncated": truncated,
                "limit": limit,
                "superseded": sum(1 for l in links if l["superseded"]),
                "earliest": min(valid_froms, default=0),
                "latest": max(valid_froms, default=0),
                "source": "sqlite",
                "entity_type_counts": type_counts,
                "predicate_type_counts": pred_counts,
            }
            stats.update(meta)
            # `groups` was read above (before conn.close); tiers stamped on nodes.
            return {"nodes": nodes[:limit], "links": links, "stats": stats, "groups": groups}
        except Exception as exc:
            return {
                "nodes": [],
                "links": [],
                "groups": [],
                "stats": {**_graph_backend_meta(), "source": "sqlite", "error": True},
                "error": safe_error(exc),
            }
        finally:
            try:
                conn.close()
            except Exception:
                pass  # already closed / never opened
