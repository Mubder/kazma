"""The matrix cases of Spaces S1-17, part a: mail and calendar
(``tests/_space_matrix.py``).

Every case runs with Spaces enforced, as person A in ``matrix-a`` against
person B's ``matrix-b``, through the product's own mail and calendar routers
behind the real tenant middleware, or through the product's own tools, on a
real vault of the case's own.

B's connections are planted through the product's own writers in B's space: a
generic IMAP mailbox, a Gmail grant, an extra account named ``home`` and a
Google Calendar grant, each naming B's marker. A request or tool call of A's
never shows B's marker and never changes what B's space holds; where A has
something of its own, A sees it. Nothing reaches the network: a tool of A's
with nothing connected answers from the sandbox, and a named account or
provider A lacks is refused -- with B's connection lent to A, the call would
reach B's mail server (the suite refuses that egress, and the case fails).
The two network steps a route takes on purpose -- Microsoft's device code and
an added account's first login -- are stood in for, and the stand-in records
the space it ran in.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID
from kazma_ui import auth, space_binding

from tests._space_matrix import Matrix, case

ITEM = "S1-17"
_ORIGIN = "http://testserver"

#: The environment the mail and calendar code read: cleared for each case.
_MAIL_ENV = ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_PROTOCOL", "EMAIL_IMAP_HOST", "EMAIL_IMAP_PORT",
             "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_GMAIL_ADDRESS", "EMAIL_GMAIL_APP_PASSWORD",
             "EMAIL_GMAIL_AUTH", "EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN", "EMAIL_GMAIL_SCOPES",
             "EMAIL_ACCOUNTS", "EMAIL_DEFAULT_PROVIDER", "GOOGLE_CALENDAR_TOKEN", "GOOGLE_CALENDAR_REFRESH",
             "GOOGLE_CALENDAR_ADDRESS", "GOOGLE_CALENDAR_SCOPES", "KAZMA_CALENDAR_PROVIDER")
#: What a space's connections are read back by.
_HELD_NAMES = ("email.generic.address", "email.imap.password", "email.gmail.refresh_token",
               "email.gmail.address", "email.account.home.password", "calendar.google.refresh_token",
               "calendar.google.address")
_HELD_ENV = ("EMAIL_ADDRESS", "EMAIL_IMAP_HOST")


class World:
    """The mail and calendar routers in one app, with two signed-in people."""

    def __init__(self, matrix: Matrix, client: TestClient, marks: Any, ran_in: list[Any]) -> None:
        self.matrix = matrix
        self.client = client
        self.marks = marks
        self.ran_in = ran_in

    def as_who(self, who: str) -> Any:
        return {"a": self.matrix.as_a, "b": self.matrix.as_b}[who]()

    def call(self, who: str, method: str, path: str, **kwargs: Any) -> Any:
        space = {"a": Matrix.SPACE_A, "b": Matrix.SPACE_B}[who]
        headers = {"x-matrix-person": who, space_binding.SPACE_HEADER: space, "Origin": _ORIGIN,
                   "X-Requested-With": "XMLHttpRequest"}
        return self.client.request(method, path, headers=headers, **kwargs)

    def held(self, who: str) -> dict[str, Any]:
        """What *who*'s space holds of its connections."""
        from kazma_skills.native.email_manager import accounts, credentials

        with self.as_who(who):
            out: dict[str, Any] = {name: credentials.vault_retrieve(name) for name in _HELD_NAMES}
            out.update({name: credentials.account_env(name) for name in _HELD_ENV})
            out["accounts"] = [(a["alias"], a.get("address")) for a in accounts.stored_accounts()]
        return out

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

        return asyncio.run(as_them())


