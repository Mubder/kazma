"""Tests for VAL-UI-001: Workspace tab is functional.

Validates that:
  - GET /workspace returns 200 with workspace content (not a redirect)
  - GET /api/workspace/files returns a JSON file listing
  - GET /api/workspace/git returns best-effort git status
  - GET /api/workspace/recent returns recently modified files
  - Path traversal is blocked (cannot escape workspace root)
  - The workspace API router is registered in the app
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

_UI_DIR = Path(__file__).resolve().parent.parent / "kazma-ui" / "kazma_ui"


# ══════════════════════════════════════════════════════════════════════════
# Source-level checks
# ══════════════════════════════════════════════════════════════════════════


class TestWorkspaceRouterWired:
    """app.py must register the workspace API router."""

    def test_workspace_router_imported_and_mounted(self) -> None:
        source = (_UI_DIR / "app.py").read_text(encoding="utf-8")
        assert "workspace_api" in source, "workspace_api module not imported in app.py"
        assert "create_workspace_router" in source, (
            "create_workspace_router not called in app.py"
        )

    def test_workspace_api_module_exists(self) -> None:
        assert (_UI_DIR / "workspace_api.py").exists(), (
            "workspace_api.py module does not exist"
        )


# ══════════════════════════════════════════════════════════════════════════
# Unit tests for workspace_api module
# ══════════════════════════════════════════════════════════════════════════


class TestWorkspaceApiUnit:
    """Unit tests for the workspace_api module functions."""

    def test_resolve_workspace_root_creates_dir(self, tmp_path: Path) -> None:
        from kazma_ui.workspace_api import _resolve_workspace_root

        fake_ws = tmp_path / "kazma-data" / "workspace"
        with patch.dict(os.environ, {"KAZMA_WORKSPACE": str(fake_ws)}):
            root = _resolve_workspace_root()
        assert root == fake_ws.resolve()
        assert root.exists(), "workspace root directory was not created"

    def test_is_within_workspace_blocks_traversal(self, tmp_path: Path) -> None:
        from kazma_ui.workspace_api import _is_within_workspace

        ws = tmp_path / "workspace"
        ws.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_text("secret")
        inside = ws / "inside.txt"
        inside.write_text("ok")

        assert _is_within_workspace(inside, ws) is True
        assert _is_within_workspace(outside, ws) is False

    def test_human_size_formatting(self) -> None:
        from kazma_ui.workspace_api import _human_size

        assert _human_size(512) == "512 B"
        assert _human_size(2048) == "2.0 KB"
        assert _human_size(5 * 1024 * 1024) == "5.0 MB"


# ══════════════════════════════════════════════════════════════════════════
# Integration tests via the full app
# ══════════════════════════════════════════════════════════════════════════


class TestWorkspaceRouteServesPage:
    """GET /workspace must serve the workspace page (not redirect)."""

    @pytest.fixture
    def client(self) -> TestClient:
        from kazma_ui.app import create_app

        app = create_app()
        return TestClient(app)

    def test_workspace_returns_200(self, client: TestClient) -> None:
        resp = client.get("/workspace", follow_redirects=False)
        assert resp.status_code == 200

    def test_workspace_not_redirect(self, client: TestClient) -> None:
        resp = client.get("/workspace", follow_redirects=False)
        assert resp.status_code not in (301, 302, 307, 308)

    def test_workspace_has_file_browser(self, client: TestClient) -> None:
        resp = client.get("/workspace")
        assert "workspaceApp" in resp.text


class TestWorkspaceFilesEndpoint:
    """GET /api/workspace/files must return a valid file listing."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        # Point workspace to a temp dir with known contents
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "file1.txt").write_text("hello")
        (ws / "file2.py").write_text("print('hi')")
        sub = ws / "subdir"
        sub.mkdir()
        (sub / "nested.md").write_text("# nested")

        with patch.dict(os.environ, {"KAZMA_WORKSPACE": str(ws)}):
            from kazma_ui.app import create_app

            app = create_app()
            with TestClient(app) as c:
                yield c

    def test_files_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files")
        assert resp.status_code == 200

    def test_files_returns_json_list(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files")
        data = resp.json()
        assert "files" in data
        assert isinstance(data["files"], list)

    def test_files_lists_known_contents(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files")
        data = resp.json()
        names = [f["name"] for f in data["files"]]
        assert "file1.txt" in names
        assert "file2.py" in names
        assert "subdir" in names

    def test_files_entry_has_required_keys(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files")
        data = resp.json()
        for entry in data["files"]:
            assert "name" in entry
            assert "path" in entry
            assert "is_dir" in entry
            assert "size" in entry

    def test_files_dirs_listed_first(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files")
        data = resp.json()
        dirs = [f for f in data["files"] if f["is_dir"]]
        files = [f for f in data["files"] if not f["is_dir"]]
        # Directories should appear before files
        all_names = [f["name"] for f in data["files"]]
        for d in dirs:
            for f in files:
                assert all_names.index(d["name"]) < all_names.index(f["name"]), (
                    f"Directory {d['name']} should appear before file {f['name']}"
                )

    def test_files_subdir_navigation(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files?path=subdir")
        data = resp.json()
        names = [f["name"] for f in data["files"]]
        assert "nested.md" in names

    def test_files_parent_path_for_subdir(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/files?path=subdir")
        data = resp.json()
        assert data["parent"] == ""  # parent of "subdir" is root ("")

    def test_files_traversal_blocked(self, client: TestClient) -> None:
        """Path traversal attempts must not escape the workspace root."""
        resp = client.get("/api/workspace/files?path=../../../etc")
        data = resp.json()
        # Must either return an error or an empty file list
        assert data.get("error") or data["files"] == [], (
            "Path traversal was not blocked"
        )


class TestWorkspaceGitEndpoint:
    """GET /api/git/status must return git status."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        ws = tmp_path / "workspace"
        ws.mkdir()
        with patch.dict(os.environ, {"KAZMA_WORKSPACE": str(ws)}):
            from kazma_ui.app import create_app

            app = create_app()
            with TestClient(app) as c:
                yield c

    def test_git_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/git/status")
        assert resp.status_code == 200

    def test_git_returns_expected_keys(self, client: TestClient) -> None:
        resp = client.get("/api/git/status")
        data = resp.json()
        assert "is_git" in data
        assert "branch" in data
        assert "dirty" in data
        assert "staged" in data
        assert "modified" in data
        assert "untracked" in data
        assert "raw_status" in data


class TestWorkspaceRecentEndpoint:
    """GET /api/workspace/recent must return recently modified files."""

    @pytest.fixture
    def client(self, tmp_path: Path) -> TestClient:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "recent.txt").write_text("recent")
        (ws / "older.txt").write_text("older")
        with patch.dict(os.environ, {"KAZMA_WORKSPACE": str(ws)}):
            from kazma_ui.app import create_app

            app = create_app()
            with TestClient(app) as c:
                yield c

    def test_recent_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/recent")
        assert resp.status_code == 200

    def test_recent_returns_files_list(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/recent")
        data = resp.json()
        assert "files" in data
        assert isinstance(data["files"], list)

    def test_recent_lists_known_files(self, client: TestClient) -> None:
        resp = client.get("/api/workspace/recent")
        data = resp.json()
        names = [f["name"] for f in data["files"]]
        assert "recent.txt" in names
        assert "older.txt" in names
