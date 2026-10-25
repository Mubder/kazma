"""An aborted request does not come back in the next turn.

Live 2026-10-20 (Telegram), test 28: "/abort" 32 s into "Write a 2,000-word
history of Kuwait" answered "I won't continue it unless you ask me to redo
it" -- and the next request ("Open https://example.com in the browser")
came back with the whole history. The abort's only trace was a system note,
and the OpenAI-compatible path moves every system note to the head of the
conversation (``hoist_system_messages``): the model saw the history request
with no answer, then the new request, and answered both. ``abort_messages``
now leaves the aborted request an answer that says it was cancelled.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from kazma_core.agent import steer
from kazma_core.agent.turn_input import build_turn_messages
from kazma_core.llm_provider import hoist_system_messages

HISTORY = [
    {"role": "user", "content": "Check swarm task task-88ae"},
    {"role": "assistant", "content": "Task completed: Kuwait Towers ..."},
    {"role": "user", "content": "Write a 2,000-word history of Kuwait"},
]
NEXT = "Open https://example.com in the browser and take a screenshot."


class _Graph:
    checkpointer = object()  # the rebuild reads history only from a saver

    def __init__(self, messages: list) -> None:
        self.messages = messages

    async def aget_state(self, config: object) -> SimpleNamespace:
        return SimpleNamespace(values={"messages": self.messages}, tasks=[], next=())


def _what_the_model_reads(after_abort: list) -> list[dict]:
    config = {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}
    turn = asyncio.run(build_turn_messages(_Graph(after_abort), config, user_text=NEXT))
    return hoist_system_messages([m for m in turn if isinstance(m, dict)])


def _unanswered_requests(messages: list[dict]) -> list[str]:
    """User messages followed by another user message: still pending."""
    talk = [m for m in messages if m.get("role") in ("user", "assistant")]
    return [str(a["content"]) for a, b in zip(talk, talk[1:])
            if a["role"] == "user" and b["role"] == "user"]


def test_the_aborted_request_is_answered_as_cancelled():
    read = _what_the_model_reads(steer.abort_messages(HISTORY))
    assert _unanswered_requests(read) == [], read
    history = next(i for i, m in enumerate(read) if m.get("content") == HISTORY[2]["content"])
    assert read[history + 1] == {"role": "assistant", "content": steer.ABORTED_ANSWER}
    assert any("[KAZMA ABORT]" in str(m.get("content")) for m in read)


def test_negative_control_the_marker_alone_leaves_it_pending():
    old = [*HISTORY, {"role": "system", "content": steer._abort_marker()}]
    read = _what_the_model_reads(old)
    assert _unanswered_requests(read) == [HISTORY[2]["content"]], read


def test_an_answered_turn_gets_no_second_answer():
    answered = [*HISTORY, {"role": "assistant", "content": "Kuwait's history began ..."}]
    out = steer.abort_messages(answered)
    assert [m["role"] for m in out[-2:]] == ["assistant", "system"]
    assert out[-2]["content"] == "Kuwait's history began ..."


def test_both_abort_paths_use_it():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    # The web abort's body is kazma_ui/turn_abort.py since 2026-10-25 (the
    # route and the run supervisor's Cancel share it).
    for rel in ("kazma-gateway/kazma_gateway/agent_handler/graph.py",
                "kazma-ui/kazma_ui/turn_abort.py"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "abort_messages(" in text and "_abort_marker()" not in text, rel
    route = (root / "kazma-ui/kazma_ui/sse_chat/__init__.py").read_text(encoding="utf-8")
    assert "return await abort_turn(thread_id, _get_graph(), session_id=session_id)" in route
