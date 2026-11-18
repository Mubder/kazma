"""Spaces S1-17, part b: an X account belongs to a space.

Guards act only where Spaces is enforced; one account, and the older
multi-user mode, behave as before. Every X key and row was already kept per
tenant (``x_api.ownership.x_config_key``), and a space is a tenant. Enforced:

* the tenant X work is under is the bound space, and work bound to no space
  reads no X account -- it read the owner's;
* the owner's environment and the legacy vault names (``x_api_key`` ...) are
  the owner's space's only: a space's vault read falls back to a global row,
  so a stray global copy would lend the owner's account to every space, and
  ``X_BEARER_TOKEN`` would spend the owner's X API budget;
* background X work -- a booked post, a receipt, a projection repair, the
  legacy schedule, the mention poller -- runs in the whole scope of the space
  whose row it is (the space and the account its own work acts for, as every
  queued work), and a row of no space does not run;
* Sign in with X finishes in the space it started in, and another space's
  X notice stays off the owner's channels (O1).

Each guard is taken out as a control. The route and tool cases are in
``tests/_x_space_cases.py``.
"""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from kazma_core.spaces import scope
from kazma_core.spaces.scope import OWNER_ACCOUNT_ID, OWNER_SPACE_ID

from tests._space_matrix import Matrix, load_cases
from tests._x_space_cases import ITEM

CASES = load_cases()


@pytest.fixture
def matrix(monkeypatch) -> Matrix:
    monkeypatch.delenv("KAZMA_PRODUCTION", raising=False)
    with Matrix() as made:
        yield made


@pytest.fixture
def enforced(monkeypatch) -> None:
    monkeypatch.setattr(scope, "_enforced", True)


