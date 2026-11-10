"""Tests for Multi-Workspace Engine & Dynamic Context Switching.

Validates:
  - Database initialization and Default Workspace creation on boot.
  - WorkspaceStore CRUD operations (create, list, get_active, set_active).
  - FastAPI workspaces router endpoints (GET /api/workspaces, POST /api/workspaces/create, POST /api/workspaces/switch).
  - ConfigStore hot-reloading from the active root.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from kazma_core.stores.workspaces import WorkspaceStore, reset_workspace_store, get_workspace_store
from kazma_core.config_store import get_config_store, reset_config_store, ConfigStore


@pytest.fixture(autouse=True)
def cleanup_stores():
    """Reset singleton stores before and after each test."""
    reset_workspace_store()
    reset_config_store()
    yield
    reset_workspace_store()
    reset_config_store()


def test_workspace_store_boot_default_creation(tmp_path: Path, monkeypatch) -> None:
    """If database is empty and no KAZMA_WORKSPACE is set, boot initializes the CWD."""
    monkeypatch.delenv("KAZMA_WORKSPACE", raising=False)  # the root conftest pins one
    db_file = tmp_path / "settings.db"
    
    # Instantiate WorkspaceStore with the temp DB file
    store = WorkspaceStore(db_path=str(db_file))
    
    workspaces = store.list_workspaces()
    assert len(workspaces) == 1
    default_ws = workspaces[0]
    
    # Boot default: when CWD is a real project (has .git/files) the store
    # names the workspace after the CWD directory, rooted at CWD — not the
    # legacy "Default Workspace" sandbox name.
    assert default_ws["name"] == Path.cwd().name
    assert default_ws["root_path"] == str(Path.cwd().resolve())
    assert default_ws["is_active"] is True
    
    # Active workspace should be retrieved correctly
    active = store.get_active_workspace()
    assert active is not None
    assert active["id"] == default_ws["id"]


def test_workspace_store_crud(tmp_path: Path) -> None:
    """Validate creating, listing, and switching workspaces via store."""
    db_file = tmp_path / "settings.db"
    store = WorkspaceStore(db_path=str(db_file))
    
    # 1. Create a new workspace
    new_path = tmp_path / "my_project"
    new_path.mkdir()
    
    record = store.create_workspace(name="My Project", path=str(new_path))
    assert record["name"] == "My Project"
    assert record["root_path"] == str(new_path.resolve())
    assert record["is_active"] is False
    
    # 2. List workspaces
    workspaces = store.list_workspaces()
    assert len(workspaces) == 2  # Default Workspace + My Project
    
    # 3. Switch active workspace
    success = store.set_active_workspace(record["id"])
    assert success is True
    
    active = store.get_active_workspace()
    assert active is not None
    assert active["id"] == record["id"]
    assert active["is_active"] is True


def test_config_store_hot_reloading(tmp_path: Path) -> None:
    """A workspace's own kazma.yaml is content, never configuration.

    The store's fallback followed the active workspace until 2026-11-10
    (security audit SEC-02); the full gate is
    tests/test_workspace_yaml_is_not_config.py.
    """
    db_file = tmp_path / "settings_config.db"
    install_yaml = tmp_path / "kazma.yaml"
    install_yaml.write_text("agent:\n  name: Kazma\n", encoding="utf-8")
    config_store = ConfigStore(db_path=str(db_file), yaml_path=str(install_yaml))

    ws_path = tmp_path / "project_alpha"
    ws_path.mkdir()
    (ws_path / "kazma.yaml").write_text("workspace:\n  id: 'alpha-123'\n", encoding="utf-8")

    assert not hasattr(config_store, "reload_from_root")
    assert config_store.get("workspace.id") is None
    assert config_store.get("agent.name") == "Kazma"


def test_workspaces_api_endpoints(tmp_path: Path) -> None:
    """Test the workspaces management API router."""
    db_file = tmp_path / "settings.db"
    
    # Force process singletons to use the test DB
    with patch("kazma_core.stores.workspaces._DEFAULT_DB", str(db_file)), \
         patch("kazma_core.config_store._DEFAULT_DB", str(db_file)):
         
        from kazma_ui.app import create_app
        app = create_app()
        client = TestClient(app)
        
        # 1. GET /api/workspaces
        resp = client.get("/api/workspaces")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert len(data["workspaces"]) == 1
        # Seeded from KAZMA_WORKSPACE (the root conftest's temp folder), never
        # the checkout the tests run in.
        assert data["workspaces"][0]["name"] == Path(os.environ["KAZMA_WORKSPACE"]).name

        default_id = data["active_workspace_id"]
        assert default_id is not None
        
        # 2. POST /api/workspaces/create
        proj_path = tmp_path / "project_beta"
        resp = client.post("/api/workspaces/create", json={
            "name": "Project Beta",
            "path": str(proj_path)
        })
        assert resp.status_code == 201
        create_data = resp.json()
        assert create_data["status"] == "ok"
        assert create_data["workspace"]["name"] == "Project Beta"
        assert proj_path.exists()
        
        beta_id = create_data["workspace"]["id"]
        
        # 3. POST /api/workspaces/switch
        resp = client.post("/api/workspaces/switch", json={
            "workspace_id": beta_id
        })
        assert resp.status_code == 200
        switch_data = resp.json()
        assert switch_data["status"] == "ok"
        assert switch_data["active_workspace"]["id"] == beta_id
        assert switch_data["active_workspace"]["is_active"] is True
        
        # Verify active is changed on GET
        resp = client.get("/api/workspaces")
        data = resp.json()
        assert data["active_workspace_id"] == beta_id
