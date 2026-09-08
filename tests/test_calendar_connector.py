"""Google/Outlook calendar must not silently sandbox.

Live 2026-09-08: after a Gmail reconnect, list_events(provider=google)
returned ``Calendar: sandbox, No events found``. The router only read
env GOOGLE_CALENDAR_TOKEN and its vault stub always returned "".
"""

from __future__ import annotations

import inspect
from unittest.mock import patch

import pytest


def test_router_has_no_vault_stub() -> None:
    from kazma_skills.native.calendar import router

    src = inspect.getsource(router)
    assert "vault_retrieve  # type: ignore" not in src
    assert "for simplicity we support env-first" not in src
    assert "CalendarNotConnectedError" in src


def test_gmail_oauth_requests_calendar_scope() -> None:
    from kazma_skills.native.email_manager.oauth_gmail import GMAIL_SCOPES

    assert "https://www.googleapis.com/auth/calendar" in GMAIL_SCOPES


def test_ms_oauth_requests_calendars_scope() -> None:
    from kazma_skills.native.email_manager.oauth_ms import SCOPES as DEVICE
    from kazma_skills.native.email_manager.oauth_ms_browser import SCOPES as BROWSER

    assert "Calendars.ReadWrite" in DEVICE
    assert "Calendars.ReadWrite" in BROWSER


def test_gmail_token_without_calendar_scope_is_not_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.calendar import credentials as creds

    monkeypatch.setattr(creds, "_email_cred", lambda env, vault="": {
        "EMAIL_GMAIL_ACCESS_TOKEN": "gmail-only",
        "EMAIL_GMAIL_SCOPES": "https://www.googleapis.com/auth/gmail.modify",
    }.get(env) or {
        "email.gmail.access_token": "gmail-only",
        "email.gmail.scopes": "https://www.googleapis.com/auth/gmail.modify",
    }.get(vault, ""))
    assert creds.google_access_token() == ""


def test_gmail_token_with_calendar_scope_is_reused(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.calendar import credentials as creds

    monkeypatch.setattr(creds, "_email_cred", lambda env, vault="": {
        "EMAIL_GMAIL_ACCESS_TOKEN": "shared",
        "EMAIL_GMAIL_SCOPES": "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar",
        "GOOGLE_CALENDAR_TOKEN": "",
        "GOOGLE_OAUTH_TOKEN": "",
    }.get(env) or {
        "email.gmail.access_token": "shared",
        "email.gmail.scopes": "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar",
        "calendar.google.access_token": "",
    }.get(vault, ""))
    assert creds.google_access_token() == "shared"


def test_explicit_google_without_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.calendar import credentials as creds
    from kazma_skills.native.calendar.router import (
        CalendarNotConnectedError,
        get_backend,
    )

    monkeypatch.setattr(creds, "google_access_token", lambda: "")
    monkeypatch.setattr(creds, "google_refresh_token", lambda: "")
    monkeypatch.setattr(creds, "google_connected", lambda: False)
    with pytest.raises(CalendarNotConnectedError) as exc:
        get_backend("google")
    assert "sandbox" not in str(exc.value).lower()
    assert "Connect" in str(exc.value)


def test_auto_without_token_uses_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.calendar import credentials as creds
    from kazma_skills.native.calendar import router as cal_router

    monkeypatch.setattr(creds, "google_connected", lambda: False)
    monkeypatch.setattr(creds, "microsoft_connected", lambda: False)
    cal_router._sandbox_instance = None
    backend = cal_router.get_backend("auto")
    assert backend.name == "sandbox"


def test_vault_google_token_selects_google(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.calendar import credentials as creds
    from kazma_skills.native.calendar.router import get_backend

    monkeypatch.setattr(creds, "google_access_token", lambda: "ya29.tok")
    monkeypatch.setattr(creds, "google_refresh_token", lambda: "1//rt")
    monkeypatch.setattr(creds, "google_connected", lambda: True)
    backend = get_backend("auto")
    assert backend.name == "google"


@pytest.mark.asyncio
async def test_list_events_google_without_token_is_honest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_skills.native.calendar import credentials as creds
    from kazma_skills.native.calendar.tools import list_events

    monkeypatch.setattr(creds, "google_access_token", lambda: "")
    monkeypatch.setattr(creds, "google_refresh_token", lambda: "")
    monkeypatch.setattr(creds, "google_connected", lambda: False)
    out = await list_events(provider="google")
    assert out.startswith("Error:")
    assert "sandbox" not in out.lower()
    assert "Connect Calendar" in out or "Connect with Google" in out


def test_calendar_oauth_reuses_gmail_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "secret")
    from kazma_skills.native.calendar.oauth_google import start_google_calendar_oauth

    r = start_google_calendar_oauth("http://127.0.0.1:9090")
    assert r["ok"] is True
    assert "gmail/callback" in r["redirect_uri"]
    assert "auth%2Fcalendar" in r["authorize_url"] or "auth/calendar" in r["authorize_url"]


def test_gmail_callback_dispatches_calendar_state() -> None:
    from kazma_ui import email_api

    src = inspect.getsource(email_api.gmail_oauth_callback)
    assert "google_calendar" in src
    assert "finish_google_calendar_oauth" in src


def test_calendar_disconnect_sends_csrf_header() -> None:
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[1]
        / "kazma-ui"
        / "kazma_ui"
        / "static"
        / "js"
        / "settings_integrations.js"
    ).read_text(encoding="utf-8")
    assert "/api/calendar/oauth/google/disconnect" in src
    idx = src.index("/api/calendar/oauth/google/disconnect")
    window = src[idx : idx + 400]
    assert "X-Requested-With" in window