@pytest.fixture
def private(monkeypatch):
    """A private data folder and a real vault of the test's own."""
    import kazma_core.x_api.reply_store as reply_store
    from kazma_core.security import vault as vault_mod

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        monkeypatch.setenv("KAZMA_DATA_DIR", tmp)
        monkeypatch.setenv("KAZMA_VAULT_KEY", "k" * 64)
        for name in ("X_API_KEY", "X_API_KEY_SECRET", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET", "X_BEARER_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(reply_store, "_store", None)
        v = vault_mod.SecretVault(db_path=str(Path(tmp) / "vault.db"))
        monkeypatch.setattr(vault_mod, "get_vault", lambda: v)
        try:
            yield v
        finally:
            v.close()


def _fails(target: str, matrix: Matrix) -> None:
    with pytest.raises((AssertionError, pytest.fail.Exception)):
        CASES[target].run(matrix)


def test_the_part_wrote_a_case_for_every_x_route_and_tool() -> None:
    import tests._x_space_cases as x_cases

    mine = sorted(t for t, c in CASES.items() if c.item == ITEM and c.run.__module__ == x_cases.__name__)
    assert len([t for t in mine if t.startswith("tool:x_")]) == 13
    assert "GET /x" in mine and "POST /api/scheduled/x" in mine


# ── the tenant X work is under ───────────────────────────────────────────


def test_once_enforced_x_work_is_the_bound_spaces_and_no_one_elses(matrix, enforced) -> None:
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.ownership import x_config_key, x_tenant_id

    with matrix.as_a():
        assert x_tenant_id() == Matrix.SPACE_A
        assert x_config_key("connectors.x.handle") == f"tenant.{Matrix.SPACE_A}.connectors.x.handle"
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert x_config_key("connectors.x.handle") == "connectors.x.handle"  # the owner's as today
    with pytest.raises(scope.NoSpaceBound):
        x_tenant_id()  # nothing bound: never the owner's
    with tenant_scope(Matrix.SPACE_A):
        assert x_tenant_id() == Matrix.SPACE_A  # a tenant bound by older code names its space


def test_not_enforced_the_tenant_rule_is_as_before(matrix) -> None:
    from kazma_core.tenant_context import tenant_scope
    from kazma_core.x_api.ownership import x_tenant_id

    with tenant_scope("someone"):
        assert x_tenant_id() == "someone"


def test_control_the_old_rule_names_the_owners_keys_for_no_one(enforced, monkeypatch) -> None:
    """The rule as before part b: with nothing bound (single-operator mode),
    the owner's tenant -- every X key named is the owner's."""
    import kazma_core.tenant_isolation as tenant_isolation
    import kazma_core.x_api.ownership as ownership
    from kazma_core.tenant_context import get_current_tenant_id

    def as_before() -> str:
        return str(get_current_tenant_id() or "").strip() or "default"

    monkeypatch.setattr(tenant_isolation, "multi_user_or_production", lambda: False)
    monkeypatch.setattr(ownership, "x_tenant_id", as_before)
    assert ownership.x_config_key("connectors.x.api_key") == "connectors.x.api_key"  # the owner's


# ── the owner's environment and legacy vault names ───────────────────────


def _credentials_in(space: str, account: str) -> Any:
    from kazma_core.x_api.config import get_x_config

    with scope.bind_scope(space, account):
        return get_x_config().credentials


def test_the_legacy_vault_names_are_the_owners_spaces_only(matrix, enforced, private) -> None:
    for name in ("x_api_key", "x_api_key_secret", "x_access_token", "x_access_token_secret"):
        private.store(name, f"owners-{name}")  # a global row, as an old setup left it
    assert _credentials_in(OWNER_SPACE_ID, OWNER_ACCOUNT_ID).api_key == "owners-x_api_key"
    theirs = _credentials_in(Matrix.SPACE_A, matrix.account_a)
    assert not theirs.api_key and not theirs.complete()


def test_control_reading_the_legacy_names_in_every_space_lends_the_owners_keys(matrix, enforced, private,
                                                                                monkeypatch) -> None:
    """The loader as before part b, reading the legacy names in any space: a
    space's vault read falls back to the global row, the owner's."""
    import kazma_core.spaces.scope as scope_mod

    for name in ("x_api_key", "x_api_key_secret", "x_access_token", "x_access_token_secret"):
        private.store(name, f"owners-{name}")
    monkeypatch.setattr(scope_mod, "spaces_enforced", lambda: False)
    lent = _credentials_in(Matrix.SPACE_A, matrix.account_a)
    assert lent.api_key == "owners-x_api_key"


def test_x_bearer_token_from_the_environment_is_the_owners(matrix, enforced, private, monkeypatch) -> None:
    from kazma_core.x_api import auth_tokens

    monkeypatch.setenv("X_BEARER_TOKEN", "owners-bearer")
    with scope.bind_scope(OWNER_SPACE_ID, OWNER_ACCOUNT_ID):
        assert auth_tokens.bearer_token() == "owners-bearer"
    with matrix.as_a():
        assert auth_tokens.bearer_token() == "" and auth_tokens.bearer_from_env() == ""
    monkeypatch.setattr(scope, "_enforced", False)
    with matrix.as_a():
        assert auth_tokens.bearer_token() == "owners-bearer"  # not enforced: as before


# ── background X work runs in the whole scope of its space ──────────────


def test_x_work_scope_binds_the_whole_scope_once_enforced(matrix, enforced) -> None:
    from kazma_core.x_api.ownership import x_tenant_id, x_work_scope

    with x_work_scope(Matrix.SPACE_A)():
        bound = scope.current_scope()
        assert bound is not None and bound.space_id == Matrix.SPACE_A
        assert bound.asker == matrix.account_a  # the space's own work acts for its person
        assert x_tenant_id() == Matrix.SPACE_A
    with pytest.raises(scope.NoSpaceBound):
        x_work_scope("no-such-space")


def test_not_enforced_x_work_binds_the_tenant_alone_as_before(matrix) -> None:
    from kazma_core.tenant_context import get_current_tenant_id
    from kazma_core.x_api.ownership import x_work_scope

    with x_work_scope("someone")():
        assert get_current_tenant_id() == "someone" and scope.current_scope() is None


def _due_post_fired(monkeypatch, tenant: str, *, binding: Any = None) -> tuple[list[Any], list[Any]]:
    """One due booked post of *tenant* through the fire loop: (where the
    dispatch ran, what was held)."""
    import kazma_core.x_api.notifications as notifications
    import kazma_core.x_api.publication_service as service
    import kazma_core.x_api.schedule as schedule

    ran: list[Any] = []
    held: list[Any] = []

    class _Store:
        def recover(self) -> int:
            return 0

        def unconfirmed_tenants(self) -> list[str]:
            return []

        def repair_tenants(self) -> list[str]:
            return []

        def due(self) -> list[dict[str, Any]]:
            return [{"id": "op1", "tenant_id": tenant}]

        def hold(self, ident: str, *, tenant_id: str, reason: str) -> bool:
            held.append((ident, tenant_id))
            return True

    class _Legacy:
        def legacy_tenants(self) -> list[str]:
            return []

    async def dispatch(ident: str) -> None:
        from kazma_core.x_api.ownership import x_tenant_id

        bound = scope.current_scope()
        ran.append((ident, bound.space_id if bound else None, bound.asker if bound else None, x_tenant_id()))

    async def nothing() -> int:
        return 0

    monkeypatch.setattr(service, "get_publication_store", lambda: _Store())
    # fire_due_operations imports it when it runs: the module's own name.
    monkeypatch.setattr(schedule, "get_x_scheduled_store", lambda: _Legacy())
    monkeypatch.setattr(service, "_dispatch_operation", dispatch)
    monkeypatch.setattr(service, "_retain", lambda: {})
    monkeypatch.setattr(notifications, "drain_notifications", nothing)
    if binding is not None:
        monkeypatch.setattr(service, "_work_scope", binding)
    asyncio.run(service.fire_due_operations())
    return ran, held


def test_a_booked_post_fires_in_its_spaces_whole_scope(matrix, enforced, monkeypatch) -> None:
    ran, held = _due_post_fired(monkeypatch, Matrix.SPACE_A)
    assert ran == [("op1", Matrix.SPACE_A, matrix.account_a, Matrix.SPACE_A)] and held == []


def test_a_booked_post_of_no_space_is_held_and_never_sent(matrix, enforced, monkeypatch) -> None:
    ran, held = _due_post_fired(monkeypatch, "no-such-space")
    assert ran == [] and held == [("op1", "no-such-space")]


async def _tenant_alone(tenant: str) -> Any:
    """The binding as before part b: the tenant alone."""
    from kazma_core.tenant_context import tenant_scope

    return lambda: tenant_scope(tenant)


def test_control_the_tenant_alone_sends_a_post_of_no_space(matrix, enforced, monkeypatch) -> None:
    """With the binding as before, a post whose tenant no space of the
    install holds is sent, by no person."""
    ran, held = _due_post_fired(monkeypatch, "no-such-space", binding=_tenant_alone)
    assert ran == [("op1", None, None, "no-such-space")] and held == []


# ── whose X work it is cannot be read ────────────────────────────────────

class _Down(ConnectionError):
    """The database away, as the database watch counts it (stood in for)."""


def _identity_fails(monkeypatch, cause: BaseException) -> list[str]:
    """The identity store failing as it does (``IdentityUnavailable`` from
    *cause*); what the database watch was told."""
    from kazma_core.db import availability
    from kazma_core.spaces import identity

    def unreadable(*args: Any, **kwargs: Any) -> Any:
        raise identity.IdentityUnavailable("the identity store cannot be read") from cause

    told: list[str] = []

    def note_failure(kind: str, exc: BaseException | None = None, **kwargs: Any) -> bool:
        told.append(kind)
        return isinstance(exc, _Down)

    monkeypatch.setattr(identity, "work_scope", unreadable)
    monkeypatch.setattr(availability, "note_failure", note_failure)
    return told


def test_a_booked_post_waits_while_the_database_is_away(matrix, enforced, monkeypatch) -> None:
    told = _identity_fails(monkeypatch, _Down("database away"))
    ran, held = _due_post_fired(monkeypatch, Matrix.SPACE_A)
    assert ran == [] and held == [] and told == ["x"]


def test_a_booked_post_whose_owner_cannot_be_read_is_held(matrix, enforced, monkeypatch) -> None:
    """A failure that does not pass (a registry that is not a mapping) ends
    the post once: held for review, never tried again every pass."""
    _identity_fails(monkeypatch, ValueError("the registry is not a mapping"))
    ran, held = _due_post_fired(monkeypatch, Matrix.SPACE_A)
    assert ran == [] and held == [("op1", Matrix.SPACE_A)]


def _legacy_post_fired(monkeypatch) -> list[tuple[str, str]]:
    """One due legacy scheduled post of A's space through the legacy fire
    loop; what the store was told."""
    import kazma_core.x_api.publication_service as service
    import kazma_core.x_api.scheduled_fire as scheduled_fire
    from kazma_core.x_api.schedule import STATUS_PENDING

    told: list[tuple[str, str]] = []
    post = type("Post", (), {"id": "p1", "tenant_id": Matrix.SPACE_A})()

    class _Legacy:
        def list_due(self) -> list[Any]:
            return [post]

        def get(self, ident: str, *, tenant_id: str) -> Any:
            return type("Current", (), {"status": STATUS_PENDING})()

        def mark_failed(self, ident: str, why: str) -> None:
            told.append(("failed", why))

        def mark_unknown(self, ident: str, why: str) -> None:
            told.append(("unknown", why))

    async def nothing() -> None:
        return None

    async def sent(given: Any) -> None:
        told.append(("sent", given.id))

    monkeypatch.setattr(service, "fire_due_operations", nothing)
    monkeypatch.setattr(scheduled_fire, "x_schedule_enabled", lambda: True)
    monkeypatch.setattr(scheduled_fire, "get_x_scheduled_store", lambda: _Legacy())
    monkeypatch.setattr(scheduled_fire, "_fire_post_inner", sent)
    asyncio.run(scheduled_fire._fire_due_posts())
    return told


def test_a_legacy_post_waits_while_the_database_is_away(matrix, enforced, monkeypatch) -> None:
    told = _identity_fails(monkeypatch, _Down("database away"))
    assert _legacy_post_fired(monkeypatch) == [] and told == ["x"]


def test_a_legacy_post_whose_owner_cannot_be_read_fails_once(matrix, enforced, monkeypatch) -> None:
    _identity_fails(monkeypatch, ValueError("the registry is not a mapping"))
    assert _legacy_post_fired(monkeypatch) == [("failed", "whose post this is cannot be read")]


def test_control_an_uncaught_identity_failure_fails_the_legacy_post_for_good(matrix, enforced,
                                                                            monkeypatch) -> None:
    """Without the wait, the same outage reaches the fire loop's catch-all,
    which fails a pending post for good."""
    from kazma_core.x_api import ownership

    _identity_fails(monkeypatch, _Down("database away"))
    monkeypatch.setattr(ownership, "identity_failure_passes", lambda exc: (_ for _ in ()).throw(exc))
    assert _legacy_post_fired(monkeypatch) == [("failed", "internal error before send")]


def _poller_reads_in(monkeypatch) -> list[Any]:
    """Where the mention poller reads its settings: (the X tenant, the person)."""
    import kazma_core.x_api.mentions_fire as mentions_fire
    import kazma_core.x_api.stance as stance

    read_in: list[Any] = []

    def reply_config() -> Any:
        from kazma_core.x_api.ownership import x_tenant_id

        bound = scope.current_scope()
        read_in.append((x_tenant_id(), bound.asker if bound else None))

        class _Off:
            def can_draft(self) -> bool:
                return False

        return _Off()

    monkeypatch.setattr(stance, "get_reply_config", reply_config)
    assert asyncio.run(mentions_fire.ensure_mentions_loop()) is False
    return read_in


def test_the_mention_poller_reads_the_owners_account_in_the_owners_scope(enforced, monkeypatch) -> None:
    assert _poller_reads_in(monkeypatch) == [(OWNER_SPACE_ID, OWNER_ACCOUNT_ID)]


def test_control_with_the_owners_tenant_alone_the_poller_works_for_no_person(enforced, monkeypatch) -> None:
    import kazma_core.x_api.ownership as ownership
    from kazma_core.tenant_context import tenant_scope

    monkeypatch.setattr(ownership, "x_work_scope", lambda tenant: (lambda: tenant_scope(tenant)))
    assert _poller_reads_in(monkeypatch) == [(OWNER_SPACE_ID, None)]


def test_the_supervisors_x_cancel_with_nothing_bound_is_refused_once_enforced(enforced) -> None:
    from kazma_core.runs.adapters.x_posts import ADAPTER

    result = ADAPTER._cancel("op1")
    assert (result.ok, result.code) == (False, "no_space")


# ── another space's X notice stays off the owner's channels (O1) ────────


def _notices_delivered(monkeypatch, tenants: tuple[str, ...]) -> tuple[list[str], dict[str, str]]:
    """One health notice of each tenant through the drain: the cards the
    owner's channels took, and each row's outcome."""
    import kazma_core.observability.ops_alerts as ops_alerts
    import kazma_core.x_api.notifications as notifications
    from kazma_core.observability.alert_card import format_operator_card
    from kazma_core.x_api.publication_store import get_publication_store

    sent: list[str] = []

    async def deliver(text: str) -> bool:
        sent.append(text)
        return True

    monkeypatch.setattr(ops_alerts, "deliver_acknowledged", deliver)
    store = get_publication_store()
    with store._connection(transaction=True) as conn:
        for n, tenant in enumerate(tenants):
            # A health notice is current while it is its tenant's newest.
            notifications.enqueue(conn, tenant=tenant, key=f"health:mentions:failed:{time.time() + n}",
                                  message=format_operator_card("X", "warning", f"notice of {tenant}"))
    asyncio.run(notifications._drain_store(store))
    with store._connection() as conn:
        rows = conn.execute("SELECT tenant_id, delivered_at, last_error FROM x_notification_outbox").fetchall()
    return sent, {r["tenant_id"]: ("delivered" if r["delivered_at"] else r["last_error"]) for r in rows}


def test_another_spaces_x_notice_stays_off_the_owners_channels(enforced, private, monkeypatch) -> None:
    from kazma_core.x_api.notifications import KEPT_IN_ITS_SPACE

    sent, outcome = _notices_delivered(monkeypatch, (OWNER_SPACE_ID, Matrix.SPACE_A))
    assert len(sent) == 1 and f"notice of {OWNER_SPACE_ID}" in sent[0]
    assert outcome == {OWNER_SPACE_ID: "delivered", Matrix.SPACE_A: KEPT_IN_ITS_SPACE}


def test_control_every_spaces_notice_reached_the_owners_channels(enforced, private, monkeypatch) -> None:
    import kazma_core.x_api.notifications as notifications

    monkeypatch.setattr(notifications, "_kept_in_its_space", lambda tenant: False)
    sent, _outcome = _notices_delivered(monkeypatch, (OWNER_SPACE_ID, Matrix.SPACE_A))
    assert len(sent) == 2


def test_not_enforced_every_notice_is_delivered_as_before(private, monkeypatch) -> None:
    sent, _outcome = _notices_delivered(monkeypatch, (OWNER_SPACE_ID, "someone"))
    assert len(sent) == 2


# ── Sign in with X finishes in the space it started in ───────────────────


#: A space B made and A joined: not A's first space.
SHARED_X = "matrix-x-shared"


def _signed_in_with_x(matrix: Matrix, monkeypatch) -> tuple[Any, dict[str, bool]]:
    """A starts Sign in with X in a shared space (not A's first); X sends the
    browser back to the callback, which names no space. The callback's
    answer, and whether each of A's spaces shows an X sign-in."""
    from urllib.parse import parse_qs, urlparse

    from kazma_core.spaces import identity
    from kazma_core.x_api import oauth2_sign_in
    from kazma_ui import space_binding

    from tests._x_space_cases import world

    exchanged = {"access_token": "a-access", "refresh_token": "a-refresh", "expires_in": 7200,
                 "scope": "tweet.read users.read"}

    with world(matrix) as w:
        identity.create_space("Shared X", created_by=matrix.account_b, space_id=SHARED_X)
        identity.add_member(SHARED_X, matrix.account_a)

        def in_space(space: str, method: str, path: str, **kwargs: Any) -> Any:
            headers = {"x-matrix-person": "a", space_binding.SPACE_HEADER: space,
                       "Origin": "http://testserver", "X-Requested-With": "XMLHttpRequest"}
            return w.client.request(method, path, headers=headers, **kwargs)

        monkeypatch.setattr(oauth2_sign_in, "exchange", lambda code, state, attempt: exchanged)
        assert in_space(SHARED_X, "POST", "/api/x/api-access", json={"client_id": "sharedclient1"}).status_code == 200
        started = in_space(SHARED_X, "POST", "/api/x/oauth2/start", json={})
        assert started.status_code == 200, started.text[:300]
        state = parse_qs(urlparse(started.json()["authorize_url"]).query)["state"][0]
        back = w.client.get(f"/api/x/oauth2/callback?code=c&state={state}", follow_redirects=False,
                            headers={"x-matrix-person": "a"})
        shown = {space: in_space(space, "GET", "/api/x/api-access").json()["signed_in"]
                 for space in (SHARED_X, Matrix.SPACE_A)}
        return back, shown


def test_sign_in_with_x_lands_in_the_space_it_started_in(matrix, monkeypatch) -> None:
    back, shown = _signed_in_with_x(matrix, monkeypatch)
    assert back.status_code == 302 and "x_oauth=ok" in back.headers["location"], back.headers.get("location")
    assert shown == {SHARED_X: True, Matrix.SPACE_A: False}


def test_control_a_callback_that_binds_no_space_keeps_it_in_the_wrong_space(matrix, monkeypatch) -> None:
    """The callback as before part b: it names no space, so it runs in the
    person's first space -- the sign-in started in the shared space landed
    in A's own."""
    import kazma_ui.oauth_sign_in as oauth_sign_in

    monkeypatch.setattr(oauth_sign_in, "attempt_scope", lambda attempt: contextlib.nullcontext())
    _back, shown = _signed_in_with_x(matrix, monkeypatch)
    assert shown == {SHARED_X: False, Matrix.SPACE_A: True}


# ── the source gate: the owner's environment is read in two places ───────

_X_ROOTS = ("kazma-core/kazma_core/x_api", "kazma-skills/kazma_skills/native/x_publisher")
#: Where X code may read the environment: the two readers that keep it the
#: owner's space's, and Kazma's own switches (a ``KAZMA_`` name, or a
#: variable the function names one).
_ENV_READERS = {
    "config.py::_load_credentials": "the OAuth 1.0a keys, the owner's space's only",
    "auth_tokens.py::bearer_from_env": "X_BEARER_TOKEN, the owner's space's only",
    "config.py::_env_flag": "Kazma's own switches (KAZMA_X_POST ...)",
    "stance.py::_killed": "Kazma's own switches (KAZMA_X_REPLY ...)",
}


def _env_reads(path: Path, source: str) -> list[str]:
    """``file::function`` of every environment read whose name is not a
    literal ``KAZMA_`` name."""
    import ast

    found: list[str] = []

    def walk(node: Any, where: str) -> None:
        for child in ast.iter_child_nodes(node):
            here = f"{path.name}::{child.name}" if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                else where
            text = ast.unparse(child) if isinstance(child, (ast.Call, ast.Subscript)) else ""
            if text.startswith(("os.environ.get(", "os.getenv(", "os.environ[")):
                arg = child.args[0] if isinstance(child, ast.Call) and child.args else getattr(child, "slice", None)
                if not (isinstance(arg, ast.Constant) and str(arg.value).startswith("KAZMA_")):
                    found.append(here)
            walk(child, here)

    walk(ast.parse(source), f"{path.name}::<module>")
    return found


def _env_findings(extra: dict[str, str] | None = None) -> list[str]:
    root = Path(__file__).resolve().parents[1]
    reads: list[str] = []
    for folder in _X_ROOTS:
        for path in sorted((root / folder).glob("*.py")):
            reads += _env_reads(path, path.read_text(encoding="utf-8"))
    for name, source in (extra or {}).items():
        reads += _env_reads(Path(name), source)
    return sorted({where for where in reads if where.split("::")[0] + "::" + where.split("::")[1]
                   not in _ENV_READERS})


def test_x_code_reads_the_owners_environment_only_where_it_keeps_it_the_owners() -> None:
    assert _env_findings() == []
    assert set(_ENV_READERS) <= {w for f in _X_ROOTS
                                  for p in (Path(__file__).resolve().parents[1] / f).glob("*.py")
                                  for w in _env_reads(p, p.read_text(encoding="utf-8"))}, \
        "a declared reader no longer reads the environment"


def test_control_a_planted_environment_read_fails_the_gate() -> None:
    planted = "import os\ndef read_key():\n    return os.environ.get('X_API_KEY')\n"
    assert _env_findings({"planted.py": planted}) == ["planted.py::read_key"]


# ── the matrix cases see each guard taken out ────────────────────────────


@pytest.mark.parametrize("target", ("GET /api/x/drafts", "GET /api/x/queue", "GET /api/x/operations",
                                    "GET /api/x/reply/recent"))
def test_control_with_another_spaces_tenant_the_cases_fail(target, matrix, monkeypatch) -> None:
    """The X tenant rule answering B's space whoever asks: the cases see B's
    rows."""
    import kazma_core.x_api.ownership as ownership

    monkeypatch.setattr(ownership, "x_tenant_id", lambda: Matrix.SPACE_B)
    _fails(target, matrix)
