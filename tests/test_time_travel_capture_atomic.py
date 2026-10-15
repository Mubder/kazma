"""A captured step and its eviction are one write transaction (TT-06, TT-G3).

The TT-G3 soak (``scripts/time_travel_soak.py``) found it: ``capture`` called
``SnapshotStore.save`` (the step's INSERT, committed on its own: the store's
connection is autocommit) and then ``enforce_bounds`` (one ``BEGIN
IMMEDIATE`` evicting until every budget holds). A process killed between the
two left the turn one step over its per-turn cap (31 of 30), and a chat never
captured again kept it until the retention pruned it; an eviction that failed
left a step stored that the capture had said was not.

Now ``capture`` calls only ``enforce_bounds(record, insert=True, ...)``,
which inserts inside its transaction. This file:

* kills a real child process (``os._exit``) at each point of a capture --
  right after the INSERT, inside the eviction, at its end before the COMMIT,
  right after the COMMIT -- and checks the store it leaves: the old state
  (the step not stored) or the new state (the step with its eviction), within
  every cap. Negative control: the old two transactions, killed between them,
  leave the turn one step over its cap;
* traces the statements of a capture: every write inside one ``BEGIN
  IMMEDIATE`` ... ``COMMIT`` (control: the old sequence's INSERT outside it);
* a class gate: in product code only ``SnapshotStore._insert`` writes a
  step, only from inside that transaction (or the seeding ``save``), nothing
  calls ``save``, and nothing outside ``time_travel.py`` holds a
  ``SnapshotStore`` (control: the old capture's source fails it).

The child runs this worktree's code: it is started WITHOUT ``-I`` and
inherits ``PYTHONPATH`` from the test process.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sqlite3
import subprocess
import sys
import textwrap
from contextlib import closing
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_TIME_TRAVEL = "kazma-core/kazma_core/time_travel.py"

#: Steps a turn keeps in these tests (the floor is 2; the shipped cap is 30).
_CAP = 4
#: Every other budget far away, so the per-turn cap is the one that bites.
_BUDGETS = {"max_per_turn": _CAP, "max_snapshots": 1000, "max_global_snapshots": 1000,
            "max_snapshot_bytes": 1 << 20, "max_thread_bytes": 1 << 30, "max_total_bytes": 1 << 30}
#: What ``enforce_bounds`` takes (the per-step ceiling is the capture's).
_EVICTION = {k: v for k, v in _BUDGETS.items() if k != "max_snapshot_bytes"}

_CHILD = textwrap.dedent(r"""
    import json, os, sys
    db, point, flight_file = sys.argv[1], sys.argv[2], sys.argv[3]
    budgets = json.loads(sys.argv[4])
    from kazma_core.time_travel import SnapshotRecorder, SnapshotStore

    def die():
        sys.stdout.flush()
        os._exit(17)

    real_insert = SnapshotStore._insert
    real_delete = SnapshotStore._delete_ids
    real_trim = SnapshotStore._trim_bytes
    real_enforce = SnapshotStore.enforce_bounds
    trims = [0]

    def insert(store, record):
        # The step in flight, named before it is written.
        with open(flight_file, "w", encoding="utf-8") as f:
            f.write(json.dumps({"id": record.id}))
        real_insert(store, record)
        if point == "after_insert":
            with open(flight_file, "w", encoding="utf-8") as f:
                f.write(json.dumps({"id": record.id, "in_transaction": store._conn.in_transaction}))
            die()

    def delete_ids(store, ids):
        n = real_delete(store, ids)
        if point == "mid_evict" and n:
            die()
        return n

    def trim(store, *a, **k):
        n = real_trim(store, *a, **k)
        trims[0] += 1
        if point == "before_commit" and trims[0] == 2:  # the last statement before COMMIT
            die()
        return n

    def enforce(store, record, *, insert=False, **kw):
        if point == "old_between":
            # The capture before TT-G3's fix: the step's INSERT committed in a
            # transaction of its own (``save``), then the eviction in another.
            # The process dies between the two.
            if insert:
                store.save(record)
            die()
        out = real_enforce(store, record, insert=insert, **kw)
        if point == "after_commit":
            die()
        return out

    SnapshotStore._insert = insert
    SnapshotStore._delete_ids = delete_ids
    SnapshotStore._trim_bytes = trim
    SnapshotStore.enforce_bounds = enforce

    rec = SnapshotRecorder(db_path=db, **budgets)
    out = rec.capture({"thread_id": "T", "iteration": budgets["max_per_turn"] + 1, "created_at": "one turn",
                       "messages": [{"role": "user", "content": "q"}]})
    print("the capture returned", out)
    os._exit(0)
