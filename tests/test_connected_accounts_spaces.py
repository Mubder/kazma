"""Spaces S1-17 (part a): a mailbox and a calendar belong to a space.

Guards act only where Spaces is enforced; an install with one account behaves
as before. Every secret and environment value of a connected mailbox or
calendar goes through ``email_manager.credentials`` (``vault_retrieve``,
``vault_store``, ``vault_delete``, ``cred``, ``account_env``,
``set_account_env``), which keeps the owner's space on today's vault names and
the process environment, and every other space on its own names
(``spaces.connected_accounts``). The install's app registration is the
install's in every space. A sign-in finishes in the space it started in; the
background connector check goes space by space.

Each rule is taken out as a control: the install-scoped names as they were (a
space's connect overwrote the owner's mailbox), the environment as it was (a
space's IMAP host became everyone's), the attempt with no space.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import Any

import pytest
from kazma_core.security import vault as vault_mod
from kazma_core.spaces import identity, scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID, NoSpaceBound

from tests._space_matrix import Matrix

REPO = Path(__file__).resolve().parents[1]

#: The environment the mail and calendar code read, cleared for each test.
_MAIL_ENV = ("EMAIL_ADDRESS", "EMAIL_PASSWORD", "EMAIL_PROTOCOL", "EMAIL_IMAP_HOST", "EMAIL_IMAP_PORT",
             "EMAIL_POP_HOST", "EMAIL_POP_PORT", "EMAIL_SMTP_HOST", "EMAIL_SMTP_PORT", "EMAIL_GMAIL_ADDRESS",
             "EMAIL_GMAIL_APP_PASSWORD", "EMAIL_GMAIL_AUTH", "EMAIL_GMAIL_ACCESS_TOKEN",
             "EMAIL_GMAIL_REFRESH_TOKEN", "EMAIL_GMAIL_SCOPES", "EMAIL_GMAIL_CLIENT_ID",
             "EMAIL_GMAIL_CLIENT_SECRET", "EMAIL_ACCOUNTS", "EMAIL_DEFAULT_PROVIDER", "GOOGLE_CALENDAR_TOKEN",
             "GOOGLE_CALENDAR_REFRESH", "GOOGLE_CALENDAR_ADDRESS", "GOOGLE_CALENDAR_SCOPES")


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """A real vault of the test's own, behind every reader."""
    monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
    for name in _MAIL_ENV:
        monkeypatch.delenv(name, raising=False)
    v = vault_mod.SecretVault(db_path=str(tmp_path / "vault.db"))
    monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
    yield v
    v.close()
    for name in _MAIL_ENV:  # what a connect of the owner's space set
        os.environ.pop(name, None)


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


def _connect(address: str, host: str) -> dict[str, Any]:
    from kazma_skills.native.email_manager.protocol_connect import connect_protocol

    return connect_protocol(provider="generic", protocol="imap", address=address, password="secret-pw",
                            imap_host=host)


def _seen() -> dict[str, Any]:
    from kazma_skills.native.email_manager import credentials

    s = credentials.status_summary()
    return {"imap": s["imap_configured"], "address": credentials.account_env("EMAIL_ADDRESS")
            or credentials.vault_retrieve("email.generic.address"),
            "host": credentials.account_env("EMAIL_IMAP_HOST")}


def _owner() -> Any:
    return scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)


# ── two spaces, two mailboxes ────────────────────────────────────────────


def test_two_spaces_two_mailboxes(vault, matrix, enforced) -> None:
    with _owner():
        assert _connect("owner@example.org", "imap.owner.example")["ok"]
    with matrix.as_a():
        assert _connect("a@example.org", "imap.a.example")["ok"]
    with matrix.as_b():
        assert _connect("b@example.org", "imap.b.example")["ok"]
    with _owner():
        assert _seen() == {"imap": True, "address": "owner@example.org", "host": "imap.owner.example"}
    with matrix.as_a():
        assert _seen() == {"imap": True, "address": "a@example.org", "host": "imap.a.example"}
    with matrix.as_b():
        assert _seen() == {"imap": True, "address": "b@example.org", "host": "imap.b.example"}
    # Another space never touched the process environment: it is the owner's.
    assert os.environ.get("EMAIL_IMAP_HOST") == "imap.owner.example"
    assert os.environ.get("EMAIL_ADDRESS") == "owner@example.org"


