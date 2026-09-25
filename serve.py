#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

from __future__ import annotations

import os
import secrets
import sys

# Can override the app factory via environment variable
app_factory = "kazma_ui.app:create_app"

_KNOWN_BAD_SECRET = "kazma-local-dev-secret"
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _is_loopback(host: str) -> bool:
    return host.strip().lower() in _LOOPBACK


def _bootstrap_bind_and_secret() -> str:
    """Resolve host + secret. Never invent a well-known default secret."""
    # .env first, so it decides the bind host, the secret, the exposure check
    # and trusted proxies — the same as `kazma serve` and `kazma-web`. This
    # launcher (the one kazma_guard runs) used to read KAZMA_HOST before any
    # .env was loaded, so a KAZMA_HOST line there was silently ignored while
    # docs/docs/ops/wsl-fixed-access.md told operators to put it there; the
    # rest only saw .env because importing kazma_core ran a package-relative
    # load_dotenv() (audit 2026-09-22).
    from kazma_core.env_files import load_env_files

    load_env_files()
    host = os.environ.get("KAZMA_HOST", "127.0.0.1").strip() or "127.0.0.1"
    existing = (os.environ.get("KAZMA_SECRET") or "").strip()

    if existing == _KNOWN_BAD_SECRET:
        print(
            "\n  [SECURITY] KAZMA_SECRET is the old hardcoded default — "
            "refusing to start. Unset it or set a strong random secret.\n"
        )
        sys.exit(1)

    if not existing:
        if not _is_loopback(host):
            print(
                "\n  [SECURITY] Non-loopback bind requires KAZMA_SECRET.\n"
                "  Set a strong secret, or bind loopback: KAZMA_HOST=127.0.0.1\n"
            )
            sys.exit(1)
        generated = secrets.token_urlsafe(32)
        os.environ["KAZMA_SECRET"] = generated
        print("\n  [SECURITY] Generated KAZMA_SECRET for this process (not persisted):")
        print(f"    {generated}")
        print("  Pin it with:  export KAZMA_SECRET='…'  (or put it in .env)\n")

    # The secret check above asks "is there a secret?"; kazma_ui.auth asks
    # "is this labelled production?". Neither asks "are you exposed?", so an
    # auth kill switch plus a non-loopback bind used to start cleanly with a
    # perfectly good secret and serve every /api endpoint to the network.
    from kazma_core.security.boot_guard import check_exposure_posture

    _ok, _msg = check_exposure_posture(host)
    if _msg:
        print(_msg)
    if not _ok:
        sys.exit(1)

    return host


def _note_proxies() -> None:
    """Say which peers may speak for a client via forwarded headers.

    ``KAZMA_TRUSTED_PROXIES`` is the single source of truth (audit F-01), and
    the APP applies it (``kazma_ui.proxy_headers``), not uvicorn: uvicorn
    replaced the client address before the app could see the TCP peer, and
    the undeclared-proxy check then flagged Cloudflare Tunnel's own visitors
    on every boot. So uvicorn is always started with ``proxy_headers=False``.
    """
    proxies = [
        h.strip()
        for h in (os.environ.get("KAZMA_TRUSTED_PROXIES") or "").split(",")
        if h.strip()
    ]
    if proxies:
        print(f"  [proxy] trusting forwarded headers from: {', '.join(proxies)}")


host = _bootstrap_bind_and_secret()

try:
    # In-process uvicorn so Windows gets SelectorEventLoop via
    # uvicorn_loop_factory. `python -m uvicorn` (the old subprocess path)
    # hardcodes ProactorEventLoop on Windows in 0.36+, which makes
    # AsyncPostgresSaver fail and silently fall back to SQLite.
    from kazma_core.eventloop import set_windows_selector_policy, uvicorn_loop_factory

    set_windows_selector_policy()

    import uvicorn

    _note_proxies()

    loop_factory = uvicorn_loop_factory()
    config_kwargs: dict = {
        "app": app_factory,
        "factory": True,
        "host": host,
        "port": 9090,
        # The app applies forwarded headers itself (see _note_proxies).
        "proxy_headers": False,
        "ws_ping_interval": 20.0,
        "ws_ping_timeout": 20.0,
        "timeout_graceful_shutdown": 15,
    }
    if loop_factory is not None:
        config_kwargs["loop"] = loop_factory

    print(f"Open http://127.0.0.1:9090 in your browser (bound host={host})")
    print("Press Ctrl+C to stop\n")
    uvicorn.run(**config_kwargs)

except KeyboardInterrupt:
    print("\nShutting down server...")
except ImportError:
    print("❌ Error: uvicorn not found")
    print("Install with: pip install uvicorn[standard]")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
