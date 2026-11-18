"""Spaces S1-17, part c: the GitHub sign-in belongs to a space.

Guards act only where Spaces is enforced; one account behaves as before
(decision N26). The owner's space keeps today's sign-in -- its stored tokens,
the install's GitHub App and the process environment's ``GITHUB_TOKEN`` /
``GITHUB_PAT``. Every other space keeps only its own OAuth token and PAT
(``connected_accounts.space_setting_key``), and until it connects one, git
push and the GitHub tools refuse there. Enforced:

* a space with nothing connected reads no token at all -- never the owner's
  environment, App or a global vault row an older install keeps;
* a push (``_git_token``) uses the App only in the owner's space, and so
  does the retry of a push GitHub refused (the review of S1-17c: the retry
  minted the App's token whatever the space); every caller of the App's
  token asks the space first (a gate, the old retry as its control);
* a token saved in another space never reaches the process environment;
* Sign in with GitHub finishes in the space it started in (the callback is
  open before sign-in: with no binding it landed in the owner's space);
* the GitHub App is the owner's space's;
* a member connects and disconnects their space's own GitHub, mailbox and
  calendar (they were admin-only, and a member is an operator: the review);
  the install's pieces stay the admin's, held equal to ``route_scopes``;
* Disconnect deletes the keys, every copy the vault keeps (two on live), and
  says so when it could not: written empty, the settings store refused to
  blank them and Disconnect cleared nothing (found on the way); deleted in
  the request's scope only, an encrypted copy stayed in the other; a failed
  delete answered "disconnected" (the review);
* the GitHub client reads its token off the event loop.

Each guard is taken out as a control. The route and tool cases are in
``tests/_github_space_cases.py``.
"""

from __future__ import annotations

import ast
import contextlib
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._github_space_cases import ITEM, OWNER_APP_TOKEN, OWNER_ENV_TOKEN, OWNER_TOKEN, world
from tests._space_matrix import Matrix, load_cases

REPO = Path(__file__).resolve().parents[1]
CASES = load_cases()


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


def test_the_part_wrote_a_case_for_every_github_route_and_tool() -> None:
    ours = sorted(target for target, registered in CASES.items()
                  if registered.item == ITEM and ("/api/github" in target or target.startswith("tool:github_")))
    assert len(ours) == 23, ours
    assert "POST /api/github/repos/clone" not in ours  # a clone is S1-20's (the workspace)


# ── what a space reads ───────────────────────────────────────────────────


def _token_in(w: Any, who: str) -> str:
    from kazma_gateway.routers.github_client import get_github_token

    with w.as_who(who):
        return get_github_token()


def test_a_space_with_nothing_connected_reads_no_token(matrix) -> None:
    with world(matrix, connect_a=False) as w:
        assert _token_in(w, "a") == ""
        assert _token_in(w, "owner") == OWNER_TOKEN  # the owner's sign-in stays the owner's
        w.connect("a")
        assert _token_in(w, "a").endswith(w.marks.a)


def test_control_one_sign_in_for_the_install_lends_the_owners(matrix, monkeypatch) -> None:
    """The sign-in as before part c: every space read the owner's keys and
    environment -- A, which connected nothing, used the owner's token."""
    from kazma_core.spaces import connected_accounts

    with world(matrix, connect_a=False) as w:
        monkeypatch.setattr(connected_accounts, "connected_space", lambda: OWNER_SPACE_ID)
        assert _token_in(w, "a") in (OWNER_TOKEN, OWNER_APP_TOKEN, OWNER_ENV_TOKEN)


def test_with_no_space_bound_no_sign_in_is_read_once_enforced(matrix) -> None:
    from kazma_core.spaces.scope import NoSpaceBound
    from kazma_gateway.routers.github_client import get_github_token

    with world(matrix):
        with pytest.raises(NoSpaceBound):
            get_github_token()


def test_one_account_reads_the_sign_in_as_before(monkeypatch) -> None:
    """Not enforced: the owner's keys, the App and the environment, in that
    order, for every tenant."""
    import kazma_core.git_identity as git_identity
    from kazma_core.tenant_context import tenant_scope
    from kazma_gateway.routers.github_client import get_github_token, github_key

    from kazma_core.config_store import get_config_store

    store = get_config_store()
    assert store.get("connectors.github.token") is None and store.get("connectors.github.oauth_token") is None
    monkeypatch.setenv("GITHUB_TOKEN", "env-token-as-before")
    monkeypatch.setattr(git_identity, "get_app_installation_token", lambda: None)
    with tenant_scope("someone-else"):
        assert github_key("connectors.github.token") == "connectors.github.token"
        assert get_github_token() == "env-token-as-before"
    monkeypatch.setattr(git_identity, "get_app_installation_token", lambda: "app-token-as-before")
    with tenant_scope("someone-else"):
        assert get_github_token() == "app-token-as-before"


