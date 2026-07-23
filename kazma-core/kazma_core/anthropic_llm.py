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

import json
import logging
import os
import time
from typing import Any

import httpx

from kazma_core.llm_provider import LLMConfig, LLMProvider, LLMResponse, ToolCall

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
        if self._http is None:
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
    ) -> LLMResponse:
        """Send a Messages-API request and return an :class:`LLMResponse`."""
        system, convo = self._split_system(messages)
        payload: dict[str, Any] = {
            "model": model or self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
            "system": system,
            "messages": [self._convert_message(m) for m in convo],
        }
        anthropic_tools = self._convert_tools(tools) if tools else None
        if anthropic_tools:
            payload["tools"] = anthropic_tools

        client = await self._get_client()
        start = time.monotonic()
        try:
            resp = await client.post("/messages", json=payload)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            body = ""
            try:
                body = exc.response.text[:400]
            except Exception:  # noqa: BLE001
                pass
            logger.error("[Anthropic] HTTP %d: %s", exc.response.status_code, body)
            return LLMResponse(
                content=f"[Anthropic API error {exc.response.status_code}]",
                finish_reason="error",
                model=payload["model"],
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[Anthropic] request failed: %s", exc)
            return LLMResponse(
                content=f"[Anthropic request failed: {type(exc).__name__}]",
                finish_reason="error",
                model=payload["model"],
                duration_ms=(time.monotonic() - start) * 1000,
            )

        data = resp.json()
        return self._parse_response(data, payload["model"], start)

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
