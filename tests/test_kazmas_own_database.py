"""One answer to "is this Kazma's own database?" (Spaces S4-01 step 5 review M1, §35).

The memory mirror and pgvector asked by address (host, port, database as
written); the checkpoints' backfill asked by server identity. Through another
name for the same database -- a ``hostaddr`` alias -- the mirror took it for
another, connected as the tables' owner, and read every space's rows once the
lock was armed. ``availability.kazma_database`` is now the one answer: the
same address at once; any other, armed (or in the operator's ``apply``), by
the server identity of both -- two system identifiers and the database name,
a start time telling nothing (delta review N2); not armed, by address alone
with no I/O (delta review N1). An identity that cannot be read is "not told"
for a while, and an armed store refuses an address not told.

The gate: nothing else reads a database's identity, compares with Kazma's own
address, parses it to compare, or uses the answer's private helpers; each
with a planted control (delta review N3). Real Postgres:
``tests/test_pg_armed_flows.py`` (the mirror and pgvector by another name,
armed) and ``tests/test_pg_checkpoint_spaces.py`` (the backfill by another
name).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import availability

from tests._as_request import lock_armed, not_armed

ROOT = Path(__file__).resolve().parents[1]
OWN = "postgresql://kazma@127.0.0.1:1/kazma"  # nothing listens on port 1
ALIAS = "host=kazma-db.invalid hostaddr=127.0.0.1 port=1 dbname=kazma user=kazma"
SERVER = "7712779680233943083"
#: The real reader: the suite's conftest stubs it where no real database is allowed.
REAL_SERVER_IDENTITY = availability.server_identity


@pytest.fixture()
def identities(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Kazma's own address, and each address's identity as a server would answer."""
    from kazma_core.db import backend

    known: dict[str, Any] = {OWN: ("system", SERVER, "kazma")}
    asked: list[tuple[str, float]] = []

    def identity(dsn: str, *, timeout: float = 5.0) -> Any:
        asked.append((dsn, timeout))
        return known.get(dsn)

    monkeypatch.setattr(backend, "get_database_url", lambda: OWN)
    monkeypatch.setattr(availability, "server_identity", identity)
    known["asked"] = asked
    return known


