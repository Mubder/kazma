"""Operator-owned remote database capabilities; never model-supplied DSNs."""
from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from kazma_core import config_store, tenant_isolation

CONNECTIONS_KEY = "security.database_client.connections"
_NAME = re.compile(r"[a-zA-Z_][a-zA-Z_0-9]*\Z")


@dataclass(frozen=True)
class ReadConnection:
    """The targets an operator authorizes this tenant to read."""

    name: str
    dsn: str
    dialect: str
    role: str
    tables: frozenset[str]


def resolve_connection(target: str) -> ReadConnection:
    """Resolve a named capability before contacting any database."""
    if not target.startswith("connection:"):
        raise ValueError("Remote reads require an operator-defined connection:<name>; raw URIs are refused")
    name = target.removeprefix("connection:")
    if not _NAME.fullmatch(name):
        raise ValueError("Invalid database connection name")
    registry = config_store.get_config_store().get(CONNECTIONS_KEY, {})
    entry = registry.get(name) if isinstance(registry, dict) else None
    if not isinstance(entry, dict) or entry.get("enabled") is not True:
        raise ValueError("Database connection unavailable")
    tenants = entry.get("tenants")
    if not isinstance(tenants, list) or tenant_isolation.require_tenant_id() not in tenants:
        raise ValueError("Database connection unavailable")
    dsn = entry.get("dsn")
    role = entry.get("role")
    tables = entry.get("tables")
    if not isinstance(dsn, str) or not isinstance(role, str) or not role:
        raise ValueError("Database connection needs a DSN and a dedicated read role")
    url = urlsplit(dsn)
    dialect = {"postgres": "postgres", "postgresql": "postgres", "mysql": "mysql",
               "mongodb": "mongodb", "mongodb+srv": "mongodb"}.get(url.scheme)
    if not dialect or not url.hostname or not url.username or not url.path.strip("/"):
        raise ValueError("Database connection needs an explicit host, user and database")
    # DSN options can change destinations/session behaviour outside this capability.
    # PostgreSQL options/service/host overrides are particularly dangerous.
    if url.query or url.fragment:
        raise ValueError("Database connection DSN options are not supported")
    if not isinstance(tables, list) or not tables:
        raise ValueError("Database connection needs an explicit table/collection allowlist")
    for table in tables:
        parts = table.split(".") if isinstance(table, str) else []
        size = 1 if dialect == "mongodb" else 2
        if len(parts) != size or not all(_NAME.fullmatch(part) for part in parts):
            raise ValueError("Use qualified schema.table names (MongoDB: collection names)")
    return ReadConnection(name, dsn, dialect, role, frozenset(tables))
