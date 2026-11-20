"""Spaces S1-20, left (5): work run on the machine acts as the admin.

``kazma ask`` (and ``kazma acp``), ``kazma mcp`` and the terminal app run on
the machine with no sign-in. Once Spaces is enforced each binds the owner's
account in the owner's personal space (or a space it names that the owner is
a member of), so its turn reads the owner's data and has the admin's host
tools; a command naming another space is refused before anything runs.

Controls: an install that does not enforce Spaces binds nothing, as before;
and a turn with nothing bound has no host tool there.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pytest
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, current_scope
from kazma_core.spaces.tools import host_allowance

TEAM = "machine-team"
THEIRS = "machine-theirs"


@pytest.fixture
def spaces(monkeypatch):
    """Spaces enforced; a shared space the owner is in, and a member's
    personal space the owner is not."""
    from kazma_core.config_store import get_config_store
    from kazma_core.security import platform_rbac
    from kazma_core.security.vault import reset_posture_cache
    from kazma_ui import space_binding

    user = platform_rbac.create_local_user("machine-member", "a-long-enough-password-1")
    member = space_binding.account_of({"source": "session", "actor": "login", "user_id": user.user_id,
                                       "username": "machine-member", "role": "operator"})
    identity.create_space("Team", created_by=OWNER_ACCOUNT_ID, space_id=TEAM)
    identity.create_space("Theirs", created_by=member, personal_of=member, space_id=THEIRS)
    monkeypatch.setattr(scope, "_enforced", True)
    try:
        yield
    finally:
        get_config_store().delete(identity.REGISTRY_KEY)
        get_config_store().delete("platform.users")
        reset_posture_cache()


def _ask(monkeypatch, space: str = "") -> tuple[Any, list[Any]]:
    """``run_ask`` with the supervisor stood in: what it ran in, if it ran."""
    from kazma_core.cli import ask

    seen: list[Any] = []

    async def invoke(text: str, **kwargs: Any) -> str:
        bound = current_scope()
        seen.append((bound.space_id, bound.asker, host_allowance().admin) if bound else None)
        return "ok"

    monkeypatch.setattr(ask, "_invoke_supervisor", invoke)
    monkeypatch.setattr(ask, "_boot_env", lambda **kwargs: None)
    result = asyncio.run(ask.run_ask("hello", ask.AskOptions(space=space)))
    return result, seen


def test_kazma_ask_acts_as_the_admin_in_the_admin_s_space(spaces, monkeypatch) -> None:
    result, seen = _ask(monkeypatch)
    assert result.ok and seen == [(OWNER_SPACE_ID, OWNER_ACCOUNT_ID, True)], (result, seen)
    result, seen = _ask(monkeypatch, TEAM)
    assert result.ok and seen == [(TEAM, OWNER_ACCOUNT_ID, False)], (result, seen)


def test_kazma_ask_naming_another_person_s_space_runs_nothing(spaces, monkeypatch) -> None:
    result, seen = _ask(monkeypatch, THEIRS)
    assert not result.ok and seen == [] and "not a member" in result.error, result


def test_control_without_spaces_kazma_ask_binds_nothing(monkeypatch) -> None:
    result, seen = _ask(monkeypatch)
    assert result.ok and seen == [None]
    result, seen = _ask(monkeypatch, TEAM)
    assert not result.ok and seen == [] and "Spaces is not on" in result.error


def test_control_with_nothing_bound_a_turn_has_no_host_tool(spaces) -> None:
    """What the binding fixes: enforced, work that bound nothing has no
    host tool (S1-20)."""
    assert current_scope() is None and not host_allowance().admin


def _mcp(monkeypatch, argv: list[str]) -> tuple[int, list[Any]]:
    from kazma_core.mcp import server

    seen: list[Any] = []

    async def serve() -> int:
        bound = current_scope()
        seen.append((bound.space_id, bound.asker) if bound else None)
        return 0

    monkeypatch.setattr(server, "serve_stdio", serve)
    return server.main(argv), seen


def test_kazma_mcp_serves_in_the_admin_s_space(spaces, monkeypatch) -> None:
    assert _mcp(monkeypatch, []) == (0, [(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)])
    assert _mcp(monkeypatch, ["--space", TEAM]) == (0, [(TEAM, OWNER_ACCOUNT_ID)])
    assert _mcp(monkeypatch, [f"--space={THEIRS}"]) == (2, [])


def test_control_without_spaces_kazma_mcp_binds_nothing(monkeypatch) -> None:
    assert _mcp(monkeypatch, []) == (0, [None])


def test_kazma_acp_serves_in_the_admin_s_space(spaces, monkeypatch) -> None:
    from kazma_core.cli import ask

    seen: list[Any] = []

    def boot(**kwargs: Any) -> None:
        bound = current_scope()
        seen.append((bound.space_id, bound.asker) if bound else None)

    monkeypatch.setattr(ask, "_boot_env", boot)
    assert asyncio.run(ask.run_acp_stdio(stdin=io.StringIO(""), stdout=io.StringIO())) == 0
    assert seen == [(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)]
    refused = asyncio.run(ask.run_acp_stdio(stdin=io.StringIO(""), stdout=io.StringIO(), space=THEIRS))
    assert refused == 2 and len(seen) == 1


def test_the_terminal_app_names_the_admin_s_space() -> None:
    """Every call of the terminal app and the CLI to the server names the
    owner's personal space (the server binds it, checking membership)."""
    from kazma_core.runtime.local_api import auth_headers
    from kazma_ui.space_binding import SPACE_HEADER

    assert auth_headers()[SPACE_HEADER] == OWNER_SPACE_ID


def test_the_terminal_app_s_calls_run_in_the_admin_s_space(spaces, monkeypatch) -> None:
    """Through the real tenant middleware: the owner is in two spaces, so a
    call that names none is refused (control); the terminal app's calls name
    the owner's personal space and run there."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from kazma_core.runtime.local_api import auth_headers
    from kazma_ui import auth

    monkeypatch.setattr(auth, "get_request_principal",
                        lambda request: {"source": "secret", "role": "admin", "user_id": "", "actor": "secret"})
    app = FastAPI()
    app.middleware("http")(auth.create_tenant_middleware())

    # A space route (route_scopes): a call that names no space is refused there.
    @app.get("/api/chat/sessions")
    def whose() -> dict[str, Any]:
        bound = current_scope()
        return {"space": bound.space_id if bound else None, "asker": bound.asker if bound else None}

    with TestClient(app) as client:
        assert client.get("/api/chat/sessions").status_code == 400
        got = client.get("/api/chat/sessions", headers=auth_headers())
    assert got.status_code == 200 and got.json() == {"space": OWNER_SPACE_ID, "asker": OWNER_ACCOUNT_ID}
