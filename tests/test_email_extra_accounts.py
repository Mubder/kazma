"""More mail accounts than one Gmail and one Microsoft (2026-09-29).

The owner asked to use three Gmail accounts and more than one Outlook
account. Kazma had a hidden, .env-only version (``EMAIL_ACCOUNTS``) with no
sign-in, and it was dangerous: refreshing a second Google or Microsoft
account wrote its tokens over the MAIN account's, so "my inbox" then read the
other mailbox. ``email_manager.accounts`` makes extra accounts first-class:

- added in Settings (a sign-in per account, or a password that is tried
  first), kept under a short name, removable;
- every token write names its account -- tested here per backend, mail and
  calendar, with the main account's keys watched;
- chat names an account by that name or by its address, for mail AND its
  calendar; ``email_accounts`` lists them.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
SKILLS = REPO / "kazma-skills" / "kazma_skills" / "native"
GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.modify https://www.googleapis.com/auth/calendar"
)
MS_SCOPES = "Mail.ReadWrite Mail.Send Calendars.ReadWrite offline_access openid"
_ENV = (
    "EMAIL_ACCOUNTS", "EMAIL_GMAIL_ACCESS_TOKEN", "EMAIL_GMAIL_REFRESH_TOKEN",
    "EMAIL_GMAIL_ADDRESS", "EMAIL_GMAIL_AUTH", "EMAIL_GMAIL_SCOPES", "EMAIL_GMAIL_APP_PASSWORD",
    "EMAIL_MS_ACCESS_TOKEN", "EMAIL_MS_REFRESH_TOKEN", "EMAIL_MS_AUTH", "EMAIL_MS_ADDRESS",
    "EMAIL_MS_PASSWORD", "EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_PROTOCOL",
    "GOOGLE_CALENDAR_TOKEN", "GOOGLE_CALENDAR_REFRESH", "GOOGLE_OAUTH_TOKEN",
    "MS_CALENDAR_TOKEN", "MS_CALENDAR_REFRESH", "MS_GRAPH_TOKEN", "EMAIL_DEFAULT_PROVIDER",
)


@pytest.fixture
def vault(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """One dict behind every vault reader and writer mail and calendar use;
    no mail setting in the environment. Settings (the account list) is the
    per-test settings store the suite's conftest gives every test."""
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


def _main_gmail(vault: dict[str, str]) -> None:
    vault.update({
        "email.gmail.access_token": "main-access", "email.gmail.refresh_token": "main-refresh",
        "email.gmail.address": "main@gmail.com", "email.gmail.auth": "oauth",
        "email.gmail.scopes": GMAIL_SCOPES,
        "calendar.google.access_token": "main-access", "calendar.google.refresh_token": "main-refresh",
    })


def _main_microsoft(vault: dict[str, str]) -> None:
    vault.update({
        "email.microsoft.access_token": "ms-main-access", "email.microsoft.refresh_token": "ms-main-refresh",
        "email.microsoft.auth": "oauth", "email.microsoft.oauth_address": "main@outlook.com",
        "email.microsoft.scopes": MS_SCOPES, "email.microsoft.client_id": "cid",
        "calendar.microsoft.access_token": "ms-main-access",
        "calendar.microsoft.refresh_token": "ms-main-refresh",
    })


def _main_keys(vault: dict[str, str]) -> dict[str, str]:
    return {k: v for k, v in vault.items() if not k.startswith("email.account.")}


# ── the list ────────────────────────────────────────────────────────────


def test_names_an_account_can_and_cannot_take(vault) -> None:
    from kazma_skills.native.email_manager import accounts

    assert accounts.alias_problem("work", kind="gmail") is None
    assert accounts.normalize_alias(" My Work_Box ") == "my-work-box"
    assert "letters" in accounts.alias_problem("", kind="gmail")
    assert "main account" in accounts.alias_problem("gmail", kind="gmail")
    assert "main account" in accounts.alias_problem("outlook", kind="microsoft")
    assert "Unknown account type" in accounts.alias_problem("work", kind="yahoo")