def _plant(who_scope: Any, mark: str) -> None:
    """A space's connections, through the product's own writers."""
    from kazma_skills.native.calendar.credentials import persist_google_tokens
    from kazma_skills.native.email_manager import accounts
    from kazma_skills.native.email_manager.oauth_gmail import persist_gmail_tokens
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol

    with who_scope:
        assert connect_protocol(provider="generic", protocol="imap", address=f"{mark}@imap.example",
                                password=f"{mark}-pw", imap_host=f"imap.{mark}.example")["ok"]
        persist_gmail_tokens(f"{mark}-access", f"{mark}-refresh", f"{mark}@gmail.example")
        accounts.add_password_account("home", "imap", f"{mark}-home@example.org", f"{mark}-home-pw",
                                      imap_host=f"imap.{mark}-home.example")
        # One the other space has no namesake of: a list shared between the
        # spaces would show it to the other.
        accounts.add_password_account(mark, "imap", f"{mark}-own@example.org", f"{mark}-own-pw",
                                      imap_host=f"imap.{mark}-own.example")
        persist_google_tokens(f"{mark}-cal-access", f"{mark}-cal-refresh", f"{mark}@cal.example")


@contextmanager
def world(matrix: Matrix, *, plant_a: bool = True) -> Iterator[World]:
    """Spaces enforced; the mail and calendar routers behind the real tenant
    middleware, a real vault of the case's own, the install's Google and
    Microsoft app set, B's connections planted (and A's unless told not)."""
    from kazma_core.security import vault as vault_mod
    from kazma_skills.native.email_manager import credentials, oauth_ms

    from kazma_ui import calendar_api, email_api

    people = {"a": matrix.principal_a, "b": matrix.principal_b}
    ran_in: list[Any] = []
    with pytest.MonkeyPatch.context() as patch, tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        patch.setattr(scope, "_enforced", True)
        patch.setenv("KAZMA_VAULT_KEY", "k" * 64)
        patch.setenv("KAZMA_PUBLIC_URL", _ORIGIN)  # where a provider sends a sign-in back
        for name in _MAIL_ENV:
            patch.delenv(name, raising=False)
        v = vault_mod.SecretVault(db_path=str(Path(tmp) / "vault.db"))
        patch.setattr(vault_mod, "get_vault", lambda: v)
        patch.setattr(auth, "get_request_principal",
                      lambda request: people.get(request.headers.get("x-matrix-person", "")))

        async def device_code(**kwargs: Any) -> dict[str, Any]:
            bound = scope.current_scope()
            ran_in.append(("device_start", bound.space_id if bound else None))
            return {"ok": True, "user_code": "ABCD", "device_code": "dc", "verification_uri": "https://x"}

        async def device_poll(device_code: str) -> dict[str, Any]:
            bound = scope.current_scope()
            ran_in.append(("device_poll", bound.space_id if bound else None))
            return {"ok": False, "pending": True}

        patch.setattr(oauth_ms, "start_device_code_flow", device_code)
        patch.setattr(oauth_ms, "poll_device_code_flow", device_poll)
        marks = matrix.plant("mail")
        with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
            credentials.vault_store("email.gmail.client_id", "12345-install.apps.googleusercontent.com")
            credentials.vault_store("email.gmail.client_secret", "install-secret")
            credentials.vault_store("email.microsoft.client_id", "install-ms-client")
        _plant(matrix.as_b(), marks.b)
        if plant_a:
            _plant(matrix.as_a(), marks.a)
        app = FastAPI()
        app.middleware("http")(auth.create_tenant_middleware())
        for router in (email_api.router, email_api.protected_router, calendar_api.router,
                       calendar_api.protected_router):
            app.include_router(router)
        try:
            with TestClient(app, base_url=_ORIGIN) as client:
                yield World(matrix, client, marks, ran_in)
        finally:
            v.close()
            for name in _MAIL_ENV:  # what the owner's space's writes set
                os.environ.pop(name, None)


def _in_world(run: Callable[[World], None], *, plant_a: bool = True) -> Callable[[Matrix], None]:
    def with_world(matrix: Matrix) -> None:
        with world(matrix, plant_a=plant_a) as made:
            run(made)

    with_world.__name__ = run.__name__
    return with_world


