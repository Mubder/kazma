"""Phase 4.4 platform RBAC unit tests."""

from __future__ import annotations

import pytest

from kazma_core.security.platform_rbac import (
    PlatformUser,
    authenticate_local_user,
    create_local_user,
    role_allows,
)


def test_role_allows_admin_everything():
    assert role_allows("admin", "/api/settings", "GET") is True
    assert role_allows("admin", "/api/approve/x", "POST") is True


def test_role_allows_viewer_read_not_settings():
    assert role_allows("viewer", "/api/chat/stream", "GET") is True
    assert role_allows("viewer", "/api/settings", "GET") is False
    assert role_allows("viewer", "/api/approve/x", "POST") is False


def test_role_allows_operator_chat_not_settings():
    assert role_allows("operator", "/api/chat/stream", "POST") is True
    assert role_allows("operator", "/api/approve/t", "POST") is True
    assert role_allows("operator", "/api/settings", "GET") is False


def test_local_user_roundtrip(tmp_path, monkeypatch):
    # Isolate config store
    monkeypatch.setenv("KAZMA_CONFIG_DB", str(tmp_path / "settings.db"))
    from kazma_core.config_store import reset_config_store

    reset_config_store()
    create_local_user("alice", "s3cret-pass", role="operator")
    user = authenticate_local_user("alice", "s3cret-pass")
    assert user is not None
    assert user.role == "operator"
    assert authenticate_local_user("alice", "wrong") is None
    assert PlatformUser("1", "alice", "operator").has_at_least("viewer") is True
    assert PlatformUser("1", "alice", "operator").has_at_least("admin") is False