def test_a_space_with_nothing_connected_sees_nothing(vault, matrix, enforced) -> None:
    with _owner():
        _connect("owner@example.org", "imap.owner.example")
    with matrix.as_a():
        assert _seen() == {"imap": False, "address": "", "host": ""}


def test_control_the_install_scoped_names_as_they_were(vault, matrix, enforced, monkeypatch) -> None:
    """The names and environment as they were: A's connect overwrote the
    owner's mailbox, and B read A's."""
    import kazma_core.spaces.connected_accounts as connected_accounts

    monkeypatch.setattr(connected_accounts, "connected_space", lambda: OWNER_SPACE_ID)
    with _owner():
        _connect("owner@example.org", "imap.owner.example")
    with matrix.as_a():
        _connect("a@example.org", "imap.a.example")
    with _owner():
        assert _seen()["address"] == "a@example.org"
    with matrix.as_b():
        assert _seen()["address"] == "a@example.org"


def test_a_refresh_writes_only_its_own(vault, matrix, enforced) -> None:
    from kazma_skills.native.email_manager import credentials
    from kazma_skills.native.email_manager.oauth_gmail import persist_gmail_tokens

    with _owner():
        persist_gmail_tokens("owner-access", "owner-refresh", "owner@gmail.example")
    with matrix.as_b():
        persist_gmail_tokens("b-access", "b-refresh", "b@gmail.example")
    with matrix.as_a():
        persist_gmail_tokens("a-access-2", "a-refresh-2")
    with _owner():
        assert credentials.cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token") == "owner-refresh"
    with matrix.as_b():
        assert credentials.cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token") == "b-refresh"
        assert credentials.cred("EMAIL_GMAIL_ADDRESS", "email.gmail.address") == "b@gmail.example"
    with matrix.as_a():
        assert credentials.cred("EMAIL_GMAIL_REFRESH_TOKEN", "email.gmail.refresh_token") == "a-refresh-2"


def test_the_app_registration_is_the_installs_in_every_space(vault, matrix, enforced) -> None:
    from kazma_skills.native.email_manager import credentials

    with _owner():
        credentials.vault_store("email.gmail.client_id", "install-client.apps.example")
    with matrix.as_a():
        assert credentials.cred("EMAIL_GMAIL_CLIENT_ID", "email.gmail.client_id") == "install-client.apps.example"


def test_the_extra_accounts_list_is_the_spaces(vault, matrix, enforced) -> None:
    from kazma_skills.native.email_manager import accounts

    with _owner():
        accounts._upsert({"alias": "home", "type": "imap", "address": "owner-home@example.org"})
    with matrix.as_a():
        accounts._upsert({"alias": "work", "type": "imap", "address": "a-work@example.org"})
        assert [a["alias"] for a in accounts.stored_accounts()] == ["work"]
    with matrix.as_b():
        assert accounts.stored_accounts() == []
    with _owner():
        assert [a["alias"] for a in accounts.stored_accounts()] == ["home"]


def test_with_no_space_bound_nothing_is_read_once_enforced(vault, enforced) -> None:
    from kazma_skills.native.email_manager import accounts, credentials

    with pytest.raises(NoSpaceBound):
        credentials.vault_retrieve("email.gmail.refresh_token")
    with pytest.raises(NoSpaceBound):
        credentials.account_env("EMAIL_IMAP_HOST")
    with pytest.raises(NoSpaceBound):
        accounts.stored_accounts()


