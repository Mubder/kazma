"""Integration test: git_commit with bot identity produces a bot-authored commit.

This verifies the full chain: get_commit_env() → subprocess git commit →
the commit author is the configured bot identity (not the local git user).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def git_repo(tmp_path):
    """Create a temporary git repo for testing bot commits."""
    repo = tmp_path / "test-repo"
    repo.mkdir()
    # Init git with a DEFAULT user (so we can verify the bot overrides it).
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.email", "default@test.com"], cwd=str(repo), check=True)
    subprocess.run(["git", "config", "user.name", "Default User"], cwd=str(repo), check=True)
    # Create an initial commit so HEAD exists.
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(repo), check=True)
    yield repo


def test_bot_commit_author_overrides_local(git_repo):
    """When bot identity is enabled, the commit author is the bot, not the local user."""
    from kazma_core.git_identity import get_commit_env

    bot_cfg = {
        "enabled": True,
        "name": "Test Bot",
        "email": "test-bot[bot]@users.noreply.github.com",
    }
    with patch("kazma_core.git_identity._read_config", return_value=bot_cfg):
        commit_env = get_commit_env()

        # Create a new file and commit with the bot env.
        (git_repo / "bot_file.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "."], cwd=str(git_repo), env=commit_env, check=True)
        subprocess.run(
            ["git", "commit", "-m", "bot commit test"],
            cwd=str(git_repo),
            env=commit_env,
            capture_output=True,
            check=True,
        )

        # Verify the commit author.
        result = subprocess.run(
            ["git", "log", "-1", "--format=%an <%ae>"],
            cwd=str(git_repo),
            capture_output=True,
            text=True,
        )
        author = result.stdout.strip()
        assert "Test Bot" in author, f"Expected bot name, got: {author}"
        assert "test-bot[bot]@users.noreply.github.com" in author, f"Expected bot email, got: {author}"
        assert "Default User" not in author, "Bot identity did NOT override the local git user"


def test_bot_disabled_uses_local_user(git_repo):
    """When bot identity is disabled, the commit uses the local git config."""
    from kazma_core.git_identity import get_commit_env

    with patch("kazma_core.git_identity._read_config", return_value={"enabled": False}):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KAZMA_BOT_NAME", None)
            os.environ.pop("KAZMA_BOT_EMAIL", None)
            commit_env = get_commit_env()

            (git_repo / "normal_file.py").write_text("y = 2\n")
            subprocess.run(["git", "add", "."], cwd=str(git_repo), env=commit_env, check=True)
            subprocess.run(
                ["git", "commit", "-m", "normal commit"],
                cwd=str(git_repo),
                env=commit_env,
                capture_output=True,
                check=True,
            )

            result = subprocess.run(
                ["git", "log", "-1", "--format=%an <%ae>"],
                cwd=str(git_repo),
                capture_output=True,
                text=True,
            )
            author = result.stdout.strip()
            assert "Default User" in author, f"Expected local user, got: {author}"
            assert "[bot]" not in author, "Bot identity leaked when disabled"
