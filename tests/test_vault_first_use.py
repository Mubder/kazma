"""The vault is one vault from its first use, and a failed start is retried (FA-103).

The 2026-11 functional audit: ``get_vault()`` set "attempted" before it built
the vault and took no lock. A second caller that arrived while the first was
still deriving the key (600,000 PBKDF2 rounds) got None, and ConfigStore then
kept the secret it was saving in plaintext; one transient failure (a locked
``vault.db``) left the process with no vault until a restart.

Concurrent first use returns one vault in every thread; a transient failure
is tried again after its backoff; a missing key is decided once. The negative
control is the old function.
"""

from __future__ import annotations

import threading
import time

import pytest

from kazma_core.security import vault as vault_mod


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("KAZMA_VAULT_KEY", "unit-test-vault-key-32bytes-long!!")
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    vault_mod.reset_vault()
    yield
    vault_mod.reset_vault()


class _SlowVault:
    built = 0
    lock = threading.Lock()

    def __init__(self) -> None:
        with _SlowVault.lock:
            _SlowVault.built += 1
        time.sleep(0.2)  # the key derivation

    def close(self) -> None:
        pass


def _at_once(n: int = 3) -> list:
    out: list = [None] * n
    gate = threading.Barrier(n)

    def one(i: int) -> None:
        gate.wait()
        out[i] = vault_mod.get_vault()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return out


def test_concurrent_first_use_returns_one_vault(monkeypatch):
    _SlowVault.built = 0
    monkeypatch.setattr(vault_mod, "SecretVault", _SlowVault)
    got = _at_once()
    assert all(v is got[0] for v in got) and got[0] is not None, got
    assert _SlowVault.built == 1


def test_a_transient_failure_is_tried_again(monkeypatch):
    calls = {"n": 0}

    class _FlakyVault:
        def __init__(self) -> None:
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("vault.db is locked")

        def close(self) -> None:
            pass

    monkeypatch.setattr(vault_mod, "SecretVault", _FlakyVault)
    assert vault_mod.get_vault() is None  # the first try failed
    assert vault_mod.get_vault() is None and calls["n"] == 1  # inside the backoff: not hammered
    monkeypatch.setattr(vault_mod, "_vault_retry_at", 0.0)  # the wait is over
    assert vault_mod.get_vault() is not None and calls["n"] == 2


def test_a_missing_key_is_decided_once(monkeypatch):
    calls = {"n": 0}

    class _NoKey:
        def __init__(self) -> None:
            calls["n"] += 1
            raise ValueError("KAZMA_VAULT_KEY not set")

    monkeypatch.setattr(vault_mod, "SecretVault", _NoKey)
    assert vault_mod.get_vault() is None and vault_mod.get_vault() is None
    assert calls["n"] == 1


def test_the_old_function_gave_a_caller_no_vault(monkeypatch):
    """Negative control: the function before FA-103, the same three callers."""
    _SlowVault.built = 0
    monkeypatch.setattr(vault_mod, "SecretVault", _SlowVault)
    state = {"vault": None, "attempted": False}

    def old_get_vault():
        if state["attempted"]:
            return state["vault"]
        state["attempted"] = True
        state["vault"] = _SlowVault()
        return state["vault"]

    monkeypatch.setattr(vault_mod, "get_vault", old_get_vault)
    got = _at_once()
    assert any(v is None for v in got), got