def test_a_password_account_is_kept_listed_and_removed(vault) -> None:
    from kazma_skills.native.email_manager import accounts, credentials

    row = accounts.add_password_account(
        "old-box", "imap", "me@example.org", "s3cret", imap_host="imap.example.org"
    )
    assert row["auth"] == "password" and row["imap_host"] == "imap.example.org"
    assert vault["email.account.old-box.password"] == "s3cret"
    assert "old-box" in credentials.list_account_aliases()
    cfg = credentials.account_config("old-box")
    assert cfg["type"] == "imap" and cfg["password"] == "s3cret" and cfg["imap_host"] == "imap.example.org"
    # A second account cannot take the same name as another kind.
    assert "already a imap account" in accounts.alias_problem("old-box", kind="gmail")

    result = accounts.remove_account("old-box")
    assert result["ok"] is True
    assert "old-box" not in credentials.list_account_aliases()
    assert not any(k.startswith("email.account.old-box.") for k in vault)
    assert accounts.remove_account("old-box")["ok"] is False


def test_an_address_already_connected_is_refused(vault) -> None:
    from kazma_skills.native.email_manager import accounts

    _main_gmail(vault)
    with pytest.raises(ValueError, match="main gmail account"):
        accounts.upsert_oauth_account("again", "gmail", "MAIN@gmail.com", "a", "r", GMAIL_SCOPES)
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "a", "r", GMAIL_SCOPES)
    with pytest.raises(ValueError, match="already connected as work"):
        accounts.add_password_account("twin", "gmail", "work@gmail.com", "app-pass")
    # Signing the same account in again under its own name is fine.
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "a2", "r2", GMAIL_SCOPES)
    assert vault["email.account.work.access_token"] == "a2"


def test_an_env_account_is_listed_and_not_removable(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import accounts

    monkeypatch.setenv("EMAIL_ACCOUNTS", "legacy")
    monkeypatch.setenv("EMAIL_ACCOUNT_LEGACY_TYPE", "imap")
    monkeypatch.setenv("EMAIL_ACCOUNT_LEGACY_ADDRESS", "l@example.org")
    row = next(r for r in accounts.accounts_overview() if r["name"] == "legacy")
    assert row["source"] == "env" and row["removable"] is False
    assert "set in .env" in accounts.remove_account("legacy")["error"]
    assert ".env" in accounts.alias_problem("legacy", kind="imap")


# ── naming an account in chat ───────────────────────────────────────────


def test_an_account_is_named_by_its_name_or_its_address(vault) -> None:
    from kazma_skills.native.email_manager import accounts
    from kazma_skills.native.email_manager.router import EmailNotConnectedError, resolve_provider

    _main_gmail(vault)
    _main_microsoft(vault)
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "a", "r", GMAIL_SCOPES)
    assert resolve_provider(account="work") == "account:work"
    assert resolve_provider(account="Work@Gmail.com") == "account:work"
    assert resolve_provider(account="main@gmail.com") == "gmail"
    assert resolve_provider(account="main@outlook.com") == "microsoft"
    assert resolve_provider(account="outlook") == "microsoft"
    with pytest.raises(EmailNotConnectedError) as exc:
        resolve_provider(account="nobody@example.com")
    assert "work (work@gmail.com)" in str(exc.value), "the refusal names what is connected"


def test_an_extra_account_gets_its_own_mail_backend(vault) -> None:
    from kazma_skills.native.email_manager import accounts
    from kazma_skills.native.email_manager.router import get_backend

    _main_gmail(vault)
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "w-a", "w-r", GMAIL_SCOPES)
    accounts.upsert_oauth_account("team", "microsoft", "team@outlook.com", "t-a", "t-r", MS_SCOPES)
    work = get_backend(account="work")
    assert work.name == "gmail_oauth:work" and work.access_token == "w-a" and work.account_alias == "work"
    team = get_backend(account="team@outlook.com")
    assert team.account_alias == "team" and team.access_token == "t-a"
    main = get_backend("gmail")
    assert main.name != work.name


