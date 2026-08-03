"""Direct route registrations for the Kazma UI web application.

Extracted from the god-module app.py to keep route registration highly modular and maintainable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse as _JSONResponse

logger = logging.getLogger(__name__)

__all__ = ["register_direct_routes"]


def register_direct_routes(self: Any) -> None:
    """Register direct FastAPI route handlers onto self.app."""

    @self.app.get("/metrics")
    async def _metrics():
        """Prometheus metrics endpoint."""
        from kazma_core.metrics import get_metrics_response
        body, status, headers = get_metrics_response()
        return _JSONResponse(content=body.decode() if isinstance(body, bytes) else body, media_type=headers["content-type"])

    @self.app.get("/api/system/debug/registry")
    async def _debug_registry():
        import kazma_core.model_registry as _mr

        reg = _mr._registry
        if reg is None:
            return {"status": "not_initialized", "hint": "ModelRegistry not initialized. Start the app normally."}
        return {
            "status": "initialized",
            "active_provider": reg._active_provider or "none",
            "active_profile": reg.get_active_profile(),
            "providers": reg._list_all_providers() if hasattr(reg, '_list_all_providers') else [],
            "saved_profiles": reg.list_model_profiles(mask_api_key=True),
            "registered_models": reg._registered_models if hasattr(reg, '_registered_models') else {},
            "discovered_models": reg.get_discovered_models(),
            "unified_options": reg.list_unified_options(),
        }

    @self.app.post("/api/system/flush")
    async def _system_flush():
        import glob as _glob_sys
        import os as _os_sys

        try:
            from kazma_core.paths import data_dir, settings_db, user_home

            _home = user_home()
            paths = {
                "kazma_home": str(_home),
                "config_db": settings_db(),
                "config_yaml": next(iter(_glob_sys.glob(str(_home / "*.yaml"))), ""),
                "pending_evolution": str(_home / "pending_evolution.json"),
                "knowledge_graph": str(data_dir() / "knowledge_graph.db"),
            }
        except Exception:
            paths = {
                "kazma_home": str(_os_sys.path.expanduser("~/.kazma")),
                "config_db": str(_os_sys.path.expanduser("~/.kazma/config.db")),
                "config_yaml": next(
                    iter(_glob_sys.glob(_os_sys.path.expanduser("~/.kazma/*.yaml"))), ""
                ),
                "pending_evolution": str(
                    _os_sys.path.expanduser("~/.kazma/pending_evolution.json")
                ),
                "knowledge_graph": str(
                    _os_sys.path.expanduser("kazma-data/knowledge_graph.json")
                ),
            }
        # Flush model registry cache
        try:
            import kazma_core.model_registry as _mr

            _mr._registry = None
        except Exception as exc:
            logger.debug("Model registry cache flush failed: %s", exc)
        # Flush WorkerRegistry cache
        try:
            from kazma_core.swarm.registry import WorkerRegistry

            WorkerRegistry._instance = None
        except Exception as exc:
            logger.debug("Worker registry cache flush failed: %s", exc)
        # Flush tool registry (the real registry is LocalToolRegistry)
        try:
            from kazma_core.agent.tool_registry import get_tool_registry

            # LocalToolRegistry caches the singleton in _builtin_registry.
            import kazma_core.agent.tool_registry as _tr_mod

            _tr_mod._builtin_registry = None
        except Exception as exc:
            logger.debug("Tool registry cache flush failed: %s", exc)
        return {"status": "flushed", "config_paths": paths}

    @self.app.get("/api/system/config-paths")
    async def _system_config_paths():
        import os as _osp

        try:
            from kazma_core.paths import data_dir, settings_db, snapshots_db, user_home

            home = str(user_home())
            cfg = settings_db()
            kg = str(data_dir() / "knowledge_graph.db")
            snap = snapshots_db()
            pending = str(user_home() / "pending_evolution.json")
        except Exception:
            home = _osp.path.expanduser("~/.kazma")
            cfg = _osp.path.join(home, "config.db")
            kg = _osp.path.expanduser("kazma-data/knowledge_graph.json")
            snap = _osp.path.expanduser("kazma-data/snapshots.db")
            pending = _osp.path.join(home, "pending_evolution.json")
        return {
            "kazma_home": home,
            "config_db": cfg if _osp.path.exists(cfg) else "NOT FOUND",
            "swarm_registry": (
                _osp.path.expanduser("swarm_registry.json")
                if _osp.path.exists(_osp.path.expanduser("swarm_registry.json"))
                else "NOT FOUND"
            ),
            "pending_evolution": pending if _osp.path.exists(pending) else "NOT FOUND",
            "knowledge_graph": kg if _osp.path.exists(kg) else "NOT FOUND",
            "snapshots_db": snap if _osp.path.exists(snap) else "NOT FOUND",
        }

    @self.app.delete("/api/mcp/servers/{server_name}")
    async def _delete_mcp_server(server_name: str):
        try:
            self.agent.remove_mcp_server(server_name)
            return {"status": "ok", "message": f"Server '{server_name}' deleted"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    @self.app.get("/api/telemetry/typing")
    async def _typing_signal():
        return {"status": "processing", "timestamp": __import__("time").time()}

    @self.app.post("/api/telemetry/typing/stream_start")
    async def _stream_start(req: dict):
        worker_name = req.get("worker_name", "unknown")
        task_id = req.get("task_id", "")
        logger.info("[Stream] Typing started — worker=%s task=%s", worker_name, task_id)
        return {"status": "stream_started", "worker_name": worker_name, "task_id": task_id}

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

        hits = search(q.strip(), limit=max(1, min(int(limit), 50)))
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
    async def _memory_graph_clear():
        """Invalidate all currently-active V2 beliefs (bi-temporal clear).

        Replaces the legacy destructive ``kg.clear()``. V2 is append-only /
        bi-temporal by design — rather than deleting rows, this marks every
        currently-active belief invalidated (sets ``invalidated_at`` /
        ``valid_until``) so they stop surfacing in recall while history is
        preserved for point-in-time queries. Episodes are not touched.
        """
        import sqlite3
        import time

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            ensure_primary_schema(conn)
            now = time.time()
            before = conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            ).fetchone()[0]
            conn.execute(
                "UPDATE beliefs SET valid_until=?, invalidated_at=? "
                "WHERE valid_until IS NULL AND invalidated_at IS NULL",
                (now, now),
            )
            conn.commit()
            after = conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            ).fetchone()[0]
            conn.close()
            return {"ok": True, "invalidated_beliefs": before, "active_remaining": after}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

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

            return federated_search(
                query,
                tenant_id=str((body or {}).get("tenant_id") or "default"),
                session_id=(body or {}).get("session_id") or None,
                limit_memory=int((body or {}).get("limit_memory") or 5),
                limit_kb=int((body or {}).get("limit_kb") or 5),
                include_memory=bool((body or {}).get("include_memory", True)),
                include_knowledge=bool((body or {}).get("include_knowledge", True)),
            )
        except Exception as exc:
            return {
                "ok": False,
                "error": str(exc)[:300],
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
                "error": str(exc)[:400],
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
        tenant_id = str((body or {}).get("tenant_id") or "default")
        if not query:
            return {"ok": False, "error": "query required", "beliefs": [], "episodes": []}
        try:
            from kazma_core.memory.recall import recall

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
            return {"ok": False, "error": str(exc)[:300], "beliefs": [], "episodes": []}

    @self.app.get("/api/memory/v2/beliefs")
    async def _memory_v2_beliefs(q: str = "", limit: int = 50):
        """Active V2 beliefs (currently valid only), optional FTS filter."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            sql = (
                "SELECT id, subject, predicate, predicate_type, object, confidence, "
                "structural_importance, valid_from, source_trust_weight, extraction_method, "
                "access_count, last_accessed, supersedes_id "
                "FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            )
            params: list = []
            if q and q.strip():
                ql = f"%{q.strip().lower()}%"
                sql += " AND (LOWER(subject) LIKE ? OR LOWER(predicate) LIKE ? OR LOWER(object) LIKE ?)"
                params = [ql, ql, ql]
            sql += " ORDER BY (structural_importance * confidence * source_trust_weight) DESC LIMIT ?"
            params.append(max(1, min(limit, 200)))
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return {"beliefs": [dict(r) for r in rows]}
        except Exception as exc:
            return {"beliefs": [], "error": str(exc)}

    @self.app.get("/api/memory/v2/beliefs/{belief_id}")
    async def _memory_v2_belief_detail(belief_id: str):
        """Belief detail + supersede chain."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            row = conn.execute("SELECT * FROM beliefs WHERE id=?", (belief_id,)).fetchone()
            if not row:
                conn.close()
                return {"ok": False, "error": "not_found"}
            chain = [dict(row)]
            # Walk supersedes_id ancestors
            cur = row
            for _ in range(20):
                sid = cur["supersedes_id"] if "supersedes_id" in cur.keys() else None
                if not sid:
                    break
                prev = conn.execute("SELECT * FROM beliefs WHERE id=?", (sid,)).fetchone()
                if not prev:
                    break
                chain.append(dict(prev))
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
                "belief": dict(row),
                "chain": chain,
                "superseded_by": [dict(k) for k in kids],
            }
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.post("/api/memory/v2/beliefs/{belief_id}/invalidate")
    async def _memory_v2_belief_invalidate(belief_id: str):
        """Soft-invalidate a belief and best-effort remove its Neo4j edge."""
        from kazma_core.memory.hygiene import invalidate_belief

        return invalidate_belief(belief_id, remove_graph=True)

    @self.app.get("/api/memory/v2/entity-merges")
    async def _memory_v2_entity_merges(limit: int = 50):
        """Pending entity merge quarantine list."""
        import sqlite3

        from kazma_core.memory.entity_resolution import list_pending_merges
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            merges = list_pending_merges(conn, limit=limit)
            conn.close()
            return {"merges": merges}
        except Exception as exc:
            return {"merges": [], "error": str(exc)[:300]}

    @self.app.post("/api/memory/v2/entity-merges/{merge_id}")
    async def _memory_v2_entity_merge_decide(merge_id: str, request: Request):
        """Approve or reject a pending entity merge. Body: {action: approve|reject}."""
        import sqlite3

        from kazma_core.memory.entity_resolution import decide_entity_merge
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        body = {}
        try:
            body = await request.json()
        except Exception:
            pass
        action = str((body or {}).get("action") or "approve").strip().lower()
        approve = action in ("approve", "approved", "yes", "true", "1")
        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            result = decide_entity_merge(conn, merge_id, approve=approve)
            conn.close()
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.get("/api/memory/v2/queue")
    async def _memory_v2_queue(status: str = "", limit: int = 50):
        """Memory task queue rows for the dashboard table."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_ops_schema
        from kazma_core.paths import memory_ops_db

        try:
            import os

            if not os.path.exists(memory_ops_db()):
                return {"tasks": []}
            conn = sqlite3.connect(memory_ops_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_ops_schema(conn)
            sql = (
                "SELECT id, task_type, status, attempts, max_attempts, "
                "created_at, updated_at, error_log FROM memory_task_queue"
            )
            params: list = []
            if status and status.strip():
                sql += " WHERE status = ?"
                params.append(status.strip())
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(limit, 200)))
            rows = conn.execute(sql, params).fetchall()
            conn.close()
            return {"tasks": [dict(r) for r in rows]}
        except Exception as exc:
            return {"tasks": [], "error": str(exc)[:300]}

    @self.app.post("/api/memory/v2/queue/{task_id}/retry")
    async def _memory_v2_queue_retry(task_id: str):
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
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.post("/api/memory/v2/queue/clear-failed")
    async def _memory_v2_queue_clear_failed():
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
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.get("/api/memory/v2/episodes")
    async def _memory_v2_episodes(limit: int = 40, tier: str = ""):
        """Recent episodes for Dashboard overlay (id, tier, preview text)."""
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            sql = (
                "SELECT id, tier, user_text, assistant_text, created_at, session_id "
                "FROM episodes WHERE 1=1"
            )
            params: list = []
            if tier and tier.strip():
                sql += " AND tier = ?"
                params.append(tier.strip())
            else:
                sql += " AND tier IN ('working','episodic','recall')"
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(max(1, min(int(limit or 40), 100)))
            rows = conn.execute(sql, params).fetchall()
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
            return {"ok": True, "episodes": out}
        except Exception as exc:
            return {"ok": False, "episodes": [], "error": str(exc)[:300]}

    @self.app.post("/api/memory/v2/reconsolidate")
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
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.get("/api/memory/v2/procedural")
    async def _memory_v2_procedural(limit: int = 20, q: str = ""):
        """List active procedural DAGs (skills browser)."""
        import sqlite3

        from kazma_core.memory.procedural import match_procedural_dags
        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            if q and q.strip():
                dags = match_procedural_dags(conn, q.strip(), limit=max(1, min(limit, 50)))
            else:
                rows = conn.execute(
                    """
                    SELECT id, name, description, confidence_score, success_count,
                           total_trials, status, dag_steps_json
                    FROM procedural_dags
                    WHERE status = 'active'
                    ORDER BY confidence_score DESC, total_trials DESC
                    LIMIT ?
                    """,
                    (max(1, min(limit, 50)),),
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
            return {"dags": [], "error": str(exc)[:300]}

    @self.app.get("/api/memory/v2/quality")
    async def _memory_v2_quality():
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
            return {"ok": False, "score": 0, "error": str(exc)[:300], "checks": checks}

    @self.app.get("/api/memory/v2/graph/export")
    async def _memory_v2_graph_export(format: str = "json", limit: int = 500):
        """Export belief graph as JSON or GraphML for download."""
        import sqlite3
        import xml.sax.saxutils as xu

        from fastapi.responses import Response

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

        fmt = (format or "json").strip().lower()
        try:
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)
            rows = conn.execute(
                """
                SELECT id, subject, object, predicate, confidence, predicate_type
                FROM beliefs
                WHERE valid_until IS NULL AND invalidated_at IS NULL
                ORDER BY structural_importance DESC, confidence DESC
                LIMIT ?
                """,
                (max(10, min(limit, 2000)),),
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
            return {"ok": False, "error": str(exc)[:300]}

    # ── Phase D: Memory backends Settings API ─────────────────────────
    @self.app.get("/api/settings/memory/merge-kb")
    async def _settings_memory_merge_kb_get():
        from kazma_core.config_store import get_config_store
        from kazma_core.memory.config import read_memory_cfg

        v2 = (read_memory_cfg() or {}).get("v2") or {}
        # Knowledge smart search lives under knowledge.* (ConfigStore)
        smart = False
        try:
            smart = bool(get_config_store().get("knowledge.smart_search") or False)
        except Exception:
            smart = False
        return {
            "merge_knowledge_into_chat": bool(v2.get("merge_knowledge_into_chat", True)),
            "promote_kb_to_episodes": bool(v2.get("promote_kb_to_episodes", True)),
            "explain_recall": bool(v2.get("explain_recall", True)),
            "smart_search": smart,
        }

    @self.app.put("/api/settings/memory/merge-kb")
    async def _settings_memory_merge_kb_put(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            return {"ok": False, "error": "invalid JSON"}
        try:
            from kazma_core.config_store import get_config_store

            store = get_config_store()
            items = []
            if "merge_knowledge_into_chat" in (body or {}):
                items.append(
                    (
                        "memory.v2.merge_knowledge_into_chat",
                        bool(body["merge_knowledge_into_chat"]),
                        "memory",
                    )
                )
            if "promote_kb_to_episodes" in (body or {}):
                items.append(
                    (
                        "memory.v2.promote_kb_to_episodes",
                        bool(body["promote_kb_to_episodes"]),
                        "memory",
                    )
                )
            if "explain_recall" in (body or {}):
                items.append(
                    (
                        "memory.v2.explain_recall",
                        bool(body["explain_recall"]),
                        "memory",
                    )
                )
            if "smart_search" in (body or {}):
                items.append(
                    (
                        "knowledge.smart_search",
                        bool(body["smart_search"]),
                        "knowledge",
                    )
                )
            if items and hasattr(store, "batch_set"):
                store.batch_set(items)
            else:
                for k, v, c in items:
                    store.set(k, v, category=c)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.get("/api/settings/memory/backends")
    async def _settings_memory_backends_get():
        from kazma_core.memory.backends import (
            get_backends_cfg,
            mask_backends_cfg,
            vector_capability,
        )

        cfg = get_backends_cfg()
        try:
            from kazma_core.memory.graph_backend import graph_capability
            from kazma_core.memory.state_backend import state_capability

            gcap = graph_capability(cfg)
            scap = state_capability(cfg)
        except Exception:
            gcap, scap = {}, {}
        return {
            "ok": True,
            "backends": mask_backends_cfg(cfg),
            "capability": vector_capability(cfg),
            "graph_capability": gcap,
            "state_capability": scap,
        }

    @self.app.put("/api/settings/memory/backends")
    async def _settings_memory_backends_put(request: Request):
        body = {}
        try:
            body = await request.json()
        except Exception:
            return {"ok": False, "error": "invalid JSON"}
        try:
            from kazma_core.memory.backends import save_backends_cfg

            masked = save_backends_cfg(body if isinstance(body, dict) else {})
            return {"ok": True, "backends": masked}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.post("/api/settings/memory/backends/test-embed")
    async def _settings_memory_test_embed():
        from kazma_core.memory.backends import test_embedder_backend

        return test_embedder_backend()

    @self.app.post("/api/settings/memory/backends/test-vector")
    async def _settings_memory_test_vector():
        from kazma_core.memory.backends import test_vector_backend

        return test_vector_backend()

    @self.app.post("/api/settings/memory/backends/test-neo4j")
    async def _settings_memory_test_neo4j(request: Request):
        """Probe Neo4j using saved config, or optional body override before save."""
        body = {}
        try:
            body = await request.json()
        except Exception:
            body = {}
        from kazma_core.memory.backends import get_backends_cfg
        from kazma_core.memory.graph_backend import test_neo4j_connection

        cfg = get_backends_cfg()
        if isinstance(body, dict) and (body.get("graph") or body.get("url")):
            g = dict(cfg.get("graph") or {})
            saved_pw = str(g.get("password") or g.get("api_key") or "")
            if body.get("graph"):
                incoming = dict(body["graph"] or {})
                # UI masks secrets as "***" — never let that overwrite vault password
                ipw = incoming.get("password")
                if ipw is None or str(ipw).strip() in ("", "***") or str(ipw).strip().startswith("***"):
                    incoming.pop("password", None)
                iak = incoming.get("api_key")
                if iak is None or str(iak).strip() in ("", "***"):
                    incoming.pop("api_key", None)
                g.update(incoming)
            else:
                if body.get("url") is not None:
                    g["url"] = body["url"]
                if body.get("user") is not None:
                    g["user"] = body["user"]
                if body.get("password") is not None and str(body.get("password")) not in (
                    "",
                    "***",
                ):
                    g["password"] = body["password"]
            # Restore saved secret if client sent mask/empty
            if not str(g.get("password") or "").strip() or str(g.get("password")).strip() in (
                "***",
            ):
                if saved_pw and saved_pw not in ("***",):
                    g["password"] = saved_pw
            g["provider"] = "neo4j"
            cfg = {**cfg, "graph": g}
        return test_neo4j_connection(cfg)

    @self.app.post("/api/settings/memory/backends/sync-neo4j")
    async def _settings_memory_sync_neo4j():
        """Backfill active SQLite beliefs into Neo4j (needed once after enabling)."""
        from kazma_core.memory.graph_backend import sync_beliefs_to_neo4j

        return sync_beliefs_to_neo4j(tenant_id="default", limit=1000)

    @self.app.post("/api/settings/memory/backends/reset-local")
    async def _settings_memory_reset_local():
        from kazma_core.memory.backends import reset_backends_to_local

        return {"ok": True, "backends": reset_backends_to_local()}

    @self.app.post("/api/settings/memory/backends/rebuild")
    async def _settings_memory_rebuild():
        """Kick off embedding rebuild (reuses reembed module)."""
        try:
            import asyncio

            from kazma_core.memory.reembed import rebuild_embeddings

            asyncio.create_task(asyncio.to_thread(rebuild_embeddings))
            return {"ok": True, "started": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}

    @self.app.get("/api/settings/memory/backends/rebuild/status")
    async def _settings_memory_rebuild_status():
        from kazma_core.memory.reembed import get_rebuild_status

        return get_rebuild_status()

    @self.app.get("/api/memory/v2/graph")
    async def _memory_v2_graph(
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
        import sqlite3

        from kazma_core.memory.schema_v2 import ensure_primary_schema
        from kazma_core.paths import primary_memory_db

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
            conn = sqlite3.connect(primary_memory_db(), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            ensure_primary_schema(conn)

            # ── Belief links ──
            bsql = (
                "SELECT id, subject, object, predicate, predicate_type, "
                "confidence, structural_importance, valid_from, valid_until "
                "FROM beliefs WHERE invalidated_at IS NULL"
            )
            bparams: list = []
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
                    f"SELECT id, name, type, is_high_stakes FROM entities WHERE id IN ({placeholders})",
                    tuple(lookup_ids),
                ).fetchall()
                ent_lookup = {r["id"]: dict(r) for r in erows}
            for oid in obj_texts:
                if oid in self_ids:
                    # Object points at a self shell — treat as hub
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
                    "beliefCount": bc,
                    "isHub": eid == HUB_ID,
                })

            # Virtual fact nodes only for pure text objects that are NOT
            # already real entity nodes (id collision → skip; keep entity).
            existing_ids = {n["id"] for n in nodes}
            for obj_text in obj_texts:
                if obj_text in existing_ids:
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

            conn.close()
            nodes.sort(key=lambda n: n["beliefCount"], reverse=True)
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
                "superseded": sum(1 for l in links if l["superseded"]),
                "earliest": min(valid_froms, default=0),
                "latest": max(valid_froms, default=0),
                "source": "sqlite",
                "entity_type_counts": type_counts,
                "predicate_type_counts": pred_counts,
            }
            stats.update(meta)
            return {"nodes": nodes[:limit], "links": links, "stats": stats}
        except Exception as exc:
            return {
                "nodes": [],
                "links": [],
                "stats": {**_graph_backend_meta(), "source": "sqlite", "error": True},
                "error": str(exc),
            }

    import kazma_core.time_travel as _tt_mod

    @self.app.get("/api/session/history")
    async def _session_history(thread_id: str = "", limit: int = 20):
        store = _tt_mod.SnapshotStore()
        if thread_id:
            records = store.list_for_thread(thread_id)[:limit]
        else:
            records = []
        return {"sessions": [r.to_dict() for r in records]}

    @self.app.post("/api/session/replay")
    async def _session_replay(req: dict):
        thread_id = req.get("thread_id", "")
        iteration = req.get("iteration", 0)
        if not thread_id:
            from fastapi import HTTPException as _httpx

            raise _httpx(status_code=400, detail="thread_id required")
        engine = _tt_mod.ReplayEngine()
        return await engine.replay_from(thread_id, iteration)

    @self.app.get("/api/system/status")
    async def _get_system_status():
        import os as _os
        import sqlite3

        from kazma_core.config_store import get_config_store
        from kazma_core.system.maintenance import get_memory_paths

        # Demo mode: report DEMO instead of DEGRADED so the UI hides the
        # install button and shows a clean "demo mode" message.
        _demo_mode = _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes")

        store = get_config_store()
        if _demo_mode:
            status = "DEMO"
        else:
            status = store.get("system.memory.status") or "ACTIVE"

        fts5_path, vector_path, _ = get_memory_paths()

        fts5_size = fts5_path.stat().st_size if fts5_path.exists() else 0
        fts5_count = 0
        if fts5_path.exists():
            try:
                conn = sqlite3.connect(fts5_path)
                cursor = conn.cursor()
                # Canonical L3 schema first; legacy memory_fts as fallback.
                for table in ("memories", "memory_fts", "memory_fts_migrated"):
                    cursor.execute(
                        "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name=?",
                        (table,),
                    )
                    if cursor.fetchone():
                        try:
                            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
                            fts5_count = int(cursor.fetchone()[0] or 0)
                            break
                        except Exception:
                            continue
                conn.close()
            except Exception as _e:
                logger.debug("fts5 count failed: %s", _e)
                try:
                    conn.close()  # type: ignore[name-defined]
                except Exception:
                    pass

        vector_size = 0
        if vector_path.exists() and vector_path.is_dir():
            vector_size = sum(
                f.stat().st_size for f in vector_path.glob("**/*") if f.is_file()
            )

        # V1 ChromaDB vector count — always 0 now (V1 stack removed). Kept in
        # the payload for dashboard backward-compat (the KPI grid still reads
        # vector_count/vector_size); the values are inert.
        vector_count = 0

        # V2 cognitive-engine KPIs (computed early so graph_stats below can
        # reuse the belief counts). When V2 is the active stack, the dashboard
        # should prefer this `v2` block over the legacy layer counts above.
        v2_block: dict = {}
        memory_stack = "v1"
        try:
            from kazma_core.memory.config import memory_v2_enabled

            if memory_v2_enabled():
                memory_stack = "v2"
                from kazma_core.memory.v2_health import build_v2_health

                v2_block = build_v2_health()
        except Exception as _e:
            logger.debug("v2 health probe failed: %s", _e)

        # V2 belief counts, surfaced in the legacy `graph` field shape so the
        # dashboard KPI strip keeps working. "nodes" = active beliefs;
        # "edges" = active + superseded (the full belief graph). The real V2
        # KPI board (/api/memory/v2/health) is the authoritative source.
        graph_stats: dict = {"nodes": 0, "edges": 0, "backend": "v2", "path": ""}
        try:
            bel = (v2_block.get("beliefs") if v2_block else None) or {}
            active = int(bel.get("active", 0))
            superseded = int(bel.get("superseded", 0))
            graph_stats["nodes"] = active
            graph_stats["edges"] = active + superseded
        except Exception as _e:
            logger.debug("v2 belief stats failed: %s", _e)

        # Per-component green/red board for Memory & Governance UI.
        health: dict = {"components": [], "issues": [], "summary": ""}
        try:
            from kazma_core.memory.health import build_memory_health

            health = build_memory_health()
            live = str(health.get("status") or "")
            # INSTALLING from ConfigStore takes priority; otherwise live probe wins.
            if status == "INSTALLING" or live == "INSTALLING":
                status = "INSTALLING"
            elif live in ("DEMO", "DEGRADED", "ACTIVE"):
                status = live
        except Exception as _e:
            logger.warning("memory health probe failed: %s", _e)
            health = {
                "components": [],
                "issues": [str(_e)],
                "summary": "health probe failed",
            }

        # Compact feature flags from health components for the KPI strip.
        flags: dict = {}
        for c in health.get("components") or []:
            cid = c.get("id")
            if cid in (
                "memory_enabled",
                "per_turn_retrieval",
                "auto_store",
                "consolidation",
                "embedder",
                "vector_memory",
                "layer_l1",
                "layer_l2",
                "layer_l3",
                "layer_l4",
            ):
                flags[cid] = {
                    "ok": bool(c.get("ok")),
                    "status": c.get("status"),
                    "detail": c.get("detail"),
                    "meta": c.get("meta") or {},
                }

        return {
            "status": status,
            "memory_stack": memory_stack,
            "fts5_size": fts5_size,
            "fts5_count": fts5_count,
            "vector_size": vector_size,
            "vector_count": vector_count,
            "graph": graph_stats,
            "flags": flags,
            "v2": v2_block,
            "components": health.get("components", []),
            "issues": health.get("issues", []),
            "summary": health.get("summary", ""),
            "headline": health.get("headline", ""),
            "backend": health.get("backend", {}),
        }

    @self.app.post("/api/system/install")
    async def _post_system_install(req: dict = None):
        """Install an allowlisted package or pyproject extra in the background.

        Body (JSON)::
            {"extra": "rag"}                  # preferred — uv pip install -e ".[rag]"
            {"package_name": "chromadb"}     # single allowlisted package

        Supply-chain safe: only extras/packages in the installer allowlists.
        """
        req = req or {}
        import os as _os
        # In demo mode, ML deps can't be installed (container has no build
        # tools and not enough RAM). Return a clean message.
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return {"status": "unavailable", "message": "ML dependencies are not available in demo mode."}

        from kazma_core.system import (
            ALLOWED_EXTRAS,
            ALLOWED_PACKAGES,
            asynchronous_install_extra,
            asynchronous_install_package,
        )

        extra = (req.get("extra") or "").strip().lower()
        package_name = (req.get("package_name") or "").strip()

        if extra:
            if extra not in ALLOWED_EXTRAS:
                return {
                    "status": "error",
                    "message": f"Extra '{extra}' is not in the allowed list: {sorted(ALLOWED_EXTRAS)}",
                }
            await asynchronous_install_extra(extra)
            return {"status": "started", "extra": extra}

        if not package_name:
            package_name = "sentence-transformers"
        if package_name not in ALLOWED_PACKAGES:
            return {
                "status": "error",
                "message": f"Package '{package_name}' is not in the allowed list: {sorted(ALLOWED_PACKAGES)}",
            }
        await asynchronous_install_package(package_name)
        return {"status": "started", "package": package_name}

    @self.app.get("/api/system/install/status")
    async def _get_install_status() -> dict[str, Any]:
        """Last background install status (for Settings → Packages UI)."""
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        return {
            "target": store.get("system.install.last_target", ""),
            "status": store.get("system.install.last_status", ""),
            "error": store.get("system.install.last_error", ""),
            "memory_status": store.get("system.memory.status", ""),
        }

    @self.app.get("/api/alerts/recent")
    async def _get_recent_alerts():
        from kazma_core.observability.alerts import AlertDispatcher
        return [
            a.to_dict() if hasattr(a, "to_dict") else a
            for a in AlertDispatcher.get_recent_alerts()
        ]

    @self.app.get("/api/system/memory/backups")
    async def _list_memory_backups():
        """List V2 native backup files (memory_state_<ts>.db / memory_ops_<ts>.db)."""
        from pathlib import Path

        from kazma_core.paths import backups_dir

        try:
            bdir = Path(backups_dir())
            backups: list[dict[str, Any]] = []
            if bdir.is_dir():
                for f in sorted(bdir.glob("memory_*_*.db"), reverse=True):
                    try:
                        st = f.stat()
                    except Exception:
                        continue
                    backups.append({
                        "name": f.name,
                        "size": st.st_size,
                        "timestamp": int(st.st_mtime),
                        "path": str(f),
                    })
            return {"backups": backups}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.get("/packages")
    async def _packages_redirect() -> RedirectResponse:
        """Legacy /packages page → Settings Packages tab."""
        return RedirectResponse("/settings?tab=packages", status_code=307)

    # ── Auth bootstrap (remote clients — loopback auto-cookie is disabled) ──
    @self.app.get("/login", response_class=HTMLResponse)
    async def _login_page(request: Request) -> HTMLResponse:
        """Render the secret login form for non-loopback browsers."""
        return self.templates.TemplateResponse(
            request,
            "login.html",
            {},
        )

    @self.app.get("/api/auth/status")
    async def _auth_status(request: Request) -> dict[str, Any]:
        """Whether auth is enabled and whether this request is authenticated."""
        from kazma_ui.auth import (
            get_kazma_secret,
            get_request_principal,
            is_authenticated,
            _is_loopback_client,
        )

        expected = get_kazma_secret()
        oidc = False
        multi_user = False
        try:
            from kazma_core.security.oidc import oidc_configured
            from kazma_core.security.platform_rbac import multi_user_enabled

            oidc = oidc_configured()
            multi_user = multi_user_enabled()
        except Exception:
            pass
        if not expected:
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "open",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        # Public demo mode: report as open/authenticated so the client skips
        # the login redirect. Matches the middleware bypass in auth.py.
        import os as _os
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return {
                "auth_enabled": False,
                "authenticated": True,
                "mode": "demo",
                "oidc": oidc,
                "multi_user": multi_user,
            }
        ok = is_authenticated(request, expected)
        principal = get_request_principal(request) if ok else None
        return {
            "auth_enabled": True,
            "authenticated": ok,
            "loopback": _is_loopback_client(request),
            "mode": "secret",
            "oidc": oidc,
            "multi_user": multi_user,
            "principal": principal,
        }

    # Login brute-force throttle (audit M3) — in-process sliding window per IP
    _login_failures: dict[str, list[float]] = {}
    _LOGIN_WINDOW_S = 300.0
    _LOGIN_MAX_FAILS = 10

    @self.app.post("/api/auth/login")
    async def _auth_login(request: Request) -> Response:
        """Exchange KAZMA_SECRET for an HttpOnly session cookie."""
        import time as _time

        from kazma_ui.auth import (
            SECRET_COOKIE,
            get_kazma_secret,
            verify_secret,
            _is_https,
        )

        client_ip = (request.client.host if request.client else "") or "unknown"
        now = _time.time()
        recent = [
            t for t in _login_failures.get(client_ip, [])
            if now - t < _LOGIN_WINDOW_S
        ]
        _login_failures[client_ip] = recent
        if len(recent) >= _LOGIN_MAX_FAILS:
            logger.warning("[auth] login rate limit hit for %s", client_ip)
            return _JSONResponse(
                {"detail": "Too many failed login attempts — try again later"},
                status_code=429,
            )

        expected = get_kazma_secret()
        if not expected:
            return _JSONResponse(
                {"status": "ok", "message": "Auth disabled (no KAZMA_SECRET)"},
                status_code=200,
            )
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        secret = str(body.get("secret") or "").strip()
        username = str(body.get("username") or "").strip()
        password = str(body.get("password") or "").strip()
        session_user = None
        session_role = "admin"
        session_uid = None
        authenticated = False

        # Path A: multi-user local username + password (Phase 4.4)
        if username and password:
            try:
                from kazma_core.security.platform_rbac import authenticate_local_user

                pu = authenticate_local_user(username, password)
                if pu is not None:
                    authenticated = True
                    session_user = pu.username
                    session_role = pu.role
                    session_uid = pu.user_id
            except Exception:
                logger.debug("[auth] local user auth failed", exc_info=True)

        # Path B: shared operator secret
        if not authenticated:
            check = secret or password
            if check and verify_secret(check, expected):
                authenticated = True
                session_user = "operator"
                session_role = "admin"
                session_uid = "shared-secret"

        if not authenticated:
            recent.append(now)
            _login_failures[client_ip] = recent
            return _JSONResponse(
                {"detail": "Invalid credentials"},
                status_code=401,
            )

        # Success — clear failures for this IP
        _login_failures.pop(client_ip, None)

        resp = _JSONResponse({
            "status": "ok",
            "authenticated": True,
            "username": session_user,
            "role": session_role,
        })
        # Opaque session cookie preferred (audit H1)
        try:
            from kazma_core.security.web_sessions import (
                SESSION_COOKIE,
                create_session,
                use_opaque_sessions,
            )

            if use_opaque_sessions():
                sid = create_session(
                    actor="login",
                    username=session_user,
                    role=session_role,
                    user_id=session_uid,
                )
                resp.set_cookie(
                    key=SESSION_COOKIE,
                    value=sid,
                    httponly=True,
                    samesite="lax",
                    path="/",
                    secure=_is_https(request),
                    max_age=60 * 60 * 24 * 14,
                )
                resp.delete_cookie(SECRET_COOKIE, path="/")
                return resp
        except Exception:
            logger.debug("[auth] opaque session create failed; legacy cookie", exc_info=True)
        resp.set_cookie(
            key=SECRET_COOKIE,
            value=expected,
            httponly=True,
            samesite="lax",  # LAN/IP + form POST login
            path="/",
            secure=_is_https(request),
            max_age=60 * 60 * 24 * 14,  # 14 days
        )
        return resp

    @self.app.get("/api/auth/oidc/start")
    async def _oidc_start(request: Request) -> Response:
        """Redirect browser to configured OIDC IdP (Phase 4.4)."""
        from fastapi.responses import RedirectResponse

        try:
            from kazma_core.security.oidc import build_authorize_url, oidc_configured

            if not oidc_configured():
                return _JSONResponse(
                    {"error": "OIDC not configured (KAZMA_OIDC_ISSUER + CLIENT_ID)"},
                    status_code=503,
                )
            info = await build_authorize_url()
            return RedirectResponse(url=info["url"], status_code=302)
        except Exception as exc:
            logger.exception("[oidc] start failed")
            return _JSONResponse({"error": str(exc)}, status_code=500)

    @self.app.get("/api/auth/oidc/callback")
    async def _oidc_callback(request: Request) -> Response:
        """OIDC callback — mint opaque session from IdP claims."""
        from fastapi.responses import RedirectResponse
        from kazma_ui.auth import SESSION_COOKIE, _is_https

        code = request.query_params.get("code") or ""
        state = request.query_params.get("state") or ""
        if not code or not state:
            return _JSONResponse({"error": "Missing code/state"}, status_code=400)
        try:
            from kazma_core.security.oidc import exchange_code
            from kazma_core.security.web_sessions import create_session, use_opaque_sessions

            result = await exchange_code(code, state)
            if not use_opaque_sessions():
                return _JSONResponse(
                    {"error": "Opaque sessions required for OIDC"},
                    status_code=500,
                )
            sid = create_session(
                actor="oidc",
                username=result.get("username"),
                role=result.get("role") or "operator",
                user_id=result.get("user_id"),
            )
            resp = RedirectResponse(url="/", status_code=302)
            resp.set_cookie(
                key=SESSION_COOKIE,
                value=sid,
                httponly=True,
                samesite="lax",
                path="/",
                secure=_is_https(request),
                max_age=60 * 60 * 24 * 14,
            )
            return resp
        except Exception as exc:
            logger.exception("[oidc] callback failed")
            return _JSONResponse({"error": str(exc)}, status_code=400)

    @self.app.get("/api/auth/me")
    async def _auth_me(request: Request) -> Response:
        """Return current principal (role/username) for UI chrome."""
        from kazma_ui.auth import get_kazma_secret, get_request_principal, is_authenticated
        import os as _os

        # Public demo mode: report as an authenticated demo visitor.
        if _os.environ.get("KAZMA_DEMO_MODE", "").lower() in ("1", "true", "yes"):
            return _JSONResponse({"authenticated": True, "source": "demo", "role": "admin"})
        secret = get_kazma_secret()
        if secret and not is_authenticated(request, secret):
            return _JSONResponse({"authenticated": False}, status_code=401)
        principal = get_request_principal(request) or {}
        return _JSONResponse({"authenticated": True, **principal})

    @self.app.post("/api/auth/logout")
    async def _auth_logout(request: Request) -> Response:
        """Clear auth cookies and revoke opaque session."""
        from kazma_ui.auth import SECRET_COOKIE, SESSION_COOKIE

        try:
            from kazma_core.security.web_sessions import revoke_session

            sid = request.cookies.get(SESSION_COOKIE) or ""
            if sid:
                revoke_session(sid)
        except Exception:
            pass
        resp = _JSONResponse({"status": "ok", "authenticated": False})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @self.app.get("/api/system/packages")
    async def _get_packages(request: Request):
        """List installed Python packages with metadata and extras status."""
        import importlib.metadata as ilm

        lang = request.cookies.get("kazma-lang") or "en"
        if lang not in ("ar", "en"):
            lang = "en"

        def _i18n(key: str, fallback: str) -> str:
            try:
                from kazma_ui.i18n import t as i18n_t

                loc = i18n_t(key, lang=lang)
                return loc if loc and loc != key else fallback
            except Exception:
                return fallback

        # ── Define the extras groups and their member packages ──
        # Keep package lists aligned with pyproject.toml optional-deps.
        # Keep package lists aligned with pyproject.toml [project.optional-dependencies]
        EXTRA_GROUPS = {
            "rag": {
                "title": _i18n("packages.extra.rag.title", "Memory & RAG"),
                "priority": 0,
                "description": _i18n(
                    "packages.extra.rag.desc",
                    "V2 cognitive memory: local embeddings (sentence-transformers) + sqlite-vec. chromadb optional legacy.",
                ),
                "packages": ["sentence-transformers", "sqlite-vec", "chromadb"],
                "install_cmd": 'uv pip install -e ".[rag]"   # embedder + local vectors',
            },
            "postgres": {
                "title": _i18n("packages.extra.postgres.title", "Postgres (multi-replica)"),
                "priority": 1,
                "description": _i18n(
                    "packages.extra.postgres.desc",
                    "Multi-replica shared state on Postgres + LangGraph Postgres checkpointer.",
                ),
                "packages": ["psycopg", "langgraph-checkpoint-postgres"],
                "install_cmd": 'uv pip install -e ".[postgres]"   # then set KAZMA_DATABASE_URL + migrate',
            },
            "document": {
                "title": _i18n("packages.extra.document.title", "Document generation"),
                "priority": 4,
                "description": _i18n(
                    "packages.extra.document.desc",
                    "PDF/DOCX/XLSX generation for document_generator skill.",
                ),
                "packages": ["reportlab", "python-docx", "openpyxl", "arabic-reshaper", "python-bidi"],
                "install_cmd": 'uv pip install -e ".[document]"',
            },
            "database": {
                "title": _i18n("packages.extra.database.title", "Extra DB drivers"),
                "priority": 5,
                "description": _i18n(
                    "packages.extra.database.desc",
                    "MySQL/Mongo drivers for database_client skill (beyond SQLite/Postgres).",
                ),
                "packages": ["psycopg", "pymysql", "pymongo"],
                "install_cmd": 'uv pip install -e ".[database]"',
            },
            "dev": {
                "title": _i18n("packages.extra.dev.title", "Development"),
                "priority": 10,
                "description": _i18n(
                    "packages.extra.dev.desc",
                    "Development tools — pytest, ruff, mypy, locust.",
                ),
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "ruff", "mypy", "locust"],
                "install_cmd": 'uv pip install -e ".[dev]"   # additive — won\'t remove other extras',
            },
            "test": {
                "title": _i18n("packages.extra.test.title", "Test"),
                "priority": 11,
                "description": _i18n(
                    "packages.extra.test.desc",
                    "Test-specific dependencies (lighter than dev).",
                ),
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "fakeredis", "httpx"],
                "install_cmd": 'uv pip install -e ".[test]"   # additive — won\'t remove other extras',
            },
            "tui": {
                "title": _i18n("packages.extra.tui.title", "TUI dashboard"),
                "priority": 6,
                "description": _i18n(
                    "packages.extra.tui.desc",
                    "Terminal dashboard UI (Textual) with RTL text.",
                ),
                "packages": ["textual", "python-bidi"],
                "install_cmd": 'uv pip install -e ".[tui]"   # additive — won\'t remove other extras',
            },
            "observability": {
                "title": _i18n("packages.extra.observability.title", "Observability"),
                "priority": 7,
                "description": _i18n(
                    "packages.extra.observability.desc",
                    "Prometheus metrics export for production monitoring.",
                ),
                "packages": ["prometheus-client"],
                "install_cmd": 'uv pip install -e ".[observability]"   # additive — won\'t remove other extras',
            },
            "web": {
                "title": _i18n("packages.extra.web.title", "Browser automation"),
                "priority": 8,
                "description": _i18n(
                    "packages.extra.web.desc",
                    "Browser automation via Playwright for JS-heavy pages.",
                ),
                "packages": ["playwright"],
                "install_cmd": 'uv pip install -e ".[web]"   # additive — won\'t remove other extras',
            },
        }

        EXTRA_PKG_DESCRIPTIONS = {
            "chromadb": "Optional legacy vector store (not required for V2 SQLite-first memory)",
            "sentence-transformers": "Local embeddings for V2 dense recall (e.g. BAAI/bge-m3)",
            "sqlite-vec": "Local vector tables for V2 hybrid dense recall",
            "neo4j": "Bolt driver for optional Neo4j belief dual-write (pip install neo4j)",
            "psycopg": "Postgres driver for multi-replica ConfigStore / sessions / dual-mirror",
            "langgraph-checkpoint-postgres": "Shared LangGraph checkpoints across replicas",
            "playwright": "Headless browser for JS-heavy crawl / research",
            "prometheus-client": "Prometheus /metrics exposition",
            "textual": "TUI framework for kazma-tui",
            "python-bidi": "Bidirectional text for Arabic TUI / documents",
            "reportlab": "PDF generation",
            "python-docx": "Word document generation",
            "openpyxl": "Excel generation",
            "arabic-reshaper": "Arabic text shaping for PDF/DOCX",
            "pymysql": "MySQL driver for database_client skill",
            "pymongo": "MongoDB driver for database_client skill",
            "fakeredis": "In-memory Redis stub for tests",
            "httpx": "HTTP client (also listed in test extra for completeness)",
        }

        # ── Core dependencies (always installed via monorepo packages) ──
        CORE_PACKAGES = [
            "fastapi", "uvicorn", "langgraph", "langgraph-checkpoint-sqlite",
            "aiosqlite", "langfuse", "pyyaml", "httpx", "cryptography",
            "PyJWT", "jinja2", "python-multipart", "psutil",
            "aiogram", "websockets", "duckduckgo-search", "trafilatura",
            "markdown", "tenacity", "networkx", "click", "rich",
            "google-cloud-aiplatform", "python-dotenv",
            # Workspace packages (editable install)
            "kazma-core", "kazma-ui", "kazma-gateway", "kazma-cli",
        ]

        CORE_DESCRIPTIONS = {
            "fastapi": "Web framework powering the Kazma dashboard + REST API",
            "uvicorn": "ASGI server that runs the FastAPI app",
            "langgraph": "LangGraph supervisor brain — the ReAct loop, checkpointing, interrupt()",
            "langgraph-checkpoint-sqlite": "SQLite-backed LangGraph checkpoints (default single-node)",
            "aiosqlite": "Async SQLite driver for default local stores",
            "langfuse": "Observability/tracing platform for LLM calls",
            "pyyaml": "YAML parser for kazma.yaml config + skill manifests",
            "httpx": "HTTP client for LLM API calls + web tools",
            "cryptography": "AES-256-GCM encryption for the secret vault",
            "PyJWT": "JWT token generation for GitHub App authentication",
            "jinja2": "HTML template engine for the web UI",
            "python-multipart": "File upload handling for FastAPI",
            "psutil": "System resource monitoring for telemetry",
            "aiogram": "Telegram Bot API framework",
            "websockets": "WebSocket support for real-time chat + gateway",
            "duckduckgo-search": "Privacy-focused web search (no API key needed)",
            "trafilatura": "Web content extraction (clean text from URLs)",
            "markdown": "Markdown rendering for chat messages",
            "tenacity": "Retry logic with exponential backoff for LLM calls",
            "networkx": "Graph algorithms for swarm DAG/topology",
            "click": "CLI framework for the `kazma` command",
            "rich": "Beautiful terminal output (colors, tables, progress bars)",
            "google-cloud-aiplatform": "Google Vertex AI provider integration",
            "python-dotenv": ".env file loading for local development",
            "kazma-core": "Agent brain, LLM providers, swarm, V2 memory, IDE",
            "kazma-ui": "FastAPI web app + Settings + Dashboard",
            "kazma-gateway": "Telegram / Discord / Slack adapters",
            "kazma-cli": "`kazma` CLI entrypoints",
        }

        # Surface neo4j driver if installed (not a pyproject extra — pip install neo4j)
        try:
            import importlib.util as _ilu

            if _ilu.find_spec("neo4j") is not None:
                EXTRA_GROUPS["neo4j"] = {
                    "title": "Neo4j (graph dual-write)",
                    "priority": 2,
                    "description": "Official neo4j Python driver for optional belief graph dual-write.",
                    "packages": ["neo4j"],
                    "install_cmd": "pip install neo4j   # then Settings → Memory → Neo4j",
                }
        except Exception:
            pass

        # Runtime DB backend badge for the Packages tab
        try:
            from kazma_core.db.backend import is_postgres, get_database_url

            _db_backend = "postgres" if is_postgres() else "sqlite"
            _db_url = get_database_url() or ""
        except Exception:
            _db_backend = "sqlite"
            _db_url = ""

        # ── Build the package list ──
        try:
            all_dists = {d.metadata["Name"]: d for d in ilm.distributions()}
            # Build a normalized lookup: lowercase + dashes/underscores unified
            norm_dists = {
                k.lower().replace("-", "_"): v for k, v in all_dists.items()
            }
        except Exception:
            all_dists = {}
            norm_dists = {}

        def _pkg_info(name: str) -> dict:
            # Normalize the search name the same way (lowercase, _ instead of -)
            norm = name.lower().replace("-", "_")
            dist = norm_dists.get(norm)
            if dist:
                return {
                    "name": dist.metadata["Name"],
                    "version": dist.version,
                    "installed": True,
                }
            return {"name": name, "version": "", "installed": False}

        # Core packages
        core_list = []
        for name in CORE_PACKAGES:
            info = _pkg_info(name)
            info["description"] = CORE_DESCRIPTIONS.get(name, "")
            info["group"] = "core"
            core_list.append(info)

        # Extras — fully installed only when *every* member package is present.
        # Partial (e.g. pytest from [dev] but no fakeredis) is reported so the
        # UI does not look like a broken install when only one niche dep is missing.
        extras_list = []
        for group_name, group_data in EXTRA_GROUPS.items():
            pkg_list = []
            for name in group_data["packages"]:
                info = _pkg_info(name)
                info["group"] = group_name
                info["description"] = EXTRA_PKG_DESCRIPTIONS.get(name, "")
                pkg_list.append(info)
            n_total = len(pkg_list)
            n_ok = sum(1 for p in pkg_list if p["installed"])
            group_installed = n_total > 0 and n_ok == n_total
            extras_list.append({
                "name": group_name,
                "title": group_data.get("title") or group_name,
                "priority": int(group_data.get("priority", 50)),
                "description": group_data["description"],
                "install_cmd": group_data["install_cmd"],
                "installed": group_installed,
                "partial": n_ok > 0 and n_ok < n_total,
                "installed_count": n_ok,
                "package_count": n_total,
                "packages": pkg_list,
            })
        extras_list.sort(key=lambda e: (e.get("priority", 50), e.get("name") or ""))

        # Live memory health snapshot for the Packages tab Memory card.
        memory_summary: dict = {
            "status": "UNKNOWN",
            "summary": "",
            "headline": "",
            "layers": {},
            "issues": [],
        }
        try:
            from kazma_core.memory.health import build_memory_health

            mh = build_memory_health()
            layers = {}
            for c in mh.get("components") or []:
                cid = c.get("id") or ""
                if cid in (
                    "embedder",
                    "vector_memory",
                    "layer_l1",
                    "layer_l2",
                    "layer_l3",
                    "layer_l4",
                    "pkg_chromadb",
                    "pkg_st",
                    "pkg_sqlite_vec",
                    "per_turn_retrieval",
                    "auto_store",
                    "consolidation",
                ):
                    layers[cid] = {
                        "ok": bool(c.get("ok")),
                        "status": c.get("status"),
                        "name": c.get("name"),
                        "detail": c.get("detail"),
                    }
            memory_summary = {
                "status": mh.get("status") or "UNKNOWN",
                "summary": mh.get("summary") or "",
                "headline": mh.get("headline") or "",
                "layers": layers,
                "issues": (mh.get("issues") or [])[:6],
            }
        except Exception as exc:
            memory_summary["summary"] = f"health probe failed: {exc}"

        # Count total installed (from distributions, not just our deps)
        total_installed = len(all_dists)

        return {
            "core": core_list,
            "extras": extras_list,
            "total_installed": total_installed,
            "python_version": __import__("sys").version.split()[0],
            "db_backend": _db_backend,
            "db_url_set": bool(_db_url),
            "memory": memory_summary,
        }

    @self.app.post("/api/system/memory/backup")
    async def _create_memory_backup():
        """V2 native backup — sqlite3.backup() of both V2 memory DBs."""
        try:
            from kazma_core.memory.backup import perform_native_backups

            written = perform_native_backups(retention=10)
            return {
                "status": "success",
                "manifest": {
                    "files": [str(p) for p in written],
                    "count": len(written),
                    "dir": "backups/",
                },
            }
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/restore")
    async def _restore_memory_backup(req: dict):
        """V2 restore — file-swap memory_state.db from a chosen backup.

        Body: ``{"backup_name": "memory_state_<ts>.db"}``. Quiesces the V2
        worker first (best-effort), then overwrites the live primary DB with
        the backup copy via sqlite3.restore(). Bi-temporal ops DB is NOT
        restored (it is rebuildable from the primary). Returns the new counts.
        """
        backup_name = str(req.get("backup_name", "")).strip()
        if not backup_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="backup_name is required")
        import sqlite3
        from pathlib import Path

        from kazma_core.paths import backups_dir, primary_memory_db

        try:
            src = Path(backups_dir()) / backup_name
            if not src.exists() or not src.is_file():
                from fastapi import HTTPException
                raise HTTPException(status_code=404, detail=f"backup {backup_name!r} not found")
            # Quiesce: nudge the durable worker to pause so no write races the swap.
            try:
                from kazma_core.memory.task_queue import get_worker

                get_worker()._wake.clear()
            except Exception:
                pass
            dest = Path(primary_memory_db())
            # Online restore: open the live DB and restore from the backup file.
            conn = sqlite3.connect(str(dest))
            try:
                with sqlite3.connect(str(src)) as bkp:
                    bkp.backup(conn, pages=100, sleep=0.01)
                conn.commit()
            finally:
                conn.close()
            # Count active beliefs in the restored DB for confirmation.
            conn = sqlite3.connect(str(dest))
            conn.row_factory = sqlite3.Row
            active = conn.execute(
                "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"
            ).fetchone()[0]
            conn.close()
            return {
                "status": "success",
                "restored_from": backup_name,
                "active_beliefs": active,
            }
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/maintenance")
    async def _run_memory_maintenance():
        """V2 maintenance — VACUUM + ANALYZE both V2 memory DBs."""
        import sqlite3

        from kazma_core.paths import memory_ops_db, primary_memory_db

        try:
            details: dict[str, Any] = {}
            for label, db in (("primary", primary_memory_db()), ("ops", memory_ops_db())):
                try:
                    conn = sqlite3.connect(db)
                    conn.execute("VACUUM")
                    conn.execute("ANALYZE")
                    conn.close()
                    details[label] = "VACUUM + ANALYZE ok"
                except Exception as exc:
                    details[label] = f"failed: {exc}"
            return {"status": "success", "details": details}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/snapshots/maintain")
    async def _run_snapshot_maintenance():
        """Time-travel snapshots: TTL prune + VACUUM to reclaim disk.

        Retention is read LIVE from the ConfigStore (Settings UI), so the
        manual run and the daily auto-loop always agree.
        """
        from kazma_core.time_travel import maintain_snapshots
        from kazma_core.time_travel import _live_maintenance_config

        try:
            cfg = _live_maintenance_config()
            stats = maintain_snapshots(retention_days=cfg["retention_days"])
            return {"status": "success", "stats": stats, "auto_maintain": cfg["auto_maintain"]}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import websocket_is_authenticated

        if not websocket_is_authenticated(websocket):
            await websocket.close(code=4003, reason="Unauthorized")
            return
        await websocket.accept()
        from kazma_core.shutdown import is_shutting_down
        from kazma_core.tracing import get_trace_store

        store = get_trace_store()
        store.register_ws(websocket)
        try:
            import json

            await websocket.send_text(
                json.dumps(
                    {
                        "type": "connected",
                        "message": "Real-time dashboard feed active",
                    }
                )
            )
            while not is_shutting_down():
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                except TimeoutError:
                    continue
                except Exception as _e:
                    logger.debug("[WS] events receive error, closing: %s", _e)
                    break
        except Exception as exc:
            logger.debug("WS events handler stopped: %s", exc)
        finally:
            store.unregister_ws(websocket)

    @self.app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "config": self.agent.config,
                "active_page": "dashboard",
                "cost_current": 0.0,
                "cost_max": 0.50,
                "cost_headroom": 0.50,
                "cost_color": "var(--success)",
                "breaker_status": "closed",
                "breaker_color": "var(--success)",
                "silence_info": "",
                "tracing_backend": "console",
                "traces": [],
                "metrics": {},
            },
        )

    @self.app.get("/chat", response_class=HTMLResponse)
    async def chat_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)

    @self.app.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "workspace.html",
            {
                "config": self.agent.config,
                "active_page": "workspace",
            },
        )

    @self.app.get("/ide", response_class=HTMLResponse)
    async def ide_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "ide.html",
            {
                "config": self.agent.config,
                "active_page": "ide",
            },
        )

    @self.app.get("/replay", response_class=HTMLResponse)
    async def replay_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "replay.html",
            {
                "config": self.agent.config,
                "active_page": "replay",
            },
        )

    @self.app.get("/research", response_class=HTMLResponse)
    async def research_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "research.html",
            {
                "config": self.agent.config,
                "active_page": "research",
            },
        )

    @self.app.get("/knowledge", response_class=HTMLResponse)
    async def knowledge_page(request: Request) -> HTMLResponse:
        return self.templates.TemplateResponse(
            request,
            "knowledge_base.html",
            {
                "config": self.agent.config,
                "active_page": "knowledge",
            },
        )

    @self.app.post("/api/gateway/refresh-adapters")
    async def refresh_gateway_adapters() -> dict[str, Any]:
        if self.gateway is None:
            return {"status": "error", "message": "Gateway not initialized"}
        logger.info("[Gateway] Refreshing adapters — stopping old adapters")

        for old_adapter in self.gateway.adapters:
            try:
                await old_adapter.stop()
            except Exception:
                logger.warning("[Gateway] Error stopping adapter %s during refresh", old_adapter.name, exc_info=True)

        self.gateway.adapters.clear()

        telegram_token = (
            self.config_store.get("connectors.telegram.token", "")
            or self.config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        )
        if not telegram_token:
            telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        if telegram_token:
            from kazma_gateway.adapters.telegram import TelegramAdapter

            voice_cfg = self.config.raw.get("gateway", {}).get("voice", {})
            tg_adapter = TelegramAdapter(
                token=telegram_token,
                voice_enabled=voice_cfg.get("enabled", False),
                voice_provider=voice_cfg.get("stt_provider", "openai"),
                stt_api_key=None,
                tts_provider=voice_cfg.get("tts_provider", "edgetts"),
                tts_voice=voice_cfg.get("tts_voice", "default"),
                tts_output_format=voice_cfg.get("tts_output_format", "mp3"),
                stt_language=voice_cfg.get("stt_language", "auto"),
            )
            allowed = self.config_store.get("connectors.telegram.allowed_users", "")
            if allowed:
                try:
                    allowed_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]
                    tg_adapter.set_allowed_users(allowed_ids)
                except ValueError:
                    logger.warning("[Gateway] Invalid allowed_users format: %s", allowed)
            self.gateway.add_adapter(tg_adapter)
            logger.info("[Gateway] Telegram adapter re-registered via refresh")

        discord_token = self.config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
        if discord_token:
            from kazma_gateway.adapters.discord import DiscordAdapter

            discord_adapter = DiscordAdapter(token=discord_token)
            self.gateway.add_adapter(discord_adapter)
            logger.info("[Gateway] Discord adapter re-registered via refresh")

        _cs_slack_bot2 = self.config_store.get("connectors.slack.token", "")
        _cs_slack_app2 = self.config_store.get("connectors.slack.app_token", "")
        slack_bot_token = (_cs_slack_bot2 if _cs_slack_bot2.startswith("xoxb-") else "") or os.environ.get("SLACK_BOT_TOKEN", "")
        slack_app_token = (_cs_slack_app2 if _cs_slack_app2.startswith("xapp-") else "") or os.environ.get("SLACK_APP_TOKEN", "")
        if slack_bot_token:
            from kazma_gateway.adapters.slack import SlackAdapter

            slack_adapter = SlackAdapter(bot_token=slack_bot_token, app_token=slack_app_token or None)
            self.gateway.add_adapter(slack_adapter)
            logger.info("[Gateway] Slack adapter re-registered via refresh")

        for new_adapter in self.gateway.adapters:
            try:
                await new_adapter.start(self.gateway.queue, self.gateway._shutdown)
                logger.info("[Gateway] Adapter %s started via refresh", new_adapter.name)
            except Exception:
                logger.warning("[Gateway] Failed to start adapter %s during refresh", new_adapter.name, exc_info=True)

        logger.info("[Gateway] Adapter refresh complete — %d adapter(s) running", len(self.gateway.adapters))
        return {
            "status": "ok",
            "adapters_count": len(self.gateway.adapters),
            "adapters": [a.name for a in self.gateway.adapters],
        }

    @self.app.get("/health")
    async def health_check() -> dict[str, Any]:
        if self.gateway is None:
            return {
                "status": "ok",
                "gateway_started": False,
                "queue_depth": 0,
                "queue_maxsize": 100,
                "adapters_count": 0,
                "adapters_running": 0,
                "adapters": [],
                "init_errors": self._init_errors,
            }
        adapters = [_a for _a in self.gateway.adapters] if hasattr(self.gateway, 'adapters') else []
        queue = getattr(self.gateway, 'queue', None)
        return {
            "status": "ok",
            "gateway_started": getattr(self.gateway, '_started', False),
            "queue_depth": queue.qsize() if queue else 0,
            "queue_maxsize": queue.maxsize if queue and hasattr(queue, 'maxsize') else 100,
            "adapters_count": len(adapters),
            "adapters_running": sum(1 for a in adapters if getattr(a, '_running', False)),
            "adapters": [
                {
                    "name": getattr(a, 'name', '?'),
                    "platform": getattr(a, 'platform', getattr(a, 'name', '?')),
                    "running": getattr(a, '_running', False),
                }
                for a in adapters
            ],
            "init_errors": self._init_errors,
        }

    def _resolve_hitl_graph() -> Any:
        return self._hitl_state.get("graph") or self._graph_holder.get("graph")

    def _resolve_hitl_checkpointer() -> Any:
        return self._hitl_state.get("checkpointer")

    @self.app.post("/api/approve/{thread_id}")
    async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
        # Use shared auth: KAZMA_SECRET *or* Account API token.
        from kazma_ui.auth import get_kazma_secret, is_authenticated

        _secret = get_kazma_secret()
        if _secret and not is_authenticated(request, _secret):
            return _JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            logger.debug("[HITL] Malformed or missing JSON body in approval request", exc_info=True)
            return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

        action = body.get("action", "deny")
        approved = action == "approve"
        # scope: once (default) | tool (session grant for this tool) | yolo
        scope = str(body.get("scope") or "once").strip().lower()
        if scope not in ("once", "tool", "yolo", "allow_tool", "session"):
            scope = "once"
        if scope == "allow_tool":
            scope = "tool"
        if scope == "session":
            scope = "yolo"

        graph_ref = _resolve_hitl_graph()
        if graph_ref is None:
            return _JSONResponse({"error": "Graph not available"}, status_code=503)

        # H-2 / S0-3: ownership for *gateway* threads only. Web chat sessions
        # live in SessionManager (not gateway session_store) and already pass
        # session_id from the browser — never 403 web users who legitimately
        # clicked Approve on their own card just because gateway has no row.
        try:
            if self.session_store is not None:
                ctx = None
                try:
                    ctx = await self.session_store.get(thread_id)
                except Exception as _e:
                    logger.debug("[HITL] Failed to fetch session context for ownership check: %s", _e)
                if ctx and isinstance(ctx, dict):
                    owner = (
                        ctx.get("sender_id")
                        or ctx.get("owner")
                        or ctx.get("session_id")
                        or ctx.get("user_id")
                    )
                    # Only enforce when this is clearly a non-web gateway owner
                    # (telegram:/discord:/slack: prefixes or numeric platform ids).
                    owner_s = str(owner or "")
                    is_gateway_owner = bool(
                        owner_s
                        and (
                            owner_s.startswith("telegram:")
                            or owner_s.startswith("discord:")
                            or owner_s.startswith("slack:")
                            or ":" in owner_s
                        )
                    )
                    if is_gateway_owner:
                        # Fail-closed: require session_id for gateway-owned threads
                        # (audit H3 — omit used to skip ownership check entirely)
                        caller_session = body.get("session_id")
                        if not caller_session:
                            logger.warning(
                                "[HITL] Web approve missing session_id for gateway thread %s owner=%s",
                                thread_id,
                                owner,
                            )
                            return _JSONResponse(
                                {
                                    "error": (
                                        "session_id required to approve gateway-owned "
                                        "HITL requests"
                                    )
                                },
                                status_code=403,
                            )
                        if str(owner) != str(caller_session):
                            logger.warning(
                                "[HITL] Web approve ownership mismatch for thread %s: owner=%s caller=%s",
                                thread_id,
                                owner,
                                caller_session,
                            )
                            return _JSONResponse(
                                {"error": "Ownership mismatch: you cannot approve another user's request"},
                                status_code=403,
                            )
        except Exception as _e:
            # Fail-closed (audit M7): never skip ownership on store errors
            logger.warning("[HITL] Ownership check failed — denying: %s", _e)
            return _JSONResponse(
                {"error": "Ownership check failed — approval denied"},
                status_code=403,
            )

        try:
            from langgraph.types import Command

            # Prefer the live checkpointed graph (same instance as SSE).
            graph_ref = _resolve_hitl_graph() or self._graph_holder.get("graph")
            if graph_ref is None:
                return _JSONResponse({"error": "Graph not available"}, status_code=503)

            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

            # Verify this thread is actually paused before resume — avoids a
            # silent no-op when the wrong graph/checkpointer is wired.
            # Also snapshot messages + pending tools for scope grants / delta text.
            pre = None
            pre_msg_count = 0
            pending_tool_name = ""
            pending_tools: list[Any] = []
            try:
                pre = await graph_ref.aget_state(config)
                has_interrupt = False
                if pre and getattr(pre, "tasks", None):
                    for task in pre.tasks or []:
                        if getattr(task, "interrupts", None):
                            has_interrupt = True
                            break
                if pre and getattr(pre, "next", None) and not has_interrupt:
                    # Pending next but no interrupt payload — still try resume.
                    has_interrupt = True
                if not has_interrupt and not (pre and getattr(pre, "next", None)):
                    logger.warning(
                        "[HITL] No pending interrupt for thread=%s — approve is a no-op",
                        thread_id,
                    )
                    return _JSONResponse(
                        {
                            "status": "expired",
                            "thread_id": thread_id,
                            "content": "",
                            "error": "No pending approval for this thread (already resumed or expired).",
                        },
                        status_code=409,
                    )
                if pre is not None:
                    vals = getattr(pre, "values", None) or {}
                    if isinstance(vals, dict):
                        pre_msgs = vals.get("messages") or []
                        pre_msg_count = len(pre_msgs) if isinstance(pre_msgs, list) else 0
                    for task in getattr(pre, "tasks", None) or []:
                        for intr in getattr(task, "interrupts", None) or []:
                            payload = getattr(intr, "value", None)
                            if isinstance(payload, dict) and payload.get("type") == "hitl_approval":
                                pending_tool_name = str(payload.get("tool") or "")
                                pending_tools = list(payload.get("tools") or [])
                                break
            except Exception:
                logger.debug("[HITL] pre-resume state probe failed", exc_info=True)

            # Apply scope grants *before* resume so subsequent danger tools in
            # later supervisor rounds skip interrupt entirely.
            actor = f"web:{(body.get('session_id') or '')[:12] or 'anon'}"
            grant_info: dict[str, Any] | None = None
            if approved and scope == "yolo":
                try:
                    from kazma_core.safety.yolo import YoloDisabledError, enable_yolo

                    grant_info = enable_yolo(thread_id, actor=actor)
                except YoloDisabledError as yde:
                    logger.warning("[HITL] YOLO scope blocked: %s", yde)
                    return _JSONResponse(
                        {
                            "error": str(yde),
                            "status": "yolo_disabled",
                        },
                        status_code=403,
                    )
                except Exception:
                    logger.exception("[HITL] failed to enable YOLO scope")
            elif approved and scope == "tool":
                try:
                    from kazma_core.safety.hitl_grants import grant_tool

                    tools_to_grant: list[str] = []
                    if pending_tools:
                        for t in pending_tools:
                            if isinstance(t, dict) and t.get("name"):
                                tools_to_grant.append(str(t["name"]))
                    elif pending_tool_name and " tools" not in pending_tool_name:
                        tools_to_grant.append(pending_tool_name)
                    # Client may also pass explicit tool name
                    explicit = body.get("tool") or body.get("grant_tool")
                    if explicit:
                        tools_to_grant.append(str(explicit))
                    tools_to_grant = list(dict.fromkeys(tools_to_grant))  # dedupe
                    grant_info = {"tools": []}
                    for tname in tools_to_grant:
                        st = grant_tool(thread_id, tname, actor=actor)
                        grant_info["tools"].append(st)
                except Exception:
                    logger.exception("[HITL] failed to apply tool grant")

            resume_value: dict[str, Any] = {
                "approved": approved,
                "reason": body.get("reason", ""),
                "scope": scope,
            }
            if isinstance(body.get("approved_ids"), list):
                resume_value["approved_ids"] = body["approved_ids"]

            from fastapi.responses import StreamingResponse
            from typing import AsyncGenerator
            from kazma_ui.sse_chat import _stream_langgraph_events, _sse_frame
            from kazma_core.safety.hitl import (
                reset_current_thread_id,
                set_current_thread_id,
            )

            async def _approval_stream_generator() -> AsyncGenerator[str, None]:
                status_msg = (
                    "Executing approved tool..."
                    if approved
                    else "Continuing after denial..."
                )
                yield _sse_frame("status", {"content": status_msg})

                # Update checkpoint metadata with HITL resolution state
                # This ensures the thread won't show up in pending approvals after this
                _hitl_state = "approved" if approved else "denied"
                _resolution_time = datetime.now(UTC).isoformat()
                # Postgres jsonb_set needs JSON text ('"approved"'), not bare approved.
                import json as _json

                _hitl_state_json = _json.dumps(_hitl_state)
                _resolution_json = _json.dumps(_resolution_time)

                try:
                    cp = _resolve_hitl_checkpointer()
                    if cp is not None:
                        conn = getattr(cp, "conn", None)
                        if conn is not None:
                            try:
                                if hasattr(conn, "execute"):
                                    # SQLite: plain strings become JSON strings
                                    await conn.execute(
                                        "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_state', ?) WHERE thread_id = ?",
                                        (_hitl_state, thread_id),
                                    )
                                    await conn.execute(
                                        "UPDATE checkpoints SET metadata = json_set(metadata, '$.hitl_resolved_at', ?) WHERE thread_id = ?",
                                        (_resolution_time, thread_id),
                                    )
                                    await conn.commit()
                                elif hasattr(conn, "connection"):
                                    # Postgres: jsonb_set requires a JSON document
                                    async with conn.connection() as pg_conn:
                                        async with pg_conn.cursor() as cur:
                                            await cur.execute(
                                                "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_state}', %s::jsonb) WHERE thread_id = %s",
                                                (_hitl_state_json, thread_id),
                                            )
                                            await cur.execute(
                                                "UPDATE checkpoints SET metadata = jsonb_set(COALESCE(metadata, '{}'::jsonb), '{hitl_resolved_at}', %s::jsonb) WHERE thread_id = %s",
                                                (_resolution_json, thread_id),
                                            )
                                            await pg_conn.commit()
                            except Exception as e:
                                logger.warning(
                                    "[HITL] Failed to update checkpoint metadata for thread=%s: %s",
                                    thread_id,
                                    e,
                                )
                except Exception as e:
                    logger.debug("[HITL] Could not update checkpoint metadata: %s", e)

                _tid_token = set_current_thread_id(thread_id)
                try:
                    async for frame in _stream_langgraph_events(
                        graph_ref,
                        Command(resume=resume_value),
                        config=config,
                    ):
                        yield frame
                finally:
                    reset_current_thread_id(_tid_token)

            return StreamingResponse(
                _approval_stream_generator(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )
        except Exception:
            logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
            return _JSONResponse({"error": "Internal error"}, status_code=500)

    @self.app.get("/api/pending-approvals")
    async def list_pending_approvals() -> _JSONResponse:
        from kazma_ui.hitl_approval import _get_pending_approvals

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        if graph is None or checkpointer is None:
            return _JSONResponse(
                {"pending": [], "count": 0, "error": "Graph/checkpointer not yet initialized"},
                status_code=503,
            )
        try:
            pending = await _get_pending_approvals(graph, checkpointer)
            return _JSONResponse({"pending": pending, "count": len(pending)})
        except Exception:
            logger.exception("[HITL] Failed to list pending approvals")
            return _JSONResponse({"pending": [], "count": 0, "error": "Internal error"}, status_code=500)

    @self.app.post("/api/pending-approvals/clear")
    @self.app.delete("/api/pending-approvals")
    async def clear_pending_approvals_route() -> _JSONResponse:
        from kazma_ui.hitl_approval import clear_pending_approvals

        graph = _resolve_hitl_graph()
        checkpointer = _resolve_hitl_checkpointer()
        if checkpointer is None:
            return _JSONResponse({"error": "Checkpointer not available"}, status_code=503)
        try:
            cleared = await clear_pending_approvals(graph, checkpointer)
            return _JSONResponse({"status": "ok", "cleared": cleared})
        except Exception:
            logger.exception("[HITL] Failed to clear pending approvals")
            return _JSONResponse({"error": "Internal error"}, status_code=500)

    @self.app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        return {
            "status": "degraded" if self._init_errors else "ok",
            "init_errors": self._init_errors,
        }

    # ── Workspace selection + file-tree scanner ────────────────────────
    try:
        from kazma_gateway.routers.workspace import create_workspace_select_router

        self.app.include_router(create_workspace_select_router())
        logger.info("[routes_direct] Workspace select/tree router mounted at /api/workspace/select, /api/workspace/tree")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspace select/tree router failed to mount: %s", _exc)

    # ── Workspaces Multi-Project Router ────────────────────────────────
    try:
        from kazma_gateway.routers.workspaces import create_workspaces_router

        self.app.include_router(create_workspaces_router())
        logger.info("[routes_direct] Workspaces router mounted at /api/workspaces")
    except Exception as _exc:
        logger.warning("[routes_direct] Workspaces router failed to mount: %s", _exc)

    # ── Live Git status ────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.git import create_git_router

        self.app.include_router(create_git_router())
        logger.info("[routes_direct] Git router mounted at /api/git/status")
    except Exception as _exc:
        logger.warning("[routes_direct] Git router failed to mount: %s", _exc)

    # ── Live GitHub integration ────────────────────────────────────────
    try:
        from kazma_gateway.routers.github import create_github_router

        self.app.include_router(create_github_router())
        logger.info("[routes_direct] GitHub router mounted at /api/github")
    except Exception as _exc:
        logger.warning("[routes_direct] GitHub router failed to mount: %s", _exc)

    # ── Bookmarks CRUD ─────────────────────────────────────────────────
    try:
        from kazma_gateway.routers.bookmarks import create_bookmarks_router

        self.app.include_router(create_bookmarks_router())
        logger.info("[routes_direct] Bookmarks router mounted at /api/bookmarks")
    except Exception as _exc:
        logger.warning("[routes_direct] Bookmarks router failed to mount: %s", _exc)

    # ── Visual Pipeline Sandbox ────────────────────────────────────────
    try:
        from kazma_gateway.routers.pipeline import create_pipeline_router

        self.app.include_router(create_pipeline_router())
        logger.info("[routes_direct] Visual pipeline router mounted at /api/pipelines")
    except Exception as _exc:
        logger.warning("[routes_direct] Visual pipeline router failed to mount: %s", _exc)

    # Config Migration UI (extracted to routes_migrate)
    try:
        from kazma_ui.routes_migrate import register_migrate_routes

        register_migrate_routes(self.app)
    except Exception as _exc:
        logger.warning("[routes_direct] Config migration endpoints failed to mount: %s", _exc)

    # Chaos Testing UI (extracted to routes_chaos; env-gated)
    try:
        from kazma_ui.routes_chaos import register_chaos_routes

        register_chaos_routes(self.app)
    except Exception as _exc:
        logger.warning("[routes_direct] Chaos testing endpoints failed to mount: %s", _exc)
