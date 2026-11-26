"""The second lock's rules, held to the source (Spaces S4-01, step 2).

``kazma_core/db/row_security.py`` gives every table Kazma keeps on Postgres
one rule: a space's (a policy and the space role's rights), a narrow grant,
or the install's. These gates need no database:

* every table of ``pg_backup.KAZMA_PG_TABLES`` has a rule and every rule names
  one of them (both ways), and every ``CREATE TABLE`` a Postgres schema maker
  sends names a table with a rule (a table with no rule is "permission
  denied" for a space, and an unlisted one would be missed by the proof);
* the rights are the review's (M12) and the one form of a row's space (M6);
* ``deploy/postgres/kazma_app_role.sql`` grants exactly the rules' rights
  (review M11: the role, its membership and its grants live in that file);
* the policy function raises when no space is set, and is made on restores;
* the space connection's address keeps the address's own options (M10);
* with the rules not in place, the two places that make tables send nothing
  more than before (Spaces off unchanged).

Each gate has a negative control. Real Postgres: ``tests/test_pg_row_security.py``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
from kazma_core.db import row_security
from kazma_core.db.pg_backup import KAZMA_PG_FUNCTIONS, KAZMA_PG_TABLES

ROOT = Path(__file__).resolve().parents[1]
ROLE_SQL = ROOT / "deploy" / "postgres" / "kazma_app_role.sql"

#: ``CREATE TABLE`` texts in a Postgres schema maker's file that are not
#: Postgres tables of Kazma's, and why.
NOT_KAZMA_PG_TABLES: dict[str, str] = {
    "document_audit_gc_control": "documents/audit.py's SQLite schema (the prune's switch is a setting on Postgres)",
    "swarm_tasks": "swarm/task_store.py's SQLite schema",
    "swarm_worker_metrics": "swarm/task_store.py's SQLite schema",
    "kazma_memory_vectors": "memory/backends.py's docstring: pgvector's table is keyed by its configured name (M9)",
}

_CREATE = re.compile(r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+([A-Za-z_]\w*)\s*\(", re.I)
_MAKER = re.compile(r"from\s+kazma_core\.db\.pg_schema\s+import\s+[^\n]*\bensure_schema\b")


def _schema_makers() -> list[str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    return sorted(rel for rel in set(listed) if _MAKER.search((ROOT / rel).read_text(encoding="utf-8")))


def _unruled(source: str) -> set[str]:
    """Tables a schema maker's text creates that have no rule and are not declared."""
    return {name for name in _CREATE.findall(source)
            if name not in row_security.RULES and name not in NOT_KAZMA_PG_TABLES}


# ── every table has a rule ──────────────────────────────────────────────


def test_every_kazma_table_has_a_rule_both_ways() -> None:
    assert set(row_security.RULES) == set(KAZMA_PG_TABLES), (
        "db/row_security.RULES and pg_backup.KAZMA_PG_TABLES name the same tables: "
        f"no rule {sorted(set(KAZMA_PG_TABLES) - set(row_security.RULES))}, "
        f"no table {sorted(set(row_security.RULES) - set(KAZMA_PG_TABLES))}")


def test_every_table_a_schema_maker_creates_has_a_rule() -> None:
    makers = _schema_makers()
    assert "kazma-core/kazma_core/db/postgres_pool.py" in makers and len(makers) >= 7, makers
    found = {rel: _unruled((ROOT / rel).read_text(encoding="utf-8")) for rel in makers}
    assert not {rel: names for rel, names in found.items() if names}, (
        "a Postgres table with no rule in db/row_security.RULES (and pg_backup.KAZMA_PG_TABLES)")
    seen = {name for rel in makers for name in _CREATE.findall((ROOT / rel).read_text(encoding="utf-8"))}
    assert set(NOT_KAZMA_PG_TABLES) <= seen, "a declared exception no file creates any more: remove it"


def test_the_scan_sees_a_table_with_no_rule() -> None:
    # Negative control: a planted table.
    planted = 'ensure_schema(cur, ["CREATE TABLE IF NOT EXISTS kazma_planted (id TEXT)"])'
    assert _unruled(planted) == {"kazma_planted"}
    assert _MAKER.search("from kazma_core.db.pg_schema import SCHEMA_LOCK_KEY, ensure_schema")


def test_pgvector_is_ruled_under_its_configured_name() -> None:
    from kazma_core.memory.backends import pgvector_table

    cfg = {"vector": {"provider": "pgvector", "url": "postgresql://x/y", "collection": "my-vectors!"}}
    assert pgvector_table(cfg) == "myvectors"
    assert row_security.rules("myvectors")["myvectors"].kind == "space"
    assert pgvector_table({"vector": {"provider": "sqlite_vec", "collection": "x"}}) is None
    assert "myvectors" not in row_security.rules(None)


# ── the rules' content ──────────────────────────────────────────────────