def test_an_extra_account_brings_its_calendar(vault) -> None:
    from kazma_skills.native.calendar.router import CalendarNotConnectedError, get_backend
    from kazma_skills.native.email_manager import accounts

    _main_gmail(vault)
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "w-a", "w-r", GMAIL_SCOPES)
    accounts.upsert_oauth_account("mailonly", "gmail", "m@gmail.com", "m-a", "m-r",
                                  "https://www.googleapis.com/auth/gmail.modify")
    accounts.upsert_oauth_account("team", "microsoft", "team@outlook.com", "t-a", "t-r", MS_SCOPES)
    accounts.add_password_account("box", "imap", "b@example.org", "pw", imap_host="imap.example.org")

    work = get_backend(account="work")
    assert work.name == "google:work" and work._token == "w-a" and work._alias == "work"
    team = get_backend(account="team")
    assert team.name == "outlook:team" and team._token == "t-a"
    assert get_backend(account="main@gmail.com").name == "google", "the main calendar by its address"
    with pytest.raises(CalendarNotConnectedError, match="did not grant calendar"):
        get_backend(account="mailonly")
    with pytest.raises(CalendarNotConnectedError, match="no calendar"):
        get_backend(account="box")


# ── every token write names its account (the bug) ───────────────────────


class _Resp:
    def __init__(self, payload: dict, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.content = json.dumps(payload).encode()
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _fake_client(payload: dict):
    class Client:
        def __init__(self, *a, **k) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a) -> None:
            return None

        async def post(self, *a, **k):
            return _Resp(payload)

        async def get(self, *a, **k):
            return _Resp(payload)

    return Client


def _refreshers():
    """Every backend that refreshes a token, built for an extra account named
    ``work``, with the module whose httpx it calls and how to refresh it."""
    from kazma_skills.native.calendar.backends import google_calendar, outlook_calendar
    from kazma_skills.native.calendar import oauth_google
    from kazma_skills.native.email_manager import oauth_gmail
    from kazma_skills.native.email_manager.backends import gmail_api, microsoft_graph

    return {
        "gmail": (
            lambda: gmail_api.GmailApiBackend(
                access_token="old", refresh_token="w-r", client_id="c", client_secret="s",
                account_alias="work"),
            oauth_gmail, "_refresh"),
        "graph": (
            lambda: microsoft_graph.MicrosoftGraphBackend(
                access_token="old", refresh_token="w-r", client_id="c", account_alias="work"),
            microsoft_graph, "_refresh"),
        "google_calendar": (
            lambda: google_calendar.GoogleCalendarBackend("old", "w-r", account_alias="work"),
            oauth_google, "_do_refresh"),
        "outlook_calendar": (
            lambda: outlook_calendar.OutlookCalendarBackend(
                "old", refresh_token="w-r", client_id="c", account_alias="work"),
            None, "_do_refresh"),
    }


@pytest.mark.parametrize("kind", ["gmail", "graph", "google_calendar", "outlook_calendar"])
def test_a_refresh_writes_only_its_own_account(vault, monkeypatch, kind) -> None:
    import httpx

    from kazma_skills.native.email_manager import accounts

    _main_gmail(vault)
    _main_microsoft(vault)
    provider = "gmail" if kind in ("gmail", "google_calendar") else "microsoft"
    accounts.upsert_oauth_account(
        "work", provider, "work@example.com", "old", "w-r",
        GMAIL_SCOPES if provider == "gmail" else MS_SCOPES,
    )
    before = _main_keys(vault)
    build, module, method = _refreshers()[kind]
    fake = _fake_client({"access_token": "new-access", "refresh_token": "new-refresh",
                         "scope": GMAIL_SCOPES})
    monkeypatch.setattr(httpx, "AsyncClient", fake)
    backend = build()
    asyncio.run(getattr(backend, method)())
    assert vault["email.account.work.access_token"] == "new-access", kind
    assert vault["email.account.work.refresh_token"] == "new-refresh", kind
    assert _main_keys(vault) == before, f"{kind}: the main accounts' tokens were written"


