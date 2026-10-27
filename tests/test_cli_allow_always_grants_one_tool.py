"""``kazma ask``'s "a" answer (and ACP's "allow always") grants the tool asked
about for this chat, never YOLO for the whole thread (audit 2026-10-26
AUD-107).

"a=always" called ``enable_yolo(thread_id)``: one "a" on a ``file_write``
card let ``shell_exec``, ``git_push`` and every other danger tool of the chat
run without a question. It now stores the per-tool grant the web card's
"Allow tool" stores (``hitl_grants.grant_tool``); an always-ask tool (X
writes) is never granted and is offered y/N only.
"""

from __future__ import annotations

import inspect
import io
import uuid
from typing import Any

import pytest

HITL_ON: dict[str, Any] = {"enabled": True}


@pytest.fixture()
def thread_id() -> Any:
    from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id
    from kazma_core.safety.hitl_grants import clear_grants
    from kazma_core.safety.yolo import disable_yolo

    tid = f"cli-always-{uuid.uuid4().hex[:8]}"
    token = set_current_thread_id(tid)
    try:
        yield tid
    finally:
        reset_current_thread_id(token)
        clear_grants(tid, actor="test")
        disable_yolo(tid, actor="test")


def test_always_grants_only_the_tool_asked_about(thread_id: str) -> None:
    from kazma_core.cli.ask import _grant_asked_tools
    from kazma_core.safety.hitl import requires_approval
    from kazma_core.safety.yolo import is_yolo_active

    payload = {"tool": "file_write", "tools": [{"id": "c1", "name": "file_write", "args": {}}]}
    assert _grant_asked_tools(thread_id, payload, actor="cli-allow-always") == ["file_write"]
    assert requires_approval("file_write", HITL_ON) is False
    assert requires_approval("shell_exec", HITL_ON) is True
    assert is_yolo_active(thread_id) is False


def test_an_always_ask_tool_is_never_granted(thread_id: str) -> None:
    from kazma_core.cli.ask import _grant_asked_tools
    from kazma_core.safety.hitl import ALWAYS_HITL_TOOLS, requires_approval

    tool = sorted(ALWAYS_HITL_TOOLS)[0]
    payload = {"tool": tool, "tools": [{"name": tool}], "always_ask": True}
    assert _grant_asked_tools(thread_id, payload, actor="cli-allow-always") == []
    assert requires_approval(tool, HITL_ON) is True


def test_the_resume_loop_grants_and_never_turns_on_yolo() -> None:
    from kazma_core.cli import ask

    loop = inspect.getsource(ask._invoke_supervisor)
    assert "_grant_asked_tools" in loop
    assert "enable_yolo" not in loop


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_the_prompt_says_what_a_does_and_hides_it_for_always_ask(monkeypatch, capsys) -> None:
    from kazma_cli import ask as cli_ask

    monkeypatch.setattr(cli_ask.sys, "stdin", _Tty("a\n"))
    decision = cli_ask._hitl_decide_tty({"tool": "file_write"}, stdin_consumed=False)
    err = capsys.readouterr().err
    assert decision == {"approved": True, "always": True}
    assert "a=allow file_write for this chat" in err and "a=always" not in err

    monkeypatch.setattr(cli_ask.sys, "stdin", _Tty("a\n"))
    decision = cli_ask._hitl_decide_tty({"tool": "x_post", "always_ask": True}, stdin_consumed=False)
    assert "[y/N]" in capsys.readouterr().err
    assert decision == {"approved": False}


def test_negative_control_the_old_answer_opened_every_danger_tool(thread_id: str) -> None:
    from kazma_core.safety.hitl import requires_approval
    from kazma_core.safety.yolo import enable_yolo

    # What "a" did before AUD-107, after a file_write card.
    enable_yolo(thread_id, actor="cli-allow-always")
    assert requires_approval("shell_exec", HITL_ON) is False
