"""A Postgres address and no driver to reach it is said for what it is.

On the drill VM (2026-11-06) ``kazma setup postgres`` wrote
``KAZMA_DATABASE_URL`` into an environment the setup scripts had synced
without the ``postgres`` extra (psycopg). The server waited for its database
for ten minutes; the startup wait said "it may still be starting. Check
PostgreSQL connectivity", the guard said "unreachable", and the cause was
only ``(ImportError)`` in kazma.log, every 14 s. The database was fine.

Now the pool names the missing driver and what to run, at once (no five
attempts for a package that is not there), the settings store passes it on,
and it is never counted as an outage. A driver installed while the boot waits
is found at its next try.
"""

from __future__ import annotations

import sys

import pytest

from kazma_core.db import postgres_pool as pp

_DSN = "postgresql://kazma_app:pw@127.0.0.1:5432/kazma"


def _block_driver(monkeypatch) -> None:
    for name in pp.DRIVER_MODULES:
        monkeypatch.setitem(sys.modules, name, None)  # import of it raises ImportError


def test_a_missing_driver_is_named_with_what_to_run(monkeypatch):
    _block_driver(monkeypatch)
    reason = pp.driver_missing()
    assert reason and "kazma setup postgres" in reason and "psycopg" in reason
    # The pool's one direct sign-in gave None here ("no reason"), and every
    # surface above it then said the database did not answer.
    assert pp.refusal_reason(_DSN) == reason


def test_the_pool_says_it_once_and_at_once(monkeypatch, caplog):
    _block_driver(monkeypatch)
    monkeypatch.setattr(pp, "get_database_url", lambda: _DSN)
    monkeypatch.setattr(pp, "_pool", None)
    monkeypatch.setattr(pp, "_driver_missing_said", False)
    built: list[int] = []
    monkeypatch.setattr(pp, "PostgresPool", lambda *a, **kw: built.append(1))
    caplog.set_level("ERROR", logger=pp.logger.name)
    for _ in range(3):  # the boot wait tries again every 10 s
        with pytest.raises(pp.PostgresDriverMissing) as caught:
            pp.get_postgres_pool()
        assert "kazma setup postgres" in caught.value.reason
    assert built == [], "no pool is tried for a package that is not there"
    said = [r for r in caplog.records if r.name == pp.logger.name and "driver is not installed" in r.getMessage()]
    assert len(said) == 1, "said once a process, not on every try"


def test_the_settings_wait_says_it_and_it_is_no_outage(monkeypatch):
    from kazma_core.config_availability import ConfigStoreUnavailableError
    from kazma_core.db import availability

    monkeypatch.setattr("kazma_core.config_availability.get_backend", lambda: "postgres")
    _block_driver(monkeypatch)
    missing = pp.PostgresDriverMissing(pp.driver_missing())
    error = ConfigStoreUnavailableError(opening=True, reason=missing.reason)
    assert "kazma setup postgres" in str(error) and "connectivity" not in str(error)
    assert error.driver_missing
    assert not availability.is_outage(missing) and not availability.is_outage(error)
    # Negative control: what the drill VM's boot said, and an outage is still one.
    before = ConfigStoreUnavailableError(opening=True, reason=None)
    assert "may still be starting" in str(before) and availability.is_outage(before)


def test_a_boot_hears_it_through_the_real_settings_store(monkeypatch):
    """The settings store a boot opens, on a Postgres address with no driver.
    (The suite's shield removes a database address from the environment, so
    the backend checks are answered here; nothing connects anywhere.)"""
    import kazma_core.config_store as cs

    monkeypatch.setattr("kazma_core.db.backend.is_postgres", lambda: True)
    monkeypatch.setattr(pp, "get_database_url", lambda: "postgresql://kazma_app:pw@127.0.0.1:1/kazma")
    _block_driver(monkeypatch)
    monkeypatch.setattr(cs, "_config_store", None)
    monkeypatch.setattr(pp, "_pool", None)
    monkeypatch.setattr(pp, "_driver_missing_said", False)
    with pytest.raises(Exception) as caught:
        cs.get_config_store()
    assert type(caught.value).__name__ == "ConfigStoreUnavailableError", repr(caught.value)
    assert "kazma setup postgres" in str(caught.value), str(caught.value)
    assert caught.value.driver_missing


def test_a_driver_installed_while_the_boot_waits_is_found(tmp_path, monkeypatch):
    """No restart: the next try of the wait imports what was installed since."""
    name = "kazma_late_driver_probe"
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, name, raising=False)
    assert pp.driver_missing((name,)) is not None
    (tmp_path / name).mkdir()
    (tmp_path / name / "__init__.py").write_text("", encoding="utf-8")
    assert pp.driver_missing((name,)) is None
    monkeypatch.delitem(sys.modules, name, raising=False)


def test_the_driver_check_imports_what_the_pool_does():
    """The modules checked are the ones PostgresPool and the store import."""
    import inspect

    source = inspect.getsource(pp.PostgresPool.__init__) + inspect.getsource(pp)
    assert "from psycopg_pool import ConnectionPool" in source
    assert set(pp.DRIVER_MODULES) == {"psycopg", "psycopg.rows", "psycopg_pool"}
