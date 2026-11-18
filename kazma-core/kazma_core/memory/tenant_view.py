"""Whose memory a caller reads and changes (Spaces S1-09).

Memory keeps every row under a tenant, and a space's id is its tenant. One
question is asked all over the memory code: is this caller narrowed to its
own tenant's rows, or does it see every tenant's?

* **Kazma's own work** (a sweep, a repair, the journal's replay) names no
  tenant (``None``) and is never narrowed.
* **The owner's tenant** (``default``) sees every tenant's rows only while
  the install does not enforce Spaces: "the install's own view" of a
  single-operator install, where every row is the owner's anyway, and of the
  older multi-user mode, where an admin bound to no tenant looked after the
  rest. **Once Spaces is enforced, ``default`` is the owner's personal space
  and a space like any other**: it reads and changes its own rows only. The
  admin cannot open a member's memory.
* **Every other tenant** is always narrowed to its own rows.

Until 2026-11-18 this was written out as ``tid != "default"`` in about forty
places. They ask here now, and ``tests/test_memory_default_is_a_space.py``
fails on a new comparison of a tenant with the literal.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

__all__ = ["ledger_reach", "narrowed_to", "sees_every_tenant", "tenants_outside_spaces"]


def sees_every_tenant(tenant_id: str | None) -> bool:
    """True when a caller of *tenant_id* is not narrowed to one tenant's rows.

    ``None`` is Kazma's own work: always. The owner's tenant (``default``, or
    an empty id, which the memory code has always read as it): only while the
    install does not enforce Spaces. Any other id: never. A read of the
    in-memory switch; no I/O.
    """
    if tenant_id is None:
        return True
    return (str(tenant_id) or OWNER_SPACE_ID) == OWNER_SPACE_ID and not spaces_enforced()


def narrowed_to(tenant_id: str | None) -> str | None:
    """The one tenant a query of this caller is narrowed to, or ``None`` for
    every tenant (the form ``(? IS NULL OR tenant_id = ?)`` takes)."""
    if sees_every_tenant(tenant_id):
        return None
    return str(tenant_id) or OWNER_SPACE_ID


def ledger_reach(tenant_id: str | None) -> tuple[str, str]:
    """The two tenants whose forget-ledger rows keep a turn of *tenant_id*
    out of memory: its own, and the owner's while the install does not
    enforce Spaces (the Memory page of a single-operator install writes its
    "Don't remember" rows there, and they reach every tenant's turns). Once
    Spaces is enforced a space's ledger is its own alone; both are then the
    same id."""
    own = str(tenant_id or OWNER_SPACE_ID)
    return own, (own if spaces_enforced() else OWNER_SPACE_ID)


def _memory_by_tenant(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """``tenant -> {"memories": n, "facts": n}``: memories still remembered
    and current facts. Reads only."""
    out: dict[str, dict[str, int]] = {}
    for key, sql in (
        ("memories", "SELECT tenant_id, COUNT(*) FROM episodes WHERE tier != 'forgotten' GROUP BY tenant_id"),
        ("facts", "SELECT tenant_id, COUNT(*) FROM beliefs WHERE invalidated_at IS NULL "
                  "AND valid_until IS NULL GROUP BY tenant_id"),
    ):
        for tenant, count in conn.execute(sql):
            out.setdefault(str(tenant or OWNER_SPACE_ID), {"memories": 0, "facts": 0})[key] = int(count)
    return out


#: Copies of memory this count does not read, said with every report: the
#: tenants named there are the ones of the three local files only.
_COPIES_NOT_COUNTED = (
    "the Postgres mirror",
    "a remote vector index",
    "the Neo4j copy of the graph",
    "backups and nightly exports",
)


def _rows_by_tenant(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """``tenant -> {table: rows}`` over every table of *conn*'s database that
    has a ``tenant_id`` column (read from the schema, so a table added later
    is counted the day it is added). Reads only."""
    tables = [
        str(r[0]) for r in conn.execute(
            "SELECT m.name FROM sqlite_master m JOIN pragma_table_info(m.name) c "
            "WHERE m.type = 'table' AND c.name = 'tenant_id' AND m.sql NOT LIKE 'CREATE VIRTUAL%' "
            "ORDER BY m.name")
    ]
    out: dict[str, dict[str, int]] = {}
    for table in tables:
        for tenant, count in conn.execute(
                f"SELECT tenant_id, COUNT(*) FROM {table} GROUP BY tenant_id"):  # nosec B608 - a schema name
            out.setdefault(str(tenant or OWNER_SPACE_ID), {})[table] = int(count)
    return out


def _other_memory_files() -> list[str]:
    """The memory files beside the main one that keep rows by tenant: the
    cold store (queue, audit trail) and the forget journal."""
    from kazma_core.paths import memory_forget_journal_db, memory_ops_db

    return [memory_ops_db(), memory_forget_journal_db()]


def tenants_outside_spaces(conn: sqlite3.Connection) -> dict[str, Any]:
    """Memory kept under a tenant that is no space of this install, counted
    (the plan's risk R6: tenants of the retired per-platform and per-user
    modes have no clear owner; nothing is merged without the owner's word).

    ``{"legacy_mode": "per_platform" | "per_user" | None, "tenants": [
    {"tenant", "memories", "facts", "rows": {table: n}}, ...],
    "copies_not_counted": [...]}``, largest first. ``rows`` covers every
    table with a tenant column in the memory database, the cold store and
    the forget journal: a tenant that holds only About me, a ledger row or a
    queued task is listed too. Blocking: the settings store (the mode, the
    spaces) and the two other files. Raises ``sqlite3.Error`` and
    ``IdentityUnavailable`` to its caller.
    """
    import os

    from kazma_core.memory.config import legacy_tenant_mode
    from kazma_core.spaces import identity

    spaces = set(identity.read_registry().get("spaces") or {}) | {OWNER_SPACE_ID}
    rows = _rows_by_tenant(conn)
    for path in _other_memory_files():
        if not os.path.exists(path):
            continue
        other = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            for tenant, tables in _rows_by_tenant(other).items():
                rows.setdefault(tenant, {}).update(tables)
        finally:
            other.close()
    kept = _memory_by_tenant(conn)
    listed = [
        {"tenant": tenant, **kept.get(tenant, {"memories": 0, "facts": 0}), "rows": tables}
        for tenant, tables in rows.items()
        if tenant not in spaces
    ]
    listed.sort(key=lambda row: (-sum(row["rows"].values()), row["tenant"]))
    return {"legacy_mode": legacy_tenant_mode(), "tenants": listed,
            "copies_not_counted": list(_COPIES_NOT_COUNTED)}
