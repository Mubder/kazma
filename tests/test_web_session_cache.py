"""The web-session read cache: fewer store reads, and never a live revoked session.

Every authenticated request resolves its session. That was a ConfigStore read
-- a Postgres round trip on the event loop -- per request, and five loop-stall
dumps on the live install caught the loop inside it (2026-09-15). The cache
must not buy that back with security: a revoke is immediate in-process, and
expiry is re-checked on every hit.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.security import web_sessions as ws

# Verified against a real Postgres (throwaway postgres:16, twice); the CI
# Postgres job runs every test carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres


@pytest.fixture
def reads(monkeypatch):
    store = get_config_store()
    real_get = store.get
    counter = {"n": 0}

    def counting_get(key, *a, **k):
        if str(key).startswith(ws.SESSION_KEY_PREFIX):
            counter["n"] += 1
        return real_get(key, *a, **k)

    monkeypatch.setattr(store, "get", counting_get)
    return counter


def test_repeated_lookups_cost_one_store_read(reads):
    sid = ws.create_session(role="admin", username="u")
    with ws._cache_lock:
        ws._cache.clear()  # measure a cold read, not the seed from create
    for _ in range(50):
        assert ws.get_session_payload(sid)["username"] == "u"
    assert reads["n"] == 1


def test_a_revoke_is_immediate(reads):
    sid = ws.create_session(role="admin")
    assert ws.validate_session(sid)
    ws.revoke_session(sid)
    assert ws.get_session_payload(sid) is None


def test_a_reader_that_raced_the_revoke_cannot_resurrect_it():
    sid = ws.create_session(role="admin")
    key = f"web_session.{ws._hash(sid)}"
    stale = ws.get_session_payload(sid)  # read before the revoke lands
    ws.revoke_session(sid)
    ws._cache_put(key, stale)  # ...and cached after it
    assert ws.get_session_payload(sid) is None


def test_expiry_is_checked_on_a_cache_hit(monkeypatch):
    sid = ws.create_session(role="admin")
    assert ws.validate_session(sid)
    real = time.time
    monkeypatch.setattr(ws.time, "time", lambda: real() + 15 * 24 * 3600)
    assert ws.get_session_payload(sid) is None


def test_callers_cannot_mutate_the_cached_payload():
    sid = ws.create_session(role="viewer")
    ws.get_session_payload(sid)["role"] = "admin"
    assert ws.get_session_payload(sid)["role"] == "viewer"
