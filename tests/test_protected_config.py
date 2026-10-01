"""The agent cannot change config that would disarm it or run code
(audit 2026-09-30, AUD-007).

Two lists guarded this before, and neither covered ``agent.hooks.*`` (shell
commands run on every tool call) or ``mcp.servers`` (server commands spawned
by the MCP manager): the agent could write its own persistent command
execution through a config change. One list now, used by ``config_save`` and
the commitment ``config_change`` resolver.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kazma_core.safety.protected_config import (
    PROTECTED_CONFIG_PREFIXES,
    is_protected_config_key,
)

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("key", [
    "agent.hooks.pre_tool", "agent.hooks.post_tool", "agent.hooks.enabled",
    "mcp.servers", "mcp.oauth.github.client_secret",
    "safety.require_approval_for", "safety.hitl_enabled",
    "agent.commitment.mode", "yolo.window", "yolo",
    "security.secret", "kazma_secret", "vault.master",
    "notifications.lifecycle.events",
    # Not settings at all (settings_restore.classify, 2026-10-01): approval
    # grants, sessions, the learned Soul.
    "task_grant.t1", "hitl_grant.t1.shell_exec", "path_grant.t1.g1",
    "web_session.abc", "account.password_hash", "self_improvement.agent_evolution",
    "long_task.t1", "platform.users",
    # A tenant's copy is the same key (tenant_isolation.tenant_key).
    "tenant.acme.mcp.servers", "tenant.acme.safety.hitl_enabled",
])
def test_command_and_safety_keys_are_protected(key) -> None:
    assert is_protected_config_key(key) is True


@pytest.mark.parametrize("key", [
    "agent.personality", "agent.language", "agent.max_iterations",
    "connectors.telegram.allowed_users", "connectors.discord.allowed_users",
    "models.defaults.chat", "", "   ",
])
def test_ordinary_keys_are_not_protected(key) -> None:
    assert is_protected_config_key(key) is False


def test_config_save_blocks_a_hook_and_an_mcp_server(monkeypatch) -> None:
    """The tool refuses the keys that carry commands, whatever it is handed."""
    from kazma_core.agent.tool_registry import LocalToolRegistry

    reg = LocalToolRegistry()
    from kazma_core.agent.tool_builtins.system import register_system_tools

    register_system_tools(reg)
    save = None
    for name in ("config_save",):
        save = reg._tools.get(name) if hasattr(reg, "_tools") else None
    assert save is not None, "config_save is registered"
    fn = save.func
    for key in ("agent.hooks.post_tool", "mcp.servers", "safety.hitl_enabled",
                "task_grant.t1", "tenant.acme.mcp.servers"):
        out = fn(key=key, value='["echo x"]')
        assert out.startswith("Error: Cannot modify restricted key"), (key, out)
    # The value checks the Settings page applies (settings_validation).
    bad = fn(key="cron.timezone", value="Mars/Olympus")
    assert bad.startswith("Error: Invalid timezone"), bad
    ok = fn(key="agent.personality", value="cheerful")
    assert ok.startswith("Setting saved"), ok


def _string_prefix_tuples(path: str) -> list[tuple[str, ...]]:
    """Every tuple-of-string-constants literal in *path* (a hand-rolled
    protected/blocked list would be one)."""
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Tuple) and len(node.elts) >= 3 and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts
        ):
            found.append(tuple(e.value for e in node.elts))
    return found


def test_neither_call_site_keeps_its_own_list() -> None:
    """Both import the shared predicate; a re-introduced local list of these
    prefixes fails here."""
    for path in (
        "kazma-core/kazma_core/agent/tool_builtins/system.py",
        "kazma-core/kazma_core/safety/commitment/authorize.py",
    ):
        src = (REPO / path).read_text(encoding="utf-8")
        assert "is_protected_config_key" in src, path
        for tup in _string_prefix_tuples(path):
            overlap = {t.rstrip(".") for t in tup} & {p.rstrip(".") for p in PROTECTED_CONFIG_PREFIXES}
            assert len(overlap) < 3, f"{path} re-declares a protected-key list: {tup}"


def test_the_commitment_resolver_denies_a_hook_write() -> None:
    from kazma_core.safety.commitment import authorize

    key = "agent.hooks.post_tool"
    assert authorize.is_protected_config_key(key) is True
