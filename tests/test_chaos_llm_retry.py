"""Does an injected provider failure actually reach the retry?

Phase 4's finding was that a mechanism can exist, pass its unit test, and
still be incapable of running -- the repetition breaker had never been
able to fire, and a green test on its detector proved nothing, because
the detector was never the broken part.

Chaos injection is only worth having if it lands on the real path. Two
things had to be true and were not:

* ``ChaosInjectionError`` carried no notion of transience, so an injected
  503 -- the textbook retry-me response -- would propagate straight out
  of ``resilient_chat`` past the retry loop it was injected to exercise.
* Injecting around ``resilient_chat`` from the outside would bypass both
  the retry and the failover, and the experiment would "pass" while
  measuring nothing but exception propagation.

So these tests assert on the number of attempts, not on the outcome.
"""

from __future__ import annotations

import pytest
from kazma_core import chaos
from kazma_core.agent import resilient_chat as rc


class _Response:
    def __init__(self):
        self.usage = {"prompt_tokens": 1, "completion_tokens": 1}
        self.model = "test-model"
        self.cost_usd = 0.0


class _Client:
    """A provider that always succeeds; every failure here is injected."""

    def __init__(self):
        self.calls = 0

    async def chat(self, messages, **kw):
        self.calls += 1
        return _Response()


@pytest.fixture
def chaos_on(monkeypatch):
    monkeypatch.setenv("KAZMA_CHAOS_ENABLED", "1")
    injector = chaos.FailureInjector()
    chaos.set_injector(injector)
    yield injector
    chaos.set_injector(chaos.FailureInjector())


def _injection(**kw):
    return chaos.FailureInjection(
        failure_type=kw.pop("failure_type", chaos.FailureType.ERROR),
        target=chaos.InjectionTarget.LLM_PROVIDER,
        probability=1.0,
        **kw,
    )


# -- transience, which decides whether the retry runs at all -------------

@pytest.mark.parametrize("code,transient", [
    (503, True),    # provider overloaded -- try again
    (504, True),    # gateway timeout
    (429, True),    # rate limited
    (408, True),    # request timeout
    (400, False),   # malformed request -- trying again changes nothing
    (401, False),   # bad key
    (403, False),   # forbidden
])
def test_injected_failures_declare_transience_like_real_ones(code, transient):
    err = chaos.ChaosInjectionError("injected", error_code=code)
    assert err.transient is transient


# -- the injection has to reach the loop, not sit outside it -------------

async def test_transient_injection_is_retried_then_succeeds(chaos_on):
    """One injected 503 must cost one retry and still return a response."""
    fired = {"n": 0}
    real = chaos.FailureInjection.should_inject

    def once(self):
        # Fail the first attempt only, so a working retry is observable
        # as a success -- a permanently-failing injection cannot tell a
        # retry that ran from one that never existed.
        if fired["n"] == 0 and real(self):
            fired["n"] += 1
            return True
        return False

    chaos.FailureInjection.should_inject = once
    try:
        await chaos_on.add_injection(_injection(error_code=503))
        client = _Client()
        resp = await rc.resilient_chat(
            client, messages=[{"role": "user", "content": "hi"}],
            max_attempts=3, backoff_base=0.0,
        )
    finally:
        chaos.FailureInjection.should_inject = real

    assert resp is not None
    assert fired["n"] == 1, "the injection never fired"
    assert client.calls == 1, "the provider was not reached on the retry"


async def test_permanent_injection_is_not_retried(chaos_on):
    """A 4xx must fail fast. Retrying a bad request is just latency."""
    await chaos_on.add_injection(_injection(error_code=400))
    client = _Client()

    with pytest.raises(chaos.ChaosInjectionError):
        await rc.resilient_chat(
            client, messages=[{"role": "user", "content": "hi"}],
            max_attempts=3, backoff_base=0.0,
        )
    assert client.calls == 0


async def test_chaos_off_is_a_hard_gate(monkeypatch):
    """The kill-switch is checked at the trigger point, so an injection
    registered by accident cannot fail a real call."""
    monkeypatch.delenv("KAZMA_CHAOS_ENABLED", raising=False)
    injector = chaos.FailureInjector()
    chaos.set_injector(injector)
    try:
        await injector.add_injection(_injection(error_code=503))
        client = _Client()
        resp = await rc.resilient_chat(
            client, messages=[{"role": "user", "content": "hi"}], backoff_base=0.0,
        )
        assert resp is not None
        assert client.calls == 1
    finally:
        chaos.set_injector(chaos.FailureInjector())
