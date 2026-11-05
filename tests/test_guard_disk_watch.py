"""The guard watches the free space where Kazma writes (resilience plan B2a).

A full drive stops Kazma in a set order -- documents refuse work below their
512 MB floor, then backups fail, then the database stops writing -- and until
now nothing said a drive was filling. The guard's ``DiskWatch`` reads the
free space of the data folder's drive (and of Docker's disk image, when it is
on another drive) once a minute and:

* pages ``warn`` once when either measure crosses the low line (15 %, 20 GB)
  and ``critical`` once at the critical one (5 %, 5 GB), naming what stops;
* leaves a level only when both measures clear it by a margin, so a drive
  hovering at a line is one page, not one a minute;
* says once when there is room again -- only after it paged;
* never raises out of the guard's loop.

Everything runs against a fake clock and a fake reader; nothing here reads
this machine's disks. Each rule has a negative control.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "scripts" / "service" / "kazma_guard.py"
GB = 1024 ** 3
Usage = namedtuple("Usage", "total used free")


def _load_guard():
    spec = importlib.util.spec_from_file_location("_disk_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Reader:
    """shutil.disk_usage for one fake 1,000 GB drive per path."""

    def __init__(self) -> None:
        self.free: dict[str, int] = {}
        self.total = 1000 * GB
        self.error: Exception | None = None
        self.calls = 0

    def __call__(self, path: str) -> Usage:
        self.calls += 1
        if self.error is not None:
            raise self.error
        free = self.free.get(path, 500 * GB)
        return Usage(self.total, self.total - free, free)


class _Harness:
    def __init__(self, monkeypatch, *, volumes=(("C:", "data", "C:/kazma-data"),)) -> None:
        self.now = 1000.0
        wall0 = time.time()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: wall0 + self.now, sleep=lambda s: None))
        monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "auto")
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        self.reader = _Reader()
        self.g = guard.Guard()
        self.g.broker.wanted = False
        self.paths = [(key, role, Path(p)) for key, role, p in volumes]
        self.g.disk = guard.DiskWatch(self.g, reader=self.reader, paths=self.paths)
        self.pages: list[str] = []
        self.g.notify.send = self.pages.append

    def set_free(self, gb: float, path: str = "C:/kazma-data") -> None:
        self.reader.free[str(Path(path))] = int(gb * GB)

    def run(self, seconds: float, step: float = 1.0) -> None:
        end = self.now + seconds
        while self.now < end:
            self.g._broker_tick()
            self.now += step

    def events(self) -> list[dict]:
        log = Path(os.environ["KAZMA_GUARD_LOG"])
        out = []
        for line in log.read_text(encoding="utf-8").splitlines() if log.exists() else []:
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out


# -- the levels -----------------------------------------------------------


def test_either_measure_enters_a_level_and_both_must_clear_it():
    T = 1000 * GB
    assert guard.disk_level(500 * GB, T) == "ok"
    assert guard.disk_level(140 * GB, T) == "low"            # 14 %
    assert guard.disk_level(40 * GB, T) == "critical"        # 4 %
    # A small drive crosses on bytes before its share does.
    assert guard.disk_level(18 * GB, 100 * GB) == "low"      # 18 %, 18 GB
    assert guard.disk_level(4 * GB, 50 * GB) == "critical"   # 8 %, 4 GB
    # Leaving takes the margin: 15.5 % stays low, 16.1 % is ok.
    assert guard.disk_level(155 * GB, T, "low") == "low"
    assert guard.disk_level(161 * GB, T, "low") == "ok"
    # Control: from ok the same 15.5 % is ok -- the margin is hysteresis only.
    assert guard.disk_level(155 * GB, T, "ok") == "ok"
    assert guard.disk_level(55 * GB, T, "critical") == "critical"
    assert guard.disk_level(61 * GB, T, "critical") == "low"
    assert guard.disk_level(0, 0, "low") == "low"            # an unreadable total


# -- the pages ------------------------------------------------------------


def test_one_page_per_level_naming_what_stops_and_one_when_room_is_back(monkeypatch):
    h = _Harness(monkeypatch)
    h.set_free(400)
    h.run(300)
    assert h.pages == []

    h.set_free(140)
    h.run(600)
    assert len(h.pages) == 1
    assert "Disk space low on C:" in h.pages[0]
    assert "512 MB" in h.pages[0] and "backups fail" in h.pages[0]

    h.set_free(40)
    h.run(600)
    assert len(h.pages) == 2
    assert "Disk almost full on C:" in h.pages[1]
    assert "4.0%" in h.pages[1]

    h.set_free(400)
    h.run(300)
    assert len(h.pages) == 3
    assert "Disk space is back on C:" in h.pages[2]
    levels = [e["state"] for e in h.events() if e.get("event") == "disk.level"]
    assert levels == ["low", "critical", "ok"]


def test_a_drive_hovering_at_the_line_is_one_page(monkeypatch):
    h = _Harness(monkeypatch)
    for i in range(20):
        h.set_free(149 if i % 2 == 0 else 152)
        h.run(60)
    assert len(h.pages) == 1


def test_without_the_margin_a_hovering_drive_pages_every_crossing(monkeypatch):
    # Control: the rule the margin replaces (enter and leave on one line).
    monkeypatch.setattr(guard, "DISK_MARGIN_PCT", 0.0)
    monkeypatch.setattr(guard, "DISK_MARGIN_BYTES", 0)
    h = _Harness(monkeypatch)
    for i in range(20):
        h.set_free(149 if i % 2 == 0 else 152)
        h.run(60)
    assert len(h.pages) > 5


def test_room_is_never_announced_without_an_earlier_page(monkeypatch):
    h = _Harness(monkeypatch)
    h.set_free(400)
    h.run(600)
    assert h.pages == []
    assert [e for e in h.events() if e.get("event") == "disk.level"] == []


def test_dockers_drive_names_the_database(monkeypatch):
    h = _Harness(monkeypatch, volumes=(("C:", "data", "C:/kazma-data"),
                                        ("D:", "docker", "D:/docker")))
    h.set_free(400)
    h.set_free(30, "D:/docker")
    h.run(120)
    assert len(h.pages) == 1
    assert "Disk almost full on D:" in h.pages[0]
    assert "database in Docker stops writing" in h.pages[0]


def test_without_the_watch_a_full_drive_says_nothing(monkeypatch):
    # Control: the guard before DiskWatch -- the tick has nothing to run.
    h = _Harness(monkeypatch)
    h.g.disk = None
    h.set_free(10)
    h.run(600)
    assert h.pages == []
    assert h.reader.calls == 0


def test_the_off_switch_reads_nothing(monkeypatch):
    h = _Harness(monkeypatch)
    monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "off")
    h.set_free(10)
    h.run(600)
    assert h.reader.calls == 0 and h.pages == []


# -- never raises -----------------------------------------------------------


def test_an_unreadable_drive_is_logged_and_a_bug_never_escapes(monkeypatch):
    h = _Harness(monkeypatch)
    h.reader.error = PermissionError("denied")
    h.run(120)
    assert "disk.unreadable" in [e.get("event") for e in h.events()]
    assert h.pages == []

    h.reader.error = ValueError("a bug in the reader")
    h.run(120)  # does not raise
    assert "disk.internal_error" in [e.get("event") for e in h.events()]


def test_one_read_a_minute(monkeypatch):
    h = _Harness(monkeypatch)
    h.run(600)
    assert 9 <= h.reader.calls <= 11


# -- what it records ------------------------------------------------------------


def test_the_state_file_and_status_say_the_level(monkeypatch):
    h = _Harness(monkeypatch)
    h.set_free(140)
    h.run(61)
    state = guard._read_state()
    vol = state["disk"]["volumes"]["C:"]
    assert vol["level"] == "low" and vol["role"] == "data" and vol["free_pct"] == 14.0
    lines = guard._disk_status_lines(state)
    assert lines and "low (14.0% / 140.0 GB free on C:, data)" in lines[0]
    assert h.g.disk.worst() == {"free_pct": 14.0, "level": "low"}
    assert "disk_watch" in guard.GUARD_FEATURES


def test_watched_volumes_are_the_data_drive_and_dockers_when_apart(monkeypatch, tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    docker = tmp_path / "docker"
    docker.mkdir()
    monkeypatch.setattr(guard, "_data_dir_guess", lambda: data)
    g = SimpleNamespace(log=lambda *a, **k: None)
    watch = guard.DiskWatch(g)

    monkeypatch.setattr(guard, "_docker_disk_dir", lambda: None)
    assert [role for _k, role, _p in watch.watched()] == ["data"]

    monkeypatch.setattr(guard, "_docker_disk_dir", lambda: docker)
    assert [role for _k, role, _p in watch.watched()] == ["data"]  # same volume

    monkeypatch.setattr(guard, "_same_volume", lambda a, b: False)
    assert [role for _k, role, _p in watch.watched()] == ["data", "docker"]

    # A data folder not made yet is watched through its nearest parent.
    monkeypatch.setattr(guard, "_data_dir_guess", lambda: tmp_path / "not" / "yet")
    monkeypatch.setattr(guard, "_docker_disk_dir", lambda: None)
    assert watch.watched()[0][2] == tmp_path


@pytest.mark.parametrize("value", ["off", "0", "False", " no "])
def test_off_spellings(monkeypatch, value):
    monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", value)
    assert guard.DiskWatch(SimpleNamespace(log=None)).mode() == "off"
