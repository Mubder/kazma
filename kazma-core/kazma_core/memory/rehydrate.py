"""Recover the memories an old archive rule erased -- verified text only.

Until 2026-09-26 the 6-hour sleep cycle archived an episode by replacing its
text with a stub -- the first 200 characters of the question, `` — ``, the
first 300 of the answer -- and nulling ``user_text`` and ``assistant_text``.
The live install had lost 76 memories that way. Most of what they held is
still stored somewhere else:

* a chat turn -- in the chat store the web UI writes and in the LangGraph
  checkpoint history (:mod:`kazma_core.memory.chat_history`);
* a note the agent saved (``memory_store``) -- in the ``noted`` belief the
  note left, and in the tool call itself, in checkpoint history;
* a knowledge-library excerpt copied into memory -- in the library's chunk;
* any of them -- in an earlier backup of this database, taken before the row
  was archived (the 6-hourly copies, snapshots saved beside them, universal
  backups).

A text is restored only when it is PROVABLY the one the memory held. A
backup copy of the same row proves itself: when the old archive expression
(:func:`_archive_stub`) turns its text into the row's stub, it is the erased
text. Any other source must reproduce two things. An episode's id is a
SHA-256 over its session, its turn and the start of the text it was written
with (``dual_write._episode_id``, ``swarm_bridge.bridge_episode_id``): a
candidate must reproduce the id, and the stub must be what the old archive
expression makes of the candidate. Then:

* both hold: the question and the answer are restored in full;
* only the id holds (the stored answers differ from the one the memory held
  -- a turn that was re-run, or a migration that rewrote a path inside the
  answer): the question is restored, and the answer too when the stub kept
  all of it (under 300 characters); otherwise the answer stays in the stub;
* the id proves the memory never had a question or an answer (a compaction
  summary IS its summary): nothing was lost, and the row says so;
* nothing verifies: the stub stays (it is searchable) and the row is marked,
  so later passes do not search again until :data:`REHYDRATE_VERSION` rises.

The verified sources must agree, or nothing is restored. Text that exists is
never overwritten. A restored row records where its text came from
(``metadata.rehydrated``). A pass is bounded by a time budget; the one
expensive step -- finding saved notes across every conversation -- resumes
where the last pass stopped. A source that cannot be read leaves its rows
pending: an outage is never recorded as "unrecoverable".
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kazma_core.memory.chat_history import STORE_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "NOTE_CALL_MARK",
    "REHYDRATE_VERSION",
    "STATE_KEY",
    "erased_counts",
    "run_rehydrate_pass",
]

#: Raised when a new source or rule can recover rows an earlier pass could
#: not: rows marked unrecovered by an older version are tried once more.
REHYDRATE_VERSION = 1

#: ConfigStore key holding the last pass's report and the note scan's cursor.
STATE_KEY = "memory.v2.rehydrate"

#: A ``memory_store`` tool call inside msgpack-serialized messages: the key
#: ``name`` (fixstr of 4) followed by the value ``memory_store`` (fixstr of 12).
NOTE_CALL_MARK = b"\xa4name\xacmemory_store"

_NOTE_SOURCE = "memory_store_tool"
_KNOWLEDGE_SOURCE = "knowledge_library_promote"

#: What each writer stores: ``dual_write.mirror_episode`` keeps 4,000
#: characters of text and 2,000 of summary; ``swarm_bridge`` keeps 8,000.
_MIRROR_CAPS = (4000, 2000)
_BRIDGE_CAPS = (8000, 8000)
#: The ``noted`` belief a memory note leaves holds the note's first 1,000
#: characters -- a shorter one is the whole note.
_NOTED_CAP = 1000
#: ``federated_search.promote_kb_hits_to_episodes``: the excerpt's text and summary.
_KB_TEXT_CAP = 1500
_KB_SUMMARY_CAP = 500

#: Rows the old rule may have erased. ``dual_write`` (``e_`` ids) writes empty
#: text as ``''``, so a NULL pair there is always the rule's work, in any tier
#: (an erased row that was recalled has since been revived out of archived).
#: ``swarm_bridge`` rows are born with NULL text, so only archived ones count.
ERASED_SQL = (
    "user_text IS NULL AND assistant_text IS NULL "
    "AND (tier = 'archived' OR substr(id, 1, 2) = 'e_')"
)


@dataclass(frozen=True)
class _Candidate:
    """A text some source says an episode held."""

    user: str
    assistant: str = ""
    summary: str = ""
    source: str = ""


# ── The two rules a candidate must reproduce ───────────────────────────────


def _archive_stub(user_text: str | None, assistant_text: str | None, summary_text: str | None = "") -> str:
    """What the pre-2026-09-26 archive rule left in ``summary_text`` -- its SQL,
    in Python::

        COALESCE(NULLIF(TRIM(summary), ''), TRIM(SUBSTR(u, 1, 200) ||
            CASE WHEN TRIM(u) <> '' AND TRIM(a) <> '' THEN ' — ' ELSE '' END ||
            SUBSTR(a, 1, 300)))

    SQLite's one-argument TRIM removes spaces only; SUBSTR counts characters.
    """
    summary = (summary_text or "").strip(" ")
    if summary:
        return summary
    u = user_text or ""
    a = assistant_text or ""
    sep = " — " if u.strip(" ") and a.strip(" ") else ""
    return (u[:200] + sep + a[:300]).strip(" ")


def _meta(row: Any) -> dict[str, Any]:
    try:
        meta = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _is_bridge(row: Any) -> bool:
    return str(row["id"]).startswith("ep_")


def _id_for(row: Any, cand: _Candidate) -> str:
    """The id the episode's writer would have given *cand*."""
    session, turn = str(row["session_id"] or ""), int(row["turn_number"] or 0)
    if _is_bridge(row):
        from kazma_core.memory.swarm_bridge import bridge_episode_id

        content = (cand.user or cand.summary or "").strip()
        return bridge_episode_id(str(_meta(row).get("source") or ""), session, turn, content)
    from kazma_core.memory.dual_write import _episode_id

    return _episode_id(session, turn, (cand.user or cand.assistant or cand.summary or "").strip())


