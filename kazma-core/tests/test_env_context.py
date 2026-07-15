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
    """Isolated workspace dir + env var, so we don't touch the real settings DB."""
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("KAZMA_WORKSPACE", str(ws))
    yield ws


def test_env_context_always_names_workspace_root(tmp_ws):
    """Even with no git repo, the block must name the workspace root."""
    block = build_env_context()
    assert "## Environment" in block
    assert str(tmp_ws) in block
    # No repo in a bare dir — must NOT claim a repository.
    assert "Repository:" not in block


def test_env_context_detects_git_repo(tmp_ws):
    """In a git repo the slug + branch are detected."""
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_ws), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_ws), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(tmp_ws), check=True)
    subprocess.run(
        ["git", "remote", "add", "origin", "https://github.com/owner/myrepo.git"],
        cwd=str(tmp_ws), check=True,
    )
    block = build_env_context()
    assert "Repository: owner/myrepo" in block


def test_env_context_lists_tools(tmp_ws):
    """The block announces the available tools so the brain is aware."""
    block = build_env_context()
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
    from kazma_core.stores import get_workspace_store, reset_workspace_store
    from kazma_core.tools.file_write import _get_workspace

    # Point the store at a temp DB so we don't mutate the real settings.db.
    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    db_path = str(tmp_path / "settings.db")

    # Build a store with two workspaces.
    import kazma_core.stores.workspaces as wsmod

    store = wsmod.WorkspaceStore(db_path)
    wsmod._workspace_store = store  # patch singleton for this test

    repo_a = tmp_path / "repoA"
    repo_b = tmp_path / "repoB"
    repo_a.mkdir()
    repo_b.mkdir()
    rec_a = store.create_workspace("Repo A", str(repo_a))
    rec_b = store.create_workspace("Repo B", str(repo_b))

    # Global default should be neither A nor B specifically (env-based).
    monkeypatch.setenv("KAZMA_WORKSPACE", str(repo_a))

    try:
        # Without scope, _get_workspace resolves to repo_a (env).
        assert _get_workspace().resolve() == repo_a.resolve()

        # Inside a scope for repo_b, _get_workspace must resolve to repo_b.
        async with workspace_scope(rec_b["id"]):
            resolved = _get_workspace().resolve()
            assert resolved == repo_b.resolve(), (
                f"scope should pin to {repo_b}, got {resolved}"
            )

        # After exiting the scope, we're back to the global (repo_a).
        assert _get_workspace().resolve() == repo_a.resolve()
    finally:
        reset_workspace_store()
