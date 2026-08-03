"""Memory hub identity — the graph ``user`` / You node.

Beliefs often use subject ``user``. Backfill and entity resolution also create
person shells like ``ent_<hash>`` named ``User``. The canvas hub is always
``id=user`` (styling, center placement). This module keeps that hub's
*display name* and self-entity shells in sync so rename (User → Mubder)
and list→graph focus work.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

logger = logging.getLogger(__name__)

# Labels that mean "the human operator / memory hub" (case-insensitive).
SELF_LABELS = frozenset(
    {
        "user",
        "you",
        "me",
        "i",
        "myself",
        "self",
    }
)

HUB_ID = "user"


def parse_aliases(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(a) for a in raw if a is not None and str(a).strip()]
    try:
        data = json.loads(raw or "[]")
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [str(a) for a in data if a is not None and str(a).strip()]


def _norm(s: str) -> str:
    return " ".join(str(s or "").strip().lower().split())


def is_self_label(text: str) -> bool:
    return _norm(text) in SELF_LABELS


def is_self_entity(
    *,
    entity_id: str = "",
    name: str = "",
    aliases: list[str] | None = None,
    entity_type: str = "",
) -> bool:
    """True if this row is the memory hub or a legacy person shell for it."""
    eid = str(entity_id or "").strip()
    if eid.lower() in ("user", "you"):
        return True
    if is_self_label(name):
        return True
    for a in aliases or []:
        if is_self_label(str(a)):
            return True
    # Person shells that only exist as backfill "User" keep User in aliases
    # after rename to Mubder — already covered. ent_* with no self surface
    # is not the hub.
    return False


def collect_self_entity_ids(conn: sqlite3.Connection, tenant_id: str = "default") -> set[str]:
    """All entity ids that should collapse to the hub for graph focus."""
    ids: set[str] = {HUB_ID}
    try:
        rows = conn.execute(
            "SELECT id, type, name, aliases_json FROM entities WHERE tenant_id=?",
            (tenant_id,),
        ).fetchall()
    except Exception:
        return ids
    by_id: dict[str, Any] = {}
    for r in rows:
        rid = str(r["id"] if isinstance(r, sqlite3.Row) else r[0])
        by_id[rid] = r
        rtype = r["type"] if isinstance(r, sqlite3.Row) else r[1]
        rname = r["name"] if isinstance(r, sqlite3.Row) else r[2]
        raliases = r["aliases_json"] if isinstance(r, sqlite3.Row) else r[3]
        if is_self_entity(
            entity_id=rid,
            name=str(rname or ""),
            aliases=parse_aliases(raliases),
            entity_type=str(rtype or ""),
        ):
            ids.add(rid)
    # Hub aliases may list person-shell ids (ent_*) after a self-rename sync.
    hub = by_id.get(HUB_ID)
    if hub is not None:
        raliases = hub["aliases_json"] if isinstance(hub, sqlite3.Row) else hub[3]
        for a in parse_aliases(raliases):
            if a in by_id:
                ids.add(a)
    return ids


def resolve_hub_display_name(
    conn: sqlite3.Connection,
    *,
    tenant_id: str = "default",
    default: str = "You",
) -> str:
    """Best display label for the canvas hub (never empty)."""
    try:
        row = conn.execute(
            "SELECT name, aliases_json FROM entities WHERE id=? AND tenant_id=?",
            (HUB_ID, tenant_id),
        ).fetchone()
        if row:
            name = str(row["name"] or "").strip()
            if name and not is_self_label(name):
                return name
            if name and name.lower() not in ("user",):
                # Prefer "You" over bare "user" for default shell
                return name if name.lower() != "user" else default
    except Exception:
        logger.debug("[self_hub] hub row read failed", exc_info=True)

    # Legacy person shells (User → Mubder) — prefer non-generic names
    try:
        rows = conn.execute(
            "SELECT id, name, aliases_json FROM entities "
            "WHERE tenant_id=? AND lower(type) IN ('person','entity')",
            (tenant_id,),
        ).fetchall()
    except Exception:
        return default

    best = ""
    for r in rows:
        name = str(r["name"] or "").strip()
        aliases = parse_aliases(r["aliases_json"])
        if not is_self_entity(
            entity_id=str(r["id"]),
            name=name,
            aliases=aliases,
            entity_type="person",
        ):
            continue
        if name and not is_self_label(name) and name.lower() != "user":
            return name
        if name and not best:
            best = name
    if best and best.lower() not in ("user",):
        return best
    return default


def ensure_user_hub(
    conn: sqlite3.Connection,
    display_name: str,
    *,
    extra_aliases: list[str] | None = None,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Upsert ``entities.id=user`` with display name + aliases. Does not commit."""
    name = (display_name or "").strip() or "You"
    aliases: list[str] = []
    row = conn.execute(
        "SELECT name, aliases_json FROM entities WHERE id=?", (HUB_ID,)
    ).fetchone()
    if row:
        aliases = parse_aliases(row["aliases_json"])
        old = str(row["name"] or "")
        if old and old not in aliases:
            aliases.append(old)
    for a in (name, HUB_ID, "You", "User", *(extra_aliases or [])):
        if a and a not in aliases:
            aliases.append(a)
    if len(aliases) > 40:
        aliases = aliases[-40:]
    aj = json.dumps(aliases, ensure_ascii=False)
    if row:
        conn.execute(
            "UPDATE entities SET name=?, aliases_json=?, type='person', is_high_stakes=1 "
            "WHERE id=?",
            (name, aj, HUB_ID),
        )
    else:
        conn.execute(
            """INSERT INTO entities
               (id, tenant_id, type, name, aliases_json, is_high_stakes, metadata_json)
               VALUES (?, ?, 'person', ?, ?, 1, '{}')""",
            (HUB_ID, tenant_id, name, aj),
        )
    return {"id": HUB_ID, "name": name, "aliases": aliases}


def graph_focus_id(
    entity_id: str,
    *,
    name: str = "",
    aliases: list[str] | None = None,
    entity_type: str = "",
    self_ids: set[str] | None = None,
) -> str:
    """Canvas node id to select for this entity (hub collapses to ``user``)."""
    eid = str(entity_id or "").strip()
    if not eid:
        return eid
    if self_ids is not None and eid in self_ids:
        return HUB_ID
    if is_self_entity(
        entity_id=eid,
        name=name,
        aliases=aliases,
        entity_type=entity_type,
    ):
        return HUB_ID
    return eid
