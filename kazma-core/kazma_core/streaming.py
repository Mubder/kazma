"""Streaming LLM support — token-by-token streaming from OpenAI-compatible APIs.

Adds a stream_chat() method that yields tokens as they arrive, enabling
real-time WebSocket chat streaming in the WebUI.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

import httpx

logger = logging.getLogger(__name__)


@dataclass
class StreamEvent:
    """A single streaming event from the LLM."""

    type: str  # "token", "tool_call", "done", "error"
    content: str = ""
    tool_call_id: str = ""
    tool_call_name: str = ""
    tool_call_args: str = ""
    finish_reason: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0


async def stream_chat(
    client: httpx.AsyncClient,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = 4096,
    temperature: float = 0.7,
    input_cost_per_1m: float = 0.15,
    output_cost_per_1m: float = 0.60,
) -> AsyncGenerator[StreamEvent, None]:
    """Stream a chat completion, yielding tokens as they arrive.

    Uses the OpenAI streaming API (stream=true) with SSE parsing.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }

    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    start = time.monotonic()

    try:
        async with client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()

            # Accumulate tool calls across chunks
            tool_calls_acc: dict[int, dict[str, str]] = {}
            content_acc = ""
            finish_reason = ""
            usage: dict[str, int] = {}

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue

                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break

                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                choice = chunk.get("choices", [{}])[0]
                delta = choice.get("delta", {})
                finish_reason = choice.get("finish_reason") or finish_reason

                # Content token
                token = delta.get("content")
                if token:
                    content_acc += token
                    yield StreamEvent(type="token", content=token)

                # Tool call deltas
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {
                            "id": tc_delta.get("id", ""),
                            "name": "",
                            "arguments": "",
                        }
                    tc = tool_calls_acc[idx]
                    if tc_delta.get("id"):
                        tc["id"] = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if func.get("name"):
                        tc["name"] = func["name"]
                    if func.get("arguments"):
                        tc["arguments"] += func["arguments"]

                # Usage (only in last chunk)
                if "usage" in chunk:
                    usage = chunk["usage"]

            # Yield accumulated tool calls
            for idx in sorted(tool_calls_acc):
                tc = tool_calls_acc[idx]
                if tc["name"]:
                    yield StreamEvent(
                        type="tool_call",
                        tool_call_id=tc["id"],
                        tool_call_name=tc["name"],
                        tool_call_args=tc["arguments"],
                    )

            # Calculate cost
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            cost = (
                (prompt_tokens * input_cost_per_1m / 1_000_000)
                + (completion_tokens * output_cost_per_1m / 1_000_000)
            )

            duration_ms = (time.monotonic() - start) * 1000

            yield StreamEvent(
                type="done",
                finish_reason=finish_reason,
                usage=usage,
                cost_usd=round(cost, 6),
            )

    except httpx.HTTPStatusError as e:
        try:
            await e.response.aread()
            body = e.response.text[:500]
        except Exception:
            body = "<unreadable>"
        logger.error("Stream LLM API error: %d %s", e.response.status_code, body)
        yield StreamEvent(type="error", content=f"API error {e.response.status_code}")
    except httpx.ConnectError:
        logger.error("Cannot connect to LLM for streaming")
        yield StreamEvent(type="error", content="Cannot connect to LLM server")
    except Exception as e:
        logger.error("Stream LLM failed: %s", e)
        yield StreamEvent(type="error", content=str(e))
