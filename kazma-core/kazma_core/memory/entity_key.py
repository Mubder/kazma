"""Entities are keyed by ``(tenant_id, id)`` (Memory audit M21, 2026-10-15).

``entities.id`` was a global primary key: a second tenant's entity with the
same name was never created (its facts kept the id with no row of their own),
the ``user`` hub went to whichever tenant made it first, and every statement
that named an entity by id alone reached every tenant's row. The key is now
the pair, so two tenants each hold their own ``kazma`` and their own ``user``;
``entity_merges`` names its entities by the same pair. The id itself follows
the one id rule (``entity_resolution.slug``, M02) and is unchanged.

Every entity statement names its tenant (``tests/test_memory_entity_key.py``
reads them from the source; a statement that reads across tenants on purpose
is declared there with why).

- :func:`entity_owner` -- which tenant's row an id names for a caller: the
  caller's own; the install's own tenant (``default``, whose view lists every
  tenant's rows) its own row first, else the one tenant that holds the id.
- :func:`size_entity_key` / :func:`migrate_entity_key` -- a database made
  before the change keeps the global key until the operator migrates it
  (``scripts/memory_entity_key.py``: ``size`` reads only; ``apply`` copies the
  file first). Every statement works on both keys: on the old one the rules
  of 2026-10-13 still hold the boundary (no tenant writes another's row), and
  memory health says which key the store has (``entity_key``).

The migration rebuilds ``entities`` and ``entity_merges`` in one transaction,
rows and rowids as they are (the install's own ids, and every other tenant's,
keep their text; each row is keyed under the tenant it already names), and
rebuilds the entity search index. Idempotent: a store already keyed by the
pair is left alone. The Postgres memory mirror holds facts and memories, never
entities (``state_backend``), so it needs nothing.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "INSTALL_TENANT",
    "entity_owner",
    "migrate_entity_key",
    "size_entity_key",
    "tenant_keyed",
]

#: The install's own tenant: single-user turns, and a principal bound to none.
INSTALL_TENANT = "default"

#: The tables the migration rebuilds, in order (the parent first).
_REKEYED = ("entities", "entity_merges")


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone() is not None


def tenant_keyed(conn: sqlite3.Connection) -> bool:
    """True when ``entities`` is keyed by ``(tenant_id, id)``."""
    info = conn.execute("PRAGMA table_info(entities)").fetchall()
    pk = sorted((int(r[5]), str(r[1])) for r in info if int(r[5] or 0) > 0)
    return [name for _pos, name in pk] == ["tenant_id", "id"]


def entity_owner(conn: sqlite3.Connection, entity_id: str, caller: str | None) -> str | None:
    """The tenant whose row *entity_id* names for a caller of tenant *caller*.

    A tenant's own row, or None. The install's own tenant (``default``) sees
    every tenant's rows (the install's view): its own row first, else the one
    other tenant that holds the id; None when no tenant does, or when several
    other tenants do and the install does not (which one is not the page's to
    guess). Raises ``sqlite3.Error`` when the store cannot answer.
    """
    eid = str(entity_id or "").strip()
    who = str(caller or INSTALL_TENANT)
    if not eid:
        return None
    rows = conn.execute(
        "SELECT DISTINCT tenant_id FROM entities WHERE id = ? AND (tenant_id = ? OR ? = ?)",
        (eid, who, who, INSTALL_TENANT),
    ).fetchall()
    tenants = {str(r[0] or INSTALL_TENANT) for r in rows}
    if who in tenants:
        return who
    if who == INSTALL_TENANT and len(tenants) == 1:
        return next(iter(tenants))
    return None


# ── Sizing and migration ──────────────────────────────────────────────────


def size_entity_key(conn: sqlite3.Connection) -> dict[str, Any]:
    """What :func:`migrate_entity_key` would do. Reads only.

    ``merges_without_own_entity``: ledger rows whose source or target is not an
    entity of the row's own tenant (written before 2026-10-13, when a second
    tenant's entity was not created). They are kept as they are; the key the
    store enforces from then on cannot name them, and ``apply`` reports them.
    ``facts_naming_another_tenants_entity``: a tenant's live facts whose
    subject is an entity only another tenant holds -- the rows the old key
    never let that tenant create. The next fact that names it creates the
    tenant's own row; nothing is guessed here.
    """
    if not _has_table(conn, "entities"):
        return {"keyed_by": None, "entities": 0, "by_tenant": {}}
    by_tenant = {
        str(t or INSTALL_TENANT): int(n)
        for t, n in conn.execute(
            "SELECT tenant_id, COUNT(*) FROM entities GROUP BY tenant_id ORDER BY tenant_id"
        ).fetchall()
    }
    total = sum(by_tenant.values())
    out: dict[str, Any] = {
        "keyed_by": "tenant_and_id" if tenant_keyed(conn) else "id",
        "entities": total,
        "by_tenant": by_tenant,
        "install_tenant": by_tenant.get(INSTALL_TENANT, 0),
        "other_tenants": total - by_tenant.get(INSTALL_TENANT, 0),
    }
    if _has_table(conn, "entity_merges"):
        out["merges"] = int(conn.execute("SELECT COUNT(*) FROM entity_merges").fetchone()[0])
        out["merges_without_own_entity"] = int(conn.execute(
            "SELECT COUNT(*) FROM entity_merges m WHERE NOT EXISTS ("
            "SELECT 1 FROM entities e WHERE e.tenant_id = m.tenant_id AND e.id = m.source_entity_id) "
            "OR NOT EXISTS (SELECT 1 FROM entities e WHERE e.tenant_id = m.tenant_id "
            "AND e.id = m.target_entity_id)"
        ).fetchone()[0])
    if _has_table(conn, "beliefs"):
        out["facts_naming_another_tenants_entity"] = int(conn.execute(
            "SELECT COUNT(*) FROM beliefs b WHERE b.valid_until IS NULL AND b.invalidated_at IS NULL "
            "AND EXISTS (SELECT 1 FROM entities o WHERE o.id = b.subject AND o.tenant_id != b.tenant_id) "
            "AND NOT EXISTS (SELECT 1 FROM entities e WHERE e.id = b.subject AND e.tenant_id = b.tenant_id)"
        ).fetchone()[0])
    return out


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[str, str, Any]]:
    """(name, declared type, default) of each column of *table*, in order."""
    return [(str(r[1]), str(r[2] or ""), r[4]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _rebuild(conn: sqlite3.Connection, table: str, ddl: str) -> int:
    """Rebuild *table* from *ddl* (its ``CREATE TABLE IF NOT EXISTS`` text)
    with every column and row it has, rowids kept. Inside the caller's
    transaction, with foreign keys off. Returns the rows copied."""
    temp = f"{table}__rekey"
    conn.execute(f"DROP TABLE IF EXISTS {temp}")
    conn.execute(ddl.replace(f"CREATE TABLE IF NOT EXISTS {table} (", f"CREATE TABLE {temp} (", 1))
    have = {name for name, _t, _d in _columns(conn, temp)}
    old = _columns(conn, table)
    for name, decl, default in old:
        if name not in have:  # a column an older build added (add_missing_columns)
            dflt = f" DEFAULT {default}" if default is not None else ""
            conn.execute(f"ALTER TABLE {temp} ADD COLUMN {name} {decl}{dflt}")
    names = ", ".join(name for name, _t, _d in old)
    copied = conn.execute(
        f"INSERT INTO {temp} (rowid, {names}) SELECT rowid, {names} FROM {table}"
    ).rowcount
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {temp} RENAME TO {table}")
    return int(copied or 0)


def migrate_entity_key(conn: sqlite3.Connection) -> dict[str, Any]:
    """Key ``entities`` (and ``entity_merges``) by ``(tenant_id, id)``, in place.

    One transaction: either the store is keyed by the pair with every row it
    had, or it is as it was. Idempotent. The caller owns the connection, which
    must not be inside a transaction (foreign keys are switched off for the
    rebuild, which SQLite allows only outside one). Raises ``sqlite3.Error``.
    """
    from kazma_core.memory.schema_v2 import (
        ENTITIES_INDEX_DDL,
        ENTITIES_TABLE_DDL,
        ENTITY_MERGES_INDEX_DDL,
        ENTITY_MERGES_TABLE_DDL,
        _ensure_fts5,
    )

    if not _has_table(conn, "entities"):
        return {"migrated": False, "reason": "no_entities_table"}
    if tenant_keyed(conn):
        return {"migrated": False, "already": True, **size_entity_key(conn)}
    if conn.in_transaction:
        conn.commit()
    before = size_entity_key(conn)
    fk_on = int(conn.execute("PRAGMA foreign_keys").fetchone()[0] or 0)
    conn.execute("PRAGMA foreign_keys = OFF")
    copied: dict[str, int] = {}
    try:
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            copied["entities"] = _rebuild(conn, "entities", ENTITIES_TABLE_DDL)
            for sql in ENTITIES_INDEX_DDL:
                conn.execute(sql)
            if _has_table(conn, "entity_merges"):
                copied["entity_merges"] = _rebuild(conn, "entity_merges", ENTITY_MERGES_TABLE_DDL)
                for sql in ENTITY_MERGES_INDEX_DDL:
                    conn.execute(sql)
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:  # whatever stopped it, the store is left as it was
                conn.execute("ROLLBACK")
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if fk_on else 'OFF'}")
    # The table's triggers went with it: put the search index's back and
    # rebuild it over the rows (their rowids are the ones it already held).
    _ensure_fts5(conn)
    try:
        conn.execute("INSERT INTO entities_fts(entities_fts) VALUES('rebuild')")
    except sqlite3.OperationalError:
        logger.warning("[entity_key] the entity search index could not be rebuilt", exc_info=True)
    conn.commit()
    unmatched = conn.execute("PRAGMA foreign_key_check(entity_merges)").fetchall() if _has_table(
        conn, "entity_merges") else []
    after = size_entity_key(conn)
    if after["entities"] != before["entities"] or not tenant_keyed(conn):
        raise sqlite3.DatabaseError("the entity key migration did not keep every row")
    logger.info(
        "[entity_key] entities keyed by (tenant_id, id): %d rows (%d of other tenants), %d merge rows, "
        "%d naming no entity of their own tenant",
        after["entities"], after["other_tenants"], copied.get("entity_merges", 0), len(unmatched),
    )
    return {
        "migrated": True,
        "copied": copied,
        "merge_rows_naming_no_own_entity": len({r[1] for r in unmatched}),
        **after,
    }
