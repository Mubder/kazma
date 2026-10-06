"""HA boots must not select an ephemeral image directory as their workspace."""
from __future__ import annotations

from pathlib import Path

import pytest

from kazma_core.stores.workspaces import WorkspaceStore


@pytest.mark.parametrize("ha", [True, False])
def test_boot_and_reopen_with_nonempty_container_cwd(tmp_path, monkeypatch, ha):
    import kazma_core.workspace.binding as binding

    image = tmp_path / "app"
    image.mkdir()
    (image / "README.md").write_text("image contents", encoding="utf-8")
    sandbox = tmp_path / "state" / "workspace"
    sandbox.mkdir(parents=True)
    monkeypatch.chdir(image)
    monkeypatch.setenv("KAZMA_RUNTIME_HA", "1" if ha else "0")
    monkeypatch.setattr(binding, "default_sandbox_root", lambda: sandbox)
    database = str(tmp_path / "state" / "settings.db")
    expected = sandbox if ha else image
    for _ in range(2):
        store = WorkspaceStore(database)
        try:
            assert Path(store.get_active_workspace()["root_path"]) == expected
        finally:
            store.close()


def test_ha_preserves_explicit_existing_workspace(tmp_path, monkeypatch):
    import kazma_core.workspace.binding as binding

    monkeypatch.setattr(binding, "_WORKSPACE_ROOT", None)
    monkeypatch.setenv("KAZMA_RUNTIME_HA", "1")
    database = str(tmp_path / "settings.db")
    store = WorkspaceStore(database)
    custom = tmp_path / "paired" / "custom-project"
    custom.mkdir(parents=True)
    row = store.create_workspace("Operator project", str(custom))
    assert store.set_active_workspace(row["id"])
    store.close()
    reopened = WorkspaceStore(database)
    try:
        assert reopened.get_active_workspace()["id"] == row["id"]
    finally:
        reopened.close()
