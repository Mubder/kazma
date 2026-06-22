"""Tests for Kazma Hub skill loader — dynamic import of installed skills."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from kazma_core.hub.loader import SkillError, SkillLoader, SkillLoadError, SkillNotFoundError

# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    """Create a temp skills directory with one valid skill."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()

    manifest = {
        "name": "test-skill",
        "version": "0.1.0",
        "description": "A test skill for loader tests",
        "author": "testuser",
        "license": "MIT",
        "entry_point": "main:TestSkill",
    }
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))

    (skill_dir / "main.py").write_text(
        textwrap.dedent("""\
            class TestSkill:
                def __init__(self):
                    self.name = "test-skill"

                def hello(self):
                    return "hello from test-skill"
        """)
    )
    return tmp_path


@pytest.fixture
def multi_skills_dir(tmp_path: Path) -> Path:
    """Create a temp skills directory with two valid skills."""
    for name in ("alpha-skill", "beta-skill"):
        skill_dir = tmp_path / name
        skill_dir.mkdir()

        manifest = {
            "name": name,
            "version": "1.0.0",
            "description": f"{name} description",
            "author": "testuser",
            "license": "MIT",
            "entry_point": "main:Handler",
        }
        (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))

        (skill_dir / "main.py").write_text(
            textwrap.dedent(f"""\
                class Handler:
                    def __init__(self):
                        self.name = "{name}"
            """)
        )
    return tmp_path


@pytest.fixture
def skill_no_entrypoint(tmp_path: Path) -> Path:
    """Create a skill with no entry_point in manifest."""
    skill_dir = tmp_path / "noep-skill"
    skill_dir.mkdir()

    manifest = {
        "name": "noep-skill",
        "version": "0.1.0",
        "description": "Skill without entry point",
        "author": "testuser",
        "license": "MIT",
    }
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))
    (skill_dir / "main.py").write_text("VALUE = 42\n")
    return tmp_path


