"""The matrix cases of Spaces S1-17, part c: GitHub (``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own GitHub router behind the
real tenant middleware and the auth middleware's role check (A and B are
members, so operators: connecting and disconnecting a space's own GitHub is
theirs, and the GitHub App the admin's -- the review of S1-17c), or through the
product's own tools, on a real vault of the case's own.

B's space connects its own GitHub account with a token, through the product's
own route; the token names B's marker. The owner's space has every sign-in
the install had before spaces: a token stored as an older install keeps it (a
global vault row, which every tenant's read falls back to), the GitHub App's
installation token and ``GITHUB_TOKEN`` / ``GITHUB_PAT`` in the process
environment (decision N26: they stay the owner's). A request or tool call of
A's never shows B's marker, never changes what B's or the owner's space holds,
and each space still holds its own token after it. A starts with no account
connected: every request it makes to GitHub carries no token at all, and the
GitHub tools refuse -- the owner's sign-in is never lent to it. Then A
connects its own token, and every request carries A's token alone. Nothing reaches the network: GitHub is stood in for, and the
stand-in records the space each request was sent from and the token it
carried; the workspace's repository is stood in for too (it is S1-19's).
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-17"
_ORIGIN = "http://testserver"

#: The owner's sign-ins from before spaces: never sent from another space.
OWNER_TOKEN = "owner-stored-token-q7"
OWNER_ENV_TOKEN = "owner-env-token-q7"
OWNER_PAT_ENV = "owner-pat-env-q7"
OWNER_APP_TOKEN = "owner-app-token-q7"
OWNER_TOKENS = (OWNER_TOKEN, OWNER_ENV_TOKEN, OWNER_PAT_ENV, OWNER_APP_TOKEN)

#: The owner's GitHub App, configured before spaces.
_OWNER_APP_KEYS = ("connectors.github.app_id", "connectors.github.app_installation_id")


class World:
    """The GitHub router in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, marks: Any, sent: list[Any],
                 role_refused: list[tuple[str, str]]) -> None:
        self.matrix = matrix
        self.client = client
        self.marks = marks
        #: Every request to GitHub: (the space it was sent from, its token).
        self.sent = sent
        #: The requests the role check refused (``Matrix.role_checked``).
        self.role_refused = role_refused

    def as_who(self, who: str) -> Any:
        if who == "owner":
            return scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)
        return {"a": self.matrix.as_a, "b": self.matrix.as_b}[who]()

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        space = {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: space, "Origin": _ORIGIN,
                   "X-Requested-With": "XMLHttpRequest"}
        return self.client.request(method, path, headers=headers, **kwargs)

    def held(self, who: str) -> dict[str, Any]:
        """What *who*'s space holds of its GitHub sign-in."""
        from kazma_core.config_store import get_config_store
        from kazma_gateway.routers.github_client import get_github_token, is_oauth_connected

        with self.as_who(who):
            out: dict[str, Any] = {"token": get_github_token(), "oauth": is_oauth_connected()}
        if who == "owner":
            out.update({key: get_config_store().get(key) for key in _OWNER_APP_KEYS})
            out["env"] = (os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_PAT"))
        return out

    def connect(self, who: str) -> None:
        """*who*'s space connects its own GitHub token, through the route."""
        mark = {"a": self.marks.a, "b": self.marks.b}[who]
        saved = self.call(who, "POST", "/api/github/token", json={"token": _token_of(mark)})
        assert saved.status_code == 200, saved.text[:300]
        assert self.held(who)["token"] == _token_of(mark), self.held(who)

    def tool(self, who: str, name: str, **kwargs: Any) -> str:
        from tests._chat_space_cases import _registry

        tool = _registry().get_tool(name)
        assert tool is not None, name

        async def as_them() -> str:
            from kazma_core.agent.tool_refusal import RefusedBeforeEffect

            with self.as_who(who):
                try:
                    return str(await tool.func(**kwargs))
                except RefusedBeforeEffect as refused:  # A's own refusal is A's answer
                    return str(refused)

        return self.client.portal.call(as_them)


def _token_of(mark: str) -> str:
    return f"ghp_space_{mark}"


@contextmanager
def world(matrix: Matrix, *, connect_a: bool = True) -> Iterator[World]:
    """Spaces enforced; the GitHub router behind the real tenant middleware,
    a real vault of the case's own, GitHub and the workspace's repository
    stood in for; the owner's sign-ins from before spaces planted, B's own
    token connected through the product's route, and A's unless told not."""
    import kazma_core.git_identity as git_identity
    from kazma_core.config_store import get_config_store
    from kazma_core.security import vault as vault_mod
    from kazma_gateway.routers import github, github_client

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    sent: list[Any] = []

    def stand_in(request: httpx.Request) -> httpx.Response:
        bound = scope.current_scope()
        sent.append((bound.space_id if bound else None,
                     request.headers.get("Authorization", "").removeprefix("Bearer ").removeprefix("token ")))
        return httpx.Response(503, json={"message": "stand-in: GitHub is not reached"})

    async def enter(self: Any) -> Any:
        self._client = httpx.AsyncClient(base_url="https://api.github.com", headers=self._headers(),
                                         transport=httpx.MockTransport(stand_in))
        return self

    store = get_config_store()
    kept = {key: store.get(key) for key in ("connectors.github.token", *_OWNER_APP_KEYS)}
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setenv("KAZMA_VAULT_KEY", "k" * 64)
        patch.setenv("KAZMA_PUBLIC_URL", _ORIGIN)
        patch.setenv("GITHUB_TOKEN", OWNER_ENV_TOKEN)
        patch.setenv("GITHUB_PAT", OWNER_PAT_ENV)
        v = vault_mod.SecretVault(db_path=str(Path(tmp) / "vault.db"))
        patch.setattr(vault_mod, "get_vault", lambda: v)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))
        patch.setattr(github_client.GitHubClient, "__aenter__", enter)
        patch.setattr(github_client, "resolve_repo", lambda cwd=None: ("octo", "planted-repo"))
        patch.setattr(git_identity, "get_app_installation_token", lambda: OWNER_APP_TOKEN)
        # A saved token is bound to the process environment in the owner's
        # space: kept to the case.
        patch.setattr(github, "bind_github_token_to_process", lambda token: None)
        marks = matrix.plant("gh")
        # Stored with nothing bound: a global vault row, as an install from
        # before spaces keeps it -- the row every tenant's read falls back to.
        # (Before Spaces: once enforced the vault writes no secret for no one.)
        with pytest.MonkeyPatch.context() as before_spaces:
            before_spaces.setattr(scope, "_enforced", False)
            store.batch_set([("connectors.github.token", OWNER_TOKEN, "connectors"),
                             ("connectors.github.app_id", "4242", "connectors"),
                             ("connectors.github.app_installation_id", "77", "connectors")])
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        role_refused = Matrix.role_checked(app)
        app.include_router(github.router)
        try:
            with TestClient(app, base_url=_ORIGIN) as client:
                made = World(matrix, client, marks, sent, role_refused)
                made.connect("b")
                if connect_a:
                    made.connect("a")
                sent.clear()
                yield made
        finally:
            v.close()
            with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
                for key, value in kept.items():
                    if value is None:
                        store.delete(key)
                    else:
                        store.set(key, value, category="connectors")
            for key in [k for keys in store.get_all().values() for k in keys]:
                if key.startswith((f"tenant.{Matrix.SPACE_A}.", f"tenant.{Matrix.SPACE_B}.")):
                    store.delete(key)


