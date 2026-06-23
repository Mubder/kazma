"""Kazma WebUI — FastAPI app factory.

Creates and configures the FastAPI application with all routers,
WebSocket endpoints, static files, and template engine.
"""

from __future__ import annotations

import asyncio
import logging
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

    # ── Telegram Webhook Router ──────────────────────────────────
    try:
        from kazma_connectors.telegram_bridge import create_telegram_webhook_router

        telegram_token = config.raw.get("connectors", {}).get("telegram", {}).get("token", "")
        telegram_router = create_telegram_webhook_router(
            graph=locals().get("sse_graph"),
            token=telegram_token,
            system_prompt=agent.system_prompt,
            cost_breaker=agent.cost_breaker,
            authority=agent.authority,
            tracer=agent.tracer,
        )
        app.include_router(telegram_router)
        logger.info("Telegram webhook router mounted at /api/telegram/webhook")
    except Exception as e:
        logger.warning("Telegram router failed to initialize: %s", e)

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
