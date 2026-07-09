"""Unit tests for the shared GitHubClient.

All HTTP is mocked — no real GitHub API calls are made. Covers token
resolution, slug parsing, repo resolution, request/error/rate-limit
mapping, Link-header pagination, and GraphQL.
"""

from __future__ import annotations

import os
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from kazma_gateway.routers.github_client import (
    GitHubClient,
    GitHubError,
    get_github_token,
    parse_github_slug,
)


def _resp(
    status_code: int = 200,
    json_data: object | None = None,
    headers: dict[str, str] | None = None,
    text: str | None = None,
) -> httpx.Response:
    """Build a fake httpx.Response."""
    kwargs: dict[str, Any] = {"headers": headers or {}, "request": httpx.Request("GET", "https://api.github.com/test")}
    if json_data is not None:
        kwargs["json"] = json_data
    elif text is not None:
        kwargs["text"] = text
    return httpx.Response(status_code, **kwargs)
    return r


class TestSlugParsing(unittest.TestCase):
    def test_https(self):
        self.assertEqual(parse_github_slug("https://github.com/Mubder/kazma.git"), ("Mubder", "kazma"))

    def test_https_no_git_suffix(self):
        self.assertEqual(parse_github_slug("https://github.com/Mubder/kazma"), ("Mubder", "kazma"))

    def test_ssh(self):
        self.assertEqual(parse_github_slug("git@github.com:Mubder/kazma.git"), ("Mubder", "kazma"))

    def test_non_github(self):
        self.assertIsNone(parse_github_slug("https://gitlab.com/foo/bar.git"))

    def test_empty(self):
        self.assertIsNone(parse_github_slug(""))
        self.assertIsNone(parse_github_slug("   "))


class TestTokenResolution(unittest.TestCase):
    def test_config_store_first(self):
        with patch("kazma_core.config_store.get_config_store") as mock_cs:
            mock_cs.return_value.get.return_value = "config-token"
            self.assertEqual(get_github_token(), "config-token")

    def test_env_fallback(self):
        with patch("kazma_core.config_store.get_config_store") as mock_cs:
            mock_cs.return_value.get.return_value = ""
            with patch.dict(os.environ, {"GITHUB_TOKEN": "env-token"}, clear=False):
                self.assertEqual(get_github_token(), "env-token")

    def test_pat_env_last_resort(self):
        with patch("kazma_core.config_store.get_config_store") as mock_cs:
            mock_cs.return_value.get.return_value = ""
            env = {"GITHUB_TOKEN": "", "GITHUB_PAT": "pat-token"}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(get_github_token(), "pat-token")

    def test_empty_when_nothing_set(self):
        with patch("kazma_core.config_store.get_config_store") as mock_cs:
            mock_cs.return_value.get.return_value = ""
            env = {"GITHUB_TOKEN": "", "GITHUB_PAT": ""}
            with patch.dict(os.environ, env, clear=False):
                self.assertEqual(get_github_token(), "")


class TestGitHubClientRequests(unittest.IsolatedAsyncioTestCase):
    async def test_get_returns_json(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_resp(200, {"login": "Mubder"}))
        client._client = mock_http
        data = await client.get("/user")
        self.assertEqual(data, {"login": "Mubder"})

    async def test_post_returns_json(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_resp(201, {"number": 7}))
        client._client = mock_http
        data = await client.post("/repos/o/r/pulls", json={"title": "x"})
        self.assertEqual(data, {"number": 7})

    async def test_delete_returns_none_on_204(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(return_value=_resp(204))
        client._client = mock_http
        self.assertIsNone(await client.delete("/repos/o/r/releases/1"))

    async def test_error_on_404(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_resp(404, {"message": "Not Found"})
        )
        client._client = mock_http
        with self.assertRaises(GitHubError) as ctx:
            await client.get("/repos/o/missing")
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertFalse(ctx.exception.rate_limited)

    async def test_rate_limit_403(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(
            return_value=_resp(
                403,
                {"message": "rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
            )
        )
        client._client = mock_http
        with self.assertRaises(GitHubError) as ctx:
            await client.get("/repos/o/r")
        self.assertTrue(ctx.exception.rate_limited)
        self.assertEqual(ctx.exception.rate_limit_reset, 1700000000)

    async def test_transport_error(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.request = AsyncMock(side_effect=httpx.ConnectError("boom"))
        client._client = mock_http
        with self.assertRaises(GitHubError) as ctx:
            await client.get("/user")
        self.assertEqual(ctx.exception.status_code, 0)

    async def test_context_manager_opens_and_closes(self):
        with patch("kazma_gateway.routers.github_client.httpx.AsyncClient") as mock_cls:
            mock_instance = AsyncMock()
            mock_cls.return_value = mock_instance
            async with GitHubClient(token="t") as gh:
                self.assertIsNotNone(gh._client)
            mock_instance.aclose.assert_awaited_once()

    async def test_used_without_context_raises(self):
        gh = GitHubClient(token="t")
        with self.assertRaises(RuntimeError):
            await gh.get("/user")


class TestPagination(unittest.IsolatedAsyncioTestCase):
    async def test_walks_link_header(self):
        client = GitHubClient(token="t")
        page1 = _resp(
            200, [{"n": 1}, {"n": 2}],
            headers={"Link": '<https://api.github.com/p2>; rel="next"'},
        )
        page2 = _resp(200, [{"n": 3}])  # no Link → last page
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[page1, page2])
        client._client = mock_http
        items = await client.paginate("/items", per_page=2, max_pages=5)
        self.assertEqual([i["n"] for i in items], [1, 2, 3])

    async def test_respects_max_pages(self):
        client = GitHubClient(token="t")
        # Always returns a next link — must stop at max_pages.
        page = _resp(200, [{"n": 1}], headers={"Link": '<https://api.github.com/p>; rel="next"'})
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=page)
        client._client = mock_http
        items = await client.paginate("/items", max_pages=3)
        self.assertEqual(mock_http.get.await_count, 3)
        self.assertEqual(len(items), 3)

    async def test_dict_key_unwrap(self):
        client = GitHubClient(token="t")
        resp = _resp(200, {"workflow_runs": [{"id": 1}]})
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=resp)
        client._client = mock_http
        items = await client.paginate("/actions/runs", key="workflow_runs")
        self.assertEqual(items, [{"id": 1}])


class TestGraphQL(unittest.IsolatedAsyncioTestCase):
    async def test_graphql_success(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_resp(200, {"data": {"viewer": {"login": "Mubder"}}})
        )
        client._client = mock_http
        data = await client.graphql("query { viewer { login } }")
        self.assertEqual(data, {"viewer": {"login": "Mubder"}})

    async def test_graphql_errors_raised(self):
        client = GitHubClient(token="t")
        mock_http = AsyncMock()
        mock_http.post = AsyncMock(
            return_value=_resp(200, {"data": None, "errors": [{"message": "bad query"}]})
        )
        client._client = mock_http
        with self.assertRaises(GitHubError) as ctx:
            await client.graphql("query { bad }")
        self.assertIn("bad query", ctx.exception.message)


if __name__ == "__main__":
    unittest.main()
