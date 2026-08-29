"""Swarm SafetyMiddleware × YOLO interaction (security fix, audit HIGH).

Regression: ``SafetyMiddleware.check()`` used to honor YOLO BEFORE evaluating
danger/ALWAYS status, so ALWAYS_HITL_TOOLS (x_post / x_delete_post) auto-ran
through the LocalToolRegistry path under YOLO. The graph path
(`hitl.requires_approval`) has ALWAYS evaluated ALWAYS-first — this pins the
registry path to the same contract:

  - ALWAYS_HITL_TOOLS are NEVER YOLO-bypassed (bus approval required).
  - CANONICAL danger tools ARE auto-approved while YOLO is active.
"""

from __future__ import annotations

import asyncio

import pytest

from kazma_core.safety.hitl import (
    ALWAYS_HITL_TOOLS,
    CANONICAL_DANGER_TOOLS,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.yolo import disable_yolo, enable_yolo, is_yolo_active
from kazma_core.swarm.safety import SafetyMiddleware

_TID = "thr-swarm-safety-yolo"


@pytest.fixture()
def yolo_thread():
    """Enable session YOLO for _TID and bind it as the current thread."""
    enable_yolo(_TID, actor="test", force=True)
    assert is_yolo_active(_TID)
    token = set_current_thread_id(_TID)
    yield _TID
    reset_current_thread_id(token)
    try:
        disable_yolo(_TID, actor="test")
    except Exception:
        pass


def _run(mw: SafetyMiddleware, tool: str) -> bool:
    return asyncio.run(mw.check(tool))


def test_always_hitl_tools_blocked_under_yolo(yolo_thread) -> None:
    """x_post / x_delete_post must reach the approval gate even under YOLO.

    With no real bus adapter wired, the gate fails closed (blocked), which is
    exactly the observable "not auto-approved" outcome we pin here.
    """
    mw = SafetyMiddleware(allow_headless_danger=False)
    for tool in sorted(ALWAYS_HITL_TOOLS):
        assert tool in CANONICAL_DANGER_TOOLS  # sanity: they are danger-tier too
        assert _run(mw, tool) is False, f"{tool} must not be YOLO-bypassed"


def test_canonical_danger_tools_allowed_under_yolo(yolo_thread) -> None:
    """YOLO still bypasses ordinary CANONICAL danger tools (unchanged behavior)."""
    mw = SafetyMiddleware()
    # `git_push_pull` was removed from CANONICAL in audit F-04 — it is
    # deprecated and never registered, so gating it protected nothing. The
    # tools that replaced it (git_push / git_pull) are gated instead.
    for tool in ("shell_exec", "file_write", "git_push", "schedule_task"):
        assert tool in CANONICAL_DANGER_TOOLS
        assert _run(mw, tool) is True, f"{tool} should be YOLO-auto-approved"


def test_safe_and_sensitive_reads_pass_normally_under_yolo(yolo_thread) -> None:
    mw = SafetyMiddleware()
    assert _run(mw, "file_read") is True
    assert _run(mw, "sqlite_query") is True  # sensitive read — allowed+logged


def test_always_hitl_still_gated_without_yolo() -> None:
    """Control: without YOLO the ALWAYS tools were already gated."""
    from kazma_core.safety.hitl import reset_current_thread_id as _rc

    tok = set_current_thread_id(None)
    try:
        mw = SafetyMiddleware(allow_headless_danger=False)
        for tool in sorted(ALWAYS_HITL_TOOLS):
            assert _run(mw, tool) is False
    finally:
        _rc(tok)


def test_check_sync_has_no_yolo_leak(yolo_thread) -> None:
    """check_sync never had a YOLO shortcut — stays fail-closed for danger."""
    mw = SafetyMiddleware(allow_headless_danger=False)
    assert mw.check_sync("shell_exec") is False
