"""Forget converges, and a restore does not undo it (Memory audits M08, M10).

Forget wrote straight into ``memory_state.db`` with a commit in the middle
(``invalidate_belief`` commits as it goes): a forget cut short was reported
done -- asking again finished the facts only, summaries stayed active, remote
vectors were never retried -- a keep-out landing between the writer's check
and its insert was lost, and recovery refilled an erased copy of a forgotten
turn. And because the ledger lives in the memory database, restoring an older
copy brought back every turn forgotten since; turn reconcile rebuilt the rest.

Now every forget is journaled first (``forget_journal``, its own file), every
step re-runs, and the maintenance pass "forget convergence" applies what the
memory database in place has not, enforces the ledger on stored rows and
retries remote deletes. Here:

- a fault after EACH step of a forget, then one pass: the M01 completeness
  scan passes (negative control: no pass -- something is left every time);
- the audit's three L05 witnesses inverted;
- the writer's check and insert as one transaction, with a keep-out racing it
  on another thread; a row that slipped through is forgotten by the pass;
- a pre-forget copy of ``memory_state.db`` restored: the pass forgets the turn
  again, and turn reconcile does not rebuild it (negative control: no pass).
"""

from __future__ import annotations

import ast
import asyncio
import json
import shutil
import sqlite3
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

from kazma_core.memory import forget, forget_journal
from tests._memory_plant import QUESTION, _everywhere, _plant, open_mem

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture()
def mem(tmp_path, monkeypatch):
    yield from open_mem(tmp_path, monkeypatch)


def _clean(mem, planted) -> bool:
    held = {planted["eid"], planted["note"], *planted["facts"]} & mem.vectors.held
    conn = sqlite3.connect(str(mem.dir / "memory_state.db"))
    try:
        left = len(forget_journal.pending(conn))
    finally:
        conn.close()
    return not _everywhere(mem) and not held and left == 0


# ── A fault after each step, then one pass ────────────────────────────────

#: (where, what) of each step of ``forget_episode``, in order.
STEPS = [
    (forget, "_tombstone"),
    (forget, "_scrub_legacy_copies"),
    (forget, "_forget_turn_facts"),
    (forget, "_forget_archived_facts"),
    ("kazma_core.memory.topic_summaries", "on_turns_forgotten"),
    (forget, "purge_search_index"),
    ("kazma_core.memory.state_backend", "remirror_episode_by_id"),
    (forget, "_delete_remote_vector"),
    (forget, "_after_facts"),
    (forget_journal, "mark_applied"),
]


def _fault(monkeypatch, where, name):
    import importlib

    module = importlib.import_module(where) if isinstance(where, str) else where
    real = getattr(module, name)
    fired = {"n": 0}

    def once(*args, **kwargs):
        if not fired["n"]:
            fired["n"] += 1
            raise RuntimeError(f"process died in {name}")
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, once)
    return fired


@pytest.mark.parametrize(("where", "name"), STEPS, ids=[s[1] for s in STEPS])
def test_a_forget_cut_short_at_any_step_converges_in_one_pass(mem, monkeypatch, where, name):
    planted = _plant(mem)
    fired = _fault(monkeypatch, where, name)
    with pytest.raises(RuntimeError):
        forget.forget_episode(planted["eid"], tenant_id="default")
    assert fired["n"] == 1
    forget.converge()
    assert _clean(mem, planted), (name, sorted(_everywhere(mem)))


@pytest.mark.parametrize(("where", "name"), STEPS, ids=[s[1] for s in STEPS])
def test_without_the_pass_a_forget_cut_short_is_left_unfinished(mem, monkeypatch, where, name):
    """Negative control: the same faults, no maintenance pass."""
    planted = _plant(mem)
    _fault(monkeypatch, where, name)
    with pytest.raises(RuntimeError):
        forget.forget_episode(planted["eid"], tenant_id="default")
    assert not _clean(mem, planted), name


# ── The audit's L05 witnesses, inverted ───────────────────────────────────


