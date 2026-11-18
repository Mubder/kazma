"""Spaces S1-17, part c: the GitHub sign-in belongs to a space.

Guards act only where Spaces is enforced; one account behaves as before
(decision N26). The owner's space keeps today's sign-in -- its stored tokens,
the install's GitHub App and the process environment's ``GITHUB_TOKEN`` /
``GITHUB_PAT``. Every other space keeps only its own OAuth token and PAT
(``connected_accounts.space_setting_key``), and until it connects one, git
push and the GitHub tools refuse there. Enforced:

* a space with nothing connected reads no token at all -- never the owner's
  environment, App or a global vault row an older install keeps;
* a push (``_git_token``) uses the App only in the owner's space;
* a token saved in another space never reaches the process environment;
* Sign in with GitHub finishes in the space it started in (the callback is
  open before sign-in: with no binding it landed in the owner's space);
* the GitHub App is the owner's space's;
* Disconnect deletes the keys: written empty, the settings store refused to
  blank them and Disconnect cleared nothing (found on the way).

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
    from kazma_core.config_store import get_config_store

    with world(matrix) as w:
        refused = w.call("a", "POST", "/api/github/app/config", json={"app_id": "999", "app_installation_id": "1"})
        assert refused.status_code == 409 and refused.json()["code"] == "owners_space_only"
        assert get_config_store().get("connectors.github.app_id") == "4242"
        from kazma_gateway.routers import github_client

        monkeypatch.setattr(github_client, "in_the_owners_space", lambda: True)  # control: the route as before
        assert w.call("a", "POST", "/api/github/app/config",
                      json={"app_id": "999", "app_installation_id": "1"}).status_code == 200
        assert get_config_store().get("connectors.github.app_id") == "999"


# ── Disconnect deletes, as it says ───────────────────────────────────────


def _one_account_disconnects(tmp_path, monkeypatch, *, revoke: Any = None) -> dict[str, Any]:
    """An install with one account saves a token and an App, then presses
    Disconnect: what it still holds."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.config_store import get_config_store
    from kazma_core.security import vault as vault_mod
    from kazma_gateway.routers import github, github_client

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
            store.set("connectors.github.app_private_key", "-----BEGIN KEY-----", category="connectors")
            if revoke is not None:
                monkeypatch.setattr(github, "oauth_revoke", revoke)
            assert client.post("/api/github/oauth/revoke").status_code == 200
            return {"token": github_client.get_github_token() if not _env_token() else "",
                    "pat": store.get("connectors.github.token"),
                    "oauth": store.get("connectors.github.oauth_token"),
                    "app_key": store.get("connectors.github.app_private_key")}
    finally:
        for key in ("connectors.github.token", "connectors.github.oauth_token", "connectors.github.oauth_scope",
                    "connectors.github.oauth_token_type", "connectors.github.app_private_key"):
            store.delete(key)
        v.close()


def _env_token() -> str:
    import os

    return os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GITHUB_PAT", "")


def test_disconnect_clears_the_sign_in(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_PAT", raising=False)
    held = _one_account_disconnects(tmp_path, monkeypatch)
    assert held == {"token": "", "pat": None, "oauth": None, "app_key": None}, held


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
#: The product files that name the sign-in's keys; each of their functions
#: that names one asks github_key.
_KEY_FILES = ("kazma-gateway/kazma_gateway/routers/github_client.py",
              "kazma-gateway/kazma_gateway/routers/github.py")
_KEYS = ("connectors.github.oauth_token", "connectors.github.token", "connectors.github.oauth_scope",
         "connectors.github.oauth_token_type")
_PACKAGES = ("kazma-core/kazma_core", "kazma-gateway/kazma_gateway", "kazma-ui/kazma_ui",
             "kazma-skills/kazma_skills", "kazma-cli/kazma_cli", "kazma-tui/kazma_tui")


def _env_reads(source: str) -> list[int]:
    """Lines that read GITHUB_TOKEN or GITHUB_PAT from the environment."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant):
            name = ast.unparse(node.func)
            if name in ("os.environ.get", "os.getenv", "environ.get", "getenv") \
                    and str(node.args[0].value) in ("GITHUB_TOKEN", "GITHUB_PAT"):
                found.append(node.lineno)
        if isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load) \
                and ast.unparse(node.value) in ("os.environ", "environ") \
                and isinstance(node.slice, ast.Constant) and node.slice.value in ("GITHUB_TOKEN", "GITHUB_PAT"):
            found.append(node.lineno)
    return found


def _owners_environment_reads(source: str) -> list[int]:
    """Lines that read the environment through a variable: only the one
    reader may (``os.environ.get(variable, ...)``)."""
    return [node.lineno for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.FunctionDef) and node.name == "owners_environment"]


def _env_findings(extra: dict[str, str] | None = None) -> list[str]:
    files = {str(p.relative_to(REPO)).replace("\\", "/"): p.read_text(encoding="utf-8")
             for package in _PACKAGES for p in (REPO / package).rglob("*.py")}
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


def _key_findings(sources: dict[str, str]) -> list[str]:
    """A function that names a sign-in key without asking github_key, in the
    files allowed to name them; any key named elsewhere."""
    findings = []
    for rel, source in sorted(sources.items()):
        tree = ast.parse(source)
        named = [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and n.value in _KEYS]
        if not named:
            continue
        if rel not in _KEY_FILES:
            findings.append(f"{rel}: names a GitHub sign-in key")
            continue
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            names_key = any(isinstance(n, ast.Constant) and n.value in _KEYS for n in ast.walk(func))
            asks = any(isinstance(n, ast.Call) and ast.unparse(n.func).endswith("github_key") for n in ast.walk(func))
            if names_key and not asks and ast.get_docstring(func) is not None:
                # A docstring naming a key is text; look at the statements.
                body = func.body[1:]
                names_key = any(isinstance(n, ast.Constant) and n.value in _KEYS
                                for stmt in body for n in ast.walk(stmt))
            if names_key and not asks:
                findings.append(f"{rel}:{func.name}")
    return findings


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