# ── a push ───────────────────────────────────────────────────────────────


def _push_token(w: Any, who: str) -> str:
    from kazma_core.agent.tool_refusal import RefusedBeforeEffect
    from kazma_skills.native.git_github_manager.tools import _git_token

    async def ask() -> str:
        with w.as_who(who):
            try:
                return await _git_token()
            except RefusedBeforeEffect:
                return "refused"

    return w.client.portal.call(ask)


def test_a_push_uses_the_spaces_own_account_or_none(matrix) -> None:
    with world(matrix, connect_a=False) as w:
        assert _push_token(w, "a") == "refused"
        assert _push_token(w, "owner") == OWNER_APP_TOKEN  # the owner's space: App first, as before
        w.connect("a")
        assert _push_token(w, "a").endswith(w.marks.a)


def test_control_the_app_first_in_every_space(matrix, monkeypatch) -> None:
    """The push as before part c: the GitHub App's token came first in every
    space -- a friend's push acted as the owner."""
    import kazma_skills.native.git_github_manager.tools as tools

    with world(matrix, connect_a=False) as w:
        monkeypatch.setattr(tools, "_in_the_owners_space", lambda: True)
        assert _push_token(w, "a") == OWNER_APP_TOKEN


#: The App's token minted fresh for the retry of a refused push.
OWNER_APP_FRESH = "owner-app-fresh-q7"


def _a_refused_push(matrix: Matrix, monkeypatch, who: str) -> tuple[list[str], list[str], str, str]:
    """*who* pushes and GitHub refuses it: the token each push carried, the
    App tokens minted, what the tool answered, and A's own token."""
    from tests._github_space_cases import _token_of

    import base64
    import subprocess

    import kazma_core.git_identity as git_identity
    from kazma_skills.native.git_github_manager import tools

    minted: list[str] = []
    pushes: list[str] = []

    def mint(**_: Any) -> str:
        minted.append(OWNER_APP_FRESH)
        return OWNER_APP_FRESH

    async def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        if "config" in argv:
            return subprocess.CompletedProcess(argv, 0, "https://github.com/octo/planted-repo.git\n", "")
        if "push" in argv:
            header = dict(kwargs.get("env") or {}).get("GIT_CONFIG_VALUE_0", "").rsplit(" ", 1)[-1]
            pushes.append(base64.b64decode(header).decode().removeprefix("x-access-token:"))
            return subprocess.CompletedProcess(argv, 1, "", "remote: Permission denied (403)")
        return subprocess.CompletedProcess(argv, 0, "", "")

    with world(matrix) as w:
        monkeypatch.setattr(tools, "_get_workspace", lambda: "/tmp/planted-repo")
        monkeypatch.setattr(tools, "run_off_loop", run)
        monkeypatch.setattr(git_identity, "invalidate_app_token_cache", lambda: None)
        monkeypatch.setattr(git_identity, "mint_app_installation_token", mint)

        async def push() -> str:
            with w.as_who(who):
                return await tools.git_push(branch="main")

        answer = w.client.portal.call(push)
        a_token = _token_of(w.marks.a)
    return pushes, minted, answer, a_token


def test_a_refused_push_is_never_tried_again_as_the_owners_app(matrix, monkeypatch) -> None:
    pushes, minted, answer, a_token = _a_refused_push(matrix, monkeypatch, "a")
    assert pushes == [a_token] and minted == [], (pushes, minted)
    assert "Auth failed" in answer


def test_the_owners_refused_push_is_tried_again_with_a_fresh_app_token(matrix, monkeypatch) -> None:
    pushes, minted, _, _ = _a_refused_push(matrix, monkeypatch, "owner")
    assert pushes == [OWNER_APP_TOKEN, OWNER_APP_FRESH] and minted == [OWNER_APP_FRESH], (pushes, minted)


