"""Memories keep all of their text (2026-11-19, the owner: "Keep more, so find
a way or a workaround to keep all text").

Until that day every episode writer cut what it stored: 4,000 characters of a
turn's question and of its answer (``dual_write``, ``turn_reconcile``), 2,000
of the summary, 8,000 of a swarm result, 16,000 of a turn waiting in the
queue. Now (``memory/limits.py``):

* every writer stores the whole text (up to a safety ceiling nothing reaches);
* code that finds a turn's row by its text compares the first 4,000
  characters, so a row written before still matches its turn (turn
  reconcile, the forget ledger, the archive's copies, weekly summaries);
* ``memory/text_fill.py`` brings the cut text back from the chat that still
  holds the turn, verified, the user's word first, one write;
* a list shows a memory bounded and says how much more there is;
  ``memory_read`` and the Memory page read it whole.

Each check runs the real code, and each has its negative control (most: the
old cut, put back through ``limits``).
"""

from __future__ import annotations

import ast
import asyncio
import json
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from kazma_core.memory import limits, turn_reconcile
from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
from tests._tool_answer import answer_now

REPO = Path(__file__).resolve().parents[1]

#: A long question and answer: past every old cut, Arabic and English, with a
#: word only their ends hold.
LONG_Q = ("Please read my notes on the pump housings and tell me what to change. " * 120
          + "The last word is ZEPHYRQUILL.")
LONG_A = ("Here is what I would change in the housings, step by step. " * 160
          + "الكلمة الأخيرة هي نيلوفري.")


def _u(text, ts=None):
    return {"role": "user", "content": text, **({"ts": ts} if ts else {})}


def _a(text):
    return {"role": "assistant", "content": text}


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


class _Embedder:
    dim = 8

    def __init__(self) -> None:
        self.texts: list[str] = []

    def encode(self, text: str) -> list[float]:
        self.texts.append(text)
        return [1.0] + [0.0] * 7


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    from kazma_core.memory import dual_write

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KAZMA_MEMORY_STATE_DB", str(tmp_path / "memory_state.db"))
    monkeypatch.setenv("KAZMA_MEMORY_OPS_DB", str(tmp_path / "memory_ops.db"))
    embedder = _Embedder()
    monkeypatch.setattr("kazma_core.memory.embedder.get_embedder", lambda: embedder)
    monkeypatch.setattr("kazma_core.memory.embedder.encode_text_to_blob", lambda text: None)
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    db.row_factory = sqlite3.Row
    ensure_primary_schema(db)
    ops = sqlite3.connect(str(tmp_path / "memory_ops.db"))
    ensure_ops_schema(ops)
    ops.close()
    dual_write._reset_mirror()
    yield SimpleNamespace(dir=tmp_path, db=db, embedder=embedder)
    dual_write._reset_mirror()
    db.close()


def _save(mem, session_id, messages, *, thread_id="", tenant="default"):
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(session_id=session_id, tenant_id=tenant, thread_id=thread_id,
                              messages=list(messages)))
    finally:
        store.close()


def _row(mem, eid):
    return mem.db.execute("SELECT * FROM episodes WHERE id = ?", (eid,)).fetchone()


def _old_cut(monkeypatch):
    """The writers as they were: limits' ceiling at the old cut."""
    monkeypatch.setattr(limits, "EPISODE_TEXT_MAX", limits.LEGACY_TEXT_CUT)


def _cut_row(mem, session_id, turn, *, tenant="default"):
    """The row the live writer stored for a long turn before 2026-11-19:
    4,000 characters of each side, under the turn's id."""
    from kazma_core.memory.dual_write import episode_ids

    eid = episode_ids(session_id, turn, LONG_Q.strip(), tenant)[0]
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, structural_importance, created_at, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, '', 'episodic', 1, ?, ?)",
        (eid, tenant, session_id, turn, LONG_Q[:4000], LONG_A[:4000], time.time() - 30 * 86400,
         json.dumps({"source": "dual_write_mirror"})),
    )
    mem.db.commit()
    return eid


# ── Stored whole ─────────────────────────────────────────────────────────


