"""A stream that ends before its answer does is a failure, not an answer (FA-213).

The 2026-11 functional audit: a model stream that closed after two tokens --
no ``[DONE]``, no ``finish_reason`` (a close-delimited body, as an HTTP/1.0
or ``Connection: close`` upstream sends) -- was taken as the complete answer:
"AUDIT-2611 fault" was saved as the reply, with no warning and no retry. The
SSE iterator returned on end-of-stream exactly as on ``[DONE]``, and the
response builder defaulted the finish reason to "stop".

The real provider on a scripted transport; the negative control is the old
iterator.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from kazma_core import llm_provider
from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider
from kazma_core.retry import friendly_llm_error


def _chunk(obj) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def _tokens(*words) -> str:
    return "".join(_chunk({"choices": [{"delta": {"content": w}}]}) for w in words)


CUT = _tokens("AUDIT-2611 ", "fault")
FINISHED = CUT + _chunk({"choices": [{"delta": {}, "finish_reason": "stop"}]})
DONE_LINE = "data: [DONE]\n\n"
WHOLE = FINISHED + DONE_LINE


def _provider(monkeypatch, body: str) -> LLMProvider:
    prov = LLMProvider(LLMConfig(base_url="http://test", api_key="k", model="stub"))

    async def handler(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())

    transport = httpx.MockTransport(handler)

    async def get_client():
        return httpx.AsyncClient(transport=transport, base_url="http://test")

    async def no_gateway():
        return None

    monkeypatch.setattr(prov, "_get_client", get_client)
    monkeypatch.setattr(prov, "_sync_gateway_async", no_gateway)
    return prov


def _run(prov):
    async def go():
        shown, final = [], None
        async for delta in prov.chat_stream([{"role": "user", "content": "q"}]):
            if delta.content:
                shown.append(delta.content)
            if delta.response is not None:
                final = delta.response
        return shown, final

    return asyncio.run(go())


def test_a_cut_stream_is_a_transient_failure(monkeypatch):
    prov = _provider(monkeypatch, CUT)
    with pytest.raises(LLMError) as raised:
        _run(prov)
    assert raised.value.transient and raised.value.kind == "stream_truncated"
    text = friendly_llm_error(raised.value)
    assert text.startswith("⚠️") and "cut off" in text


@pytest.mark.parametrize("body", [FINISHED, WHOLE], ids=["finish-reason", "done"])
def test_a_stream_that_ends_is_an_answer(monkeypatch, body):
    shown, final = _run(_provider(monkeypatch, body))
    assert final.content == "AUDIT-2611 fault" and "".join(shown) == "AUDIT-2611 fault"


def test_the_old_iterator_could_not_tell_a_cut_from_an_end():
    """Negative control: the iterator before FA-213 returned on end-of-stream
    exactly as on [DONE], so a cut stream and an ended one gave the reader the
    same objects -- and the builder then defaulted the finish reason to "stop"."""
    async def old_iter(resp):
        async for line in resp.aiter_lines():
            data = line.strip()[5:].strip() if line.strip().startswith("data:") else ""
            if not data:
                continue
            if data == "[DONE]":
                return
            yield json.loads(data)

    async def read(iterator, body):
        resp = httpx.Response(200, content=body.encode())
        return [obj async for obj in iterator(resp)]

    ended = CUT + DONE_LINE
    assert asyncio.run(read(old_iter, CUT)) == asyncio.run(read(old_iter, ended))
    new = LLMProvider._iter_sse_json
    assert asyncio.run(read(new, CUT)) != asyncio.run(read(new, ended))
    assert llm_provider._SSE_DONE in asyncio.run(read(new, WHOLE))
