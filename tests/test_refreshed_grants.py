"""A refreshed sign-in is kept only where it is still held (2026-09-29).

Found while answering "can Kazma take three Gmail accounts": a token refresh
wrote its new tokens without asking whose place it was writing to.

- The main Gmail refresh copied its grant into Google Calendar whenever the
  calendar was connected -- also when the calendar was signed in as ANOTHER
  Google account. The calendar then read the Gmail account's events under
  the other account's name, and the connector-health check runs a Gmail
  refresh on every pass.
- Every refresh (main and extra accounts, mail and calendar, Google and
  Microsoft) wrote back after a disconnect or a new sign-in that happened
  while its request ran: the account signed back in, or the old account put
  back over the new one.
- The main Microsoft mailbox signed in before Kazma read the address
  (2026-09-28) had none, so "send it from me@msn.com" was refused as not
  connected. Its refresh now asks for the sign-in's identity too and keeps
  the address the id_token names.
"""

from __future__ import annotations

import ast
import asyncio
import base64
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "kazma-skills" / "kazma_skills" / "native"
GMAIL_ONLY = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_WITH_CALENDAR = GMAIL_ONLY + " https://www.googleapis.com/auth/calendar"
CALENDAR_ONLY = "https://www.googleapis.com/auth/calendar openid"
MS_SCOPES = "Mail.ReadWrite Mail.Send Calendars.ReadWrite offline_access openid profile"
_ENV = (
    "EMAIL_ACCOUNTS", "EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN",
    "EMAIL_GMAIL_ADDRESS", "EMAIL_GMAIL_AUTH", "EMAIL_GMAIL_SCOPES", "EMAIL_GMAIL_APP_PASSWORD",
    "EMAIL_GMAIL_CLIENT_ID", "EMAIL_GMAIL_CLIENT_SECRET", "GOOGLE_OAUTH_CLIENT_ID",
    "EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN", "EMAIL_MS_AUTH", "EMAIL_MS_ADDRESS",
    "EMAIL_MS_PASSWORD", "EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_PROTOCOL",
    "GOOGLE_CALENDAR_TOKEN", "GOOGLE_CALENDAR_REFRESH", "GOOGLE_OAUTH_TOKEN",
    "GOOGLE_CALENDAR_SCOPES", "GOOGLE_CALENDAR_ADDRESS",
    "MS_CALENDAR_TOKEN", "MS_CALENDAR_REFRESH", "MS_GRAPH_TOKEN", "EMAIL_DEFAULT_PROVIDER",
)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """One dict behind every vault reader and writer mail and calendar use;
    no mail setting in the environment. The account list is the per-test
    settings store the suite's conftest gives every test."""
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager import credentials as mail
    from kazma_skills.native.email_manager import oauth_gmail, oauth_ms, protocol_connect

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

    for mod in (mail, oauth_gmail, oauth_ms):
        monkeypatch.setattr(mod, "vault_store", put)
    monkeypatch.setattr(mail, "vault_retrieve", lambda name: store.get(name, ""))
    monkeypatch.setattr(mail, "vault_delete", drop)
    monkeypatch.setattr(protocol_connect, "_vault_delete", drop)
    monkeypatch.setattr(cal, "_vault_delete", drop)
    return store


def _jwt(claims: dict) -> str:
    def seg(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{seg({'alg': 'RS256'})}.{seg(claims)}.sig"


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _token_endpoint(answers: list[tuple[int, dict]], *, meanwhile=None, posts: list | None = None):
    """An ``httpx.AsyncClient`` whose POSTs get *answers* in turn (the last
    one repeats). *meanwhile* runs before the first answer: what happened
    while the refresh request was on the network."""
    pending = [meanwhile] if meanwhile else []
    queue = list(answers)

    class Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, url, data=None, **k):
            if posts is not None:
                posts.append(dict(data or {}))
            while pending:
                pending.pop()()
            status, payload = queue.pop(0) if len(queue) > 1 else queue[0]
            return _Resp(payload, status)

    return Client


def _use(monkeypatch: pytest.MonkeyPatch, client) -> None:
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", client)


# ── the bug: a Gmail refresh moved a calendar of another account ────────


def _gmail_signed_in(refresh: str = "gmail-r", scopes: str = GMAIL_WITH_CALENDAR) -> None:
    from kazma_skills.native.email_manager.oauth_gmail import persist_gmail_tokens

    persist_gmail_tokens("gmail-a", refresh, "me@gmail.com", scopes=scopes)