def test_control_the_retry_as_before_pushes_as_the_owners_app(matrix, monkeypatch) -> None:
    """The retry as before the review: the first push asked the space, the
    retry minted the App's token whatever it -- A's push, refused with A's
    own token, went again as the owner's App."""
    import kazma_core.git_identity as git_identity
    from kazma_skills.native.git_github_manager import tools

    async def as_before(*, fresh: bool = False) -> str:
        if fresh:
            git_identity.invalidate_app_token_cache()
            return git_identity.mint_app_installation_token(force=True) or ""
        return OWNER_APP_TOKEN if tools._in_the_owners_space() else ""

    monkeypatch.setattr(tools, "_owners_app_token", as_before)
    pushes, minted, _, a_token = _a_refused_push(matrix, monkeypatch, "a")
    assert pushes == [a_token, OWNER_APP_FRESH] and minted == [OWNER_APP_FRESH], pushes


# ── every caller of the App's token asks the space ───────────────────────

#: The App's token, by the names that hand it out.
_APP_TOKEN = ("get_app_installation_token", "mint_app_installation_token")
#: What asks whether this work is the owner's space's.
_ASKS_THE_SPACE = ("_in_the_owners_space", "in_the_owners_space")
#: A function that uses the App's token without asking the space itself, and why.
_APP_TOKEN_DECLARED = {
    "kazma-core/kazma_core/git_identity.py:get_app_installation_token":
        "the App's own cache: it mints through mint_app_installation_token; its callers ask",
}


def _own_nodes(func: ast.AST) -> list[ast.AST]:
    """The nodes of *func*'s own body: never a function, lambda or class
    defined in it (that one is judged on its own)."""
    found: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(func))
    while stack:
        node = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda | ast.ClassDef):
            continue
        found.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return found


def _app_token_findings(sources: dict[str, str]) -> list[str]:
    """Every function (a nested one too) that names the App's token -- calls
    it, or hands it to a thread -- without asking the space before."""
    findings = []
    for rel, source in sorted(sources.items()):
        if not any(name in source for name in _APP_TOKEN):
            continue
        for func in ast.walk(ast.parse(source)):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            own = _own_nodes(func)
            uses = [n.lineno for n in own if (isinstance(n, ast.Name) and n.id in _APP_TOKEN)
                    or (isinstance(n, ast.Attribute) and n.attr in _APP_TOKEN)]
            if not uses or f"{rel}:{func.name}" in _APP_TOKEN_DECLARED:
                continue
            asks = [n.lineno for n in own if isinstance(n, ast.Call)
                    and ast.unparse(n.func).rpartition(".")[2] in _ASKS_THE_SPACE]
            if not asks or min(asks) > min(uses):
                findings.append(f"{rel}:{func.name}")
    return sorted(findings)


def test_every_caller_of_the_apps_token_asks_the_space() -> None:
    sources = _product_sources()
    assert _app_token_findings(sources) == []
    for where in _APP_TOKEN_DECLARED:  # a declaration that excuses nothing fails
        rel, _, name = where.partition(":")
        assert f"def {name}(" in sources[rel], where


def test_control_the_retry_as_before_fails_the_gate() -> None:
    """The retry as before the review, and a caller that asks only after
    taking the token."""
    planted = {"kazma-skills/kazma_skills/planted.py": (
        "import asyncio\n"
        "async def _git_sync():\n"
        "    async def _refresh_and_retry(prev_output):\n"
        "        from kazma_core.git_identity import invalidate_app_token_cache, mint_app_installation_token\n"
        "        invalidate_app_token_cache()\n"
        "        return await asyncio.to_thread(mint_app_installation_token, force=True)\n"
        "    return await _refresh_and_retry('')\n"
        "def late():\n"
        "    import kazma_core.git_identity as gi\n"
        "    token = gi.get_app_installation_token()\n"
        "    return token if in_the_owners_space() else ''\n")}
    assert _app_token_findings(planted) == ["kazma-skills/kazma_skills/planted.py:_refresh_and_retry",
                                            "kazma-skills/kazma_skills/planted.py:late"]


# ── a saved token and the process environment ────────────────────────────


def _bound_to_the_environment(matrix: Matrix, monkeypatch) -> list[str]:
    from kazma_gateway.routers import github

    bound: list[str] = []
    with world(matrix, connect_a=False) as w:
        monkeypatch.setattr(github, "bind_github_token_to_process", bound.append)
        w.connect("a")
    return bound


def test_a_token_saved_in_another_space_never_reaches_the_environment(matrix, monkeypatch) -> None:
    assert _bound_to_the_environment(matrix, monkeypatch) == []


def test_control_every_saved_token_bound_to_the_environment(matrix, monkeypatch) -> None:
    """As before part c: A's token became the process environment's, which
    every space read when it had none of its own."""
    from kazma_gateway.routers import github_client

    monkeypatch.setattr(github_client, "in_the_owners_space", lambda: True)
    assert len(_bound_to_the_environment(matrix, monkeypatch)) == 1


