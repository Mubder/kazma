"""The second lock's rules on Kazma's Postgres tables (Spaces S4-01, step 2).

The second lock (design: ``.claude/briefs/SPACES_S4_01_DESIGN.md``; its review
and the decisions win where they differ) lets a statement run in one space see
and change that space's rows only, whatever its ``WHERE`` says. This module
holds what the database needs for it, and nothing uses it at run time yet:

* :data:`RULES` -- one rule for every table of ``pg_backup.KAZMA_PG_TABLES``,
  and for pgvector's table under the name the settings give it (review M9). A
  table is a space's (a policy and the rights :data:`SPACE_ROLE` has on it),
  a narrow grant (the intake mutex), or the install's (no grant: the space
  role gets "permission denied", never rows). A row's space is one expression
  for every table, ``COALESCE(NULLIF(x, ''), 'default')`` (review M6): an
  empty space is the owner's, as the code reads it today.
* :data:`SPACE_ROLE`, ``kazma_space``: may not sign in, owns nothing, is a
  member of nothing; Kazma's login is a member of it. It is made by
  ``deploy/postgres/kazma_app_role.sql`` (setup, the upgrade, the move, the
  emptied database: review M11), or here when the login may make roles.
* the policy calls ``kazma_space_id()`` (``pg_backup.KAZMA_PG_FUNCTIONS``),
  which raises when no space is set for the transaction (decision 2), so a
  statement that forgot to say its space fails instead of finding nothing.
* :func:`check` -- the catalog against the rules, both ways, read-only.
* :func:`apply` -- what is missing, as the owner of the tables only (a login
  that owns nothing would blind the install: the way back), one table per
  transaction under the schema lock with a short lock wait and one budget
  for the pass (review M15). A table that could not be taken is left and said.
* :func:`space_rows` (review M6) and :func:`prove` -- every row's space is one
  the registry knows; on a connection as the space role each policy table is
  under row security, raises with nothing set, and shows nothing of a space
  that does not exist.
* :func:`space_conninfo` -- the one function that builds the space
  connection's address (decision 5; review M10: the address's own options and
  ``PGOPTIONS`` kept, the role last).

Spaces off is unchanged: nothing here runs at start. The two places that make
tables -- ``pg_schema.ensure_schema`` and the checkpointer's ``setup()`` --
put a new table's rule in place only after :func:`apply` succeeded in this
process (:func:`in_place`) and only in a database that holds Kazma's rules
already (review H3). Arming (the space pool, the checkout's rule) is the
later steps' (3 to 6).

Policies are ``TO PUBLIC`` and enabled, never forced: every Kazma dump is
``--no-owner --no-privileges`` and restores where the role does not exist,
and the owner (backups, restores, the upgrade, an older build) sees every row
as today. Grants are lost on a restore; :func:`apply` puts them back.
"""

from __future__ import annotations

import contextvars
import logging
import os
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from kazma_core.db.pg_backup import KAZMA_PG_FUNCTIONS
from kazma_core.db.pg_schema import SCHEMA_LOCK_KEY

logger = logging.getLogger(__name__)

__all__ = [
    "RULES",
    "SPACE_ROLE",
    "apply",
    "check",
    "in_place",
    "prove",
    "rules",
    "space_rows",
]

#: The role a space's connection runs as.
SPACE_ROLE = "kazma_space"
#: The setting that names the space of a transaction.
SPACE_SETTING = "kazma.space"
#: The function every policy compares with; raises when no space is set.
SPACE_FUNCTION = "kazma_space_id"
#: The one policy Kazma puts on each space's table.
POLICY = "kazma_space_rows"
#: The checkpoint tables' space (review L9), filled by its default.
SPACE_COLUMN = "space_id"

#: What puts a missing piece in place.
_APPLY = "python scripts/row_security.py apply"

#: The owner's space: an empty or missing space is the owner's (N19).
_OWNER_SPACE = "default"

_SPACE = "space"      # a policy: the row's space against the transaction's
_GRANT = "grant"      # rights, no policy (one row that every space locks)
_INSTALL = "install"  # the install's: no grant, no policy

_ALL = ("SELECT", "INSERT", "UPDATE", "DELETE")
_PRIVILEGES = ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")


@dataclass(frozen=True)
class Rule:
    """What one table is: whose rows, and what the space role may do."""

    kind: str
    space: str = ""
    rights: tuple[str, ...] = ()
    column: str = ""
    own: tuple[str, ...] = ()
    why: str = ""

    @property
    def expression(self) -> str:
        """A row's space (review M6: one rule for every table)."""
        return f"COALESCE(NULLIF({self.space}, ''), '{_OWNER_SPACE}')"


def _space(space: str = "tenant_id", rights: tuple[str, ...] = _ALL, **kw: Any) -> Rule:
    return Rule(_SPACE, space=space, rights=rights, **kw)


def _install(why: str) -> Rule:
    return Rule(_INSTALL, why=why)


#: Every table of ``pg_backup.KAZMA_PG_TABLES``, and its rule. Rights per
#: table (review M12): what is written once and read after (audit and job
#: events) may not be changed or deleted by a space, since the audit prune's
#: switch is a setting any role may set; a reservation is the intake's own.
RULES: dict[str, Rule] = {
    # The checkpointer (LangGraph): the thread's space, a column of Kazma's.
    "checkpoints": _space(SPACE_COLUMN, column=SPACE_COLUMN),
    "checkpoint_blobs": _space(SPACE_COLUMN, column=SPACE_COLUMN),
    "checkpoint_writes": _space(SPACE_COLUMN, column=SPACE_COLUMN),
    "checkpoint_migrations": _install("LangGraph's schema version, the install's"),
    # The shared stores.
    "kazma_settings": _install("one key space for the install, every space's keys (decision 4)"),
    "kazma_chat_sessions": _space(),
    "kazma_swarm_tasks": _space("metadata->>'tenant_id'"),
    "kazma_swarm_worker_metrics": _install("the install's workers"),
    "kazma_platform_users": _install("no code reads or writes it"),
    "kazma_web_sessions": _install("no code reads or writes it"),
    # Documents.
    "document_jobs": _space(),
    "document_job_events": _space(rights=("SELECT", "INSERT")),
    "document_intake_reservations": _space(rights=("SELECT",)),
    "document_intake_mutex": Rule(_GRANT, rights=("SELECT", "UPDATE"),
                                  why="one row: the enqueue of a space's job locks it"),
    "documents": _space(),
    "document_blobs": _space(rights=("SELECT", "INSERT", "UPDATE")),
    "document_versions": _space(),
    "document_artifacts": _space(),
    "document_acl": _space(),
    "document_tombstones": _space(),
    "document_chunks": _space(),
    "document_audit_events": _space(rights=("SELECT", "INSERT"), own=("system",)),
    # The memory mirror.
    "kazma_episodes": _space(),
    "kazma_beliefs": _space(),
}

#: pgvector's table: made by the memory backend when it is used, under the
#: name the settings give it (review M9), so it is keyed by that name.
_VECTOR_RULE = _space(why="pgvector: memory's vectors")

#: The checkpointer's tables (``open_postgres_checkpointer`` puts their rules
#: in place after LangGraph's ``setup()`` makes them).
_CHECKPOINT_TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations")


def _role_sql_rights() -> str:
    """The rights block of ``deploy/postgres/kazma_app_role.sql``, from
    :data:`RULES` (the file holds it between its ``rights`` markers; the gate
    compares them)."""
    return ",\n".join(f"        ('{table}', '{rule.kind}', '{', '.join(rule.rights)}')"
                      for table, rule in RULES.items())


def rules(vector_table: str | None = None) -> dict[str, Rule]:
    """:data:`RULES`, with pgvector's table under *vector_table* when named."""
    out = dict(RULES)
    if vector_table and vector_table not in out:
        out[vector_table] = _VECTOR_RULE
    return out


