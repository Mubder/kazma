"""Kazma Gateway — Unified multi-platform message gateway.

Provides a polling-based, headless architecture for interfacing with
messaging platforms (Telegram, Discord, etc.) through a unified message bus.

Architecture:
    Platform Adapter → asyncio.Queue (Unified Message Bus) → Agent Loop

Usage:
    from kazma_gateway import GatewayManager, UniversalMessage
    from kazma_gateway.adapters.telegram import TelegramAdapter

    manager = GatewayManager()
    manager.add_adapter(TelegramAdapter(token="..."))
    await manager.start()
"""

from kazma_gateway.base import BaseAdapter
from kazma_gateway.manager import GatewayManager
from kazma_gateway.schemas import UniversalMessage

__all__ = [
    "BaseAdapter",
    "GatewayManager",
    "UniversalMessage",
]
