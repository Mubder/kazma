"""Kazma Gateway — Platform-independent message bus for omnichannel agents.

Architecture
════════════

    Telegram Adapter ──┐
    Discord Adapter  ──┤──→ asyncio.Queue ──→ GatewayManager ──→ KazmaAgent.run()
    WhatsApp Adapter ──┘                              │
                                                      ↓
                                              send_message(target_id, content)
                                                      │
                                                      ↓
                                              Platform-specific adapter.send()
"""

from kazma_gateway.gateway import GatewayManager, get_gateway

__all__ = ["GatewayManager", "get_gateway"]
