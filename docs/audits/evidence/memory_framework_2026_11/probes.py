"""Audit evidence (docs/audits/AUDIT_MEMORY_FRAMEWORK_2026-11.md): ten small
probes of the memory framework through its own writers and its own recall.
No model is called. Everything is written to private, temporary databases.

1. ``two_names``: one fact under two predicate names, through the real
   ``mutate_belief``. The names are the ones the recorded deep-pass replies
   used for the same statement (``tests/fixtures/memory_bench`` and the
   Spaces R3 study's evidence).
2. ``same_words_rule``: which of those name pairs the product's own rule
   (``predicates._predicate_key``) treats as one name.
3. ``one_turn_two_spaces``: the same chat turn written for two tenants
   through the real episode writer, and what history import says it holds.
4. ``time_in_recall``: a fact that changed, and the turns that said so, asked
   about through the real ``recall()`` and shown as the model would see it.
5. ``a_correction_in_the_users_own_words``: a fact the quick pass read,
   corrected later in words only the deep pass reads.
6. ``a_set_label_on_a_single_valued_fact``: an agent write labelled ``set``
   beside the user's own single-valued fact.
7. ``quick_pass_reads``: what the quick pass keeps from text the user quoted,
   and short statements it never sees.
8. ``deep_pass_over_the_same_turns_quick_pass``: one benchmark turn through
   both passes.
9. ``what_forget_leaves``: a forgotten turn's fact, and "forget everything
   that mentions X", read back from the database.
10. ``how_often_the_deep_pass_respells``: over the benchmark's turns, how the
    model's recorded reply wrote the values the quick pass also read.

    KAZMA_DATA_DIR=<empty folder> KAZMA_USER_HOME=<empty folder> python probes.py [--real]

Without ``--real`` every probe runs with no embedder (recall ranks on words
alone) and the result is ``probes.json`` beside this file. ``--real`` runs
probe 4 alone with the real embedder (bge-m3 from the local cache, network
off, four threads) and writes ``probes_real_embedder.json``. Either way the
process runs at below-normal priority.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import tempfile
import time
from pathlib import Path
from typing import Any
from unittest import mock

HERE = Path(__file__).resolve().parent
DAY = 86400.0
#: 2026-01-10 00:00 UTC: a fixed clock for the facts and turns below.
T0 = 1767916800.0


def _private_db() -> tuple[sqlite3.Connection, str]:
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    fd, path = tempfile.mkstemp(prefix="kazma-audit-probe-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    ensure_primary_schema(conn)
    return conn, path


def _remove(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(path + suffix):
            os.remove(path + suffix)


def _current(conn: sqlite3.Connection, subject: str) -> list[str]:
    rows = conn.execute(
        "SELECT predicate, object FROM beliefs WHERE subject = ? AND valid_until IS NULL "
        "AND invalidated_at IS NULL ORDER BY valid_from", (subject,)).fetchall()
    return [f"{subject} {r['predicate']} {r['object']}" for r in rows]


def two_names() -> dict[str, Any]:
    """The same kind of fact stated twice, as the recorded replies named it."""
    from kazma_core.memory.belief_mutation import mutate_belief

    cases = [
        ("control_same_name", "hana", "functional", ("occupation", "pilot"), ("occupation", "captain")),
        ("job_under_two_names", "hana", "functional", ("occupation", "pilot"), ("works_as", "captain")),
        ("job_filler_word_only", "hana", "functional", ("occupation", "pilot"), ("occupation_is", "captain")),
        ("drink_under_two_names", "user", "functional",
         ("favorite_drink", "tea"), ("favorite_morning_drink", "coffee")),
        ("allergy_said_twice", "user", "set", ("allergic_to", "peanuts"), ("has_allergy", "peanuts")),
    ]
    out: dict[str, Any] = {}
    for name, subject, kind, first, second in cases:
        conn, path = _private_db()
        try:
            for i, (predicate, value) in enumerate((first, second)):
                mutate_belief(conn, subject, predicate, value, predicate_type=kind, confidence=0.9,
                              extraction_method="llm_inferred", tenant_id="probe",
                              now=T0 + i * 30 * DAY, private=True)
            conn.commit()
            current = _current(conn, subject)
        finally:
            conn.close()
            _remove(path)
        out[name] = {"first": " ".join((subject, *first)), "then": " ".join((subject, *second)),
                     "current_facts_afterwards": current}
    return out


def same_words_rule() -> dict[str, Any]:
    """Pairs of names the recordings used for one statement: does the
    product's same-words rule make them one name?"""
    from kazma_core.memory.predicates import _predicate_key

    pairs = [
        ("occupation", "occupation_is"), ("occupation", "works_as"), ("profession_is", "works_as"),
        ("job_title", "occupation"), ("allergic_to", "has_allergy"),
        ("previously_worked_at", "former_employer"), ("favorite_drink", "favorite_morning_drink"),
        ("has_sister", "relation_to_user"), ("has_daughter", "child_of"),
    ]
    return {f"{a} / {b}": _predicate_key(a) == _predicate_key(b) for a, b in pairs}


