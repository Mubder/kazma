"""What changed in the user's facts, and what is waiting for the user.

The second half of the memory framework audit's F1 (2026-11): a fact the
user stated changes only in ways the user can see and take back.

* **An update** -- a fact the user stated was replaced, by the user's own
  later words or by a correction the user confirmed ("home Paris ->
  Rotterdam"). ``belief_mutation`` records it in the same transaction as the
  replacement. Undo reopens the old fact and closes the new one, audited.
* **A pending change** -- the trust gate refused the model's reading over a
  fact the user stated, and the value stands in the turn's own words with no
  one but the speaker named before it ("Moved to Rotterdam last month": the
  strict reading, ``clause_subject.speaks_of_self``, cannot credit a
  sentence with no subject to the user). The user confirms it -- the value
  is then written at the user's own trust -- or dismisses it. "My sister
  moved to Rotterdam" records nothing (``clause_subject.names_no_one_else``).

The rows live in ``memory_state.db`` (``memory_changes``), so a restored
older copy holds the changes of its own time. Forget takes a change with the
fact or the turn it names (``forget._forget_fact_rows``,
``forget._forget_turn``, ``forget.forget_chat``), and with the text it holds
(``forget.forget_mentions``); it holds the old and new values, which are what
was forgotten. An open pending change expires after
:data:`PENDING_DAYS`; of the decided ones, the newest :data:`KEEP_DECIDED`
per tenant are kept.

Nothing here calls a model or leaves the machine: a confirmation is one
``mutate_belief`` at the user's own trust.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "KEEP_DECIDED",
    "PENDING_DAYS",
    "ChangeRefusedError",
    "confirm",
    "dismiss",
    "forget_chat",
    "forget_facts",
    "forget_mentioning",
    "forget_turn",
    "list_changes",
    "record_pending",
    "record_update",
    "undo",
]

UPDATE = "update"
PENDING = "pending"
#: An open pending change no one answered is let go after this many days.
PENDING_DAYS = 30
#: Decided changes kept per tenant, newest first.
KEEP_DECIDED = 200

_COLUMNS = ("id", "tenant_id", "kind", "state", "subject", "predicate", "old_belief_id", "old_value",
            "new_belief_id", "new_value", "source_session", "source_turn", "created_at", "decided_at",
            "decided_by", "noted_at")


class ChangeRefusedError(ValueError):
    """A decision that cannot be applied: the reason is a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _new_id() -> str:
    return "mc_" + uuid.uuid4().hex[:20]


def _row(conn: sqlite3.Connection, change_id: str, tenant_id: str) -> dict[str, Any] | None:
    found = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM memory_changes WHERE id = ? AND tenant_id = ?",  # nosec B608 - fixed columns
        (change_id, tenant_id or "default"),
    ).fetchone()
    return dict(zip(_COLUMNS, found, strict=True)) if found is not None else None


def record_update(
    conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str, old_belief_id: str,
    old_value: str, new_belief_id: str, new_value: str, session: str | None, turn: int | None, now: float,
    confirms: tuple[str, str] | None = None,
) -> str:
    """Record that the user's fact *old_belief_id* was replaced by
    *new_belief_id*. Runs inside the caller's transaction (``belief_mutation``
    commits it with the replacement). *confirms* is ``(change id, actor)`` of
    the pending change this replacement answers: marked ``confirmed``. Any
    other open pending change for the same fact is answered by it too:
    marked ``superseded``."""
    tenant = tenant_id or "default"
    if confirms:
        _decide(conn, confirms[0], tenant, "confirmed", confirms[1], now)
    conn.execute(
        "UPDATE memory_changes SET state = 'superseded', decided_at = ?, decided_by = 'update' "
        "WHERE tenant_id = ? AND subject = ? AND predicate = ? AND kind = ? AND state = 'open'",
        (now, tenant, subject, predicate, PENDING),
    )
    change_id = _new_id()
    conn.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, old_value, "
        "new_belief_id, new_value, source_session, source_turn, created_at) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (change_id, tenant, UPDATE, subject, predicate, old_belief_id, str(old_value or ""), new_belief_id,
         str(new_value or ""), session, turn, now),
    )
    _prune(conn, tenant, now)
    return change_id


