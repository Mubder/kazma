"""An OIDC sign-in's state is its own, good once, in the browser that began it.

2026-11 functional audit, FA-133. The state lived in ONE settings key and was
compared with ``!=``: a second sign-in (another tab, another person) replaced
the first, which then failed, and a state that reached another browser (a
shared link, a referrer) signed THAT browser in. Now:

- each sign-in has its own pending record (``auth.oidc.pending``, keyed by
  the state's hash), so sign-ins started together all finish;
- the callback takes the state only from the browser holding its binding
  cookie, compared in constant time;
- the record is taken out of the store in one compare-and-set before the
  token exchange, so a state is good for one callback, also two at once.

Each test stops the exchange right after the state is accepted (discovery
raises ``_Accepted``), so it is the state rule that is measured. The
one-record store (the old model) and a skipped binding are the negative
controls.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import pytest

from kazma_core.security import oidc as oidc_mod

ISSUER = "https://idp.example"


class _Accepted(Exception):
    """Raised where the exchange goes on to the identity provider."""


@pytest.fixture
def oidc_env(monkeypatch):
    monkeypatch.setenv("KAZMA_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("KAZMA_OIDC_CLIENT_ID", "kazma")
    monkeypatch.setenv("KAZMA_OIDC_REDIRECT_URI", "https://app.example/api/auth/oidc/callback")
    building = {"on": True}

    async def _discovery(_issuer: str) -> dict[str, str]:
        if building["on"]:
            return {"authorization_endpoint": f"{ISSUER}/auth", "token_endpoint": f"{ISSUER}/token"}
        raise _Accepted()

    monkeypatch.setattr(oidc_mod, "fetch_discovery", _discovery)
    return building


class _Store:
    """The settings store's ``atomic_update``, one writer at a time."""

    def __init__(self) -> None:
        self.d: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, k: str, default: Any = None) -> Any:
        return self.d.get(k, default)

    def atomic_update(self, k: str, fn: Any, category: str = "general") -> Any:
        with self._lock:
            self.d[k] = fn(self.d.get(k))
            return self.d[k]


class _OneSignInStore(_Store):
    """The old model: a new sign-in replaces whatever was pending."""

    def atomic_update(self, k: str, fn: Any, category: str = "general") -> Any:
        with self._lock:
            before = self.d.get(k)
            new = fn(before)
            if isinstance(new, dict) and len(new) > 1:
                # The record this write added, not the latest expiry: two
                # sign-ins in one clock tick share an `exp` (Windows ticks
                # 15.6 ms), and `max` then kept the first one.
                added = [s for s in new if s not in (before or {})]
                if added:
                    new = {added[-1]: new[added[-1]]}
            self.d[k] = new
            return new


@pytest.fixture
def store(monkeypatch):
    s = _Store()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: s)
    return s


async def _start(building: dict) -> dict[str, str]:
    building["on"] = True
    info = await oidc_mod.build_authorize_url()
    building["on"] = False
    return info


async def _finishes(state: str, bound: str) -> bool:
    """True when the callback accepts the state (and goes on to the IdP)."""
    try:
        await oidc_mod.exchange_code("code", state, bound_state=bound)
    except _Accepted:
        return True
    except PermissionError:
        return False
    raise AssertionError("the exchange neither refused nor went on")


async def test_two_sign_ins_started_together_both_finish(oidc_env, store):
    first = await _start(oidc_env)
    second = await _start(oidc_env)
    assert first["binding_cookie"] != second["binding_cookie"]
    assert await _finishes(first["state"], first["state"])
    assert await _finishes(second["state"], second["state"])


async def test_negative_control_one_record_loses_the_first_sign_in(oidc_env, monkeypatch):
    old = _OneSignInStore()
    monkeypatch.setattr("kazma_core.config_store.get_config_store", lambda: old)
    first = await _start(oidc_env)
    second = await _start(oidc_env)
    assert not await _finishes(first["state"], first["state"])
    assert await _finishes(second["state"], second["state"])


async def test_a_state_another_browser_holds_is_refused(oidc_env, store):
    info = await _start(oidc_env)
    assert not await _finishes(info["state"], "")  # no binding cookie
    assert not await _finishes(info["state"], info["state"][:-1] + "x")
    # The refusal took nothing: the browser that began it still finishes.
    assert await _finishes(info["state"], info["state"])


async def test_negative_control_without_the_binding_any_browser_gets_in(oidc_env, store):
    """What the binding refuses: a browser handed the state, passing it as
    its own binding, is exactly what the old callback let in."""
    info = await _start(oidc_env)
    stolen = info["state"]
    assert await _finishes(stolen, stolen)


async def test_a_state_is_good_once(oidc_env, store):
    info = await _start(oidc_env)
    assert await _finishes(info["state"], info["state"])
    assert not await _finishes(info["state"], info["state"])


async def test_two_callbacks_at_once_one_finishes(oidc_env, store):
    info = await _start(oidc_env)
    results = await asyncio.gather(*[_finishes(info["state"], info["state"]) for _ in range(6)])
    assert sorted(results) == [False] * 5 + [True]


async def test_an_expired_sign_in_is_refused_and_pruned(oidc_env, store, monkeypatch):
    info = await _start(oidc_env)
    clock = {"now": oidc_mod.time.time() + oidc_mod._PENDING_TTL_S + 1}
    monkeypatch.setattr(oidc_mod.time, "time", lambda: clock["now"])
    assert not await _finishes(info["state"], info["state"])
    assert store.d["auth.oidc.pending"] == {}


async def test_pending_sign_ins_are_bounded(oidc_env, store):
    for _ in range(oidc_mod._PENDING_MAX + 5):
        await _start(oidc_env)
    assert len(store.d["auth.oidc.pending"]) == oidc_mod._PENDING_MAX


async def test_the_store_never_holds_a_state(oidc_env, store):
    """Only its hash: a settings backup or a read of the store gives nobody
    a state to finish a sign-in with."""
    info = await _start(oidc_env)
    assert info["state"] not in repr(store.d)


def test_the_routes_bind_and_check_the_cookie():
    """The start route sets the binding cookie and the callback passes it on:
    read from the source, the behaviour of each half is held above."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "kazma-ui" / "kazma_ui" / "routes_direct"
           / "auth.py").read_text(encoding="utf-8")
    start = src.split('"/api/auth/oidc/start"')[1].split("@self.app")[0]
    callback = src.split('"/api/auth/oidc/callback"')[1].split("@self.app")[0]
    assert 'key=info["binding_cookie"]' in start and "httponly=True" in start
    assert "bound_state=request.cookies.get(binding" in callback
