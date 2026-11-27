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
* PUBLIC may run no function of Kazma's: each one's DDL takes it away, and
  every copy restored without privileges takes it away after (step 2 review
  L3: a constrained SQL-tool reader refuses a database where it may run one);
* the space connection's address keeps the address's own options (M10);
* with the rules not in place, the two places that make tables send nothing
  more than before (Spaces off unchanged).

Each gate has a negative control. Real Postgres: ``tests/test_pg_row_security.py``.
"""

from __future__ import annotations

import ast
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


def _unmatched(rules: dict[str, Any], tables: list[str]) -> tuple[list[str], list[str]]:
    """Tables with no rule, and rules with no table."""
    return sorted(set(tables) - set(rules)), sorted(set(rules) - set(tables))


def test_every_kazma_table_has_a_rule_both_ways() -> None:
    no_rule, no_table = _unmatched(row_security.RULES, KAZMA_PG_TABLES)
    assert (no_rule, no_table) == ([], []), (
        "db/row_security.RULES and pg_backup.KAZMA_PG_TABLES name the same tables: "
        f"no rule {no_rule}, no table {no_table}")


def test_the_gate_sees_a_table_with_no_rule_and_a_rule_with_no_table() -> None:
    # Negative control (step 2 review L7): one of each, planted.
    rules = {**row_security.RULES, "kazma_gone": row_security.RULES["documents"]}
    assert _unmatched(rules, [*KAZMA_PG_TABLES, "kazma_new"]) == (["kazma_new"], ["kazma_gone"])


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
    # Step 2 review L1: Postgres folds the unquoted name it is made with.
    capitals = {"vector": {"provider": "pgvector", "url": "postgresql://x/y", "collection": "Kazma_Vectors"}}
    assert pgvector_table(capitals) == "kazma_vectors"
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
    assert "TO PUBLIC" in sql and "FOR ALL" in sql and "(SELECT public.kazma_space_id())" in sql
    assert f"TO {row_security.SPACE_ROLE}" not in sql, "a policy that names the role makes dumps unrestorable elsewhere"
    assert sql.count("COALESCE(NULLIF(tenant_id, ''), 'default')") == 2  # USING and WITH CHECK


def test_a_stored_policy_reads_as_the_rule() -> None:
    rule = row_security.RULES["kazma_swarm_tasks"]
    stored = ("(COALESCE(NULLIF((metadata ->> 'tenant_id'::text), ''::text), 'default'::text) = "
              "( SELECT kazma_space_id() AS kazma_space_id))")  # PostgreSQL 18 prints it so
    assert row_security._same_expression(stored, rule)
    # As a reader whose search path lacks public prints it (step 2 review L2).
    assert row_security._same_expression(stored.replace("SELECT kazma_space_id()", "SELECT public.kazma_space_id()"),
                                         rule)
    # Controls: another column, a widened match, nothing.
    assert not row_security._same_expression(stored.replace("tenant_id", "owner_id"), rule)
    assert not row_security._same_expression(stored[:-1] + " OR true)", rule)
    assert not row_security._same_expression(None, rule)


def test_the_policy_function_raises_with_no_space_and_is_restored() -> None:
    ddl = KAZMA_PG_FUNCTIONS["kazma_space_id"]
    assert "STABLE PARALLEL SAFE" in ddl and "SECURITY DEFINER" not in ddl
    assert "FUNCTION public.kazma_space_id()" in ddl, "schema-qualified (step 2 review L2)"
    assert "REVOKE EXECUTE ON FUNCTION public.kazma_space_id() FROM PUBLIC" in ddl, "step 2 review L3"
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
    options = conninfo_to_dict(row_security.space_conninfo(url, environ={}))["options"]
    assert options == "-c search_path=kazma -c role=kazma_space"
    plain = "postgresql://kazma_app:pw@127.0.0.1:5432/kazma"
    assert conninfo_to_dict(row_security.space_conninfo(plain, environ={"PGOPTIONS": "-c statement_timeout=5s"}))[
        "options"] == "-c statement_timeout=5s -c role=kazma_space"
    # The role comes last, so an address that names another role is overruled.
    other = "postgresql://u@h/d?options=-c%20role%3Dkazma_app"
    assert conninfo_to_dict(row_security.space_conninfo(other, environ={}))["options"].endswith(
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


# ── the script takes no address on its command line (step 2 review L4) ──


def _script() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("row_security_script", ROOT / "scripts" / "row_security.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_script_reads_its_address_from_a_named_variable(monkeypatch: pytest.MonkeyPatch,
                                                           capsys: pytest.CaptureFixture[str]) -> None:
    script = _script()
    monkeypatch.setenv("KAZMA_SCRATCH_URL", "sqlite:///nothing")
    assert script.main(["check", "--url-from", "KAZMA_SCRATCH_URL"]) == 1
    assert "not Postgres" in capsys.readouterr().err
    # An empty variable is named, not read as "not Postgres" (delta review N3).
    monkeypatch.setenv("KAZMA_SCRATCH_URL", "")
    assert script.main(["check", "--url-from", "KAZMA_SCRATCH_URL"]) == 1
    assert "KAZMA_SCRATCH_URL is not set, or empty" in capsys.readouterr().err
    # The control: an address on the command line is no option any more.
    with pytest.raises(SystemExit):
        script.main(["check", "--url", "postgresql://kazma:secret@db/kazma"])


def test_a_named_address_still_knows_this_installs_own_database(monkeypatch: pytest.MonkeyPatch) -> None:
    """Step 4 delta review N3: with ``--url-from``, this install's files were
    not read, so ``is_kazma_database`` took its own database for another and
    the backfill was skipped. The files hold its address; here a stand-in
    loads them (the test session's dotenv reads nothing, and its shield takes
    a database address back out of the environment)."""
    pytest.importorskip("psycopg")  # an address is read with psycopg's conninfo
    from kazma_core import env_files
    from kazma_core.db import backend
    from kazma_core.db.availability import is_kazma_database

    script = _script()
    own = "postgresql://kazma@db.example:5432/kazma"
    monkeypatch.setattr(backend, "get_database_url", lambda: None)  # nothing read yet
    monkeypatch.setenv("KAZMA_SCRATCH_URL", own)
    loaded: list[bool] = []

    def load(**_kw: Any) -> list[str]:
        loaded.append(True)
        monkeypatch.setattr(backend, "get_database_url", lambda: own)  # as this install's .env holds it
        return [".env"]

    monkeypatch.setattr(env_files, "load_env_files", load)
    assert not is_kazma_database(own), "the control: unread, the address is no install's"
    assert script._url("KAZMA_SCRATCH_URL") == own and loaded
    assert is_kazma_database(own), "read, it is this install's own"


@pytest.mark.parametrize("skipped", [True, False], ids=["not backfilled", "the control: backfilled"])
def test_a_backfill_that_did_not_run_is_said_loudly(monkeypatch: pytest.MonkeyPatch,
                                                    capsys: pytest.CaptureFixture[str], skipped: bool) -> None:
    """N3: a skipped backfill was one ``[NOTE]`` under "The rules are in
    place.", and arming on that database would leave old threads the owner's."""
    pytest.importorskip("psycopg")  # the script's apply reads Postgres
    script = _script()
    monkeypatch.setenv("KAZMA_SCRATCH_URL", "postgresql://kazma@elsewhere.example/kazma")
    done = row_security.Applied()
    if skipped:
        done.not_backfilled = "checkpoints: not this install's own database"
        done.notes.append(done.not_backfilled)
    monkeypatch.setattr(row_security, "apply", lambda url, **_kw: done)
    assert script.main(["apply", "--url-from", "KAZMA_SCRATCH_URL", "--yes"]) == 0
    out = capsys.readouterr().out
    assert ("NOT BACKFILLED" in out and "do not arm" in out and "[NOTE]" not in out) == skipped, out


