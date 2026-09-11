"""Industry eval pack — golden trajectories (no live LLM).

CI already collects ``tests/`` via ``scripts/fast_test.py``. This file is the
policy regression SoT: a prompt/supervisor change that breaks honesty, HITL,
hoist, or routing fails the merge gate.

Run just the pack::

    python scripts/eval_pack.py
    python -m pytest tests/test_eval_pack.py -q

**What this pack does and does not prove.** Be precise about this, because the
claim travels further than the code does.

It proves the *harness* cannot lie: that a dead turn is not synthesized over,
that system notes stay at the head, that a danger tool interrupts before it
runs, and that the prefix stays stable. The model is scripted — every case
feeds a canned ``LLMResponse`` list — so none of it proves a *live* model
behaves. That is a separate, still-open piece of work.

Within the harness the loop is closed end to end. The danger cases gate on the
list Kazma actually ships (``kazma.yaml`` ``safety.hitl.require_approval_for``,
read by :func:`shipped_danger_tools`), not on a list the fixture invents, and
:func:`test_every_shipped_danger_tool_interrupts` sweeps *all* of
``CANONICAL_DANGER_TOOLS`` rather than the handful the demo happens to use.
Drop a tool from the shipped config and this pack fails. The un-gated control
in :func:`test_a_non_danger_tool_is_not_gated` is what keeps that sweep
meaningful.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from kazma_core.llm_provider import LLMError, LLMResponse, ToolCall

GOLDEN = Path(__file__).resolve().parent / "fixtures" / "eval_pack.json"
SHIPPED_YAML = Path(__file__).resolve().parents[1] / "kazma.yaml"


def shipped_danger_tools() -> set[str]:
    """The danger list Kazma actually ships, read from ``kazma.yaml``.

    The pack used to synthesize ``require_approval_for`` from the fixture — so
    it proved the tool worker honours a list it was just handed, not that
    ``shell_exec`` is on the list a user gets. Reading the shipped file is what
    makes this an end-to-end claim instead of a tautology.
    """
    import yaml

    data = yaml.safe_load(SHIPPED_YAML.read_text(encoding="utf-8")) or {}
    listed = ((data.get("safety") or {}).get("hitl") or {}).get("require_approval_for")
    return set(listed or ())


def _load_cases() -> list[dict[str, Any]]:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    cases = data.get("cases") or []
    assert cases, "eval pack fixture is empty"
    return cases


class _HaltNever:
    def should_halt(self) -> bool:
        return False

    def record_cost(self, cost: float) -> None:
        pass

    def record_user_interaction(self) -> None:
        pass


class _NoCounterAuthority:
    async def check_and_enforce(self, state):
        return state


class _NoopTracer:
    def trace_llm_call(self, **kwargs):
        pass


class _CountingLLM:
    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        self.calls = 0
        self.chat_calls: list[dict[str, Any]] = []
        self._responses = list(responses or [])

    async def chat(self, messages=None, tools=None, model=None, **kwargs):
        self.calls += 1
        self.chat_calls.append({"messages": list(messages or []), "tools": tools})
        if self._responses:
            return self._responses.pop(0)
        return LLMResponse(content="fallback")


def _tool_calls(raw: list[dict[str, Any]] | None) -> list[ToolCall]:
    out: list[ToolCall] = []
    for tc in raw or []:
        out.append(
            ToolCall(
                id=str(tc.get("id") or "call"),
                name=str(tc.get("name") or ""),
                arguments=dict(tc.get("arguments") or {}),
            )
        )
    return out


def _scripted_from_case(case: dict[str, Any]) -> _CountingLLM:
    responses: list[LLMResponse] = []
    for step in case.get("llm") or []:
        responses.append(
            LLMResponse(
                content=str(step.get("content") or ""),
                tool_calls=_tool_calls(step.get("tool_calls")),
                finish_reason="tool_calls" if step.get("tool_calls") else "stop",
                usage={"total_tokens": 8},
            )
        )
    return _CountingLLM(responses)


@pytest.fixture(autouse=True)
def _eval_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KAZMA_LLM_STREAM", "0")
    monkeypatch.setenv("KAZMA_SEMANTIC_COMPACT", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    def _no_registry() -> None:
        raise RuntimeError("eval pack does not use the live model registry")

    monkeypatch.setattr(
        "kazma_core.model_registry.get_model_registry",
        _no_registry,
    )
    try:
        from kazma_core.memory import config as mem_cfg

        monkeypatch.setattr(mem_cfg, "memory_per_turn_enabled", lambda: False)
        monkeypatch.setattr(mem_cfg, "memory_v2_enabled", lambda: False)
    except Exception:
        pass


async def _run_supervisor(case: dict[str, Any], llm: Any) -> dict[str, Any]:
    from kazma_core.agent.graph_supervisor import supervisor_node

    return await supervisor_node(
        {
            "messages": [{"role": "user", "content": case["user"]}],
            "iteration": 0,
            "max_iterations": 8,
            "thread_id": f"eval-{case['id']}",
        },
        llm=llm,
        system_prompt="You are Kazma, an autonomous multi-platform AI agent.",
        tool_definitions=[],
        tool_executor=None,
        cost_breaker=_HaltNever(),
        authority=_NoCounterAuthority(),
        tracer=_NoopTracer(),
    )


@pytest.mark.eval
@pytest.mark.parametrize("case", _load_cases(), ids=lambda c: c["id"])
@pytest.mark.asyncio
async def test_eval_pack_case(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    kind = case["kind"]
    expect = case.get("expect") or {}

    if kind == "supervisor":
        llm = _scripted_from_case(case)
        out = await _run_supervisor(case, llm)
        if expect.get("next_node"):
            assert out.get("next_node") == expect["next_node"]
        pending = [p.get("name") for p in (out.get("tool_calls_pending") or [])]
        if "pending_tools" in expect:
            assert pending == expect["pending_tools"]
        blob = " ".join(
            str(m.get("content") or "")
            for m in (out.get("messages") or [])
            if isinstance(m, dict)
        )
        if expect.get("content_contains"):
            assert expect["content_contains"] in blob

    elif kind == "supervisor_error":
        err = case["error"]

        class _Boom:
            async def chat(self, *a, **k):
                raise LLMError(err["message"], transient=bool(err.get("transient")))

        out = await _run_supervisor(case, _Boom())
        assert out.get("turn_failed") is True
        assert out.get("next_node") == expect.get("next_node", "respond")
        blob = " ".join(
            str(m.get("content") or "")
            for m in (out.get("messages") or [])
            if isinstance(m, dict)
        )
        if expect.get("content_contains"):
            assert expect["content_contains"] in blob

    elif kind == "supervisor_capture":
        llm = _scripted_from_case(case)
        await _run_supervisor(case, llm)
        assert llm.chat_calls, "supervisor never called the LLM"
        sent = llm.chat_calls[0]["messages"]
        if expect.get("systems_at_head"):
            from kazma_core.llm_provider import hoist_system_messages

            hoisted = hoist_system_messages(sent)
            roles = [m.get("role") for m in hoisted if isinstance(m, dict)]
            first_user = roles.index("user")
            assert all(r == "system" for r in roles[:first_user])
            assert "system" not in roles[first_user:]
        needle = expect.get("llm_messages_contain")
        if needle:
            blob = "\n".join(str(m.get("content") or "") for m in sent)
            assert needle in blob

    elif kind == "respond":
        from kazma_core.agent.graph_respond import respond_node

        llm = _CountingLLM()
        out = await respond_node(dict(case["state"]), llm=llm)
        if expect.get("no_llm_call"):
            assert llm.calls == 0
        blob = " ".join(
            str(m.get("content") or "")
            for m in (out.get("messages") or [])
            if isinstance(m, dict)
        )
        if expect.get("content_contains"):
            assert expect["content_contains"] in blob

    elif kind == "pack":
        from kazma_core.prompt_cache import pack_system_messages

        packed = [pack_system_messages(h) for h in case["histories"]]
        if expect.get("prefix_identical"):
            assert packed[0][0]["content"] == packed[1][0]["content"] == case["identity"]

    elif kind == "tool_trace":
        from kazma_core.agent.graph_supervisor import supervisor_node

        llm = _scripted_from_case(case)
        out1 = await _run_supervisor(case, llm)
        pending = [p.get("name") for p in (out1.get("tool_calls_pending") or [])]
        if "first_pending" in expect:
            assert pending == expect["first_pending"]
        tool = (out1.get("tool_calls_pending") or [{}])[0]
        tr = case.get("tool_result") or {}
        messages = list(out1.get("messages") or [])
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tool.get("id") or "c_read",
                "name": tr.get("name") or tool.get("name"),
                "content": tr.get("content") or "",
            }
        )
        out2 = await supervisor_node(
            {
                "messages": messages,
                "iteration": 1,
                "max_iterations": 8,
                "thread_id": f"eval-{case['id']}",
                "tool_calls_pending": [],
            },
            llm=llm,
            system_prompt="You are Kazma, an autonomous multi-platform AI agent.",
            tool_definitions=[],
            tool_executor=None,
            cost_breaker=_HaltNever(),
            authority=_NoCounterAuthority(),
            tracer=_NoopTracer(),
        )
        if expect.get("second_node"):
            assert out2.get("next_node") == expect["second_node"]
        if "second_pending" in expect:
            pending2 = [p.get("name") for p in (out2.get("tool_calls_pending") or [])]
            assert pending2 == expect["second_pending"]
        blob = " ".join(
            str(m.get("content") or "")
            for m in (out2.get("messages") or [])
            if isinstance(m, dict)
        )
        if expect.get("second_contains"):
            assert expect["second_contains"] in blob

    elif kind == "hitl":
        from kazma_core.agent.graph_tool_worker import tool_worker_node

        executed: list[str] = []

        class _Exec:
            async def execute(self, name: str, arguments: dict) -> dict:
                executed.append(name)
                return {"content": "ran", "is_error": False}

        state = {
            "messages": [{"role": "user", "content": "write a file"}],
            "tool_calls_pending": [
                {
                    "id": "c1",
                    "name": case["tool"],
                    "arguments": dict(case.get("arguments") or {}),
                }
            ],
            "iteration": 1,
            "thread_id": f"eval-{case['id']}",
        }
        # Close the loop: gate on the list Kazma ships, and prove this tool is
        # on it. Handing the worker a fixture-built list only ever proved the
        # worker can read a list.
        from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

        shipped = shipped_danger_tools()
        tool = case["tool"]
        assert tool in shipped, (
            f"{tool} is an eval-pack danger case but is not in kazma.yaml "
            f"safety.hitl.require_approval_for — the shipped config would not gate it"
        )
        assert tool in CANONICAL_DANGER_TOOLS, (
            f"{tool} is an eval-pack danger case but is not in CANONICAL_DANGER_TOOLS"
        )
        hitl = {
            "enabled": True,
            "require_approval_for": sorted(shipped),
        }

        def _irq(payload: Any) -> Any:
            raise RuntimeError("HITL_INTERRUPT")

        monkeypatch.setattr("langgraph.types.interrupt", _irq)
        raised = False
        try:
            await tool_worker_node(
                state, tool_executor=_Exec(), tracer=_NoopTracer(), hitl_config=hitl
            )
        except RuntimeError as exc:
            raised = "HITL_INTERRUPT" in str(exc)
        if expect.get("interrupt"):
            assert raised, "danger tool must interrupt for HITL"
        if expect.get("tool_not_executed"):
            assert executed == []

    elif kind == "sanitize":
        from kazma_core.agent.graph_helpers import sanitize_tool_chains

        out = sanitize_tool_chains(list(case["messages"]))
        if expect.get("no_tool_calls_left"):
            assert not any(
                isinstance(m, dict) and m.get("tool_calls") for m in out
            )

    elif kind == "unusable":
        from kazma_core.agent.graph_helpers import is_unusable_assistant_content

        assert is_unusable_assistant_content(case["text"]) is bool(expect["unusable"])

    elif kind == "friendly_error":
        from kazma_core.retry import friendly_llm_error

        msg = friendly_llm_error(LLMError("boom", transient=False))
        if expect.get("content_prefix"):
            assert msg.startswith(expect["content_prefix"])

    elif kind == "commitment_remind":
        monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "1")
        monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "ops.db"))
        from datetime import datetime, timezone

        from kazma_core.safety.commitment import authorize_effect

        request_at = datetime.fromisoformat(
            str(case.get("request_at") or "2026-08-11T10:00:00+00:00")
        )
        if request_at.tzinfo is None:
            request_at = request_at.replace(tzinfo=timezone.utc)
        d = authorize_effect(
            str(case.get("tool") or "schedule_task"),
            dict(case.get("arguments") or {}),
            user_text=str(case.get("user") or ""),
            request_at=request_at,
            memory_beliefs=list(case.get("memory_beliefs") or []),
            thread_id=f"eval-{case['id']}",
            turn_id="turn1",
        )
        assert d.decision == expect.get("decision")
        if expect.get("timing_prefix"):
            assert d.rewritten_args is not None
            assert str(d.rewritten_args.get("timing") or "").startswith(
                expect["timing_prefix"]
            )

    else:
        pytest.fail(f"unknown eval kind: {kind}")


# ══════════════════════════════════════════════════════════════════════════
# Whole-surface danger gate
#
# The four `hitl` cases in the fixture are spot checks. They prove the tools
# the tape happens to demonstrate interrupt; they say nothing about the other
# fifty-three. This sweeps the entire shipped danger list through the real
# tool worker, so adding a tool to CANONICAL_DANGER_TOOLS without it actually
# gating is a failed merge rather than a discovery in production.
# ══════════════════════════════════════════════════════════════════════════


def _canonical_danger_tools() -> list[str]:
    from kazma_core.safety.hitl import CANONICAL_DANGER_TOOLS

    return sorted(CANONICAL_DANGER_TOOLS)


@pytest.mark.eval
@pytest.mark.parametrize("tool", _canonical_danger_tools())
@pytest.mark.asyncio
async def test_every_shipped_danger_tool_interrupts(
    tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every tool Kazma ships as dangerous must interrupt before it runs."""
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    assert tool in shipped_danger_tools(), (
        f"{tool} is in CANONICAL_DANGER_TOOLS but not in kazma.yaml "
        f"safety.hitl.require_approval_for"
    )

    executed: list[str] = []

    class _Exec:
        async def execute(self, name: str, arguments: dict) -> dict:
            executed.append(name)
            return {"content": "ran", "is_error": False}

    state = {
        "messages": [{"role": "user", "content": f"run {tool}"}],
        "tool_calls_pending": [{"id": "c1", "name": tool, "arguments": {}}],
        "iteration": 1,
        "thread_id": f"eval-danger-{tool}",
    }

    def _irq(payload: Any) -> Any:
        raise RuntimeError("HITL_INTERRUPT")

    monkeypatch.setattr("langgraph.types.interrupt", _irq)

    raised = False
    try:
        await tool_worker_node(
            state,
            tool_executor=_Exec(),
            tracer=_NoopTracer(),
            hitl_config={
                "enabled": True,
                "require_approval_for": sorted(shipped_danger_tools()),
            },
        )
    except RuntimeError as exc:
        raised = "HITL_INTERRUPT" in str(exc)

    assert raised, f"{tool} is shipped as danger but did not interrupt for HITL"
    assert executed == [], f"{tool} executed before approval"


