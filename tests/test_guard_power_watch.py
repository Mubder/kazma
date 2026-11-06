"""The guard watches the power (resilience plan B2c).

The PC had no UPS: a power cut killed the database mid-write. With a USB UPS
the OS sees a battery, and the guard's ``PowerWatch``:

* pages once when the machine goes on battery;
* at 30 % stops Kazma cleanly (a pause of its own: the guard's graceful stop),
  paging once, before the UPS runs dry;
* when power is back lifts its own pause (never another one) and pages once,
  and Kazma starts as usual -- after a reboot too;
* does nothing on a machine with no battery.

A fake clock and a fake reader; each rule with a negative control.
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
    spec = importlib.util.spec_from_file_location("_power_guard", GUARD)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


guard = _load_guard()


class _Harness:
    def __init__(self, monkeypatch, tmp_path) -> None:
        self.now = 1000.0
        wall0 = time.time()
        monkeypatch.setattr(guard, "time", SimpleNamespace(
            monotonic=lambda: self.now, time=lambda: wall0 + self.now, sleep=lambda s: None))
        monkeypatch.setenv("KAZMA_GUARD_POWER_WATCH", "auto")
        monkeypatch.setenv("KAZMA_GUARD_DISK_WATCH", "off")
        monkeypatch.setenv("KAZMA_GUARD_DOCUMENT_BROKER", "off")
        monkeypatch.setenv("KAZMA_GUARD_PAUSE_FILE", str(tmp_path / "guard.paused"))
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_TOKEN", "")
        monkeypatch.setenv("KAZMA_GUARD_TELEGRAM_CHAT", "")
        self.reading: dict | None = {"on_battery": False, "percent": 100}
        self.g = guard.Guard()
        self.g.broker.wanted = False
        self.g.power = guard.PowerWatch(self.g, reader=lambda: self.reading)
        self.pages: list[str] = []
        self.g.notify.send = self.pages.append

    def run(self, seconds: float = 31.0) -> None:
        end = self.now + seconds
        while self.now < end:
            self.g._broker_tick()
            self.now += 1.0


def test_on_battery_pages_once_low_battery_stops_kazma_power_back_starts_it(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.run()
    assert h.pages == [] and h.g.power.state == "ac"

    h.reading = {"on_battery": True, "percent": 80}
    h.run(120)
    assert len(h.pages) == 1 and "On battery power (80%)" in h.pages[0]
    assert guard.read_pause() is None, "on battery alone, Kazma keeps running"

    h.reading = {"on_battery": True, "percent": 29}
    h.run(120)
    assert len(h.pages) == 2 and "Stopping Kazma: battery at 29%" in h.pages[1]
    pause = guard.read_pause()
    assert pause and pause["reason"].startswith(guard.POWER_PAUSE_PREFIX), "the guard's graceful stop"
    assert not pause["until"], "a power pause waits for the power, not a clock"

    h.reading = {"on_battery": False, "percent": 31}
    h.run(60)
    assert len(h.pages) == 3 and "Power is back" in h.pages[2] and "starts again" in h.pages[2]
    assert guard.read_pause() is None


def test_a_reboot_after_the_ups_ran_dry_lifts_the_power_pause(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    guard.write_pause(f"{guard.POWER_PAUSE_PREFIX} on battery at 12%", 0)
    h.g.power.on_guard_start()
    h.run()
    assert guard.read_pause() is None


def test_another_pause_is_never_lifted(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    guard.write_pause("maintenance: updates", 3600)
    h.reading = {"on_battery": True, "percent": 20}
    h.run()
    # Paused for maintenance already: the power watch adds no pause of its own...
    assert guard.read_pause()["reason"] == "maintenance: updates"
    h.reading = {"on_battery": False, "percent": 40}
    h.run()
    # ...and power back leaves the owner's pause in place.
    assert guard.read_pause()["reason"] == "maintenance: updates"


def test_no_battery_means_nothing_happens(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.reading = None
    h.run(300)
    assert h.pages == [] and h.g.power.state == "none" and guard.read_pause() is None


def test_negative_control_a_guard_without_the_watch_never_stops_kazma(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    h.g.power = None
    h.reading = {"on_battery": True, "percent": 5}
    h.run(300)
    assert h.pages == [] and guard.read_pause() is None


def test_the_switch_turns_it_off(monkeypatch, tmp_path):
    h = _Harness(monkeypatch, tmp_path)
    monkeypatch.setenv("KAZMA_GUARD_POWER_WATCH", "off")
    h.reading = {"on_battery": True, "percent": 5}
    h.run(120)
    assert h.pages == [] and guard.read_pause() is None


def test_the_heartbeat_carries_the_battery():
    body = guard.heartbeat_body({"power": {"on_battery": True, "percent": 44, "path": "C:/secret"}})
    assert b'"on_battery": true' in body.replace(b'":true', b'": true') or b'"on_battery":true' in body
    assert b"secret" not in body
