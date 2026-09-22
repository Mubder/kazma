"""Exact browser-origin policy shared by CORS and CSRF.

Same-origin requests need no CORS exception. Additional credentialed origins
are explicit operator trust: the public deployment URL and optional CORS list.
Forwarded headers never enlarge this set.
"""

from __future__ import annotations

import ipaddress
import logging
import os
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)


def normalize_origin(value: str, *, allow_path: bool = False) -> str | None:
    """Return scheme/host/effective-port, rejecting malformed authorities."""
    if not value or any(ord(c) <= 32 or ord(c) == 127 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        if not allow_path and (parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return None
        host = parsed.hostname
        if "%" in host or "\\" in host:
            return None
        try:
            address = ipaddress.ip_address(host)
            host = f"[{address.compressed}]" if address.version == 6 else str(address)
        except ValueError:
            host = host.encode("idna").decode("ascii").lower()
        port = parsed.port
        if port == 0:
            return None
        default_port = 443 if parsed.scheme == "https" else 80
        suffix = f":{port}" if port is not None and port != default_port else ""
        return f"{parsed.scheme}://{host}{suffix}"
    except (ValueError, UnicodeError):
        return None


def configured_browser_origins() -> list[str]:
    """Explicit credentialed origins; no ambient development-port trust."""
    origins: set[str] = set()
    public = (os.environ.get("KAZMA_PUBLIC_URL") or "").strip()
    if public:
        origin = normalize_origin(public, allow_path=True)
        if origin:
            origins.add(origin)
        else:
            logger.warning("Ignoring malformed KAZMA_PUBLIC_URL browser origin")
    for item in (os.environ.get("KAZMA_CORS_ORIGINS") or "").split(","):
        if any(ord(c) < 32 or ord(c) == 127 for c in item):
            logger.warning("Ignoring KAZMA_CORS_ORIGINS entry containing control characters")
            continue
        item = item.strip()
        if not item:
            continue
        origin = normalize_origin(item)
        if origin:
            origins.add(origin)
        else:
            logger.warning("Ignoring invalid KAZMA_CORS_ORIGINS entry (exact HTTP(S) origins required)")
    return sorted(origins)