@pytest.mark.parametrize("argv", [
    ["check", "--url", "postgresql://kazma:s3cret-pw@db/kazma"],
    ["check", "--url-from", "X", "postgresql://kazma:s3cret-pw@db/kazma"],
    ["postgresql://kazma:s3cret-pw@db/kazma"],  # typed where the action goes
])
def test_a_refused_argument_is_never_repeated_with_its_password(argv: list[str],
                                                                capsys: pytest.CaptureFixture[str]) -> None:
    """N3: argparse's own error echoed the refused value, password included."""
    with pytest.raises(SystemExit) as stopped:
        _script().main(argv)
    err = capsys.readouterr().err
    assert stopped.value.code == 2 and "error:" in err
    assert "s3cret-pw" not in err, err


def test_argparse_alone_would_repeat_it() -> None:
    # The control: the plain parser's message carries the value.
    import argparse

    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("action", choices=("check",))
    said: list[str] = []
    parser.error = said.append  # type: ignore[method-assign]
    parser.parse_args(["check", "--url", "postgresql://kazma:s3cret-pw@db/kazma"])
    assert said and "s3cret-pw" in said[0]


# ── PUBLIC may run no function of Kazma's (step 2 review L3) ────────────

#: Product files that restore with ``--no-privileges`` and need not take
#: PUBLIC's EXECUTE away after, and why.
RESTORES_KEEPING_FUNCTIONS: dict[str, str] = {
    "scripts/restore_rehearsal.py": "a scratch database, dropped when the rehearsal ends",
}


def _open_to_public(functions: dict[str, str]) -> list[str]:
    """Functions whose DDL does not take PUBLIC's EXECUTE away."""
    return sorted(name for name, ddl in functions.items()
                  if not re.search(rf"REVOKE\s+EXECUTE\s+ON\s+FUNCTION\s+(public\.)?{name}\(\)\s+FROM\s+PUBLIC\b",
                                   ddl))


