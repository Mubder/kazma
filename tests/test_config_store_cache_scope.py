"""A settings write drops only what it can change in the read cache (2026-10-01).

Every ``set`` / ``batch_set`` / ``delete`` cleared the whole read cache,
misses included, so any write anywhere -- the heartbeat's stamp, a session
write -- sent the next read of EVERY setting back to the database (on
Postgres a round trip), often from the event loop. A write now drops the key,
its ancestors (``get`` merges a missing key's children, so ``a``'s view holds
``a.b``) and its descendants; a raw transaction, a reset and a YAML reload
still clear everything.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kazma_core.config_store import ConfigStore


@pytest.fixture()
def store(tmp_path: Path):
    s = ConfigStore(db_path=str(tmp_path / "settings.db"))
    yield s, tmp_path / "settings.db"
    s.close()


def _write_behind(db: Path, key: str, value: str) -> None:
    """Change a row the way another process would: the store does not know."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (f'"{value}"', key))
        conn.commit()
    finally:
        conn.close()


def test_an_unrelated_write_keeps_a_cached_read(store) -> None:
    s, db = store
    s.set("ui.theme", "dark")
    assert s.get("ui.theme") == "dark"  # cached
    _write_behind(db, "ui.theme", "light")
    s.set("system.heartbeat", "now")  # any other write
    assert s.get("ui.theme") == "dark"  # still the cached read: no database trip
    assert s.get("never.set", "fallback") == "fallback"
    s.set("system.heartbeat", "later")
    assert s.get("never.set", "fallback") == "fallback"  # a cached miss survives too


def test_a_write_drops_the_key_its_ancestors_and_descendants(store) -> None:
    s, db = store
    s.set("agent.trim.budget", 1)
    s.set("agent.trim.mode", "auto")
    assert s.get("agent.trim") == {"budget": 1, "mode": "auto"}  # merged view, cached
    s.set("agent.trim.budget", 2)
    assert s.get("agent.trim") == {"budget": 2, "mode": "auto"}  # ancestor refreshed
    s.set("agent.trim.mode", "manual")
    assert s.get("agent.trim.mode") == "manual"
    s.delete("agent.trim.mode")
    assert s.get("agent.trim") == {"budget": 2}
    assert s.get("agent.trim.mode", "gone") == "gone"


def test_the_whole_cache_still_clears_for_unknown_keys(store) -> None:
    s, db = store
    s.set("ui.theme", "dark")
    assert s.get("ui.theme") == "dark"
    with s.transaction() as conn:  # raw SQL names no keys
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", ('"light"', "ui.theme"))
    assert s.get("ui.theme") == "light"


def test_the_old_full_clear_sent_every_read_back(store, monkeypatch) -> None:
    """Negative control: with a write clearing everything (the old rule), the
    unrelated read goes back to the database and sees the row changed behind
    the store's back."""
    s, db = store
    monkeypatch.setattr(ConfigStore, "_invalidate_key", lambda self, key: self._clear_cache())
    s.set("ui.theme", "dark")
    assert s.get("ui.theme") == "dark"
    _write_behind(db, "ui.theme", "light")
    s.set("system.heartbeat", "now")
    assert s.get("ui.theme") == "light"
