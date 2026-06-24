"""Kazma Gateway — Unified multi-platform message gateway.

Headless, polling-based architecture. No public IP, no tunnels, no webhooks.

Usage:
    from kazma_gateway import GatewayManager, IncomingMessage, OutboundMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter

    manager = GatewayManager(max_queue_size=100)
    manager.add_adapter(TelegramAdapter(token="..."))
    manager.on_message(my_handler)
    await manager.start()
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
