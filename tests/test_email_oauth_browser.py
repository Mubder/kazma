"""Browser OAuth helpers for Gmail + Microsoft (unit, no live network)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


def test_oauth_state_roundtrip() -> None:
    from kazma_skills.native.email_manager.oauth_common import new_state, pop_state

    s = new_state("gmail", redirect_uri="http://localhost/cb")
    meta = pop_state(s)
    assert meta is not None
    assert meta["provider"] == "gmail"
    assert pop_state(s) is None


def test_gmail_start_requires_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_GMAIL_CLIENT_ID", raising=False)
    monkeypatch.delenv("EMAIL_GMAIL_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("GOOGLE_OAUTH_CLIENT_ID", raising=False)
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    r = start_gmail_oauth("http://127.0.0.1:9090")
    assert r["ok"] is False


def test_gmail_start_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "cid.apps.googleusercontent.com")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "secret")
    from kazma_skills.native.email_manager.oauth_gmail import start_gmail_oauth

    r = start_gmail_oauth("http://127.0.0.1:9090")
    assert r["ok"] is True
    assert "accounts.google.com" in r["authorize_url"]
    assert "gmail/callback" in r["redirect_uri"]
    assert "gmail.modify" in r["authorize_url"]
    assert "include_granted_scopes" not in r["authorize_url"]


def test_ms_browser_start_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_MS_CLIENT_ID", "azure-cid")
    monkeypatch.setenv("EMAIL_MS_TENANT_ID", "common")
    from kazma_skills.native.email_manager.oauth_ms_browser import start_ms_browser_oauth

    r = start_ms_browser_oauth("http://127.0.0.1:9090")
    assert r["ok"] is True
    assert "login.microsoftonline.com" in r["authorize_url"]
    assert "microsoft/callback" in r["redirect_uri"]


@pytest.mark.asyncio
async def test_gmail_finish_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.email_manager.oauth_common import new_state
    from kazma_skills.native.email_manager import oauth_gmail as og

    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "sec")
    state = new_state("gmail", redirect_uri="http://127.0.0.1:9090/api/email/oauth/gmail/callback")
    stored: dict[str, str] = {}

    def fake_store(name: str, value: str, category: str = "email") -> bool:
        stored[name] = value
        return True

    monkeypatch.setattr(og, "vault_store", fake_store)

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
                    "scope": "https://www.googleapis.com/auth/gmail.modify openid",
                },
            )

        async def get(self, url, *a, **k):
            u = str(url)
            if "tokeninfo" in u:
                return FakeResp(
                    200,
                    {"scope": "https://www.googleapis.com/auth/gmail.modify openid"},
                )
            if "gmail.googleapis.com" in u:
                return FakeResp(200, {"emailAddress": "user@company.com"})
            return FakeResp(200, {"email": "user@company.com"})

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        r = await og.finish_gmail_oauth("code123", state)
    assert r["ok"] is True
    assert r.get("email") == "user@company.com"
    assert stored.get("email.gmail.access_token") == "at"
    assert stored.get("email.gmail.refresh_token") == "rt"


@pytest.mark.asyncio
async def test_gmail_finish_rejects_insufficient_scopes(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.email_manager.oauth_common import new_state
    from kazma_skills.native.email_manager import oauth_gmail as og

    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_ID", "cid")
    monkeypatch.setenv("EMAIL_GMAIL_CLIENT_SECRET", "sec")
    state = new_state("gmail", redirect_uri="http://127.0.0.1:9090/api/email/oauth/gmail/callback")
    monkeypatch.setattr(og, "vault_store", lambda *a, **k: True)

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
            # Token with only email — no Gmail
            return FakeResp(
                200,
                {
                    "access_token": "at",
                    "refresh_token": "rt",
                    "scope": "openid https://www.googleapis.com/auth/userinfo.email",
                },
            )

        async def get(self, url, *a, **k):
            u = str(url)
            if "gmail.googleapis.com" in u:
                return FakeResp(
                    403,
                    {},
                    text='{"error":{"message":"Request had insufficient authentication scopes."}}',
                )
            if "tokeninfo" in u:
                return FakeResp(
                    200,
                    {"scope": "openid https://www.googleapis.com/auth/userinfo.email"},
                )
            return FakeResp(200, {"email": "user@company.com"})

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        r = await og.finish_gmail_oauth("code123", state)
    assert r["ok"] is False
    assert r.get("code") == "insufficient_scopes"


def test_scopes_include_gmail_mail() -> None:
    from kazma_skills.native.email_manager.oauth_gmail import scopes_include_gmail_mail

    assert scopes_include_gmail_mail(
        "openid https://www.googleapis.com/auth/gmail.modify"
    )
    assert not scopes_include_gmail_mail(
        "openid https://www.googleapis.com/auth/userinfo.email"
    )


def test_detect_prefers_gmail_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_GMAIL_REFRESH_TOKEN", "rt")
    monkeypatch.delenv("EMAIL_GMAIL_APP_PASSWORD", raising=False)
    from kazma_skills.native.email_manager.router import detect_available_provider

    assert detect_available_provider() == "gmail"
