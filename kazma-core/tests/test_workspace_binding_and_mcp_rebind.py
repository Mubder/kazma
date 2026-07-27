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
