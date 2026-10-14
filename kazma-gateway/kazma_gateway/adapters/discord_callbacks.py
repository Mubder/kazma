"""Discord INTERACTION_CREATE routing (shared callback schemes)."""

from __future__ import annotations


from kazma_gateway.adapters.platform_callbacks import CallbackAction, parse_callback_data

__all__ = ["CallbackAction", "parse_custom_id", "route_swarm_bus"]


def parse_custom_id(custom_id: str) -> CallbackAction:
    """Parse Discord component custom_id (same schemes as Telegram)."""
    return parse_callback_data(custom_id or "")


def route_swarm_bus(custom_id: str, *, actor: str = "") -> str | None:
    """If *custom_id* is a swarm approve/reject, decide that request and return
    its id. The decision is the shared row's (``shared_approvals``): no
    adapter instance is needed, and every platform's card shows the outcome."""
    action = parse_custom_id(custom_id)
    if action.kind != "swarm":
        return None
    from kazma_core.swarm.shared_approvals import decide_from_button

    return decide_from_button(action.swarm_data, actor=actor, platform="discord")


def is_install_action(custom_id: str) -> bool:
    action = parse_custom_id(custom_id)
    return action.kind in ("sys_install", "install_dep")


def package_from_install(custom_id: str) -> str:
    return parse_custom_id(custom_id).package_name
