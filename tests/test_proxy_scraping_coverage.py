"""Proxy Provider coverage: Playwright dict, sync client, discover path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_playwright_proxy_none_when_unconfigured():
    from kazma_core.proxy.client import playwright_proxy

    with patch(
        "kazma_core.proxy.client.get_active_proxy_url", return_value=None
    ):
        assert playwright_proxy() is None


def test_playwright_proxy_parses_url_with_auth():
    from kazma_core.proxy.client import playwright_proxy

    raw = "http://user_ABC_type_residential:s3cret@portal.anyip.io:1080"
    with patch("kazma_core.proxy.client.get_active_proxy_url", return_value=raw):
        d = playwright_proxy()
    assert d is not None
    assert d["server"] == "http://portal.anyip.io:1080"
    assert d["username"] == "user_ABC_type_residential"
    assert d["password"] == "s3cret"


def test_playwright_proxy_no_auth():
    from kazma_core.proxy.client import playwright_proxy

    with patch(
        "kazma_core.proxy.client.get_active_proxy_url",
        return_value="http://127.0.0.1:8888",
    ):
        d = playwright_proxy()
    assert d == {"server": "http://127.0.0.1:8888"}


def test_get_scraping_client_injects_proxy():
    from kazma_core.proxy import client as pc

    with patch.object(
        pc, "get_active_proxy_url", return_value="http://u:p@proxy.example:1080"
    ):
        kw = pc._client_common_kwargs(timeout=5.0)
        assert kw.get("proxy") == "http://u:p@proxy.example:1080"
        c = pc.get_scraping_client(timeout=5.0)
        assert c is not None
        import asyncio

        asyncio.run(c.aclose())


def test_brightdata_oxylabs_unconfigured_are_direct():
    from kazma_core.proxy.brightdata import BrightDataProvider
    from kazma_core.proxy.oxylabs import OxylabsProvider
    from kazma_core.proxy.registry import list_provider_names

    names = list_provider_names()
    assert "brightdata" in names and "oxylabs" in names
    with patch(
        "kazma_core.config_store.get_config_store",
        side_effect=RuntimeError("no store"),
    ):
        assert BrightDataProvider().get_proxy_url() is None
        assert OxylabsProvider().get_proxy_url() is None
        assert BrightDataProvider().is_configured() is False


def test_get_scraping_client_sync_proxy():
    from kazma_core.proxy import client as pc

    with patch.object(pc, "get_active_proxy_url", return_value="http://proxy.test:9999"):
        with pc.get_scraping_client_sync(timeout=3.0) as c:
            assert c is not None
        kw = pc._client_common_kwargs()
        assert kw["proxy"] == "http://proxy.test:9999"


def test_get_scraping_client_direct_when_none():
    from kazma_core.proxy import client as pc

    with patch.object(pc, "get_active_proxy_url", return_value=None):
        kw = pc._client_common_kwargs()
        assert "proxy" not in kw


@pytest.mark.asyncio
async def test_kb_http_get_text_uses_scraping_client(monkeypatch):
    """KB sitemap discover must not use raw httpx when proxy helpers exist."""
    import kazma_core.stores.knowledge_ingest as ki

    calls: list[str] = []

    class _FakeResp:
        status_code = 200
        content = b"<urlset></urlset>"
        text = "<urlset></urlset>"
        url = "https://example.com/sitemap.xml"

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        async def get(self, url):
            calls.append(url)
            return _FakeResp()

    def fake_gsc(**kw):
        calls.append("get_scraping_client")
        return _FakeClient()

    monkeypatch.setattr(
        "kazma_core.proxy.client.get_scraping_client", fake_gsc
    )
    # Also patch at use site if imported at call time — knowledge_ingest
    # imports inside the function.
    text, final = await ki._http_get_text("https://example.com/sitemap.xml")
    assert "get_scraping_client" in calls
    assert text is not None
    assert "example.com" in final


def test_exports():
    from kazma_core.proxy import (
        get_active_proxy_url,
        get_scraping_client_sync,
        playwright_proxy,
    )

    assert callable(playwright_proxy)
    assert callable(get_scraping_client_sync)
    assert callable(get_active_proxy_url)
