"""LLM Provider — OpenAI-compatible API client for Kazma.

Connects to any OpenAI-compatible endpoint (OpenAI, LM Studio, Ollama,
LiteLLM, vLLM, etc.) using httpx. No SDK dependency required.

Usage:
    provider = LLMProvider(config)
    response = await provider.chat(messages, tools=tools)
    # response["content"] = text response
    # response["tool_calls"] = list of tool calls (if any)
    # response["usage"] = {prompt_tokens, completion_tokens, total_tokens}
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from kazma_core.url_utils import get_dummy_api_key, normalize_model_name, normalize_provider_url

logger = logging.getLogger(__name__)

# ── Configuration ─────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: float = 60.0
    # Cost tracking (per 1M tokens, in USD)
    input_cost_per_1m: float = 0.15
    output_cost_per_1m: float = 0.60
    # LiteLLM router support
    router: str | None = None
    fallback_model: str | None = None

    def __post_init__(self) -> None:
        """Normalize base_url on construction — catches ALL code paths."""
        if self.base_url:
            self.base_url = normalize_provider_url(self.base_url)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LLMConfig:
        """Create config from a dict (e.g. from kazma.yaml).

        Automatically normalizes base_url and model for local providers.
        """
        raw_url = d.get("base_url", cls.base_url)
        normalized_url = normalize_provider_url(raw_url)

        raw_model = d.get("model", cls.model)
        normalized_model = normalize_model_name(raw_model, normalized_url)

        raw_key = d.get("api_key", cls.api_key)
        resolved_key = get_dummy_api_key(normalized_url, raw_key)

        return cls(
            base_url=normalized_url,
            api_key=resolved_key,
            model=normalized_model,
            max_tokens=d.get("max_tokens", cls.max_tokens),
            temperature=d.get("temperature", cls.temperature),
            timeout=d.get("timeout", cls.timeout),
            input_cost_per_1m=d.get("input_cost_per_1m", cls.input_cost_per_1m),
            output_cost_per_1m=d.get("output_cost_per_1m", cls.output_cost_per_1m),
            router=d.get("router", cls.router),
            fallback_model=d.get("fallback_model", cls.fallback_model),
        )


# ── Response types ────────────────────────────────────────────────────


@dataclass
class ToolCall:
    """A single tool call from the LLM."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """Parsed response from an LLM call."""

    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = ""  # "stop", "tool_calls", "length"
    model: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    cost_usd: float = 0.0
    duration_ms: float = 0.0


# ── Provider ──────────────────────────────────────────────────────────


class LLMProvider:
    """OpenAI-compatible LLM client using httpx.

    Works with:
    - OpenAI (api.openai.com)
    - LM Studio (localhost:1234)
    - Ollama (localhost:11434/v1)
    - LiteLLM (localhost:4000)
    - vLLM (localhost:8000/v1)
    - Any OpenAI-compatible API
    """

    def __init__(self, config: LLMConfig | None = None) -> None:
        self.config = config or LLMConfig()
        # Safety net: normalize base_url even if LLMConfig.__post_init__ was bypassed
        if self.config.base_url:
            self.config.base_url = normalize_provider_url(self.config.base_url)
        self._resolve_api_key()
        self._http: httpx.AsyncClient | None = None
        logger.info(
            "LLMProvider initialized: base_url=%s model=%s",
            self.config.base_url,
            self.config.model,
        )

    def _resolve_api_key(self) -> None:
        """Resolve API key from config or environment."""
        key = self.config.api_key
        if not key:
            key = os.getenv("OPENAI_API_KEY", "")
        if not key:
            key = os.getenv("KAZMA_API_KEY", "")
        # LM Studio / Ollama don't need a real key
        if not key:
            key = "not-needed"
        self.config.api_key = key

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init the HTTP client."""
        if self._http is None or self._http.is_closed:
            base = self.config.base_url.rstrip("/")

            # HARD ASSERT: /v1 must be in the path for OpenAI-compatible APIs
            # This prevents the "empty bubble" bug where requests go to
            # /chat/completions instead of /v1/chat/completions
            from urllib.parse import urlparse as _up

            parsed = _up(base)
            if parsed.path not in ("/v1", "/v1/"):
                port = parsed.port
                # Skip assertion for Ollama (11434) and LiteLLM (4000)
                if port not in (11434, 4000):
                    # Force /v1
                    base = base.rstrip("/") + "/v1"
                    self.config.base_url = base
                    logger.warning("LLMProvider: /v1 was missing — forced to %s", base)

            logger.debug("Creating httpx client: base_url=%s", base)
            self._http = httpx.AsyncClient(
                base_url=base,
                headers={
                    "Authorization": f"Bearer {self.config.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._http

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions in OpenAI function-calling format.
            max_tokens: Override max_tokens for this call.
            temperature: Override temperature for this call.

        Returns:
            LLMResponse with content, tool_calls, usage, and cost.
        """
        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        start = time.monotonic()

        try:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPStatusError, httpx.ConnectError, Exception) as e:
            logger.error("LLM call failed: %s", e)
            # Try fallback model if configured
            if self.config.fallback_model and self.config.router == "litellm":
                logger.info("Retrying with fallback model: %s", self.config.fallback_model)
                payload["model"] = self.config.fallback_model
                try:
                    resp = await client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as fallback_error:
                    logger.error("Fallback model also failed: %s", fallback_error)
                    raise LLMError(f"Primary and fallback models failed: {e} / {fallback_error}") from e
            else:
                raise LLMError(f"LLM call failed: {e}") from e

        duration_ms = (time.monotonic() - start) * 1000

        return self._parse_response(data, duration_ms)

    def _parse_response(self, data: dict[str, Any], duration_ms: float) -> LLMResponse:
        """Parse the OpenAI-format response into an LLMResponse."""
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        finish_reason = choice.get("finish_reason", "")

        content = message.get("content", "") or ""
        tool_calls: list[ToolCall] = []

        for tc in message.get("tool_calls", []):
            func = tc.get("function", {})
            args_raw = func.get("arguments", "{}")
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except json.JSONDecodeError:
                args = {"raw": args_raw}

            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                )
            )

        usage = data.get("usage", {})
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

        # Calculate cost
        cost = (prompt_tokens * self.config.input_cost_per_1m / 1_000_000) + (
            completion_tokens * self.config.output_cost_per_1m / 1_000_000
        )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            model=data.get("model", self.config.model),
            usage=usage,
            cost_usd=round(cost, 6),
            duration_ms=round(duration_ms, 1),
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def reconfigure(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Reconfigure the provider at runtime (e.g. after provider switch).

        Closes the existing HTTP client so the next request uses the new config.
        All parameters are optional — only provided values are updated.
        """
        changed = False
        if base_url is not None:
            normalized = normalize_provider_url(base_url)
            logger.info("reconfigure: raw=%s normalized=%s", base_url, normalized)
            # HARD FORCE /v1 for non-Ollama endpoints
            if normalized:
                from urllib.parse import urlparse as _up

                parsed = _up(normalized)
                port = parsed.port
                if port != 11434 and not normalized.rstrip("/").endswith("/v1"):
                    normalized = normalized.rstrip("/") + "/v1"
                    logger.info("reconfigure: forced /v1 → %s", normalized)
            self.config.base_url = normalized
            changed = True
        if model is not None:
            self.config.model = normalize_model_name(model, self.config.base_url)
            changed = True
        if api_key is not None:
            self.config.api_key = api_key
            changed = True

        if changed:
            # Force client recreation on next request (old client will be GC'd)
            self._http = None
            logger.info(
                "LLMProvider reconfigured: base_url=%s model=%s api_key=%s",
                self.config.base_url,
                self.config.model,
                self.config.api_key[:10] + "..." if self.config.api_key else "(empty)",
            )


class LLMError(Exception):
    """Raised when an LLM API call fails."""
