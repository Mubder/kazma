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

import asyncio
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
            # Check if the path ALREADY ends with /v1 (e.g. /openai/v1 for Groq)
            if not parsed.path.rstrip("/").endswith("/v1"):
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

    async def get_client(self) -> httpx.AsyncClient:
        """Public accessor for the HTTP client (lazy-init).

        This is the public alias for ``_get_client()`` so UI code does
        not need to access a private method.
        """
        return await self._get_client()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions in OpenAI function-calling format.
            max_tokens: Override max_tokens for this call.
            temperature: Override temperature for this call.
            model: Override model for this call (e.g. from ModelRouter).

        Returns:
            LLMResponse with content, tool_calls, usage, and cost.
        """
        # Check semantic cache if enabled. Defaults to OFF: the LLM layer has
        # no user/session identity (AGENTS.md platform isolation), so a shared
        # global cache can return one user's response to another for identical
        # or semantically-similar prompts. Enable KAZMA_SEMANTIC_CACHE=true
        # only for single-operator deployments or all-global-prompt workloads.
        cache_enabled = os.environ.get("KAZMA_SEMANTIC_CACHE", "false").lower() == "true"
        prompt_str = json.dumps(messages, sort_keys=True)
        if cache_enabled:
            try:
                from kazma_core.swarm.semantic_cache import SemanticCache
                global _semantic_cache_singleton
                if "_semantic_cache_singleton" not in globals():
                    _semantic_cache_singleton = SemanticCache()
                cached_data = _semantic_cache_singleton.lookup(prompt_str, tools=tools)
                if cached_data is not None:
                    tool_calls = [
                        ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"])
                        for tc in cached_data.get("tool_calls", [])
                    ]
                    logger.info("[LLMProvider] Cache hit! Returning cached response.")
                    return LLMResponse(
                        content=cached_data.get("content", ""),
                        tool_calls=tool_calls,
                        finish_reason=cached_data.get("finish_reason", ""),
                        model=cached_data.get("model", ""),
                        usage=cached_data.get("usage", {}),
                        cost_usd=cached_data.get("cost_usd", 0.0),
                        duration_ms=0.0,
                    )
            except Exception as cache_exc:
                logger.warning("[LLMProvider] Semantic cache lookup error: %s", cache_exc)

        client = await self._get_client()

        payload: dict[str, Any] = {
            "model": model or self.config.model,
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
        except httpx.HTTPStatusError as e:
            # Capture the response body so the user can see WHY the API
            # rejected the request (e.g. invalid model, bad tool schema).
            detail = ""
            try:
                detail = e.response.text
            except Exception as _e:
                logger.debug("Failed to read error response body: %s", _e)
                detail = ""
            status_code = e.response.status_code if e.response is not None else 0

            logger.error(
                "LLM call failed: %s | status=%s | response_body=%s | model=%s | tools=%d",
                e,
                status_code,
                detail[:500],
                payload.get("model"),
                len(tools) if tools else 0,
            )

            # ── Rate-limit handling (429 Too Many Requests) ────────────────
            if status_code == 429:
                # Extract retry-after header if present
                retry_after = 30.0  # default fallback
                if e.response is not None:
                    retry_header = e.response.headers.get("retry-after")
                    if retry_header:
                        try:
                            retry_after = float(retry_header)
                        except (ValueError, TypeError):
                            pass
                logger.warning(
                    "Rate limited (429) — retrying after %.1fs with exponential backoff",
                    retry_after,
                )

                # Exponential backoff (max 3 retries)
                for retry_attempt in range(3):
                    await asyncio.sleep(retry_after * (1.5 ** retry_attempt))
                    try:
                        resp = await client.post("/chat/completions", json=payload)
                        if resp.status_code != 429:
                            resp.raise_for_status()
                            data = resp.json()
                            # Return the successful response after retry
                            duration_ms = (time.monotonic() - start) * 1000
                            response = self._parse_response(data, duration_ms)
                            if cache_enabled:
                                try:
                                    response_dict = {
                                        "content": response.content,
                                        "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls],
                                        "finish_reason": response.finish_reason,
                                        "model": response.model,
                                        "usage": response.usage,
                                        "cost_usd": response.cost_usd,
                                        "duration_ms": response.duration_ms,
                                    }
                                    _semantic_cache_singleton.store(prompt_str, response_dict, tools=tools)
                                except Exception as cache_exc:
                                    logger.warning("[LLMProvider] Semantic cache store error: %s", cache_exc)
                            return response
                        # Still 429, continue retrying
                    except httpx.HTTPStatusError as retry_err:
                        if retry_err.response is not None and retry_err.response.status_code != 429:
                            # Non-429 error, propagate it
                            raise
                        # Still rate-limited, continue retrying
                        continue
                else:
                    # All retries exhausted with 429
                    raise LLMError(
                        f"LLM rate-limited after 3 retries: {detail[:300]}"
                    ) from e

            # ── Tool-definition fallback ────────────────────────────────
            # NVIDIA NIM / some providers reject tool-calling with a
            # 404 "Function not found for account" for models that don't
            # support function calling. OpenAI-compatible providers may
            # also reject malformed tool schemas with 400/422. Retry
            # without tools so the user still gets a text response.
            detail_lower = detail.lower()
            # NOTE: the 404 "function not found" branch must stay (AGENTS.md).
            nim_function_not_found = (
                status_code == 404 and "function" in detail_lower
            )
            # 400/422 with tool/function validation language indicates a
            # tool-definition problem rather than a request-shape problem.
            tool_schema_error = (
                status_code in (400, 422)
                and any(tok in detail_lower for tok in ("tool", "function"))
            )
            if tools and (nim_function_not_found or tool_schema_error):
                logger.warning(
                    "Provider rejected tool definitions (HTTP %s) — retrying "
                    "without tools (model may not support tool use).",
                    status_code,
                )
                payload.pop("tools", None)
                payload.pop("tool_choice", None)
                try:
                    resp = await client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as retry_err:
                    retry_detail = ""
                    try:
                        retry_detail = retry_err.response.text
                    except Exception as _e:
                        logger.debug("Failed to read retry error body: %s", _e)
                        retry_detail = ""
                    raise LLMError(
                        f"LLM call failed (HTTP {retry_err.response.status_code}): {retry_detail[:300]}"
                    ) from retry_err
                duration_ms = (time.monotonic() - start) * 1000
                response = self._parse_response(data, duration_ms)
                if cache_enabled:
                    try:
                        response_dict = {
                            "content": response.content,
                            "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls],
                            "finish_reason": response.finish_reason,
                            "model": response.model,
                            "usage": response.usage,
                            "cost_usd": response.cost_usd,
                            "duration_ms": response.duration_ms,
                        }
                        _semantic_cache_singleton.store(prompt_str, response_dict, tools=tools)
                    except Exception as cache_exc:
                        logger.warning("[LLMProvider] Semantic cache store error: %s", cache_exc)
                return response

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
                    raise LLMError(
                        f"Primary and fallback models failed: {e} / {fallback_error}"
                    ) from e
            else:
                raise LLMError(
                    f"LLM call failed (HTTP {status_code}): {detail[:300]}"
                ) from e
        except LLMError:
            # Already a structured LLM error — re-raise without wrapping
            # to avoid nested "LLM call failed: LLM call failed: ..." messages.
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            logger.error("LLM call failed (network): %s", e)
            raise LLMError(f"LLM call failed (network): {e}") from e
        except Exception as e:
            logger.error("LLM call failed: %s", e, exc_info=True)
            raise LLMError(f"LLM call failed: {e}") from e

        duration_ms = (time.monotonic() - start) * 1000

        response = self._parse_response(data, duration_ms)
        if cache_enabled:
            try:
                response_dict = {
                    "content": response.content,
                    "tool_calls": [{"id": tc.id, "name": tc.name, "arguments": tc.arguments} for tc in response.tool_calls],
                    "finish_reason": response.finish_reason,
                    "model": response.model,
                    "usage": response.usage,
                    "cost_usd": response.cost_usd,
                    "duration_ms": response.duration_ms,
                }
                _semantic_cache_singleton.store(prompt_str, response_dict, tools=tools)
            except Exception as cache_exc:
                logger.warning("[LLMProvider] Semantic cache store error: %s", cache_exc)
        return response

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
                if port not in (11434, 4000) and not normalized.rstrip("/").endswith("/v1"):
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
                "(set)" if self.config.api_key else "(empty)",
            )


class LLMError(Exception):
    """Raised when an LLM API call fails."""
