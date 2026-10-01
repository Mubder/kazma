"""The IDE's swarm dispatch builds its environment block off the loop (2026-10-02).

``IdeService.send_to_swarm`` -- the web IDE's "send to swarm" and the chat
``/ide`` commands -- ran the blocking environment builder (two git probes of
up to 4 s each plus a workspace-store read) on the event loop that serves
every chat, "on purpose" since 2026-08-27. It now awaits the threaded
builder. The block must still reach the dispatched task (AGENTS.md §10C).
The loop-stall gate lists ``_build_env_context_sync``, so no async code can
call it directly again.
"""

from __future__ import annotations

import threading

import pytest


@pytest.mark.asyncio
async def test_the_environment_block_is_built_off_the_loop_and_attached(monkeypatch):
    from kazma_core.ide import env_context
    from kazma_core.ide.service import IdeService
    from kazma_core.swarm import engine as engine_mod

    builds: list[tuple[int, str | None]] = []

    def build(workspace_id: str | None = None) -> str:
        builds.append((threading.get_ident(), workspace_id))
        return "## Environment\nroot: ws"

    dispatched: list = []

    class _Engine:
        async def dispatch(self, task):
            dispatched.append(task)

    monkeypatch.setattr(env_context, "_build_env_context_sync", build)
    monkeypatch.setattr(engine_mod, "get_swarm_engine", lambda: _Engine())

    result = await IdeService().send_to_swarm(
        "fix the failing test", context="tests/test_x.py", workspace_id="ws-1"
    )

    assert result["ok"], result
    assert builds and builds[0][1] == "ws-1"
    assert builds[0][0] != threading.get_ident(), "the builder ran on the event loop's thread"
    context = dispatched[0].context
    assert context.startswith("## Environment")
    assert context.endswith("--- Task context ---\ntests/test_x.py")


def test_the_blocking_builder_is_a_loop_stall_helper():
    from tests.test_static_gates import _LOOP_STALL_HELPERS

    assert "_build_env_context_sync" in _LOOP_STALL_HELPERS
