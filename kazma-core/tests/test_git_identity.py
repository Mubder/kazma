"""Tests for the bot commit identity feature (kazma_core.git_identity).

These tests are config-independent: they mock ``_read_config`` so they
don't depend on what's in ``kazma.yaml`` at test time (which may have
``enabled: true`` with real GitHub App credentials).
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from kazma_core.git_identity import (
    get_bot_identity,
    get_commit_env,
    _DEFAULT_BOT_NAME,
    _DEFAULT_BOT_EMAIL,
)


def test_disabled_when_config_says_false():
    """When config says enabled: false, bot identity is disabled."""
    with patch("kazma_core.git_identity._read_config", return_value={"enabled": False}):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KAZMA_BOT_NAME", None)
            os.environ.pop("KAZMA_BOT_EMAIL", None)
            assert get_bot_identity() is None


def test_env_vars_enable():
    """KAZMA_BOT_NAME / KAZMA_BOT_EMAIL env vars implicitly enable bot identity."""
    with patch("kazma_core.git_identity._read_config", return_value={"enabled": False}):
        with patch.dict(os.environ, {
            "KAZMA_BOT_NAME": "Test Bot",
            "KAZMA_BOT_EMAIL": "test-bot[bot]@users.noreply.github.com",
        }):
            identity = get_bot_identity()
            assert identity is not None
            assert identity["name"] == "Test Bot"
            assert identity["email"] == "test-bot[bot]@users.noreply.github.com"


def test_get_commit_env_disabled():
    """When disabled, get_commit_env returns base env unchanged."""
    with patch("kazma_core.git_identity._read_config", return_value={"enabled": False}):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KAZMA_BOT_NAME", None)
            os.environ.pop("KAZMA_BOT_EMAIL", None)
            env = get_commit_env({"PATH": "/usr/bin"})
            assert "GIT_AUTHOR_NAME" not in env
            assert "GIT_COMMITTER_EMAIL" not in env
            assert env["PATH"] == "/usr/bin"


def test_get_commit_env_injects_vars():
    """When enabled via config, get_commit_env injects GIT_AUTHOR_* / GIT_COMMITTER_*."""
    cfg = {
        "enabled": True,
        "name": "CI Bot",
        "email": "ci-bot[bot]@users.noreply.github.com",
    }
    with patch("kazma_core.git_identity._read_config", return_value=cfg):
        env = get_commit_env({"PATH": "/usr/bin", "HOME": "/home/user"})
        assert env["GIT_AUTHOR_NAME"] == "CI Bot"
        assert env["GIT_AUTHOR_EMAIL"] == "ci-bot[bot]@users.noreply.github.com"
        assert env["GIT_COMMITTER_NAME"] == "CI Bot"
        assert env["GIT_COMMITTER_EMAIL"] == "ci-bot[bot]@users.noreply.github.com"
        assert env["PATH"] == "/usr/bin"
        assert env["HOME"] == "/home/user"


def test_get_commit_env_preserves_base():
    """Base env vars (when base_env=None) are preserved from os.environ."""
    cfg = {"enabled": True, "name": "Env Bot", "email": "env-bot[bot]@users.noreply.github.com"}
    with patch("kazma_core.git_identity._read_config", return_value=cfg):
        os.environ["MY_TEST_VAR"] = "preserved"
        try:
            env = get_commit_env()
            assert env["GIT_AUTHOR_NAME"] == "Env Bot"
            assert env.get("MY_TEST_VAR") == "preserved"
        finally:
            del os.environ["MY_TEST_VAR"]


def test_default_constants():
    """Default name/email are the expected Kazma Agent values."""
    assert _DEFAULT_BOT_NAME == "Kazma Agent"
    assert "[bot]@" in _DEFAULT_BOT_EMAIL
