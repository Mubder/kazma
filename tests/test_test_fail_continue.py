"""TESTS FAILED must not route to a user-facing answer."""

from __future__ import annotations

from kazma_core.agent.graph_supervisor import _last_tool_tests_failed


def test_detects_failed_verify() -> None:
    msgs = [
        {"role": "user", "content": "fix tests"},
        {"role": "tool", "content": "Patched app.py\nTESTS FAILED\n1 failed"},
    ]
    assert _last_tool_tests_failed(msgs) is True


def test_passed_is_not_failed() -> None:
    msgs = [{"role": "tool", "content": "Patched app.py\nTESTS PASSED"}]
    assert _last_tool_tests_failed(msgs) is False


def test_user_turn_resets() -> None:
    msgs = [
        {"role": "tool", "content": "TESTS FAILED"},
        {"role": "user", "content": "never mind"},
    ]
    assert _last_tool_tests_failed(msgs) is False
