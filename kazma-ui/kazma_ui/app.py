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
from fastapi.responses import HTMLResponse, StreamingResponse
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

    # Load agent config and create agent
    config = load_config(config_path)
    agent = KazmaAgent(config)

    # Create config store for runtime settings
    config_store = ConfigStore()

    # Create FastAPI app
    app = FastAPI(
        title="Kazma",
        version=config.version,
        description="Autonomous AI Agent Framework — Arabic RTL Dashboard",
    )

    # Mount static files
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Setup Jinja2 templates
    _TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    # Global template context for language/direction
    templates.env.globals["lang"] = agent.config.language if hasattr(agent.config, "language") else "ar"
    templates.env.globals["dir"] = "rtl" if getattr(agent.config, "rtl", True) else "ltr"

    # Create routers
    from kazma_ui.agents import create_agents_router
    from kazma_ui.chat import chat_websocket_handler, create_chat_router, list_sessions
    from kazma_ui.mcp_ui import create_mcp_router
    from kazma_ui.settings import create_settings_router
    from kazma_ui.skills_ui import create_skills_router

    chat_router = create_chat_router(agent, templates)
    settings_router = create_settings_router(agent, config_store, templates)
    skills_router = create_skills_router(agent, templates)
    mcp_router = create_mcp_router(agent, templates)
    agents_router = create_agents_router(agent, templates)

    # Mount routers
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.include_router(skills_router)
    app.include_router(mcp_router)
    app.include_router(agents_router)

    # ── SSE Chat Router (LangGraph astream_events → HTMX/Alpine) ──
    _checkpointer = None
    try:
        from kazma_core.agent.graph_builder import build_supervisor_graph
        from kazma_core.agent.tool_registry import LocalToolRegistry

        # Build the Supervisor graph for SSE streaming
        sse_tools = LocalToolRegistry(include_builtins=True)
        # Also register MCP tools if any were connected
        for tool_def in agent.tools.get_tool_definitions():
            fname = tool_def.get("function", {})
            sse_tools.register_function(
                name=fname.get("name", ""),
                func=lambda **kw: {"content": "MCP tool (use WebSocket)", "is_error": False},
                description=fname.get("description", ""),
                category="mcp",
            )

        sse_graph = build_supervisor_graph(
            llm=agent.llm,
            system_prompt=agent.system_prompt,
            tool_definitions=sse_tools.get_tool_definitions(),
            tool_executor=sse_tools,
            cost_breaker=agent.cost_breaker,
            authority=agent.authority,
            tracer=agent.tracer,
        )

        from kazma_ui.sse_chat import create_sse_chat_router

        sse_router = create_sse_chat_router(
            graph=sse_graph,
            checkpointer=None,
            system_prompt=agent.system_prompt,
            cost_breaker=agent.cost_breaker,
            authority=agent.authority,
            tracer=agent.tracer,
            llm_provider=agent.llm,
        )
        app.include_router(sse_router)
        logger.info("SSE chat router mounted at /api/chat/stream")
    except Exception as e:
        logger.warning("SSE chat router failed to initialize: %s", e)

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

    # Dashboard (legacy)
    from kazma_ui.dashboard import router as dashboard_router

    app.include_router(dashboard_router)

    # Dashboard WebSocket for real-time trace updates
    @app.websocket("/ws/dashboard")
    async def ws_dashboard(websocket: WebSocket) -> None:
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
        await chat_websocket_handler(websocket, agent)

    # ── Root — Unified Master Workspace ──
    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        """Serve the unified orchestration workspace."""
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "config": agent.config,
                "sessions": list_sessions(),
            },
        )

    # ── Legacy routes -> redirect to / ──
    @app.get("/chat", response_class=HTMLResponse)
    async def chat_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)

    @app.get("/workspace", response_class=HTMLResponse)
    async def workspace_redirect() -> RedirectResponse:
        return RedirectResponse("/", status_code=307)

    # ── Models & Ollama Management Router ────────────────────────
    from kazma_ui.models_route import create_models_router

    models_router = create_models_router()
    app.include_router(models_router)
    logger.info("Models router mounted at /api/models, /api/ollama/*")

    # ── Gateway (Omnichannel Message Bus) ────────────────────────────
    _gateway: Any = None  # module-level ref for shutdown handler

    try:
        from kazma_gateway import GatewayManager
        from kazma_gateway.adapters.telegram import TelegramAdapter
        from kazma_gateway.agent_handler import create_graph_handler
        from kazma_gateway.stores import SQLiteSessionStore

        gateway = GatewayManager(max_queue_size=100)

        # Resolve Telegram token from config or environment
        telegram_token = config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        if not telegram_token:
            telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")

        tg_adapter: TelegramAdapter | None = None
        if telegram_token:
            tg_adapter = TelegramAdapter(token=telegram_token)
            gateway.add_adapter(tg_adapter)
            logger.info("[Gateway] Telegram adapter registered (polling mode)")

            # Mount webhook ingress for testing / optional push mode
            webhook_router = tg_adapter.create_webhook_router()
            app.include_router(webhook_router, prefix="/api/webhooks/telegram")
            logger.info("[Gateway] Webhook ingress mounted at /api/webhooks/telegram")
        else:
            logger.info("[Gateway] No Telegram token — Telegram adapter skipped")

        # Discord adapter (optional, via env var)
        discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
        if discord_token:
            from kazma_gateway.adapters.discord import DiscordAdapter

            discord_adapter = DiscordAdapter(token=discord_token)
            gateway.add_adapter(discord_adapter)
            logger.info("[Gateway] Discord adapter registered")
        else:
            logger.info("[Gateway] No DISCORD_BOT_TOKEN — Discord adapter skipped")

        # Register the Brain handler (IncomingMessage → LangGraph → reply)
        session_store = SQLiteSessionStore("kazma-data/sessions.db")
        gateway.set_persistence(
            session_store=session_store,
            session_store_path="kazma-data/sessions.db",
        )
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

        # Mount the gateway monitor router
        from kazma_ui.gateway_monitor import create_gateway_router

        monitor_router = create_gateway_router(
            gateway=gateway,
            session_store=session_store,
            checkpointer=None,  # set at startup when checkpointer is created
        )
        app.include_router(monitor_router)
        logger.info("[Gateway] Monitor router mounted at /api/gateway/*")

        # ── HITL Approval Endpoint ────────────────────────────────
        from fastapi import Request as _Request
        from fastapi.responses import JSONResponse as _JSONResponse

        @app.post("/api/approve/{thread_id}")
        async def approve_tool(thread_id: str, _request: _Request) -> _JSONResponse:
            """Resume a paused graph after HITL approval/deny.

            Body: {"action": "approve" | "deny", "reason": "optional"}
            """
            try:
                body = await _request.json()
            except Exception:
                return _JSONResponse({"error": "Invalid JSON"}, status_code=400)

            action = body.get("action", "deny")
            approved = action == "approve"

            # Get the graph reference
            graph_ref = locals().get("_sse_graph_ref") or globals().get("_sse_graph_ref")
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

        # ── Sub-Agent Manager ─────────────────────────────────────
        try:
            from kazma_core.agent.sub_agent import SubAgentManager, set_sub_agent_manager

            sub_agent_mgr = SubAgentManager(
                graph_builder=lambda tools=None, hitl_config=None: build_supervisor_graph(
                    llm=agent.llm,
                    system_prompt=agent.system_prompt,
                    tool_definitions=locals().get("sse_tools", sse_tools).get_tool_definitions()
                    if "sse_tools" in dir()
                    else [],
                    tool_executor=locals().get("sse_tools", sse_tools),
                    cost_breaker=agent.cost_breaker,
                    authority=agent.authority,
                    tracer=agent.tracer,
                    hitl_config=hitl_config,
                ),
                max_concurrent=3,
            )
            set_sub_agent_manager(sub_agent_mgr)
            logger.info("[SubAgent] Manager initialized (max_concurrent=3)")
        except Exception as e:
            logger.warning("[SubAgent] Manager not available: %s", e)

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

        _gateway = gateway
        _sse_tools_ref = locals().get("sse_tools")
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

                if _sse_graph_ref is not None and _sse_tools_ref is not None:
                    from kazma_core.agent.graph_builder import build_supervisor_graph

                    _sse_graph_ref = build_supervisor_graph(
                        llm=agent.llm,
                        system_prompt=agent.system_prompt,
                        tool_definitions=_sse_tools_ref.get_tool_definitions(),
                        tool_executor=_sse_tools_ref,
                        cost_breaker=agent.cost_breaker,
                        authority=agent.authority,
                        tracer=agent.tracer,
                        checkpointer=checkpointer,
                    )
                    logger.info("[Checkpoint] Graph recompiled with checkpointer")

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

    # ── /api/telemetry — Mock telemetry data for Chart.js dashboard ──
    import random
    import time as time_module

    _telemetry_state = {
        "tokens_base": 1200,
        "vram_base": 3072,
        "last_tick": time_module.time(),
    }

    @app.get("/api/telemetry")
    async def get_telemetry() -> dict:
        """Return mock telemetry data: token usage and VRAM allocation.

        Simulates a local-edge inference setup (RTX 4090, 24GB VRAM).
        Token counts vary to produce a realistic scrolling chart.
        """
        now = time_module.time()
        # Drift the base values smoothly every tick
        if now - _telemetry_state["last_tick"] > 2.5:
            _telemetry_state["tokens_base"] = max(200, _telemetry_state["tokens_base"] + random.randint(-150, 200))
            _telemetry_state["vram_base"] = max(
                512, min(20480, _telemetry_state["vram_base"] + random.randint(-128, 128))
            )
            _telemetry_state["last_tick"] = now

        return {
            "tokens": _telemetry_state["tokens_base"],
            "vram_mb": _telemetry_state["vram_base"],
            "model": agent.llm_config.model if hasattr(agent, "llm_config") else "local",
            "timestamp": now,
        }

    # ── /api/telemetry/stream — SSE stream for live Chart.js metrics ──
    @app.get("/api/telemetry/stream")
    async def telemetry_stream(request: Request):
        """Server-Sent Events endpoint pushing hardware metrics every 1.5s.

        Yields JSON payloads: {"cpu": 23, "ram_used_gb": 4.2, "gpu": 45, "vram_used_gb": 3.1}
        """
        import asyncio
        import json as json_mod

        from kazma_core.shutdown import is_shutting_down

        async def event_generator():
            tokens_base = 1200
            vram_base = 3072
            while not is_shutting_down():
                try:
                    # Simulate drifting metrics
                    tokens_base = max(200, tokens_base + random.randint(-80, 120))
                    vram_base = max(512, min(20480, vram_base + random.randint(-64, 64)))
                    cpu = random.randint(10, 85)
                    ram = round(random.uniform(1.5, 12.0), 1)
                    gpu = random.randint(5, 95)
                    vram = round(vram_base / 1024, 1)  # MB -> GB

                    payload = {
                        "cpu": cpu,
                        "ram_used_gb": ram,
                        "gpu": gpu,
                        "vram_used_gb": vram,
                    }
                    yield f"data: {json_mod.dumps(payload)}\n\n"
                    await asyncio.sleep(1.5)
                except asyncio.CancelledError:
                    break

        return StreamingResponse(event_generator(), media_type="text/event-stream")

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
        from kazma_core.shutdown import signal_shutdown

        signal_shutdown()
        # Give loops time to exit cleanly
        await asyncio.sleep(0.5)
        await agent.shutdown()
        config_store.close()
        logger.info("Kazma WebUI shut down.")

    # ── Global Error Handlers ──────────────────────────────────────────

    @app.exception_handler(404)
    async def not_found(request: Request, exc: Any) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 404, "message": "Page not found", "detail": str(exc)},
            status_code=404,
        )

    @app.exception_handler(500)
    async def server_error(request: Request, exc: Any) -> HTMLResponse:
        logger.error("Internal server error: %s", exc)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Internal server error", "detail": str(exc)},
            status_code=500,
        )

    @app.exception_handler(Exception)
    async def catch_all(request: Request, exc: Any) -> HTMLResponse:
        logger.error("Unhandled exception: %s", exc)
        return templates.TemplateResponse(
            request,
            "error.html",
            {"code": 500, "message": "Something went wrong", "detail": str(exc)},
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

    import uvicorn

    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