def test_one_account_behaves_as_before(vault) -> None:
    """Not enforced: today's names and the process environment."""
    from kazma_skills.native.email_manager import credentials

    assert not scope.spaces_enforced()
    _connect("owner@example.org", "imap.owner.example")
    assert os.environ.get("EMAIL_IMAP_HOST") == "imap.owner.example"
    assert vault.retrieve("email.generic.address") == "owner@example.org"
    assert credentials.vault_retrieve("email.generic.address") == "owner@example.org"


# ── a sign-in lands in the space it started in ───────────────────────────


def _taken(monkeypatch) -> dict[str, Any]:
    from kazma_core.security import oauth_attempts

    state = oauth_attempts.begin("gmail", redirect_uri="https://kazma.example/cb")
    return oauth_attempts.take(state, bound_state=state, providers=("gmail",))


def test_a_sign_in_finishes_in_the_space_it_started_in(vault, matrix, enforced, monkeypatch) -> None:
    from kazma_ui.oauth_sign_in import attempt_scope

    with matrix.as_a():
        attempt = _taken(monkeypatch)
    assert (attempt["space"], attempt["asker"]) == (Matrix.SPACE_A, matrix.account_a)
    with attempt_scope(attempt):
        bound = scope.current_scope()
        assert (bound.space_id, bound.asker) == (Matrix.SPACE_A, matrix.account_a)


def test_a_sign_in_of_no_space_or_a_left_member_is_refused(vault, matrix, enforced, monkeypatch) -> None:
    from kazma_core.security.oauth_attempts import AttemptRefused

    from kazma_ui.oauth_sign_in import attempt_scope

    with pytest.raises(AttemptRefused) as refused:
        attempt_scope({"provider": "gmail"})
    assert refused.value.reason == "no_space"
    identity.create_space("Shared", created_by=matrix.account_a, space_id="mail-shared")
    identity.add_member("mail-shared", matrix.account_b)
    with scope.bind_scope("mail-shared", matrix.account_b):
        attempt = _taken(monkeypatch)
    identity.remove_member("mail-shared", matrix.account_b)
    with pytest.raises(AttemptRefused):
        attempt_scope(attempt)


def test_not_enforced_a_callback_binds_nothing(vault) -> None:
    from kazma_ui.oauth_sign_in import attempt_scope

    with attempt_scope({"provider": "gmail"}):
        assert scope.current_scope() is None


async def test_a_callback_keeps_the_tokens_in_its_space(vault, matrix, enforced, monkeypatch) -> None:
    """The Gmail callback route, through the real router: A's sign-in, its
    tokens exchanged (stood in for), lands in A's space and nowhere else."""
    assert await _callback_kept(matrix, monkeypatch) == {"a": "a-refresh", "owner": "", "b": ""}


async def test_control_a_callback_that_binds_nothing_keeps_nothing_in_the_space(vault, matrix, enforced,
                                                                               monkeypatch) -> None:
    """The callback as it was (nothing bound; the provider's redirect names no
    space): A's tokens never reach A's space."""
    import contextlib

    import kazma_ui.oauth_sign_in as oauth_sign_in

    monkeypatch.setattr(oauth_sign_in, "attempt_scope", lambda attempt: contextlib.nullcontext())
    assert (await _callback_kept(matrix, monkeypatch))["a"] == ""


