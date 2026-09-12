"""Building an SSL context must not happen on the event loop.

`httpx.AsyncClient(...)` builds a default SSL context at construction, which
loads the system CA store. `LLMProvider._get_client` is `async`, so that load
ran ON the loop — blocking every other turn, heartbeat and websocket for its
duration — and was paid again on every client rebuild (provider switch, key
change, reconfigure).

Caught in production by the operator's loop-stall watchdog, 2026-09-12. The
dump's thread `0x0000e790` has `base_events._run_once` → `run_forever` →
uvicorn → `serve.py` at its base, so it is unambiguously the event-loop thread,
and its top frames were::

    ssl.py                  create_default_context
    llm_provider.py         _get_client
    llm_provider.py         chat_stream
    graph_supervisor.py     supervisor_node

**What is proven and what is not.** That the call sat on the loop: proven by
the stack. That it accounts for the full 16 seconds the watchdog reported: not
proven — one sample caught the loop there, and a warm `create_default_context`
measures ~19ms locally. It is a real blocking call in the LLM hot path and it
is now paid once, in a worker thread. Any remaining stall is a separate
question and should be measured rather than assumed fixed.
"""

from __future__ import annotations

import asyncio
import ssl

import pytest
import kazma_core.llm_provider as lp
from kazma_core.llm_provider import _shared_ssl_context


@pytest.fixture(autouse=True)
def _fresh_context():
    lp._SSL_CONTEXT = None
    yield
    lp._SSL_CONTEXT = None


@pytest.mark.asyncio
async def test_the_context_is_built_once_and_reused():
    first = await _shared_ssl_context()
    second = await _shared_ssl_context()
    assert first is second, "a second call must not reload the CA store"
    assert isinstance(first, ssl.SSLContext)


@pytest.mark.asyncio
async def test_it_is_built_off_the_event_loop():
    """The whole point. If the build ran inline, the loop would be blocked for
    its duration -- which is exactly the production stall."""
    seen: dict[str, object] = {}
    real = ssl.create_default_context

    def _record(*a, **kw):
        seen["thread"] = __import__("threading").current_thread().name
        return real(*a, **kw)

    loop_thread = __import__("threading").current_thread().name
    orig = ssl.create_default_context
    ssl.create_default_context = _record  # type: ignore[assignment]
    try:
        await _shared_ssl_context()
    finally:
        ssl.create_default_context = orig  # type: ignore[assignment]

    assert seen["thread"] != loop_thread, (
        f"create_default_context ran on the loop thread ({loop_thread})"
    )


@pytest.mark.asyncio
async def test_verification_stays_on():
    """A 'fix' that quietly disabled certificate verification would be a far
    worse bug than the stall it cured."""
    ctx = await _shared_ssl_context()
    assert ctx is not False, "verify=False would disable TLS verification"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


@pytest.mark.asyncio
async def test_a_failure_degrades_to_httpx_default_not_to_insecure():
    """If the context cannot be built, fall back to httpx's own verification
    (`True`) -- never to `False`."""
    orig = ssl.create_default_context

    def _boom(*a, **kw):
        raise OSError("no CA store")

    ssl.create_default_context = _boom  # type: ignore[assignment]
    try:
        got = await _shared_ssl_context()
    finally:
        ssl.create_default_context = orig  # type: ignore[assignment]

    assert got is True, "must degrade to verify=True, not to verify=False"


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_build():
    """Twenty coroutines racing on first use must not trigger twenty CA loads
    -- the lock is what keeps a burst of turns from re-creating the stall."""
    builds = 0
    real = ssl.create_default_context

    def _count(*a, **kw):
        nonlocal builds
        builds += 1
        return real(*a, **kw)

    ssl.create_default_context = _count  # type: ignore[assignment]
    try:
        results = await asyncio.gather(*(_shared_ssl_context() for _ in range(20)))
    finally:
        ssl.create_default_context = real  # type: ignore[assignment]

    assert builds == 1, f"CA store loaded {builds} times under concurrency"
    assert len({id(r) for r in results}) == 1


def test_the_client_actually_uses_it():
    """The helper is inert unless the client is handed the context."""
    import inspect

    src = inspect.getsource(lp.LLMProvider._get_client)
    assert "verify=await _shared_ssl_context()" in src
