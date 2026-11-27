"""The second lock's rules on a real Postgres (Spaces S4-01, step 2).

``kazma_core/db/row_security.py`` puts a policy on every space's table and
gives the role ``kazma_space`` the rights its rule names; the role file
(``deploy/postgres/kazma_app_role.sql``) makes the role. Nothing arms the
lock yet. These prove, on a private database seeded by every store's own
schema setup and owned by ``kazma_app`` as setup makes it:

* the role file makes ``kazma_space`` as the design wants it, and repairs a
  wrong one (a membership the wrong way round, an inheriting one);
* ``apply`` puts every rule in place, ``check`` then finds nothing, and a
  second ``apply`` sends nothing; each planted difference is found;
* on a connection as the space role, a statement with no filter reads,
  changes and deletes its own space's rows only, another space's row cannot
  be written, no space set raises, an empty space is the owner's (M6), the
  install's tables are refused, and the rights are the review's (M12) --
  each with the owner's view as the control;
* rows whose space is unknown are listed and the proof refuses them (M6);
* ``apply`` refuses a login that does not own the tables (the way back) and
  a missing role it may not make, and leaves a table a dump holds instead of
  waiting behind it (M15);
* a table made after the rules (H3) -- the memory mirror's, the
  checkpointer's -- gets its rule as it is made; with the rules not in place,
  it does not;
* dumps, the restore, the upgrade's and the move's whole copy and the restore
  rehearsal work with the rules, name no role, and ``apply`` gives the rights
  back after them;
* the step 2 review: rows and the proof read a database the rules never
  reached (M1); a superuser, and a PostgreSQL 16 creator holding the role
  without SET, are granted it as it must be, and a login that may not grant
  it is said, not raised (M2, L5); a function of another role is refused
  (L5); PUBLIC may run none of Kazma's functions, after a restore or the
  upgrade's whole copy too (L3);
* ``kazma_space_role_remove.sql`` takes it all out, and the owner reads on.

Needs a throwaway server and a superuser DSN (``KAZMA_DATABASE_URL`` +
``KAZMA_TEST_ALLOW_REAL_DB=1``); it makes ``kazma_app`` and ``kazma_space``
cluster-wide, so a server that already has ``kazma_app`` is refused.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.test_pg_app_role import SCRIPT, _every_store_set_up, _with

try:
    import psycopg
except ImportError:  # CI's light job has no psycopg: collected and skipped, so the counts agree
    psycopg = None

pytestmark = [pytest.mark.postgres, pytest.mark.skipif(psycopg is None, reason="psycopg is not installed")]

ROOT = Path(__file__).resolve().parents[1]
REMOVE = ROOT / "deploy" / "postgres" / "kazma_space_role_remove.sql"
KNOWN = {"default", "sa", "sb"}


def _sql(dsn: str, statement: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.execute(statement, params or None)
        return list(cur.fetchall()) if cur.description else []


@pytest.fixture(scope="module")
def db() -> Iterator[dict[str, str]]:
    """A private database seeded as setup leaves it: every store's tables,
    owned by ``kazma_app``, the role file run by the superuser."""
    base = os.environ.get("KAZMA_DATABASE_URL", "")
    if not base or os.environ.get("KAZMA_TEST_ALLOW_REAL_DB") != "1":
        pytest.skip("needs a throwaway Postgres (KAZMA_DATABASE_URL + KAZMA_TEST_ALLOW_REAL_DB=1)")
    with psycopg.connect(base, autocommit=True) as admin:
        if not admin.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user").fetchone()[0]:
            pytest.skip("making roles needs a superuser DSN")
        if admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_app'").fetchone():
            pytest.skip("kazma_app already exists on this server: not a throwaway one")
        had_space_role = bool(admin.execute("SELECT 1 FROM pg_roles WHERE rolname = 'kazma_space'").fetchone())
        name = f"kazma_rls_{uuid.uuid4().hex[:10]}"
        admin.execute(f'CREATE DATABASE "{name}"')
    su = _with(base, db=name)
    password = secrets.token_urlsafe(24)
    extra: list[str] = []
    try:
        _every_store_set_up(su)
        _sql(su, SCRIPT.read_text(encoding="utf-8"))
        _sql(su, f"ALTER ROLE kazma_app PASSWORD '{password}'")
        yield {"base": base, "su": su, "app": _with(base, user="kazma_app", password=password, db=name),
               "name": name, "password": password, "extra": extra}  # type: ignore[dict-item]
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            for other in [name, *extra]:
                admin.execute(f'DROP DATABASE IF EXISTS "{other}" WITH (FORCE)')
            for (scratch,) in admin.execute(
                    "SELECT datname FROM pg_database WHERE pg_get_userbyid(datdba) = 'kazma_app'").fetchall():
                admin.execute(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)')
            admin.execute("DROP ROLE IF EXISTS kazma_app")
            if not had_space_role:
                admin.execute("DROP ROLE IF EXISTS kazma_space")


@pytest.fixture()
def rs(db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> Any:
    """The module, with the rules in place in the private database and the
    process flag as a fresh process has it."""
    from kazma_core.db import row_security

    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)  # what a fresh process has; given back after
    done = row_security.apply(db["app"])
    assert done.complete, (done.refused, done.left, done.unresolved)
    row_security._mark_in_place(False)
    return row_security


def _new_database(db: dict[str, Any], *, owner: str = "kazma_app") -> str:
    name = f"kazma_rls_{uuid.uuid4().hex[:10]}"
    _sql(db["base"], f'CREATE DATABASE "{name}" OWNER {owner}')
    db["extra"].append(name)
    return name


def _space(db: dict[str, str]) -> Any:
    from kazma_core.db import row_security

    return psycopg.connect(row_security.space_conninfo(db["app"]), autocommit=False)


def _in(conn: Any, space: str | None, statement: str, params: tuple[Any, ...] = ()) -> Any:
    """*statement* in its own transaction on a space connection, in *space*
    (None: nothing set); rolled back. Returns the rows, or the row count."""
    try:
        with conn.cursor() as cur:
            if space is not None:
                cur.execute("SELECT set_config('kazma.space', %s, true)", (space,))
            cur.execute(statement, params)
            return cur.fetchall() if cur.description else cur.rowcount
    finally:
        conn.rollback()


def _seed(db: dict[str, str]) -> None:
    """Rows of two spaces and of the owner ('' and none), as the owner."""
    app = db["app"]
    _sql(app, "DELETE FROM kazma_chat_sessions; DELETE FROM kazma_swarm_tasks; DELETE FROM kazma_episodes; "
              "DELETE FROM checkpoints")
    _sql(app, "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('sa', 'c1'), ('sb', 'c2'), ('', 'c3')")
    _sql(app, "INSERT INTO kazma_swarm_tasks (id, type, prompt, status, created_at, metadata) VALUES "
              "('t1', 'x', 'p', 'done', 'now', '{\"tenant_id\": \"sa\"}'), "
              "('t2', 'x', 'p', 'done', 'now', '{\"tenant_id\": \"sb\"}'), ('t3', 'x', 'p', 'done', 'now', '{}')")
    _sql(app, "INSERT INTO kazma_episodes (id, tenant_id) VALUES ('e1', 'sa'), ('e2', 'sb')")
    with psycopg.connect(app) as conn:
        for space, thread in (("sa", "th-a"), ("sb", "th-b"), (None, "th-owner")):
            with conn.transaction():
                if space:
                    conn.execute("SELECT set_config('kazma.space', %s, true)", (space,))
                conn.execute("INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                             "VALUES (%s, '', 'k1', '{}', '{}')", (thread,))


# ── the role file ───────────────────────────────────────────────────────


def test_the_role_file_makes_the_space_role(db: dict[str, str]) -> None:
    su = db["su"]
    attrs = _sql(su, "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, rolinherit, "
                     "rolreplication FROM pg_roles WHERE rolname = 'kazma_space'")
    assert attrs == [(False,) * 7]
    assert _sql(su, "SELECT count(*) FROM pg_auth_members WHERE member = 'kazma_space'::regrole") == [(0,)]
    if int(_sql(su, "SHOW server_version_num")[0][0]) >= 160000:
        assert _sql(su, "SELECT inherit_option, set_option FROM pg_auth_members WHERE roleid = 'kazma_space'::regrole "
                        "AND member = 'kazma_app'::regrole") == [(False, True)]
    # Controls: a membership the wrong way round, and an inheriting one; the role file repairs both.
    from kazma_core.db import row_security

    def _role_findings() -> set[str]:
        return {f.problem for f in row_security.check(db["app"], vector_table="") if f.subject == "kazma_space"}

    # (Postgres refuses a circle, so the wrong way round is with the right way gone.)
    _sql(su, "REVOKE kazma_space FROM kazma_app")
    _sql(su, "GRANT kazma_app TO kazma_space")
    found = _role_findings()
    assert any("member of kazma_app" in p for p in found) and any("not a member" in p for p in found), found
    _sql(su, SCRIPT.read_text(encoding="utf-8"))
    _sql(su, SCRIPT.read_text(encoding="utf-8"))  # twice: safe to run again
    assert not _role_findings()
    if int(_sql(su, "SHOW server_version_num")[0][0]) >= 160000:
        _sql(su, "REVOKE kazma_space FROM kazma_app")
        _sql(su, "GRANT kazma_space TO kazma_app WITH INHERIT TRUE, SET TRUE")
        assert any("INHERIT" in p for p in _role_findings())
        _sql(su, SCRIPT.read_text(encoding="utf-8"))
        assert not _role_findings()


# ── apply and check ─────────────────────────────────────────────────────


def test_apply_puts_every_rule_in_place_once(rs: Any, db: dict[str, str]) -> None:
    assert rs.check(db["app"], vector_table="") == []
    again = rs.apply(db["app"], vector_table="")
    assert again.complete and again.changed == [], "a second pass sends nothing"
    policies = {r[0] for r in _sql(db["app"], "SELECT c.relname FROM pg_policy p JOIN pg_class c ON c.oid = p.polrelid "
                                              "WHERE p.polname = 'kazma_space_rows'")}
    assert policies == {t for t, r in rs.RULES.items() if r.kind == "space"}
    assert _sql(db["app"], "SELECT count(*) FROM pg_class WHERE relforcerowsecurity") == [(0,)], "never forced"
    assert rs.in_place()


def test_check_finds_each_planted_difference(rs: Any, db: dict[str, str]) -> None:
    app = db["app"]
    plants = [
        ("CREATE TABLE kazma_planted (id TEXT); GRANT SELECT ON kazma_planted TO PUBLIC", "kazma_planted",
         "open to the space role", "DROP TABLE kazma_planted"),
        ("ALTER POLICY kazma_space_rows ON kazma_episodes TO kazma_space", "kazma_episodes", "not the rule's", ""),
        ("CREATE POLICY wide ON documents FOR SELECT USING (true)", "documents", "not Kazma's",
         "DROP POLICY wide ON documents"),
        ("ALTER TABLE document_acl FORCE ROW LEVEL SECURITY", "document_acl", "forced", ""),
        ("ALTER DEFAULT PRIVILEGES GRANT SELECT ON TABLES TO PUBLIC", "default privileges", "open to the space role",
         "ALTER DEFAULT PRIVILEGES REVOKE SELECT ON TABLES FROM PUBLIC"),
        ("CREATE VIEW kazma_every_chat AS SELECT * FROM kazma_chat_sessions", "kazma_every_chat", "view",
         "DROP VIEW kazma_every_chat"),
        ("CREATE FUNCTION kazma_peek() RETURNS bigint LANGUAGE sql SECURITY DEFINER AS "
         "'SELECT count(*) FROM kazma_chat_sessions'", "kazma_peek()", "SECURITY DEFINER", "DROP FUNCTION kazma_peek()"),
        ("GRANT CREATE ON SCHEMA public TO PUBLIC", "schema public", "may create", "REVOKE CREATE ON SCHEMA public FROM PUBLIC"),
        ("GRANT TRUNCATE ON kazma_beliefs TO kazma_space", "kazma_beliefs", "TRUNCATE", ""),
    ]
    for plant, subject, words, undo in plants:
        _sql(app, plant)
        found = [f for f in rs.check(app, vector_table="") if f.subject == subject and f.blocking]
        assert any(words in f.problem for f in found), (plant, found)
        if undo:
            _sql(app, undo)
    rs.apply(app, vector_table="")
    assert rs.check(app, vector_table="") == []


# ── a space connection ──────────────────────────────────────────────────


def test_a_space_reads_and_changes_its_own_rows_only(rs: Any, db: dict[str, str]) -> None:
    _seed(db)
    with _space(db) as conn:
        assert _in(conn, "sa", "SELECT current_user")[0][0] == "kazma_space"
        assert _in(conn, "sa", "SELECT session_id FROM kazma_chat_sessions") == [("c1",)]
        assert _in(conn, "sa", "SELECT id FROM kazma_swarm_tasks") == [("t1",)]
        assert _in(conn, "sa", "SELECT thread_id FROM checkpoints") == [("th-a",)]
        assert _in(conn, "sa", "SELECT id FROM kazma_episodes") == [("e1",)]
        # The owner's: an empty or missing space (M6), and a thread written with none set.
        assert sorted(_in(conn, "default", "SELECT session_id FROM kazma_chat_sessions")) == [("c3",)]
        assert _in(conn, "default", "SELECT id FROM kazma_swarm_tasks") == [("t3",)]
        assert _in(conn, "default", "SELECT thread_id FROM checkpoints") == [("th-owner",)]
        # No WHERE at all: its own rows only.
        assert _in(conn, "sa", "UPDATE kazma_chat_sessions SET title = 'x'") == 1
        assert _in(conn, "sb", "DELETE FROM kazma_episodes") == 1
        assert _in(conn, "sa", "DELETE FROM checkpoints") == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, "sa", "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('sb', 'planted')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, "sa", "UPDATE kazma_episodes SET tenant_id = 'sb'")
    # The control: the owner, as Kazma signs in today, sees and changes every row.
    assert _sql(db["app"], "SELECT count(*) FROM kazma_chat_sessions") == [(3,)]
    assert _sql(db["app"], "SELECT count(*) FROM checkpoints") == [(3,)]


def test_a_thread_is_stamped_with_its_transactions_space(rs: Any, db: dict[str, str]) -> None:
    _seed(db)
    assert sorted(_sql(db["app"], "SELECT thread_id, space_id FROM checkpoints"), key=str) == sorted(
        [("th-a", "sa"), ("th-b", "sb"), ("th-owner", None)], key=str)


def test_no_space_set_raises(rs: Any, db: dict[str, str]) -> None:
    _seed(db)
    with _space(db) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, None, "SELECT * FROM kazma_chat_sessions")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, None, "SELECT kazma_space_id()")
        # After a transaction that set a space, the setting reads '': still none.
        assert _in(conn, "sa", "SELECT count(*) FROM kazma_chat_sessions") == [(1,)]
        assert _in(conn, None, "SELECT current_setting('kazma.space', true)") == [("",)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, None, "SELECT * FROM kazma_chat_sessions")


def test_the_install_tables_are_refused_and_rights_are_narrow(rs: Any, db: dict[str, str]) -> None:
    with _space(db) as conn:
        for table in ("kazma_settings", "kazma_platform_users", "kazma_web_sessions", "checkpoint_migrations"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _in(conn, "sa", f"SELECT 1 FROM {table} LIMIT 0")  # noqa: S608
        assert _in(conn, "sa", "SELECT id FROM document_intake_mutex") == [(1,)]
        assert _in(conn, "sa", "UPDATE document_intake_mutex SET id = id") == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, "sa", "DELETE FROM document_intake_mutex")
        assert _in(conn, "sa", "INSERT INTO document_audit_events (tenant_id, event_type, action, outcome, created_at) "
                               "VALUES ('sa', 'e', 'a', 'success', 'now')") == 1
        for statement in ("DELETE FROM document_audit_events", "UPDATE document_job_events SET tenant_id = 'sa'",
                          "DELETE FROM document_blobs", "DELETE FROM document_intake_reservations",
                          "TRUNCATE kazma_chat_sessions"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                _in(conn, "sa", statement)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            _in(conn, "sa", "CREATE TABLE kazma_space_made (id int)")


# ── every row's space is known (M6) ─────────────────────────────────────


def test_rows_of_unknown_spaces_are_listed_and_refused(rs: Any, db: dict[str, str]) -> None:
    app = db["app"]
    _seed(db)
    assert rs.space_rows(app, KNOWN, vector_table="") == []
    assert rs.prove(app, KNOWN, vector_table="").holds
    _sql(app, "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('ghost', 'g1')")
    _sql(app, "INSERT INTO kazma_beliefs (id, tenant_id) VALUES ('b1', 'telegram:42')")
    _sql(app, "INSERT INTO document_audit_events (tenant_id, event_type, action, outcome, created_at) "
              "VALUES ('system', 'gc', 'prune', 'success', 'now')")
    try:
        strays = {(s.table, s.space, s.why) for s in rs.space_rows(app, KNOWN, vector_table="")}
        assert strays == {("kazma_chat_sessions", "ghost", "a space the registry does not know"),
                          ("kazma_beliefs", "telegram:42", "not a space id")}
        proof = rs.prove(app, KNOWN, vector_table="")
        assert not proof.holds and len(proof.strays) == 2 and not proof.failed
    finally:
        _sql(app, "DELETE FROM kazma_chat_sessions WHERE tenant_id = 'ghost'")
        _sql(app, "DELETE FROM kazma_beliefs WHERE id = 'b1'")


# ── what the owner is shown ─────────────────────────────────────────────


def test_the_migrate_check_and_doctor_name_the_second_lock(rs: Any, db: dict[str, str],
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_cli import doctor
    from kazma_core.spaces import identity, preconditions

    monkeypatch.setenv("KAZMA_DATABASE_URL", db["app"])
    monkeypatch.setenv("KAZMA_DB_BACKEND", "postgres")
    monkeypatch.setattr(identity, "enforcement_enabled", lambda: False)
    note = " ".join(preconditions._second_lock_note())
    assert "is not armed" in note and "needs the role" not in note, note
    rows = doctor._second_lock_rows()
    assert len(rows) == 1, "one line while nothing is armed (step 2 review L6)"
    assert "every Kazma table holds its rule" in rows[0][1] and "Spaces is off" in rows[0][1], rows
    assert {status for status, _line in doctor._second_lock_rows()} == {doctor.INFO}, "shown, never counted"
    _sql(db["su"], "ALTER ROLE kazma_space RENAME TO kazma_space_away")
    try:
        note = " ".join(preconditions._second_lock_note())
        assert "needs the role kazma_space" in note and "kazma_app_role.sql" in note, note
        assert "does not exist" in " ".join(line for _status, line in doctor._second_lock_rows())
    finally:
        _sql(db["su"], "ALTER ROLE kazma_space_away RENAME TO kazma_space")


# ── apply refuses, and does not wait ───────────────────────────────────


def test_apply_refuses_a_login_that_does_not_own_the_tables(rs: Any, db: dict[str, str]) -> None:
    refused = rs.apply(db["su"], vector_table="")
    assert "does not own" in refused.refused and not refused.changed
    _sql(db["su"], "ALTER ROLE kazma_space RENAME TO kazma_space_away")
    try:
        refused = rs.apply(db["app"], vector_table="")
        assert "does not exist" in refused.refused and not refused.changed
        assert not rs.in_place()
    finally:
        _sql(db["su"], "ALTER ROLE kazma_space_away RENAME TO kazma_space")


def test_a_table_a_dump_holds_is_left_not_waited_for(rs: Any, db: dict[str, str]) -> None:
    app = db["app"]
    _sql(app, "DROP POLICY kazma_space_rows ON documents; ALTER TABLE documents DISABLE ROW LEVEL SECURITY")
    holder = psycopg.connect(app)
    try:
        holder.execute("SELECT 1 FROM documents LIMIT 1")  # a dump's lock: ACCESS SHARE, held
        began = time.monotonic()
        done = rs.apply(app, vector_table="", lock_timeout_ms=300, tries=2, budget_s=5.0)
        took = time.monotonic() - began
        assert done.left == ["documents"] and not done.refused, done
        assert took < 3.0, f"apply waited {took:.1f}s behind the dump"
    finally:
        holder.rollback()
        holder.close()
    assert rs.apply(app, vector_table="").complete
    assert rs.check(app, vector_table="") == []


def test_a_column_is_never_added_with_a_space_set(rs: Any, db: dict[str, str]) -> None:
    app = db["app"]
    _sql(app, "DROP POLICY kazma_space_rows ON checkpoint_writes; ALTER TABLE checkpoint_writes DROP COLUMN space_id")
    with psycopg.connect(app) as conn, conn.transaction(), conn.cursor() as cur:
        cur.execute("SELECT set_config('kazma.space', 'sa', true)")
        with pytest.raises(RuntimeError, match="added with none"):
            rs._apply_table(cur, "checkpoint_writes", rs.RULES["checkpoint_writes"])
    assert rs.apply(app, vector_table="").complete


# ── tables made later (H3) ──────────────────────────────────────────────


def _mirror_ensure(dsn: str) -> None:
    from kazma_core.memory.state_backend import PostgresStateBackend

    mirror = PostgresStateBackend(dsn)
    try:
        mirror._ensure()
    finally:
        mirror.close()


def test_a_table_made_later_gets_its_rule(rs: Any, db: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    app = db["app"]

    def _mirror_findings() -> list[Any]:
        return [f for f in rs.check(app, vector_table="") if f.subject in ("kazma_episodes", "kazma_beliefs")]

    # The rules not in place in this process: the tables are made as today.
    _sql(app, "DROP TABLE kazma_episodes, kazma_beliefs")
    _mirror_ensure(app)
    assert any("policy is missing" in f.problem for f in _mirror_findings())
    # In place: made with their rule, in the same transaction.
    _sql(app, "DROP TABLE kazma_episodes, kazma_beliefs")
    monkeypatch.setattr(rs, "_in_place", True)
    _mirror_ensure(app)
    assert _mirror_findings() == []
    # A database that holds no rule of Kazma's (a mirror on another server) gets none.
    other = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    _mirror_ensure(other)
    assert _sql(other, "SELECT count(*) FROM pg_policy") == [(0,)]


def test_the_checkpointer_tables_get_their_rules_after_setup(rs: Any, db: dict[str, str],
                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    from kazma_core.checkpoints_pg import close_postgres_checkpointer, open_postgres_checkpointer

    app = db["app"]

    def _open() -> None:
        async def _go() -> None:
            await close_postgres_checkpointer(await open_postgres_checkpointer(app, max_size=2))

        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
            runner.run(_go())

    drop = "DROP TABLE checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations"
    _sql(app, drop)
    _open()
    assert _sql(app, "SELECT count(*) FROM pg_attribute WHERE attrelid = 'checkpoints'::regclass "
                     "AND attname = 'space_id'") == [(0,)], "not in place: made as today"
    _sql(app, drop)
    monkeypatch.setattr(rs, "_in_place", True)
    _open()
    assert not [f for f in rs.check(app, vector_table="") if f.subject.startswith("checkpoint")]


# ── dumps, restores, the upgrade, the move, the rehearsal ───────────────


def _tools() -> str:
    found = shutil.which("pg_dump")
    if not found:
        pytest.skip("pg_dump is not on PATH")
    return str(Path(found).parent)


#: Whether PUBLIC may run each of Kazma's functions.
_PUBLIC_RUNS = ("SELECT has_function_privilege('public', 'public.kazma_space_id()', 'EXECUTE'), "
                "has_function_privilege('public', 'public.kazma_document_audit_guard()', 'EXECUTE')")


def _counts(dsn: str, tables: list[str]) -> dict[str, int]:
    return {t: _sql(dsn, f"SELECT count(*) FROM {t}")[0][0] for t in tables}  # noqa: S608


def test_a_dump_by_table_restores_with_its_policies(rs: Any, db: dict[str, str], tmp_path: Path) -> None:
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.migration.pg_bridge import dump_database, restore_database

    bin_dir = _tools()
    _seed(db)
    app = db["app"]
    dump = dump_database(app, tmp_path / "kazma.dump", tables=KAZMA_PG_TABLES)
    listing = subprocess.run([str(Path(bin_dir) / "pg_restore"), "-f", "-", str(dump)], capture_output=True,
                             text=True, check=True).stdout
    assert "CREATE POLICY kazma_space_rows" in listing
    assert "TO kazma_space" not in listing and "GRANT " not in listing, "the dump names no role"
    target = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    restore_database(dump, target, strict=True)  # the function is made first
    assert _sql(target, _PUBLIC_RUNS) == [(False, False)], "a restore makes functions PUBLIC may not run (L3)"
    present = [t for t in KAZMA_PG_TABLES if _sql(app, "SELECT to_regclass(%s) IS NOT NULL", (t,))[0][0]]
    assert _counts(target, present) == _counts(app, present)
    assert _sql(target, "SELECT count(*) FROM pg_policy WHERE polname = 'kazma_space_rows'") == \
        _sql(app, "SELECT count(*) FROM pg_policy WHERE polname = 'kazma_space_rows'")
    # Grants are a restore's loss; apply gives them back.
    assert any("may do nothing" in f.problem for f in rs.check(target, vector_table=""))
    assert rs.apply(target, vector_table="").complete and rs.check(target, vector_table="") == []
    # The control: a strict restore without the policy's function fails (the
    # trigger's is made, so the policy is what stops it).
    from kazma_core.db.pg_backup import KAZMA_PG_FUNCTIONS

    bare = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    _sql(bare, KAZMA_PG_FUNCTIONS["kazma_document_audit_guard"])
    failed = subprocess.run(
        [str(Path(bin_dir) / "pg_restore"), "--no-owner", "--no-privileges", "--single-transaction",
         "--exit-on-error", "-d", bare, str(dump)],
        capture_output=True, text=True, check=False)
    assert failed.returncode != 0 and "kazma_space_id" in failed.stderr, failed.stderr[-300:]


def test_the_upgrades_and_the_moves_whole_copy_keeps_the_rules(rs: Any, db: dict[str, str]) -> None:
    from kazma_core.postgres_upgrade import copy_database

    bin_dir = _tools()
    _seed(db)
    target = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    said: list[str] = []
    assert copy_database(db["app"], target, bin_dir=bin_dir, say=said.append), said
    # Restored without privileges, they had PUBLIC's default (step 2 review L3).
    assert _sql(target, _PUBLIC_RUNS) == [(False, False)], said
    assert rs.apply(target, vector_table="").complete
    assert rs.check(target, vector_table="") == []
    assert rs.prove(target, KNOWN, vector_table="").holds


def test_the_restore_rehearsal_passes_with_the_rules(rs: Any, db: dict[str, str], tmp_path: Path) -> None:
    from kazma_core.backup.restore_drill import DrillResult
    from kazma_core.backup.restore_rehearsal import rehearse_pg_restore
    from kazma_core.db.pg_backup import KAZMA_PG_TABLES
    from kazma_core.migration.pg_bridge import dump_database

    _tools()
    _sql(db["app"], "INSERT INTO kazma_settings (key, value, category) VALUES ('drill.rules', '1', 'drill') "
                    "ON CONFLICT (key) DO NOTHING")
    dump = dump_database(db["app"], tmp_path / "kazma.dump", tables=KAZMA_PG_TABLES)
    res = DrillResult(backup_dir=str(tmp_path))
    rehearse_pg_restore(dump, db["app"], res)
    assert res.ok, res.checks


# ── the step 2 review ────────────────────────────────────────────────────


def test_rows_and_the_proof_read_a_database_the_rules_never_reached(db: dict[str, Any],
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """M1: before any apply the checkpoints have no space column, and the
    check the owner was to run on live raised UndefinedColumn."""
    from kazma_core.db import row_security

    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)
    fresh = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    _every_store_set_up(fresh)
    assert _sql(fresh, "SELECT count(*) FROM pg_attribute WHERE attrelid = 'checkpoints'::regclass "
                       "AND attname = 'space_id'") == [(0,)], "a database the rules never reached"
    _sql(fresh, "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
                "VALUES ('th', '', 'k1', '{}', '{}')")
    _sql(fresh, "INSERT INTO kazma_chat_sessions (tenant_id, session_id) VALUES ('ghost', 'g1')")
    strays = {(s.table, s.space) for s in row_security.space_rows(fresh, KNOWN, vector_table="")}
    assert strays == {("kazma_chat_sessions", "ghost")}, "the checkpoint, with no space column yet, is the owner's"
    proof = row_security.prove(fresh, KNOWN, vector_table="")
    assert not proof.holds and proof.failed, "said, not raised"


def _drop_login(base: str, login: str) -> None:
    """A test's own login, and every membership it holds or granted."""
    _sql(base, "DO $$ DECLARE r record; BEGIN "
               "FOR r IN SELECT b.rolname AS role, g.rolname AS grantor FROM pg_auth_members m "
               "JOIN pg_roles b ON b.oid = m.roleid JOIN pg_roles g ON g.oid = m.grantor "
               f"WHERE m.member = '{login}'::regrole ORDER BY m.grantor = '{login}'::regrole DESC LOOP "
               f"EXECUTE format('REVOKE %I FROM {login} GRANTED BY %I CASCADE', r.role, r.grantor); "
               "END LOOP; END $$")
    _sql(base, f"DROP ROLE IF EXISTS {login}")


