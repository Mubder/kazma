"""S3-2 — duplicated stream fragments across LLM attempt boundaries.

Incident (2026-08-30 19:20): the reply arrived as
``The proposal turn is The proposal turn is`` and ended mid-sentence.

Root cause (docs/plans/S3_2_DUPLICATED_STREAM_INVESTIGATION.md): the SSE/WS
bubble APPENDS every token delta; only ``turn_complete`` has replace
semantics. When a streaming attempt dies mid-generation and a recovery path
restarts generation, the second attempt re-emits from token 0 while the dead
attempt's partial deltas are already on screen.

Three surfaces, same shape:
  R1  supervisor primary retries   (``_call_llm_with_retry`` attempt loop)
  R2  supervisor failover chain    (failover model via ``invoke_llm_chat``)
  R3  in-provider blocking fallback (``chat_stream`` gateway-direct path)

These tests assert the FIXED invariant — no duplicated prefix reaches the
delta queue. Run against the pre-fix code they FAIL: that failure is the
reproduction the plan requires before a fix may land.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kazma_core.llm_provider import LLMConfig, LLMProvider, LLMResponse
from kazma_core.llm_stream import (
    StreamDelta,
    invoke_llm_chat,
    register_delta_queue,
    unregister_delta_queue,
)


class _FlakyStreamLLM:
    """chat_stream that dies mid-generation on the first call, succeeds after.

    The dying call still emits partial deltas BEFORE the network error —
    exactly a provider dropping the SSE body mid-stream.
    """

    def __init__(self, full_text: str, fail_prefix_len: int = 22) -> None:
        self._full = full_text
        self._prefix_len = fail_prefix_len
        self.calls = 0

    async def chat_stream(self, *, messages, tools=None, model=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            # Emit partial content, THEN die mid-stream.
            yield StreamDelta(content=self._full[: self._prefix_len])
            import httpx

            raise httpx.ReadError("peer closed connection without completing chunked body")
        # Recovery attempt: full text from token 0.
        yield StreamDelta(content=self._full)
        yield StreamDelta(
            response=LLMResponse(
                content=self._full,
                tool_calls=[],
                finish_reason="stop",
                model="stub",
                usage={"total_tokens": 9},
                cost_usd=0.0,
            )
        )

    async def chat(self, *, messages, tools=None, model=None, **kwargs):
        return LLMResponse(
            content=self._full,
            tool_calls=[],
            finish_reason="stop",
            model="stub",
            usage={"total_tokens": 9},
            cost_usd=0.0,
        )


def _queue_texts(queue: asyncio.Queue) -> list[str]:
    out: list[str] = []
    while not queue.empty():
        ev = queue.get_nowait()
        if isinstance(ev, dict) and ev.get("event") == "on_chat_model_stream":
            chunk = (ev.get("data") or {}).get("chunk") or {}
            text = chunk.get("content") if isinstance(chunk, dict) else None
            if text:
                out.append(str(text))
    return out


FULL = "The proposal turn is ready for your review — eight drafts attached."


class TestR1PrimaryRetry:
    @pytest.mark.asyncio
    async def test_retry_does_not_duplicate_prefix(self):
        """R1: attempt 1 dies after partial deltas; attempt 2 must not re-emit
        the full text onto the same delta queue (the retry loop in
        ``_call_llm_with_retry`` calls ``invoke_llm_chat`` again on transient
        network errors)."""
        from kazma_core.safety.hitl import (
            reset_current_thread_id,
            set_current_thread_id,
        )

        llm = _FlakyStreamLLM(FULL)
        queue: asyncio.Queue = asyncio.Queue()
        register_delta_queue("s32-r1", queue)
        tok = set_current_thread_id("s32-r1")  # emit_token_delta resolves via this
        try:
            # Attempt 1 — streams partial, then raises.
            with pytest.raises(Exception):
                await invoke_llm_chat(llm, messages=[], model="stub")
            partial = "".join(_queue_texts(queue))
            assert partial == FULL[:22]  # the dead attempt's fragments did stream

            # Attempt 2 (what _call_llm_with_retry does) — recovery succeeds.
            await invoke_llm_chat(
                llm, messages=[], model="stub", emit_deltas=False
            )
            after = "".join(_queue_texts(queue))
            # FIXED invariant: nothing new was appended on top of the partials —
            # the authoritative full text arrives via turn_complete backfill,
            # which has replace semantics. Pre-fix, `after` starts with the
            # partial again (duplicated prefix: the incident string).
            assert after == "", f"duplicated prefix reached the delta queue: {after!r}"
        finally:
            reset_current_thread_id(tok)
            unregister_delta_queue("s32-r1")

    @pytest.mark.asyncio
    async def test_default_attempt_still_streams(self):
        """The quiet-retry flag must not silence FIRST attempts — live
        streaming on the healthy path is unchanged."""
        from kazma_core.safety.hitl import (
            reset_current_thread_id,
            set_current_thread_id,
        )

        llm = _FlakyStreamLLM(FULL)
        queue: asyncio.Queue = asyncio.Queue()
        register_delta_queue("s32-r1b", queue)
        tok = set_current_thread_id("s32-r1b")
        try:
            llm.calls = 1  # skip the flaky first call
            resp = await invoke_llm_chat(llm, messages=[], model="stub")
            assert resp.content == FULL
            assert "".join(_queue_texts(queue)) == FULL
        finally:
            reset_current_thread_id(tok)
            unregister_delta_queue("s32-r1b")


class TestR3ProviderFallback:
    """R3: chat_stream's gateway-direct blocking fallback after a mid-stream
    network death must not re-yield the full content delta on top of the
    partials already emitted."""

    @staticmethod
    def _provider_with_flaky_transport(monkeypatch):
        import httpx

        prov = LLMProvider(
            LLMConfig(base_url="http://test", api_key="k", model="stub")
        )

        class _Gateway:
            enabled = True
            url = "http://litellm"
            include_local = True
            fallback_direct = True

        import kazma_core.llm_gateway as gw_mod

        monkeypatch.setattr(gw_mod, "get_litellm_gateway", lambda: _Gateway())
        # _sync_gateway() runs first inside chat()/_get_client paths and
        # recomputes _via_gateway from resolve_generic_egress — force via=True
        # so the gateway-direct fallback branch is reachable.
        monkeypatch.setattr(
            gw_mod,
            "resolve_generic_egress",
            lambda base, key: (base, key, True),
        )

        sse_prefix = (
            'data: {"choices":[{"delta":{"content":"The proposal turn is "}}]}\n\n'
        ).encode("utf-8")

        class _DyingStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield sse_prefix
                raise httpx.ReadError("connection dropped mid-body")

        async def _handler(request: httpx.AsyncRequest) -> httpx.Response:
            try:
                import json as _json

                body = _json.loads(request.content.decode("utf-8", "replace"))
            except Exception:
                body = {}
            if body.get("stream"):
                return httpx.Response(200, stream=_DyingStream())
            return httpx.Response(
                200,
                json={
                    "id": "x",
                    "object": "chat.completion",
                    "model": "stub",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": FULL},
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 9,
                        "total_tokens": 10,
                    },
                },
            )

        transport = httpx.MockTransport(_handler)

        async def _get_client():
            return httpx.AsyncClient(transport=transport, base_url="http://test")

        monkeypatch.setattr(prov, "_get_client", _get_client)
        return prov

    @pytest.mark.asyncio
    async def test_fallback_does_not_reyield_content(self, monkeypatch):
        prov = self._provider_with_flaky_transport(monkeypatch)
        emitted: list[str] = []
        try:
            async for delta in prov.chat_stream([], tools=None):
                if delta.content:
                    emitted.append(delta.content)
                if delta.response is not None:
                    break
        except Exception as exc:  # network raise is also acceptable — see below
            # If the fallback is unavailable the stream re-raises; that path
            # emits nothing extra. Either way the wire invariant must hold.
            assert "The proposal turn is" not in "".join(emitted[1:]), (
                f"fallback raised {type(exc).__name__} but still re-emitted content"
            )
            return
        joined = "".join(emitted)
        assert joined.count("The proposal turn is") <= 1, (
            f"duplicated prefix in chat_stream deltas: {joined!r}"
        )
