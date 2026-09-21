"""A swarm-bus approval card must be answerable, and must not be asked twice.

THE INCIDENT (operator's install, 2026-09-14 16:41 UTC)

    :28.758  [ToolWorker] exec mcp__filesystem__list_directory_with_sizes x2
    :28.767  [Safety] Danger tool blocked pending approval
    :30.825  [Safety] Danger tool REJECTED: ... (task=)
    :33.039  [SSE] HITL interrupt: tool=shell_exec  <- the Web UI card
    :34.961  HITL GRANT tool=shell_exec

Telegram and Discord both showed:

    REJECTED -- Worker: mcp:filesystem  (auto-reject in 300s)

Two seconds, not three hundred. And note `(task=)`.

TWO DEFECTS, AND THE SECOND IS THE UGLY ONE

1. The card could not be answered by anyone. Every caller reads `task_id`
   out of the TOOL'S OWN ARGUMENTS -- `arguments.get("task_id", "")`. A
   filesystem call carries {"path": ...}. So it is empty for essentially
   every real tool, and `wait_for_resolution` opens with
   `if not task_id: return False`. The adapter posts the card, gets an
   instant False, and edits it to REJECTED. The buttons encode
   `swarm_approve_` with no id, so pressing them would resolve nothing
   either. It was decoration with a countdown printed on it.

2. It should not have been asked at all. The supervisor graph was already
   the HITL authority for that turn -- that is what the :33 Web UI card is.
   `LocalToolRegistry.execute` skips the bus gate on exactly that signal,
   documented as "a second bus prompt would deadlock the turn". The MCP
   manager has its own copy of the gate and never learned it.

`requires_approval` routes `mcp__` names through `classify_mcp_tool` and
treats anything not 'safe' as needing approval, so deferring to the graph
does not widen what runs unasked -- the graph gates these tools itself.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SAFETY = _ROOT / "kazma-core" / "kazma_core" / "swarm" / "safety.py"
_MANAGER = _ROOT / "kazma-core" / "kazma_core" / "mcp" / "manager.py"


class TestAnEmptyIdIsInstantRejection:
    """The mechanism that made the card decorative, stated as a test."""

    def test_wait_for_resolution_refuses_an_empty_id_immediately(self) -> None:
        from kazma_core.swarm.shared_approvals import wait_for_resolution

        async def go() -> bool:
            # A 30s timeout it will not use: the empty id short-circuits.
            return await asyncio.wait_for(
                wait_for_resolution("", timeout=30.0), timeout=2.0
            )

        assert asyncio.run(go()) is False


class TestTheCardGetsARealId:
    def test_check_mints_an_id_when_the_caller_has_none(self) -> None:
        """What actually reaches the bus adapter, captured."""
        from kazma_core.swarm import safety as safety_mod

        seen: dict[str, object] = {}

        class _Bus:
            class _Adapter:
                pass

            adapter = _Adapter()

            async def request_approval(self, **kwargs):
                seen.update(kwargs)
                return True

        mw = safety_mod.SafetyMiddleware()
        mw.enabled = True

        import kazma_core.swarm.bus as bus_mod

        real_get = bus_mod.get_message_bus
        bus_mod.get_message_bus = lambda: _Bus()  # type: ignore[assignment]
        try:
            approved = asyncio.run(
                mw.check("shell_exec", "rm -rf /tmp/x", force_danger=True)
            )
        finally:
            bus_mod.get_message_bus = real_get  # type: ignore[assignment]

        assert approved is True
        assert seen, "the bus was never asked"
        task_id = str(seen.get("task_id") or "")
        assert task_id, "an empty task_id is an unanswerable card"
        assert len(task_id) >= 8, task_id

    def test_two_parallel_requests_get_different_ids(self) -> None:
        """Two calls to the same tool are two questions, not one."""
        from kazma_core.swarm import safety as safety_mod

        ids: list[str] = []

        class _Bus:
            class _Adapter:
                pass

            adapter = _Adapter()

            async def request_approval(self, **kwargs):
                ids.append(str(kwargs.get("task_id") or ""))
                return True

        import kazma_core.swarm.bus as bus_mod

        real_get = bus_mod.get_message_bus
        bus_mod.get_message_bus = lambda: _Bus()  # type: ignore[assignment]
        try:
            mw = safety_mod.SafetyMiddleware()
            mw.enabled = True

            async def both():
                await asyncio.gather(
                    mw.check("shell_exec", "a", force_danger=True),
                    mw.check("shell_exec", "b", force_danger=True),
                )

            asyncio.run(both())
        finally:
            bus_mod.get_message_bus = real_get  # type: ignore[assignment]

        assert len(ids) == 2 and all(ids), ids
        assert ids[0] != ids[1], "parallel calls collided on one approval key"

    def test_an_explicit_task_id_is_still_used(self) -> None:
        """Minting must not override a caller that knows the real id."""
        src = _SAFETY.read_text(encoding="utf-8")
        assert "approval_id = task_id" in src
        assert "if not approval_id:" in src
        assert "task_id=approval_id," in src


class TestTheGraphIsNotOverruled:
    """Parsed, not grepped: the check must be real code, not a comment."""

    def test_the_mcp_gate_does_not_treat_graph_ownership_as_approval(self) -> None:
        """The graph flag is set for the whole turn, including calls nobody approved.

        Clearing ``force_hitl`` on that flag let ``read_env`` run. The skip
        that prevents a second prompt is the approval ContextVar, set only
        after this call was approved.
        """
        src = _MANAGER.read_text(encoding="utf-8")
        tree = ast.parse(src)
        names = {
            n.id for n in ast.walk(tree) if isinstance(n, ast.Name)
        } | {
            n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
        }
        assert "_hitl_approved_ctx" in names
        assert "_graph_hitl_gate_ctx" not in names
        assert "_graph_owns_gate" not in names

    def test_it_turns_the_prompt_off_rather_than_approving(self) -> None:
        """A recorded approval skips the ASK. It must not invent a yes."""
        src = _MANAGER.read_text(encoding="utf-8")
        assert "_hitl_already_approved = _hitl_approved_ctx.get()" in src
        assert "if not _hitl_already_approved and not _server_trusted:" in src
        assert "force_hitl = False" not in src
        assert "approved = True" not in src.split("force_hitl = not mcp_safe_allowlisted", 1)[-1][:500]

    def test_both_gates_honour_an_actual_approval(self) -> None:
        """Local tools and MCP tools skip a second prompt on the same signal.

        That signal is ``_hitl_approved_ctx``. The graph-authority flag stays
        on the local registry, where the tool list is ours. It is not an
        MCP approval.
        """
        registry = (
            _ROOT / "kazma-core" / "kazma_core" / "agent" / "tool_registry.py"
        ).read_text(encoding="utf-8")
        manager = _MANAGER.read_text(encoding="utf-8")
        assert "_hitl_approved_ctx" in registry
        assert "_hitl_approved_ctx" in manager
        assert "_graph_hitl_gate_ctx" in registry
        assert "_graph_hitl_gate_ctx" not in manager


class TestTheCountdownIsHonest:
    def test_the_adapter_prints_the_timeout_it_will_actually_wait(self) -> None:
        """The card said 300s while the answer came in 2.

        The countdown text is fine; it was the id that made the wait a lie.
        Pinned so a future change cannot print one number and use another.
        """
        tg = (
            _ROOT / "kazma-gateway" / "kazma_gateway" / "adapters" / "telegram_bus.py"
        ).read_text(encoding="utf-8")
        body = tg.split("async def request_approval", 1)[1]
        assert "reject in {int(timeout)}s" in body
        wait_call = re.search(
            r"wait_for_resolution\(\s*approval\.task_id,\s*timeout=timeout", body
        )
        assert wait_call, "the wait must use the same timeout the card advertises"