# ── Sign in with GitHub finishes in the space it started in ──────────────

SHARED_GH = "matrix-gh-shared"


def _signed_in_with_github(matrix: Matrix, monkeypatch) -> dict[str, str]:
    """A starts Sign in with GitHub in a shared space (not A's first), and
    GitHub sends the browser back to the callback, which is open before
    sign-in and names no space: the OAuth token each space then holds."""
    from urllib.parse import parse_qs, urlparse

    from kazma_core.spaces import identity
    from kazma_gateway.routers import github_client

    from kazma_ui import space_binding

    async def exchanged(code: str, redirect_uri: str) -> dict[str, Any]:
        return {"access_token": "gho_shared_space_token", "scope": "repo", "token_type": "bearer"}

    with world(matrix) as w:
        identity.create_space("Shared GitHub", created_by=matrix.account_b, space_id=SHARED_GH)
        identity.add_member(SHARED_GH, matrix.account_a)
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_ID", "install-client")
        monkeypatch.setenv("GITHUB_OAUTH_CLIENT_SECRET", "install-secret")
        monkeypatch.setattr(github_client, "exchange_code_for_token", exchanged)
        started = w.client.post("/api/github/oauth/start", json={}, headers={
            "x-matrix-person": "a", space_binding.SPACE_HEADER: SHARED_GH,
            "Origin": "http://testserver", "X-Requested-With": "XMLHttpRequest"})
        assert started.status_code == 200, started.text[:300]
        state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
        back = w.client.get(f"/api/github/oauth/callback?code=c&state={state}", follow_redirects=False)
        assert back.status_code == 302 and "github_oauth=ok" in back.headers["location"], back.headers
        held: dict[str, str] = {}
        for name, bound in (("shared", lambda: scope.bind_scope(SHARED_GH, matrix.account_a)),
                            ("a", matrix.as_a), ("owner", lambda: scope.bind_scope(OWNER_SPACE_ID,
                                                                                     OWNER_ACCOUNT_ID))):
            with bound():
                held[name] = github_client.get_github_token()
        return held


def test_sign_in_with_github_lands_in_the_space_it_started_in(matrix, monkeypatch) -> None:
    held = _signed_in_with_github(matrix, monkeypatch)
    assert held["shared"] == "gho_shared_space_token"
    assert held["owner"] == OWNER_TOKEN and held["a"] != "gho_shared_space_token"


def test_control_a_callback_that_binds_no_space_gives_the_owner_a_members_sign_in(matrix, monkeypatch) -> None:
    """The callback as before part c: open before sign-in and binding no
    space, it ran in the owner's space -- a member's GitHub account became
    the owner's sign-in."""
    import kazma_ui.oauth_sign_in as oauth_sign_in

    monkeypatch.setattr(oauth_sign_in, "attempt_scope", lambda attempt: contextlib.nullcontext())
    held = _signed_in_with_github(matrix, monkeypatch)
    assert held["owner"] == "gho_shared_space_token" and held["shared"] != "gho_shared_space_token"


# ── the GitHub App is the owner's space's ────────────────────────────────


def test_the_github_app_is_the_owners_spaces_only(matrix, monkeypatch) -> None:
    """A member (an operator) is refused the GitHub App by role -- it is the
    install's -- and so is an admin in any space but the owner's, by the
    route (N26)."""
    from kazma_core.config_store import get_config_store

    body = {"app_id": "999", "app_installation_id": "1"}
    with world(matrix) as w:
        assert w.call("a", "POST", "/api/github/app/config", json=body).status_code == 403
        monkeypatch.setitem(matrix.principal_a, "role", "admin")  # an admin, in a space of their own
        refused = w.call("a", "POST", "/api/github/app/config", json=body)
        assert refused.status_code == 409 and refused.json()["code"] == "owners_space_only"
        assert get_config_store().get("connectors.github.app_id") == "4242"
        from kazma_gateway.routers import github_client

        monkeypatch.setattr(github_client, "in_the_owners_space", lambda: True)  # control: the route as before
        assert w.call("a", "POST", "/api/github/app/config", json=body).status_code == 200
        assert get_config_store().get("connectors.github.app_id") == "999"


# ── a member connects their space's own accounts ─────────────────────────


