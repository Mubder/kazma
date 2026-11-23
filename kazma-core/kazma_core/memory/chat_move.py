"""What memory holds of a chat follows it when a person moves it between
their own space and a shared one (Spaces S2-12, ``kazma_ui.chat_move``).

Agreed with the memory session (2026-11-23): memory is **rebuilt** in the
space the chat moved to, from the chat store, never moved row by row --
except what the chat store cannot rebuild, which is **carried**:

* **Carried** into the target first, before anything leaves the source (a
  stop between the two loses nothing: the chat move review's H1): the
  person's own facts the chat produced -- at the user's word, by a memory
  tool or in their own words (``user_explicit``, ``agent_asserted``,
  ``user_stated``) -- current and ended alike, at their own ``valid_from``
  and trust, the subject mapped ``user`` <-> the mover's ``member_<account>``
  entry; the person's decisions on them (a correction made on the page that
  replaced one, carried with it; "No longer true", "this is wrong" and Undo,
  which closed one); the agent's notes and the swarm results the chat
  started (episodes, written again through the episode writer); and the
  "Not me" and "No, keep it" answers to this chat's facts. A value the
  person corrected or ended is **never rebuilt as current** (the review's
  H4): it is closed again after the rebuild's quick pass, and the deep pass
  is told it on the rebuilt turns (``metadata.not_again``).
* **Kept out** in the target too: every ledger row of the chat -- the whole
  chat kept out, each turn the person forgot -- journaled, or reconcile
  would bring a forgotten turn back from the chat store. The source's rows
  go once copied (the review's M3): moved back later, a keep-out the person
  lifted in the target does not come back.
* **Retired** in the source as a forget leaves it -- memories, facts, the
  changes the chat put to the user, the weekly summaries over its turns,
  the lessons it taught -- and then its tombstones (the chat's memories and
  facts) are **deleted**: under the same ids a move back would write
  nothing and say "written" (``dual_write._id_in_tenant``) -- only the
  tombstones the move itself made: a turn the person forgot earlier stays
  forgotten. The chat store holds the turns. No ledger row names the chat
  there: it left, it was not forgotten. A current fact another chat of the
  source states too (a restatement keeps the first row's source) stays
  current there, named by that chat now (the review's H5). What this chat's
  facts closed in the source (a home another chat stated, a list entry an
  update closed) is reopened while the chat's fact still held its place --
  never one the person ended or undid since (the review's M2). Open pending
  changes go; the Changes card's history of the chat and its Undo do not
  move (the dialog says so).
* **Rebuilt** in the target at once (turn reconcile's cursor may already be
  past the chat): its turns written by ``turn_reconcile._reconcile_session``
  with the target's rules (who said what, ``speakers``), the quick pass on
  each, the deep pass queued as Kazma's own work (``rebuild`` in its
  payload: not counted against the mover's limit), and the weeks the chat
  covers queued for summaries again.

Writers already in flight -- a queued post-turn write, a deep pass, a
reconcile pass -- cannot write into the source afterwards: the episode
writer refuses a chat whose thread is recorded in another space than the one
it writes for (``forget.refuses_write``).

Queued as a durable task by the move (``chat_move_memory``, which the
memory queue retries) and journaled first (``forget_journal`` kind
``chat_moved``): a move a stop cut off is finished by the forget convergence
pass, and a restored memory database is moved again while the chat is still
away from its source. Idempotent. Blocking (SQLite, the identity store):
call it off the loop.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from typing import Any

from kazma_core.memory import forget as _forget
from kazma_core.memory.forget import (
    FORGOTTEN_TIER,
    _after_facts,
    _chat_notes,
    _delete_remote_vector,
    _forget_archived_facts,
    _forget_chat_facts,
    _ledger_rows,
    _memory_texts,
    _open,
    _scrub_legacy_copies,
    _tombstone,
    purge_search_index,
)

__all__ = ["CARRIED_METHODS", "REBUILT_SOURCE", "move_chat_memory", "replay_chat_moved"]

logger = logging.getLogger(__name__)

#: The source a moved chat's rebuilt turns are written under: turn
#: reconcile's facts pass reads ``turn_reconcile`` turns only, so it does not
#: extract them a second time (nor queue their deep pass as the person's).
REBUILT_SOURCE = "chat_move"

#: The facts a moved chat carries to its new space as they are: the user's
#: own decisions, a memory tool's notes and the user's own statements (the
#: rebuild writes the same again, and the trust gate keeps what was carried).
#: Kazma's readings are written again from the turns.
CARRIED_METHODS = ("user_explicit", "agent_asserted", "user_stated")


def _still_away(keys: list[str], from_space: str) -> bool:
    """Whether the chat *keys* is recorded in another space than
    *from_space*: a moved chat that has not come back. Blocking."""
    from kazma_core.spaces import threads

    for key in keys:
        record = threads.record_of(key)
        if record is not None:
            return record.space_id != from_space
    return False


def _mapped(subject: str, *, mover: str, from_space: str, to_space: str) -> str | None:
    """A carried fact's subject in the target: the mover's ``user`` and
    their ``member_<account>`` entry are the same person, filed as each
    space files the speaker (``speakers.Room.entry_for``). Another member's
    entry does not move (a chat others wrote in is never taken out)."""
    from kazma_core.memory import speakers

    source, target = speakers.room(from_space), speakers.room(to_space)
    mine = source.entry_for(mover) if source.shared else speakers.USER
    if subject == mine or subject == speakers.USER:
        return target.entry_for(mover) if target.shared else speakers.USER
    if speakers.is_member_entry(subject):
        return None
    return subject


_FACT_COLUMNS = ("id, subject, predicate, predicate_type, object, confidence, structural_importance, valid_from, "
                 "valid_until, invalidated_at, extraction_method, source_session, source_turn, metadata_json")


def _chat_facts(conn: sqlite3.Connection, keys: list[str],
                from_space: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """``(carried, decided)``. *carried*: the person's own facts the chat
    produced (:data:`CARRIED_METHODS`), current or decided, with the
    corrections made on the page that replaced one of them, oldest first.
    *decided*: every fact of the chat the person closed -- ended, called
    wrong, undone, or replaced by anything but a later fact of this chat,
    which the rebuild writes again in its order."""
    marks = ",".join("?" for _ in keys)
    rows = [dict(r) for r in conn.execute(
        f"SELECT {_FACT_COLUMNS} FROM beliefs WHERE tenant_id = ? AND source_session IN ({marks}) "  # nosec B608 - constants and placeholders
        "AND object != '' ORDER BY valid_from", (from_space, *keys)).fetchall()]
    carried: list[dict[str, Any]] = []
    decided: list[dict[str, Any]] = []
    for row in rows:
        if row["valid_until"] is not None or row["invalidated_at"] is not None:
            by_the_chat = conn.execute(
                f"SELECT 1 FROM beliefs WHERE tenant_id = ? AND supersedes_id = ? AND source_session IN ({marks}) "  # nosec B608 - placeholders only
                "LIMIT 1", (from_space, row["id"], *keys)).fetchone()
            if by_the_chat is not None:
                continue
            decided.append(row)
        if row["extraction_method"] in CARRIED_METHODS:
            carried.append(row)
    if decided:
        ids = ",".join("?" for _ in decided)
        carried.extend(dict(r) for r in conn.execute(
            f"SELECT {_FACT_COLUMNS} FROM beliefs WHERE tenant_id = ? AND supersedes_id IN ({ids}) "  # nosec B608 - constants and placeholders
            "AND extraction_method = 'user_explicit' AND COALESCE(source_session, '') = '' "
            "AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''",
            (from_space, *[d["id"] for d in decided])).fetchall())
    carried.sort(key=lambda r: float(r["valid_from"] or 0))
    return carried, decided


def _not_again(decided: list[dict[str, Any]], *, mover: str, from_space: str,
               to_space: str) -> list[tuple[str, str, str, Any, Any]]:
    """The decided facts as the target files them: ``(subject, predicate,
    casefolded value, valid_until, invalidated_at)``."""
    out = []
    for row in decided:
        subject = _mapped(str(row["subject"]), mover=mover, from_space=from_space, to_space=to_space)
        if subject:
            out.append((subject, str(row["predicate"]), str(row["object"]).casefold(),
                        row["valid_until"], row["invalidated_at"]))
    return out


def _keep_decided_closed(conn: sqlite3.Connection, decided: list[tuple[str, str, str, Any, Any]],
                         keys: list[str], to_space: str) -> int:
    """No value the person corrected or ended is current in *to_space* from
    this chat: one the carry or the rebuild's quick pass wrote is closed as
    the person closed it. Returns how many."""
    from kazma_core.memory.hygiene import beliefs_write

    marks = ",".join("?" for _ in keys)
    closed = 0
    for subject, predicate, value, until, invalidated in decided:
        for bid, obj in conn.execute(
                f"SELECT id, object FROM beliefs WHERE tenant_id = ? AND subject = ? AND predicate = ? "  # nosec B608 - placeholders only
                f"AND source_session IN ({marks}) AND valid_until IS NULL AND invalidated_at IS NULL "
                "AND object != ''", (to_space, subject, predicate, *keys)).fetchall():
            if str(obj).casefold() != value:
                continue
            beliefs_write(conn, "UPDATE beliefs SET valid_until = ?, invalidated_at = ? WHERE id = ? AND tenant_id = ?",
                          (until if until is not None or invalidated is not None else time.time(), invalidated,
                           bid, to_space))
            closed += 1
    conn.commit()
    return closed


def _kept_by_another_chat(conn: sqlite3.Connection, keys: list[str], from_space: str) -> int:
    """A current fact of the chat that another chat of the source states too
    stays current there, named by that chat from now: a restatement keeps
    the first row's source (``mutate_belief``), and retiring it would take a
    fact another chat still says. Returns how many."""
    from kazma_core.memory.hygiene import beliefs_write
    from kazma_core.memory.mention_match import Needle

    marks = ",".join("?" for _ in keys)
    kept = 0
    for bid, value in conn.execute(
            f"SELECT id, object FROM beliefs WHERE tenant_id = ? AND source_session IN ({marks}) "  # nosec B608 - placeholders only
            "AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''", (from_space, *keys)).fetchall():
        needle = Needle.of(str(value))
        if not needle.folded:
            continue
        for session, turn, text in conn.execute(
                f"SELECT session_id, turn_number, user_text FROM episodes WHERE tenant_id = ? "  # nosec B608 - placeholders only
                f"AND session_id NOT IN ({marks}) AND session_id != 'memory_store' AND tier != ? "
                "AND user_text LIKE ? ORDER BY created_at", (from_space, *keys, FORGOTTEN_TIER, f"%{value}%")).fetchall():
            if needle.in_text(text):
                beliefs_write(conn, "UPDATE beliefs SET source_session = ?, source_turn = ? WHERE id = ? "
                              "AND tenant_id = ?", (session, turn, bid, from_space))
                kept += 1
                break
    conn.commit()
    return kept


def _note_rows(conn: sqlite3.Connection, ids: list[str], tenant_id: str) -> list[dict[str, Any]]:
    if not ids:
        return []
    marks = ",".join("?" for _ in ids)
    out: list[dict[str, Any]] = []
    for r in conn.execute(
        "SELECT session_id, turn_number, user_text, assistant_text, summary_text, tier, "
        "structural_importance AS importance, created_at, metadata_json FROM episodes "
        f"WHERE id IN ({marks}) AND tenant_id = ? ORDER BY created_at", (*ids, tenant_id),  # nosec B608 - placeholders only
    ).fetchall():
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        out.append({**dict(r), "metadata": meta if isinstance(meta, dict) else {}})
    return out


def _answer_rows(conn: sqlite3.Connection, keys: list[str], from_space: str) -> list[dict[str, Any]]:
    """The person's answers to this chat's facts that the rebuild would ask
    again: "Not me" (``changes.user_said_not_me``) and "No, keep it" (a
    question dismissed: ``changes._answered_no_from``)."""
    from kazma_core.memory import changes

    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        "SELECT kind, subject, predicate, old_value, new_value, source_session, source_turn, created_at, decided_at, "
        f"decided_by FROM memory_changes WHERE tenant_id = ? AND kind IN (?, ?) AND state = 'dismissed' "  # nosec B608 - placeholders only
        f"AND source_session IN ({marks})",
        (from_space, changes.READING, changes.PENDING, *keys),
    ).fetchall()
    return [dict(r) for r in rows]


def _closed_by(conn: sqlite3.Connection, keys: list[str], from_space: str) -> list[str]:
    """What this chat's facts closed in the source and still keep closed: a
    fact another chat stated that one of them superseded, and the list
    entries an update of this chat closed with it (``changes.also_closed``)
    -- only while that fact of the chat is current. One the person ended,
    called wrong or undid since closed nothing that should come back: the
    person's decision stands (the chat move review's M2)."""
    marks = ",".join("?" for _ in keys)
    mine = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM beliefs WHERE tenant_id = ? AND source_session IN ({marks}) "  # nosec B608 - placeholders only
        "AND valid_until IS NULL AND invalidated_at IS NULL AND object != ''",
        (from_space, *keys))]
    if not mine:
        return []
    ids = ",".join("?" for _ in mine)
    closed = [str(r[0]) for r in conn.execute(
        f"SELECT supersedes_id FROM beliefs WHERE id IN ({ids}) AND tenant_id = ? "  # nosec B608 - placeholders only
        "AND supersedes_id IS NOT NULL", (*mine, from_space))]
    for (also,) in conn.execute(
        f"SELECT also_closed FROM memory_changes WHERE tenant_id = ? AND new_belief_id IN ({ids})",  # nosec B608 - placeholders only
        (from_space, *mine),
    ):
        try:
            extra = json.loads(also or "[]")
        except ValueError:
            extra = []
        closed.extend(str(i) for i in extra if i) if isinstance(extra, list) else None
    own = set(mine)
    return [c for c in dict.fromkeys(closed) if c and c not in own]


def _reopen(conn: sqlite3.Connection, closed: list[str], from_space: str) -> list[str]:
    """Reopen each fact *closed* names, once this chat's facts are retired,
    unless something else holds its place now: a current fact of the same
    subject and name (one value), or the same value (a list entry,
    ``changes._held_again``), or it was forgotten (its value emptied)."""
    from kazma_core.memory.changes import _held_again
    from kazma_core.memory.hygiene import beliefs_write

    reopened: list[str] = []
    for bid in closed:
        row = conn.execute(
            "SELECT subject, predicate, predicate_type, object, valid_until, invalidated_at FROM beliefs "
            "WHERE id = ? AND tenant_id = ?", (bid, from_space)).fetchone()
        if row is None or not str(row["object"] or "") or (row["valid_until"] is None and row["invalidated_at"] is None):
            continue
        if str(row["predicate_type"] or "") != "set":
            held = conn.execute(
                "SELECT 1 FROM beliefs WHERE tenant_id = ? AND subject = ? AND predicate = ? AND id != ? "
                "AND valid_until IS NULL AND invalidated_at IS NULL", (from_space, row["subject"], row["predicate"], bid),
            ).fetchone()
            if held is not None:
                continue
        elif _held_again(conn, bid, from_space):
            continue
        beliefs_write(conn, "UPDATE beliefs SET valid_until = NULL, invalidated_at = NULL WHERE id = ? "
                      "AND tenant_id = ? AND COALESCE(object, '') != ''", (bid, from_space))
        reopened.append(bid)
    return reopened


def _copy_ledger(conn: sqlite3.Connection, keys: list[str], *, from_space: str, to_space: str) -> int:
    """Every ledger row of the chat in *from_space* -- the whole chat kept
    out, each turn the person forgot -- written for *to_space* too,
    journaled first like any keep-out (a restore keeps them). Returns the
    rows copied."""
    from kazma_core.memory import forget_journal

    marks = ",".join("?" for _ in keys)
    rows = _ledger_rows(
        conn,
        "SELECT DISTINCT turn_number, question_sha FROM memory_forgotten "
        f"WHERE tenant_id = ? AND session_key IN ({marks}) ORDER BY turn_number, question_sha",  # nosec B608 - placeholders only
        (from_space, *keys),
    )
    now = time.time()
    detail = forget_journal.keys_detail(keys)
    for turn, sha in rows:
        turn, sha = int(turn or 0), str(sha or "")
        if turn == 0 and not sha:
            seq = forget_journal.append("keep_out", tenant_id=to_space, session_key=keys[0], detail=detail)
        else:
            seq = forget_journal.append("turn", tenant_id=to_space, session_key=keys[0], turn_number=turn,
                                        question_sha=sha, detail=detail)
        conn.executemany(
            "INSERT OR IGNORE INTO memory_forgotten "
            "(tenant_id, session_key, turn_number, question_sha, episode_id, forgotten_at) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            [(to_space, k, turn, sha, now) for k in keys],
        )
        forget_journal.mark_applied(conn, seq)
    # The source's rows go (the review's M3): moved back later, a keep-out
    # the person lifted in the target must not come back from them.
    conn.execute(f"DELETE FROM memory_forgotten WHERE tenant_id = ? AND session_key IN ({marks})",  # nosec B608 - placeholders only
                 (from_space, *keys))
    conn.commit()
    return len(rows)


def _retire_source(conn: sqlite3.Connection, keys: list[str], from_space: str) -> dict[str, int]:
    """The chat's memory in *from_space* retired as a forget leaves it, what
    its facts closed reopened, and its tombstones deleted."""
    marks = ",".join("?" for _ in keys)
    episodes = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM episodes WHERE session_id IN ({marks}) AND tenant_id = ?",  # nosec B608 - placeholders only
        (*keys, from_space)).fetchall()]
    notes = _chat_notes(conn, tenant_id=from_space, keys=keys)
    live = [e for e in dict.fromkeys(episodes + notes)]
    # What the move itself empties, and so deletes after: a memory or a fact
    # the person forgot before is no tombstone of the move's (LOW).
    ids = ",".join("?" for _ in live) or "''"
    made_episodes = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM episodes WHERE id IN ({ids}) AND tenant_id = ? AND tier != ?",  # nosec B608 - placeholders only
        (*live, from_space, FORGOTTEN_TIER)).fetchall()]
    made_facts = [str(r[0]) for r in conn.execute(
        f"SELECT id FROM beliefs WHERE source_session IN ({marks}) AND tenant_id = ? AND object != ''",  # nosec B608 - placeholders only
        (*keys, from_space)).fetchall()]
    closed = _closed_by(conn, keys, from_space)
    now = time.time()
    texts = _memory_texts(conn, live)
    for eid in live:
        _tombstone(conn, eid, now=now, by="moved")
    _scrub_legacy_copies(conn, live, texts)
    facts = _forget_chat_facts(conn, tenant_id=from_space, keys=keys, now=now)
    _forget_archived_facts(conn, tenant_id=from_space, keys=keys, turn=None, now=now)
    from kazma_core.memory import changes

    changes.forget_chat(conn, tenant_id=from_space, keys=keys)
    if live:
        from kazma_core.memory.topic_summaries import on_turns_forgotten

        on_turns_forgotten(conn, live)
    reopened = _reopen(conn, closed, from_space)
    conn.commit()
    if live:
        from kazma_core.memory.state_backend import remirror_episodes

        remirror_episodes(conn, live)
        for eid in live:
            _delete_remote_vector(conn, eid, from_space)
    _after_facts(conn, owner=from_space, changed=facts, keys=keys, turn=None)
    from kazma_core.skills.self_improvement import forget_lessons_from

    lessons = forget_lessons_from(keys, None, tenant_id=from_space)
    # The tombstones it made go: under the same ids a move back would write
    # nothing and say "written". The chat store holds the turns; the mirror
    # keeps its tombstone copies, which recall's top-up leaves out.
    _delete_moved_tombstones(conn, made_episodes, made_facts, from_space)
    purge_search_index(conn)
    conn.commit()
    return {"retired": len(live), "facts_retired": len(facts), "reopened": len(reopened),
            "lessons_removed": int(lessons or 0), "deleted": len(made_episodes) + len(made_facts)}


def _delete_moved_tombstones(conn: sqlite3.Connection, episode_ids: list[str], fact_ids: list[str],
                             tenant_id: str) -> None:
    """Delete the tombstones a move left in its source (declared in
    ``tests/test_memory_deletes.py``): only rows the move itself emptied, in
    the space the chat left."""
    if episode_ids:
        marks = ",".join("?" for _ in episode_ids)
        conn.execute(
            f"DELETE FROM episodes WHERE id IN ({marks}) AND tenant_id = ? AND tier = ? "  # nosec B608 - placeholders only
            "AND user_text = '' AND assistant_text = ''", (*episode_ids, tenant_id, FORGOTTEN_TIER))
    if fact_ids:
        marks = ",".join("?" for _ in fact_ids)
        conn.execute(
            f"DELETE FROM beliefs WHERE id IN ({marks}) AND tenant_id = ? AND object = ''",  # nosec B608 - placeholders only
            (*fact_ids, tenant_id))


def _carry_notes(notes: list[dict[str, Any]], *, mover: str, to_space: str) -> int:
    """The agent's notes and the chat's swarm results, written again in
    *to_space* through the episode writer (their own ids, vectors and
    mirror). Raises when one is not written: its source is kept, and the
    journal entry stays pending for the convergence pass."""
    from kazma_core.memory import speakers
    from kazma_core.memory.dual_write import write_episode

    said_by = speakers.room(to_space).said_by(mover)
    written = 0
    for note in notes:
        meta = {k: v for k, v in note["metadata"].items() if k not in ("forgotten", "said_by")}
        if said_by:
            meta["said_by"] = said_by
        status, _eid = write_episode(
            session_id=str(note["session_id"]), turn_number=int(note["turn_number"] or 0),
            user_text=str(note["user_text"] or ""), assistant_text=str(note["assistant_text"] or ""),
            summary_text=str(note["summary_text"] or ""), tenant_id=to_space,
            tier=str(note["tier"] or "episodic"), importance=int(note["importance"] or 1),
            source=str(meta.get("source") or "chat_move"), created_at=float(note["created_at"] or time.time()),
            metadata=meta,
        )
        if status == "failed":
            raise RuntimeError("a note of the moved chat was not written in its new space")
        written += status == "written"
    return written


def _carry_facts(conn: sqlite3.Connection, facts: list[dict[str, Any]], *, mover: str,
                 from_space: str, to_space: str) -> int:
    """The person's own facts, filed in *to_space* at their own time and
    trust (``mutate_belief``: the same value again is no new row)."""
    from kazma_core.memory import speakers
    from kazma_core.memory.belief_mutation import mutate_belief

    said_by = speakers.room(to_space).said_by(mover)
    carried = 0
    for fact in facts:
        subject = _mapped(str(fact["subject"]), mover=mover, from_space=from_space, to_space=to_space)
        if not subject:
            continue
        try:
            meta = json.loads(fact["metadata_json"] or "{}")
        except (TypeError, ValueError):
            meta = {}
        out = mutate_belief(
            conn, subject, str(fact["predicate"]), str(fact["object"]),
            predicate_type=str(fact["predicate_type"] or "") or None,
            confidence=float(fact["confidence"] or 0.5), importance=int(fact["structural_importance"] or 1),
            extraction_method=str(fact["extraction_method"]), tenant_id=to_space,
            source_session=fact["source_session"], source_turn=fact["source_turn"],
            now=float(fact["valid_from"]), said_by=said_by,
            started=meta.get("started") if isinstance(meta, dict) else None,
        )
        carried += out.get("action") not in (None, "noop")
    return carried


def _carry_answers(conn: sqlite3.Connection, rows: list[dict[str, Any]], *, mover: str,
                   from_space: str, to_space: str) -> int:
    from kazma_core.memory import changes

    carried = 0
    for row in rows:
        subject = _mapped(str(row["subject"]), mover=mover, from_space=from_space, to_space=to_space)
        if not subject:
            continue
        if row["kind"] == changes.READING:
            if changes.user_said_not_me(conn, tenant_id=to_space, subject=subject,
                                        predicate=str(row["predicate"]), value=str(row["new_value"])):
                continue
        elif changes._answered_no_from(conn, to_space, subject, str(row["predicate"]), str(row["new_value"]),
                                       row["source_session"], row["source_turn"]):
            continue
        conn.execute(
            "INSERT INTO memory_changes (id, tenant_id, kind, state, subject, predicate, old_value, new_value, "
            "source_session, source_turn, created_at, decided_at, decided_by) "
            "VALUES (?, ?, ?, 'dismissed', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (changes._new_id(), to_space, row["kind"], subject, str(row["predicate"]), str(row["old_value"] or ""),
             str(row["new_value"]), row["source_session"], row["source_turn"],
             float(row["created_at"] or time.time()), row["decided_at"], row["decided_by"]),
        )
        carried += 1
    conn.commit()
    return carried


def _target_row(keys: list[str], to_space: str) -> dict[str, Any] | None:
    """The moved chat's row in *to_space*, as turn reconcile reads one (no
    row times: each turn carries its own, ``ts``). Raises when the chat
    store cannot be read: the entry stays pending."""
    from kazma_core.memory.chat_history import conversations_for

    for key in keys:
        for row in conversations_for(key, strict=True):
            if str(row.get("tenant_id") or "") == to_space:
                return {**row, "created_at": "", "updated_at": ""}
    return None


def _drop_stale_tombstones(conn: sqlite3.Connection, keys: list[str], to_space: str) -> int:
    """A turn the person forgot while the chat was here before keeps its
    tombstone when the chat moves away (only what a move empties is
    deleted). Moved back, a turn no ledger row keeps out any more -- the
    person lifted it while the chat was away -- is written again: under the
    tombstone's id the writer would write nothing. Returns how many."""
    from kazma_core.memory.tenant_view import ledger_reach

    marks = ",".join("?" for _ in keys)
    stale = []
    for eid, turn in conn.execute(
            f"SELECT id, turn_number FROM episodes WHERE tenant_id = ? AND session_id IN ({marks}) "  # nosec B608 - placeholders only
            "AND tier = ? AND user_text = '' AND assistant_text = ''", (to_space, *keys, FORGOTTEN_TIER)).fetchall():
        held = _ledger_rows(
            conn,
            f"SELECT 1 FROM memory_forgotten WHERE tenant_id IN (?, ?) AND session_key IN ({marks}) "  # nosec B608 - placeholders only
            "AND (turn_number = 0 AND question_sha = '' OR turn_number = ?) LIMIT 1",
            (*ledger_reach(to_space), *keys, int(turn or 0)))
        if not held:
            stale.append(str(eid))
    _delete_moved_tombstones(conn, stale, [], to_space)
    conn.commit()
    return len(stale)


