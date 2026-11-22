"""Which account a request is, and which space it is in (Spaces S1-04, S1-06).

Two questions, each with one answer:

* :func:`account_of` -- the account behind a signed-in principal. Until
  2026-11 the shared secret, the loopback auto-login and an API token were an
  admin "with no account behind them"; a session of the older multi-user
  mode named a user and nothing more. Now each is an account of the identity
  store, or none: the owner's own entries are the owner's account, a local
  user's session is that user's account, and a sign-in Kazma has no account
  for (an OIDC session, today) is none -- refused once Spaces is enforced.

* :func:`space_for_request` -- the space a request runs in. **The page names
  it on every call** (header :data:`SPACE_HEADER`, or the ``space`` query
  value where a request cannot carry a header: a download, an image, an
  event stream), and the server checks every time that the caller is a
  member. The space is never kept in the session: two tabs in two spaces
  must not mix. A request that names none runs in the account's only space;
  with several, a ``space`` route is refused until it says which.

While the install does not enforce Spaces (one operator, or the older
multi-user mode), a request that names no space resolves exactly as before:
the tenant the older middleware worked out. So does a request that names
exactly that tenant: a page is rendered for the tenant its request ran in,
and a page that names it back asks for nothing it would not get anyway. The
older mode's users and OIDC sign-ins are members of no space, so checking
membership there refused every call a page made after it loaded. Naming any
other space needs membership in both modes; once Spaces is enforced, every
named space does.

Everything here reads the identity store: call it off the event loop.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from kazma_core.spaces import identity
from kazma_core.spaces.scope import (
    OWNER_ACCOUNT_ID,
    OWNER_SPACE_ID,
    bind_scope,
    spaces_enforced,
    valid_space_id,
)

__all__ = [
    "SPACE_HEADER",
    "SPACE_QUERY",
    "Binding",
    "SpaceRefused",
    "account_of",
    "bound_to",
    "must_name_its_space",
    "named_space",
    "space_for_request",
    "space_for_socket",
    "spaces_for_account",
]

logger = logging.getLogger(__name__)

#: The header a page sends on every call to say which space it is in.
SPACE_HEADER = "X-Kazma-Space"

#: The query value for requests that cannot carry the header.
SPACE_QUERY = "space"

#: Session actors only the owner's own entry mints: the loopback auto-login.
_OWNER_SESSION_ACTORS = frozenset({"auto-cookie"})

#: The user id a sign-in with the install's shared secret is given.
_SECRET_USER_ID = "shared-secret"


class SpaceRefused(Exception):
    """The request may not run in the space it asked for, or names none where
    one is needed. ``code`` is stable for pages; ``status`` is the answer's."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message

    @property
    def close_code(self) -> int:
        """The code a refused socket closes with: 4403 for a feature that is
        off for the person (Spaces S2-11), 4003 for every other refusal."""
        return 4403 if self.code == "feature_off" else 4003


@dataclass(frozen=True)
class Binding:
    """What a request is bound to: its space, and its asker when the sign-in
    is an account (None for a sign-in Kazma has no account for, which only
    an install that does not enforce Spaces lets through)."""

    space_id: str
    asker: str | None


def account_of(principal: dict[str, Any] | None) -> str | None:
    """The account id behind *principal*, or None when it is no account.

    * the shared secret, an API token, the loopback auto-login and a session
      made by signing in with the secret: the owner's account. An API token
      keeps the role it was made with; it is still the owner's.
    * a session of a local user: that user's account, while it is enabled.
    * anything else (an OIDC session, a session with no user): None.
    """
    if not principal:
        return None
    source = principal.get("source")
    if source in ("secret", "api_token"):
        return OWNER_ACCOUNT_ID
    if source != "session":
        return None
    if principal.get("actor") in _OWNER_SESSION_ACTORS:
        return OWNER_ACCOUNT_ID
    user_id = str(principal.get("user_id") or "")
    if principal.get("actor") == "oidc":
        return None
    if user_id:
        for account in identity.list_accounts():
            if str(account.get("legacy_user_id") or "") == user_id:
                return account["id"] if account.get("enabled", True) else None
    if user_id == _SECRET_USER_ID and principal.get("actor") == "login":
        # Only a sign-in with the install's secret mints this session; a
        # local user holding that id was found above and is themselves.
        return OWNER_ACCOUNT_ID
    return None


def named_space(headers: Any, query_params: Any) -> str | None:
    """The space a request names, or None. The header wins; the query value
    serves a download, an image or an event stream."""
    raw = (headers.get(SPACE_HEADER) or "").strip() or (query_params.get(SPACE_QUERY) or "").strip()
    return raw or None


def space_for_socket(principal: dict[str, Any] | None, websocket: Any) -> Binding:
    """The space and asker of a WebSocket handshake, or :class:`SpaceRefused`.

    A socket is a request for a space's data like any other (Spaces S1-08):
    the page names its space in the address (``?space=``), membership is
    checked at the handshake, and the socket then stays in that space. On an
    install that does not enforce Spaces a socket that names none runs in the
    sign-in's own tenant, as before. Blocking: the identity store.
    """
    from kazma_core.tenant_isolation import principal_tenant_id

    legacy = principal_tenant_id(principal) or OWNER_SPACE_ID
    named = named_space(websocket.headers, websocket.query_params)
    try:
        binding = space_for_request(principal, named, legacy_tenant=legacy, needs_space=True)
        # The tenant middleware is HTTP only: a socket of a feature the
        # person has not got is refused here (Spaces S2-11).
        refused = _socket_feature_refusal(str(websocket.url.path), binding)
        if refused:
            raise SpaceRefused("feature_off", 403, refused)
        return binding
    except identity.IdentityUnavailable:
        if spaces_enforced() or (named is not None and named != legacy):
            # Membership cannot be checked: never guessed.
            raise SpaceRefused(
                "identity_unavailable", 503, "Accounts cannot be read right now. Try again."
            ) from None
        logger.warning("[spaces] the identity store is unreadable; the socket runs with no asker bound")
        return Binding(space_id=legacy, asker=None)


