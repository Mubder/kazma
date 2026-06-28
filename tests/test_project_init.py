"""Tests for project-level .kazma/ directory manager."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml
from kazma_cli.project import (
    init_project,
    load_project,
    project_exists,
    show_project,
    validate_project,
)


class TestProjectInit:
    """Tests for init_project()."""

    def test_init_creates_directory(self) -> None:
        """init_project creates .kazma/ directory."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            assert (Path(tmp) / ".kazma").is_dir()

    def test_init_creates_all_files(self) -> None:
        """init_project creates all template files."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            kazma = Path(tmp) / ".kazma"
            assert (kazma / "rules.yaml").is_file()
            assert (kazma / "context.md").is_file()
            assert (kazma / "personality.yaml").is_file()
            assert (kazma / "tools.yaml").is_file()
            assert (kazma / "history").is_dir()

    def test_init_idempotent(self) -> None:
        """Running init twice does not break or overwrite custom content."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            rules_path = Path(tmp) / ".kazma" / "rules.yaml"
            # Modify rules.yaml
            rules_path.write_text("language: rust\ntest_command: cargo test\n")
            # Run init again
            init_project(tmp)
            # Content should be preserved
            assert rules_path.read_text() == "language: rust\ntest_command: cargo test\n"

    def test_init_writes_valid_yaml_defaults(self) -> None:
        """Default rules.yaml contains expected keys and valid YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            rules_path = Path(tmp) / ".kazma" / "rules.yaml"
            data = yaml.safe_load(rules_path.read_text())
            assert data["language"] == "python"
            assert data["test_command"] == "python -m pytest tests/ -q"
            assert data["git_branch"] == "main"


class TestAutoDetect:
    """Tests for project_exists() and load_project()."""

    def test_auto_detect_existing(self) -> None:
        """project_exists returns True when .kazma/ is present."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            assert project_exists(tmp) is True

    def test_auto_detect_missing(self) -> None:
        """project_exists returns False when no .kazma/ directory."""
        with tempfile.TemporaryDirectory() as tmp:
            assert project_exists(tmp) is False

    def test_load_project_returns_config(self) -> None:
        """load_project returns a dict with expected keys."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            config = load_project(tmp)
            assert config is not None
            assert "rules" in config
            assert "personality" in config
            assert "tools" in config
            assert "context" in config
            assert "_path" in config

    def test_load_project_missing_returns_none(self) -> None:
        """load_project returns None when no .kazma/ exists."""
        with tempfile.TemporaryDirectory() as tmp:
            config = load_project(tmp)
            assert config is None


class TestShowProject:
    """Tests for show_project()."""

    def test_show_displays_config(self) -> None:
        """show_project outputs config summary for an initialized project."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            output = show_project(tmp)
            assert "Project config:" in output
            assert ".kazma" in output
            assert "[rules]" in output
            assert "language: python" in output

    def test_show_no_project(self) -> None:
        """show_project gives helpful message when no .kazma/ exists."""
        with tempfile.TemporaryDirectory() as tmp:
            output = show_project(tmp)
            assert "No .kazma/ project directory found" in output
            assert "kazma project init" in output


class TestValidateProject:
    """Tests for validate_project()."""

    def test_validate_valid(self) -> None:
        """validate_project passes for a correctly initialized project."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            is_valid, issues = validate_project(tmp)
            assert is_valid is True
            assert issues == []

    def test_validate_invalid_missing_file(self) -> None:
        """validate_project catches missing required files."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            # Remove a required file
            (Path(tmp) / ".kazma" / "rules.yaml").unlink()
            is_valid, issues = validate_project(tmp)
            assert is_valid is False
            assert any("rules.yaml" in i for i in issues)

    def test_validate_invalid_yaml(self) -> None:
        """validate_project catches invalid YAML."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            # Corrupt rules.yaml
            (Path(tmp) / ".kazma" / "rules.yaml").write_text(": bad yaml: :")
            is_valid, issues = validate_project(tmp)
            assert is_valid is False
            assert any("Invalid YAML" in i for i in issues)

    def test_validate_missing_directory(self) -> None:
        """validate_project reports missing .kazma/ directory."""
        with tempfile.TemporaryDirectory() as tmp:
            is_valid, issues = validate_project(tmp)
            assert is_valid is False
            assert any("Missing .kazma/" in i for i in issues)

    def test_validate_missing_language_key(self) -> None:
        """validate_project flags missing 'language' in rules.yaml."""
        with tempfile.TemporaryDirectory() as tmp:
            init_project(tmp)
            (Path(tmp) / ".kazma" / "rules.yaml").write_text(
                "test_command: pytest\n"
            )
            is_valid, issues = validate_project(tmp)
            assert is_valid is False
            assert any("language" in i for i in issues)