def test_a_long_turn_is_stored_whole(mem):
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id="s-long", turn_number=1, user_text=LONG_Q, assistant_text=LONG_A,
                         summary_text="S" * 5000)
    row = _row(mem, eid)
    assert row["user_text"] == LONG_Q and row["assistant_text"] == LONG_A
    assert row["summary_text"] == "S" * 5000
    # The vector is made from the start, as it always was.
    assert mem.embedder.texts and all(len(t) <= limits.EMBED_TEXT_CHARS for t in mem.embedder.texts)


def test_negative_control_the_old_ceiling_cuts(mem, monkeypatch):
    from kazma_core.memory.dual_write import mirror_episode

    _old_cut(monkeypatch)
    eid = mirror_episode(session_id="s-cut", turn_number=1, user_text=LONG_Q, assistant_text=LONG_A)
    assert len(_row(mem, eid)["user_text"]) == 4000


def test_turn_reconcile_writes_a_long_turn_whole(mem):
    _save(mem, "s-rec", [_u(LONG_Q, _iso(3)), _a(LONG_A)], thread_id="th-rec")
    report = turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() + 60, time.time())
    assert report["turns_written"] == 1
    row = mem.db.execute("SELECT user_text, assistant_text FROM episodes").fetchone()
    assert row["user_text"] == LONG_Q and row["assistant_text"] == LONG_A


def test_a_swarm_result_is_stored_whole(mem):
    from kazma_core.memory.swarm_bridge import store_swarm_result

    result = "Swarm finding. " * 2000
    eid = store_swarm_result("researcher", "task-long", result, tenant_id="default")
    assert eid and len(_row(mem, eid)["user_text"]) == len(result)


def test_a_turn_waiting_in_the_queue_carries_all_of_it(mem):
    from kazma_core.memory import consolidator
    from kazma_core.paths import memory_ops_db

    consolidator._defer_turn_memory([_u("x" * 30000), _a("y" * 30000)], session_id="s-q", turn=1,
                                    tenant_id="default", why="test")
    conn = sqlite3.connect(memory_ops_db())
    try:
        payload = json.loads(conn.execute(
            "SELECT payload_json FROM memory_task_queue WHERE task_type = 'post_turn_memory'").fetchone()[0])
    finally:
        conn.close()
    assert len(payload["user_text"]) == 30000 and len(payload["assistant_text"]) == 30000


# ── Old rows still match their turn ─────────────────────────────────────


def test_a_row_cut_before_still_counts_as_its_turn(mem):
    """A long turn the live writer stored cut is in memory: reconcile must
    not write it a second time."""
    _save(mem, "s-old", [_u(LONG_Q, _iso(3)), _a(LONG_A)])
    _cut_row(mem, "s-old", 1)
    report = turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() + 60, time.time())
    assert report["turns_written"] == 0
    assert mem.db.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1


def test_negative_control_compared_whole_the_cut_row_misses(mem):
    _cut_row(mem, "s-old", 1)
    texts, _stubs = turn_reconcile._in_memory(mem.db, "default", ["s-old"])
    assert texts[LONG_Q.strip()] == 0  # the whole text is not what the old row holds
    assert texts[limits.turn_key(LONG_Q)] == 1


def test_the_forget_ledger_names_the_same_turn_cut_or_whole(mem):
    from kazma_core.memory.forget import question_sha

    assert question_sha(LONG_Q) == question_sha(LONG_Q[:4000])
    assert question_sha(LONG_Q) != question_sha("another question")


def test_a_migrated_copy_of_a_long_turn_is_still_a_copy(mem):
    from kazma_core.memory.legacy_tables import legacy_copies

    whole = mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "structural_importance, created_at) VALUES ('orig', 'default', 's1', 1, ?, ?, 'episodic', 1, 0)",
        (LONG_Q, LONG_A),
    )
    assert whole.rowcount == 1
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, tier, structural_importance, "
        "created_at) VALUES ('copy', 'default', 'legacy-abc', 0, ?, 'episodic', 1, 0)",
        ("User: " + LONG_Q[:3000] + " Assistant: " + LONG_A[:300] + "…",),
    )
    mem.db.commit()
    assert legacy_copies(mem.db, "default") == {"copy": "orig"}


# ── The cut text brought back ───────────────────────────────────────────


