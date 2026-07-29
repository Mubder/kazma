"""Workspace binding SoT + MCP workspace rebind unit tests."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_resolve_active_root_respects_process_pin(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.workspace import binding as b

    # Isolate from store/env
    monkeypatch.delenv("KAZMA_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )

    root = tmp_path / "ws"
    root.mkdir()
    b.configure_workspace(str(root))
    assert b.resolve_active_root() == root.resolve()
    b.configure_workspace(None)


def test_default_sandbox_under_data_dir(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.workspace.binding import default_sandbox_root

    monkeypatch.setattr(
        "kazma_core.paths.data_dir",
        lambda: tmp_path / "kazma-data",
    )
    root = default_sandbox_root()
    assert root == (tmp_path / "kazma-data" / "workspace").resolve()
    assert root.is_dir()


def test_notify_root_changed_calls_subscribers(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.workspace import binding as b

    monkeypatch.delenv("KAZMA_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )

    seen: list[tuple[Path, str]] = []

    def cb(root: Path, reason: str) -> None:
        seen.append((root, reason))

    b.subscribe_root_changed(cb)
    try:
        target = tmp_path / "shipx"
        target.mkdir()
        b.notify_root_changed(target, reason="test")
        assert len(seen) == 1
        assert seen[0][0] == target.resolve()
        assert seen[0][1] == "test"
        # Pin alone resolves when store has no active row
        assert b.resolve_active_root() == target.resolve()
    finally:
        b.unsubscribe_root_changed(cb)
        b.configure_workspace(None)


def test_apply_workspace_substitutes_filesystem_root(tmp_path: Path) -> None:
    from kazma_core.workspace.mcp_rebind import (
        ACTIVE_WORKSPACE_PLACEHOLDER,
        apply_workspace_to_server_config,
        is_workspace_bound_server,
    )

    root = tmp_path / "repo"
    root.mkdir()
    cfg = {
        "name": "filesystem",
        "transport": "stdio",
        "workspace_bound": True,
        "command": [
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem",
            ACTIVE_WORKSPACE_PLACEHOLDER,
        ],
    }
    assert is_workspace_bound_server(cfg)
    out = apply_workspace_to_server_config(cfg, root)
    assert out["command"][-1] == str(root.resolve())
    assert out["_resolved_workspace"] == str(root.resolve())


def test_legacy_relative_sandbox_arg_replaced(tmp_path: Path) -> None:
    from kazma_core.workspace.mcp_rebind import apply_workspace_to_server_config

    root = tmp_path / "active"
    root.mkdir()
    cfg = {
        "name": "filesystem",
        "transport": "stdio",
        "command": [
            "npx",
            "-y",
            "@modelcontextprotocol/server-filesystem",
            "kazma-data/workspace",
        ],
    }
    out = apply_workspace_to_server_config(cfg, root)
    assert out["command"][-1] == str(root.resolve())


def test_file_write_get_workspace_delegates(tmp_path: Path, monkeypatch) -> None:
    from kazma_core.tools.file_write import _get_workspace
    from kazma_core.workspace import binding as b

    monkeypatch.delenv("KAZMA_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )
    root = tmp_path / "pin"
    root.mkdir()
    b.configure_workspace(str(root))
    assert _get_workspace() == root.resolve()
    b.configure_workspace(None)


@pytest.mark.asyncio
async def test_notify_root_changed_schedules_async_subscriber(tmp_path: Path, monkeypatch) -> None:
    """An async subscriber (returns a coroutine) is scheduled without errors.

    Regression for the workspace-delete warning:
    ``subscriber _on_root_changed failed: a coroutine was expected, got <Task>``.
    The subscriber must return a bare coroutine (awaitable), NOT a pre-scheduled
    Task, so notify_root_changed's dispatcher creates exactly one task for it.
    """
    from kazma_core.workspace import binding as b

    monkeypatch.delenv("KAZMA_WORKSPACE", raising=False)
    monkeypatch.setattr(
        "kazma_core.stores.get_workspace_store",
        lambda: type("S", (), {"get_active_workspace": staticmethod(lambda: None)})(),
    )

    fired = []

    async def async_cb(root: Path, reason: str) -> None:
        fired.append((root, reason))

    b.subscribe_root_changed(async_cb)
    try:
        target = tmp_path / "repo"
        target.mkdir()
        # Must not raise "a coroutine was expected, got <Task>".
        b.notify_root_changed(target, reason="test")
        # Give the scheduled task a chance to run.
        import asyncio

        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert len(fired) == 1
        assert fired[0][0] == target.resolve()
        assert fired[0][1] == "test"
    finally:
        b.unsubscribe_root_changed(async_cb)
        b.configure_workspace(None)


@pytest.mark.asyncio
async def test_on_root_changed_returns_coroutine_not_task(tmp_path: Path) -> None:
    """_on_root_changed must return a bare coroutine, not a pre-scheduled Task.

    If it returns a Task, notify_root_changed's dispatcher tries to wrap the
    Task in another task and throws. This pins the corrected contract.
    """
    import asyncio
    from collections.abc import Coroutine

    from kazma_core.workspace import mcp_rebind

    target = tmp_path / "repo"
    target.mkdir()

    result = mcp_rebind._on_root_changed(target, "set_active_workspace")
    try:
        # Must be a bare coroutine (awaitable), not an asyncio.Task/Future.
        assert isinstance(result, Coroutine), (
            f"expected a coroutine, got {type(result).__name__}"
        )
        assert not isinstance(result, asyncio.Future), (
            "must not be a pre-scheduled Task/Future"
        )
    finally:
        # Close the coroutine to avoid "coroutine was never awaited" warning.
        result.close()
