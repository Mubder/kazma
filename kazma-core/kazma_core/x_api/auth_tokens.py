"""The two X credentials besides the OAuth 1.0a user keys (2026-11-17).

X accepts three kinds of authentication, and an operation names which
(``x_api.operations``):

* ``user`` -- the four OAuth 1.0a keys of Settings -> X (``config.py``);
* ``oauth2`` -- an OAuth 2.0 user token from signing in with X (PKCE): the
  only way to bookmarks, X Chat, broadcasts, calls and a few more. The access
  token lasts two hours; with ``offline.access`` X gives a refresh token, and
  :func:`oauth2_access_token` refreshes it before it runs out;
* ``app`` -- the app's Bearer token (developer console -> Keys and tokens):
  app-only reads (post counts, spaces, trends by place, usage) and the
  streams.

Every value lives in the settings store under ``connectors.x.`` -- the
secrets as vault pointers (``is_sensitive_config_key``) -- and is never
logged or answered. Blocking reads: call from a thread.

The sign-in's PKCE verifier is never stored: it is derived from the
sign-in's state with Kazma's own secret (:func:`pkce_verifier`), so the
callback can rebuild it and nothing secret sits in the attempt row.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "BEARER_KEY",
    "OAUTH2_KEYS",
    "available_auth",
    "bearer_token",
    "oauth2_access_token",
    "oauth2_account",
    "pkce_challenge",
    "pkce_verifier",
    "save_oauth2_tokens",
    "forget_oauth2",
]

BEARER_KEY = "connectors.x.bearer_token"
CLIENT_ID_KEY = "connectors.x.oauth2.client_id"
CLIENT_SECRET_KEY = "connectors.x.oauth2.client_secret"
ACCESS_KEY = "connectors.x.oauth2.access_token"
REFRESH_KEY = "connectors.x.oauth2.refresh_token"
EXPIRES_KEY = "connectors.x.oauth2.expires_at"
ACCOUNT_KEY = "connectors.x.oauth2.account"
OAUTH2_KEYS = (CLIENT_ID_KEY, CLIENT_SECRET_KEY, ACCESS_KEY, REFRESH_KEY, EXPIRES_KEY, ACCOUNT_KEY)

#: Refresh when less than this is left of the access token's life.
_REFRESH_MARGIN_S = 120
_refresh_lock = threading.Lock()


def _raw(key: str) -> Any:
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.ownership import x_config_key

    return get_config_store().get(x_config_key(key))


def _get(key: str) -> str:
    """A text setting, ``""`` when unset or when its vault pointer cannot be read."""
    value = _raw(key)
    if not isinstance(value, str) or not value.strip() or value.startswith("vault://"):
        return ""
    return value.strip()


def _expires_at() -> float:
    try:
        return float(_raw(EXPIRES_KEY) or 0)
    except (TypeError, ValueError):
        return 0.0


def bearer_token() -> str:
    """The app's Bearer token, or ``""``; ``X_BEARER_TOKEN`` wins when set.
    The environment is the owner's: once Spaces is enforced only the owner's
    space reads it (another space would spend the owner's X API budget,
    Spaces S1-17 part b)."""
    return bearer_from_env() or _get(BEARER_KEY)


def bearer_from_env() -> str:
    """``X_BEARER_TOKEN`` when this space may use it, else ``""``: always
    while Spaces is not enforced, else in the owner's space only."""
    from kazma_core.spaces.scope import spaces_enforced
    from kazma_core.x_api.ownership import x_tenant_id

    if spaces_enforced() and x_tenant_id() != "default":
        return ""
    return os.environ.get("X_BEARER_TOKEN", "").strip()


def oauth2_client() -> tuple[str, str]:
    """The OAuth 2.0 client id and secret (the secret empty for a public client)."""
    return _get(CLIENT_ID_KEY), _get(CLIENT_SECRET_KEY)


def oauth2_account() -> dict[str, Any]:
    """The account signed in with X (username, id, the scopes granted)."""
    found = _raw(ACCOUNT_KEY)
    return found if isinstance(found, dict) else {}


def save_oauth2_tokens(answer: dict[str, Any], *, account: dict[str, Any] | None = None) -> None:
    """Keep X's token answer (access, refresh, expiry) in one write."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.ownership import x_config_key

    access = str(answer.get("access_token") or "")
    if not access:
        raise ValueError("X answered without an access token.")
    values: dict[str, Any] = {
        ACCESS_KEY: access,
        EXPIRES_KEY: int(time.time() + int(answer.get("expires_in") or 7200)),
    }
    if answer.get("refresh_token"):
        values[REFRESH_KEY] = str(answer["refresh_token"])
    if account is not None:
        values[ACCOUNT_KEY] = {**account, "scopes": str(answer.get("scope") or "").split()}
    get_config_store().batch_set([(x_config_key(k), v, "connectors") for k, v in values.items()])


def forget_oauth2() -> None:
    """Sign out: drop the tokens and the account, keep the client id."""
    from kazma_core.config_store import get_config_store
    from kazma_core.x_api.ownership import x_config_key

    store = get_config_store()
    for key in (ACCESS_KEY, REFRESH_KEY, EXPIRES_KEY, ACCOUNT_KEY):
        store.delete(x_config_key(key))


def _refresh(refresh_token: str) -> dict[str, Any]:
    """X's answer to renewing the sign-in; raises ``client.XApiError``."""
    from kazma_core.x_api.client import XApiError, oauth2_token_request

    client_id, client_secret = oauth2_client()
    if not client_id:
        raise XApiError("The X OAuth 2.0 client id is not set (Settings -> X).", outcome="not_sent")
    data = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
    return oauth2_token_request(data, client_id=client_id, client_secret=client_secret)


def oauth2_access_token() -> str:
    """A live OAuth 2.0 user access token, refreshed when it is about to
    run out; ``""`` when nobody signed in with X."""
    access = _get(ACCESS_KEY)
    if not access:
        return ""
    if _expires_at() - time.time() > _REFRESH_MARGIN_S:
        return access
    with _refresh_lock:
        access = _get(ACCESS_KEY)
        if access and _expires_at() - time.time() > _REFRESH_MARGIN_S:
            return access
        refresh = _get(REFRESH_KEY)
        if not refresh:
            return ""
        answer = _refresh(refresh)
        save_oauth2_tokens(answer)
        logger.info("[x] the OAuth 2.0 sign-in was refreshed")
        return str(answer.get("access_token") or "")


def available_auth() -> dict[str, bool]:
    """Which authentications this install has right now."""
    from kazma_core.x_api.config import get_x_config

    return {
        "user": get_x_config().credentials.complete(),
        "oauth2": bool(_get(ACCESS_KEY) and (_get(REFRESH_KEY) or _expires_at() > time.time())),
        "app": bool(bearer_token()),
    }


def _pkce_key() -> bytes:
    """Kazma's own secret (``KAZMA_SECRET``, made at the first start and kept
    in the install's ``.env``)."""
    secret = os.environ.get("KAZMA_SECRET", "")
    if len(secret) < 16:
        raise RuntimeError("KAZMA_SECRET is not set: Kazma cannot sign in with X without it.")
    return secret.encode("utf-8")


def pkce_verifier(state: str) -> str:
    """The sign-in's PKCE code verifier, rebuilt from its state (43 characters)."""
    mac = hmac.new(_pkce_key(), b"x-oauth2-pkce:" + state.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode("ascii").rstrip("=")


def pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
