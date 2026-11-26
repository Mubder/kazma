"""What changed in the user's facts, and what is waiting for the user.

The second half of the memory framework audit's F1 (2026-11): a fact the
user stated changes only in ways the user can see and take back.

* **An update** -- a fact the user stated was replaced, by the user's own
  later words or by a correction the user confirmed ("home Paris ->
  Rotterdam"). ``belief_mutation`` records it in the same transaction as the
  replacement. Undo reopens the old fact and closes the new one, audited.
* **A pending change** -- a question: the trust gate refused the model's
  reading over a fact the user stated ("Moved to Rotterdam last month"), and
  the value stands in the turn's own words with no one but the speaker named
  before it (``clause_subject.names_no_one_else``; "My sister moved to
  Rotterdam" records nothing). A model's reading never gets the user's trust
  by itself (the second security review of F1, 2026-11-18): only the user
  can say whether its name fits the words ("I'm visiting Dubai next week"
  read as a home). The user confirms it -- the value is then written at the
  user's own trust -- or dismisses it. The next turn's note asks at most one
  question, never about one fact twice in a day (:func:`read_changes_note`),
  marked told once the turn's model has answered (:func:`mark_noted`).
* **A reading** -- no question: the model's first reading of one of the
  user's own facts, where the user gave none (:func:`record_first_reading`).
  It is written as the model's, and any later statement of the user's
  replaces it; the Memory page shows it as Kazma's reading. "This is right"
  makes it the user's; "Not me" takes it out, and that value is never
  filed again (:func:`user_said_not_me`).

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

import json
import logging
import sqlite3
import threading
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "DECISIONS",
    "KEEP_DECIDED",
    "NOTE_OPENING",
    "PENDING_DAYS",
    "REF_CHARS",
    "ChangeRefusedError",
    "answer_pending",
    "mark_noted",
    "read_changes_note",
    "confirm",
    "decide_change",
    "dismiss",
    "forget_chat",
    "forget_facts",
    "forget_turn",
    "format_changes_note",
    "list_changes",
    "open_changes",
    "is_ending",
    "record_first_reading",
    "record_pending",
    "record_pending_end",
    "record_update",
    "resolve_ref",
    "short_ref",
    "undo",
    "user_said_not_me",
]

UPDATE = "update"
PENDING = "pending"
READING = "reading"
#: An open pending change or reading no one answered is let go after this
#: many days.
PENDING_DAYS = 30
#: Decided changes kept per tenant, newest first.
KEEP_DECIDED = 200
#: "Not me" answers kept per tenant, newest first, on their own bound: each
#: keeps a value from being filed again.
KEEP_NOT_ME = 500
#: The start of a change id the chat apps show (``mc_`` and eight hex
#: digits); a command takes any unique start of at least
#: :data:`_REF_MIN_CHARS`.
REF_CHARS = 11
_REF_MIN_CHARS = 8
#: What the user can do with a change, by kind.
DECISIONS = {"undo": (UPDATE,), "confirm": (PENDING, READING), "dismiss": (PENDING, READING)}
#: Questions in one turn's note, and how long before the same fact is asked
#: about again: a question a user ignores teaches nothing.
_NOTE_QUESTIONS = 1
_ASK_AGAIN_AFTER_S = 86400.0

_COLUMNS = ("id", "tenant_id", "kind", "state", "subject", "predicate", "old_belief_id", "old_value",
            "new_belief_id", "new_value", "source_session", "source_turn", "created_at", "decided_at",
            "decided_by", "noted_at")


class ChangeRefusedError(ValueError):
    """A decision that cannot be applied: the reason is a stable code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def is_ending(row: dict[str, Any]) -> bool:
    """Whether a change ends the user's fact (it stopped being true: no new
    value) rather than replacing it (the memory framework audit's part C2)."""
    return row.get("kind") in (UPDATE, PENDING) and not str(row.get("new_value") or "")


def _new_id() -> str:
    return "mc_" + uuid.uuid4().hex[:20]


def _not_theirs_to_decide(row: dict[str, Any], tenant_id: str) -> None:
    """Refuse (``not_theirs``) a decision on a change of another member's
    fact in a shared space: once Spaces is enforced, a change whose fact is
    filed under a member's entry (``speakers``: ``member_<account>``) is
    decided by that member or an owner of the space (the stage 3 review,
    L13). A fact under no member's entry, a personal space and an install
    that does not enforce Spaces are as before. Who decides is the account
    bound now; no one bound decides no member's change. An identity store
    that cannot answer refuses (``identity_unavailable``), never a 500.
    Asked BEFORE the memory mutation lock (``_decided_before_the_lock``):
    the identity store is no call to wait on under it (§15 D2; the second
    review of the stage 3 fixes). Blocking (the identity store)."""
    from kazma_core.spaces.scope import bound_asker, spaces_enforced

    if not spaces_enforced():
        return
    from kazma_core.memory.speakers import account_of_entry

    member = account_of_entry(row.get("subject"))
    if member is None:
        return
    person = bound_asker() or ""
    if person == member:
        return
    from kazma_core.spaces.identity import IdentityUnavailable, owns_space

    try:
        owner = bool(person) and owns_space(person, tenant_id)
    except IdentityUnavailable as exc:
        raise ChangeRefusedError("identity_unavailable") from exc
    if not owner:
        raise ChangeRefusedError("not_theirs")