def one_turn_two_spaces() -> dict[str, Any]:
    """One chat turn (same chat id, turn number and words) written for two
    tenants. Runs on the data folder ``KAZMA_DATA_DIR`` names."""
    from kazma_core.memory import dual_write, history_import
    from kazma_core.memory.recall import recall
    from kazma_core.paths import primary_memory_db

    turn = {"session_id": "import-chatgpt-abc123", "turn_number": 1,
            "user_text": "Our family trip to Salalah is booked for the second week of August.",
            "assistant_text": "Noted: Salalah, second week of August."}
    dual_write._reset_mirror()
    try:
        first = dual_write.write_episode(**turn, tenant_id="space_a", source="history_import", created_at=T0)
        second = dual_write.write_episode(**turn, tenant_id="space_b", source="history_import", created_at=T0)
    finally:
        dual_write._reset_mirror()
    conn = sqlite3.connect(str(primary_memory_db()))
    conn.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in conn.execute("SELECT id, tenant_id FROM episodes")]
        found = {space: len(recall("when is the family trip to Salalah", conn=conn, tenant_id=space,
                                   local_only=True).episodes)
                 for space in ("space_a", "space_b")}
    finally:
        conn.close()
    return {
        "write_for_space_a": list(first), "write_for_space_b": list(second),
        "rows_in_episodes": rows,
        "recall_finds_the_turn": found,
        "history_import_says_already_held": sorted(history_import._existing([str(first[1])])),
    }


