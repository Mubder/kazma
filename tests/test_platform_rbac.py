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


def test_new_hash_uses_aligned_iterations_and_encoded_format(tmp_path, monkeypatch):
    """2026-09-02 audit: login-password PBKDF2 must match the vault's 600k
    and encode its iteration count so a future bump cannot orphan hashes."""
    monkeypatch.setenv("KAZMA_CONFIG_DB", str(tmp_path / "settings.db"))
    from kazma_core.config_store import reset_config_store
    from kazma_core.security.platform_rbac import (
        _PBKDF2_ITERATIONS,
        _hash_password,
        _verify_password,
    )

    reset_config_store()
    assert _PBKDF2_ITERATIONS == 600_000
    stored = _hash_password("s3cret-pass")
    parts = stored.split("$")
    assert len(parts) == 4 and parts[0] == "pbkdf2_sha256"
    assert int(parts[1]) == 600_000
    assert _verify_password("s3cret-pass", stored) is True
    assert _verify_password("wrong", stored) is False


def test_legacy_200k_hash_verifies_then_upgrades_on_login(tmp_path, monkeypatch):
    """A pre-alignment 3-field hash (implicit 200k) must still authenticate
    and be silently re-hashed at 600k on the next successful login."""
    import hashlib as _hl
    import secrets as _secrets

    monkeypatch.setenv("KAZMA_CONFIG_DB", str(tmp_path / "settings.db"))
    from kazma_core.config_store import get_config_store, reset_config_store
    from kazma_core.security.platform_rbac import (
        _PBKDF2_LEGACY_ITERATIONS,
        _load_users_from_store,
        authenticate_local_user,
        create_local_user,
    )

    reset_config_store()
    create_local_user("bob", "legacy-pass", role="viewer")
    # Overwrite with a legacy-format hash exactly as the old code wrote it.
    salt = _secrets.token_hex(16)
    dk = _hl.pbkdf2_hmac(
        "sha256", b"legacy-pass", salt.encode("utf-8"),
        _PBKDF2_LEGACY_ITERATIONS,
    )
    legacy = f"pbkdf2_sha256${salt}${dk.hex()}"
    store = get_config_store()
    users = _load_users_from_store()
    for u in users:
        if str(u.get("username", "")).lower() == "bob":
            u["password_hash"] = legacy
    store.set("platform.users", users)

    # Legacy hash still authenticates…
    user = authenticate_local_user("bob", "legacy-pass")
    assert user is not None and user.role == "viewer"
    assert authenticate_local_user("bob", "wrong") is None
    # …and the store now holds the upgraded 4-field 600k format.
    upgraded = next(
        u["password_hash"]
        for u in _load_users_from_store()
        if str(u.get("username", "")).lower() == "bob"
    )
    parts = upgraded.split("$")
    assert len(parts) == 4 and int(parts[1]) == 600_000
    # Still authenticating after the upgrade.
    assert authenticate_local_user("bob", "legacy-pass") is not None
