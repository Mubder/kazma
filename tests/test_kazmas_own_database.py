"""One answer to "is this Kazma's own database?" (Spaces S4-01 step 5 review M1, §35).

The memory mirror and pgvector asked by address (host, port, database as
written); the checkpoints' backfill asked by server identity. Through another
name for the same database -- a ``hostaddr`` alias -- the mirror took it for
another, connected as the tables' owner, and read every space's rows once the
lock was armed. ``availability.kazma_database`` is now the one answer: the
same address at once, any other by the server identity of both (system
identifier and database name), kept once known.

The gate: nothing else reads a database's identity, parses Kazma's own
address to compare, or uses the answer's private helpers; each with a planted
control. Real Postgres: ``tests/test_pg_armed_flows.py`` (the mirror and
pgvector by another name, armed) and ``tests/test_pg_checkpoint_spaces.py``
(the backfill by another name).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import availability

ROOT = Path(__file__).resolve().parents[1]
OWN = "postgresql://kazma@127.0.0.1:1/kazma"  # nothing listens on port 1
ALIAS = "host=kazma-db.invalid hostaddr=127.0.0.1 port=1 dbname=kazma user=kazma"


@pytest.fixture()
def identities(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Kazma's own address, and each address's identity as a server would answer."""
    from kazma_core.db import backend

    known: dict[str, Any] = {OWN: ("7712779680233943083", "kazma")}
    asked: list[str] = []

    def identity(dsn: str) -> Any:
        asked.append(dsn)
        return known.get(dsn)

    monkeypatch.setattr(backend, "get_database_url", lambda: OWN)
    monkeypatch.setattr(availability, "server_identity", identity)
    known["asked"] = asked
    return known


def test_the_same_address_is_kazmas_with_nothing_read(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")  # an address is read with psycopg's conninfo
    assert availability.kazma_database(OWN) is True
    assert identities["asked"] == [], "no connection for the same address"


def test_another_name_for_kazmas_database_is_kazmas(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = identities[OWN]
    assert not availability._same_as_kazmas(ALIAS, availability._database_of), "the control: by address, another"
    assert availability.kazma_database(ALIAS) is True
    assert availability.kazma_database(ALIAS, or_its_server=True) is True


def test_another_database_on_kazmas_server_is_its_server_only(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = (identities[OWN][0], "other")
    assert availability.kazma_database(ALIAS) is False
    assert availability.kazma_database(ALIAS, or_its_server=True) is True


def test_another_server_is_neither(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    identities[ALIAS] = ("1111111111111111111", "kazma")
    assert availability.kazma_database(ALIAS) is False
    assert availability.kazma_database(ALIAS, or_its_server=True) is False


def test_an_identity_that_cannot_be_read_is_no_answer(identities: dict[str, Any]) -> None:
    """The server away, a refusal: unknown -- a caller keeps no answer and asks
    again; is_kazma_database reads it as not Kazma's."""
    pytest.importorskip("psycopg")
    assert availability.kazma_database(ALIAS) is None
    assert availability.is_kazma_database(ALIAS) is False


def test_an_unknown_answer_is_not_kept_by_pgvector(identities: dict[str, Any]) -> None:
    pytest.importorskip("psycopg")
    from kazma_core.memory.backends import PgvectorBackend

    vectors = PgvectorBackend(dsn=ALIAS)
    with pytest.raises(Exception):  # nothing listens there; the answer is what matters
        vectors._connect()
    assert vectors._kazmas_database is None, "unknown is asked again"
    identities[ALIAS] = identities[OWN]
    with pytest.raises(Exception):
        vectors._connect()
    assert vectors._kazmas_database is True, "known, kept"


# ── the gate: one answer ────────────────────────────────────────────────

#: The one module that answers.
ANSWER = "kazma-core/kazma_core/db/availability.py"
#: SQL that reads which server a connection reached.
IDENTITY_SQL = ("pg_control_system", "pg_postmaster_start_time")
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


def _second_answers(sources: dict[str, str]) -> list[str]:
    """Every own-database decision outside the one answer: the identity read
    in SQL, a function that takes Kazma's own address and parses an address to
    compare, or the answer's private helpers used elsewhere."""
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
            called = _called(node)
            if "get_database_url" in called and called & PARSERS and f"{rel}::{node.name}" not in READS_ITS_ADDRESS:
                found.append(f"{rel}::{node.name}: parses Kazma's own address to compare")
            for helper in sorted(called & HELPERS):
                found.append(f"{rel}::{node.name}: uses {helper}")
    return sorted(found)


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
        assert funcs and "get_database_url" in _called(funcs[0]), entry


def test_the_gate_sees_a_second_answer() -> None:
    planted = {
        "kazma-core/kazma_core/a.py": "def ours(cur):\n    cur.execute('SELECT system_identifier FROM pg_control_system()')\n",
        "kazma-core/kazma_core/b.py": ("def same(dsn):\n"
                                       "    from psycopg.conninfo import conninfo_to_dict\n"
                                       "    return conninfo_to_dict(dsn)['host'] == conninfo_to_dict(get_database_url())['host']\n"),
        "kazma-core/kazma_core/c.py": "def mine(dsn):\n    return availability._database_of(dsn)\n",
        "kazma-core/kazma_core/fine.py": "def fine(dsn):\n    return availability.kazma_database(dsn)\n",
    }
    assert _second_answers(planted) == [
        "kazma-core/kazma_core/a.py: reads a server's identity (pg_control_system)",
        "kazma-core/kazma_core/b.py::same: parses Kazma's own address to compare",
        "kazma-core/kazma_core/c.py::mine: uses _database_of",
    ]
