"""A dead SQLite settings DB must not silently impersonate the live config.

Switching an install to Postgres leaves `kazma-data/settings.db` on disk,
frozen at the moment of the switch. Nothing reads it again — and it looks
exactly like the live configuration to anyone who opens it.

Measured on the operator's install, 2026-09-12:

    sqlite settings.db :  90 keys        postgres: 884 keys
    deepseek    sqlite=(disabled, no key)   postgres=(enabled, has key)
    groq        sqlite=(disabled, no key)   postgres=(enabled, has key)
    openrouter  sqlite=(disabled, no key)   postgres=(enabled, has key)

Every provider disagreed. Debugging a credential failure against that file
gives a confident, wrong answer — which is what happened while tracing why a
cron turn could not see its DeepSeek key, and it has produced a wrong answer in
this repo before.

The fix is one warning at boot. It cannot fix the confusion after the fact, but
it puts the sentence "that file is not read" in the log the operator is already
looking at.
"""

from __future__ import annotations

import logging
import sqlite3

import pytest
from kazma_core.config_store import ConfigStore


def _seed_sqlite(path, rows: int) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings "
        "(key TEXT PRIMARY KEY, value TEXT, category TEXT, updated_at REAL)"
    )
    # Insert by name, not by position: ConfigStore's own migrations may have
    # already created this table with more columns, and a positional INSERT
    # then fails on a detail this test does not care about.
    for i in range(rows):
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value, category, updated_at) "
            "VALUES (?, ?, ?, ?)",
            (f"k{i}", "v", "general", 0.0),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def store(tmp_path):
    """A ConfigStore whose SQLite path we control, without a real Postgres."""
    return ConfigStore(
        db_path=str(tmp_path / "settings.db"),
        yaml_path=str(tmp_path / "kazma.yaml"),
    )


def test_a_populated_leftover_is_called_out(store, tmp_path, caplog):
    _seed_sqlite(tmp_path / "settings.db", rows=90)

    with caplog.at_level(logging.WARNING):
        store._warn_if_stale_sqlite_shadow()

    # getMessage() applies the %-args; `.message` is the raw template.
    msg = "\n".join(r.getMessage() for r in caplog.records)
    assert "leftover" in msg and "NOT read" in msg
    assert "90" in msg, "say how many rows, so the operator can tell it is real data"
    assert "credential" in msg.lower(), (
        "name the failure mode -- the reason this matters is that it lies about keys"
    )


def test_no_leftover_file_is_silent(store, caplog):
    """A fresh Postgres install has no such file. Warning anyway would train
    the operator to ignore the line that matters."""
    with caplog.at_level(logging.WARNING):
        store._warn_if_stale_sqlite_shadow()
    # Level-filtered, not `== []`: caplog accumulates for the whole test and
    # any INFO emitted elsewhere in it would fail a bare emptiness check
    # depending only on the ambient log level another test happened to set.
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_an_empty_leftover_is_silent(store, tmp_path, caplog):
    """A zero-row file is schema, not stale configuration."""
    _seed_sqlite(tmp_path / "settings.db", rows=0)
    with caplog.at_level(logging.WARNING):
        store._warn_if_stale_sqlite_shadow()
    # Level-filtered, not `== []`: caplog accumulates for the whole test and
    # any INFO emitted elsewhere in it would fail a bare emptiness check
    # depending only on the ambient log level another test happened to set.
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []


def test_the_check_never_breaks_boot(store, tmp_path):
    """It runs inside ConfigStore init on the Postgres path. A corrupt or
    unreadable leftover must not take the process down over a diagnostic."""
    (tmp_path / "settings.db").write_bytes(b"this is not a database")
    store._warn_if_stale_sqlite_shadow()  # must not raise


def test_it_is_wired_into_the_postgres_init_path():
    """The warning is worthless if nothing calls it."""
    import inspect

    src = inspect.getsource(ConfigStore._init_db)
    assert "_warn_if_stale_sqlite_shadow()" in src
    assert src.index("_use_postgres") < src.index("_warn_if_stale_sqlite_shadow"), (
        "it must only fire on the Postgres path -- on SQLite that file IS the "
        "live store and warning about it would be nonsense"
    )
