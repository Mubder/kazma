"""Every exported mutation/exec alias needs a secret and the canonical gate."""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from kazma_gateway import mcp_server


@pytest.mark.parametrize("alias,canonical", [("write_file", "file_write"),
                                              ("run_tests", "run_unit_tests"),
                                              ("run_command", "shell_exec")])
@pytest.mark.parametrize("secret", [None, "wrong", "خطأ", "correct"])
@pytest.mark.parametrize("allowed", [False, True])
def test_mutating_alias_needs_both_auth_and_gate(monkeypatch, tmp_path, alias, canonical, secret, allowed):
    from kazma_core.swarm import safety

    monkeypatch.setenv("KAZMA_SECRET", "correct")
    gate = MagicMock(enabled=True)
    gate.is_danger_tool.return_value = True
    gate.check_sync.return_value = allowed
    monkeypatch.setattr(safety, "get_safety", lambda: gate)
    run = MagicMock(return_value={"ok": True})
    monkeypatch.setitem(mcp_server.DISPATCH, alias, run)
    args = {} if secret is None else {"_secret": secret}
    result = json.loads(mcp_server.MCPServer(tmp_path)._handle_tools_call(
        1, {"name": alias, "arguments": args}))
    if secret == "correct" and allowed:
        assert "error" not in result
        run.assert_called_once()
        gate.check_sync.assert_called_once_with(canonical)
        assert "_secret" not in run.call_args.args[1]
    else:
        assert "error" in result
        run.assert_not_called()
    # Caller-owned JSON is not consumed or mutated by authentication.
    assert args == ({} if secret is None else {"_secret": secret})


def test_all_exported_aliases_have_explicit_canonical_tiers():
    from kazma_core.safety.hitl import TOOL_TIERS

    assert set(mcp_server.MCP_TOOL_TO_SAFETY) == set(mcp_server.DISPATCH)
    assert all(name in TOOL_TIERS for name in mcp_server.MCP_TOOL_TO_SAFETY.values())