def _configured_vector_table() -> str | None:
    """pgvector's table name when memory's vectors are on pgvector, else None."""
    try:
        from kazma_core.memory.backends import get_backends_cfg, pgvector_table
    except ImportError:  # memory is an optional part
        return None
    return pgvector_table(get_backends_cfg())


# ── the space connection's address ──────────────────────────────────────


def _role_option() -> str:
    return f"-c role={SPACE_ROLE}"


def space_conninfo(url: str, environ: Mapping[str, str] | None = None) -> str:
    """The space connection's address: *url* with the role as a start option.

    The address's own ``options`` and ``PGOPTIONS`` (a ``search_path``, a
    timeout) stay; the role comes last, so nothing before it can name another
    (review M10). One function, so a second login for the space role (the
    design's E, a later item) changes only this.
    """
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    env = os.environ if environ is None else environ
    own = str(conninfo_to_dict(url).get("options") or "").strip()
    if not own:
        own = str(env.get("PGOPTIONS") or "").strip()
    options = f"{own} {_role_option()}".strip()
    return make_conninfo(url, options=options)


# ── connections: the install's own sign-ins (path 5 of the design) ──────


def _owner_connect(url: str, *, autocommit: bool = False) -> Any:
    """The owner's connection, for the rules' own reads and changes."""
    import psycopg

    return psycopg.connect(url, autocommit=autocommit, connect_timeout=10)


def _space_connect(url: str) -> Any:
    """A connection as the space role, for :func:`prove` only."""
    import psycopg

    return psycopg.connect(space_conninfo(url), autocommit=False, connect_timeout=10)