def _route(target: str, body: Any = None, *, check: Callable[[World, Any], None] | None = None) -> None:
    """A case for *target*: as A, B's space is left as it was and B's marker
    is never shown; *check* the positive for A."""
    method, path = target.split(" ", 1)

    def run(w: World) -> None:
        before = w.held("b")
        answer = w.call("a", method, path.replace("{alias}", "home"),
                        **({"json": body} if body is not None else {}))
        assert answer.status_code < 500, answer.text[:300]
        assert w.marks.b not in answer.text, answer.text[:300]
        assert w.held("b") == before, "A's request changed what B's space holds"
        if check is not None:
            check(w, answer)

    run.__name__ = "mail_" + path.strip("/").replace("/", "_").replace("{", "").replace("}", "")
    case(target, ITEM)(_in_world(run))


def _shows_mine(w: World, answer: Any) -> None:
    assert w.marks.a in answer.text, answer.text[:300]


def _started_in_a(w: World, answer: Any) -> None:
    from kazma_core.security import oauth_attempts
    from kazma_core.security.oauth_attempts import binding_cookie_name

    assert answer.status_code == 200 and answer.json().get("authorize_url"), answer.text[:300]
    state = next(c.value for c in w.client.cookies.jar if c.value and binding_cookie_name(c.value) == c.name)
    attempt = oauth_attempts.take(state, bound_state=state, providers=oauth_attempts.PROVIDERS)
    assert (attempt["space"], attempt["asker"]) == (Matrix.SPACE_A, w.matrix.account_a), attempt


def _cleared(names: tuple[str, ...]) -> Callable[[World, Any], None]:
    def check(w: World, answer: Any) -> None:
        held = w.held("a")
        assert all(not held[name] for name in names), {n: held[n] for n in names}

    return check


def _device_ran_in_a(step: str) -> Callable[[World, Any], None]:
    def check(w: World, answer: Any) -> None:
        assert (step, Matrix.SPACE_A) in w.ran_in, w.ran_in

    return check


def _home_removed(w: World, answer: Any) -> None:
    assert ("home", f"{w.marks.a}-home@example.org") not in w.held("a")["accounts"]


def _connected_in_a(w: World, answer: Any) -> None:
    assert answer.json().get("ok"), answer.text[:300]
    assert w.held("a")["email.generic.address"] == "newbox@imap.example"


def _added_in_a(w: World, answer: Any) -> None:
    assert answer.status_code == 200 and answer.json().get("ok"), answer.text[:300]
    assert ("work", "a-work@example.org") in w.held("a")["accounts"]


_route("GET /api/email/status", check=_shows_mine)
_route("GET /api/email/accounts", check=_shows_mine)
_route("GET /api/calendar/status", check=_shows_mine)
_route("POST /api/email/oauth/gmail/start", {}, check=_started_in_a)
_route("POST /api/email/oauth/microsoft/start", {}, check=_started_in_a)
_route("POST /api/calendar/oauth/google/start", {}, check=_started_in_a)
_route("POST /api/calendar/oauth/microsoft/start", {}, check=_started_in_a)
_route("POST /api/email/oauth/microsoft/device/start", {}, check=_device_ran_in_a("device_start"))
_route("POST /api/calendar/oauth/microsoft/device/start", {}, check=_device_ran_in_a("device_start"))
_route("POST /api/email/oauth/microsoft/device/poll", {"device_code": "dc"},
       check=_device_ran_in_a("device_poll"))
_route("POST /api/email/gmail/disconnect", check=_cleared(("email.gmail.refresh_token",)))
_route("POST /api/email/oauth/microsoft/disconnect")
_route("POST /api/calendar/oauth/google/disconnect", check=_cleared(("calendar.google.refresh_token",)))
_route("POST /api/calendar/oauth/microsoft/disconnect")
_route("POST /api/email/protocol/disconnect", {"provider": "generic"},
       check=_cleared(("email.generic.address", "EMAIL_ADDRESS")))
_route("POST /api/email/protocol/connect",
       {"provider": "generic", "protocol": "imap", "address": "newbox@imap.example", "password": "pw-new",
        "imap_host": "imap.new.example"}, check=_connected_in_a)