def _socket_feature_refusal(path: str, binding: Binding) -> str | None:
    """Why the socket at *path* is not for *binding*'s person: a feature of
    the socket (``route_scopes.features_of_route``, method ``WEBSOCKET``)
    they have not got, or None. Images and voice's admission for live voice
    (``features.ADMISSIONS``). No read unless Spaces is enforced and the
    socket has a feature. Blocking (the identity store)."""
    if not spaces_enforced() or not binding.asker:
        return None
    from kazma_core.spaces.features import work_refusal

    from kazma_ui.route_scopes import features_of_route

    for feature in features_of_route(path, "WEBSOCKET"):
        refused = work_refusal(feature, binding.asker)
        if refused:
            return refused
    return None


@contextmanager
def bound_to(binding: Binding) -> Iterator[None]:
    """Run the block in *binding*'s space, as its asker when it has one."""
    from kazma_core.tenant_context import tenant_scope

    if binding.asker is not None and valid_space_id(binding.space_id):
        with bind_scope(binding.space_id, binding.asker):
            yield
    else:
        with tenant_scope(binding.space_id):
            yield


def other_authors(authors: Iterable[str], viewer: str | None) -> dict[str, str]:
    """``account id -> name`` for the authors of a chat's messages who are
    not *viewer*: what the page shows beside another member's message
    (Spaces S1-08). Empty on an install that does not enforce Spaces, and
    for a chat only the viewer wrote in, so one person's chat shows no
    names. Reads the identity store: off the loop."""
    wanted = {str(a) for a in authors if a and a != viewer}
    if not wanted or not spaces_enforced():
        return {}
    names = {a["id"]: str(a.get("display_name") or a.get("name") or a["id"])
             for a in identity.list_accounts()}
    return {a: names.get(a, a) for a in wanted}


def spaces_for_account(account_id: str) -> list[dict[str, Any]]:
    """The spaces an account may switch between, for the header's switcher:
    id, name, kind, whether it is the account's personal space, its role."""
    return [
        {
            "id": space["id"],
            "name": space.get("name") or "",
            "kind": space.get("kind") or "sandboxed",
            "personal": space.get("personal_of") == account_id,
            "role": space.get("role") or "member",
        }
        for space in identity.spaces_of(account_id)
    ]


def must_name_its_space(method: str, path: str, route_scope: str | None) -> bool:
    """Whether a request of *route_scope* must name its space (Spaces S1-06).

    A ``space`` route must, except a page: a GET outside ``/api/`` and the
    sockets. A page that names none (a reload after the sidebar moved the
    tab, a bookmark) is drawn for the person's own first space, and the
    page's script (``space_scope.js``) loads it again for the space the tab
    remembers. Refused, the browser showed an error and the tab could never
    get back to its space.
    """
    if route_scope != "space":
        return False
    page = method.upper() in ("GET", "HEAD") and not path.startswith(("/api/", "/ws/"))
    return not page


def space_for_request(
    principal: dict[str, Any] | None,
    named: str | None,
    *,
    legacy_tenant: str,
    needs_space: bool,
) -> Binding:
    """The space and asker of a request, or :class:`SpaceRefused`.

    *legacy_tenant* is what the older tenant middleware worked out (a JWT or
    session tenant, else ``default``); it is the answer for a request that
    names no space on an install that does not enforce Spaces. *needs_space*
    says the route reads a space's data (``route_scopes``): only such a route
    is refused for naming none.
    """
    enforced = spaces_enforced()
    legacy = legacy_tenant or OWNER_SPACE_ID
    if named is not None and not enforced and named == legacy:
        # The space the request would run in anyway: as if it named none.
        named = None
    account = account_of(principal)
    if account is None:
        if principal is not None and enforced:
            raise SpaceRefused(
                "no_account", 403,
                "This sign-in is not an account of this Kazma. Ask its admin for one.",
            )
        if named is not None and principal is not None:
            # A sign-in with no account is a member of nothing.
            raise SpaceRefused("not_a_member", 403, "You are not a member of that space.")
        if enforced and needs_space and named is not None and named != legacy:
            # No sign-in at all (sign-in switched off) cannot be checked
            # against a space: a space route that names another one is
            # refused, never answered from the space it would run in.
            raise SpaceRefused("not_a_member", 403, "You are not a member of that space.")
        return Binding(space_id=legacy, asker=None)
    if named is not None:
        # One answer for a space that does not exist and one the caller is
        # not in: the refusal never says which.
        if not valid_space_id(named) or not identity.is_member(account, named):
            raise SpaceRefused("not_a_member", 403, "You are not a member of that space.")
        return Binding(space_id=named, asker=account)
    if not enforced:
        return Binding(space_id=legacy, asker=account)
    own = identity.spaces_of(account)
    if len(own) == 1:
        return Binding(space_id=own[0]["id"], asker=account)
    if not needs_space and own:
        # A route about the install or the person runs in their first space
        # (the personal one) for the stores that still key by tenant.
        return Binding(space_id=own[0]["id"], asker=account)
    if not own:
        raise SpaceRefused("no_space", 403, "This account is in no space yet.")
    raise SpaceRefused(
        "space_required", 400,
        "This request must say which space it is for.",
    )
