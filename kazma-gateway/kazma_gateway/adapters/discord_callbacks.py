"""Discord INTERACTION_CREATE routing (shared callback schemes)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.adapters.platform_callbacks import CallbackAction, parse_callback_data

__all__ = ["CallbackAction", "parse_custom_id", "route_swarm_bus"]


def parse_custom_id(custom_id: str) -> CallbackAction:
    """Parse Discord component custom_id (same schemes as Telegram)."""
    return parse_callback_data(custom_id or "")


def route_swarm_bus(custom_id: str) -> str | None:
    """If *custom_id* is a swarm approve/reject, resolve the bus and return task_id."""
    action = parse_custom_id(custom_id)
    if action.kind != "swarm":
        return None
    try:
        from kazma_core.swarm.bus import FanOutBusAdapter, get_message_bus
        from kazma_gateway.adapters.discord_bus import DiscordBusAdapter

        adapter = get_message_bus().adapter
        # Fan-out: try each child adapter
        if isinstance(adapter, FanOutBusAdapter):
            for child in adapter.adapters:
                if isinstance(child, DiscordBusAdapter):
                    return child.handle_callback(action.swarm_data)
            return None
        if isinstance(adapter, DiscordBusAdapter):
            return adapter.handle_callback(action.swarm_data)
    except Exception:
        return None
    return None


def is_install_action(custom_id: str) -> bool:
    action = parse_custom_id(custom_id)
    return action.kind in ("sys_install", "install_dep")


def package_from_install(custom_id: str) -> str:
    return parse_custom_id(custom_id).package_name