def time_in_recall(real: bool) -> dict[str, Any]:
    """A move, said in two turns five months apart; then three questions."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob
    from kazma_core.memory.recall import format_recall_block, recall

    conn, path = _private_db()
    turns = [
        (0, "I live in Paris, near the Canal Saint-Martin.", "Paris, noted."),
        (142, "We moved to London last month, the flat is in Hackney.", "London, Hackney: noted."),
    ]
    try:
        for i, (day, user, assistant) in enumerate(turns, 1):
            row = episode_row(session_id="probe-chat", turn_number=i, user_text=user, assistant_text=assistant,
                              tenant_id="probe", source="benchmark", created_at=T0 + day * DAY)
            blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
            conn.execute(
                "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
                "summary_text, tier, structural_importance, created_at, metadata_json, "
                "embedding_model_version, embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
                 row["assistant_text"], row["summary_text"], row["tier"], row["importance"], row["created_at"],
                 json.dumps(row["meta"], ensure_ascii=False), row["embedding_model_version"], blob))
            mutate_belief(conn, "user", "lives_in", ("Paris", "London")[i - 1], predicate_type="functional",
                          confidence=0.9, extraction_method="user_stated", tenant_id="probe",
                          source_session="probe-chat", source_turn=i, now=T0 + day * DAY + 30, private=True)
        conn.commit()
        stored = [dict(r) for r in conn.execute(
            "SELECT object, valid_from, valid_until FROM beliefs WHERE predicate = 'lives_in' ORDER BY valid_from")]
        asked = {}
        for question in ("Where do I live?", "Where did I live before London?", "When did I move to London?"):
            result = recall(question, conn=conn, tenant_id="probe", local_only=True)
            asked[question] = {
                "facts_shown": [h.content for h in result.beliefs],
                "turns_shown": len(result.episodes),
                "block_as_the_model_sees_it": format_recall_block(result),
            }
    finally:
        conn.close()
        _remove(path)
    block = "\n".join(a["block_as_the_model_sees_it"] for a in asked.values())
    return {
        "meaning_search": "real embedder" if real else "off (words only)",
        "facts_as_stored": [{"object": r["object"],
                             "valid_from": time.strftime("%Y-%m-%d", time.gmtime(r["valid_from"])),
                             "valid_until": time.strftime("%Y-%m-%d", time.gmtime(r["valid_until"]))
                             if r["valid_until"] else None} for r in stored],
        "questions": asked,
        "any_date_or_year_in_what_the_model_sees": any(y in block for y in ("2026", "January", "June", "May")),
        "the_earlier_home_is_among_the_facts_shown": {q: any("Paris" in f for f in a["facts_shown"])
                                                      for q, a in asked.items()},
    }

def _apply(conn: sqlite3.Connection, beliefs: list[dict[str, Any]], *, source: str, session: str,
           turn: int, at: float) -> dict[str, Any]:
    """The product's own fact writer for a turn (``_apply_beliefs_to_v2``):
    *source* is ``heuristic`` for the quick pass, ``llm`` for the deep pass."""
    from kazma_core.memory.belief_extractor import _apply_beliefs_to_v2

    stats = {"skipped_filler": False, "source": source, "applied": 0, "rejected": 0, "actions": []}
    return _apply_beliefs_to_v2(beliefs, conn, None, stats=stats, session_id=session, turn=turn,
                                tenant_id="probe", now=at)


def _recorded_deep_pass(turn_id: str) -> tuple[str, list[dict[str, Any]]]:
    """A benchmark turn's text and the facts in the model's own recorded
    reply to it, read by the product's parser."""
    import asyncio

    from kazma_core.memory import benchmark as bench
    from kazma_core.memory.belief_extractor import _llm_extract

    root = HERE.parents[3]
    turn = next(t for t in json.loads(bench.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]
                if t["id"] == turn_id)
    replies = json.loads((root / "tests" / "fixtures" / "memory_bench" / "extraction_llm_2026-10-15.json")
                         .read_text(encoding="utf-8"))["replies"]
    reply = replies[bench._text_key(bench._llm_blob(turn["user"]))]

    class _One:
        async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
            return reply

    registry = type("_Registry", (), {"get_client": lambda self: _One()})()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        beliefs, _down = asyncio.run(_llm_extract(turn["user"], ""))
    return turn["user"], list(beliefs or [])


def _rows(conn: sqlite3.Connection, predicate: str) -> list[dict[str, Any]]:
    return [{"object": r["object"], "source": r["extraction_method"], "current": r["valid_until"] is None
             and r["invalidated_at"] is None}
            for r in conn.execute(
                "SELECT object, extraction_method, valid_until, invalidated_at FROM beliefs "
                "WHERE subject = 'user' AND predicate = ? ORDER BY valid_from, ingested_at", (predicate,))]


