"""Direct route registrations for the Kazma UI web application.

Extracted from the god-module app.py to keep route registration highly modular and maintainable.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse as _JSONResponse

logger = logging.getLogger(__name__)


def register_direct_routes(self: Any) -> None:
    """Register direct FastAPI route handlers onto self.app."""

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

        paths = {
            "kazma_home": str(_os_sys.path.expanduser("~/.kazma")),
            "config_db": str(_os_sys.path.expanduser("~/.kazma/config.db")),
            "config_yaml": next(iter(_glob_sys.glob(_os_sys.path.expanduser("~/.kazma/*.yaml"))), ""),
            "pending_evolution": str(_os_sys.path.expanduser("~/.kazma/pending_evolution.json")),
            "knowledge_graph": str(_os_sys.path.expanduser("kazma-data/knowledge_graph.json")),
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
        # Flush tool registry
        try:
            from kazma_core.tools.registry import ToolRegistry

            ToolRegistry._instance = None
        except Exception as exc:
            logger.debug("Tool registry cache flush failed: %s", exc)
        return {"status": "flushed", "config_paths": paths}

    @self.app.get("/api/system/config-paths")
    async def _system_config_paths():
        import os as _osp

        home = _osp.path.expanduser("~/.kazma")
        return {
            "kazma_home": home,
            "config_db": _osp.path.join(home, "config.db") if _osp.path.exists(_osp.path.join(home, "config.db")) else "NOT FOUND",
            "swarm_registry": _osp.path.expanduser("swarm_registry.json") if _osp.path.exists(_osp.path.expanduser("swarm_registry.json")) else "NOT FOUND",
            "pending_evolution": _osp.path.join(home, "pending_evolution.json") if _osp.path.exists(_osp.path.join(home, "pending_evolution.json")) else "NOT FOUND",
            "knowledge_graph": _osp.path.expanduser("kazma-data/knowledge_graph.json") if _osp.path.exists(_osp.path.expanduser("kazma-data/knowledge_graph.json")) else "NOT FOUND",
            "snapshots_db": _osp.path.expanduser("kazma-data/snapshots.db") if _osp.path.exists(_osp.path.expanduser("kazma-data/snapshots.db")) else "NOT FOUND",
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

    import kazma_core.swarm.memory.graph as _kg_mod

    @self.app.get("/api/memory/graph")
    async def _memory_graph():
        kg = _kg_mod.KnowledgeGraph()
        return kg.to_json()

    @self.app.get("/api/memory/graph/stats")
    async def _memory_graph_stats():
        kg = _kg_mod.KnowledgeGraph()
        return kg.stats()

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
        from kazma_core.config_store import get_config_store
        from kazma_core.system.maintenance import get_memory_paths
        import sqlite3
        
        store = get_config_store()
        status = store.get("system.memory.status") or "ACTIVE"
        
        fts5_path, vector_path, _ = get_memory_paths()
        
        fts5_size = fts5_path.stat().st_size if fts5_path.exists() else 0
        fts5_count = 0
        if fts5_path.exists():
            try:
                conn = sqlite3.connect(fts5_path)
                cursor = conn.cursor()
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) FROM memory_fts")
                    fts5_count = cursor.fetchone()[0]
                conn.close()
            except Exception as _e:
                logger.debug("fts5 count failed: %s", _e)
                
        vector_size = 0
        if vector_path.exists() and vector_path.is_dir():
            vector_size = sum(f.stat().st_size for f in vector_path.glob("**/*") if f.is_file())
            
        vector_count = 0
        from kazma_core.agent.tool_registry import get_vector_memory
        vm = get_vector_memory()
        if vm and not getattr(vm, "degraded", False):
            try:
                vector_count = vm.count
            except Exception as _e:
                logger.debug("vector count failed: %s", _e)
                
        return {
            "status": status,
            "fts5_size": fts5_size,
            "fts5_count": fts5_count,
            "vector_size": vector_size,
            "vector_count": vector_count
        }

    @self.app.post("/api/system/install")
    async def _post_system_install(req: dict = None):
        req = req or {}
        package_name = req.get("package_name", "sentence-transformers")
        from kazma_core.system import asynchronous_install_package
        await asynchronous_install_package(package_name)
        return {"status": "started", "package": package_name}

    @self.app.get("/api/alerts/recent")
    async def _get_recent_alerts():
        from kazma_core.observability.alerts import AlertDispatcher
        return [
            a.to_dict() if hasattr(a, "to_dict") else a
            for a in AlertDispatcher.get_recent_alerts()
        ]

    @self.app.get("/api/system/memory/backups")
    async def _list_memory_backups():
        from kazma_core.system.maintenance import list_memory_backups
        try:
            backups = list_memory_backups()
            return {"backups": backups}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/backup")
    async def _create_memory_backup():
        from kazma_core.system.maintenance import create_memory_backup
        try:
            manifest = create_memory_backup()
            return {"status": "success", "manifest": manifest}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/restore")
    async def _restore_memory_backup(req: dict):
        backup_name = req.get("backup_name", "")
        if not backup_name:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="backup_name is required")
        from kazma_core.system.maintenance import restore_memory_backup
        try:
            res = await restore_memory_backup(backup_name)
            return res
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.post("/api/system/memory/maintenance")
    async def _run_memory_maintenance():
        from kazma_core.system.maintenance import run_memory_maintenance
        try:
            res = run_memory_maintenance()
            return {"status": "success", "details": res}
        except Exception as e:
            from fastapi import HTTPException
            raise HTTPException(status_code=500, detail=str(e))

    @self.app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        from kazma_ui.auth import get_kazma_secret, SECRET_COOKIE
        expected = get_kazma_secret()
        if expected:
            provided = websocket.headers.get("x-kazma-secret", "")
            if not provided:
                provided = websocket.cookies.get(SECRET_COOKIE, "")
            import hmac as _hmac

            if not provided or not _hmac.compare_digest(provided, expected):
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

    @self.app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        from kazma_ui.auth import get_kazma_secret, SECRET_COOKIE
        expected2 = get_kazma_secret()
        if expected2:
            provided2 = websocket.headers.get("x-kazma-secret", "")
            if not provided2:
                provided2 = websocket.cookies.get(SECRET_COOKIE, "")
            import hmac as _hmac2

            if not provided2 or not _hmac2.compare_digest(provided2, expected2):
                await websocket.close(code=4003, reason="Unauthorized")
                return
        from kazma_ui.chat import chat_websocket_handler

        await chat_websocket_handler(websocket, self.agent)

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

            tg_adapter = TelegramAdapter(token=telegram_token)
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

    _KAZMA_SECRET = os.environ.get("KAZMA_SECRET", "")

    def _resolve_hitl_graph() -> Any:
        return self._hitl_state.get("graph") or self._graph_holder.get("graph")

    def _resolve_hitl_checkpointer() -> Any:
        return self._hitl_state.get("checkpointer")

    @self.app.post("/api/approve/{thread_id}")
    async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
        if _KAZMA_SECRET:
            import secrets as _secrets
            from kazma_ui.auth import SECRET_COOKIE

            provided = request.headers.get("X-Kazma-Secret", "")
            if not provided:
                provided = request.cookies.get(SECRET_COOKIE, "")

            if not provided or not _secrets.compare_digest(provided, _KAZMA_SECRET):
                return _JSONResponse({"error": "Unauthorized"}, status_code=401)

        try:
            body = await request.json()
        except Exception:
            logger.debug("[HITL] Malformed or missing JSON body in approval request", exc_info=True)
            return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

        action = body.get("action", "deny")
        approved = action == "approve"

        graph_ref = _resolve_hitl_graph()
        if graph_ref is None:
            return _JSONResponse({"error": "Graph not available"}, status_code=503)

        # H-2 fix attempt: basic ownership check using session_store context (similar to gateway).
        try:
            if self.session_store is not None:
                ctx = None
                try:
                    ctx = self.session_store.get(thread_id)
                except Exception as _e:
                    logger.debug("[HITL] Failed to fetch session context for ownership check: %s", _e)
                if ctx and isinstance(ctx, dict):
                    owner = ctx.get("sender_id") or ctx.get("owner") or ctx.get("session_id")
                    caller_session = body.get("session_id")
                    if owner and caller_session and str(owner) != str(caller_session):
                        logger.warning("[HITL] Web approve ownership mismatch for thread %s", thread_id)
        except Exception as _e:
            logger.debug("[HITL] Ownership check failed: %s", _e)

        try:
            from langgraph.types import Command

            config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
            resume_value = {"approved": approved, "reason": body.get("reason", "")}
            result = await graph_ref.ainvoke(
                Command(resume=resume_value),
                config=config,
            )
            return _JSONResponse({
                "status": "approved" if approved else "denied",
                "thread_id": thread_id,
            }, status_code=202)
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