def _one(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> Any:
    cur.execute(sql, params or None)
    row = cur.fetchone()
    if row is None:
        return None
    return next(iter(row.values())) if isinstance(row, dict) else row[0]


def _rows(cur: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    cur.execute(sql, params or None)
    return [tuple(r.values()) if isinstance(r, dict) else tuple(r) for r in cur.fetchall()]


def _quoted(table: str) -> str:
    return 'public."' + table.replace('"', '""') + '"'


def _policy_sql(table: str, rule: Rule) -> str:
    match = f"{rule.expression} = (SELECT public.{SPACE_FUNCTION}())"
    return (f"CREATE POLICY {POLICY} ON {_quoted(table)} AS PERMISSIVE FOR ALL TO PUBLIC "
            f"USING ({match}) WITH CHECK ({match})")


_CASTS = re.compile(r"::\w+")


def _same_expression(stored: str | None, rule: Rule) -> bool:
    """A policy's stored text against the rule's, as Postgres prints it back
    (casts and brackets added, the sub-select named)."""
    def norm(text: str) -> str:
        text = _CASTS.sub("", text.lower()).replace(f" as {SPACE_FUNCTION}", "").replace("public.", "")
        return re.sub(r"[\s()]", "", text)

    want = f"{rule.expression} = (SELECT public.{SPACE_FUNCTION}())"
    return stored is not None and norm(stored) == norm(want)


# ── the catalog against the rules ───────────────────────────────────────


@dataclass(frozen=True)
class Finding:
    """One way the database differs from the rules."""

    subject: str
    problem: str
    fix: str = ""
    blocking: bool = True


def _present(cur: Any, names: Iterable[str]) -> list[str]:
    return [t for t in names if _one(cur, "SELECT to_regclass(%s) IS NOT NULL", (_quoted(t),))]


def _role_row(cur: Any) -> tuple[Any, ...] | None:
    found = _rows(cur, "SELECT oid, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls, "
                       "rolinherit, rolreplication FROM pg_roles WHERE rolname = %s", (SPACE_ROLE,))
    return found[0] if found else None


def _version(cur: Any) -> int:
    return int(_one(cur, "SELECT current_setting('server_version_num')::int"))


def _role_findings(cur: Any) -> list[Finding]:
    role = _role_row(cur)
    if role is None:
        return [Finding(SPACE_ROLE, "the role does not exist",
                        "run deploy/postgres/kazma_app_role.sql as the superuser (kazma setup postgres does)")]
    out: list[Finding] = []
    attrs = dict(zip(("login", "superuser", "createdb", "createrole", "bypassrls", "inherit", "replication"),
                     role[1:], strict=True))
    wrong = sorted(name for name, on in attrs.items() if on)
    if wrong:
        out.append(Finding(SPACE_ROLE, f"the role may {', '.join(wrong)}",
                           "run deploy/postgres/kazma_app_role.sql as the superuser"))
    parents = [r[0] for r in _rows(cur, "SELECT b.rolname FROM pg_auth_members m JOIN pg_roles b ON b.oid = m.roleid "
                                        "WHERE m.member = %s ORDER BY 1", (role[0],))]
    if parents:
        out.append(Finding(SPACE_ROLE, f"the role is a member of {', '.join(parents)}: it would count as them",
                           "run deploy/postgres/kazma_app_role.sql as the superuser"))
    wrong = _membership(cur, role[0])
    if wrong:
        out.append(Finding(SPACE_ROLE, f"Kazma's login {wrong}",
                           "run deploy/postgres/kazma_app_role.sql as the superuser, or apply as a login that may "
                           "grant the role"))
    return out


_LOGIN = "(SELECT oid FROM pg_roles WHERE rolname = session_user)"


def _membership(cur: Any, role_oid: Any) -> str:
    """How Kazma's login holds the space role: ``""`` when as it must (it may
    switch to it and inherits none of its rights), else what is wrong. One
    answer, read from ``pg_auth_members`` for every grant: ``pg_has_role``
    says yes for a superuser always, and on PostgreSQL 16 for a CREATEROLE
    login's own automatic grant, which has no SET (step 2 review M2)."""
    if _version(cur) >= 160000:
        grants = _rows(cur, f"SELECT inherit_option, set_option FROM pg_auth_members "  # noqa: S608  # nosec B608 - a fixed sub-select
                            f"WHERE roleid = %s AND member = {_LOGIN}", (role_oid,))
        if any(inherit for inherit, _set in grants):
            return "holds the role with INHERIT"
        if not any(may_set for _inherit, may_set in grants):
            return "is not a member of the role with SET"
        return ""
    held = _one(cur, f"SELECT count(*) FROM pg_auth_members WHERE roleid = %s AND member = {_LOGIN}",  # noqa: S608  # nosec B608 - a fixed sub-select
                (role_oid,))
    return "" if held else "is not a member of the role"


def _function_state(cur: Any) -> str:
    """``""`` when the policies' function is Kazma's, else what it is not."""
    found = _rows(cur, "SELECT prosrc, provolatile, proparallel, prosecdef FROM pg_proc "
                       "WHERE oid = to_regprocedure(%s)", (f"public.{SPACE_FUNCTION}()",))
    if not found:
        return "the function does not exist"
    src, volatile, parallel, definer = found[0]
    if _body(KAZMA_PG_FUNCTIONS[SPACE_FUNCTION]) != str(src).strip() or volatile != "s" or parallel != "s" or definer:
        return "the function is not Kazma's"
    return ""


def _function_findings(cur: Any) -> list[Finding]:
    name = f"public.{SPACE_FUNCTION}()"
    state = _function_state(cur)
    if state:
        return [Finding(name, state, _APPLY)]
    out: list[Finding] = []
    if _role_row(cur) is not None and not _one(cur, "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                                               (SPACE_ROLE, name)):
        out.append(Finding(name, "the space role may not run the function its policies call", _APPLY))
    return out


def _public_findings(cur: Any) -> list[Finding]:
    """Each function of Kazma's PUBLIC may run: a note (step 2 review L3)."""
    return [Finding(name, "PUBLIC may run the function: a reader that may run no function is refused "
                          "Kazma's database", _APPLY, blocking=False) for name in _public_functions(cur)]


def _public_functions(cur: Any) -> list[str]:
    """Kazma's functions there are that PUBLIC may run."""
    return [name for name in (f"public.{fn}()" for fn in KAZMA_PG_FUNCTIONS) if _public_may_run(cur, name)]


def _body(ddl: str) -> str:
    return ddl.split("$space$")[1].strip()


def _table_rights(cur: Any, table: str) -> set[str]:
    return {p for p in _PRIVILEGES
            if _one(cur, "SELECT has_table_privilege(%s, to_regclass(%s), %s)", (SPACE_ROLE, _quoted(table), p))}


def _direct_rights(cur: Any, table: str) -> set[str]:
    return {r[0] for r in _rows(
        cur, "SELECT a.privilege_type FROM pg_class c, aclexplode(c.relacl) a "
             "WHERE c.oid = to_regclass(%s) AND a.grantee = (SELECT oid FROM pg_roles WHERE rolname = %s)",
        (_quoted(table), SPACE_ROLE))}


def _policies(cur: Any, table: str) -> list[tuple[Any, ...]]:
    return _rows(cur, "SELECT polname, polcmd, polpermissive, polroles = '{0}'::oid[], "
                      "pg_get_expr(polqual, polrelid), pg_get_expr(polwithcheck, polrelid) "
                      "FROM pg_policy WHERE polrelid = to_regclass(%s) ORDER BY polname", (_quoted(table),))


def _flags(cur: Any, table: str) -> tuple[bool, bool]:
    row = _rows(cur, "SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE oid = to_regclass(%s)",
                (_quoted(table),))
    return (bool(row[0][0]), bool(row[0][1])) if row else (False, False)


def _has_column(cur: Any, table: str, column: str) -> bool:
    return bool(_one(cur, "SELECT count(*) FROM pg_attribute WHERE attrelid = to_regclass(%s) AND attname = %s "
                          "AND attnum > 0 AND NOT attisdropped", (_quoted(table), column)))


def _owned_sequences(cur: Any, table: str) -> list[str]:
    return [r[0] for r in _rows(
        cur, "SELECT s.oid::regclass::text FROM pg_class s JOIN pg_depend d ON d.objid = s.oid "
             "AND d.classid = 'pg_class'::regclass AND d.deptype IN ('a', 'i') "
             "WHERE s.relkind = 'S' AND d.refobjid = to_regclass(%s) ORDER BY 1", (_quoted(table),))]


def _table_findings(cur: Any, table: str, rule: Rule, role: bool) -> list[Finding]:
    out: list[Finding] = []
    enabled, forced = _flags(cur, table)
    policies = _policies(cur, table)
    if rule.kind == _SPACE:
        if rule.column and not _has_column(cur, table, rule.column):
            out.append(Finding(table, f"no {rule.column} column", _APPLY))
        if not enabled:
            out.append(Finding(table, "row security is off", _APPLY))
        ours = [p for p in policies if p[0] == POLICY]
        if not ours:
            out.append(Finding(table, "Kazma's policy is missing", _APPLY))
        else:
            name, cmd, permissive, public, qual, check = ours[0]
            if cmd != "*" or not permissive or not public or not _same_expression(qual, rule) \
                    or not _same_expression(check, rule):
                out.append(Finding(table, "Kazma's policy is not the rule's (command, roles or text)",
                                   _APPLY))
    elif enabled:
        out.append(Finding(table, "row security is on for a table with no policy rule", _APPLY))
    if forced:
        out.append(Finding(table, "row security is forced: dumps fail and the owner sees nothing",
                           "ALTER TABLE ... NO FORCE ROW LEVEL SECURITY as the owner"))
    others = [p[0] for p in policies if p[0] != POLICY]
    if others:
        out.append(Finding(table, f"policies that are not Kazma's: {', '.join(others)}",
                           "DROP POLICY as the owner: a second permissive policy widens what a space sees"))
    if role:
        held = _table_rights(cur, table)
        want = set(rule.rights)
        if held != want:
            out.append(Finding(table, f"the space role may {', '.join(sorted(held)) or 'do nothing'}; the rule "
                                      f"says {', '.join(sorted(want)) or 'nothing'}",
                               "python scripts/row_security.py apply; a PUBLIC grant is revoked by the owner"))
        if "INSERT" in want:
            for seq in _owned_sequences(cur, table):
                if not _one(cur, "SELECT has_sequence_privilege(%s, %s, 'USAGE')", (SPACE_ROLE, seq)):
                    out.append(Finding(seq, "the space role may not use the sequence of its inserts",
                                       _APPLY))
    return out


def _schema_findings(cur: Any, all_rules: dict[str, Rule], role: bool) -> list[Finding]:
    out: list[Finding] = []
    if role:
        if not _one(cur, "SELECT has_schema_privilege(%s, 'public', 'USAGE')", (SPACE_ROLE,)):
            out.append(Finding("schema public", "the space role may not use the schema", _APPLY))
        if _one(cur, "SELECT has_schema_privilege(%s, 'public', 'CREATE')", (SPACE_ROLE,)):
            out.append(Finding("schema public", "the space role may create in the schema (review L3)",
                               "REVOKE CREATE ON SCHEMA public FROM PUBLIC as the owner"))
        defaults = _rows(cur, "SELECT pg_get_userbyid(d.defaclrole) FROM pg_default_acl d, aclexplode(d.defaclacl) a "
                              "WHERE a.grantee IN (0, (SELECT oid FROM pg_roles WHERE rolname = %s)) "
                              "AND d.defaclobjtype = 'r'", (SPACE_ROLE,))
        if defaults:
            out.append(Finding("default privileges", "tables made later would be open to the space role "
                                                     f"(set by {', '.join(sorted({d[0] for d in defaults}))})",
                               "ALTER DEFAULT PRIVILEGES ... REVOKE as that role"))
    tables = _rows(cur, "SELECT c.relname, c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = 'public' AND c.relkind IN ('r', 'p', 'v', 'm', 'f') "
                        "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = c.oid AND d.deptype = 'e') "
                        "ORDER BY 1")
    for name, kind in tables:
        if kind in ("v", "m"):
            out.append(Finding(name, "a view runs as its owner and shows every space's rows",
                               "drop it, or move it out of Kazma's database"))
        elif name not in all_rules:
            held = _table_rights(cur, name) if role else set()
            out.append(Finding(name, "a table with no rule" + (f", open to the space role: {sorted(held)}"
                                                                if held else " (the space role may not read it)"),
                               "give it a rule in db/row_security.RULES" if held else "",
                               blocking=bool(held)))
    definers = _rows(cur, "SELECT p.oid::regprocedure::text FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                          "WHERE n.nspname = 'public' AND p.prosecdef AND NOT EXISTS "
                          "(SELECT 1 FROM pg_depend d WHERE d.objid = p.oid AND d.deptype = 'e') ORDER BY 1")
    for (name,) in definers:
        out.append(Finding(name, "a SECURITY DEFINER function runs as its owner and can show every space's rows",
                           "drop it, or make it SECURITY INVOKER"))
    return out


def _findings(cur: Any, all_rules: dict[str, Rule]) -> list[Finding]:
    out = _role_findings(cur)
    role = _role_row(cur) is not None
    out += _function_findings(cur) + _public_findings(cur)
    for table in _present(cur, all_rules):
        out += _table_findings(cur, table, all_rules[table], role)
    return out + _schema_findings(cur, all_rules, role)


def check(url: str, *, vector_table: str | None = None) -> list[Finding]:
    """How the database at *url* differs from the rules, read-only; empty
    when every table there holds its rule. A table not made yet is skipped."""
    all_rules = rules(vector_table if vector_table is not None else _configured_vector_table())
    with _owner_connect(url, autocommit=True) as conn, conn.cursor() as cur:
        return _findings(cur, all_rules)


# ── putting the rules in place ──────────────────────────────────────────


@dataclass
class Applied:
    """What :func:`apply` did."""

    changed: list[str] = field(default_factory=list)
    left: list[str] = field(default_factory=list)
    refused: str = ""
    #: What still keeps the lock from holding after the pass (the role, its
    #: membership, the function): said, never "complete" (step 2 review M2).
    unresolved: list[str] = field(default_factory=list)
    #: Checkpoint rows given their thread's space by the backfill (step 4).
    backfilled: int = 0

    @property
    def complete(self) -> bool:
        return not self.refused and not self.left and not self.unresolved


_in_place = False


def in_place() -> bool:
    """True once :func:`apply` has put the rules in place in this process:
    from then on a table Kazma makes gets its rule as it is made."""
    return _in_place


def _mark_in_place(value: bool = True) -> None:
    global _in_place
    _in_place = value


def _who(cur: Any) -> tuple[str, bool, bool]:
    row = _rows(cur, "SELECT session_user::text, rolsuper, rolcreaterole FROM pg_roles WHERE rolname = session_user")[0]
    return str(row[0]), bool(row[1]), bool(row[2])


def _not_the_owner(cur: Any, login: str, tables: list[str]) -> list[str]:
    return [t for t in tables
            if _one(cur, "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid = to_regclass(%s)",
                    (_quoted(t),)) != login]


def _make_role(cur: Any, version: int) -> list[str]:
    """The role, when the login may make roles (a compose install that signs
    in as the superuser; otherwise the role file makes it), and Kazma's
    login's membership as it must be (:func:`_membership`), granted
    explicitly. A grant the login may not make is left for the role file and
    said in the result, never raised (step 2 review M2, L5)."""
    import psycopg

    done: list[str] = []
    if _role_row(cur) is None:
        cur.execute(f"CREATE ROLE {SPACE_ROLE} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION "
                    "NOBYPASSRLS NOINHERIT")
        done.append(f"made the role {SPACE_ROLE}")
    role = _role_row(cur)
    state = _membership(cur, role[0]) if role is not None else ""
    if role is None or state not in ("is not a member of the role with SET", "is not a member of the role"):
        return done  # held as it must be, or with INHERIT, which only the role file mends
    grant = (f"GRANT {SPACE_ROLE} TO SESSION_USER WITH INHERIT FALSE, SET TRUE" if version >= 160000
             else f"GRANT {SPACE_ROLE} TO SESSION_USER")
    try:
        with cur.connection.transaction():
            cur.execute(grant)
        done.append(f"made Kazma's login a member of {SPACE_ROLE}")
    except psycopg.errors.InsufficientPrivilege:
        logger.warning("[row_security] Kazma's login may not grant itself %s: the role file does", SPACE_ROLE)
    return done


def _make_function(cur: Any) -> list[str]:
    """The policies' function, as Kazma's (made again when it is not), which
    the space role may run; PUBLIC may run none of Kazma's functions: a
    reader of Kazma's database that may run no function stays one (step 2
    review L3)."""
    done: list[str] = []
    if _function_state(cur):  # missing, or not Kazma's: made again (a right alone is only granted)
        cur.execute(KAZMA_PG_FUNCTIONS[SPACE_FUNCTION])
        done.append(f"made {SPACE_FUNCTION}()")
    name = f"public.{SPACE_FUNCTION}()"
    # PUBLIC's first: while PUBLIC may run it, so may the role, and its own
    # grant would look done (a whole copy restores it with PUBLIC's default).
    for public in _public_functions(cur):
        cur.execute(f"REVOKE EXECUTE ON FUNCTION {public} FROM PUBLIC")
        if not _public_may_run(cur, public):  # another role's function only warns: check's note says it
            done.append(f"PUBLIC may no longer run {public}")
    if not _one(cur, "SELECT has_function_privilege(%s, %s, 'EXECUTE')", (SPACE_ROLE, name)):
        cur.execute(f"GRANT EXECUTE ON FUNCTION {name} TO {SPACE_ROLE}")
        done.append(f"{SPACE_ROLE} may run {name}")
    return done


def _public_may_run(cur: Any, name: str) -> bool:
    return bool(_one(cur, "SELECT EXISTS (SELECT 1 FROM pg_proc p, aclexplode(coalesce(p.proacl, "
                          "acldefault('f', p.proowner))) a WHERE p.oid = to_regprocedure(%s) AND a.grantee = 0)",
                     (name,)))


@contextmanager
def _table_transaction(conn: Any, lock_timeout_ms: int) -> Iterator[Any]:
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(f"SET LOCAL lock_timeout = '{int(lock_timeout_ms)}ms'")
        cur.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (SCHEMA_LOCK_KEY,))
        yield cur


def _apply_table(cur: Any, table: str, rule: Rule) -> list[str]:
    """One table's rule, inside the caller's transaction. Only what is missing."""
    done: list[str] = []
    q = _quoted(table)
    enabled, forced = _flags(cur, table)
    if rule.kind == _SPACE:
        if rule.column and not _has_column(cur, table, rule.column):
            # The default is taken at ALTER time for every row already there,
            # so nothing may be set (design 2): those rows stay the owner's.
            if _one(cur, "SELECT coalesce(current_setting(%s, true), '')", (SPACE_SETTING,)):
                raise RuntimeError(f"a space is set on this connection: {table}'s column is added with none")
            from kazma_core.db.pg_schema import ensure_schema

            # A rule's table names are Kazma's own (letters, digits, _).
            ensure_schema(cur, [f"ALTER TABLE public.{table} ADD COLUMN IF NOT EXISTS {rule.column} TEXT "
                                f"DEFAULT NULLIF(current_setting('{SPACE_SETTING}', true), '')"])
            done.append(f"{table}: added {rule.column}")
        if not enabled:
            cur.execute(f"ALTER TABLE {q} ENABLE ROW LEVEL SECURITY")
            done.append(f"{table}: row security on")
        ours = [p for p in _policies(cur, table) if p[0] == POLICY]
        good = bool(ours) and ours[0][1] == "*" and ours[0][2] and ours[0][3] \
            and _same_expression(ours[0][4], rule) and _same_expression(ours[0][5], rule)
        if not good:
            if ours:
                cur.execute(f"DROP POLICY {POLICY} ON {q}")
            cur.execute(_policy_sql(table, rule))
            done.append(f"{table}: policy {'made again' if ours else 'made'}")
    elif enabled:
        cur.execute(f"ALTER TABLE {q} DISABLE ROW LEVEL SECURITY")
        done.append(f"{table}: row security off (no policy rule)")
    if forced:
        cur.execute(f"ALTER TABLE {q} NO FORCE ROW LEVEL SECURITY")
        done.append(f"{table}: row security no longer forced")
    if _direct_rights(cur, table) != set(rule.rights):
        cur.execute(f"REVOKE ALL ON {q} FROM {SPACE_ROLE}")
        if rule.rights:
            cur.execute(f"GRANT {', '.join(rule.rights)} ON {q} TO {SPACE_ROLE}")
        done.append(f"{table}: the space role may {', '.join(rule.rights) or 'nothing'}")
    if "INSERT" in rule.rights:
        for seq in _owned_sequences(cur, table):
            if not _one(cur, "SELECT has_sequence_privilege(%s, %s, 'USAGE')", (SPACE_ROLE, seq)):
                cur.execute(f"GRANT USAGE ON SEQUENCE {seq} TO {SPACE_ROLE}")
                done.append(f"{seq}: the space role may use it")
    return done


def _grant_schema(cur: Any, login: str) -> list[str]:
    """The space role's use of the schema. A login that may not grant it (the
    schema is another role's) leaves it to that role, said in the result
    (step 2 review L5)."""
    import psycopg

    if _one(cur, "SELECT has_schema_privilege(%s, 'public', 'USAGE')", (SPACE_ROLE,)):
        return []
    try:
        with cur.connection.transaction():
            cur.execute(f"GRANT USAGE ON SCHEMA public TO {SPACE_ROLE}")
    except psycopg.errors.InsufficientPrivilege:
        logger.warning("[row_security] %s may not let %s use the schema public: its owner does", login, SPACE_ROLE)
        return []
    return [f"{SPACE_ROLE} may use the schema"]


def _lock_wait(exc: BaseException) -> bool:
    return getattr(exc, "sqlstate", None) == "55P03"


def apply(url: str, *, tables: Iterable[str] | None = None, vector_table: str | None = None,
          lock_timeout_ms: int = 300, tries: int = 3, budget_s: float = 20.0) -> Applied:
    """Put the rules in place in the database at *url*, as its owner.

    Idempotent: only what is missing is sent. Refused (nothing changed) when
    the role is missing and the login may not make it, or when the login does
    not own Kazma's tables -- a superuser that owns nothing would put every
    space's rows out of reach of the install itself. Each table is its own
    transaction under the schema lock, waiting at most *lock_timeout_ms* for
    it (a dump's lock must not queue every reader behind ours: review M15),
    *tries* times, all within *budget_s*; a table not taken is in ``left``.
    """
    import psycopg

    all_rules = rules(vector_table if vector_table is not None else _configured_vector_table())
    wanted = [t for t in (tables if tables is not None else all_rules) if t in all_rules]
    result = Applied()
    deadline = time.monotonic() + budget_s
    with _owner_connect(url) as conn:
        with conn.transaction(), conn.cursor() as cur:
            login, superuser, createrole = _who(cur)
            present = _present(cur, wanted)
            strangers = _not_the_owner(cur, login, present)
            if strangers:
                result.refused = (f"{login} does not own {', '.join(strangers)}: only the tables' owner puts the "
                                  "rules in place")
            elif _role_row(cur) is None and not (superuser or createrole):
                result.refused = (f"the role {SPACE_ROLE} does not exist and {login} may not make it: run "
                                  "deploy/postgres/kazma_app_role.sql as the superuser")
            if result.refused:
                logger.warning("[row_security] the rules were not put in place: %s", result.refused)
                return result
            result.changed += _make_role(cur, _version(cur))
            try:
                with conn.transaction():
                    result.changed += _make_function(cur)
            except psycopg.errors.InsufficientPrivilege:
                result.refused = (f"{SPACE_FUNCTION}() belongs to another role: its owner, or the superuser, "
                                  "drops it, and the next pass makes it Kazma's")
                logger.warning("[row_security] the rules were not put in place: %s", result.refused)
                return result
            result.changed += _grant_schema(cur, login)
            result.unresolved = [f"{f.subject}: {f.problem}" for f in _role_findings(cur) + _function_findings(cur)
                                 if f.blocking]
            if not _one(cur, "SELECT has_schema_privilege(%s, 'public', 'USAGE')", (SPACE_ROLE,)):
                result.unresolved.append("schema public: the space role may not use the schema")
        for table in present:
            if not _take(conn, table, all_rules[table], result, lock_timeout_ms, tries, deadline):
                result.left.append(table)
        stamped = [t for t in present if all_rules[t].column == SPACE_COLUMN and t not in result.left]
        if stamped:
            _backfill(conn, stamped, result, lock_timeout_ms, tries, deadline)
    _mark_in_place()
    if result.left:
        logger.warning("[row_security] %d table(s) could not be taken in time and keep no rule until the next "
                       "pass: %s", len(result.left), ", ".join(result.left))
    if result.changed:
        logger.info("[row_security] rules put in place: %d change(s)", len(result.changed))
    return result


def _take(conn: Any, table: str, rule: Rule, result: Applied, lock_timeout_ms: int, tries: int,
          deadline: float) -> bool:
    """One table's rule in its own transaction; False when its lock was not
    had within the tries and the pass's budget."""
    import psycopg

    for attempt in range(max(1, tries)):
        if time.monotonic() >= deadline:
            return False
        try:
            with _table_transaction(conn, lock_timeout_ms) as cur:
                result.changed += _apply_table(cur, table, rule)
            return True
        except psycopg.Error as exc:
            if not _lock_wait(exc):
                raise
            time.sleep(min(0.2 * (attempt + 1), max(0.0, deadline - time.monotonic())))
    return False


# ── the checkpoints' backfill (S4-01 step 4, design 7.3) ────────────────

#: Threads per statement of the backfill.
_BACKFILL_BATCH = 500


def _backfill(conn: Any, tables: list[str], result: Applied, lock_timeout_ms: int, tries: int,
              deadline: float) -> None:
    """Every checkpoint row gets its thread's space: the record store's
    (``thread_scopes.db``) for a thread of another space, NULL for the
    owner's -- a thread with no record is from before Spaces (N19), and a
    wrong stamp (a thread moved to the owner) goes back. At every pass, not
    once: an older build run after a revert writes NULL for another space's
    thread. In batches, one transaction each under the schema lock, waiting
    at most *lock_timeout_ms*; a table not taken in time is in ``left``. A
    record store that cannot be read is said in ``unresolved``: the lock is
    not to be armed on a half backfill."""
    import sqlite3

    import psycopg

    from kazma_core.spaces import threads
    from kazma_core.spaces.scope import OWNER_SPACE_ID

    try:
        homes = threads.thread_spaces()
    except (sqlite3.Error, OSError) as exc:
        result.unresolved.append(f"checkpoints: the threads' spaces could not be read ({type(exc).__name__})")
        logger.warning("[row_security] the checkpoints' backfill could not read the thread records: %s",
                       type(exc).__name__)
        return
    by_space: dict[str, list[str]] = {}
    for thread_id, space in homes.items():
        if space and space != OWNER_SPACE_ID:
            by_space.setdefault(space, []).append(thread_id)
    others = [t for ids in by_space.values() for t in ids]
    for table in tables:
        statements: list[tuple[str, tuple[Any, ...]]] = []
        for space, ids in sorted(by_space.items()):
            for i in range(0, len(ids), _BACKFILL_BATCH):
                statements.append((f"UPDATE public.{table} SET {SPACE_COLUMN} = %s "  # nosec B608 - a rule's table
                                   f"WHERE thread_id = ANY(%s) AND {SPACE_COLUMN} IS DISTINCT FROM %s",
                                   (space, ids[i:i + _BACKFILL_BATCH], space)))
        statements.append((f"UPDATE public.{table} SET {SPACE_COLUMN} = NULL "  # nosec B608 - a rule's table
                           f"WHERE {SPACE_COLUMN} IS NOT NULL AND {SPACE_COLUMN} <> %s AND NOT (thread_id = ANY(%s))",
                           (OWNER_SPACE_ID, others)))
        for statement, params in statements:
            done = False
            for attempt in range(max(1, tries)):
                if time.monotonic() >= deadline:
                    break
                try:
                    with _table_transaction(conn, lock_timeout_ms) as cur:
                        cur.execute(statement, params)
                        result.backfilled += max(0, cur.rowcount)
                    done = True
                    break
                except psycopg.Error as exc:
                    if not _lock_wait(exc):
                        raise
                    time.sleep(min(0.2 * (attempt + 1), max(0.0, deadline - time.monotonic())))
            if not done:
                result.left.append(f"{table} (its rows' spaces)")
                break
    if result.backfilled:
        result.changed.append(f"checkpoints: {result.backfilled} row(s) given their thread's space")


# ── tables made later (review H3) ───────────────────────────────────────


def _holds_rules(cur: Any) -> bool:
    """This database already holds Kazma's rules: a table made here joins them."""
    return bool(_one(cur, "SELECT EXISTS (SELECT 1 FROM pg_policy WHERE polname = %s) "
                          "AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s)", (POLICY, SPACE_ROLE)))


def apply_made_tables(cur: Any, made: Iterable[str], *, vector_table: str | None = None) -> list[str]:
    """The rule of each table just made, in the caller's transaction.

    Called by ``pg_schema.ensure_schema`` with the tables it made (and
    pgvector's name, when it is pgvector that makes its table: no settings
    read here). Nothing -- not even a catalog read -- until :func:`in_place`,
    and nothing in a database that holds no rule of Kazma's (another server's
    memory mirror).
    """
    if not _in_place:
        return []
    names = [t.split(".")[-1].strip('"') for t in made]
    all_rules = rules(vector_table)
    ruled = [t for t in names if t in all_rules]
    if not ruled or not _holds_rules(cur):
        return []
    done: list[str] = []
    for table in ruled:
        done += _apply_table(cur, table, all_rules[table])
    if done:
        logger.info("[row_security] a new table's rule: %s", "; ".join(done))
    return done


def after_checkpointer_setup(url: str) -> list[str]:
    """The checkpointer's tables' rules, after LangGraph's ``setup()`` made
    them (it runs ``CREATE INDEX CONCURRENTLY``, outside any transaction of
    ours). Blocking: called through ``asyncio.to_thread``. Nothing until
    :func:`in_place`, and nothing where the database holds no rule."""
    if not _in_place:
        return []
    with _owner_connect(url, autocommit=True) as conn, conn.cursor() as cur:
        if not _holds_rules(cur):
            return []
    return apply(url, tables=_CHECKPOINT_TABLES, vector_table="").changed


# ── the proof ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class StrayRows:
    """Rows whose space is not one Kazma knows (review M6): listed, never guessed."""

    table: str
    space: str
    rows: int
    why: str


def _strays(cur: Any, table: str, rule: Rule, known: set[str]) -> list[StrayRows]:
    from kazma_core.spaces.scope import valid_space_id

    # A name from the rules, quoted; the expression is the rule's own text.
    # Before the rules were ever put in place the checkpoints have no space
    # column: every row there is the owner's, as it will be once it is added
    # (step 2 review M1: the check this exists for failed on live).
    space = rule.expression if not rule.column or _has_column(cur, table, rule.column) else f"'{_OWNER_SPACE}'"
    sql = f"SELECT {space} AS space, count(*) FROM {_quoted(table)} GROUP BY 1"  # noqa: S608  # nosec B608 - a rule's table, quoted
    out: list[StrayRows] = []
    for space, count in _rows(cur, sql):
        space = str(space)
        if space in known or space in rule.own:
            continue
        why = "not a space id" if not valid_space_id(space) else "a space the registry does not know"
        out.append(StrayRows(table, space, int(count), why))
    return out


def space_rows(url: str, known: Iterable[str], *, vector_table: str | None = None) -> list[StrayRows]:
    """Every row whose space the registry does not know, by table and space,
    as the owner and read-only (review M6). An empty list is the answer the
    lock needs before it may be armed: such rows would be no space's. It
    groups every space's table in full (L8: on a live install, when idle)."""
    all_rules = rules(vector_table if vector_table is not None else _configured_vector_table())
    spaces = set(known)
    out: list[StrayRows] = []
    with _owner_connect(url, autocommit=True) as conn, conn.cursor() as cur:
        for table in _present(cur, all_rules):
            rule = all_rules[table]
            if rule.kind == _SPACE:
                out += _strays(cur, table, rule, spaces)
    return out


@dataclass
class Proof:
    """What :func:`prove` found: empty lists are a lock that holds."""

    findings: list[Finding] = field(default_factory=list)
    strays: list[StrayRows] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)

    @property
    def holds(self) -> bool:
        return not self.failed and not self.strays and not any(f.blocking for f in self.findings)


#: A space no registry holds (not a valid space id, so never one): the proof
#: reads each table in it and must find nothing.
_NO_SPACE = "kazma proof: no such space"


def _raises_unset(conn: Any, sql: str) -> bool:
    """*sql* run with no space set raises ``insufficient_privilege``."""
    import psycopg

    try:
        with conn.cursor() as cur:
            cur.execute(sql)
            cur.fetchall()
    except psycopg.errors.InsufficientPrivilege:
        conn.rollback()
        return True
    conn.rollback()
    return False


def _space_checks(conn: Any, owner_counts: dict[str, int], all_rules: dict[str, Rule]) -> list[str]:
    import psycopg

    failed: list[str] = []
    with conn.cursor() as cur:
        who = _one(cur, "SELECT current_user::text")
    conn.rollback()
    if who != SPACE_ROLE:
        return [f"the space connection runs as {who}, not {SPACE_ROLE}: a pooler that drops start options "
                "would leave every row open"]
    if not _raises_unset(conn, f"SELECT {SPACE_FUNCTION}()"):
        failed.append(f"{SPACE_FUNCTION}() answers with no space set")
    for table, count in owner_counts.items():
        rule = all_rules[table]
        q = _quoted(table)
        if rule.kind != _SPACE:
            denied = _raises_unset(conn, f"SELECT 1 FROM {q} LIMIT 0")  # noqa: S608  # nosec B608 - a rule's table, quoted
            if denied == bool(rule.rights):
                failed.append(f"{table}: the space role {'may not' if rule.rights else 'may'} read it")
            continue
        with conn.cursor() as cur:
            active = _one(cur, "SELECT row_security_active(to_regclass(%s))", (q,))
        conn.rollback()
        if not active:
            failed.append(f"{table}: row security is not active for the space role")
        if count and not _raises_unset(conn, f"SELECT 1 FROM {q} LIMIT 1"):  # noqa: S608  # nosec B608 - a rule's table, quoted
            failed.append(f"{table}: a statement with no space set did not raise")
        try:
            with conn.transaction(), conn.cursor() as cur:
                cur.execute("SELECT set_config(%s, %s, true)", (SPACE_SETTING, _NO_SPACE))
                seen = _one(cur, f"SELECT count(*) FROM {q}")  # noqa: S608  # nosec B608 - a rule's table, quoted
        except psycopg.Error as exc:
            failed.append(f"{table}: could not be read in a space ({type(exc).__name__})")
            conn.rollback()
            continue
        if seen:
            failed.append(f"{table}: a space that does not exist sees {seen} of {count} rows")
    return failed


def prove(url: str, known: Iterable[str], *, vector_table: str | None = None) -> Proof:
    """Whether the lock holds in the database at *url*, read-only.

    The catalog equals the rules (:func:`check`), every row's space is known
    (:func:`space_rows`), and on a connection as the space role each space's
    table is under row security, raises with no space set, and shows nothing
    of a space that does not exist while its owner counts its rows. The
    install's tables are refused to it. Nothing is written: the space
    connection only reads, and each of its transactions ends there. It
    counts and groups every space's table in full as the owner (L8: run it on
    a live install when it is idle).
    """
    import psycopg

    table = vector_table if vector_table is not None else _configured_vector_table()
    all_rules = rules(table)
    proof = Proof(findings=check(url, vector_table=table or ""), strays=space_rows(url, known, vector_table=table or ""))
    # What stopped it, in the finding's own words (delta review N2).
    role = [f.problem for f in proof.findings if f.subject == SPACE_ROLE and f.blocking]
    if role:
        proof.failed.append(f"the space role ({'; '.join(role)}): nothing could be read as it")
        return proof
    function = [f.problem for f in proof.findings if f.subject == f"public.{SPACE_FUNCTION}()" and f.blocking]
    if function:
        proof.failed.append(f"the policies' function ({'; '.join(function)}): no space could be read")
        return proof
    with _owner_connect(url, autocommit=True) as conn, conn.cursor() as cur:
        counts = {t: int(_one(cur, f"SELECT count(*) FROM {_quoted(t)}"))  # noqa: S608  # nosec B608 - a rule's table, quoted
                  for t in _present(cur, all_rules)}
    try:
        with _space_connect(url) as conn:
            proof.failed += _space_checks(conn, counts, all_rules)
    except psycopg.OperationalError as exc:
        proof.failed.append(f"Kazma's login could not sign in as {SPACE_ROLE} ({type(exc).__name__})")
    return proof


# ── the space half of a pool (S4-01 step 3; armed in tests only) ────────
#
# Once armed, a checkout of Kazma's own database asks whose statements these
# are: a space's go to a pool that signs in as the space role, with the
# space set for the transaction; the install's to the owner's pool as today;
# nothing said is refused before anything is sent (design 3 and 6). Nothing
# arms it yet but tests: arming at start is step 6. Not armed, a pool takes
# today's path and sends nothing more.

_armed = False


def armed() -> bool:
    """True once the second lock is armed in this process."""
    return _armed


def _arm(value: bool = True) -> None:
    """Arm (or disarm) the second lock in this process: tests now, the
    server's start in step 6, after :func:`apply` and :func:`prove`."""
    global _armed
    _armed = value


def _refusal_base() -> type:
    from kazma_core.spaces.scope import NoSpaceBound

    return NoSpaceBound


class _SecondTransaction(_refusal_base()):  # type: ignore[misc]
    """A statement after the checkout's own transaction ended: it has no
    space, and the policy would answer it with nothing found when no row is
    reached (review M4). A refusal (``NoSpaceBound``): no other store answers
    for it (H2)."""


class _NestedInstallCheckout(_refusal_base()):  # type: ignore[misc]
    """An install checkout while this context holds a space one: a space
    transaction holding a lock the install's statement waits for would wait
    for good, and Postgres sees no deadlock (review M13). A refusal, like
    ``NoSpaceBound``."""


class _NestedSpaceCheckout(_refusal_base()):  # type: ignore[misc]
    """A space checkout while this context holds one: the second connection
    can wait for a lock the first holds (a ``FOR UPDATE`` row), for good
    (step 3 review L5). A refusal, unless :func:`space_inside_a_space`
    declares it."""


class _NotASpace(_refusal_base()):  # type: ignore[misc]
    """A space id of no space's shape: refused before a connection is asked
    for (step 3 review L4)."""


class _NotTheSpaceRole(_refusal_base()):  # type: ignore[misc]
    """A space checkout's connection was not the space role: a role a
    checkout set for its session, or a pooler. Closed and refused, never used
    (step 3 review M1)."""


#: The space a checkout of this context holds, while it holds one.
_holding: contextvars.ContextVar[str] = contextvars.ContextVar("kazma_space_checkout", default="")
#: Why an install checkout inside a space one is declared safe, when it is.
_nested_said: contextvars.ContextVar[str] = contextvars.ContextVar("kazma_nested_install", default="")
#: Why a space checkout inside a space one is declared safe, when it is.
_nested_space_said: contextvars.ContextVar[str] = contextvars.ContextVar("kazma_nested_space", default="")


def checkout_space() -> str:
    """Whose statements a checkout of Kazma's own database is for, once armed:
    a space's id, or ``""`` for the install's (said by
    ``row_work.install_statements`` or ``scope.install_work``).

    Nothing said raises ``NoSpaceBound`` before any statement is sent (design
    6.1): no fallback to the owner's space. A space id of no space's shape
    raises :class:`_NotASpace` (L4). While this context holds a space
    checkout, another checkout raises -- :class:`_NestedInstallCheckout`
    (review M13) or :class:`_NestedSpaceCheckout` (step 3 review L5) --
    unless :func:`install_inside_a_space` or :func:`space_inside_a_space`
    declares it. No I/O."""
    from kazma_core.spaces.row_work import Door
    from kazma_core.spaces.scope import NoSpaceBound, valid_space_id

    door = Door.now()
    if door is None:
        raise NoSpaceBound("a statement to Kazma's database said no space and no install door: refused before "
                           "it was sent (Spaces S4-01)")
    held = _holding.get()
    if door.space:
        if not valid_space_id(door.space):
            raise _NotASpace(f"not a space id: {door.space!r}")
        if held and not _nested_space_said.get():
            raise _NestedSpaceCheckout(f"a checkout of space {door.space} while this context holds one of "
                                       f"space {held}")
        return door.space
    if held and not _nested_said.get():
        raise _NestedInstallCheckout(f"an install checkout while this context holds a checkout of space {held}")
    return ""


@contextmanager
def _declared(var: contextvars.ContextVar[str], reason: str) -> Iterator[None]:
    if not reason.strip():
        raise ValueError("a declared nested checkout needs its reason")
    token = var.set(reason.strip())
    try:
        yield
    finally:
        var.reset(token)


@contextmanager
def install_inside_a_space(reason: str) -> Iterator[None]:
    """Declare an install checkout made while a space one is held safe:
    *reason* says why no lock of the space transaction is waited for."""
    with _declared(_nested_said, reason):
        yield


@contextmanager
def space_inside_a_space(reason: str) -> Iterator[None]:
    """Declare a space checkout made while another is held safe: *reason*
    says why the second waits for no lock the first holds."""
    with _declared(_nested_space_said, reason):
        yield


@contextmanager
def holding(space: str) -> Iterator[None]:
    """This context holds a checkout of *space* for the block (M13)."""
    token = _holding.set(space)
    try:
        yield
    finally:
        _holding.reset(token)


_space_class: type | None = None


def space_connection_class() -> type:
    """The space half's connection: after its checkout's transaction ends
    (a commit or a rollback), the next statement is refused, never run with
    no space (review M4). Reset when the pool takes it back."""
    global _space_class
    if _space_class is None:
        import psycopg

        def _refuse_spent(conn: Any) -> None:
            if conn._kazma_out and conn._kazma_spent:
                raise _SecondTransaction("a second transaction in one checkout has no space: check out again")

        class SpaceCursor(psycopg.Cursor):  # type: ignore[type-arg,misc]
            """A cursor made in the checkout's transaction refuses to run
            after it ended, as a new one does (step 3 review L1)."""

            def execute(self, *args: Any, **kwargs: Any) -> Any:
                _refuse_spent(self.connection)
                return super().execute(*args, **kwargs)

            def executemany(self, *args: Any, **kwargs: Any) -> Any:
                _refuse_spent(self.connection)
                return super().executemany(*args, **kwargs)

            def copy(self, *args: Any, **kwargs: Any) -> Any:
                _refuse_spent(self.connection)
                return super().copy(*args, **kwargs)

            def stream(self, *args: Any, **kwargs: Any) -> Any:
                _refuse_spent(self.connection)
                return super().stream(*args, **kwargs)

        class SpaceConnection(psycopg.Connection):  # type: ignore[type-arg,misc]
            _kazma_out = False
            _kazma_spent = False

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                self.cursor_factory = SpaceCursor

            def commit(self) -> None:
                super().commit()
                self._kazma_spent = True

            def rollback(self) -> None:
                super().rollback()
                self._kazma_spent = True

            def cursor(self, *args: Any, **kwargs: Any) -> Any:
                _refuse_spent(self)
                return super().cursor(*args, **kwargs)

        _space_class = SpaceConnection
    return _space_class


def begin_space(conn: Any, space: str) -> None:
    """Open the checkout's transaction in *space*: ``BEGIN`` and the setting
    in one message, so psycopg finds the connection in a transaction and
    sends no ``BEGIN`` of its own (design 3). Blocking libpq: the sync pool's
    caller only, never on the loop (review H4). Its result is read: a dead
    backend or a refused setting raises psycopg's error, so the pool discards
    the connection and §44 sees an outage (review M3).

    The same message reads ``current_user``: a connection that is not the
    space role -- a session ``SET ROLE NONE`` a checkout left, a pooler -- is
    closed and refused, never used (step 3 review M1: the next checkout, in
    another space, ran as the owner and saw every row)."""
    import psycopg
    from psycopg import pq

    from kazma_core.spaces.scope import valid_space_id

    if not valid_space_id(space):
        raise _NotASpace(f"not a space id: {space!r}")
    literal = pq.Escaping(conn.pgconn).escape_literal(space.encode())
    result = conn.pgconn.exec_(b"BEGIN; SELECT set_config('" + SPACE_SETTING.encode() + b"', " + literal
                               + b", true), current_user::text")
    if result.status != pq.ExecStatus.TUPLES_OK:
        said = (result.error_message or b"").decode(errors="replace").strip()
        raise psycopg.OperationalError(f"the checkout could not set its space ({said or 'no answer'})")
    who = (result.get_value(0, 1) or b"").decode(errors="replace")
    if who != SPACE_ROLE:
        conn.close()  # the pool discards a closed connection: never handed out again
        raise _NotTheSpaceRole(f"a space checkout's connection was {who or 'no role'}, not {SPACE_ROLE}: "
                               "closed and refused")
    conn._kazma_out, conn._kazma_spent = True, False


#: What a space connection's session is given back as, before the next
#: checkout (step 3 review M1, delta review N1): both halves send it.
_RESET_SESSION = "CLOSE ALL; UNLISTEN *; SELECT pg_advisory_unlock_all(); RESET ALL; RESET ROLE; DISCARD TEMP"


def end_space(conn: Any) -> None:
    """The pool took the connection back: it belongs to no checkout, and
    what a checkout set for the session is taken back before the next one
    (step 3 review M1). ``RESET ALL`` leaves the role (a session ``SET ROLE
    NONE`` made the next checkout the owner); ``RESET ROLE`` is the start
    option's again; a temporary table one space made is gone before another
    space's checkout, and so are a cursor held past its commit (it read the
    first space's rows into the next checkout), what the session listens
    to, and its advisory locks (delta review N1). Not ``DISCARD ALL``: it
    drops psycopg's prepared statements under it. Raising discards the
    connection."""
    import psycopg
    from psycopg import pq

    conn._kazma_out = False
    result = conn.pgconn.exec_(_RESET_SESSION.encode())
    if result.status != pq.ExecStatus.COMMAND_OK:
        said = (result.error_message or b"").decode(errors="replace").strip()
        raise psycopg.OperationalError(f"a space connection could not be reset ({said or 'no answer'})")


def check_space_role(conn: Any) -> None:
    """Every new connection of the space half must be the space role: a
    pooler that drops start options would hand out the owner, who sees every
    row (review M10). Asked of each connection, not once at arming."""
    import psycopg

    global _role_refused
    with conn.cursor() as cur:
        who = _one(cur, "SELECT current_user::text")
    conn.rollback()
    if who != SPACE_ROLE:
        _role_refused = (time.monotonic(), str(who))
        raise psycopg.OperationalError(f"the space pool signed in as {who}, not {SPACE_ROLE}")


#: When a new space connection was last refused for its role, and as whom.
_role_refused: tuple[float, str] = (0.0, "")
#: When each kind of space half failure was last said.
_half_said: dict[str, float] = {}


def say_space_half_failed(signin_refused: str | None, failure: str) -> None:
    """A space checkout failed while Kazma's login signs in: the space
    half's own problem, never the outage of §44 (review M1), said in words
    that tell its kinds apart (step 3 review L3): the space role refused at
    sign-in; connections signed in as another role (M10); or both logins
    sign in, and the pool was full or its connection broke. Each kind at
    most once a minute."""
    now = time.monotonic()
    at, who = _role_refused
    if signin_refused:
        kind = "sign-in"
        text = (f"the space role cannot sign in while Kazma's login can ({signin_refused}): a space's statements "
                "are refused until it can (deploy/postgres/kazma_app_role.sql)")
    elif who and now - at < 60.0:
        kind = "role"
        text = (f"the space half's connections sign in as {who}, not {SPACE_ROLE}: a pooler or the address "
                "drops the start option, and a space's statements are refused until it does not")
    else:
        kind = "pool"
        text = (f"a space checkout failed ({failure}) while both logins sign in: the space half's pool was full "
                "or its connection broke; not an outage")
    if now - _half_said.get(kind, -1e9) < 60.0:
        return
    _half_said[kind] = now
    logger.warning("[row_security] %s", text)


# ── the checkpointer's door (S4-01 step 4) ──────────────────────────────
#
# The checkpointer's pool is psycopg's async pool, on the loop, autocommit
# (LangGraph's ``setup()`` makes indexes concurrently). Its checkout asks the
# same question as the shared pool's: whose statements these are. Its
# opening message goes with ``await conn.execute(..., prepare=False)``:
# ``pgconn.exec_`` blocks the loop for the server's whole answer (review H4),
# and the pool prepares every statement at once, which two commands cannot be.


def checkpoint_door() -> str | None:
    """Whose statements a checkpointer checkout is for. None: today's path
    -- Spaces not enforced and the lock not armed, or, not armed, the
    install's door or nothing said. Armed, :func:`checkout_space`'s answer:
    ``""`` the install's, a space's id, nothing said refused. Enforced and not
    armed, a space's id too: its transaction is opened in the space on the
    owner's connection, so what it writes is stamped with it (review M7:
    armed or not). No I/O."""
    if armed():
        return checkout_space()
    from kazma_core.spaces.row_work import Door
    from kazma_core.spaces.scope import spaces_enforced, valid_space_id

    if not spaces_enforced():
        return None
    door = Door.now()
    if door is None or not door.space or not valid_space_id(door.space):
        return None
    return door.space


class _KeepLatestRefused(_refusal_base()):  # type: ignore[misc]
    """``aprune`` keeping the latest checkpoints, in a space's checkout
    (Spaces enforced, armed or not): it sets its transaction's isolation
    inside the checkout's own ``BEGIN`` and fails with 25001 (review L1).
    Kazma never calls it; the ``delete`` strategy works."""


async def begin_space_async(conn: Any, space: str, *, role: bool) -> None:
    """``BEGIN`` and the space in one message on a checkpointer connection,
    awaited and not prepared (review H4). With *role* (the space half), the
    same message reads ``current_user``: anything but the space role is
    closed and refused (step 3 review M1)."""
    from psycopg import pq

    from kazma_core.spaces.scope import valid_space_id

    if not valid_space_id(space):
        raise _NotASpace(f"not a space id: {space!r}")
    literal = pq.Escaping(conn.pgconn).escape_literal(space.encode()).decode()
    cur = await conn.execute(f"BEGIN; SELECT set_config('{SPACE_SETTING}', {literal}, true), current_user::text",
                             prepare=False)
    who = ""
    while True:
        result = cur.pgresult
        if result is not None and result.status == pq.ExecStatus.TUPLES_OK:
            who = (result.get_value(0, 1) or b"").decode(errors="replace")
            break
        if not cur.nextset():
            break
    if role and who != SPACE_ROLE:
        await conn.close()  # the pool discards a closed connection
        raise _NotTheSpaceRole(f"a space checkout's connection was {who or 'no role'}, not {SPACE_ROLE}: "
                               "closed and refused")


async def end_space_transaction_async(conn: Any, ok: bool) -> None:
    """The checkout's transaction ends: ``COMMIT`` when its block ended
    well (a failure is the caller's: its write did not happen), else
    ``ROLLBACK``; a rollback that cannot be sent closes the connection,
    which the pool discards and the server rolls back."""
    import asyncio

    import psycopg

    if ok:
        await conn.execute("COMMIT", prepare=False)
        return
    try:
        await asyncio.wait_for(conn.execute("ROLLBACK", prepare=False), timeout=5.0)
    except (psycopg.Error, OSError, TimeoutError) as exc:
        logger.debug("[row_security] a checkpointer rollback failed (%s): the connection is closed",
                     type(exc).__name__)
        await conn.close()


async def check_space_role_async(conn: Any) -> None:
    """:func:`check_space_role` for the checkpointer's space half."""
    import psycopg

    global _role_refused
    cur = await conn.execute("SELECT current_user::text", prepare=False)
    who = (cur.pgresult.get_value(0, 0) or b"").decode(errors="replace") if cur.pgresult is not None else ""
    if who != SPACE_ROLE:
        _role_refused = (time.monotonic(), who)
        raise psycopg.OperationalError(f"the space pool signed in as {who or 'no role'}, not {SPACE_ROLE}")


async def end_space_async(conn: Any) -> None:
    """:func:`end_space` for the checkpointer's space half: the session given
    back before the next checkout. Raising discards the connection."""
    await conn.execute(_RESET_SESSION, prepare=False)