def _rebuild(conn: sqlite3.Connection, keys: list[str], to_space: str,
             not_again: list[tuple[str, str, str, Any, Any]] | None = None) -> dict[str, int]:
    """The chat's turns written in *to_space* now (turn reconcile's cursor
    may be past the chat), the quick pass on each, the deep pass queued as
    Kazma's own work, and the weeks it covers summarized again."""
    from kazma_core.memory import turn_reconcile
    from kazma_core.memory.belief_extractor import is_filler_turn
    from kazma_core.memory.consolidator import _v2_extract_sync
    from kazma_core.memory.task_queue import REBUILD_CHAT_MOVE, enqueue_task
    from kazma_core.memory.topic_summaries import requeue_weeks

    row = _target_row(keys, to_space)
    if row is None:
        return {"turns": 0, "deep_queued": 0, "weeks": 0}
    _drop_stale_tombstones(conn, keys, to_space)
    before = int(conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM episodes").fetchone()[0])
    written, _young = turn_reconcile._reconcile_session(conn, row, time.time() + 1.0, source=REBUILT_SOURCE)
    marks = ",".join("?" for _ in keys)
    rows = conn.execute(
        "SELECT id, session_id, turn_number, user_text, assistant_text, created_at, "
        "json_extract(metadata_json, '$.said_by') FROM episodes "
        f"WHERE rowid > ? AND tenant_id = ? AND session_id IN ({marks}) ORDER BY rowid",  # nosec B608 - placeholders only
        (before, to_space, *keys)).fetchall()
    if rows and not_again:
        # The deep pass never files again a value the person corrected or
        # ended (the review's H4; ``worker_bootstrap._apply_if_still_remembered``).
        held = json.dumps(sorted({(s_, p_, v_) for s_, p_, v_, _u, _i in not_again}))
        conn.executemany(
            "UPDATE episodes SET metadata_json = json_set(COALESCE(metadata_json, '{}'), '$.not_again', json(?)) "
            "WHERE id = ? AND tenant_id = ?", [(held, r[0], to_space) for r in rows])
        conn.commit()
    deep = 0
    for eid, session, turn, user, answer, at, said_by in rows:
        messages = [{"role": "user", "content": user or ""}]
        if answer:
            messages.append({"role": "assistant", "content": answer})
        _v2_extract_sync(messages, session_id=session, turn=turn, tenant_id=to_space, now=at, said_by=said_by)
        if not is_filler_turn(user or "") and enqueue_task(
                "micro_consolidation", {"episode_id": eid, "rebuild": REBUILD_CHAT_MOVE}):
            deep += 1
    weeks = requeue_weeks(conn, to_space, [float(r[5] or 0) for r in rows])
    return {"turns": int(written), "deep_queued": deep, "weeks": weeks}


def move_chat_memory(
    key: str, *, from_space: str, to_space: str, mover: str, keys: list[str] | None = None,
    conn: sqlite3.Connection | None = None, journal: bool = True,
) -> dict[str, Any]:
    """Make what memory holds of the chat *key* follow it from *from_space*
    to *to_space* (the module's docstring). The chat's thread must already
    be recorded in *to_space* (``threads.move_thread``): the writers refuse
    the source from then. Raises when a step fails: the journal entry stays
    pending, and the convergence pass runs it again."""
    keys = list(dict.fromkeys([*(keys or []), *_forget.chat_keys(key)]))
    if not keys or not from_space or not to_space or from_space == to_space or not mover:
        return {"ok": False, "error": "chat required"}
    seq = None
    if journal:
        from kazma_core.memory import forget_journal

        seq = forget_journal.append(
            "chat_moved", tenant_id=from_space, session_key=key,
            detail=json.dumps({"keys": keys, "to": to_space, "by": mover}))
    own = conn is None
    conn = conn or _open()
    try:
        carried_facts, decided = _chat_facts(conn, keys, from_space)
        not_again = _not_again(decided, mover=mover, from_space=from_space, to_space=to_space)
        notes = _note_rows(conn, _chat_notes(conn, tenant_id=from_space, keys=keys), from_space)
        answers = _answer_rows(conn, keys, from_space)
        ledger = _copy_ledger(conn, keys, from_space=from_space, to_space=to_space)
        # Everything carried before anything leaves the source (the review's
        # H1): a stop in between is finished by the convergence pass.
        notes_written = _carry_notes(notes, mover=mover, to_space=to_space)
        facts_carried = _carry_facts(conn, carried_facts, mover=mover, from_space=from_space, to_space=to_space)
        answers_carried = _carry_answers(conn, answers, mover=mover, from_space=from_space, to_space=to_space)
        _keep_decided_closed(conn, not_again, keys, to_space)
        kept_elsewhere = _kept_by_another_chat(conn, keys, from_space)
        retired = _retire_source(conn, keys, from_space)
        rebuilt = _rebuild(conn, keys, to_space, not_again)
        _keep_decided_closed(conn, not_again, keys, to_space)
        if seq is not None:
            from kazma_core.memory import forget_journal

            forget_journal.mark_applied(conn, seq)
            conn.commit()
        logger.info("[memory] a moved chat's memory followed it: %d retired, %d facts and %d notes carried, "
                    "%d turns rebuilt", retired["retired"], facts_carried, notes_written, rebuilt["turns"])
        return {"ok": True, "chat_keys": keys, "ledger_rows": ledger, "notes_carried": notes_written,
                "facts_carried": facts_carried, "answers_carried": answers_carried,
                "kept_by_another_chat": kept_elsewhere, **retired, **rebuilt}
    finally:
        if own:
            conn.close()


def replay_chat_moved(conn: sqlite3.Connection, entry: Any) -> None:
    """A ``chat_moved`` journal entry, again (``forget_journal._apply``):
    only while the chat is still away from the space it left -- moved back
    since, a restored database must keep it."""
    try:
        detail = json.loads(entry.detail or "{}")
    except (TypeError, ValueError):
        detail = {}
    keys = [str(k) for k in (detail.get("keys") or []) if k] if isinstance(detail, dict) else []
    to_space = str(detail.get("to") or "") if isinstance(detail, dict) else ""
    mover = str(detail.get("by") or "") if isinstance(detail, dict) else ""
    keys = list(dict.fromkeys([*keys, *_forget.chat_keys(entry.session_key), entry.session_key]))
    if not to_space or not mover or not _still_away(keys, entry.tenant_id):
        return
    out = move_chat_memory(entry.session_key, from_space=entry.tenant_id, to_space=to_space, mover=mover,
                           keys=keys, conn=conn, journal=False)
    if not out.get("ok"):
        raise RuntimeError(f"the memory of moved chat {entry.session_key} did not follow it: {out}")