def _week():
    from kazma_core.memory import topic_summaries as ts

    return ts._week_of(time.time() - 21 * 86400)


def _ship_week(mem):
    _key, start, _end = _week()
    for i in range(5):
        mem.db.execute(
            "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "tier, created_at, metadata_json) VALUES (?, 'default', 'c1', ?, ?, ?, 'episodic', ?, '{}')",
            (f"ship{i}", i + 1, f"ShipX delivery routing phase {i} status",
             f"ShipX phase {i} is built.", start + 3600 * (i + 1)),
        )
    mem.db.commit()


async def _fake_chat(messages):
    body = messages[-1]["content"]
    return json.dumps({"title": "ShipX", "summary": "ShipX: " + body.split("\n\n", 1)[1][:600], "skip": False})


def test_l05_a_forget_cut_short_finishes_its_summary_when_asked_again(mem, monkeypatch):
    from kazma_core.memory import topic_summaries as ts

    monkeypatch.setattr("kazma_core.memory.task_queue.enqueue_task", lambda *a, **k: "t")
    monkeypatch.setattr(forget, "chat_keys", lambda key: [key] if key else [])
    _ship_week(mem)
    key, start, end = _week()
    assert asyncio.run(ts.summarize_period("default", key, start, end, chat=_fake_chat))
    # A fact of the turn: invalidating it commits as it goes, so the forget's
    # tombstone is committed before the summary step (the witness's shape).
    from kazma_core.memory.belief_mutation import mutate_belief

    mutate_belief(mem.db, "shipx", "phase_2_state", "built", predicate_type="functional",
                  source_session="c1", source_turn=3, extraction_method="user_explicit")
    mem.db.commit()
    _fault(monkeypatch, ts, "on_turns_forgotten")
    with pytest.raises(RuntimeError):
        forget.forget_episode("ship2", tenant_id="default")
    again = forget.forget_episode("ship2", tenant_id="default")
    assert again["ok"] and again.get("already") is True
    status, text = mem.db.execute(
        "SELECT s.status, s.summary_text FROM memory_summaries s JOIN memory_summary_sources src "
        "ON src.summary_id = s.id WHERE src.episode_id = 'ship2'").fetchone()
    assert status == "rebuild" and "phase 2" not in text


def _iso(days_ago: float) -> str:
    return datetime.fromtimestamp(time.time() - days_ago * 86400, UTC).isoformat()


WORK_Q = "where do I work these days"
WORK_A = "At Acme, in Lisbon."


def _work_chat(mem):
    from kazma_ui.session_manager import ChatSession, SessionManager

    store = SessionManager(db_path=str(mem.dir / "chat_sessions.db"))
    try:
        store.put(ChatSession(
            session_id="w1", tenant_id="default", thread_id="wth1", title="Work",
            messages=[{"role": "user", "content": WORK_Q, "ts": _iso(40)}, {"role": "assistant", "content": WORK_A}],
        ))
    finally:
        store.close()


def _live_turn(key="wth1"):
    from kazma_core.memory.dual_write import mirror_episode

    return mirror_episode(session_id=key, turn_number=1, user_text=WORK_Q, assistant_text=WORK_A,
                          created_at=time.time() - 40 * 86400)


def test_l05_a_keep_out_racing_a_write_leaves_the_turn_forgotten(mem, monkeypatch):
    """The writer's ledger check and insert are one transaction: a keep-out +
    forget-past that starts between them waits for the insert, then finds it."""
    _work_chat(mem)
    real = forget.refuses_write
    worker: dict = {}

    def check_then_user_acts(conn, **kw):
        out = real(conn, **kw)
        if "t" not in worker:
            def act():
                forget.set_chat_remembered("wth1", False, tenant_id="default")
                worker["forgot"] = forget.forget_chat("wth1", tenant_id="default")
            worker["t"] = threading.Thread(target=act)
            worker["t"].start()
            time.sleep(0.5)  # the user's request is under way while the writer holds its transaction
        return out

    monkeypatch.setattr(forget, "refuses_write", check_then_user_acts)
    eid = _live_turn()
    monkeypatch.setattr(forget, "refuses_write", real)
    worker["t"].join(30)
    assert eid and worker["forgot"]["ok"]
    assert mem.db.execute("SELECT tier FROM episodes WHERE id = ?", (eid,)).fetchone()[0] == forget.FORGOTTEN_TIER


