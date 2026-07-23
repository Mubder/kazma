"""Workspace delete: registry + safe on-disk wipe under clone dirs."""

from __future__ import annotations

from pathlib import Path

import pytest

from kazma_gateway.routers.workspaces import _is_safe_to_delete_files


def test_safe_delete_under_kazma_repos(tmp_path, monkeypatch):
    base = tmp_path / "kazma-repos"
    shipx = base / "ShipX"
    shipx.mkdir(parents=True)
    (shipx / "README.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        "kazma_gateway.routers.workspaces._clone_base_dirs",
        lambda: [base.resolve()],
    )
    ok, reason = _is_safe_to_delete_files(shipx)
    assert ok is True


def test_refuse_delete_home(monkeypatch):
    home = Path.home()
    ok, reason = _is_safe_to_delete_files(home)
    assert ok is False
    assert "home" in reason.lower()


def test_refuse_delete_kazma_pyproject(tmp_path, monkeypatch):
    fake = tmp_path / "kazma-host"
    fake.mkdir()
    (fake / "pyproject.toml").write_text(
        '[project]\nname = "kazma"\nversion = "0.6.1"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "kazma_gateway.routers.workspaces._clone_base_dirs",
        lambda: [tmp_path.resolve()],
    )
    ok, reason = _is_safe_to_delete_files(fake)
    assert ok is False
    assert "kazma" in reason.lower()


def test_store_delete_workspace(tmp_path, monkeypatch):
    from kazma_core.stores import reset_workspace_store
    import kazma_core.stores.workspaces as wsmod

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    reset_workspace_store()
    store = wsmod.WorkspaceStore(str(tmp_path / "settings.db"))
    wsmod._workspace_store = store

    root = tmp_path / "proj"
    root.mkdir()
    rec = store.create_workspace("proj", str(root))
    store.set_active_workspace(rec["id"])
    assert store.get_active_workspace() is not None

    deleted = store.delete_workspace(rec["id"])
    assert deleted is not None
    assert deleted["id"] == rec["id"]
    assert store.get_workspace(rec["id"]) is None
    assert store.get_active_workspace() is None

    store.close()
    reset_workspace_store()