def test_negative_control_the_main_account_still_refreshes_its_own_keys(vault, monkeypatch) -> None:
    """The watch above can see a write: the main Gmail refresh writes the
    main keys (and nothing under an extra account)."""
    import httpx

    from kazma_skills.native.email_manager.backends.gmail_api import GmailApiBackend

    _main_gmail(vault)
    monkeypatch.setattr(httpx, "AsyncClient", _fake_client(
        {"access_token": "main-new", "refresh_token": "main-refresh-2", "scope": GMAIL_SCOPES}))
    asyncio.run(GmailApiBackend(access_token="x", refresh_token="main-refresh",
                                client_id="c", client_secret="s")._refresh())
    assert vault["email.gmail.access_token"] == "main-new"
    assert not any(k.startswith("email.account.") for k in vault)


def test_every_refreshing_backend_takes_an_account() -> None:
    """Gate: a backend class with a token refresh must accept the account it
    belongs to (and the test above must cover it) -- a new OAuth backend
    without it would refresh into the main account's keys."""
    import ast

    found: set[str] = set()
    for folder in (SKILLS / "email_manager" / "backends", SKILLS / "calendar" / "backends"):
        for path in folder.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in tree.body:
                if not isinstance(node, ast.ClassDef):
                    continue
                methods = {n.name: n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                if not ({"_refresh", "_do_refresh"} & set(methods)):
                    continue
                init = methods.get("__init__")
                args = [a.arg for a in (init.args.args + init.args.kwonlyargs)] if init else []
                assert "account_alias" in args, f"{path.name}:{node.name} refreshes without an account"
                found.add(node.name)
    assert found == {"GmailApiBackend", "MicrosoftGraphBackend", "GoogleCalendarBackend",
                     "OutlookCalendarBackend"}, found


# ── signing an extra account in ─────────────────────────────────────────


def test_a_google_sign_in_for_an_extra_account_keeps_its_own_tokens(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import oauth_common, oauth_gmail

    _main_gmail(vault)
    before = _main_keys(vault)
    monkeypatch.setattr(oauth_gmail, "_client_id", lambda: "cid.apps.googleusercontent.com")
    monkeypatch.setattr(oauth_gmail, "_client_secret", lambda: "GOCSPX-secret")

    started = oauth_gmail.start_gmail_oauth("https://kazma.test", account="Work")
    assert started["ok"] and "select_account" in started["authorize_url"]
    state = next(k for k, v in oauth_common._oauth_states.items() if v.get("account") == "work")

    monkeypatch.setattr(oauth_gmail.httpx, "AsyncClient", _fake_client(
        {"access_token": "w-a", "refresh_token": "w-r", "scope": GMAIL_SCOPES, "email": "work@gmail.com"}))

    async def probe(client, access):
        return {"ok": True, "email": "work@gmail.com"}

    monkeypatch.setattr(oauth_gmail, "_probe_gmail_api", probe)
    result = asyncio.run(oauth_gmail.finish_gmail_oauth("code", state))
    assert result["ok"] and result["account"] == "work" and result["calendar_ok"] is True
    assert vault["email.account.work.refresh_token"] == "w-r"
    assert _main_keys(vault) == before, "the main Gmail account was not touched"

    # The main account's own address as an extra account: refused, nothing kept.
    started = oauth_gmail.start_gmail_oauth("https://kazma.test", account="dup")
    state = next(k for k, v in oauth_common._oauth_states.items() if v.get("account") == "dup")
    monkeypatch.setattr(oauth_gmail.httpx, "AsyncClient", _fake_client(
        {"access_token": "d-a", "refresh_token": "d-r", "scope": GMAIL_SCOPES, "email": "main@gmail.com"}))

    async def probe_main(client, access):
        return {"ok": True, "email": "main@gmail.com"}

    monkeypatch.setattr(oauth_gmail, "_probe_gmail_api", probe_main)
    refused = asyncio.run(oauth_gmail.finish_gmail_oauth("code", state))
    assert refused["ok"] is False and "already connected" in refused["error"]
    assert "email.account.dup.refresh_token" not in vault


def test_a_bad_name_is_refused_before_the_sign_in(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import oauth_gmail, oauth_ms_browser

    monkeypatch.setattr(oauth_gmail, "_client_id", lambda: "cid.apps.googleusercontent.com")
    monkeypatch.setattr(oauth_gmail, "_client_secret", lambda: "GOCSPX-secret")
    monkeypatch.setattr(oauth_ms_browser, "_client_id", lambda: "cid")
    assert oauth_gmail.start_gmail_oauth("https://k.test", account="gmail")["code"] == "bad_account_name"
    assert oauth_ms_browser.start_ms_browser_oauth("https://k.test", account="outlook")["code"] == "bad_account_name"


def test_a_microsoft_sign_in_for_an_extra_account_keeps_its_own_tokens(vault, monkeypatch) -> None:
    import base64

    from kazma_skills.native.email_manager import oauth_common, oauth_ms_browser

    _main_microsoft(vault)
    before = _main_keys(vault)
    monkeypatch.setattr(oauth_ms_browser, "_client_id", lambda: "cid")
    monkeypatch.setattr(oauth_ms_browser, "_client_secret", lambda: "")
    started = oauth_ms_browser.start_ms_browser_oauth("https://kazma.test", account="team")
    assert started["ok"] and "prompt=select_account" in started["authorize_url"]
    state = next(k for k, v in oauth_common._oauth_states.items() if v.get("account") == "team")

    def seg(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    id_token = f"{seg({'alg': 'RS256'})}.{seg({'preferred_username': 'team@outlook.com'})}.sig"
    monkeypatch.setattr(oauth_ms_browser.httpx, "AsyncClient", _fake_client(
        {"access_token": "t-a", "refresh_token": "t-r", "scope": MS_SCOPES, "id_token": id_token}))
    result = asyncio.run(oauth_ms_browser.finish_ms_browser_oauth("code", state))
    assert result["ok"] and result["account"] == "team" and result["email"] == "team@outlook.com"
    assert vault["email.account.team.access_token"] == "t-a"
    assert _main_keys(vault) == before


# ── the routes ──────────────────────────────────────────────────────────


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from kazma_ui import email_api

    app = FastAPI()
    app.include_router(email_api.router)
    app.include_router(email_api.protected_router)
    return TestClient(app, headers={"Origin": "http://testserver", "X-Requested-With": "XMLHttpRequest"})


def test_adding_a_password_account_tries_the_login_first(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import router

    class Works:
        async def list_messages(self, query):
            return []

    class Refused:
        async def list_messages(self, query):
            raise RuntimeError("AUTHENTICATIONFAILED Invalid credentials")

    body = {"alias": "Side Box", "type": "gmail", "address": "side@gmail.com", "password": "abcd efgh ijkl mnop"}
    c = _client()
    monkeypatch.setattr(router, "backend_for_account", lambda alias, cfg: Refused())
    r = c.post("/api/email/accounts", json=body)
    error = r.json()["error"]
    assert r.status_code == 400 and "imap.gmail.com refused the address and password" in error
    assert "app password" in error and "AUTHENTICATIONFAILED" in error, "the server's own words stay"
    assert "email.account.side-box.password" not in vault, "a login that fails is never kept"

    seen = {}

    def works(alias, cfg):
        seen.update(cfg)
        return Works()

    monkeypatch.setattr(router, "backend_for_account", works)
    r = c.post("/api/email/accounts", json=body)
    assert r.status_code == 200 and r.json()["account"]["alias"] == "side-box"
    assert seen["password"] == "abcdefghijklmnop", "an app password's spaces are dropped, as Google shows it"
    assert vault["email.account.side-box.password"] == "abcdefghijklmnop"
    listed = c.get("/api/email/accounts").json()["accounts"]
    assert any(a["alias"] == "side-box" and a["source"] == "settings" and a["removable"] for a in listed)

    r = c.post("/api/email/accounts/side-box/remove")
    assert r.status_code == 200
    assert not any(a["alias"] == "side-box" for a in c.get("/api/email/accounts").json()["accounts"])


def _failures():
    import socket
    import ssl

    return [
        (socket.gaierror(11001, "getaddrinfo failed"),
         "The mail server imap.example.invalid could not be found. Check its name."),
        (ConnectionRefusedError(10061, "No connection could be made"),
         "imap.example.invalid refused the connection"),
        (ssl.SSLError(1, "[SSL: WRONG_VERSION_NUMBER] wrong version number"),
         "The secure connection to imap.example.invalid failed"),
        (RuntimeError("Cannot select folder INBOX"), "The login did not work: Cannot select folder INBOX"),
    ]


@pytest.mark.parametrize("raised, says", _failures(), ids=["not-found", "refused", "tls", "other"])
def test_a_failed_login_says_what_to_fix(vault, monkeypatch, raised, says) -> None:
    """A mistyped server name came back as "[Errno 11001] getaddrinfo
    failed" on the live install (2026-09-29)."""
    from kazma_skills.native.email_manager import router

    class Fails:
        async def list_messages(self, query):
            raise raised

    monkeypatch.setattr(router, "backend_for_account", lambda alias, cfg: Fails())
    r = _client().post("/api/email/accounts", json={
        "alias": "box", "type": "imap", "address": "b@example.invalid", "password": "not-a-password",
        "imap_host": "imap.example.invalid",
    })
    assert r.status_code == 400 and says in r.json()["error"], r.json()
    assert "email.account.box.password" not in vault


def test_the_sign_in_routes_take_the_account(vault, monkeypatch) -> None:
    from kazma_skills.native.email_manager import oauth_gmail

    monkeypatch.setattr(oauth_gmail, "_client_id", lambda: "cid.apps.googleusercontent.com")
    monkeypatch.setattr(oauth_gmail, "_client_secret", lambda: "GOCSPX-secret")
    c = _client()
    ok = c.get("/api/email/oauth/gmail/start.json", params={"account": "work"})
    assert ok.status_code == 200 and ok.json()["authorize_url"]
    bad = c.get("/api/email/oauth/gmail/start.json", params={"account": "sandbox"})
    assert bad.status_code == 400


# ── the agent's view ────────────────────────────────────────────────────


def test_the_agent_can_list_the_accounts(vault) -> None:
    from kazma_skills.native.email_manager import accounts
    from kazma_skills.native.email_manager.tools import email_accounts

    _main_gmail(vault)
    accounts.upsert_oauth_account("work", "gmail", "work@gmail.com", "a", "r", GMAIL_SCOPES)
    out = asyncio.run(email_accounts())
    assert "- gmail: main Gmail account, main@gmail.com, signed in" in out
    assert "- work: Gmail account, work@gmail.com, signed in, calendar yes" in out
    assert "account=" in out


def test_every_mail_and_calendar_tool_takes_an_account() -> None:
    from kazma_skills.native.calendar import tools as cal_tools
    from kazma_skills.native.email_manager import tools as mail_tools

    for mod, prefix in ((mail_tools, "email_"), (cal_tools, "")):
        for name, fn in vars(mod).items():
            if not inspect.iscoroutinefunction(fn) or name.startswith("_") or not name.startswith(prefix):
                continue
            if "provider" in inspect.signature(fn).parameters:
                assert "account" in inspect.signature(fn).parameters, name


def test_the_settings_card_offers_every_way_in() -> None:
    html = (REPO / "kazma-ui/kazma_ui/templates/settings.html").read_text(encoding="utf-8")
    card = html[html.index("<!-- Other accounts -->"):]
    card = card[: card.index("{{ t('settings.email_docs_hint') }}")]
    for call in ("addGoogleAccount()", "addMicrosoftAccount()", "addMicrosoftAccountWithCode()",
                 "addPasswordAccount()", "reconnectEmailAccount(a)", "removeEmailAccount(a)"):
        assert call in card, call
    js = (REPO / "kazma-ui/kazma_ui/static/js/settings_integrations.js").read_text(encoding="utf-8")
    assert "'?account=' + encodeURIComponent(alias)" in js
    assert "'/api/email/accounts/' + encodeURIComponent(a.alias) + '/remove'" in js
