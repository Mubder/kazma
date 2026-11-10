"""A sign-in costs one PBKDF2 whatever the username.

Security audit 2026-11-10. ``authenticate_local_user`` returned at once for
an unknown or a disabled username and ran PBKDF2 at 600,000 iterations for a
known one, so the time of a refusal said which usernames exist. Measured by
counting PBKDF2 calls, not wall time.
"""

from __future__ import annotations

import pytest
from kazma_core.security import platform_rbac


@pytest.fixture()
def counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    platform_rbac.create_local_user("timing-known", "the-right-password", role="operator")
    platform_rbac.create_local_user("timing-off", "the-right-password", role="operator")
    users = platform_rbac._load_users_from_store()
    for u in users:
        if u["username"] == "timing-off":
            u["enabled"] = False
    platform_rbac._save_users_to_store(users)

    calls: list[int] = []
    real = platform_rbac._pbkdf2_hex

    def _count(password: str, salt: str, iterations: int) -> str:
        calls.append(iterations)
        return real(password, salt, iterations)

    monkeypatch.setattr(platform_rbac, "_pbkdf2_hex", _count)
    return calls


@pytest.mark.parametrize("username,password,ok", [
    ("timing-known", "the-right-password", True),
    ("timing-known", "wrong", False),
    ("timing-off", "the-right-password", False),
    ("nobody-by-this-name", "wrong", False),
])
def test_every_answer_costs_one_full_verification(counted, username, password, ok) -> None:
    result = platform_rbac.authenticate_local_user(username, password)
    assert (result is not None) is ok
    assert counted == [platform_rbac._PBKDF2_ITERATIONS]


def test_the_dummy_hash_matches_no_password(counted) -> None:
    for guess in ("", "0", "kazma-no-such-user", "0" * 64):
        assert not platform_rbac._verify_password(guess, platform_rbac._DUMMY_PASSWORD_HASH)


def test_control_without_the_dummy_an_unknown_name_cost_nothing(counted, monkeypatch) -> None:
    """Negative control: with no hash to verify against (the old early
    return), an unknown name answers without a single PBKDF2."""
    monkeypatch.setattr(platform_rbac, "_DUMMY_PASSWORD_HASH", "")
    assert platform_rbac.authenticate_local_user("nobody-by-this-name", "wrong") is None
    assert counted == []