_route("POST /api/email/gmail/connect", {"address": "a-gmail@example.org", "app_password": "abcd efgh"})
_route("POST /api/email/accounts/{alias}/remove", check=_home_removed)


def _add_account(w: World) -> None:
    """Adding an extra account: its first login is stood in for, and the
    account lands in A's list only."""
    import kazma_skills.native.email_manager.router as mail_router

    class _Box:
        async def list_messages(self, query: Any) -> list[Any]:
            return []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mail_router, "backend_for_account", lambda alias, cfg: _Box())
        before = w.held("b")
        answer = w.call("a", "POST", "/api/email/accounts", json={
            "alias": "work", "type": "imap", "address": "a-work@example.org", "password": "pw-work",
            "imap_host": "imap.work.example"})
        assert w.marks.b not in answer.text and w.held("b") == before
        _added_in_a(w, answer)


case("POST /api/email/accounts", ITEM)(_in_world(_add_account))


# ── the tools: A has nothing connected, B has everything ─────────────────


_TOOL_CALLS: dict[str, list[dict[str, Any]]] = {
    "email_accounts": [{}],
    "email_list": [{}, {"account": "home"}, {"provider": "imap"}],
    "email_get": [{"message_id": "m1"}, {"message_id": "m1", "account": "home"}],
    "email_analyze": [{"message_id": "m1"}, {"message_id": "m1", "account": "home"}],
    "email_send": [{"to": "x@example.org", "subject": "s", "body": "b", "account": "home"},
                   {"to": "x@example.org", "subject": "s", "body": "b", "provider": "gmail"}],
    "email_categorize": [{"message_id": "m1", "mark_read": True, "account": "home"}],
    "email_delete": [{"message_id": "m1", "account": "home"}],
    "list_events": [{}, {"provider": "google"}, {"account": "home"}],
    "find_free_slots": [{"date": "2026-12-01"}, {"date": "2026-12-01", "provider": "google"}],
    "create_event": [{"summary": "s", "start": "2026-12-01T10:00:00Z", "end": "2026-12-01T11:00:00Z",
                      "provider": "google"}],
    "update_event": [{"event_id": "e1", "fields": {"summary": "s"}, "provider": "google"}],
    "delete_event": [{"event_id": "e1", "provider": "google"}],
}


def _sandbox_kept_apart(w: World) -> None:
    """The practice mailbox is each space's (the S1-17 follow-up's probe):
    B sends from its own, A sends from its own (nothing connected: the
    sandbox), and each one's Sent folder holds its own message only -- B
    reading after A sent does not see A's, A does not see B's."""
    w.tool("b", "email_send", to="x@example.org", subject=f"sent by {w.marks.b}", body=w.marks.b,
           provider="sandbox")
    w.tool("a", "email_send", to="x@example.org", subject=f"sent by {w.marks.a}", body=w.marks.a)
    mine = w.tool("a", "email_list", folder="Sent")
    assert w.marks.a in mine and w.marks.b not in mine, mine[:300]
    theirs = w.tool("b", "email_list", folder="Sent", provider="sandbox")
    assert w.marks.b in theirs and w.marks.a not in theirs, theirs[:300]


#: The tools whose case also sends and reads the practice mailbox.
_SANDBOX_TOOLS = ("email_send", "email_list")


def _tool_case(name: str) -> Callable[[Matrix], None]:
    def run(w: World) -> None:
        before = w.held("b")
        for kwargs in _TOOL_CALLS[name]:
            answer = w.tool("a", name, **kwargs)
            assert w.marks.b not in answer, f"{name}({kwargs}) showed B's connection: {answer[:300]}"
            assert w.held("b") == before, f"{name}({kwargs}) changed what B's space holds"
        if name in _SANDBOX_TOOLS:
            _sandbox_kept_apart(w)

    run.__name__ = f"tool_{name}"
    return _in_world(run, plant_a=False)


for _name in sorted(_TOOL_CALLS):
    case(f"tool:{_name}", ITEM)(_tool_case(_name))
