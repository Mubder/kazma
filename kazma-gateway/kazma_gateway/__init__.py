"""Kazma Gateway — Unified multi-platform message gateway.

Headless, polling-based architecture. No public IP, no tunnels, no webhooks.

Usage:
    from kazma_gateway import GatewayManager, IncomingMessage, OutboundMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter
    from kazma_gateway.consumer import make_agent_handler
    from kazma_gateway.dispatcher import MessageDispatcher

    manager = GatewayManager()
    manager.add_adapter(TelegramAdapter(token="..."))
    manager.on_message(make_agent_handler(my_agent))
    await manager.start()
"""

from kazma_gateway.dispatcher import MessageDispatcher
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
    "OutboundMessage",
    "SessionStore",
]
