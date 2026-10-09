"""Gateway delivery must preserve the graph's finalized answer and one reply."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from kazma_gateway.gateway import IncomingMessage


class CompletedGraph:
    def __init__(self, answer):
        self.answer = answer
        self.values = {}

    async def aget_state(self, config):
        return SimpleNamespace(values=self.values, next=(), tasks=())

    async def ainvoke(self, state, config):
        self.values = {**state, "messages": [*state["messages"],
                       {"role": "assistant", "content": self.answer}]}
        return self.values


@pytest.mark.parametrize("platform", ["telegram", "discord", "slack"])
@pytest.mark.parametrize("answer", [
    "الإجمالي ٢٫٥٠ دولار والمتبقي ٣٫٥٠ دولارات.",
    '{"path":"exports/preview-only.json"}',
    "- Review approved.\n- Execution not started.",
])
async def test_terminal_answer_is_delivered_once_without_cultural_rewrite(monkeypatch, platform, answer):
    from kazma_core import cultural_context
    from kazma_gateway.agent_handler.graph import create_graph_handler
    from kazma_gateway.agent_handler.store import _InMemoryStore
    from kazma_ui.session_manager import get_session_manager

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")

    # The old gateway always adds an Eid suffix, even to requested bare JSON.
    # Exercise the actual completion path rather than a copy of its formatter.
    monkeypatch.setattr(cultural_context, "CulturalContext", lambda: SimpleNamespace(
        state=SimpleNamespace(is_ramadan=False, is_eid=True, is_national_day=False),
    ))
    tid = "terminal-delivery-" + uuid4().hex
    manager = SimpleNamespace(send=AsyncMock(), adapters=[])
    graph = CompletedGraph(answer)
    message = IncomingMessage(platform, f"{platform}:{uuid4().hex}",
                              "بناءً على البيانات، أعد الإجابة فقط دون تحية أو توقيع.",
                              context_metadata={"thread_id": tid, "chat_id": 42,
                                                "channel_id": "test-channel", "user_id": "test-user"})
    await create_graph_handler(graph=graph, manager=manager, store=_InMemoryStore())(message)
    manager.send.assert_awaited_once()
    sent = manager.send.call_args.args[0].text
    # These fixtures contain no Markdown that requires Telegram conversion.
    assert sent == answer
    session = get_session_manager().get_by_thread_id(tid)
    assert session is not None
    replies = [row["content"] for row in session.messages if row["role"] == "assistant"]
    assert replies == [answer]
    assert [row["content"] for row in graph.values["messages"] if row["role"] == "assistant"] == [answer]
