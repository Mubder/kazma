"""Tests for the Kazma CLI banner and first-run experience."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from kazma_cli.banner import (
    _load_config,
    check_config,
    show_banner,
    show_help_brief,
    show_status,
)


class TestBannerRenders:
    """Banner output tests."""

    def test_banner_renders(self) -> None:
        """Banner outputs ASCII art + version."""
        output = show_banner(suppress=False)
        # Should contain the KAZMA ASCII art (look for distinctive unicode boxes)
        assert "█" in output
        assert "Autonomous AI Agent Framework" in output
        assert "v" in output
        # Should end with a blank line
        assert output.endswith("\n")

    def test_banner_suppressed(self) -> None:
        """--no-banner flag returns minimal one-liner."""
        output = show_banner(suppress=True)
        assert output.startswith("Kazma CLI v")
        # Should be a single line, no ASCII art
        assert "\n" not in output
        assert "█" not in output
        assert "Autonomous" not in output


class TestStatusDisplay:
    """Status overview tests."""

    def test_status_shows_model(self) -> None:
        """Model name appears in status output."""
        config = {
            "llm": {"model": "gpt-4o", "api_key": "sk-test"},
            "models": {"default": "gpt-4o", "router": "openai"},
            "connectors": {},
        }
        output = show_status(config=config)
        assert "gpt-4o" in output
        assert "Model:" in output

    def test_status_shows_tools(self) -> None:
        """Tool count appears in status output."""
        config: dict = {}
        output = show_status(config=config)
        assert "Tools:" in output
        # Tools count should be a number (could be 0 if gateway not importable)
        import re

        match = re.search(r"Tools:\s+(\d+)", output)
        assert match is not None, f"Expected 'Tools: N' in output, got: {output[:200]}"

    def test_status_shows_adapters(self) -> None:
        """Active adapters are listed in status."""
        config = {
            "llm": {"model": "test"},
            "connectors": {
                "telegram": {"enabled": True},
                "discord": {"enabled": False},
                "slack": {"enabled": True},
            },
        }
        output = show_status(config=config)
        assert "telegram" in output
        assert "slack" in output
        assert "discord" not in output  # disabled


class TestHelpBrief:
    """First-run help hint tests."""

    def test_help_brief_on_first_run(self) -> None:
        """Shows quick-help hint with /help reference."""
        output = show_help_brief()
        assert "/help" in output
        assert "Quick start" in output
        assert "kazma --help" in output


class TestConfigGraceful:
    """Graceful handling of missing / broken config."""

    def test_no_config_graceful(self) -> None:
        """show_status handles missing config gracefully (no crash, no stack trace)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Point _load_config at a directory with no kazma.yaml
            with patch("kazma_cli.banner._load_config", return_value={}):
                with patch("kazma_cli.banner._find_project_root", return_value=Path(tmpdir)):
                    output = show_status(config=None)
        assert isinstance(output, str)
        assert len(output) > 0
        # Should indicate config is missing
        assert "not found" in output

    def test_empty_config_does_not_crash(self) -> None:
        """Empty config dict produces valid status output."""
        output = show_status(config={})
        assert isinstance(output, str)
        assert len(output) > 0
        assert "System Status" in output

    def test_check_config_missing_yaml(self) -> None:
        """check_config returns warnings when kazma.yaml is missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            warnings = check_config(project_root=root)
            assert any("kazma.yaml" in w for w in warnings)
            assert any(".venv" in w for w in warnings)

    def test_check_config_no_api_key(self) -> None:
        """check_config warns about missing API key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            # Write a minimal kazma.yaml with no API key
            config_path = root / "kazma.yaml"
            config_path.write_text("llm:\n  model: gpt-4o\n")
            warnings = check_config(project_root=root)
            assert any("API key" in w for w in warnings)

    def test_check_config_with_api_key_env(self) -> None:
        """check_config does not warn when OPENAI_API_KEY env var is set."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}):
                warnings = check_config(project_root=root)
                assert not any("API key" in w for w in warnings)

    def test_load_config_empty_for_missing(self) -> None:
        """_load_config returns empty dict for missing file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            config = _load_config(project_root=root)
            assert config == {}

    def test_show_banner_has_version(self) -> None:
        """Banner always includes version number."""
        output = show_banner(suppress=False)
        import re

        assert re.search(r"v\d+\.\d+\.\d+", output), f"No version found in: {output[:200]}"
