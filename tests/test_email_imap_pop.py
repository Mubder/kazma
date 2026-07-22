"""IMAP/POP presets, protocol connect, POP backend unit tests."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_presets_gmail_microsoft() -> None:
    from kazma_skills.native.email_manager.presets import get_preset, list_presets

    g_imap = get_preset("gmail", "imap")
    assert g_imap["imap_host"] == "imap.gmail.com"
    assert g_imap["smtp_host"] == "smtp.gmail.com"
    g_pop = get_preset("gmail", "pop")
    assert g_pop["pop_host"] == "pop.gmail.com"
    ms = get_preset("microsoft", "imap")
    assert "outlook" in ms["imap_host"]
    assert "office365" in ms["smtp_host"]
    all_p = list_presets()
    assert "gmail" in all_p and "microsoft" in all_p


def test_connect_protocol_gmail_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)

    stored: dict[str, str] = {}

    def fake_store(name: str, value: str, category: str = "email") -> bool:
        stored[name] = value
        return True

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.protocol_connect.vault_store",
        fake_store,
    )
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol
    from kazma_skills.native.email_manager.router import get_backend

    r = connect_protocol(
        provider="gmail",
        protocol="imap",
        address="me@gmail.com",
        password="abcd efgh ijkl mnop",
    )
    assert r["ok"] is True
    assert r["protocol"] == "imap"
    assert stored.get("email.gmail.auth") == "imap"

    b = get_backend("gmail")
    assert b.name == "gmail"
    assert "imap.gmail.com" in b.imap_host


def test_connect_protocol_gmail_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.protocol_connect.vault_store",
        lambda *a, **k: True,
    )
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol
    from kazma_skills.native.email_manager.router import get_backend

    r = connect_protocol(
        provider="gmail",
        protocol="pop",
        address="me@gmail.com",
        password="apppasswordhere",
    )
    assert r["ok"] is True
    b = get_backend("gmail")
    assert b.name == "gmail_pop"
    assert "pop.gmail.com" in b.pop_host


def test_connect_protocol_microsoft_imap(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.protocol_connect.vault_store",
        lambda *a, **k: True,
    )
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol
    from kazma_skills.native.email_manager.router import get_backend

    r = connect_protocol(
        provider="microsoft",
        protocol="imap",
        address="you@contoso.com",
        password="secretpass",
    )
    assert r["ok"] is True
    b = get_backend("microsoft")
    assert "microsoft" in b.name
    assert "outlook" in b.imap_host


def test_connect_protocol_microsoft_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)

    monkeypatch.setattr(
        "kazma_skills.native.email_manager.protocol_connect.vault_store",
        lambda *a, **k: True,
    )
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol
    from kazma_skills.native.email_manager.router import detect_available_provider, get_backend

    r = connect_protocol(
        provider="microsoft",
        protocol="pop",
        address="you@contoso.com",
        password="secretpass",
    )
    assert r["ok"] is True
    assert detect_available_provider() == "microsoft"
    b = get_backend("microsoft")
    assert b.name == "microsoft_pop"


def test_status_summary_auth_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EMAIL_GMAIL_ADDRESS", "a@gmail.com")
    monkeypatch.setenv("EMAIL_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_GMAIL_AUTH", "pop")
    from kazma_skills.native.email_manager.credentials import status_summary

    s = status_summary()
    assert s["gmail_configured"] is True
    assert s["gmail_auth_mode"] == "pop"
    assert s["gmail_pop"] is True


def test_detect_prefers_gmail_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("EMAIL_GMAIL_ADDRESS", "a@gmail.com")
    monkeypatch.setenv("EMAIL_GMAIL_APP_PASSWORD", "pw")
    monkeypatch.setenv("EMAIL_GMAIL_AUTH", "imap")
    from kazma_skills.native.email_manager.router import detect_available_provider

    assert detect_available_provider() == "gmail"


@pytest.mark.asyncio
async def test_pop_backend_list_send_delete() -> None:
    from kazma_skills.native.email_manager.backends.pop_smtp import PopSmtpBackend
    from kazma_skills.native.email_manager.models import ListQuery, SendRequest

    raw_headers = [
        b"From: boss@corp.com",
        b"To: me@example.com",
        b"Subject: Hello POP",
        b"Date: Mon, 1 Jan 2024 00:00:00 +0000",
        b"",
        b"Body line",
    ]

    class FakePop:
        def stat(self):
            return (1, 100)

        def top(self, n, lines):
            return (b"+OK", raw_headers, len(b"\r\n".join(raw_headers)))

        def retr(self, n):
            return (b"+OK", raw_headers, 1)

        def dele(self, n):
            return b"+OK"

        def quit(self):
            return b"+OK"

        def user(self, u):
            pass

        def pass_(self, p):
            pass

    b = PopSmtpBackend(
        name="pop_test",
        address="me@example.com",
        password="secret",
        pop_host="pop.example.com",
        smtp_host="smtp.example.com",
    )

    with patch.object(b, "_pop", return_value=FakePop()):
        msgs = await b.list_messages(ListQuery(limit=10))
        assert len(msgs) == 1
        assert "Hello POP" in msgs[0].subject
        full = await b.get_message("1")
        assert "Body" in full.body or full.subject
        await b.delete("1")

    # SMTP send
    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance
        res = await b.send(
            SendRequest(
                action="send",
                to=["x@y.com"],
                subject="Hi",
                body="Hello",
            )
        )
        assert res.ok is True
        instance.send_message.assert_called()


def test_resolve_provider_pop(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in list(__import__("os").environ):
        if k.startswith("EMAIL_"):
            monkeypatch.delenv(k, raising=False)
    from kazma_skills.native.email_manager.router import resolve_provider

    assert resolve_provider("pop") == "pop"
    assert resolve_provider("imap") == "imap"
