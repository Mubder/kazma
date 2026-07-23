"""Session-scoped HITL tool grants + requires_approval integration."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from kazma_core.safety.hitl import requires_approval, set_current_thread_id, reset_current_thread_id
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


def test_clear_grants(mem_store):
    store, data = mem_store
    with patch("kazma_core.config_store.get_config_store", return_value=store):
        grant_tool("th4", "shell_exec", actor="test")
        grant_tool("th4", "file_write", actor="test")
        n = clear_grants("th4", actor="test")
        assert n == 2
        assert has_tool_grant("th4", "shell_exec") is False