def test_the_same_address_is_kazmas_with_nothing_read(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")  # an address is read with psycopg's conninfo
    assert availability.kazma_database(OWN) is True
    assert availability.kazma_database(OWN, by_identity=True) is True
    assert identities["asked"] == [], "no connection for the same address"


def test_not_armed_another_address_is_told_by_itself_with_nothing_read(identities: dict[str, Any],
                                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    """Delta review N1: not armed, the address alone answers, as before step
    5's review -- no identity read, so no outage costs a connection seconds.
    Armed (the control), the same address is told by identity."""
    pytest.importorskip("psycopg")
    identities[ALIAS] = identities[OWN]
    not_armed(monkeypatch)
    assert availability.kazma_database(ALIAS) is False
    assert availability.kazma_database(ALIAS, or_its_server=True) is False
    assert identities["asked"] == []
    lock_armed(monkeypatch, True)
    assert availability.kazma_database(ALIAS, timeout=2.0) is True
    assert {dsn for dsn, _ in identities["asked"]} == {ALIAS, OWN}
    assert {timeout for _, timeout in identities["asked"]} == {2.0}, "read with the asker's timeout"


def test_another_name_for_kazmas_database_is_kazmas(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = identities[OWN]
    assert not availability._same_as_kazmas(ALIAS, availability._database_of), "the control: by address, another"
    assert availability.kazma_database(ALIAS, by_identity=True) is True
    assert availability.kazma_database(ALIAS, or_its_server=True, by_identity=True) is True


def test_another_database_on_kazmas_server_is_its_server_only(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = ("system", SERVER, "other")
    assert availability.kazma_database(ALIAS, by_identity=True) is False
    assert availability.kazma_database(ALIAS, or_its_server=True, by_identity=True) is True


def test_another_server_is_neither(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = ("system", "1111111111111111111", "kazma")
    assert availability.kazma_database(ALIAS, by_identity=True) is False
    assert availability.kazma_database(ALIAS, or_its_server=True, by_identity=True) is False


@pytest.mark.parametrize(("theirs", "mine"), [
    (("system", SERVER, "kazma"), ("start", "2026-11-27 13:38:04+00", "kazma")),  # the review's probe
    (("start", "2026-11-27 13:38:04+00", "kazma"), ("system", SERVER, "kazma")),
    (("start", "2026-11-27 13:38:04+00", "kazma"), ("start", "2026-11-27 13:38:04+00", "kazma")),
    (("start", "2026-11-27 13:38:04+00", "kazma"), ("start", "2026-11-27 14:02:11+00", "kazma")),  # a restart
])
def test_a_start_time_tells_nothing(identities: dict[str, Any], theirs: tuple[str, str, str],
                                    mine: tuple[str, str, str]) -> None:
    """Delta review N2: where one login may not read pg_control_system(), its
    side read the start time, and the two sides were compared as they came:
    never equal, so another name for Kazma's database counted as another --
    armed, an owner's sign-in the lock does not hold. Like is compared with
    like: a start time on either side is "not told" (None), which an armed
    store refuses; so is a restart between two reads."""
    pytest.importorskip("psycopg")
    identities[OWN], identities[ALIAS] = mine, theirs
    assert availability.kazma_database(ALIAS, by_identity=True) is None
    assert availability.kazma_database(ALIAS, or_its_server=True, by_identity=True) is None


def test_an_identity_that_cannot_be_read_is_no_answer(identities: dict[str, Any]) -> None:
    """The server away, a refusal: unknown -- a caller keeps no answer and asks
    again; is_kazma_database reads it as not Kazma's."""
    pytest.importorskip("psycopg")
    assert availability.kazma_database(ALIAS, by_identity=True) is None
    assert availability.is_kazma_database(ALIAS) is False


def test_pgvector_keeps_only_an_answer_that_cannot_change(identities: dict[str, Any],
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    """Not armed, "not by address" is asked again (by address, no I/O), so an
    arming later reads the identity; armed, an unknown one is asked again and
    a told one kept."""
    pytest.importorskip("psycopg")
    from kazma_core.memory.backends import PgvectorBackend

    vectors = PgvectorBackend(dsn=ALIAS)
    not_armed(monkeypatch)
    with pytest.raises(Exception):  # nothing listens there; the answer is what matters
        vectors._connect()
    assert vectors._kazmas_database is None and identities["asked"] == [], "by address, not kept, nothing read"
    lock_armed(monkeypatch, True)
    with pytest.raises(Exception):
        vectors._connect()
    assert vectors._kazmas_database is None, "not told: asked again"
    identities[ALIAS] = identities[OWN]
    with pytest.raises(Exception):
        vectors._connect()
    assert vectors._kazmas_database is True, "told, kept"


# ── not told: read again only after a while, with the asker's timeout ──────


class _Refusing:
    """psycopg.connect, as a server that cannot be reached answers."""

    def __init__(self) -> None:
        self.tried: list[tuple[str, Any]] = []

    def __call__(self, dsn: str, **kwargs: Any) -> Any:
        psycopg = pytest.importorskip("psycopg")
        self.tried.append((dsn, kwargs.get("connect_timeout")))
        raise psycopg.OperationalError("connection refused")


def test_an_identity_not_read_is_not_read_again_for_a_while(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delta review N1: a failed read was asked again at every connection,
    each up to 5 s. Now it is "not told" for UNTOLD_FOR_SECONDS, and read with
    the asker's timeout."""
    psycopg = pytest.importorskip("psycopg")
    refusing = _Refusing()
    monkeypatch.setattr(psycopg, "connect", refusing)
    now = [1000.0]
    monkeypatch.setattr(availability, "_clock", lambda: now[0])
    availability.reset()
    assert REAL_SERVER_IDENTITY(ALIAS, timeout=2.0) is None
    assert refusing.tried == [(ALIAS, 2)]
    now[0] += availability.UNTOLD_FOR_SECONDS - 1
    assert REAL_SERVER_IDENTITY(ALIAS, timeout=2.0) is None
    assert len(refusing.tried) == 1, "not read again inside the window"
    now[0] += 2
    assert REAL_SERVER_IDENTITY(ALIAS, timeout=2.0) is None
    assert len(refusing.tried) == 2, "read again after it"
    availability.reset()


def _store_on(store: str, dsn: str, monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> Any:
    """The mirror or pgvector on *dsn*, its own sign-in recorded instead of
    made (the identity reader's connections still go to psycopg.connect)."""
    if store == "mirror":
        from kazma_core.memory.state_backend import PostgresStateBackend

        backend = PostgresStateBackend(dsn, timeout_s=2.0)
        monkeypatch.setattr(backend, "_open_connection", lambda: opened.append(store) or "its own sign-in")
        return backend
    from kazma_core.memory.backends import PgvectorBackend

    psycopg = pytest.importorskip("psycopg")
    backend = PgvectorBackend(dsn=dsn, timeout_s=2.0)
    real_connect = psycopg.connect

    def own_or_identity(conninfo: str, **kwargs: Any) -> Any:
        if kwargs.get("autocommit"):  # the identity reader's
            return real_connect(conninfo, **kwargs)
        opened.append(store)
        return "its own sign-in"

    monkeypatch.setattr(psycopg, "connect", own_or_identity)
    return backend


@pytest.mark.parametrize("store", ["mirror", "pgvector"])
def test_during_an_outage_a_store_elsewhere_reads_no_identity_unarmed(monkeypatch: pytest.MonkeyPatch,
                                                                      store: str) -> None:
    """The review's case: Spaces off, Kazma's address unreachable, the mirror
    elsewhere. Not armed, ``_connect`` opens no connection for an identity
    (9351979cb opened two, 5 s each). Armed (the control): at most one try
    each, with the store's own timeout, then refused; the next connection in
    the window tries none."""
    psycopg = pytest.importorskip("psycopg")
    from kazma_core.db import backend as db_backend
    from kazma_core.spaces.scope import NoSpaceBound

    elsewhere = "postgresql://kazma@127.0.0.1:2/memory"
    monkeypatch.setattr(db_backend, "get_database_url", lambda: OWN)
    monkeypatch.setattr(availability, "server_identity", REAL_SERVER_IDENTITY)
    availability.reset()
    opened: list[str] = []
    refusing = _Refusing()
    store_backend = _store_on(store, elsewhere, monkeypatch, opened)
    real_or_refusing = psycopg.connect

    def identity_connect(conninfo: str, **kwargs: Any) -> Any:
        if kwargs.get("autocommit"):
            return refusing(conninfo, **kwargs)
        return real_or_refusing(conninfo, **kwargs)

    monkeypatch.setattr(psycopg, "connect", identity_connect)
    not_armed(monkeypatch)
    assert store_backend._connect() == "its own sign-in"
    assert store_backend._connect() == "its own sign-in"
    assert refusing.tried == [], "no identity read while not armed"
    lock_armed(monkeypatch, True)
    with pytest.raises(NoSpaceBound):
        store_backend._connect()
    assert sorted(dsn for dsn, _ in refusing.tried) == sorted([elsewhere, OWN])
    assert {timeout for _, timeout in refusing.tried} == {2}, "the store's own timeout, not a fixed 5 s"
    with pytest.raises(NoSpaceBound):
        store_backend._connect()
    assert len(refusing.tried) == 2, "not told is kept for a while: no second try"
    assert opened == [store, store], "armed, never reached on its own sign-in"
    availability.reset()


def _untold_store(store: str, monkeypatch: pytest.MonkeyPatch, opened: list[str]) -> Any:
    """The mirror or pgvector on ALIAS, whose identity is not told; its own
    sign-in recorded instead of made."""
    if store == "mirror":
        from kazma_core.memory.state_backend import PostgresStateBackend

        backend = PostgresStateBackend(ALIAS)
        monkeypatch.setattr(backend, "_open_connection", lambda: opened.append(store) or "its own sign-in")
        return backend
    psycopg = pytest.importorskip("psycopg")
    from kazma_core.memory.backends import PgvectorBackend

    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: opened.append(store) or "its own sign-in")
    return PgvectorBackend(dsn=ALIAS)


@pytest.mark.parametrize("store", ["mirror", "pgvector"])
def test_armed_an_address_not_told_is_refused_never_reached_as_the_owner(
        identities: dict[str, Any], monkeypatch: pytest.MonkeyPatch, store: str) -> None:
    """When an identity could not be read, the mirror and pgvector took the
    address for another database and signed in on their own -- on another
    name for Kazma's database, as the tables' owner, whom the lock does not
    hold -- armed too. Armed, an untold address is now a space refusal,
    asked again next time; not armed, it is reached as before (the control)."""
    pytest.importorskip("psycopg")
    from kazma_core.db.pg_helpers import is_space_refusal
    from kazma_core.spaces.scope import NoSpaceBound

    opened: list[str] = []
    backend = _untold_store(store, monkeypatch, opened)
    lock_armed(monkeypatch, True)
    with pytest.raises(NoSpaceBound) as refused:
        backend._connect()
    assert is_space_refusal(refused.value), "said as a refusal, never as an outage"
    assert opened == [], "not reached on its own sign-in"
    not_armed(monkeypatch)
    assert backend._connect() == "its own sign-in"
    assert opened == [store]


def test_a_backfill_not_told_says_so_and_changes_nothing(identities: dict[str, Any],
                                                         monkeypatch: pytest.MonkeyPatch,
                                                         caplog: pytest.LogCaptureFixture) -> None:
    """Not told is not "another database": the backfill says it could not
    tell, with the remedy (run again), at WARNING, before it reads any
    record. It asks by identity armed or not: the operator's apply."""
    import logging

    pytest.importorskip("psycopg")
    from kazma_core.db import row_security
    from kazma_core.spaces import threads

    not_armed(monkeypatch)
    monkeypatch.setattr(threads, "thread_spaces", lambda: pytest.fail("no record is read when not told"))
    result = row_security.Applied()
    caplog.set_level(logging.WARNING, logger="kazma_core.db.row_security")
    row_security._backfill(ALIAS, object(), ["checkpoints"], result, 100, 1, 0.0)
    assert "could not be told now" in result.not_backfilled and "run it again" in result.not_backfilled
    assert result.not_backfilled in result.notes and result.backfilled == 0
    assert any("could not be told now" in r.getMessage() for r in caplog.records)
    assert {dsn for dsn, _ in identities["asked"]} == {ALIAS, OWN}, "by identity, not armed"
    # The control: told another database, the other message, said as before.
    identities[ALIAS] = ("system", "1111111111111111111", "kazma")
    other = row_security.Applied()
    row_security._backfill(ALIAS, object(), ["checkpoints"], other, 100, 1, 0.0)
    assert other.not_backfilled.startswith("checkpoints: not the database this install's own address names")


# ── the gate: one answer ────────────────────────────────────────────────

#: The one module that answers.
ANSWER = "kazma-core/kazma_core/db/availability.py"
#: SQL that reads which server a connection reached.
IDENTITY_SQL = ("pg_control_system", "pg_postmaster_start_time", "inet_server_addr", "inet_server_port")
#: The variable that holds Kazma's own address.
ADDRESS_VARIABLE = "KAZMA_DATABASE_URL"
#: The answer's own helpers.
HELPERS = {"_same_as_kazmas", "_database_of", "_server_of", "_read_server_identity", "server_identity"}
#: What parses an address to compare it.
PARSERS = {"conninfo_to_dict", "make_conninfo", "urlparse", "urlsplit", "_database_of", "_server_of"}
#: Functions that read parts of Kazma's own address for another question than
#: "is this database Kazma's", and what they read.
READS_ITS_ADDRESS = {
    "kazma-cli/kazma_cli/setup.py::_existing_app_password":
        "kazma_app's password in the install's address, when it names the local database setup makes",
    "kazma-core/kazma_core/backup/pg_move.py::_container_login":
        "the user and database name to sign in with inside Kazma's container",
    "kazma-cli/kazma_cli/setup.py::_setup_postgres":
        "setup writes the new address into .env only when the one there differs",
}


def _product() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                             "kazma-*/kazma_*/*.py", "scripts/*.py"], cwd=ROOT, capture_output=True, text=True,
                            check=True)
    return {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed.stdout.split()))}


def _called(node: ast.AST) -> set[str]:
    names: set[str] = set()
    for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
        func = call.func
        names.add(func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", ""))
    return names


def _names_the_variable(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == ADDRESS_VARIABLE


def _reads_kazmas_address(node: ast.AST) -> bool:
    """Whether *node* reads Kazma's own address: ``get_database_url()``, or
    the ``KAZMA_DATABASE_URL`` variable's value however it is read
    (``os.environ[...]``, ``.get``, ``os.getenv``, a ``.env`` reader). The
    variable's name alone (``name == "KAZMA_DATABASE_URL"``) is not its value."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and (
                (n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")) == "get_database_url"
                or any(_names_the_variable(a) for a in n.args)):
            return True
        if isinstance(n, ast.Subscript) and _names_the_variable(n.slice):
            return True
    return False


def _second_answers(sources: dict[str, str]) -> list[str]:
    """Every own-database decision outside the one answer: the identity read
    in SQL (the identifier, the start time, the server's address and port), a
    comparison with Kazma's own address, a function that reads Kazma's own
    address and parses an address to compare, or the answer's private helpers
    used elsewhere (step 5 delta review N3: the gate saw three of six planted)."""
    found: list[str] = []
    for rel, source in sources.items():
        if rel == ANSWER:
            continue
        found += [f"{rel}: reads a server's identity ({sql})" for sql in IDENTITY_SQL if sql in source]
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            where = f"{rel}::{node.name}"
            called = _called(node)
            compares = [c for c in ast.walk(node) if isinstance(c, ast.Compare)
                        and any(_reads_kazmas_address(side) for side in (c.left, *c.comparators))]
            if compares and where not in READS_ITS_ADDRESS:
                found.append(f"{where}: compares with Kazma's own address")
            elif _reads_kazmas_address(node) and called & PARSERS and where not in READS_ITS_ADDRESS:
                found.append(f"{where}: parses Kazma's own address to compare")
            for helper in sorted(called & HELPERS):
                found.append(f"{where}: uses {helper}")
    return sorted(set(found))


def test_kazmas_own_database_has_one_answer() -> None:
    sources = _product()
    assert ANSWER in sources and "def kazma_database" in sources[ANSWER]
    assert _second_answers(sources) == []


def test_every_function_let_read_kazmas_address_still_does() -> None:
    """No stale entry: each named function exists and still reads it."""
    sources = _product()
    for entry in READS_ITS_ADDRESS:
        rel, name = entry.split("::")
        funcs = [n for n in ast.walk(ast.parse(sources[rel]))
                 if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
        assert funcs and _reads_kazmas_address(funcs[0]), entry


def test_the_gate_sees_a_second_answer() -> None:
    planted = {
        "kazma-core/kazma_core/a.py": ("def ours(cur):\n"
                                       "    cur.execute('SELECT system_identifier FROM pg_control_system()')\n"),
        "kazma-core/kazma_core/b.py": ("def same(dsn):\n"
                                       "    from psycopg.conninfo import conninfo_to_dict\n"
                                       "    return (conninfo_to_dict(dsn)['host']\n"
                                       "            == conninfo_to_dict(get_database_url())['host'])\n"),
        "kazma-core/kazma_core/c.py": "def mine(dsn):\n    return availability._database_of(dsn)\n",
        # The delta review's three that the gate missed (N3).
        "kazma-core/kazma_core/d.py": "def equal(dsn):\n    return dsn == get_database_url()\n",
        "kazma-core/kazma_core/e.py": ("import os\n"
                                       "def parsed(dsn):\n"
                                       "    ours = conninfo_to_dict(os.environ['KAZMA_DATABASE_URL'])\n"
                                       "    return conninfo_to_dict(dsn)['host'] == ours['host']\n"),
        "kazma-core/kazma_core/f.py": ("def where(cur):\n"
                                       "    cur.execute('SELECT inet_server_addr(), inet_server_port(), '\n"
                                       "                'current_database()')\n"),
        "kazma-core/kazma_core/g.py": ("import os\n"
                                       "def env(dsn):\n"
                                       "    return dsn != os.getenv('KAZMA_DATABASE_URL', '')\n"),
        "kazma-core/kazma_core/fine.py": "def fine(dsn):\n    return availability.kazma_database(dsn)\n",
        "kazma-core/kazma_core/reads.py": ("import os\n"
                                           "def address():\n"
                                           "    return os.environ.get('KAZMA_DATABASE_URL') or get_database_url()\n"),
        "kazma-core/kazma_core/name.py": ("def named(line):\n"
                                          "    name, _, value = line.partition('=')\n"
                                          "    return name.strip() == 'KAZMA_DATABASE_URL'\n"),
    }
    assert _second_answers(planted) == [
        "kazma-core/kazma_core/a.py: reads a server's identity (pg_control_system)",
        "kazma-core/kazma_core/b.py::same: compares with Kazma's own address",
        "kazma-core/kazma_core/c.py::mine: uses _database_of",
        "kazma-core/kazma_core/d.py::equal: compares with Kazma's own address",
        "kazma-core/kazma_core/e.py::parsed: parses Kazma's own address to compare",
        "kazma-core/kazma_core/f.py: reads a server's identity (inet_server_addr)",
        "kazma-core/kazma_core/f.py: reads a server's identity (inet_server_port)",
        "kazma-core/kazma_core/g.py::env: compares with Kazma's own address",
    ], "reading Kazma's address (reads.py) or naming its variable (name.py) is no decision; each other form is one"