async def _callback_kept(matrix: Matrix, monkeypatch) -> dict[str, str]:
    """A's Gmail sign-in through the callback route; whose refresh token
    each space then holds."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from kazma_core.security import oauth_attempts
    from kazma_skills.native.email_manager import credentials, oauth_gmail

    from kazma_ui import email_api
    from kazma_ui.oauth_sign_in import binding_cookie_name

    async def finish(code: str, attempt: dict[str, Any]) -> dict[str, Any]:
        oauth_gmail.persist_gmail_tokens("a-access", "a-refresh", "a@gmail.example")
        return {"ok": True, "email": "a@gmail.example"}

    monkeypatch.setattr(oauth_gmail, "finish_gmail_oauth", finish)
    with matrix.as_a():
        state = oauth_attempts.begin("gmail", redirect_uri="https://kazma.example/api/email/oauth/gmail/callback")
    app = FastAPI()
    app.include_router(email_api.router)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://kazma.example") as client:
        client.cookies.set(binding_cookie_name(state), state)
        await client.get("/api/email/oauth/gmail/callback", params={"code": "c", "state": state})
    kept: dict[str, str] = {}
    for who, bound in (("a", matrix.as_a), ("owner", _owner), ("b", matrix.as_b)):
        with bound():
            kept[who] = credentials.vault_retrieve("email.gmail.refresh_token")
    return kept


# ── a sign-in whose person cannot be read is a refusal ───────────────────


async def _callback_while_identity_fails(matrix: Matrix, monkeypatch) -> Any:
    """A's Gmail sign-in comes back while the identity store cannot be read
    (the database away): the callback's answer."""
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from kazma_core.db import availability
    from kazma_core.security import oauth_attempts

    from kazma_ui import email_api
    from kazma_ui.oauth_sign_in import binding_cookie_name

    with matrix.as_a():
        state = oauth_attempts.begin("gmail", redirect_uri="https://kazma.example/api/email/oauth/gmail/callback")

    def unreadable(*args: Any, **kwargs: Any) -> bool:
        raise identity.IdentityUnavailable("the identity store could not be read: PoolTimeout")

    monkeypatch.setattr(identity, "is_member", unreadable)
    monkeypatch.setattr(availability, "note_failure", lambda kind, exc=None, **kwargs: True)
    app = FastAPI()
    app.include_router(email_api.router)
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="https://kazma.example") as client:
        client.cookies.set(binding_cookie_name(state), state)
        return await client.get("/api/email/oauth/gmail/callback", params={"code": "c", "state": state})


async def test_a_sign_in_whose_person_cannot_be_read_is_refused(vault, matrix, enforced, monkeypatch) -> None:
    answer = await _callback_while_identity_fails(matrix, monkeypatch)
    assert answer.status_code == 302, answer.text[:300]
    assert answer.headers["location"].endswith("email_oauth=error&reason=identity_unavailable")


async def test_control_the_scope_without_the_refusal_answers_500(vault, matrix, enforced, monkeypatch) -> None:
    """The scope as before: the identity store's error reached the route."""
    import contextlib

    import kazma_ui.oauth_sign_in as oauth_sign_in

    def as_before(attempt: dict[str, Any]) -> Any:
        identity.is_member(str(attempt.get("asker")), str(attempt.get("space")))
        return contextlib.nullcontext()

    monkeypatch.setattr(oauth_sign_in, "attempt_scope", as_before)
    assert (await _callback_while_identity_fails(matrix, monkeypatch)).status_code == 500


# ── the practice mailbox and calendar are each space's ───────────────────


async def _sent_folders(matrix: Matrix, db: Path) -> dict[str, list[str]]:
    """A sends from its practice mailbox; then each space reads its Sent
    folder: the subjects each one sees."""
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend
    from kazma_skills.native.email_manager.models import ListQuery, SendRequest

    with matrix.as_a():
        box = SandboxBackend(db_path=db)
    await box.send(SendRequest(to=["x@example.org"], subject="sent by A", body="a"))
    seen: dict[str, list[str]] = {}
    for who, bound in (("a", matrix.as_a), ("b", matrix.as_b)):
        with bound():
            box = SandboxBackend(db_path=db)
        seen[who] = [m.subject for m in await box.list_messages(ListQuery(folder="Sent"))]
    return seen


async def test_each_space_has_its_own_practice_mailbox(matrix, enforced, tmp_path) -> None:
    seen = await _sent_folders(matrix, tmp_path / "sandbox_emails.db")
    assert "sent by A" in seen["a"] and "sent by A" not in seen["b"]


async def test_control_one_practice_mailbox_for_every_space(matrix, enforced, tmp_path, monkeypatch) -> None:
    """The mailbox as it was: one for every space -- B reads what A sent."""
    from kazma_skills.native.email_manager.backends import sandbox

    monkeypatch.setattr(sandbox, "_mailbox_space", lambda: OWNER_SPACE_ID)
    seen = await _sent_folders(matrix, tmp_path / "sandbox_emails.db")
    assert "sent by A" in seen["b"]


