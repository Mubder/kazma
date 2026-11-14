"""Kazma as an app (REMAINING 4.1): the web app manifest every page links, its
icons, and the service worker's offline page. The browser half (Chrome's own
installability check, the offline page served) is
tests/e2e/test_web_app_install_browser.py.
"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from kazma_ui.web_app import ICONS, manifest

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "kazma-ui" / "kazma_ui" / "static"
TEMPLATES = ROOT / "kazma-ui" / "kazma_ui" / "templates"


def test_the_manifest_has_what_an_install_needs_in_both_languages() -> None:
    for lang, direction in (("en", "ltr"), ("ar", "rtl")):
        m = manifest(lang)
        assert m["name"] == "Kazma" and m["short_name"] == "Kazma"
        assert m["start_url"] == "/chat" and m["scope"] == "/" and m["display"] == "standalone"
        assert m["lang"] == lang and m["dir"] == direction
        assert m["description"] and m["shortcuts"][0]["url"] == "/chat?new=1"
        sizes = {(i["sizes"], i["purpose"]) for i in m["icons"]}
        assert {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")} <= sizes
    assert manifest("en")["description"] != manifest("ar")["description"]
    assert manifest("fr")["lang"] == "en", "a language Kazma does not speak falls back to English"


def test_every_icon_is_the_size_it_says() -> None:
    for src, size, _purpose in ICONS:
        path = STATIC / src.removeprefix("/static/")
        with Image.open(path) as im:
            assert im.size == (size, size), src
    with Image.open(STATIC / "img" / "apple-touch-icon.png") as im:
        assert im.size == (180, 180) and im.mode == "RGB", "iOS fills transparency with black"
    # The maskable icon is opaque to its corners: a launcher crops it to any shape.
    with Image.open(STATIC / "img" / "app-icon-maskable-512.png") as im:
        assert im.convert("RGBA").getpixel((0, 0))[3] == 255


def test_every_page_links_the_manifest_and_the_touch_icon() -> None:
    for name in ("base.html", "login.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert '<link rel="manifest" href="/manifest.webmanifest">' in text, name
        assert 'href="/static/img/apple-touch-icon.png"' in text, name


def test_the_manifest_and_the_offline_page_are_served_before_sign_in() -> None:
    from kazma_ui.auth import is_always_open, is_sensitive_path

    for path in ("/manifest.webmanifest", "/sw.js", "/static/offline.html"):
        assert not is_sensitive_path(path), path
    assert is_sensitive_path("/api/support/report") and not is_always_open("/api/support/report")


def test_the_service_worker_answers_only_navigations_and_caches_one_page() -> None:
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert "event.request.mode !== 'navigate'" in sw
    assert re.findall(r"cache\.addAll\(\[([^\]]*)\]\)", sw) == ["OFFLINE_URL, '/static/img/app-icon-192.png'"]
    assert sw.count("respondWith(") == 1, "nothing but a failed navigation is answered from the cache"
    assert "cache.put(" not in sw, "no answer of the server is ever cached"


def test_the_offline_page_needs_nothing_from_elsewhere() -> None:
    page = (STATIC / "offline.html").read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="([^"]+)"', page):
        assert ref.startswith("/"), ref
    assert "app-icon-192.png" in page and 'lang="ar"' in page


def test_the_route_answers_as_a_manifest() -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui.web_app import create_web_app_router

    app = FastAPI()
    app.include_router(create_web_app_router())
    res = TestClient(app).get("/manifest.webmanifest")
    assert res.status_code == 200 and res.headers["content-type"].startswith("application/manifest+json")
    assert res.json()["icons"]
