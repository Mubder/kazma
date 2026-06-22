"""Tests for PermissionManager — YAML-backed permission management."""

from __future__ import annotations

from pathlib import Path

from kazma_core.permissions import PermissionManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pm(tmp_path: Path) -> PermissionManager:
    """Create a PermissionManager pointing at a temp YAML file."""
    config = tmp_path / "permissions.yaml"
    return PermissionManager(config_path=config)


# ---------------------------------------------------------------------------
# Construction & loading
# ---------------------------------------------------------------------------


class TestPermissionsLoading:
    def test_creates_default_if_missing(self, tmp_path: Path) -> None:
        config = tmp_path / "new.yaml"
        assert not config.exists()
        pm = PermissionManager(config_path=config)
        data = pm.load_permissions()
        assert "users" in data
        assert "default" in data["users"]
        assert config.exists()

    def test_loads_existing_file(self, tmp_path: Path) -> None:
        config = tmp_path / "perms.yaml"
        config.write_text("users:\n  alice:\n    allowed:\n      - read\n    denied:\n      - write\n")
        pm = PermissionManager(config_path=config)
        data = pm.load_permissions()
        assert "alice" in data["users"]
        assert "read" in data["users"]["alice"]["allowed"]


# ---------------------------------------------------------------------------
# is_allowed
# ---------------------------------------------------------------------------


class TestIsAllowed:
    def test_default_user_starts_empty(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        # Default user has no tools
        assert not pm.is_allowed("anything")

    def test_grant_makes_allowed(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("web_search")
        assert pm.is_allowed("web_search")
        assert not pm.is_allowed("shell_exec")

    def test_deny_blocks_even_wildcard(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("*")
        pm.deny("shell_exec")
        assert pm.is_allowed("web_search")
        assert not pm.is_allowed("shell_exec")


# ---------------------------------------------------------------------------
# grant / revoke / deny
# ---------------------------------------------------------------------------


class TestGrantRevokeDeny:
    def test_grant_adds_to_allowed(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("foo")
        assert "foo" in pm.list_allowed()

    def test_grant_idempotent(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("foo")
        pm.grant("foo")
        assert pm.list_allowed().count("foo") == 1

    def test_grant_removes_from_denied(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.deny("foo")
        assert "foo" in pm.list_denied()
        pm.grant("foo")
        assert "foo" not in pm.list_denied()
        assert "foo" in pm.list_allowed()

    def test_revoke_removes_from_allowed(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("foo")
        pm.revoke("foo")
        assert "foo" not in pm.list_allowed()
        assert not pm.is_allowed("foo")

    def test_revoke_wildcard_adds_to_denied(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("*")
        pm.revoke("dangerous")
        assert not pm.is_allowed("dangerous")
        assert "dangerous" in pm.list_denied()

    def test_deny_adds_to_denied_list(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.deny("shell_exec")
        assert "shell_exec" in pm.list_denied()

    def test_deny_removes_from_allowed(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("shell_exec")
        pm.deny("shell_exec")
        assert "shell_exec" not in pm.list_allowed()


# ---------------------------------------------------------------------------
# Multi-user
# ---------------------------------------------------------------------------


class TestMultiUser:
    def test_separate_user_permissions(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("web_search", user="alice")
        pm.grant("shell_exec", user="bob")

        assert pm.is_allowed("web_search", user="alice")
        assert not pm.is_allowed("shell_exec", user="alice")
        assert pm.is_allowed("shell_exec", user="bob")
        assert not pm.is_allowed("web_search", user="bob")

    def test_users_list(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("foo", user="alice")
        pm.grant("bar", user="bob")
        users = pm.users()
        assert "alice" in users
        assert "bob" in users

    def test_default_user(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("foo")  # no user arg = default
        assert pm.is_allowed("foo")
        assert not pm.is_allowed("foo", user="other")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_permissions_survive_reload(self, tmp_path: Path) -> None:
        config = tmp_path / "perms.yaml"
        pm1 = PermissionManager(config_path=config)
        pm1.grant("web_search")
        pm1.deny("shell_exec")

        # Create a new instance pointing at the same file
        pm2 = PermissionManager(config_path=config)
        pm2.load_permissions()
        assert pm2.is_allowed("web_search")
        assert not pm2.is_allowed("shell_exec")
        assert "shell_exec" in pm2.list_denied()


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_tool_name(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("")
        assert pm.is_allowed("")

    def test_unicode_tool_name(self, tmp_path: Path) -> None:
        pm = _make_pm(tmp_path)
        pm.grant("arabic_tool_قراءة")
        assert pm.is_allowed("arabic_tool_قراءة")

    def test_auto_load_on_first_access(self, tmp_path: Path) -> None:
        """is_allowed should trigger auto-load if permissions aren't loaded yet."""
        pm = _make_pm(tmp_path)
        pm._data = None  # force unloaded state
        assert not pm.is_allowed("foo")  # should auto-load and return False
