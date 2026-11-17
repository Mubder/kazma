"""Sign in with X: OAuth 2.0 with PKCE (2026-11-17).

Part of X's API answers only an OAuth 2.0 user token: bookmarks, X Chat,
broadcasts, calls, a few more (``x_api.operations`` names each operation's
authentication). Settings -> X holds the X app's OAuth 2.0 client id (and
its secret for a confidential client); "Sign in with X" sends the browser to
X's consent page in the same tab and back to ``/api/x/oauth2/callback``.

* :func:`start` records the attempt (``security.oauth_attempts``, ten
  minutes, taken once, bound to the browser by a cookie) and answers X's
  consent URL. The PKCE verifier is never stored: it is rebuilt from the
  state with Kazma's own secret (``auth_tokens.pkce_verifier``).
* :func:`exchange` trades the callback's code for X's tokens; the route
  keeps them (``auth_tokens.save_oauth2_tokens``) and reads the signed-in
  account with them.

Both are blocking: call from a thread. Nothing secret is logged or answered.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote, urlencode

from kazma_core.x_api import auth_tokens
from kazma_core.x_api.operations import authorize_url, oauth2_scopes

logger = logging.getLogger(__name__)

__all__ = ["DEFAULT_SCOPES", "CALLBACK_PATH", "SignInFailed", "exchange", "redirect_uri", "start"]

CALLBACK_PATH = "/api/x/oauth2/callback"

#: What a sign-in asks for unless the owner asks for every permission: X's
#: long-standing OAuth 2.0 permissions, which every app with OAuth 2.0 on can
#: be granted. The newer ones (broadcasts, calls, settings, media library,
#: saved searches, custom timelines, the developer account) can need the app
#: approved for them, and X refuses the whole sign-in over one it does not
#: grant; ``every_scope`` asks for those too.
DEFAULT_SCOPES = (
    "block.read", "block.write", "bookmark.read", "bookmark.write",
    "dm.read", "dm.write", "follows.read", "follows.write",
    "like.read", "like.write", "list.read", "list.write",
    "media.write", "mute.read", "mute.write", "offline.access",
    "space.read", "tweet.moderate.write", "tweet.read", "tweet.write",
    "users.read",
)


class SignInFailed(RuntimeError):
    """The sign-in did not finish; ``code`` is what the page words."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def redirect_uri(base: str) -> str:
    """Where X sends the browser back: register this exact address in the X
    app's settings (User authentication settings -> Callback URI)."""
    return base.rstrip("/") + CALLBACK_PATH


def start(base: str, *, every_scope: bool = False) -> dict[str, Any]:
    """Begin a sign-in: ``{"ok": True, "authorize_url", "state"}``, or
    ``{"ok": False, "code", "error"}`` with nothing recorded."""
    from kazma_core.security import oauth_attempts

    client_id, _secret = auth_tokens.oauth2_client()
    if not client_id:
        return {"ok": False, "code": "no_client",
                "error": "Save the X app's OAuth 2.0 client id first (Settings → X)."}
    try:
        auth_tokens.pkce_verifier("probe")  # Kazma's secret is there, before any attempt is kept
    except RuntimeError as exc:
        return {"ok": False, "code": "no_secret", "error": str(exc)}
    scopes = sorted(oauth2_scopes()) if every_scope else list(DEFAULT_SCOPES)
    callback = redirect_uri(base)
    state = oauth_attempts.begin("x", redirect_uri=callback, every_scope="1" if every_scope else "")
    query = urlencode({
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": callback,
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": auth_tokens.pkce_challenge(auth_tokens.pkce_verifier(state)),
        "code_challenge_method": "S256",
    }, quote_via=quote)
    return {"ok": True, "authorize_url": f"{authorize_url()}?{query}", "state": state}


def exchange(code: str, state: str, attempt: dict[str, Any]) -> dict[str, Any]:
    """Trade the callback's *code* for X's token answer (access token, its
    lifetime, the refresh token, the scopes granted); raise
    :class:`SignInFailed`."""
    from kazma_core.x_api.client import XApiError, oauth2_token_request

    client_id, client_secret = auth_tokens.oauth2_client()
    if not client_id:
        raise SignInFailed("no_client", "The X app's OAuth 2.0 client id was removed during the sign-in.")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": str(attempt.get("redirect_uri") or ""),
        "client_id": client_id,
        "code_verifier": auth_tokens.pkce_verifier(state),
    }
    try:
        return oauth2_token_request(data, client_id=client_id, client_secret=client_secret)
    except XApiError as exc:
        logger.warning("[x] the sign-in's code exchange did not go through (HTTP %s)", exc.status or "none")
        raise SignInFailed("unreachable" if exc.outcome == "not_sent" else "refused", str(exc)) from exc
