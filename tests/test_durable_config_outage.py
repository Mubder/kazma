"""Outage drills use disposable stores; never disrupt the live database."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from kazma_core.config_availability import ConfigStoreUnavailableError
from kazma_core.config_store import ConfigStore, _InMemoryStore
from kazma_ui.config_errors import install_config_error_handler
from kazma_ui.settings import create_settings_router


@pytest.mark.parametrize("batch", [False, True])
def test_no_false_settings_success(tmp_path, batch):
    store = _InMemoryStore()
    app = FastAPI()
    install_config_error_handler(app)
    app.include_router(create_settings_router(SimpleNamespace(), store, Jinja2Templates(directory=str(tmp_path))))
    setting = {"key": "agent.language", "value": "ar", "category": "agent"}
    with TestClient(app) as client:
        response = client.put("/api/settings" if batch else "/api/settings/single", json=[setting] if batch else setting)
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["code"] == "config_store_unavailable"
    assert store.get("agent.language") is None


@pytest.mark.parametrize("operation", [
    lambda s: s.set("a", 1),
    lambda s: s.set_if_absent("a", 1),
    lambda s: s.batch_set([("a", 1, "general")]),
    lambda s: s.atomic_update("a", lambda _: pytest.fail("updater ran")),
    lambda s: s.delete("a"),
    lambda s: s.import_yaml("a: 1"),
    lambda s: s.reset_all(),
    lambda s: s.reconcile_from_yaml(),
    lambda s: s.transaction(),
])
def test_every_memory_mutator_refuses_before_side_effects(operation):
    store = _InMemoryStore()
    store.add_change_listener(lambda _: pytest.fail("write notice fired"))
    with pytest.raises(ConfigStoreUnavailableError):
        operation(store)
    assert store.get_all() == {"general": {}}


def test_locked_store_recovery_has_one_committed_change(tmp_path):
    path = tmp_path / "settings.db"
    store = ConfigStore(db_path=str(path))
    notices = []
    store.add_change_listener(notices.append)
    blocker = sqlite3.connect(path, isolation_level=None)
    try:
        with store._lock:
            store._get_conn().execute("PRAGMA busy_timeout=20")
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(ConfigStoreUnavailableError):
            store.batch_set([("agent.language", "ar", "agent"), ("test.marker", "once", "test")])
        assert not notices
        blocker.execute("ROLLBACK")
        assert store.get("test.marker") is None
        store.batch_set([("agent.language", "ar", "agent"), ("test.marker", "once", "test")])
        assert len(notices) == 1
        store.close()
        reopened = ConfigStore(db_path=str(path))
        try:
            assert reopened.get("agent.language") == "ar"
            assert reopened.get("test.marker") == "once"
        finally:
            reopened.close()
    finally:
        blocker.close()
        store.close()


def test_failed_boot_can_retry_durable_storage(monkeypatch, tmp_path):
    import kazma_core.config_store as module

    real = module.ConfigStore
    monkeypatch.setattr(module, "_config_store", None)
    monkeypatch.setattr(module, "_INIT_RETRY_BACKOFF_S", 0)
    def unavailable():
        raise sqlite3.OperationalError("unable to open database file")
    monkeypatch.setattr(module, "ConfigStore", unavailable)
    with pytest.raises(ConfigStoreUnavailableError):
        module.get_config_store()
    assert module.peek_config_store() is None
    monkeypatch.setattr(module, "ConfigStore", lambda: real(db_path=str(tmp_path / "settings.db")))
    store = module.get_config_store()
    store.set("test.after_recovery", True)
    store.close()
    assert module.get_config_store().get("test.after_recovery") is True


def test_failed_postgres_pools_are_closed_before_retry(monkeypatch):
    from kazma_core.db import postgres_pool as module

    pools = []
    class Pool:
        def __init__(self, *args, **kwargs):
            self.closed = False
            pools.append(self)

        def close(self):
            self.closed = True

    def schema(pool):
        if len(pools) < 3:
            raise RuntimeError("connection closed")
        assert all(old.closed for old in pools[:-1])

    monkeypatch.setattr(module, "_pool", None)
    monkeypatch.setattr(module, "get_database_url", lambda: "postgresql://isolated")
    monkeypatch.setattr(module, "PostgresPool", Pool)
    monkeypatch.setattr(module, "_ensure_core_schema", schema)
    monkeypatch.setenv("KAZMA_PG_POOL_RETRY_DELAY", "0")
    assert module.get_postgres_pool() is pools[-1]
    assert len(pools) == 3
    assert not pools[-1].closed


def test_postgres_retries_are_not_multiplied_by_settings(monkeypatch):
    import kazma_core.config_store as module

    calls = []
    def unavailable():
        calls.append(True)
        raise RuntimeError("pool exhausted its bounded retries")

    monkeypatch.setattr(module, "_config_store", None)
    monkeypatch.setattr(module, "ConfigStore", unavailable)
    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    with pytest.raises(ConfigStoreUnavailableError) as error:
        module.get_config_store()
    assert len(calls) == 1
    assert error.value.backend == "postgres"
    assert "settings.db" not in str(error.value)


def test_agent_boot_stops_before_creating_secondary_stores(monkeypatch):
    from kazma_core.agent_runner import KazmaAgent

    def unavailable():
        raise ConfigStoreUnavailableError()

    monkeypatch.setattr("kazma_core.config_store.get_config_store", unavailable)
    monkeypatch.setattr("kazma_core.time_travel.create_recorder", lambda **kwargs: pytest.fail("snapshot store created"))
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: pytest.fail("model registry initialized"))
    with pytest.raises(ConfigStoreUnavailableError):
        KazmaAgent()
