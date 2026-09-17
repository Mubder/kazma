"""Audit 2026-09-17 — regressions for the cold-read follow-up.

Each test pins a defect that was green in ``tests/test_static_gates.py``
while the live path was wrong (nested helpers, a second ContextVar, a
third-party tool name).
"""

from __future__ import annotations

import ast
import os
import pathlib

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_hitl_and_vault_tenant_setters_mirror():
    """graph_tool_worker binds HITL; vault reads tenant_context. They must move together."""
    from kazma_core.safety import hitl
    from kazma_core.tenant_context import (
        get_current_tenant_id,
        reset_current_tenant_id,
        set_current_tenant_id,
    )

    token = set_current_tenant_id("audit-17")
    try:
        assert get_current_tenant_id() == "audit-17"
        assert hitl.get_current_tenant_id() == "audit-17"
    finally:
        reset_current_tenant_id(token)

    token = hitl.set_current_tenant_id("audit-17-hitl")
    try:
        assert hitl.get_current_tenant_id() == "audit-17-hitl"
        assert get_current_tenant_id() == "audit-17-hitl"
    finally:
        hitl.reset_current_tenant_id(token)


def test_mcp_force_hitl_does_not_trust_safe_names():
    src = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "mcp" / "manager.py"
    ).read_text(encoding="utf-8")
    assert 'force_hitl = tool_name.lower() not in allowlist' in src
    assert 'force_hitl = tier in ("danger", "unknown")' not in src


def test_mcp_get_prompt_fences():
    """Fence once, in spec_tools — not also in manager + tool_builtins."""
    spec = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "mcp" / "spec_tools.py"
    ).read_text(encoding="utf-8")
    mgr = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "mcp" / "manager.py"
    ).read_text(encoding="utf-8")
    builtins = (
        REPO_ROOT
        / "kazma-core"
        / "kazma_core"
        / "agent"
        / "tool_builtins"
        / "mcp.py"
    ).read_text(encoding="utf-8")
    assert "fence_untrusted" in spec
    assert 'source=f"mcp_prompt:' in spec or "mcp_prompt:" in spec
    # manager.get_prompt must not wrap again (triple-fence follow-up).
    get_prompt = mgr.split("async def get_prompt", 1)[1].split("async def ", 1)[0]
    assert "fence_untrusted" not in get_prompt
    assert "fence_untrusted" not in builtins


def test_error_message_is_declared_on_supervisor_state():
    from kazma_core.agent.state import SupervisorState, initial_supervisor_state

    assert "error_message" in SupervisorState.__annotations__
    state = initial_supervisor_state()
    assert state.get("error_message") == ""


def test_supervisor_sets_error_message_on_turn_failed():
    src = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "agent" / "graph_supervisor.py"
    ).read_text(encoding="utf-8")
    assert '"error_message": error_content' in src


def test_resolve_redirects_blocks_unresolved_hops():
    src = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "security" / "ssrf.py"
    ).read_text(encoding="utf-8")
    assert "block_unresolved: bool = True" in src
    assert "validate_url(url, block_unresolved=block_unresolved)" in src
    assert "validate_url(current, block_unresolved=block_unresolved)" in src


def test_dialect_pipelines_do_not_call_the_llm():
    src = (
        REPO_ROOT / "kazma-core" / "kazma_core" / "router.py"
    ).read_text(encoding="utf-8")
    assert "get_model_registry" not in src
    assert "await provider.chat" not in src


def test_proposal_filter_module_exists():
    from kazma_core.safety.commitment.proposals import is_proposal_tool

    assert is_proposal_tool("x_post")
    assert is_proposal_tool("x_schedule_post")
    assert is_proposal_tool("book_x_post")
    assert not is_proposal_tool("save_proposal")


def test_proposal_fallback_matches_sot():
    """The ImportError copy in the tool worker must equal PROPOSAL_TOOLS."""
    from kazma_core.agent.graph_tool_worker import _PROPOSAL_PUBLISH_FALLBACK
    from kazma_core.safety.commitment.proposals import PROPOSAL_TOOLS

    assert _PROPOSAL_PUBLISH_FALLBACK == PROPOSAL_TOOLS


def test_git_mint_retry_passes_force_as_keyword():
    src = (
        REPO_ROOT
        / "kazma-skills"
        / "kazma_skills"
        / "native"
        / "git_github_manager"
        / "tools.py"
    ).read_text(encoding="utf-8")
    assert "mint_app_installation_token, force=True" in src
    assert "mint_app_installation_token, True)" not in src


def test_git_sync_has_no_nested_subprocess_run():
    path = (
        REPO_ROOT
        / "kazma-skills"
        / "kazma_skills"
        / "native"
        / "git_github_manager"
        / "tools.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef) or node.name != "_git_sync":
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            func = sub.func
            if isinstance(func, ast.Attribute) and func.attr == "run":
                if isinstance(func.value, ast.Name) and func.value.id == "subprocess":
                    pytest.fail(
                        f"_git_sync still calls subprocess.run at line {sub.lineno}"
                    )


def test_sse_persist_does_not_create_task_in_a_lambda():
    src = (
        REPO_ROOT / "kazma-ui" / "kazma_ui" / "sse_chat" / "_streaming.py"
    ).read_text(encoding="utf-8")
    assert "spawn_background" in src
    assert "lambda: _turn_loop.create_task" not in src.replace(" ", "")


def test_saas_status_requires_admin():
    src = (
        REPO_ROOT / "kazma-ui" / "kazma_ui" / "saas_api.py"
    ).read_text(encoding="utf-8")
    # The status handler must call _require_admin (the only route that skipped it).
    status_idx = src.index("async def saas_status")
    users_idx = src.index("async def list_platform_users")
    block = src[status_idx:users_idx]
    assert "_require_admin" in block


@pytest.mark.asyncio
async def test_sqlite_query_requires_path_and_denies_internal(tmp_path):
    from kazma_skills.native.database_client.tools import (
        _deny_internal,
        sqlite_query,
    )

    missing = await sqlite_query("SELECT 1")
    assert "db_path is required" in missing

    vault = tmp_path / "vault.db"
    vault.write_bytes(b"")
    denied = _deny_internal(str(vault))
    assert denied is not None
    assert "internal database" in denied.lower()


def test_remote_db_host_denied_without_allowlist(monkeypatch):
    from kazma_skills.native.database_client.tools import _remote_host_error

    monkeypatch.delenv("KAZMA_DB_CLIENT_ALLOWED_HOSTS", raising=False)
    err = _remote_host_error("postgresql://db.example.com/x", "postgres")
    assert err is not None
    assert "KAZMA_DB_CLIENT_ALLOWED_HOSTS" in err

    loopback = _remote_host_error("postgresql://localhost/x", "postgres")
    assert loopback is None
