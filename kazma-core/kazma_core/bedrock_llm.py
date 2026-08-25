"""AWS Bedrock provider.

Bedrock is NOT OpenAI-compatible: requests are signed with AWS SigV4 and sent
to a region-scoped endpoint using model-specific APIs. This provider uses the
``Converse`` API (``/model/{id}/converse``) which gives a uniform interface
across Bedrock-hosted models (Anthropic Claude, Meta Llama, Mistral, etc.).

Credentials come from the standard boto3 chain (env vars, shared-credentials
file, IAM role). Requires ``boto3`` (``pip install boto3``); degrades with a
clear message when boto3 is missing.

Configuration keys (provider entry or env):
  * ``AWS_REGION`` / ``region`` — e.g. ``us-east-1``.
  * Standard boto3 creds: ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
    ``AWS_SESSION_TOKEN``.
  * ``model`` — a Bedrock model id, e.g.
    ``anthropic.claude-3-5-sonnet-20241022-v2:0`` or ``meta.llama3-1-70b-instruct-v1:0``.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from kazma_core.llm_provider import LLMConfig, LLMError, LLMProvider, LLMResponse, ToolCall
from kazma_core.llm_stream import StreamDelta

logger = logging.getLogger(__name__)

_SERVICE = "bedrock"

# Approx cost per 1M tokens (USD) for common Bedrock models. Input/Output.
_MODEL_COSTS: dict[str, tuple[float, float]] = {
    "claude-3-5-sonnet": (3.0, 15.0),
    "claude-3-7-sonnet": (3.0, 15.0),
    "claude-3-haiku": (0.25, 1.25),
    "claude-3-opus": (15.0, 75.0),
    "llama3-1-70b": (0.99, 0.99),
    "llama3-1-8b": (0.22, 0.22),
}


class BedrockProvider(LLMProvider):
    """AWS Bedrock client via the Converse API (SigV4-signed via boto3)."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        super().__init__(config)
        self._region = (
            os.getenv("AWS_REGION", "")
            or os.getenv("AWS_DEFAULT_REGION", "")
            or "us-east-1"
        )
        self._client = None
        logger.info("BedrockProvider initialized: model=%s region=%s",
                    self.config.model, self._region)

    def _get_client(self):
        """Lazily build a boto3 bedrock-runtime client. Returns None on error."""
        if self._client is not None:
            return self._client
        try:
            import boto3  # type: ignore
        except ImportError:
            logger.error("[Bedrock] boto3 not installed. Run: pip install boto3")
            return None
        try:
            self._client = boto3.client(_SERVICE, region_name=self._region)
        except Exception as exc:  # noqa: BLE001
            logger.error("[Bedrock] could not create boto3 client: %s", exc)
            return None
        return self._client

    # ── Format translation (OpenAI → Bedrock Converse) ───────────────

    @staticmethod
    def _build_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Split into (system, messages) in Converse format.

        Bedrock Converse: system is a list of ``{text: ...}`` blocks at the
        top level; messages are ``{role, content: [{text|...}]}``.
        """
        system: list[dict[str, Any]] = []
        convo: list[dict[str, Any]] = []
        for m in messages:
            role = m.get("role", "user")
            content = m.get("content")
            text = content if isinstance(content, str) else json.dumps(content)
            if role == "system":
                system.append({"text": text})
            elif role == "tool":
                # OpenAI tool result → Bedrock toolResult on the next user turn.
                convo.append({
                    "role": "user",
                    "content": [{"toolResult": {
                        "toolUseId": str(m.get("tool_call_id", "")),
                        "content": [{"text": text}],
                    }}],
                })
            elif role == "assistant" and m.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                if text:
                    blocks.append({"text": text})
                for tc in m["tool_calls"]:
                    fn = tc.get("function", {})
                    blocks.append({"toolUse": {
                        "toolUseId": tc.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": fn.get("arguments") if isinstance(fn.get("arguments"), dict)
                        else _safe_json(fn.get("arguments", "{}")),
                    }})
                convo.append({"role": "assistant", "content": blocks})
            else:
                convo.append({"role": role, "content": [{"text": text}]})
        return system, convo

    @staticmethod
    def _convert_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for t in tools:
            fn = (t.get("function") or {}) if t.get("type") == "function" else t
            spec = fn.get("parameters") or {"type": "object", "properties": {}}
            out.append({
                "toolSpec": {
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "inputSchema": {"json": spec},
                }
            })
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
        import asyncio

        _ = response_format  # OpenAI-shaped; Bedrock Converse has no equivalent
        model_id = model or self.config.model
        system, convo = self._build_messages(messages)

        def _run() -> LLMResponse:
            client = self._get_client()
            if client is None:
                # boto3 not installed is a permanent configuration error, not a
                # model reply. Previously this returned a string as content,
                # making the failure indistinguishable from a normal response
                # (AGENTS.md §3). Raise so the supervisor retry/turn_failed /
                # friendly_llm_error machinery handles it.
                raise LLMError(
                    "Bedrock unavailable: boto3 is not installed",
                    transient=False,
                )
            start = time.monotonic()
            request: dict[str, Any] = {
                "modelId": model_id,
                "messages": convo,
                "inferenceConfig": {
                    "maxTokens": max_tokens or self.config.max_tokens,
                    "temperature": temperature if temperature is not None else self.config.temperature,
                },
            }
            if system:
                request["system"] = system
            if tools:
                request["toolConfig"] = {"tools": self._convert_tools(tools)}
            try:
                resp = client.converse(**request)
            except Exception as exc:  # noqa: BLE001
                logger.error("[Bedrock] converse failed: %s", exc)
                # Classify boto3 ClientError via duck typing (avoid hard-importing
                # botocore, which is optional). 429 / 5xx / ThrottlingException
                # are transient; everything else is permanent — mirrors the
                # generic LLMProvider classification (AGENTS.md §3).
                transient = False
                kind = ""
                err_resp = getattr(exc, "response", None)
                if isinstance(err_resp, dict):
                    meta = err_resp.get("ResponseMetadata") or {}
                    status = meta.get("HTTPStatusCode")
                    code = str((err_resp.get("Error") or {}).get("Code", ""))
                    if status == 429 or (status and status >= 500) or "Throttling" in code:
                        transient = True
                    if status == 429 or "Throttling" in code:
                        kind = "rate_limit_exhausted"
                raise LLMError(
                    f"Bedrock converse failed: {exc}",
                    transient=transient,
                    kind=kind,
                ) from exc
            return self._parse(resp, model_id, start)

        return await asyncio.to_thread(_run)

    def _parse(self, resp: dict[str, Any], model_id: str, start: float) -> LLMResponse:
        """Map a Converse response onto :class:`LLMResponse`."""
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        output = (resp.get("output") or {}).get("message") or {}
        for block in output.get("content", []):
            if "text" in block:
                text_parts.append(block["text"])
            elif "toolUse" in block:
                tu = block["toolUse"]
                tool_calls.append(ToolCall(
                    id=tu.get("toolUseId", ""),
                    name=tu.get("name", ""),
                    arguments=tu.get("input") or {},
                ))

        stop_reason = resp.get("stopReason", "")
        finish = {
            "end_turn": "stop", "stop_sequence": "stop", "max_tokens": "length",
            "tool_use": "tool_calls", "finish": "stop",
        }.get(stop_reason, stop_reason or "stop")

        usage = resp.get("usage") or {}
        usage_in = usage.get("inputTokens", 0)
        usage_out = usage.get("outputTokens", 0)
        key = next((k for k in _MODEL_COSTS if k in model_id.lower()), "")
        in_cost, out_cost = _MODEL_COSTS.get(key, (3.0, 15.0))
        cost = (usage_in / 1_000_000) * in_cost + (usage_out / 1_000_000) * out_cost

        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            finish_reason=finish,
            model=model_id,
            usage={
                "input_tokens": usage_in,
                "output_tokens": usage_out,
                "total_tokens": usage_in + usage_out,
            },
            cost_usd=cost,
            duration_ms=(time.monotonic() - start) * 1000,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        """Bedrock ConverseStream is not wired yet — one-chunk fallback.

        Must override the parent OpenAI SSE path: Bedrock is SigV4, not
        ``/chat/completions``.
        """
        resp = await self.chat(
            messages, tools, max_tokens, temperature, model, response_format
        )
        if resp.content:
            yield StreamDelta(content=resp.content)
        yield StreamDelta(response=resp)

    async def close(self) -> None:
        # boto3 clients hold no asyncio resources.
        self._client = None


def _safe_json(s: str) -> dict[str, Any]:
    try:
        return json.loads(s) if isinstance(s, str) else (s or {})
    except Exception:  # noqa: BLE001
        return {}
