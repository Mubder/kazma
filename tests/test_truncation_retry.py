"""Regression: finish_reason='length' must trigger one transparent retry
with a doubled max_tokens instead of returning severed tool-call JSON.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _resp_payload(finish_reason: str, content: str = "", tool_args: str | None = None) -> dict:
    message: dict = {"content": content}
    if tool_args is not None:
        message["tool_calls"] = [{
            "id": "tc1",
            "function": {"name": "file_write", "arguments": tool_args},
        }]
    return {
        "choices": [{"message": message, "finish_reason": finish_reason}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _make_provider():
    from kazma_core.llm_provider import LLMConfig, LLMProvider

    cfg = LLMConfig(
        base_url="https://api.test/v1",
        api_key="k",
        model="test-model",
        max_tokens=4096,
    )
    return LLMProvider(cfg)


@pytest.mark.asyncio
async def test_length_finish_retries_with_doubled_tokens() -> None:
    provider = _make_provider()

    calls: list[dict] = []

    good_args = json.dumps({"path": "x.html", "content": "<html>ok</html>"})

    class FakeHTTP:
        async def post(self, url: str, json: dict):
            calls.append(json)
            r = MagicMock()
            r.raise_for_status = MagicMock()
            if len(calls) == 1:
                r.json = MagicMock(return_value=_resp_payload(
                    "length", tool_args='{"path": "x.html", "content": "<html'
                ))
            else:
                r.json = MagicMock(return_value=_resp_payload(
                    "tool_calls", tool_args=good_args,
                ))
            return r

    provider._http = FakeHTTP()
    provider._client_lock = None

    with patch.object(provider, "_get_client", AsyncMock(return_value=FakeHTTP())):
        resp = await provider.chat(messages=[{"role": "user", "content": "write a file"}])

    assert len(calls) == 2
    assert calls[1]["max_tokens"] == calls[0]["max_tokens"] * 2
    assert resp.finish_reason == "tool_calls"
    assert resp.tool_calls[0].arguments == {"path": "x.html", "content": "<html>ok</html>"}


@pytest.mark.asyncio
async def test_still_truncated_after_retry_returns_truncated() -> None:
    provider = _make_provider()

    class FakeHTTP:
        async def post(self, url: str, json: dict):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=_resp_payload("length", content="partial…"))
            return r

    with patch.object(provider, "_get_client", AsyncMock(return_value=FakeHTTP())):
        resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    # Retry happened once and gave up — no infinite loop
    assert resp.finish_reason == "length"


@pytest.mark.asyncio
async def test_no_retry_when_not_truncated() -> None:
    provider = _make_provider()
    calls = 0

    class FakeHTTP:
        async def post(self, url: str, json: dict):
            nonlocal calls
            calls += 1
            r = MagicMock()
            r.raise_for_status = MagicMock()
            r.json = MagicMock(return_value=_resp_payload("stop", content="done"))
            return r

    with patch.object(provider, "_get_client", AsyncMock(return_value=FakeHTTP())):
        resp = await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert calls == 1
    assert resp.content == "done"


@pytest.mark.asyncio
async def test_file_append_appends() -> None:
    from kazma_core.agent.tool_registry import (
        LocalToolRegistry,
        _graph_hitl_gate_ctx,
    )
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as td:
        target = pathlib.Path(td) / "big.html"
        reg = LocalToolRegistry()
        token = _graph_hitl_gate_ctx.set(True)
        try:
            r1 = await reg.execute(
                "file_write", {"path": str(target), "content": "<html>"}
            )
            r2 = await reg.execute(
                "file_append", {"path": str(target), "content": "<body>hi</body>"}
            )
            r3 = await reg.execute(
                "file_append", {"path": str(target), "content": "</html>"}
            )
        finally:
            _graph_hitl_gate_ctx.reset(token)

        assert r1["is_error"] is False
        assert r2["is_error"] is False
        assert r3["is_error"] is False
        assert target.read_text(encoding="utf-8") == "<html><body>hi</body></html>"
