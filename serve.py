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

    return host


def _proxy_args() -> list[str]:
    """uvicorn flags so forwarded headers are parsed from declared proxies only.

    Without these, ``request.client.host`` is the proxy for every request and
    the app cannot tell an internet visitor from the local operator (audit
    F-01). ``KAZMA_TRUSTED_PROXIES`` is the single source of truth: unset means
    no proxy, and uvicorn is told to trust nothing.
    """
    proxies = [
        h.strip()
        for h in (os.environ.get("KAZMA_TRUSTED_PROXIES") or "").split(",")
        if h.strip()
    ]
    if not proxies:
        return ["--no-proxy-headers"]
    print(f"  [proxy] trusting forwarded headers from: {', '.join(proxies)}")
    return ["--proxy-headers", "--forwarded-allow-ips", ",".join(proxies)]


host = _bootstrap_bind_and_secret()

try:
    # In-process uvicorn so Windows gets SelectorEventLoop via
    # uvicorn_loop_factory. `python -m uvicorn` (the old subprocess path)
    # hardcodes ProactorEventLoop on Windows in 0.36+, which makes
    # AsyncPostgresSaver fail and silently fall back to SQLite.
    from kazma_core.eventloop import set_windows_selector_policy, uvicorn_loop_factory

    set_windows_selector_policy()

    import uvicorn

    proxy = _proxy_args()
    forwarded = None
    proxy_headers = "--proxy-headers" in proxy
    if proxy_headers:
        for i, arg in enumerate(proxy):
            if arg == "--forwarded-allow-ips" and i + 1 < len(proxy):
                forwarded = proxy[i + 1]
                break

    loop_factory = uvicorn_loop_factory()
    config_kwargs: dict = {
        "app": app_factory,
        "factory": True,
        "host": host,
        "port": 9090,
        "proxy_headers": proxy_headers,
        "ws_ping_interval": 20.0,
        "ws_ping_timeout": 20.0,
        "timeout_graceful_shutdown": 15,
    }
    if forwarded:
        config_kwargs["forwarded_allow_ips"] = forwarded
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
