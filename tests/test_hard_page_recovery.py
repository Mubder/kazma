"""Local Chromium precedes explicitly permitted hosted page recovery."""

from __future__ import annotations

import importlib

import pytest

ru = importlib.import_module("kazma_core.tools.read_url")


@pytest.fixture(autouse=True)
def _clear_cache():
    ru.clear_url_cache()
    yield
    ru.clear_url_cache()


def test_jina_hard_disabled(monkeypatch):
    monkeypatch.setenv("KAZMA_JINA_READER", "0")
    assert ru._jina_hard_disabled() is True
    assert ru._jina_explicit_opt_in() is False


def test_jina_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("KAZMA_JINA_READER", raising=False)
    assert ru._jina_hard_disabled() is False
    assert ru._jina_explicit_opt_in() is False
    monkeypatch.setenv("KAZMA_JINA_READER", "1")
    assert ru._jina_explicit_opt_in() is True


@pytest.mark.asyncio
async def test_recover_hard_page_order(monkeypatch):
    order: list[str] = []

    async def fire(url):
        order.append("firecrawl")
        return None

    async def jina(url):
        order.append("jina")
        return "JINA BODY " + ("x" * 80)

    async def pw(url):
        order.append("playwright")
        return None

    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-key")
    monkeypatch.setenv("KAZMA_ACQUISITION_MODE", "local_first_with_remote_fallback")
    monkeypatch.setenv("KAZMA_ACQUISITION_REMOTE_PROVIDERS", "firecrawl,jina")
    monkeypatch.delenv("KAZMA_JINA_READER", raising=False)
    monkeypatch.setattr(ru, "_try_firecrawl", fire)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    monkeypatch.setattr(ru, "_fetch_with_playwright", pw)

    out = await ru._recover_hard_page("https://example.com/hard", why="bot")
    assert out and out.startswith("JINA BODY")
    assert order == ["playwright", "firecrawl", "jina"]


@pytest.mark.asyncio
async def test_recover_skips_jina_when_disabled(monkeypatch):
    order: list[str] = []

    async def jina(url):
        order.append("jina")
        return "should-not"

    async def pw(url):
        order.append("playwright")
        return "from-pw " + ("y" * 80)

    monkeypatch.setenv("KAZMA_JINA_READER", "off")
    monkeypatch.delenv("KAZMA_FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    monkeypatch.setattr(ru, "_fetch_with_playwright", pw)

    out = await ru._recover_hard_page("https://example.com/hard", why="bot")
    assert "from-pw" in (out or "")
    assert "jina" not in order
    assert order == ["playwright"]


@pytest.mark.asyncio
async def test_fetch_full_text_uses_recovery_on_bot_wall(monkeypatch):
    """httpx returns challenge HTML → recovery path runs."""
    from types import SimpleNamespace
    import httpx

    def blocked(request):
        # A real response/stream exercises the shared bounded reader. Earlier
        # get-only stubs implicitly selected its unbounded mock fallback.
        peer = SimpleNamespace(get_extra_info=lambda _: ("93.184.216.34", 443))
        return httpx.Response(403, text=(
            "<html><body>Just a moment... checking your browser "
            "cf-browser-verification cloudflare</body></html>"
        ), request=request, extensions={"network_stream": peer})

    async def no_opt(u):
        return None

    async def recover(url, *, why=""):
        return "# Recovered markdown\n\n" + ("content " * 20)

    # Bypass SSRF for unit test (public-looking host). The stub must accept
    # the guard's keyword arguments and return the validated-IP tuple the
    # caller expects: `block_unresolved=True` was added on every LLM-facing
    # fetcher (audit 2026-09-16 F-6), and a positional-only `lambda u: u`
    # raised TypeError inside the production call rather than failing here.
    monkeypatch.setattr(
        "kazma_core.security.ssrf.validate_url",
        lambda u, **_kw: ("93.184.216.34",),
    )
    monkeypatch.setattr(
        "kazma_core.proxy.client.get_scraping_client",
        lambda **k: httpx.AsyncClient(transport=httpx.MockTransport(blocked)),
    )
    monkeypatch.setattr(ru, "_fetch_via_optional_backends", no_opt)
    monkeypatch.setattr(ru, "_recover_hard_page", recover)

    out = await ru._fetch_full_text("https://hard.example/page")
    assert "Recovered markdown" in out
    assert not out.startswith("Error:")