def _check(row: Any, cand: _Candidate) -> str:
    """``"full"`` (id and stub both reproduced), ``"question"`` (the id only), or ``""``."""
    if _id_for(row, cand) != str(row["id"]):
        return ""
    text_cap, summary_cap = _BRIDGE_CAPS if _is_bridge(row) else _MIRROR_CAPS
    stub = _archive_stub(cand.user[:text_cap], cand.assistant[:text_cap], cand.summary[:summary_cap])
    return "full" if stub == (row["summary_text"] or "") else "question"


def _answer_kept_whole(stub: str, question: str) -> str | None:
    """The answer, when the stub provably kept all of it; else None.

    The stub is ``question[:200] + " — " + answer[:300]``, trimmed: an answer
    part under 300 characters is the whole answer. A stub that is the
    question alone means the answer was empty.
    """
    head = question[:200]
    if not head.strip(" "):
        return None
    if stub == head.strip(" "):
        return ""
    prefix = (head + " — ").lstrip(" ")
    if stub.startswith(prefix):
        tail = stub[len(prefix):]
        if tail and len(tail) < 300:
            return tail
    return None


# ── Where candidate texts come from ────────────────────────────────────────


def _legacy_turn_texts(messages: list[dict[str, Any]]) -> tuple[str, str]:
    """``consolidator.extract_turn_texts`` as it was until 2026-09-26: the scan
    went on past the question, so a turn with no answer took the previous
    turn's. The rows this module recovers were written with it."""
    from kazma_core.memory.consolidator import message_text

    user = ""
    assistant = ""
    for m in reversed(messages or []):
        role = m.get("role")
        content = message_text(m.get("content"))
        if not content:
            continue
        if role == "assistant" and not assistant:
            assistant = content
        elif role == "user" and not user:
            user = content
        if user and assistant:
            break
    return user, assistant


