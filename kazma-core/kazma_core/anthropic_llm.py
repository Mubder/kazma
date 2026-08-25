"""Anthropic Messages API provider — native Claude support.

The generic ``LLMProvider`` always sends ``Authorization: Bearer`` to
``/chat/completions``, which Anthropic's native API rejects. This class
talks to the Anthropic ``/v1/messages`` endpoint with the correct
``x-api-key`` + ``anthropic-version`` headers and the Messages schema
(system is top-level, content is a list of typed blocks, tool calls use
``tool_use``/``tool_result`` blocks).

It implements the same ``chat(...)`` interface as ``LLMProvider`` so the
agent loop needs no changes. ``model_registry.get_client()`` returns it
when the active provider is ``"anthropic"``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import httpx

from kazma_core.llm_provider import (
    LLMConfig,
    LLMError,
    LLMProvider,
    LLMResponse,
    ToolCall,
    retry_after_seconds,
)
from kazma_core.llm_stream import StreamDelta

logger = logging.getLogger(__name__)

_API_BASE = "https://api.anthropic.com/v1"
_ANTHROPIC_VERSION = "2023-06-01"

# Claude model → approx cost per 1M tokens (USD), input/output. Update as
# Anthropic changes pricing. Used only for cost accounting, not billing.
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-opus-4": (15.0, 75.0),
    "claude-sonnet-4": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-5-haiku": (0.8, 4.0),
    "claude-3-opus": (15.0, 75.0),
    "claude-3-haiku": (0.25, 1.25),
}


class AnthropicProvider(LLMProvider):
    """Native Anthropic Messages API client."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        super().__init__(config)
        self.config.base_url = _API_BASE
        if not self.config.api_key or self.config.api_key == "not-needed":
            self.config.api_key = os.getenv("ANTHROPIC_API_KEY", "")
        self._http: httpx.AsyncClient | None = None
        logger.info("AnthropicProvider initialized: model=%s", self.config.model)

    async def _get_client(self) -> httpx.AsyncClient:
        # Recreate if closed externally too (mirror LLMProvider._get_client) —
        # a bare `is None` check reused a dead client after external close
        # (audit finding).
        if self._http is None or getattr(self._http, "is_closed", False):
            self._http = httpx.AsyncClient(
                base_url=_API_BASE,
                headers={
                    "x-api-key": self.config.api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._http

    # ── Format translation ───────────────────────────────────────────

    @staticmethod
    def _split_system(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
        """Pull leading system messages into a single top-level system string.

        Anthropic puts system content at the top level, not in the message
        list. Concatenate all role=system entries.
        """
        system_parts: list[str] = []
        convo: list[dict[str, Any]] = []
        for m in messages:
            if m.get("role") == "system":
                c = m.get("content")
                if isinstance(c, str):
                    system_parts.append(c)
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and block.get("type") == "text":
                            system_parts.append(block.get("text", ""))
            else:
                convo.append(m)
        return "\n\n".join(system_parts), convo

    @staticmethod
    def _convert_message(m: dict[str, Any]) -> dict[str, Any]:
        """Convert one OpenAI-format message to Anthropic content-block form."""
        role = m.get("role", "user")
        content = m.get("content")
        # Already a list of blocks (multimodal) — coerce block types.
        if isinstance(content, list):
            blocks: list[dict[str, Any]] = []
            for b in content:
                if not isinstance(b, dict):
                    continue
                btype = b.get("type")
                if btype == "text":
                    blocks.append({"type": "text", "text": b.get("text", "")})
                elif btype == "image_url":
                    # OpenAI image_url → Anthropic image source.
                    url = (b.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        header, _, b64 = url.partition(",")
                        media = header.split(";")[0].split(":")[-1] or "image/png"
                        blocks.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": media, "data": b64},
                        })
                elif btype == "tool_result":
                    blocks.append(b)
                elif btype == "tool_use":
                    blocks.append(b)
            return {"role": role, "content": blocks or [{"type": "text", "text": ""}]}

        # Plain string content.
        text = content if isinstance(content, str) else json.dumps(content)
        return {"role": role, "content": text}

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert OpenAI function-tool defs to Anthropic's tool schema."""
        out: list[dict[str, Any]] = []
        for t in tools:
            if t.get("type") == "function":
                fn = t.get("function") or {}
                out.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters") or {"type": "object", "properties": {}},
                })
            elif "name" in t:  # already Anthropic-shaped
                out.append(t)
        return out

    # ── Main chat call ───────────────────────────────────────────────

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a Messages-API request and return an :class:`LLMResponse`.

        ``response_format`` is accepted for signature parity with
        ``LLMProvider.chat`` (structured outputs). Anthropic Messages has
        no equivalent field — it is ignored here.
        """
        _ = response_format
        from kazma_core.prompt_cache import build_anthropic_system, pack_system_messages, stamp_anthropic_tool_cache

        packed = pack_system_messages(messages)
        system = build_anthropic_system(packed)
        convo = [
            m for m in packed
            if isinstance(m, dict) and m.get("role") not in ("system", "developer")
        ]
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "messages": [self._convert_message(m) for m in convo],
        }
        if system:
            payload["system"] = system
        anthropic_tools = self._convert_tools(tools) if tools else None
        if anthropic_tools:
            payload["tools"] = stamp_anthropic_tool_cache(anthropic_tools)

        client = await self._get_client()
        start = time.monotonic()
        try:
            resp = await client.post("/messages", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            body = ""
            try:
                body = exc.response.text[:400]
            except Exception:  # noqa: BLE001
                pass
            # Mirror LLMProvider: 429 is transient with bounded Retry-After
            # backoff here; kind=rate_limit_exhausted so the supervisor does
            # not re-retry this provider (failover may still fire).
            if status_code == 429:
                retry_after = retry_after_seconds(
                    exc.response.headers if exc.response is not None else None
                )
                logger.warning(
                    "[Anthropic] Rate limited (429) — retrying after %.1fs",
                    retry_after,
                )
                for retry_attempt in range(3):
                    await asyncio.sleep(retry_after * (1.5 ** retry_attempt))
                    try:
                        resp = await client.post("/messages", json=payload)
                        if resp.status_code != 429:
                            resp.raise_for_status()
                            data = resp.json()
                            return self._parse_response(data, payload["model"], start)
                    except httpx.HTTPStatusError as retry_err:
                        sc = (
                            retry_err.response.status_code
                            if retry_err.response is not None
                            else 0
                        )
                        if sc != 429:
                            _detail = ""
                            try:
                                _detail = retry_err.response.text[:300]
                            except Exception:
                                _detail = ""
                            raise LLMError(
                                f"Anthropic API error during 429 backoff "
                                f"(HTTP {sc}): {_detail}",
                                transient=(sc >= 500),
                            ) from retry_err
                        continue
                raise LLMError(
                    f"Anthropic rate-limited after 3 retries: {body}",
                    transient=True,
                    kind="rate_limit_exhausted",
                ) from exc
            transient = status_code >= 500
            logger.error("[Anthropic] HTTP %d: %s", status_code, body)
            raise LLMError(
                f"Anthropic API error (HTTP {status_code}): {body}",
                transient=transient,
            ) from exc
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ) as exc:
            logger.error("[Anthropic] network failure: %s", exc)
            raise LLMError(
                f"Anthropic request failed (network): {exc}", transient=True
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.error("[Anthropic] request failed: %s", exc)
            raise LLMError(
                f"Anthropic request failed: {exc}", transient=False
            ) from exc

        data = resp.json()
        return self._parse_response(data, payload["model"], start)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        """Native Anthropic SSE (``stream: true`` on ``/messages``)."""
        from kazma_core.llm_stream import stream_enabled

        if not stream_enabled():
            resp = await self.chat(
                messages, tools, max_tokens, temperature, model, response_format
            )
            if resp.content:
                yield StreamDelta(content=resp.content)
            yield StreamDelta(response=resp)
            return

        from kazma_core.prompt_cache import build_anthropic_system, pack_system_messages, stamp_anthropic_tool_cache

        packed = pack_system_messages(messages)
        system = build_anthropic_system(packed)
        convo = [
            m for m in packed
            if isinstance(m, dict) and m.get("role") not in ("system", "developer")
        ]
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "messages": [self._convert_message(m) for m in convo],
            "stream": True,
        }
        if system:
            payload["system"] = system
        anthropic_tools = self._convert_tools(tools) if tools else None
        if anthropic_tools:
            payload["tools"] = stamp_anthropic_tool_cache(anthropic_tools)

        client = await self._get_client()
        start = time.monotonic()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        current_tool: dict[str, Any] | None = None
        stop_reason = ""
        usage_in = 0
        usage_out = 0

        try:
            async with client.stream("POST", "/messages", json=payload) as resp:
                if resp.status_code >= 400:
                    body = ""
                    try:
                        body = (await resp.aread()).decode("utf-8", errors="replace")
                    except Exception:
                        body = ""
                    transient = resp.status_code == 429 or resp.status_code >= 500
                    raise LLMError(
                        f"Anthropic stream error (HTTP {resp.status_code}): {body[:400]}",
                        transient=transient,
                    )
                event_name = ""
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    stripped = line.strip()
                    if stripped.startswith("event:"):
                        event_name = stripped[6:].strip()
                        continue
                    if not stripped.startswith("data:"):
                        continue
                    raw = stripped[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    etype = event_name or str(data.get("type") or "")
                    if etype == "content_block_start":
                        block = data.get("content_block") or {}
                        if block.get("type") == "tool_use":
                            current_tool = {
                                "id": block.get("id", ""),
                                "name": block.get("name", ""),
                                "arguments": "",
                            }
                    elif etype == "content_block_delta":
                        delta = data.get("delta") or {}
                        dtype = delta.get("type")
                        if dtype == "text_delta":
                            piece = str(delta.get("text") or "")
                            if piece:
                                text_parts.append(piece)
                                yield StreamDelta(content=piece)
                        elif dtype == "input_json_delta" and current_tool is not None:
                            current_tool["arguments"] += str(delta.get("partial_json") or "")
                    elif etype == "content_block_stop":
                        if current_tool is not None:
                            args_raw = current_tool.get("arguments") or "{}"
                            try:
                                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
                                if not isinstance(args, dict):
                                    args = {"_malformed": args}
                            except json.JSONDecodeError:
                                args = {"raw": args_raw}
                            tool_calls.append(
                                ToolCall(
                                    id=str(current_tool.get("id") or ""),
                                    name=str(current_tool.get("name") or ""),
                                    arguments=args,
                                )
                            )
                            current_tool = None
                    elif etype in ("message_delta", "message_stop"):
                        delta = data.get("delta") or {}
                        if delta.get("stop_reason"):
                            stop_reason = str(delta["stop_reason"])
                        usage = data.get("usage") or {}
                        if usage.get("input_tokens") is not None:
                            usage_in = int(usage.get("input_tokens") or 0)
                        if usage.get("output_tokens") is not None:
                            usage_out = int(usage.get("output_tokens") or usage_out)
        except LLMError:
            raise
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ) as exc:
            raise LLMError(
                f"Anthropic stream failed (network): {exc}",
                transient=True,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Anthropic] stream failed (%s) — falling back to chat()", exc)
            resp = await self.chat(messages, tools, max_tokens, temperature, model)
            if resp.content:
                yield StreamDelta(content=resp.content)
            yield StreamDelta(response=resp)
            return

        finish = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }.get(stop_reason, stop_reason or ("tool_calls" if tool_calls else "stop"))
        in_cost, out_cost = _MODEL_COSTS.get(payload["model"], (3.0, 15.0))
        cost = (usage_in / 1_000_000) * in_cost + (usage_out / 1_000_000) * out_cost
        assembled = LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            model=payload["model"],
            usage={
                "input_tokens": usage_in,
                "output_tokens": usage_out,
                "total_tokens": usage_in + usage_out,
            },
            cost_usd=cost,
            duration_ms=(time.monotonic() - start) * 1000,
        )
        yield StreamDelta(response=assembled)

    def _parse_response(
        self, data: dict[str, Any], model: str, start: float
    ) -> LLMResponse:
        """Map an Anthropic Messages response onto :class:`LLMResponse`."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        stop_reason = data.get("stop_reason", "")
        for block in data.get("content", []):
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.get("id", ""),
                        name=block.get("name", ""),
                        arguments=block.get("input") or {},
                    )
                )

        usage_in = (data.get("usage") or {}).get("input_tokens", 0)
        usage_out = (data.get("usage") or {}).get("output_tokens", 0)
        # Map Anthropic stop_reason → OpenAI finish_reason.
        finish = {
            "end_turn": "stop",
            "tool_use": "tool_calls",
            "max_tokens": "length",
            "stop_sequence": "stop",
        }.get(stop_reason, stop_reason or "stop")

        # Cost accounting.
        in_cost, out_cost = _MODEL_COSTS.get(model, (3.0, 15.0))
        cost = (usage_in / 1_000_000) * in_cost + (usage_out / 1_000_000) * out_cost

        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            model=model,
            usage={
                "input_tokens": usage_in,
                "output_tokens": usage_out,
                "total_tokens": usage_in + usage_out,
            },
            cost_usd=cost,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def close(self) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None
