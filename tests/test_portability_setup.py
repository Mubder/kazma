"""Cross-platform portability tests for setup, environment, and completion.

Covers VAL-PORT-004 (Windows setup script), VAL-PORT-005 (no hardcoded
.venv/bin/pytest), VAL-PORT-006 (Telegram chat ID from env var).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


# ===========================================================================
# VAL-PORT-004: Windows setup script exists
# ===========================================================================

class TestWindowsSetupScript:
    """A Windows-compatible setup script (setup.ps1) exists at the repo root."""

    def test_setup_ps1_exists(self) -> None:
        """setup.ps1 exists at the repo root."""
        assert (REPO_ROOT / "setup.ps1").is_file(), "setup.ps1 not found at repo root"

    def test_setup_ps1_no_posix_commands(self) -> None:
        """setup.ps1 does not rely on POSIX-only commands in its main body."""
        content = (REPO_ROOT / "setup.ps1").read_text(encoding="utf-8")
        # Should NOT use bash-style shebang or bash-only constructs
        assert "#!/usr/bin/env bash" not in content
        assert "set -euo pipefail" not in content


# ===========================================================================
# VAL-PORT-005: No hardcoded .venv/bin/pytest
# ===========================================================================

class TestNoHardcodedVenvBinPytest:
    """No .venv/bin/pytest (POSIX venv path) in suggestions, templates, or docs."""

    def test_suggestions_no_venv_bin(self) -> None:
        """suggestions.py does not hardcode .venv/bin/pytest."""
        content = (
            REPO_ROOT / "kazma-gateway" / "kazma_gateway" / "suggestions.py"
        ).read_text(encoding="utf-8")
        assert ".venv/bin/pytest" not in content, (
            "suggestions.py still contains hardcoded .venv/bin/pytest"
        )

    def test_project_template_no_venv_bin(self) -> None:
        """project.py DEFAULT_RULES does not hardcode .venv/bin/pytest."""
        content = (
            REPO_ROOT / "kazma-cli" / "kazma_cli" / "project.py"
        ).read_text(encoding="utf-8")
        assert ".venv/bin/pytest" not in content, (
            "project.py DEFAULT_RULES still contains .venv/bin/pytest"
        )

    def test_project_template_uses_portable_pytest(self) -> None:
        """project.py DEFAULT_RULES uses 'python -m pytest' (portable)."""
        from kazma_cli.project import DEFAULT_RULES

        assert "python -m pytest" in DEFAULT_RULES
        assert ".venv/bin/pytest" not in DEFAULT_RULES

    def test_suggestions_use_portable_pytest(self) -> None:
        """suggestions.py action suggestions use 'python -m pytest'."""
        from kazma_gateway.suggestions import PostTaskSuggester

        suggester = PostTaskSuggester(enabled=True)
        hints = suggester.suggest(actions=["file_write"])
        assert any("python -m pytest" in h for h in hints), (
            "file_write suggestion should recommend 'python -m pytest'"
        )
        assert not any(".venv/bin/pytest" in h for h in hints)


# ===========================================================================
# VAL-PORT-006: Telegram chat ID from env var (not hardcoded in kazma.yaml)
# ===========================================================================

class TestTelegramChatIdFromEnv:
    """The Telegram swarm group chat ID is not hardcoded in kazma.yaml."""

    def test_no_hardcoded_chat_id_in_kazma_yaml(self) -> None:
        """kazma.yaml does not contain the hardcoded chat ID literal -5553328924."""
        content = (REPO_ROOT / "kazma.yaml").read_text(encoding="utf-8")
        assert "-5553328924" not in content, (
            "kazma.yaml still contains hardcoded chat ID -5553328924"
        )

    def test_swarm_chat_id_env_var_overrides_yaml(self) -> None:
        """SwarmConfig.from_dict reads SWARM_CHAT_ID env var over the YAML value."""
        from kazma_core.swarm.config import SwarmConfig

        data = {
            "enabled": True,
            "group_chat_id": 0,
            "workers": [],
        }
        with patch.dict("os.environ", {"SWARM_CHAT_ID": "-123456789"}, clear=False):
            config = SwarmConfig.from_dict(data)
        assert config.group_chat_id == -123456789

    def test_swarm_chat_id_falls_back_to_yaml(self) -> None:
        """When SWARM_CHAT_ID is not set, the YAML value is used."""
        from kazma_core.swarm.config import SwarmConfig

        data = {
            "enabled": True,
            "group_chat_id": -999,
            "workers": [],
        }
        # Ensure SWARM_CHAT_ID is not set
        env_without = {k: v for k, v in os.environ.items() if k != "SWARM_CHAT_ID"}
        with patch.dict("os.environ", env_without, clear=True):
            config = SwarmConfig.from_dict(data)
        assert config.group_chat_id == -999

    def test_swarm_chat_id_invalid_env_falls_back(self) -> None:
        """An invalid (non-integer) SWARM_CHAT_ID falls back to YAML value."""
        from kazma_core.swarm.config import SwarmConfig

        data = {
            "enabled": True,
            "group_chat_id": -42,
            "workers": [],
        }
        with patch.dict("os.environ", {"SWARM_CHAT_ID": "not-a-number"}, clear=False):
            config = SwarmConfig.from_dict(data)
        assert config.group_chat_id == -42


# ===========================================================================
# PowerShell completion support
# ===========================================================================

class TestPowerShellCompletion:
    """PowerShell completion script generation and installation."""

    def test_generate_powershell_completion(self) -> None:
        """generate_completions('powershell') returns a non-empty script."""
        from kazma_cli.completions import generate_completions

        script = generate_completions("powershell")
        assert "Register-ArgumentCompleter" in script
        assert "kazma" in script

    def test_generate_powershell_completion_aliases(self) -> None:
        """generate_completions accepts 'pwsh' and 'ps' aliases."""
        from kazma_cli.completions import generate_completions

        for alias in ("pwsh", "ps"):
            script = generate_completions(alias)
            assert "Register-ArgumentCompleter" in script

    def test_unsupported_shell_raises(self) -> None:
        """generate_completions raises ValueError for unsupported shells."""
        from kazma_cli.completions import generate_completions

        with pytest.raises(ValueError):
            generate_completions("fish")

    def test_install_powershell_completion(self, tmp_path: Path) -> None:
        """install_completion('powershell') writes the script to disk."""
        from kazma_cli import completions as completions_mod

        # Patch the profile dir to a temp location
        with patch.object(
            completions_mod, "_powershell_profile_dir", return_value=tmp_path
        ):
            result = completions_mod.install_completion("powershell")

        assert "PowerShell completion installed" in result
        written = (tmp_path / "kazma_completion.ps1").read_text(encoding="utf-8")
        assert "Register-ArgumentCompleter" in written
