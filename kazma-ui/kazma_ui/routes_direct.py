"""Direct route registrations for the Kazma UI web application.

Extracted from the god-module app.py to keep route registration highly modular and maintainable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from fastapi import Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse as _JSONResponse

logger = logging.getLogger(__name__)


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
        import os as _os
        from kazma_core.config_store import get_config_store
        from kazma_core.system.maintenance import get_memory_paths
        import sqlite3

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
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='memory_fts'")
                if cursor.fetchone():
                    cursor.execute("SELECT COUNT(*) FROM memory_fts")
                    fts5_count = cursor.fetchone()[0]
                conn.close()
            except Exception as _e:
                logger.debug("fts5 count failed: %s", _e)
                if 'conn' in dir():
                    try:
                        conn.close()
                    except Exception:
                        pass
                
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
        from kazma_core.system.maintenance import list_memory_backups
        try:
            backups = list_memory_backups()
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
            SECRET_COOKIE,
            SECRET_HEADER,
            get_kazma_secret,
            verify_secret,
            _is_loopback_client,
        )

        expected = get_kazma_secret()
        if not expected:
            return {"auth_enabled": False, "authenticated": True, "mode": "open"}
        provided = request.headers.get(SECRET_HEADER, "") or request.cookies.get(SECRET_COOKIE, "")
        ok = bool(provided and verify_secret(provided, expected))
        return {
            "auth_enabled": True,
            "authenticated": ok,
            "loopback": _is_loopback_client(request),
            "mode": "secret",
        }

    @self.app.post("/api/auth/login")
    async def _auth_login(request: Request) -> Response:
        """Exchange KAZMA_SECRET for an HttpOnly session cookie."""
        from kazma_ui.auth import (
            SECRET_COOKIE,
            get_kazma_secret,
            verify_secret,
            _is_https,
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
        secret = str(body.get("secret") or body.get("password") or "")
        if not verify_secret(secret, expected):
            return _JSONResponse(
                {"detail": "Invalid secret"},
                status_code=401,
            )
        resp = _JSONResponse({"status": "ok", "authenticated": True})
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

    @self.app.post("/api/auth/logout")
    async def _auth_logout() -> Response:
        """Clear the auth session cookie."""
        from kazma_ui.auth import SECRET_COOKIE

        resp = _JSONResponse({"status": "ok", "authenticated": False})
        resp.delete_cookie(SECRET_COOKIE, path="/")
        return resp

    @self.app.get("/api/system/packages")
    async def _get_packages():
        """List installed Python packages with metadata and extras status."""
        import importlib.metadata as ilm

        # ── Define the extras groups and their member packages ──
        EXTRA_GROUPS = {
            "rag": {
                "description": "Vector memory (ChromaDB) + local embeddings (sentence-transformers). Without this, memory search falls back to FTS5 text-only search.",
                "packages": ["chromadb", "sentence-transformers"],
                "install_cmd": 'uv pip install -e ".[rag]"   # additive — won\'t remove other extras',
            },
            "dev": {
                "description": "Development tools — testing (pytest), linting (ruff), type checking (mypy), load testing (locust).",
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "ruff", "mypy", "locust"],
                "install_cmd": 'uv pip install -e ".[dev]"   # additive — won\'t remove other extras',
            },
            "test": {
                "description": "Test-specific dependencies (lighter than dev). Includes pytest + fakeredis for unit/integration tests.",
                "packages": ["pytest", "pytest-asyncio", "pytest-cov", "pytest-mock", "fakeredis"],
                "install_cmd": 'uv pip install -e ".[test]"   # additive — won\'t remove other extras',
            },
            "tui": {
                "description": "Terminal dashboard UI (Textual) with RTL/bidirectional text rendering (python-bidi).",
                "packages": ["textual", "python-bidi"],
                "install_cmd": 'uv pip install -e ".[tui]"   # additive — won\'t remove other extras',
            },
            "observability": {
                "description": "Prometheus metrics export for monitoring Kazma in production (Grafana dashboards, alerting).",
                "packages": ["prometheus-client"],
                "install_cmd": 'uv pip install -e ".[observability]"   # additive — won\'t remove other extras',
            },
            "web": {
                "description": "Browser automation via Playwright. Used by the web crawler skill to render JavaScript-heavy pages.",
                "packages": ["playwright"],
                "install_cmd": 'uv pip install -e ".[web]"   # additive — won\'t remove other extras',
            },
        }

        # ── Core dependencies (always installed) ──
        CORE_PACKAGES = [
            "fastapi", "uvicorn", "langgraph", "langgraph-checkpoint-sqlite",
            "aiosqlite", "langfuse", "pyyaml", "httpx", "cryptography",
            "PyJWT", "jinja2", "python-multipart", "textual", "psutil",
            "aiogram", "websockets", "duckduckgo-search", "trafilatura",
            "markdown", "tenacity", "networkx", "click", "rich",
            "google-cloud-aiplatform", "python-dotenv",
        ]

        CORE_DESCRIPTIONS = {
            "fastapi": "Web framework powering the Kazma dashboard + REST API",
            "uvicorn": "ASGI server that runs the FastAPI app",
            "langgraph": "LangGraph supervisor brain — the ReAct loop, checkpointing, interrupt()",
            "langgraph-checkpoint-sqlite": "SQLite-backed checkpoint persistence for LangGraph",
            "aiosqlite": "Async SQLite driver used by all Kazma data stores",
            "langfuse": "Observability/tracing platform for LLM calls",
            "pyyaml": "YAML parser for kazma.yaml config + skill manifests",
            "httpx": "HTTP client for LLM API calls + web tools",
            "cryptography": "AES-256-GCM encryption for the secret vault",
            "PyJWT": "JWT token generation for GitHub App authentication",
            "jinja2": "HTML template engine for the web UI",
            "python-multipart": "File upload handling for FastAPI",
            "textual": "Terminal UI framework (also a core dep for the TUI)",
            "psutil": "System resource monitoring (CPU, RAM, disk) for telemetry",
            "aiogram": "Telegram Bot API framework",
            "websockets": "WebSocket support for real-time chat + gateway",
            "duckduckgo-search": "Privacy-focused web search (no API key needed)",
            "trafilatura": "Web content extraction (clean text from URLs)",
            "markdown": "Markdown rendering for chat messages",
            "tenacity": "Retry logic with exponential backoff for LLM calls",
            "networkx": "Graph algorithms for swarm DAG/topology validation",
            "click": "CLI framework for the `kazma` command",
            "rich": "Beautiful terminal output (colors, tables, progress bars)",
            "google-cloud-aiplatform": "Google Vertex AI provider integration",
            "python-dotenv": ".env file loading for local development",
        }

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

        # Extras
        extras_list = []
        for group_name, group_data in EXTRA_GROUPS.items():
            group_installed = True
            pkg_list = []
            for name in group_data["packages"]:
                info = _pkg_info(name)
                info["group"] = group_name
                pkg_list.append(info)
                if not info["installed"]:
                    group_installed = False
            extras_list.append({
                "name": group_name,
                "description": group_data["description"],
                "install_cmd": group_data["install_cmd"],
                "installed": group_installed,
                "packages": pkg_list,
            })

        # Count total installed (from distributions, not just our deps)
        total_installed = len(all_dists)

        return {
            "core": core_list,
            "extras": extras_list,
            "total_installed": total_installed,
            "python_version": __import__("sys").version.split()[0],
        }

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

    def _resolve_hitl_graph() -> Any:
        return self._hitl_state.get("graph") or self._graph_holder.get("graph")

    def _resolve_hitl_checkpointer() -> Any:
        return self._hitl_state.get("checkpointer")

    @self.app.post("/api/approve/{thread_id}")
    async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
        # Use dynamic secret resolution (checks env AND config_store)
        from kazma_ui.auth import get_kazma_secret

        _secret = get_kazma_secret()
        if _secret:
            import secrets as _secrets
            from kazma_ui.auth import SECRET_COOKIE

            provided = request.headers.get("X-Kazma-Secret", "")
            if not provided:
                provided = request.cookies.get(SECRET_COOKIE, "")

            if not provided or not _secrets.compare_digest(provided, _secret):
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

        # H-2 / S0-3: enforce ownership — require session_id when owner is known
        try:
            if self.session_store is not None:
                ctx = None
                try:
                    ctx = await self.session_store.get(thread_id)
                except Exception as _e:
                    logger.debug("[HITL] Failed to fetch session context for ownership check: %s", _e)
                if ctx and isinstance(ctx, dict):
                    # Resolve the owning identity across platforms:
                    #   - Telegram: sender_id ("telegram:<chat_id>")
                    #   - Discord/Slack: user_id
                    #   - Web/SSE: session_id
                    owner = (
                        ctx.get("sender_id")
                        or ctx.get("owner")
                        or ctx.get("session_id")
                        or ctx.get("user_id")
                    )
                    if owner:
                        caller_session = body.get("session_id")
                        if not caller_session:
                            logger.warning(
                                "[HITL] Web approve missing session_id for owned thread %s",
                                thread_id,
                            )
                            return _JSONResponse(
                                {"error": "session_id required to approve this request"},
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