def test_a_row_that_slipped_past_a_keep_out_is_forgotten_by_the_pass(mem, monkeypatch):
    """However a row gets past the ledger, the pass applies the ledger to it
    (negative control: before the pass, it is there)."""
    _work_chat(mem)
    forget.set_chat_remembered("wth1", False, tenant_id="default")
    from kazma_core.memory.dual_write import mirror_episode

    with monkeypatch.context() as mp:
        mp.setattr(forget, "refuses_write", lambda conn, **kw: False)
        # A live turn's write: its time is the turn's close, after the keep-out.
        eid = mirror_episode(session_id="wth1", turn_number=2, user_text="and the office?",
                             assistant_text="Lisbon.", created_at=time.time())
    assert mem.db.execute("SELECT tier FROM episodes WHERE id = ?", (eid,)).fetchone()[0] != forget.FORGOTTEN_TIER
    forget.converge()
    assert mem.db.execute("SELECT tier FROM episodes WHERE id = ?", (eid,)).fetchone()[0] == forget.FORGOTTEN_TIER


def test_l05_recovery_never_refills_an_erased_copy_of_a_forgotten_turn(mem):
    from kazma_core.memory import rehydrate
    from kazma_core.memory.dual_write import _episode_id

    _work_chat(mem)
    eid = _live_turn()
    erased = _episode_id("w1", 1, WORK_Q)
    mem.db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
        "summary_text, tier, created_at, metadata_json) VALUES (?, 'default', 'w1', 1, NULL, NULL, ?, "
        "'archived', ?, '{}')",
        (erased, rehydrate._archive_stub(WORK_Q, WORK_A, ""), time.time() - 40 * 86400))
    mem.db.commit()
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    report = rehydrate._rehydrate_erased(mem.db, time_budget_s=30)
    assert report.get("restored", 0) == 0 and report.get("forgotten") == 1
    assert mem.db.execute("SELECT user_text FROM episodes WHERE id = ?", (erased,)).fetchone()[0] is None


# ── A restore does not undo a forget (M10) ────────────────────────────────


def _restore(mem, backup: Path) -> None:
    """Put an older copy of memory_state.db in place, as an operator would."""
    from kazma_core.memory import dual_write

    mem.db.close()
    dual_write._reset_mirror()
    forget._schema_ready.clear()
    target = mem.dir / "memory_state.db"
    for suffix in ("-wal", "-shm"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    shutil.copyfile(backup, target)
    mem.db = sqlite3.connect(str(target))
    mem.db.row_factory = sqlite3.Row


def _backup(mem) -> Path:
    path = mem.dir / "memory_state.pre-forget.db"
    dest = sqlite3.connect(str(path))
    try:
        mem.db.commit()
        mem.db.backup(dest)
    finally:
        dest.close()
    return path


def test_a_restored_copy_forgets_again_and_reconcile_does_not_rebuild(mem):
    from kazma_core.memory import turn_reconcile

    _work_chat(mem)
    eid = _live_turn()
    backup = _backup(mem)
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    _restore(mem, backup)
    # The restored copy holds the turn again (negative control) ...
    assert mem.db.execute("SELECT user_text FROM episodes WHERE id = ?", (eid,)).fetchone()[0] == WORK_Q
    forget.converge()
    # ... the pass forgets it again, and turn reconcile does not bring it back.
    turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() + 60, time.time())
    rows = mem.db.execute("SELECT tier FROM episodes WHERE user_text = ? OR id = ?", (WORK_Q, eid)).fetchall()
    assert [r[0] for r in rows] == [forget.FORGOTTEN_TIER]


