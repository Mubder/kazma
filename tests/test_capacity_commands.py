"""Capacity slash commands: /long, /long yolo, /unrestricted — independent knobs."""

from __future__ import annotations

from kazma_core.agent.capacity_commands import (
    apply_capacity_command,
    is_capacity_command,
    snapshot_capacity,
)
from kazma_core.agent.long_task import (
    consume_long_task_turn,
    disable_long_task,
    enable_long_task,
    is_long_task_active,
    resolve_turn_budgets,
)
from kazma_core.safety.yolo import disable_yolo, is_yolo_active


def _tid(name: str) -> str:
    return f"cap-{name}"


def setup_function() -> None:
    for name in ("combo", "long-only", "consume", "off-both", "help"):
        disable_long_task(_tid(name))
        disable_yolo(_tid(name), actor="test")


def test_is_capacity_command_requires_slash_on_web() -> None:
    assert is_capacity_command("/long yolo", require_slash=True) is True
    assert is_capacity_command("/unrestricted", require_slash=True) is True
    assert is_capacity_command("long on", require_slash=True) is False
    assert is_capacity_command("long on", require_slash=False) is True
    assert is_capacity_command("hello long", require_slash=False) is False


def test_long_does_not_enable_yolo() -> None:
    tid = _tid("long-only")
    r = apply_capacity_command(tid, "/long on", actor="t")
    assert r.handled is True
    assert r.long_active is True
    assert r.yolo_active is False
    assert is_yolo_active(tid) is False
    disable_long_task(tid)


def test_long_yolo_enables_both() -> None:
    tid = _tid("combo")
    r = apply_capacity_command(tid, "/long yolo", actor="t")
    assert r.handled is True
    assert r.long_active is True
    assert r.yolo_active is True
    assert "YOLO" in r.reply
    snap = snapshot_capacity(tid)
    assert snap["long_active"] is True
    assert snap["yolo_active"] is True
    assert snap["max_iterations"] >= 15
    disable_yolo(tid, actor="t")
    disable_long_task(tid)


def test_unrestricted_is_mission_plus_yolo() -> None:
    tid = _tid("combo")
    r = apply_capacity_command(tid, "/unrestricted", actor="t")
    assert r.handled is True
    assert r.long_active is True
    assert r.yolo_active is True
    budgets = resolve_turn_budgets(tid)
    assert budgets["mode"] == "mission"
    assert budgets["max_iterations"] >= 100
    disable_yolo(tid, actor="t")
    disable_long_task(tid)


def test_yolo_off_does_not_kill_budget() -> None:
    tid = _tid("combo")
    apply_capacity_command(tid, "/long yolo", actor="t")
    disable_yolo(tid, actor="t")
    assert is_long_task_active(tid) is True
    assert is_yolo_active(tid) is False
    disable_long_task(tid)


def test_long_off_does_not_clear_yolo() -> None:
    tid = _tid("combo")
    apply_capacity_command(tid, "/long yolo", actor="t")
    apply_capacity_command(tid, "/long off", actor="t")
    assert is_long_task_active(tid) is False
    assert is_yolo_active(tid) is True
    disable_yolo(tid, actor="t")


def test_unrestricted_off_clears_both() -> None:
    tid = _tid("off-both")
    apply_capacity_command(tid, "/unrestricted", actor="t")
    r = apply_capacity_command(tid, "/unrestricted off", actor="t")
    assert r.handled is True
    assert is_long_task_active(tid) is False
    assert is_yolo_active(tid) is False


def test_consume_does_not_kill_the_first_task_turn() -> None:
    """Regression: remaining_turns=1 + expire-on-0 in status killed /long
    on the first real prompt (the turn that should benefit)."""
    tid = _tid("consume")
    enable_long_task(tid, actor="t", preset="research", remaining_turns=1)
    assert is_long_task_active(tid) is True
    consume_long_task_turn(tid)
    # Still active for this turn
    assert is_long_task_active(tid) is True
    assert resolve_turn_budgets(tid)["max_iterations"] >= 15
    # Next user message expires it
    consume_long_task_turn(tid)
    assert is_long_task_active(tid) is False


def test_unknown_sub_returns_help() -> None:
    tid = _tid("help")
    r = apply_capacity_command(tid, "/long bananas", actor="t")
    assert r.handled is True
    assert "Usage" in r.reply
    disable_long_task(tid)
