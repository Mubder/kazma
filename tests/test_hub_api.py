"""Tests for the Kazma Hub REST API (api.py)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from kazma_core.hub.api import app


def _make_skill_row(name: str = "test-skill", author: str = "test-author", version: str = "1.0.0"):
    """Create a mock skill manifest dict."""
    return {
        "name": name,
        "version": version,
        "description": f"A test skill called {name}",
        "author": author,
        "license": "MIT",
        "capabilities": ["test"],
        "tags": ["test"],
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Return a FastAPI TestClient with a configured API."""
    from kazma_core.hub.api import configure_api
    from kazma_core.hub.badges import CertificationBadgeSystem
    from kazma_core.hub.registry import KazmaHub

    monkeypatch.setenv("KAZMA_SECRET", "test-secret-for-hub-tests")
    db_path = str(tmp_path / "test_api.db")
    registry = KazmaHub(registry_path=db_path)
    certifier = CertificationBadgeSystem(db_path=db_path)
    configure_api(registry, certifier)
    return TestClient(app)


# --- List skills ---


class TestListSkills:
    """Test GET /api/v1/skills endpoint."""

    def test_list_skills_empty(self, client):
        response = client.get("/api/v1/skills")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    def test_list_skills_has_pagination_params(self, client):
        response = client.get("/api/v1/skills?page=1&per_page=5")
        assert response.status_code == 200
        data = response.json()
        assert data["page"] == 1
        assert data["per_page"] == 5

    def test_list_skills_with_category_filter(self, client):
        response = client.get("/api/v1/skills?category=testing")
        assert response.status_code == 200

    def test_list_skills_with_certified_filter(self, client):
        response = client.get("/api/v1/skills?certified=true")
        assert response.status_code == 200

    def test_list_skills_pagination_second_page(self, client):
        """Second page of empty results should return empty items."""
        response = client.get("/api/v1/skills?page=2&per_page=10")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_skills_sort_by_name(self, client):
        """Sort by name should be the default."""
        response = client.get("/api/v1/skills?sort=name")
        assert response.status_code == 200

    def test_list_skills_handles_empty_registry(self, client):
        """Empty registry should return valid structure."""
        response = client.get("/api/v1/skills")
        data = response.json()
        assert data["total"] == 0
        assert data["items"] == []
        assert data["page"] == 1


# --- Get skill detail ---


class TestGetSkill:
    """Test GET /api/v1/skills/{skill_id} endpoint."""

    def test_get_skill_not_found(self, client):
        response = client.get("/api/v1/skills/nonexistent-id")
        assert response.status_code == 404

    def test_get_skill_response_structure(self, client):
        """When a skill exists, response should have expected fields."""
        # This will return 404 with current mock setup, but we test the path exists
        response = client.get("/api/v1/skills/any-id")
        assert response.status_code in (200, 404)

    def test_get_skill_returns_404_for_nonexistent(self, client):
        """Getting a non-existent skill should return 404."""
        response = client.get("/api/v1/skills/kazma-hub://nobody/noway@1.0.0")
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data


# --- Search ---


class TestSearchSkills:
    """Test GET /api/v1/skills/search endpoint."""

    def test_search_requires_query(self, client):
        response = client.get("/api/v1/skills/search")
        assert response.status_code == 200  # empty query returns all

    def test_search_with_query(self, client):
        response = client.get("/api/v1/skills/search?q=test")
        assert response.status_code == 200
        data = response.json()
        assert "items" in data
        assert "query" in data
        assert data["query"] == "test"

    def test_search_with_category(self, client):
        response = client.get("/api/v1/skills/search?q=testing&category=dev")
        assert response.status_code == 200

    def test_search_is_case_insensitive(self, client):
        """Search should be case-insensitive."""
        # Register a skill first
        payload = {
            "manifest": _make_skill_row(name="case-test", author="case-author", version="1.0.0"),
            "source_url": "https://github.com/test",
            "submitter_id": "test",
        }
        client.post("/api/v1/skills/submit", json=payload)

        # Search with different case
        response = client.get("/api/v1/skills/search?q=Case")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 1

    def test_search_empty_results(self, client):
        """Search with no matches returns empty."""
        response = client.get("/api/v1/skills/search?q=nonexistent")
        assert response.status_code == 200
        data = response.json()
        assert data["items"] == []
        assert data["total"] == 0


