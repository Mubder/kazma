"""GitHub integration router — Personal Access Token setup, repo stats, and Action workflows.

Endpoints:
  GET  /api/github/status  — fetch repo details (stars, forks, open issues, PRs) and Action status.
  POST /api/github/token   — save Personal Access Token securely.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
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
    # Resolve .env relative to CWD only (no hardcoded absolute fallback —
    # that was a portability bug). If absent, mirror to the live env only.
    env_path = Path.cwd() / ".env"

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


# ── OAuth flow (read-only integration) ────────────────────────────────
#
# The user clicks "Connect GitHub" → /oauth/start redirects to GitHub →
# the user authorizes → GitHub calls back /oauth/callback → Kazma
# exchanges the code for a token and stores it in ConfigStore only.


def _oauth_redirect_uri(request: Request) -> str:
    """Build the callback URL from the incoming request."""
    # Prefer the Host header (handles reverse proxies) over request.url.
    host = request.headers.get("host") or f"{request.url.hostname}:{request.url.port}"
    scheme = request.url.scheme
    return f"{scheme}://{host}/api/github/oauth/callback"


@router.get("/oauth/status")
async def oauth_status() -> JSONResponse:
    """Report whether the OAuth App is configured and a token is stored."""
    from kazma_gateway.routers.github_client import is_oauth_connected, oauth_configured

    return JSONResponse({
        "configured": oauth_configured(),
        "connected": is_oauth_connected(),
    })


@router.get("/oauth/start", response_model=None)
async def oauth_start(request: Request) -> RedirectResponse | JSONResponse:
    """Redirect the user to GitHub's authorization page."""
    from kazma_gateway.routers.github_client import build_authorize_url, oauth_configured

    if not oauth_configured():
        return JSONResponse(
            {"error": "GitHub OAuth App is not configured (set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET)."},
            status_code=503,
        )
    state = secrets.token_urlsafe(16)
    redirect_uri = _oauth_redirect_uri(request)
    # Stash the state + redirect_uri in ConfigStore so the callback can
    # validate the state and replay the exact redirect_uri used here.
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.batch_set([
            ("connectors.github.oauth_state", state, "connectors"),
            ("connectors.github.oauth_redirect_uri", redirect_uri, "connectors"),
        ])
    except Exception:
        logger.exception("[github/oauth] failed to store OAuth state")
    authorize_url = build_authorize_url(state=state, redirect_uri=redirect_uri)
    logger.info("[github/oauth] redirecting to GitHub authorization (redirect_uri=%s)", redirect_uri)
    return RedirectResponse(url=authorize_url, status_code=302)


@router.get("/oauth/callback", response_model=None)
async def oauth_callback(request: Request) -> RedirectResponse | JSONResponse:
    """Handle the GitHub redirect-back: exchange the code for a token."""
    from kazma_gateway.routers.github_client import (
        exchange_code_for_token,
        store_oauth_token,
        GitHubError,
    )

    code = request.query_params.get("code", "")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error", "")

    # GitHub surfaces user-denial / errors as query params on the callback.
    if error:
        logger.warning("[github/oauth] authorization error from GitHub: %s", error)
        return _oauth_result_page(False, f"Authorization denied: {error}")

    # Validate the state to prevent CSRF.
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        expected_state = store.get("connectors.github.oauth_state", "")
        redirect_uri = store.get("connectors.github.oauth_redirect_uri", "")
    except Exception:
        expected_state, redirect_uri = "", ""

    if not state or state != expected_state:
        logger.warning("[github/oauth] state mismatch — possible CSRF (got=%s expected=%s)", state, expected_state)
        return _oauth_result_page(False, "Security check failed (invalid state). Please try connecting again.")

    if not code:
        return _oauth_result_page(False, "No authorization code returned by GitHub.")

    if not redirect_uri:
        redirect_uri = _oauth_redirect_uri(request)

    try:
        token_data = await exchange_code_for_token(code=code, redirect_uri=redirect_uri)
    except GitHubError as exc:
        logger.error("[github/oauth] token exchange failed: %s", exc)
        return _oauth_result_page(False, f"Token exchange failed: {exc.message}")
    except Exception as exc:
        logger.exception("[github/oauth] unexpected error during token exchange")
        return _oauth_result_page(False, f"Unexpected error: {exc}")

    store_oauth_token(token_data)
    # Clear the one-time state so it can't be replayed.
    try:
        from kazma_core.config_store import get_config_store

        get_config_store().set("connectors.github.oauth_state", "", category="connectors")
    except Exception:
        pass
    logger.info("[github/oauth] successfully connected (scope=%s)", token_data.get("scope", ""))
    return _oauth_result_page(True, "GitHub connected successfully.")


@router.post("/oauth/revoke")
async def oauth_revoke() -> JSONResponse:
    """Disconnect: clear the stored OAuth token (does not revoke at GitHub)."""
    from kazma_gateway.routers.github_client import clear_oauth_token

    clear_oauth_token()
    logger.info("[github/oauth] OAuth token cleared (disconnected)")
    return JSONResponse({"status": "ok", "connected": False})