def _both_passes_then_a_correction(moved_text: str, moved_deep: list[dict[str, Any]]) -> dict[str, Any]:
    """Production order with the model's own recorded replies: the first turn
    goes through the quick pass and then the deep pass; the correction (the
    benchmark's e06) comes later. Two first turns: one whose value the model
    wrote in other letters than the user (e01), one it wrote the same (m01)."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    out: dict[str, Any] = {}
    for turn_id in ("e01", "m01"):
        first_text, first_deep = _recorded_deep_pass(turn_id)
        conn, path = _private_db()
        try:
            _apply(conn, extract_beliefs_heuristic(first_text), source="heuristic", session="chat", turn=1, at=T0)
            _apply(conn, first_deep, source="llm", session="chat", turn=1, at=T0)
            after_first = _rows(conn, "lives_in")
            stats = _apply(conn, moved_deep, source="llm", session="chat", turn=2, at=T0 + 60 * DAY)
            conn.commit()
            out[turn_id] = {
                "first_message": first_text,
                "deep_pass_wrote_the_home_as": [b.get("object") for b in first_deep if b.get("predicate") == "lives_in"],
                "home_after_the_first_turn": after_first, "later_message": moved_text,
                "correction": [a.get("blocked") or a.get("action") for a in stats.get("actions") or []],
                "home_afterwards": _rows(conn, "lives_in"),
            }
        finally:
            conn.close()
            _remove(path)
    return out


def how_often_the_deep_pass_respells() -> dict[str, Any]:
    """Every benchmark turn the quick pass reads a fact from: the same fact
    in the model's recorded reply, compared letter for letter."""
    from kazma_core.memory import benchmark as bench
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic
    from kazma_core.memory.predicates import _predicate_key

    turns = json.loads(bench.EXTRACTION_PATH.read_text(encoding="utf-8"))["turns"]
    same = respelled = other_value = quick_only = 0
    examples: list[str] = []
    for turn in turns:
        quick = extract_beliefs_heuristic(turn["user"])
        if not quick:
            continue
        try:
            _text, deep = _recorded_deep_pass(turn["id"])
        except KeyError:
            deep = []
        by_name = {_predicate_key(str(b.get("predicate") or "")): str(b.get("object") or "") for b in deep
                   if str(b.get("subject") or "") == "user"}
        for fact in quick:
            theirs = by_name.get(_predicate_key(fact["predicate"]))
            mine = str(fact["object"])
            if theirs is None:
                quick_only += 1
            elif theirs == mine:
                same += 1
            elif theirs.replace("_", " ").casefold() == mine.replace("_", " ").casefold():
                respelled += 1
                examples.append(f"{turn['id']}: {mine!r} -> {theirs!r}")
            else:
                other_value += 1
                examples.append(f"{turn['id']}: {mine!r} -> {theirs!r} (another value)")
    return {"facts_both_passes_read": same + respelled + other_value, "same_letters": same,
            "same_value_other_letters": respelled, "another_value": other_value,
            "read_by_the_quick_pass_only": quick_only, "examples": examples[:12]}


