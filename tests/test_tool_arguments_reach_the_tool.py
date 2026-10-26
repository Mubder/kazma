"""A tool receives every argument the model sent it (audit 2026-10-26 AUD-001).

``UnifiedToolExecutor.execute`` -- the executor every chat surface's graph runs
tool calls through (``agent_runner``: ``self.tools``) -- popped ``user_id``,
``division``, ``resource`` and ``action`` out of the arguments as "RBAC
context". ``email_send(action="draft")`` was approved on a card that said
``draft`` and reached the tool with no ``action``: its default is ``"send"``.
The RBAC check those keys fed ran only when the model itself named a user.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kazma_core.agent.tool_registry import LocalToolRegistry
from kazma_core.mcp.manager import AsyncMCPManager, UnifiedToolExecutor

_SENT = {
    "to": "a@example.test",
    "action": "draft",
    "resource": "inbox",
    "user_id": "u-1",
    "division": "ops",
}


def _executor_with_recorder() -> tuple[UnifiedToolExecutor, list[dict[str, Any]]]:
    seen: list[dict[str, Any]] = []
    local = LocalToolRegistry(include_builtins=False)

    async def mail_tool(
        to: str = "",
        action: str = "send",
        resource: str = "",
        user_id: str = "",
        division: str = "",
    ) -> str:
        seen.append(
            {"to": to, "action": action, "resource": resource,
             "user_id": user_id, "division": division}
        )
        return f"{action} ok"

    local.register_function("mail_tool", mail_tool, description="test")
    return UnifiedToolExecutor(local=local, mcp=AsyncMCPManager()), seen


def test_every_argument_reaches_the_tool(monkeypatch) -> None:
    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")  # a test tool, not registered
    executor, seen = _executor_with_recorder()
    result = asyncio.run(executor.execute("mail_tool", dict(_SENT)))
    assert not result.get("is_error"), result
    assert seen == [_SENT]
    assert "draft ok" in str(result.get("content"))


def test_the_old_prelude_strips_them(monkeypatch) -> None:
    """Negative control: the removed prelude, run on the same call."""

    def old_prelude(arguments: dict[str, Any]) -> dict[str, Any]:
        arguments.pop("user_id", arguments.pop("_user_id", None))
        arguments.pop("division", arguments.pop("_division", None))
        arguments.pop("resource", arguments.pop("_resource", None))
        arguments.pop("action", arguments.pop("_action", None))
        return arguments

    monkeypatch.setenv("KAZMA_COMMITMENT_ENABLED", "0")
    executor, seen = _executor_with_recorder()
    asyncio.run(executor.execute("mail_tool", old_prelude(dict(_SENT))))
    assert seen[0]["action"] == "send"  # the draft became a send
    assert seen[0] != _SENT


def test_execute_no_longer_reads_rbac_keys_from_the_arguments() -> None:
    import inspect

    from kazma_core.mcp import manager

    src = inspect.getsource(manager.UnifiedToolExecutor.execute)
    for key in ("user_id", "division", "resource", "action"):
        assert f'arguments.pop("{key}"' not in src
    assert "check_permission" not in src
