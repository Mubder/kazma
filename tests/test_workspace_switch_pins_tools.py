"""Switch Repo must re-pin tools to the new workspace (no restart required)."""

from __future__ import annotations

from pathlib import Path

import pytest


def test_set_active_workspace_repins_file_write(tmp_path, monkeypatch):
    import importlib

    from kazma_core.stores import reset_workspace_store
    import kazma_core.stores.workspaces as wsmod

    fw = importlib.import_module("kazma_core.tools.file_write")

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    db = str(tmp_path / "settings.db")
    reset_workspace_store()
    store = wsmod.WorkspaceStore(db)
    wsmod._workspace_store = store

    kazma = tmp_path / "kazma"
    shipx = tmp_path / "ShipX"
    kazma.mkdir()
    shipx.mkdir()
    (kazma / "AGENTS.md").write_text("kazma", encoding="utf-8")
    (shipx / "README.md").write_text("shipx product", encoding="utf-8")

    rec_k = store.create_workspace("kazma", str(kazma))
    rec_s = store.create_workspace("ShipX", str(shipx))

    # Boot-like pin to kazma monorepo
    fw.configure_workspace(workspace=str(kazma))
    store.set_active_workspace(rec_k["id"])
    assert fw._get_workspace().resolve() == kazma.resolve()

    # Switch Repo → ShipX
    assert store.set_active_workspace(rec_s["id"]) is True
    assert fw._get_workspace().resolve() == shipx.resolve()
    # configure pin must also match
    assert fw._WORKSPACE_ROOT is not None
    assert fw._WORKSPACE_ROOT.resolve() == shipx.resolve()

    # env_context must advertise ShipX, not kazma
    from kazma_core.ide.env_context import build_env_context

    block = build_env_context()
    assert str(shipx) in block
    assert "ShipX" in block
    assert "BINDING" in block or "Hard rules" in block

    store.close()
    reset_workspace_store()
    fw.configure_workspace(workspace=None)