def test_no_practice_mailbox_is_opened_for_no_one(enforced, tmp_path) -> None:
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

    with pytest.raises(NoSpaceBound):
        SandboxBackend(db_path=tmp_path / "sandbox_emails.db")


async def test_a_mailbox_from_before_spaces_is_the_owners(matrix, enforced, tmp_path) -> None:
    """A mailbox file written before spaces (``id`` its key, no space) is
    kept, every message the owner's; another space gets its own practice
    mailbox beside it, the same seed ids and all."""
    import sqlite3

    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend
    from kazma_skills.native.email_manager.models import ListQuery

    db = tmp_path / "sandbox_emails.db"
    old = sqlite3.connect(db)
    old.executescript("""
        CREATE TABLE messages (id TEXT PRIMARY KEY, subject TEXT NOT NULL DEFAULT '',
            from_addr TEXT NOT NULL DEFAULT '', to_addrs TEXT NOT NULL DEFAULT '[]',
            cc_addrs TEXT NOT NULL DEFAULT '[]', date TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            snippet TEXT NOT NULL DEFAULT '', unread INTEGER NOT NULL DEFAULT 0,
            starred INTEGER NOT NULL DEFAULT 0, labels TEXT NOT NULL DEFAULT '[]',
            folder TEXT NOT NULL DEFAULT 'INBOX', thread_id TEXT NOT NULL DEFAULT '');
        INSERT INTO messages (id, subject, date) VALUES ('old-1', 'kept from before', '2026-01-01');
    """)
    old.commit()
    old.close()
    with _owner():
        owners = SandboxBackend(db_path=db)
    with matrix.as_b():
        theirs = SandboxBackend(db_path=db)
    owner_inbox = [m.subject for m in await owners.list_messages(ListQuery(folder="INBOX", limit=50))]
    their_inbox = [m.subject for m in await theirs.list_messages(ListQuery(folder="INBOX", limit=50))]
    assert owner_inbox == ["kept from before"]  # an owner's mailbox that had messages is not seeded
    assert "kept from before" not in their_inbox and their_inbox  # B's own, seeded
    seeded = {m.id for m in await theirs.list_messages(ListQuery(folder="INBOX", limit=50))}
    with _owner():
        assert SandboxBackend(db_path=db).space == OWNER_SPACE_ID  # opened again: no second rebuild
    check = sqlite3.connect(db)
    try:
        rows = check.execute("SELECT tenant_id, id FROM messages WHERE id = 'old-1'").fetchall()
        keyed = {row[1] for row in check.execute("SELECT * FROM pragma_table_info('messages') WHERE pk > 0")}
    finally:
        check.close()
    assert rows == [(OWNER_SPACE_ID, "old-1")] and keyed == {"tenant_id", "id"} and seeded


def test_one_account_reads_the_one_mailbox_as_before(tmp_path) -> None:
    """Not enforced: whatever tenant is bound, the owner's mailbox."""
    from kazma_core.tenant_context import tenant_scope
    from kazma_skills.native.email_manager.backends.sandbox import SandboxBackend

    with tenant_scope("someone-else"):
        assert SandboxBackend(db_path=tmp_path / "sandbox_emails.db").space == OWNER_SPACE_ID


async def _calendars(matrix: Matrix, monkeypatch) -> dict[str, list[str]]:
    """A adds an event to its practice calendar; what each space lists."""
    from kazma_skills.native.calendar import credentials as calendar_credentials
    from kazma_skills.native.calendar import router as calendar_router

    monkeypatch.setattr(calendar_credentials, "google_connected", lambda: False)
    monkeypatch.setattr(calendar_credentials, "microsoft_connected", lambda: False)
    monkeypatch.setattr(calendar_router, "_sandbox_instances", {})
    with matrix.as_a():
        calendar = calendar_router.get_backend("auto")
    await calendar.create_event("A's meeting", "2026-12-01T10:00:00+00:00", "2026-12-01T11:00:00+00:00")
    listed: dict[str, list[str]] = {}
    for who, bound in (("a", matrix.as_a), ("b", matrix.as_b)):
        with bound():
            calendar = calendar_router.get_backend("auto")
        events = await calendar.list_events("2026-12-01T00:00:00+00:00", "2026-12-02T00:00:00+00:00")
        listed[who] = [e["summary"] for e in events]
    return listed


