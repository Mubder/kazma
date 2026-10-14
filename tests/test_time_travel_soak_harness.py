"""The Time Travel soak harness works, and sees a violation (TT-G3).

``scripts/time_travel_soak.py`` is the harness for the soak of hours or days
that TT-G3 asks for; a session cannot hold that run, so this keeps the
harness honest. A short run (a 300-iteration mission instead of 2,000) ends
healthy having done the work it reports: captures, cut steps, every user
operation, every fault kind with its honest ending, the three kills and the
turns they cut short continued (the two kills inside a step's write
transaction checked to have stored nothing of it), the prune and a VACUUM,
peaks within the declared budgets with no slack after a kill; and its report
is on disk, ``running``, while it goes. The negative controls plant what the
checks must see, on a copy of the run's stores: rows past a cap (also on the
turn a kill cut short), a step in flight at a kill found stored, a cleared
chat's step and a deleted step back, a restore whose state is not its step's,
a checkpoint changed across a restart.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sqlite3
import sys
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import time_travel_soak as soak  # noqa: E402

pytestmark = pytest.mark.timeout(300)

#: Each status a reader of the report file saw, in order, during the run.
_WRITTEN: list[str] = []


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    data = tmp_path_factory.mktemp("tt-soak") / "data"
    report = tmp_path_factory.mktemp("tt-soak-report") / "report.json"
    real_write = soak._write_report

    def record(path, summary):
        real_write(path, summary)
        # What a reader of the file sees at that moment, not what was passed.
        _WRITTEN.append(json.loads(Path(path).read_text(encoding="utf-8"))["status"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(soak, "_write_report", record)
        code = soak.main([
            "--duration", "240", "--max-cycles", "5", "--cycle-seconds", "0", "--chats", "3",
            "--mission-iterations", "300", "--mission-chunk-min", "2000", "--mission-chunk-max", "3000",
            "--seed", "7", "--data-dir", str(data), "--report", str(report),
        ])
    return code, json.loads(report.read_text(encoding="utf-8")), data


def _copy(data: Path, tmp_path: Path) -> tuple[Path, dict]:
    copy = tmp_path / "copy"
    shutil.copytree(data, copy)
    return copy, json.loads((copy / "soak" / "state.json").read_text(encoding="utf-8"))


def _insert(db: Path, *, sid: str, thread: str, turn_key: str = "t", seq: int = 0) -> None:
    with closing(sqlite3.connect(str(db))) as conn, conn:
        conn.execute(
            "INSERT INTO snapshots (id, thread_id, iteration, state_json, timestamp, turn_key, turn_index, "
            "message_count, nbytes, truncated, seq) VALUES (?, ?, 1, '{}', ?, ?, 1, 0, 2, 0, ?)",
            (sid, thread, datetime.now(UTC).isoformat(), turn_key, seq or 9 * 10**18),
        )


def test_a_short_soak_does_the_work_it_reports_and_is_healthy(run):
    code, report, _ = run
    assert code == 0 and report["healthy"], report["violations"]
    assert report["status"] == "passed" and report["cycles"] == 6
    captures = report["captures"]
    # Every capture kind: stored, cut (the large chats), thinned (the missions)
    # and failed (one per write-lock, I/O and unreadable fault, counted).
    assert captures["stored"] > 50 and captures["truncated"] >= 2 and captures["skipped"] > 100, captures
    assert captures["failed"] == 3, captures
    for kind in soak._OP_KINDS:
        assert report["operations"][kind]["outcomes"].get("ok"), (kind, report["operations"].get(kind))
    for kind in soak._FAULT_KINDS:
        fault = report["faults"][kind]
        assert fault["injected"] >= 1 and "VIOLATION" not in fault["endings"], (kind, fault)
    kills = report["kills"]
    assert kills.get("after_insert") == 1 and kills.get("mid_evict") == 1, kills
    assert kills.get("hard", 0) + kills.get("hard (the cycle ended first)", 0) == 1, kills
    # Each kill inside a step's transaction stored nothing of that step.
    assert report["kill_checks"] == {"in_flight_not_stored": 2}, report["kill_checks"]
    assert "peaks_right_after_a_kill" not in report  # no slack after a kill
    assert report["missions"]["turns"] == 4 and report["missions"]["resumed"] >= 2, report["missions"]
    maintenance = report["maintenance"]
    assert maintenance["runs"] == 3 and maintenance["vacuums"] >= 1 and maintenance["pruned_rows"] >= 3, maintenance
    chats = report["chats"]
    assert chats["large"] >= 2 and chats["cut_steps"] >= 2 and chats["forked"] >= 1 and chats["deleted"] >= 3, chats
    budgets, peaks = report["budgets"], report["peaks"]
    assert budgets["max_per_turn"] == 30 and budgets["max_snapshot_bytes"] == 2 * 1024 * 1024  # the shipped budgets
    assert peaks["rows_per_turn"] <= budgets["max_per_turn"]
    assert peaks["rows_per_chat"] <= budgets["max_snapshots"]
    assert peaks["stored_bytes_per_chat"] <= budgets["max_thread_bytes"]
    assert peaks["step_bytes"] <= budgets["max_snapshot_bytes"]
    assert peaks["db_file_bytes"] <= budgets["db_file_bytes"] and peaks["wal_file_bytes"] <= budgets["wal_file_bytes"]
    assert peaks["rss_mib"] <= budgets["rss_ceiling_mib"] and peaks["rss_growth_mib"] <= budgets["rss_growth_mib"]
    assert report["operations"]["restore"]["latency_ms"]["n"] >= 4


def test_the_report_is_on_disk_while_the_soak_runs(run):
    """A run of days that a reboot ends early must still leave its evidence:
    the report is rewritten, whole, after every cycle, then marked finished."""
    assert _WRITTEN.count("running") >= 5, _WRITTEN
    assert _WRITTEN[-1] == "passed" and "passed" not in _WRITTEN[:-1], _WRITTEN


def test_the_check_sees_rows_past_a_cap(run, tmp_path):
    """Negative control: one turn given a step more than its cap, directly."""
    copy, state = _copy(run[2], tmp_path)
    assert soak._check(copy, state) == []
    for i in range(state["limits"]["max_per_turn"] + 1):
        _insert(copy / "snapshots.db", sid=uuid.uuid4().hex, thread="planted", turn_key="one turn", seq=9 * 10**18 + i)
    problems = soak._check(copy, state)
    assert any(p.startswith("cap: turn one turn of planted holds 31 steps") for p in problems), problems


def test_a_step_over_its_cap_right_after_a_kill_is_a_violation(run, tmp_path):
    """Negative control: the turn a kill cut short, one step over its cap
    (what the two transactions left: 31 of 30). The check used to allow it
    until the turn continued; a step and its eviction are one transaction
    now, so it is a violation -- even with a record that still names the
    turn as the old harness's allowance did."""
    copy, state = _copy(run[2], tmp_path)
    cap = state["limits"]["max_per_turn"]
    for i in range(cap + 1):
        _insert(copy / "snapshots.db", sid=uuid.uuid4().hex, thread="soak-m9", turn_key="soak-m9-turn",
                seq=9 * 10**18 + i)
    old_record = {**state, "allow": {"thread": "soak-m9", "turn_key": "soak-m9-turn"}}
    for record in (state, old_record):
        problems = soak._check(copy, record)
        assert any(p.startswith(f"cap: turn soak-m9-turn of soak-m9 holds {cap + 1} steps") for p in problems), \
            problems


