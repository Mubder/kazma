"""Kazma Gateway — Unified multi-platform message gateway.

Headless, polling-based architecture. No public IP, no tunnels, no webhooks required.

Usage:
    from kazma_gateway import GatewayManager, IncomingMessage, OutboundMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.agent_handler import create_graph_handler

    manager = GatewayManager(max_queue_size=100)
    adapter = TelegramAdapter(token="...")
    manager.add_adapter(adapter)

    handler = create_graph_handler(graph=compiled_graph, manager=manager)
    manager.on_message(handler)

    # FastAPI lifespan
    app = FastAPI(lifespan=manager.lifespan)

    # Optional webhook ingress for testing
    app.include_router(adapter.create_webhook_router(), prefix="/api/webhooks/telegram")
"""

from kazma_gateway.gateway import (
    BaseAdapter,
    GatewayManager,
    IncomingMessage,
    OutboundMessage,
)

__all__ = [
    "BaseAdapter",
    "GatewayManager",
    "IncomingMessage",
    "OutboundMessage",
]
