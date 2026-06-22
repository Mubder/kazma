"""Tests for the Kazma Hub SQLite registry."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from kazma_core.hub.manifest_schema import SkillManifest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_manifest(
    name: str = "test-skill",
    author: str = "test-author",
    version: str = "0.1.0",
    description: str = "A test skill",
    license: str = "MIT",
    capabilities: list | None = None,
    tags: list | None = None,
    dependencies: list | None = None,
) -> dict:
    """Return a plain dict suitable for SkillManifest.from_dict."""
    data: dict = {
        "name": name,
        "author": author,
        "version": version,
        "description": description,
        "license": license,
    }
    if capabilities is not None:
        data["capabilities"] = capabilities
    if tags is not None:
        data["tags"] = tags
    if dependencies is not None:
        data["dependencies"] = dependencies
    return data


def _skill_id(author: str, name: str, version: str) -> str:
    return f"kazma-hub://{author}/{name}@{version}"


# ---------------------------------------------------------------------------
# Tests — Skill ID parsing
# ---------------------------------------------------------------------------


class TestSkillIdParsing:
    def test_valid_id(self):
        pattern = re.compile(r"^kazma-hub://([^/]+)/([^@]+)@(.+)$")
        m = pattern.match("kazma-hub://alice/my-skill@1.2.3")
        assert m is not None
        assert m.group(1) == "alice"
        assert m.group(2) == "my-skill"
        assert m.group(3) == "1.2.3"

    def test_invalid_id_missing_version(self):
        pattern = re.compile(r"^kazma-hub://([^/]+)/([^@]+)@(.+)$")
        assert pattern.match("kazma-hub://alice/my-skill") is None

    def test_invalid_id_missing_author(self):
        pattern = re.compile(r"^kazma-hub://([^/]+)/([^@]+)@(.+)$")
        assert pattern.match("kazma-hub:///my-skill@1.0.0") is None


# ---------------------------------------------------------------------------
# Tests — DB initialisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDbInit:
    async def test_tables_created(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        db_path = tmp_path / "test.db"
        hub = KazmaHub(registry_path=str(db_path))
        try:
            # Trigger async init (tables created on first DB access)
            await hub.search()
            assert db_path.exists(), "DB file should be created"
            # Check both tables exist via sqlite3 directly
            import sqlite3

            conn = sqlite3.connect(str(db_path))
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('skills', 'skill_dependencies')"
            )
            tables = {row[0] for row in cur.fetchall()}
            conn.close()
            assert "skills" in tables
            assert "skill_dependencies" in tables
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Register / Get
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRegisterGet:
    async def test_register_returns_id(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            manifest = SkillManifest.from_dict(_make_manifest())
            sid = await hub.register(manifest)
            assert sid == "kazma-hub://test-author/test-skill@0.1.0"
        finally:
            await hub.close()

    async def test_get_after_register(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            manifest = SkillManifest.from_dict(_make_manifest())
            sid = await hub.register(manifest)
            fetched = await hub.get(sid)
            assert fetched is not None
            assert fetched.data["name"] == "test-skill"
            assert fetched.data["author"] == "test-author"
            assert fetched.data["version"] == "0.1.0"
        finally:
            await hub.close()

    async def test_get_nonexistent_returns_none(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            result = await hub.get("kazma-hub://nobody/nowhere@0.0.1")
            assert result is None
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Search
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSearch:
    async def _setup_hub(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        skills = [
            _make_manifest(
                name="web-scraper", author="alice", version="1.0.0", capabilities=["http"], tags=["web", "scraping"]
            ),
            _make_manifest(name="db-writer", author="bob", version="1.0.0", capabilities=["database"], tags=["db"]),
            _make_manifest(
                name="web-monitor",
                author="alice",
                version="2.0.0",
                capabilities=["http", "alerts"],
                tags=["web", "monitoring"],
            ),
        ]
        for s in skills:
            await hub.register(SkillManifest.from_dict(s))
        return hub

    async def test_search_all(self, tmp_path):
        hub = await self._setup_hub(tmp_path)
        try:
            results = await hub.search()
            assert len(results) == 3
        finally:
            await hub.close()

    async def test_search_by_name(self, tmp_path):
        hub = await self._setup_hub(tmp_path)
        try:
            results = await hub.search(query="web")
            assert len(results) == 2
            names = {m.data["name"] for m in results}
            assert "web-scraper" in names
            assert "web-monitor" in names
        finally:
            await hub.close()

    async def test_search_by_author(self, tmp_path):
        hub = await self._setup_hub(tmp_path)
        try:
            results = await hub.search(author="bob")
            assert len(results) == 1
            assert results[0].data["name"] == "db-writer"
        finally:
            await hub.close()

    async def test_search_by_capability(self, tmp_path):
        hub = await self._setup_hub(tmp_path)
        try:
            results = await hub.search(capabilities=["database"])
            assert len(results) == 1
            assert results[0].data["name"] == "db-writer"
        finally:
            await hub.close()

    async def test_search_by_tag(self, tmp_path):
        hub = await self._setup_hub(tmp_path)
        try:
            results = await hub.search(tags=["monitoring"])
            assert len(results) == 1
            assert results[0].data["name"] == "web-monitor"
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Unregister
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestUnregister:
    async def test_unregister_removes(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            manifest = SkillManifest.from_dict(_make_manifest())
            sid = await hub.register(manifest)
            removed = await hub.unregister(sid)
            assert removed is True
            assert await hub.get(sid) is None
        finally:
            await hub.close()

    async def test_unregister_nonexistent(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            removed = await hub.unregister("kazma-hub://x/y@0.0.0")
            assert removed is False
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Duplicate registration (upsert)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestDuplicateRegistration:
    async def test_duplicate_updates(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            m1 = SkillManifest.from_dict(_make_manifest(version="1.0.0"))
            m2 = SkillManifest.from_dict(_make_manifest(version="1.0.0", description="Updated"))
            sid1 = await hub.register(m1)
            sid2 = await hub.register(m2)
            assert sid1 == sid2
            fetched = await hub.get(sid1)
            assert fetched.data["description"] == "Updated"
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — List installed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestListInstalled:
    async def test_list_installed(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            m1 = SkillManifest.from_dict(_make_manifest(name="installed-one"))
            m2 = SkillManifest.from_dict(_make_manifest(name="not-installed"))
            sid1 = await hub.register(m1)
            await hub.register(m2)
            await hub.install(sid1)
            installed = await hub.list_installed()
            assert len(installed) == 1
            assert installed[0].data["name"] == "installed-one"
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Install
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestInstall:
    async def test_install_returns_path(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            manifest = SkillManifest.from_dict(_make_manifest())
            sid = await hub.register(manifest)
            install_path = await hub.install(sid)
            assert isinstance(install_path, Path)
            # Default install path should contain the skill name
            assert "test-skill" in str(install_path)
        finally:
            await hub.close()


# ---------------------------------------------------------------------------
# Tests — Full round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestRoundTrip:
    async def test_create_register_search_get(self, tmp_path):
        from kazma_core.hub.registry import KazmaHub

        hub = KazmaHub(registry_path=str(tmp_path / "db.sqlite"))
        try:
            data = _make_manifest(
                name="round-trip-skill",
                author="tester",
                version="3.2.1",
                capabilities=["compute", "storage"],
                tags=["utility"],
            )
            manifest = SkillManifest.from_dict(data)

            # Register
            sid = await hub.register(manifest)
            assert sid == "kazma-hub://tester/round-trip-skill@3.2.1"

            # Search by tag
            results = await hub.search(tags=["utility"])
            assert len(results) == 1

            # Search by capability
            results = await hub.search(capabilities=["compute"])
            assert len(results) == 1

            # Get by ID
            fetched = await hub.get(sid)
            assert fetched is not None
            assert fetched.data["version"] == "3.2.1"
            assert fetched.data["capabilities"] == ["compute", "storage"]
        finally:
            await hub.close()
