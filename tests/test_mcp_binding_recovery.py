"""Workspace bindings describe connected handles, even across failures."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kazma_core.mcp.manager import AsyncMCPManager, MCPServerHandle
from kazma_core.workspace import binding, mcp_rebind


@pytest.fixture
def roots(tmp_path, monkeypatch):
    old, new = tmp_path / "old", tmp_path / "new"
    old.mkdir()
    new.mkdir()
    monkeypatch.setattr(binding, "_bound_mcp_root", old.resolve())
    monkeypatch.setattr(mcp_rebind, "_rebind_lock", asyncio.Lock())
    monkeypatch.setattr(mcp_rebind, "_last_rebind_at", 0.0)
    return old, new


@pytest.mark.asyncio
async def test_failed_rebind_cannot_claim_new_root(roots):
    old, new = roots
    ex = SimpleNamespace(
        _server_configs={"fs": {"name": "fs", "workspace_bound": True, "command": ["fixture", "${KAZMA_ACTIVE_WORKSPACE}"]}},
        disconnect_server=AsyncMock(side_effect=ConnectionError("old connection survives")),
        connect_server=AsyncMock(),
    )
    assert await mcp_rebind.rebind_workspace_mcp_servers(new, executor=ex) == 0
    assert binding.get_bound_mcp_root() != new.resolve()
    ex.connect_server.assert_not_awaited()


@pytest.mark.asyncio
async def test_swallowed_connection_failure_is_not_success(roots):
    _, new = roots
    ex = SimpleNamespace(
        _server_configs={"fs": {"name": "fs", "workspace_bound": True}},
        disconnect_server=AsyncMock(return_value=True),
        connect_server=AsyncMock(return_value=0),
        is_server_connected=lambda _: False,
    )
    assert await mcp_rebind.rebind_workspace_mcp_servers(new, executor=ex) == 0
    assert binding.get_bound_mcp_root() is None


@pytest.mark.asyncio
async def test_concurrent_scope_creation_spawns_once(roots, monkeypatch):
    _, new = roots
    mgr = AsyncMCPManager()
    mgr._server_templates["fs"] = {"name": "fs", "workspace_bound": True}
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def connect(name, cfg):
        calls.append(name)
        entered.set()
        await release.wait()
        mgr._servers[name] = MCPServerHandle(name=name, transport="stdio", connected=True)
        return 0

    monkeypatch.setattr(mgr, "_connect_stdio", connect)
    one = asyncio.create_task(mgr._get_or_spawn_scoped("fs", new))
    await entered.wait()
    two = asyncio.create_task(mgr._get_or_spawn_scoped("fs", new))
    await asyncio.sleep(0)
    release.set()
    first, second = await asyncio.gather(one, two)
    assert len(calls) == 1
    assert first is second


@pytest.mark.asyncio
async def test_scoped_capacity_does_not_evict_busy_handle(roots, monkeypatch):
    old, new = roots
    mgr = AsyncMCPManager()
    mgr._MAX_SCOPED = 1
    busy = MCPServerHandle(name="busy", transport="stdio", connected=True)
    busy.in_flight = 1
    mgr._scoped[("fs", str(old))] = busy
    mgr._server_templates["fs"] = {"name": "fs", "workspace_bound": True}

    async def connect(name, cfg):
        mgr._servers[name] = MCPServerHandle(name=name, transport="stdio", connected=True)

    monkeypatch.setattr(mgr, "_connect_stdio", connect)
    close = AsyncMock()
    monkeypatch.setattr(mgr, "_close_handle", close)
    assert await mgr._get_or_spawn_scoped("fs", new) is None
    assert busy in mgr._scoped.values()
    assert not any(c.args[0] is busy for c in close.call_args_list)


@pytest.mark.asyncio
async def test_scope_checks_actual_handle_root(roots, monkeypatch):
    old, new = roots
    mgr = AsyncMCPManager()
    mgr._server_templates["fs"] = {"name": "fs", "workspace_bound": True}
    handle = MCPServerHandle(name="fs", transport="stdio", connected=True)
    handle.workspace_root = old.resolve()
    monkeypatch.setattr(binding, "_bound_mcp_root", new.resolve())
    import kazma_core.ide.workspace_scope as scope
    monkeypatch.setattr(scope, "resolve_workspace_root", lambda: new.resolve())
    monkeypatch.setattr(mgr, "_get_or_spawn_scoped", AsyncMock(return_value=None))
    _, denial = await mgr._route_workspace_scope("fs", handle)
    assert denial is not None and denial["is_error"]


def test_initial_binding_is_recorded_only_from_connected_handles(roots, monkeypatch):
    old, new = roots
    manager = AsyncMCPManager()
    ex = SimpleNamespace(_mcp=manager, _server_configs={"fs": {"name": "fs", "workspace_bound": True}})
    monkeypatch.setattr(mcp_rebind, "_executor_ref", ex)
    mcp_rebind.record_verified_mcp_binding(manager)
    assert binding.get_bound_mcp_root() is None
    manager._servers["fs"] = MCPServerHandle(name="fs", transport="stdio", connected=True, workspace_root=old.resolve())
    mcp_rebind.record_verified_mcp_binding(manager)
    assert binding.get_bound_mcp_root() == old.resolve()
    manager._servers["fs"].workspace_root = new.resolve()
    manager._servers["fs"].connected = False
    mcp_rebind.record_verified_mcp_binding(manager)
    assert binding.get_bound_mcp_root() is None
