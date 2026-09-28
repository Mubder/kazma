"""Kazma knows the Microsoft mailbox's own address (2026-09-28).

Asked to email the owner's MSN account from itself, the agent had no way to
learn the address: Settings showed none (``status_summary`` reported one only
for IMAP/POP logins), the tools' banner said "[microsoft_graph mode]", and it
read the address out of Sent Items. The sign-in has always asked for
``openid profile``; the id_token in Microsoft's answer names the account, and
nothing read it.

- ``oauth_common.address_from_id_token`` reads it;
- ``oauth_ms.store_microsoft_tokens`` is the one place a Microsoft token
  response is kept (device-code and browser flows both end there) and keeps
  the address as ``email.microsoft.oauth_address`` -- apart from an IMAP/POP
  address, so a leftover protocol login never names the OAuth account;
- ``status_summary`` and the tools' banner show it.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
EMAIL = REPO / "kazma-skills" / "kazma_skills" / "native" / "email_manager"


def _jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256', 'typ': 'JWT'})}.{seg(claims)}.signature"


def test_the_id_token_names_the_account() -> None:
    from kazma_skills.native.email_manager.oauth_common import address_from_id_token

    assert address_from_id_token(_jwt({"preferred_username": "B.Alfaris@MSN.com"})) == "b.alfaris@msn.com"
    assert address_from_id_token(_jwt({"email": "me@outlook.com", "preferred_username": "x@y.com"})) == "me@outlook.com"
    # Not an address, not a token, or no token at all: nothing, never a guess.
    assert address_from_id_token(_jwt({"preferred_username": "live.com#someone"})) == ""
    assert address_from_id_token("not-a-jwt") == ""
    assert address_from_id_token("a.%%%.c") == ""
    assert address_from_id_token(None) == ""


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """A vault in a dict, for every reader and writer the email code uses."""
    from kazma_skills.native.email_manager import credentials, oauth_ms

    store: dict[str, str] = {}

    def put(name: str, value: str, category: str = "email") -> bool:
        store[name] = value
        return True

    monkeypatch.setattr(credentials, "vault_store", put)
    monkeypatch.setattr(oauth_ms, "vault_store", put)
    monkeypatch.setattr(credentials, "vault_retrieve", lambda name: store.get(name, ""))
    import kazma_skills.native.calendar.credentials as cal

    monkeypatch.setattr(cal, "persist_microsoft_tokens", lambda *a, **k: None)
    return store


def test_a_sign_in_keeps_its_address_apart_from_a_protocol_login(vault: dict[str, str]) -> None:
    from kazma_skills.native.email_manager.oauth_ms import store_microsoft_tokens

    vault["email.microsoft.address"] = "old-imap@hotmail.com"  # a leftover IMAP login
    got = store_microsoft_tokens(
        {"access_token": "a", "refresh_token": "r", "scope": "Mail.Send openid",
         "id_token": _jwt({"preferred_username": "b.alfaris@msn.com"})},
        "client-1",
    )
    assert got == "b.alfaris@msn.com"
    assert vault["email.microsoft.oauth_address"] == "b.alfaris@msn.com"
    assert vault["email.microsoft.address"] == "old-imap@hotmail.com", "the protocol address is not overwritten"
    assert vault["email.microsoft.access_token"] == "a" and vault["email.microsoft.auth"] == "oauth"


def test_no_id_token_stores_no_address(vault: dict[str, str]) -> None:
    from kazma_skills.native.email_manager.oauth_ms import store_microsoft_tokens

    assert store_microsoft_tokens({"access_token": "a"}, "client-1") == ""
    assert "email.microsoft.oauth_address" not in vault


def test_settings_name_the_oauth_account_not_the_leftover(vault: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_skills.native.email_manager import credentials

    monkeypatch.setattr(credentials, "microsoft_auth_mode", lambda: "oauth")
    vault["email.microsoft.address"] = "old-imap@hotmail.com"
    vault["email.microsoft.oauth_address"] = "b.alfaris@msn.com"
    assert credentials.status_summary()["microsoft_address"] == "b.alfaris@msn.com"

    monkeypatch.setattr(credentials, "microsoft_auth_mode", lambda: "imap")
    assert credentials.status_summary()["microsoft_address"] == "old-imap@hotmail.com"


def test_the_tools_banner_names_the_mailbox() -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend
    from kazma_skills.native.email_manager.router import mode_banner

    graph = MicrosoftGraphBackend(access_token="t", address="b.alfaris@msn.com")
    assert mode_banner(graph) == "[microsoft_graph mode · mailbox b.alfaris@msn.com]"
    assert mode_banner(MicrosoftGraphBackend(access_token="t")) == "[microsoft_graph mode]"
    assert mode_banner(SandboxBackend()) == "[sandbox mode]"


def test_one_place_keeps_a_microsoft_token_response() -> None:
    """Both sign-in flows end in store_microsoft_tokens; nothing else writes
    the access token (the two flows used to keep two copies of the storing
    code, and only one would have learned the address)."""
    writers = []
    for path in sorted(EMAIL.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if re.search(r'vault_store\(\s*"email\.microsoft\.access_token"', src):
            writers.append(path.name)
    # The Graph backend refreshes its own token; the sign-in writes it once.
    assert sorted(writers) == ["microsoft_graph.py", "oauth_ms.py"], writers
    for flow in ("oauth_ms.py", "oauth_ms_browser.py"):
        assert "store_microsoft_tokens(" in (EMAIL / flow).read_text(encoding="utf-8"), flow