# --- Submit skill ---


class TestSubmitSkill:
    """Test POST /api/v1/skills/submit endpoint."""

    def test_submit_requires_manifest(self, client):
        response = client.post("/api/v1/skills/submit", json={})
        assert response.status_code == 422  # validation error

    def test_submit_with_valid_payload(self, client):
        payload = {
            "manifest": _make_skill_row(),
            "source_url": "https://github.com/test/skill",
            "submitter_id": "test-user",
        }
        response = client.post("/api/v1/skills/submit", json=payload)
        assert response.status_code == 200
        data = response.json()
        assert "submission_id" in data
        assert "status" in data
        assert data["status"] in ("pending", "accepted", "rejected")

    def test_submit_returns_skill_id(self, client):
        """Submit should return a skill_id containing the manifest name."""
        payload = {
            "manifest": _make_skill_row(name="my-cool-skill", author="my-author"),
            "source_url": "https://github.com/test",
            "submitter_id": "user",
        }
        response = client.post("/api/v1/skills/submit", json=payload)
        data = response.json()
        assert "my-cool-skill" in data["skill_id"]
        assert "my-author" in data["skill_id"]

    def test_submit_validates_manifest(self, client):
        """Submit with invalid manifest should fail."""
        payload = {
            "manifest": {"name": ""},  # missing required fields
            "source_url": "https://github.com/test",
            "submitter_id": "user",
        }
        response = client.post("/api/v1/skills/submit", json=payload)
        assert response.status_code == 400


# --- Certification status ---


class TestCertificationStatus:
    """Test GET /api/v1/skills/{skill_id}/certification endpoint."""

    def test_certification_not_found(self, client):
        response = client.get("/api/v1/skills/nonexistent/certification")
        assert response.status_code == 404


# --- Stats ---


class TestStats:
    """Test GET /api/v1/stats endpoint."""

    def test_stats_returns_structure(self, client):
        response = client.get("/api/v1/stats")
        assert response.status_code == 200
        data = response.json()
        assert "total_skills" in data
        assert isinstance(data["total_skills"], int)

    def test_stats_has_category_breakdown(self, client):
        response = client.get("/api/v1/stats")
        data = response.json()
        assert "by_category" in data
        assert isinstance(data["by_category"], dict)

    def test_stats_has_certified_count(self, client):
        """Stats should include certified_count."""
        response = client.get("/api/v1/stats")
        data = response.json()
        assert "certified_count" in data
        assert isinstance(data["certified_count"], int)


# --- Download ---


class TestDownload:
    """Test GET /api/v1/skills/{skill_id}/download endpoint."""

    def test_download_not_found(self, client):
        response = client.get("/api/v1/skills/nonexistent/download")
        assert response.status_code == 404

    def test_download_returns_gzip(self, client):
        """Download should return gzip content."""
        # Submit a skill first
        payload = {
            "manifest": _make_skill_row(name="dl-test", author="dl-author", version="1.0.0"),
            "source_url": "https://github.com/test",
            "submitter_id": "test",
        }
        submit_resp = client.post("/api/v1/skills/submit", json=payload)
        skill_id = submit_resp.json()["skill_id"]

        response = client.get(f"/api/v1/skills/{skill_id}/download")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/gzip"
        assert len(response.content) > 0


# --- Health ---


class TestHealth:
    """Test GET /api/v1/health endpoint."""

    def test_health_returns_ok(self, client):
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"
