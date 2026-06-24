"""Kazma Gateway — Unified multi-platform message gateway.

Headless, polling-based architecture. No public IP, no tunnels, no webhooks required.

Usage:
    from kazma_gateway import GatewayManager, IncomingMessage, OutboundMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter
<<<<<<< HEAD
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
=======
    from kazma_gateway.consumer import make_agent_handler
    from kazma_gateway.dispatcher import MessageDispatcher

    manager = GatewayManager(max_queue_size=100)
    manager.add_adapter(TelegramAdapter(token="..."))
    manager.on_message(make_agent_handler(my_agent))
    await manager.start()
>>>>>>> d7c7d00 (feat(ui): persistence-aware resume indicator + reset + gateway panel)
"""

from kazma_gateway.gateway import (
    BaseAdapter,
    GatewayManager,
    IncomingMessage,
    OutboundMessage,
)

from kazma_gateway.dispatcher import MessageDispatcher

__all__ = [
    "BaseAdapter",
    "GatewayManager",
    "IncomingMessage",
    "MessageDispatcher",
    "OutboundMessage",
]
