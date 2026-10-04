"""Tenant and operator scoped composer revisions; saving never publishes."""

from __future__ import annotations

import json
import time
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS x_composers (
 tenant_id TEXT NOT NULL, actor TEXT NOT NULL, revision INTEGER NOT NULL,
 content TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(tenant_id, actor)
);
CREATE TABLE IF NOT EXISTS x_composer_history (
 tenant_id TEXT NOT NULL, actor TEXT NOT NULL, revision INTEGER NOT NULL,
 content TEXT NOT NULL, updated_at REAL NOT NULL, PRIMARY KEY(tenant_id, actor, revision)
);
"""


def load(*, actor: str) -> dict[str, Any]:
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.publication_store import get_publication_store

    with get_publication_store()._connection() as conn:
        row = conn.execute("SELECT * FROM x_composers WHERE tenant_id = ? AND actor = ?", (x_tenant_id(), actor)).fetchone()
        return {"revision": row["revision"], "content": json.loads(row["content"]), "updated_at": row["updated_at"]} if row else {"revision": 0, "content": {}, "updated_at": None}


def save(*, actor: str, expected_revision: int, content: dict[str, str]) -> dict[str, Any]:
    from kazma_core.x_api.ownership import x_tenant_id
    from kazma_core.x_api.publication_store import PublicationConflictError, get_publication_store

    tenant, now = x_tenant_id(), time.time()
    body = json.dumps(content, ensure_ascii=False)
    with get_publication_store()._connection(transaction=True) as conn:
        row = conn.execute("SELECT revision FROM x_composers WHERE tenant_id = ? AND actor = ?", (tenant, actor)).fetchone()
        revision = row[0] if row else 0
        if revision != expected_revision:
            raise PublicationConflictError("Composer changed in another tab. Reload the saved composer before saving over it.")
        conn.execute("INSERT INTO x_composers VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, actor) "
                     "DO UPDATE SET revision = excluded.revision, content = excluded.content, updated_at = excluded.updated_at",
                     (tenant, actor, revision + 1, body, now))
        conn.execute("INSERT INTO x_composer_history VALUES (?, ?, ?, ?, ?)", (tenant, actor, revision + 1, body, now))
        conn.execute("DELETE FROM x_composer_history WHERE tenant_id = ? AND actor = ? AND revision <= ?", (tenant, actor, revision - 49))
    return {"revision": revision + 1, "updated_at": now}
