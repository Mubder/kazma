"""No hidden hosted fallback or proxy route, even with legacy credentials."""

from __future__ import annotations

import importlib
from unittest.mock import AsyncMock, Mock

import pytest

ru = importlib.import_module("kazma_core.tools.read_url")
ki = importlib.import_module("kazma_core.stores.knowledge_ingest")


@pytest.fixture(autouse=True)
def local_policy(monkeypatch):
    for name in ("KAZMA_ACQUISITION_MODE", "KAZMA_ACQUISITION_REMOTE_PROVIDERS",
                 "KAZMA_FETCH_BACKEND", "KAZMA_JINA_READER", "KAZMA_KB_JINA_FALLBACK"):
        monkeypatch.delenv(name, raising=False)
    ru.clear_url_cache()
    yield
    ru.clear_url_cache()


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy_backend", ["auto", "jina", "firecrawl", "httpx"])
async def test_default_recovery_never_calls_hosted_services(monkeypatch, legacy_backend):
    monkeypatch.setenv("KAZMA_FETCH_BACKEND", legacy_backend)
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "old-key")
    monkeypatch.setenv("KAZMA_JINA_READER", "1")
    monkeypatch.setenv("KAZMA_KB_JINA_FALLBACK", "1")
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ru, "_try_firecrawl", fire)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    monkeypatch.setattr(ru, "_fetch_with_playwright", AsyncMock(return_value=None))
    assert await ru._fetch_via_optional_backends("https://example.com/page") is None
    assert await ru._recover_hard_page("https://example.com/page") is None
    assert ki._kb_jina_fallback_allowed() is False
    fire.assert_not_awaited()
    jina.assert_not_awaited()


@pytest.mark.asyncio
async def test_direct_provider_helpers_refuse_before_client_creation(monkeypatch):
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "old-key")
    client = Mock(side_effect=AssertionError("hosted client must not be constructed"))
    monkeypatch.setattr("httpx.AsyncClient", client)
    assert await ru._try_jina_reader("https://example.com/") is None
    assert await ru._try_firecrawl("https://example.com/") is None
    assert await ki._firecrawl_map_site("https://example.com/") is None
    client.assert_not_called()


@pytest.mark.asyncio
async def test_local_browser_success_prevents_allowed_remote_calls(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "jina,firecrawl")
    monkeypatch.setattr(ru, "_fetch_with_playwright", AsyncMock(return_value="local content"))
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ru, "_try_firecrawl", fire)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    assert await ru._recover_hard_page("https://example.com/page") == "local content"
    fire.assert_not_awaited()
    jina.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_mode_still_requires_named_provider(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "jina")
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "old-key")
    monkeypatch.setattr(ru, "_fetch_with_playwright", AsyncMock(return_value=None))
    fire, jina = AsyncMock(), AsyncMock(return_value="authorized result")
    monkeypatch.setattr(ru, "_try_firecrawl", fire)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    assert await ru._recover_hard_page("https://example.com/page") == "authorized result"
    fire.assert_not_awaited()
    jina.assert_awaited_once()


def test_local_only_does_not_resolve_configured_proxy_and_refuses_injection(monkeypatch):
    from kazma_core.proxy import client as pc
    from kazma_core.web_acquire.policy import AcquisitionPolicyError

    provider = Mock(side_effect=AssertionError("no proxy resolution"))
    monkeypatch.setattr(pc, "get_proxy_provider", provider)
    assert pc.get_active_proxy_url() is None
    assert pc.playwright_proxy() is None
    assert pc._client_common_kwargs()["trust_env"] is False
    with pytest.raises(AcquisitionPolicyError, match="proxy refused"):
        pc.get_scraping_client(proxy="http://proxy.example:8080")
    with pytest.raises(AcquisitionPolicyError, match="proxy refused"):
        pc.get_scraping_client_sync(proxy="http://proxy.example:8080")
    provider.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_policy_is_visible_before_network_or_cache(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_onyl")
    network = Mock(side_effect=AssertionError("no network"))
    monkeypatch.setattr("kazma_core.security.ssrf.validate_url", network)
    assert "Error: Invalid KAZMA_ACQUISITION_MODE" in await ru.fetch_full_text("https://example.com/")
    network.assert_not_called()


def test_cache_does_not_cross_policy_boundary(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "jina")
    ru._cache_put("https://example.com/page", "hosted extract")
    assert ru._cache_get("https://example.com/page") == "hosted extract"
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_only")
    assert ru._cache_get("https://example.com/page") is None


def test_readiness_reports_policy_and_unknown_credit_accounting(monkeypatch):
    from kazma_core.tools.research_readiness import research_readiness

    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "stored-key")
    result = research_readiness()
    assert result["acquisition"]["mode"] == "local_only"
    assert result["acquisition"]["credits"] is None
    assert result["acquisition"]["usage"] is None
    checks = {entry["id"]: entry for entry in result["checks"]}
    assert "Firecrawl=off" in checks["hard_page_backends"]["message"]
    assert "Jina recovery=disabled" in checks["hard_page_backends"]["message"]
    assert checks["acquisition_accounting"]["level"] == "warn"


@pytest.mark.asyncio
async def test_local_kb_discovery_precedes_hosted_mapping(monkeypatch):
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "firecrawl,jina")
    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "key")
    urls = ["https://example.com/docs/" + str(i) for i in range(20)]
    monkeypatch.setattr(ki, "_llms_txt_discover", AsyncMock(return_value=[]))
    monkeypatch.setattr(ki, "_fetch_sitemaps", AsyncMock(return_value=[]))
    monkeypatch.setattr(ki, "_bfs_discover", AsyncMock(return_value=urls))
    fire, jina = AsyncMock(), AsyncMock()
    monkeypatch.setattr(ki, "_firecrawl_map_discover", fire)
    monkeypatch.setattr(ki, "_jina_expand_seed", jina)
    result = await ki.kb_discover_pages("https://example.com/docs/", mode="prefix")
    assert set(urls) <= set(result)
    fire.assert_not_awaited()
    jina.assert_not_awaited()