def _in_world(run: Callable[[World], None], *, connect_a: bool = True) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix, connect_a=connect_a) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def _each_holds_its_own(w: World) -> None:
    """B's space holds B's own token and the owner's space the owner's: a
    sign-in kept for the install would hold one of them for all."""
    assert w.held("b")["token"] == _token_of(w.marks.b), w.held("b")
    assert w.held("owner")["token"] == OWNER_TOKEN, w.held("owner")


def _only_own_token(w: World) -> None:
    """Every request to GitHub A's request made was sent from A's space with
    A's token: never B's, never one of the owner's."""
    for space, token in w.sent:
        assert space == Matrix.SPACE_A and token == _token_of(w.marks.a), (space, token)
        assert token not in OWNER_TOKENS and w.marks.b not in token, (space, token)


def _route(target: str, body: Any = None, *, check: Callable[[World, Any], None] | None = None,
           the_admins: bool = False) -> None:
    """A case for *target*: as A, B's and the owner's spaces hold what they
    held, B's marker is never shown, and every request to GitHub carried A's
    own token; *check* the positive for A. A member reaches it, unless it is
    *the_admins* (an install piece): then the role check refuses A."""
    method, path = target.split(" ", 1)
    concrete = path.replace("{number}", "1")

    def run(w: World) -> None:
        before = {who: w.held(who) for who in ("b", "owner")}
        answer = w.call("a", method, concrete, **({"json": body} if body is not None else {}))
        assert w.role_refused == ([(method, concrete)] if the_admins else []), w.role_refused
        assert answer.status_code < 500, answer.text[:300]
        assert w.marks.b not in answer.text, answer.text[:300]
        assert not any(token in answer.text for token in OWNER_TOKENS), answer.text[:300]
        assert {who: w.held(who) for who in ("b", "owner")} == before, "A's request changed another space's"
        _each_holds_its_own(w)
        _only_own_token(w)
        if check is not None:
            check(w, answer)

    run.__name__ = "github_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    case(target, ITEM)(_in_world(run))


