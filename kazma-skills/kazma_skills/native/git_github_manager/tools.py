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
    """Commit modified or untracked files with a detailed commit message."""
    cwd = _get_workspace()
    try:
        # Stage files
        add_args = ["git", "add", "."] if not files else ["git", "add"] + files
        subprocess.run(add_args, cwd=cwd, check=True)
        
        # Commit
        res = subprocess.run(
            ["git", "commit", "-m", message],
            cwd=cwd,
            capture_output=True,
            text=True,
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
    """Create a new Pull Request on the GitHub repository using GitHub APIs."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        return "Error: GITHUB_TOKEN not found in environment."
        
    cwd = _get_workspace()
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
        )
        url = res.stdout.strip()
        m = re.search(r"github\.com[:/]([^/]+)/([^.]+)", url)
        if not m:
            return f"Could not determine owner/repo from origin remote URL: {url}"
        
        owner, repo = m.group(1), m.group(2)
        async with httpx.AsyncClient() as client:
            api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls"
            headers = {
                "Authorization": f"token {token}",
                "Accept": "application/vnd.github.v3+json",
            }
            payload = {"title": title, "body": body, "head": head, "base": base}
            r = await client.post(api_url, json=payload, headers=headers)
            if r.status_code == 201:
                return f"Successfully created Pull Request: {r.json().get('html_url')}"
            return f"Failed to create PR (status {r.status_code}): {r.text}"
    except Exception as e:
        return f"Error creating Pull Request: {e}"


async def github_list_issues(repo: str, state: str = "open") -> str:
    """Retrieve and view list of issues currently open on the remote repository."""
    token = os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github.v3+json"}
    if token:
        headers["Authorization"] = f"token {token}"
        
    async with httpx.AsyncClient() as client:
        url = f"https://api.github.com/repos/{repo}/issues?state={state}"
        try:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                issues = r.json()
                results = []
                for iss in issues[:10]:
                    results.append(f"#{iss.get('number')}: {iss.get('title')} ({iss.get('html_url')})")
                return "\n".join(results) or f"No {state} issues found."
            return f"Failed to fetch issues (status {r.status_code}): {r.text}"
        except Exception as e:
            return f"Error listing issues: {e}"
