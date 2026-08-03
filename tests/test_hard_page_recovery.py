"""Hard-page recovery cascade (Firecrawl → Jina → Playwright) for read_url."""

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
        return "PW"

    monkeypatch.setenv("KAZMA_FIRECRAWL_API_KEY", "test-key")
    monkeypatch.delenv("KAZMA_JINA_READER", raising=False)
    monkeypatch.setattr(ru, "_try_firecrawl", fire)
    monkeypatch.setattr(ru, "_try_jina_reader", jina)
    monkeypatch.setattr(ru, "_fetch_with_playwright", pw)

    out = await ru._recover_hard_page("https://example.com/hard", why="bot")
    assert out and out.startswith("JINA BODY")
    assert order == ["firecrawl", "jina"]  # stop after jina success


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

    class _Resp:
        status_code = 403
        text = (
            "<html><body>Just a moment... checking your browser "
            "cf-browser-verification cloudflare</body></html>"
        )
        headers: dict = {}

        def raise_for_status(self):
            raise RuntimeError("403")

    class _Client:
        def __init__(self, *a, **k):
            self.headers = {"user-agent": "test"}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return _Resp()

    async def no_opt(u):
        return None

    async def recover(url, *, why=""):
        return "# Recovered markdown\n\n" + ("content " * 20)

    import httpx

    # Bypass SSRF for unit test (public-looking host)
    monkeypatch.setattr(
        "kazma_core.security.ssrf.validate_url",
        lambda u: u,
    )
    # get_scraping_client builds the real client; patch it + httpx for safety
    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    monkeypatch.setattr(
        "kazma_core.proxy.client.get_scraping_client",
        lambda **k: _Client(),
    )
    monkeypatch.setattr(ru, "_fetch_via_optional_backends", no_opt)
    monkeypatch.setattr(ru, "_recover_hard_page", recover)

    out = await ru._fetch_full_text("https://hard.example/page")
    assert "Recovered markdown" in out
    assert not out.startswith("Error:")
