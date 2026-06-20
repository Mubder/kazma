"""End-to-end tests for the Kazma Hub submission flow.

Tests the full lifecycle: submit -> status -> badge -> install,
using in-memory registries and mock HTTP where needed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from kazma_core.hub.api import app, configure_api
from kazma_core.hub.badges import (
    BADGE_LEVELS,
    Badge,
    CertificationBadgeSystem,
    _CREATE_BADGES,
)
from kazma_core.hub.cli import hub
from kazma_core.hub.manifest_schema import SkillManifest
from kazma_core.hub.registry import KazmaHub


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_manifest(
    name: str = "test-skill",
    author: str = "test-author",
    version: str = "1.0.0",
    description: str = "A test skill for e2e testing",
    license: str = "MIT",
) -> dict:
    """Return a valid manifest dict."""
    return {
        "name": name,
        "version": version,
        "description": description,
        "author": author,
        "license": license,
        "category": "testing",
        "tags": ["test"],
    }


# ---------------------------------------------------------------------------
# Test Hub End-to-End via API (TestClient)
# ---------------------------------------------------------------------------


class TestHubEndToEnd:
    """End-to-end test for hub submission flow via API."""

    @pytest.fixture
    def client(self, tmp_path):
        """FastAPI TestClient with real in-memory registry."""
        db_path = str(tmp_path / "e2e.db")
        registry = KazmaHub(registry_path=db_path)
        certifier = CertificationBadgeSystem(db_path=db_path)
        configure_api(registry, certifier)
        return TestClient(app)

    def test_submit_and_get_status(self, client):
        """Submit a skill and check its status."""
        payload = {
            "manifest": _make_manifest(),
            "source_url": "https://github.com/test/skill",
            "submitter_id": "test-user",
        }
        # Submit
        response = client.post("/api/v1/skills/submit", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "submission_id" in data
        assert data["status"] == "pending"
        submission_id = data["submission_id"]
        skill_id = data["skill_id"]
        assert submission_id  # non-empty
        assert "test-skill" in skill_id

    def test_submit_then_search(self, client):
        """Submit a skill and find it via search."""
        payload = {
            "manifest": _make_manifest(name="e2e-searchable"),
            "source_url": "https://github.com/test/skill",
            "submitter_id": "test-user",
        }
        client.post("/api/v1/skills/submit", json=payload)

        # Search
        response = client.get("/api/v1/skills/search?q=e2e-searchable")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1
        names = [item["name"] for item in data["items"]]
        assert "e2e-searchable" in names

    def test_submit_then_get_detail(self, client):
        """Submit a skill and retrieve its detail."""
        manifest = _make_manifest(name="detail-skill")
        payload = {
            "manifest": manifest,
            "source_url": "https://github.com/test/skill",
            "submitter_id": "test-user",
        }
        submit_resp = client.post("/api/v1/skills/submit", json=payload)
        skill_id = submit_resp.json()["skill_id"]

        # Get detail
        response = client.get(f"/api/v1/skills/{skill_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "detail-skill"
        assert data["author"] == "test-author"
        assert data["version"] == "1.0.0"

    def test_search_is_case_insensitive(self, client):
        """Search should be case-insensitive."""
        payload = {
            "manifest": _make_manifest(name="case-test"),
            "source_url": "https://github.com/test",
            "submitter_id": "test-user",
        }
        client.post("/api/v1/skills/submit", json=payload)

        response = client.get("/api/v1/skills/search?q=Case")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1

    def test_get_nonexistent_skill_returns_404(self, client):
        """Getting a non-existent skill should return 404."""
        response = client.get("/api/v1/skills/kazma-hub://nobody/noway@1.0.0")
        assert response.status_code == 404

    def test_search_empty_registry(self, client):
        """Search with no skills should return empty results."""
        response = client.get("/api/v1/skills/search?q=anything")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []


# ---------------------------------------------------------------------------
# Badge Issuance Flow via API
# ---------------------------------------------------------------------------


class TestBadgeIssuanceFlow:
    """End-to-end badge flow via API."""

    @pytest.fixture
    def client(self, tmp_path):
        db_path = str(tmp_path / "badge_e2e.db")
        registry = KazmaHub(registry_path=db_path)
        certifier = CertificationBadgeSystem(db_path=db_path)
        configure_api(registry, certifier)
        return TestClient(app)

    @pytest.fixture
    def badge_system(self, tmp_path):
        db_path = str(tmp_path / "badge_e2e_bs.db")
        # Ensure skills table exists
        conn = sqlite3.connect(db_path)
        conn.executescript(_CREATE_BADGES)
        conn.executescript(
            """CREATE TABLE IF NOT EXISTS skills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                author TEXT NOT NULL,
                version TEXT NOT NULL,
                description TEXT,
                license TEXT,
                capabilities TEXT,
                tags TEXT,
                manifest_json TEXT,
                checksum TEXT,
                installed_path TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(name, author, version)
            )"""
        )
        conn.close()
        return CertificationBadgeSystem(db_path=db_path)

    @pytest.fixture
    def badge_system_with_skill(self, badge_system):
        """Badge system with a skill pre-registered."""
        conn = sqlite3.connect(badge_system.db_path)
        conn.execute(
            "INSERT INTO skills (name, author, version, description, license) "
            "VALUES (?, ?, ?, ?, ?)",
            ("e2e-badge-skill", "e2e-author", "1.0.0", "E2E badge skill", "MIT"),
        )
        conn.commit()
        conn.close()
        return badge_system

    @pytest.mark.asyncio
    async def test_badge_issuance_flow(self, badge_system_with_skill):
        """Issue badge after certification review, then verify."""
        bs = badge_system_with_skill

        # Issue badge
        badge = bs.issue_badge("e2e-badge-skill", "basic")
        assert badge.skill_id == "e2e-badge-skill"
        assert badge.level == "basic"
        assert badge.revoked is False

        # Verify badge
        verification = bs.verify_badge("e2e-badge-skill")
        assert verification.valid is True
        assert verification.level == "basic"

    @pytest.mark.asyncio
    async def test_badge_replacement_flow(self, badge_system_with_skill):
        """Issue basic badge, then replace with standard."""
        bs = badge_system_with_skill

        # Issue basic
        bs.issue_badge("e2e-badge-skill", "basic")
        v1 = bs.verify_badge("e2e-badge-skill")
        assert v1.level == "basic"

        # Replace with standard
        bs.issue_badge("e2e-badge-skill", "standard")
        v2 = bs.verify_badge("e2e-badge-skill")
        assert v2.valid is True
        assert v2.level == "standard"

    @pytest.mark.asyncio
    async def test_badge_revocation_flow(self, badge_system_with_skill):
        """Issue badge, then revoke it."""
        bs = badge_system_with_skill

        bs.issue_badge("e2e-badge-skill", "basic")
        bs.revoke_badge("e2e-badge-skill", "security vulnerability")

        verification = bs.verify_badge("e2e-badge-skill")
        assert verification.valid is False
        assert "revoked" in verification.reason.lower()

    def test_submit_then_certification_check(self, client):
        """Submit a skill, then check its certification status via API."""
        manifest = _make_manifest(name="cert-check")
        payload = {
            "manifest": manifest,
            "source_url": "https://github.com/test",
            "submitter_id": "test-user",
        }
        submit_resp = client.post("/api/v1/skills/submit", json=payload)
        skill_id = submit_resp.json()["skill_id"]

        # Check certification (should be 404 since no badge issued)
        response = client.get(f"/api/v1/skills/{skill_id}/certification")
        # No badge = 404
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Search and Download Flow
# ---------------------------------------------------------------------------


class TestSearchAndDownload:
    """Test searching for skills and downloading packages."""

    @pytest.fixture
    def client(self, tmp_path):
        db_path = str(tmp_path / "dl_e2e.db")
        registry = KazmaHub(registry_path=db_path)
        certifier = CertificationBadgeSystem(db_path=db_path)
        configure_api(registry, certifier)
        return TestClient(app)

    def test_search_then_download(self, client):
        """Register skill, search, then download."""
        manifest = _make_manifest(name="download-me")
        payload = {
            "manifest": manifest,
            "source_url": "https://github.com/test",
            "submitter_id": "test-user",
        }
        client.post("/api/v1/skills/submit", json=payload)

        # Search
        search_resp = client.get("/api/v1/skills/search?q=download-me")
        assert search_resp.status_code == 200
        items = search_resp.json()["items"]
        assert len(items) >= 1

        skill_id = items[0]["id"]

        # Download
        dl_resp = client.get(f"/api/v1/skills/{skill_id}/download")
        assert dl_resp.status_code == 200
        assert dl_resp.headers["content-type"] == "application/gzip"
        assert len(dl_resp.content) > 0

    def test_download_nonexistent_returns_404(self, client):
        """Download a non-existent skill should return 404."""
        response = client.get("/api/v1/skills/kazma-hub://nobody/noway@1.0.0/download")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# Stats Flow
# ---------------------------------------------------------------------------


class TestStatsFlow:
    """Test hub statistics."""

    @pytest.fixture
    def client(self, tmp_path):
        db_path = str(tmp_path / "stats_e2e.db")
        registry = KazmaHub(registry_path=db_path)
        certifier = CertificationBadgeSystem(db_path=db_path)
        configure_api(registry, certifier)
        return TestClient(app)

    def test_stats_after_submissions(self, client):
        """Stats should reflect submitted skills."""
        for i in range(3):
            payload = {
                "manifest": _make_manifest(name=f"stat-skill-{i}"),
                "source_url": "https://github.com/test",
                "submitter_id": "test-user",
            }
            client.post("/api/v1/skills/submit", json=payload)

        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_skills"] >= 3


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------


class TestHealthCheck:
    """Test the health endpoint."""

    def test_health_returns_ok(self, client):
        """Health endpoint should return ok."""
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert "version" in data

    @pytest.fixture
    def client(self, tmp_path):
        db_path = str(tmp_path / "health_e2e.db")
        registry = KazmaHub(registry_path=db_path)
        certifier = CertificationBadgeSystem(db_path=db_path)
        configure_api(registry, certifier)
        return TestClient(app)


# ---------------------------------------------------------------------------
# CLI Integration
# ---------------------------------------------------------------------------


class TestCliIntegration:
    """Test CLI commands in end-to-end context."""

    def test_submit_command_help(self):
        """submit command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["submit", "--help"])
        assert result.exit_code == 0
        assert "submit" in result.output.lower() or "skill" in result.output.lower()

    def test_status_command_help(self):
        """status command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["status", "--help"])
        assert result.exit_code == 0

    def test_badge_command_help(self):
        """badge command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["badge", "--help"])
        assert result.exit_code == 0

    def test_certified_command_help(self):
        """certified command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["certified", "--help"])
        assert result.exit_code == 0

    def test_stats_command_help(self):
        """stats command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["stats", "--help"])
        assert result.exit_code == 0

    def test_check_certification_command_help(self):
        """check-certification command should show help."""
        runner = CliRunner()
        result = runner.invoke(hub, ["check-certification", "--help"])
        assert result.exit_code == 0

    def test_hub_help_lists_all_commands(self):
        """Hub help should list all commands including new ones."""
        runner = CliRunner()
        result = runner.invoke(hub, ["--help"])
        assert result.exit_code == 0
        output = result.output.lower()
        assert "submit" in output
        assert "status" in output
        assert "badge" in output
        assert "certified" in output
        assert "stats" in output
        assert "validate" in output
        assert "check-certification" in output
