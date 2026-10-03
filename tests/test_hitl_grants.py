"""Session-scoped HITL tool grants + requires_approval integration."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.safety.hitl import (
    ALWAYS_HITL_TOOLS,
    requires_approval,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.hitl_grants import clear_grants, grant_tool, has_tool_grant


@pytest.fixture()
def mem_store():
    data: dict = {}

    store = MagicMock()

    def _get(key, default=None):
        return data.get(key, default)

    def _set(key, value, category="general"):
        data[key] = value

    def _delete(key):
        data.pop(key, None)

    def _get_category(category):
        # Keys we store don't carry category; return full map for prefix scan.
        return dict(data)

    store.get.side_effect = _get
    store.set.side_effect = _set
    store.delete.side_effect = _delete
    store.get_category.side_effect = _get_category
    return store, data


def test_grant_tool_and_has_grant(mem_store):
    store, data = mem_store
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        st = grant_tool("th1", "shell_exec", actor="test")
        assert st["active"] is True
        assert has_tool_grant("th1", "shell_exec") is True
        assert has_tool_grant("th1", "file_write") is False


def test_grant_expiry(mem_store, monkeypatch):
    store, data = mem_store
    monkeypatch.setenv("KAZMA_HITL_GRANT_TTL_SECONDS", "60")
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        grant_tool("th2", "shell_exec", actor="test")
        data["hitl_grant.th2.shell_exec"]["expires_at"] = time.time() - 1
        assert has_tool_grant("th2", "shell_exec") is False


def test_requires_approval_respects_grant(mem_store):
    store, _ = mem_store
    cfg = {"enabled": True, "require_approval_for": {"shell_exec", "file_write"}}
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        token = set_current_thread_id("th3")
        try:
            assert requires_approval("shell_exec", cfg) is True
            grant_tool("th3", "shell_exec", actor="test")
            assert requires_approval("shell_exec", cfg) is False
            assert requires_approval("file_write", cfg) is True
        finally:
            reset_current_thread_id(token)


def test_grant_does_not_cross_threads(mem_store):
    """Allow-tool on session A must not skip HITL on session B.

    Web session_id == LangGraph thread_id, so a new chat is a new thread.
    Approving (or granting) in one season cannot immunize the next.
    """
    store, _ = mem_store
    cfg = {"enabled": True, "require_approval_for": {"shell_exec", "file_write"}}
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        grant_tool("session-a", "file_write", actor="test")
        assert has_tool_grant("session-a", "file_write") is True
        assert has_tool_grant("session-b", "file_write") is False
        tok = set_current_thread_id("session-b")
        try:
            assert requires_approval("file_write", cfg) is True
        finally:
            reset_current_thread_id(tok)
        tok = set_current_thread_id("session-a")
        try:
            assert requires_approval("file_write", cfg) is False
            assert requires_approval("file_delete", cfg) is True
        finally:
            reset_current_thread_id(tok)


def test_yolo_does_not_cross_threads(mem_store, monkeypatch):
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    from kazma_core.safety.yolo import disable_yolo, enable_yolo

    store, _ = mem_store
    cfg = {"enabled": True, "require_approval_for": {"file_write"}}
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        enable_yolo("session-a", actor="test", force=True)
        try:
            tok = set_current_thread_id("session-b")
            try:
                assert requires_approval("file_write", cfg) is True
            finally:
                reset_current_thread_id(tok)
        finally:
            disable_yolo("session-a", actor="test")


def test_always_hitl_tools_ignore_grant(mem_store, monkeypatch):
    """Public X writes stay gated even if a grant or YOLO is on this thread."""
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    from kazma_core.safety.yolo import disable_yolo, enable_yolo

    store, _ = mem_store
    cfg = {"enabled": True, "require_approval_for": set()}
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        tok = set_current_thread_id("th-always")
        try:
            enable_yolo("th-always", actor="test", force=True)
            for tool in sorted(ALWAYS_HITL_TOOLS):
                grant_tool("th-always", tool, actor="test")
                assert has_tool_grant("th-always", tool) is True
                assert requires_approval(tool, cfg) is True, tool
        finally:
            try:
                disable_yolo("th-always", actor="test")
            except Exception:
                pass
            reset_current_thread_id(tok)


def test_clear_grants(mem_store):
    store, data = mem_store
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        grant_tool("th4", "shell_exec", actor="test")
        grant_tool("th4", "file_write", actor="test")
        n = clear_grants("th4", actor="test")
        assert n == 2
        assert has_tool_grant("th4", "shell_exec") is False


# -- what an "allow for this chat" answer grants ------------------------------


def test_a_batch_card_grants_its_tools_never_its_label():
    """Live 2026-10-03: "allow for this chat" on a card for two tools wrote a
    grant for a tool called "2 tools" -- the card's label, which the page sent
    as the tool to grant."""
    from kazma_core.safety.hitl_grants import _is_tool_batch_label, tool_batch_label, tools_to_grant

    label = tool_batch_label(2)
    assert label == "2 tools" and _is_tool_batch_label(label)
    reads = [{"name": "mcp__filesystem__read_text_file"}, {"name": "mcp__filesystem__read_text_file"}]
    assert tools_to_grant(reads, label, label) == ["mcp__filesystem__read_text_file"]
    # Naming a tool the question did not ask about grants nothing more.
    assert tools_to_grant(reads, label, "shell_exec") == ["mcp__filesystem__read_text_file"]
    # One tool: its own name, named again or not.
    assert tools_to_grant([], "file_write", "file_write") == ["file_write"]
    assert tools_to_grant([], "file_write") == ["file_write"]
    # Nothing known about the question: the named tool, never a label.
    assert tools_to_grant([], "", "file_write") == ["file_write"]
    assert tools_to_grant([], "3 tools", "3 tools") == []
    assert not _is_tool_batch_label("file_write") and not _is_tool_batch_label("tools")


def test_the_old_selection_granted_the_label():
    """Negative control: the approve route's selection before the fix."""

    def old(pending_tools, pending_tool_name, explicit):
        chosen = [str(t["name"]) for t in pending_tools if isinstance(t, dict) and t.get("name")]
        if not pending_tools and pending_tool_name and " tools" not in pending_tool_name:
            chosen.append(pending_tool_name)
        if explicit:
            chosen.append(str(explicit))
        return list(dict.fromkeys(chosen))

    assert "2 tools" in old([{"name": "a"}], "2 tools", "2 tools")


def test_the_gate_and_the_approve_route_share_one_label_and_one_rule():
    """The worker mints the label with tool_batch_label and the approve route
    decides with tools_to_grant: no second copy of either."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    worker = (root / "kazma-core" / "kazma_core" / "agent" / "graph_tool_worker.py").read_text(encoding="utf-8")
    route = (root / "kazma-ui" / "kazma_ui" / "routes_direct" / "misc.py").read_text(encoding="utf-8")
    assert "tool_batch_label(len(danger_tools))" in worker
    assert '} tools"' not in worker
    assert "tools_to_grant(" in route
    assert '" tools" not in' not in route
