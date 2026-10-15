"""Relabel the facts the memory tools stored as the user's (Memory audit M03).

Until 2026-10-13 the agent's ``memory_store`` and ``memory_link_entities``
wrote ``user_explicit`` -- the user's own label, which no later statement but
the user's own replaces -- for whatever the model chose to store. The tools
are fixed (:mod:`kazma_core.memory.provenance`); the rows they wrote still
carry the label, and each one still blocks the user's own correction. This
module finds them by what the store recorded and relabels them
``agent_asserted`` (with that label's trust weight).

A ``user_explicit`` fact is a memory tool's when:

- a note ``memory_store`` wrote (an episode of the same tenant whose
  ``metadata.source`` is ``memory_store_tool``) names the fact's chat turn --
  or, for a note from before notes named their chat (2026-09-27), holds the
  fact's value and was written within seconds of it; or
- the fact names a chat turn (``source_session`` and ``source_turn``): only a
  memory tool writes ``user_explicit`` with one. The Memory page's link names
  none, its edit keeps the fact's own, and the V1 backfill named none.

It keeps ``user_explicit`` when the user's own channel shows:

- an edit on the Memory page (``metadata.operator_edit``), or a successful
  operator action on the Memory page naming the fact (``memory_audit_log``
  ``operator_action`` rows, Memory audit M35);
- the turn the tool ran in asked to remember those words: today's rule
  (``provenance.agent_write_source``), judged against the turn's stored
  question. A fact a tool wrote since 2026-10-13 under that rule passes it.

Every other ``user_explicit`` row is left as it is and counted
``unattributed``: the Memory page's links, the V1 backfill's facts, and a tool
write from before 2026-09-27 that left no note (``memory_link_entities``) --
nothing recorded says which it was, and nothing is guessed.

:func:`size_relabel` reads only and names facts by id, never by value.
:func:`apply_relabel` relabels in one transaction on the memory database and
writes one ``memory_audit_log`` row per fact (``provenance_relabel``, the label
before and after, never a value). Idempotent: a relabelled fact is
``agent_asserted`` and is not read again. ``scripts/memory_relabel_provenance.py``
runs both; the live run is the owner's.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["apply_relabel", "size_relabel"]

#: A note written before notes named their chat matches a fact written within
#: this many seconds of it (the tool wrote the note, then the fact).
_NOTE_WINDOW_S = 5.0
_EXAMPLES = 10
#: A note's metadata fields, read past a malformed row.
_SOURCE = "CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.source') END"
_CHAT = "CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.chat') END"
_CHAT_TURN = "CASE WHEN json_valid(metadata_json) THEN json_extract(metadata_json, '$.chat_turn') END"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    cur = conn.cursor()
    cur.row_factory = sqlite3.Row
    return cur.execute(sql, params).fetchall()


def _meta(raw: Any) -> dict[str, Any]:
    try:
        out = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return out if isinstance(out, dict) else {}


def _operator_named(ops_conn: sqlite3.Connection | None) -> set[str]:
    """Fact ids a successful Memory page action named. Raises when the
    operational database cannot answer: nothing is relabelled on a guess."""
    if ops_conn is None:
        return set()
    named: set[str] = set()
    for row in ops_conn.execute(
        "SELECT target_id, reason FROM memory_audit_log WHERE event_type = 'operator_action'"
    ).fetchall():
        reason = str(row[1] or "")
        status = reason.rsplit("->", 1)[-1].strip()
        if not status.startswith("2"):
            continue
        named.update(p.strip() for p in str(row[0] or "").split(",") if p.strip())
    return named


def _store_notes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every note ``memory_store`` wrote: its tenant, chat turn, text and time."""
    return _rows(
        conn,
        f"SELECT id, tenant_id, user_text, created_at, {_CHAT} AS chat, {_CHAT_TURN} AS chat_turn "
        f"FROM episodes WHERE {_SOURCE} = 'memory_store_tool'",
    )


