"""Slack block_actions routing (shared callback schemes)."""

from __future__ import annotations

from typing import Any

from kazma_gateway.adapters.platform_callbacks import CallbackAction, parse_callback_data

__all__ = ["CallbackAction", "parse_action_value", "route_swarm_bus", "iter_block_actions"]


def parse_action_value(value: str) -> CallbackAction:
    return parse_callback_data(value or "")


def iter_block_actions(payload: dict[str, Any]) -> list[tuple[str, CallbackAction]]:
    """Yield (raw_value, action) for each button in a block_actions payload."""
    out: list[tuple[str, CallbackAction]] = []
    for action in payload.get("actions") or []:
        value = action.get("value") or action.get("action_id") or ""
        out.append((value, parse_action_value(value)))
    return out


def route_swarm_bus(value: str) -> str | None:
    action = parse_action_value(value)
    if action.kind != "swarm":
        return None
    try:
        from kazma_core.swarm.bus import FanOutBusAdapter, get_message_bus
        from kazma_gateway.adapters.slack_bus import SlackBusAdapter

        adapter = get_message_bus().adapter
        if isinstance(adapter, FanOutBusAdapter):
            for child in adapter.adapters:
                if isinstance(child, SlackBusAdapter):
                    return child.handle_callback(action.swarm_data)
            return None
        if isinstance(adapter, SlackBusAdapter):
            return adapter.handle_callback(action.swarm_data)
    except Exception:
        return None
    return None
