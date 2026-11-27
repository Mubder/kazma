"""A decision and a chat's going away, raced step by step (2026-11-28).

The review of e916a8f96 built this matrix (its scratch probe): an Approve, a
Deny or an expiry against a delete (from a thread and on the loop) or an
archive, in the web route's shape and the chat app's, with and without the
TTL sweep as a third racer -- 36 cases. A cooperative scheduler hands each
racer the turn; every registry call and token step is a yield point, and the
schedules come from a seeded RNG, so every run replays. No sleeps.

Each decider is modelled as the product now does it: it reads
``drive_clock`` before recording, asks ``begin_drive`` and registers its drive
in one step (the chat app too: ``_drive_decision``), and only then awaits.
The rules, every run: no decision is flipped; no call runs after a delete's
cancel; a call that ran is never recorded not run; no gate is left live after
a delete; a refused drive leaves no live gate; only an approval is "cut". On
e916a8f96 the chat app's shape broke them in 11 to 28 runs of 150 per delete
case (review M1) and the TTL sweep in the web shape in 1 to 4 (review L1).

``KAZMA_RACE_RUNS`` sets the runs per case (default 10; the review ran 150).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import os
import random
import threading
import time
from collections import Counter
from typing import Any

import pytest
from kazma_core.safety import hitl_gates
from kazma_core.safety.hitl_gates import TIMEOUT_ACTOR, TransitionConflict
from kazma_ui import chat_runs_end

import tests.test_chat_gone_ends_runs as _gone
from tests.test_chat_gone_ends_runs import _waiting_chat

store = _gone.store

RACER: contextvars.ContextVar[str | None] = contextvars.ContextVar("racer", default=None)
RUNS = int(os.environ.get("KAZMA_RACE_RUNS", "10"))


class Sched:
    def __init__(self, rng: random.Random) -> None:
        self.cv = threading.Condition()
        self.turn: str | None = None
        self.done: set[str] = set()
        self.rng = rng
        self.seq = 0
        self.log: list[str] = []

    def point(self, what: str = "") -> None:
        me = RACER.get()
        if me is None:
            return
        with self.cv:
            self.log.append(f"{me}:{what}")
            self.turn = None
            self.cv.notify_all()
            self.cv.wait_for(lambda: self.turn == me, timeout=20)

    def stamp(self) -> int:
        with self.cv:
            self.seq += 1
            return self.seq

    def run(self, racers: dict[str, Any]) -> None:
        threads = {}
        errors: list[BaseException] = []

        def body(name: str, fn: Any) -> None:
            RACER.set(name)
            with self.cv:
                self.cv.wait_for(lambda: self.turn == name, timeout=20)
            try:
                fn()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)
            finally:
                with self.cv:
                    self.done.add(name)
                    self.turn = None
                    self.cv.notify_all()

        for name, fn in racers.items():
            threads[name] = threading.Thread(target=body, args=(name, fn), daemon=True)
            threads[name].start()
        while len(self.done) < len(racers):
            with self.cv:
                live = sorted(n for n in racers if n not in self.done)
                pick = self.rng.choice(live)
                self.turn = pick
                self.cv.notify_all()
                ok = self.cv.wait_for(lambda: self.turn is None, timeout=20)
                assert ok, ("stuck", self.log[-10:])
        for t in threads.values():
            t.join(5)
        if errors:
            raise errors[0]


@pytest.fixture
def hooks(monkeypatch):
    """Yield points inside the end, and the turn registry modelled."""
    state: dict[str, Any] = {}

    def sched() -> Sched:
        return state["sched"]

    real_live, real_end, real_for = hitl_gates.live_gates, hitl_gates.end_gate, hitl_gates.gate_for
    real_take = chat_runs_end._take
    real_going = chat_runs_end._going_away

    def live_gates(tid):
        sched().point("live_gates")
        return real_live(tid)

    def end_gate(*a, **k):
        sched().point("end_gate")
        return real_end(*a, **k)

    def gate_for(gid):
        sched().point("gate_for")
        return real_for(gid)

    def take(gate_id):
        sched().point("take")
        return real_take(gate_id)

    @contextlib.contextmanager
    def going(tid):
        sched().point("going+")
        with real_going(tid):
            m = state["model"]
            m["going_at"] = sched().stamp()
            yield
        sched().point("going-")

    def cancel_where(tid):
        sched().point("cancel")
        m = state["model"]
        with m["lock"]:
            m["cancel_at"] = sched().stamp()
            if m["registered"]:
                m["cancelled"] = True
                return True
        return False

    async def cancel_and_wait(tid):
        return cancel_where(tid)

    def running(tid):
        m = state["model"]
        with m["lock"]:
            return m["registered"] and not m["finished"]

    monkeypatch.setattr(hitl_gates, "live_gates", live_gates)
    monkeypatch.setattr(hitl_gates, "end_gate", end_gate)
    monkeypatch.setattr(hitl_gates, "gate_for", gate_for)
    monkeypatch.setattr(chat_runs_end, "_take", take)
    monkeypatch.setattr(chat_runs_end, "_going_away", going)
    monkeypatch.setattr(chat_runs_end, "_cancel_where_it_runs", cancel_where)
    monkeypatch.setattr(chat_runs_end, "_cancel_and_wait", cancel_and_wait)
    monkeypatch.setattr(chat_runs_end, "_running", running)
    return state


def _approver(sched: Sched, m: dict, gate_id: str, thread_id: str, decision: str, actor: str, shape: str):
    def run() -> None:
        sched.point("start")
        since = chat_runs_end.drive_clock()      # read before the decision is recorded
        try:
            hitl_gates.claim_gate(gate_id, decision, actor, allow_reclaim=False)
        except TransitionConflict:
            m["claim"] = "conflict"
            return
        m["claim"] = "ok"
        sched.point("claimed")
        try:
            hitl_gates.mark_resuming(gate_id)
        except TransitionConflict:
            m["resume"] = "conflict"
            return
        sched.point("resuming")       # the scope grants (web), the gate's real id: off the loop
        # begin_drive and the drive's registration, one step on every path (the
        # web route: create_task + register_turn; the chat app's _drive_decision:
        # register_turn of its own task) -- then the chat app's awaits.
        with m["lock"]:
            ok = chat_runs_end.begin_drive(thread_id, gate_id, since=since)
            if ok:
                m["registered"] = True
        if shape == "late-register":  # e916a8f96's chat app: the awaits came BEFORE the register
            with m["lock"]:
                m["registered"] = False
            if ok:
                sched.point("aget_state")
                sched.point("resolve_session")
                with m["lock"]:
                    m["registered"] = True
        elif ok and shape == "chatapp":
            sched.point("aget_state")
            sched.point("resolve_session")
        m["began"] = ok
        if not ok:
            return
        sched.point("registered")
        with m["lock"]:
            if m["cancelled"]:
                return
            m["ran_at"] = sched.stamp()
            m["ran"] = True
        sched.point("ran")
        with m["lock"]:
            m["finished"] = True
        row = hitl_gates.gate_for(gate_id)
        if row is not None and row.is_live:
            with contextlib.suppress(TransitionConflict):
                hitl_gates.settle_gate(gate_id)
    return run


def _ender(sched: Sched, chat: Any, way: str):
    def run() -> None:
        sched.point("start")
        if way == "delete":
            chat_runs_end.chat_went_away(chat.thread_id, reason="chat_deleted")
        elif way == "delete_loop":
            asyncio.run(chat_runs_end.end_chat_runs(chat.thread_id, reason="chat_deleted"))
        else:
            asyncio.run(chat_runs_end.end_chat_runs(chat.thread_id, reason="chat_archived",
                                                    session_id=chat.session_id, waiting_only=True))
    return run


def _expirer(sched: Sched):
    def run() -> None:
        sched.point("start")
        hitl_gates.expire_due_gates(time.time() + 10 ** 7)
    return run


CASES = [(way, shape, dec, exp)
         for way in ("delete", "delete_loop", "archive")
         for shape in ("web", "chatapp")
         for dec in ("approve", "deny", "timeout")
         for exp in (False, True)]


def _matrix(store: Any, hooks: dict, way: str, shape: str, decision: str, expirer: bool,
            runs: int) -> tuple[Counter, Counter, dict[str, list[str]]]:
    """(violations, fates, an example schedule per violation) over *runs*."""
    violations: Counter = Counter()
    fates: Counter = Counter()
    examples: dict[str, list[str]] = {}
    for i in range(runs):
        rng = random.Random(f"{way}-{shape}-{decision}-{expirer}-{i}")
        sched = Sched(rng)
        chat = _waiting_chat(store)
        # an expiry date to be past
        conn = hitl_gates._connect()
        conn.execute("UPDATE hitl_gates SET expires_at = ? WHERE gate_id = ?", (time.time() + 600, chat.gate_id))
        conn.commit()
        conn.close()
        m = {"lock": threading.Lock(), "registered": False, "cancelled": False, "ran": False, "finished": False,
             "claim": "", "resume": "", "began": None, "ran_at": None, "cancel_at": None, "going_at": None}
        hooks["sched"], hooks["model"] = sched, m
        dec, actor = ("deny", TIMEOUT_ACTOR) if decision == "timeout" else (decision, "owner")
        racers = {"A": _approver(sched, m, chat.gate_id, chat.thread_id, dec, actor, shape),
                  "E": _ender(sched, chat, way)}
        if expirer:
            racers["X"] = _expirer(sched)
        sched.run(racers)
        row = hitl_gates.gate_for(chat.gate_id)   # main thread: no racer, no park
        fate = hitl_gates.ended_fate(row.state, row.decision, row.actor, row.ended_reason)
        fates[f"{row.state}/{row.decision}/{fate or '-'}"] += 1
        bad = []
        if m["claim"] == "ok" and row.decision != dec:
            bad.append("decision flipped")
        if m["ran"] and dec != "approve":
            pass  # a deny's resume "running" is the graph going on, modelled the same
        if m["ran"] and way != "archive" and m["cancel_at"] is not None and m["ran_at"] > m["cancel_at"]:
            bad.append("call ran after the delete's cancel (chat gone)")
        if m["ran"] and fate in ("not_run", "not_needed"):
            bad.append(f"call ran but recorded {fate}")
        if way != "archive" and row.is_live:
            bad.append(f"gate left live after delete ({row.state})")
        if m["began"] is False and row.is_live:
            bad.append(f"drive refused, gate left {row.state}")
        if fate == "cut" and dec != "approve":
            bad.append("cut on a non-approval")
        for b in bad:
            violations[b] += 1
            examples.setdefault(b, sched.log[-40:])
    return violations, fates, examples


@pytest.mark.parametrize(("way", "shape", "decision", "expirer"), CASES)
def test_race(store, hooks, way, shape, decision, expirer) -> None:
    violations, fates, examples = _matrix(store, hooks, way, shape, decision, expirer, RUNS)
    print(f"\n[{way}/{shape}/{decision}/exp={expirer}] runs={RUNS} fates={dict(fates)} violations={dict(violations)}")
    for b, log in examples.items():
        print("  example", b, "::", " ".join(log))
    assert not violations, (dict(violations), {b: " ".join(log) for b, log in examples.items()})


def test_control_a_chat_app_that_registers_after_its_awaits_is_caught(store, hooks) -> None:
    """Control: the chat app's drive as e916a8f96 had it -- its turn
    registered only after the checkpoint read and the hops -- breaks the
    rules under the same seeded schedules (the review's M1)."""
    violations, _, _ = _matrix(store, hooks, "delete", "late-register", "approve", False, 40)
    assert violations["call ran after the delete's cancel (chat gone)"] > 0, dict(violations)
