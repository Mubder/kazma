"""The guard watches the database as an OS service (resilience plan A1).

``kazma setup postgres`` records the service in ``setup_record.json``. The
guard checks it every 30 s: the service manager's state and the port. Down
for a minute (after the guard's first two minutes), it asks the OS to start it
once per outage; down for five minutes, one page; answering again after that
page, one recovery page. Nothing for an install with no record.

A fake clock, a fake ``sc`` and a fake port; the guard without the watch is
the negative control.
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"


def _load_guard():
    spec = importlib.util.spec_from_file_location("_dbsvc_guard", GUARD)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Harness:
    def __init__(self, monkeypatch, *, record=None) -> None:
        self.now = 1000.0
        wall0 = time.time()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: wall0 + self.now, sleep=lambda s: None))
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        self.running = True
        self.answers = True
        self.calls: list[list[str]] = []
        self.g = guard.Guard()
        self.g.broker.wanted = False
        self.g._started_mono = self.now
        self.record = record if record is not None else {"os": "windows", "service": "postgresql-x64-16",
                                                          "port": 5432}
        self.g.dbsvc = guard.DatabaseServiceWatch(self.g, record=lambda: self.record, run=self._run,
                                                  answers=lambda port: self.answers)
        self.pages: list[str] = []
        self.g.notify.send = self.pages.append

    def _run(self, argv, timeout):
        self.calls.append(list(argv))
        if argv[:2] == ["sc", "query"]:
            return 0, "STATE : 4  RUNNING" if self.running else "STATE : 1  STOPPED", ""
        if argv[:2] == ["sc", "start"]:
            return 0, "", ""
        return 1, "", ""

    def run(self, seconds: float, step: float = 1.0) -> None:
        end = self.now + seconds
        while self.now < end:
            self.g._broker_tick()
            self.now += step

    def starts(self) -> int:
        return sum(1 for c in self.calls if c[:2] == ["sc", "start"])


def test_a_stopped_service_is_started_once_and_paged_once(monkeypatch):
    h = _Harness(monkeypatch)
    h.run(300)
    assert h.pages == [] and h.starts() == 0
    h.running = h.answers = False
    h.run(90)
    assert h.starts() == 1                       # down a minute: asked once
    h.run(600)
    assert h.starts() == 1                       # never again this outage
    assert len(h.pages) == 1 and "database service is down" in h.pages[0]
    assert "asked the OS to start it" in h.pages[0]
    h.running = h.answers = True
    h.run(60)
    assert len(h.pages) == 2 and "running again" in h.pages[1]
    assert guard._read_state()["database_service"]["state"] == "running"


def test_a_short_outage_is_neither_started_nor_paged(monkeypatch):
    h = _Harness(monkeypatch)
    h.run(300)
    h.running = h.answers = False
    h.run(45)
    h.running = h.answers = True
    h.run(120)
    assert h.starts() == 0 and h.pages == []


def test_nothing_is_started_in_the_guards_first_two_minutes(monkeypatch):
    # At boot the OS starts the service beside the guard.
    h = _Harness(monkeypatch)
    h.running = h.answers = False
    h.run(110)
    assert h.starts() == 0


def test_no_record_no_watch(monkeypatch):
    h = _Harness(monkeypatch, record={})
    h.running = h.answers = False
    h.run(900)
    assert h.calls == [] and h.pages == []


def test_without_the_watch_a_stopped_service_says_nothing(monkeypatch):
    # The negative control: the guard before A1.
    h = _Harness(monkeypatch)
    h.g.dbsvc = None
    h.running = h.answers = False
    h.run(900)
    assert h.calls == [] and h.pages == []


def test_status_says_the_service():
    lines = guard._dbsvc_status_lines({"database_service": {"state": "down", "service": "postgresql-x64-16"}})
    assert lines == ["database    : down (postgresql-x64-16)"]
    assert "database_service" in guard.GUARD_FEATURES
