"""Human-in-the-Loop (HITL) tool approval gate.

Classifies tools by risk tier and provides interrupt logic for
LangGraph's HITL mechanism. Config-driven via kazma.yaml:

    safety:
      hitl:
        enabled: true
        require_approval_for: ["file_write", "file_delete", "shell_exec"]
        approval_timeout_seconds: 300
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
    "DEFAULT_APPROVAL_TIMEOUT_SECONDS",
    "DEFAULT_DANGER_TOOLS",
    "TOOL_TIERS",
    "approval_deadline_from",
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

# Default auto-deny window. 60s minted a deny-retry card storm when the
# operator stepped away (gateway hitl.py burst suppressor). 5 minutes is
# still fail-closed; Settings clamps 10–600.
DEFAULT_APPROVAL_TIMEOUT_SECONDS = 300

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
    # (`send_message` moved to danger in the F-04 block below — it dispatches
    # to Telegram/Discord/Slack, which is an outbound side effect.)
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
    "edit_scheduled": "danger",
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
    "x_schedule_post": "danger",
    "x_cancel_scheduled_post": "danger",
    "x_list_scheduled": "read",
    "x_status": "read",
    # ── Audit F-04: tiers for every remaining registered tool ─────────────
    # `requires_approval` now default-denies anything it cannot classify, so
    # a tool missing from this map is gated rather than silently exempt. That
    # makes the map exhaustive by construction — `test_tool_tier_coverage`
    # fails if a newly registered tool is not listed here.
    #
    # Danger — destructive, externally visible, or credential-touching.
    "file_append": "danger",        # same blast radius as file_write
    "git_push": "danger",           # publishes to a remote
    "git_pull": "danger",           # rewrites the working tree
    "git_merge": "danger",
    "git_checkout": "danger",
    "github_create_issue": "danger",   # public write
    "github_comment_issue": "danger",  # public write
    "send_message": "danger",       # outbound to Telegram/Discord/Slack
    "send_file": "danger",          # outbound attachment
    "dispatch_notification": "danger",
    "vault_store": "danger",        # writes a credential
    "memory_admin": "danger",
    "memory_delete_entity": "danger",
    "memory_invalidate": "danger",
    "memory_merge_entities": "danger",
    "memory_purge_empty_entities": "danger",
    "create_event": "danger",       # external calendar write
    "update_event": "danger",
    "delete_event": "danger",
    "browser_click": "danger",      # drives a real, logged-in browser session
    "browser_fill_form": "danger",
    "browser_navigate": "danger",
    "document_redact": "danger",    # destructive, irreversible
    "pdf_redact": "danger",
    "pdf_fill_form": "danger",
    "document_cancel": "danger",
    #
    # Write — internal, reversible state changes. No approval required.
    "update_scratchpad": "write",
    "save_proposal": "write",       # durable draft persistence (S1-3) — makes posting SAFE
    "task_ledger_update": "write",
    "memory_link_entities": "write",
    "knowledge_create_library": "write",
    "knowledge_ingest_url": "write",
    "knowledge_ingest_site": "write",
    "document_import": "write",
    "document_index": "write",
    "document_convert": "write",
    "convert_document": "write",
    "generate_docx": "write",
    "generate_pdf": "write",
    "generate_pptx": "write",
    "generate_xlsx": "write",
    "generate_markdown_doc": "write",
    "generate_image": "write",
    "generate_ui_mockup": "write",
    "pdf_merge": "write",
    "pdf_split": "write",
    "ocr_document": "write",
    "read_url_to_file": "write",
    "export_session": "write",
    "format_code": "write",
    "activate_skill": "write",
    "browser_screenshot": "write",
    # Delegation runs isolated sub-agent graphs whose own tool calls are gated
    # independently; gating the spawn itself timed out research at the 60s
    # auto-deny (pre-existing decision, preserved).
    "spawn_agent": "write",
    "spawn_agents": "write",
    "dispatch_swarm": "write",
    "run_research_pipeline": "write",
    "start_deep_research": "write",
    "digest_research_file": "write",
    "summarize_research_file": "write",
    "synthesize_from_digests": "write",
    "plan_research_queries": "write",
    "send_approval_request": "write",  # this IS the approval channel
    "mcp_test_server": "write",
    #
    # Read — no side effects beyond caches and outbound GETs.
    "list_scheduled": "read",
    "browser_extract_text": "read",
    "find_free_slots": "read",
    "list_events": "read",
    "lint_code": "read",
    "run_unit_tests": "read",
    "execute_db_query": "read",     # SELECT/WITH only, enforced in the tool
    "inspect_db_schema": "read",
    "context_info": "read",
    "get_system_stats": "read",
    "list_active_processes": "read",
    "read_system_logs": "read",
    "document_read": "read",
    "document_search": "read",
    "document_status": "read",
    "read_document": "read",
    "parse_document": "read",
    "pdf_info": "read",
    "git_status": "read",
    "github_list_issues": "read",
    "knowledge_list_libraries": "read",
    "knowledge_search": "read",
    "mcp_get_prompt": "read",
    "mcp_list_prompts": "read",
    "mcp_list_resources": "read",
    "mcp_read_resource": "read",
    "analyze_image": "read",
    "analyze_local_image": "read",
    "memory_list_beliefs": "read",
    "memory_list_entities": "read",
    "arabic_translate": "read",
    "hijri_convert": "read",
    "insert_diacritics": "read",
    "critique_synthesis_gaps": "read",
    "list_research_papers": "read",
    "list_research_chunks": "read",
    "read_research_chunk": "read",
    "research_readiness": "read",
    "crawl_site": "read",
    "crawl_page": "read",
    "read_url": "read",
    "web_search": "read",
    "web_search_duckduckgo": "read",
    "vault_list": "read",           # names + metadata only, never values
    "list_agent_skills": "read",
    "search_agent_skills": "read",
    "check_swarm_task": "read",
    "check_environment": "read",
    "config_read": "read",
    # Unsafe — always blocked (reserved)
}

#: Tiers that execute without human approval. Everything else — including any
#: tool absent from :data:`TOOL_TIERS` — requires it (audit F-04).
AUTO_APPROVED_TIERS: frozenset[str] = frozenset({"read", "write", "safe"})

# Single source of truth for graph interrupt + swarm bus danger tiers.
# Keep in sync with kazma.yaml safety.hitl.require_approval_for (parity test).
CANONICAL_DANGER_TOOLS: tuple[str, ...] = (
    "file_write",
    "file_apply_patch",
    "file_delete",
    "file_append",
    "shell_exec",
    "code_exec",
    "python_exec",
    "schedule_task",
    "cancel_scheduled",
    "edit_scheduled",
    "vault_retrieve",
    "vault_delete",
    "config_save",
    "run_tests",
    "git_commit",
    # `git_push_pull` is deprecated and deliberately absent from the skill
    # manifest, so it is not registered and gating it protected nothing. The
    # tools that replaced it are gated here instead (audit F-04).
    "git_push",
    "git_pull",
    "git_merge",
    "git_checkout",
    "github_create_pr",
    "github_merge_pr",
    "github_create_issue",
    "github_comment_issue",
    "send_message",
    "send_file",
    "dispatch_notification",
    "vault_store",
    "memory_admin",
    "memory_delete_entity",
    "memory_invalidate",
    "memory_merge_entities",
    "memory_purge_empty_entities",
    "create_event",
    "update_event",
    "delete_event",
    "browser_click",
    "browser_fill_form",
    "browser_navigate",
    "document_redact",
    "pdf_redact",
    "pdf_fill_form",
    "document_cancel",
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
    "x_schedule_post",
    "x_cancel_scheduled_post",
)

# Public posts cannot be YOLO-skipped, granted, or run with HITL disabled.
# X ToU / automation rules require a human to approve each outbound tweet.
# Scheduling/cancelling a public post is an outbound X write too, so it is
# gated the same way (approve once at booking).
ALWAYS_HITL_TOOLS: frozenset[str] = frozenset({
    "x_post",
    "x_delete_post",
    "x_schedule_post",
    "x_cancel_scheduled_post",
})

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
    approval_timeout_seconds = hitl.get(
        "approval_timeout_seconds", DEFAULT_APPROVAL_TIMEOUT_SECONDS
    )
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
    # CANONICAL is capped back up.
    #
    # Largely superseded by audit F-04: `requires_approval` now default-denies
    # on the TOOL_TIERS classification, so a canonical tool narrowed out of
    # this list is still gated by its `danger` tier. The flag remains useful
    # because it puts the tools in the *effective list* itself, which is what
    # the graph interrupt and swarm bus read directly.
    try:
        _floor_raw = os.environ.get("KAZMA_HITL_CANONICAL_FLOOR", "").strip().lower()
        _floor_on = _floor_raw in ("1", "true", "yes", "on")
        _floor_off = _floor_raw in ("0", "false", "no", "off")
        if not _floor_on and not _floor_off:
            try:
                from kazma_core.tenant_isolation import multi_user_or_production

                _floor_on = bool(multi_user_or_production())
            except Exception:
                _floor_on = False
        if _floor_on:
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
                # Informational since audit F-04: `requires_approval` now
                # default-denies on the TOOL_TIERS classification, so a
                # canonical tool missing from the effective list is still
                # gated by its `danger` tier. This reports config drift worth
                # correcting, not an open door.
                logger.info(
                    "[Safety] HITL config drift — canonical tools absent from "
                    "the effective require_approval_for list=%s; extra "
                    "configured tools=%s. These are still gated by their "
                    "TOOL_TIERS tier (audit F-04); re-sync kazma.yaml to "
                    "silence this.",
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


def approval_deadline_from(created_at: float | None = None) -> float | None:
    """Epoch seconds when an unattended approval auto-denies (watchdog).

    Stamp onto approval payloads/cards so surfaces can show a countdown
    instead of a silent 300s drop (2026-09-02). ``created_at`` is when the
    pause was minted (defaults to now — the SSE interrupt scan calls it at
    the pause moment; registry-derived callers pass the gate row's
    ``created_at``). Returns None when the timeout is disabled (<= 0) or
    the config cannot be read — a card without a deadline simply shows no
    countdown, never a wrong one.
    """
    try:
        cfg = get_hitl_config()
        timeout_s = float(cfg.get("approval_timeout_seconds", 300) or 0)
    except Exception:
        return None
    if timeout_s <= 0:
        return None
    base = created_at if created_at is not None else time.time()
    return base + timeout_s


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
    if tool_name in danger_tools:
        return True

    # Default-deny (audit F-04). This used to end at `tool_name in
    # danger_tools`, so any tool nobody remembered to add ran unapproved —
    # 125 of 153 registered tools, including `file_append` while `file_write`
    # was gated. Approval is now the default and exemption is explicit, which
    # matches how the HTTP layer treats new /api routes.
    tier = get_tool_tier(tool_name)
    if tier in AUTO_APPROVED_TIERS:
        return False
    logger.info(
        "[HITL] %r has no tier — requiring approval (add it to TOOL_TIERS)",
        tool_name,
    )
    return True


def get_tool_tier(tool_name: str) -> str:
    """Get the risk tier for a tool.

    Args:
        tool_name: Name of the tool.

    Returns:
        "read", "write", "danger", "unsafe", or "unknown".
    """
    return TOOL_TIERS.get(tool_name, "unknown")
