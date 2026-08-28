"""Chaos injection: safe enough to leave on a hot path.

The audit found 544 lines of chaos framework with zero call sites -- it
could be switched on and had nothing to inject into. Wiring it is Phase 4.

Wiring it NAIVELY would have made Kazma worse. The original decorator
called `record_call` before checking the kill switch, and `record_call`
takes a process-wide asyncio.Lock unconditionally. Decorating the LLM call,
the database and the tool executor would then have serialised every
decorated call in the process through one lock, in production, forever,
with chaos switched off. These tests exist so that cannot come back.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from kazma_core.chaos import (
    FailureInjection,
    FailureInjector,
    FailureType,
    InjectionTarget,
    chaos_injection,
    get_injector,
    set_injector,
)


@pytest.fixture(autouse=True)
def _fresh_injector():
    set_injector(FailureInjector())
    yield
    set_injector(FailureInjector())


@pytest.fixture
def chaos_on(monkeypatch):
    monkeypatch.setenv("KAZMA_CHAOS_ENABLED", "true")


@pytest.fixture(autouse=True)
def _chaos_off_by_default(monkeypatch):
    monkeypatch.delenv("KAZMA_CHAOS_ENABLED", raising=False)


def _always(target, ftype=FailureType.ERROR, **kw):
    return FailureInjection(failure_type=ftype, target=target,
                            probability=1.0, **kw)


# -- the fast path: nothing is touched when chaos is off ---------------


def test_disabled_never_reaches_the_injector(monkeypatch):
    """The whole point. If the injector is consulted at all when chaos is
    off, a process-wide lock sits on every decorated call."""
    import kazma_core.chaos as chaos

    def _boom():
        raise AssertionError("injector must not be touched when chaos is off")

    monkeypatch.setattr(chaos, "get_injector", _boom)

    @chaos_injection(InjectionTarget.DATABASE)
    async def work():
        return "done"

    assert asyncio.run(work()) == "done"


def test_disabled_never_reaches_the_injector_sync(monkeypatch):
    import kazma_core.chaos as chaos

    monkeypatch.setattr(chaos, "get_injector", lambda: (_ for _ in ()).throw(
        AssertionError("injector must not be touched when chaos is off")))

    @chaos_injection(InjectionTarget.DATABASE)
    def work():
        return "done"

    assert work() == "done"


def test_record_call_is_a_noop_when_disabled():
    """Defence in depth: even called directly it must not take the lock."""
    inj = get_injector()
    asyncio.run(inj.record_call(InjectionTarget.DATABASE))  # must not hang
    assert asyncio.run(inj.get_stats()) == {}


# -- sync support: the database target is not awaitable ----------------


def test_a_sync_function_stays_sync():
    """Wrapping a sync function in a coroutine would silently break every
    caller that does not await it."""

    @chaos_injection(InjectionTarget.DATABASE)
    def work():
        return 42

    assert not asyncio.iscoroutinefunction(work)
    assert work() == 42


def test_sync_error_injection_raises(chaos_on):
    from kazma_core.chaos import ChaosInjectionError

    asyncio.run(get_injector().add_injection(_always(InjectionTarget.DATABASE)))

    @chaos_injection(InjectionTarget.DATABASE)
    def work():
        return "should not get here"

    with pytest.raises(ChaosInjectionError):
        work()


def test_sync_latency_actually_blocks(chaos_on):
    """A slow blocking driver blocks the thread; simulating it with a
    non-blocking sleep would test something that cannot happen."""
    asyncio.run(get_injector().add_injection(
        _always(InjectionTarget.DATABASE, FailureType.LATENCY, latency_ms=120)))

    @chaos_injection(InjectionTarget.DATABASE)
    def work():
        return "ok"

    t0 = time.monotonic()
    assert work() == "ok"
    assert time.monotonic() - t0 >= 0.1


# -- enabled: the framework still does its job -------------------------


def test_async_error_injection_raises(chaos_on):
    from kazma_core.chaos import ChaosInjectionError

    asyncio.run(get_injector().add_injection(
        _always(InjectionTarget.LLM_PROVIDER)))

    @chaos_injection(InjectionTarget.LLM_PROVIDER)
    async def work():
        return "should not get here"

    with pytest.raises(ChaosInjectionError):
        asyncio.run(work())


def test_zero_probability_never_fires(chaos_on):
    asyncio.run(get_injector().add_injection(FailureInjection(
        failure_type=FailureType.ERROR, target=InjectionTarget.LLM_PROVIDER,
        probability=0.0)))

    @chaos_injection(InjectionTarget.LLM_PROVIDER)
    async def work():
        return "ok"

    assert asyncio.run(work()) == "ok"


def test_injection_is_scoped_to_its_target(chaos_on):
    """A fault aimed at the database must not take out the LLM path."""
    asyncio.run(get_injector().add_injection(_always(InjectionTarget.DATABASE)))

    @chaos_injection(InjectionTarget.LLM_PROVIDER)
    async def other():
        return "untouched"

    assert asyncio.run(other()) == "untouched"


def test_the_kill_switch_wins_over_a_registered_injection():
    """An injection left registered must not fire once chaos is switched
    off -- the operator turning it off is the one moment it must obey."""
    asyncio.run(get_injector().add_injection(_always(InjectionTarget.DATABASE)))

    @chaos_injection(InjectionTarget.DATABASE)
    async def work():
        return "ok"

    assert asyncio.run(work()) == "ok"


def test_the_decorator_preserves_identity():
    @chaos_injection(InjectionTarget.CACHE)
    async def nicely_named():
        """A docstring worth keeping."""

    assert nicely_named.__name__ == "nicely_named"
    assert "worth keeping" in (nicely_named.__doc__ or "")


# -- armed but idle: the live install's actual steady state -------------


def test_armed_but_no_injection_skips_the_locks(chaos_on, monkeypatch):
    """KAZMA_CHAOS_ENABLED is ON against the live install, so the disabled
    fast path never runs there. "Armed with nothing aimed at this target"
    is the steady state, and it must not cost two lock acquisitions."""
    inj = get_injector()

    async def _boom(*a, **k):
        raise AssertionError("no injection registered: must not take the lock")

    monkeypatch.setattr(inj, "record_call", _boom)
    monkeypatch.setattr(inj, "should_inject", _boom)

    @chaos_injection(InjectionTarget.LLM_PROVIDER)
    async def work():
        return "ok"

    assert asyncio.run(work()) == "ok"


def test_armed_but_no_injection_skips_the_locks_sync(chaos_on, monkeypatch):
    import kazma_core.chaos as chaos

    monkeypatch.setattr(chaos, "_should_inject_sync", lambda t: (_ for _ in ()).throw(
        AssertionError("no injection registered: must not be consulted")))

    @chaos_injection(InjectionTarget.LLM_PROVIDER)
    def work():
        return "ok"

    assert work() == "ok"


def test_has_injections_tracks_add_and_remove(chaos_on):
    inj = get_injector()
    assert not inj.has_injections(InjectionTarget.CACHE)
    iid = asyncio.run(inj.add_injection(_always(InjectionTarget.CACHE)))
    assert inj.has_injections(InjectionTarget.CACHE), "arming must open the gate"
    asyncio.run(inj.remove_injection(iid))
    assert not inj.has_injections(InjectionTarget.CACHE), (
        "removing the last injection must close it again, or the hot path "
        "stays expensive for the rest of the process's life"
    )


# -- the wiring itself: injection points that actually exist ------------


def test_the_reply_sink_is_wired_to_the_database_target():
    """The audit's finding 03 was 544 lines of chaos with zero call sites:
    a framework you could switch on that had nothing to inject into.

    The reply sink is the highest-value target in the system -- it is the
    single durable writer introduced to fix silent reply loss, and "the
    database failed" is precisely the fault whose recovery was never
    exercised."""
    import inspect

    from kazma_ui import reply_sink

    src = inspect.getsource(reply_sink)
    assert "@chaos_injection(InjectionTarget.DATABASE)" in src


def test_the_tool_executor_is_wired():
    import inspect

    from kazma_core.mcp import manager

    src = inspect.getsource(manager)
    assert "@chaos_injection(InjectionTarget.TOOL_EXECUTOR)" in src


def test_wiring_did_not_turn_a_sync_writer_into_a_coroutine():
    """upsert_reply has sync callers. Returning an un-awaited coroutine
    would make every one of them a silent no-op -- reintroducing the exact
    bug this sink was built to end, via its own instrumentation."""
    from kazma_ui.reply_sink import upsert_reply

    assert not asyncio.iscoroutinefunction(upsert_reply)


def test_wiring_kept_the_tool_executor_awaitable():
    from kazma_core.mcp.manager import UnifiedToolExecutor

    assert asyncio.iscoroutinefunction(UnifiedToolExecutor.execute)


def test_a_database_fault_surfaces_instead_of_being_swallowed(chaos_on):
    """What the injection point is FOR.

    A write that fails must raise, not return quietly. Silent success on a
    failed write is the original defect: the reply looked stored, the row
    was never there, and nobody found out until a reload.
    """
    from kazma_core.chaos import ChaosInjectionError
    from kazma_ui.reply_sink import upsert_reply

    asyncio.run(get_injector().add_injection(_always(InjectionTarget.DATABASE)))
    with pytest.raises(ChaosInjectionError):
        upsert_reply("s1", "t1", "a reply that must not vanish quietly")
