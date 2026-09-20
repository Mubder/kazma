"""Tests for Kazma Hub CLI commands."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from kazma_core.hub.cli import hub

# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def sample_skill_dir(tmp_path: Path) -> Path:
    """Create a valid skill directory for CLI testing."""
    skill_dir = tmp_path / "drone-inspection"
    skill_dir.mkdir()

    manifest = {
        "name": "drone-inspection",
        "version": "0.1.0",
        "description": "Drone fleet inspection management",
        "author": "almuhalab",
        "license": "MIT",
        "capabilities": ["drone_inspection"],
        "tags": ["drone", "inspection"],
        "entry_point": "main:DroneInspection",
    }
    (skill_dir / "skill_manifest.yaml").write_text(yaml.dump(manifest))

    (skill_dir / "main.py").write_text(
        textwrap.dedent("""\
            class DroneInspection:
                def __init__(self):
                    self.name = "drone-inspection"
        """)
    )
    return skill_dir


@pytest.fixture
def invalid_skill_dir(tmp_path: Path) -> Path:
    """Create a skill directory with invalid manifest."""
    skill_dir = tmp_path / "bad-skill"
    skill_dir.mkdir()
    (skill_dir / "skill_manifest.yaml").write_text("name: bad-skill\n")
    return skill_dir


# ─── Tests: CLI group ─────────────────────────────────────────────────────


class TestCliGroup:
    """Basic CLI group tests."""

    def test_hub_help(self, runner: CliRunner) -> None:
        """Hub group shows help text."""
        result = runner.invoke(hub, ["--help"])
        assert result.exit_code == 0
        assert "Kazma Hub" in result.output or "skill registry" in result.output.lower()

    def test_hub_no_command(self, runner: CliRunner) -> None:
        """Hub group with no command shows help."""
        result = runner.invoke(hub, [])
        # click exits with 2 (usage error) when no subcommand given
        assert result.exit_code == 2


# ─── Tests: validate command ──────────────────────────────────────────────


class TestCliValidate:
    """validate command tests."""

    def test_validate_valid_skill(self, runner: CliRunner, sample_skill_dir: Path) -> None:
        """Validate a valid skill directory."""
        result = runner.invoke(hub, ["validate", str(sample_skill_dir)])
        assert result.exit_code == 0
        assert "PASS" in result.output
        assert "Security Score" in result.output

    def test_validate_invalid_skill(self, runner: CliRunner, invalid_skill_dir: Path) -> None:
        """Validate an invalid skill directory shows errors."""
        result = runner.invoke(hub, ["validate", str(invalid_skill_dir)])
        # Should still exit 0 (validation is informational)
        assert result.exit_code == 0
        assert "FAIL" in result.output or "Security Score" in result.output


# ─── Tests: register command ──────────────────────────────────────────────


class TestCliRegister:
    """register command tests."""

    def test_register_skill(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """Register a skill from a directory."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        assert result.exit_code == 0
        assert "drone-inspection" in result.output.lower() or "registered" in result.output.lower()

    def test_register_nonexistent_dir(self, runner: CliRunner) -> None:
        """Register with a non-existent path fails."""
        result = runner.invoke(hub, ["register", "/nonexistent/path/"])
        assert result.exit_code != 0


# ─── Tests: search command ────────────────────────────────────────────────


class TestCliSearch:
    """search command tests."""

    def test_search_empty(self, runner: CliRunner, tmp_path: Path) -> None:
        """Search with no results."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(hub, ["--registry-path", str(db_path), "search", "nonexistent"])
        assert result.exit_code == 0
        # Should show header but no data rows
        assert "NAME" in result.output or "no skills found" in result.output.lower()

    def test_search_after_register(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """Search returns registered skills."""
        db_path = tmp_path / "test_registry.db"
        # Register first
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        # Search
        result = runner.invoke(hub, ["--registry-path", str(db_path), "search", "drone"])
        assert result.exit_code == 0
        assert "drone-inspection" in result.output


# ─── Tests: list command ──────────────────────────────────────────────────


class TestCliList:
    """list command tests."""

    def test_list_empty(self, runner: CliRunner, tmp_path: Path) -> None:
        """List with no installed skills."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(hub, ["--registry-path", str(db_path), "list"])
        assert result.exit_code == 0

    def test_list_after_install(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """List shows installed skills."""
        db_path = tmp_path / "test_registry.db"
        # Register then install
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "install", "kazma-hub://almuhalab/drone-inspection@0.1.0"],
        )
        result = runner.invoke(hub, ["--registry-path", str(db_path), "list"])
        assert result.exit_code == 0


# ─── Tests: install command ───────────────────────────────────────────────


class TestCliInstall:
    """install command tests."""

    def test_install_skill(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """Install a registered skill."""
        db_path = tmp_path / "test_registry.db"
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "install", "kazma-hub://almuhalab/drone-inspection@0.1.0"],
        )
        assert result.exit_code == 0

    def test_install_unregistered_skill(self, runner: CliRunner, tmp_path: Path) -> None:
        """Install a skill that isn't registered fails gracefully."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "install", "kazma-hub://nobody/fake-skill@1.0.0"],
        )
        # Should handle gracefully (not crash)
        assert result.exit_code == 0 or "not found" in result.output.lower()


# ─── Tests: info command ──────────────────────────────────────────────────


class TestCliInfo:
    """info command tests."""

    def test_info_registered_skill(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """Info shows details of a registered skill."""
        db_path = tmp_path / "test_registry.db"
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "info", "kazma-hub://almuhalab/drone-inspection@0.1.0"],
        )
        assert result.exit_code == 0
        assert "drone-inspection" in result.output

    def test_info_unregistered_skill(self, runner: CliRunner, tmp_path: Path) -> None:
        """Info for unregistered skill shows not found."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "info", "kazma-hub://nobody/fake@1.0.0"],
        )
        assert result.exit_code == 0
        assert "not found" in result.output.lower()


# ─── Tests: uninstall command ─────────────────────────────────────────────


class TestCliUninstall:
    """uninstall command tests."""

    def test_uninstall_skill(self, runner: CliRunner, sample_skill_dir: Path, tmp_path: Path) -> None:
        """Uninstall a registered skill."""
        db_path = tmp_path / "test_registry.db"
        runner.invoke(
            hub,
            ["--registry-path", str(db_path), "register", str(sample_skill_dir)],
        )
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "uninstall", "kazma-hub://almuhalab/drone-inspection@0.1.0"],
        )
        assert result.exit_code == 0
        assert "uninstalled" in result.output.lower() or "removed" in result.output.lower()

    def test_uninstall_nonexistent(self, runner: CliRunner, tmp_path: Path) -> None:
        """Uninstall a skill that doesn't exist."""
        db_path = tmp_path / "test_registry.db"
        result = runner.invoke(
            hub,
            ["--registry-path", str(db_path), "uninstall", "kazma-hub://nobody/fake@1.0.0"],
        )
        assert result.exit_code == 0
        assert "not found" in result.output.lower()
