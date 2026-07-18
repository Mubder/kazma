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

__all__ = [
    "CloneRepoRequest",
    "TokenSaveRequest",
    "create_github_router",
    "parse_github_slug",
    "save_github_token_to_env",
]

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

    # 3. Shared token resolution (OAuth → PAT ConfigStore → env).
    # Must match GitHubClient / get_github_token() — the old path only read
    # connectors.github.token and ignored OAuth, which caused "Token Missing"
    # + unauthenticated 60/hr rate limits while the user was OAuth-connected.
    from kazma_gateway.routers.github_client import get_github_token, is_oauth_connected

    token = get_github_token()
    oauth_connected = is_oauth_connected()

    # 4. Fetch details from GitHub API
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Kazma-Agent-Framework",
    }
    if token:
        # Bearer works for OAuth + classic/fine-grained PATs.
        headers["Authorization"] = f"Bearer {token}"

    api_url = f"https://api.github.com/repos/{owner}/{repo}"
    pulls_url = f"https://api.github.com/repos/{owner}/{repo}/pulls?state=open&per_page=1"
    workflows_url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs?per_page=1"

    def _base_payload(**extra: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "is_github": True,
            "owner": owner,
            "repo": repo,
            "has_token": bool(token),
            "oauth_connected": oauth_connected,
        }
        payload.update(extra)
        return payload

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            # Fetch repo metadata
            repo_resp = await client.get(api_url, headers=headers)
            if repo_resp.status_code == 401:
                return JSONResponse(_base_payload(
                    token_valid=False,
                    error="Invalid GitHub Token (401 Unauthorized).",
                ))
            elif repo_resp.status_code == 403:
                # Check rate limit
                rate_limit_remaining = repo_resp.headers.get("X-RateLimit-Remaining", "")
                if rate_limit_remaining == "0":
                    return JSONResponse(_base_payload(
                        token_valid=bool(token),
                        rate_limited=True,
                        error=(
                            "GitHub API rate limit exceeded."
                            if token
                            else "GitHub API rate limit exceeded (unauthenticated 60/hr). "
                            "Connect GitHub OAuth or save a Personal Access Token."
                        ),
                    ))
                else:
                    return JSONResponse(_base_payload(
                        token_valid=bool(token),
                        error="Access Forbidden (403). For private repositories, ensure your token has the correct scopes.",
                    ))
            elif repo_resp.status_code == 404:
                return JSONResponse(_base_payload(
                    token_valid=bool(token),
                    error=(
                        "Repository not found (404). It may be private — configure a token with access, "
                        "or the remote owner/repo may be wrong."
                        if not token
                        else "Repository not found (404). Check remote URL and token scopes."
                    ),
                ))
            elif repo_resp.status_code != 200:
                return JSONResponse(_base_payload(
                    token_valid=bool(token),
                    error=f"Failed to retrieve repository details. HTTP {repo_resp.status_code}",
                ))

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

            return JSONResponse(_base_payload(
                token_valid=True,
                private=repo_data.get("private", False),
                stars=repo_data.get("stargazers_count", 0),
                forks=repo_data.get("forks_count", 0),
                open_issues=net_issues,
                open_prs=open_prs_count,
                description=repo_data.get("description", ""),
                html_url=repo_data.get("html_url", ""),
                latest_workflow=latest_run,
            ))

        except httpx.RequestError as exc:
            logger.error("[github/status] Connection error: %s", exc)
            return JSONResponse(_base_payload(
                error="Failed to reach GitHub API.",
            ))


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
    """Report whether the OAuth App is configured and any usable token exists.

    ``connected`` remains OAuth-specific (Disconnect button). ``has_token`` is
    true for OAuth *or* PAT/env so the Workspace UI can show telemetry after a
    PAT-only setup without requiring the OAuth flow.
    """
    from kazma_gateway.routers.github_client import (
        get_github_token,
        is_oauth_connected,
        oauth_configured,
    )

    return JSONResponse({
        "configured": oauth_configured(),
        "connected": is_oauth_connected(),
        "has_token": bool(get_github_token()),
    })