def test_the_fill_brings_back_what_the_old_cut_left_out(mem):
    from kazma_core.memory.text_fill import run_text_fill_pass, text_fill_counts

    _save(mem, "s-fill", [_u(LONG_Q, _iso(30)), _a(LONG_A)])
    eid = _cut_row(mem, "s-fill", 1)
    before = _row(mem, eid)
    report = run_text_fill_pass()
    assert report["filled"] == 1
    row = _row(mem, eid)
    assert row["user_text"] == LONG_Q and row["assistant_text"] == LONG_A
    assert (row["tenant_id"], row["created_at"], row["tier"]) == (before["tenant_id"], before["created_at"],
                                                                    before["tier"])
    assert json.loads(row["metadata_json"])["text_fill"]["state"] == "filled"
    assert json.loads(row["metadata_json"])["source"] == "dual_write_mirror"  # merged, never replaced
    # Keyword search covers all of it: a word only the end holds is found.
    hit = mem.db.execute(
        "SELECT e.id FROM episodes_fts f JOIN episodes e ON e.rowid = f.rowid WHERE episodes_fts MATCH ?",
        ("ZEPHYRQUILL",),
    ).fetchall()
    assert [r[0] for r in hit] == [eid]
    # The mirror's outbox holds it for the next sync; the vector is unchanged
    # (its text, the first 4,000 characters, did not change): no embedding.
    assert mem.db.execute("SELECT count(*) FROM memory_mirror_outbox WHERE row_id = ?",
                          (eid,)).fetchone()[0] >= 1
    assert mem.embedder.texts == []
    assert text_fill_counts(mem.db)["filled"] == 1
    assert run_text_fill_pass()["filled"] == 0  # once


def test_negative_control_without_the_fill_the_end_is_not_findable(mem):
    _cut_row(mem, "s-fill", 1)
    assert mem.db.execute("SELECT count(*) FROM episodes_fts WHERE episodes_fts MATCH ?",
                          ("ZEPHYRQUILL",)).fetchone()[0] == 0


def test_a_turn_the_user_forgot_is_never_filled(mem):
    from kazma_core.memory.forget import question_sha
    from kazma_core.memory.text_fill import run_text_fill_pass

    _save(mem, "s-gone", [_u(LONG_Q, _iso(30)), _a(LONG_A)])
    eid = _cut_row(mem, "s-gone", 1)
    mem.db.execute(
        "INSERT INTO memory_forgotten (tenant_id, session_key, turn_number, question_sha, episode_id, "
        "forgotten_at) VALUES ('default', 's-gone', 1, ?, NULL, ?)",
        (question_sha(LONG_Q), time.time()),
    )
    mem.db.commit()
    assert run_text_fill_pass()["kept_out"] == 1
    assert len(_row(mem, eid)["user_text"]) == 4000


def test_a_forgotten_row_and_another_space_s_chat_are_never_read_in(mem):
    from kazma_core.memory.text_fill import run_text_fill_pass

    # The chat holds the turn, but in another space: the row is not filled from it.
    _save(mem, "s-other", [_u(LONG_Q, _iso(30)), _a(LONG_A)], tenant="space-b")
    eid = _cut_row(mem, "s-other", 1)
    # A forgotten memory is never looked at.
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "structural_importance, created_at) VALUES ('gone', 'default', 's-other', 2, ?, '', 'forgotten', 1, 0)",
        ("q" * 4000,),
    )
    mem.db.commit()
    report = run_text_fill_pass()
    assert report["filled"] == 0 and report["unavailable"] == 1
    assert len(_row(mem, eid)["user_text"]) == 4000
    assert _row(mem, "gone")["metadata_json"] in (None, "", "{}")


def test_a_write_that_landed_first_wins(mem, monkeypatch):
    """Compare-and-set on the text read: a forget between the read and the
    write is never overwritten."""
    from kazma_core.memory import chat_history
    from kazma_core.memory.text_fill import run_text_fill_pass

    _save(mem, "s-race", [_u(LONG_Q, _iso(30)), _a(LONG_A)])
    eid = _cut_row(mem, "s-race", 1)
    real = chat_history.conversations_for

    def read_then_forget(key, **kw):
        out = real(key, **kw)
        other = sqlite3.connect(str(mem.dir / "memory_state.db"))
        other.execute("UPDATE episodes SET user_text = '', assistant_text = '', tier = 'forgotten' WHERE id = ?",
                      (eid,))
        other.commit()
        other.close()
        return out

    monkeypatch.setattr(chat_history, "conversations_for", read_then_forget)
    assert run_text_fill_pass()["changed"] == 1
    row = _row(mem, eid)
    assert row["user_text"] == "" and row["tier"] == "forgotten"