def _oauth_result_page(success: bool, message: str) -> HTMLResponseType:
    """Render a minimal close-the-tab page after the OAuth callback."""
    from fastapi.responses import HTMLResponse

    color = "#10b981" if success else "#ef4444"
    icon = "✅" if success else "❌"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GitHub Connection</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e5e7eb;
         display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
  .card {{ text-align: center; padding: 40px; border-radius: 12px;
           background: #1a1d27; border: 1px solid #2a2f3e; max-width: 420px; }}
  .icon {{ font-size: 3rem; margin-bottom: 16px; }}
  .msg {{ color: {color}; font-size: 1.1rem; margin-bottom: 8px; }}
  .hint {{ color: #6b7280; font-size: 0.85rem; }}
</style></head>
<body><div class="card">
  <div class="icon">{icon}</div>
  <div class="msg">{message}</div>
  <div class="hint">You can close this tab and return to Kazma.</div>
</div>
<script>
  // Try to signal the opener (the Kazma tab) so it refreshes immediately.
  if (window.opener) {{ try {{ window.opener.postMessage({{ type: 'github-oauth-done', success: {str(success).lower()} }}, '*'); }} catch(e) {{}} }}
  // Reliable fallback: redirect THIS tab back to the workspace page after
  // a short delay. On success the user lands on Kazma (which re-checks
  // OAuth status on load); we can't always window.close() a non-script
  // opened tab, so redirecting is more robust than a stuck success page.
  setTimeout(function() {{
    try {{ window.close(); }} catch(e) {{}}
    // If the tab didn't close (common for non-script-opened tabs), redirect.
    window.location.href = '/workspace';
  }}, 2000);
</script>
</body></html>"""
    return HTMLResponse(html)


# Type alias for readability (HTMLResponse imported lazily above).
HTMLResponseType = Any


# ── Read-only GitHub data endpoints ───────────────────────────────────
#
# All GET, all read-only. They resolve the active workspace's repo and
# use the shared GitHubClient. Each returns a JSONResponse; failures are
# surfaced as {"error": ..., "rate_limited": ...} with HTTP 200 so the
# frontend can render them inline (matching the existing /status style).


async def _resolve_owner_repo() -> tuple[str, str] | JSONResponse:
    """Resolve (owner, repo) for the active workspace, or an error response."""
    from kazma_gateway.routers.github_client import resolve_repo

    slug = resolve_repo()
    if not slug:
        return JSONResponse({"error": "Workspace is not a GitHub repository (no GitHub remote found)."}, status_code=200)
    return slug


def _gh_error_response(exc: Exception) -> JSONResponse:
    """Map a GitHubError (or any error) to an inline JSONResponse."""
    rate_limited = getattr(exc, "rate_limited", False)
    reset = getattr(exc, "rate_limit_reset", None)
    return JSONResponse({
        "error": str(exc)[:400],
        "rate_limited": rate_limited,
        "rate_limit_reset": reset,
    }, status_code=200)


@router.get("/pulls")
async def list_pulls(state: str = "open", limit: int = 20) -> JSONResponse:
    """Open/closed PRs with author, branch, draft + mergeable state."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 100))
    try:
        async with GitHubClient() as gh:
            pulls = await gh.get(
                f"/repos/{owner}/{repo}/pulls",
                params={"state": state, "per_page": limit, "sort": "updated", "direction": "desc"},
            )
        items = [
            {
                "number": p.get("number"),
                "title": p.get("title"),
                "state": p.get("state"),
                "draft": p.get("draft", False),
                "author": (p.get("user") or {}).get("login", ""),
                "head": (p.get("head") or {}).get("ref", ""),
                "base": (p.get("base") or {}).get("ref", ""),
                "html_url": p.get("html_url"),
                "updated_at": p.get("updated_at"),
            }
            for p in (pulls or [])
        ]
        return JSONResponse({"pulls": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/pulls] failed: %s", exc)
        return _gh_error_response(exc)


@router.get("/pulls/{number}")
async def get_pull(number: int) -> JSONResponse:
    """Single PR detail: metadata, files, plus head-commit checks via GraphQL."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    try:
        async with GitHubClient() as gh:
            pr = await gh.get(f"/repos/{owner}/{repo}/pulls/{number}")
            if not pr:
                return JSONResponse({"error": f"PR #{number} not found."})
            # Reviews + checks (best-effort GraphQL; fall back to REST).
            reviews: list[dict[str, Any]] = []
            try:
                raw_reviews = await gh.get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", params={"per_page": 30})
                reviews = [
                    {"user": (r.get("user") or {}).get("login", ""), "state": r.get("state"), "body": r.get("body", "")}
                    for r in (raw_reviews or [])
                ]
            except Exception:
                pass
        result = {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "state": pr.get("state"),
            "draft": pr.get("draft", False),
            "merged": pr.get("merged", False),
            "mergeable_state": pr.get("mergeable_state"),
            "author": (pr.get("user") or {}).get("login", ""),
            "head": (pr.get("head") or {}).get("ref", ""),
            "base": (pr.get("base") or {}).get("ref", ""),
            "body": pr.get("body", ""),
            "html_url": pr.get("html_url"),
            "additions": pr.get("additions", 0),
            "deletions": pr.get("deletions", 0),
            "changed_files": pr.get("changed_files", 0),
            "commits": pr.get("commits", 0),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "reviews": reviews,
        }
        return JSONResponse(result)
    except Exception as exc:
        logger.warning("[github/pulls/%s] failed: %s", number, exc)
        return _gh_error_response(exc)


@router.get("/issues")
async def list_issues(state: str = "open", limit: int = 30) -> JSONResponse:
    """Open/closed issues (PRs excluded via the issues API's pull_request filter)."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 100))
    try:
        async with GitHubClient() as gh:
            raw = await gh.get(
                f"/repos/{owner}/{repo}/issues",
                params={"state": state, "per_page": limit, "sort": "updated", "direction": "desc"},
            )
        # The issues API includes PRs; filter them out.
        items = [
            {
                "number": i.get("number"),
                "title": i.get("title"),
                "state": i.get("state"),
                "author": (i.get("user") or {}).get("login", ""),
                "labels": [l.get("name") for l in (i.get("labels") or [])],
                "comments": i.get("comments", 0),
                "html_url": i.get("html_url"),
                "updated_at": i.get("updated_at"),
            }
            for i in (raw or [])
            if "pull_request" not in i
        ]
        return JSONResponse({"issues": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/issues] failed: %s", exc)
        return _gh_error_response(exc)


@router.get("/commits")
async def list_commits(limit: int = 20) -> JSONResponse:
    """Recent commits: sha, message, author, date."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 100))
    try:
        async with GitHubClient() as gh:
            raw = await gh.get(f"/repos/{owner}/{repo}/commits", params={"per_page": limit})
        items = [
            {
                "sha": c.get("sha", "")[:7],
                "full_sha": c.get("sha", ""),
                "message": (c.get("commit") or {}).get("message", "").split("\n")[0][:160],
                "author": (c.get("author") or {}).get("login") or ((c.get("commit") or {}).get("author") or {}).get("name", ""),
                "date": ((c.get("commit") or {}).get("author") or {}).get("date", ""),
                "html_url": c.get("html_url"),
            }
            for c in (raw or [])
        ]
        return JSONResponse({"commits": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/commits] failed: %s", exc)
        return _gh_error_response(exc)


@router.get("/workflows")
async def list_workflows(limit: int = 10) -> JSONResponse:
    """Recent workflow runs (history, not just the latest)."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 30))
    try:
        async with GitHubClient() as gh:
            runs = await gh.paginate(
                f"/repos/{owner}/{repo}/actions/runs",
                params={"per_page": limit},
                key="workflow_runs",
                max_pages=1,
            )
        items = [
            {
                "id": r.get("id"),
                "name": r.get("name", ""),
                "status": r.get("status", ""),
                "conclusion": r.get("conclusion"),
                "event": r.get("event", ""),
                "branch": (r.get("head_branch") or ""),
                "html_url": r.get("html_url"),
                "created_at": r.get("created_at"),
            }
            for r in (runs or [])[:limit]
        ]
        return JSONResponse({"workflows": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/workflows] failed: %s", exc)
        return _gh_error_response(exc)


@router.get("/branches")
async def list_branches(limit: int = 30) -> JSONResponse:
    """Repository branches."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 100))
    try:
        async with GitHubClient() as gh:
            raw = await gh.get(f"/repos/{owner}/{repo}/branches", params={"per_page": limit})
        items = [
            {
                "name": b.get("name"),
                "protected": b.get("protected", False),
                "sha": (b.get("commit") or {}).get("sha", "")[:7],
            }
            for b in (raw or [])
        ]
        return JSONResponse({"branches": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/branches] failed: %s", exc)
        return _gh_error_response(exc)


@router.get("/releases")
async def list_releases(limit: int = 10) -> JSONResponse:
    """Published releases + draft releases (for collaborators)."""
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 50))
    try:
        async with GitHubClient() as gh:
            raw = await gh.get(f"/repos/{owner}/{repo}/releases", params={"per_page": limit})
        items = [
            {
                "id": r.get("id"),
                "tag": r.get("tag_name", ""),
                "name": r.get("name", ""),
                "prerelease": r.get("prerelease", False),
                "draft": r.get("draft", False),
                "html_url": r.get("html_url"),
                "published_at": r.get("published_at"),
            }
            for r in (raw or [])
        ]
        return JSONResponse({"releases": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/releases] failed: %s", exc)
        return _gh_error_response(exc)


def create_github_router() -> APIRouter:
    """Helper method to return the APIRouter instance."""
    return router