@router.get("/oauth/start", response_model=None)
async def oauth_start(request: Request) -> RedirectResponse | JSONResponse:
    """Redirect the user to GitHub's authorization page."""
    from kazma_gateway.routers.github_client import build_authorize_url, oauth_configured

    if not oauth_configured():
        # Browser tab expects HTML, not raw JSON (window.open from Workspace).
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept or "Kazma-Soft-Nav" not in request.headers:
            return _oauth_setup_page(request)
        return JSONResponse(
            {
                "error": "GitHub OAuth App is not configured",
                "hint": "Set GITHUB_OAUTH_CLIENT_ID and GITHUB_OAUTH_CLIENT_SECRET in .env, "
                "or paste a Personal Access Token on the Workspace page.",
            },
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


def _oauth_setup_page(request: Request) -> HTMLResponseType:
    """HTML guide when OAuth App env vars are missing (browser-friendly)."""
    from fastapi.responses import HTMLResponse

    host = request.headers.get("host") or "127.0.0.1:9090"
    scheme = request.url.scheme or "http"
    callback = f"{scheme}://{host}/api/github/oauth/callback"
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>GitHub OAuth setup</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #0f1117; color: #e5e7eb;
         display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 24px; }}
  .card {{ padding: 28px 32px; border-radius: 12px; background: #1a1d27; border: 1px solid #2a2f3e;
           max-width: 560px; line-height: 1.5; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 12px; color: #fbbf24; }}
  code {{ background: #0f1117; padding: 2px 6px; border-radius: 4px; font-size: 0.85rem; color: #67e8f9; }}
  ol {{ margin: 12px 0; padding-left: 1.25rem; color: #cbd5e1; font-size: 0.9rem; }}
  li {{ margin-bottom: 8px; }}
  .cb {{ display: block; margin: 10px 0 16px; padding: 10px 12px; background: #0f1117;
         border: 1px solid #2a2f3e; border-radius: 8px; word-break: break-all; font-family: ui-monospace, monospace;
         font-size: 0.8rem; color: #a5f3fc; }}
  a.btn {{ display: inline-block; margin-top: 8px; margin-right: 8px; padding: 8px 14px; border-radius: 8px;
           background: #22d3ee; color: #0a0f14; font-weight: 600; text-decoration: none; font-size: 0.88rem; }}
  a.btn2 {{ background: transparent; color: #94a3b8; border: 1px solid #334155; }}
  .alt {{ margin-top: 18px; padding-top: 14px; border-top: 1px solid #2a2f3e; font-size: 0.88rem; color: #94a3b8; }}
</style></head>
<body><div class="card">
  <h1>GitHub OAuth App is not configured</h1>
  <p style="margin:0 0 8px;font-size:0.9rem;color:#cbd5e1">
    Create a free GitHub OAuth App, put the credentials in <code>.env</code>, restart Kazma, then try again.
  </p>
  <ol>
    <li>Open <a href="https://github.com/settings/developers" style="color:#67e8f9" target="_blank" rel="noopener">GitHub → Developer settings → OAuth Apps</a> → <strong>New OAuth App</strong>.</li>
    <li>Application name: <code>Kazma</code> (any name).</li>
    <li>Homepage URL: <code>{scheme}://{host}/</code></li>
    <li><strong>Authorization callback URL</strong> (must match exactly):</li>
  </ol>
  <code class="cb">{callback}</code>
  <ol start="5">
    <li>Create the app → copy <strong>Client ID</strong>.</li>
    <li>Generate a new <strong>Client secret</strong> and copy it.</li>
    <li>Add to your Kazma <code>.env</code> file:</li>
  </ol>
  <code class="cb">GITHUB_OAUTH_CLIENT_ID=Iv1.xxxxxxxx<br>GITHUB_OAUTH_CLIENT_SECRET=xxxxxxxxxxxxxxxx</code>
  <ol start="8">
    <li>Restart the Kazma server, then click <strong>Connect GitHub</strong> again.</li>
  </ol>
  <div class="alt">
    <strong>Faster alternative:</strong> on the Workspace page use a
    <a href="https://github.com/settings/tokens" style="color:#67e8f9" target="_blank" rel="noopener">Personal Access Token</a>
    (classic, scope <code>repo</code>) via the PAT field — no OAuth App required.
  </div>
  <a class="btn" href="/workspace">Back to Workspace</a>
  <a class="btn btn2" href="https://github.com/settings/developers" target="_blank" rel="noopener">Open GitHub OAuth Apps</a>
</div></body></html>"""
    return HTMLResponse(html)


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


# ── Repo picker (list + clone) ───────────────────────────────────────


@router.get("/repos")
async def list_user_repos(limit: int = 50) -> JSONResponse:
    """List the authenticated user's repos for the picker dropdown."""
    from kazma_gateway.routers.github_client import GitHubClient

    try:
        async with GitHubClient() as gh:
            raw = await gh.get("/user/repos", params={
                "affiliation": "owner,collaborator",
                "sort": "updated",
                "per_page": max(1, min(limit, 100)),
            })
        items = [
            {
                "full_name": r.get("full_name", ""),
                "name": r.get("name", ""),
                "private": r.get("private", False),
                "default_branch": r.get("default_branch", "main"),
                "html_url": r.get("html_url", ""),
                "clone_url": r.get("clone_url", ""),
                "ssh_url": r.get("ssh_url", ""),
                "updated_at": r.get("updated_at", ""),
            }
            for r in (raw or [])
        ]
        return JSONResponse({"repos": items, "count": len(items)})
    except Exception as exc:
        logger.warning("[github/repos] failed: %s", exc)
        return _gh_error_response(exc)


class CloneRepoRequest(BaseModel):
    full_name: str
    use_ssh: bool = False
    clone_url: str = ""
    ssh_url: str = ""


@router.post("/repos/clone", status_code=201)
async def clone_repo(body: CloneRepoRequest) -> JSONResponse:
    """Clone a GitHub repo and activate it as the workspace.

    If the repo is already open locally (a workspace root with a matching
    remote), just activates that workspace. Otherwise clones into
    ``$KAZMA_CLONE_DIR`` (default ``~/kazma-repos``) and registers it.
    """
    import subprocess
    from kazma_gateway.routers.github_client import GitHubClient

    full_name = body.full_name.strip()
    if not full_name or "/" not in full_name:
        return JSONResponse({"error": "Invalid full_name (expected 'owner/repo')."}, status_code=422)

    # Resolve the clone URL.
    if body.clone_url and not body.use_ssh:
        url = body.clone_url
    elif body.use_ssh and body.ssh_url:
        url = body.ssh_url
    else:
        # Fetch from the API if not provided.
        try:
            async with GitHubClient() as gh:
                repo_info = await gh.get(f"/repos/{full_name}")
            url = (repo_info.get("ssh_url") if body.use_ssh else repo_info.get("clone_url")) or ""
        except Exception as exc:
            return JSONResponse({"error": f"Could not resolve clone URL: {exc}"}, status_code=502)
    if not url:
        return JSONResponse({"error": "Could not determine clone URL."}, status_code=422)

    # Check if already open locally — match by remote.origin.url.
    from kazma_core.stores import get_workspace_store

    store = get_workspace_store()
    try:
        for ws in store.list_workspaces():
            root = str(ws.get("root_path", ""))
            if not Path(root).joinpath(".git").exists():
                continue
            res = subprocess.run(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=root, capture_output=True, text=True, timeout=5,
            )
            if res.returncode == 0 and full_name in res.stdout:
                store.set_active_workspace(ws["id"])
                return JSONResponse({"status": "ok", "message": "Already open locally.", "path": root, "workspace_id": ws["id"]}, status_code=200)
    except Exception as exc:
        logger.debug("[github/repos/clone] local-check failed: %s", exc)

    # Clone into the base dir.
    base_dir = os.environ.get("KAZMA_CLONE_DIR", "").strip() or str(Path.home() / "kazma-repos")
    Path(base_dir).mkdir(parents=True, exist_ok=True)
    repo_dir = Path(base_dir) / full_name.split("/")[-1]
    if repo_dir.exists():
        # Append a counter to avoid clobbering an existing dir.
        i = 1
        while Path(f"{repo_dir}-{i}").exists():
            i += 1
        repo_dir = Path(f"{repo_dir}-{i}")

    try:
        subprocess.run(["git", "clone", "--depth", "1", url, str(repo_dir)], check=True, capture_output=True, text=True, timeout=120)
    except subprocess.CalledProcessError as exc:
        return JSONResponse({"error": f"git clone failed: {(exc.stderr or '')[:300]}"}, status_code=502)
    except subprocess.TimeoutExpired:
        return JSONResponse({"error": "git clone timed out."}, status_code=504)
    except Exception as exc:
        return JSONResponse({"error": f"git clone failed: {exc}"}, status_code=502)

    # Register + activate the cloned repo as a workspace.
    from kazma_core.config_store import get_config_store

    name = full_name.split("/")[-1]
    record = store.create_workspace(name, str(repo_dir))
    store.set_active_workspace(record["id"])
    cs = get_config_store()
    cs.set("workspace.selected_path", str(repo_dir), category="workspace")
    try:
        cs.reload_from_root(str(repo_dir))
    except Exception:
        pass

    # Persist the repo identity so it doesn't have to be re-derived from
    # `git remote` on every call (Phase 2). full_name is "owner/repo".
    try:
        _owner, _repo = full_name.split("/", 1)
        store.set_repo_identity(
            str(repo_dir),
            repo_url=url,
            owner=_owner,
            repo=_repo,
            default_branch="main",
            is_github=True,
        )
    except Exception:
        logger.debug("[github/repos/clone] repo identity cache failed", exc_info=True)

    logger.info("[github/repos/clone] cloned %s → %s", full_name, repo_dir)
    return JSONResponse({"status": "ok", "path": str(repo_dir), "workspace_id": record["id"]}, status_code=201)


# ── Activity timeline (GraphQL, single call) ─────────────────────────


@router.get("/activity")
async def repo_activity(limit: int = 30) -> JSONResponse:
    """Unified recent-activity feed: commits + PRs + issues + CI runs.

    Uses a single GraphQL query (1 API call) to avoid the rate-limit cost
    of merging 4 REST endpoints. Falls back to a small REST merge on error.
    """
    from kazma_gateway.routers.github_client import GitHubClient

    owner_repo = await _resolve_owner_repo()
    if isinstance(owner_repo, JSONResponse):
        return owner_repo
    owner, repo = owner_repo
    limit = max(1, min(limit, 50))
    # Per-type cap so high-volume types (commits/CI) don't crowd out PRs/
    # issues after the merge+sort+truncate. Each type gets up to `per_type`.
    per_type = max(5, limit // 4)

    # GraphQL: fetch recent commits, PRs, and issues in one round-trip.
    query = """
    query($owner: String!, $repo: String!, $lim: Int!) {
      repository(owner: $owner, name: $repo) {
        defaultBranchRef { target { ... on Commit { history(first: $lim) {
          nodes { oid messageHeadline author { user { login } name } committedDate url } } } } }
        pullRequests(first: $lim, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { number title author { login } updatedAt url state } }
        issues(first: $lim, orderBy: {field: UPDATED_AT, direction: DESC}) {
          nodes { number title author { login } updatedAt url state } }
      }
    }"""
    try:
        async with GitHubClient() as gh:
            data = await gh.graphql(query, {"owner": owner, "repo": repo, "lim": per_type})
        items: list[dict[str, Any]] = []
        repo_node = data.get("repository") or {}

        # Commits (capped per-type)
        for c in (((((repo_node.get("defaultBranchRef") or {}).get("target") or {}).get("history") or {}).get("nodes")) or [])[:per_type]:
            author = c.get("author") or {}
            items.append({
                "type": "commit", "descriptor": (c.get("oid", "") or "")[:7] + " " + (c.get("messageHeadline") or "")[:100],
                "actor": (author.get("user") or {}).get("login") or author.get("name") or "",
                "timestamp": c.get("committedDate") or "", "html_url": c.get("url") or "",
            })
        # PRs
        for p in (((repo_node.get("pullRequests") or {}).get("nodes")) or [])[:per_type]:
            items.append({
                "type": "pr", "descriptor": f"#{p.get('number')} {p.get('title', '')[:100]}",
                "actor": (p.get("author") or {}).get("login") or "", "timestamp": p.get("updatedAt") or "",
                "html_url": p.get("url") or "", "state": p.get("state") or "",
            })
        # Issues
        for i in (((repo_node.get("issues") or {}).get("nodes")) or [])[:per_type]:
            items.append({
                "type": "issue", "descriptor": f"#{i.get('number')} {i.get('title', '')[:100]}",
                "actor": (i.get("author") or {}).get("login") or "", "timestamp": i.get("updatedAt") or "",
                "html_url": i.get("url") or "", "state": i.get("state") or "",
            })

        # CI runs — separate REST call (actions has no GraphQL equivalent
        # in the public schema); bounded per-type so CI doesn't dominate.
        try:
            async with GitHubClient() as gh:
                runs = await gh.get(f"/repos/{owner}/{repo}/actions/runs", params={"per_page": per_type})
            for r in ((runs or {}).get("workflow_runs") or [])[:per_type]:
                items.append({
                    "type": "ci", "descriptor": f"{r.get('name', 'workflow')} ({r.get('conclusion') or r.get('status', '')})",
                    "actor": (r.get("actor") or {}).get("login", ""),
                    "timestamp": r.get("created_at") or "", "html_url": r.get("html_url") or "",
                })
        except Exception:
            pass  # CI is best-effort; don't fail the whole feed.

        items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
        return JSONResponse({"activity": items[:limit], "count": len(items[:limit])})
    except Exception as exc:
        logger.warning("[github/activity] GraphQL failed: %s — trying REST fallback", exc)
        return await _activity_rest_fallback(owner, repo, limit)


async def _activity_rest_fallback(owner: str, repo: str, limit: int) -> JSONResponse:
    """Merge small slices of commits/pulls/issues/workflows via REST."""
    from kazma_gateway.routers.github_client import GitHubClient

    items: list[dict[str, Any]] = []
    try:
        async with GitHubClient() as gh:
            commits = await gh.get(f"/repos/{owner}/{repo}/commits", params={"per_page": min(limit, 10)})
            pulls = await gh.get(f"/repos/{owner}/{repo}/pulls", params={"state": "all", "per_page": min(limit, 10)})
            issues = await gh.get(f"/repos/{owner}/{repo}/issues", params={"state": "all", "per_page": min(limit, 10)})
        for c in (commits or []):
            items.append({"type": "commit", "descriptor": (c.get("sha") or "")[:7] + " " + ((c.get("commit") or {}).get("message") or "").split("\n")[0][:100], "actor": (c.get("author") or {}).get("login", ""), "timestamp": ((c.get("commit") or {}).get("author") or {}).get("date", ""), "html_url": c.get("html_url", "")})
        for p in (pulls or []):
            items.append({"type": "pr", "descriptor": f"#{p.get('number')} {p.get('title', '')[:100]}", "actor": (p.get("user") or {}).get("login", ""), "timestamp": p.get("updated_at", ""), "html_url": p.get("html_url", "")})
        for i in (issues or []):
            if "pull_request" in i:
                continue
            items.append({"type": "issue", "descriptor": f"#{i.get('number')} {i.get('title', '')[:100]}", "actor": (i.get("user") or {}).get("login", ""), "timestamp": i.get("updated_at", ""), "html_url": i.get("html_url", "")})
    except Exception as exc:
        return _gh_error_response(exc)
    items.sort(key=lambda x: x.get("timestamp") or "", reverse=True)
    return JSONResponse({"activity": items[:limit], "count": len(items[:limit])})


def create_github_router() -> APIRouter:
    """Helper method to return the APIRouter instance."""
    return router