def _turn_texts(messages: Iterable[Any]) -> set[tuple[str, str]]:
    """``(question, answer)`` of every turn of a conversation, the ways the
    post-turn mirror has extracted them -- the old rule the recovered rows
    were written with, and today's -- over the conversation as it stood when
    each turn ended."""
    from kazma_core.memory.chat_history import normalize_message
    from kazma_core.memory.consolidator import extract_turn_texts

    msgs = [normalize_message(m) for m in messages or []]
    out: set[tuple[str, str]] = set()
    for end in range(1, len(msgs) + 1):
        if end == len(msgs) or msgs[end].get("role") == "user":
            for rule in (_legacy_turn_texts, extract_turn_texts):
                user, answer = rule(msgs[:end])
                if user or answer:
                    out.add((user, answer))
    return out


def _note_texts(messages: Iterable[Any]) -> set[str]:
    """The ``text`` argument of every ``memory_store`` call in a conversation."""
    from kazma_core.memory.chat_history import normalize_message

    out: set[str] = set()
    for m in messages or []:
        msg = normalize_message(m)
        calls = msg.get("tool_calls") or (msg.get("additional_kwargs") or {}).get("tool_calls") or []
        for call in calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function") if isinstance(call.get("function"), dict) else {}
            if (call.get("name") or fn.get("name")) != "memory_store":
                continue
            args = call.get("args")
            if args is None:
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except (TypeError, ValueError):
                    args = {}
            text = args.get("text") if isinstance(args, dict) else None
            if isinstance(text, str) and text.strip():
                out.add(text)
    return out


def _noted_index(conn: sqlite3.Connection) -> dict[str, list[str]]:
    """Whole notes kept in ``noted`` beliefs -- current, superseded, and those
    moved to ``beliefs_archive`` (stored there as the belief's JSON) -- keyed
    by the stub they would leave."""
    objects = [
        str(obj or "")
        for (obj,) in conn.execute("SELECT object FROM beliefs WHERE predicate = 'noted'")
    ]
    try:
        archived = conn.execute("SELECT original_belief_json FROM beliefs_archive").fetchall()
    except sqlite3.OperationalError:  # a database from before the archive table
        archived = []
    for (raw,) in archived:
        try:
            belief = json.loads(raw or "{}")
        except (TypeError, ValueError):
            continue
        if isinstance(belief, dict) and belief.get("predicate") == "noted":
            objects.append(str(belief.get("object") or ""))
    index: dict[str, list[str]] = {}
    for text in objects:
        if text.strip() and len(text) < _NOTED_CAP:
            index.setdefault(_archive_stub(text, ""), []).append(text)
    return index


def _knowledge_candidates(stub: str) -> list[_Candidate]:
    """The library chunk a knowledge excerpt was copied from, in the forms the
    copy could have taken (the title is the document title, else the URL)."""
    from kazma_core.stores.knowledge import get_knowledge_store

    out: list[_Candidate] = []
    for chunk in get_knowledge_store().chunks_starting_with(stub, limit=5):
        content = str(chunk.get("content") or "").strip()
        titles = [chunk.get("document_title"), chunk.get("source_url"), "Knowledge"]
        for title in dict.fromkeys(t for t in titles if t):
            out.append(
                _Candidate(
                    user=f"[Knowledge: {title}] {content[:_KB_TEXT_CAP]}",
                    summary=content[:_KB_SUMMARY_CAP],
                    source="knowledge_library",
                )
            )
    return out


def _conversation_candidates(key: str) -> tuple[set[_Candidate], bool]:
    """Every turn and saved note of the conversation *key* names -- the chat
    store's copies and every checkpoint version of its threads -- and whether
    every one of those sources could be read. One unreadable source keeps
    what the others gave: a text they prove is restored either way; only the
    verdict "unrecoverable" waits for a complete read."""
    from kazma_core.memory.chat_history import checkpoint_message_versions, conversations_for

    out: set[_Candidate] = set()
    complete = True
    threads = {key}
    try:
        for conv in conversations_for(key, strict=True):
            threads.add(conv["thread_id"])
            for user, answer in _turn_texts(conv["messages"]):
                out.add(_Candidate(user=user, assistant=answer, source="chat_store"))
    except STORE_ERRORS:
        logger.debug("[rehydrate] chat store unreadable for %s", key, exc_info=True)
        complete = False
    for thread in sorted(t for t in threads if t):
        try:
            versions = checkpoint_message_versions(thread, strict=True)
        except STORE_ERRORS:
            logger.debug("[rehydrate] checkpoints of %s unreadable", thread, exc_info=True)
            complete = False
            continue
        for msgs in versions:
            for user, answer in _turn_texts(msgs):
                out.add(_Candidate(user=user, assistant=answer, source="checkpoint"))
            for text in _note_texts(msgs):
                out.add(_Candidate(user=text, source="checkpoint_note"))
    return out, complete