def test_calendar_api_mounted() -> None:
    from kazma_ui import calendar_api

    paths = [getattr(r, "path", "") for r in calendar_api.router.routes]
    assert any("/status" in p for p in paths)
    assert any("oauth/google/start.json" in p for p in paths)


def test_ms_device_client_id_reads_vault() -> None:
    from kazma_skills.native.email_manager import oauth_ms

    src = inspect.getsource(oauth_ms._client_id)
    assert "cred(" in src
    assert "email.microsoft.client_id" in src


def test_peek_state_does_not_consume() -> None:
    from kazma_skills.native.email_manager.oauth_common import (
        new_state,
        peek_state,
        pop_state,
    )

    s = new_state("google_calendar", redirect_uri="http://localhost/cb")
    meta = peek_state(s)
    assert meta is not None and meta["provider"] == "google_calendar"
    meta2 = peek_state(s)
    assert meta2 is not None
    popped = pop_state(s)
    assert popped is not None
    assert peek_state(s) is None


@pytest.mark.asyncio
async def test_gmail_finish_persists_calendar_when_scope_granted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kazma_skills.native.email_manager import oauth_gmail as og
    from kazma_skills.native.email_manager.oauth_common import new_state

    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "sec")
    state = new_state(
        "gmail",
        redirect_uri="http://127.0.0.1:9090/api/email/oauth/gmail/callback",
    )
    stored: dict[str, str] = {}

    def fake_store(name: str, value: str, category: str = "email") -> bool:
        stored[name] = value
        return True

    monkeypatch.setattr(og, "vault_store", fake_store)
    monkeypatch.setattr(
        "kazma_skills.native.calendar.credentials._vault_store",
        fake_store,
    )
    monkeypatch.setattr(
        "kazma_skills.native.email_manager.credentials.vault_store",
        fake_store,
    )

    class FakeResp:
        def __init__(self, status: int, data: dict, text: str = ""):
            self.status_code = status
            self.content = b"1"
            self.text = text or "{}"
            self._data = data

        def json(self):
            return self._data

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp(
                200,
                {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "scope": (
                        "https://www.googleapis.com/auth/gmail.modify "
                        "https://www.googleapis.com/auth/calendar openid"
                    ),
                },
            )

        async def get(self, url, *a, **k):
            u = str(url)
            if "calendar" in u:
                return FakeResp(200, {"items": []})
            if "gmail.googleapis.com" in u:
                return FakeResp(200, {"emailAddress": "user@company.com"})
            if "drive/v3/about" in u:
                return FakeResp(200, {"user": {}})
            if "tokeninfo" in u:
                return FakeResp(
                    200,
                    {
                        "scope": (
                            "https://www.googleapis.com/auth/gmail.modify "
                            "https://www.googleapis.com/auth/calendar"
                        )
                    },
                )
            return FakeResp(200, {"email": "user@company.com"})

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        r = await og.finish_gmail_oauth("code123", state)
    assert r["ok"] is True
    assert r.get("calendar_ok") is True
    assert stored.get("calendar.google.access_token") == "at"
    assert stored.get("calendar.google.refresh_token") == "rt"