def record_pending(
    conn: sqlite3.Connection, *, tenant_id: str, refused: dict[str, Any], new_value: str,
    user_text: str | None, session: str | None, turn: int | None, now: float | None = None,
) -> str | None:
    """Put a refused correction to the user: *refused* is ``mutate_belief``'s
    answer (``blocked == "lower_trust_source"``), *new_value* the value the
    model read from *user_text*. Recorded only when the value stands in the
    user's own words with no one else named before it; never twice for one
    value of one fact. Returns the change id, or None. Commits."""
    from kazma_core.memory.belief_mutation import _same_value
    from kazma_core.memory.clause_subject import names_no_one_else

    if refused.get("blocked") != "lower_trust_source" or not refused.get("belief_id"):
        return None
    spelled = names_no_one_else(user_text, new_value)
    if not spelled:
        return None
    new_value = spelled  # the user's own spelling, as an update keeps it
    tenant = tenant_id or "default"
    stated = conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE id = ? AND tenant_id = ?",
        (refused["belief_id"], tenant),
    ).fetchone()
    if stated is None or _same_value(stated[2], new_value):
        return None
    subject, predicate, old_value = str(stated[0]), str(stated[1]), str(stated[2] or "")
    for (value,) in conn.execute(
        "SELECT new_value FROM memory_changes WHERE tenant_id = ? AND subject = ? AND predicate = ? "
        "AND kind = ? AND state = 'open'",
        (tenant, subject, predicate, PENDING),
    ).fetchall():
        if _same_value(value, new_value):
            return None
    when = time.time() if now is None else float(now)
    change_id = _new_id()
    conn.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, old_value, "
        "new_value, source_session, source_turn, created_at) VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?)",
        (change_id, tenant, PENDING, subject, predicate, refused["belief_id"], old_value, str(new_value),
         session, turn, when),
    )
    _prune(conn, tenant, when)
    conn.commit()
    logger.info("[memory] a correction the user did not say of themself waits for them: %s %s (change %s)",
                subject, predicate, change_id)
    return change_id


def list_changes(
    conn: sqlite3.Connection, *, tenant_id: str, states: tuple[str, ...] = ("open",), limit: int = 50,
) -> list[dict[str, Any]]:
    """The tenant's changes in *states*, newest first."""
    marks = ",".join("?" for _ in states) or "''"
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM memory_changes WHERE tenant_id = ? AND state IN ({marks}) "  # nosec B608
        "ORDER BY created_at DESC LIMIT ?",
        (tenant_id or "default", *states, max(1, int(limit))),
    ).fetchall()
    return [dict(zip(_COLUMNS, r, strict=True)) for r in rows]


def _decide(conn: sqlite3.Connection, change_id: str, tenant: str, state: str, actor: str, now: float) -> bool:
    return conn.execute(
        "UPDATE memory_changes SET state = ?, decided_at = ?, decided_by = ? WHERE id = ? AND tenant_id = ? "
        "AND state = 'open'",
        (state, now, str(actor or "user")[:120], change_id, tenant),
    ).rowcount > 0


