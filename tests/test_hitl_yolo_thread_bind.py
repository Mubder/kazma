"""Regression: YOLO / tool grants must resolve via state.thread_id fallback.

Root cause of "YOLO Session acts like Approve once":
  requires_approval / is_yolo_active only looked at a ContextVar that the
  WebSocket resume path never set. After enabling YOLO for the thread, the
  next danger tool still interrupted because get_current_thread_id() was None.

Fix (graph_builder.tool_worker_node): if the ContextVar is empty, bind
state.thread_id before the safe/danger split so grants apply for the turn.
"""

from __future__ import annotations

import pytest

from kazma_core.safety.hitl import (
    get_current_thread_id,
    requires_approval,
    reset_current_thread_id,
    set_current_thread_id,
)
from kazma_core.safety.hitl_grants import clear_grants, grant_tool, has_tool_grant
from kazma_core.safety.yolo import disable_yolo, enable_yolo, is_yolo_active


@pytest.fixture(autouse=True)
def _clean_yolo_grants():
    tid = "thr-yolo-bind"
    try:
        disable_yolo(tid)
    except Exception:
        pass
    try:
        clear_grants(tid)
    except Exception:
        pass
    # Ensure no leaked ContextVar
    tok = set_current_thread_id(None)
    reset_current_thread_id(tok)
    yield
    try:
        disable_yolo(tid)
    except Exception:
        pass
    try:
        clear_grants(tid)
    except Exception:
        pass


def test_try_enable_yolo_downgrades_when_flag_off(monkeypatch) -> None:
    from kazma_core.safety.yolo import try_enable_yolo

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "0")
    st = try_enable_yolo("thr-yolo-bind", actor="test")
    assert st.get("downgraded") is True
    assert st.get("active") is False
    assert "ALLOW_YOLO=0" in (st.get("reason") or "")


def test_allow_yolo_zero_blocks_even_without_production(monkeypatch) -> None:
    """KAZMA_ALLOW_YOLO=0 must disable YOLO in lab mode, not only in production."""
    from kazma_core.safety.yolo import yolo_allowed

    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "0")
    assert yolo_allowed() is False
    monkeypatch.setenv("KAZMA_ALLOW_YOLO", "1")
    assert yolo_allowed() is True
    monkeypatch.delenv("KAZMA_ALLOW_YOLO", raising=False)
    assert yolo_allowed() is True
    monkeypatch.setenv("KAZMA_PRODUCTION", "1")
    assert yolo_allowed() is False


def test_yolo_requires_context_or_is_ignored():
    tid = "thr-yolo-bind"
    enable_yolo(tid, actor="test")
    assert is_yolo_active(tid) is True
    assert get_current_thread_id() is None

    cfg = {"enabled": True, "require_approval_for": {"shell_exec", "file_write"}}
    # Without ContextVar, requires_approval cannot see YOLO
    assert requires_approval("shell_exec", cfg) is True

    tok = set_current_thread_id(tid)
    try:
        assert requires_approval("shell_exec", cfg) is False
        assert requires_approval("file_write", cfg) is False
    finally:
        reset_current_thread_id(tok)


def test_tool_grant_same_binding():
    tid = "thr-yolo-bind"
    grant_tool(tid, "shell_exec", actor="test")
    assert has_tool_grant(tid, "shell_exec") is True

    cfg = {"enabled": True, "require_approval_for": {"shell_exec", "file_write"}}
    assert requires_approval("shell_exec", cfg) is True  # no ContextVar

    tok = set_current_thread_id(tid)
    try:
        assert requires_approval("shell_exec", cfg) is False
        assert requires_approval("file_write", cfg) is True  # not granted
    finally:
        reset_current_thread_id(tok)


@pytest.mark.asyncio
async def test_tool_worker_binds_state_thread_id_for_yolo(monkeypatch):
    """tool_worker_node must treat danger tools as safe when state.thread_id has YOLO."""
    from kazma_core.agent.graph_builder import tool_worker_node
    from kazma_core.agent.state import PendingToolCall

    tid = "thr-yolo-bind"
    enable_yolo(tid, actor="test")
    assert get_current_thread_id() is None

    executed: list[str] = []

    class _Exec:
        async def execute(self, name, args):
            executed.append(name)
            return {"content": "ok", "is_error": False}

    class _Tracer:
        def trace_tool_execution(self, **kwargs):
            pass

    state = {
        "thread_id": tid,
        "messages": [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "shell_exec",
                            "arguments": '{"command": "echo hi"}',
                        },
                    }
                ],
            }
        ],
        "tool_calls_pending": [
            PendingToolCall(id="c1", name="shell_exec", arguments={"command": "echo hi"})
        ],
        "tool_calls_done": [],
        "tool_results": {},
        "consecutive_tool_failures": 0,
    }

    # Avoid real interrupt — if YOLO binding fails, interrupt would be called.
    interrupted = {"called": False}

    def _fake_interrupt(payload):
        interrupted["called"] = True
        raise AssertionError(f"interrupt should not run under YOLO: {payload}")

    # interrupt is imported from langgraph INSIDE the function (local import,
    # not a module-level graph_builder attribute) — patch the source module.
    import langgraph.types as lg_types

    monkeypatch.setattr(lg_types, "interrupt", _fake_interrupt)

    hitl_cfg = {
        "enabled": True,
        "require_approval_for": {"shell_exec", "file_write", "file_delete"},
    }

    # tool_worker expects pending list already on state via earlier code path —
    # call with tool_calls_pending set (the node reads state tool_calls_pending).
    result = await tool_worker_node(
        state,
        tool_executor=_Exec(),
        tracer=_Tracer(),
        hitl_config=hitl_cfg,
    )

    assert interrupted["called"] is False, "YOLO must skip interrupt"
    assert "shell_exec" in executed
    assert result.get("tool_calls_pending") == []
    # ContextVar must not leak after the node returns
    assert get_current_thread_id() is None