@pytest.mark.eval
@pytest.mark.asyncio
async def test_a_non_danger_tool_is_not_gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Negative control for the sweep above.

    If ``tool_worker_node`` interrupted on everything, the 57 passing cases
    would prove nothing at all. A read tool must run straight through.
    """
    from kazma_core.agent.graph_tool_worker import tool_worker_node

    assert "file_read" not in shipped_danger_tools(), (
        "file_read is meant to be the un-gated control for this test"
    )

    executed: list[str] = []

    class _Exec:
        async def execute(self, name: str, arguments: dict) -> dict:
            executed.append(name)
            return {"content": "contents", "is_error": False}

    def _irq(payload: Any) -> Any:
        raise RuntimeError("HITL_INTERRUPT")

    monkeypatch.setattr("langgraph.types.interrupt", _irq)

    await tool_worker_node(
        {
            "messages": [{"role": "user", "content": "read a file"}],
            "tool_calls_pending": [
                {"id": "c1", "name": "file_read", "arguments": {"path": "x.txt"}}
            ],
            "iteration": 1,
            "thread_id": "eval-danger-control",
        },
        tool_executor=_Exec(),
        tracer=_NoopTracer(),
        hitl_config={
            "enabled": True,
            "require_approval_for": sorted(shipped_danger_tools()),
        },
    )

    assert executed == ["file_read"], "a read tool must not need approval"
