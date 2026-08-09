"""Resilient LLM chat for non-graph paths (swarm workers, research tools).

The supervisor graph has its own retry+failover in ``graph_builder``; swarm
workers (``swarm/worker.py``) and the research tools (``tools/research_*.py``)
previously called ``provider.chat()`` with either a small local retry loop or
no resilience at all. This module gives those paths the same guarantees:

* transient-only retries with exponential backoff (permanent 4xx fails fast);
* optional model failover via ``agent.nonstop.failover.chain`` — one-off
  clients from the registry, active profile never mutated, per-model
  cooldowns so a failing provider isn't hammered;
* a per-call ledger record (``observability.llm_ledger``) for every
  success and terminal failure.

Best-effort by contract: ledger/config failures never break the call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

__all__ = ["resilient_chat"]

logger = logging.getLogger(__name__)

# Failover client cache + cooldowns for non-graph paths (the graph keeps its
# own in graph_builder; keyed identically by model id).
_clients: dict[str, Any] = {}
_cooldowns: dict[str, float] = {}


def _retryable() -> tuple[type[Exception], ...]:
    exc: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, asyncio.TimeoutError)
    try:
        import httpx

        exc = exc + (
            httpx.TimeoutException,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.ReadError,
        )
    except ImportError:
        pass
    return exc


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, _retryable()):
        return True
    return bool(getattr(exc, "transient", False))


def _record(**kwargs: Any) -> None:
    try:
        from kazma_core.agent.nonstop import get_nonstop_config

        if not get_nonstop_config().ledger_enabled:
            return
        from kazma_core.observability.llm_ledger import record_llm_call

        record_llm_call(**kwargs)
    except Exception:
        pass


async def resilient_chat(
    client: Any,
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    max_attempts: int = 3,
    backoff_base: float = 2.0,
    thread_id: str = "",
    iteration: int = 0,
    label: str = "",
    **chat_kwargs: Any,
) -> Any:
    """Call ``client.chat`` with transient retries, then failover chain.

    Args:
        client:       Primary LLMProvider-like object (``async chat``).
        messages:     Conversation messages.
        tools:        Optional tool schemas.
        model:        Model override passed to the primary client.
        max_attempts: Primary-model attempts before failover (min 1).
        backoff_base: First retry wait in seconds (doubles per attempt, cap 30s).
        thread_id:    Ledger correlation id (swarm task id / turn id).
        iteration:    Ledger iteration counter.
        label:        Log prefix (e.g. worker name) for diagnostics.
        chat_kwargs:  Extra kwargs forwarded to ``client.chat`` (e.g.
                      ``max_tokens``).

    Returns the first successful response. Raises the last error when the
    primary and all failover models fail.
    """
    from kazma_core.llm_provider import LLMError

    retryable = _retryable()
    attempts = max(1, int(max_attempts))
    last_exc: BaseException | None = None
    start = time.monotonic()

    for attempt in range(1, attempts + 1):
        try:
            response = await client.chat(
                messages,
                tools=tools if tools else None,
                model=model,
                **chat_kwargs,
            )
            usage = getattr(response, "usage", {}) or {}
            _record(
                thread_id=thread_id,
                iteration=iteration,
                provider=type(client).__name__,
                model=str(getattr(response, "model", "") or model or ""),
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
                duration_ms=(time.monotonic() - start) * 1000,
                status="ok",
            )
            return response
        except retryable as exc:
            last_exc = exc
        except LLMError as exc:
            last_exc = exc
            if not getattr(exc, "transient", False):
                raise  # permanent — fail fast, no retry, no failover
        if attempt < attempts:
            wait = min(backoff_base * (2 ** (attempt - 1)), 30.0)
            logger.warning(
                "[%s] LLM call attempt %d/%d failed transiently — retrying in %.0fs",
                label or "resilient-chat",
                attempt,
                attempts,
                wait,
            )
            await asyncio.sleep(wait)

    # ── Failover chain (opt-in via agent.nonstop.failover) ────────────
    try:
        from kazma_core.agent.nonstop import get_nonstop_config

        ns = get_nonstop_config()
    except Exception:
        ns = None
    if ns is not None and ns.failover.enabled and ns.failover.chain:
        try:
            from kazma_core.model_registry import get_model_registry

            registry = get_model_registry()
        except Exception:
            registry = None
        if registry is not None:
            now = time.monotonic()
            for fb_model in ns.failover.chain:
                if not fb_model or fb_model == model:
                    continue
                cooled_until = _cooldowns.get(fb_model, 0.0)
                if now < cooled_until:
                    continue
                try:
                    fb_client = _clients.get(fb_model)
                    if fb_client is None:
                        fb_client = registry.get_client(fb_model)
                        _clients[fb_model] = fb_client
                    logger.warning(
                        "[%s] primary model failed transiently — failover to '%s'",
                        label or "resilient-chat",
                        fb_model,
                    )
                    response = await fb_client.chat(
                        messages,
                        tools=tools if tools else None,
                        model=fb_model,
                        **chat_kwargs,
                    )
                    usage = getattr(response, "usage", {}) or {}
                    _record(
                        thread_id=thread_id,
                        iteration=iteration,
                        provider=type(fb_client).__name__,
                        model=fb_model,
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
                        duration_ms=(time.monotonic() - start) * 1000,
                        status="ok",
                        failover_from=str(model or ""),
                    )
                    return response
                except Exception as fb_exc:
                    _cooldowns[fb_model] = now + ns.failover.cooldown_seconds
                    logger.warning(
                        "[%s] failover model '%s' failed: %s (cooldown %.0fs)",
                        label or "resilient-chat",
                        fb_model,
                        fb_exc,
                        ns.failover.cooldown_seconds,
                    )

    _record(
        thread_id=thread_id,
        iteration=iteration,
        provider=type(client).__name__,
        model=str(model or ""),
        duration_ms=(time.monotonic() - start) * 1000,
        status="error",
        error_kind=str(getattr(last_exc, "kind", "") or type(last_exc).__name__),
    )
    assert last_exc is not None
    raise last_exc
