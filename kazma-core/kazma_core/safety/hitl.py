"""Human-in-the-Loop (HITL) tool approval gate.

Classifies tools by risk tier and provides interrupt logic for
LangGraph's HITL mechanism. Config-driven via kazma.yaml:

    safety:
      hitl:
        enabled: true
        require_approval_for: ["file_write", "file_delete", "shell_exec"]
        approval_timeout_seconds: 60
        auto_deny_on_timeout: true
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Default tool tiers ────────────────────────────────────────────────

TOOL_TIERS: dict[str, str] = {
    # Read — always allowed
    "file_read": "read",
    "file_search": "read",
    "file_list": "read",
    "memory_search": "read",
    "sqlite_query": "read",
    "current_datetime": "read",
    # Write — always allowed
    "send_message": "write",
    "memory_store": "write",
    # Danger — require HITL approval
    "file_write": "danger",
    "file_delete": "danger",
    "shell_exec": "danger",
    # Unsafe — always blocked (reserved)
}

DEFAULT_DANGER_TOOLS = ["file_write", "file_delete", "shell_exec", "vault_retrieve", "vault_delete"]


def get_hitl_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Extract HITL config from raw kazma.yaml dict.

    Args:
        raw_config: The full kazma.yaml dict.

    Returns:
        HITL config dict with enabled, require_approval_for, timeout.
    """
    safety = raw_config.get("safety", {})
    hitl = safety.get("hitl", {})
    return {
        "enabled": hitl.get("enabled", True),
        "require_approval_for": set(
            hitl.get("require_approval_for", DEFAULT_DANGER_TOOLS)
        ),
        "approval_timeout_seconds": hitl.get("approval_timeout_seconds", 60),
        "auto_deny_on_timeout": hitl.get("auto_deny_on_timeout", True),
    }


def requires_approval(tool_name: str, hitl_config: dict[str, Any]) -> bool:
    """Check if a tool requires HITL approval.

    Args:
        tool_name:   Name of the tool being called.
        hitl_config: HITL config from get_hitl_config().

    Returns:
        True if the tool requires approval.
    """
    if not hitl_config.get("enabled", True):
        return False
    danger_tools = hitl_config.get("require_approval_for", set())
    return tool_name in danger_tools


def get_tool_tier(tool_name: str) -> str:
    """Get the risk tier for a tool.

    Args:
        tool_name: Name of the tool.

    Returns:
        "read", "write", "danger", "unsafe", or "unknown".
    """
    return TOOL_TIERS.get(tool_name, "unknown")