async def test_each_space_has_its_own_practice_calendar(vault, matrix, enforced, monkeypatch) -> None:
    assert await _calendars(matrix, monkeypatch) == {"a": ["A's meeting"], "b": []}


async def test_control_one_practice_calendar_for_every_space(vault, matrix, enforced, monkeypatch) -> None:
    from kazma_core.spaces import connected_accounts

    monkeypatch.setattr(connected_accounts, "connected_space", lambda: OWNER_SPACE_ID)
    assert (await _calendars(matrix, monkeypatch))["b"] == ["A's meeting"]


# ── the background check goes space by space ─────────────────────────────


async def test_the_connector_check_goes_space_by_space(matrix, enforced, monkeypatch) -> None:
    from kazma_core.observability import connector_health

    ran: list[tuple[str, Any, bool]] = []

    def record(name: str):
        async def check(*, alert_on_findings: bool = True) -> Any:
            bound = scope.current_scope()
            ran.append((name, bound.space_id if bound else None, alert_on_findings))
            return connector_health.ConnectorStatus(name=name, ok=True)
        return check

    monkeypatch.setattr(connector_health, "check_google", record("google"))
    monkeypatch.setattr(connector_health, "check_google_calendar", record("google_calendar"))
    statuses = await connector_health.check_connectors()
    spaces = [space for _name, space, _alert in ran]
    assert spaces[:2] == [OWNER_SPACE_ID, OWNER_SPACE_ID]
    assert {Matrix.SPACE_A, Matrix.SPACE_B} <= set(spaces)
    assert all(alert == (space == OWNER_SPACE_ID) for _name, space, alert in ran), ran
    assert {s.space for s in statuses if s.space} >= {Matrix.SPACE_A, Matrix.SPACE_B}


async def test_not_enforced_the_check_is_the_owners_as_before(monkeypatch) -> None:
    from kazma_core.observability import connector_health

    ran: list[Any] = []

    async def check(*, alert_on_findings: bool = True) -> Any:
        ran.append((scope.current_scope(), alert_on_findings))
        return connector_health.ConnectorStatus(name="x", ok=True)

    monkeypatch.setattr(connector_health, "check_google", check)
    monkeypatch.setattr(connector_health, "check_google_calendar", check)
    statuses = await connector_health.check_connectors()
    assert ran == [(None, True), (None, True)] and all(not s.space for s in statuses)


# ── the source gate: every reader names a space ──────────────────────────

#: The mail and calendar code, held to ``email_manager.credentials``.
_HELD = (
    "kazma-skills/kazma_skills/native/email_manager",
    "kazma-skills/kazma_skills/native/calendar",
    "kazma-ui/kazma_ui/email_api.py",
    "kazma-ui/kazma_ui/calendar_api.py",
    "kazma-core/kazma_core/observability/connector_health.py",
)
_HELPERS = "kazma-skills/kazma_skills/native/email_manager/credentials.py"
#: Reaching the process environment or the vault yourself.
_SIDE_DOORS = {"environ", "getenv", "putenv", "SecretVault", "get_vault", "retrieve_with_tenant_ladder",
               "retrieve_scoped"}
#: Product code outside the held modules that names a mail or calendar
#: secret, each with why it may.
_DECLARED = {
    "kazma-core/kazma_core/backup/cloud_sync.py": (
        "the offsite backup to Google Drive is the install's work and uses the owner's Google grant "
        "(the owner's space's names, read with nothing bound)"),
    "kazma-core/kazma_core/settings_restore.py": (
        "a settings restore brings back the owner's extra mail accounts only while this vault holds "
        "their sign-in: the owner's names"),
}


