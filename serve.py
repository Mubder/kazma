#!/usr/bin/env python3
"""Kazma serve script - starts the WebUI server."""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time

# Use the same Python that's running this script
python_exe = sys.executable

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
    # Start the server
    proc = subprocess.Popen(
        [
            python_exe,
            "-m",
            "uvicorn",
            app_factory,
            "--factory",
            "--host",
            host,
            "--port",
            "9090",
            *_proxy_args(),
        ],
        # stdout/stderr are INHERITED, never piped.
        #
        # This used to capture both into pipes that nothing ever read, and
        # then call proc.wait(). Once uvicorn wrote more than the OS pipe
        # buffer (~4-64 KB) it blocked forever on write, freezing the whole
        # server -- alive, listening on nothing, logging nothing.
        #
        # Interactively you might never reach the buffer limit. Under a
        # service manager you do, which is why this only surfaced when
        # Kazma was first run under supervision (2026-08-28): the process
        # stayed up, the app log stopped mid-startup, and there was nothing
        # to diagnose from.
        #
        # Inheriting means output goes to the console when run by hand and
        # to the supervisor's stream otherwise. Kazma's real logs go to
        # .kazma/kazma.log either way.
    )

    print(f"Server started with PID {proc.pid}")
    print(f"Open http://127.0.0.1:9090 in your browser (bound host={host})")
    print("Press Ctrl+C to stop\n")

    # Wait for server to start
    time.sleep(2)

    # Check if process is still running. With stdio inherited, uvicorn's
    # own error output has already reached the console/supervisor, so there
    # is nothing to drain here -- just report and fail loudly.
    if proc.poll() is not None:
        print(
            f"Server failed to start (uvicorn exited {proc.returncode}). "
            "See the output above and .kazma/kazma.log."
        )
        sys.exit(1)

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("Server stopped")

except FileNotFoundError:
    print("❌ Error: uvicorn not found")
    print("Install with: pip install uvicorn[standard]")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    sys.exit(1)
