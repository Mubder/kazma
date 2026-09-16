"""A relocated data dir does not relocate a Postgres config store.

`KAZMA_DATA_DIR` moves an install's files. It does not move its settings when
the backend is Postgres: `_use_postgres()` keys off `KAZMA_DB_BACKEND` /
`KAZMA_DATABASE_URL` and nothing else. So a second checkout, a script or a
test harness that points the data dir at a scratch path still reads *and
writes* the production store while looking isolated.

That is how the dev clone in this repo spent days sharing the operator's live
Postgres: `kazma doctor` gave different answers on the two boxes, a test run
wrote into the real `llm_calls.db`, and a `vault://` pointer written by one
machine resolved to nothing on the other — the 2026-09-16 outage.

The behaviour is deliberately unchanged. `KAZMA_DATA_DIR` is documented as a
relocatable *production* layout, so making it imply SQLite would silently
detach a legitimately relocated install from its own database. What is fixed
is that the combination is no longer silent.
"""

from __future__ import annotations

import logging

import pytest
from kazma_core.config_store import ConfigStore


@pytest.fixture
def store(tmp_path):
    return ConfigStore(db_path=str(tmp_path / "settings.db"))


def test_a_relocated_data_dir_on_postgres_warns(store, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "scratch"))

    with caplog.at_level(logging.WARNING):
        store._warn_if_data_dir_implies_isolation()

    assert "SHARED" in caplog.text
    assert "KAZMA_DB_BACKEND=sqlite" in caplog.text, "name the actual fix"
    assert "WRITE" in caplog.text, "reading is not the dangerous half"


def test_an_unset_data_dir_is_quiet(store, monkeypatch, caplog):
    monkeypatch.delenv("KAZMA_DATA_DIR", raising=False)

    with caplog.at_level(logging.WARNING):
        store._warn_if_data_dir_implies_isolation()
    assert "SHARED" not in caplog.text


def test_a_data_dir_pointing_at_its_own_default_is_quiet(store, monkeypatch, caplog):
    """Setting it to the value it already has is not a misconfiguration."""
    from kazma_core.paths import get_project_root

    monkeypatch.setenv("KAZMA_DATA_DIR", str(get_project_root() / "kazma-data"))

    with caplog.at_level(logging.WARNING):
        store._warn_if_data_dir_implies_isolation()
    assert "SHARED" not in caplog.text


def test_the_check_never_raises(store, tmp_path, monkeypatch, caplog):
    """A diagnostic that can fail boot is worse than the gap it reports.

    Raise from inside the check rather than feeding it a bad env value: the
    first version of this test passed a null byte, which `monkeypatch.setenv`
    rejects itself — so it asserted nothing about the code under test and
    failed in its own setup.
    """
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path / "scratch"))

    import kazma_core.paths as paths

    def _boom():
        raise OSError("filesystem is on fire")

    monkeypatch.setattr(paths, "get_project_root", _boom)

    with caplog.at_level(logging.WARNING):
        store._warn_if_data_dir_implies_isolation()  # must not raise

    assert "SHARED" not in caplog.text, "it could not decide; it must not guess"


def test_it_is_wired_into_the_postgres_branch():
    """The warning is worthless if nothing calls it."""
    import inspect

    src = inspect.getsource(ConfigStore._init_db)
    assert "_warn_if_data_dir_implies_isolation()" in src
