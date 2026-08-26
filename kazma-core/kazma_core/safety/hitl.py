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
import os
import time
from typing import Any

__all__ = [
    "ALWAYS_HITL_TOOLS",
    "CANONICAL_DANGER_TOOLS",
    "DEFAULT_DANGER_TOOLS",
    "TOOL_TIERS",
    "get_hitl_config",
    "get_tool_tier",
    "requires_approval",
    "set_current_thread_id",
    "reset_current_thread_id",
    "get_current_thread_id",
    "set_current_tenant_id",
    "reset_current_tenant_id",
    "get_current_tenant_id",
]

logger = logging.getLogger(__name__)

# HITL/CANONICAL drift warning cooldown: the warning repeats at this cadence
# so a narrowed danger list keeps nagging the log instead of scrolling away
# once per process (deep-audit 2026-08-19, finding #10).
_DRIFT_WARN_COOLDOWN_S = 900.0  # 15 minutes
_DRIFT_WARN_LAST: list[float] = [0.0]

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


# Thread-safe context var for the active tenant_id (memory isolation).
# Mirrors the _current_thread_id pattern — set in the tool worker / SSE / WS
# so stateless memory tools (memory_search / memory_store) can read it.
_current_tenant_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_tenant_id", default="default"
)


def set_current_tenant_id(tenant_id: str) -> contextvars.Token[str]:
    """Set the active tenant_id for the current async task context."""
    return _current_tenant_id.set(tenant_id)


def reset_current_tenant_id(token: contextvars.Token[str]) -> None:
    """Reset the tenant_id context to its previous state."""
    _current_tenant_id.reset(token)


def get_current_tenant_id() -> str:
    """Get the active tenant_id for the current context (default 'default')."""
    return _current_tenant_id.get()


# ── Default tool tiers ────────────────────────────────────────────────

TOOL_TIERS: dict[str, str] = {
    # Read — always allowed
    "file_read": "read",
    "file_search": "read",
    "file_list": "read",
    "codebase_search": "read",
    "codebase_status": "read",
    "memory_search": "read",
    "sqlite_query": "read",
    "current_datetime": "read",
    # Write — always allowed
    "send_message": "write",
    "memory_store": "write",
    # Danger — require HITL approval
    "file_write": "danger",
    "file_apply_patch": "danger",
    "file_delete": "danger",
    "shell_exec": "danger",
    "code_exec": "danger",
    "python_exec": "danger",
    "schedule_task": "danger",
    "cancel_scheduled": "danger",
    "vault_retrieve": "danger",
    "vault_delete": "danger",
    "config_save": "danger",
    "run_tests": "danger",
    "git_commit": "danger",
    "git_push_pull": "danger",
    "github_create_pr": "danger",
    "github_merge_pr": "danger",
    "install_python_packages": "danger",
    "install_npm_packages": "danger",
    "install_agent_skill": "danger",
    "uninstall_agent_skill": "danger",
    # spawn_agent/spawn_agents are delegation tools, not destructive —
    # they run isolated sub-agent graphs. Remove from danger so they
    # don't time out on HITL approval (60s auto-deny killed research).
    "browser_eval_js": "danger",
    "computer_use": "danger",
    "request_path_access": "danger",
    "email_list": "safe",
    "email_get": "safe",
    "email_analyze": "safe",
    "email_send": "danger",
    "email_delete": "danger",
    "email_categorize": "danger",
    "x_post": "danger",
    "x_delete_post": "danger",
    "x_status": "read",
    # Unsafe — always blocked (reserved)
}

# Single source of truth for graph interrupt + swarm bus danger tiers.
# Keep in sync with kazma.yaml safety.hitl.require_approval_for (parity test).
CANONICAL_DANGER_TOOLS: tuple[str, ...] = (
    "file_write",
    "file_apply_patch",
    "file_delete",
    "shell_exec",
    "code_exec",
    "python_exec",
    "schedule_task",
    "cancel_scheduled",
    "vault_retrieve",
    "vault_delete",
    "config_save",
    "run_tests",
    "git_commit",
    "git_push_pull",
    "github_create_pr",
    "github_merge_pr",
    "install_python_packages",
    "install_npm_packages",
    "install_agent_skill",
    "uninstall_agent_skill",
    "email_send",
    "email_delete",
    "email_categorize",
    "browser_eval_js",
    "computer_use",
    # Path grants expand the FS allowlist — always require human approval.
    "request_path_access",
    # Official X API writes — also in ALWAYS_HITL_TOOLS (YOLO cannot skip).
    "x_post",
    "x_delete_post",
)

# Public posts cannot be YOLO-skipped, granted, or run with HITL disabled.
# X ToU / automation rules require a human to approve each outbound tweet.
ALWAYS_HITL_TOOLS: frozenset[str] = frozenset({"x_post", "x_delete_post"})

# Backward-compatible alias used throughout the codebase / tests.
DEFAULT_DANGER_TOOLS: list[str] = list(CANONICAL_DANGER_TOOLS)


