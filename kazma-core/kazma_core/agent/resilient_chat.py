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

from kazma_core.observability.llm_ledger import provider_of

__all__ = ["reset_failover_cache", "resilient_chat"]

logger = logging.getLogger(__name__)

# Failover client cache + cooldowns for non-graph paths (the graph keeps its
# own in graph_supervisor; keyed identically by model id). Good for one
# registry generation (FA-106): a provider's new key or base URL reaches the
# next failover, not the next restart.
_clients: dict[str, Any] = {}
_cooldowns: dict[str, float] = {}
_built_for: int | None = None


def reset_failover_cache() -> None:
    """Forget the cached failover clients and cooldowns (a model or provider
    changed). ``KazmaAgent.sync_active_model`` calls it beside the graph's."""
    global _built_for
    _clients.clear()
    _cooldowns.clear()
    _built_for = None


def _follow(registry: Any) -> None:
    """Drop the cache when the registry changed since it was built."""
    global _built_for
    generation = getattr(registry, "generation", None)
    if isinstance(generation, int) and generation != _built_for:
        reset_failover_cache()
        _built_for = generation


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


def _chaos_error() -> type[BaseException]:
    """The injected-failure type, or a class nothing ever raises."""
    try:
        from kazma_core.chaos import ChaosInjectionError

        return ChaosInjectionError
    except Exception:  # noqa: BLE001

        class _UnraisableError(Exception):
            pass

        return _UnraisableError


def _chaos_gate() -> Any:
    """Return the chaos hook for LLM calls, or None when chaos is off.

    Injected here rather than around ``resilient_chat`` as a whole,
    because the point of an experiment is to exercise the retry and
    failover this function performs. A failure injected on the outside
    would bypass both and prove only that an exception propagates.
    """
    try:
        from kazma_core.chaos import InjectionTarget, get_injector

        injector = get_injector()
        if not injector.has_injections(InjectionTarget.LLM_PROVIDER):
            return None
        return (injector, InjectionTarget.LLM_PROVIDER)
    except Exception:  # noqa: BLE001 -- chaos must never break a real call
        return None


async def _maybe_inject(gate: Any) -> None:
    if not gate:
        return
    injector, target = gate
    try:
        injection = await injector.should_inject(target)
    except Exception:  # noqa: BLE001
        return
    if injection is None:
        return
    from kazma_core import chaos as _chaos

    logger.warning("[Chaos] Injecting %s into LLM_PROVIDER (%s)",
                   injection.failure_type.value, injection.injection_id)
    await _chaos._apply_injection(injection)


async def _record(**kwargs: Any) -> None:
    """The call's ledger row, written off the loop and awaited (the ledger is
    SQLite, and the switch that turns it on a settings read). It was written
    on the loop until 2026-10-15, once per worker and research model call.
    Never raises but the caller's own cancellation (``arecord_llm_call``)."""
    from kazma_core.observability.llm_ledger import arecord_llm_call

    await arecord_llm_call(**kwargs)


def _primary_name(client: Any, model: str | None) -> str:
    """The model the primary call asked for: the override, else the client's own."""
    return str(model or getattr(getattr(client, "config", None), "model", "") or "")