def _turn_question(
    conn: sqlite3.Connection, tenant: str, session: str, turn: int, keys_of: Callable[[str], Iterable[str]]
) -> str:
    """The user's question of a chat turn, as memory stored it ('' when it holds none)."""
    keys = sorted({str(k) for k in (keys_of(session) or ()) if k} | {session})
    marks = ",".join("?" for _ in keys)
    for row in _rows(
        conn,
        f"SELECT user_text FROM episodes WHERE tenant_id = ? AND turn_number = ? AND session_id IN ({marks}) "
        f"AND COALESCE({_SOURCE}, '') != 'memory_store_tool' AND tier != 'forgotten' "
        "ORDER BY created_at",
        (tenant, int(turn), *keys),
    ):
        if str(row["user_text"] or "").strip():
            return str(row["user_text"])
    return ""


def _classify_tool_facts(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    *,
    keys_of: Callable[[str], Iterable[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Every ``user_explicit`` fact, by what the store says of it:
    ``relabel`` (a memory tool's, no user channel), ``kept`` (a tool's, but the
    user's channel shows) and ``unattributed`` (nothing says a tool wrote it).
    Each entry: ``id``, ``tenant``, ``tool`` and ``why`` -- never the value."""
    from kazma_core.memory.provenance import USER_EXPLICIT, agent_write_source

    if keys_of is None:
        from kazma_core.memory.forget import chat_keys as keys_of
    named = _operator_named(ops_conn)
    notes = _store_notes(conn)
    by_turn: dict[tuple[str, str, int], sqlite3.Row] = {}
    unnamed: list[sqlite3.Row] = []
    for note in notes:
        if note["chat"] and note["chat_turn"] is not None:
            by_turn[(str(note["tenant_id"] or "default"), str(note["chat"]), int(note["chat_turn"]))] = note
        else:
            unnamed.append(note)
    out: dict[str, list[dict[str, Any]]] = {"relabel": [], "kept": [], "unattributed": []}
    for fact in _rows(
        conn,
        "SELECT id, tenant_id, subject, predicate, object, source_session, source_turn, ingested_at, metadata_json "
        "FROM beliefs WHERE extraction_method = ? ORDER BY ingested_at, id",
        (USER_EXPLICIT,),
    ):
        tenant = str(fact["tenant_id"] or "default")
        session = str(fact["source_session"] or "")
        turn = fact["source_turn"]
        note = by_turn.get((tenant, session, int(turn))) if session and turn is not None else None
        value = str(fact["object"] or "")
        if note is None and not session and value:
            for candidate in unnamed:
                if (str(candidate["tenant_id"] or "default") == tenant
                        and abs(float(fact["ingested_at"] or 0) - float(candidate["created_at"] or 0)) <= _NOTE_WINDOW_S
                        and value in str(candidate["user_text"] or "")):
                    note = candidate
                    break
        if note is not None:
            tool = "memory_store"
        elif session and turn is not None:
            tool = "memory_link_entities"
        else:
            out["unattributed"].append({"id": fact["id"], "tenant": tenant, "tool": None,
                                        "why": "nothing recorded says a memory tool wrote it"})
            continue
        entry = {"id": fact["id"], "tenant": tenant, "tool": tool}
        if "operator_edit" in _meta(fact["metadata_json"]):
            out["kept"].append({**entry, "why": "edited on the Memory page"})
            continue
        if str(fact["id"]) in named:
            out["kept"].append({**entry, "why": "an operator action on the Memory page named it"})
            continue
        stored = str(note["user_text"] or "") if note is not None else " ".join(
            str(fact[k] or "") for k in ("subject", "predicate", "object"))
        question = _turn_question(conn, tenant, session, int(turn), keys_of) if session and turn is not None else ""
        if question and agent_write_source(stored, question) == USER_EXPLICIT:
            out["kept"].append({**entry, "why": "the turn asked to remember these words"})
            continue
        out["relabel"].append({**entry, "why": "a memory tool wrote it and the user's turn did not ask for it"})
    return out


def size_relabel(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection | None,
    *,
    keys_of: Callable[[str], Iterable[str]] | None = None,
) -> dict[str, Any]:
    """What :func:`apply_relabel` would do: counts by class, tool, reason and
    tenant, and up to ten fact ids per class. Reads only."""
    groups = _classify_tool_facts(conn, ops_conn, keys_of=keys_of)

    def _count(items: list[dict[str, Any]], key: str) -> dict[str, int]:
        out: dict[str, int] = {}
        for item in items:
            name = str(item[key])
            out[name] = out.get(name, 0) + 1
        return dict(sorted(out.items()))

    return {
        "user_explicit": sum(len(v) for v in groups.values()),
        "relabel": len(groups["relabel"]),
        "relabel_by_tool": _count(groups["relabel"], "tool"),
        "relabel_by_tenant": _count(groups["relabel"], "tenant"),
        "kept": len(groups["kept"]),
        "kept_by_reason": _count(groups["kept"], "why"),
        "unattributed": len(groups["unattributed"]),
        "examples": {kind: [str(i["id"]) for i in items[:_EXAMPLES]] for kind, items in groups.items()},
    }


def apply_relabel(
    conn: sqlite3.Connection,
    ops_conn: sqlite3.Connection,
    *,
    keys_of: Callable[[str], Iterable[str]] | None = None,
    actor: str = "operator:memory_relabel_provenance",
    now: float | None = None,
    trust_weight: float | None = None,
) -> dict[str, Any]:
    """Relabel every fact :func:`_classify_tool_facts` puts in ``relabel``.

    One ``BEGIN IMMEDIATE`` on the memory database (a fact relabelled since the
    classification -- it is no longer ``user_explicit`` -- is left), then one
    audit row per fact on *ops_conn*. Both connections are committed."""
    from kazma_core.memory.belief_mutation import _trust_weight
    from kazma_core.memory.provenance import AGENT_ASSERTED, USER_EXPLICIT

    if trust_weight is None:
        try:
            from kazma_core.memory.config import read_memory_cfg

            cfg = read_memory_cfg()
        except (sqlite3.Error, OSError, RuntimeError, ValueError):  # no settings store: the shipped weight
            logger.info("[provenance_relabel] memory settings unreadable; the shipped trust weight is used")
            cfg = None
        trust_weight = _trust_weight(AGENT_ASSERTED, cfg)
    ts = float(now if now is not None else time.time())
    groups = _classify_tool_facts(conn, ops_conn, keys_of=keys_of)
    if conn.in_transaction:
        conn.commit()
    done: list[dict[str, Any]] = []
    conn.execute("BEGIN IMMEDIATE")
    committed = False
    try:
        for item in groups["relabel"]:
            cur = conn.execute(
                "UPDATE beliefs SET extraction_method = ?, source_trust_weight = ? "
                "WHERE id = ? AND tenant_id = ? AND extraction_method = ?",
                (AGENT_ASSERTED, float(trust_weight), item["id"], item["tenant"], USER_EXPLICIT),
            )
            if cur.rowcount:
                done.append(item)
        conn.execute("COMMIT")
        committed = True
    finally:
        if not committed:  # whatever stopped it, no fact is half relabelled
            conn.execute("ROLLBACK")
    for item in done:
        ops_conn.execute(
            "INSERT INTO memory_audit_log (id, tenant_id, timestamp, event_type, target_table, target_id, actor, "
            "reason, state_before_json, state_after_json) VALUES (?, ?, ?, 'provenance_relabel', 'beliefs', ?, ?, ?, ?, ?)",
            ("pr_" + uuid.uuid4().hex[:20], item["tenant"], ts, item["id"], actor,
             f"Memory audit M03: {item['why']} ({item['tool']})",
             json.dumps({"extraction_method": USER_EXPLICIT}), json.dumps({"extraction_method": AGENT_ASSERTED})),
        )
    ops_conn.commit()
    if done:
        logger.info("[provenance_relabel] relabelled %d tool-written fact(s) agent_asserted: %s",
                    len(done), [i["id"] for i in done[:10]])
    return {
        "relabelled": len(done),
        "kept": len(groups["kept"]),
        "unattributed": len(groups["unattributed"]),
        "relabelled_ids": [str(i["id"]) for i in done[:_EXAMPLES]],
    }
