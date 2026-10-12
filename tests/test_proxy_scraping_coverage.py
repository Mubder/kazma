"""Proxy Provider coverage: Playwright dict, sync client, discover path."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _explicit_proxy_policy(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "proxy")


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


def test_brightdata_oxylabs_distinguish_unconfigured_from_unavailable():
    from kazma_core.proxy.brightdata import BrightDataProvider
    from kazma_core.proxy.oxylabs import OxylabsProvider
    from kazma_core.proxy.registry import list_provider_names
    from kazma_core.web_acquire.result import AcquisitionError

    names = list_provider_names()
    assert "brightdata" in names and "oxylabs" in names
    with patch(
        "kazma_core.config_store.get_config_store",
        side_effect=RuntimeError("no store"),
    ):
        for provider in (BrightDataProvider(), OxylabsProvider()):
            with pytest.raises(AcquisitionError, match='configuration could not be read'):
                provider.get_proxy_url()
    store = MagicMock()
    store.get.return_value = {}
    with patch('kazma_core.config_store.get_config_store', return_value=store):
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

    import httpx
    from types import SimpleNamespace

    def respond(request):
        calls.append(str(request.url))
        peer = SimpleNamespace(get_extra_info=lambda _: ("93.184.216.34", 443))
        return httpx.Response(200, text="<urlset></urlset>", request=request,
                              extensions={"network_stream": peer})

    def fake_gsc(**kw):
        calls.append("get_scraping_client")
        assert kw["follow_redirects"] is False
        assert kw["pin_hosts"] is not None
        return httpx.AsyncClient(transport=httpx.MockTransport(respond))

    monkeypatch.setattr(
        "kazma_core.proxy.client.get_scraping_client", fake_gsc
    )
    monkeypatch.setattr("kazma_core.proxy.client.get_active_proxy_url", lambda: None)
    monkeypatch.setattr("kazma_core.security.ssrf._resolve_host_ips", lambda _: ["93.184.216.34"])
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
