"""A shell command that cannot run is refused before the card, and a refusal
before anything ran is an ordinary error, never "effects unknown".

Live 2026-10-18 (Telegram): the approval card for

    grep -rIl ... . 2>/dev/null | head -40

said "BLOCKED: host shell is disabled by the execution policy" and still
asked the owner to approve it (the command's ``|`` and ``>`` are refused on
every install too). The owner approved; ``shell_exec`` refused before
starting anything; the registry marked the failure ``effect_uncertain`` and
the effect journal ended the turn: "Its effects are unknown ... this call
must not be repeated".
"""

from __future__ import annotations

from typing import Any

import pytest

from kazma_core.agent.graph_builder import tool_worker_node
from kazma_core.agent.state import initial_supervisor_state
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.safety import shell_preflight
from kazma_core.safety.shell_preflight import HOST_SHELL_OFF, _command_refusal

LIVE_COMMAND = ('grep -rIl --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=.venv '
                '-e "collected, not passes" -e "Kazma, counted" -e "collected_tests" . '
                '2>/dev/null | head -40')


def _host_shell(monkeypatch, allowed: bool) -> None:
    monkeypatch.setattr("kazma_core.safety.post_hitl.host_shell_allowed", lambda: allowed)


# ── the command's text ──────────────────────────────────────────────────


def test_the_live_command_is_refused_from_its_text_alone():
    assert "metacharacters" in (_command_refusal(LIVE_COMMAND) or "")
    assert _command_refusal("   ") == "Empty command"
    assert _command_refusal("git --version") is None


@pytest.mark.anyio
async def test_the_policy_comes_first(monkeypatch):
    _host_shell(monkeypatch, False)
    assert await shell_preflight.shell_refusal("git --version") == HOST_SHELL_OFF
    _host_shell(monkeypatch, True)
    assert await shell_preflight.shell_refusal("git --version") is None


# ── the registry: a refusal before effect is not uncertain ─────────────


def _registry_with(func, monkeypatch) -> LocalToolRegistry:
    """*func* as ``file_write`` (a registered mutator) in a private registry,
    with the swarm approval bus off."""
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())
    reg = LocalToolRegistry()
    reg.register(name="file_write", description="test", category="test")(func)
    return reg


@pytest.mark.anyio
async def test_a_refusal_before_effect_is_an_ordinary_error(monkeypatch):
    async def refusing_writer(path: str) -> str:
        raise RefusedBeforeEffect("path is outside the workspace")

    out = await _registry_with(refusing_writer, monkeypatch).execute("file_write", {"path": "x"})
    assert out["is_error"] and out["content"] == "Error: path is outside the workspace"
    assert not out.get("effect_uncertain")


@pytest.mark.anyio
async def test_negative_control_a_failure_after_invocation_stays_uncertain(monkeypatch):
    """The old shape (a returned "Error:") keeps the uncertainty: a returned
    failure does not prove that a mutator changed nothing."""
    async def refusing_writer(path: str) -> str:
        return "Error: path is outside the workspace"

    out = await _registry_with(refusing_writer, monkeypatch).execute("file_write", {"path": "x"})
    assert out["is_error"] and out.get("effect_uncertain") is True


# ── shell_exec itself ───────────────────────────────────────────────────


async def _shell(command: str) -> dict[str, Any]:
    from kazma_core.agent.tool_registry import get_tool_registry

    registry = get_tool_registry()
    tool = registry._tools["shell_exec"]
    reg = LocalToolRegistry()
    reg._tools["shell_exec"] = tool
    return await reg.execute("shell_exec", {"command": command})


@pytest.mark.anyio
@pytest.mark.parametrize(("allowed", "command", "says"), [
    (False, "git --version", "disabled by the execution policy"),
    (True, LIVE_COMMAND, "metacharacters"),
    (True, "curl https://example.com", "not in the allowed binary list"),
])
async def test_shell_exec_refusals_are_not_uncertain(monkeypatch, allowed, command, says):
    _host_shell(monkeypatch, allowed)
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())
    out = await _shell(command)
    assert out["is_error"] and says in out["content"], out
    assert not out.get("effect_uncertain"), out


@pytest.mark.anyio
async def test_a_path_refusal_inside_the_tools_try_is_not_swallowed(monkeypatch, tmp_path):
    """The outside-workspace refusal sits inside a try whose `except
    Exception: pass` would have hidden a raise."""
    _host_shell(monkeypatch, True)
    from kazma_core.swarm import safety as swarm_safety
    from kazma_core.tools import file_write

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())
    monkeypatch.setattr(file_write, "_get_workspace", lambda: tmp_path)
    outside = str(tmp_path.parent / "elsewhere.txt")
    out = await _shell(f"cat {outside}")
    assert out["is_error"] and ("outside" in out["content"] or "could not resolve" in out["content"]), out
    assert "execution failed" not in out["content"]
    assert not out.get("effect_uncertain")


# ── the chat tool worker: no card for a call that cannot run ───────────


class _Tracer:
    def trace_tool_execution(self, *a, **k):
        pass


class _Exec:
    def __init__(self):
        self.calls: list[str] = []

    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(name)
        return {"content": "ok", "is_error": False}


@pytest.mark.anyio
@pytest.mark.parametrize(("allowed", "command"), [(False, "git --version"), (True, LIVE_COMMAND)])
async def test_the_tool_worker_asks_no_approval_for_a_shell_call_that_cannot_run(
        monkeypatch, allowed, command):
    _host_shell(monkeypatch, allowed)
    cards: list[Any] = []

    def _card(payload):
        cards.append(payload)
        return {"approved": True}

    monkeypatch.setattr("langgraph.types.interrupt", _card, raising=False)
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [{"id": "s1", "name": "shell_exec", "arguments": {"command": command}}]
    executor = _Exec()
    result = await tool_worker_node(state, tool_executor=executor, tracer=_Tracer(),
                                    hitl_config={"enabled": True, "require_approval_for": []})
    assert cards == [], "no approval card for a call that cannot run"
    assert executor.calls == [], "nothing dispatched"
    done = result.get("tool_calls_done") or []
    assert [r["name"] for r in done] == ["shell_exec"]
    assert done[0]["is_error"] and done[0].get("outcome") == "policy"
    assert "no approval was asked" in done[0]["content"]


@pytest.mark.anyio
async def test_negative_control_a_runnable_shell_call_still_gets_its_card(monkeypatch):
    _host_shell(monkeypatch, True)
    cards: list[Any] = []

    def _card(payload):
        cards.append(payload)
        return {"approved": True}

    monkeypatch.setattr("langgraph.types.interrupt", _card, raising=False)
    state = initial_supervisor_state()
    state["tool_calls_pending"] = [{"id": "s1", "name": "shell_exec", "arguments": {"command": "git --version"}}]
    executor = _Exec()
    await tool_worker_node(state, tool_executor=executor, tracer=_Tracer(),
                           hitl_config={"enabled": True, "require_approval_for": []})
    assert len(cards) == 1 and executor.calls == ["shell_exec"]