@pytest.mark.parametrize(("method", "path"), (
    ("POST", "/api/github/token"), ("POST", "/api/github/oauth/start"), ("POST", "/api/github/oauth/revoke"),
    ("POST", "/api/email/gmail/connect"), ("POST", "/api/email/oauth/gmail/start"),
    ("POST", "/api/email/protocol/disconnect"), ("GET", "/api/email/status"),
    ("POST", "/api/calendar/oauth/google/start"), ("POST", "/api/calendar/oauth/microsoft/disconnect"),
))
def test_a_member_connects_their_spaces_own_accounts_once_enforced(method, path, monkeypatch) -> None:
    from kazma_core.security.platform_rbac import role_allows

    assert not role_allows("operator", path, method)  # not enforced: the admin's, as before
    monkeypatch.setattr(scope, "_enforced", True)
    assert role_allows("operator", path, method)
    assert not role_allows("viewer", path, method)


@pytest.mark.parametrize(("method", "path"), (
    ("POST", "/api/github/app/config"), ("POST", "/api/github/repos/clone"),
    ("POST", "/api/email/oauth/gmail/client"), ("POST", "/api/email/oauth/microsoft/client"),
    ("GET", "/api/email/presets"),
))
def test_the_installs_own_pieces_stay_the_admins(method, path, monkeypatch) -> None:
    from kazma_core.security.platform_rbac import role_allows

    for enforced in (False, True):
        monkeypatch.setattr(scope, "_enforced", enforced)
        assert not role_allows("operator", path, method), (path, enforced)
        assert role_allows("admin", path, method)


def test_the_install_pieces_are_the_install_routes() -> None:
    """Every route under the space-account prefixes is an install piece
    exactly when ``route_scopes`` classes it ``install``: a new install
    route there must not become a member's, nor a space route the admin's."""
    from kazma_core.security import platform_rbac
    from kazma_gateway.routers import github
    from kazma_ui import calendar_api, email_api
    from kazma_ui.route_scopes import INSTALL, OPEN, scope_of

    routes = [(method, route.path) for router in (github.router, email_api.router, email_api.protected_router,
                                                  calendar_api.router, calendar_api.protected_router)
              for route in router.routes for method in getattr(route, "methods", ()) or ()]
    under = [(m, p) for m, p in routes if platform_rbac._under(p, platform_rbac._SPACE_ACCOUNT_PREFIXES)]
    assert len(under) > 20, under
    for method, path in under:
        found = scope_of(method, path)
        if found == OPEN:  # a sign-in callback: before sign-in, no role
            continue
        assert (found == INSTALL) == platform_rbac._under(path, platform_rbac._INSTALL_ACCOUNT_PIECES), \
            (method, path, found)


@pytest.mark.parametrize("target", (
    "POST /api/github/oauth/revoke", "POST /api/github/oauth/start", "POST /api/email/gmail/disconnect",
    "POST /api/calendar/oauth/google/start", "GET /api/email/status",
))
def test_control_as_before_no_member_reaches_their_own_accounts(target, matrix, monkeypatch) -> None:
    """The role rule as before the review: every one the admin's, and a
    member is an operator -- the cases, which check the role as the auth
    middleware does, fail."""
    from kazma_core.security import platform_rbac

    monkeypatch.setattr(platform_rbac, "_connects_a_spaces_own_account", lambda path: False)
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


# ── Disconnect deletes, as it says ───────────────────────────────────────