def confirm(conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user") -> dict[str, Any]:
    """The user says the pending change is right: its value is written at
    the user's own trust (``user_explicit``), replacing the old fact -- which
    records an update the user can still undo. The change is marked confirmed
    in the replacement's own transaction (``mutate_belief(confirms_change=)``)."""
    from kazma_core.memory.belief_mutation import mutate_belief

    tenant = tenant_id or "default"
    row = _row(conn, change_id, tenant)
    if row is None:
        raise ChangeRefusedError("not_found")
    if row["kind"] != PENDING or row["state"] != "open":
        raise ChangeRefusedError("not_open")
    result = mutate_belief(
        conn, row["subject"], row["predicate"], row["new_value"], predicate_type="functional",
        confidence=1.0, importance=4, extraction_method="user_explicit", tenant_id=tenant,
        source_session=row["source_session"], source_turn=row["source_turn"],
        confirms_change=(change_id, actor),
    )
    if result.get("blocked") or result.get("rejected") or result.get("error"):
        raise ChangeRefusedError("not_written")
    after = _row(conn, change_id, tenant)
    if after is not None and after["state"] == "open":
        # Nothing was replaced (the value is already the current one): the
        # answer is still the user's.
        _decide(conn, change_id, tenant, "confirmed", actor, time.time())
        conn.commit()
    return {"ok": True, "change_id": change_id, "belief_id": result.get("belief_id"),
            "action": result.get("action")}


def dismiss(conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user") -> dict[str, Any]:
    """The user says the pending change is wrong: nothing changes."""
    tenant = tenant_id or "default"
    row = _row(conn, change_id, tenant)
    if row is None:
        raise ChangeRefusedError("not_found")
    if row["kind"] != PENDING or row["state"] != "open":
        raise ChangeRefusedError("not_open")
    _decide(conn, change_id, tenant, "dismissed", actor, time.time())
    conn.commit()
    return {"ok": True, "change_id": change_id}


def undo(
    conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user",
    ops_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Take an update back: the old fact current again, the new one closed,
    in one transaction under the mutation lock. Refused when the new fact is
    no longer the current one (replaced since) or the old one was forgotten."""
    from kazma_core.memory.belief_mutation import _mutation_lock, _write_audit
    from kazma_core.memory.hygiene import beliefs_write

    tenant = tenant_id or "default"
    with _mutation_lock:
        row = _row(conn, change_id, tenant)
        if row is None:
            raise ChangeRefusedError("not_found")
        if row["kind"] != UPDATE or row["state"] != "open":
            raise ChangeRefusedError("not_open")
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            new = conn.execute(
                "SELECT 1 FROM beliefs WHERE id = ? AND tenant_id = ? AND valid_until IS NULL "
                "AND invalidated_at IS NULL", (row["new_belief_id"], tenant),
            ).fetchone()
            old = conn.execute(
                "SELECT object FROM beliefs WHERE id = ? AND tenant_id = ?", (row["old_belief_id"], tenant),
            ).fetchone()
            if new is None:
                raise ChangeRefusedError("replaced_since")
            if old is None or not str(old[0] or ""):
                raise ChangeRefusedError("forgotten")
            now = time.time()
            beliefs_write(conn, "UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE id = ? "
                          "AND tenant_id = ?", (now, now, row["new_belief_id"], tenant))
            beliefs_write(conn, "UPDATE beliefs SET valid_until = NULL, invalidated_at = NULL WHERE id = ? "
                          "AND tenant_id = ?", (row["old_belief_id"], tenant))
            _decide(conn, change_id, tenant, "undone", actor, now)
            conn.commit()
            committed = True
        finally:
            # A refusal or a failure leaves nothing changed, and the write
            # lock released.
            if not committed and conn.in_transaction:
                conn.rollback()
    _write_audit(
        ops_conn, tenant_id=tenant, event_type="undo_update", target_id=str(row["old_belief_id"]),
        actor=str(actor or "user")[:120], reason=f"undo of change {change_id}",
        state_before={"id": row["new_belief_id"]}, state_after={"id": row["old_belief_id"]},
    )
    return {"ok": True, "change_id": change_id, "restored": row["old_belief_id"]}


def forget_facts(conn: sqlite3.Connection, fact_ids: list[str]) -> int:
    """Forget takes every change naming one of *fact_ids* (old or new): it
    holds their values. Runs in the caller's transaction."""
    if not fact_ids:
        return 0
    marks = ",".join("?" for _ in fact_ids)
    return conn.execute(
        f"DELETE FROM memory_changes WHERE old_belief_id IN ({marks}) OR new_belief_id IN ({marks})",  # nosec B608
        (*fact_ids, *fact_ids),
    ).rowcount


def forget_mentioning(conn: sqlite3.Connection, *, tenant_id: str, pattern: str) -> int:
    """``forget.forget_mentions`` takes every change of the tenant whose old
    or new value holds the text: *pattern* is its LIKE pattern (escape
    ``!``). Caller's transaction."""
    return conn.execute(
        "DELETE FROM memory_changes WHERE tenant_id = ? AND (old_value LIKE ? ESCAPE '!' "
        "OR new_value LIKE ? ESCAPE '!')",
        (tenant_id or "default", pattern, pattern),
    ).rowcount


def forget_turn(conn: sqlite3.Connection, *, tenant_id: str, keys: list[str], turn: int) -> int:
    """Forget takes every change a forgotten turn made. Caller's transaction."""
    if not keys or turn <= 0:
        return 0
    marks = ",".join("?" for _ in keys)
    return conn.execute(
        f"DELETE FROM memory_changes WHERE tenant_id = ? AND source_turn = ? AND source_session IN ({marks})",  # nosec B608
        (tenant_id or "default", turn, *keys),
    ).rowcount


def forget_chat(conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str]) -> int:
    """Forget takes every change a forgotten chat made. *tenant_id* None: any
    tenant. Caller's transaction."""
    if not keys:
        return 0
    marks = ",".join("?" for _ in keys)
    sql = f"DELETE FROM memory_changes WHERE source_session IN ({marks})"  # nosec B608
    params: list[Any] = [*keys]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return conn.execute(sql, params).rowcount


def _prune(conn: sqlite3.Connection, tenant: str, now: float) -> None:
    """Let go of open pending changes no one answered in :data:`PENDING_DAYS`,
    and keep the newest :data:`KEEP_DECIDED` decided ones."""
    conn.execute(
        "UPDATE memory_changes SET state = 'expired', decided_at = ?, decided_by = 'expiry' "
        "WHERE tenant_id = ? AND kind = ? AND state = 'open' AND created_at < ?",
        (now, tenant, PENDING, now - PENDING_DAYS * 86400.0),
    )
    conn.execute(
        "DELETE FROM memory_changes WHERE tenant_id = ? AND state != 'open' AND id NOT IN ("
        "SELECT id FROM memory_changes WHERE tenant_id = ? AND state != 'open' "
        "ORDER BY COALESCE(decided_at, created_at) DESC LIMIT ?)",
        (tenant, tenant, KEEP_DECIDED),
    )
