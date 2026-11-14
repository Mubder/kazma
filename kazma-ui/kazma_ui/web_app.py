"""Kazma as an app on a phone or computer (REMAINING 4.1, 2026-11-14).

``GET /manifest.webmanifest`` is the web app manifest every page links: the
name, the icons (``scripts/make_app_icons.py``), where the app opens
(``/chat``), standalone display, the brand colours, two shortcuts, in the
page's language (``lang``/``dir`` follow it). With it a browser offers
"Install app" / "Add to Home Screen", and the installed app opens in its own
window. It names nothing of the install: it is the same for every visitor and
is served before sign-in (a page's manifest fetch carries no cookie).

The service worker (``/sw.js``, also Web Push's) answers a page navigation
the server does not answer -- Kazma restarting, the computer asleep -- with
``/static/offline.html``, which tries again by itself. It caches nothing else:
every page, request and answer goes to the server as before.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

__all__ = ["ICONS", "create_web_app_router", "manifest"]

#: The manifest's icons: (path, size, purpose). Held to the files by
#: ``tests/test_web_app_manifest.py``.
ICONS: tuple[tuple[str, int, str], ...] = (
    ("/static/img/app-icon-192.png", 192, "any"),
    ("/static/img/app-icon-512.png", 512, "any"),
    ("/static/img/app-icon-maskable-512.png", 512, "maskable"),
)

_GROUND = "#0e1626"


def manifest(lang: str) -> dict[str, Any]:
    """The manifest in *lang* (``en`` or ``ar``)."""
    from kazma_ui.i18n import t

    lang = "ar" if lang == "ar" else "en"
    small = {"src": ICONS[0][0], "sizes": f"{ICONS[0][1]}x{ICONS[0][1]}", "type": "image/png"}
    return {
        "id": "/",
        "name": "Kazma",
        "short_name": "Kazma",
        "description": t("webapp.description", lang),
        "lang": lang,
        "dir": "rtl" if lang == "ar" else "ltr",
        "start_url": "/chat",
        "scope": "/",
        "display": "standalone",
        "background_color": _GROUND,
        "theme_color": _GROUND,
        "icons": [{"src": src, "sizes": f"{size}x{size}", "type": "image/png", "purpose": purpose}
                  for src, size, purpose in ICONS],
        "shortcuts": [
            {"name": t("webapp.new_chat", lang), "url": "/chat?new=1", "icons": [small]},
            {"name": t("webapp.dashboard", lang), "url": "/dashboard", "icons": [small]},
        ],
    }


def create_web_app_router() -> APIRouter:
    router = APIRouter(tags=["web-app"])

    @router.get("/manifest.webmanifest", include_in_schema=False)
    def web_app_manifest() -> JSONResponse:
        """The manifest, in the language the request carries (a plain def:
        the language read is the middleware's, nothing else is read)."""
        from kazma_ui.i18n import current_language

        return JSONResponse(manifest(current_language()), media_type="application/manifest+json",
                            headers={"Cache-Control": "no-cache"})

    return router
