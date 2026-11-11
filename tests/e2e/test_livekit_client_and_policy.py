"""The vendored LiveKit client runs under the page policy, which lets the page
reach this server and the configured LiveKit server only (2026-11-11).

In a browser, on the Chat page reached by the sidebar: the client ``voice.js``
loads (``/static/vendor/livekit/...``, no CDN) defines ``LivekitClient.Room``
with no policy refusal; with LiveKit configured the page may open its socket
and fetch its origin (a ``.test`` host: nothing answers, which is not the
policy's business), and an outside host is refused (``connect-src``). Without
LiveKit configured, the same LiveKit host is refused. The page's own socket
(``/ws/voice``, live voice) is never refused. No LiveKit server is run: what
is tested is what the browser is allowed to try.
"""

from __future__ import annotations

import pytest

pytest.importorskip("playwright")
httpx = pytest.importorskip("httpx")

from tests.e2e.test_provider_settings_browser import kazma  # noqa: E402,F401 -- the server fixture

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

LIVEKIT = "wss://lk.kazma-drill.test"

#: Every policy refusal the page sees, as "<directive> <blocked URI>".
_WATCH = """() => {
  window.__refused = [];
  document.addEventListener('securitypolicyviolation',
    (e) => window.__refused.push(e.effectiveDirective + ' ' + e.blockedURI));
}"""

_LOAD_CLIENT = """async () => {
  await new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = '/static/vendor/livekit/livekit-client.umd.js?v=2.23.0';
    s.onload = resolve; s.onerror = () => reject(new Error('client did not load'));
    document.head.appendChild(s);
  });
  return typeof (window.LivekitClient && window.LivekitClient.Room);
}"""

_TRY = """async (urls) => {
  for (const url of urls) {
    try {
      if (url.startsWith('ws')) { const s = new WebSocket(url); setTimeout(() => s.close(), 50); }
      else { await fetch(url, { mode: 'no-cors' }).catch(() => null); }
    } catch (e) { /* a refused socket throws SecurityError in some builds */ }
  }
  await new Promise((r) => setTimeout(r, 400));
  return window.__refused.slice();
}"""


def _chat(ctx, base: str):
    pg = ctx.new_page()
    pg.goto(base + "/", wait_until="domcontentloaded")
    pg.locator("a.nav-link[href='/chat']").first.click()
    pg.wait_for_function("location.pathname === '/chat'", timeout=15000)
    pg.wait_for_function("() => !!window.Alpine", timeout=15000)
    pg.evaluate(_WATCH)
    return pg


def test_the_client_loads_and_only_the_configured_livekit_server_is_reached(kazma, monkeypatch) -> None:  # noqa: F811
    from kazma_ui import security_headers
    from playwright.sync_api import sync_playwright

    base, _fake_url = kazma
    own_socket = base.replace("http://", "ws://") + "/ws/voice"
    monkeypatch.setenv("LIVEKIT_URL", LIVEKIT)
    monkeypatch.setenv("LIVEKIT_API_KEY", "standin-key")
    monkeypatch.setenv("LIVEKIT_API_SECRET", "standin-secret-standin-secret-0")
    monkeypatch.delenv("KAZMA_VOICE_DUPLEX", raising=False)
    security_headers._LIVEKIT.forget()
    errors: list[str] = []
    try:
        policy = httpx.get(f"{base}/chat", timeout=20).headers["content-security-policy"]
        assert "wss://lk.kazma-drill.test https://lk.kazma-drill.test" in policy, policy
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                ctx = browser.new_context()
                ctx.route("https://cdn.jsdelivr.net/**", lambda route: route.abort())
                pg = _chat(ctx, base)
                pg.on("pageerror", lambda exc: errors.append(str(exc)))
                assert pg.evaluate(_LOAD_CLIENT) == "function"
                refused = pg.evaluate(_TRY, [
                    LIVEKIT + "/rtc", "https://lk.kazma-drill.test/settings/regions", own_socket,
                ])
                assert refused == [], refused
                refused = pg.evaluate(_TRY, ["wss://outside.kazma-drill.test/x",
                                             "https://outside.kazma-drill.test/x"])
                assert len(refused) == 2 and all(r.startswith("connect-src ") for r in refused), refused

                # LiveKit off: its host is no longer this page's to reach.
                monkeypatch.delenv("LIVEKIT_URL")
                security_headers._LIVEKIT.forget()
                pg2 = _chat(ctx, base)
                refused = pg2.evaluate(_TRY, [LIVEKIT + "/rtc", own_socket])
                assert len(refused) == 1 and "lk.kazma-drill.test" in refused[0], refused
            finally:
                browser.close()
    finally:
        security_headers._LIVEKIT.forget()
    assert not errors, errors
