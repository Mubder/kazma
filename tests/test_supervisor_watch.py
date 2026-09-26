"""The server pages when the guard that started it is gone (2026-09-26).

The guard died with exit code 1 and wrote nothing; the server kept running,
unsupervised, and nothing said so. The guard now leaves a heartbeat in its
state file and gives the server that file's path; the server checks it on
the maintenance cadence.
"""

from __future__ import annotations

import json
import logging
import time

import pytest
from kazma_core.observability import supervisor_watch as sw


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    sw._reset_for_tests()
    sent: list[tuple] = []
    from kazma_core.observability import ops_alerts

    monkeypatch.setattr(ops_alerts, "alert", lambda *a, **k: sent.append((a, k)) or True)
    yield sent
    sw._reset_for_tests()


def _state(tmp_path, monkeypatch, **fields):
    path = tmp_path / "guard.state.json"
    path.write_text(json.dumps(fields), encoding="utf-8")
    monkeypatch.setenv(sw.STATE_ENV, str(path))
    return path


def test_a_server_the_guard_did_not_start_is_not_watched(monkeypatch, _fresh):
    monkeypatch.delenv(sw.STATE_ENV, raising=False)
    assert sw._supervisor_status() is None
    sw.check_supervisor()
    assert _fresh == []


def test_a_live_guard_is_quiet_and_said_once(tmp_path, monkeypatch, _fresh, caplog):
    _state(tmp_path, monkeypatch, guard_pid=7, heartbeat=time.time())
    caplog.set_level(logging.INFO, logger="kazma_core.observability.supervisor_watch")
    sw.check_supervisor()
    sw.check_supervisor()
    assert _fresh == []
    said = [r for r in caplog.records if "supervised by guard pid 7" in r.getMessage()]
    assert len(said) == 1


def test_a_dead_guard_pages(tmp_path, monkeypatch, _fresh):
    _state(tmp_path, monkeypatch, guard_pid=7, heartbeat=time.time() - 3600)
    sw.check_supervisor()
    assert len(_fresh) == 1
    (key, title, detail), kw = _fresh[0]
    assert key == "guard.gone" and kw["severity"] == "critical"
    assert "60 min" in detail and "KazmaAgent" in detail


def test_a_missing_heartbeat_pages(tmp_path, monkeypatch, _fresh):
    monkeypatch.setenv(sw.STATE_ENV, str(tmp_path / "nothing-here.json"))
    sw.check_supervisor()
    assert [a[0] for a, _ in _fresh] == ["guard.gone"]


def test_negative_control_the_threshold_decides(tmp_path, monkeypatch, _fresh):
    """Just inside the threshold is supervised; just outside is not."""
    now = time.time()
    _state(tmp_path, monkeypatch, heartbeat=now - sw.STALE_AFTER_S + 5)
    assert sw._supervisor_status(now)["supervised"] is True
    _state(tmp_path, monkeypatch, heartbeat=now - sw.STALE_AFTER_S - 5)
    assert sw._supervisor_status(now)["supervised"] is False


def test_the_check_runs_on_the_maintenance_cadence(monkeypatch):
    from kazma_core.memory import worker_bootstrap as wb

    ran: list[int] = []
    monkeypatch.setattr(sw, "check_supervisor", lambda: ran.append(1))
    sweeps = dict(wb._MAINTENANCE_SWEEPS)
    assert "supervisor watch" in sweeps
    sweeps["supervisor watch"]()
    assert ran == [1]