def _decided_before_the_lock(conn: sqlite3.Connection, change_id: str, tenant_id: str) -> None:
    """Who may decide the change, asked before the memory mutation lock is
    taken (:func:`_not_theirs_to_decide`): a change's subject never changes,
    so the row read here decides it; the decision re-reads the row under the
    lock. A change that is not there is left to the decision's own
    ``not_found``."""
    row = _row(conn, change_id, tenant_id)
    if row is not None:
        _not_theirs_to_decide(row, tenant_id)


def _row(conn: sqlite3.Connection, change_id: str, tenant_id: str) -> dict[str, Any] | None:
    found = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM memory_changes WHERE id = ? AND tenant_id = ?",  # nosec B608 - fixed columns
        (change_id, tenant_id),
    ).fetchone()
    return dict(zip(_COLUMNS, found, strict=True)) if found is not None else None


def record_update(
    conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str, old_belief_id: str,
    old_value: str, new_belief_id: str, new_value: str, session: str | None, turn: int | None, now: float,
    confirms: tuple[str, str] | None = None, also_closed: list[str] | None = None,
) -> str:
    """Record that the user's fact *old_belief_id* was replaced by
    *new_belief_id*. Runs inside the caller's transaction (``belief_mutation``
    commits it with the replacement). *confirms* is ``(change id, actor)`` of
    the pending change this replacement answers: marked ``confirmed``. Any
    other open pending change for the same fact is answered by it too:
    marked ``superseded``. *also_closed* are the user's other list entries
    the same write closed: an undo reopens them all."""
    tenant = tenant_id
    answer_pending(conn, tenant_id=tenant, subject=subject, predicate=predicate, now=now, confirms=confirms)
    change_id = _new_id()
    conn.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, old_value, "
        "new_belief_id, new_value, source_session, source_turn, created_at, also_closed) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (change_id, tenant, UPDATE, subject, predicate, old_belief_id, str(old_value or ""), new_belief_id,
         str(new_value or ""), session, turn, now, json.dumps([str(i) for i in also_closed or []])),
    )
    _prune(conn, tenant, now)
    return change_id


def answer_pending(
    conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str, now: float,
    confirms: tuple[str, str] | None = None,
) -> None:
    """A fact of the user's own was just written for *subject* *predicate*,
    in the caller's transaction: *confirms* (``(change id, actor)``) is the
    pending change or reading it answers, marked ``confirmed``; any other
    open one of that fact is answered by it too, marked ``superseded``."""
    if confirms:
        _decide(conn, confirms[0], tenant_id, "confirmed", confirms[1], now)
    conn.execute(
        "UPDATE memory_changes SET state = 'superseded', decided_at = ?, decided_by = 'update' "
        "WHERE tenant_id = ? AND subject = ? AND predicate = ? AND kind IN (?, ?) AND state = 'open'",
        (now, tenant_id, subject, predicate, PENDING, READING),
    )


def record_pending(
    conn: sqlite3.Connection, *, tenant_id: str, refused: dict[str, Any], new_value: str,
    user_text: str | None, session: str | None, turn: int | None, now: float | None = None,
) -> str | None:
    """Put a refused correction to the user: *refused* is ``mutate_belief``'s
    answer (``blocked == "lower_trust_source"``), *new_value* the value the
    model read from *user_text*. Recorded only when the value stands in the
    user's own words with no one else named before it; never twice for one
    value of one fact; never over a fact the user stated in this same turn
    (the user's own words of a moment ago: "My name is Sam. I met Dana" asks
    nothing). Returns the change id, or None. Commits."""
    from kazma_core.memory.belief_mutation import _same_value
    from kazma_core.memory.clause_subject import names_no_one_else

    if refused.get("blocked") != "lower_trust_source" or not refused.get("belief_id"):
        return None
    spelled = names_no_one_else(user_text, new_value)
    if not spelled:
        return None
    new_value = spelled  # the user's own spelling, as an update keeps it
    tenant = tenant_id
    stated = conn.execute(
        "SELECT subject, predicate, object, source_session, source_turn FROM beliefs WHERE id = ? AND tenant_id = ?",
        (refused["belief_id"], tenant),
    ).fetchone()
    if stated is None or _same_value(stated[2], new_value):
        return None
    if session is not None and stated[3] == session and stated[4] == turn:
        return None
    subject, predicate, old_value = str(stated[0]), str(stated[1]), str(stated[2] or "")
    if _recorded(conn, tenant, PENDING, subject, predicate, new_value, ("open",)):
        return None
    return _insert(conn, kind=PENDING, tenant=tenant, subject=subject, predicate=predicate,
                   old_belief_id=refused["belief_id"], old_value=old_value, new_belief_id=None,
                   new_value=new_value, session=session, turn=turn, now=now)