def _held_files() -> list[Path]:
    out: list[Path] = []
    for where in _HELD:
        path = REPO / where
        out.extend([path] if path.is_file() else sorted(path.rglob("*.py")))
    return out


def _side_doors(source: str) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(ast.parse(source)):
        name = node.attr if isinstance(node, ast.Attribute) else node.id if isinstance(node, ast.Name) else None
        if name in _SIDE_DOORS:
            lines.append(node.lineno)
        elif isinstance(node, ast.alias) and node.name in _SIDE_DOORS:
            lines.append(getattr(node, "lineno", 0))
    return sorted(set(lines))


def test_the_mail_and_calendar_code_reads_only_through_the_helpers() -> None:
    files = _held_files()
    assert len(files) > 15
    found = {f.relative_to(REPO).as_posix(): doors for f in files
             if f.relative_to(REPO).as_posix() != _HELPERS
             and (doors := _side_doors(f.read_text(encoding="utf-8")))}
    assert found == {}


def test_control_a_planted_side_door_is_found() -> None:
    assert _side_doors('import os\ndef f():\n    return os.environ.get("EMAIL_IMAP_HOST")\n') == [3]
    assert _side_doors("from kazma_core.security.vault import get_vault\n") == [1]


_SECRET_NAME = ("email.gmail.", "email.microsoft.", "email.imap.", "email.generic.", "email.account.",
                "calendar.google.", "calendar.microsoft.")


def _names_a_secret(source: str) -> bool:
    return any(isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.startswith(_SECRET_NAME)
               for n in ast.walk(ast.parse(source)))


def test_no_other_code_names_a_mail_or_calendar_secret() -> None:
    held = {f.relative_to(REPO).as_posix() for f in _held_files()}
    others = set()
    for package in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway",
                    "kazma-cli/kazma_cli", "kazma-tui/kazma_tui", "kazma-skills/kazma_skills"):
        for f in (REPO / package).rglob("*.py"):
            rel = f.relative_to(REPO).as_posix()
            if rel not in held and _names_a_secret(f.read_text(encoding="utf-8")):
                others.add(rel)
    assert others == set(_DECLARED)


def test_control_a_planted_reader_outside_is_found() -> None:
    assert _names_a_secret('def f(v):\n    return v.retrieve("email.gmail.refresh_token")\n')
    assert not _names_a_secret('X = "email.accounts"\n')


def test_every_owner_name_stays_as_it_was() -> None:
    from kazma_core.spaces.connected_accounts import space_secret_name

    assert space_secret_name("email.gmail.refresh_token", OWNER_SPACE_ID) == "email.gmail.refresh_token"
    assert space_secret_name("email.gmail.refresh_token", "s1") == "space:s1:email.gmail.refresh_token"
    assert vault_mod.is_install_scoped_secret("space:s1:email.gmail.refresh_token")


def test_the_side_door_list_still_names_real_things() -> None:
    """The gate's words are the vault's and the os module's own."""
    assert hasattr(vault_mod, "get_vault") and hasattr(vault_mod, "SecretVault")
    assert hasattr(os, "environ") and hasattr(os, "getenv")


# ── the matrix cases fail with the space names taken out ─────────────────


@pytest.mark.parametrize("target", (
    "tool:email_accounts", "POST /api/email/protocol/disconnect", "POST /api/email/accounts/{alias}/remove",
    "GET /api/email/accounts",
))
def test_control_the_matrix_cases_fail_on_the_install_scoped_names(target, matrix, monkeypatch) -> None:
    """Every space on the owner's names, as before S1-17: A's request shows
    or changes B's mailbox. Cases that reach no network either way."""
    import kazma_core.spaces.connected_accounts as connected_accounts

    from tests._space_matrix import load_cases

    monkeypatch.setattr(connected_accounts, "connected_space", lambda: OWNER_SPACE_ID)
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        load_cases()[target].run(matrix)
