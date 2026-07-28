"""Unit tests for GitHub App integration and new git/github tools."""

from __future__ import annotations

import os
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from kazma_core.git_identity import get_bot_identity, get_commit_env, _try_app_email


def test_github_app_bot_email_derivation():
    """Verify GitHub App bot email is derived as <app_id>+<app_slug>[bot]@users.noreply.github.com."""
    cfg = {
        "app_id": "1234567",
        "app_slug": "kazma-agent",
    }
    email = _try_app_email(cfg)
    assert email == "1234567+kazma-agent[bot]@users.noreply.github.com"


def test_github_app_bot_identity_resolution():
    """Verify get_bot_identity uses the derived GitHub App bot email when configured."""
    cfg = {
        "enabled": True,
        "name": "Kazma Agent Bot",
        "app_id": "1234567",
        "app_slug": "kazma-agent",
    }
    with patch("kazma_core.git_identity._read_config", return_value=cfg):
        identity = get_bot_identity()
        assert identity is not None
        assert identity["name"] == "Kazma Agent Bot"
        assert identity["email"] == "1234567+kazma-agent[bot]@users.noreply.github.com"


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
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"):

        # Mock branch query & rev-parse fail (no upstream)
        mock_b = MagicMock(returncode=0, stdout="feature/my-branch\n")
        mock_u = MagicMock(returncode=1, stdout="")
        mock_push = MagicMock(returncode=0, stdout="Everything up-to-date", stderr="")

        mock_run.side_effect = [mock_b, mock_u, mock_push]

        res = await git_push_pull(action="push")
        assert "Everything up-to-date" in res

        # Verify the push call included --set-upstream origin feature/my-branch
        push_call_args = mock_run.call_args_list[2][0][0]
        assert "--set-upstream" in push_call_args
        assert "origin" in push_call_args
        assert "feature/my-branch" in push_call_args


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
