"""Kazma Gateway — Unified multi-platform message gateway.

Headless, polling-based architecture. No public IP, no tunnels, no webhooks.

Usage:
    from kazma_gateway import GatewayManager, IncomingMessage, OutboundMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.agent_handler import create_graph_handler

    manager = GatewayManager(max_queue_size=100)
    manager.add_adapter(TelegramAdapter(token="..."))
    handler = create_graph_handler(graph=..., manager=manager)
    manager.on_message(handler)
    await manager.start()
"""

from kazma_gateway.dispatcher import MessageDispatcher, MessageTracker
from kazma_gateway.gateway import (
    BaseAdapter,
    GatewayManager,
    IncomingMessage,
    OutboundMessage,
    SessionStore,
)

__all__ = [
    "BaseAdapter",
    "GatewayManager",
    "IncomingMessage",
    "MessageDispatcher",
    "MessageTracker",
    "OutboundMessage",
    "SessionStore",
]
