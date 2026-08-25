"""Token-streaming adapter + LiteLLM proxy egress (industry stack part 1)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider, LLMResponse
from kazma_core.llm_stream import (
    StreamDelta,
    bridged_event_stream,
    emit_token_delta,
    invoke_llm_chat,
    register_delta_queue,
    stream_enabled,
    unregister_delta_queue,
)


class _FakeStreamCM:
    """Async context manager standing in for ``httpx.AsyncClient.stream``."""

    def __init__(self, status: int = 200, lines: list[str] | None = None, body: bytes = b""):
        self.status_code = status
        self._lines = list(lines or [])
        self._body = body
        self.request = httpx.Request("POST", "http://fake.api/v1/chat/completions")

    async def __aenter__(self) -> _FakeStreamCM:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aread(self) -> bytes:
        return self._body


def _provider_with_stream(lines: list[str], status: int = 200, body: bytes = b"") -> LLMProvider:
    provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="test"))
    mock_client = AsyncMock()
    mock_client.is_closed = False
    mock_client.stream = MagicMock(return_value=_FakeStreamCM(status, lines, body))
    provider._http = mock_client
    return provider


class TestStreamEnabled:
    def test_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KAZMA_LLM_STREAM", raising=False)
        assert stream_enabled() is True

    def test_kill_switch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LLM_STREAM", "0")
        assert stream_enabled() is False


class TestDeltaQueue:
    @pytest.mark.asyncio
    async def test_emit_injects_on_chat_model_stream(self) -> None:
        q: asyncio.Queue = asyncio.Queue()
        register_delta_queue("t-1", q)
        try:
            emit_token_delta("Hello", thread_id="t-1")
            ev = q.get_nowait()
            assert ev["event"] == "on_chat_model_stream"
            assert ev["data"]["chunk"]["content"] == "Hello"
        finally:
            unregister_delta_queue("t-1")

    def test_emit_without_queue_is_noop(self) -> None:
        emit_token_delta("x", thread_id="missing")  # must not raise


class TestInvokeLlmChat:
    @pytest.mark.asyncio
    async def test_magicmock_uses_chat_not_stream(self) -> None:
        mock = MagicMock()
        mock.chat = AsyncMock(return_value=LLMResponse(content="ok"))
        result = await invoke_llm_chat(mock, messages=[{"role": "user", "content": "hi"}])
        assert result.content == "ok"
        mock.chat.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_real_provider_streams_and_emits(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"Hel"}}]}',
            'data: {"choices":[{"delta":{"content":"lo"},"finish_reason":"stop"}],'
            '"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}',
            "data: [DONE]",
        ]
        provider = _provider_with_stream(lines)
        q: asyncio.Queue = asyncio.Queue()
        register_delta_queue("tid-stream", q)
        try:
            from kazma_core.safety.hitl import reset_current_thread_id, set_current_thread_id

            tok = set_current_thread_id("tid-stream")
            try:
                result = await invoke_llm_chat(
                    provider,
                    messages=[{"role": "user", "content": "hi"}],
                )
            finally:
                reset_current_thread_id(tok)
        finally:
            unregister_delta_queue("tid-stream")
        assert result.content == "Hello"
        chunks = []
        while not q.empty():
            ev = q.get_nowait()
            chunks.append(ev["data"]["chunk"]["content"])
        assert chunks == ["Hel", "lo"]


class TestChatStream:
    @pytest.mark.asyncio
    async def test_assembles_content_and_usage(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"A"}}]}',
            'data: {"choices":[{"delta":{"content":"B"},"finish_reason":"stop"}],'
            '"model":"m1","usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6}}',
            "data: [DONE]",
        ]
        provider = _provider_with_stream(lines)
        deltas = [d async for d in provider.chat_stream([{"role": "user", "content": "x"}])]
        texts = [d.content for d in deltas if d.content]
        assert texts == ["A", "B"]
        final = deltas[-1].response
        assert isinstance(final, LLMResponse)
        assert final.content == "AB"
        assert final.finish_reason == "stop"
        assert final.usage.get("total_tokens") == 6

    @pytest.mark.asyncio
    async def test_assembles_tool_call_fragments(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1",'
            '"function":{"name":"file_read","arguments":""}}]}}]}',
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,'
            '"function":{"arguments":"{\\"path\\": \\"a.py\\"}"}}]},'
            '"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
        provider = _provider_with_stream(lines)
        deltas = [d async for d in provider.chat_stream([{"role": "user", "content": "x"}])]
        final = deltas[-1].response
        assert final.finish_reason == "tool_calls"
        assert len(final.tool_calls) == 1
        assert final.tool_calls[0].name == "file_read"
        assert final.tool_calls[0].arguments == {"path": "a.py"}

    @pytest.mark.asyncio
    async def test_hoists_system_messages_on_stream_payload(self) -> None:
        lines = [
            'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}',
            "data: [DONE]",
        ]
        provider = _provider_with_stream(lines)
        _ = [d async for d in provider.chat_stream([
            {"role": "user", "content": "hi"},
            {"role": "system", "content": "mid-stream note"},
        ])]
        sent = provider._http.stream.call_args.kwargs["json"]["messages"]
        assert [m["role"] for m in sent] == ["system", "user"]
        assert sent[0]["content"] == "mid-stream note"
        assert sent[1]["content"] == "hi"
        assert provider._http.stream.call_args.kwargs["json"]["stream"] is True

    @pytest.mark.asyncio
    async def test_http_4xx_falls_back_to_chat(self) -> None:
        provider = _provider_with_stream([], status=400, body=b'{"error":"no stream"}')
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "fallback"}, "finish_reason": "stop"}],
            "model": "test",
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()
        provider._http.post = AsyncMock(return_value=mock_resp)
        deltas = [d async for d in provider.chat_stream([{"role": "user", "content": "x"}])]
        assert deltas[-1].response.content == "fallback"
        provider._http.post.assert_awaited()

    @pytest.mark.asyncio
    async def test_429_is_transient(self) -> None:
        provider = _provider_with_stream([], status=429, body=b"rate limited")
        with pytest.raises(LLMError) as ei:
            _ = [d async for d in provider.chat_stream([{"role": "user", "content": "x"}])]
        assert ei.value.transient is True

    @pytest.mark.asyncio
    async def test_kill_switch_uses_chat(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LLM_STREAM", "0")
        provider = LLMProvider(LLMConfig(base_url="http://fake.api/v1", api_key="test"))
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "blocked-stream"}, "finish_reason": "stop"}],
            "model": "test",
            "usage": {},
        }
        mock_resp.raise_for_status = MagicMock()
        mock_client = AsyncMock()
        mock_client.is_closed = False
        mock_client.post = AsyncMock(return_value=mock_resp)
        provider._http = mock_client
        deltas = [d async for d in provider.chat_stream([{"role": "user", "content": "x"}])]
        assert deltas[-1].response.content == "blocked-stream"
        mock_client.post.assert_awaited()
        assert mock_client.stream.call_count == 0


class TestLiteLlmProxy:
    def test_generic_provider_rewrites_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        p = LLMProvider(LLMConfig(base_url="https://api.openai.com/v1", api_key="sk"))
        assert "4000" in p.config.base_url

    def test_anthropic_subclass_does_not_rewrite(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KAZMA_LITELLM_URL", "http://127.0.0.1:4000")
        from kazma_core.anthropic_llm import AnthropicProvider

        p = AnthropicProvider(LLMConfig(api_key="sk-ant", model="claude-sonnet-4"))
        assert "anthropic.com" in p.config.base_url


class TestBridgedEventStream:
    @pytest.mark.asyncio
    async def test_merges_graph_events_and_tokens(self) -> None:
        tid = "bridge-1"

        async def _src():
            emit_token_delta("tok", thread_id=tid)
            yield {"event": "on_chain_start", "name": "Supervisor", "data": {}}

        out = []
        async for ev in bridged_event_stream(tid, _src()):
            out.append(ev)
        kinds = [e["event"] for e in out]
        assert "on_chain_start" in kinds
        assert "on_chat_model_stream" in kinds