# ── Deciding and writing ───────────────────────────────────────────────────


#: Which copy of a text to keep when sources agree up to whitespace: the
#: conversation the memory was made from first, the stub itself last.
_SOURCE_RANK = {
    "checkpoint": 0,
    "chat_store": 1,
    "checkpoint_note": 2,
    "noted_belief": 3,
    "knowledge_library": 4,
    "stub": 9,
}


def _decide(row: Any, cands: Iterable[_Candidate]) -> dict[str, Any] | None:
    """What *cands* prove about the row: a restoration, "nothing lost",
    "ambiguous", or None (nothing verifies).

    Texts that differ only in surrounding whitespace are one text (the id is
    computed over the stripped text); the copy from the highest-ranked
    source is the one restored.
    """
    text_cap = (_BRIDGE_CAPS if _is_bridge(row) else _MIRROR_CAPS)[0]
    full: dict[tuple[str, str], tuple[tuple[str, str], set[str]]] = {}
    questions: dict[str, tuple[str, set[str]]] = {}
    ranked = sorted(
        set(cands),
        key=lambda c: (_SOURCE_RANK.get(c.source, 5), c.source, c.user, c.assistant),
    )
    for cand in ranked:
        verdict = _check(row, cand)
        if not verdict:
            continue
        user, answer = cand.user[:text_cap], cand.assistant[:text_cap]
        questions.setdefault(user.strip(), (user, set()))[1].add(cand.source)
        if verdict == "full":
            full.setdefault((user.strip(), answer.strip()), ((user, answer), set()))[1].add(
                cand.source
            )
    if not questions:
        return None
    if len(questions) > 1:
        return {"result": "ambiguous", "sources": sorted(set().union(*(s for _, s in questions.values())))}
    question, q_sources = next(iter(questions.values()))
    sources = sorted(q_sources)
    if len(full) == 1:
        (user, answer), srcs = next(iter(full.values()))
        if not user.strip() and not answer.strip():
            return {"result": "nothing_lost", "sources": sorted(srcs)}
        return {
            "result": "restored",
            "user": user,
            "assistant": answer,
            "sources": sorted(srcs),
            "verified": "id+stub",
            "answer": "full",
        }
    # The id holds but no single stored answer reproduces the stub.
    kept = _answer_kept_whole(str(row["summary_text"] or ""), question)
    return {
        "result": "restored",
        "user": question,
        "assistant": kept,
        "sources": sources,
        "verified": "id",
        "answer": "whole_in_stub" if kept is not None else "stub_only",
    }


def _backup_copies() -> list[Path]:
    """Earlier copies of the memory database, newest first: the 6-hourly
    backups, any snapshot saved beside them (``memory_state_pre_*.db``), and
    the copies inside universal backups."""
    from kazma_core.paths import backups_dir

    root = Path(backups_dir())
    files = [f for f in root.glob("memory_state*.db") if f.is_file()]
    universal = root / "universal"
    if universal.is_dir():
        files.extend(f for f in universal.glob("*/**/memory_state.db") if f.is_file())
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def _from_backups(rows: list[Any]) -> dict[str, tuple[str | None, str | None]]:
    """``{episode id: (user_text, assistant_text)}`` from the newest earlier
    copy of each row whose text the old archive rule turns into the row's
    stub. A copy of the same row (same primary key) that reproduces the stub
    IS the erased text -- no conversation has to be found."""
    want = {str(r["id"]): str(r["summary_text"] or "") for r in rows}
    found: dict[str, tuple[str | None, str | None]] = {}
    for path in _backup_copies():
        missing = [eid for eid in want if eid not in found]
        if not missing:
            break
        try:
            # immutable: a backup is never written while read, and a plain
            # read-only open would leave -wal/-shm files beside it.
            copy = sqlite3.connect(f"file:{path.as_posix()}?immutable=1", uri=True, timeout=5)
        except sqlite3.Error:
            continue
        try:
            for i in range(0, len(missing), 500):
                chunk = missing[i:i + 500]
                for eid, user, answer, summary in copy.execute(
                    "SELECT id, user_text, assistant_text, summary_text FROM episodes "
                    f"WHERE id IN ({','.join('?' for _ in chunk)}) "
                    "AND (user_text IS NOT NULL OR assistant_text IS NOT NULL)",
                    chunk,
                ):
                    if eid not in found and _archive_stub(user, answer, summary) == want[eid]:
                        found[eid] = (user, answer)
        except sqlite3.Error:  # an unreadable or half-written copy is skipped
            logger.debug("[rehydrate] backup %s unreadable", path.name, exc_info=True)
        finally:
            copy.close()
    return found


