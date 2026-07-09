"""S4: Thin unit coverage for streaming.stream_chat."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kazma_core.streaming import StreamEvent, stream_chat


class _FakeResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.status_code = 200

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    def raise_for_status(self):
        return None


class _StreamCM:
    def __init__(self, resp: _FakeResponse):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


@pytest.mark.asyncio
async def test_stream_chat_yields_token_then_done():
    payload_line = "data: " + json.dumps(
        {"choices": [{"delta": {"content": "Hi"}, "finish_reason": None}]}
    )
    done_line = "data: [DONE]"

    client = MagicMock()
    client.stream = MagicMock(
        return_value=_StreamCM(_FakeResponse([payload_line, done_line]))
    )

    events: list[StreamEvent] = []
    async for ev in stream_chat(
        client=client,
        model="test-model",
        messages=[{"role": "user", "content": "hi"}],
    ):
        events.append(ev)

    types = [e.type for e in events]
    assert "token" in types
    assert "done" in types
    assert any(e.content == "Hi" for e in events if e.type == "token")


@pytest.mark.asyncio
async def test_stream_event_dataclass_defaults():
    ev = StreamEvent(type="token", content="x")
    assert ev.tool_call_name == ""
    assert ev.usage == {}
