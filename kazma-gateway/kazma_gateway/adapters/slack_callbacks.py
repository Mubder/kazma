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


def route_swarm_bus(value: str, *, actor: str = "") -> str | None:
    """If *value* is a swarm approve/reject, decide that request and return
    its id. The decision is the shared row's (``shared_approvals``): no
    adapter instance is needed, and every platform's card shows the outcome."""
    action = parse_action_value(value)
    if action.kind != "swarm":
        return None
    from kazma_core.swarm.shared_approvals import decide_from_button

    return decide_from_button(action.swarm_data, actor=actor, platform="slack")