def _has_own_token(w: World, answer: Any) -> None:
    assert answer.json().get("has_token") is True, answer.text[:300]


def _saved_in_a(w: World, answer: Any) -> None:
    assert w.held("a")["token"] == "ghp_a_new_token_for_a"


def _cleared_in_a(w: World, answer: Any) -> None:
    assert w.held("a") == {"token": "", "oauth": False}


def _app_is_the_admins(w: World, answer: Any) -> None:
    """The GitHub App is the install's: a member is refused it by role (and
    an admin in a space other than the owner's by the route,
    ``test_the_github_app_is_the_owners_spaces_only``)."""
    assert answer.status_code == 403, answer.text[:300]


def _started_in_a(w: World, answer: Any) -> None:
    """Sign in with GitHub started in A's space, as A."""
    from kazma_core.security import oauth_attempts
    from kazma_core.security.oauth_attempts import binding_cookie_name

    assert answer.status_code == 200 and answer.json().get("authorize_url"), answer.text[:300]
    state = next(c.value for c in w.client.cookies.jar if c.value and binding_cookie_name(c.value) == c.name)
    attempt = oauth_attempts.take(state, bound_state=state, providers=("github",))
    assert (attempt["space"], attempt["asker"]) == (Matrix.SPACE_A, w.matrix.account_a), attempt


def _github_app_registered(run: Callable[[World], None]) -> Callable[[World], None]:
    """*run* with the install's GitHub OAuth App registered (the install's,
    in every space, as the mail sign-ins' client)."""
    def with_app(w: World) -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("GITHUB_OAUTH_CLIENT_ID", "install-client")
            patch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "install-secret")
            run(w)

    return with_app


# ── the sign-in and its settings ─────────────────────────────────────────

_route("POST /api/github/token", {"token": "ghp_a_new_token_for_a"}, check=_saved_in_a)
_route("POST /api/github/app/config", {"app_id": "999", "app_installation_id": "1"}, check=_app_is_the_admins,
       the_admins=True)
_route("GET /api/github/status")
_route("GET /api/github/oauth/status", check=_has_own_token)
_route("GET /api/github/oauth/setup")
_route("POST /api/github/oauth/revoke", check=_cleared_in_a)


