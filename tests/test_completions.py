"""Tests for kazma CLI shell tab completion."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from kazma_cli.completions import (
    FLAGS,
    SUBCMDS,
    _bash_completion_script,
    _zsh_completion_script,
    generate_completions,
    install_completion,
    list_available_models,
)


class TestCompletionBashGenerates:
    """Bash completion script generation."""

    def test_completion_bash_generates(self) -> None:
        """Outputs a valid bash completion script."""
        output = generate_completions("bash")
        assert "complete -F _kazma_completion kazma" in output
        assert "_kazma_completion()" in output
        assert "COMPREPLY" in output

    def test_bash_includes_subcommands(self) -> None:
        """Bash script references all subcommands."""
        output = _bash_completion_script()
        for cmd in SUBCMDS:
            assert cmd in output, f"{cmd} missing from bash completion"

    def test_bash_includes_flags(self) -> None:
        """Bash script references all flags."""
        output = _bash_completion_script()
        for flag in FLAGS:
            assert flag in output, f"{flag} missing from bash completion"

    def test_bash_handles_dynamic_model(self) -> None:
        """Bash script has --model handling with dynamic model list."""
        output = _bash_completion_script()
        assert '"--model"' in output or "--model" in output
        assert "kazma completion --list-models" in output


class TestCompletionZshGenerates:
    """Zsh completion script generation."""

    def test_completion_zsh_generates(self) -> None:
        """Outputs a valid zsh completion script."""
        output = generate_completions("zsh")
        assert "#compdef kazma" in output
        assert "_arguments" in output
        assert "_kazma()" in output

    def test_zsh_includes_subcommand_descriptions(self) -> None:
        """Zsh script has descriptive subcommand entries."""
        output = _zsh_completion_script()
        assert "chat[Start an interactive chat session]" in output
        assert "serve[Start the WebUI server]" in output
        assert "completion[Manage shell completions]" in output

    def test_zsh_has_model_state_handler(self) -> None:
        """Zsh script has the ->models state handler."""
        output = _zsh_completion_script()
        assert "->models" in output
        assert "kazma completion --list-models" in output


class TestDynamicModelList:
    """Dynamic model name listing."""

    def test_dynamic_model_list(self) -> None:
        """list_available_models returns a non-empty sorted list."""
        models = list_available_models()
        assert isinstance(models, list)
        assert len(models) >= 1
        # Must be sorted
        assert models == sorted(models)
        # Models look like valid model names (no empty strings)
        assert all(isinstance(m, str) and len(m) > 0 for m in models)

    def test_model_list_via_cli(self) -> None:
        """--list-models output is parseable."""
        result = subprocess.run(
            [sys.executable, "-m", "kazma_cli.main", "completion", "--list-models"],
            capture_output=True,
            text=True,
            cwd=Path(__file__).parent.parent,
        )
        assert result.returncode == 0
        models = [line.strip() for line in result.stdout.strip().splitlines() if line.strip()]
        assert len(models) >= 1
        # Each line should be a model name (no empty strings)
        assert all(isinstance(m, str) and len(m) > 0 for m in models)


class TestInstallCommand:
    """Install subcommand writes files to correct locations."""

    def test_install_command(self) -> None:
        """install_completion writes to a path and returns a message."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch("kazma_cli.completions.Path.home", return_value=home):
                result = install_completion("bash")
            assert "installed" in result.lower()
            # Verify file was written
            candidates = [
                home / ".local" / "share" / "bash-completion" / "completions" / "kazma",
                home / ".bash_completion.d" / "kazma",
            ]
            written = any(c.exists() for c in candidates)
            assert written, f"Completion file not found in any candidate: {candidates}"

    def test_install_zsh_writes_file(self) -> None:
        """install_completion for zsh writes _kazma file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch("kazma_cli.completions.Path.home", return_value=home):
                result = install_completion("zsh")
            assert "installed" in result.lower()
            candidates = [
                home / ".zsh" / "completions" / "_kazma",
                home / ".local" / "share" / "zsh" / "site-functions" / "_kazma",
            ]
            written = any(c.exists() for c in candidates)
            assert written, f"Zsh completion file not found: {candidates}"

    def test_install_file_content_is_valid(self) -> None:
        """Installed bash file is valid completion script."""
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            with patch("kazma_cli.completions.Path.home", return_value=home):
                install_completion("bash")
            # Find the written file
            for candidate in [
                home / ".local" / "share" / "bash-completion" / "completions" / "kazma",
                home / ".bash_completion.d" / "kazma",
            ]:
                if candidate.exists():
                    content = candidate.read_text()
                    assert "complete -F _kazma_completion kazma" in content
                    return
            pytest.fail("No completion file was written")


class TestEdgeCases:
    """Edge case and error handling."""

    def test_unsupported_shell_raises(self) -> None:
        """Unsupported shell raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported shell"):
            generate_completions("fish")

    def test_install_unsupported_shell_raises(self) -> None:
        """install_completion raises for unsupported shell."""
        with pytest.raises(ValueError, match="Unsupported shell"):
            install_completion("fish")

    def test_list_models_deduplicates(self) -> None:
        """list_available_models returns no duplicates."""
        with patch("kazma_cli.completions.FALLBACK_MODELS", ["a", "b", "a", "a", "b"]):
            with patch("kazma_cli.banner._load_config", return_value={}):
                models = list_available_models()
        assert models == ["a", "b"]

    def test_generated_scripts_are_non_empty_strings(self) -> None:
        """generate_completions returns a non-empty string for both shells."""
        for shell in ("bash", "zsh"):
            output = generate_completions(shell)
            assert isinstance(output, str)
            assert len(output) > 100, f"{shell} output too short: {len(output)} chars"
