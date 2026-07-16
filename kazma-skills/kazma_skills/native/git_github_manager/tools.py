"""Git and GitHub Native Skill — tools for local repository and remote API operations."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import httpx
from kazma_core.tools.file_write import _get_workspace

logger = logging.getLogger(__name__)


async def git_status() -> str:
    """Get the current git repository status, branch, and staged/unstaged changes."""
    cwd = _get_workspace()
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return "Not a git repository or git command failed."
        
        # Get active branch name
        branch_res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_res.stdout.strip() or "Detached HEAD"
        
        status_lines = res.stdout.strip()
        if not status_lines:
            return f"On branch {branch}\nWorking tree clean."
        
        return f"On branch {branch}\nChanges:\n{status_lines}"
    except Exception as e:
        return f"Error executing git status: {e}"


async def git_commit(message: str, files: list[str] | None = None) -> str:
    """Commit modified or untracked files with a detailed commit message.

    When bot identity is enabled (``git.bot_identity`` in ``kazma.yaml``),
    the commit is authored as the bot (e.g. ``Kazma Agent [bot]``) via
    ``GIT_AUTHOR_*`` / ``GIT_COMMITTER_*`` env vars — without mutating the
    repo's ``.git/config``.
    """
    cwd = _get_workspace()
    try:
        # Resolve bot identity env (no-op when disabled).
        from kazma_core.git_identity import get_commit_env

        commit_env = get_commit_env()

        # Stage files
        add_args = ["git", "add", "."] if not files else ["git", "add"] + files
        subprocess.run(add_args, cwd=cwd, check=True, env=commit_env)

        # Commit
        res = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=commit_env,
        )
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error committing changes: {e}"


async def git_push_pull(action: str = "pull") -> str:
    """Synchronize local branch changes by executing git pull or git push."""
    cwd = _get_workspace()
    if action not in ("push", "pull"):
        return "Invalid action. Use 'push' or 'pull'."
    try:
        res = subprocess.run(
            ["git", action],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return res.stdout.strip() or res.stderr.strip()
    except Exception as e:
        return f"Error running git {action}: {e}"


async def github_create_pr(title: str, body: str, head: str, base: str = "main") -> str:
    """Create a new Pull Request on the GitHub repository.

    Uses the shared ``GitHubClient`` when available (so OAuth tokens saved
    via the Web UI are visible — they were previously invisible to this
    tool, which only read ``$GITHUB_TOKEN``). Falls back to a direct
    ``httpx`` call using the env-var token in headless/core-only deployments.
    The repo is inferred from the workspace's ``origin`` remote (matching
    the gateway's ``resolve_repo``).
    """
    owner_repo = _resolve_owner_repo()
    if isinstance(owner_repo, str):
        return owner_repo  # error message

    owner, repo = owner_repo
    client = _get_shared_client()
    if client is not None:
        try:
            async with client as gh:
                data = await gh.request(
                    "POST", f"/repos/{owner}/{repo}/pulls",
                    json={"title": title, "body": body, "head": head, "base": base},
                )
            return f"Successfully created Pull Request: {data.get('html_url')}"
        except Exception as e:
            return f"Error creating Pull Request: {e}"

    # Fallback: direct httpx with env-var token (headless deployment).
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: No GitHub token configured. Set GITHUB_TOKEN or connect GitHub in the Web UI."
    try:
        async with httpx.AsyncClient() as http:
            r = await http.post(
                f"https://api.github.com/repos/{owner}/{repo}/pulls",
                json={"title": title, "body": body, "head": head, "base": base},
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github.v3+json"},
            )
            if r.status_code == 201:
                return f"Successfully created Pull Request: {r.json().get('html_url')}"
            return f"Failed to create PR (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error creating Pull Request: {e}"


async def github_list_issues(repo: str | None = None, state: str = "open") -> str:
    """List issues on the repository.

    ``repo`` is optional — when omitted, the repo is inferred from the
    workspace's ``origin`` remote (consistent with ``github_create_pr``).
    Uses the shared ``GitHubClient`` when available.
    """
    # Resolve repo: explicit arg → workspace remote.
    if not repo:
        owner_repo = _resolve_owner_repo()
        if isinstance(owner_repo, str):
            return owner_repo
        slug = f"{owner_repo[0]}/{owner_repo[1]}"
    else:
        slug = repo

    client = _get_shared_client()
    if client is not None:
        try:
            async with client as gh:
                issues = await gh.request(
                    "GET", f"/repos/{slug}/issues", params={"state": state},
                )
            return _format_issues(issues, state)
        except Exception as e:
            return f"Error listing issues: {e}"

    # Fallback: direct httpx (headless).
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        async with httpx.AsyncClient() as http:
            r = await http.get(
                f"https://api.github.com/repos/{slug}/issues?state={state}",
                headers=headers,
            )
            if r.status_code == 200:
                return _format_issues(r.json(), state)
            return f"Failed to fetch issues (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error listing issues: {e}"


def _format_issues(issues: list, state: str) -> str:
    """Render an issues list as a compact string."""
    results = []
    for iss in (issues or [])[:10]:
        # GitHub's issues endpoint also returns PRs; filter them out.
        if "pull_request" in iss:
            continue
        results.append(f"#{iss.get('number')}: {iss.get('title')} ({iss.get('html_url')})")
    return "\n".join(results) or f"No {state} issues found."


def _resolve_owner_repo() -> tuple[str, str] | str:
    """Resolve (owner, repo) from the workspace git remote.

    Returns an error-message string on failure (so callers can return it
    directly). Prefers the shared gateway resolver when available.
    """
    try:
        from kazma_gateway.routers.github_client import resolve_repo, get_active_cwd  # type: ignore

        slug = resolve_repo(get_active_cwd())
        if slug:
            return slug
    except Exception:
        pass
    # Fallback: parse locally.
    cwd = _get_workspace()
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd, capture_output=True, text=True, timeout=5,
        )
        url = res.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$", url)
        if not m:
            return f"Could not determine owner/repo from origin remote URL: {url}"
        return m.group(1), m.group(2)
    except Exception as e:
        return f"Error resolving repository: {e}"


def _get_shared_client():
    """Return a GitHubClient instance if the gateway is importable, else None.

    Lazy import so this skill (in ``kazma-skills``, which depends only on
    ``kazma_core``) doesn't hard-depend on ``kazma-gateway``. When the
    gateway is present, the returned client resolves the token via
    ConfigStore (OAuth → PAT → env), closing the gap where OAuth-saved
    tokens were invisible to these tools.
    """
    try:
        from kazma_gateway.routers.github_client import GitHubClient  # type: ignore

        return GitHubClient()
    except Exception:
        return None