def _restore(conn: sqlite3.Connection, row: Any, decision: dict[str, Any]) -> bool:
    meta = _meta(row)
    meta.pop("rehydrate", None)
    meta["rehydrated"] = {
        "version": REHYDRATE_VERSION,
        "at": round(time.time(), 3),
        "sources": decision["sources"],
        "verified": decision["verified"],
        "answer": decision["answer"],
    }
    cur = conn.execute(
        "UPDATE episodes SET user_text = ?, assistant_text = ?, metadata_json = ? "
        "WHERE id = ? AND user_text IS NULL AND assistant_text IS NULL",
        (
            decision["user"],
            decision["assistant"],
            json.dumps(meta, ensure_ascii=False),
            row["id"],
        ),
    )
    return cur.rowcount == 1


def _mark(conn: sqlite3.Connection, row: Any, result: str, sources: list[str] | None = None) -> None:
    meta = _meta(row)
    meta["rehydrate"] = {
        "version": REHYDRATE_VERSION,
        "result": result,
        "at": round(time.time(), 3),
        **({"sources": sources} if sources else {}),
    }
    conn.execute(
        "UPDATE episodes SET metadata_json = ? "
        "WHERE id = ? AND user_text IS NULL AND assistant_text IS NULL",
        (json.dumps(meta, ensure_ascii=False), row["id"]),
    )


def _pending(row: Any) -> bool:
    meta = _meta(row)
    if meta.get("rehydrated"):
        return False
    done = meta.get("rehydrate")
    return not (isinstance(done, dict) and int(done.get("version") or 0) >= REHYDRATE_VERSION)


def _is_note(row: Any) -> bool:
    return _meta(row).get("source") == _NOTE_SOURCE or str(row["session_id"] or "") == "memory_store"


