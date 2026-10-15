"""A swarm and a memory with no model behind them, for tests that run them
end to end (2026-10-15).

``tests/_egress_guard.py`` refuses the real embedder and the network. A test
that runs a swarm task or a memory write through the real engine reaches the
model paths beside the one it is about:

* the embedder -- a fact's vector (``vector_write``), recall, the swarm's
  result bridge (``swarm_bridge.store_swarm_result``), the Knowledge
  Library's meaning search (``vector_store_global``, which imports
  ``get_embedder`` at module level), and the swarm's semantic router, a
  process-wide singleton that keeps the embedder it first saw;
* the model -- the swarm Refiner asks the registry for a client, and
  self-improvement analyses every finished task.

:func:`keep_models_out` closes them the way the rest of the suite does: no
embedder (``get_embedder`` -> None, so meaning search and routing fall back
to keywords), the model package absent (``sys.modules`` holds None for it,
as on CI where it is not installed: memory health's package check and a
local embedder built directly both see it missing instead of importing
torch), self-improvement off (its kill switch), and a registry client that
fails like a provider with no usable key -- the answer every caller already
handles (the Refiner then writes its raw report) -- without building a real
one.
"""

from __future__ import annotations

from typing import Any


class NoModelClient:
    """An LLM client that refuses every call, as an unconfigured provider does."""

    async def chat(self, *args: Any, **kwargs: Any) -> Any:
        from kazma_core.llm_provider import LLMError

        raise LLMError("no model in tests (tests/_no_models.py)", transient=False, kind="auth")


def keep_models_out(monkeypatch: Any, *, embedder: Any = None, llm: bool = True) -> None:
    """No real embedder or model for this test (see the module docstring).

    ``embedder`` is what ``get_embedder`` answers (None: no meaning search);
    ``llm=False`` leaves the registry's clients alone, for a test that
    configures its own.
    """
    import sys

    from kazma_core.swarm import semantic_router

    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")
    if "sentence_transformers" not in sys.modules:
        monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.vector_store_global.get_embedder", lambda: embedder)
    # A fresh router for this test, built from the embedder above; the
    # process's own comes back after it.
    monkeypatch.setattr(semantic_router, "_semantic_router", None)
    if llm:
        from kazma_core.model_registry import ModelRegistry

        monkeypatch.setattr(ModelRegistry, "get_client", lambda self, *a, **k: NoModelClient())


def finish_turn_memory(timeout: float = 15.0) -> None:
    """Wait for the post-turn memory threads a test's turns started.

    A finished turn hands its memory write to a daemon thread
    (``consolidator._schedule_post_turn_memory``, ``kazma-v2-extract``),
    which outlived the test that ran the turn: it embedded the turn after
    the test's stand-ins were undone, with the real embedder. A fixture
    that runs turns calls this before its own teardown ends.
    """
    import threading
    import time

    deadline = time.monotonic() + timeout
    for thread in list(threading.enumerate()):
        if thread.name == "kazma-v2-extract" and thread is not threading.current_thread():
            thread.join(max(0.0, deadline - time.monotonic()))