@pytest.fixture
def skill_invalid_manifest(tmp_path: Path) -> Path:
    """Create a skill with invalid manifest (missing required fields)."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "skill_manifest.yaml").write_text("name: bad-skill\n")
    return tmp_path


@pytest.fixture
def skill_bad_entrypoint(tmp_path: Path) -> Path:
    """Create a skill with entry_point pointing to a non-existent module."""
    skill_dir = tmp_path / "badep-skill"
    skill_dir.mkdir()

    manifest = {
        "name": "badep-skill",
        "version": "0.1.0",
        "description": "Skill with bad entry point",
        "author": "testuser",
        "license": "MIT",
        "entry_point": "nonexistent_module:SomeClass",
    }
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))
    return tmp_path


# ─── Tests: SkillLoader ───────────────────────────────────────────────────


class TestSkillLoaderLoadSkill:
    """load_skill — loading individual skills."""

    @pytest.mark.asyncio
    async def test_load_skill_returns_instance(self, skills_dir: Path) -> None:
        """load_skill loads a valid installed skill and returns an instance."""
        loader = SkillLoader(skills_dir=str(skills_dir))
        instance = await loader.load_skill("test-skill")

        assert instance is not None
        assert hasattr(instance, "hello")
        assert instance.hello() == "hello from test-skill"

    @pytest.mark.asyncio
    async def test_load_skill_not_found(self, tmp_path: Path) -> None:
        """load_skill raises SkillNotFoundError for missing skill."""
        loader = SkillLoader(skills_dir=str(tmp_path))

        with pytest.raises(SkillNotFoundError, match="nonexistent"):
            await loader.load_skill("nonexistent")

    @pytest.mark.asyncio
    async def test_load_skill_invalid_entrypoint(self, skill_bad_entrypoint: Path) -> None:
        """load_skill raises SkillLoadError for invalid entry_point module."""
        loader = SkillLoader(skills_dir=str(skill_bad_entrypoint))

        with pytest.raises(SkillLoadError, match="badep-skill"):
            await loader.load_skill("badep-skill")

    @pytest.mark.asyncio
    async def test_load_skill_invalid_manifest(self, skill_invalid_manifest: Path) -> None:
        """load_skill raises SkillLoadError for invalid manifest."""
        loader = SkillLoader(skills_dir=str(skill_invalid_manifest))

        with pytest.raises(SkillLoadError, match="bad-skill"):
            await loader.load_skill("bad-skill")

    @pytest.mark.asyncio
    async def test_load_skill_no_entrypoint(self, skill_no_entrypoint: Path) -> None:
        """load_skill returns the module when no entry_point is specified."""
        loader = SkillLoader(skills_dir=str(skill_no_entrypoint))
        module = await loader.load_skill("noep-skill")

        assert module is not None
        # Module-level attribute should be accessible
        assert getattr(module, "VALUE", None) == 42


class TestSkillLoaderLoadAll:
    """load_all — discovering and loading all skills."""

    @pytest.mark.asyncio
    async def test_load_all_discovers_all(self, multi_skills_dir: Path) -> None:
        """load_all discovers all valid skills in directory."""
        loader = SkillLoader(skills_dir=str(multi_skills_dir))
        skills = await loader.load_all()

        assert len(skills) == 2
        assert "alpha-skill" in skills
        assert "beta-skill" in skills
        assert skills["alpha-skill"].name == "alpha-skill"
        assert skills["beta-skill"].name == "beta-skill"

    @pytest.mark.asyncio
    async def test_load_all_empty_dir(self, tmp_path: Path) -> None:
        """load_all returns empty dict for empty directory."""
        empty = tmp_path / "empty"
        empty.mkdir()
        loader = SkillLoader(skills_dir=str(empty))
        skills = await loader.load_all()

        assert skills == {}

    @pytest.mark.asyncio
    async def test_load_all_mixed_valid_invalid(self, skill_invalid_manifest: Path) -> None:
        """load_all skips skills with invalid manifests."""
        # Add a valid skill alongside the invalid one
        valid_dir = skill_invalid_manifest / "valid-skill"
        valid_dir.mkdir()
        manifest = {
            "name": "valid-skill",
            "version": "0.1.0",
            "description": "Valid skill",
            "author": "testuser",
            "license": "MIT",
            "entry_point": "main:Runner",
        }
        (valid_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))
        (valid_dir / "main.py").write_text("class Runner: pass\n")

        loader = SkillLoader(skills_dir=str(skill_invalid_manifest))
        skills = await loader.load_all()

        assert "valid-skill" in skills
        assert "bad-skill" not in skills


class TestSkillLoaderReload:
    """reload — hot-reloading skills."""

    @pytest.mark.asyncio
    async def test_reload_picks_up_changes(self, skills_dir: Path) -> None:
        """reload removes cached module and re-imports fresh code."""
        loader = SkillLoader(skills_dir=str(skills_dir))

        # First load
        instance1 = await loader.load_skill("test-skill")
        assert instance1.hello() == "hello from test-skill"

        # Modify the skill code
        skill_file = skills_dir / "test-skill" / "main.py"
        skill_file.write_text(
            textwrap.dedent("""\
                class TestSkill:
                    def __init__(self):
                        self.name = "test-skill"

                    def hello(self):
                        return "modified version"
            """)
        )

        # Reload
        instance2 = await loader.reload("test-skill")
        assert instance2.hello() == "modified version"

    @pytest.mark.asyncio
    async def test_reload_not_found(self, tmp_path: Path) -> None:
        """reload raises SkillNotFoundError for missing skill."""
        loader = SkillLoader(skills_dir=str(tmp_path))

        with pytest.raises(SkillNotFoundError, match="ghost"):
            await loader.reload("ghost")


class TestSkillLoaderListAvailable:
    """list_available — scanning for skill directories with valid manifests."""

    @pytest.mark.asyncio
    async def test_list_available(self, multi_skills_dir: Path) -> None:
        """list_available returns correct skill names."""
        loader = SkillLoader(skills_dir=str(multi_skills_dir))
        names = await loader.list_available()

        assert sorted(names) == ["alpha-skill", "beta-skill"]

    @pytest.mark.asyncio
    async def test_list_available_empty(self, tmp_path: Path) -> None:
        """list_available returns empty list when no skills exist."""
        empty = tmp_path / "empty"
        empty.mkdir()
        loader = SkillLoader(skills_dir=str(empty))
        names = await loader.list_available()

        assert names == []

    @pytest.mark.asyncio
    async def test_list_available_skips_invalid(self, skill_invalid_manifest: Path) -> None:
        """list_available skips directories with invalid manifests."""
        loader = SkillLoader(skills_dir=str(skill_invalid_manifest))
        names = await loader.list_available()

        assert "bad-skill" not in names


# ─── Tests: Custom Exceptions ─────────────────────────────────────────────


class TestExceptions:
    """Exception hierarchy for skill errors."""

    def test_skill_error_is_base(self) -> None:
        """SkillError is the base exception."""
        assert issubclass(SkillNotFoundError, SkillError)
        assert issubclass(SkillLoadError, SkillError)
        assert issubclass(SkillError, Exception)

    def test_can_catch_by_base(self) -> None:
        """Catching SkillError catches both subtypes."""
        with pytest.raises(SkillError):
            raise SkillNotFoundError("missing")
        with pytest.raises(SkillError):
            raise SkillLoadError("broken")
