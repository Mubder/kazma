"""Rewind and fork persist the same snapshot fields.

``/replay`` used to write only ``messages`` onto the live thread while
``/fork`` wrote the rest of the snapshot. Both mouths now call
``apply_snapshot_to_thread``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _snapshot() -> dict:
    return {
        "thread_id": "source-thread",
        "messages": [{"role": "user", "content": "keep the draft"}],
        "scratchpad": {"draft": "the non-message field"},
        "last_model": "model-a",
        "_gateway": {
            "thread_id": "source-thread",
            "chat_id": "999",
            "user_id": "operator",
            "message_id": "7",
        },
    }


class _Graph:
    def __init__(self) -> None:
        self.writes: list[tuple[dict, dict]] = []

    async def aget_state(self, config: dict) -> None:
        return None  # an empty thread: nothing to take back from memory

    async def aupdate_state(self, config: dict, values: dict) -> None:
        self.writes.append((config, values))


@pytest.mark.asyncio
async def test_rewind_and_fork_keep_scratchpad_and_thread_identity() -> None:
    from kazma_core.time_travel import apply_snapshot_to_thread

    source = _snapshot()
    graph = _Graph()

    rewind = await apply_snapshot_to_thread(graph, source, "source-thread")
    fork = await apply_snapshot_to_thread(graph, source, "fork-thread")

    assert source["thread_id"] == "source-thread"
    assert source["_gateway"]["chat_id"] == "999"
    assert source["scratchpad"]["draft"] == "the non-message field"

    rewind_cfg, rewind_vals = graph.writes[0]
    assert rewind_cfg["configurable"]["thread_id"] == "source-thread"
    assert rewind_vals["thread_id"] == "source-thread"
    assert rewind_vals["scratchpad"]["draft"] == "the non-message field"
    assert rewind_vals["messages"][0]["content"] == "keep the draft"
    assert rewind_vals["last_model"] == "model-a"
    assert rewind_vals["_gateway"]["thread_id"] == "source-thread"
    assert "chat_id" not in rewind_vals["_gateway"]
    assert "user_id" not in rewind_vals["_gateway"]
    assert "message_id" not in rewind_vals["_gateway"]
    assert rewind is rewind_vals

    fork_cfg, fork_vals = graph.writes[1]
    assert fork_cfg["configurable"]["thread_id"] == "fork-thread"
    assert fork_vals["thread_id"] == "fork-thread"
    assert fork_vals["scratchpad"]["draft"] == "the non-message field"
    assert fork_vals["messages"] == rewind_vals["messages"]
    assert fork_vals["scratchpad"] is not source["scratchpad"]
    assert fork_vals["_gateway"]["thread_id"] == "fork-thread"
    assert "chat_id" not in fork_vals["_gateway"]


def test_replay_and_fork_call_sites_use_that_payload() -> None:
    """The handlers must apply the shared payload, not a messages-only dict.

    Re-pointed 2026-10-14 (Time Travel TT-G1): the four surfaces call the one
    service, ``kazma_ui.thread_rewind`` (``rewind_thread`` / ``fork_thread``),
    and the service applies the shared payload. The gate in
    ``tests/test_time_travel_rewind.py`` holds that no other module calls
    ``apply_snapshot_to_thread``.
    """
    gateway = (
        _ROOT / "kazma-gateway" / "kazma_gateway" / "agent_handler" / "graph.py"
    ).read_text(encoding="utf-8")
    web = (_ROOT / "kazma-ui" / "kazma_ui" / "replay_routes.py").read_text(encoding="utf-8")
    service = (_ROOT / "kazma-ui" / "kazma_ui" / "thread_rewind.py").read_text(encoding="utf-8")
    replay = gateway.split("async def _handle_replay", 1)[1].split("async def _handle_fork", 1)[0]
    fork = gateway.split("async def _handle_fork", 1)[1].split("    # ── Register", 1)[0]
    restore = web.split("async def restore_snapshot", 1)[1].split("async def fork_snapshot", 1)[0]
    web_fork = web.split("async def fork_snapshot", 1)[1].split("@router.", 1)[0]
    for body, label, call in (
        (replay, "gateway replay", "rewind_thread("),
        (fork, "gateway fork", "fork_thread("),
        (restore, "web restore", "rewind_thread("),
        (web_fork, "web fork", "fork_thread("),
    ):
        assert call in body, label
        assert '{"messages":' not in body, label
    assert service.count("apply_snapshot_to_thread(graph, record.get_state()") == 2