""")


def _state(it: int) -> dict:
    return {"thread_id": "T", "iteration": it, "created_at": "one turn",
            "messages": [{"role": "user", "content": "q"}, {"role": "tool", "content": f"result {it}"}]}


def _seed(db: Path) -> list[str]:
    """A turn holding its cap of steps; returns their ids in capture order."""
    from kazma_core.time_travel import SnapshotRecorder

    rec = SnapshotRecorder(db_path=str(db), **_BUDGETS)
    try:
        for it in range(1, _CAP + 1):
            assert rec.capture(_state(it)) is not None
        return [s.id for s in rec.list_snapshots("T")]
    finally:
        rec.close()


def _run_child(tmp_path: Path, db: Path, point: str) -> tuple[int, dict, str]:
    script = tmp_path / "child.py"
    script.write_text(_CHILD, encoding="utf-8")
    flight = tmp_path / "flight.json"
    env = dict(os.environ)  # PYTHONPATH -> this worktree's packages
    proc = subprocess.run([sys.executable, str(script), str(db), point, str(flight), json.dumps(_BUDGETS)],
                          env=env, capture_output=True, text=True, timeout=120)
    record = json.loads(flight.read_text(encoding="utf-8")) if flight.exists() else {}
    return proc.returncode, record, proc.stdout + proc.stderr


def _store_after(db: Path) -> tuple[list[str], str]:
    """The ids the store holds for turn T, in capture order, and its
    integrity -- read by a new connection, as the next process would."""
    with closing(sqlite3.connect(str(db))) as conn:
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        ids = [r[0] for r in conn.execute(
            "SELECT id FROM snapshots WHERE thread_id = 'T' AND turn_key = 'one turn' ORDER BY seq")]
    return ids, integrity


def _outcome(before: list[str], flight: str, after: list[str]) -> str:
    """``old`` (nothing of the step stored), ``new`` (the step with its
    eviction: the turn's first step kept, the next oldest evicted), else what
    is wrong."""
    if len(after) > _CAP:
        return f"the turn holds {len(after)} steps, over its cap of {_CAP}"
    if after == before:
        return "old"
    if after == [before[0], *before[2:], flight]:
        return "new"
    return f"neither the old nor the new state: {after}"


@pytest.mark.parametrize("point, expected", [
    ("after_insert", "old"),
    ("mid_evict", "old"),
    ("before_commit", "old"),
    ("after_commit", "new"),
])
def test_a_capture_killed_at_any_point_leaves_the_old_or_the_new_state_within_its_cap(tmp_path, point, expected):
    db = tmp_path / "snapshots.db"
    before = _seed(db)
    assert len(before) == _CAP
    code, flight, output = _run_child(tmp_path, db, point)
    assert code == 17, f"the child did not die at {point} (exit {code}): {output}"
    if point == "after_insert":
        # The kill landed inside the step's write transaction.
        assert flight.get("in_transaction") is True, flight
    after, integrity = _store_after(db)
    assert integrity == "ok"
    assert _outcome(before, flight["id"], after) == expected
    assert (flight["id"] in after) == (expected == "new")
    # The store carries on: the turn's next capture (as a resumed turn takes
    # it) is stored and the turn stays within its cap.
    from kazma_core.time_travel import SnapshotRecorder

    rec = SnapshotRecorder(db_path=str(db), **_BUDGETS)
    try:
        assert rec.capture(_state(_CAP + 2)) is not None
        assert len(rec.list_snapshots("T")) == _CAP
    finally:
        rec.close()


def test_negative_control_the_old_two_transactions_leave_a_turn_over_its_cap(tmp_path):
    """The capture as it was: the step's INSERT committed alone, the process
    killed before its eviction. The turn holds one step over its cap -- what
    the soak saw as 31 of 30 -- and the matrix above holds the fix to it."""
    db = tmp_path / "snapshots.db"
    before = _seed(db)
    code, flight, output = _run_child(tmp_path, db, "old_between")
    assert code == 17, output
    after, integrity = _store_after(db)
    assert integrity == "ok"
    assert len(after) == _CAP + 1 and after == [*before, flight["id"]]
    assert _outcome(before, flight["id"], after).startswith("the turn holds 5 steps, over its cap")


# ── the statements of one capture ───────────────────────────────────────────


def _writes_outside_one_transaction(statements: list[str]) -> list[str]:
    """What is wrong with *statements* as one capture's: every write must sit
    inside one ``BEGIN IMMEDIATE`` ... ``COMMIT``."""
    problems: list[str] = []
    depth = blocks = 0
    for sql in statements:
        head = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
        if head == "BEGIN":
            depth += 1
            blocks += 1
            if "IMMEDIATE" not in sql.upper():
                problems.append(f"a deferred transaction: {sql!r}")
        elif head in ("COMMIT", "END", "ROLLBACK"):
            depth -= 1
        elif head in ("INSERT", "UPDATE", "DELETE", "REPLACE") and depth == 0:
            problems.append(f"a write outside the transaction: {sql[:60]!r}")
    if blocks != 1:
        problems.append(f"{blocks} transactions, not 1")
    return problems


def _traced(store, action) -> list[str]:
    statements: list[str] = []
    store._conn.set_trace_callback(statements.append)
    try:
        action()
    finally:
        store._conn.set_trace_callback(None)
    return statements


def test_a_capture_is_one_write_transaction(tmp_path):
    from kazma_core.time_travel import SnapshotRecorder

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"), **_BUDGETS)
    try:
        store = rec._get_store()  # opened (its schema statements) before the trace
        for it in range(1, _CAP + 3):  # the last two evict a step each
            statements = _traced(store, lambda it=it: rec.capture(_state(it)))
            assert _writes_outside_one_transaction(statements) == [], statements
            assert any(s.lstrip().upper().startswith("INSERT") for s in statements)
        assert len(rec.list_snapshots("T")) == _CAP
    finally:
        rec.close()


def test_negative_control_the_old_sequence_inserts_outside_the_transaction(tmp_path):
    """The old capture's two calls on the same store: the INSERT is traced
    outside the eviction's ``BEGIN IMMEDIATE``, and the check says so."""
    from kazma_core.time_travel import SnapshotRecord, SnapshotRecorder

    rec = SnapshotRecorder(db_path=str(tmp_path / "snapshots.db"), **_BUDGETS)
    try:
        store = rec._get_store()
        record = SnapshotRecord(thread_id="T", iteration=1, state_json="{}", turn_key="one turn")

        def old_capture():
            store.save(record)
            store.enforce_bounds(record, **_EVICTION)

        problems = _writes_outside_one_transaction(_traced(store, old_capture))
        assert problems and problems[0].startswith("a write outside the transaction: 'INSERT"), problems
    finally:
        rec.close()


# ── class gate: nothing else writes a step ──────────────────────────────────


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "--", "kazma-*/kazma_*/*.py"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8") for rel in sorted(set(listed))}


#: SQL is written upper case here; prose ("written into snapshots") is not SQL.
_STEP_INSERT = re.compile(r"\bINTO\s+snapshots\b")
_STORE_CLASS = re.compile(r"\bSnapshotStore\b(?!Error)")


def _enclosing_functions(tree: ast.AST) -> dict[ast.AST, str]:
    """Each node's innermost enclosing function (``ast.walk`` is breadth
    first, so an inner function's walk comes later and wins)."""
    owner: dict[ast.AST, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(fn):
                owner[node] = fn.name
    return owner


def _transaction_try(fn: ast.AST) -> ast.Try | None:
    """The ``try`` of *fn* whose body commits and whose handler rolls back."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try):
            body = "".join(ast.dump(s) for s in node.body)
            handlers = "".join(ast.dump(h) for h in node.handlers)
            if "'COMMIT'" in body and "'ROLLBACK'" in handlers:
                return node
    return None


def _step_write_problems(sources: dict[str, str]) -> list[str]:
    """Where a product module could store a step outside the capture's one
    transaction."""
    problems: list[str] = []
    for rel, source in sources.items():
        tree = ast.parse(source)
        owner = _enclosing_functions(tree)
        for node in ast.walk(tree):
            text = None
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))
            if text and _STEP_INSERT.search(text) and (rel, owner.get(node)) != (_TIME_TRAVEL, "_insert"):
                problems.append(f"{rel}: {owner.get(node) or '<module>'} writes a step itself")
        if rel != _TIME_TRAVEL and _STORE_CLASS.search(source):
            problems.append(f"{rel} holds a SnapshotStore (only time_travel.py may: its writes are bounded there)")
        if rel != _TIME_TRAVEL:
            continue
        functions = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        guarded = _transaction_try(functions["enforce_bounds"]) if "enforce_bounds" in functions else None
        inside = set(ast.walk(guarded)) if guarded is not None else set()
        capture_inserts = False
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            name, where = node.func.attr, owner.get(node)
            if name == "save":
                problems.append(f"{rel}: {where} calls save (a step's INSERT committed alone, no budget kept)")
            if name == "_insert" and where != "save" and node not in inside:
                problems.append(f"{rel}: {where} inserts a step outside enforce_bounds' transaction")
            if name == "enforce_bounds" and where == "capture":
                capture_inserts = any(k.arg == "insert" and isinstance(k.value, ast.Constant) and k.value.value is True
                                      for k in node.keywords)
        if not capture_inserts:
            problems.append(f"{rel}: capture does not insert its step inside enforce_bounds (insert=True)")
    return problems


def test_only_the_bounded_transaction_writes_a_step():
    sources = _product_sources()
    assert _TIME_TRAVEL in sources
    assert _step_write_problems(sources) == []


def test_negative_control_the_old_capture_fails_the_gate():
    """The capture as it was (``save`` then ``enforce_bounds`` without
    ``insert``), a module writing a step with its own SQL, and one holding a
    store: each is named."""
    current = (REPO / _TIME_TRAVEL).read_text(encoding="utf-8")
    new_call = "            evicted = store.enforce_bounds(\n                record,\n                insert=True,\n"
    old_call = "            store.save(record)\n            evicted = store.enforce_bounds(\n                record,\n"
    assert current.count(new_call) == 1
    old = current.replace(new_call, old_call)
    problems = _step_write_problems({_TIME_TRAVEL: old})
    assert any("capture calls save" in p for p in problems), problems
    assert any("capture does not insert its step inside enforce_bounds" in p for p in problems), problems

    sneak = ("from kazma_core.time_travel import SnapshotStore\n"
             "def seed(conn):\n    conn.execute(\"INSERT INTO snapshots (id) VALUES ('x')\")\n")
    problems = _step_write_problems({_TIME_TRAVEL: current, "kazma-ui/kazma_ui/sneak.py": sneak})
    assert problems == ["kazma-ui/kazma_ui/sneak.py: seed writes a step itself",
                        "kazma-ui/kazma_ui/sneak.py holds a SnapshotStore (only time_travel.py may: its "
                        "writes are bounded there)"], problems
