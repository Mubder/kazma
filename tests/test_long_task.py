"""Long-task mode: budgets, enable/disable, independence from YOLO."""

from __future__ import annotations

import pytest

from kazma_core.agent.long_task import (
    PRESETS,
    clamp_iterations,
    derive_recursion_limit,
    disable_long_task,
    enable_long_task,
    format_status_message,
    is_long_task_active,
    long_task_status,
    resolve_turn_budgets,
)


def test_derive_recursion_aligns_with_research() -> None:
    # Research 40 must not be capped at 100 steps
    r = derive_recursion_limit(40)
    assert r >= 200
    assert r <= 500
    assert derive_recursion_limit(15) >= 100
    assert derive_recursion_limit(30) > derive_recursion_limit(15)


def test_clamp_iterations() -> None:
    assert clamp_iterations(3) == 5
    assert clamp_iterations(200) == 100
    assert clamp_iterations("40") == 40
    assert clamp_iterations("x") == 15


def test_enable_disable_thread(tmp_path, monkeypatch) -> None:
    from kazma_core import config_store as cs_mod

    # Isolate ConfigStore if possible
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    # Fresh module path — use enable on a fake thread
    tid = "test-thread-long-1"
    disable_long_task(tid)
    assert is_long_task_active(tid) is False

    st = enable_long_task(tid, actor="tester", preset="research")
    assert st["active"] is True
    assert st["max_iterations"] == PRESETS["research"]
    assert st["recursion_limit"] >= 200
    assert is_long_task_active(tid) is True

    budgets = resolve_turn_budgets(tid)
    assert budgets["max_iterations"] == PRESETS["research"]
    assert budgets["recursion_limit"] == st["recursion_limit"]

    disable_long_task(tid, actor="tester")
    assert is_long_task_active(tid) is False
    base = resolve_turn_budgets(tid)
    # Without long-task, still derives from baseline max_iterations
    assert base["recursion_limit"] == derive_recursion_limit(base["max_iterations"])


def test_custom_iterations() -> None:
    tid = "test-thread-long-custom"
    disable_long_task(tid)
    st = enable_long_task(tid, actor="t", max_iterations=50)
    assert st["max_iterations"] == 50
    assert st["preset"] == "custom"
    assert st["recursion_limit"] == derive_recursion_limit(50)
    disable_long_task(tid)


def test_format_status_message() -> None:
    tid = "test-thread-long-msg"
    disable_long_task(tid)
    off = format_status_message(tid)
    assert "OFF" in off or "off" in off.lower() or "OFF" in off
    enable_long_task(tid, actor="t", preset="deep")
    on = format_status_message(tid)
    assert "ON" in on
    assert "30" in on
    disable_long_task(tid)


def test_mission_mode_hard_wall_not_soft_40() -> None:
    """Mission must raise max_iterations + recursion past Research budget."""
    from kazma_core.agent.long_task import (
        is_mission_mode,
        mission_hard_rounds,
        mission_recursion_limit,
    )

    tid = "test-thread-mission-1"
    disable_long_task(tid)
    st = enable_long_task(tid, actor="tester", mode="mission")
    assert st["active"] is True
    assert st["mode"] == "mission"
    assert is_mission_mode(tid) is True
    # Real wall — not Research soft 40
    assert st["max_iterations"] >= mission_hard_rounds()
    assert st["max_iterations"] >= 100  # must beat budget clamp
    assert st["recursion_limit"] >= 500
    assert st["recursion_limit"] >= mission_recursion_limit() // 2
    budgets = resolve_turn_budgets(tid)
    assert budgets["mode"] == "mission"
    assert budgets["max_iterations"] == st["max_iterations"]
    assert budgets["recursion_limit"] == st["recursion_limit"]
    msg = format_status_message(tid)
    assert "MISSION" in msg or "mission" in msg.lower()
    disable_long_task(tid)
    assert is_mission_mode(tid) is False


def test_mission_preset_alias() -> None:
    tid = "test-thread-mission-alias"
    disable_long_task(tid)
    st = enable_long_task(tid, actor="t", preset="mission")
    assert st["mode"] == "mission"
    assert st["max_iterations"] >= 100
    disable_long_task(tid)


def test_continue_protocol_store_and_consume() -> None:
    from kazma_core.agent.long_task import (
        consume_continue_context,
        store_continue_context,
    )

    tid = "test-continue-thread"
    assert consume_continue_context(tid) is None
    store_continue_context(tid, summary="Found A and B in documents.", reason="test")
    ctx = consume_continue_context(tid)
    assert ctx is not None
    assert "Found A and B" in ctx
    assert "Do **not** re-do" in ctx or "not" in ctx.lower()
    # Consumed once
    assert consume_continue_context(tid) is None


def test_detect_tool_loop() -> None:
    from kazma_core.agent.long_task import detect_tool_loop, tool_call_signature

    sig = tool_call_signature("shell_exec", {"command": "ls"})
    hist = [sig, sig, sig]
    assert detect_tool_loop(hist) == sig
    assert detect_tool_loop([sig, sig]) is None


def test_baseline_research_settings_raise_recursion(monkeypatch) -> None:
    """Even without /long on, high max_iterations must raise recursion_limit."""
    from kazma_core.config_store import get_config_store

    cs = get_config_store()
    prev = cs.get("agent.max_iterations")
    try:
        cs.set("agent.max_iterations", 40, category="agent")
        # Ensure no thread long-task
        tid = "no-long-task-thread-xyz"
        disable_long_task(tid)
        b = resolve_turn_budgets(tid)
        assert b["max_iterations"] == 40
        assert b["recursion_limit"] >= 200
    finally:
        if prev is None:
            try:
                cs.delete("agent.max_iterations")
            except Exception:
                pass
        else:
            cs.set("agent.max_iterations", prev, category="agent")
