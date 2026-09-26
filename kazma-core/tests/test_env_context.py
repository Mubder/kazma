"""Tests for the IDE awareness layer (env_context) + workspace scope (Phase 3).

These lock in the three properties that make the IDE a "primary element":

  1. ``build_env_context()`` always returns a non-empty block naming the
     workspace root, even in a bare non-git directory (graceful degradation).
  2. In a git repo, it detects the repo slug and branch.
  3. The per-task ``workspace_scope`` (Phase 3) makes ``_get_workspace()``
     resolve to the scoped workspace, not the global one — so concurrent
     tasks can target different repos without colliding.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kazma_core.ide.env_context import build_env_context
from kazma_core.ide.workspace_scope import (
    current_workspace_id,
    resolve_workspace_root,
    workspace_scope,
)


@pytest.fixture
def tmp_ws(tmp_path, monkeypatch):
    """Isolated workspace dir + WorkspaceStore (no real settings.db leak).

    Note: empty WorkspaceStore auto-seeds ``Default Workspace`` at ``cwd``
    (the Kazma monorepo). We immediately create + activate our temp root.
    """
    from kazma_core.stores import reset_workspace_store
    import kazma_core.stores.workspaces as wsmod
    from kazma_core.tools.file_write import configure_workspace

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(ws))
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    reset_workspace_store()
    store = wsmod.WorkspaceStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(wsmod, "_workspace_store", store)
    rec = store.create_workspace("test-ws", str(ws))
    store.set_active_workspace(rec["id"])
    configure_workspace(workspace=str(ws))
    yield ws
    store.close()
    reset_workspace_store()
    configure_workspace(workspace=None)


async def test_env_context_always_names_workspace_root(tmp_ws):
    """Even with no git repo, the block must name the workspace root."""
    block = await build_env_context()
    assert "Active Workspace" in block or "Workspace root" in block
    assert str(tmp_ws) in block
    # No repo in a bare dir — must NOT claim a real remote.
    assert "Repository: `" not in block or "not a git" in block.lower() or "unknown" in block.lower()


async def test_env_context_detects_git_repo(tmp_ws):
    """In a git repo the slug + branch are detected."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_ws), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_ws), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_ws), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/myrepo.git"],
        cwd=str(tmp_ws), check=True,
    )
    block = await build_env_context()
    assert "owner/myrepo" in block
    assert "Hard rules" in block or "BINDING" in block


async def test_env_context_lists_tools(tmp_ws):
    """The block announces the available tools so the brain is aware."""
    block = await build_env_context()
    # At least file_read/file_write/shell_exec should be named.
    assert "file_read" in block or "Available tools" in block


async def test_workspace_scope_is_noop_when_none():
    """A None workspace_id scope is a no-op (backward compatible)."""
    assert current_workspace_id() is None
    async with workspace_scope(None):
        assert current_workspace_id() is None  # still None
    assert current_workspace_id() is None


async def test_workspace_scope_pins_resolution(tmp_path, monkeypatch):
    """A scoped workspace_id overrides the global _get_workspace()."""
    from kazma_core.stores import reset_workspace_store
    from kazma_core.tools.file_write import _get_workspace, configure_workspace

    # Point the store at a temp DB so we don't mutate the real settings.db.
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "settings.db")

    # Build a store with two workspaces.
    import kazma_core.stores.workspaces as wsmod

    reset_workspace_store()
    store = wsmod.WorkspaceStore(db_path)
    monkeypatch.setattr(wsmod, "_workspace_store", store)

    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    repo_a.mkdir()
    repo_b.mkdir()
    rec_a = store.create_workspace("Repo A", str(repo_a))
    rec_b = store.create_workspace("Repo B", str(repo_b))
    store.set_active_workspace(rec_a["id"])

    try:
        # Without scope, active workspace is repo_a.
        assert _get_workspace().resolve() == repo_a.resolve()

        # Inside a scope for repo_b, _get_workspace must resolve to repo_b.
        async with workspace_scope(rec_b["id"]):
            resolved = _get_workspace().resolve()
            assert resolved == repo_b.resolve(), (
                f"scope should pin to {repo_b}, got {resolved}"
            )

        # After exiting the scope, we're back to the active workspace (repo_a).
        assert _get_workspace().resolve() == repo_a.resolve()
    finally:
        # Close the SQLite connection before resetting so Windows can
        # clean up the temp dir (open file handles block deletion).
        store.close()
        reset_workspace_store()
        configure_workspace(workspace=None)


def test_an_unresolvable_workspace_id_is_announced(tmp_ws, monkeypatch, caplog):
    """A task that names its workspace must not be handed another one quietly.

    The fallback (the active workspace) stays -- the block is always
    rendered -- but a missing or unreadable id is logged at WARNING, and a
    store that is busy once is read again before giving up.
    """
    import logging
    import sqlite3

    from kazma_core.ide.env_context import _resolve_root
    import kazma_core.stores.workspaces as wsmod

    with caplog.at_level(logging.WARNING, logger="kazma_core.ide.env_context"):
        _resolve_root("no-such-workspace")
    assert "is not registered" in caplog.text

    store = wsmod.get_workspace_store()
    real = store.list_workspaces
    calls = {"n": 0}

    def busy_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return real()

    monkeypatch.setattr(store, "list_workspaces", busy_once)
    target = tmp_ws / "sub"
    target.mkdir()
    rec = store.create_workspace("Sub", str(target))
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="kazma_core.ide.env_context"):
        assert _resolve_root(rec["id"]) == target.resolve()
    assert calls["n"] == 2 and not caplog.text

    def always_busy():
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store, "list_workspaces", always_busy)
    with caplog.at_level(logging.WARNING, logger="kazma_core.ide.env_context"):
        _resolve_root(rec["id"])
    assert "could not be read (OperationalError: database is locked)" in caplog.text
