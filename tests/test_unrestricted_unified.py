"""Unified /unrestricted — one clock, no turn metering, loud-once expiry.

Design contract (2026-09-09, born from the live self-check session):
  - ``/unrestricted`` arms mission + YOLO on a SINGLE sliding TTL
    (default 60 min of inactivity; every user turn refreshes BOTH records).
  - No per-turn metering: consume() never exhausts a unified record on
    turn count — the old 3-turn envelope killed the "unrestricted" promise.
  - Expiry (idle TTL only) is loud-once: the discovering consume() returns
    a user-facing notice; transports surface it instead of silently
    degrading to a normal turn.
  - ``/unrestricted off`` still disables both knobs.
"""

from __future__ import annotations

import time

import pytest
from kazma_core.agent.capacity_commands import apply_capacity_command
from kazma_core.agent.long_task import (
    consume_long_task_turn,
    disable_long_task,
    long_task_status,
)
from kazma_core.config_store import reset_config_store
from kazma_core.safety.yolo import (
    disable_yolo,
    slide_yolo_expiry,
    yolo_status,
)


def _tid(name: str) -> str:
    return f"unr-{name}"


@pytest.fixture(autouse=True)
def _isolated_store(monkeypatch):
    """Fresh volatile ConfigStore per test — no writes to the real settings."""
    monkeypatch.setenv("KAZMA_SECRET", "unrestricted-test-secret")
    reset_config_store()
    yield
    reset_config_store()


@pytest.fixture(autouse=True)
def _ttl(monkeypatch):
    monkeypatch.setenv("KAZMA_UNRESTRICTED_TTL_SECONDS", "3600")


def _cleanup(tid: str) -> None:
    disable_long_task(tid)
    disable_yolo(tid, actor="test")


def test_unrestricted_arms_one_clock() -> None:
    tid = _tid("arm")
    r = apply_capacity_command(tid, "/unrestricted", actor="t")
    assert r.handled and r.long_active and r.yolo_active
    assert "No turn limit" in r.reply

    st = long_task_status(tid)
    assert st["active"] is True
    assert st["unified"] is True
    assert st["mode"] == "mission"
    # Turn metering OFF — status reports no remaining-turn count.
    assert st["remaining_turns"] is None

    # YOLO grant shares the SAME expiry (one clock, ±1s).
    yst = yolo_status(tid)
    assert yst["active"] is True
    assert yst["expires_at"] is not None
    assert abs(float(yst["expires_at"]) - float(st["expires_at"])) <= 1.0
    _cleanup(tid)


def test_consume_never_exhausts_unified_on_turns() -> None:
    """Ten real turns later the mode is STILL armed (no 3-turn envelope)."""
    tid = _tid("many-turns")
    apply_capacity_command(tid, "/unrestricted", actor="t")
    for _ in range(10):
        notice = consume_long_task_turn(tid)
        assert notice is None
    assert long_task_status(tid)["active"] is True
    assert is_yolo_active_via_status(tid) is True
    _cleanup(tid)


def is_yolo_active_via_status(tid: str) -> bool:
    return bool(yolo_status(tid).get("active"))


def test_consume_slides_both_clocks() -> None:
    """Every real turn refreshes the single clock on BOTH records."""
    tid = _tid("slide")
    apply_capacity_command(tid, "/unrestricted", actor="t")
    before = float(long_task_status(tid)["expires_at"])
    y_before = float(yolo_status(tid)["expires_at"])

    time.sleep(1.1)
    assert consume_long_task_turn(tid) is None

    after = float(long_task_status(tid)["expires_at"])
    y_after = float(yolo_status(tid)["expires_at"])
    assert after > before  # slid forward
    assert y_after > y_before  # yolo slid with it
    assert abs(y_after - after) <= 1.0  # still ONE clock
    _cleanup(tid)


def test_idle_expiry_is_loud_once() -> None:
    tid = _tid("expire")
    # Arm with a 60s (minimum) TTL, then forge an already-expired clock —
    # the record shape is what matters, not the wall-clock wait.
    monkeypatcher = pytest.MonkeyPatch()
    monkeypatcher.setenv("KAZMA_UNRESTRICTED_TTL_SECONDS", "60")
    try:
        apply_capacity_command(tid, "/unrestricted", actor="t")
    finally:
        monkeypatcher.undo()

    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    raw = cs.get(f"long_task.{tid}")
    raw["expires_at"] = time.time() - 5
    cs.set(f"long_task.{tid}", raw, category="agent")

    notice = consume_long_task_turn(tid)
    assert notice is not None and "Unrestricted expired" in notice

    # Loud ONCE: the record is gone; the next consume is silent.
    assert consume_long_task_turn(tid) is None
    assert long_task_status(tid)["active"] is False
    _cleanup(tid)


def test_unrestricted_off_disables_both() -> None:
    tid = _tid("off")
    apply_capacity_command(tid, "/unrestricted", actor="t")
    r = apply_capacity_command(tid, "/unrestricted off", actor="t")
    assert r.long_active is False
    assert r.yolo_active is False
    _cleanup(tid)


def test_non_unified_long_still_meters_turns() -> None:
    """Plain /long keeps the old one-turn envelope (unchanged semantics)."""
    tid = _tid("legacy")
    apply_capacity_command(tid, "/long on", actor="t")
    st = long_task_status(tid)
    assert st["unified"] is False
    assert st["remaining_turns"] is not None
    consume_long_task_turn(tid)
    st2 = long_task_status(tid)
    assert st2["active"] is True  # status stays active for the consuming turn
    disable_long_task(tid)


def test_standalone_yolo_is_not_slid() -> None:
    """A standalone /yolo keeps its OWN clock — slide is unified-only."""
    tid = _tid("yolo-own")
    from kazma_core.safety.yolo import enable_yolo

    enable_yolo(tid, actor="t")
    yst = yolo_status(tid)
    assert yst["active"] is True
    expires = float(yst["expires_at"])

    slide_yolo_expiry(tid, expires + 9999)
    assert float(yolo_status(tid)["expires_at"]) == expires  # unchanged
    disable_yolo(tid, actor="t")