def _oauth_start(w: World) -> None:
    before = {who: w.held(who) for who in ("b", "owner")}
    answer = w.call("a", "POST", "/api/github/oauth/start", json={})
    assert w.role_refused == [], w.role_refused
    assert w.marks.b not in answer.text and {who: w.held(who) for who in ("b", "owner")} == before
    _each_holds_its_own(w)
    _started_in_a(w, answer)


case("POST /api/github/oauth/start", ITEM)(_in_world(_github_app_registered(_oauth_start)))

# ── the repository's data: nothing of anyone else's, then A's own ───────


def _nothing_of_others_sent(w: World) -> None:
    """A has connected nothing: every request it made to GitHub carried no
    token at all -- never B's, never one of the owner's."""
    for space, token in w.sent:
        assert space == Matrix.SPACE_A and token == "", (space, token)


def _data_route(target: str, body: Any = None) -> None:
    """A data route as A: with nothing connected, nothing of anyone else's
    reaches GitHub; with A's own token connected, A's alone."""
    method, path = target.split(" ", 1)

    def ask(w: World) -> None:
        before = {who: w.held(who) for who in ("b", "owner")}
        answer = w.call("a", method, path.replace("{number}", "1"),
                        **({"json": body} if body is not None else {}))
        assert w.role_refused == [], w.role_refused
        assert answer.status_code < 500, answer.text[:300]
        assert w.marks.b not in answer.text, answer.text[:300]
        assert not any(token in answer.text for token in OWNER_TOKENS), answer.text[:300]
        assert {who: w.held(who) for who in ("b", "owner")} == before
        _each_holds_its_own(w)
        assert w.sent, answer.text[:300]

    def run(w: World) -> None:
        ask(w)
        _nothing_of_others_sent(w)
        w.connect("a")
        w.sent.clear()
        ask(w)
        _only_own_token(w)

    run.__name__ = "github_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    case(target, ITEM)(_in_world(run, connect_a=False))


_data_route("GET /api/github/pulls")
_data_route("GET /api/github/pulls/{number}")
_data_route("PUT /api/github/pulls/{number}/merge", {"merge_method": "squash"})
_data_route("POST /api/github/issues", {"title": "an issue of A's", "body": "b"})
_data_route("GET /api/github/issues")
_data_route("GET /api/github/commits")
_data_route("GET /api/github/workflows")
_data_route("GET /api/github/branches")
_data_route("GET /api/github/releases")
_data_route("GET /api/github/repos")
_data_route("GET /api/github/activity")


# ── the tools: refused with nothing connected, then A's own token ────────

_TOOL_CALLS: dict[str, dict[str, Any]] = {
    "github_list_issues": {},
    "github_create_issue": {"title": "an issue of A's", "body": "b"},
    "github_comment_issue": {"number": 1, "body": "a comment of A's"},
    "github_create_pr": {"title": "a PR of A's", "body": "b", "head": "a-branch"},
    "github_merge_pr": {"number": 1},
}

#: What a GitHub tool answers in a space that has connected no account.
NO_ACCOUNT = "this space has no GitHub account connected"


def _tool_case(name: str) -> Callable[[Matrix], None]:
    def ask(w: World) -> str:
        before = {who: w.held(who) for who in ("b", "owner")}
        answer = w.tool("a", name, **_TOOL_CALLS[name])
        assert w.marks.b not in answer and not any(token in answer for token in OWNER_TOKENS), answer[:300]
        assert {who: w.held(who) for who in ("b", "owner")} == before
        _each_holds_its_own(w)
        return answer

    def run(w: World) -> None:
        answer = ask(w)
        assert NO_ACCOUNT in answer and w.sent == [], (answer[:300], w.sent)
        w.connect("a")
        w.sent.clear()
        answer = ask(w)
        assert w.sent, f"{name} sent nothing: {answer[:300]}"
        _only_own_token(w)

    run.__name__ = f"tool_{name}"
    return _in_world(run, connect_a=False)


for _name in sorted(_TOOL_CALLS):
    case(f"tool:{_name}", ITEM)(_tool_case(_name))