def record_pending_end(
    conn: sqlite3.Connection, *, tenant_id: str, belief_id: str, user_text: str | None, session: str | None,
    turn: int | None, now: float | None = None,
) -> str | None:
    """Put to the user that their fact *belief_id* may have stopped being
    true: the model read an ending ("I don't work there anymore") the trust
    gate did not let it make. Recorded only when the fact's value stands in
    the user's own words with no one else named before it (a sister who quit
    asks nothing about the user's job); never twice for one fact. Returns the
    change id, or None. Commits."""
    from kazma_core.memory.clause_subject import ends_in_own_words

    tenant = tenant_id
    stated = conn.execute(
        "SELECT subject, predicate, object FROM beliefs WHERE id = ? AND tenant_id = ? AND valid_until IS NULL "
        "AND invalidated_at IS NULL", (belief_id, tenant)).fetchone()
    if stated is None or not ends_in_own_words(user_text, str(stated[2] or "")):
        return None
    subject, predicate, old_value = str(stated[0]), str(stated[1]), str(stated[2] or "")
    if _recorded(conn, tenant, PENDING, subject, predicate, "", ("open",)):
        return None
    return _insert(conn, kind=PENDING, tenant=tenant, subject=subject, predicate=predicate,
                   old_belief_id=belief_id, old_value=old_value, new_belief_id=None, new_value="",
                   session=session, turn=turn, now=now)


#: The user's own facts a first reading is shown for: Kazma's names for a
#: person's single-valued facts (``fact_names``).
def _reading_names() -> frozenset[str]:
    from kazma_core.memory.fact_names import KINDS

    return frozenset(KINDS)


def record_first_reading(
    conn: sqlite3.Connection, *, tenant_id: str, written: dict[str, Any], predicate: str, new_value: str,
    user_text: str | None, session: str | None, turn: int | None, now: float | None = None,
) -> str | None:
    """Show the model's first reading of one of the user's own facts on the
    Memory page -- no question: *written* is ``mutate_belief``'s answer for
    the model's fact under *predicate* (one of ``fact_names.KINDS``), where
    the user gave none. Recorded when the value stands in the user's own
    words with no one else named; never twice for one value. "This is
    right" makes it the user's, "Not me" takes it out
    (:func:`user_said_not_me`). Returns the change id, or None. Commits."""
    from kazma_core.memory.clause_subject import names_no_one_else

    if predicate not in _reading_names() or not written.get("belief_id"):
        return None
    spelled = names_no_one_else(user_text, new_value)
    if not spelled:
        return None
    tenant = tenant_id
    if _recorded(conn, tenant, READING, "user", predicate, spelled, ("open", "dismissed")):
        return None
    return _insert(conn, kind=READING, tenant=tenant, subject="user", predicate=predicate, old_belief_id=None,
                   old_value="", new_belief_id=str(written["belief_id"]), new_value=spelled,
                   session=session, turn=turn, now=now)


def user_said_not_me(conn: sqlite3.Connection, *, tenant_id: str, subject: str, predicate: str,
                     value: str) -> bool:
    """Whether the user said "Not me" to *value* of this fact: the deep pass
    never files it again (``belief_extractor._apply_beliefs_to_v2``); the
    user's own words still can. Under whatever name the model gives it now
    (``fact_names.same_statement``)."""
    from kazma_core.memory.belief_mutation import _same_value
    from kazma_core.memory.fact_names import same_statement

    return any(same_statement(str(name), predicate) and _same_value(found, value) for name, found in conn.execute(
        "SELECT predicate, new_value FROM memory_changes WHERE tenant_id = ? AND subject = ? AND kind = ? "
        "AND state = 'dismissed'", (tenant_id, subject, READING)).fetchall())