def test_no_function_of_kazmas_may_be_run_by_public() -> None:
    assert _open_to_public(KAZMA_PG_FUNCTIONS) == [], (
        "CREATE FUNCTION gives PUBLIC EXECUTE: end the DDL with REVOKE EXECUTE ON FUNCTION <it> FROM PUBLIC")
    # Negative control: a function made as CREATE FUNCTION leaves it.
    planted = {**KAZMA_PG_FUNCTIONS,
               "kazma_new": "CREATE FUNCTION public.kazma_new() RETURNS int LANGUAGE sql AS $$ SELECT 1 $$"}
    assert _open_to_public(planted) == ["kazma_new"]


def _restores_without_privileges() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py",
         "scripts/*.py"], cwd=ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed))}
    return {rel: text for rel, text in texts.items() if "pg_restore" in text and "--no-privileges" in text}


def _not_closing(sources: dict[str, str]) -> list[str]:
    return sorted(rel for rel, text in sources.items()
                  if "revoke_public_execute" not in text and rel not in RESTORES_KEEPING_FUNCTIONS)


def test_every_copy_restored_without_privileges_takes_publics_execute_away() -> None:
    sources = _restores_without_privileges()
    assert _not_closing(sources) == [], (
        "a restore with --no-privileges makes Kazma's functions with PUBLIC's EXECUTE: call "
        "pg_backup.revoke_public_execute after it, or say why not in RESTORES_KEEPING_FUNCTIONS")
    assert {"kazma-core/kazma_core/postgres_upgrade.py", "kazma-core/kazma_core/backup/pg_move.py",
            "kazma-core/kazma_core/postgres_compose_upgrade.py",
            "kazma-core/kazma_core/migration/pg_bridge.py"} <= set(sources), "the gate reads the restores"
    stale = sorted(set(RESTORES_KEEPING_FUNCTIONS) - set(sources))
    assert not stale, f"no longer restores without privileges: take out of RESTORES_KEEPING_FUNCTIONS {stale}"
    # Negative control: a planted restore that leaves them.
    planted = {"kazma-core/kazma_core/planted.py": 'run(["pg_restore", "--no-owner", "--no-privileges", dump])'}
    assert _not_closing(planted) == ["kazma-core/kazma_core/planted.py"]


# ── who may declare a checkout inside a space one (step 3 delta review N2) ──

#: Every product function that declares an install checkout inside a space
#: one, and why none of the space transaction's locks is waited for.
INSTALL_INSIDE_A_SPACE: dict[str, str] = {
    "kazma-core/kazma_core/config_store.py::ConfigStore._pg_access":
        "the settings table: the space role has no right on it and holds none of its locks",
}
#: The same for a space checkout inside another: none.
SPACE_INSIDE_A_SPACE: dict[str, str] = {}

_DECLARES = ("install_inside_a_space", "space_inside_a_space")


def _declarers(sources: dict[str, str]) -> dict[str, set[str]]:
    """Each declaration's callers, ``path::function``."""
    found: dict[str, set[str]] = {name: set() for name in _DECLARES}
    for rel, text in sources.items():
        stack: list[str] = []

        class _Visit(ast.NodeVisitor):
            def _scope(self, node: Any) -> None:
                stack.append(node.name)
                self.generic_visit(node)
                stack.pop()

            visit_FunctionDef = visit_AsyncFunctionDef = visit_ClassDef = _scope  # noqa: N815 - ast's names

            def visit_Call(self, node: ast.Call) -> None:
                func = node.func
                name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
                if name in found:
                    found[name].add(f"{rel}::{'.'.join(stack) or '<module>'}")  # noqa: B023 - read while visiting
                self.generic_visit(node)

        _Visit().visit(ast.parse(text))
    return found


def _product_declaring() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "--",
                             "kazma-*/kazma_*/*.py"], cwd=ROOT, capture_output=True, text=True, check=True)
    texts = {rel: (ROOT / rel).read_text(encoding="utf-8") for rel in sorted(set(listed.stdout.split()))}
    return {rel: text for rel, text in texts.items() if "inside_a_space" in text}


def test_only_the_declared_functions_declare_a_checkout_inside_a_space() -> None:
    found = _declarers(_product_declaring())
    for name, declared in (("install_inside_a_space", INSTALL_INSIDE_A_SPACE),
                           ("space_inside_a_space", SPACE_INSIDE_A_SPACE)):
        assert found[name] == set(declared), (
            f"{name} lets a checkout wait inside a space transaction: declare each caller and why no lock "
            f"of the space's is waited for; undeclared {sorted(found[name] - set(declared))}, "
            f"stale {sorted(set(declared) - found[name])}")


def test_the_gate_sees_a_planted_declaration() -> None:
    # Negative control: a store that declares its own.
    planted = {"kazma-core/kazma_core/planted.py": (
        "from kazma_core.db import row_security\n"
        "class Store:\n"
        "    def read(self):\n"
        "        with row_security.install_inside_a_space('mine'):\n"
        "            return 1\n")}
    assert _declarers(planted)["install_inside_a_space"] == {"kazma-core/kazma_core/planted.py::Store.read"}