def get_hitl_config(raw_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract HITL config from raw kazma.yaml dict.

    Also checks ConfigStore for runtime overrides (set by the Settings UI).
    ConfigStore keys use the flat ``safety.hitl_enabled`` convention
    (matching SettingsManager), while YAML uses the nested
    ``safety.hitl.enabled`` convention.

    Args:
        raw_config: The full kazma.yaml dict (optional; auto-loads merged YAML if omitted/empty).

    Returns:
        HITL config dict with enabled, require_approval_for, timeout.
    """
    if not raw_config:
        try:
            from kazma_core.config_loader import load_merged_yaml

            raw_config = load_merged_yaml()
        except Exception:
            raw_config = {}

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
        except Exception as exc:
            logger.warning("YOLO check failed in get_hitl_config: %s", exc)

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
        # Settings UI "require_approval_for" → flat key safety.require_approval_for
        # Without this, the Settings danger list is a dead control plane.
        cs_require = cs.get("safety.require_approval_for")
        if cs_require is not None:
            if isinstance(cs_require, str):
                parts = [p.strip() for p in cs_require.split(",") if p.strip()]
                if parts:
                    require_approval_for = set(parts)
            elif isinstance(cs_require, (list, tuple, set)):
                cleaned = {str(x).strip() for x in cs_require if str(x).strip()}
                if cleaned:
                    require_approval_for = cleaned
    except Exception as exc:
        logger.error("ConfigStore overrides read failed in get_hitl_config — using yaml defaults: %s", exc)

    # Optional canonical floor (deep-audit 2026-08-19, finding #10): when
    # KAZMA_HITL_CANONICAL_FLOOR is set, canonical danger tools can never be
    # narrowed out of the effective list — Settings/YAML narrowing below
    # CANONICAL is capped back up. Opt-in so existing single-operator
    # installs that deliberately narrowed the list keep working; strict
    # multi-operator deployments should set it.
    try:
        if os.environ.get("KAZMA_HITL_CANONICAL_FLOOR", "").strip().lower() in (
            "1", "true", "yes", "on",
        ):
            require_approval_for = set(require_approval_for or []) | set(
                CANONICAL_DANGER_TOOLS
            )
    except Exception:
        logger.debug("[Safety] KAZMA_HITL_CANONICAL_FLOOR check failed", exc_info=True)

    # Phase 0 instrumentation (Commitment Layer §5.1): surface drift between
    # the effective require_approval_for list and CANONICAL_DANGER_TOOLS.
    # The YAML list ships IDENTICAL to CANONICAL (set-parity tested); the
    # effective list may still be narrowed via Settings — this makes the
    # drift visible so danger tools missing from it (which would silently
    # skip approval) are caught. Cooldown-based repeat, not one-shot
    # (deep-audit 2026-08-19, finding #10). Diagnostic; enforcement is the
    # KAZMA_HITL_CANONICAL_FLOOR flag above.
    try:
        _now = time.monotonic()
        if _now - _DRIFT_WARN_LAST[0] >= _DRIFT_WARN_COOLDOWN_S:
            _canonical = set(CANONICAL_DANGER_TOOLS)
            _effective = set(require_approval_for or [])
            _canonical_only = _canonical - _effective
            _effective_only = _effective - _canonical
            if _canonical_only or _effective_only:
                logger.warning(
                    "[Safety] HITL/CANONICAL drift — canonical_only (danger "
                    "tools NOT requiring approval under the effective list)=%s; "
                    "effective_only (configured, not in canonical)=%s; set "
                    "KAZMA_HITL_CANONICAL_FLOOR=1 to enforce the canonical floor",
                    sorted(_canonical_only), sorted(_effective_only),
                )
            _DRIFT_WARN_LAST[0] = _now
    except Exception:
        logger.debug("[Safety] HITL/CANONICAL drift check failed", exc_info=True)

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
    if tool_name in ALWAYS_HITL_TOOLS:
        return True

    tid = get_current_thread_id()
    if tid:
        try:
            from kazma_core.safety.yolo import is_yolo_active

            if is_yolo_active(tid):
                return False
        except Exception as exc:
            logger.warning("YOLO check failed in requires_approval: %s", exc)
        try:
            from kazma_core.safety.task_grants import has_task_grant

            if has_task_grant(tid):
                return False
        except Exception as exc:
            logger.debug("Task grant check failed in requires_approval: %s", exc)
        try:
            from kazma_core.safety.hitl_grants import has_tool_grant

            if has_tool_grant(tid, tool_name):
                return False
        except Exception as exc:
            logger.warning("Tool grant check failed in requires_approval: %s", exc)

    if not hitl_config.get("enabled", True):
        return False

    if tool_name.startswith("mcp__"):
        try:
            from kazma_core.mcp.manager import classify_mcp_tool

            return classify_mcp_tool(tool_name) != "safe"
        except Exception as exc:
            # Fail-closed: if we cannot classify an MCP tool, require
            # approval rather than silently letting it through.
            logger.warning(
                "MCP tool classification failed for %r — requiring approval: %s",
                tool_name, exc,
            )
            return True

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
