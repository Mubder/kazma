"""A refusal before anything ran is correctable, not tool death (plan D1).

Live 2026-11-04: a research turn asked for a file under a wrong path three
times, each refused before the tool ran ("not found", "outside the
workspace"), and the hard-failure breaker ended the turn as if the tools
were dying. A refusal tells the model what to change; it now counts on its
own allowance (``REFUSAL_ALLOWANCE`` rounds, reset by any success), the
same call refused twice stops the turn at once with the refusal kept, and
hard failures still trip at ``HARD_FAILURE_THRESHOLD``.

The registry and the unified executor mark every error returned before the
tool ran with ``refused``; the class gate below holds every error result
they build to that, or to a declared post-invocation reason.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from kazma_core.agent.tool_loop_breaker import (
    HARD_FAILURE_THRESHOLD,
    REFUSAL_ALLOWANCE,
    ToolOutcome,
    classify_tool_result,
    tool_signature,
    update_breaker,
)
from kazma_core.agent.tool_refusal import RefusedBeforeEffect
from kazma_core.agent.tool_registry import LocalToolRegistry

ROOT = Path(__file__).resolve().parents[1]


def _refused(i: int, name: str = "file_read") -> dict[str, Any]:
    return {"tool_call_id": f"c{i}", "name": name, "content": f"Error: missing required argument 'sheet{i}'",
            "is_error": True, "refused": True}


def _hard(i: int) -> dict[str, Any]:
    return {"tool_call_id": f"h{i}", "name": "web_fetch", "content": "Error: connection reset",
            "is_error": True}


def _ok(i: int) -> dict[str, Any]:
    return {"tool_call_id": f"o{i}", "name": "file_read", "content": "hello", "is_error": False}


def _run(rounds: list[list[dict[str, Any]]], sigs: dict[str, str] | None = None):
    hard = 0
    refusals = 0
    known: list[str] = []
    state = None
    out = None
    for batch in rounds:
        state, out = update_breaker(hard, batch, previous_refusals=refusals,
                                    previous_refused_signatures=known, signatures_by_id=sigs)
        if state.tripped:
            return state, out
        hard = state.consecutive_hard_rounds
        refusals = state.consecutive_refusal_rounds
        known = state.refused_signatures
    return state, out


# ── classification ──────────────────────────────────────────────────────


def test_a_refused_error_is_its_own_class():
    assert classify_tool_result(_refused(1)) == ToolOutcome.REFUSED


def test_a_refused_result_with_unknown_effects_is_hard():
    assert classify_tool_result({**_refused(1), "effect_uncertain": True}) == ToolOutcome.HARD


def test_an_explicit_outcome_still_wins():
    assert classify_tool_result({**_refused(1), "outcome": "policy"}) == ToolOutcome.POLICY


def test_a_policy_worded_refusal_keeps_its_class():
    r = {**_refused(1), "content": "Safety: this action is not permitted by policy"}
    assert classify_tool_result(r) in (ToolOutcome.POLICY, ToolOutcome.REFUSED)
    assert classify_tool_result(r) != ToolOutcome.HARD


# ── the breaker ─────────────────────────────────────────────────────────


def test_three_different_refusals_do_not_end_the_turn():
    state, _ = _run([[_refused(i)] for i in range(HARD_FAILURE_THRESHOLD)])
    assert not state.tripped
    assert state.consecutive_hard_rounds == 0
    assert state.consecutive_refusal_rounds == HARD_FAILURE_THRESHOLD


def test_negative_control_the_same_errors_without_the_mark_end_the_turn():
    """The old classification: refusals counted as tool death."""
    rounds = [[{k: v for k, v in _refused(i).items() if k != "refused"}]
              for i in range(HARD_FAILURE_THRESHOLD)]
    state, _ = _run(rounds)
    assert state.tripped and state.tripped_by == "hard"


def test_a_success_resets_the_refusal_count():
    state, _ = _run([[_refused(1)], [_refused(2)], [_ok(1)]])
    assert not state.tripped and state.consecutive_refusal_rounds == 0


def test_the_allowance_ends_a_turn_of_refusals():
    state, out = _run([[_refused(i)] for i in range(REFUSAL_ALLOWANCE)])
    assert state.tripped and state.tripped_by == "refusals"
    assert out[0]["content"].startswith("Error: missing required argument")  # the refusal is kept
    assert "STOP calling tools" in out[0]["content"]


def test_the_same_call_refused_twice_ends_the_turn_at_once():
    args = {"path": "notes/missing.md"}
    sig = tool_signature("file_read", args)
    sigs = {"c1": sig, "c2": sig}
    state, out = _run([[_refused(1)], [_refused(2)]], sigs)
    assert state.tripped and state.tripped_by == "repeated_refusal"
    assert "sheet2" in out[0]["content"]
    assert "already refused" in out[0]["content"]


def test_different_arguments_are_not_a_repeat():
    sigs = {"c1": tool_signature("file_read", {"path": "a"}),
            "c2": tool_signature("file_read", {"path": "b"})}
    state, _ = _run([[_refused(1)], [_refused(2)]], sigs)
    assert not state.tripped


def test_hard_failures_still_trip_at_the_threshold():
    state, _ = _run([[_hard(i)] for i in range(HARD_FAILURE_THRESHOLD)])
    assert state.tripped and state.tripped_by == "hard"


def test_a_refusal_between_hard_failures_does_not_reset_them():
    """hard, refused, hard, hard: the dying tool still counts."""
    state, _ = _run([[_hard(1)], [_refused(1)], [_hard(2)], [_hard(3)]])
    assert state.tripped and state.tripped_by == "hard"


# ── the registry marks what it refused ─────────────────────────────────


def _registry(monkeypatch, func=None, *, name: str = "file_write") -> LocalToolRegistry:
    from kazma_core.swarm import safety as swarm_safety

    monkeypatch.setattr(swarm_safety, "get_safety", lambda: type("S", (), {"enabled": False})())
    reg = LocalToolRegistry()
    if func is not None:
        reg.register(name=name, description="test", category="test")(func)
    return reg


@pytest.mark.anyio
async def test_an_unknown_tool_is_refused(monkeypatch):
    out = await _registry(monkeypatch).execute("no_such_tool", {})
    assert out["is_error"] and out.get("refused") is True
    assert classify_tool_result(out) == ToolOutcome.REFUSED


@pytest.mark.anyio
async def test_a_refusal_before_effect_is_refused(monkeypatch):
    async def writer(path: str) -> str:
        raise RefusedBeforeEffect("path is outside the workspace")

    out = await _registry(monkeypatch, writer).execute("file_write", {"path": "x"})
    assert out.get("refused") is True and not out.get("effect_uncertain")


@pytest.mark.anyio
async def test_a_read_that_answers_with_an_error_is_refused(monkeypatch):
    async def reader(path: str) -> str:
        return "Error: file not found: x"

    out = await _registry(monkeypatch, reader, name="file_read").execute("file_read", {"path": "x"})
    assert out["is_error"] and out.get("refused") is True


@pytest.mark.anyio
async def test_negative_control_a_writer_that_failed_after_running_is_not_refused(monkeypatch):
    async def writer(path: str) -> str:
        raise RuntimeError("disk went away mid-write")

    out = await _registry(monkeypatch, writer).execute("file_write", {"path": "x"})
    assert out["is_error"] and not out.get("refused")
    assert classify_tool_result(out) == ToolOutcome.HARD


# ── the class gate: every error result the executors build is declared ─


# Errors built AFTER the tool was invoked: they may follow an effect, so
# they stay hard (or uncertain). Matched on the start of the content.
_POST_INVOCATION = {
    "tool_registry.py": (
        "content",                    # the tool's own answer (_is_err payload)
        "f\"Error: Tool '{tool_name}' rejected the arguments",  # TypeError in the call
        "f\"Error: Tool '{tool_name}' failed:",                 # an exception from the tool
        "f'Error: {last_exc}'",       # retries exhausted
    ),
    "manager.py": (),
}

_EXECUTORS = (
    ("kazma-core/kazma_core/agent/tool_registry.py", "LocalToolRegistry", "_execute_inner"),
    ("kazma-core/kazma_core/mcp/manager.py", "UnifiedToolExecutor", "execute"),
)


def _error_dicts(source: str, cls: str, fn: str) -> list[tuple[int, list[str], str]]:
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.ClassDef) and node.name == cls):
            continue
        for f in node.body:
            if not (isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == fn):
                continue
            for d in ast.walk(f):
                if not isinstance(d, ast.Dict):
                    continue
                keys = [k.value if isinstance(k, ast.Constant) else None for k in d.keys]
                if "is_error" not in keys:
                    continue
                content = ""
                if "content" in keys:
                    content = ast.unparse(d.values[keys.index("content")])
                found.append((d.lineno, keys, content))
    return found


def _undeclared(source: str, cls: str, fn: str, declared: tuple[str, ...]) -> list[str]:
    bad = []
    for lineno, keys, content in _error_dicts(source, cls, fn):
        if "refused" in keys:
            continue
        flat = " ".join(content.split())
        if any(flat.startswith(d) for d in declared):
            continue
        bad.append(f"line {lineno}: {flat[:80]}")
    return bad


@pytest.mark.parametrize(("path", "cls", "fn"), _EXECUTORS)
def test_every_error_an_executor_builds_is_refused_or_declared(path, cls, fn):
    source = (ROOT / path).read_text(encoding="utf-8")
    assert _error_dicts(source, cls, fn), "the gate found nothing to check"
    bad = _undeclared(source, cls, fn, _POST_INVOCATION[Path(path).name])
    assert not bad, (
        "An error result built before the tool ran must carry \"refused\": True "
        "(or be declared post-invocation here with why):\n" + "\n".join(bad)
    )


def test_negative_control_the_gate_finds_an_unmarked_refusal():
    planted = (
        "class LocalToolRegistry:\n"
        "    async def _execute_inner(self, tool_name, arguments):\n"
        "        if tool_name not in self._tools:\n"
        "            return {'content': f'Error: unknown {tool_name}', 'is_error': True}\n"
    )
    assert _undeclared(planted, "LocalToolRegistry", "_execute_inner",
                       _POST_INVOCATION["tool_registry.py"])


# ── through the real chat tool worker ──────────────────────────────────


class _Tracer:
    def trace_tool_execution(self, *a, **k):
        pass


class _RefusingExec:
    async def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"content": f"Error: missing required argument for {arguments}",
                "is_error": True, "refused": True}


@pytest.mark.anyio
async def test_the_tool_worker_keeps_the_turn_through_three_refusals():
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import initial_supervisor_state

    state = initial_supervisor_state()
    for i in range(HARD_FAILURE_THRESHOLD):
        state["tool_calls_pending"] = [
            {"id": f"t{i}", "name": "file_read", "arguments": {"path": f"draft-{i}.md"}}]
        out = await tool_worker_node(state, tool_executor=_RefusingExec(), tracer=_Tracer(),
                                     hitl_config={"enabled": False})
        assert not out.get("circuit_breaker_tripped"), f"round {i + 1} ended the turn"
        state.update({k: v for k, v in out.items() if k != "messages"})
    assert state["consecutive_refusals"] == HARD_FAILURE_THRESHOLD
    assert state["consecutive_tool_failures"] == 0

    # The same call refused again ends it at once, the refusal kept.
    state["tool_calls_pending"] = [
        {"id": "again", "name": "file_read", "arguments": {"path": "draft-0.md"}}]
    out = await tool_worker_node(state, tool_executor=_RefusingExec(), tracer=_Tracer(),
                                 hitl_config={"enabled": False})
    assert out.get("circuit_breaker_tripped")
    done = out.get("tool_calls_done") or []
    assert "missing required argument" in done[-1]["content"]
    assert "already refused" in done[-1]["content"]
