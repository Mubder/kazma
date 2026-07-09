"""Shared GitHub API client — single source of truth for auth, repo
resolution, HTTP calls, pagination, and GraphQL.

Used by the ``github`` router (``/api/github/*``) and the
``git_github_manager`` native skill so they share one token-resolution
path and one set of rate-limit/error handling.

Token resolution order:
    1. ``ConfigStore`` key ``connectors.github.oauth_token`` (OAuth flow)
    2. ``ConfigStore`` key ``connectors.github.token`` (legacy PAT)
    3. ``$GITHUB_TOKEN`` environment variable
    4. ``$GITHUB_PAT`` environment variable
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API_BASE = "https://api.github.com"
_GRAPHQL_URL = "https://api.github.com/graphql"
_DEFAULT_TIMEOUT = httpx.Timeout(15.0, connect=10.0)
_USER_AGENT = "Kazma-Agent-Framework"


class GitHubError(Exception):
    """Raised when a GitHub API call fails.

    Attributes:
        status_code: HTTP status from GitHub (0 for transport errors).
        message: Human-readable detail.
        rate_limited: True if this was a primary rate-limit (403 + remaining 0).
        rate_limit_reset: Epoch seconds when the limit resets (or None).
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        *,
        rate_limited: bool = False,
        rate_limit_reset: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.rate_limited = rate_limited
        self.rate_limit_reset = rate_limit_reset


# ── Token + repo resolution (shared helpers) ──────────────────────────


def get_github_token() -> str:
    """Return the configured GitHub token (OAuth → PAT → env). Empty if unset."""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        # OAuth token (from the OAuth flow) takes precedence.
        oauth = store.get("connectors.github.oauth_token", "")
        if oauth:
            return oauth
        token = store.get("connectors.github.token", "")
        if token:
            return token
    except Exception:
        logger.debug("[github] ConfigStore token lookup failed", exc_info=True)

    return os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_PAT", "")


def parse_github_slug(url: str) -> tuple[str, str] | None:
    """Parse a GitHub remote URL (HTTPS or SSH) into an (owner, repo) tuple."""
    url = (url or "").strip()
    if not url:
        return None
    pattern = r"(?:https://github\.com/|git@github\.com:)([^/]+)/([^/\.]+?)(?:\.git)?$"
    match = re.search(pattern, url)
    if match:
        return match.group(1), match.group(2)
    return None


def get_active_cwd() -> str:
    """Resolve the workspace directory to run git / read .git from.

    Order: active workspace ``root_path`` → ConfigStore
    ``workspace.selected_path`` → ``os.getcwd()``.
    """
    try:
        from kazma_core.stores import get_workspace_store

        active = get_workspace_store().get_active_workspace()
        if active and active.get("root_path"):
            return str(Path(str(active["root_path"])).resolve())
    except Exception:
        logger.debug("[github] active-workspace lookup failed", exc_info=True)

    try:
        from kazma_core.config_store import get_config_store

        selected = get_config_store().get("workspace.selected_path", "")
        if selected:
            return str(Path(str(selected)).resolve())
    except Exception:
        logger.debug("[github] workspace.selected_path lookup failed", exc_info=True)

    return str(Path.cwd())


