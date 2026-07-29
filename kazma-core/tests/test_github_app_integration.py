"""Unit tests for GitHub App integration and new git/github tools."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from kazma_core.git_identity import get_bot_identity, get_commit_env, _try_app_email


def test_github_app_bot_email_derivation():
    """Verify GitHub App bot email is auto-derived as <bot_user_id>+<app_slug>[bot]@users.noreply.github.com.

    The bot *user id* (305657551) is NOT the App id — only the user id links
    the avatar. It is fetched from GitHub's /users/{slug}[bot] API.
    """
    cfg = {
        "app_id": "1234567",
        "app_slug": "kazma-agent",
    }
    with patch("kazma_core.git_identity._fetch_bot_user_id", return_value=305657551):
        email = _try_app_email(cfg)
    assert email == "305657551+kazma-agent[bot]@users.noreply.github.com"


def test_github_app_bot_email_explicit_override_wins():
    """An explicit app_email in config overrides the auto-derived email."""
    cfg = {
        "app_id": "1234567",
        "app_slug": "kazma-agent",
        "app_email": "999999999+kazma-agent[bot]@users.noreply.github.com",
    }
    email = _try_app_email(cfg)
    assert email == "999999999+kazma-agent[bot]@users.noreply.github.com"


def test_github_app_bot_identity_resolution():
    """Verify get_bot_identity uses the derived GitHub App bot email when configured."""
    cfg = {
        "enabled": True,
        "name": "Kazma Agent Bot",
        "app_id": "1234567",
        "app_slug": "kazma-agent",
    }
    with patch("kazma_core.git_identity._read_config", return_value=cfg), \
         patch("kazma_core.git_identity._fetch_bot_user_id", return_value=305657551):
        identity = get_bot_identity()
        assert identity is not None
        assert identity["name"] == "Kazma Agent Bot"
        assert identity["email"] == "305657551+kazma-agent[bot]@users.noreply.github.com"


def test_get_github_token_falls_back_to_app_token():
    """Verify get_github_token falls back to get_app_installation_token when no PAT/OAuth exists."""
    from kazma_gateway.routers.github_client import get_github_token

    with patch("kazma_core.config_store.get_config_store") as mock_cs:
        store_mock = MagicMock()
        store_mock.get.return_value = ""
        mock_cs.return_value = store_mock

        with patch.dict(os.environ, {}, clear=True):
            with patch("kazma_core.git_identity.get_app_installation_token", return_value="ghs_app_token_12345"):
                token = get_github_token()
                assert token == "ghs_app_token_12345"


@pytest.mark.asyncio
async def test_git_push_pull_upstream():
    """Verify git_push_pull action='push' attaches --set-upstream when branch has no tracking."""
    from kazma_skills.native.git_github_manager.tools import git_push_pull

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", return_value="ghp_test_token"):

        # Subprocess call order in git_push_pull: branch → upstream check
        # (rev-parse fails → no upstream) → remote-url → push.
        mock_b = MagicMock(returncode=0, stdout="feature/my-branch\n")
        mock_u = MagicMock(returncode=1, stdout="")
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        mock_push = MagicMock(returncode=0, stdout="Everything up-to-date", stderr="")

        mock_run.side_effect = [mock_b, mock_u, mock_url, mock_push]

        res = await git_push_pull(action="push")
        assert "Everything up-to-date" in res

        # Verify the push call included --set-upstream origin feature/my-branch
        push_call_args = mock_run.call_args_list[3][0][0]
        assert "--set-upstream" in push_call_args
        assert "origin" in push_call_args
        assert "feature/my-branch" in push_call_args


@pytest.mark.asyncio
async def test_git_push_pull_retries_on_auth_failure_after_re_mint():
    """Verify a push that fails on a dead cached ghs_* token retries after a fresh re-mint.

    This is the recovery path: the cached installation token is revoked
    server-side, git push returns a 401/403-style auth error, so the tool
    invalidates the cache, mints a fresh token, and retries the push once.
    """
    from kazma_skills.native.git_github_manager.tools import git_push_pull

    # First get_github_token → stale token; second (during retry) → fresh token.
    token_seq = {"calls": 0}

    def fake_get_token():
        token_seq["calls"] += 1
        return "ghs_stale_dead_token" if token_seq["calls"] == 1 else "ghs_fresh_token"

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", side_effect=fake_get_token), \
         patch("kazma_core.git_identity.invalidate_app_token_cache") as mock_invalidate, \
         patch("kazma_core.git_identity.mint_app_installation_token", return_value="ghs_fresh_token") as mock_mint:

        mock_b = MagicMock(returncode=0, stdout="main\n")
        mock_u = MagicMock(returncode=0, stdout="origin/main\n")  # has upstream
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        # First push → auth failure; second push (retry) → success.
        mock_push_fail = MagicMock(returncode=128, stdout="", stderr="fatal: Authentication failed for https://github.com/owner/repo.git/")
        mock_push_ok = MagicMock(returncode=0, stdout="To https://github.com/owner/repo.git\n   abc..def  main -> main\n", stderr="")

        mock_run.side_effect = [mock_b, mock_u, mock_url, mock_push_fail, mock_push_ok]

        res = await git_push_pull(action="push")

        # Recovery happened: cache invalidated + fresh token minted.
        mock_invalidate.assert_called_once()
        mock_mint.assert_called_once_with(force=True)
        # The retry succeeded, so the success output is returned (no diagnostic header).
        assert "main -> main" in res
        assert "Auth failed" not in res


@pytest.mark.asyncio
async def test_github_merge_pr():
    """Verify github_merge_pr tool sends PUT to pulls/{number}/merge."""
    from kazma_skills.native.git_github_manager.tools import github_merge_pr

    mock_client = AsyncMock()
    mock_client.request.return_value = {"merged": True, "message": "Pull Request successfully merged"}

    with patch("kazma_skills.native.git_github_manager.tools._resolve_owner_repo", return_value=("owner", "repo")), \
         patch("kazma_skills.native.git_github_manager.tools._get_shared_client", return_value=mock_client):

        # Mock async context manager __aenter__
        mock_client.__aenter__.return_value = mock_client

        res = await github_merge_pr(number=42, commit_title="Squash merge feature", merge_method="squash")
        assert "Successfully merged PR #42" in res
        mock_client.request.assert_called_once_with(
            "PUT",
            "/repos/owner/repo/pulls/42/merge",
            json={"merge_method": "squash", "commit_title": "Squash merge feature"}
        )


@pytest.mark.asyncio
async def test_github_create_issue():
    """Verify github_create_issue sends POST to issues endpoint."""
    from kazma_skills.native.git_github_manager.tools import github_create_issue

    mock_client = AsyncMock()
    mock_client.request.return_value = {"number": 101, "html_url": "https://github.com/owner/repo/issues/101"}

    with patch("kazma_skills.native.git_github_manager.tools._resolve_owner_repo", return_value=("owner", "repo")), \
         patch("kazma_skills.native.git_github_manager.tools._get_shared_client", return_value=mock_client):

        mock_client.__aenter__.return_value = mock_client

        res = await github_create_issue(title="Bug in auth", body="Description", labels=["bug"])
        assert "Successfully created Issue #101" in res
        mock_client.request.assert_called_once_with(
            "POST",
            "/repos/owner/repo/issues",
            json={"title": "Bug in auth", "body": "Description", "labels": ["bug"]}
        )
