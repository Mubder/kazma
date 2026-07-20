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

import contextvars
import logging
from typing import Any

__all__ = [
    "CANONICAL_DANGER_TOOLS",
    "DEFAULT_DANGER_TOOLS",
    "TOOL_TIERS",
    "get_hitl_config",
    "get_tool_tier",
    "requires_approval",
    "set_current_thread_id",
    "reset_current_thread_id",
    "get_current_thread_id",
]

logger = logging.getLogger(__name__)

# Thread-safe context var for tracking active session/thread_id
_current_thread_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("_current_thread_id", default=None)


def set_current_thread_id(thread_id: str | None) -> contextvars.Token[str | None]:
    """Set the active thread ID for the current async task context."""
    return _current_thread_id.set(thread_id)


def reset_current_thread_id(token: contextvars.Token[str | None]) -> None:
    """Reset the thread ID context to its previous state."""
    _current_thread_id.reset(token)


def get_current_thread_id() -> str | None:
    """Get the active thread ID for the current context."""
    return _current_thread_id.get()


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
    "code_exec": "danger",
    "python_exec": "danger",
    "spawn_agent": "danger",
    "spawn_agents": "danger",
    "schedule_task": "danger",
    "cancel_scheduled": "danger",
    "vault_retrieve": "danger",
    "vault_delete": "danger",
    "config_save": "danger",
    "run_tests": "danger",
    "git_commit": "danger",
    "git_push_pull": "danger",
    "github_create_pr": "danger",
    "install_python_packages": "danger",
    "install_npm_packages": "danger",
    "install_agent_skill": "danger",
    "uninstall_agent_skill": "danger",
    # Unsafe — always blocked (reserved)
}

# Single source of truth for graph interrupt + swarm bus danger tiers.
# Keep in sync with kazma.yaml safety.hitl.require_approval_for (parity test).
CANONICAL_DANGER_TOOLS: tuple[str, ...] = (
    "file_write",
    "file_delete",
    "shell_exec",
    "code_exec",
    "python_exec",
    "spawn_agent",
    "spawn_agents",
    "schedule_task",
    "cancel_scheduled",
    "vault_retrieve",
    "vault_delete",
    "config_save",
    "run_tests",
    "git_commit",
    "git_push_pull",
    "github_create_pr",
    "install_python_packages",
    "install_npm_packages",
    "install_agent_skill",
    "uninstall_agent_skill",
)

# Backward-compatible alias used throughout the codebase / tests.
DEFAULT_DANGER_TOOLS: list[str] = list(CANONICAL_DANGER_TOOLS)


def get_hitl_config(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Extract HITL config from raw kazma.yaml dict.

    Also checks ConfigStore for runtime overrides (set by the Settings UI).
    ConfigStore keys use the flat ``safety.hitl_enabled`` convention
    (matching SettingsManager), while YAML uses the nested
    ``safety.hitl.enabled`` convention.

    Args:
        raw_config: The full kazma.yaml dict.

    Returns:
        HITL config dict with enabled, require_approval_for, timeout.
    """
    safety = raw_config.get("safety", {})
    hitl = safety.get("hitl", {})

    enabled = hitl.get("enabled", True)
    require_approval_for = set(
        hitl.get("require_approval_for", DEFAULT_DANGER_TOOLS)
    )
    approval_timeout_seconds = hitl.get("approval_timeout_seconds", 60)
    auto_deny_on_timeout = hitl.get("auto_deny_on_timeout", True)

    # Check YOLO mode for thread ID override
    tid = get_current_thread_id()
    if tid:
        try:
            from kazma_core.safety.yolo import is_yolo_active

            if is_yolo_active(tid):
                enabled = False
        except Exception:
            pass

    # Apply ConfigStore overrides (set by SettingsManager.save_safety_settings)
    try:
        from kazma_core.config_store import get_config_store

        cs = get_config_store()
        # SettingsManager uses "safety.hitl_enabled" (flat key)
        cs_enabled = cs.get("safety.hitl_enabled")
        if cs_enabled is not None:
            # Only override if YOLO hasn't already forced it to False
            if enabled:
                enabled = bool(cs_enabled)
        cs_timeout = cs.get("safety.approval_timeout")
        if cs_timeout is not None:
            approval_timeout_seconds = int(cs_timeout)
        cs_auto_deny = cs.get("safety.auto_deny_on_timeout")
        if cs_auto_deny is not None:
            auto_deny_on_timeout = bool(cs_auto_deny)
    except Exception:
        pass

    return {
        "enabled": enabled,
        "require_approval_for": require_approval_for,
        "approval_timeout_seconds": approval_timeout_seconds,
        "auto_deny_on_timeout": auto_deny_on_timeout,
    }


def requires_approval(tool_name: str, hitl_config: dict[str, Any]) -> bool:
    """Check if a tool requires HITL approval.

    Args:
        tool_name:   Name of the tool being called.
        hitl_config: HITL config from get_hitl_config().

    Returns:
        True if the tool requires approval.
    """
    tid = get_current_thread_id()
    if tid:
        try:
            from kazma_core.safety.yolo import is_yolo_active

            if is_yolo_active(tid):
                return False
        except Exception:
            pass

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