def test_a_row_that_was_never_cut_is_marked_whole(mem):
    from kazma_core.memory.dual_write import episode_ids
    from kazma_core.memory.text_fill import run_text_fill_pass

    question = "q" * 4000
    _save(mem, "s-exact", [_u(question, _iso(30)), _a("short answer")])
    eid = episode_ids("s-exact", 1, question, "default")[0]
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, tier, "
        "structural_importance, created_at) VALUES (?, 'default', 's-exact', 1, ?, 'short answer', 'episodic', 1, 0)",
        (eid, question),
    )
    mem.db.commit()
    assert run_text_fill_pass()["whole"] == 1
    assert _row(mem, eid)["user_text"] == question


# ── Shown bounded, read whole ───────────────────────────────────────────


def test_the_recall_block_shows_a_long_memory_bounded_and_says_how_to_read_it():
    from kazma_core.memory.recall import RecallHit, RecallResult, _shown, format_recall_block

    hit = RecallHit(id="e_long", content=LONG_Q[:400], score=1.0, metadata=_shown(LONG_Q, LONG_A, ""))
    block = format_recall_block(RecallResult(beliefs=[], episodes=[hit]))
    line = next(x for x in block.splitlines() if "User:" in x)
    assert 'memory_read("e_long")' in line and "not shown" in line
    assert len(line) < limits.RECALL_SHOW_QUESTION + limits.RECALL_SHOW_ANSWER + 200
    assert "ZEPHYRQUILL" not in block
    # A short memory carries no note.
    short = RecallHit(id="e_s", content="hi", score=1.0, metadata=_shown("short question", "short answer", ""))
    assert "memory_read" not in format_recall_block(RecallResult(beliefs=[], episodes=[short])).split(
        "## Relevant History", 1)[1]


def test_the_note_is_in_the_memory_s_language_and_right_for_any_number():
    from kazma_core.memory.episode_text import more_note

    assert more_note(1, "e_1", "an English memory") == '[1 character not shown: memory_read("e_1") reads the whole memory]'
    assert "2,500 characters" in more_note(2500, "e_1", "an English memory")
    assert more_note(7, "e_1", "ذاكرة باللغة العربية").startswith("[لم يُعرض 7 من الأحرف")
    assert more_note(0, "e_1", "x") == ""


def _tools():
    from kazma_core.agent.tool_builtins.memory import register_memory_tools

    tools: dict = {}

    class _Registry:
        def register(self, **_kw):
            def deco(fn):
                tools[fn.__name__] = fn
                return fn

            return deco

    register_memory_tools(_Registry())
    return tools


def _as_tenant(tenant, fn, **kwargs):
    from kazma_core.tenant_context import reset_current_tenant_id, set_current_tenant_id

    token = set_current_tenant_id(tenant)
    try:
        return answer_now(fn, **kwargs)
    finally:
        reset_current_tenant_id(token)


def test_memory_read_pages_through_a_whole_memory_fenced(mem):
    from kazma_core.memory.dual_write import mirror_episode

    eid = mirror_episode(session_id="s-read", turn_number=1, user_text=LONG_Q, assistant_text=LONG_A)
    read = _tools()["memory_read"]
    pages, offset = [], 0
    while offset is not None and len(pages) < 20:
        out = _as_tenant("default", read, id=eid, offset=offset)
        assert "kazma:data" in out  # the memory is data, never instructions
        body = out.split("\n", 1)[1]
        pages.append(body)
        if "Next page" not in out.split("\n", 1)[0]:
            offset = None
        else:
            offset = int(out.split("offset=", 1)[1].split(")", 1)[0])
    assert len(pages) >= 2
    joined = "".join(pages)
    assert "ZEPHYRQUILL" in joined and "نيلوفري" in joined


def test_memory_read_keeps_to_the_caller_s_space(mem, monkeypatch):
    from kazma_core.memory.dual_write import mirror_episode

    monkeypatch.setattr("kazma_core.memory.tenant_view.spaces_enforced", lambda: True)
    eid = mirror_episode(session_id="s-b", turn_number=1, user_text="space b's secret plan",
                         assistant_text="noted", tenant_id="space-b")
    read = _tools()["memory_read"]
    assert "No memory" in _as_tenant("default", read, id=eid)
    assert "secret plan" in _as_tenant("space-b", read, id=eid)