def _rehydrate_erased(
    conn: sqlite3.Connection,
    *,
    time_budget_s: float = 90.0,
    note_scan_after: str = "",
) -> dict[str, Any]:
    """One recovery pass over every tenant's erased memories.

    Returns a report: ``candidates`` (rows still without a verdict when the
    pass began), ``restored`` / ``restored_question_only`` / ``nothing_lost``
    / ``unrecovered`` / ``ambiguous`` (this pass's verdicts), ``pending``
    (rows left for a later pass), ``sources`` (restorations per source), and
    ``note_scan_after`` / ``note_scan_done`` -- where the saved-note scan
    stopped, for the next pass to resume.
    """
    started = time.monotonic()
    deadline = started + max(0.0, float(time_budget_s))
    rows = [
        r
        for r in conn.execute(
            "SELECT id, tenant_id, session_id, turn_number, summary_text, metadata_json "
            f"FROM episodes WHERE {ERASED_SQL} ORDER BY created_at DESC"
        ).fetchall()
        if _pending(r)
    ]
    report: dict[str, Any] = {
        "candidates": len(rows),
        "restored": 0,
        "restored_question_only": 0,
        "nothing_lost": 0,
        "unrecovered": 0,
        "ambiguous": 0,
        "pending": 0,
        "sources": {},
        "note_scan_after": note_scan_after,
        "note_scan_done": False,
    }
    if not rows:
        report["note_scan_done"] = True
        return report

    backups = _from_backups(rows)
    noted = _noted_index(conn)
    cands: dict[str, set[_Candidate]] = {}
    unreadable: set[str] = set()
    for row in rows:
        eid, stub = str(row["id"]), str(row["summary_text"] or "")
        found = cands.setdefault(eid, set())
        if stub:
            # The stub may be the whole memory. A bridge row keeps its text in
            # the summary (its question is NULL from birth); a mirrored turn
            # kept it as the question. One reading each: both at once would
            # verify alike and read as a conflict.
            if _is_bridge(row):
                found.add(_Candidate(user="", summary=stub, source="stub"))
            else:
                found.add(_Candidate(user=stub, source="stub"))
            found.update(_Candidate(user=t, source="noted_belief") for t in noted.get(stub, []))
        if stub and _meta(row).get("source") == _KNOWLEDGE_SOURCE:
            try:
                found.update(_knowledge_candidates(stub))
            except (sqlite3.Error, OSError):  # the library unreadable: retry next pass
                logger.debug("[rehydrate] knowledge library unreadable", exc_info=True)
                unreadable.add(eid)

    def settle(row: Any, *, final: bool) -> bool:
        """Apply what the candidates prove. True when the row got a verdict."""
        eid = str(row["id"])
        if eid in backups:
            user, answer = backups[eid]
            decision: dict[str, Any] | None = {
                "result": "restored",
                "user": user,
                "assistant": answer,
                "sources": ["backup"],
                "verified": "row+stub",
                "answer": "full",
            }
        else:
            decision = _decide(row, cands.get(eid, ()))
        if decision is None:
            if final:
                _mark(conn, row, "unrecovered")
                report["unrecovered"] += 1
            return final
        if decision["result"] in ("ambiguous", "nothing_lost"):
            _mark(conn, row, decision["result"], decision.get("sources"))
            report[decision["result"]] += 1
            return True
        if _restore(conn, row, decision):
            key = "restored" if decision["answer"] == "full" else "restored_question_only"
            report[key] += 1
            for src in decision["sources"]:
                report["sources"][src] = report["sources"].get(src, 0) + 1
            _remirror(conn, str(row["id"]))
        return True

    # 1. What the cheap sources settle, settled now.
    open_rows = [r for r in rows if not settle(r, final=False)]
    conn.commit()

    # 2. Conversations, one read per session. A row whose session was read
    #    (or that has none) has had every source but the note scan.
    conv_read: set[str] = set()
    groups: dict[str, list[Any]] = {}
    for row in open_rows:
        key = str(row["session_id"] or "")
        if key and key != "memory_store":
            groups.setdefault(key, []).append(row)
        else:
            conv_read.add(str(row["id"]))
    for key, group in groups.items():
        if time.monotonic() >= deadline:
            break  # the rest are read next pass
        conv, complete = _conversation_candidates(key)
        for row in group:
            cands[str(row["id"])].update(conv)
            if complete:  # an outage is never a verdict: incomplete rows stay pending
                conv_read.add(str(row["id"]))

    def searched(row: Any) -> bool:
        eid = str(row["id"])
        return eid in conv_read and eid not in unreadable

    open_rows = [
        r for r in open_rows if not settle(r, final=searched(r) and not _is_note(r))
    ]
    conn.commit()

    # 3. Saved notes: every conversation's memory_store calls, resumable.
    notes = [r for r in open_rows if _is_note(r)]
    if notes:
        done, cursor = _scan_notes(notes, cands, note_scan_after, deadline)
        report["note_scan_after"], report["note_scan_done"] = cursor, done
    else:
        done = True
        report["note_scan_after"], report["note_scan_done"] = "", True
    for row in open_rows:
        final = searched(row) and (done or not _is_note(row))
        if not settle(row, final=final):
            report["pending"] += 1
    conn.commit()
    report["duration_s"] = round(time.monotonic() - started, 3)
    return report