def _calendar_signed_in(refresh: str, address: str) -> None:
    from kazma_skills.native.calendar import credentials as cal

    cal.persist_google_tokens("cal-a", refresh, address, CALENDAR_ONLY)


def test_a_gmail_refresh_leaves_a_calendar_of_another_account_alone(vault, monkeypatch) -> None:
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.email_manager.oauth_gmail import refresh_gmail_access_token

    _gmail_signed_in()
    _calendar_signed_in("other-r", "other@gmail.com")
    _use(monkeypatch, _token_endpoint([(200, {"access_token": "gmail-a2", "scope": GMAIL_WITH_CALENDAR})]))
    asyncio.run(refresh_gmail_access_token("gmail-r", client_id="c", client_secret="s"))

    assert vault["email.gmail.access_token"] == "gmail-a2"
    assert cal.google_refresh_token() == "other-r", "the calendar was moved to the Gmail account"
    assert cal.google_access_token() == "cal-a"
    assert cal.status_summary()["google_address"] == "other@gmail.com"


def test_a_shared_google_sign_in_stays_one(vault, monkeypatch) -> None:
    """Negative control of the test above, and the other direction: a
    calendar holding the Gmail grant follows a Gmail refresh, and Gmail
    follows a calendar refresh of it."""
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.calendar.oauth_google import refresh_google_calendar_access_token
    from kazma_skills.native.email_manager.oauth_gmail import refresh_gmail_access_token

    _gmail_signed_in("shared-r")
    _calendar_signed_in("shared-r", "me@gmail.com")
    _use(monkeypatch, _token_endpoint([(200, {"access_token": "a2", "scope": GMAIL_WITH_CALENDAR})]))
    asyncio.run(refresh_gmail_access_token("shared-r", client_id="c", client_secret="s"))
    assert vault["email.gmail.access_token"] == "a2" and cal.google_access_token() == "a2"

    _use(monkeypatch, _token_endpoint([(200, {"access_token": "a3", "scope": GMAIL_WITH_CALENDAR})]))
    asyncio.run(refresh_google_calendar_access_token("shared-r", client_id="c", client_secret="s"))
    assert vault["email.gmail.access_token"] == "a3" and cal.google_access_token() == "a3"
    assert vault["calendar.google.ok"] == "ok"


# ── every refresh: what happened meanwhile is not undone ────────────────


def _backend_classes() -> set[str]:
    """Every backend class with a token refresh (the same walk as
    test_email_extra_accounts' gate)."""
    found: set[str] = set()
    for folder in (SKILLS / "email_manager" / "backends", SKILLS / "calendar" / "backends"):
        for path in folder.glob("*.py"):
            for node in ast.parse(path.read_text(encoding="utf-8")).body:
                if isinstance(node, ast.ClassDef) and {"_refresh", "_do_refresh"} & {
                    n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                }:
                    found.add(node.name)
    return found