def _one_account_disconnects(tmp_path, monkeypatch, *, before_revoke: Any = None) -> dict[str, Any]:
    """An install with one account saves a token and an App, with the shape
    live keeps -- two copies of its OAuth token, a global row and an older
    one under the tenant ``default`` -- then presses Disconnect: what the
    route answered and what the install still holds."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_core.security import vault as vault_mod
    from kazma_gateway.routers import github, github_client

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    monkeypatch.setattr(github, "bind_github_token_to_process", lambda token: None)
    store = get_config_store()
    app = FastAPI()
    app.include_router(github.router)
    try:
        with TestClient(app) as client:
            assert client.post("/api/github/token", json={"token": "ghp_one_account"}).status_code == 200
            github_client.store_oauth_token({"access_token": "gho_one_account", "scope": "repo"})
            v.store("cfg:connectors.github.oauth_token", "gho_older_default_copy", category="config",
                    tenant_id="default")
            store.set("connectors.github.app_private_key", "-----BEGIN KEY-----", category="connectors")
            dups = v.find_divergent_duplicates()  # the shape live keeps
            assert [d["name"] for d in dups] == ["cfg:connectors.github.oauth_token"], dups
            assert sorted(s["tenant_id"] or "global" for s in dups[0]["scopes"]) == ["default", "global"]
            if before_revoke is not None:
                before_revoke(v)
            answer = client.post("/api/github/oauth/revoke")
            return {"status": answer.status_code, "answer": answer.json(),
                    "token": github_client.get_github_token(),
                    "pat": store.get("connectors.github.token"),
                    "oauth": store.get("connectors.github.oauth_token"),
                    "app_key": store.get("connectors.github.app_private_key"),
                    "vault": sorted((r["name"], r["tenant"]) for r in v.list_secrets()
                                    if r["name"].startswith("cfg:connectors.github."))}
    finally:
        for key in ("connectors.github.token", "connectors.github.oauth_token", "connectors.github.oauth_scope",
                    "connectors.github.oauth_token_type", "connectors.github.app_private_key"):
            store.delete(key)
        v.close()


def test_disconnect_clears_the_sign_in_every_copy(tmp_path, monkeypatch) -> None:
    held = _one_account_disconnects(tmp_path, monkeypatch)
    assert held["status"] == 200 and held["answer"]["connected"] is False, held
    assert {k: held[k] for k in ("token", "pat", "oauth", "app_key", "vault")} == {
        "token": "", "pat": None, "oauth": None, "app_key": None, "vault": []}, held


def test_control_a_delete_in_the_requests_scope_leaves_the_other_copy(tmp_path, monkeypatch) -> None:
    """Disconnect as before the review: the settings store's delete removed
    the vault copy of the request's scope only, and the ``default`` copy --
    the one a web request read on live -- stayed, encrypted."""
    from kazma_core.config_store import get_config_store
    from kazma_gateway.routers import github_client

    def as_before(keys: list[str]) -> None:
        for key in keys:
            get_config_store().delete(key)

    monkeypatch.setattr(github_client, "forget_github_keys", as_before)
    held = _one_account_disconnects(tmp_path, monkeypatch)
    assert held["vault"] == [("cfg:connectors.github.oauth_token", "default")], held


def _a_locked_vault(v: Any) -> None:
    import sqlite3

    def refused(*args: Any, **kwargs: Any) -> bool:
        raise sqlite3.OperationalError("database is locked")

    v.delete = refused


def test_a_disconnect_that_cannot_delete_says_so(tmp_path, monkeypatch) -> None:
    held = _one_account_disconnects(tmp_path, monkeypatch, before_revoke=_a_locked_vault)
    assert held["status"] == 503 and held["answer"]["code"] == "disconnect_failed", held
    assert held["answer"].get("connected") is not False and held["vault"], held


def test_control_a_delete_that_swallows_its_failure_answers_disconnected(tmp_path, monkeypatch) -> None:
    """The delete as before the review: a failure was logged and the route
    answered "disconnected" over a token still held."""
    from kazma_gateway.routers import github_client

    real = github_client.forget_github_keys

    def swallowed(keys: list[str]) -> None:
        try:
            real(keys)
        except Exception:
            pass

    monkeypatch.setattr(github_client, "forget_github_keys", swallowed)
    held = _one_account_disconnects(tmp_path, monkeypatch, before_revoke=_a_locked_vault)
    assert held["status"] == 200 and held["answer"]["connected"] is False and held["vault"], held


def test_control_an_empty_write_does_not_clear_a_secret(tmp_path, monkeypatch) -> None:
    """Disconnect as before: it wrote the keys empty, and the settings store
    refuses to blank a stored secret -- the token stayed."""
    from kazma_core.config_store import get_config_store
    from kazma_core.security import vault as vault_mod

    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    store = get_config_store()
    try:
        store.set("connectors.github.token", "ghp_one_account", category="connectors")
        store.batch_set([("connectors.github.token", "", "connectors")])
        assert store.get("connectors.github.token") == "ghp_one_account"
    finally:
        store.delete("connectors.github.token")
        v.close()


# ── the source gate: the owner's environment and the sign-in's keys ──────

#: Every product file that reads ``GITHUB_TOKEN`` / ``GITHUB_PAT``, and why.
_ENV_READERS = {
    "kazma-core/kazma_core/spaces/connected_accounts.py": "owners_environment: the one reader",
}
#: The product files that name the sign-in's keys; each key they name goes
#: to github_key.
_KEY_FILES = ("kazma-gateway/kazma_gateway/routers/github_client.py",
              "kazma-gateway/kazma_gateway/routers/github.py")
_KEYS = ("connectors.github.oauth_token", "connectors.github.token", "connectors.github.oauth_scope",
         "connectors.github.oauth_token_type")
_PACKAGES = ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
             "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui")
_ENV_NAMES = ("GITHUB_TOKEN", "GITHUB_PAT")


def _names_the_variable(value: object) -> bool:
    """A string that is one of the owner's variables, or the start of one
    (``"GITHUB_"``: a filter over a copy of the environment)."""
    return isinstance(value, str) and len(value) >= len("GITHUB") and any(
        name.startswith(value) for name in _ENV_NAMES)


def _env_reads(source: str) -> list[int]:
    """Lines that name ``GITHUB_TOKEN`` / ``GITHUB_PAT`` (or the start of
    one) in any shape but the two allowed: an argument of
    ``owners_environment(...)`` (the one reader) and the key of an
    ``os.environ[...] =`` write (the owner's binding, a write).

    A read by the name's constant was all the gate saw until the review of
    S1-17c; a variable holding the name (``name = "GITHUB_TOKEN"``) and a read
    of a copy of the environment (``dict(os.environ)``, ``os.environ.copy()``,
    ``{**os.environ}``) name it in another shape, and are findings now."""
    tree = ast.parse(source)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).rpartition(".")[2] == "owners_environment":
            allowed.update(id(arg) for arg in node.args if isinstance(arg, ast.Constant))
        if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Store)
                and ast.unparse(node.value) in ("os.environ", "environ") and isinstance(node.slice, ast.Constant)):
            allowed.add(id(node.slice))
    return sorted(node.lineno for node in ast.walk(tree)
                  if isinstance(node, ast.Constant) and _names_the_variable(node.value) and id(node) not in allowed)


def _owners_environment_reads(source: str) -> list[int]:
    """Lines that read the environment through a variable: only the one
    reader may (``os.environ.get(variable, ...)``)."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "owners_environment"]


def _env_findings(extra: dict[str, str] | None = None) -> list[str]:
    files = _product_sources()
    files.update(extra or {})
    findings = [f"{rel}:{line}" for rel, source in sorted(files.items()) for line in _env_reads(source)
                if rel not in _ENV_READERS]
    assert _owners_environment_reads(files["kazma-core/kazma_core/spaces/connected_accounts.py"])
    return findings


def test_only_the_one_reader_reads_the_owners_github_environment() -> None:
    assert _env_findings() == []


def test_control_a_planted_environment_read_fails_the_gate() -> None:
    planted = {"kazma-core/kazma_core/planted.py": "import os\nTOKEN = os.environ.get('GITHUB_TOKEN')\n"}
    assert _env_findings(planted) == ["kazma-core/kazma_core/planted.py:2"]


def test_control_a_variable_name_or_a_copy_of_the_environment_fails_the_gate() -> None:
    """The shapes the review found the gate missed: the name in a variable,
    a copy of the whole environment read by the name, and one filtered by
    the name's start."""
    planted = {"kazma-core/kazma_core/planted.py": (
        "import os\n"
        "NAME = 'GITHUB_TOKEN'\n"
        "def by_variable():\n"
        "    return os.environ.get(NAME)\n"
        "def by_copy():\n"
        "    return dict(os.environ).get('GITHUB_PAT')\n"
        "def by_filter():\n"
        "    return {k: v for k, v in os.environ.items() if k.startswith('GITHUB_')}\n"
        "def binding(token):\n"
        "    os.environ['GITHUB_TOKEN'] = token\n")}
    assert _env_findings(planted) == ["kazma-core/kazma_core/planted.py:2", "kazma-core/kazma_core/planted.py:6",
                                      "kazma-core/kazma_core/planted.py:8"]


def _docstrings(tree: ast.AST) -> set[int]:
    """id() of every docstring: text, never a read."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _given_to_github_key(tree: ast.AST) -> set[int]:
    """id() of every key constant that goes to ``github_key``: its argument,
    or an element of the literal a comprehension hands it one by one
    (``[github_key(key) for key in ("a", "b")]``)."""
    given = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and ast.unparse(node.func).rpartition(".")[2] == "github_key":
            given.update(id(arg) for arg in node.args[:1] if isinstance(arg, ast.Constant))
        if isinstance(node, ast.ListComp | ast.SetComp | ast.GeneratorExp) and len(node.generators) == 1:
            elt, gen = node.elt, node.generators[0]
            if (isinstance(elt, ast.Call) and ast.unparse(elt.func).rpartition(".")[2] == "github_key"
                    and len(elt.args) == 1 and isinstance(elt.args[0], ast.Name)
                    and isinstance(gen.target, ast.Name) and gen.target.id == elt.args[0].id
                    and isinstance(gen.iter, ast.Tuple | ast.List) and not gen.ifs):
                given.update(id(e) for e in gen.iter.elts if isinstance(e, ast.Constant))
    return given


def _where(tree: ast.AST, node: ast.AST) -> str:
    """The innermost function holding *node*, else ``<module>``."""
    best, line = "<module>", -1
    for func in ast.walk(tree):
        if (isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef)
                and func.lineno <= node.lineno <= (func.end_lineno or func.lineno) and func.lineno > line):
            best, line = func.name, func.lineno
    return best


def _key_findings(sources: dict[str, str]) -> list[str]:
    """Every sign-in key named where it does not go to ``github_key``, in
    the files allowed to name them; any key named elsewhere.

    The gate asked, per function, whether it called ``github_key`` at all
    until the review of S1-17c: a function that called it once and read
    another key raw passed, and so did a key named outside a function."""
    findings: set[str] = set()
    for rel, source in sorted(sources.items()):
        tree = ast.parse(source)
        text = _docstrings(tree)
        named = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Constant) and n.value in _KEYS and id(n) not in text]
        if not named:
            continue
        if rel not in _KEY_FILES:
            findings.add(f"{rel}: names a GitHub sign-in key")
            continue
        given = _given_to_github_key(tree)
        findings.update(f"{rel}:{_where(tree, n)}" for n in named if id(n) not in given)
    return sorted(findings)


def _product_sources() -> dict[str, str]:
    return {str(p.relative_to(REPO)).replace("\\", "/"): p.read_text(encoding="utf-8")
            for package in _PACKAGES for p in (REPO / package).rglob("*.py")}


def test_every_reader_of_the_sign_in_names_its_space() -> None:
    assert _key_findings(_product_sources()) == []


def test_control_a_planted_reader_without_the_space_fails_the_gate() -> None:
    sources = _product_sources()
    sources["kazma-gateway/kazma_gateway/routers/github.py"] += (
        "\n\ndef planted():\n    return get_config_store().get('connectors.github.token')\n")
    sources["kazma-core/kazma_core/planted.py"] = "KEY = 'connectors.github.oauth_token'\n"
    assert _key_findings(sources) == ["kazma-core/kazma_core/planted.py: names a GitHub sign-in key",
                                      "kazma-gateway/kazma_gateway/routers/github.py:planted"]


def test_control_one_key_asked_and_another_read_raw_fails_the_gate() -> None:
    """The shapes the review found the gate missed: a function that asks
    ``github_key`` for one key and reads another raw, and a key named
    outside any function (a module's list, read raw later)."""
    sources = _product_sources()
    sources["kazma-gateway/kazma_gateway/routers/github.py"] += (
        "\n\ndef planted_two():\n"
        "    store = get_config_store()\n"
        "    store.get(github_key('connectors.github.token'))\n"
        "    return store.get('connectors.github.oauth_token')\n"
        "\n_RAW = ('connectors.github.oauth_scope',)\n")
    assert _key_findings(sources) == ["kazma-gateway/kazma_gateway/routers/github.py:<module>",
                                      "kazma-gateway/kazma_gateway/routers/github.py:planted_two"]


# ── the GitHub client reads its token off the event loop ─────────────────


def test_the_github_client_is_never_made_on_the_loop() -> None:
    """``GitHubClient()`` reads the space's token (settings, the vault, maybe
    an App token mint): a loop-stall helper, so the gate refuses it in async
    code; ``await GitHubClient.of_this_space()`` reads in a thread."""
    from tests.test_static_gates import _loop_stall_helper_calls

    planted = ast.parse(
        "async def route():\n"
        "    async with GitHubClient() as gh:\n"
        "        return await gh.get('/user')\n"
        "async def tool():\n"
        "    return _make()\n"
        "def _make():\n"
        "    return GitHubClient()\n"
        "async def fine():\n"
        "    async with (await GitHubClient.of_this_space()) as gh:\n"
        "        return await gh.get('/user')\n")
    assert sorted(_loop_stall_helper_calls(planted)) == [(2, "GitHubClient"), (5, "_make")]


def test_of_this_space_reads_the_token_off_the_loop(monkeypatch) -> None:
    import asyncio
    import threading

    from kazma_gateway.routers import github_client

    loop_thread: list[int] = []
    read_in: list[int] = []

    def token() -> str:
        read_in.append(threading.get_ident())
        return "space-token"

    monkeypatch.setattr(github_client, "get_github_token", token)

    async def make() -> str:
        loop_thread.append(threading.get_ident())
        client = await github_client.GitHubClient.of_this_space()
        return client._token

    assert asyncio.run(make()) == "space-token"
    assert read_in and read_in[0] != loop_thread[0]
