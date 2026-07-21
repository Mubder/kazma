"""Unit tests for read_url hardening (UA, bot/SPA triggers, truncation)."""

from __future__ import annotations

import importlib

ru = importlib.import_module("kazma_core.tools.read_url")


def test_browser_ua_not_kazma_bot() -> None:
    assert "KazmaBot" not in ru._BROWSER_UA
    assert "Chrome" in ru._BROWSER_UA
    assert ru._HTTP_HEADERS["User-Agent"] == ru._BROWSER_UA


def test_looks_like_bot_block_cloudflare_200() -> None:
    html = "<html><body>Just a moment... Cloudflare checking your browser</body></html>"
    assert ru._looks_like_bot_block(html, 200) is True


def test_looks_like_bot_block_clean_200() -> None:
    html = "<html><body><h1>Hello world article content here</h1><p>More text</p></body></html>"
    assert ru._looks_like_bot_block(html, 200) is False


def test_looks_like_js_shell() -> None:
    assert ru._looks_like_js_shell('<div id="root"></div><script src="app.js"></script>')
    assert not ru._looks_like_js_shell("<html><body><p>Plain article</p></body></html>")


def test_is_thin_extraction() -> None:
    big_shell = '<div id="root"></div>' + ("x" * 600)
    assert ru._is_thin_extraction("", big_shell) is True
    assert ru._is_thin_extraction("short", big_shell) is True
    rich = "A" * 500
    assert ru._is_thin_extraction(rich, big_shell) is False


def test_should_try_playwright_for_thin() -> None:
    html = '<html><body><div id="__next"></div>' + ("s" * 800) + "</body></html>"
    assert ru._should_try_playwright(html, 200, extracted="") is True
    assert ru._should_try_playwright(html, 200, extracted="x" * 50) is True
    assert ru._should_try_playwright("<p>ok</p>", 200, extracted="x" * 300) is False


def test_truncate() -> None:
    short = "hello"
    assert ru._truncate(short) == "hello"
    long = "z" * (ru.MAX_CONTENT_CHARS + 50)
    out = ru._truncate(long)
    assert "truncated" in out
    assert out.startswith("z" * ru.MAX_CONTENT_CHARS)
    assert len(long) == ru.MAX_CONTENT_CHARS + 50
