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

from kazma_core.llm_stream import StreamDelta
from kazma_core.url_utils import get_dummy_api_key, normalize_model_name, normalize_provider_url

__all__ = [
    "LLMConfig",
    "LLMError",
    "LLMProvider",
    "LLMResponse",
    "StreamDelta",
    "ToolCall",
    "hoist_system_messages",
    "retry_after_seconds",
]

logger = logging.getLogger(__name__)


def hoist_system_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Move system/developer messages to the head of the message list.

    Kazma injects ``role: system`` notes mid-conversation (INTENT ENGINE plan
    notes, iteration budget nudges, mission patches). Cloud providers accept
    them anywhere, but strict local chat templates (LM Studio / llama.cpp
    Qwen3) raise ``System message must be at the beginning`` when a system
    message appears after the first user message.

    When prompt-cache packing is on (default), stable identity/personality
    is merged into one prefix message and per-turn notes into a second
    blob so OpenAI/Gemini/Anthropic prefix caches can hit. Kill-switch:
    ``KAZMA_PROMPT_CACHE=0`` restores the legacy flatten (no merge).
    """
    from kazma_core.prompt_cache import pack_system_messages, prompt_cache_enabled

    if prompt_cache_enabled():
        return pack_system_messages(messages)
    head = [
        m for m in messages
        if isinstance(m, dict) and m.get("role") in ("system", "developer")
    ]
    rest = [
        m for m in messages
        if not (isinstance(m, dict) and m.get("role") in ("system", "developer"))
    ]
    return head + rest


def retry_after_seconds(headers: Any, default: float = 30.0) -> float:
    """Parse a Retry-After header, floored at 1.0s so a ``0`` cannot spin."""
    retry_after = default
    if headers is not None:
        try:
            raw = headers.get("retry-after")
            if raw is None:
                raw = headers.get("Retry-After")
            if raw is not None and str(raw).strip():
                retry_after = float(raw)
        except (TypeError, ValueError, AttributeError):
            pass
    return max(1.0, float(retry_after))


# ── Configuration ─────────────────────────────────────────────────────


@dataclass
class LLMConfig:
    """Configuration for an LLM provider."""

    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    model: str = "gpt-4o-mini"
    # 16384 default: 8192 truncates content-generation tasks (document
    # restructuring, research synthesis, long code) mid-stream, forcing
    # the wasteful auto-retry (doubles inference cost). 16384 is safe
    # across OpenAI (16k+), DeepSeek (16k+), Anthropic (8k+ with automatic
    # mapping), Qwen (32k+). Callers can override per-call for short tasks.
    max_tokens: int = 16384
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
        # Strong references for fire-and-forget aclose() tasks scheduled by
        # reconfigure(), so CPython doesn't GC them before aclose runs. A set
        # (not a list) so add_done_callback(discard) works.
        self._pending_closes: set = set()
        # Direct (pre-gateway) egress — LiteLLM proxy is opt-in and live-read.
        self._direct_base_url = self.config.base_url
        self._direct_api_key = self.config.api_key
        self._via_gateway = False
        self._sync_gateway()
        logger.info(
            "LLMProvider initialized: base_url=%s model=%s",
            self.config.base_url,
            self.config.model,
        )

    def _sync_gateway(self) -> None:
        """Apply or remove the optional LiteLLM proxy (generic client only).

        Subclasses (Anthropic/Azure/Bedrock/Gemini) keep native endpoints
        (AGENTS.md §1). Live-read so ``KAZMA_LITELLM_URL`` / ConfigStore
        changes take effect on the next ``chat()``.
        """
        if type(self) is not LLMProvider:
            self._via_gateway = False
            return
        from kazma_core.llm_gateway import resolve_generic_egress

        url, key, via = resolve_generic_egress(
            self._direct_base_url, self._direct_api_key or ""
        )
        self._via_gateway = via
        if url == self.config.base_url and key == (self.config.api_key or ""):
            return
        logger.info(
            "LLMProvider: generic egress %s → %s (gateway=%s)",
            self.config.base_url,
            url,
            via,
        )
        self.reconfigure(base_url=url, api_key=key)

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

    @staticmethod
    def _strip_non_ascii(value: str) -> str:
        """Remove non-ASCII characters from a value destined for an HTTP header.

        httpx raises ``UnicodeEncodeError`` (ascii codec) when a header value
        contains non-ASCII characters — e.g. an API key pasted with Arabic text
        by an Arabic-first operator. Strip them so the request can proceed
        instead of crashing with a cryptic "ascii codec can't encode" error.
        For local providers (Ollama/LM Studio) the key is ignored anyway; for
        cloud providers a stripped key fails auth with a clear 401 instead.
        """
        if not value:
            return value
        return "".join(ch for ch in value if ord(ch) < 128)

    # kazma-internal routing prefixes added by normalize_model_name()
    # (e.g. "ollama/llama3.2", "openai/local-model"). These identify the
    # provider inside kazma's registry/router but must NOT be sent to the
    # provider's API — Ollama/LM Studio expect the bare model name.
    # NOTE: only LOCAL providers (ollama, lm-studio) get a kazma-internal
    # routing prefix that must be stripped. Hosted providers (groq, openai,
    # anthropic, bedrock, azure) use model ids where the prefix is part of
    # the real upstream name (e.g. Groq's "groq/compound-mini") — stripping
    # it causes a 404 "model not found".
    _ROUTING_PREFIXES = ("ollama/", "lm-studio/")

    @staticmethod
    def _strip_routing_prefix(model: str) -> str:
        """Strip kazma's internal provider routing prefix from a model name.

        ``normalize_model_name()`` tags local models with a provider prefix
        ("ollama/", "lm-studio/") for routing, but the upstream local API
        (Ollama, LM Studio, …) expects the bare name ("qwen2.5:7b"). Sending
        "ollama/qwen2.5:7b" makes Ollama reply 404 "model not found".

        Hosted providers are NOT stripped: Groq's ``groq/compound-mini`` and
        similar ids include the prefix as part of the real upstream name.
        """
        if not model:
            return model
        for prefix in LLMProvider._ROUTING_PREFIXES:
            if model.startswith(prefix):
                return model[len(prefix):]
        return model

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
            api_key = self.config.api_key or ""
            safe_key = LLMProvider._strip_non_ascii(api_key)
            if safe_key != api_key:
                logger.warning(
                    "LLMProvider: API key contained non-ASCII characters; these "
                    "were stripped. If you use a cloud provider, set a valid ASCII "
                    "API key in Settings > Models/Providers."
                )
            self._http = httpx.AsyncClient(
                base_url=base,
                headers={
                    "Authorization": f"Bearer {safe_key}",
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
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request.

        Args:
            messages: Conversation messages in OpenAI format.
            tools: Optional tool definitions in OpenAI function-calling format.
            max_tokens: Override max_tokens for this call.
            temperature: Override temperature for this call.
            model: Override model for this call (e.g. from ModelRouter).
            response_format: Optional OpenAI structured-output body
                (``{"type": "json_schema", "json_schema": {...}}`` or
                ``{"type": "json_object"}``). Never attached by the
                supervisor loop — callers opt in per request. Unsupported
                providers are retried once without it.

        Returns:
            LLMResponse with content, tool_calls, usage, and cost.
        """
        self._sync_gateway()
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

        payload = self._chat_payload(
            messages, tools, max_tokens, temperature, model, response_format
        )

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
                retry_after = retry_after_seconds(
                    e.response.headers if e.response is not None else None
                )
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
                            # Non-429 during 429 backoff (e.g. a 5xx). A bare
                            # `raise` escapes chat() as a raw
                            # httpx.HTTPStatusError — unclassified, so the
                            # supervisor treats it as permanent and model
                            # failover refuses to fire. Classify here: 5xx is
                            # transient per the §3 taxonomy; 4xx is permanent.
                            _retry_sc = retry_err.response.status_code
                            _retry_detail = ""
                            try:
                                _retry_detail = retry_err.response.text
                            except Exception:
                                _retry_detail = ""
                            raise LLMError(
                                f"LLM call failed during 429 backoff (HTTP {_retry_sc}): "
                                f"{_retry_detail[:300]}",
                                transient=(_retry_sc >= 500),
                            ) from retry_err
                        # Still rate-limited, continue retrying
                        continue
                else:
                    # All retries exhausted with 429. Per AGENTS.md §3 a 429 IS
                    # transient (so model failover still fires), but we tag it
                    # kind="rate_limit_exhausted" so the supervisor's own
                    # retry loop skips re-invoking THIS provider (we already
                    # did bounded exponential backoff here) — re-retrying would
                    # amplify load against an already-rate-limited provider.
                    raise LLMError(
                        f"LLM rate-limited after 3 retries: {detail[:300]}",
                        transient=True,
                        kind="rate_limit_exhausted",
                    ) from e

            # ── Context-window overflow ─────────────────────────────────
            # Providers reject over-long prompts with 400 (OpenAI
            # "context_length_exceeded", Anthropic "invalid_request_error:
            # prompt is too long", DeepSeek "context length", etc.). This is
            # NOT a tool-schema problem and NOT transient — the only correct
            # recovery is compact-and-retry. Tag it with kind so the
            # supervisor/watchdog can route it to compaction instead of
            # failing the turn or stripping tools.
            detail_lower = detail.lower()
            if status_code in (400, 413, 422) and any(
                marker in detail_lower
                for marker in (
                    "context_length_exceeded",
                    "maximum context length",
                    "context window",
                    "prompt is too long",
                    "too many tokens",
                    "input is too long",
                    "exceeds the context",
                    "context length",
                )
            ):
                logger.warning(
                    "Provider rejected request for context overflow (HTTP %s): %s",
                    status_code,
                    detail[:200],
                )
                raise LLMError(
                    f"Prompt exceeds the model context window (HTTP {status_code}): "
                    f"{detail[:300]}",
                    transient=False,
                    kind="context_overflow",
                ) from e

            # ── Tool-definition fallback ────────────────────────────────
            # NVIDIA NIM / some providers reject tool-calling with a
            # 404 "Function not found for account" for models that don't
            # support function calling. OpenAI-compatible providers may
            # also reject malformed tool schemas with 400/422. Retry
            # without tools so the user still gets a text response.
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
                        f"LLM call failed (HTTP {retry_err.response.status_code}): {retry_detail[:300]}",
                        transient=False,
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

            # ── Structured-output fallback ──────────────────────────────
            # Local servers and older OpenAI-compat endpoints 400 on
            # response_format / json_schema. Retry once without it so a
            # caller that opted in still gets a text reply.
            rf_unsupported = (
                bool(response_format)
                and status_code in (400, 422)
                and any(
                    tok in detail_lower
                    for tok in ("response_format", "json_schema", "json_object")
                )
            )
            if rf_unsupported and "response_format" in payload:
                logger.warning(
                    "Provider rejected response_format (HTTP %s) — retrying without it.",
                    status_code,
                )
                payload.pop("response_format", None)
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
                        f"LLM call failed (HTTP {retry_err.response.status_code}): {retry_detail[:300]}",
                        transient=False,
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
            if self.config.fallback_model:
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
                    f"LLM call failed (HTTP {status_code}): {detail[:300]}",
                    transient=False,
                ) from e
        except LLMError:
            # Already a structured LLM error — re-raise without wrapping
            # to avoid nested "LLM call failed: LLM call failed: ..." messages.
            raise
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            recovered = await self._retry_direct_after_gateway_fail(payload, e)
            if recovered is not None:
                return recovered
            logger.error("LLM call failed (network): %s", e)
            raise LLMError(
                f"LLM call failed (network): {e}", transient=True
            ) from e
        except httpx.ReadError as e:
            # Mid-stream response drop (connection reset while reading the
            # response body). Transient — retrying may succeed.
            logger.error("LLM call failed (network read): %s", e)
            raise LLMError(
                f"LLM call failed (network): {e}", transient=True
            ) from e
        except httpx.RemoteProtocolError as e:
            # Server closed the connection unexpectedly. Transient.
            logger.error("LLM call failed (network): %s", e)
            raise LLMError(
                f"LLM call failed (network): {e}", transient=True
            ) from e
        except UnicodeEncodeError as e:
            # Non-ASCII in a header value (e.g. API key) — httpx fails to
            # encode the request. Surface a clear, actionable message.
            logger.error("LLM request encoding failed (non-ASCII config?): %s", e)
            raise LLMError(
                "The model request could not be encoded. Check that your API key "
                "and model name contain only standard (ASCII) characters — "
                "remove any Arabic or other non-English text from Settings.",
                transient=False,
            ) from e
        except RuntimeError as e:
            if "Event loop is closed" in str(e):
                logger.warning("LLMProvider: Event loop was closed. Re-creating HTTP client and retrying request.")
                self._http = None
                client = await self._get_client()
                # This retry lives inside the except handler, so it is NOT
                # covered by the outer try — wrap it so a failure raises a
                # classified LLMError (not a raw httpx exception) the
                # supervisor's `except LLMError` can handle (audit finding).
                try:
                    resp = await client.post("/chat/completions", json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                except httpx.HTTPStatusError as exc:
                    sc = exc.response.status_code
                    raise LLMError(
                        f"LLM call failed (HTTP {sc}) after event-loop recovery: {exc.response.text[:300]}",
                        transient=(sc == 429 or sc >= 500),
                    ) from exc
                except (
                    httpx.ConnectError,
                    httpx.TimeoutException,
                    httpx.ReadError,
                    httpx.RemoteProtocolError,
                ) as exc:
                    raise LLMError(
                        f"LLM call failed (network) after event-loop recovery: {exc}",
                        transient=True,
                    ) from exc
                except Exception as exc:  # noqa: BLE001
                    raise LLMError(
                        f"LLM call failed after event-loop recovery: {exc}",
                        transient=False,
                    ) from exc
            else:
                logger.error("LLM call failed: %s", e, exc_info=True)
                raise LLMError(f"LLM call failed: {e}") from e
        except Exception as e:
            logger.error("LLM call failed: %s", e, exc_info=True)
            raise LLMError(f"LLM call failed: {e}") from e

        duration_ms = (time.monotonic() - start) * 1000

        response = self._parse_response(data, duration_ms)

        # ── Auto-recover from output truncation ─────────────────────────
        # finish_reason="length" means the provider cut the completion at
        # max_tokens — tool-call JSON is severed mid-string and the task
        # fails in a retry loop. Instead of telling the USER to bump
        # Settings, transparently retry once with a doubled limit. Capped at
        # 4x the configured value (or 32k) so a runaway can't balloon cost.
        # No re-entrancy guard needed: the retry is an inline client.post
        # (never a recursive chat() call), so an instance-level flag would
        # only make CONCURRENT truncating calls skip their own retry.
        if response.finish_reason == "length":
            current_cap = int(payload.get("max_tokens") or self.config.max_tokens)
            retry_cap = min(current_cap * 2, max(self.config.max_tokens * 4, 32768))
            logger.warning(
                "[LLMProvider] Response truncated at max_tokens=%d — retrying once "
                "with max_tokens=%d (model=%s)",
                current_cap, retry_cap, payload.get("model"),
            )
            try:
                retry_resp = await client.post(
                    "/chat/completions",
                    json={**payload, "max_tokens": retry_cap},
                )
                retry_resp.raise_for_status()
                retry_data = retry_resp.json()
            except Exception as retry_exc:
                logger.warning(
                    "[LLMProvider] Truncation retry failed (%s) — keeping truncated response",
                    retry_exc,
                )
            else:
                retry_duration = (time.monotonic() - start) * 1000
                retry_response = self._parse_response(retry_data, retry_duration)
                if retry_response.finish_reason != "length":
                    logger.info(
                        "[LLMProvider] Truncation retry succeeded (finish_reason=%s)",
                        retry_response.finish_reason,
                    )
                    response = retry_response
                else:
                    logger.warning(
                        "[LLMProvider] Still truncated at max_tokens=%d — returning "
                        "truncated response; tool worker will guide chunked writes",
                        retry_cap,
                    )

        if cache_enabled and response.finish_reason != "length":
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

    # ── Streaming (OpenAI-compatible SSE, including LiteLLM proxy) ──

    def _chat_payload(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int | None,
        temperature: float | None,
        model: str | None,
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build the OpenAI-compatible request body (hoist applied)."""
        payload: dict[str, Any] = {
            "model": LLMProvider._strip_routing_prefix(model or self.config.model),
            "messages": hoist_system_messages(messages),
            "max_tokens": max_tokens or self.config.max_tokens,
            "temperature": temperature if temperature is not None else self.config.temperature,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if response_format:
            payload["response_format"] = response_format
        return payload

    @staticmethod
    def _delta_text(delta: Any) -> str:
        """Extract token text from an OpenAI-style ``delta`` object."""
        if not isinstance(delta, dict):
            return ""
        content = delta.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    parts.append(str(part.get("text") or part.get("content") or ""))
            return "".join(parts)
        return ""

    @staticmethod
    async def _iter_sse_json(resp: httpx.Response) -> Any:
        """Yield parsed JSON objects from an SSE ``data:`` stream."""
        async for line in resp.aiter_lines():
            if not line:
                continue
            stripped = line.strip()
            if not stripped or stripped.startswith(":"):
                continue
            if not stripped.startswith("data:"):
                continue
            data = stripped[5:].strip()
            if data == "[DONE]":
                return
            try:
                yield json.loads(data)
            except json.JSONDecodeError:
                continue

    @staticmethod
    def _accumulate_tool_delta(
        acc: dict[int, dict[str, str]],
        tool_calls: list[Any],
    ) -> None:
        """Merge streaming ``delta.tool_calls`` fragments by index."""
        for item in tool_calls:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", 0))
            except (TypeError, ValueError):
                idx = 0
            slot = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if item.get("id"):
                slot["id"] = str(item["id"])
            func = item.get("function") or {}
            if not isinstance(func, dict):
                func = {}
            if func.get("name"):
                slot["name"] = str(func["name"])
            args_piece = func.get("arguments")
            if args_piece:
                slot["arguments"] += str(args_piece)

    def _response_from_stream_acc(
        self,
        *,
        content: str,
        tool_acc: dict[int, dict[str, str]],
        finish_reason: str,
        model: str,
        usage: dict[str, Any],
        duration_ms: float,
    ) -> LLMResponse:
        """Build an LLMResponse from accumulated stream fragments."""
        fake = {
            "choices": [{
                "message": {
                    "content": content,
                    "tool_calls": [
                        {
                            "id": slot.get("id") or f"call_{idx}",
                            "type": "function",
                            "function": {
                                "name": slot.get("name") or "",
                                "arguments": slot.get("arguments") or "{}",
                            },
                        }
                        for idx, slot in sorted(tool_acc.items())
                    ] or None,
                },
                "finish_reason": finish_reason or ("tool_calls" if tool_acc else "stop"),
            }],
            "model": model or self.config.model,
            "usage": usage or {},
        }
        # Drop empty tool_calls list so _parse_response doesn't iterate None-as-missing
        if not tool_acc:
            fake["choices"][0]["message"].pop("tool_calls", None)
        return self._parse_response(fake, duration_ms)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> Any:
        """Yield ``StreamDelta`` tokens then a final delta with ``response``.

        OpenAI-compatible SSE (``stream: true``). LiteLLM proxy, OpenAI,
        DeepSeek, Groq, NIM, Ollama, LM Studio all speak this. On providers
        that reject streaming, falls back to ``chat()`` and yields one chunk.
        """
        from kazma_core.llm_stream import stream_enabled

        self._sync_gateway()
        if not stream_enabled():
            resp = await self.chat(
                messages, tools, max_tokens, temperature, model, response_format
            )
            if resp.content:
                yield StreamDelta(content=resp.content)
            yield StreamDelta(response=resp)
            return

        payload = self._chat_payload(
            messages, tools, max_tokens, temperature, model, response_format
        )
        payload["stream"] = True
        # Best-effort: OpenAI/LiteLLM return usage on the last chunk.
        payload["stream_options"] = {"include_usage": True}

        client = await self._get_client()
        start = time.monotonic()

        class _StreamHttpError(Exception):
            def __init__(self, status_code: int, detail: str) -> None:
                super().__init__(detail)
                self.status_code = status_code
                self.detail = detail

        async def _run_stream(body: dict[str, Any]) -> Any:
            content_parts: list[str] = []
            tool_acc: dict[int, dict[str, str]] = {}
            finish_reason = ""
            usage: dict[str, Any] = {}
            model_id = str(body.get("model") or self.config.model)
            async with client.stream("POST", "/chat/completions", json=body) as resp:
                if resp.status_code >= 400:
                    err_body = ""
                    try:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")
                    except Exception:
                        err_body = ""
                    raise _StreamHttpError(resp.status_code, err_body)
                async for obj in self._iter_sse_json(resp):
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("error"):
                        err = obj["error"]
                        msg = err if isinstance(err, str) else str(
                            (err or {}).get("message") or err
                        )
                        raise LLMError(
                            f"LLM stream error: {msg[:300]}",
                            transient=False,
                        )
                    if obj.get("usage"):
                        usage = obj["usage"] if isinstance(obj["usage"], dict) else usage
                    if obj.get("model"):
                        model_id = str(obj["model"])
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    choice = choices[0] if isinstance(choices[0], dict) else {}
                    if choice.get("finish_reason"):
                        finish_reason = str(choice["finish_reason"])
                    delta = choice.get("delta") or {}
                    text = self._delta_text(delta)
                    if text:
                        content_parts.append(text)
                        yield StreamDelta(content=text)
                    tcs = delta.get("tool_calls")
                    if isinstance(tcs, list) and tcs:
                        self._accumulate_tool_delta(tool_acc, tcs)
            duration_ms = (time.monotonic() - start) * 1000
            assembled = self._response_from_stream_acc(
                content="".join(content_parts),
                tool_acc=tool_acc,
                finish_reason=finish_reason,
                model=model_id,
                usage=usage,
                duration_ms=duration_ms,
            )
            yield StreamDelta(response=assembled)

        try:
            async for delta in _run_stream(payload):
                yield delta
            return
        except _StreamHttpError as e:
            detail = e.detail or ""
            status_code = e.status_code
            detail_lower = detail.lower()
            logger.warning(
                "[LLMProvider] stream HTTP %s: %s",
                status_code,
                detail[:300],
            )

            # Unknown field `stream_options` — retry stream without it.
            if (
                status_code in (400, 422)
                and "stream_options" in detail_lower
                and "stream_options" in payload
            ):
                payload.pop("stream_options", None)
                try:
                    async for delta in _run_stream(payload):
                        yield delta
                    return
                except Exception as retry_exc:
                    logger.warning(
                        "[LLMProvider] stream retry without stream_options failed: %s",
                        retry_exc,
                    )

            # 4xx (except 429): fall back to blocking chat() which already
            # classifies 404-function, context overflow, schema errors.
            if status_code != 429 and status_code < 500:
                logger.info(
                    "[LLMProvider] streaming rejected (HTTP %s) — falling back to chat()",
                    status_code,
                )
                resp = await self.chat(
                    messages, tools, max_tokens, temperature, model, response_format
                )
                if resp.content:
                    yield StreamDelta(content=resp.content)
                yield StreamDelta(response=resp)
                return

            kind = "rate_limit_exhausted" if status_code == 429 else ""
            raise LLMError(
                f"LLM stream failed (HTTP {status_code}): {detail[:300]}",
                transient=True,
                kind=kind,
            ) from e
        except LLMError:
            raise
        except (
            httpx.ConnectError,
            httpx.TimeoutException,
            httpx.ReadError,
            httpx.RemoteProtocolError,
        ) as e:
            if self._via_gateway:
                try:
                    from kazma_core.llm_gateway import get_litellm_gateway

                    if get_litellm_gateway().fallback_direct:
                        logger.warning(
                            "LiteLLM stream failed (%s) — falling back to blocking chat()",
                            type(e).__name__,
                        )
                        resp = await self.chat(
                            messages, tools, max_tokens, temperature, model, response_format
                        )
                        if resp.content:
                            yield StreamDelta(content=resp.content)
                        yield StreamDelta(response=resp)
                        return
                except LLMError:
                    raise
                except Exception:
                    logger.debug("LiteLLM stream direct fallback failed", exc_info=True)
            raise LLMError(
                f"LLM stream failed (network): {e}",
                transient=True,
            ) from e
        except Exception as e:
            logger.error("[LLMProvider] stream failed: %s", e, exc_info=True)
            raise LLMError(f"LLM stream failed: {e}", transient=False) from e

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
                if not isinstance(args, dict):
                    # Model emitted a bare list/string instead of an object —
                    # the registry would splat it into nothing. Keep it
                    # visible so validation can produce a corrective error.
                    logger.warning(
                        "[LLMProvider] Tool call '%s' arguments are not a JSON object: %r",
                        func.get("name", "?"), str(args)[:200],
                    )
                    args = {"_malformed": args}
            except json.JSONDecodeError:
                # Truncated/malformed JSON (common with DeepSeek on long
                # contexts). Log the raw payload for diagnosis; the tool
                # registry will reject it with a corrective message.
                logger.warning(
                    "[LLMProvider] Tool call '%s' has unparseable arguments JSON: %r",
                    func.get("name", "?"), str(args_raw)[:500],
                )
                args = {"raw": args_raw}

            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=args,
                )
            )

        usage = data.get("usage", {}) or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cached = 0
        details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or details.get("cache_read_input_tokens") or 0)
        if not cached:
            cached = int(usage.get("cache_read_input_tokens") or 0)
        if cached:
            logger.info(
                "[LLMProvider] prompt cache hit cached_tokens=%s / prompt=%s",
                cached,
                prompt_tokens,
            )

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

    async def _retry_direct_after_gateway_fail(
        self, payload: dict[str, Any], exc: BaseException
    ) -> LLMResponse | None:
        """If the LiteLLM proxy is down and fallback_direct is on, one direct retry."""
        if type(self) is not LLMProvider or not self._via_gateway:
            return None
        try:
            from kazma_core.llm_gateway import get_litellm_gateway

            if not get_litellm_gateway().fallback_direct:
                return None
        except Exception:
            return None
        logger.warning(
            "LiteLLM proxy unreachable (%s) — retrying direct %s",
            type(exc).__name__,
            self._direct_base_url,
        )
        self.reconfigure(
            base_url=self._direct_base_url, api_key=self._direct_api_key
        )
        self._via_gateway = False
        try:
            start = time.monotonic()
            client = await self._get_client()
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            duration_ms = (time.monotonic() - start) * 1000
            return self._parse_response(data, duration_ms)
        except Exception as retry_exc:
            logger.error("LiteLLM direct fallback also failed: %s", retry_exc)
            return None

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
            # Force client recreation on next request — always aclose the old
            # client (audit H9); relying on GC leaks sockets/FDs under frequent
            # Settings provider switches.
            old = self._http
            self._http = None
            if old is not None:
                try:
                    import asyncio

                    try:
                        loop = asyncio.get_running_loop()
                        task = loop.create_task(old.aclose())
                        # Keep a strong reference so CPython doesn't GC the
                        # task before aclose runs ("Task was destroyed but it
                        # is pending!"); drop once done (audit finding).
                        self._pending_closes.add(task)
                        task.add_done_callback(self._pending_closes.discard)
                    except RuntimeError:
                        # No running loop (sync context / tests) — best-effort.
                        try:
                            asyncio.run(old.aclose())
                        except Exception:
                            pass
                except Exception as exc:
                    logger.debug("LLMProvider reconfigure aclose failed: %s", exc)
            logger.info(
                "LLMProvider reconfigured: base_url=%s model=%s api_key=%s",
                self.config.base_url,
                self.config.model,
                "(set)" if self.config.api_key else "(empty)",
            )


class LLMError(Exception):
    """Raised when an LLM API call fails.

    The ``transient`` flag classifies a failure so callers (notably the
    supervisor retry loop) can decide whether a retry is worthwhile:

    * ``transient=True`` — network blips, timeouts, mid-stream read errors,
      rate-limiting (429). Retrying may succeed.
    * ``transient=False`` — 4xx content/auth/schema errors. Retrying with
      the same request will fail identically; fail fast instead.

    Defaults to ``False`` (fail-closed: unknown errors are NOT retried) so
    that pre-existing ``LLMError(...)`` call sites keep their old behavior.

    The optional ``kind`` tag gives watchdog/supervision layers a stable
    machine-readable classification (``""`` = unclassified):

    * ``"context_overflow"`` — the provider rejected the request because the
      prompt exceeds the model's context window. NOT retryable as-is; the
      correct recovery is compact-and-retry, so it is raised with
      ``transient=False`` but is distinguishable from content/schema errors.
    * ``"rate_limit_exhausted"`` — 429 after the provider's own bounded
      Retry-After backoff. Still ``transient=True`` (failover may try another
      model) but the supervisor must not re-retry the same provider.
    """

    def __init__(self, *args: Any, transient: bool = False, kind: str = "") -> None:
        super().__init__(*args)
        self.transient = bool(transient)
        self.kind = str(kind or "")
