"""What Kazma knows about you: the asker's own facts, where each came from,
since when, what it was before, and the asker's correction (the memory
framework audit's F14, part H).

The Memory page lists every fact of every subject, for the operator. This is
the person's view: only the facts about the asker (``user`` in a personal
space, the asker's own entry in a shared one, Spaces S1-11), each with

* **its source**: ``you`` (the person said it: ``provenance.USER_SOURCES``),
  ``reading`` (Kazma's reading of a conversation), ``agent`` (Kazma wrote it
  with a memory tool), ``tool`` (a tool reported it) or ``unknown``;
* **its dates**: since when it holds, and the start the person named for it
  ("since 2024", ``metadata.started``), if any;
* **its history**: the values it had before, each with when it held, as long
  as they were not forgotten.

A correction is the person's own words through their own channel
(``user_explicit``): a single-valued fact is replaced (the old value stays in
its history, and the change is one the person can undo on the Memory page,
``changes.record_update``); a list entry is ended and the new value added.
"No longer true" ends the fact at the person's trust. Each refuses with a
code the page words (``not_found``, ``no_account``, ``empty``, ``too_long``,
``unchanged``). No model call.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["HISTORY_PER_FACT", "correct_fact", "end_fact", "facts_about"]

#: Earlier values shown per fact.
HISTORY_PER_FACT = 10


def _source_of(method: str | None) -> str:
    """The page's word for where a fact came from."""
    from kazma_core.memory.provenance import USER_SOURCES

    method = str(method or "")
    if method in USER_SOURCES:
        return "you"
    return {"llm_inferred": "reading", "agent_asserted": "agent", "system_tool": "tool"}.get(method, "unknown")


def _whose(tenant_id: str, account: str | None) -> tuple[str, str] | None:
    from kazma_core.memory.profile_suggestions import _whose as whose

    return whose(tenant_id, account)


def _started(meta: str | None) -> str:
    try:
        data = json.loads(meta or "{}")
    except ValueError:
        return ""
    return str(data.get("started") or "") if isinstance(data, dict) else ""


def facts_about(conn: sqlite3.Connection, *, tenant_id: str, account: str | None = None) -> dict[str, Any]:
    """The asker's current facts, newest first within each name, each with
    its source, dates and history. Refused ``no_account`` in a shared space
    with no asker."""
    whose = _whose(tenant_id, account)
    if whose is None:
        return {"ok": False, "error": "no_account"}
    subject = whose[0]
    current = conn.execute(
        "SELECT id, predicate, predicate_type, object, valid_from, extraction_method, metadata_json "
        "FROM beliefs WHERE tenant_id = ? AND subject = ? AND valid_until IS NULL AND invalidated_at IS NULL "
        "AND object != '' ORDER BY predicate, valid_from DESC", (tenant_id, subject)).fetchall()
    earlier: dict[str, list[dict[str, Any]]] = {}
    for row in conn.execute(
            "SELECT predicate, object, valid_from, valid_until, extraction_method FROM beliefs "
            "WHERE tenant_id = ? AND subject = ? AND valid_until IS NOT NULL AND object != '' "
            "ORDER BY valid_from DESC LIMIT 500", (tenant_id, subject)):
        held = earlier.setdefault(str(row[0]), [])
        if len(held) < HISTORY_PER_FACT:
            held.append({"value": str(row[1]), "from": row[2], "until": row[3], "source": _source_of(row[4])})
    facts = [{
        "id": str(r[0]),
        "name": str(r[1]),
        "label": str(r[1]).replace("_", " "),
        "kind": str(r[2] or "set"),
        "value": str(r[3]),
        "since": r[4],
        "started": _started(r[6]),
        "source": _source_of(r[5]),
        "history": earlier.get(str(r[1]), []),
    } for r in current]
    return {"ok": True, "facts": facts}


def _own_current(conn: sqlite3.Connection, belief_id: str, tenant_id: str, subject: str) -> sqlite3.Row | None:
    row = conn.execute(
        "SELECT id, subject, predicate, predicate_type, object FROM beliefs WHERE id = ? AND tenant_id = ? "
        "AND subject = ? AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''",
        (belief_id, tenant_id, subject)).fetchone()
    return row


def correct_fact(
    conn: sqlite3.Connection, belief_id: str, value: str, *, tenant_id: str, account: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """The asker's correction of one of their own facts (module docstring)."""
    from kazma_core.memory.belief_mutation import _same_value, end_belief, mutate_belief
    from kazma_core.memory.fact_names import users_names
    from kazma_core.memory.limits import too_long
    from kazma_core.memory.speakers import room

    conn.row_factory = sqlite3.Row
    whose = _whose(tenant_id, account)
    if whose is None:
        return {"ok": False, "error": "no_account"}
    row = _own_current(conn, belief_id, tenant_id, whose[0])
    if row is None:
        return {"ok": False, "error": "not_found"}
    value = " ".join(str(value or "").split())
    if not value:
        return {"ok": False, "error": "empty"}
    if too_long(obj=value):
        return {"ok": False, "error": "too_long"}
    if _same_value(value, str(row["object"])):
        return {"ok": False, "error": "unchanged"}
    now = time.time() if now is None else float(now)
    subject, predicate = users_names(row["subject"], str(row["predicate"]))
    said_by = account if room(tenant_id).shared else None
    if str(row["predicate_type"] or "") == "set":
        ended = end_belief(conn, belief_id, tenant_id=tenant_id, extraction_method="user_explicit", now=now)
        if _refused(ended):
            conn.commit()
            return {"ok": False, "error": "refused"}
    out = mutate_belief(conn, str(subject), predicate, value, predicate_type=row["predicate_type"],
                        extraction_method="user_explicit", confidence=0.95, tenant_id=tenant_id,
                        said_by=said_by, now=now)
    conn.commit()
    if _refused(out) or not out.get("belief_id"):
        return {"ok": False, "error": "refused"}
    return {"ok": True, "belief_id": out.get("belief_id"), "replaced": belief_id}


def _refused(out: dict[str, Any]) -> bool:
    """A mutation that changed nothing: blocked by the trust gate, rejected,
    or the fact no longer current."""
    return bool(out.get("blocked") or out.get("rejected") or out.get("reason") == "not_current")


def end_fact(
    conn: sqlite3.Connection, belief_id: str, *, tenant_id: str, account: str | None = None,
    now: float | None = None,
) -> dict[str, Any]:
    """"No longer true": the asker ends one of their own facts, at their own
    trust (an ending the Memory page's changes can undo)."""
    from kazma_core.memory.belief_mutation import end_belief

    conn.row_factory = sqlite3.Row
    whose = _whose(tenant_id, account)
    if whose is None:
        return {"ok": False, "error": "no_account"}
    if _own_current(conn, belief_id, tenant_id, whose[0]) is None:
        return {"ok": False, "error": "not_found"}
    out = end_belief(conn, belief_id, tenant_id=tenant_id, extraction_method="user_explicit",
                     now=time.time() if now is None else float(now))
    conn.commit()
    if _refused(out):
        return {"ok": False, "error": "refused"}
    return {"ok": True, "ended": belief_id}