def _recorded(conn: sqlite3.Connection, tenant: str, kind: str, subject: str, predicate: str, value: str,
              states: tuple[str, ...]) -> bool:
    """Whether a change of *kind* for *value* of this fact is in *states*."""
    from kazma_core.memory.belief_mutation import _same_value

    marks = ",".join("?" for _ in states)
    return any(_same_value(found, value) for (found,) in conn.execute(
        f"SELECT new_value FROM memory_changes WHERE tenant_id = ? AND subject = ? AND predicate = ? "  # nosec B608 - placeholders only
        f"AND kind = ? AND state IN ({marks})",
        (tenant, subject, predicate, kind, *states),
    ).fetchall())


def _insert(
    conn: sqlite3.Connection, *, kind: str, tenant: str, subject: str, predicate: str, old_belief_id: str | None,
    old_value: str, new_belief_id: str | None, new_value: str, session: str | None, turn: int | None,
    now: float | None,
) -> str:
    when = time.time() if now is None else float(now)
    change_id = _new_id()
    conn.execute(
        "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_belief_id, old_value, "
        "new_belief_id, new_value, source_session, source_turn, created_at) "
        "VALUES (?, ?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (change_id, tenant, kind, subject, predicate, old_belief_id, str(old_value or ""), new_belief_id,
         str(new_value), session, turn, when),
    )
    _prune(conn, tenant, when)
    conn.commit()
    logger.info("[memory] %s of the user's fact recorded: %s %s (change %s)", kind, subject, predicate, change_id)
    return change_id


def list_changes(
    conn: sqlite3.Connection, *, tenant_id: str, states: tuple[str, ...] = ("open",), limit: int = 50,
) -> list[dict[str, Any]]:
    """The tenant's changes in *states*, newest first."""
    marks = ",".join("?" for _ in states) or "''"
    rows = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM memory_changes WHERE tenant_id = ? AND state IN ({marks}) "  # nosec B608 - fixed columns, placeholders only
        "ORDER BY created_at DESC LIMIT ?",
        (tenant_id,*states, max(1, int(limit))),
    ).fetchall()
    return [dict(zip(_COLUMNS, r, strict=True)) for r in rows]


def _decide(conn: sqlite3.Connection, change_id: str, tenant: str, state: str, actor: str, now: float) -> bool:
    return conn.execute(
        "UPDATE memory_changes SET state = ?, decided_at = ?, decided_by = ? WHERE id = ? AND tenant_id = ? "
        "AND state = 'open'",
        (state, now, str(actor or "user")[:120], change_id, tenant),
    ).rowcount > 0


