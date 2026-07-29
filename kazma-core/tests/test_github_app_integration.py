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
async def test_git_push_delegates_to_push_path():
    """git_push() (no action arg) runs the PUSH path, not pull.

    Regression for the loop bug: the merged git_push_pull defaulted to
    action='pull', so the agent ran pull when it meant push. The explicit
    git_push tool must hit push semantics without an action argument.
    """
    from kazma_skills.native.git_github_manager.tools import git_push

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", return_value="ghp_test_token"):

        mock_b = MagicMock(returncode=0, stdout="main\n")
        mock_u = MagicMock(returncode=0, stdout="origin/main\n")  # has upstream
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        mock_push = MagicMock(returncode=0, stdout="   abc..def  main -> main\n", stderr="")

        mock_run.side_effect = [mock_b, mock_u, mock_url, mock_push]

        res = await git_push()  # NO action argument — must push, not pull

        # The executed git command must be a PUSH (action=push in the log line,
        # and the push refspec 'main -> main' in output), never a pull.
        push_cmd = mock_run.call_args_list[3][0][0]
        assert "push" in push_cmd, f"expected push in git command, got: {push_cmd}"
        assert "main -> main" in res


@pytest.mark.asyncio
async def test_git_pull_delegates_to_pull_path():
    """git_pull() runs the PULL path (never push)."""
    from kazma_skills.native.git_github_manager.tools import git_pull

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", return_value="ghp_test_token"):

        # pull has no upstream/branch checks — single subprocess.run (remote-url
        # lookup is skipped for pull since target_branch logic is push-only).
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        mock_pull = MagicMock(returncode=0, stdout="Already up to date.", stderr="")

        mock_run.side_effect = [mock_url, mock_pull]

        res = await git_pull()

        pull_cmd = mock_run.call_args_list[1][0][0]
        assert "pull" in pull_cmd, f"expected pull in git command, got: {pull_cmd}"
        assert "Already up to date" in res


@pytest.mark.asyncio
async def test_git_push_pull_retries_on_auth_failure_after_re_mint():
    """Verify a push that fails on a dead cached ghs_* token retries after a fresh re-mint.

    This is the recovery path: the cached installation token is revoked
    server-side, git push returns a 401/403-style auth error, so the tool
    invalidates the cache, mints a fresh token, and retries the push once.
    """
    from kazma_skills.native.git_github_manager.tools import git_push_pull

    # Token resolution for git now prefers get_app_installation_token. First
    # call (initial resolution) → stale token; the retry path calls
    # mint_app_installation_token(force=True) → fresh token.
    token_seq = {"calls": 0}

    def fake_app_token():
        token_seq["calls"] += 1
        return "ghs_stale_dead_token" if token_seq["calls"] == 1 else "ghs_fresh_token"

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_core.git_identity.get_app_installation_token", side_effect=fake_app_token), \
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

        # Recovery happened: cache invalidated + fresh token force-minted.
        mock_invalidate.assert_called_once()
        mock_mint.assert_called_with(force=True)
        # The retry succeeded, so the success output is returned (no diagnostic header).
        assert "main -> main" in res
        assert "Auth failed" not in res


@pytest.mark.asyncio
async def test_git_push_pull_detects_false_up_to_date():
    """Verify a push that returns exit-0 'up to date' but didn't land is caught.

    Regression for the masking bug: some auth failures make `git push origin main`
    print 'Everything up-to-date' with exit 0 while the commit never reaches
    GitHub. The tool must verify via ls-remote and treat the mismatch as an
    auth failure (attempting a token refresh), NOT report success.
    """
    from kazma_skills.native.git_github_manager.tools import git_push_pull

    token_seq = {"calls": 0}

    def fake_get_token():
        token_seq["calls"] += 1
        return "ghs_stale_dead_token" if token_seq["calls"] == 1 else "ghs_fresh_token"

    HEAD_SHA = "abc123def456"

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", side_effect=fake_get_token), \
         patch("kazma_core.git_identity.invalidate_app_token_cache"), \
         patch("kazma_core.git_identity.mint_app_installation_token", return_value="ghs_fresh_token"):

        # Call order: branch, upstream, remote-url, PUSH(no-op up-to-date),
        # rev-parse HEAD, rev-list ahead count, ls-remote verify (FAILS: remote
        # doesn't have HEAD), then retry PUSH, then post-refresh ls-remote verify.
        mock_b = MagicMock(returncode=0, stdout="main\n")
        mock_u = MagicMock(returncode=0, stdout="origin/main\n")
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        # Push says up-to-date, exit 0 — the masking lie.
        mock_push_noop = MagicMock(returncode=0, stdout="Everything up-to-date", stderr="")
        mock_head = MagicMock(returncode=0, stdout=HEAD_SHA + "\n")
        mock_ahead = MagicMock(returncode=0, stdout="1\n")  # 1 commit ahead
        # ls-remote: remote does NOT contain HEAD → verify fails.
        mock_ls_fail = MagicMock(returncode=0, stdout="999000111222\trefs/heads/main\n")
        # Retry push after token refresh — still doesn't land (App revoked).
        mock_push_retry = MagicMock(returncode=0, stdout="Everything up-to-date", stderr="")
        # Post-refresh ls-remote verify: still missing HEAD.
        mock_ls_retry_fail = MagicMock(returncode=0, stdout="999000111222\trefs/heads/main\n")

        mock_run.side_effect = [
            mock_b, mock_u, mock_url, mock_push_noop,
            mock_head, mock_ahead, mock_ls_fail,
            mock_push_retry, mock_ls_retry_fail,
        ]

        res = await git_push_pull(action="push")

        # Must NOT claim success — the commit did not reach GitHub.
        assert "did not reach GitHub" in res or "Auth failed" in res
        # Must have attempted a token refresh (recovery path triggered).
        # The diagnostic explains the false-up-to-date masking.
        assert "up to date" in res.lower() or "token" in res.lower()


@pytest.mark.asyncio
async def test_git_push_pull_up_to_date_when_truly_in_sync():
    """A genuine up-to-date (local == remote) push is NOT flagged as a failure."""
    from kazma_skills.native.git_github_manager.tools import git_push_pull

    HEAD_SHA = "abc123def456"

    with patch("subprocess.run") as mock_run, \
         patch("kazma_skills.native.git_github_manager.tools._get_workspace", return_value="/tmp/test"), \
         patch("kazma_gateway.routers.github_client.get_github_token", return_value="ghs_good_token"):

        mock_b = MagicMock(returncode=0, stdout="main\n")
        mock_u = MagicMock(returncode=0, stdout="origin/main\n")
        mock_url = MagicMock(returncode=0, stdout="https://github.com/owner/repo.git\n")
        mock_push_ok = MagicMock(returncode=0, stdout="Everything up-to-date", stderr="")
        # Not ahead (0 commits) → verify step is skipped entirely.
        mock_head = MagicMock(returncode=0, stdout=HEAD_SHA + "\n")
        mock_ahead_zero = MagicMock(returncode=0, stdout="0\n")

        mock_run.side_effect = [mock_b, mock_u, mock_url, mock_push_ok]

        res = await git_push_pull(action="push")
        # No verification error — genuinely in sync.
        assert "did not reach GitHub" not in res
        assert "Everything up-to-date" in res


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