def test_without_the_replay_reconcile_and_recall_keep_the_restored_turn(mem):
    """Negative control: the same restore, no pass."""
    from kazma_core.memory import turn_reconcile

    _work_chat(mem)
    eid = _live_turn()
    backup = _backup(mem)
    forget.forget_episode(eid, tenant_id="default")
    _restore(mem, backup)
    turn_reconcile._reconcile(mem.db, ("", ""), time.monotonic() + 60, time.time())
    tiers = [r[0] for r in mem.db.execute("SELECT tier FROM episodes WHERE user_text = ?", (WORK_Q,))]
    assert tiers and forget.FORGOTTEN_TIER not in tiers


def test_a_restore_replays_keep_outs_and_summary_forgets_in_order(mem):
    """A keep-out, then letting the chat back in: the restored copy ends where
    the user left it; a forgotten summary is forgotten again."""
    _work_chat(mem)
    backup = _backup(mem)
    forget.set_chat_remembered("wth1", False, tenant_id="default")
    forget.set_chat_remembered("wth1", True, tenant_id="default")
    forget.set_chat_remembered("other", False, tenant_id="default")
    _restore(mem, backup)
    forget.converge()
    assert forget.chat_remembered("wth1", tenant_id="default", conn=mem.db)
    assert not forget.chat_remembered("other", tenant_id="default", conn=mem.db)


def test_forget_convergence_runs_before_reconcile_and_the_mirror_sync():
    from kazma_core.memory import worker_bootstrap as wb

    labels = [label for label, _ in wb._MAINTENANCE_SWEEPS]
    first = labels.index("forget convergence")
    assert first < labels.index("memory turn reconcile") and first < labels.index("memory mirror sync")
    assert first < labels.index("memory recovery")


# ── The gate: only the journal's own replay skips the journal ─────────────


def _unjournaled_calls(sources: dict[str, str]) -> list[str]:
    out = []
    for rel, text in sources.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.keyword) and node.arg == "journal" and \
                    isinstance(node.value, ast.Constant) and node.value.value is False:
                out.append(rel)
    return sorted(set(out))


def test_only_the_forget_code_skips_the_journal():
    sources = {}
    for pkg in ("kazma-core/kazma_core", "kazma-ui/kazma_ui", "kazma-gateway/kazma_gateway"):
        for p in (REPO / pkg).rglob("*.py"):
            sources[p.relative_to(REPO).as_posix()] = p.read_text(encoding="utf-8", errors="replace")
    assert _unjournaled_calls(sources) == [
        "kazma-core/kazma_core/memory/forget.py", "kazma-core/kazma_core/memory/forget_journal.py"]


def test_a_route_skipping_the_journal_is_caught():
    """Negative control."""
    planted = "forget_episode(eid, tenant_id=tid, journal=False)\n"
    assert _unjournaled_calls({"kazma-ui/kazma_ui/memory_api.py": planted}) == ["kazma-ui/kazma_ui/memory_api.py"]


def test_the_journal_holds_no_text(mem):
    planted = _plant(mem)
    forget.forget_episode(planted["eid"], tenant_id="default")
    journal = sqlite3.connect(str(mem.dir / "memory_forget_journal.db"))
    try:
        rows = journal.execute("SELECT * FROM forget_journal").fetchall()
    finally:
        journal.close()
    assert rows and QUESTION not in json.dumps(rows)


def test_the_restore_drill_names_the_forgets_a_backup_would_replay(mem):
    """The drill reads the live journal and the backup's applied marks, and
    writes nothing (the journal file is not created by the check)."""
    from kazma_core.backup.restore_drill import verify_backup

    _work_chat(mem)
    eid = _live_turn()
    pre = _backup(mem)
    assert forget.forget_episode(eid, tenant_id="default")["ok"]
    backup = mem.dir / "universal-1"
    (backup / "dbs").mkdir(parents=True)
    shutil.copyfile(pre, backup / "dbs" / "memory_state.db")
    res = verify_backup(backup, scratch_dir=mem.dir / "scratch")
    line = next(c for c in res.checks if c["check"] == "memory:forget_journal")
    assert line["ok"] and "1 made since" in line["detail"], line
