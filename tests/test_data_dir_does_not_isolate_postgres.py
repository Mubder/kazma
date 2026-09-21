"""``KAZMA_DATA_DIR`` moves files. It does not move a Postgres config store.

``_use_postgres()`` keys off ``KAZMA_DB_BACKEND`` / ``KAZMA_DATABASE_URL`` and
nothing else, so a script, a test harness or a second checkout that points the
data dir at a scratch path still reads *and writes* the production settings
store while looking thoroughly isolated.

That is not hypothetical. The dev clone in this repo shared the operator's live
Postgres for days: ``kazma doctor`` gave different answers on the two boxes, a
test run wrote junk into the real ``llm_calls.db``, and a ``vault://`` pointer
written by one machine resolved to nothing on the other — the 2026-09-16
outage.

The warning shipped once and was rolled back on 2026-09-17, bundled with the
vault tripwire, because main was red and only one of the two could be cleared
of causing it. Neither was: the red runs were a 15-second teardown tax
(``379ca476``), and ``KNOWN_GAPS`` records the tripwire as *wrongly* reverted.
This file is the part that was missing the first time — a test, so the next
person deciding whether it is load-bearing can read one instead of guessing.

Deliberately a warning, not a behaviour change: ``KAZMA_DATA_DIR`` is a
documented relocatable-production-layout feature, and making it imply SQLite
would silently detach a legitimately relocated install from its own database.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from kazma_core.config_store import ConfigStore


class _Recorder(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.messages.append(record.getMessage())
        except Exception:  # pragma: no cover
            pass


@pytest.fixture
def warnings_seen():
    log = logging.getLogger("kazma_core.config_store")
    rec = _Recorder()
    rec.setLevel(logging.WARNING)
    log.addHandler(rec)
    prev = log.level
    log.setLevel(logging.WARNING)
    yield rec.messages
    log.removeHandler(rec)
    log.setLevel(prev)


def _probe(monkeypatch, data_dir: str | None, project_root: Path) -> None:
    """Run the check alone, without booting a real Postgres ConfigStore."""
    import kazma_core.paths as paths

    if data_dir is None:
        monkeypatch.delenv("KAZMA_DATA_DIR", raising=False)
    else:
        monkeypatch.setenv("KAZMA_DATA_DIR", data_dir)
    monkeypatch.setattr(paths, "get_project_root", lambda: project_root)

    store = ConfigStore.__new__(ConfigStore)       # no __init__: no DB, no IO
    ConfigStore._warn_if_data_dir_implies_isolation(store)


def test_warns_when_the_data_dir_is_relocated(monkeypatch, warnings_seen, tmp_path):
    """The dangerous case: looks isolated, shares the production store."""
    project = tmp_path / "checkout"
    (project / "kazma-data").mkdir(parents=True)
    scratch = tmp_path / "scratch-data"
    scratch.mkdir()

    _probe(monkeypatch, str(scratch), project)

    hits = [m for m in warnings_seen if "KAZMA_DATA_DIR relocates files" in m]
    assert hits, (
        "a relocated data dir on a Postgres backend produced no warning — the "
        "process reads and writes the production store while looking isolated"
    )
    assert "SHARED" in hits[0]
    assert "KAZMA_DB_BACKEND=sqlite" in hits[0], (
        "the warning must say how to actually get isolation, or it is just noise"
    )


def test_silent_when_the_data_dir_is_its_own_default(monkeypatch, warnings_seen, tmp_path):
    """Pointing at your own kazma-data is not surprising and must not warn."""
    project = tmp_path / "checkout"
    own = project / "kazma-data"
    own.mkdir(parents=True)

    _probe(monkeypatch, str(own), project)

    assert not [m for m in warnings_seen if "KAZMA_DATA_DIR" in m], (
        "warned about the default layout; a warning that fires on the normal "
        "case is one people learn to ignore"
    )


def test_silent_when_the_variable_is_unset(monkeypatch, warnings_seen, tmp_path):
    project = tmp_path / "checkout"
    (project / "kazma-data").mkdir(parents=True)

    _probe(monkeypatch, None, project)

    assert not [m for m in warnings_seen if "KAZMA_DATA_DIR" in m]


def test_never_raises(monkeypatch, warnings_seen):
    """A diagnostic must not be able to fail boot. It was rolled back once."""
    import kazma_core.paths as paths

    monkeypatch.setenv("KAZMA_DATA_DIR", "/some/relocated/path")

    def _boom():
        raise RuntimeError("project root unavailable")

    monkeypatch.setattr(paths, "get_project_root", _boom)

    store = ConfigStore.__new__(ConfigStore)
    ConfigStore._warn_if_data_dir_implies_isolation(store)   # must not raise