def test_rights_are_the_reviews() -> None:
    rules = row_security.RULES
    for table, rule in rules.items():
        assert set(rule.rights) <= {"SELECT", "INSERT", "UPDATE", "DELETE"}, table
        if rule.kind == "install":
            assert not rule.rights and rule.why, table
        if rule.kind == "space":
            assert rule.space and rule.expression == f"COALESCE(NULLIF({rule.space}, ''), 'default')", table
    # M12: written once, read after; the intake's reservations are its own; blobs are shared.
    assert rules["document_audit_events"].rights == ("SELECT", "INSERT")
    assert rules["document_job_events"].rights == ("SELECT", "INSERT")
    assert rules["document_intake_reservations"].rights == ("SELECT",)
    assert "DELETE" not in rules["document_blobs"].rights
    assert rules["document_intake_mutex"].kind == "grant"
    assert rules["kazma_settings"].kind == "install"  # decision 4
    assert rules["document_audit_events"].own == ("system",)  # the GC's audit, named
    assert {rules[t].space for t in ("checkpoints", "checkpoint_blobs", "checkpoint_writes")} == {"space_id"}


def test_policies_are_to_public_and_call_the_raising_function() -> None:
    sql = row_security._policy_sql("kazma_chat_sessions", row_security.RULES["kazma_chat_sessions"])
    assert "TO PUBLIC" in sql and "FOR ALL" in sql and "(SELECT kazma_space_id())" in sql
    assert f"TO {row_security.SPACE_ROLE}" not in sql, "a policy that names the role makes dumps unrestorable elsewhere"
    assert sql.count("COALESCE(NULLIF(tenant_id, ''), 'default')") == 2  # USING and WITH CHECK


def test_a_stored_policy_reads_as_the_rule() -> None:
    rule = row_security.RULES["kazma_swarm_tasks"]
    stored = ("(COALESCE(NULLIF((metadata ->> 'tenant_id'::text), ''::text), 'default'::text) = "
              "( SELECT kazma_space_id() AS kazma_space_id))")  # PostgreSQL 18 prints it so
    assert row_security._same_expression(stored, rule)
    # Controls: another column, a widened match, nothing.
    assert not row_security._same_expression(stored.replace("tenant_id", "owner_id"), rule)
    assert not row_security._same_expression(stored[:-1] + " OR true)", rule)
    assert not row_security._same_expression(None, rule)


def test_the_policy_function_raises_with_no_space_and_is_restored() -> None:
    ddl = KAZMA_PG_FUNCTIONS["kazma_space_id"]
    assert "STABLE PARALLEL SAFE" in ddl and "SECURITY DEFINER" not in ddl
    assert "insufficient_privilege" in ddl and "space = ''" in ddl and "IS NULL" in ddl
    assert "current_setting('kazma.space', true)" in ddl
    assert row_security._body(ddl).startswith("DECLARE")


# ── the role file ───────────────────────────────────────────────────────


def _rights_block(text: str) -> str:
    match = re.search(r"-- BEGIN rights[^\n]*\n(.*?)\n\s*-- END rights", text, re.S)
    assert match, "the role file has its rights markers"
    return match.group(1)


def test_the_role_file_grants_the_rules_rights() -> None:
    text = ROLE_SQL.read_text(encoding="utf-8")
    assert _rights_block(text).replace("\r", "") == row_security._role_sql_rights(), (
        "deploy/postgres/kazma_app_role.sql: put row_security._role_sql_rights() between its rights markers")
    for needed in ("CREATE ROLE kazma_space NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                   "NOBYPASSRLS NOINHERIT", "WITH INHERIT FALSE, SET TRUE", "REVOKE %I FROM kazma_space",
                   "GRANT USAGE ON SCHEMA public TO kazma_space", "c.relrowsecurity"):
        assert needed in text, needed
    assert "ALTER DEFAULT PRIVILEGES" not in text, "a table added later must have no right until it has a rule"


def test_the_rights_gate_sees_a_changed_right() -> None:
    # Negative control: one right more in the file.
    text = ROLE_SQL.read_text(encoding="utf-8").replace(
        "('document_audit_events', 'space', 'SELECT, INSERT')",
        "('document_audit_events', 'space', 'SELECT, INSERT, DELETE')")
    assert _rights_block(text).replace("\r", "") != row_security._role_sql_rights()


# ── the space connection's address (decision 5, M10) ────────────────────


def test_the_space_address_keeps_the_addresss_own_options() -> None:
    conninfo_to_dict = pytest.importorskip("psycopg.conninfo").conninfo_to_dict

    url = "postgresql://kazma_app:pw@127.0.0.1:5432/kazma?options=-c%20search_path%3Dkazma"
    options = conninfo_to_dict(row_security._space_conninfo(url, environ={}))["options"]
    assert options == "-c search_path=kazma -c role=kazma_space"
    plain = "postgresql://kazma_app:pw@127.0.0.1:5432/kazma"
    assert conninfo_to_dict(row_security._space_conninfo(plain, environ={"PGOPTIONS": "-c statement_timeout=5s"}))[
        "options"] == "-c statement_timeout=5s -c role=kazma_space"
    # The role comes last, so an address that names another role is overruled.
    other = "postgresql://u@h/d?options=-c%20role%3Dkazma_app"
    assert conninfo_to_dict(row_security._space_conninfo(other, environ={}))["options"].endswith(
        "-c role=kazma_space")


