"""Daily, decision and concurrency limits hold work without orphaning a slot."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from kazma_core.config_store import get_config_store
from kazma_core.x_api.ai_budget import DEFAULT_LIMITS, XBudgetUnavailable, reserve_daily
from kazma_core.x_api.model_selection import current_x_usage, x_chat, x_model_scope


@pytest.fixture
def provider(monkeypatch):
    calls = []
    class Client:
        async def chat(self, messages, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content="draft")
    class Registry:
        def get_client(self):
            return Client()
    monkeypatch.setattr("kazma_core.model_registry.get_model_registry", lambda: Registry())
    return calls


async def test_decision_limit_and_output_ceiling_are_reserved_before_call(provider):
    get_config_store().set("connectors.x.ai_limits", {"max_calls_per_decision": 1})
    async with x_model_scope():
        await x_chat("drafting", [{"role": "user", "content": "brief"}], max_tokens=100)
        with pytest.raises(XBudgetUnavailable):
            await x_chat("verification", [{"role": "user", "content": "check"}], max_tokens=100)
        assert current_x_usage()["calls"] == 1 and current_x_usage()["reserved_output_tokens"] == 100
    assert len(provider) == 1


async def test_oversized_input_does_not_call_or_charge_provider(provider):
    get_config_store().set("connectors.x.ai_limits", {"max_input_bytes_per_call": 20})
    async with x_model_scope():
        with pytest.raises(XBudgetUnavailable):
            await x_chat("drafting", [{"role": "user", "content": "x" * 21}], max_tokens=100)
    assert provider == [] and get_config_store().get("connectors.x.ai_usage") is None


def test_daily_limit_is_atomic_across_workers():
    limits = {**DEFAULT_LIMITS, "max_daily_calls": 1}
    def reserve():
        try:
            reserve_daily(100, limits)
            return True
        except XBudgetUnavailable:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: reserve(), range(8)))
    assert sum(results) == 1 and get_config_store().get("connectors.x.ai_usage")["calls"] == 1


@pytest.mark.parametrize("usage", ["broken", {"day": "future", "calls": 0, "reserved_output_tokens": 0},
                                  {"day": "2099-01-01", "calls": 0, "reserved_output_tokens": 0}])
def test_corrupted_or_future_usage_fails_closed(usage):
    get_config_store().set("connectors.x.ai_usage", usage)
    with pytest.raises(XBudgetUnavailable):
        reserve_daily(100, DEFAULT_LIMITS)


async def test_cancellation_while_acquiring_slot_does_not_leak(provider, monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    semaphore.acquire()
    monkeypatch.setattr("kazma_core.x_api.ai_budget.CALL_SLOTS", semaphore)
    async def decision():
        async with x_model_scope():
            return await x_chat("drafting", [{"role": "user", "content": "brief"}], max_tokens=100)
    task = asyncio.create_task(decision())
    # Ensure the reservation reached the acquiring stage before cancelling.
    for _ in range(100):
        usage = await asyncio.to_thread(get_config_store().get, "connectors.x.ai_usage")
        if usage:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    semaphore.release()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert semaphore.acquire(blocking=False), "A cancelled constructor must return any acquired slot"
    semaphore.release()
    assert provider == []


async def test_provider_failure_retains_daily_reservation(provider, monkeypatch):
    async def fail(*args, **kwargs):
        raise TimeoutError("unknown provider outcome")
    monkeypatch.setattr("kazma_core.x_api.model_selection.get_x_client", lambda role: asyncio.sleep(0, result=SimpleNamespace(chat=fail)))
    async with x_model_scope():
        with pytest.raises(TimeoutError):
            await x_chat("drafting", [{"role": "user", "content": "brief"}], max_tokens=100)
    assert get_config_store().get("connectors.x.ai_usage")["reserved_output_tokens"] == 100
