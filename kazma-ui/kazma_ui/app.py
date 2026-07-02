"""Kazma WebUI — FastAPI app factory.

Creates and configures the FastAPI application with all routers,
WebSocket endpoints, static files, and template engine.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

logger = logging.getLogger(__name__)

# Package paths
_PACKAGE_DIR = Path(__file__).resolve().parent
_TEMPLATES_DIR = _PACKAGE_DIR / "templates"
_STATIC_DIR = _PACKAGE_DIR / "static"


def create_app(config_path: str | None = None) -> FastAPI:
    """Create and configure the Kazma FastAPI application.

    Args:
        config_path: Optional path to kazma.yaml override.

    Returns:
        Configured FastAPI instance ready to run.
    """
    from kazma_core.agent import KazmaAgent, load_config
    from kazma_core.config_store import ConfigStore
    from kazma_core.model_registry import initialize_model_registry

    # Load config and initialize registry BEFORE creating agent,
    # because KazmaAgent.__init__ calls get_model_registry().
    config = load_config(config_path)
    config_store = ConfigStore()
    registry = initialize_model_registry(config_store)
    agent = KazmaAgent(config)
    startup_provider_profile = registry.get_active_profile()

    # ── Configure the agent workspace (config-relative, not drive root) ──
    # file_write / file_read default to Path.cwd() when no workspace is
    # configured, which can create a C:\workspace folder at the drive root
    # on Windows. Pin the workspace to kazma-data/workspace (under the
    # current working directory) so all file operations stay scoped.
    # KAZMA_WORKSPACE env var overrides this for custom deployments.
    try:
        from kazma_core.tools.file_write import configure_workspace

        _workspace_env = os.environ.get("KAZMA_WORKSPACE", "").strip()
        _workspace_path = _workspace_env or "kazma-data/workspace"
        configure_workspace(workspace=_workspace_path)
        logger.info("[Workspace] Configured to %s", _workspace_path)
    except Exception as e:
        logger.warning("[Workspace] Failed to configure: %s", e)

    # Create FastAPI app
    app = FastAPI(
        title="Kazma",
        version=config.version,
        description="Autonomous AI Agent Framework — Arabic RTL Dashboard",
    )

    # ── Auth Middleware: gate sensitive API endpoints behind KAZMA_SECRET ──
    # When KAZMA_SECRET env var is set, all /api/settings, /api/swarm,
    # /api/mcp, /api/skills, /api/models, /api/ollama endpoints require the
    # X-Kazma-Secret header (timing-safe comparison).  Read-only endpoints
    # and page routes remain open.  When the secret is unset everything is
    # open (backward compatible).
    from kazma_ui.auth import create_auth_middleware

    app.middleware("http")(create_auth_middleware())
    # ── Debug: dump full ModelRegistry state ────────────────────────
    @app.get("/api/system/debug/registry")
    async def _debug_registry():
        """Dump the exact model/provider state the backend sees."""
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

    # ── System: flush caches + show config paths ──────────────────
    import os as _os_sys, glob as _glob_sys
    @app.get("/api/system/flush")
    async def _system_flush():
        """Flush in-memory caches and return config file paths."""
        paths = {
            "kazma_home": str(_os_sys.path.expanduser("~/.kazma")),
            "config_db": str(_os_sys.path.expanduser("~/.kazma/config.db")),
            "config_yaml": next(iter(_glob_sys.glob(_os_sys.path.expanduser("~/.kazma/*.yaml"))), ""),
            "pending_evolution": str(_os_sys.path.expanduser("~/.kazma/pending_evolution.json")),
            "knowledge_graph": str(_os_sys.path.expanduser("kazma-data/knowledge_graph.json")),
        }
        # Flush model registry cache
        try:
            from kazma_core.model_registry import _registry
            import kazma_core.model_registry as _mr
            _mr._registry = None
        except Exception:
            pass
        # Flush WorkerRegistry cache
        try:
            from kazma_core.swarm.registry import WorkerRegistry
            WorkerRegistry._instance = None
        except Exception:
            pass
        # Flush tool registry
        try:
            from kazma_core.tools.registry import ToolRegistry
            ToolRegistry._instance = None
        except Exception:
            pass
        return {"status": "flushed", "config_paths": paths}

    @app.get("/api/system/config-paths")
    async def _system_config_paths():
        """Return the file paths of all active configuration sources."""
        import os as _osp, glob as _g
        home = _osp.path.expanduser("~/.kazma")
        return {
            "kazma_home": home,
            "config_db": _osp.path.join(home, "config.db") if _osp.path.exists(_osp.path.join(home, "config.db")) else "NOT FOUND",
            "swarm_registry": _osp.path.expanduser("swarm_registry.json") if _osp.path.exists(_osp.path.expanduser("swarm_registry.json")) else "NOT FOUND",
            "pending_evolution": _osp.path.join(home, "pending_evolution.json") if _osp.path.exists(_osp.path.join(home, "pending_evolution.json")) else "NOT FOUND",
            "knowledge_graph": _osp.path.expanduser("kazma-data/knowledge_graph.json") if _osp.path.exists(_osp.path.expanduser("kazma-data/knowledge_graph.json")) else "NOT FOUND",
            "snapshots_db": _osp.path.expanduser("kazma-data/snapshots.db") if _osp.path.exists(_osp.path.expanduser("kazma-data/snapshots.db")) else "NOT FOUND",
        }

    # ── MCP server management ───────────────────────────────────────
    @app.delete("/api/mcp/servers/{server_name}")
    async def _delete_mcp_server(server_name: str):
        """Delete an MCP server configuration."""
        try:
            from kazma_core.mcp.manager import MCPManager
            manager = MCPManager()
            manager.remove_server(server_name)
            return {"status": "ok", "message": f"Server '{server_name}' deleted"}
        except Exception as exc:
            return {"status": "error", "message": str(exc)}

    # ── Typing indicator signal (#3) ─────────────────────────────────
    @app.get("/api/telemetry/typing")
    async def _typing_signal():
        """Lightweight signal that a worker is processing — used for UI typing indicators."""
        return {"status": "processing", "timestamp": __import__("time").time()}

    @app.post("/api/telemetry/typing/stream_start")
    async def _stream_start(req: dict):
        """Notify gateways that a streaming response has begun."""
        worker_name = req.get("worker_name", "unknown")
        task_id = req.get("task_id", "")
        logger.info("[Stream] Typing started — worker=%s task=%s", worker_name, task_id)
        return {"status": "stream_started", "worker_name": worker_name, "task_id": task_id}

    # ── Global error boundary (#16) ──────────────────────────────────
    from kazma_core.swarm.middleware import GracefulErrorFallback as _gef
    @app.exception_handler(Exception)
    async def _global_error_handler(request: Request, exc: Exception):
        """Catch-all handler — never let a broken skill crash the pipeline."""
        logger.error("[GlobalError] %s: %s", type(exc).__name__, exc)
        return JSONResponse(
            status_code=500,
            content=_gef.to_json_error(exc),
        )

    # ── Swarm Brain — Knowledge Graph (#18) ───────────────────────────
    import kazma_core.swarm.memory.graph as _kg_mod
    @app.get("/api/memory/graph")
    async def _memory_graph():
        kg = _kg_mod.KnowledgeGraph()
        return kg.to_json()

    @app.get("/api/memory/graph/stats")
    async def _memory_graph_stats():
        kg = _kg_mod.KnowledgeGraph()
        return kg.stats()

    # ── Time Travel — Session Replay (#19) ────────────────────────────
    import kazma_core.time_travel as _tt_mod
    @app.get("/api/session/history")
    async def _session_history(thread_id: str = "", limit: int = 20):
        store = _tt_mod.SnapshotStore()
        if thread_id:
            records = store.list_for_thread(thread_id)[:limit]
        else:
            records = []
        return {"sessions": [r.to_dict() for r in records]}

    @app.post("/api/session/replay")
    async def _session_replay(req: dict):
        thread_id = req.get("thread_id", "")
        iteration = req.get("iteration", 0)
        if not thread_id:
            from fastapi import HTTPException as _httpx
            raise _httpx(status_code=400, detail="thread_id required")
        engine = _tt_mod.ReplayEngine()
        return await engine.replay_from(thread_id, iteration)

    logger.info("[Auth] KAZMA_SECRET middleware registered")

    # ── CORS Middleware ──────────────────────────────────────────────────
    # Restrictive by default (localhost variants only). Override via the
    # KAZMA_CORS_ORIGINS env var (comma-separated list of origins).
    from fastapi.middleware.cors import CORSMiddleware

    _default_cors_origins = [
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]
    _cors_env = os.environ.get("KAZMA_CORS_ORIGINS", "").strip()
    if _cors_env:
        _cors_origins = [o.strip() for o in _cors_env.split(",") if o.strip()]
    else:
        _cors_origins = _default_cors_origins

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
        allow_headers=["*"],
    )
    logger.info("[CORS] allow_origins=%s", _cors_origins)

    # Mount static files
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Setup Jinja2 templates
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Global template context for language/direction
    _lang = agent.config.language if hasattr(agent.config, "language") else "en"
    _dir = "rtl" if getattr(agent.config, "rtl", False) else "ltr"
    templates.env.globals["lang"] = _lang
    templates.env.globals["dir"] = _dir
    # i18n: expose ``t()`` to all Jinja2 templates so ``{{ t('nav.chat') }}``
    # renders the string in the configured language.
    from kazma_ui.i18n import make_translator

    templates.env.globals["t"] = make_translator(_lang)
    # Expose KAZMA_SECRET to templates so the HITL approval frontend can
    # send it as the X-Kazma-Secret header on /api/approve requests.
    templates.env.globals["kazma_secret"] = os.environ.get("KAZMA_SECRET", "")

    # ── i18n Middleware: read kazma-lang cookie per-request ────────────
    # The cookie is set by the language toggle button (JS) and overrides
    # the startup language from kazma.yaml.  The middleware mutates the
    # shared Jinja2 env globals before each request so that server-side
    # rendering reflects the user's language choice.
    from kazma_ui.i18n import make_translator as _make_translator

    _startup_lang = _lang  # default language from kazma.yaml

    @app.middleware("http")
    async def language_middleware(request: Request, call_next):
        """Set i18n globals (t, lang, dir) per-request based on kazma-lang cookie."""
        cookie_lang = request.cookies.get("kazma-lang")
        if cookie_lang in ("ar", "en"):
            req_lang = cookie_lang
        else:
            req_lang = _startup_lang
        req_dir = "rtl" if req_lang == "ar" else "ltr"
        templates.env.globals["t"] = _make_translator(req_lang)
        templates.env.globals["lang"] = req_lang
        templates.env.globals["dir"] = req_dir
        return await call_next(request)

    # Create routers
    from kazma_ui.agents import create_agents_router
    from kazma_ui.chat import chat_websocket_handler, create_chat_router
    from kazma_ui.mcp_ui import create_mcp_router
    from kazma_ui.providers import create_providers_router
    from kazma_ui.settings import create_settings_router
    from kazma_ui.skills_ui import create_skills_router

    chat_router = create_chat_router(agent, templates)
    settings_router = create_settings_router(agent, config_store, templates)
    skills_router = create_skills_router(agent, templates)
    mcp_router = create_mcp_router(agent, templates)
    agents_router = create_agents_router(agent, templates)
    providers_router = create_providers_router(config_store)

    # Mount routers
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(agents_router)
    app.include_router(providers_router)
    logger.info("Providers & Connectors router mounted at /api/providers, /api/connectors, /api/models/profiles")

    # ── SSE Chat Router (LangGraph astream_events → HTMX/Alpine) ──
    _init_errors: list[dict[str, str]] = []
    _checkpointer = None
    try:
        # Use the agent's facade method to get the streaming graph, so app.py
        # does not reach into private graph-builder internals. This builds
        # (and caches) a supervisor graph configured for SSE streaming.
        sse_graph = agent.get_streaming_graph()

        from kazma_ui.sse_chat import create_sse_chat_router

        sse_router = create_sse_chat_router(
            graph=sse_graph,
            checkpointer=None,
            system_prompt=agent.system_prompt,
            cost_breaker=agent.cost_breaker,
            authority=agent.authority,
            tracer=agent.tracer,
            provider_profile=startup_provider_profile,
            llm_provider=agent.llm,
            registry=registry,
        )
        app.include_router(sse_router)
        logger.info("SSE chat router mounted at /api/chat/stream")
    except Exception as e:
        logger.warning("SSE chat router failed to initialize: %s", e)
        _init_errors.append({"subsystem": "sse_chat", "error": str(e)})

    # ── Telemetry SSE Route (real hardware metrics) ───────────────
    try:
        from kazma_core.telemetry import HardwareMonitor

        from kazma_ui.telemetry_route import create_telemetry_router

        hw_monitor = HardwareMonitor()
        telemetry_router = create_telemetry_router(monitor=hw_monitor)
        app.include_router(telemetry_router)
        logger.info("Telemetry SSE router mounted at /api/telemetry/stream")
    except Exception as e:
        logger.warning("Telemetry router failed to initialize: %s", e)
        _init_errors.append({"subsystem": "telemetry", "error": str(e)})

    # Dashboard (legacy)
    from kazma_ui.dashboard import router as dashboard_router
    from kazma_ui.dashboard import set_templates as set_dashboard_templates

    # Reuse the app's shared templates instance so the dashboard renders
    # in the correct per-request language (middleware-driven i18n globals).
    set_dashboard_templates(templates)

    app.include_router(dashboard_router)

    # Dashboard WebSocket for real-time trace updates
    @app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
        # Authenticate WebSocket — check X-Kazma-Secret header or query param
        import os as _os
        expected = _os.environ.get("KAZMA_SECRET", "")
        if expected:
            provided = (
                websocket.headers.get("x-kazma-secret", "")
                or websocket.query_params.get("secret", "")
            )
            import hmac as _hmac
            if not _hmac.compare_digest(provided, expected):
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
            # Hold connection until client disconnects or shutdown
            while not is_shutting_down():
                try:
                    await asyncio.wait_for(websocket.receive_text(), timeout=2.0)
                except TimeoutError:
                    continue
                except Exception:
                    break
        except Exception:
            pass
        finally:
            store.unregister_ws(websocket)

    # WebSocket endpoint for chat
    @app.websocket("/ws/chat")
    async def ws_chat(websocket: WebSocket) -> None:
        # Authenticate WebSocket — check X-Kazma-Secret header or query param
        import os as _os2
        expected2 = _os2.environ.get("KAZMA_SECRET", "")
        if expected2:
            provided2 = (
                websocket.headers.get("x-kazma-secret", "")
                or websocket.query_params.get("secret", "")
            )
            import hmac as _hmac2
            if not _hmac2.compare_digest(provided2, expected2):
                await websocket.close(code=4003, reason="Unauthorized")
                return
        await chat_websocket_handler(websocket, agent)

    # ── Root — Unified Master Workspace ──
    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        """Serve the dashboard as default page."""
        return templates.TemplateResponse(
            request,
            "dashboard.html",
            {
                "config": agent.config,
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

    # ── Legacy routes -> redirect to / ──
    @app.get("/chat", response_class=HTMLResponse)
    async def chat_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)

    @app.get("/workspace", response_class=HTMLResponse)
    async def workspace_page(request: Request) -> HTMLResponse:
        """Serve the workspace page (file browser, git status, terminal)."""
        return templates.TemplateResponse(
            request,
            "workspace.html",
            {
                "config": agent.config,
                "active_page": "workspace",
            },
        )

    # ── Models & Ollama Management Router ────────────────────────
    from kazma_ui.models_route import create_models_router

    models_router = create_models_router(config_store=config_store)
    app.include_router(models_router)
    logger.info("Models router mounted at /api/models, /api/ollama/*")

    # ── Workspace File Browser API ───────────────────────────────
    from kazma_ui.workspace_api import create_workspace_router

    workspace_router = create_workspace_router()
    app.include_router(workspace_router)
    logger.info("Workspace API router mounted at /api/workspace/*")

    # ── Sub-Agent Manager ─────────────────────────────────────
    try:
        from kazma_core.agent.sub_agent import SubAgentManager, set_sub_agent_manager

        sub_agent_mgr = SubAgentManager(
            graph_builder=lambda **kwargs: agent.get_streaming_graph(),
            max_concurrent=3,
        )
        set_sub_agent_manager(sub_agent_mgr)
        logger.info("[SubAgent] Manager initialized (max_concurrent=3)")
    except Exception as e:
        logger.warning("[SubAgent] Manager not available: %s", e)

    # ── Swarm Panel ─────────────────────────────────────────────
    from kazma_ui.swarm_panel import create_swarm_router

    _swarm_mgr = None
    try:
        from kazma_core.swarm import (
            SwarmConfig,
            SwarmManager,
            TaskStore,
            set_swarm_engine,
        )

        swarm_task_store = TaskStore()
        swarm_cfg_path = config_path or "kazma.yaml"
        swarm_cfg = SwarmConfig.from_yaml(swarm_cfg_path)
        if swarm_cfg is not None and swarm_cfg.enabled:
            _swarm_mgr = SwarmManager(swarm_cfg, task_store=swarm_task_store)
            logger.info(
                "[Swarm] SwarmManager initialized from %s — %d worker(s)",
                swarm_cfg_path,
                len(_swarm_mgr.worker_names),
            )
        else:
            _swarm_mgr = SwarmManager(
                SwarmConfig(enabled=True, workers=[]),
                task_store=swarm_task_store,
            )
            logger.info("[Swarm] SwarmManager initialized (empty — UI-driven mode)")
        set_swarm_engine(_swarm_mgr.engine)
        try:
            _swarm_mgr.engine.restore_paused_tasks()
            logger.info("[Swarm] Restored paused tasks from TaskStore")
        except Exception as e:
            logger.warning("[Swarm] Failed to restore paused tasks: %s", e)
    except Exception as e:
        logger.warning("[Swarm] SwarmManager not available: %s", e)
        _swarm_mgr = None

    swarm_router = create_swarm_router(
        templates,
        swarm_manager=_swarm_mgr,
        config_store=config_store,
    )
    app.include_router(swarm_router)
    logger.info("[Swarm] Swarm Panel mounted at /api/swarm/*, /swarm")

    # ── Gateway (Omnichannel Message Bus) ────────────────────────────
    _gateway: Any = None  # module-level ref for shutdown handler

    try:
        from kazma_gateway import GatewayManager
        from kazma_gateway.adapters.telegram import TelegramAdapter
        from kazma_gateway.agent_handler import create_graph_handler
        from kazma_gateway.stores import SQLiteSessionStore

        gateway = GatewayManager(max_queue_size=100)

        # Resolve Telegram token from config store, YAML config, or environment
        # (config_store is SQLite-backed, updated via Settings page saves)
        telegram_token = config_store.get("connectors.telegram.token", "") or \
                         config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        if not telegram_token:
            telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        tg_adapter: TelegramAdapter | None = None
        if telegram_token:
            tg_adapter = TelegramAdapter(token=telegram_token)
            # Set allowed users from config store
            allowed = config_store.get("connectors.telegram.allowed_users", "")
            if allowed:
                try:
                    allowed_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]
                    tg_adapter.set_allowed_users(allowed_ids)
                    logger.info("[Gateway] Telegram allowed users: %d IDs", len(allowed_ids))
                except ValueError:
                    logger.warning("[Gateway] Invalid allowed_users format: %s", allowed)
            gateway.add_adapter(tg_adapter)
            logger.info("[Gateway] Telegram adapter registered (polling mode)")

            # Mount webhook ingress for testing / optional push mode
            webhook_router = tg_adapter.create_webhook_router()
            app.include_router(webhook_router, prefix="/api/webhooks/telegram")
            logger.info("[Gateway] Webhook ingress mounted at /api/webhooks/telegram")
        else:
            logger.info("[Gateway] No Telegram token — Telegram adapter skipped")

        # Discord adapter (from config store → env)
        discord_token = config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
        if discord_token:
            from kazma_gateway.adapters.discord import DiscordAdapter

            discord_adapter = DiscordAdapter(token=discord_token)
            gateway.add_adapter(discord_adapter)
            logger.info("[Gateway] Discord adapter registered")
        else:
            logger.info("[Gateway] No DISCORD_BOT_TOKEN — Discord adapter skipped")

        # Slack adapter (optional, via env vars)
        # Slack adapter (from config store → env)
        slack_bot_token = config_store.get("connectors.slack.token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
        slack_app_token = config_store.get("connectors.slack.app_token", "") or os.environ.get("SLACK_APP_TOKEN", "")
        if slack_bot_token and slack_app_token:
            from kazma_gateway.adapters.slack import SlackAdapter

            slack_adapter = SlackAdapter(bot_token=slack_bot_token, app_token=slack_app_token)
            gateway.add_adapter(slack_adapter)
            logger.info("[Gateway] Slack adapter registered (Socket Mode)")
        else:
            logger.info("[Gateway] No SLACK_BOT_TOKEN/SLACK_APP_TOKEN — Slack adapter skipped")

        # Register the Brain handler (IncomingMessage → LangGraph → reply)
        session_store = SQLiteSessionStore("kazma-data/sessions.db")
        gateway.set_persistence(
            session_store=session_store,
            session_store_path="kazma-data/sessions.db",
        )

        # Wire the dashboard's legacy route so it can enrich sessions with
        # platform/display_name from the persistent session store.
        from kazma_ui.dashboard import set_dashboard_context

        set_dashboard_context(
            tracer=agent.tracer,
            cost_breaker=agent.cost_breaker,
            session_store=session_store,
        )

        # ── Vector Memory (RAG) ───────────────────────────────────
        try:
            from kazma_core.agent.tool_registry import set_vector_memory
            from kazma_core.memory.vector_store import VectorMemory

            vector_memory_path = os.environ.get("KAZMA_VECTOR_PATH", "~/.kazma/vector_memory")
            vector_memory_collection = os.environ.get("KAZMA_VECTOR_COLLECTION", "agent_memory")
            vector_memory_model = os.environ.get("KAZMA_VECTOR_MODEL", "all-MiniLM-L6-v2")

            vector_memory = VectorMemory(
                path=vector_memory_path,
                collection_name=vector_memory_collection,
                model_name=vector_memory_model,
            )
            set_vector_memory(vector_memory)
            logger.info(
                "[VectorMemory] Initialized at %s (collection=%s, model=%s)",
                vector_memory_path, vector_memory_collection, vector_memory_model,
            )
        except Exception as e:
            # ChromaDB / sentence-transformers are optional (rag extra).
            # Log at DEBUG so the default startup output stays clean, with a
            # one-time hint about how to enable RAG memory.
            logger.debug("[VectorMemory] Not available: %s", e)
            if not getattr(app.state, "_vector_memory_hint_shown", False):
                logger.info(
                    "[VectorMemory] RAG memory disabled. "
                    "Install the 'rag' extra (pip install -e '.[rag]') to enable."
                )
                app.state._vector_memory_hint_shown = True

        try:
            # Use the SSE graph built above if available
            sse_graph_ref = locals().get("sse_graph")
            if sse_graph_ref:
                brain_handler = create_graph_handler(
                    graph=sse_graph_ref,
                    manager=gateway,
                    system_prompt=agent.system_prompt,
                    cost_breaker=agent.cost_breaker,
                    store=session_store,
                )
                gateway.on_message(brain_handler)
                logger.info("[Gateway] Brain handler registered")
            else:
                logger.warning("[Gateway] No graph available — Brain handler not registered")
        except Exception as e:
            logger.warning("[Gateway] Brain handler failed to register: %s", e)

        from kazma_ui.gateway_monitor import create_gateway_router

        monitor_router = create_gateway_router(
            gateway=gateway,
            session_store=session_store,
            checkpointer=None,  # set at startup when checkpointer is created
        )
        app.include_router(monitor_router)
        logger.info("[Gateway] Monitor router mounted at /api/gateway/*")

        # ── Dynamic Adapter Refresh ──────────────────────────────────────
        @app.post("/api/gateway/refresh-adapters")
        async def refresh_gateway_adapters() -> dict[str, Any]:
            """Re-read connector tokens from config_store and re-register adapters.

            Call this after saving connector settings to hot-reload adapters
            without restarting the server.
            """
            logger.info("[Gateway] Refreshing adapters — stopping old adapters")

            # 1. Stop old adapters before clearing (cancel their listen tasks)
            for old_adapter in gateway.adapters:
                try:
                    await old_adapter.stop()
                except Exception:
                    logger.warning(
                        "[Gateway] Error stopping adapter %s during refresh",
                        old_adapter.name,
                    )

            # 2. Clear old adapters
            gateway.adapters.clear()

            # 3. Re-resolve Telegram token from config_store > YAML > env
            telegram_token = config_store.get("connectors.telegram.token", "") or \
                             config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
            if not telegram_token:
                telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

            if telegram_token:
                tg_adapter = TelegramAdapter(token=telegram_token)
                # Set allowed users from config store
                allowed = config_store.get("connectors.telegram.allowed_users", "")
                if allowed:
                    try:
                        allowed_ids = [int(uid.strip()) for uid in allowed.split(",") if uid.strip()]
                        tg_adapter.set_allowed_users(allowed_ids)
                    except ValueError:
                        logger.warning("[Gateway] Invalid allowed_users format: %s", allowed)
                gateway.add_adapter(tg_adapter)
                logger.info("[Gateway] Telegram adapter re-registered via refresh")

            # Discord adapter (config_store > env)
            discord_token = config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
            if discord_token:
                from kazma_gateway.adapters.discord import DiscordAdapter
                discord_adapter = DiscordAdapter(token=discord_token)
                gateway.add_adapter(discord_adapter)
                logger.info("[Gateway] Discord adapter re-registered via refresh")

            # Slack adapter (config_store > env)
            slack_bot_token = config_store.get("connectors.slack.token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
            slack_app_token = config_store.get("connectors.slack.app_token", "") or os.environ.get("SLACK_APP_TOKEN", "")
            if slack_bot_token and slack_app_token:
                from kazma_gateway.adapters.slack import SlackAdapter
                slack_adapter = SlackAdapter(bot_token=slack_bot_token, app_token=slack_app_token)
                gateway.add_adapter(slack_adapter)
                logger.info("[Gateway] Slack adapter re-registered via refresh")

            # 4. Start new adapters so they begin polling
            for new_adapter in gateway.adapters:
                try:
                    await new_adapter.start(gateway.queue, gateway._shutdown)
                    logger.info(
                        "[Gateway] Adapter %s started via refresh",
                        new_adapter.name,
                    )
                except Exception:
                    logger.warning(
                        "[Gateway] Failed to start adapter %s during refresh",
                        new_adapter.name,
                    )

            logger.info(
                "[Gateway] Adapter refresh complete — %d adapter(s) running",
                len(gateway.adapters),
            )

            return {
                "status": "ok",
                "adapters_count": len(gateway.adapters),
                "adapters": [a.name for a in gateway.adapters],
            }

        # ── Prometheus Metrics Endpoint ───────────────────────────
        from kazma_ui.metrics import create_metrics_router

        metrics_router = create_metrics_router(gateway=gateway, session_store=session_store)
        app.include_router(metrics_router)
        logger.info("[Metrics] Prometheus /metrics endpoint mounted")

        # ── Health Check Endpoint ──────────────────────────────────
        @app.get("/health")
        async def health_check() -> dict[str, Any]:
            """Health endpoint returning queue depth, adapter status, and uptime."""
            adapters = [_a for _a in gateway.adapters] if hasattr(gateway, 'adapters') else []
            queue = getattr(gateway, 'queue', None)
            return {
                "status": "ok",
                "gateway_started": getattr(gateway, '_started', False),
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
                "init_errors": _init_errors,
            }
        logger.info("[Health] /health endpoint mounted")

        # ── HITL Approval Endpoint ────────────────────────────────
        from fastapi.responses import JSONResponse as _JSONResponse

        # HITL auth — shared secret validation
        _KAZMA_SECRET = os.environ.get("KAZMA_SECRET", "")

        # Mutable holder for the latest graph/checkpointer references.
        # The graph is recompiled with a checkpointer during startup, so
        # we resolve the latest reference at request time instead of
        # capturing the pre-startup closure variable.
        _hitl_state: dict[str, Any] = {}

        def _resolve_hitl_graph() -> Any:
            """Return the latest compiled graph reference (post-startup)."""
            return _hitl_state.get("graph") or _sse_graph_ref

        def _resolve_hitl_checkpointer() -> Any:
            """Return the latest checkpointer (post-startup)."""
            return _hitl_state.get("checkpointer")

        @app.post("/api/approve/{thread_id}")
        async def approve_tool(thread_id: str, request: Request) -> _JSONResponse:
            """Resume a paused graph after HITL approval/deny.

            Body: {"action": "approve" | "deny", "reason": "optional"}
            Headers: X-Kazma-Secret (required if KAZMA_SECRET env var is set)
            """
            # Validate shared secret (timing-safe)
            if _KAZMA_SECRET:
                import secrets as _secrets

                provided = request.headers.get("X-Kazma-Secret", "")
                if not _secrets.compare_digest(provided, _KAZMA_SECRET):
                    return _JSONResponse({"error": "Unauthorized"}, status_code=401)

            try:
                body = await request.json()
            except Exception:
                return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

            action = body.get("action", "deny")
            approved = action == "approve"

            # Get the graph reference (latest, post-startup)
            graph_ref = _resolve_hitl_graph()
            if graph_ref is None:
                return _JSONResponse({"error": "Graph not available"}, status_code=503)

            try:
                from langgraph.types import Command

                config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
                resume_value = {"approved": approved, "reason": body.get("reason", "")}

                # Resume the graph — this continues from the interrupt()
                result = await graph_ref.ainvoke(
                    Command(resume=resume_value),
                    config=config,
                )

                return _JSONResponse({
                    "status": "approved" if approved else "denied",
                    "thread_id": thread_id,
                }, status_code=202)

            except Exception as e:
                logger.exception("[HITL] Failed to resume graph for thread=%s", thread_id)
                return _JSONResponse({"error": str(e)}, status_code=500)

        logger.info("[HITL] Approval endpoint mounted at /api/approve/{thread_id}")

        # ── Pending Approvals Listing (GET /api/pending-approvals) ──
        # _hitl_state, _resolve_hitl_graph, _resolve_hitl_checkpointer
        # are defined above alongside the approve endpoint.

        @app.get("/api/pending-approvals")
        async def list_pending_approvals() -> _JSONResponse:
            """List all threads currently waiting for HITL tool approval.

            Returns:
                {"pending": [{"thread_id","tool_name","arguments","message"}], "count": N}
            """
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
            except Exception as exc:
                logger.exception("[HITL] Failed to list pending approvals")
                return _JSONResponse({"pending": [], "count": 0, "error": str(exc)}, status_code=500)

        logger.info("[HITL] Pending approvals endpoint mounted at /api/pending-approvals")

        # ── Cron Scheduler ────────────────────────────────────────
        try:
            from kazma_core.cron.scheduler import CronScheduler, SQLiteCronStore, set_cron_scheduler

            cron_store = SQLiteCronStore("kazma-data/cron.db")
            # Init is async — defer to startup
            _cron_store_ref = cron_store

            cron_scheduler = CronScheduler(
                store=cron_store,
                poll_interval=30.0,
            )
            set_cron_scheduler(cron_scheduler)
            logger.info("[Cron] Scheduler initialized")
        except Exception as e:
            logger.warning("[Cron] Scheduler not available: %s", e)
            _cron_store_ref = None

        # ── Wire SwarmMessageBus (swarm → platform outbound) ────────
        # Connect the swarm engine's message bus to the active platform
        # adapter so worker results, HITL checkpoints, and progress events
        # are visible in Telegram/Discord/Slack.
        if _swarm_mgr is not None:
            try:
                from kazma_core.swarm.bus import get_message_bus

                bus = get_message_bus()
                # Wire a platform BusAdapter. Only one adapter is active at
                # a time (bus singleton) — priority: Telegram > Discord > Slack.
                _bus_wired = False

                # TelegramBusAdapter
                if tg_adapter is not None and telegram_token:
                    try:
                        from kazma_gateway.adapters.telegram_bus import TelegramBusAdapter

                        tg_bus = TelegramBusAdapter(
                            bot_token=telegram_token,
                            chat_id=config_store.get("connectors.telegram.swarm_chat_id", ""),
                        )
                        bus.set_adapter(tg_bus)
                        _bus_wired = True
                        logger.info("[SwarmBus] TelegramBusAdapter wired — swarm events will appear in Telegram")
                    except ImportError:
                        logger.debug("[SwarmBus] TelegramBusAdapter not available")
                    except Exception as e:
                        logger.warning("[SwarmBus] Failed to wire TelegramBusAdapter: %s", e)

                # DiscordBusAdapter (fallback if Telegram not wired)
                if not _bus_wired:
                    _discord_tok = config_store.get("connectors.discord.token", "") or os.environ.get("DISCORD_BOT_TOKEN", "")
                    _discord_chan = config_store.get("connectors.discord.swarm_channel_id", "")
                    if _discord_tok and _discord_chan:
                        try:
                            from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

                            bus.set_adapter(DiscordBusAdapter(bot_token=_discord_tok, channel_id=_discord_chan))
                            _bus_wired = True
                            logger.info("[SwarmBus] DiscordBusAdapter wired — swarm events will appear in Discord")
                        except ImportError:
                            logger.debug("[SwarmBus] DiscordBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to wire DiscordBusAdapter: %s", e)

                # SlackBusAdapter (fallback if neither Telegram nor Discord wired)
                if not _bus_wired:
                    _slack_tok = config_store.get("connectors.slack.token", "") or os.environ.get("SLACK_BOT_TOKEN", "")
                    _slack_chan = config_store.get("connectors.slack.swarm_channel_id", "")
                    if _slack_tok and _slack_chan:
                        try:
                            from kazma_gateway.adapters.slack_bus import SlackBusAdapter

                            bus.set_adapter(SlackBusAdapter(bot_token=_slack_tok, channel_id=_slack_chan))
                            _bus_wired = True
                            logger.info("[SwarmBus] SlackBusAdapter wired — swarm events will appear in Slack")
                        except ImportError:
                            logger.debug("[SwarmBus] SlackBusAdapter not available")
                        except Exception as e:
                            logger.warning("[SwarmBus] Failed to wire SlackBusAdapter: %s", e)

                if not _bus_wired:
                    logger.info("[SwarmBus] No platform adapter — swarm events stay internal (NullBusAdapter)")
            except Exception as e:
                logger.warning("[SwarmBus] Failed to initialize message bus: %s", e)

        _gateway = gateway
        _sse_graph_ref = locals().get("sse_graph")

        @app.on_event("startup")
        async def _start_gateway() -> None:
            """Start the gateway, initialize checkpointer, recompile graph."""
            nonlocal _gateway, _sse_graph_ref

            # Initialize checkpointer and recompile graph with it
            try:
                from kazma_gateway.stores.checkpoint import create_checkpointer

                checkpointer = await create_checkpointer("kazma-data/checkpoints.db")
                logger.info("[Checkpoint] SQLite checkpointer initialized")

                # Wire the legacy dashboard route so it can list checkpoints.
                from kazma_ui.dashboard import set_dashboard_context

                set_dashboard_context(checkpoint_manager=checkpointer)

                if _sse_graph_ref is not None:
                    from kazma_core.agent.graph_builder import build_supervisor_graph
                    from kazma_core.safety.hitl import get_hitl_config

                    # Pass hitl_config so danger tools interrupt() on the
                    # checkpointed SSE/gateway graph too. Without this the
                    # HITL gate compiled into tool_worker_node stays dormant.
                    recompile_hitl = get_hitl_config(config.raw)
                    if not recompile_hitl.get("enabled", True):
                        recompile_hitl = None

                    _sse_graph_ref = build_supervisor_graph(
                        llm=agent.llm,
                        system_prompt=agent.system_prompt,
                        tool_definitions=agent.tools.get_tool_definitions(),
                        tool_executor=agent.tools,
                        cost_breaker=agent.cost_breaker,
                        authority=agent.authority,
                        tracer=agent.tracer,
                        checkpointer=checkpointer,
                        hitl_config=recompile_hitl,
                    )
                    logger.info("[Checkpoint] Graph recompiled with checkpointer")

                    # Update HITL references so /api/pending-approvals can
                    # enumerate interrupted threads from the checkpoint DB.
                    _hitl_state["graph"] = _sse_graph_ref
                    _hitl_state["checkpointer"] = checkpointer
                    logger.info("[HITL] Pending approvals endpoint linked to checkpointed graph")

                    # Re-register brain handler with the checkpointed graph
                    from kazma_gateway.agent_handler import create_graph_handler

                    brain_handler = create_graph_handler(
                        graph=_sse_graph_ref,
                        manager=_gateway,
                        system_prompt=agent.system_prompt,
                        cost_breaker=agent.cost_breaker,
                        store=session_store,
                    )
                    _gateway.on_message(brain_handler)
                    logger.info("[Checkpoint] Brain handler re-registered with checkpointed graph")
            except Exception as e:
                logger.warning("[Checkpoint] Checkpointer not available: %s", e)

            if _gateway is None:
                return
            try:
                await _gateway.start()
                logger.info(
                    "[Gateway] Started — adapters: [%s], queue maxsize=%d",
                    ", ".join(a.name for a in _gateway.adapters),
                    _gateway.queue.maxsize,
                )
            except Exception as e:
                logger.warning("[Gateway] Failed to start: %s", e)

            # Initialize cron store and start scheduler
            if _cron_store_ref is not None:
                try:
                    await _cron_store_ref.init()
                    from kazma_core.cron.scheduler import get_cron_scheduler

                    cron_sched = get_cron_scheduler()
                    if cron_sched:
                        await cron_sched.start()
                        logger.info("[Cron] Scheduler started")
                except Exception as e:
                    logger.warning("[Cron] Failed to start: %s", e)

        @app.on_event("shutdown")
        async def _stop_gateway() -> None:
            """Stop the gateway and drain remaining messages."""
            nonlocal _gateway
            if _gateway is None:
                return
            try:
                await _gateway.stop()
                logger.info("[Gateway] Stopped cleanly")
            except Exception as e:
                logger.warning("[Gateway] Error during shutdown: %s", e)
            _gateway = None

    except Exception as e:
        logger.warning("Gateway failed to initialize: %s", e)
        _init_errors.append({"subsystem": "gateway", "error": str(e)})

    # ── /api/telemetry — Mock telemetry data for Chart.js dashboard ──
    import time as time_module

    _telemetry_state = {
        "tokens_base": 1200,
        "vram_base": 3072,
        "last_tick": time_module.time(),
    }

    @app.get("/api/telemetry", deprecated=True)
    async def get_telemetry() -> dict:
        """Return hardware telemetry data.

        NOTE: This endpoint is deprecated.  Real metrics are available
        at /api/telemetry/stream via SSE.  This endpoint returns empty
        values for backward compatibility.
        """
        return {
            "tokens": 0,
            "vram_mb": 0,
            "model": "deprecated",
            "timestamp": time_module.time(),
        }

    # NOTE: The duplicate mock /api/telemetry/stream route that was defined
    # here has been removed.  The real telemetry SSE route is provided by
    # telemetry_route.create_telemetry_router() (mounted above) and serves
    # genuine HardwareMonitor metrics at 1 Hz.

    # ── /api/status — Subsystem init error reporting ──────────────
    @app.get("/api/status")
    async def get_status() -> dict[str, Any]:
        """Return subsystem initialization status.

        Any subsystem (SSE chat, telemetry, gateway) that failed to
        initialize is listed in ``init_errors`` so the UI can surface
        a warning banner to the operator.
        """
        return {
            "status": "degraded" if _init_errors else "ok",
            "init_errors": _init_errors,
        }

    # ── Lifecycle events
    @app.on_event("startup")
    async def on_startup() -> None:
        try:
            tool_count = await agent.connect_mcp_servers()
            logger.info("Connected %d MCP tools on startup", tool_count)
        except Exception as e:
            logger.warning("Failed to connect MCP servers: %s", e)

    @app.on_event("shutdown")
    async def on_shutdown() -> None:
        """Graceful shutdown — drain queues, close DBs, flush traces."""
        from kazma_core.shutdown import signal_shutdown

        logger.info("[Shutdown] Starting graceful shutdown...")

        # 1. Signal all background tasks to stop
        signal_shutdown()

        # 2. Stop gateway (drains message queue, closes adapter connections)
        if _gateway is not None:
            try:
                await _gateway.stop()
                logger.info("[Shutdown] Gateway stopped")
            except Exception as e:
                logger.warning("[Shutdown] Gateway stop error: %s", e)

        # 3. Stop cron scheduler (stops poll loop, saves pending jobs)
        try:
            from kazma_core.cron.scheduler import get_cron_scheduler

            cron_sched = get_cron_scheduler()
            if cron_sched:
                await cron_sched.stop()
                logger.info("[Shutdown] Cron scheduler stopped")
        except Exception as e:
            logger.warning("[Shutdown] Cron stop error: %s", e)

        # 4. Close session store (flush WAL, close DB connection)
        try:
            store_ref = locals().get("_cron_store_ref") or globals().get("_cron_store_ref")
            if store_ref is not None:
                await store_ref.close()
        except Exception:
            pass

        # 5. Give loops time to exit cleanly
        await asyncio.sleep(0.5)

        # 6. Shutdown agent
        try:
            await agent.shutdown()
        except Exception:
            pass

        # 7. Close config store
        try:
            config_store.close()
        except Exception:
            pass

        # 8. Flush Langfuse tracer
        try:
            if hasattr(agent, "tracer") and hasattr(agent.tracer, "flush"):
                agent.tracer.flush()
                logger.info("[Shutdown] Tracer flushed")
        except Exception:
            pass

        logger.info("[Shutdown] Graceful shutdown complete")

    # ── Global Error Handlers ──────────────────────────────────────────

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Any) -> HTMLResponse:
        # 404s are safe to surface to the user — the path is visible in the
        # URL bar anyway. We do NOT log at error level to avoid noise.
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 404, "message": "Page not found", "detail": ""},
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc: Any) -> HTMLResponse:
        # Log full traceback server-side; never expose internals to clients.
        logger.exception("[app] Internal server error on %s %s", request.method, request.url.path)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Internal server error", "detail": ""},
            status_code=500,
        )

    @app.exception_handler(Exception)
    async def catch_all(request: Request, exc: Any) -> HTMLResponse:
        # Log full traceback server-side; never expose internals to clients.
        logger.exception("[app] Unhandled exception on %s %s", request.method, request.url.path)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Internal server error", "detail": ""},
            status_code=500,
        )

    return app


def main() -> None:
    """Entry point for `kazma-web` command.

    Usage:
        kazma-web              # port 8000
        kazma-web --port 8080  # custom port
    """
    import argparse

    parser = argparse.ArgumentParser(description="Kazma Web UI")
    parser.add_argument("--port", "-p", type=int, default=8000, help="Port to bind (default: 8000)")
    args, _ = parser.parse_known_args()

    import os as _os3

    import uvicorn

    # Security: default to localhost.  Only bind 0.0.0.0 when
    # KAZMA_SECRET is explicitly set, otherwise log a warning.
    host = "127.0.0.1"
    if _os3.environ.get("KAZMA_SECRET"):
        host = "0.0.0.0"
    else:
        logger.warning(
            "[app] KAZMA_SECRET not set — binding to 127.0.0.1 only. "
            "Set KAZMA_SECRET to bind on all interfaces."
        )

    app = create_app()
    uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