def _creator(db: dict[str, Any]) -> tuple[str, str, str]:
    """A login that may make roles (not a superuser), owning a new database
    every store has set up: ``(login, address, database)``."""
    login, password = f"kazma_c_{uuid.uuid4().hex[:8]}", secrets.token_urlsafe(16)
    _sql(db["base"], f"CREATE ROLE {login} LOGIN CREATEROLE PASSWORD '{password}'")
    name = f"kazma_rls_{uuid.uuid4().hex[:10]}"
    _sql(db["base"], f'CREATE DATABASE "{name}" OWNER {login}')
    dsn = _with(db["base"], user=login, password=password, db=name)
    _every_store_set_up(dsn)
    return login, dsn, name


def _membership(base: str, login: str) -> list[tuple[Any, ...]]:
    return _sql(base, "SELECT inherit_option, set_option FROM pg_auth_members "
                      "WHERE roleid = 'kazma_space'::regrole AND member = %s::regrole ORDER BY 2", (login,))


def _pg16(db: dict[str, Any]) -> None:
    if int(_sql(db["base"], "SHOW server_version_num")[0][0]) < 160000:
        pytest.skip("membership with SET and INHERIT options is PostgreSQL 16's")


def test_a_superuser_that_owns_the_tables_holds_the_role_as_it_must(db: dict[str, Any],
                                                                      monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: the compose install signs in as the superuser. ``pg_has_role``
    says yes for a superuser, so apply granted nothing and its own check and
    proof failed for good."""
    from kazma_core.db import row_security

    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)
    name = f"kazma_rls_{uuid.uuid4().hex[:10]}"
    _sql(db["base"], f'CREATE DATABASE "{name}"')
    db["extra"].append(name)
    su = _with(db["base"], db=name)
    _every_store_set_up(su)
    login = _sql(su, "SELECT session_user::text")[0][0]
    try:
        done = row_security.apply(su, vector_table="")
        assert done.complete, done
        assert not [f for f in row_security.check(su, vector_table="") if f.subject == "kazma_space"]
        assert row_security.prove(su, KNOWN, vector_table="").holds
    finally:
        _sql(db["base"], f'REVOKE kazma_space FROM "{login}"')  # the role is the server's: as it was found


def test_a_creator_that_holds_the_role_without_set_is_granted_it(db: dict[str, Any],
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    """M2: on PostgreSQL 16 a CREATEROLE login holds the role it made with
    ADMIN and neither INHERIT nor SET. ``pg_has_role`` says member, so apply
    granted nothing and could never be complete."""
    from kazma_core.db import row_security

    _pg16(db)
    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)
    login, dsn, name = _creator(db)
    try:
        _sql(db["base"], f"GRANT kazma_space TO {login} WITH ADMIN OPTION, INHERIT FALSE, SET FALSE")
        assert _sql(db["base"], "SELECT pg_has_role(%s, 'kazma_space', 'MEMBER')", (login,)) == [(True,)]
        done = row_security.apply(dsn, vector_table="")
        assert done.complete, done
        assert (False, True) in _membership(db["base"], login), "granted explicitly: INHERIT FALSE, SET TRUE"
        assert not [f for f in row_security.check(dsn, vector_table="") if f.subject == "kazma_space"]
        assert row_security.prove(dsn, KNOWN, vector_table="").holds
    finally:
        _sql(db["base"], f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        _drop_login(db["base"], login)


def test_a_login_that_may_not_grant_the_role_is_said_not_raised(db: dict[str, Any],
                                                               monkeypatch: pytest.MonkeyPatch) -> None:
    """M2, L5: a CREATEROLE login with no ADMIN on the role the superuser
    made may not grant it on PostgreSQL 16: apply says the role is not in
    place and is not complete, and raises nothing."""
    from kazma_core.db import row_security

    _pg16(db)
    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)
    login, dsn, name = _creator(db)
    try:
        done = row_security.apply(dsn, vector_table="")
        assert not done.complete and not done.refused, done
        assert any("not a member" in problem for problem in done.unresolved), done.unresolved
        assert not row_security.prove(dsn, KNOWN, vector_table="").holds
    finally:
        _sql(db["base"], f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        _drop_login(db["base"], login)


def test_a_function_of_another_role_is_refused_not_raised(db: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """L5: CREATE OR REPLACE of a function another role owns raised."""
    from kazma_core.db import row_security

    monkeypatch.setattr(row_security, "_configured_vector_table", lambda: None)
    monkeypatch.setattr(row_security, "_in_place", False)
    name = _new_database(db)
    fresh = _with(db["base"], user="kazma_app", password=db["password"], db=name)
    _every_store_set_up(fresh)
    _sql(_with(db["base"], db=name), "CREATE FUNCTION public.kazma_space_id() RETURNS text LANGUAGE sql STABLE "
                                     "AS $$ SELECT 'x' $$")  # the superuser's, and not Kazma's
    done = row_security.apply(fresh, vector_table="")
    assert "belongs to another role" in done.refused and not done.complete


def test_public_may_not_run_the_function_and_the_space_role_may(rs: Any, db: dict[str, Any]) -> None:
    """L3: a reader the owner set up to run no function in Kazma's database
    was refused after the next restore made one PUBLIC could run."""
    app = db["app"]
    fn = "public.kazma_space_id()"
    assert _sql(app, _PUBLIC_RUNS) == [(False, False)]
    assert _sql(app, "SELECT has_function_privilege('kazma_space', %s, 'EXECUTE')", (fn,)) == [(True,)]
    # The control: PUBLIC given them back is said, and apply takes it away again.
    _sql(app, "GRANT EXECUTE ON FUNCTION public.kazma_space_id(), public.kazma_document_audit_guard() TO PUBLIC")
    assert {f.subject for f in rs.check(app, vector_table="") if "PUBLIC may run" in f.problem} == \
        {fn, "public.kazma_document_audit_guard()"}
    done = rs.apply(app, vector_table="")
    assert done.complete and f"PUBLIC may no longer run {fn}" in done.changed, done
    assert _sql(app, _PUBLIC_RUNS) == [(False, False)]
    assert _sql(app, "SELECT has_function_privilege('kazma_space', %s, 'EXECUTE')", (fn,)) == [(True,)]


# ── the delta review of the step 2 review ───────────────────────────────


def test_a_bundles_whole_dump_restores_functions_public_may_not_run(rs: Any, db: dict[str, Any], tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    """N1: the migration bundle dumps the whole database and its import
    restores with --clean --no-privileges: the functions came back with
    PUBLIC's EXECUTE."""
    from kazma_core.migration.pg_bridge import dump_database, restore_database

    _tools()
    dump = dump_database(db["app"], tmp_path / "whole.dump")  # no tables named: the bundle's
    target = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    restore_database(dump, target)
    assert _sql(target, _PUBLIC_RUNS) == [(False, False)]
    # The control: the same restore with the take-away left out.
    from kazma_core.migration import pg_bridge

    bare = _with(db["base"], user="kazma_app", password=db["password"], db=_new_database(db))
    monkeypatch.setattr(pg_bridge, "_close_kazma_functions", lambda dsn, progress=None: None)
    restore_database(dump, bare)
    assert _sql(bare, _PUBLIC_RUNS) != [(False, False)], "the restore made them PUBLIC's again"


def test_taking_publics_execute_away_says_when_it_could_not(rs: Any, db: dict[str, Any]) -> None:
    """N4: a REVOKE by a login that does not own the function only warns, and
    the take-away said it was done."""
    from kazma_core.db.pg_backup import revoke_public_execute

    name = _new_database(db)
    fresh = _with(db["base"], user="kazma_app", password=db["password"], db=name)
    _sql(_with(db["base"], db=name), "CREATE FUNCTION public.kazma_space_id() RETURNS text LANGUAGE sql STABLE "
                                     "AS $$ SELECT 'x' $$")  # the superuser's: PUBLIC may run it
    assert revoke_public_execute(fresh) == "InsufficientPrivilege"
    assert _sql(fresh, "SELECT has_function_privilege('public', 'public.kazma_space_id()', 'EXECUTE')") == [(True,)]
    # The control: as its owner it is done, and said done.
    assert revoke_public_execute(_with(db["base"], db=name)) == ""
    assert _sql(fresh, "SELECT has_function_privilege('public', 'public.kazma_space_id()', 'EXECUTE')") == [(False,)]


def test_the_proof_says_what_stopped_it(rs: Any, db: dict[str, Any]) -> None:
    """N2: with the function there and the space role not allowed to run it,
    the proof said it was "missing or not Kazma's"."""
    app = db["app"]
    _sql(app, "REVOKE EXECUTE ON FUNCTION public.kazma_space_id() FROM kazma_space")
    try:
        failed = " ".join(rs.prove(app, KNOWN, vector_table="").failed)
        assert "may not run the function its policies call" in failed and "missing" not in failed, failed
    finally:
        assert rs.apply(app, vector_table="").complete
    # The control: a missing function says it is missing.
    _sql(app, "ALTER FUNCTION public.kazma_space_id() RENAME TO kazma_space_id_away")
    try:
        assert "does not exist" in " ".join(rs.prove(app, KNOWN, vector_table="").failed)
    finally:
        _sql(app, "ALTER FUNCTION public.kazma_space_id_away() RENAME TO kazma_space_id")


# ── the way out (last: it drops the role) ───────────────────────────────


def test_zz_the_remove_file_takes_the_lock_out(rs: Any, db: dict[str, str]) -> None:
    with psycopg.connect(db["base"], autocommit=True) as admin:
        for other in db["extra"]:  # the role's rights in other databases go with them
            admin.execute(f'DROP DATABASE IF EXISTS "{other}" WITH (FORCE)')
    db["extra"].clear()
    _seed(db)
    with psycopg.connect(db["su"], autocommit=True) as conn:
        conn.execute(REMOVE.read_text(encoding="utf-8"))
    assert _sql(db["su"], "SELECT count(*) FROM pg_policy WHERE polname = 'kazma_space_rows'") == [(0,)]
    assert _sql(db["su"], "SELECT count(*) FROM pg_class WHERE relrowsecurity") == [(0,)]
    assert _sql(db["base"], "SELECT count(*) FROM pg_roles WHERE rolname = 'kazma_space'") == [(0,)]
    assert _sql(db["app"], "SELECT count(*) FROM kazma_chat_sessions") == [(3,)]
    assert any(f.subject == "kazma_space" and "does not exist" in f.problem
               for f in rs.check(db["app"], vector_table=""))
    # The role file brings it back, as a repair run of setup would.
    _sql(db["su"], SCRIPT.read_text(encoding="utf-8"))
