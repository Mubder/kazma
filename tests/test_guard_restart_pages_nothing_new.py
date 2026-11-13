"""A restarted guard pages nothing the guard before it already paged.

A guard update restarts the guard while Kazma is paused. On 2026-11-12 and
11-13 each one sent the owner a second "Kazma supervision paused" six seconds
after the first -- the new guard read the same pause file and announced it
again -- and the new guard's disk watch, starting every drive at "ok", paged
"Disk space low on C:" again, six hours after the same page with nothing
changed. The state file now says what was announced: the pause (by its
signature) and each drive's level (a record younger than DISK_INHERIT_S).
Each case runs two Guard objects on one state file; each control shows the
old behaviour.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
GB = 1024 ** 3


def _load_guard():
    spec = importlib.util.spec_from_file_location("kazma_guard_restart_test", ROOT / "scripts" / "service" / "kazma_guard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = _load_guard()


def _guard(monkeypatch, pages: list[str]):
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
    monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
    monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
    g = guard.Guard()
    g.broker.wanted = False
    g.notify.send = pages.append
    return g


def _hold_until(g, *, then):
    """The guard's wait inside the pause: one step, then *then*."""
    def sleep(*_a, **_k):
        then()
    g._sleep = sleep


# -- the pause ---------------------------------------------------------------------------


def test_a_guard_restarted_while_paused_does_not_announce_the_pause_again(monkeypatch):
    pages: list[str] = []
    guard.write_pause("guard update", 600)
    first = _guard(monkeypatch, pages)
    _hold_until(first, then=lambda: setattr(first, "_stop", True))  # the old guard is stopped
    assert first._await_resume()
    second = _guard(monkeypatch, pages)
    _hold_until(second, then=guard.clear_pause)  # the update ends
    assert second._await_resume()
    heads = [p.splitlines()[0] for p in pages]
    assert sum("supervision paused" in h for h in heads) == 1, heads
    assert sum("supervision resumed" in h for h in heads) == 1, heads


def test_a_new_pause_after_a_resume_is_announced(monkeypatch):
    pages: list[str] = []
    for _ in range(2):
        guard.write_pause("guard update", 600)
        g = _guard(monkeypatch, pages)
        _hold_until(g, then=guard.clear_pause)
        assert g._await_resume()
    assert sum("supervision paused" in p for p in pages) == 2


def test_negative_control_without_the_record_the_pause_is_announced_twice(monkeypatch):
    pages: list[str] = []
    guard.write_pause("guard update", 600)
    first = _guard(monkeypatch, pages)
    _hold_until(first, then=lambda: setattr(first, "_stop", True))
    first._await_resume()
    guard._update_state(pause_announced=None)  # what the state file held before
    second = _guard(monkeypatch, pages)
    _hold_until(second, then=guard.clear_pause)
    second._await_resume()
    assert sum("supervision paused" in p for p in pages) == 2


# -- the disk watch ----------------------------------------------------------------------


class _Disk:
    def __init__(self, free_gb: float) -> None:
        self.free_gb = free_gb

    def __call__(self, _path):
        return SimpleNamespace(total=1000 * GB, used=int((1000 - self.free_gb) * GB), free=int(self.free_gb * GB))


def _watch(monkeypatch, pages: list[str], disk: _Disk):
    monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "auto")
    g = _guard(monkeypatch, pages)
    g.disk = guard.DiskWatch(g, reader=disk, paths=[("C:", "data", Path("C:/kazma-data"))])
    g.disk.tick()
    return g


def test_a_restarted_guard_does_not_page_a_low_drive_again(monkeypatch):
    pages: list[str] = []
    disk = _Disk(140)
    _watch(monkeypatch, pages, disk)
    _watch(monkeypatch, pages, disk)  # the guard restarted, the drive as it was
    assert [p for p in pages if "Disk space low on C:" in p] == [pages[0]]
    disk.free_gb = 400
    _watch(monkeypatch, pages, disk)
    assert "Disk space is back on C:" in pages[-1]  # the room coming back is still said


def test_an_old_record_is_paged_again_as_a_reminder(monkeypatch):
    pages: list[str] = []
    disk = _Disk(140)
    _watch(monkeypatch, pages, disk)
    state = guard._read_state()
    state["disk"]["updated"] -= guard.DISK_INHERIT_S + 60
    guard._update_state(disk=state["disk"])
    _watch(monkeypatch, pages, disk)
    assert sum("Disk space low on C:" in p for p in pages) == 2


def test_negative_control_without_inheriting_the_low_drive_is_paged_again(monkeypatch):
    pages: list[str] = []
    disk = _Disk(140)
    _watch(monkeypatch, pages, disk)
    monkeypatch.setattr(guard.DiskWatch, "_inherit", lambda self: None)
    _watch(monkeypatch, pages, disk)
    assert sum("Disk space low on C:" in p for p in pages) == 2