def resolve_repo(cwd: str | None = None) -> tuple[str, str] | None:
    """Resolve the (owner, repo) slug from the workspace's git remote.

    Returns ``None`` if the cwd is not a git repo or has no GitHub remote.
    """
    work_dir = cwd or get_active_cwd()
    if not Path(work_dir, ".git").exists():
        return None
    try:
        res = subprocess.run(
            ["git", "config", "--get", "remote.origin.url"],
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode != 0:
            return None
        return parse_github_slug(res.stdout.strip())
    except Exception as exc:
        logger.debug("[github] failed to resolve repo: %s", exc)
        return None


# ── GitHubClient ──────────────────────────────────────────────────────


class GitHubClient:
    """Async GitHub REST + GraphQL client.

    Use as an async context manager so all calls share one ``httpx`` client::

        async with GitHubClient() as gh:
            repo = await gh.get(f"/repos/{owner}/{repo}")

    Raises :class:`GitHubError` on non-2xx / rate-limit / transport errors.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        self._token = token if token is not None else get_github_token()
        self._timeout = timeout or _DEFAULT_TIMEOUT
        self._client: httpx.AsyncClient | None = None

    # ── lifecycle ───────────────────────────────────────────────────

    @property
    def has_token(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        h = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": _USER_AGENT,
        }
        if self._token:
            # GitHub recommends Bearer for PATs; ``token`` is deprecated.
            h["Authorization"] = f"Bearer {self._token}"
        return h

    async def __aenter__(self) -> GitHubClient:
        self._client = httpx.AsyncClient(
            base_url=_API_BASE, timeout=self._timeout, headers=self._headers()
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("GitHubClient must be used as an async context manager")
        return self._client

    # ── rate-limit + error mapping ──────────────────────────────────

    @staticmethod
    def _is_rate_limited(resp: httpx.Response) -> bool:
        if resp.status_code != 403:
            return False
        return resp.headers.get("X-RateLimit-Remaining", "1") == "0"

    @staticmethod
    def _rate_limit_reset(resp: httpx.Response) -> int | None:
        raw = resp.headers.get("X-RateLimit-Reset", "")
        if raw.isdigit():
            return int(raw)
        return None

    def _raise_for_status(self, resp: httpx.Response, *, method: str, path: str) -> None:
        if resp.status_code < 400:
            return
        rate_limited = self._is_rate_limited(resp)
        reset = self._rate_limit_reset(resp) if rate_limited else None
        try:
            body = resp.json()
            msg = str(body.get("message") or body)[:500]
        except Exception:
            msg = (resp.text or "")[:500] or f"HTTP {resp.status_code}"
        if rate_limited:
            when = f" (resets at {reset})" if reset else ""
            raise GitHubError(
                403, f"GitHub API rate limit exceeded{when}.", rate_limited=True, rate_limit_reset=reset
            )
        raise GitHubError(resp.status_code, f"{method} {path} → {resp.status_code}: {msg}")

    # ── core request ────────────────────────────────────────────────

    async def request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        """Issue a request and return parsed JSON. Raises GitHubError on failure."""
        client = self._require_client()
        try:
            resp = await client.request(method, path, params=params, json=json)
        except httpx.RequestError as exc:
            raise GitHubError(0, f"Transport error calling GitHub: {exc}") from exc
        self._raise_for_status(resp, method=method, path=path)
        if resp.status_code == 204 or not resp.content:
            return None
        return resp.json()

    async def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self.request("POST", path, json=json)

    async def patch(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self.request("PATCH", path, json=json)

    async def put(self, path: str, *, json: dict[str, Any] | None = None) -> Any:
        return await self.request("PUT", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self.request("DELETE", path)

    # ── pagination (Link-header following) ──────────────────────────

    @staticmethod
    def _next_link(link_header: str) -> str | None:
        for part in link_header.split(","):
            segment = part.strip()
            if 'rel="next"' in segment:
                m = re.search(r"<([^>]+)>", segment)
                if m:
                    return m.group(1)
        return None

    async def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        per_page: int = 30,
        max_pages: int = 10,
        key: str | None = None,
    ) -> list[Any]:
        """Walk Link-header pages and accumulate items.

        ``key``: if the JSON response is a dict (e.g. ``{"workflow_runs": [...]}``),
        pull the list from ``response[key]``.

        Bounded by ``max_pages`` so a huge repo can't exhaust the budget.
        """
        client = self._require_client()
        base_params = dict(params or {})
        base_params.setdefault("per_page", per_page)
        items: list[Any] = []
        next_url: str | None = None
        for _ in range(max_pages):
            try:
                if next_url:
                    resp = await client.get(next_url)
                else:
                    resp = await client.get(path, params=base_params)
            except httpx.RequestError as exc:
                raise GitHubError(0, f"Transport error paginating GitHub: {exc}") from exc
            self._raise_for_status(resp, method="GET", path=path)
            data = resp.json()
            chunk = data[key] if key and isinstance(data, dict) else data
            if isinstance(chunk, list):
                items.extend(chunk)
            link = resp.headers.get("Link", "")
            next_url = self._next_link(link)
            if not next_url:
                break
        return items

    # ── GraphQL ─────────────────────────────────────────────────────

    async def graphql(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run a GraphQL query against the GitHub GraphQL endpoint.

        Raises :class:`GitHubError` on transport/HTTP errors or GraphQL ``errors``.
        """
        client = self._require_client()
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        try:
            resp = await client.post(_GRAPHQL_URL, json=payload)
        except httpx.RequestError as exc:
            raise GitHubError(0, f"Transport error in GraphQL call: {exc}") from exc
        self._raise_for_status(resp, method="POST", path="/graphql")
        data = resp.json()
        if data.get("errors"):
            msgs = "; ".join(str(e.get("message", "")) for e in data["errors"])[:500]
            raise GitHubError(200, f"GraphQL errors: {msgs}")
        return data.get("data") or {}


# ── OAuth App helpers ─────────────────────────────────────────────────
#
# Read-only integration via a GitHub OAuth App. The user clicks "Connect
# GitHub", authorizes in their browser, and GitHub redirects back with a
# code that Kazma exchanges for a token. The token is stored ONLY in
# ConfigStore (never written to .env or any repo file).

_OAUTH_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
_OAUTH_TOKEN_URL = "https://github.com/login/oauth/access_token"
# ``repo`` scope grants read of public + private repos (no public-only
# scope exists for OAuth Apps; ``repo`` is the private-read enabler).
_OAUTH_SCOPE = "repo"


def get_oauth_client_id() -> str:
    """Return the OAuth App client id from env."""
    return os.environ.get("GITHUB_OAUTH_CLIENT_ID", "").strip()


def get_oauth_client_secret() -> str:
    """Return the OAuth App client secret from env."""
    return os.environ.get("GITHUB_OAUTH_CLIENT_SECRET", "").strip()


def oauth_configured() -> bool:
    """True if the OAuth client id + secret are present."""
    return bool(get_oauth_client_id() and get_oauth_client_secret())


def build_authorize_url(state: str, redirect_uri: str) -> str:
    """Build the GitHub authorization URL for the browser redirect."""
    from urllib.parse import urlencode

    params = {
        "client_id": get_oauth_client_id(),
        "redirect_uri": redirect_uri,
        "scope": _OAUTH_SCOPE,
        "state": state,
        "allow_signup": "true",
    }
    return f"{_OAUTH_AUTHORIZE_URL}?{urlencode(params)}"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an OAuth ``code`` for an access token.

    Returns the token dict (``access_token``, ``token_type``, ``scope``).
    Raises :class:`GitHubError` on failure.
    """
    payload = {
        "client_id": get_oauth_client_id(),
        "client_secret": get_oauth_client_secret(),
        "code": code,
        "redirect_uri": redirect_uri,
    }
    headers = {"Accept": "application/json", "User-Agent": _USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
            resp = await client.post(_OAUTH_TOKEN_URL, json=payload, headers=headers)
    except httpx.RequestError as exc:
        raise GitHubError(0, f"Transport error during OAuth exchange: {exc}") from exc
    if resp.status_code != 200:
        raise GitHubError(resp.status_code, f"OAuth token exchange failed: HTTP {resp.status_code}")
    data = resp.json()
    if data.get("error"):
        raise GitHubError(400, f"OAuth error: {data.get('error_description') or data['error']}")
    return data


def store_oauth_token(token_data: dict[str, Any]) -> None:
    """Persist the OAuth access token to ConfigStore (never to .env)."""
    access_token = token_data.get("access_token", "")
    if not access_token:
        return
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.batch_set([
            ("connectors.github.oauth_token", access_token, "connectors"),
            ("connectors.github.oauth_scope", token_data.get("scope", ""), "connectors"),
            ("connectors.github.oauth_token_type", token_data.get("token_type", ""), "connectors"),
        ])
    except Exception:
        logger.exception("[github] failed to persist OAuth token")


def clear_oauth_token() -> None:
    """Remove the stored OAuth token (disconnect)."""
    try:
        from kazma_core.config_store import get_config_store

        store = get_config_store()
        store.batch_set([
            ("connectors.github.oauth_token", "", "connectors"),
            ("connectors.github.oauth_scope", "", "connectors"),
            ("connectors.github.oauth_token_type", "", "connectors"),
        ])
    except Exception:
        logger.exception("[github] failed to clear OAuth token")


def is_oauth_connected() -> bool:
    """True if an OAuth access token is stored in ConfigStore."""
    try:
        from kazma_core.config_store import get_config_store

        return bool(get_config_store().get("connectors.github.oauth_token", ""))
    except Exception:
        return False