def _scan_notes(
    rows: list[Any], cands: dict[str, set[_Candidate]], after: str, deadline: float
) -> tuple[bool, str]:
    """Look for these notes in every conversation's ``memory_store`` calls.

    Returns ``(done, cursor)``: *done* once every conversation that holds a
    ``memory_store`` call has been read; *cursor* the last one read, where
    the next pass resumes. An unreadable store ends the scan, not done.
    """
    from kazma_core.memory.chat_history import checkpoint_message_versions, threads_containing

    by_stub: dict[str, list[str]] = {}
    for row in rows:
        by_stub.setdefault(str(row["summary_text"] or ""), []).append(str(row["id"]))
    cursor = after
    try:
        threads = threads_containing(NOTE_CALL_MARK, after=after, strict=True)
        for thread in threads:
            if time.monotonic() >= deadline:
                return False, cursor
            for msgs in checkpoint_message_versions(thread, containing=NOTE_CALL_MARK, strict=True):
                for text in _note_texts(msgs):
                    for eid in by_stub.get(_archive_stub(text, ""), []):
                        cands[eid].add(_Candidate(user=text, source="checkpoint_note"))
            cursor = thread
    except STORE_ERRORS:  # unreadable now: resume from the cursor next pass
        logger.debug("[rehydrate] note scan stopped", exc_info=True)
        return False, cursor
    return True, ""


def _remirror(conn: sqlite3.Connection, eid: str) -> None:
    """Best effort: the state mirror reports its own failures; SQLite is the truth."""
    from kazma_core.memory.state_backend import remirror_episode_by_id

    remirror_episode_by_id(conn, eid)


# ── Counts, and the maintenance-cadence entry point ────────────────────────


def erased_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Erased memories by state: ``pending`` (not yet searched by this
    version), ``unrecovered`` (searched; only the stub remains), ``restored``
    and ``restored_question_only`` (the answer is only in the stub)."""
    out = {"pending": 0, "unrecovered": 0, "restored": 0, "restored_question_only": 0}
    for row in conn.execute(f"SELECT id, metadata_json FROM episodes WHERE {ERASED_SQL}"):
        meta = _meta(row)
        done = meta.get("rehydrate") if isinstance(meta.get("rehydrate"), dict) else {}
        if _pending(row):
            out["pending"] += 1
        elif done.get("result") in ("unrecovered", "ambiguous"):
            out["unrecovered"] += 1
    for row in conn.execute(
        "SELECT metadata_json FROM episodes WHERE metadata_json LIKE '%\"rehydrated\"%'"
    ):
        info = _meta(row).get("rehydrated")
        if isinstance(info, dict):
            key = "restored" if info.get("answer") == "full" else "restored_question_only"
            out[key] += 1
    return out


def run_rehydrate_pass(*, time_budget_s: float = 90.0) -> dict[str, Any]:
    """One recovery pass over the primary memory database (15-minute cadence).

    Reads and saves the note scan's cursor, and the report, under
    :data:`STATE_KEY`. Returns the report (``{}`` when there is no database).
    """
    from kazma_core.config_store import apply_sqlite_pragmas, get_config_store
    from kazma_core.paths import primary_memory_db

    db = primary_memory_db()
    if not db or not os.path.isfile(db):
        return {}
    store = get_config_store()
    state = store.get(STATE_KEY)
    state = state if isinstance(state, dict) else {}
    conn = sqlite3.connect(db, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        apply_sqlite_pragmas(conn, busy_timeout=15000)
        report = _rehydrate_erased(
            conn,
            time_budget_s=time_budget_s,
            note_scan_after=str(state.get("note_scan_after") or ""),
        )
    except sqlite3.OperationalError:
        logger.debug("[rehydrate] memory database not ready", exc_info=True)
        return {}
    finally:
        conn.close()
    if report["candidates"] or state.get("note_scan_after") != report["note_scan_after"]:
        store.set(
            STATE_KEY,
            {"note_scan_after": report["note_scan_after"], "last": {**report, "at": time.time()}},
            category="memory",
        )
    settled = report["candidates"] - report["pending"]
    if settled:
        logger.info(
            "[memory] recovered %d erased memories in full and %d by their question; "
            "%d never lost anything; %d cannot be recovered (their stub stays searchable); "
            "%d pending",
            report["restored"],
            report["restored_question_only"],
            report["nothing_lost"],
            report["unrecovered"] + report["ambiguous"],
            report["pending"],
        )
    return report