def a_correction_in_the_users_own_words() -> dict[str, Any]:
    """The user states a fact in a form the quick pass reads, and later
    corrects it in a form only the deep pass reads."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    out: dict[str, Any] = {}
    moved_text, moved_deep = _recorded_deep_pass("e06")
    cases = [
        ("moved_house", "lives_in", "I live in Paris.", moved_text, moved_deep, "the model's recorded reply"),
        ("changed_job", "works_at", "I work at Google.", "I left Google, I am at Meta now.",
         [{"subject": "user", "predicate": "works_at", "predicate_type": "functional", "object": "meta",
           "confidence": 0.95}], "written for this probe, in the shape the model returns"),
        ("control_quick_pass_form", "lives_in", "I live in Paris.", "I live in Rotterdam now.", [], "none"),
    ]
    out["both_passes_on_the_first_turn"] = _both_passes_then_a_correction(moved_text, moved_deep)
    for name, predicate, first, second, deep, deep_from in cases:
        conn, path = _private_db()
        try:
            _apply(conn, extract_beliefs_heuristic(first), source="heuristic", session="chat", turn=1, at=T0)
            quick = extract_beliefs_heuristic(second)
            if quick:
                _apply(conn, quick, source="heuristic", session="chat", turn=2, at=T0 + 60 * DAY)
            stats = _apply(conn, deep, source="llm", session="chat", turn=2, at=T0 + 60 * DAY) if deep else {}
            conn.commit()
            out[name] = {
                "first_message": first, "later_message": second,
                "quick_pass_reads_the_later_message_as": [f"{b['predicate']} {b['object']}" for b in quick],
                "deep_pass_facts": [f"{b.get('predicate')} {b.get('object')}" for b in deep],
                "deep_pass_facts_from": deep_from,
                "deep_pass_applied": stats.get("applied"), "deep_pass_actions": stats.get("actions"),
                "facts_afterwards": _rows(conn, predicate),
            }
        finally:
            conn.close()
            _remove(path)
    return out


def a_set_label_on_a_single_valued_fact() -> dict[str, Any]:
    """The user's own "I live in London", then an agent write of the same
    predicate: labelled ``functional`` (the control), and labelled ``set``
    (the default of the agent's link tool) followed by any later
    single-valued write."""
    from kazma_core.memory.belief_mutation import mutate_belief

    def write(conn: sqlite3.Connection, value: str, kind: str, source: str, day: int) -> dict[str, Any]:
        result = mutate_belief(conn, "user", "lives_in", value, predicate_type=kind, confidence=0.9,
                               extraction_method=source, tenant_id="probe", now=T0 + day * DAY, private=True)
        return {k: result.get(k) for k in ("action", "blocked")}

    out: dict[str, Any] = {}
    conn, path = _private_db()
    try:
        write(conn, "London", "functional", "user_stated", 0)
        out["control_agent_write_labelled_functional"] = write(conn, "Tokyo", "functional", "agent_asserted", 1)
        conn.commit()
        out["control_facts_afterwards"] = _rows(conn, "lives_in")
    finally:
        conn.close()
        _remove(path)
    conn, path = _private_db()
    try:
        write(conn, "London", "functional", "user_stated", 0)
        out["agent_write_labelled_set"] = write(conn, "Tokyo", "set", "agent_asserted", 1)
        conn.commit()
        out["facts_after_the_set_write"] = _rows(conn, "lives_in")
        out["a_later_agent_write_labelled_functional"] = write(conn, "Osaka", "functional", "agent_asserted", 2)
        conn.commit()
        out["facts_after_the_later_write"] = _rows(conn, "lives_in")
    finally:
        conn.close()
        _remove(path)
    return out


def quick_pass_reads() -> dict[str, Any]:
    """What the quick pass keeps, at the user's own trust, from text that is
    not the user's statement; and short statements it never sees."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic, is_filler_turn

    texts = [
        "Translate to French: My name is Pierre and I live in Lyon.",
        "I need to finish this report by Friday.",
        "Fix the grammar: I work at Initech and I love Mondays.",
        "Summarise this email: Hi, my name is Dana and I work at Acme Corp.",
    ]
    short = ["I'm diabetic", "Call me Sam", "No, Lisbon", "I'm vegan"]
    # An attached document as the chat apps put it into the user's message
    # (kazma_gateway/agent_handler/attachments.py): fenced, in the same text.
    from kazma_core.safety.prompt_fence import format_untrusted_block

    fenced = format_untrusted_block("Dear team, my name is Sara Lee and I work at Acme Corp.",
                                    source="document_attachment")
    attached = f"Please file this.\n[Attached: letter.pdf (application/pdf) — parsed contents:\n{fenced}]"
    return {
        "kept_from_an_attached_documents_text": [
            f"{b['subject']} {b['predicate']} {b['object']}" for b in extract_beliefs_heuristic(attached)],
        "kept_from_text_that_is_not_the_users_statement": {
            t: [f"{b['subject']} {b['predicate']} {b['object']}" for b in extract_beliefs_heuristic(t)]
            for t in texts},
        "short_statements_skipped_as_filler": {t: bool(is_filler_turn(t)) for t in short},
    }


def deep_pass_over_the_same_turns_quick_pass() -> dict[str, Any]:
    """Benchmark turn e01 through both passes, the deep pass from the
    model's own recorded reply, as the worker applies it (same turn)."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    text, deep = _recorded_deep_pass("e01")
    conn, path = _private_db()
    try:
        _apply(conn, extract_beliefs_heuristic(text), source="heuristic", session="chat", turn=1, at=T0)
        after_quick = {p: _rows(conn, p) for p in ("name_is", "lives_in")}
        _apply(conn, deep, source="llm", session="chat", turn=1, at=T0)
        conn.commit()
        return {"message": text, "after_the_quick_pass": after_quick,
                "deep_pass_facts": [f"{b.get('predicate')} {b.get('object')}" for b in deep],
                "after_the_deep_pass": {p: _rows(conn, p) for p in ("name_is", "lives_in")}}
    finally:
        conn.close()
        _remove(path)


def what_forget_leaves() -> dict[str, Any]:
    """A turn and its fact, forgotten the way the Memory page forgets; and
    "forget everything that mentions X". Runs on ``KAZMA_DATA_DIR``."""
    from kazma_core.memory import dual_write, forget
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.paths import primary_memory_db

    dual_write._reset_mirror()
    try:
        status, eid = dual_write.write_episode(
            session_id="chat-forget", turn_number=1, tenant_id="probe", created_at=T0,
            user_text="Sara was diagnosed with celiac disease last spring.", assistant_text="Noted.")
    finally:
        dual_write._reset_mirror()
    conn = sqlite3.connect(str(primary_memory_db()))
    conn.row_factory = sqlite3.Row
    try:
        fact = mutate_belief(conn, "sara", "diagnosed_with", "celiac disease", predicate_type="set",
                             confidence=0.9, extraction_method="llm_inferred", tenant_id="probe",
                             source_session="chat-forget", source_turn=1, now=T0)
        note = mutate_belief(conn, "user", "noted", "the vault codeword is BLUE-HERON-42", predicate_type="set",
                             confidence=0.9, extraction_method="agent_asserted", tenant_id="probe", now=T0)
        conn.commit()
    finally:
        conn.close()
    forgot = forget.forget_episode(str(eid), tenant_id="probe")
    mentions = forget.forget_mentions("BLUE-HERON-42", tenant_id="probe")
    conn = sqlite3.connect(str(primary_memory_db()))
    conn.row_factory = sqlite3.Row
    try:
        def row(belief_id: Any) -> dict[str, Any]:
            r = conn.execute("SELECT subject, predicate, object, valid_until, invalidated_at, "
                             "embedding IS NOT NULL AS has_vector FROM beliefs WHERE id = ?",
                             (belief_id,)).fetchone()
            return dict(r) if r else {}

        turn_fact, noted = row(fact.get("belief_id")), row(note.get("belief_id"))
        episode = dict(conn.execute("SELECT tier, user_text FROM episodes WHERE id = ?", (eid,)).fetchone())
        fts = conn.execute("SELECT count(*) FROM beliefs_fts WHERE beliefs_fts MATCH 'diagnosed'").fetchone()[0]
    finally:
        conn.close()
    return {
        "wrote": status, "forget_the_turn": {k: forgot.get(k) for k in ("ok", "facts", "error")},
        "the_turn_afterwards": episode,
        "its_fact_afterwards": {k: turn_fact.get(k) for k in ("subject", "predicate", "object")},
        "its_predicate_still_in_the_search_index": bool(fts),
        "forget_mentions": {k: mentions.get(k) for k in ("ok", "memories", "facts", "error")},
        "the_mentioned_fact_afterwards": {"object": noted.get("object"),
                                          "ended": noted.get("valid_until") is not None},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--real", action="store_true", help="load the real embedder for probe 4")
    args = parser.parse_args(argv)
    if not os.environ.get("KAZMA_DATA_DIR"):
        raise SystemExit("set KAZMA_DATA_DIR to a private, empty folder: this run must not read an install")

    import psutil

    if hasattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS"):
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    else:
        os.nice(5)

    report: dict[str, Any] = {}
    if args.real:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        import torch

        torch.set_num_threads(4)
        report["measured_at"] = time.strftime("%Y-%m-%d %H:%M %z")
        report["4_time_in_recall"] = time_in_recall(True)
        out = HERE / "probes_real_embedder.json"
    else:
        with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: None):
            report["measured_at"] = time.strftime("%Y-%m-%d %H:%M %z")
            report["1_two_names"] = two_names()
            report["2_same_words_rule"] = same_words_rule()
            report["3_one_turn_two_spaces"] = one_turn_two_spaces()
            report["4_time_in_recall"] = time_in_recall(False)
            report["5_a_correction_in_the_users_own_words"] = a_correction_in_the_users_own_words()
            report["6_a_set_label_on_a_single_valued_fact"] = a_set_label_on_a_single_valued_fact()
            report["7_quick_pass_reads"] = quick_pass_reads()
            report["8_deep_pass_over_the_same_turns_quick_pass"] = deep_pass_over_the_same_turns_quick_pass()
            report["9_what_forget_leaves"] = what_forget_leaves()
            report["10_how_often_the_deep_pass_respells"] = how_often_the_deep_pass_respells()
        out = HERE / "probes.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(report, ensure_ascii=True, indent=1))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
