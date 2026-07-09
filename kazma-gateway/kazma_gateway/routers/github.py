"""GitHub integration router — Personal Access Token setup, repo stats, and Action workflows.

Endpoints:
  GET  /api/github/status  — fetch repo details (stars, forks, open issues, PRs) and Action status.
  POST /api/github/token   — save Personal Access Token securely.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/github", tags=["github"])


class TokenSaveRequest(BaseModel):
    token: str


def parse_github_slug(url: str) -> tuple[str, str] | None:
    """Parse a GitHub remote URL (HTTPS or SSH) into an (owner, repo) tuple."""
    url = url.strip()
    if not url:
        return None
    # Matches HTTPS: https://github.com/owner/repo.git or https://github.com/owner/repo
    # Matches SSH: git@github.com:owner/repo.git or git@github.com:owner/repo
    pattern = r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/\.]+?)(?:\.git)?$"
    match = re.search(pattern, url)
    if match:
        owner, repo = match.groups()
        return owner, repo
    return None


def save_github_token_to_env(token: str) -> None:
    """Save GITHUB_TOKEN to the root .env file securely."""
    env_path = Path.cwd() / ".env"
    if not env_path.exists():
        env_path = Path("g:/GitHubRepos/kazma/.env")

    content = ""
    if env_path.exists():
        try:
            content = env_path.read_text(encoding="utf-8")
        except Exception:
            pass

    lines = content.splitlines()
    updated = False
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("GITHUB_TOKEN=") or stripped.startswith("GITHUB_PAT="):
            new_lines.append(f"GITHUB_TOKEN={token}")
            updated = True
        elif stripped.startswith("connectors.github.token="):
            new_lines.append(f"connectors.github.token={token}")
        else:
            new_lines.append(line)

    if not updated:
        new_lines.append(f"GITHUB_TOKEN={token}")

    try:
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        os.environ["GITHUB_TOKEN"] = token
        os.environ["GITHUB_PAT"] = token
    except Exception as exc:
        logger.error("[github] Failed to write to .env file: %s", exc)


@router.post("/token")
async def save_token(body: TokenSaveRequest) -> JSONResponse:
    """Save the GitHub PAT token to SQLite (settings.db) and .env file."""
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=422, detail="Token must not be empty.")

    # Save to SQLite ConfigStore
    try:
        from kazma_core.config_store import get_config_store
        get_config_store().set("connectors.github.token", token, category="connectors")
    except Exception as exc:
        logger.error("[github/token] Failed to write to ConfigStore: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to save token to database.") from exc

    # Save to .env
    save_github_token_to_env(token)

    return JSONResponse({"status": "ok", "message": "GitHub token successfully configured and synchronized."})


@router.get("/status")
async def github_status() -> JSONResponse:
    """Fetch live GitHub repository details, stats, and workflows."""
    # 1. Resolve workspace path
    try:
        from kazma_core.stores import get_workspace_store
        active_ws = get_workspace_store().get_active_workspace()
        if active_ws:
            cwd = active_ws["root_path"]
        else:
            from kazma_core.config_store import get_config_store
            cwd = get_config_store().get("workspace.selected_path") or os.getcwd()
    except Exception:
        cwd = os.getcwd()

    # 2. Check if git repository and get remote URL
    git_dir = Path(cwd) / ".git"
    if not git_dir.exists():
        return JSONResponse({
            "is_github": False,
            "error": "Workspace is not a Git repository."
        })

    # Run git config remote.origin.url
    try:
        res = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5
        )
        remote_url = res.stdout.strip() if res.returncode == 0 else ""
    except Exception as exc:
        logger.error("[github/status] Failed to fetch remote origin: %s", exc)
        remote_url = ""

    if not remote_url:
        return JSONResponse({
            "is_github": False,
            "error": "No remote origin URL configured for this repository."
        })

    slug_info = parse_github_slug(remote_url)
    if not slug_info:
        return JSONResponse({
            "is_github": False,
            "remote_url": remote_url,
            "error": f"Failed to parse GitHub owner/repo from remote URL: {remote_url}"
        })

    owner, repo = slug_info

    # 3. Retrieve token from ConfigStore, env, or settings
    token = ""
    try:
        from kazma_core.config_store import get_config_store
        token = get_config_store().get("connectors.github.token", "")
    except Exception:
        pass

    if not token:
        token = os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_PAT", "")

    # 4. Fetch details from GitHub API
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "Kazma-Agent-Framework"
    }
    if token:
        headers["Authorization"] = f"token {token}"

    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    pulls_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=1"
    workflows_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs?per_page=1"

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Fetch repo metadata
            repo_resp = await client.get(api_url, headers=headers)
            if repo_resp.status_code == 401:
                return JSONResponse({
                    "is_github": True,
                    "owner": owner,
                    "repo": repo,
                    "has_token": bool(token),
                    "token_valid": False,
                    "error": "Invalid GitHub Token (401 Unauthorized)."
                })
            elif repo_resp.status_code == 403:
                # Check rate limit
                rate_limit_remaining = repo_resp.headers.get("X-RateLimit-Remaining", "")
                if rate_limit_remaining == "0":
                    return JSONResponse({
                        "is_github": True,
                        "owner": owner,
                        "repo": repo,
                        "has_token": bool(token),
                        "rate_limited": True,
                        "error": "GitHub API rate limit exceeded. Please configure a Personal Access Token to lift limits."
                    })
                else:
                    return JSONResponse({
                        "is_github": True,
                        "owner": owner,
                        "repo": repo,
                        "has_token": bool(token),
                        "error": "Access Forbidden (403). For private repositories, please ensure your Personal Access Token has correct access."
                    })
            elif repo_resp.status_code != 200:
                return JSONResponse({
                    "is_github": True,
                    "owner": owner,
                    "repo": repo,
                    "error": f"Failed to retrieve repository details. HTTP {repo_resp.status_code}"
                })

            repo_data = repo_resp.json()

            # Fetch open PRs count
            pulls_resp = await client.get(pulls_url, headers=headers)
            open_prs_count = 0
            if pulls_resp.status_code == 200:
                link_header = pulls_resp.headers.get("Link", "")
                if link_header:
                    last_match = re.search(r"page=(\d+)>;\s*rel=\"last\"", link_header)
                    if last_match:
                        open_prs_count = int(last_match.group(1))
                    else:
                        open_prs_count = len(pulls_resp.json())
                else:
                    open_prs_count = len(pulls_resp.json())

            # Fetch latest workflow run
            workflows_resp = await client.get(workflows_url, headers=headers)
            latest_run = None
            if workflows_resp.status_code == 200:
                run_data = workflows_resp.json()
                runs = run_data.get("workflow_runs", [])
                if runs:
                    r = runs[0]
                    latest_run = {
                        "name": r.get("name", "Workflow"),
                        "status": r.get("status", ""),
                        "conclusion": r.get("conclusion", ""),
                        "html_url": r.get("html_url", ""),
                        "event": r.get("event", ""),
                        "branch": r.get("head_branch", ""),
                        "id": r.get("id", "")
                    }

            # Subtract PRs from open_issues because GitHub api includes PRs in open_issues_count
            raw_issues = repo_data.get("open_issues_count", 0)
            net_issues = max(0, raw_issues - open_prs_count)

            return JSONResponse({
                "is_github": True,
                "owner": owner,
                "repo": repo,
                "has_token": bool(token),
                "token_valid": True,
                "private": repo_data.get("private", False),
                "stars": repo_data.get("stargazers_count", 0),
                "forks": repo_data.get("forks_count", 0),
                "open_issues": net_issues,
                "open_prs": open_prs_count,
                "description": repo_data.get("description", ""),
                "html_url": repo_data.get("html_url", ""),
                "latest_workflow": latest_run
            })

        except httpx.RequestError as exc:
            logger.error("[github/status] Connection error: %s", exc)
            return JSONResponse({
                "is_github": True,
                "owner": owner,
                "repo": repo,
                "error": "Failed to reach GitHub API."
            })


def create_github_router() -> APIRouter:
    """Helper method to return the APIRouter instance."""
    return router
