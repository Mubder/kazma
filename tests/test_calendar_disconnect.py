"""A calendar disconnect disconnects, and one account's sign-in never undoes
another's (2026-09-28).

Found checking what Settings offers against what it does:

- "Disconnect Calendar" (Google) deleted the calendar's own tokens, but the
  Gmail sign-in covers Calendar and the calendar reads through it, so the
  calendar stayed connected -- and the next Gmail refresh wrote a fresh copy
  back. The button changed nothing while Gmail was connected.
- Outlook Calendar had no disconnect at all, and its only connect was the
  mail sign-in, which also reconnects mail.
- An Outlook Calendar token refresh wrote the mail tokens every time: back
  after the owner disconnected Microsoft mail, and over a mailbox signed in
  as another account.
- Disconnecting Microsoft mail left the sign-in's address behind.

A disconnect now turns the calendar OFF (``calendar.<provider>.off``): while
off nothing reads its tokens and nothing writes them, and only a sign-in from
the calendar card turns it back on.
"""

from __future__ import annotations

import base64
import json

import pytest

GMAIL_WITH_CALENDAR = (
    "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
)
_ENV = (
    "GOOGLE_CALENDAR_TOKEN", "GOOGLE_CALENDAR_REFRESH", "GOOGLE_OAUTH_TOKEN",
    "GOOGLE_CALENDAR_SCOPES", "GOOGLE_CALENDAR_ADDRESS",
    "MS_CALENDAR_TOKEN", "MS_CALENDAR_REFRESH", "MS_GRAPH_TOKEN",
    "EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN", "EMAIL_GMAIL_SCOPES",
    "EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN", "EMAIL_MS_AUTH",
)


def _jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """One dict behind every vault reader and writer the calendar and mail
    code use; no token in the environment."""
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager import credentials as mail
    from kazma_skills.native.email_manager import oauth_ms, protocol_connect

    for name in _ENV:
        monkeypatch.delenv(name, raising=False)
    store: dict[str, str] = {}

    def put(name: str, value: str, category: str = "email") -> bool:
        if not value:
            return False
        store[name] = value
        return True

    def drop(*names: str) -> None:
        for n in names:
            store.pop(n, None)

    monkeypatch.setattr(mail, "vault_store", put)
    monkeypatch.setattr(mail, "vault_retrieve", lambda name: store.get(name, ""))
    monkeypatch.setattr(oauth_ms, "vault_store", put)
    monkeypatch.setattr(protocol_connect, "_vault_delete", drop)
    monkeypatch.setattr(cal, "_vault_delete", drop)
    return store


def _gmail_with_calendar(vault: dict[str, str]) -> None:
    """Gmail signed in with the Calendar scope, and the calendar's own copy of
    that grant (what the Gmail sign-in writes)."""
    vault.update({
        "email.gmail.access_token": "g-access",
        "email.gmail.refresh_token": "g-refresh",
        "email.gmail.scopes": GMAIL_WITH_CALENDAR,
        "calendar.google.access_token": "g-access",
        "calendar.google.refresh_token": "g-refresh",
    })


# ── Google ──────────────────────────────────────────────────────────────


def test_the_calendar_reads_through_the_gmail_grant(vault: dict[str, str]) -> None:
    """Negative control -- why deleting the calendar's tokens was not enough:
    with only them gone, the calendar is still connected through Gmail."""
    from kazma_skills.native.calendar import credentials as cal

    _gmail_with_calendar(vault)
    del vault["calendar.google.access_token"], vault["calendar.google.refresh_token"]
    assert cal.google_connected()
    assert cal.google_access_token() == "g-access"