def test_a_bare_role_option_would_drop_the_addresss_own() -> None:
    # Negative control: what M10 measured -- options= replaces the address's.
    conninfo = pytest.importorskip("psycopg.conninfo")
    conninfo_to_dict, make_conninfo = conninfo.conninfo_to_dict, conninfo.make_conninfo

    url = "postgresql://u@h/d?options=-c%20search_path%3Dkazma"
    assert conninfo_to_dict(make_conninfo(url, options="-c role=kazma_space"))["options"] == "-c role=kazma_space"


# ── with the rules not in place, nothing more is sent ───────────────────


class _Cursor:
    """A cursor that answers 'missing' to every catalog read and records all."""

    def __init__(self, policies: bool = False) -> None:
        self.sent: list[str] = []
        self._policies = policies
        self._last = ""

    def execute(self, sql: str, params: Any = None) -> None:
        self.sent.append(sql)
        self._last = sql

    def fetchone(self) -> Any:
        if "pg_policy" in self._last:
            return (self._policies,)
        if "to_regclass" in self._last and "IS NOT NULL" in self._last:
            return (False,)
        return None

    def fetchall(self) -> list[Any]:
        return []


def test_spaces_off_sends_the_same_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.db.pg_schema import ensure_schema

    monkeypatch.setattr(row_security, "_in_place", False)
    cur = _Cursor(policies=True)
    sent = ensure_schema(cur, ["CREATE TABLE IF NOT EXISTS kazma_chat_sessions (tenant_id TEXT)"])
    assert sent == ["CREATE TABLE IF NOT EXISTS kazma_chat_sessions (tenant_id TEXT)"]
    assert not [s for s in cur.sent if "pg_policy" in s or "kazma_space" in s or "ROW LEVEL" in s], cur.sent


def _no_settings_read() -> None:
    raise AssertionError("a table made inside a schema transaction reads no setting")


def test_rules_in_place_give_a_new_table_its_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    # Negative control of the one above: the same call once the rules are in place.
    from kazma_core.db.pg_schema import ensure_schema

    monkeypatch.setattr(row_security, "_in_place", True)
    monkeypatch.setattr(row_security, "_configured_vector_table", _no_settings_read)
    cur = _Cursor(policies=True)
    ensure_schema(cur, ["CREATE TABLE IF NOT EXISTS kazma_chat_sessions (tenant_id TEXT)"])
    assert any("ENABLE ROW LEVEL SECURITY" in s for s in cur.sent)
    assert any(s.startswith("CREATE POLICY kazma_space_rows") for s in cur.sent)
    # pgvector's table, under the name it gives (no settings read on this path).
    vectors = _Cursor(policies=True)
    ensure_schema(vectors, ["CREATE TABLE IF NOT EXISTS my_vectors (tenant_id TEXT)"], vector_table="my_vectors")
    assert any(s.startswith("CREATE POLICY kazma_space_rows ON public.\"my_vectors\"") for s in vectors.sent)
    unnamed = _Cursor(policies=True)
    ensure_schema(unnamed, ["CREATE TABLE IF NOT EXISTS my_vectors (tenant_id TEXT)"])
    assert not [s for s in unnamed.sent if "ROW LEVEL" in s], "a table with no rule gets none"
    # A database that holds no rule of Kazma's (another server's mirror) gets none.
    elsewhere = _Cursor(policies=False)
    ensure_schema(elsewhere, ["CREATE TABLE IF NOT EXISTS kazma_episodes (tenant_id TEXT)"])
    assert not [s for s in elsewhere.sent if "ROW LEVEL" in s]


def test_the_checkpointer_sends_nothing_more_with_spaces_off(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from kazma_core import checkpoints_pg

    def _never(url: str) -> list[str]:
        raise AssertionError("the rules are not in place: nothing may be sent")

    monkeypatch.setattr(row_security, "_in_place", False)
    monkeypatch.setattr(row_security, "after_checkpointer_setup", _never)
    asyncio.run(checkpoints_pg._rules_after_setup("postgresql://u@h/d"))
    pytest.importorskip("psycopg")  # the rules in place: Postgres is there
    called: list[str] = []
    monkeypatch.setattr(row_security, "_in_place", True)
    monkeypatch.setattr(row_security, "after_checkpointer_setup", lambda url: called.append(url) or [])
    asyncio.run(checkpoints_pg._rules_after_setup("postgresql://u@h/d"))
    assert called == ["postgresql://u@h/d"]