def confirm(
    conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user", private: bool = False,
) -> dict[str, Any]:
    """The user says the pending change -- or Kazma's reading ("This is
    right") -- is right: its value is written at the user's own trust
    (``user_explicit``), replacing the old fact, which records an update the
    user can still undo, or raising the model's reading to the user's. The
    change is marked confirmed in the write's own transaction
    (``mutate_belief(confirms_change=)``). *private*: a private database (the
    memory benchmark's write scenarios): nothing reaches a mirror
    (``mutate_belief(private=)``)."""
    from kazma_core.memory.belief_mutation import mutate_belief

    tenant = tenant_id
    row = _row(conn, change_id, tenant)
    if row is None:
        raise ChangeRefusedError("not_found")
    _not_theirs_to_decide(row, tenant)
    if row["kind"] not in (PENDING, READING) or row["state"] != "open":
        raise ChangeRefusedError("not_open")
    if row["kind"] == READING and not _current(conn, row["new_belief_id"], tenant):
        raise ChangeRefusedError("replaced_since")  # the model read something else since
    if is_ending(row):
        return _confirm_ending(conn, row, tenant=tenant, actor=actor)
    from kazma_core.memory.fact_names import users_names

    subject, predicate = users_names(row["subject"], row["predicate"])  # recorded so already; one rule
    result = mutate_belief(
        conn, subject, predicate, row["new_value"], predicate_type="functional",
        confidence=1.0, importance=4, extraction_method="user_explicit", tenant_id=tenant,
        source_session=row["source_session"], source_turn=row["source_turn"],
        confirms_change=(change_id, actor), private=private,
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


def _confirm_ending(conn: sqlite3.Connection, row: dict[str, Any], *, tenant: str, actor: str) -> dict[str, Any]:
    """The user says their fact did stop being true: it is ended at their own
    trust, which records an update they can still undo."""
    from kazma_core.memory.belief_mutation import end_belief

    if not _current(conn, row["old_belief_id"], tenant):
        raise ChangeRefusedError("replaced_since")
    result = end_belief(conn, str(row["old_belief_id"]), tenant_id=tenant, extraction_method="user_explicit",
                        source_session=row["source_session"], source_turn=row["source_turn"],
                        confirms_change=(row["id"], actor))
    if result.get("action") != "ended":
        raise ChangeRefusedError("not_written")
    return {"ok": True, "change_id": row["id"], "belief_id": None, "action": "ended",
            "update_id": result.get("change_id")}


def _current(conn: sqlite3.Connection, belief_id: str | None, tenant: str) -> bool:
    return bool(belief_id) and conn.execute(
        "SELECT 1 FROM beliefs WHERE id = ? AND tenant_id = ? AND valid_until IS NULL AND invalidated_at IS NULL",
        (belief_id, tenant),
    ).fetchone() is not None


def dismiss(
    conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user",
    ops_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """The user says the pending change is wrong: nothing changes. Or says
    "Not me" to Kazma's reading: it is taken out -- while it is still current
    and still the model's -- in one transaction under the mutation lock,
    audited, and the deep pass never files that value again
    (:func:`user_said_not_me`)."""
    from kazma_core.memory.belief_mutation import _mutation_lock, _write_audit
    from kazma_core.memory.hygiene import beliefs_write
    from kazma_core.memory.provenance import USER_SOURCES

    tenant = tenant_id
    _decided_before_the_lock(conn, change_id, tenant)
    with _mutation_lock:
        row = _row(conn, change_id, tenant)
        if row is None:
            raise ChangeRefusedError("not_found")
        if row["kind"] not in (PENDING, READING) or row["state"] != "open":
            raise ChangeRefusedError("not_open")
        now = time.time()
        conn.execute("BEGIN IMMEDIATE")
        taken_out = None
        try:
            if row["kind"] == READING:
                held = conn.execute(
                    "SELECT extraction_method FROM beliefs WHERE id = ? AND tenant_id = ? AND valid_until IS NULL "
                    "AND invalidated_at IS NULL", (row["new_belief_id"], tenant),
                ).fetchone()
                if held is not None and held[0] not in USER_SOURCES:
                    beliefs_write(conn, "UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE id = ? "
                                  "AND tenant_id = ?", (now, now, row["new_belief_id"], tenant))
                    taken_out = row["new_belief_id"]
            _decide(conn, change_id, tenant, "dismissed", actor, now)
            conn.commit()
        finally:
            if conn.in_transaction:
                conn.rollback()
    if taken_out:
        _write_audit(
            ops_conn, tenant_id=tenant, event_type="dismiss_reading", target_id=str(taken_out),
            actor=str(actor or "user")[:120], reason=f"the user said no to change {change_id}",
            state_before={"id": taken_out}, state_after={"id": taken_out, "invalidated": True},
        )
    return {"ok": True, "change_id": change_id, "taken_out": taken_out}


def _also_closed(conn: sqlite3.Connection, change_id: str, tenant: str) -> list[str]:
    """The user's other list entries an update closed with its fact."""
    found = conn.execute("SELECT also_closed FROM memory_changes WHERE id = ? AND tenant_id = ?",
                         (change_id, tenant)).fetchone()
    try:
        ids = json.loads(found[0] or "[]") if found is not None else []
    except ValueError:
        logger.warning("[memory_changes] change %s names its closed entries unreadably", change_id)
        return []
    return [str(i) for i in ids if i] if isinstance(ids, list) else []


def _held_again(conn: sqlite3.Connection, belief_id: str, tenant: str) -> bool:
    """Whether a current fact of the same subject and name holds the closed
    fact *belief_id*'s value, written any way (``belief_mutation._same_value``):
    the user said it again after the update (the main session's check of the
    third review, fix 5)."""
    from kazma_core.memory.belief_mutation import _same_value

    closed = conn.execute("SELECT subject, predicate, object FROM beliefs WHERE id = ? AND tenant_id = ?",
                          (belief_id, tenant)).fetchone()
    if closed is None or not str(closed[2] or ""):
        return False
    return any(_same_value(current[0], closed[2]) for current in conn.execute(
        "SELECT object FROM beliefs WHERE tenant_id = ? AND subject = ? AND predicate = ? AND id != ? "
        "AND valid_until IS NULL AND invalidated_at IS NULL", (tenant, closed[0], closed[1], belief_id)))


def undo(
    conn: sqlite3.Connection, change_id: str, *, tenant_id: str, actor: str = "user",
    ops_conn: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    """Take an update back: the old fact current again, the new one closed,
    in one transaction under the mutation lock. Refused when the new fact is
    no longer the current one (replaced since) or the old one was forgotten."""
    from kazma_core.memory.belief_mutation import _mutation_lock, _write_audit
    from kazma_core.memory.hygiene import beliefs_write

    tenant = tenant_id
    _decided_before_the_lock(conn, change_id, tenant)
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
            # An ending (no new fact) has nothing to close.
            if new is None and row["new_belief_id"]:
                raise ChangeRefusedError("replaced_since")
            if old is None or not str(old[0] or ""):
                raise ChangeRefusedError("forgotten")
            now = time.time()
            if row["new_belief_id"]:
                beliefs_write(conn, "UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE id = ? "
                              "AND tenant_id = ?", (now, now, row["new_belief_id"], tenant))
            # The old fact and the user's other list entries the update closed
            # with it: every one back, never one forgotten since (its value
            # emptied), nor one the user has said again since (it is current
            # already; reopened, it would stand twice).
            already_held = []
            for closed in (row["old_belief_id"], *_also_closed(conn, change_id, tenant)):
                if _held_again(conn, closed, tenant):
                    already_held.append(closed)
                    continue
                beliefs_write(conn, "UPDATE beliefs SET valid_until = NULL, invalidated_at = NULL WHERE id = ? "
                              "AND tenant_id = ? AND COALESCE(object, '') != ''", (closed, tenant))
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
    return {"ok": True, "change_id": change_id, "restored": row["old_belief_id"], "already_held": already_held}


def forget_facts(conn: sqlite3.Connection, fact_ids: list[str]) -> int:
    """Forget takes every change naming one of *fact_ids* (old or new): it
    holds their values. Runs in the caller's transaction."""
    if not fact_ids:
        return 0
    marks = ",".join("?" for _ in fact_ids)
    return conn.execute(
        f"DELETE FROM memory_changes WHERE old_belief_id IN ({marks}) OR new_belief_id IN ({marks})",  # nosec B608 - placeholders only
        (*fact_ids, *fact_ids),
    ).rowcount


def forget_turn(conn: sqlite3.Connection, *, tenant_id: str, keys: list[str], turn: int,
                before: float | None = None) -> int:
    """Forget takes every change a forgotten turn made -- by *before*, when
    another turn may have its number by now. Caller's transaction."""
    if not keys or turn <= 0:
        return 0
    marks = ",".join("?" for _ in keys)
    return conn.execute(
        f"DELETE FROM memory_changes WHERE tenant_id = ? AND source_turn = ? AND source_session IN ({marks}) "  # nosec B608 - placeholders only
        "AND (? IS NULL OR created_at <= ?)",
        (tenant_id, turn, *keys, before, before),
    ).rowcount


def forget_chat(conn: sqlite3.Connection, *, tenant_id: str | None, keys: list[str]) -> int:
    """Forget takes every change a forgotten chat made. *tenant_id* None: any
    tenant. Caller's transaction."""
    if not keys:
        return 0
    marks = ",".join("?" for _ in keys)
    sql = f"DELETE FROM memory_changes WHERE source_session IN ({marks})"  # nosec B608 - placeholders only
    params: list[Any] = [*keys]
    if tenant_id is not None:
        sql += " AND tenant_id = ?"
        params.append(tenant_id)
    return conn.execute(sql, params).rowcount


# ── the next turn's note ─────────────────────────────────────────────────

#: How the note opens (``turn_notes.TURN_NOTE_KINDS["memory_changes"]``).
NOTE_OPENING = "MEMORY CHANGES ("
#: At most this many changes in one note, newest first.
_NOTE_MAX = 5


def format_changes_note(rows: list[dict[str, Any]]) -> str:
    """The turn note telling the model what changed in facts the user stated
    since its last reply in this chat, to say once. The values are the
    data's (fenced); Kazma's own lines -- what to say, how the user decides
    -- stand outside the fence. ``""`` for no rows."""
    from kazma_core.safety.prompt_fence import format_untrusted_block

    if not rows:
        return ""
    lines = []
    for row in rows[:_NOTE_MAX]:
        what = str(row.get("predicate") or "").replace("_", " ")
        ref = short_ref(str(row.get("id") or ""))
        if row.get("kind") == UPDATE and is_ending(row):
            lines.append(f"- {ref} ended from the user's own words: {what}: no longer \"{row.get('old_value')}\"")
        elif row.get("kind") == UPDATE:
            lines.append(f"- {ref} updated from the user's own words: {what}: was "
                         f"\"{row.get('old_value')}\", now \"{row.get('new_value')}\"")
        elif is_ending(row):
            lines.append(f"- {ref} not applied, waiting for the user: {what}: still \"{row.get('old_value')}\"; "
                         "their message may have meant it is no longer true")
        else:
            lines.append(f"- {ref} not applied, waiting for the user: {what}: still "
                         f"\"{row.get('old_value')}\"; their message may have meant \"{row.get('new_value')}\"")
    return (
        f"{NOTE_OPENING}facts the user told you, changed since your last reply in this chat. Tell the user "
        "once, briefly and in their language, then go on with what they asked; do not bring these up "
        "again in later turns.)\n"
        + format_untrusted_block("\n".join(lines), source="memory_changes")
        + "\nAn update: say what you changed; the user can undo it on the Memory page (Changes to what "
        "you told Kazma), or with `/memory undo <id>` in a chat app. A change not applied: ask once "
        "whether it is right. It changes when the user says it in their own words (for example \"I "
        "work at Initech now\"), or with \"Yes, change it\" on the Memory page, or `/memory confirm "
        "<id>` in a chat app. You cannot apply it yourself."
    )


def _to_tell(conn: sqlite3.Connection, rows: list[dict[str, Any]], tenant: str, now: float) -> list[dict[str, Any]]:
    """Of *rows* (open, untold, newest first), what one turn's note says:
    every update, and at most :data:`_NOTE_QUESTIONS` question -- never about
    a fact the user was asked about in the last :data:`_ASK_AGAIN_AFTER_S`.
    A question the user ignores teaches nothing; the rest wait on the
    Memory page and in ``/memory changes``."""
    told: list[dict[str, Any]] = []
    asked = 0
    for row in rows:
        if row["kind"] == UPDATE:
            told.append(row)
            continue
        if row["kind"] != PENDING or asked >= _NOTE_QUESTIONS:
            continue
        recently = conn.execute(
            "SELECT 1 FROM memory_changes WHERE tenant_id = ? AND subject = ? AND predicate = ? AND kind = ? "
            "AND noted_at > ? LIMIT 1",
            (tenant, row["subject"], row["predicate"], PENDING, now - _ASK_AGAIN_AFTER_S),
        ).fetchone()
        if recently is None:
            told.append(row)
            asked += 1
    return told[:_NOTE_MAX]


def read_changes_note(*, tenant_id: str, thread_id: str) -> tuple[str, list[str]]:
    """The note for this turn of the chat *thread_id*, and the changes it
    tells: the tenant's open changes made in that chat (any of its ids,
    ``forget.chat_keys``) no turn was told of yet (:func:`_to_tell`: every
    update, at most one question, never Kazma's readings). Nothing is marked
    told here: the caller marks them (:func:`mark_noted`) once the model has
    answered, so a failed call does not spend the note (the third review of
    F1, item 6). A turn of another thread -- a scheduled task, a sub-agent,
    another agent's request -- never reads them."""
    from kazma_core.memory.forget import chat_keys

    if not thread_id:
        return "", []
    keys = [k for k in (chat_keys(thread_id) or [thread_id]) if k]
    tenant = tenant_id
    marks = ",".join("?" for _ in keys)
    conn = _open()
    try:
        found = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM memory_changes WHERE tenant_id = ? AND state = 'open' "  # nosec B608 - fixed columns, placeholders only
            f"AND kind IN (?, ?) AND noted_at IS NULL AND source_session IN ({marks}) "
            "ORDER BY created_at DESC LIMIT 50",
            (tenant, UPDATE, PENDING, *keys),
        ).fetchall()
        rows = _to_tell(conn, [dict(zip(_COLUMNS, r, strict=True)) for r in found], tenant, time.time())
    finally:
        conn.close()
    if not rows:
        return "", []
    return format_changes_note(rows), [str(row["id"]) for row in rows]


def mark_noted(*, tenant_id: str, ids: list[str]) -> None:
    """Mark the changes *ids* told (``noted_at``): the turn that read them
    (:func:`read_changes_note`) got its model's answer."""
    if not ids:
        return
    now = time.time()
    conn = _open()
    try:
        conn.executemany("UPDATE memory_changes SET noted_at = ? WHERE id = ? AND tenant_id = ? AND noted_at IS NULL",
                         [(now, change_id, tenant_id) for change_id in ids])
        conn.commit()
    finally:
        conn.close()


# ── for a caller with no connection of its own (the chat apps' /memory) ──


_schema_ready: set[str] = set()
_schema_lock = threading.Lock()


def _open() -> sqlite3.Connection:
    """The memory database, its schema made ready once per file per process."""
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.memory.schema_v2 import ensure_primary_schema
    from kazma_core.paths import primary_memory_db

    path = str(primary_memory_db())
    conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
    with _schema_lock:
        if path not in _schema_ready:
            ensure_primary_schema(conn)
            _schema_ready.add(path)
            return conn
    apply_sqlite_pragmas(conn)
    return conn


def short_ref(change_id: str) -> str:
    """The start of *change_id* a chat shows (:data:`REF_CHARS`)."""
    return str(change_id or "")[:REF_CHARS]


def resolve_ref(conn: sqlite3.Connection, ref: str, *, tenant_id: str) -> str:
    """The id of the tenant's change *ref* names: the whole id, or a start of
    it at least eight characters long that no other change of the tenant
    shares. Refused ``not_found`` or ``ambiguous``."""
    from kazma_core.db.sql_patterns import like_literal

    ref = (ref or "").strip()
    if len(ref) < _REF_MIN_CHARS:
        raise ChangeRefusedError("not_found")
    found = [r[0] for r in conn.execute(
        "SELECT id FROM memory_changes WHERE tenant_id = ? AND id LIKE ? ESCAPE '!' LIMIT 2",
        (tenant_id,like_literal(ref) + "%"),
    ).fetchall()]
    if not found:
        raise ChangeRefusedError("not_found")
    if len(found) > 1:
        raise ChangeRefusedError("ambiguous")
    return str(found[0])


def open_changes(*, tenant_id: str, limit: int = 20) -> list[dict[str, Any]]:
    """The tenant's open changes, newest first, on a connection of its own."""
    conn = _open()
    try:
        return list_changes(conn, tenant_id=tenant_id, limit=limit)
    finally:
        conn.close()


def decide_change(ref: str, decision: str, *, tenant_id: str, actor: str) -> dict[str, Any]:
    """Undo, confirm or dismiss the tenant's change *ref* names
    (:func:`resolve_ref`), on connections of its own. The answer carries the
    change as it was (``change``); a confirmation also names the update it
    made (``update_ref``), which can be undone in turn. A refusal raises
    :class:`ChangeRefusedError` (``not_found``, ``ambiguous``, ``not_open``,
    ``replaced_since``, ``forgotten``, ``not_written``, ``wrong_kind``)."""
    if decision not in DECISIONS:
        raise ChangeRefusedError("unknown_decision")
    tenant = tenant_id
    conn = _open()
    ops = None
    try:
        change_id = resolve_ref(conn, ref, tenant_id=tenant)
        row = _row(conn, change_id, tenant)
        if row is None:
            raise ChangeRefusedError("not_found")
        if row["kind"] not in DECISIONS[decision]:
            raise ChangeRefusedError("wrong_kind")
        if decision in ("undo", "dismiss"):
            from kazma_core.memory.schema_v2 import ensure_ops_schema
            from kazma_core.paths import memory_ops_db

            ops = sqlite3.connect(memory_ops_db(), timeout=30, check_same_thread=False)
            ensure_ops_schema(ops)
        if decision == "undo":
            out = undo(conn, change_id, tenant_id=tenant, actor=actor, ops_conn=ops)
        elif decision == "confirm":
            out = confirm(conn, change_id, tenant_id=tenant, actor=actor)
            made = (out["update_id"],) if out.get("update_id") else conn.execute(
                "SELECT id FROM memory_changes WHERE tenant_id = ? AND kind = ? AND state = 'open' "
                "AND new_belief_id = ? ORDER BY created_at DESC LIMIT 1",
                (tenant, UPDATE, out.get("belief_id")),
            ).fetchone()
            out["update_ref"] = short_ref(made[0]) if made else ""
        else:
            out = dismiss(conn, change_id, tenant_id=tenant, actor=actor, ops_conn=ops)
        return {**out, "change": row}
    finally:
        if ops is not None:
            ops.close()
        conn.close()


def _prune(conn: sqlite3.Connection, tenant: str, now: float) -> None:
    """Let go of open pending changes and readings no one answered in
    :data:`PENDING_DAYS`, and keep the newest :data:`KEEP_DECIDED` decided
    ones; a "Not me" (a dismissed reading) on its own bound, the newest
    :data:`KEEP_NOT_ME`: it is what keeps the value from being filed again
    (:func:`user_said_not_me`), and it must not grow without end (the third
    review of F1, item 6)."""
    conn.execute(
        "UPDATE memory_changes SET state = 'expired', decided_at = ?, decided_by = 'expiry' "
        "WHERE tenant_id = ? AND kind IN (?, ?) AND state = 'open' AND created_at < ?",
        (now, tenant, PENDING, READING, now - PENDING_DAYS * 86400.0),
    )
    conn.execute(
        "DELETE FROM memory_changes WHERE tenant_id = ? AND state != 'open' "
        "AND NOT (kind = ? AND state = 'dismissed') AND id NOT IN ("
        "SELECT id FROM memory_changes WHERE tenant_id = ? AND state != 'open' "
        "ORDER BY COALESCE(decided_at, created_at) DESC LIMIT ?)",
        (tenant, READING, tenant, KEEP_DECIDED),
    )
    conn.execute(
        "DELETE FROM memory_changes WHERE tenant_id = ? AND kind = ? AND state = 'dismissed' AND id NOT IN ("
        "SELECT id FROM memory_changes WHERE tenant_id = ? AND kind = ? AND state = 'dismissed' "
        "ORDER BY COALESCE(decided_at, created_at) DESC LIMIT ?)",
        (tenant, READING, tenant, READING, KEEP_NOT_ME),
    )