def test_disconnecting_google_calendar_disconnects_it(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.calendar.router import detect_available_provider

    _gmail_with_calendar(vault)
    assert cal.google_connected()
    result = cal.clear_google_tokens()
    assert result["ok"] is True
    assert not cal.google_connected()
    assert cal.google_access_token() == "" and cal.google_refresh_token() == ""
    assert detect_available_provider() == "sandbox"
    assert cal.status_summary()["google_connected"] is False
    assert "calendar.google.access_token" not in vault
    # Gmail itself is untouched.
    assert vault["email.gmail.refresh_token"] == "g-refresh"


def test_a_gmail_refresh_does_not_bring_it_back(vault: dict[str, str]) -> None:
    """The Gmail refresh copies its grant to the calendar; while the calendar
    is off it keeps nothing."""
    from kazma_skills.native.calendar import credentials as cal

    _gmail_with_calendar(vault)
    cal.clear_google_tokens()
    cal.persist_google_tokens("g-access-2", "g-refresh-2", scopes=GMAIL_WITH_CALENDAR)
    assert "calendar.google.access_token" not in vault
    assert not cal.google_connected()


def test_connecting_it_from_the_card_turns_it_back_on(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar import credentials as cal

    _gmail_with_calendar(vault)
    cal.clear_google_tokens()
    cal.turn_calendar_on("google")
    cal.persist_google_tokens("c-access", "c-refresh", "me@gmail.com", GMAIL_WITH_CALENDAR)
    assert cal.google_access_token() == "c-access"
    assert cal.status_summary()["google_address"] == "me@gmail.com"


def test_a_disconnect_the_vault_did_not_keep_says_so(
    vault: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager import credentials as mail

    _gmail_with_calendar(vault)
    monkeypatch.setattr(mail, "vault_store", lambda *a, **k: False)
    result = cal.clear_google_tokens()
    assert result["ok"] is False and "vault" in result["error"]
    assert vault["calendar.google.access_token"] == "g-access", "nothing half-done"


# ── Microsoft ───────────────────────────────────────────────────────────


def _ms_payload(access: str, refresh: str, who: str) -> dict[str, str]:
    return {
        "access_token": access,
        "refresh_token": refresh,
        "scope": "Mail.Send Calendars.ReadWrite openid",
        "id_token": _jwt({"preferred_username": who}),
    }


def test_a_mail_sign_in_connects_outlook_calendar_and_names_it(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager.oauth_ms import store_microsoft_tokens

    store_microsoft_tokens(_ms_payload("m-access", "m-refresh", "me@msn.com"), "client")
    assert cal.microsoft_connected()
    status = cal.status_summary()
    assert status["outlook_connected"] is True and status["outlook_address"] == "me@msn.com"


def test_disconnecting_outlook_calendar_leaves_mail(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager.oauth_ms import store_microsoft_tokens

    store_microsoft_tokens(_ms_payload("m-access", "m-refresh", "me@msn.com"), "client")
    result = cal.clear_microsoft_calendar_tokens()
    assert result["ok"] is True
    assert not cal.microsoft_connected(), "not through the mail grant either"
    assert cal.status_summary()["outlook_address"] == ""
    assert vault["email.microsoft.refresh_token"] == "m-refresh"
    # A later mail sign-in keeps mail and leaves the calendar off.
    store_microsoft_tokens(_ms_payload("m-access-2", "m-refresh-2", "me@msn.com"), "client")
    assert not cal.microsoft_connected()
    assert vault["email.microsoft.access_token"] == "m-access-2"


def test_the_calendar_card_sign_in_touches_the_calendar_only(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager.oauth_ms import store_microsoft_calendar_tokens

    cal.clear_microsoft_calendar_tokens()  # off, and no mail signed in
    got = store_microsoft_calendar_tokens(_ms_payload("c-access", "c-refresh", "cal@outlook.com"))
    assert got == "cal@outlook.com"
    assert cal.microsoft_access_token() == "c-access"
    assert not any(k.startswith("email.microsoft.") for k in vault), "mail stays disconnected"


def test_disconnecting_microsoft_mail_forgets_the_account(vault: dict[str, str]) -> None:
    from kazma_skills.native.email_manager.oauth_ms import (
        clear_microsoft_tokens,
        store_microsoft_tokens,
    )

    store_microsoft_tokens(_ms_payload("m-access", "m-refresh", "me@msn.com"), "client")
    clear_microsoft_tokens()
    left = {k for k in vault if k.startswith("email.microsoft.")}
    assert left == {"email.microsoft.client_id"}, left  # the app registration stays
    assert vault["calendar.microsoft.refresh_token"] == "m-refresh", "the calendar has its own switch"


def test_disconnecting_gmail_forgets_the_grant(vault: dict[str, str]) -> None:
    from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

    _gmail_with_calendar(vault)
    vault.update({"email.gmail.connected_at": "1", "email.gmail.drive_ok": "ok",
                  "email.gmail.client_id": "cid"})
    disconnect_protocol("gmail")
    assert {k for k in vault if k.startswith("email.gmail.")} == {"email.gmail.client_id"}


# ── Outlook token refresh ───────────────────────────────────────────────


def test_a_calendar_refresh_updates_mail_only_on_the_same_grant(vault: dict[str, str]) -> None:
    from kazma_skills.native.calendar.backends.outlook_calendar import _keep_refreshed_tokens

    # Same grant (a mail sign-in copied it): mail follows.
    vault["email.microsoft.refresh_token"] = "r1"
    _keep_refreshed_tokens("a2", "r2", "r1", "Calendars.ReadWrite")
    assert vault["email.microsoft.refresh_token"] == "r2"
    assert vault["email.microsoft.access_token"] == "a2"
    assert vault["calendar.microsoft.refresh_token"] == "r2"

    # Mail disconnected: the refresh must not sign it back in.
    vault.clear()
    _keep_refreshed_tokens("a3", "r3", "r2", "Calendars.ReadWrite")
    assert "email.microsoft.refresh_token" not in vault
    assert "email.microsoft.access_token" not in vault

    # Mail signed in as another account: left alone.
    vault["email.microsoft.refresh_token"] = "other-account"
    _keep_refreshed_tokens("a4", "r4", "r3", "Calendars.ReadWrite")
    assert vault["email.microsoft.refresh_token"] == "other-account"


# ── The sign-in's way back ──────────────────────────────────────────────


@pytest.mark.parametrize("purpose, flag, provider", [
    ("calendar", "calendar_oauth", "outlook"),
    ("mail", "email_oauth", "microsoft"),
])
def test_the_microsoft_callback_answers_the_card_that_asked(
    monkeypatch: pytest.MonkeyPatch, purpose: str, flag: str, provider: str
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_skills.native.email_manager import oauth_common, oauth_ms_browser
    from kazma_ui import email_api

    state = oauth_common.new_state("microsoft", redirect_uri="x", purpose=purpose)

    async def finish(code: str, st: str) -> dict:
        assert oauth_common.pop_state(st)["purpose"] == purpose
        return {"ok": True, "email": "me@msn.com"}

    monkeypatch.setattr(oauth_ms_browser, "finish_ms_browser_oauth", finish)
    app = FastAPI()
    app.include_router(email_api.router)
    r = TestClient(app).get(
        "/api/email/oauth/microsoft/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 302
    assert f"{flag}=ok&provider={provider}&email=me%40msn.com" in r.headers["location"]


def test_the_calendar_sign_in_keeps_only_calendar_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    """finish_ms_browser_oauth dispatches on the state's purpose."""
    import asyncio

    from kazma_skills.native.email_manager import oauth_common, oauth_ms, oauth_ms_browser

    kept: list[str] = []
    monkeypatch.setattr(oauth_ms_browser, "_client_id", lambda: "cid")
    monkeypatch.setattr(oauth_ms_browser, "_client_secret", lambda: "")
    monkeypatch.setattr(oauth_ms, "store_microsoft_calendar_tokens",
                        lambda payload: kept.append("calendar") or "me@msn.com")
    monkeypatch.setattr(oauth_ms, "store_microsoft_tokens",
                        lambda payload, cid: kept.append("mail") or "me@msn.com")

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self) -> dict:
            return {"access_token": "a", "refresh_token": "r"}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(oauth_ms_browser.httpx, "AsyncClient", _Client)
    for purpose in ("calendar", "mail"):
        state = oauth_common.new_state("microsoft", redirect_uri="x", purpose=purpose)
        result = asyncio.run(oauth_ms_browser.finish_ms_browser_oauth("code", state))
        assert result["ok"] and result["purpose"] == purpose
    assert kept == ["calendar", "mail"]


def test_the_outlook_calendar_routes_are_guarded_like_the_google_ones() -> None:
    from kazma_ui import calendar_api

    posts = {r.path: r for r in calendar_api.protected_router.routes}
    for path in (
        "/api/calendar/oauth/google/disconnect",
        "/api/calendar/oauth/microsoft/disconnect",
        "/api/calendar/oauth/microsoft/device/start",
    ):
        deps = [d.call for d in posts[path].dependant.dependencies]
        assert calendar_api._verify_same_origin in deps, path
    gets = {r.path for r in calendar_api.router.routes}
    assert "/api/calendar/oauth/microsoft/start.json" in gets


def test_the_code_sign_in_can_bring_a_disconnected_calendar_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Microsoft refuses the redirect, the calendar card's code sign-in
    is the way back; its poll keeps calendar tokens only. The mail card's
    code sign-in still keeps mail."""
    import asyncio
    import time

    from kazma_skills.native.email_manager import oauth_ms

    kept: list[str] = []
    monkeypatch.setattr(oauth_ms, "store_microsoft_calendar_tokens",
                        lambda payload: kept.append("calendar") or "cal@outlook.com")
    monkeypatch.setattr(oauth_ms, "store_microsoft_tokens",
                        lambda payload, cid: kept.append("mail") or "me@msn.com")

    class _Resp:
        status_code = 200
        content = b"{}"

        def json(self) -> dict:
            return {"access_token": "a", "refresh_token": "r"}

    class _Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(oauth_ms.httpx, "AsyncClient", _Client)
    for purpose in ("calendar", "mail"):
        oauth_ms._pending["dc-" + purpose] = {
            "interval": 5, "expires_at": time.time() + 60,
            "client_id": "cid", "tenant": "common", "purpose": purpose,
        }
        result = asyncio.run(oauth_ms.poll_device_code_flow("dc-" + purpose))
        assert result["ok"] and result["purpose"] == purpose, result
    assert kept == ["calendar", "mail"]
    with pytest.raises(ValueError):
        asyncio.run(oauth_ms.start_device_code_flow(purpose="everything"))
