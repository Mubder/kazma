"""OAuth 1.0a (RFC 5849) HMAC-SHA1 for the official X API.

User-context posting requires the four OAuth 1.0a tokens (consumer key/secret
+ access token/secret). An app-only Bearer token can read but never POST
``/2/tweets``. JSON bodies are **not** signed (X/Twitter convention); only
oauth_* params and the URL query string enter the signature base.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl, quote, urlparse, urlunparse

__all__ = [
    "percent_encode",
    "oauth1_authorization_header",
    "sign_request",
]


def percent_encode(value: str) -> str:
    """RFC 3986 percent-encoding (unreserved: ALPHA / DIGIT / '-' / '.' / '_' / '~')."""
    return quote(str(value), safe="-._~")


def _collect_params(
    url: str,
    oauth_params: Mapping[str, str],
) -> list[tuple[str, str]]:
    parsed = urlparse(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    items = [(k, v) for k, v in query] + [(k, v) for k, v in oauth_params.items()]
    return items


def _normalized_url(url: str) -> str:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "https").lower()
    host = (parsed.hostname or "").lower()
    port = parsed.port
    default = (scheme == "https" and port in (None, 443)) or (
        scheme == "http" and port in (None, 80)
    )
    netloc = host if default or port is None else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunparse((scheme, netloc, path, "", "", ""))


def sign_request(
    *,
    method: str,
    url: str,
    consumer_key: str,
    consumer_secret: str,
    token: str,
    token_secret: str,
    nonce: str | None = None,
    timestamp: str | None = None,
) -> dict[str, str]:
    """Return oauth_* params including ``oauth_signature``."""
    oauth: dict[str, str] = {
        "oauth_consumer_key": consumer_key,
        "oauth_nonce": nonce or secrets.token_hex(16),
        "oauth_signature_method": "HMAC-SHA1",
        "oauth_timestamp": timestamp or str(int(time.time())),
        "oauth_token": token,
        "oauth_version": "1.0",
    }
    params = _collect_params(url, oauth)
    params.sort(key=lambda kv: (percent_encode(kv[0]), percent_encode(kv[1])))
    param_str = "&".join(
        f"{percent_encode(k)}={percent_encode(v)}" for k, v in params
    )
    base = "&".join(
        [
            method.upper(),
            percent_encode(_normalized_url(url)),
            percent_encode(param_str),
        ]
    )
    key = f"{percent_encode(consumer_secret)}&{percent_encode(token_secret)}"
    digest = hmac.new(key.encode("utf-8"), base.encode("utf-8"), hashlib.sha1).digest()
    oauth["oauth_signature"] = base64.b64encode(digest).decode("ascii")
    return oauth


def oauth1_authorization_header(oauth_params: Mapping[str, str]) -> str:
    """Build the ``Authorization: OAuth …`` header (sorted, quoted)."""
    parts = []
    for name in sorted(oauth_params):
        parts.append(
            f'{percent_encode(name)}="{percent_encode(oauth_params[name])}"'
        )
    return "OAuth " + ", ".join(parts)