def test_the_kill_check_sees_the_step_in_flight_stored(run, tmp_path):
    """Negative control: after a kill inside a step's write transaction, the
    step in flight must not be in the store, and the kill must have landed
    inside the transaction. A step found stored (what the old separate insert
    left), a kill outside the transaction and a missing record are named."""
    copy, _ = _copy(run[2], tmp_path)
    facts = soak._store_facts(copy)
    stored = facts["rows"][0]["id"]
    assert soak._kill_problems(facts, {"id": uuid.uuid4().hex, "in_transaction": True}, "after_insert") == []
    found = soak._kill_problems(facts, {"id": stored, "iteration": 7, "in_transaction": True}, "mid_evict")
    assert found == [f"kill: step {stored} (iteration 7) was in flight at the mid_evict kill and is in the store"], \
        found
    outside = soak._kill_problems(facts, {"id": "x", "in_transaction": False}, "after_insert")
    assert outside == ["kill: the after_insert kill of step x landed outside its write transaction"], outside
    assert soak._kill_problems(facts, None, "after_insert") == [
        "kill: the after_insert kill left no record of the step in flight"]


def test_the_check_sees_a_cleared_step_put_back(run, tmp_path):
    """Negative control: a step of a chat the run deleted, and a step id the
    run saw deleted, put back in the store."""
    copy, state = _copy(run[2], tmp_path)
    assert state["retired"] and state["gone"], state
    assert soak._check(copy, state) == []
    cleared, deleted = state["retired"][0], state["gone"][0]
    _insert(copy / "snapshots.db", sid=uuid.uuid4().hex, thread=cleared)
    _insert(copy / "snapshots.db", sid=deleted, thread="elsewhere")
    problems = soak._check(copy, state)
    assert any(p.startswith(f"back: chat {cleared} ") for p in problems), problems
    assert any(p.startswith(f"back: step {deleted} ") for p in problems), problems


