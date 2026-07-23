"""Email polish: multi-account routing, Graph mapping, OAuth helpers, vault persist mock."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_list_account_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ACCOUNTS", "personal, work")
    from kazma_skills.native.email_manager.credentials import list_account_aliases

    assert list_account_aliases() == ["personal", "work"]


def test_account_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ACCOUNT_WORK_TYPE", "gmail")
    monkeypatch.setenv("EMAIL_ACCOUNT_WORK_ADDRESS", "w@example.com")
    monkeypatch.setenv("EMAIL_ACCOUNT_WORK_PASSWORD", "secret")
    from kazma_skills.native.email_manager.credentials import account_config

    cfg = account_config("work")
    assert cfg["type"] == "gmail"
    assert cfg["address"] == "w@example.com"
    assert cfg["password"] == "secret"


def test_resolve_account_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_ACCOUNTS", "work")
    monkeypatch.setenv("EMAIL_ACCOUNT_WORK_TYPE", "sandbox")
    from kazma_skills.native.email_manager.router import resolve_provider

    assert resolve_provider(account="work") == "account:work"
    assert resolve_provider("work") == "account:work"


def test_graph_folder_path() -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
    )

    b = MicrosoftGraphBackend(access_token="t")
    assert "inbox" in b._folder_path("INBOX").lower()
    assert "sentitems" in b._folder_path("Sent").lower()
    assert "deleteditems" in b._folder_path("Trash").lower()


def test_graph_map_message_categories() -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
    )

    b = MicrosoftGraphBackend(access_token="t")
    m = b._map_message(
        {
            "id": "abc",
            "subject": "Hi",
            "from": {"emailAddress": {"address": "a@b.com"}},
            "toRecipients": [],
            "bodyPreview": "hello",
            "isRead": False,
            "flag": {"flagStatus": "flagged"},
            "categories": ["Important"],
        }
    )
    assert m.id == "abc"
    assert m.unread is True
    assert m.starred is True


@pytest.mark.asyncio
async def test_graph_refresh_persists(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import (
        MicrosoftGraphBackend,
    )

    stored: dict[str, str] = {}

    def fake_store(name: str, value: str, category: str = "email") -> bool:
        stored[name] = value
        return True

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.credentials.vault_store",
        fake_store,
    )

    b = MicrosoftGraphBackend(
        access_token="old",
        refresh_token="r1",
        client_id="cid",
        tenant_id="common",
    )

    class FakeResp:
        status_code = 200
        content = b'{"access_token":"new_access","refresh_token":"new_refresh"}'

        def json(self):
            return {"access_token": "new_access", "refresh_token": "new_refresh"}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await b._refresh()

    assert b.access_token == "new_access"
    assert b.refresh_token == "new_refresh"
    assert stored.get("email.microsoft.access_token") == "new_access"
    assert stored.get("email.microsoft.refresh_token") == "new_refresh"


@pytest.mark.asyncio
async def test_device_code_start_requires_client_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_MS_CLIENT_ID", raising=False)
    from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

    r = await start_device_code_flow()
    assert r["ok"] is False
    assert "CLIENT_ID" in r["error"]


@pytest.mark.asyncio
async def test_device_code_start_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMAIL_MS_CLIENT_ID", "test-client")

    class FakeResp:
        status_code = 200
        text = "{}"

        def json(self):
            return {
                "device_code": "dev123",
                "user_code": "ABCD",
                "verification_uri": "https://microsoft.com/devicelogin",
                "expires_in": 900,
                "interval": 5,
                "message": "go",
            }

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return FakeResp()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        from kazma_skills.native.email_manager.oauth_ms import start_device_code_flow

        r = await start_device_code_flow()
    assert r["ok"] is True
    assert r["user_code"] == "ABCD"
    assert r["device_code"] == "dev123"


def test_status_summary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EMAIL_GMAIL_ADDRESS", raising=False)
    monkeypatch.delenv("EMAIL_GMAIL_APP_PASSWORD", raising=False)
    from kazma_skills.native.email_manager.credentials import status_summary

    s = status_summary()
    assert s["sandbox_always"] is True
    assert "gmail_configured" in s