def _paths():
    """How each backend is signed in, built, refreshed, disconnected, signed
    in again and read back -- for the main account and for an extra one."""
    from kazma_skills.native.calendar import credentials as cal
    from kazma_skills.native.calendar.backends.google_calendar import GoogleCalendarBackend
    from kazma_skills.native.calendar.backends.outlook_calendar import OutlookCalendarBackend
    from kazma_skills.native.email_manager import accounts, oauth_gmail, oauth_ms
    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend
    from kazma_skills.native.email_manager.credentials import account_config, cred
    from kazma_skills.native.email_manager.protocol_connect import disconnect_protocol

    def ms_payload(access: str, refresh: str) -> dict:
        return {"access_token": access, "refresh_token": refresh, "scope": MS_SCOPES,
                "id_token": _jwt({"preferred_username": "me@msn.com"})}

    def extra(kind: str, scopes: str):
        return (
            lambda: accounts.upsert_oauth_account("work", kind, "work@example.com", "old-a", "old-r", scopes),
            lambda: accounts.remove_account("work"),
            lambda: accounts.upsert_oauth_account("work", kind, "work@example.com", "newer-a", "newer", scopes),
            lambda: (account_config("work").get("access_token") or "", account_config("work").get("refresh_token") or ""),
        )

    return {
        ("GmailApiBackend", "main"): (
            lambda: oauth_gmail.persist_gmail_tokens("old-a", "old-r", "me@gmail.com", scopes=GMAIL_ONLY),
            lambda: disconnect_protocol("gmail"),
            lambda: oauth_gmail.persist_gmail_tokens("newer-a", "newer", "me2@gmail.com", scopes=GMAIL_ONLY),
            lambda: (cred("EMAIL_GMAIL_ACCESS_TOKEN", "email.gmail.access_token"),
                     cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token")),
            lambda: GmailApiBackend(access_token="old-a", refresh_token="old-r", client_id="c", client_secret="s"),
            "_refresh",
        ),
        ("GmailApiBackend", "extra"): (
            *extra("gmail", GMAIL_WITH_CALENDAR),
            lambda: GmailApiBackend(access_token="old-a", refresh_token="old-r", client_id="c",
                                    client_secret="s", account_alias="work"),
            "_refresh",
        ),
        ("MicrosoftGraphBackend", "main"): (
            lambda: oauth_ms.store_microsoft_tokens(ms_payload("old-a", "old-r"), "cid"),
            lambda: disconnect_protocol("microsoft"),
            lambda: oauth_ms.store_microsoft_tokens(ms_payload("newer-a", "newer"), "cid"),
            lambda: (cred("EMAIL_MS_ACCESS_TOKEN", "email.microsoft.access_token"),
                     cred("EMAIL_MS_REFRESH_TOKEN", "email.microsoft.refresh_token")),
            lambda: MicrosoftGraphBackend(access_token="old-a", refresh_token="old-r", client_id="cid"),
            "_refresh",
        ),
        ("MicrosoftGraphBackend", "extra"): (
            *extra("microsoft", MS_SCOPES),
            lambda: MicrosoftGraphBackend(access_token="old-a", refresh_token="old-r", client_id="cid",
                                          account_alias="work"),
            "_refresh",
        ),
        ("GoogleCalendarBackend", "main"): (
            lambda: cal.persist_google_tokens("old-a", "old-r", "cal@gmail.com", CALENDAR_ONLY),
            cal.clear_google_tokens,
            lambda: (cal.turn_calendar_on("google"),
                     cal.persist_google_tokens("newer-a", "newer", "cal2@gmail.com", CALENDAR_ONLY)),
            lambda: (cred("GOOGLE_CALENDAR_TOKEN", cal.VAULT_GOOGLE_ACCESS),
                     cred("GOOGLE_CALENDAR_REFRESH", cal.VAULT_GOOGLE_REFRESH)),
            lambda: GoogleCalendarBackend("old-a", "old-r"),
            "_do_refresh",
        ),
        ("GoogleCalendarBackend", "extra"): (
            *extra("gmail", GMAIL_WITH_CALENDAR),
            lambda: GoogleCalendarBackend("old-a", "old-r", account_alias="work"),
            "_do_refresh",
        ),
        ("OutlookCalendarBackend", "main"): (
            lambda: oauth_ms.store_microsoft_calendar_tokens(ms_payload("old-a", "old-r")),
            cal.clear_microsoft_calendar_tokens,
            lambda: oauth_ms.store_microsoft_calendar_tokens(ms_payload("newer-a", "newer")),
            lambda: (cred("MS_CALENDAR_TOKEN", cal.VAULT_MS_ACCESS),
                     cred("MS_CALENDAR_REFRESH", cal.VAULT_MS_REFRESH)),
            lambda: OutlookCalendarBackend("old-a", refresh_token="old-r", client_id="cid"),
            "_do_refresh",
        ),
        ("OutlookCalendarBackend", "extra"): (
            *extra("microsoft", MS_SCOPES),
            lambda: OutlookCalendarBackend("old-a", refresh_token="old-r", client_id="cid",
                                           account_alias="work"),
            "_do_refresh",
        ),
    }


_CASES = [
    (cls, who, meanwhile)
    for cls in ("GmailApiBackend", "MicrosoftGraphBackend", "GoogleCalendarBackend", "OutlookCalendarBackend")
    for who in ("main", "extra")
    for meanwhile in ("nothing", "disconnected", "signed_in_again")
]


@pytest.mark.parametrize("cls, who, meanwhile", _CASES)
def test_a_refresh_never_undoes_what_happened_while_it_ran(vault, monkeypatch, cls, who, meanwhile) -> None:
    sign_in, disconnect, sign_in_again, held, build, method = _paths()[(cls, who)]
    sign_in()
    backend = build()
    action = {"nothing": None, "disconnected": disconnect, "signed_in_again": sign_in_again}[meanwhile]
    _use(monkeypatch, _token_endpoint(
        [(200, {"access_token": "fresh-a", "refresh_token": "fresh-r", "scope": MS_SCOPES})],
        meanwhile=action,
    ))
    asyncio.run(getattr(backend, method)())

    expected = {
        # The control: with nothing in between, the refresh is kept.
        "nothing": ("fresh-a", "fresh-r"),
        "disconnected": ("", ""),
        "signed_in_again": ("newer-a", "newer"),
    }[meanwhile]
    assert held() == expected, f"{cls} ({who}) after '{meanwhile}'"


def test_every_refreshing_backend_is_covered() -> None:
    """Gate: the cases above cover every backend class with a refresh, for
    the main account and an extra one."""
    assert {c for c, _, _ in _CASES} == _backend_classes()
    assert {(c, w) for c, w, _ in _CASES} == set(_paths())


# ── the main Microsoft mailbox learns its address ───────────────────────


def _ms_mail_without_address(vault: dict[str, str]) -> None:
    """Signed in before Kazma read the address: tokens, no address."""
    vault.update({
        "email.microsoft.access_token": "old-a", "email.microsoft.refresh_token": "old-r",
        "email.microsoft.auth": "oauth", "email.microsoft.scopes": MS_SCOPES,
        "email.microsoft.client_id": "cid",
    })


def test_a_microsoft_refresh_names_the_mailbox(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import accounts
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend
    from kazma_skills.native.email_manager.credentials import status_summary
    from kazma_skills.native.email_manager.router import resolve_provider

    _ms_mail_without_address(vault)
    with pytest.raises(Exception, match="No connected mail account"):
        resolve_provider(account="me@msn.com")

    posts: list[dict] = []
    _use(monkeypatch, _token_endpoint([(200, {
        "access_token": "fresh-a", "refresh_token": "fresh-r", "scope": MS_SCOPES,
        "id_token": _jwt({"preferred_username": "Me@MSN.com"}),
    })], posts=posts))
    backend = MicrosoftGraphBackend(access_token="old-a", refresh_token="old-r", client_id="cid")
    asyncio.run(backend._refresh())

    assert "openid" in posts[0]["scope"].split(), "the refresh asks for the sign-in's identity"
    assert backend.address == "me@msn.com"
    assert vault["email.microsoft.oauth_address"] == "me@msn.com"
    assert status_summary()["microsoft_address"] == "me@msn.com"
    assert resolve_provider(account="me@msn.com") == "microsoft"
    row = next(r for r in accounts.accounts_overview() if r["name"] == "microsoft")
    assert row["address"] == "me@msn.com"


def test_a_grant_without_openid_still_refreshes(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend

    _ms_mail_without_address(vault)
    posts: list[dict] = []
    _use(monkeypatch, _token_endpoint([
        (400, {"error": "invalid_scope", "error_description": "AADSTS70011: openid is not valid"}),
        (200, {"access_token": "fresh-a", "refresh_token": "fresh-r"}),
    ], posts=posts))
    asyncio.run(MicrosoftGraphBackend(access_token="old-a", refresh_token="old-r", client_id="cid")._refresh())

    assert len(posts) == 2 and "openid" not in posts[1]["scope"].split()
    assert posts[1]["refresh_token"] == "old-r", "the refused token is tried again"
    assert vault["email.microsoft.refresh_token"] == "fresh-r"
    assert "email.microsoft.oauth_address" not in vault


def test_a_refresh_refused_otherwise_is_not_retried(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager.backends.microsoft_graph import MicrosoftGraphBackend

    _ms_mail_without_address(vault)
    posts: list[dict] = []
    _use(monkeypatch, _token_endpoint([(401, {"error": "invalid_client"})], posts=posts))
    with pytest.raises(RuntimeError, match="Token refresh failed: 401"):
        asyncio.run(MicrosoftGraphBackend(access_token="old-a", refresh_token="old-r", client_id="cid")._refresh())
    assert len(posts) == 1
    assert vault["email.microsoft.refresh_token"] == "old-r"
