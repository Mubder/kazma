"""FastAPI integration for the Kazma Gateway.

Provides a ready-to-use router and lifespan hook for plugging the
GatewayManager into the existing Kazma web application.

Usage in app.py:
    from kazma_gateway.fastapi_integration import setup_gateway, gateway_router

    # Inside create_app():
    gateway = setup_gateway(config, graph, agent)
    app.include_router(gateway_router(gateway))
"""

from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import APIRouter

from kazma_gateway.manager import GatewayManager

logger = logging.getLogger(__name__)


def setup_gateway(
    config: Any,
    graph: Any = None,
    agent: Any = None,
) -> GatewayManager:
    """Create and configure a GatewayManager from kazma.yaml config.

    Reads the ``connectors`` section to instantiate adapters automatically.

    Args:
        config: Loaded AgentConfig (has .raw dict from kazma.yaml).
        graph: Optional compiled LangGraph for message processing.
        agent: Optional KazmaAgent for system_prompt, cost_breaker, etc.

    Returns:
        Configured GatewayManager (not yet started).
    """
    manager = GatewayManager()

    raw = config.raw if hasattr(config, "raw") else {}
    connectors = raw.get("connectors", {})

    # ── Telegram Adapter ─────────────────────────────────────────
    tg_config = connectors.get("telegram", {})
    tg_token = tg_config.get("token", "") or os.environ.get("TELEGRAM_BOT_TOKEN", "")

    if tg_token:
        from kazma_gateway.adapters.telegram import TelegramAdapter

        adapter = TelegramAdapter(
            token=tg_token,
            poll_interval=tg_config.get("poll_interval", 1.0),
            allowed_users=tg_config.get("allowed_users", []),
        )
        manager.add_adapter(adapter)
        logger.info("Telegram adapter configured (token=%s...)", tg_token[:8])
    else:
        logger.info("No Telegram token found — Telegram adapter skipped")

    # ── Register message handler if graph is available ────────────
    if graph is not None:
        from kazma_gateway.agent_handler import create_graph_handler

        handler = create_graph_handler(
            graph=graph,
            manager=manager,
            system_prompt=getattr(agent, "system_prompt", "") if agent else "",
            cost_breaker=getattr(agent, "cost_breaker", None) if agent else None,
        )
        manager.on_message(handler)

    return manager


def gateway_router(gateway: GatewayManager) -> APIRouter:
    """Create a FastAPI router with gateway management endpoints.

    Endpoints:
        GET  /api/gateway/status   — Gateway statistics
        POST /api/gateway/start    — Start the gateway
        POST /api/gateway/stop     — Stop the gateway

    Args:
        gateway: The GatewayManager instance.

    Returns:
        APIRouter ready for app.include_router().
    """
    r = APIRouter(prefix="/api/gateway", tags=["gateway"])

    @r.get("/status")
    async def gateway_status() -> dict[str, Any]:
        """Return gateway statistics."""
        return gateway.stats

    @r.post("/start")
    async def gateway_start() -> dict[str, Any]:
        """Start the gateway (idempotent)."""
        await gateway.start()
        return {"status": "started", **gateway.stats}

    @r.post("/stop")
    async def gateway_stop() -> dict[str, Any]:
        """Stop the gateway (idempotent)."""
        await gateway.stop()
        return {"status": "stopped", **gateway.stats}

    return r