def _kwargs_for(client: Any, chat_kwargs: dict[str, Any]) -> dict[str, Any]:
    """*chat_kwargs* for *client*: ``reasoning`` only where it is a switch.

    ``LLMProvider.chat`` takes ``reasoning="off"`` (DeepSeek's own API turns
    hidden reasoning off; elsewhere it is ignored), but the providers with a
    ``chat`` of their own (Anthropic, Gemini, Azure, Bedrock) take no such
    argument, and a failover can reach one of them.
    """
    if "reasoning" not in chat_kwargs:
        return chat_kwargs
    can_switch = getattr(client, "can_turn_off_reasoning", None)
    if callable(can_switch) and can_switch() is True:
        return chat_kwargs
    return {key: value for key, value in chat_kwargs.items() if key != "reasoning"}


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
    frozen_failover: Any = None,
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
                      ``max_tokens``). ``reasoning="off"`` reaches only a
                      client whose ``can_turn_off_reasoning()`` is True.

    Returns the first successful response. Raises the last error when the
    primary and all failover models fail.
    """
    from kazma_core.llm_provider import LLMError

    retryable = _retryable()
    _ChaosInjectionError = _chaos_error()
    attempts = max(1, int(max_attempts))
    last_exc: BaseException | None = None
    start = time.monotonic()

    gate = _chaos_gate()

    for attempt in range(1, attempts + 1):
        try:
            await _maybe_inject(gate)
            response = await client.chat(
                messages,
                tools=tools if tools else None,
                model=model,
                **_kwargs_for(client, chat_kwargs),
            )
            usage = getattr(response, "usage", {}) or {}
            await _record(
                thread_id=thread_id,
                iteration=iteration,
                provider=provider_of(client),
                model=str(getattr(response, "model", "") or model or ""),
                prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
                duration_ms=(time.monotonic() - start) * 1000,
                status="ok",
                priced=bool(getattr(response, "cost_known", True)),
            )
            # The primary answered: a failover it was under is over.
            from kazma_core.observability.model_fallback import report_model_served

            report_model_served(_primary_name(client, model))
            return response
        except retryable as exc:
            last_exc = exc
        except LLMError as exc:
            last_exc = exc
            if not getattr(exc, "transient", False):
                raise  # permanent — fail fast, no retry, no failover
        except _ChaosInjectionError as exc:
            # Same rule as a real provider error, deliberately: an
            # injected 503 must take the retry path, or the experiment
            # measures nothing but exception propagation.
            last_exc = exc
            if not getattr(exc, "transient", False):
                raise
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

        ns = await asyncio.to_thread(get_nonstop_config) if frozen_failover is None else None
    except Exception:
        ns = None
    chain = tuple(name for name, _ in frozen_failover.clients) if frozen_failover is not None else tuple(ns.failover.chain) if ns and ns.failover.enabled else ()
    cooldown_seconds = frozen_failover.cooldown_seconds if frozen_failover is not None else ns.failover.cooldown_seconds if ns else 0
    if chain:
        try:
            from kazma_core.model_registry import get_model_registry

            registry = get_model_registry() if frozen_failover is None else None
        except Exception:
            registry = None
        if registry is not None:
            _follow(registry)
        if registry is not None or frozen_failover is not None:
            now = time.monotonic()
            for fb_model in chain:
                if not fb_model or fb_model == model:
                    continue
                cooled_until = _cooldowns.get(fb_model, 0.0)
                if now < cooled_until:
                    continue
                try:
                    fb_client = dict(frozen_failover.clients)[fb_model] if frozen_failover is not None else _clients.get(fb_model)
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
                        **_kwargs_for(fb_client, chat_kwargs),
                    )
                    usage = getattr(response, "usage", {}) or {}
                    await _record(
                        thread_id=thread_id,
                        iteration=iteration,
                        provider=provider_of(fb_client),
                        model=fb_model,
                        prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
                        completion_tokens=int(usage.get("completion_tokens", 0) or 0),
                        cost_usd=float(getattr(response, "cost_usd", 0.0) or 0.0),
                        duration_ms=(time.monotonic() - start) * 1000,
                        status="ok",
                        failover_from=str(model or ""),
                        priced=bool(getattr(response, "cost_known", True)),
                    )
                    from kazma_core.observability.model_fallback import report_failover

                    report_failover(
                        model=_primary_name(client, model), used_model=fb_model,
                        reason=str(getattr(last_exc, "kind", "") or type(last_exc).__name__),
                    )
                    return response
                except Exception as fb_exc:
                    _cooldowns[fb_model] = now + cooldown_seconds
                    logger.warning(
                        "[%s] failover model '%s' failed: %s (cooldown %.0fs)",
                        label or "resilient-chat",
                        fb_model,
                        fb_exc,
                        cooldown_seconds,
                    )

    await _record(
        thread_id=thread_id,
        iteration=iteration,
        provider=provider_of(client),
        model=str(model or ""),
        duration_ms=(time.monotonic() - start) * 1000,
        status="error",
        error_kind=str(getattr(last_exc, "kind", "") or type(last_exc).__name__),
    )
    assert last_exc is not None
    raise last_exc
