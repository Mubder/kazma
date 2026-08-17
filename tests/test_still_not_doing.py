"""The leftover items that were 'still not doing' — now wired."""

from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent


def test_chat_ide_swarm_soft_nav_and_teardown():
    nav = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "modules" / "nav.js").read_text(
        encoding="utf-8"
    )
    hard = nav.split("HARD_RELOAD_ALWAYS")[1].split("]")[0]
    assert "'/chat'" not in hard
    assert "'/ide'" not in hard
    assert "'/swarm'" not in hard
    assert "isSoftNavPageScript" in nav
    chat = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "chat.js").read_text(
        encoding="utf-8"
    )
    ide = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "ide.js").read_text(
        encoding="utf-8"
    )
    swarm = (_ROOT / "kazma-ui" / "kazma_ui" / "static" / "js" / "swarm.js").read_text(
        encoding="utf-8"
    )
    assert "kazmaOnSoftNavLeave" in chat
    assert "kazmaOnSoftNavLeave" in ide
    assert "kazmaOnSoftNavLeave" in swarm


def test_disclosure_and_division_routes_exist():
    settings = (_ROOT / "kazma-ui" / "kazma_ui" / "settings.py").read_text(encoding="utf-8")
    assert "/api/security/disclosure" in settings
    assert "/api/divisions/status" in settings
    registry = (
        _ROOT / "kazma-core" / "kazma_core" / "agent" / "tool_registry.py"
    ).read_text(encoding="utf-8")
    assert "check_division_tool" in registry


@pytest.mark.asyncio
async def test_division_fail_open_without_context(monkeypatch):
    from kazma_core.division_runtime import check_division_tool, reset_division_runtime

    monkeypatch.delenv("KAZMA_DIVISION", raising=False)
    monkeypatch.delenv("KAZMA_DIVISION_ENFORCE", raising=False)
    reset_division_runtime()
    assert await check_division_tool("mcp__tourism-booking-api__book") is None


@pytest.mark.asyncio
async def test_division_blocks_denied_mcp(monkeypatch, tmp_path):
    from kazma_core.division_runtime import check_division_tool, reset_division_runtime
    from kazma_core.rbac import RBACEngine
    import kazma_core.division_runtime as dr

    monkeypatch.setenv("KAZMA_DIVISION", "gas_oil")
    reset_division_runtime()
    dr._rbac = RBACEngine(db_path=str(tmp_path / "rbac.db"))
    err = await check_division_tool("mcp__tourism-booking-api__book")
    assert err is not None
    assert "division" in err.lower()


def test_unified_index_roundtrip(tmp_path, monkeypatch):
    from kazma_core.memory import unified_index as ui

    monkeypatch.setattr(
        "kazma_core.paths.memory_ops_db", lambda: str(tmp_path / "ops.db")
    )
    ui.upsert_unified(item_id="b1", kind="belief", text="user lives_in Kuwait", tenant_id="t")
    ui.upsert_unified(item_id="kb:1", kind="kb", text="Kuwait office handbook", tenant_id="t")
    hits = ui.search_unified("Kuwait", tenant_id="t", limit=10)
    kinds = {h["kind"] for h in hits}
    assert "belief" in kinds
    assert "kb" in kinds