def test_memory_search_shows_up_to_the_list_bound_with_the_note(mem, monkeypatch):
    from kazma_core.memory.dual_write import mirror_episode
    from kazma_core.memory.recall import RecallHit, RecallResult, _shown

    eid = mirror_episode(session_id="s-srch", turn_number=1, user_text=LONG_Q, assistant_text=LONG_A)
    hit = RecallHit(id=eid, content=LONG_Q[:400], score=1.0, metadata=_shown(LONG_Q, LONG_A, ""))
    monkeypatch.setattr("kazma_core.memory.recall.recall",
                        lambda *a, **k: RecallResult(beliefs=[], episodes=[hit]))
    out = _as_tenant("default", _tools()["memory_search"], query="pump housings")
    body = out.split("--- BEGIN OBSERVATION ---\n", 1)[1].split("\n--- END OBSERVATION ---", 1)[0]
    content = json.loads(body)[0]["content"]
    assert f'memory_read("{eid}")' in content
    assert len(content) <= limits.MEMORY_SHOW_MAX + 200


# ── The gate: no episode text cut anywhere but limits.py ────────────────

#: The modules that hold, read or show memories of conversations.
GATE_FILES = sorted(
    {p.relative_to(REPO).as_posix() for p in (REPO / "kazma-core" / "kazma_core" / "memory").glob("*.py")}
    | {"kazma-core/kazma_core/agent/tool_builtins/memory.py", "kazma-ui/kazma_ui/memory_api.py",
       "kazma-ui/kazma_ui/routes_direct/memory.py"}
)
_TEXT_NAMES = ("user_text", "assistant_text", "summary_text")


def _limits_names(tree: ast.AST) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "kazma_core.memory.limits":
            out.update(a.asname or a.name for a in node.names)
    return out


def text_cuts(source: str, *, rel: str = "x.py") -> list[str]:
    """Slices of a memory's text whose bound is not a name ``limits`` defines."""
    if rel.endswith("memory/limits.py"):
        return []
    tree = ast.parse(source)
    allowed = _limits_names(tree)
    found: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice)):
            continue
        upper = node.slice.upper
        if upper is None:
            continue
        value = ast.unparse(node.value)
        if not any(name in value for name in _TEXT_NAMES):
            continue
        if isinstance(upper, ast.Name) and upper.id in allowed:
            continue
        if isinstance(upper, ast.Attribute) and ast.unparse(upper).startswith("limits."):
            continue
        found.append(f"{rel}:{node.lineno}: {ast.unparse(node)}")
    return found


def test_no_memory_text_is_cut_anywhere_but_limits():
    found: list[str] = []
    for rel in GATE_FILES:
        found += text_cuts((REPO / rel).read_text(encoding="utf-8"), rel=rel)
    assert found == [], (
        "a memory's text is cut with a bound of its own; memories are kept whole and every "
        "bound lives in kazma_core/memory/limits.py:\n  " + "\n  ".join(found)
    )


def test_negative_control_the_old_cuts_are_found():
    old = (
        'def episode_row(user_text, assistant_text, summary_text):\n'
        '    return {"user_text": (user_text or "")[:4000], "assistant_text": (assistant_text or "")[:4000],\n'
        '            "summary_text": (summary_text or "")[:2000]}\n'
        '_TEXT_CAP = 4000\n'
        'def bridge(user_text):\n'
        '    return user_text[:8000], user_text[:_TEXT_CAP]\n'
    )
    assert len(text_cuts(old)) == 5
    fixed = 'from kazma_core.memory.limits import DEEP_PASS_QUESTION\nx = user_text[:DEEP_PASS_QUESTION]\n'
    assert text_cuts(fixed) == []


def test_the_old_caps_are_gone_from_the_writers():
    for rel in ("kazma-core/kazma_core/memory/dual_write.py", "kazma-core/kazma_core/memory/turn_reconcile.py",
                "kazma-core/kazma_core/memory/swarm_bridge.py", "kazma-core/kazma_core/memory/consolidator.py",
                "kazma-core/kazma_core/memory/forget.py"):
        text = (REPO / rel).read_text(encoding="utf-8")
        assert "_TEXT_CAP" not in text and "_DEFERRED_TEXT_MAX" not in text, rel
