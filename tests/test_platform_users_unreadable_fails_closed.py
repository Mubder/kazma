"""An unreadable platform-user list is not "no users" (audit 2026-10-26
AUD-123).

``_load_users_from_store`` swallowed a settings-store error and returned
``[]``. "No users" is single-operator mode, so during a store outage
``multi_user_enabled()`` said False and ``tenant_isolation.
multi_user_or_production`` -- written to fail closed "on RBAC store error"
(audit C2b) -- never saw an error: its fail-closed branch could not run. And
``create_local_user`` read the list as empty and saved it back with only the
new user. ``_load_users_from_store`` now raises; only sign-in and the user
listing keep the empty answer (``_users_for_sign_in``: no local user
authenticates).
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.security import platform_rbac


class _BrokenStore:
    def __init__(self) -> None:
        self.saved: list[Any] = []

    def get(self, key: str, default: Any = None) -> Any:
        raise RuntimeError("database is away")

    def set(self, key: str, value: Any, category: str = "general") -> None:
        self.saved.append((key, value))


@pytest.fixture
def broken(monkeypatch) -> _BrokenStore:
    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    store = _BrokenStore()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: store)
    return store


def test_the_multi_user_check_raises_instead_of_answering_no(broken) -> None:
    with pytest.raises(platform_rbac.PlatformUsersUnavailable):
        platform_rbac.multi_user_enabled()


def test_tenant_isolation_fails_closed_on_the_store_error(broken) -> None:
    from kazma_core.security.single_operator import is_single_operator
    from kazma_core.tenant_isolation import client_tenant_spoof_allowed, multi_user_or_production

    assert multi_user_or_production() is True
    assert client_tenant_spoof_allowed() is False
    assert is_single_operator() is False


def test_creating_a_user_does_not_overwrite_the_list(broken) -> None:
    with pytest.raises(platform_rbac.PlatformUsersUnavailable):
        platform_rbac.create_local_user("newcomer", "correct horse battery")
    assert broken.saved == []


def test_sign_in_still_finds_no_user(broken) -> None:
    assert platform_rbac.authenticate_local_user("owner", "whatever-password") is None


def test_the_swallowing_reader_said_no_users(broken) -> None:
    """Negative control: the swallowing read the decisions used (kept for a
    sign-in, as ``_users_for_sign_in``) answers ``[]`` -- the
    single-operator answer -- over the same broken store."""
    assert platform_rbac._users_for_sign_in() == []
    with pytest.raises(platform_rbac.PlatformUsersUnavailable):
        platform_rbac._load_users_from_store()


def test_a_readable_empty_list_is_still_single_operator(monkeypatch) -> None:
    monkeypatch.delenv("KAZMA_MULTI_USER", raising=False)

    class _Store:
        def get(self, key: str, default: Any = None) -> Any:
            return default

    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: _Store())
    assert platform_rbac.multi_user_enabled() is False
