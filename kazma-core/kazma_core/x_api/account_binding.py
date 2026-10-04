"""Verified account identity and credential revision for exact publication binding."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kazma_core.x_api.config import XCredentials


def credential_revision(credentials: XCredentials) -> str:
    """A nonsecret comparison token; credentials themselves never enter operations."""
    raw = "\0".join((credentials.api_key, credentials.api_key_secret,
                    credentials.access_token, credentials.access_token_secret))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def record_account(credentials: XCredentials, me: dict[str, Any]) -> str:
    """Bind only a complete identity response to these exact credentials."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.ownership import x_config_key

    ident = str(me.get("id") or "")
    if not ident.isascii() or not ident.isdigit():
        raise ValueError("X did not return a valid account ID. Queued work remains held.")
    get_config_store().batch_set([
        (x_config_key("connectors.x.account"), {"id": ident, "username": str(me.get("username") or ""),
                                               "credential_revision": credential_revision(credentials), "verified_at": time.time()}, "connectors"),
    ])
    return ident