def test_the_check_sees_a_restore_that_does_not_match_its_step(run, tmp_path):
    """Negative control: a chat restored from a step, as the restore must
    leave it, passes; with a message missing, a message changed, the step's
    unanswered tool call kept, or left runnable, it does not."""
    copy, _ = _copy(run[2], tmp_path)
    with closing(sqlite3.connect(str(copy / "snapshots.db"))) as conn:
        rows = [json.loads(r[0]) for r in conn.execute(
            "SELECT state_json FROM snapshots WHERE truncated = 0 AND message_count >= 4 ORDER BY seq")]
    mid_turn = next(s for s in rows if s["messages"][-1].get("tool_calls"))
    restored = {"messages": soak._expected_after_restore(mid_turn["messages"]), "tool_calls_pending": [],
                "scratchpad": dict(mid_turn.get("scratchpad") or {})}
    assert soak._restore_problems(mid_turn, restored, ()) == []
    assert len(restored["messages"]) == len(mid_turn["messages"]) - 1  # the unanswered call is dropped
    short = {**restored, "messages": restored["messages"][:-1]}
    changed = {**restored, "messages": [*restored["messages"][:-1], {**restored["messages"][-1], "content": "x"}]}
    kept_call = {**restored, "messages": mid_turn["messages"]}
    for planted in (short, changed, kept_call):
        assert soak._restore_problems(mid_turn, planted, ()), planted["messages"][-1]
    assert soak._restore_problems(mid_turn, restored, ("tool_worker",)) == ["left runnable (next ['tool_worker'])"]
    assert soak._restore_problems(mid_turn, {**restored, "scratchpad": {"note": "later"}}, ())


def test_a_checkpoint_changed_across_a_restart_is_seen(run, tmp_path):
    """The restart check reads each chat's checkpoint back by digest: on the
    run's stores it reads what the last cycle recorded, and a checkpoint
    rewritten since (a planted write) reads differently."""
    copy, state = _copy(run[2], tmp_path)
    thread, recorded = sorted(state["digests"].items())[0]

    async def read_tamper_read():
        from kazma_core.checkpoints_shared import close_shared_checkpoints, get_shared_sqlite_saver

        saver = await get_shared_sqlite_saver(str(copy / "checkpoints.db"))
        try:
            before = await soak._checkpoint_digests(saver, [thread])
            graph = soak._build_graph(None, saver, {})
            await graph.aupdate_state(soak._cfg(thread), {"messages": [{"role": "user", "content": "planted"}]},
                                      as_node="respond")
            after = await soak._checkpoint_digests(saver, [thread])
        finally:
            await close_shared_checkpoints()
        return before[thread], after[thread]

    if sys.platform == "win32":
        before, after = asyncio.run(read_tamper_read(), loop_factory=asyncio.SelectorEventLoop)
    else:
        before, after = asyncio.run(read_tamper_read())
    assert before == recorded and after != recorded
