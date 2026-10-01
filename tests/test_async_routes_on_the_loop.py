"""An async route that never awaits says why it runs on the event loop.

A handler that never awaits is a plain ``def``: FastAPI runs it in its
threadpool, context included (AGENTS §35). An ``async def`` that never
awaits runs its whole body on the loop that serves every chat stream, which
is right only when the loop is the point: it reads or changes state the loop
owns (the swarm engine's tasks, breakers and workers, an adapter's queue),
it starts a background task or returns a stream, or it is a probe whose
answer proves the loop is alive.

The debt ratchet counted these (115 on 2026-10-01). After the sweep that
day every one left names its reason here, and a new one fails until it is a
``def`` or has a reason. Its detector is the ratchet's
(``async_routes_never_awaiting``). A handler that DOES await but also
blocks is ``tests/test_get_routes_off_the_loop.py``'s subject.
"""

from __future__ import annotations

import re

from tests.test_debt_ratchet import _is_test_path, _tracked, async_routes_never_awaiting

_LOOP_STATE = "reads or changes the swarm engine's in-memory state, which the loop owns"

#: "path:function" -> why it runs on the loop.
ON_THE_LOOP: dict[str, str] = {
    "kazma-ui/kazma_ui/health.py:liveness": (
        "the liveness probe: an answer from the loop proves the loop is alive"
    ),
    "kazma-ui/kazma_ui/routes_direct/misc.py:health_check": (
        "the /health probe: gateway counters in memory, answered from the loop like liveness"
    ),
    "kazma-ui/kazma_ui/routes_direct/settings.py:_settings_memory_rebuild": (
        "starts the rebuild with spawn_background, which needs the running loop; "
        "the rebuild itself runs in a thread"
    ),
    "kazma-ui/kazma_ui/telemetry_route.py:telemetry_stream": (
        "returns the telemetry stream, whose generator runs on the loop"
    ),
    "kazma-gateway/kazma_gateway/adapters/telegram.py:webhook_health": (
        "reads the running Telegram adapter's asyncio queue, which the loop owns"
    ),
    "kazma-ui/kazma_ui/mcp_ui.py:api_server_tools": (
        "reads the connected MCP server's tool list, held by the executor on the loop"
    ),
    "kazma-ui/kazma_ui/models_route.py:ollama_pulls": (
        "reads the pull tasks' progress, which the pull coroutines update on the loop"
    ),
    "kazma-ui/kazma_ui/swarm_panel/routes_general.py:reap_idle_workers": (
        "removes idle workers from the swarm engine, whose state the loop owns"
    ),
    "kazma-ui/kazma_ui/swarm_panel/routes_tasks.py:swarm_active_tasks": _LOOP_STATE,
    "kazma-ui/kazma_ui/swarm_panel/routes_workers.py:swarm_worker_logs": _LOOP_STATE,
    "kazma-ui/kazma_ui/swarm_panel/routes_workers.py:swarm_circuit_breakers": _LOOP_STATE,
    "kazma-ui/kazma_ui/swarm_panel/routes_workers.py:swarm_worker_circuit_breaker": _LOOP_STATE,
}


def _key(item: str) -> str:
    """``"path:12 name"`` (the detector's form) -> ``"path:name"``."""
    rel, rest = item.split(":", 1)
    return f"{rel}:{rest.split(' ', 1)[1]}"


def _never_awaiting() -> set[str]:
    product = {
        f: t for f, t in _tracked(["*.py"]).items()
        if f.startswith("kazma-") and not _is_test_path(f)
    }
    return {_key(i) for i in async_routes_never_awaiting(product)}


def test_every_async_route_that_never_awaits_says_why() -> None:
    undeclared = sorted(_never_awaiting() - set(ON_THE_LOOP))
    assert not undeclared, (
        "These async route handlers never await, so their whole body runs on "
        "the event loop. Make each a plain def (FastAPI threadpools it), await "
        "asyncio.to_thread around its blocking work, or -- when the loop is the "
        "point -- declare it in ON_THE_LOOP with the reason:\n  " + "\n  ".join(undeclared)
    )


def test_no_declaration_is_stale() -> None:
    stale = sorted(set(ON_THE_LOOP) - _never_awaiting())
    assert not stale, f"These now await or are plain defs; remove them from ON_THE_LOOP: {stale}"


def test_every_reason_says_something() -> None:
    thin = [k for k, why in ON_THE_LOOP.items() if len(re.findall(r"\w+", why)) < 6]
    assert not thin, thin


def test_an_undeclared_one_is_found() -> None:
    """Negative control (§28): the detector sees a planted handler."""
    planted = {
        "kazma-ui/kazma_ui/planted.py": (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            "@router.get('/x')\n"
            "async def reads_a_store():\n"
            "    return open('x').read()\n"
            "@router.get('/y')\n"
            "def threaded():\n"
            "    return 1\n"
        )
    }
    assert {_key(i) for i in async_routes_never_awaiting(planted)} == {
        "kazma-ui/kazma_ui/planted.py:reads_a_store"
    }
