#!/usr/bin/env python3
"""Soak of Time Travel: long turns, many chats, restores, forgets, faults and restarts.

Qualifies tracker row TT-G3 (docs/plans/TIME_TRAVEL_INDUSTRIAL_HARDENING_GOAL.md):
a mission-length soak stays within the declared disk and RAM budgets, and
every injected fault ends with an honest error. This is the harness for the
run of hours or days, not the result. It runs the product's own Time Travel
code on PRIVATE stores (a temporary data directory unless ``--data-dir`` names
one; never an install's, never a database server, never the network), one
cycle per child process -- every cycle is a restart on the same stores.

A cycle (the child):

* starts as Kazma does: a recorder from ``create_recorder`` with the shipped
  budgets (``kazma.yaml``'s ``time_travel`` block, AGENTS §12 C), registered
  as the process recorder; the product's SQLite checkpoint saver; a real
  LangGraph graph on the product's ``SupervisorState`` with the product's node
  names, routing and capture hook (``time_travel.capture_after_supervisor``),
  a tool worker that pauses at ``interrupt()`` for an approval;
* checks that each chat's checkpoint is what the last completed cycle left
  (across the restart), and continues a mission turn a kill cut short;
* runs turns on a pool of chats (approved, denied, some left waiting at an
  approval), with their transcript in the chat store as the web keeps it;
* captures, through the same hook, a mission-length turn (``--mission-iterations``,
  2,000 by default) and chats with large states (past the 2 MiB per-step
  ceiling: stored cut and marked) -- without LangGraph, as graph_builder's
  supervisor node calls the hook: run through LangGraph, a 2,000-step turn
  would checkpoint every superstep, which measures the checkpoint store (§41),
  not Time Travel -- and "aged" chats captured with the module's clock set
  back past the retention, so the prune has rows to take;
* runs the user operations through the real services: list (with the web
  chat's ``/replay``), detail, compare, restore and fork
  (``kazma_ui.thread_rewind.rewind_thread`` / ``fork_thread``), forget of a
  turn and of a chat (``forget_turn_snapshots`` / ``forget_thread_snapshots``),
  ``/replay clear`` (the web chat's ``time_travel_reply``), chat delete (a
  chat that is cleared, forgotten or has taken ``--chat-max-turns`` turns is
  deleted, and a restored or forked chat not in use -- a mission's, a fork of
  one -- is deleted the cycle after, once the restart check has read it, so
  the run's own checkpoints stay bounded over days);
* injects faults: the database write-locked by another connection for a
  while (``locked``), every statement failing (``io_error``), the file not a
  database (``unreadable``), a restore while a turn runs
  (``restore_running``), a restore of a cut step (``restore_cut``), a restore
  while an approval waits (``restore_pending``), and a kill mid-capture: the
  process exits right after a step's insert (``after_insert``), inside the
  eviction transaction (``mid_evict``), or the parent kills it at a seeded
  moment of the mission (``hard``; the moment lands where it lands);
* runs maintenance on its cadence (``maintain_snapshots``: the prune, and
  VACUUM only past its threshold) every ``--maintain-every`` cycles (the
  product's cadence is daily).

The first five cycles run every operation, every fault and each kill once,
so even a short run covers them; later cycles draw them by their rates. The
plan of every cycle is a function of ``--seed``.

Checked after every cycle, from the stores: ``PRAGMA integrity_check``; every
step's bytes as recorded and within the per-step ceiling, cut steps marked and
only cut steps; rows within every cap (per turn, per chat, install total) and
bytes within every budget (per chat, total) -- after a kill, the one step in
flight may stand over until the next cycle continues its turn (the insert and
the eviction are two transactions); the database file within its bound (the
budgets plus page overhead: SQLite reuses freed pages, so the file grows only
to the peak live size) and the WAL within its own (VACUUM writes the whole
database through it); after maintenance, no step older than the retention,
the free pages below the VACUUM threshold (else a VACUUM was due and did not
run), and no WAL left by a VACUUM that answered ``ok`` (maintenance truncates
it, so the reclaim it reports is the disk's: this soak's first runs found the
WAL kept the rewritten file's size); each cycle child's peak RSS under ``--rss-ceiling-mib`` and its
growth over its own start under ``--rss-growth-mib`` (each child is a fresh
process, so a leak shows as growth within a cycle); a restored or forked
chat's state equal to its step's (messages, content and count, the step's
unanswered tool call dropped, nothing pending, nothing runnable, the
scratchpad replaced), the transcript cut to it, the steps after a restore
point gone; a cleared, forgotten or deleted chat's steps never back, and no
deleted step back by its id; every injected fault ended with the honest error
it should (a failed capture returns no step and is counted; a read raises
``SnapshotStoreError``, never "no steps saved"; a restore is refused with the
right code and changes nothing). The run stops at the first violation, and
FAILS (exit 1).

The report (``--report``, JSON) is rewritten whole after every cycle
(``"status": "running"``), then once with ``passed`` or ``failed``: the
declared budgets beside the observed peaks (rows, bytes, file and WAL size,
RSS), captures by result, operations by kind with their outcomes and latency
summaries, each fault kind with how each one ended, maintenance runs and
VACUUMs (each one's file and WAL sizes before and after, the recorder's
connection still open as on a server, and what it reported reclaimed), kills,
cycles and wall time, and the
peak size of the run's other stores (checkpoints, transcripts, memory: the
harness's footprint, not a Time Travel budget). The parent's own record of the run
(chats cleared, steps deleted, the checkpoint digests to check next) is
``<data dir>/soak/state.json``.

Usage:
  python scripts/time_travel_soak.py --duration 259200 --report tt-soak.json
  python scripts/time_travel_soak.py --duration 300 --cycle-seconds 2 --report tt-soak.json
  python scripts/time_travel_soak.py --duration 120 --max-cycles 5 --mission-iterations 300
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import time
import zlib
from collections import Counter, OrderedDict
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# The shared report helpers sit beside this file; a runner that does not put
# the script's folder on the path (runpy, a priority wrapper) still finds them.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from soak_report import finished, latency_summary, process_tree_sample, utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
for _pkg in ("kazma-tui", "kazma-skills", "kazma-cli", "kazma-gateway", "kazma-ui", "kazma-core"):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

_KILLED = 137
_MiB = 1 << 20
#: VACUUM runs only when a quarter of the file and 8 MiB are free (AGENTS §12 C).
_VACUUM_FREE_RATIO = 0.25
_VACUUM_MIN_FREE_BYTES = 8 * _MiB
#: Deleted step ids remembered, newest last: a step that comes back is caught
#: within this many deletions (a soak of days deletes millions).
_GONE_WINDOW = 50_000
#: The key a cut step carries (``time_travel.TRUNCATION_MARK``).
_MARK = "_time_travel_truncated"

_OP_KINDS = ("list", "detail", "compare", "restore", "fork", "forget_turn", "forget_chat", "replay_clear")
_OP_WEIGHTS = (3, 3, 2, 3, 1, 1, 0.5, 0.5)
_FAULT_KINDS = ("locked", "io_error", "unreadable", "restore_running", "restore_cut", "restore_pending")
_KILL_MODES = ("after_insert", "mid_evict", "hard")

#: The first cycles: every operation, every fault and each kill once.
_COVERAGE: tuple[dict[str, Any], ...] = (
    {"ops": ["list", "detail", "compare", "restore", "fork"],
     "faults": ["locked", "restore_running", "restore_pending"],
     "mission": True, "large": True, "aged": True, "maintain": True},
    {"ops": ["forget_turn", "replay_clear:biggest", "forget_chat:biggest", "restore", "detail", "list"],
     "faults": ["io_error", "unreadable", "restore_cut"], "large": True, "maintain": True},
    {"ops": ["list", "fork", "restore"], "faults": [], "mission": True, "kill": "after_insert"},
    {"ops": ["restore", "compare"], "faults": [], "mission": True, "kill": "hard"},
    {"ops": ["detail", "forget_chat"], "faults": [], "mission": True, "kill": "mid_evict", "maintain": True},
)


def _env(data_dir: Path) -> dict[str, str]:
    """The child's environment: no install's settings, only private stores."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("KAZMA_", "PYTEST_")) and k not in ("DATABASE_URL",)}
    env.update({
        "KAZMA_DATA_DIR": str(data_dir),
        "KAZMA_MEMORY_STATE_DB": str(data_dir / "memory_state.db"),
        "KAZMA_MEMORY_OPS_DB": str(data_dir / "memory_ops.db"),
        "KAZMA_BACKUPS_DIR": str(data_dir / "backups"),
        "KAZMA_LOG_FILE": str(data_dir / "soak" / "kazma.log"),
        # Nothing of the user's: the home folders a Kazma process reads.
        "KAZMA_USER_HOME": str(data_dir / "home"),
        "KAZMA_SKILLS_HOME": str(data_dir / "home"),
        # Never a database server: a soak writes thousands of rows.
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_PROCESS_PRIORITY": "keep",
        "PYTHONIOENCODING": "utf-8",
    })
    return env


# ── shared: what a restored chat must hold ─────────────────────────────────


def _role(m: Any) -> str:
    return str(m.get("role") or "") if isinstance(m, dict) else ""


def _text(m: Any) -> str:
    content = m.get("content") if isinstance(m, dict) else None
    return content if isinstance(content, str) else ""


def _digest(messages: Any) -> str:
    return hashlib.sha256(json.dumps(messages or [], sort_keys=True, default=str).encode()).hexdigest()[:24]


def _expected_after_restore(messages: list[Any]) -> list[Any]:
    """The messages a restore to a step writes (AGENTS §12 D: a turn-boundary
    state, tool chains sanitized): the step's messages, without an assistant
    tool call that has no result after it (a step is captured right after the
    supervisor decided one) and without a tool result whose call is gone.
    Written here from the rule, not taken from the product's code."""
    results: dict[str, list[int]] = {}
    for i, m in enumerate(messages):
        if _role(m) == "tool":
            results.setdefault(str(m.get("tool_call_id") or ""), []).append(i)
    out: list[Any] = []
    valid: set[str] = set()
    for i, m in enumerate(messages):
        if not isinstance(m, dict):
            out.append(m)
            continue
        if _role(m) == "assistant" and m.get("tool_calls"):
            kept = [c for c in m["tool_calls"]
                    if any(j > i for j in results.get(str(c.get("id") or ""), []))]
            if kept:
                valid.update(str(c.get("id") or "") for c in kept)
                out.append(m if len(kept) == len(m["tool_calls"]) else {**m, "tool_calls": kept})
            elif _text(m).strip():
                out.append({k: v for k, v in m.items() if k != "tool_calls"})
            continue
        if _role(m) == "tool" and str(m.get("tool_call_id") or "") not in valid:
            continue
        out.append(m)
    return out


def _projected(messages: list[Any]) -> list[dict[str, str]]:
    """A fork's chat row: the questions and the answers with text (TT-07)."""
    return [{"role": _role(m), "content": _text(m).strip()} for m in messages
            if _role(m) in ("user", "assistant") and _text(m).strip()]


def _restore_problems(step_state: dict[str, Any], values: dict[str, Any], next_nodes: tuple[Any, ...]) -> list[str]:
    """What is wrong with a chat restored (or forked) from *step_state*: its
    checkpoint *values* and its next nodes. Empty when it holds the step."""
    from kazma_core.agent.state import SCRATCHPAD_CLEAR

    problems: list[str] = []
    want = _expected_after_restore(list(step_state.get("messages") or []))
    got = list(values.get("messages") or [])
    if _digest(got) != _digest(want):
        problems.append(f"the chat holds {len(got)} messages and the step {len(want)}"
                        + (" (their content differs)" if len(got) == len(want) else ""))
    if next_nodes:
        problems.append(f"left runnable (next {list(next_nodes)})")
    if values.get("tool_calls_pending"):
        problems.append("left a tool call pending")
    want_pad = {k: v for k, v in (step_state.get("scratchpad") or {}).items() if k != SCRATCHPAD_CLEAR}
    got_pad = {k: v for k, v in (values.get("scratchpad") or {}).items() if k != SCRATCHPAD_CLEAR}
    if want_pad != got_pad:
        problems.append(f"scratchpad {sorted(got_pad)} is not the step's {sorted(want_pad)}")
    return problems


def _filler(it: int, size: int) -> str:
    unit = f"out{it} "
    return (unit * (size // len(unit) + 1))[:size]


# ── the child: one cycle ─────────────────────────────────────────────────


def _cfg(thread_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}


def _build_graph(recorder: Any, checkpointer: Any, holds: dict[str, dict[str, Any]]) -> Any:
    """A scripted supervisor on the product's graph shape (the state schema
    with the scratchpad's merge reducer, the node names and routing, the
    capture hook, an ``interrupt()`` approval). A goal ``tool:target`` calls
    ``tool`` behind an approval, then answers; anything else answers at once.
    A thread in *holds* waits in its first supervisor step until released."""
    import uuid

    from kazma_core.agent.state import NodeName, SupervisorState
    from kazma_core.time_travel import capture_after_supervisor
    from langgraph.graph import END, StateGraph
    from langgraph.types import interrupt

    async def supervisor(state: dict[str, Any]) -> dict[str, Any]:
        hold = holds.get(str(state.get("thread_id") or ""))
        if hold is not None and not state.get("tool_calls_done"):
            hold["entered"].set()
            await hold["release"].wait()
        it = int(state.get("iteration") or 0)
        goal = str(state.get("active_goal") or "")
        messages = list(state.get("messages") or [])
        tool, sep, target = goal.partition(":")
        if state.get("tool_calls_done") or not sep:
            messages.append({"role": "assistant", "content": f"Answer to {goal}"})
            result: dict[str, Any] = {"iteration": it + 1, "next_node": NodeName.RESPOND,
                                      "tool_calls_pending": [], "messages": messages}
        else:
            call_id = f"call-{uuid.uuid4().hex[:8]}"
            messages.append({"role": "assistant", "content": "", "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": tool, "arguments": json.dumps({"target": target})},
            }]})
            result = {"iteration": it + 1, "next_node": NodeName.TOOL_WORKER, "messages": messages,
                      "tool_calls_pending": [{"id": call_id, "name": tool, "arguments": {"target": target}}],
                      "tool_calls_done": [], "scratchpad": {f"note {target}": target}}
        return await capture_after_supervisor(recorder, state, result)

    def tool_worker(state: dict[str, Any]) -> dict[str, Any]:
        pending = list(state.get("tool_calls_pending") or [])
        decision = interrupt({
            "type": "hitl_approval", "kind": "security",
            "tool": pending[0]["name"] if pending else "",
            "args": pending[0]["arguments"] if pending else {},
            "tools": [p["name"] for p in pending], "message": "approve?",
        })
        ok = isinstance(decision, dict) and bool(decision.get("approved"))
        messages = list(state.get("messages") or [])
        done = []
        for p in pending:
            content = "ran" if ok else "denied"
            messages.append({"role": "tool", "tool_call_id": p["id"], "content": content})
            done.append({"tool_call_id": p["id"], "name": p["name"], "content": content})
        return {"tool_calls_done": done, "tool_calls_pending": [], "messages": messages,
                "next_node": NodeName.SUPERVISOR}

    def respond(state: dict[str, Any]) -> dict[str, Any]:
        return {"next_node": "end", "messages": list(state.get("messages") or [])}

    def route(state: dict[str, Any]) -> str:
        nxt = state.get("next_node")
        if nxt in (NodeName.TOOL_WORKER, NodeName.SUPERVISOR):
            return str(nxt)
        return NodeName.RESPOND

    g = StateGraph(SupervisorState)
    g.add_node(NodeName.SUPERVISOR, supervisor)
    g.add_node(NodeName.TOOL_WORKER, tool_worker)
    g.add_node(NodeName.RESPOND, respond)
    g.set_entry_point(NodeName.SUPERVISOR)
    g.add_conditional_edges(NodeName.SUPERVISOR, route, {
        NodeName.SUPERVISOR: NodeName.SUPERVISOR,
        NodeName.TOOL_WORKER: NodeName.TOOL_WORKER,
        NodeName.RESPOND: NodeName.RESPOND,
    })
    g.add_edge(NodeName.TOOL_WORKER, NodeName.SUPERVISOR)
    g.add_edge(NodeName.RESPOND, END)
    return g.compile(checkpointer=checkpointer)


async def _checkpoint_digests(saver: Any, threads: list[str]) -> dict[str, str]:
    """Each thread's latest checkpointed messages, digested (what the next
    cycle, a new process on the same file, must read back)."""
    out = {}
    for thread in threads:
        tup = await saver.aget_tuple(_cfg(thread))
        values = (tup.checkpoint.get("channel_values") or {}) if tup is not None else {}
        out[thread] = _digest(values.get("messages") or [])
    return out


class _FailingConnection:
    """The snapshot store's connection while the disk fails: every statement
    raises, as a disk I/O error does."""

    def __init__(self, real: sqlite3.Connection) -> None:
        self._real = real

    def execute(self, *_a: Any, **_k: Any) -> Any:
        raise sqlite3.OperationalError("disk I/O error (soak fault)")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._real, name)


def _hold_write_lock(db: str) -> sqlite3.Connection:
    """Another connection holding the database's write lock (``BEGIN IMMEDIATE``)."""
    conn = sqlite3.connect(db, isolation_level=None, timeout=5, check_same_thread=False)
    conn.execute("BEGIN IMMEDIATE")
    return conn


def _release_write_lock(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    finally:
        conn.close()


_ASIDE = ".soak-aside"


def _set_aside(db: Path) -> list[str]:
    """Move the store's files aside and put a file that is not a database in
    its place. Returns the suffixes moved."""
    moved = []
    for suffix in ("", "-wal", "-shm"):
        path = Path(str(db) + suffix)
        if path.exists():
            for attempt in range(20):
                try:
                    os.replace(path, str(path) + _ASIDE)
                    break
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.1)
            moved.append(suffix)
    db.write_bytes(b"this is not a sqlite database " * 200)
    return moved


def _put_back(db: Path) -> bool:
    """Undo :func:`_set_aside` (also the parent's recovery). True when the
    files were aside."""
    if not Path(str(db) + _ASIDE).exists():
        return False
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    for suffix in ("", "-wal", "-shm"):
        aside = Path(str(db) + suffix + _ASIDE)
        if aside.exists():
            os.replace(aside, str(db) + suffix)
    return True


def _pages(db: Path) -> dict[str, int]:
    """Page size and counts of *db*, and the file sizes (read only)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        size = int(conn.execute("PRAGMA page_size").fetchone()[0])
        count = int(conn.execute("PRAGMA page_count").fetchone()[0])
        free = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
    finally:
        conn.close()
    wal = Path(str(db) + "-wal")
    return {"page_size": size, "page_count": count, "freelist": free,
            "db_bytes": db.stat().st_size if db.exists() else 0,
            "wal_bytes": wal.stat().st_size if wal.exists() else 0}


def _rss() -> tuple[float, float]:
    """``(resident, peak resident)`` of this process, in MiB."""
    import psutil

    info = psutil.Process().memory_info()
    peak = getattr(info, "peak_wset", 0)
    if not peak:
        import resource  # POSIX

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = maxrss if sys.platform == "darwin" else maxrss * 1024
    return round(info.rss / _MiB, 1), round(max(peak, info.rss) / _MiB, 1)


@contextmanager
def _clock_set_back(days: float):
    """Time Travel's clock *days* in the past: steps captured meanwhile carry
    a timestamp older than the retention (the prune's work)."""
    from kazma_core import time_travel

    real = time_travel.datetime
    back = timedelta(days=days)

    class _Earlier(real):  # type: ignore[misc, valid-type]
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return real.now(tz) - back

    time_travel.datetime = _Earlier
    try:
        yield
    finally:
        time_travel.datetime = real


class _Cycle:
    """One cycle: the phases of the module docstring, in order."""

    def __init__(self, plan: dict[str, Any]) -> None:
        self.plan = plan
        self.knobs = plan["knobs"]
        self.rng = random.Random(plan["seed"])
        self.result_file = Path(plan["result_file"])
        self.pool: dict[str, dict[str, Any]] = {c["id"]: dict(c) for c in plan["pool"]}
        self.retired: list[str] = []
        self.holds: dict[str, dict[str, Any]] = {}
        self.touched: set[str] = set()
        # A chat a restore or a fork wrote that is not one of the pool's (a
        # mission's, or a fork of one): checked across the next restart, then
        # deleted, so the checkpoint store does not keep megabyte states of
        # chats nobody uses again.
        self.transients: set[str] = set()
        self.counts: Counter[str] = Counter()
        self.capture_ms: list[float] = []
        self.serial = 0
        self.out: dict[str, Any] = {
            "cycle": plan["cycle"], "done": False, "phase": "boot", "ops": {}, "faults": {},
            "violations": [], "captures": {}, "capture_ms": [], "created": 0, "forked": 0, "deleted": 0,
            "large": 0, "aged": 0, "resumed": False, "maintenance": None, "mission": None,
        }

    # ── bookkeeping ──

    def save(self) -> None:
        self.out["captures"] = dict(self.counts)
        self.out["capture_ms"] = list(self.capture_ms)
        self.out["pool"] = sorted(self.pool.values(), key=lambda c: c["id"])
        self.out["retired"] = list(self.retired)
        self.out["transients"] = sorted(self.transients - set(self.retired))
        self.out["rss_mib"], self.out["rss_peak_mib"] = _rss()
        tmp = self.result_file.with_name(self.result_file.name + ".tmp")
        tmp.write_text(json.dumps(self.out, ensure_ascii=False, default=str), encoding="utf-8")
        os.replace(tmp, self.result_file)

    def violation(self, text: str) -> None:
        self.out["violations"].append(text)

    def op(self, kind: str, ms: float, outcome: str = "ok") -> None:
        entry = self.out["ops"].setdefault(kind, {"n": 0, "outcomes": {}, "ms": []})
        entry["n"] += 1
        entry["outcomes"][outcome] = entry["outcomes"].get(outcome, 0) + 1
        entry["ms"].append(round(ms, 3))

    def fault(self, kind: str, problems: list[str], ending: str) -> None:
        entry = self.out["faults"].setdefault(kind, {"injected": 0, "endings": {}})
        entry["injected"] += 1
        ended = ending if not problems else "VIOLATION"
        entry["endings"][ended] = entry["endings"].get(ended, 0) + 1
        for p in problems:
            self.violation(f"fault {kind}: {p}")

    def token(self) -> str:
        return "zq" + "".join(self.rng.choice("bcdfghjklmnpqrstvwx") for _ in range(8))

    def new_id(self, tag: str) -> str:
        self.serial += 1
        return f"soak-{tag}{self.plan['cycle']}-{self.serial}"

    # ── boot ──

    async def boot(self) -> None:
        import yaml
        from kazma_core import metrics, time_travel
        from kazma_core.checkpoints_shared import get_shared_sqlite_saver
        from kazma_core.paths import checkpoints_db
        from kazma_ui.session_manager import get_session_manager

        shipped = yaml.safe_load((_ROOT / "kazma.yaml").read_text(encoding="utf-8")) or {}
        self.rec = time_travel.create_recorder(config={"time_travel": shipped.get("time_travel") or {}})
        time_travel.set_process_recorder(self.rec)
        self.out["limits"] = self.rec.limits
        self.out["db_path"] = self.rec.db_path
        # Every capture result the product counts (kazma_time_travel_capture_total),
        # and each capture's time.
        real_metric = metrics.record_time_travel_capture

        def counted(result: str) -> None:
            self.counts[result] += 1
            real_metric(result)

        metrics.record_time_travel_capture = counted
        real_capture = self.rec.capture

        def timed(state: dict[str, Any], **kw: Any) -> Any:
            t0 = time.perf_counter()
            try:
                return real_capture(state, **kw)
            finally:
                self.capture_ms.append(round((time.perf_counter() - t0) * 1000, 3))

        self.rec.capture = timed  # type: ignore[method-assign]
        self.saver = await get_shared_sqlite_saver(checkpoints_db())
        self.graph = _build_graph(self.rec, self.saver, self.holds)
        self.store = get_session_manager()
        self.out["rss_baseline_mib"] = _rss()[0]
        self.save()

    async def verify(self) -> None:
        """The checkpoints the last completed cycle left, read back by this
        new process (a restored or forked chat survives the restart)."""
        expected = self.plan.get("verify") or {}
        if not expected:
            return
        got = await _checkpoint_digests(self.saver, sorted(expected))
        for thread, digest in sorted(expected.items()):
            if got.get(thread) != digest:
                self.violation(f"restart: the checkpoint of {thread} is not what the last cycle left")
        self.out["verified"] = len(expected)

    async def drop_transients(self) -> None:
        """Delete the last cycle's transient chats, now that the restart
        check has read them back."""
        for thread in self.plan.get("transients") or []:
            if thread not in self.pool:
                await self.delete_chat(thread)

    async def close(self) -> None:
        from kazma_core.checkpoints_shared import close_shared_checkpoints
        from kazma_core.time_travel import set_process_recorder

        await asyncio.to_thread(self.rec.close)
        set_process_recorder(None)
        await close_shared_checkpoints()
        await asyncio.to_thread(self.store.close)

    # ── chats on the graph ──

    async def values(self, thread: str) -> tuple[dict[str, Any], tuple[Any, ...]]:
        snap = await self.graph.aget_state(_cfg(thread))
        if snap is None:
            return {}, ()
        return dict(snap.values or {}), tuple(snap.next or ())

    async def paused(self, thread: str) -> tuple[str, dict[str, Any]] | None:
        snap = await self.graph.aget_state(_cfg(thread))
        for task in getattr(snap, "tasks", None) or ():
            for intr in getattr(task, "interrupts", None) or ():
                value = getattr(intr, "value", None)
                if isinstance(value, dict) and value.get("type") == "hitl_approval":
                    return str(intr.id), value
        return None

    def _transcript_add(self, thread: str, rows: list[tuple[str, str]]) -> None:
        from kazma_ui.session_manager import ChatSession

        if self.store.get(thread) is None:
            self.store.put(ChatSession(session_id=thread, thread_id=thread, title=f"Soak {thread}", messages=[]))
        with self.store.transact(thread) as sess:
            sess.messages = list(sess.messages or []) + [{"role": r, "content": c} for r, c in rows]

    async def fresh_chat(self, tag: str) -> str:
        thread = self.new_id(tag)
        await asyncio.to_thread(self._transcript_add, thread, [])
        self.pool[thread] = {"id": thread, "kind": "graph", "turns": 0}
        self.out["created"] += 1
        return thread

    async def turn(self, thread: str, goal: str) -> dict[str, Any]:
        """One turn as a transport sends it: a fresh state carrying the
        checkpoint's messages and the question, until it ends or pauses."""
        from kazma_core.agent.state import initial_supervisor_state

        values, _ = await self.values(thread)
        state = dict(initial_supervisor_state(thread_id=thread, max_iterations=15))
        state.pop("scratchpad", None)  # the transports never send it (merge reducer, §29A)
        state["messages"] = [*list(values.get("messages") or []), {"role": "user", "content": goal}]
        state["active_goal"] = goal
        final = await self.graph.ainvoke(state, _cfg(thread))
        await asyncio.to_thread(self._transcript_add, thread, [("user", goal)])
        if thread in self.pool:
            self.pool[thread]["turns"] = int(self.pool[thread].get("turns") or 0) + 1
        if await self.paused(thread) is None:
            await self._answer_to_transcript(thread, final)
        return dict(final or {})

    async def _answer_to_transcript(self, thread: str, final: Any) -> None:
        messages = list((final or {}).get("messages") or [])
        if messages and _role(messages[-1]) == "assistant" and _text(messages[-1]):
            await asyncio.to_thread(self._transcript_add, thread, [("assistant", _text(messages[-1]))])

    async def decide(self, thread: str, approved: bool) -> dict[str, Any]:
        from kazma_core.safety.commitment.resume import build_resume_command

        live = await self.paused(thread)
        if live is None:
            return {}
        iid, payload = live
        final = await self.graph.ainvoke(build_resume_command(payload, approved=approved, interrupt_id=iid),
                                         _cfg(thread))
        await self._answer_to_transcript(thread, final)
        return dict(final or {})

    async def delete_chat(self, thread: str) -> None:
        """A chat delete: its steps (every key), its checkpoint, its transcript."""
        from kazma_core.time_travel import forget_thread_snapshots
        from kazma_ui.thread_locks import thread_mutation
        from kazma_ui.thread_rewind import chat_keys_for

        t0 = time.perf_counter()
        keys = await asyncio.to_thread(chat_keys_for, thread)
        await asyncio.to_thread(forget_thread_snapshots, keys, recorder=self.rec)
        async with thread_mutation(thread):
            await self.saver.adelete_thread(thread)
        await asyncio.to_thread(self.store.delete, thread)
        self.op("chat_delete", (time.perf_counter() - t0) * 1000)
        left = await asyncio.to_thread(self.rec.list_snapshots, thread)
        values, _ = await self.values(thread)
        if left or values.get("messages"):
            self.violation(f"chat delete of {thread} left {len(left)} steps or its checkpoint")
        self.pool.pop(thread, None)
        self.retired.append(thread)
        self.out["deleted"] += 1

    async def graph_turns(self) -> None:
        """Answer what waits, run turns, start chats, retire long ones."""
        for thread in sorted(self.pool):
            if await self.paused(thread) is not None:
                await self.decide(thread, approved=self.rng.random() < 0.7)
                continue
            if self.rng.random() >= self.knobs["turn_rate"]:
                continue
            await self._one_turn(thread)
        for thread in self.plan["new_chats"]:
            await asyncio.to_thread(self._transcript_add, thread, [])
            self.pool[thread] = {"id": thread, "kind": "graph", "turns": 0}
            self.out["created"] += 1
            await self._one_turn(thread)
        for thread in sorted(self.pool):
            if (int(self.pool[thread].get("turns") or 0) >= self.knobs["chat_max_turns"]
                    and await self.paused(thread) is None):
                await self.delete_chat(thread)

    async def _one_turn(self, thread: str) -> None:
        tok = self.token()
        if self.rng.random() < 0.55:
            await self.turn(thread, f"read:{tok}")
            if self.rng.random() >= self.knobs["pending_rate"]:
                await self.decide(thread, approved=self.rng.random() < 0.7)
        else:
            await self.turn(thread, f"hello {tok}")

    # ── chats captured through the hook alone ──

    async def direct_turn(self, thread: str, *, turn_key: str, question: str, iterations: int, chunk: int,
                          start: int = 1, big: int = 0) -> list[str]:
        """A turn of *iterations* supervisor steps captured through the
        product hook as graph_builder's supervisor node calls it: each step
        decides a tool call (captured with it unanswered), the result
        follows; the last step answers. ``big`` makes the first tool result
        that large. Returns the ids of the steps it stored."""
        from kazma_core.time_travel import capture_after_supervisor

        stored = []
        messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
        for it in range(1, iterations + 1):
            last = it == iterations
            call = {"id": f"{thread}-c{it}", "type": "function",
                    "function": {"name": "shell_exec", "arguments": "{}"}}
            if last:
                step = [*messages, {"role": "assistant", "content": f"Answer to {question}"}]
            else:
                step = [*messages, {"role": "assistant", "content": "", "tool_calls": [call]}]
            if it >= start:
                state = {"thread_id": thread, "iteration": it - 1, "created_at": turn_key, "last_model": "soak",
                         "tenant_id": "default", "messages": step, "tool_calls_pending": [], "scratchpad": {}}
                result = await capture_after_supervisor(
                    self.rec, state, {"iteration": it, "next_node": "respond" if last else "tool_worker",
                                      "messages": step})
                if result.get("snapshot_id"):
                    stored.append(result["snapshot_id"])
            if not last:
                content = "B" * big if (big and it == 1) else _filler(it, chunk)
                messages = [*step, {"role": "tool", "tool_call_id": call["id"], "content": content}]
        return stored

    async def large_chat(self, thread: str) -> None:
        """A chat whose state passes the per-step ceiling after its first
        step: those steps are stored cut, marked, and read back so."""
        from kazma_core.time_travel import TRUNCATION_MARK

        limit = int(self.rec.limits["max_snapshot_bytes"])
        ids = await self.direct_turn(thread, turn_key=f"{thread}-turn", question=f"Large {self.token()}",
                                     iterations=4, chunk=200, big=self.knobs["large_bytes"])
        cut = 0
        for i, sid in enumerate(ids):
            rec = await asyncio.to_thread(self.rec.get, sid)
            if rec is None:
                continue  # evicted already (a full install)
            marked = TRUNCATION_MARK in rec.get_state()
            if rec.nbytes > limit:
                self.violation(f"large chat {thread}: step {sid} holds {rec.nbytes} bytes, over the ceiling")
            if i >= 1 and not (rec.truncated and marked):
                self.violation(f"large chat {thread}: step {sid} is past the ceiling and not stored cut and marked")
            if i == 0 and (rec.truncated or marked):
                self.violation(f"large chat {thread}: step {sid} is under the ceiling and was cut")
            cut += bool(rec.truncated)
        self.out["large"] += 1
        self.out["cut_steps"] = self.out.get("cut_steps", 0) + cut

    async def direct_chats(self) -> None:
        if self.plan.get("large"):
            await self.large_chat(self.new_id("l"))
        if self.plan.get("aged"):
            days = self.knobs["retention_days"] + 2
            with _clock_set_back(days):
                await self.direct_turn(self.new_id("a"), turn_key=f"aged-{self.plan['cycle']}",
                                       question=f"Aged {self.token()}", iterations=3, chunk=300)
            self.out["aged"] += 1

    async def resume(self) -> None:
        """Continue the mission turn a kill cut short: the same turn, from
        the step after the last one stored (its eviction runs again)."""
        r = self.plan.get("resume")
        if not r:
            return
        await self.direct_turn(r["thread"], turn_key=r["turn_key"], question=r["question"],
                               iterations=r["iterations"], chunk=r["chunk"], start=r["start"])
        self.out["resumed"] = True

    # ── the user operations ──

    async def steps(self) -> list[Any]:
        def read() -> list[Any]:
            out = []
            for thread in self.rec.list_distinct_threads():
                out.extend(self.rec.list_snapshots(thread))
            return out

        steps = await asyncio.to_thread(read)
        return [s for s in steps if s.thread_id not in self.holds]

    async def operations(self) -> None:
        for spec in self.plan["ops"]:
            kind, _, how = spec.partition(":")
            await getattr(self, f"op_{kind}")(how)

    async def op_list(self, _how: str) -> None:
        from kazma_ui.replay_routes import group_steps
        from kazma_ui.sse_chat._time_travel import time_travel_reply

        steps = await self.steps()
        if not steps:
            self.op("list", 0.0, "nothing saved")
            return
        thread = self.rng.choice(sorted({s.thread_id for s in steps}))
        t0 = time.perf_counter()
        rows = await asyncio.to_thread(self.rec.list_snapshots, thread)
        turns = group_steps(rows)
        reply = await asyncio.to_thread(time_travel_reply, "/replay", thread, self.rec, "en")
        self.op("list", (time.perf_counter() - t0) * 1000)
        listed = [s["id"] for t in turns for s in t["steps"]]
        if sorted(listed) != sorted(r.id for r in rows) or len(set(listed)) != len(listed):
            self.violation(f"list {thread}: the page's turns do not list each step once")
        for t in turns:
            if [s["step"] for s in t["steps"]] != list(range(1, len(t["steps"]) + 1)):
                self.violation(f"list {thread}: steps of turn {t['turn_key']} are not numbered 1..n")
        if str(len(rows)) not in reply.splitlines()[0]:
            self.violation(f"list {thread}: the web chat's /replay does not say {len(rows)} steps: {reply[:80]!r}")

    async def op_detail(self, _how: str) -> None:
        from kazma_core.time_travel import TRUNCATION_MARK

        steps = await self.steps()
        if not steps:
            self.op("detail", 0.0, "nothing saved")
            return
        step = self.rng.choice(steps)
        t0 = time.perf_counter()
        rec = await asyncio.to_thread(self.rec.get, step.id, thread_id=step.thread_id)
        ref = await asyncio.to_thread(self.rec.resolve_ref, step.thread_id, step.id[:8])
        self.op("detail", (time.perf_counter() - t0) * 1000)
        if rec is None:
            self.violation(f"detail {step.id}: listed, then not found")
            return
        state = rec.get_state()
        if rec.nbytes != len(rec.state_json):
            self.violation(f"detail {step.id}: {rec.nbytes} bytes recorded, {len(rec.state_json)} stored")
        if not rec.truncated and len(state.get("messages") or []) != rec.message_count:
            self.violation(f"detail {step.id}: the listing counts {rec.message_count} messages, the step holds "
                           f"{len(state.get('messages') or [])}")
        if rec.truncated != (TRUNCATION_MARK in state):
            self.violation(f"detail {step.id}: cut {rec.truncated} but marked {TRUNCATION_MARK in state}")
        twins = [s for s in steps if s.thread_id == step.thread_id and s.id[:8] == step.id[:8]]
        if ref != (step.id if len(twins) == 1 else None):
            self.violation(f"detail {step.id}: its first characters resolve to {ref}")

    async def op_compare(self, _how: str) -> None:
        from kazma_core.time_travel import ReplayEngine

        steps = await self.steps()
        by_thread: dict[str, list[Any]] = {}
        for s in steps:
            by_thread.setdefault(s.thread_id, []).append(s)
        threads = sorted(t for t, ss in by_thread.items() if len(ss) >= 2)
        if not threads:
            self.op("compare", 0.0, "nothing to compare")
            return
        thread = self.rng.choice(threads)
        a, b = self.rng.sample(by_thread[thread], 2)
        engine = ReplayEngine(self.rec)
        t0 = time.perf_counter()
        sa = await asyncio.to_thread(engine.replay_from, thread, a.id)
        sb = await asyncio.to_thread(engine.replay_from, thread, b.id)
        diff = engine.compare_replays(sa or {}, sb or {})
        self.op("compare", (time.perf_counter() - t0) * 1000)
        if sa is None or sb is None:
            self.violation(f"compare {a.id}/{b.id}: a listed step was not found")
            return
        if (diff["original_message_count"], diff["replayed_message_count"]) != (
                len(sa.get("messages") or []), len(sb.get("messages") or [])):
            self.violation(f"compare {a.id}/{b.id}: the diff's message counts are not the steps'")

    async def op_restore(self, _how: str) -> None:
        from kazma_ui.thread_rewind import RewindRefused, rewind_thread

        steps = await self.steps()
        whole = [s for s in steps if not s.truncated]
        if not whole:
            self.op("restore", 0.0, "nothing to restore")
            return
        step = self.rng.choice(whole)
        thread = step.thread_id
        waiting = await self.paused(thread) is not None
        full = await asyncio.to_thread(self.rec.get, step.id)
        later = [s.id for s in steps if s.thread_id == thread and s.seq > step.seq]
        t0 = time.perf_counter()
        try:
            out = await rewind_thread(self.graph, thread, step.id, actor="soak", surface="soak", recorder=self.rec)
        except RewindRefused as refused:
            self.op("restore", (time.perf_counter() - t0) * 1000, refused.code)
            if refused.code != ("approval_pending" if waiting else ""):
                self.violation(f"restore {step.id} of {thread}: refused {refused.code} ({refused.detail})")
            return
        self.op("restore", (time.perf_counter() - t0) * 1000)
        if waiting:
            self.violation(f"restore {step.id} of {thread}: done while an approval waited")
        await self._check_restored(thread, step, full.get_state(), out, later)

    async def _check_restored(self, thread: str, step: Any, state: dict[str, Any], out: dict[str, Any],
                              later: list[str]) -> None:
        values, nxt = await self.values(thread)
        for p in _restore_problems(state, values, nxt):
            self.violation(f"restore {step.id} of {thread}: {p}")
        after = await asyncio.to_thread(self.rec.list_snapshots, thread)
        if any(s.seq > step.seq for s in after):
            self.violation(f"restore {step.id} of {thread}: steps after the restore point remain")
        if out.get("later_steps_removed") != len(later):
            self.violation(f"restore {step.id} of {thread}: said {out.get('later_steps_removed')} later steps "
                           f"removed, {len(later)} were there")
        session = await asyncio.to_thread(self.store.get, thread)
        if session is not None:
            asked = [_text(m) for m in values.get("messages") or [] if _role(m) == "user"]
            kept = [_text(m) for m in session.messages or [] if _role(m) == "user"]
            if asked != kept:
                self.violation(f"restore {step.id} of {thread}: the transcript asks {len(kept)} questions, "
                               f"the restored chat {len(asked)}")
        if thread in self.pool:
            self.pool[thread]["turns"] = sum(1 for m in values.get("messages") or [] if _role(m) == "user")
        else:
            self.transients.add(thread)
        self.touched.add(thread)

    async def op_fork(self, _how: str) -> None:
        from kazma_ui.thread_rewind import fork_thread

        steps = await self.steps()
        whole = [s for s in steps if not s.truncated]
        if not whole:
            self.op("fork", 0.0, "nothing to fork")
            return
        step = self.rng.choice(whole)
        thread = step.thread_id
        new_id = self.new_id("f")
        before, _ = await self.values(thread)
        full = await asyncio.to_thread(self.rec.get, step.id)
        t0 = time.perf_counter()
        out = await fork_thread(self.graph, thread, step.id, actor="soak", surface="soak", recorder=self.rec,
                                new_thread_id=new_id, lang="en")
        self.op("fork", (time.perf_counter() - t0) * 1000)
        state = full.get_state()
        values, nxt = await self.values(new_id)
        for p in _restore_problems(state, values, nxt):
            self.violation(f"fork {step.id} of {thread}: {p}")
        after, _ = await self.values(thread)
        if _digest(after.get("messages")) != _digest(before.get("messages")):
            self.violation(f"fork {step.id} of {thread}: the original chat changed")
        chat = await asyncio.to_thread(self.store.get, new_id)
        want = _projected(_expected_after_restore(list(state.get("messages") or [])))
        rows = [{"role": _role(m), "content": _text(m)} for m in (chat.messages if chat else [])]
        if not out.get("chat_saved") or rows != want:
            self.violation(f"fork {step.id} of {thread}: the new chat's transcript is not the step's questions "
                           f"and answers ({len(rows)} rows, {len(want)} expected)")
        if await asyncio.to_thread(self.rec.list_snapshots, new_id):
            self.violation(f"fork {step.id}: the new chat has steps it never took")
        if thread in self.pool and len(self.pool) < 2 * int(self.knobs["chats"]):
            # A fork of a chat in use goes on taking turns.
            self.pool[new_id] = {"id": new_id, "kind": "fork",
                                 "turns": sum(1 for m in values.get("messages") or [] if _role(m) == "user")}
        else:
            self.transients.add(new_id)
        self.touched.add(new_id)
        self.out["forked"] += 1

    def _biggest(self, steps: list[Any], rank: int) -> str | None:
        size: Counter[str] = Counter()
        for s in steps:
            size[s.thread_id] += s.nbytes
        ordered = [t for t, _ in size.most_common()]
        return ordered[rank] if len(ordered) > rank else None

    async def op_forget_turn(self, _how: str) -> None:
        from kazma_core.time_travel import forget_turn_snapshots
        from kazma_ui.thread_rewind import chat_keys_for

        steps = await self.steps()
        threads = sorted({s.thread_id for s in steps if s.thread_id in self.pool})
        if not threads:
            self.op("forget_turn", 0.0, "nothing to forget")
            return
        thread = self.rng.choice(threads)
        mine = [s for s in steps if s.thread_id == thread]
        turn = self.rng.choice(sorted({s.turn_index for s in mine}))
        below = sorted(s.id for s in mine if s.turn_index < turn)
        t0 = time.perf_counter()
        keys = await asyncio.to_thread(chat_keys_for, thread)
        n = await asyncio.to_thread(forget_turn_snapshots, keys, turn, recorder=self.rec)
        self.op("forget_turn", (time.perf_counter() - t0) * 1000)
        left = await asyncio.to_thread(self.rec.list_snapshots, thread)
        if n != len(mine) - len(below) or sorted(s.id for s in left) != below:
            self.violation(f"forget turn {turn} of {thread}: deleted {n} of {len(mine) - len(below)}, "
                           f"{len(left)} steps left ({len(below)} expected)")
        await self.delete_chat(thread)

    async def _clear(self, kind: str, how: str) -> None:
        from kazma_core.time_travel import forget_thread_snapshots
        from kazma_ui.i18n import t_plural
        from kazma_ui.sse_chat._time_travel import time_travel_reply
        from kazma_ui.thread_rewind import chat_keys_for

        steps = await self.steps()
        if how == "biggest":
            thread = self._biggest(steps, 0)
        else:
            threads = sorted({s.thread_id for s in steps})
            thread = self.rng.choice(threads) if threads else None
        if thread is None:
            self.op(kind, 0.0, "nothing saved")
            return
        before = sum(1 for s in steps if s.thread_id == thread)
        t0 = time.perf_counter()
        if kind == "replay_clear":
            reply = await asyncio.to_thread(time_travel_reply, "/replay clear", thread, self.rec, "en")
            if reply != t_plural("replay.chat_cleared", before, "en"):
                self.violation(f"/replay clear of {thread}: answered {reply!r} for {before} steps")
        else:
            keys = await asyncio.to_thread(chat_keys_for, thread)
            n = await asyncio.to_thread(forget_thread_snapshots, keys, recorder=self.rec)
            if n != before:
                self.violation(f"forget chat {thread}: deleted {n} of {before} steps")
        self.op(kind, (time.perf_counter() - t0) * 1000)
        if await asyncio.to_thread(self.rec.list_snapshots, thread):
            self.violation(f"{kind} of {thread}: steps remain")
        await self.delete_chat(thread)

    async def op_forget_chat(self, how: str) -> None:
        await self._clear("forget_chat", how)

    async def op_replay_clear(self, how: str) -> None:
        await self._clear("replay_clear", how)

    # ── faults ──

    async def faults(self) -> None:
        for kind in self.plan["faults"]:
            await getattr(self, f"fault_{kind}")()

    async def fault_locked(self) -> None:
        """Another connection holds the write lock: the capture waits the busy
        timeout and fails (no step, counted), the turn answers, reads answer
        the stored steps (WAL readers are not blocked), and the next capture
        after the lock is released is stored."""
        thread = await self.fresh_chat("k")
        before = sorted(s.id for s in await self.steps())
        failed = self.counts["failed"]
        holder = await asyncio.to_thread(_hold_write_lock, self.rec.db_path)
        try:
            t0 = time.perf_counter()
            final = await self.turn(thread, f"hello {self.token()}")
            ms = (time.perf_counter() - t0) * 1000
            listed = sorted(s.id for s in await self.steps())
        finally:
            await asyncio.to_thread(_release_write_lock, holder)
        problems = []
        if final.get("snapshot_id"):
            problems.append("a step was announced while the write lock was held")
        if not str((final.get("messages") or [{}])[-1].get("content") or "").startswith("Answer to"):
            problems.append("the turn did not answer")
        if self.counts["failed"] - failed != 1:
            problems.append(f"{self.counts['failed'] - failed} failed captures counted, 1 expected")
        if listed != before:
            problems.append("a read under the write lock did not answer the stored steps")
        after = await self.turn(thread, f"hello {self.token()}")
        if not after.get("snapshot_id") or await asyncio.to_thread(self.rec.get, after["snapshot_id"]) is None:
            problems.append("the capture after the lock was released was not stored")
        self.out["fault_turn_ms"] = round(ms, 1)
        self.fault("locked", problems, "capture none and counted; turn answered; reads answered; next stored")

    async def _unavailable_checks(self, thread: str, before: list[Any]) -> list[str]:
        """Under an unavailable store: the turn answers with no step, the
        failed capture is counted, every read raises SnapshotStoreError (never
        'no steps saved'), restore and fork answer 503 store_unavailable,
        /replay clear raises and deletes nothing."""
        from kazma_core.time_travel import SnapshotStoreError
        from kazma_ui.sse_chat._time_travel import time_travel_reply
        from kazma_ui.thread_rewind import RewindRefused, fork_thread, rewind_thread

        problems = []
        failed = self.counts["failed"]
        final = await self.turn(thread, f"hello {self.token()}")
        if final.get("snapshot_id"):
            problems.append("a step was announced")
        if self.counts["failed"] - failed != 1:
            problems.append(f"{self.counts['failed'] - failed} failed captures counted, 1 expected")
        target = before[0] if before else None
        reads: list[tuple[str, Any]] = [
            ("list", lambda: self.rec.list_snapshots(target.thread_id if target else thread)),
            ("threads", self.rec.list_distinct_threads),
            ("/replay", lambda: time_travel_reply("/replay", target.thread_id if target else thread, self.rec, "en")),
            ("/replay clear", lambda: time_travel_reply("/replay clear", target.thread_id if target else thread,
                                                        self.rec, "en")),
        ]
        if target is not None:
            reads.append(("detail", lambda: self.rec.get(target.id)))
        for name, read in reads:
            try:
                answer = await asyncio.to_thread(read)
                problems.append(f"{name} answered {str(answer)[:60]!r} instead of saying the store is unreadable")
            except SnapshotStoreError:
                pass
        if target is not None:
            for name, action in (("restore", rewind_thread), ("fork", fork_thread)):
                try:
                    await action(self.graph, target.thread_id, target.id, actor="soak", surface="soak",
                                 recorder=self.rec)
                    problems.append(f"{name} went ahead")
                except RewindRefused as refused:
                    if (refused.code, refused.status) != ("store_unavailable", 503):
                        problems.append(f"{name} refused {refused.code} {refused.status}, not store_unavailable 503")
        return problems

    async def fault_io_error(self) -> None:
        thread = await self.fresh_chat("i")
        before = await self.steps()
        store = await asyncio.to_thread(self.rec._get_store)
        real = store._conn
        store._conn = _FailingConnection(real)
        try:
            problems = await self._unavailable_checks(thread, before)
        finally:
            store._conn = real
        if sorted(s.id for s in await self.steps()) != sorted(s.id for s in before):
            problems.append("the steps changed while the store failed")
        self.fault("io_error", problems, "capture none and counted; reads raised; restore/fork 503; nothing lost")

    async def fault_unreadable(self) -> None:
        thread = await self.fresh_chat("u")
        before = await self.steps()
        db = Path(self.rec.db_path)
        await asyncio.to_thread(self.rec.close)
        await asyncio.to_thread(_set_aside, db)
        try:
            problems = await self._unavailable_checks(thread, before)
        finally:
            await asyncio.to_thread(self.rec.close)
            await asyncio.to_thread(_put_back, db)
        if sorted(s.id for s in await self.steps()) != sorted(s.id for s in before):
            problems.append("the steps changed while the file was unreadable")
        self.fault("unreadable", problems, "capture none and counted; reads raised; restore/fork 503; nothing lost")

    async def fault_restore_running(self) -> None:
        from kazma_ui.active_turns import register_turn, unregister_turn
        from kazma_ui.thread_rewind import RewindRefused, rewind_thread

        thread = await self.fresh_chat("r")
        await self.turn(thread, f"hello {self.token()}")
        step = (await asyncio.to_thread(self.rec.list_snapshots, thread))[0]
        goal = f"slow {self.token()}"
        self.holds[thread] = {"entered": asyncio.Event(), "release": asyncio.Event()}
        task = asyncio.create_task(self.turn(thread, goal))
        register_turn(thread, task)
        problems = []
        try:
            await asyncio.wait_for(self.holds[thread]["entered"].wait(), 60)
            try:
                await rewind_thread(self.graph, thread, step.id, actor="soak", surface="soak", recorder=self.rec)
                problems.append("the restore went ahead under the running turn")
            except RewindRefused as refused:
                if refused.code != "turn_running":
                    problems.append(f"refused {refused.code}, not turn_running")
        finally:
            self.holds[thread]["release"].set()
            await asyncio.wait_for(task, 120)
            unregister_turn(thread, task)
            self.holds.pop(thread, None)
        values, _ = await self.values(thread)
        messages = values.get("messages") or []
        if not messages or _text(messages[-1]) != f"Answer to {goal}":
            problems.append("the running turn's answer is not the chat's last message")
        self.fault("restore_running", problems, "refused turn_running; the turn's answer stood")

    async def fault_restore_cut(self) -> None:
        from kazma_ui.thread_rewind import RewindRefused, fork_thread, rewind_thread

        cut = [s for s in await self.steps() if s.truncated]
        if not cut:
            await self.large_chat(self.new_id("l"))
            cut = [s for s in await self.steps() if s.truncated]
        problems = []
        if not cut:
            self.fault("restore_cut", ["no cut step to restore"], "")
            return
        step = self.rng.choice(cut)
        before, _ = await self.values(step.thread_id)
        new_id = self.new_id("x")
        for name, action, extra in (("restore", rewind_thread, {}), ("fork", fork_thread, {"new_thread_id": new_id})):
            try:
                await action(self.graph, step.thread_id, step.id, actor="soak", surface="soak",
                             recorder=self.rec, **extra)
                problems.append(f"{name} of a cut step went ahead")
            except RewindRefused as refused:
                if refused.code != "snapshot_truncated":
                    problems.append(f"{name} refused {refused.code}, not snapshot_truncated")
        after, _ = await self.values(step.thread_id)
        if _digest(after.get("messages")) != _digest(before.get("messages")):
            problems.append("the chat changed")
        forked, _ = await self.values(new_id)
        if forked.get("messages"):
            problems.append("a fork chat was written")
        self.fault("restore_cut", problems, "restore and fork refused snapshot_truncated; nothing written")

    async def fault_restore_pending(self) -> None:
        from kazma_ui.thread_rewind import RewindRefused, rewind_thread

        thread = await self.fresh_chat("p")
        await self.turn(thread, f"read:{self.token()}")
        problems = []
        if await self.paused(thread) is None:
            self.fault("restore_pending", ["the tool turn did not wait at its approval"], "")
            return
        step = (await asyncio.to_thread(self.rec.list_snapshots, thread))[0]
        try:
            await rewind_thread(self.graph, thread, step.id, actor="soak", surface="soak", recorder=self.rec)
            problems.append("the restore went ahead while an approval waited")
        except RewindRefused as refused:
            if refused.code != "approval_pending" or [g["tool"] for g in refused.gates] != ["read"]:
                problems.append(f"refused {refused.code} naming {refused.gates}, not approval_pending naming read")
        final = await self.decide(thread, approved=True)
        if not _text((final.get("messages") or [{}])[-1]).startswith("Answer to"):
            problems.append("the approval was not answerable after the refusal")
        self.fault("restore_pending", problems, "refused approval_pending naming the card; the card still decided")

    # ── maintenance ──

    async def maintenance(self) -> None:
        if not self.plan.get("maintain"):
            return
        from kazma_core.time_travel import maintain_snapshots

        db = Path(self.rec.db_path)
        retention = int(self.knobs["retention_days"])
        before = await asyncio.to_thread(_pages, db)
        t0 = time.perf_counter()
        out = await asyncio.to_thread(maintain_snapshots, None, retention)
        ms = (time.perf_counter() - t0) * 1000
        after = await asyncio.to_thread(_pages, db)
        cutoff = (datetime.now(UTC) - timedelta(days=retention)).isoformat()
        self.op("maintenance", ms, "vacuum" if str(out.get("vacuum")).startswith("ok") else "no vacuum")
        free = after["freelist"] * after["page_size"]
        ratio = after["freelist"] / after["page_count"] if after["page_count"] else 0.0
        if out.get("prune") != "ok" or out.get("remaining_old") != 0:
            self.violation(f"maintenance: the prune said {out.get('prune')!r}, {out.get('remaining_old')} old rows left")
        if str(out.get("vacuum")).startswith("failed"):
            self.violation(f"maintenance: {out.get('vacuum')}")
        if free >= _VACUUM_MIN_FREE_BYTES and ratio >= _VACUUM_FREE_RATIO:
            self.violation(f"maintenance: {free} bytes ({ratio:.0%}) free after it: a VACUUM was due and did not run")
        if out.get("vacuum") == "ok" and after.get("wal_bytes"):
            self.violation(f"maintenance: a VACUUM answered ok and left a WAL of {after['wal_bytes']} bytes "
                           "(the reclaim it reported is not the disk's)")
        self.out["maintenance"] = {"result": out, "before": before, "after": after, "cutoff": cutoff,
                                   "retention_days": retention, "ms": round(ms, 1)}

    # ── the mission ──

    async def mission(self) -> None:
        m = self.plan.get("mission")
        if not m:
            return
        kill = self.plan.get("kill") or {}
        self.out["mission"] = {"thread": m["thread"], "iterations": m["iterations"], "chunk": m["chunk"],
                               "kill": kill.get("mode")}
        self.save()
        if kill.get("mode") in ("after_insert", "mid_evict"):
            self._die_at(kill["mode"], int(kill["at_capture"]))
        marker = Path(m["marker"])
        marker.write_text("started", encoding="utf-8")
        t0 = time.perf_counter()
        await self.direct_turn(m["thread"], turn_key=m["turn_key"], question=m["question"],
                               iterations=m["iterations"], chunk=m["chunk"])
        marker.unlink(missing_ok=True)
        self.out["mission"]["seconds"] = round(time.perf_counter() - t0, 2)

    def _die_at(self, mode: str, at: int) -> None:
        """Exit the process at the *at*-th capture of the mission: right after
        its insert committed (``after_insert``), or inside its eviction
        transaction, before the commit (``mid_evict``)."""
        from kazma_core import time_travel

        cls = time_travel.SnapshotStore
        real_save, real_trim = cls.save, cls._trim_bytes
        saves = [0]

        def die() -> None:
            sys.stdout.flush()
            os._exit(_KILLED)

        def save(store: Any, record: Any) -> None:
            real_save(store, record)
            saves[0] += 1
            if mode == "after_insert" and saves[0] >= at:
                die()

        def trim(store: Any, *a: Any, **k: Any) -> int:
            n = real_trim(store, *a, **k)
            if saves[0] >= at:
                die()
            return n

        cls.save = save  # type: ignore[method-assign]
        if mode == "mid_evict":
            cls._trim_bytes = trim  # type: ignore[method-assign]

    async def digests(self) -> None:
        threads = sorted(set(self.pool) | (self.touched - set(self.retired)))
        self.out["digests"] = await _checkpoint_digests(self.saver, threads)

    async def run(self) -> None:
        await self.boot()
        await self.verify()
        await self.resume()
        await self.drop_transients()
        for phase in ("graph_turns", "direct_chats", "operations", "faults", "maintenance", "digests"):
            self.out["phase"] = phase
            await getattr(self, phase)()
            self.save()
        self.out["phase"] = "mission"
        await self.mission()
        await self.close()
        self.out["phase"] = "done"
        self.out["done"] = True
        self.save()


def _child(plan_path: str) -> int:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
    cycle = _Cycle(plan)
    if sys.platform == "win32":
        # The server's loop kind (AGENTS §23).
        asyncio.run(cycle.run(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(cycle.run())
    return 0


# ── the parent: the plan, the checks, the report ─────────────────────────


class _Latency:
    """Every sample's count and maximum; a bounded uniform sample (seeded)
    for the percentiles, so a run of days keeps a fixed size."""

    def __init__(self, seed: int, cap: int = 20_000) -> None:
        self.n = 0
        self.max = 0.0
        self.sample: list[float] = []
        self.cap = cap
        self._rng = random.Random(seed)

    def add(self, value: float) -> None:
        self.n += 1
        self.max = max(self.max, value)
        if len(self.sample) < self.cap:
            self.sample.append(value)
        else:
            j = self._rng.randrange(self.n)
            if j < self.cap:
                self.sample[j] = value

    def summary(self) -> dict[str, Any]:
        out = latency_summary(self.sample)
        out["n"] = self.n
        out["max"] = round(self.max, 3) if self.n else None
        return out


def _file_bounds(limits: dict[str, int], page_size: int = 4096) -> dict[str, int]:
    """The database and WAL file bounds the budgets imply.

    Stored bytes are at most the install budget plus the one step written
    before its eviction runs. SQLite allocates from its free pages before it
    grows the file, so the file is never larger than the most pages ever live:
    the stored bytes on overflow pages (4 bytes of each page are its link),
    plus for each row at most three more pages (its cell and the partial last
    page of its overflow chain; index entries and interior pages share pages),
    plus 8 MiB for the schema, the free-list trunks and the indexes' interior.
    The VACUUM rule bounds what stays free after maintenance, not the peak.
    The WAL holds at most the largest transaction since its last checkpoint
    (VACUUM writes the whole database through it) plus the 1,000 pages after
    which SQLite checkpoints, at 24 bytes of frame header a page.
    """
    stored = int(limits["max_total_bytes"]) + int(limits["max_snapshot_bytes"])
    rows = int(limits["max_global_snapshots"]) + 1
    db = int(stored * page_size / (page_size - 4)) + rows * 3 * page_size + 8 * _MiB
    wal = int(db * (page_size + 24) / page_size) + 1000 * (page_size + 24) + _MiB
    return {"db_file_bytes": db, "wal_file_bytes": wal}


def _store_facts(data_dir: Path) -> dict[str, Any]:
    """What the snapshot store holds, read only."""
    db = data_dir / "snapshots.db"
    wal = Path(str(db) + "-wal")
    facts: dict[str, Any] = {"db_bytes": db.stat().st_size if db.exists() else 0,
                             "wal_bytes": wal.stat().st_size if wal.exists() else 0,
                             "rows": [], "integrity": "ok", "page_size": 4096, "page_count": 0, "freelist": 0}
    if not db.exists():
        return facts
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        facts["integrity"] = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        facts["page_size"] = int(conn.execute("PRAGMA page_size").fetchone()[0])
        facts["page_count"] = int(conn.execute("PRAGMA page_count").fetchone()[0])
        facts["freelist"] = int(conn.execute("PRAGMA freelist_count").fetchone()[0])
        facts["rows"] = [
            {"id": r[0], "thread": r[1], "turn_key": r[2], "turn": r[3], "iteration": r[4], "nbytes": r[5],
             "length": r[6], "truncated": bool(r[7]), "marked": bool(r[8]), "timestamp": r[9]}
            for r in conn.execute(
                "SELECT id, thread_id, turn_key, turn_index, iteration, nbytes, length(state_json), truncated, "
                "instr(state_json, ?) > 0, timestamp FROM snapshots", (f'"{_MARK}"',))
        ]
    finally:
        conn.close()
    return facts


def _judge(facts: dict[str, Any], state: dict[str, Any], cutoff: str | None = None) -> list[str]:
    """The invariants, from the store's facts and the parent's record."""
    limits = state["limits"]
    bounds = state["bounds"]
    allow = state.get("allow") or {}
    problems: list[str] = []
    if facts["integrity"] != "ok":
        problems.append(f"integrity: {facts['integrity']}")
    per_turn: Counter[tuple[str, str]] = Counter()
    rows_of: Counter[str] = Counter()
    bytes_of: Counter[str] = Counter()
    for r in facts["rows"]:
        if r["nbytes"] != r["length"]:
            problems.append(f"bytes: step {r['id']} records {r['nbytes']} bytes and holds {r['length']}")
        if r["length"] > limits["max_snapshot_bytes"]:
            problems.append(f"bytes: step {r['id']} holds {r['length']} bytes (ceiling {limits['max_snapshot_bytes']})")
        if r["truncated"] != r["marked"]:
            problems.append(f"cut: step {r['id']} is {'cut' if r['truncated'] else 'whole'} and "
                            f"{'marked' if r['marked'] else 'unmarked'}")
        if cutoff and r["timestamp"] < cutoff:
            problems.append(f"prune: step {r['id']} of {r['timestamp']} outlived the retention after maintenance")
        per_turn[(r["thread"], r["turn_key"])] += 1
        rows_of[r["thread"]] += 1
        bytes_of[r["thread"]] += r["length"]
    over_turn = 1 if allow else 0
    for (thread, turn_key), n in per_turn.items():
        cap = limits["max_per_turn"] + (over_turn if (thread, turn_key) == (allow.get("thread"), allow.get("turn_key"))
                                        else 0)
        if n > cap:
            problems.append(f"cap: turn {turn_key} of {thread} holds {n} steps (max {limits['max_per_turn']})")
    for thread, n in rows_of.items():
        mine = thread == allow.get("thread")
        if n > limits["max_snapshots"] + (1 if mine else 0):
            problems.append(f"cap: chat {thread} holds {n} steps (max {limits['max_snapshots']})")
        if bytes_of[thread] > limits["max_thread_bytes"] + (limits["max_snapshot_bytes"] if mine else 0):
            problems.append(f"cap: chat {thread} holds {bytes_of[thread]} bytes (max {limits['max_thread_bytes']})")
    total_rows, total_bytes = len(facts["rows"]), sum(bytes_of.values())
    if total_rows > limits["max_global_snapshots"] + over_turn:
        problems.append(f"cap: the install holds {total_rows} steps (max {limits['max_global_snapshots']})")
    if total_bytes > limits["max_total_bytes"] + (limits["max_snapshot_bytes"] if allow else 0):
        problems.append(f"cap: the install holds {total_bytes} bytes (max {limits['max_total_bytes']})")
    retired = set(state["retired"])
    for thread in sorted(set(rows_of) & retired):
        problems.append(f"back: chat {thread} was cleared, forgotten or deleted and holds {rows_of[thread]} steps")
    gone = set(state["gone"])
    for r in facts["rows"]:
        if r["id"] in gone:
            problems.append(f"back: step {r['id']} of {r['thread']} was deleted and is in the store again")
    # The model the file bound rests on, checked at every size: the pages in
    # use hold the stored bytes plus at most three pages a step and 8 MiB.
    page = int(facts["page_size"])
    live = (int(facts["page_count"]) - int(facts["freelist"])) * page
    model = int(total_bytes * page / (page - 4)) + (total_rows + 1) * 3 * page + 8 * _MiB
    if live > model:
        problems.append(f"file: {live} bytes of pages in use for {total_bytes} stored in {total_rows} steps "
                        f"(the bound's model allows {model})")
    if facts["db_bytes"] > bounds["db_file_bytes"]:
        problems.append(f"file: snapshots.db is {facts['db_bytes']} bytes (bound {bounds['db_file_bytes']})")
    if facts["wal_bytes"] > bounds["wal_file_bytes"]:
        problems.append(f"file: snapshots.db-wal is {facts['wal_bytes']} bytes (bound {bounds['wal_file_bytes']})")
    return problems


def _check(data_dir: Path, state: dict[str, Any], cutoff: str | None = None) -> list[str]:
    """The invariants, read from the store (the parent runs this after every cycle)."""
    return _judge(_store_facts(data_dir), state, cutoff)


def _mission_progress(data_dir: Path, thread: str, turn_key: str) -> int:
    """The last iteration of a mission turn the store holds (0: none)."""
    db = data_dir / "snapshots.db"
    if not db.exists():
        return 0
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(iteration) FROM snapshots WHERE thread_id = ? AND turn_key = ?",
                           (thread, turn_key)).fetchone()
    finally:
        conn.close()
    return int(row[0] or 0)


def _captures_of(iterations: int) -> int:
    """Steps a turn of *iterations* captures (every step to 20, every 10th after, the last)."""
    return min(iterations, 20) + max(0, (iterations - 20) // 10) + (1 if iterations > 20 and iterations % 10 else 0)


def _plan(cycle: int, rng: random.Random, args: argparse.Namespace, state: dict[str, Any],
          soak_dir: Path, *, final: bool = False) -> dict[str, Any]:
    cover = _COVERAGE[cycle - 1] if cycle <= len(_COVERAGE) and not final else None
    plan: dict[str, Any] = {
        "cycle": cycle, "seed": rng.randrange(1 << 30), "result_file": str(soak_dir / f"result-{cycle}.json"),
        "pool": state["pool"], "verify": state.get("digests") or {}, "resume": state.get("resume"),
        "transients": state.get("transients") or [],
        "new_chats": [], "ops": [], "faults": [], "mission": None, "kill": None,
        "large": False, "aged": False, "maintain": False,
        "knobs": {"chats": args.chats, "turn_rate": args.turn_rate, "pending_rate": args.pending_rate,
                  "chat_max_turns": args.chat_max_turns, "large_bytes": args.large_bytes,
                  "retention_days": args.retention_days},
    }
    if final:
        plan["knobs"]["turn_rate"] = 0.0  # nothing new: waiting approvals answered, a killed turn continued
        return plan
    plan["new_chats"] = [f"soak-c{cycle}-{k}" for k in range(max(0, args.chats - len(state["pool"])))]
    if cover is not None:
        plan["ops"], plan["faults"] = list(cover["ops"]), list(cover["faults"])
        plan["large"], plan["aged"] = bool(cover.get("large")), bool(cover.get("aged"))
        plan["maintain"] = bool(cover.get("maintain"))
        with_mission, kill = bool(cover.get("mission")), cover.get("kill")
    else:
        plan["ops"] = rng.choices(_OP_KINDS, weights=_OP_WEIGHTS, k=args.ops)
        rates = {"locked": args.lock_rate, "io_error": args.io_error_rate, "unreadable": args.unreadable_rate,
                 "restore_running": args.refusal_rate, "restore_cut": args.refusal_rate,
                 "restore_pending": args.refusal_rate}
        plan["faults"] = [k for k in _FAULT_KINDS if rng.random() < rates[k]]
        plan["large"] = cycle % args.large_every == 0
        plan["aged"] = cycle % args.aged_every == 0
        plan["maintain"] = cycle % args.maintain_every == 0
        with_mission = cycle % args.mission_every == 0
        kill = rng.choice(_KILL_MODES) if with_mission and rng.random() < args.kill_rate else None
    if with_mission:
        iterations = args.mission_iterations
        plan["mission"] = {
            "thread": f"soak-m{cycle}", "turn_key": f"soak-m{cycle}-turn", "iterations": iterations,
            "question": f"Mission {cycle} " + "".join(rng.choice("bcdfghjklmnpqrstvwx") for _ in range(8)),
            "chunk": rng.randint(args.mission_chunk_min, args.mission_chunk_max),
            "marker": str(soak_dir / f"mission-{cycle}.started"),
        }
        if kill:
            plan["kill"] = {"mode": kill, "at_capture": rng.randint(1, max(1, _captures_of(iterations))),
                            "delay": round(rng.uniform(0.05, 0.6), 3)}
    return plan


def _run_child(plan: dict[str, Any], env: dict[str, str], soak_dir: Path, timeout: float) -> dict[str, Any]:
    """Run one cycle; poll its memory; kill it when the plan says so."""
    import psutil

    plan_file = soak_dir / f"plan-{plan['cycle']}.json"
    plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    result_file = Path(plan["result_file"])
    result_file.unlink(missing_ok=True)
    kill = plan.get("kill") or {}
    marker = Path((plan.get("mission") or {}).get("marker") or soak_dir / "no-marker")
    started = time.monotonic()
    peak = 0
    kill_at: float | None = None
    killed_by_parent = timed_out = False
    with open(soak_dir / "child.out", "w", encoding="utf-8") as out, \
            open(soak_dir / "child.err", "w", encoding="utf-8") as err:
        proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child", str(plan_file)],
                                env=env, stdout=out, stderr=err)
        try:
            child = psutil.Process(proc.pid)
        except psutil.Error:
            child = None
        while proc.poll() is None:
            if child is not None:
                try:
                    peak = max(peak, child.memory_info().rss)
                except psutil.Error:
                    pass
            now = time.monotonic()
            if kill.get("mode") == "hard" and kill_at is None and marker.exists():
                kill_at = now + float(kill.get("delay") or 0.1)
            if kill_at is not None and now >= kill_at and not killed_by_parent:
                proc.kill()
                killed_by_parent = True
            if now - started > timeout:
                proc.kill()
                timed_out = True
            time.sleep(0.05)
        proc.wait()
    plan_file.unlink(missing_ok=True)  # thousands of cycles in a run of days
    marker.unlink(missing_ok=True)
    result: dict[str, Any] = {}
    if result_file.exists():
        result = json.loads(result_file.read_text(encoding="utf-8"))
        result_file.unlink(missing_ok=True)
    err_tail = (soak_dir / "child.err").read_text(encoding="utf-8", errors="replace")[-2000:]
    return {"rc": proc.returncode, "killed_by_parent": killed_by_parent and not timed_out, "timed_out": timed_out,
            "result": result, "polled_peak_mib": round(peak / _MiB, 1), "seconds": round(time.monotonic() - started, 2),
            "stderr": err_tail}


class _Run:
    """The parent: its record of the run (``state``) and the aggregates the
    report is made of."""

    def __init__(self, args: argparse.Namespace, data_dir: Path) -> None:
        self.args = args
        self.data_dir = data_dir
        self.soak_dir = data_dir / "soak"
        self.soak_dir.mkdir(parents=True, exist_ok=True)
        self.env = _env(data_dir)
        self.started = time.monotonic()
        self.state: dict[str, Any] = {"pool": [], "transients": [], "retired": [], "gone": [], "present": [],
                                      "digests": {}, "resume": None, "allow": None, "limits": None, "bounds": None}
        self.gone: OrderedDict[str, None] = OrderedDict()
        self.ops: dict[str, _Latency] = {}
        self.outcomes: dict[str, Counter[str]] = {}
        self.faults: dict[str, dict[str, Any]] = {}
        self.capture = _Latency(args.seed ^ 0xC0)
        self.capture_results: Counter[str] = Counter()
        self.kills: Counter[str] = Counter()
        self.chats: Counter[str] = Counter()
        self.maintenance: dict[str, Any] = {"runs": 0, "vacuums": 0, "pruned_rows": 0, "last": None,
                                            "wal_after_vacuum_bytes": 0}
        self.missions: Counter[str] = Counter()
        self.peaks: dict[str, Any] = {}
        self.kill_peaks: dict[str, Any] = {}
        self.other_stores: dict[str, Any] = {}
        self.violations: list[str] = []
        self.cycles = 0
        self.child_seconds = _Latency(args.seed ^ 0xC1)
        self.samples: list[dict[str, Any]] = []
        self.end: dict[str, Any] = {}

    def _peak(self, key: str, value: float, into: dict[str, Any] | None = None) -> None:
        peaks = self.peaks if into is None else into
        if value is not None and value > peaks.get(key, float("-inf")):
            peaks[key] = value

    def save_state(self) -> None:
        self.state["gone"] = list(self.gone)
        tmp = self.soak_dir / "state.json.tmp"
        tmp.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.soak_dir / "state.json")

    def cycle(self, plan: dict[str, Any]) -> list[str]:
        """Run one cycle and check the stores. Returns its violations."""
        c = plan["cycle"]
        run = _run_child(plan, self.env, self.soak_dir, self.args.cycle_timeout)
        res = run["result"]
        self.cycles = c
        self.child_seconds.add(run["seconds"])
        problems = [f"cycle {c}: {v}" for v in res.get("violations", [])]
        killed = run["rc"] == _KILLED or run["killed_by_parent"]
        if run["timed_out"]:
            problems.append(f"cycle {c}: the child ran past {self.args.cycle_timeout} s and was stopped")
        elif run["rc"] != 0 and not killed:
            problems.append(f"cycle {c}: the child failed ({run['rc']}) in {res.get('phase')}: {run['stderr']}")
        elif not killed and not res.get("done"):
            problems.append(f"cycle {c}: the child ended without its result: {run['stderr']}")
        if self.state["limits"] is None and res.get("limits"):
            self.state["limits"] = res["limits"]
            self.state["bounds"] = _file_bounds(res["limits"])
        self._aggregate(plan, res, killed, run)
        if _put_back(self.data_dir / "snapshots.db"):
            problems.append(f"cycle {c}: the cycle ended with the store's files set aside (put back)")
        # The record the next cycle builds on.
        if "pool" in res:
            self.state["pool"] = res["pool"]
            self.state["transients"] = res.get("transients") or []
        self.state["retired"] = sorted(set(self.state["retired"]) | set(res.get("retired") or []))
        if "digests" in res:
            self.state["digests"] = res["digests"]
        elif not killed:
            self.state["digests"] = {}
        mission = plan.get("mission")
        if killed and mission:
            self.state["allow"] = {"thread": mission["thread"], "turn_key": mission["turn_key"]}
            done = _mission_progress(self.data_dir, mission["thread"], mission["turn_key"])
            self.state["resume"] = {**{k: mission[k] for k in ("thread", "turn_key", "question", "iterations",
                                                                "chunk")},
                                    "start": min(done + 1, mission["iterations"]) if done else 1}
        else:
            self.state["allow"] = None
            self.state["resume"] = None
        if self.state["limits"] is None:
            problems.append(f"cycle {c}: the child reported no budgets")
            return problems
        maint = res.get("maintenance") or {}
        facts = _store_facts(self.data_dir)
        problems += [f"cycle {c}: {p}" for p in _judge(facts, self.state, maint.get("cutoff"))]
        # The WAL right after maintenance: the child closes its connections
        # before this check, and the last one to close deletes the WAL, so a
        # VACUUM's WAL is only seen there.
        wal_after = int((maint.get("after") or {}).get("wal_bytes") or 0)
        self._peak("wal_file_bytes", wal_after)
        if wal_after > self.state["bounds"]["wal_file_bytes"]:
            problems.append(f"cycle {c}: file: the WAL after maintenance is {wal_after} bytes "
                            f"(bound {self.state['bounds']['wal_file_bytes']})")
        self._observe(facts, res, run, problems, c)
        present = {r["id"] for r in facts["rows"]}
        for sid in set(self.state["present"]) - present:
            self.gone[sid] = None
        while len(self.gone) > _GONE_WINDOW:
            self.gone.popitem(last=False)
        self.state["present"] = sorted(present)
        self.save_state()
        return problems

    def _aggregate(self, plan: dict[str, Any], res: dict[str, Any], killed: bool, run: dict[str, Any]) -> None:
        for kind, entry in (res.get("ops") or {}).items():
            lat = self.ops.setdefault(kind, _Latency(self.args.seed ^ zlib.crc32(kind.encode())))
            for ms in entry.get("ms") or []:
                lat.add(ms)
            self.outcomes.setdefault(kind, Counter()).update(entry.get("outcomes") or {})
        for kind, entry in (res.get("faults") or {}).items():
            agg = self.faults.setdefault(kind, {"injected": 0, "endings": Counter()})
            agg["injected"] += entry.get("injected", 0)
            agg["endings"].update(entry.get("endings") or {})
        for ms in res.get("capture_ms") or []:
            self.capture.add(ms)
        self.capture_results.update(res.get("captures") or {})
        for key in ("created", "forked", "deleted", "large", "aged"):
            self.chats[key] += int(res.get(key) or 0)
        self.chats["cut_steps"] += int(res.get("cut_steps") or 0)
        mission = plan.get("mission")
        if mission:
            self.missions["turns"] += 1
            self.missions["iterations"] += mission["iterations"]
        if res.get("resumed"):
            self.missions["resumed"] += 1
        kill = (plan.get("kill") or {}).get("mode")
        if kill:
            self.kills[kill if killed else f"{kill} (the cycle ended first)"] += 1
        maint = res.get("maintenance")
        if maint:
            out = maint.get("result") or {}
            self.maintenance["runs"] += 1
            self.maintenance["pruned_rows"] += int(out.get("deleted") or 0)
            if str(out.get("vacuum")).startswith("ok"):
                self.maintenance["vacuums"] += 1
                wal = int((maint.get("after") or {}).get("wal_bytes") or 0)
                self.maintenance["wal_after_vacuum_bytes"] = max(self.maintenance["wal_after_vacuum_bytes"], wal)
                # The file a VACUUM shrank and any WAL it left (maintenance
                # truncates it; a reader holding an old snapshot can delay that).
                before, after = maint.get("before") or {}, maint.get("after") or {}
                self.maintenance["vacuum_runs"] = [*self.maintenance.get("vacuum_runs", [])[-19:], {
                    "cycle": plan["cycle"], "db_before": out.get("size_before"), "db_after": out.get("size_after"),
                    "pages_before": before.get("page_count"), "free_before": before.get("freelist"),
                    "wal_before": before.get("wal_bytes"), "pages_after": after.get("page_count"),
                    "wal_after": wal, "pruned": out.get("deleted"), "reported_reclaimed": out.get("reclaimed"),
                    # What the disk holds: the file and its WAL, before and after.
                    "disk_before": int(before.get("db_bytes") or 0) + int(before.get("wal_bytes") or 0),
                    "disk_after": int(after.get("db_bytes") or 0) + wal,
                }]
            self.maintenance["last"] = {"vacuum": out.get("vacuum"), "deleted": out.get("deleted"),
                                        "size_before": out.get("size_before"), "size_after": out.get("size_after"),
                                        "free_before": (maint.get("before") or {}).get("freelist"),
                                        "page_count_before": (maint.get("before") or {}).get("page_count")}

    def _observe(self, facts: dict[str, Any], res: dict[str, Any], run: dict[str, Any], problems: list[str],
                 c: int) -> None:
        rows = facts["rows"]
        per_thread_n: Counter[str] = Counter(r["thread"] for r in rows)
        per_thread_b: Counter[str] = Counter()
        per_turn: Counter[tuple[str, str]] = Counter((r["thread"], r["turn_key"]) for r in rows)
        for r in rows:
            per_thread_b[r["thread"]] += r["length"]
        stored = sum(per_thread_b.values())
        # Right after a kill the step in flight may stand over a cap until
        # its turn continues: those counts are kept apart from the peaks.
        into = self.kill_peaks if self.state.get("allow") else None
        self._peak("rows_total", len(rows), into)
        self._peak("rows_per_chat", max(per_thread_n.values(), default=0), into)
        self._peak("rows_per_turn", max(per_turn.values(), default=0), into)
        self._peak("stored_bytes_total", stored, into)
        self._peak("stored_bytes_per_chat", max(per_thread_b.values(), default=0), into)
        self._peak("step_bytes", max((r["length"] for r in rows), default=0))
        self._peak("db_file_bytes", facts["db_bytes"])
        self._peak("wal_file_bytes", facts["wal_bytes"])
        live = (facts["page_count"] - facts["freelist"]) * facts["page_size"]
        self._peak("live_page_bytes", live)
        self._peak("live_page_bytes_over_stored", live - stored)
        for path in self.data_dir.iterdir():
            if path.is_file() and not path.name.startswith("snapshots.db"):
                self._peak(path.name, path.stat().st_size, self.other_stores)
        peak = max(float(res.get("rss_peak_mib") or 0), run["polled_peak_mib"])
        base = res.get("rss_baseline_mib")
        self._peak("rss_mib", peak)
        if base:
            self._peak("rss_growth_mib", round(peak - float(base), 1))
            self._peak("rss_baseline_mib", float(base))
            if peak - float(base) > self.args.rss_growth_mib:
                problems.append(f"cycle {c}: RSS grew {peak - float(base):.0f} MiB over its start "
                                f"(ceiling {self.args.rss_growth_mib})")
        if peak > self.args.rss_ceiling_mib:
            problems.append(f"cycle {c}: RSS peaked at {peak:.0f} MiB (ceiling {self.args.rss_ceiling_mib})")
        self.end = {"rows": len(rows), "stored_bytes": stored, "db_file_bytes": facts["db_bytes"],
                    "wal_file_bytes": facts["wal_bytes"], "free_pages": facts["freelist"],
                    "page_count": facts["page_count"], "chats_with_steps": len(per_thread_n),
                    "cut_steps": sum(1 for r in rows if r["truncated"])}
        sample = {"cycle": c, "seconds": run["seconds"], "rss_peak_mib": peak, "rows": len(rows),
                  "stored_mib": round(stored / _MiB, 2), "db_mib": round(facts["db_bytes"] / _MiB, 2)}
        self.samples.append(sample)
        if len(self.samples) > 400:
            self.samples = self.samples[::2]

    def report(self, status: str) -> dict[str, Any]:
        limits = self.state["limits"] or {}
        budgets = {**limits, **(self.state["bounds"] or {}),
                   "vacuum_rule": "after maintenance, free pages under a quarter of the file or under 8 MiB",
                   "rss_ceiling_mib": self.args.rss_ceiling_mib, "rss_growth_mib": self.args.rss_growth_mib}
        out: dict[str, Any] = {
            "harness": "scripts/time_travel_soak.py", "qualifies": "TT-G3", "status": status,
            "updated_at": utc_stamp(), "seconds": round(time.monotonic() - self.started, 1), "cycles": self.cycles,
            "seed": self.args.seed, "data_dir": str(self.data_dir),
            "options": {k: v for k, v in vars(self.args).items() if k not in ("child", "report")},
            "budgets": budgets, "peaks": dict(self.peaks), "peaks_right_after_a_kill": dict(self.kill_peaks),
            # The run's other stores (checkpoints, transcripts, memory, audit):
            # what the harness leaves on disk, not a Time Travel budget.
            "other_stores_peak_bytes": dict(sorted(self.other_stores.items())),
            "end": dict(self.end),
            "captures": {**dict(self.capture_results), "latency_ms": self.capture.summary()},
            "missions": dict(self.missions), "kills": dict(self.kills), "chats": dict(self.chats),
            "operations": {k: {"n": self.ops[k].n, "outcomes": dict(self.outcomes.get(k) or {}),
                               "latency_ms": self.ops[k].summary()} for k in sorted(self.ops)},
            "faults": {k: {"injected": v["injected"], "endings": dict(v["endings"])}
                       for k, v in sorted(self.faults.items())},
            "maintenance": dict(self.maintenance),
            "cycle_seconds": self.child_seconds.summary(),
            "process_tree_now": process_tree_sample(),
            "samples": self.samples[-200:],
            "violations": self.violations[:200], "violation_count": len(self.violations),
            "healthy": not self.violations,
        }
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--duration", type=float, default=600.0, help="seconds to run (then one last cycle)")
    parser.add_argument("--cycle-seconds", type=float, default=10.0, help="pause between cycles")
    parser.add_argument("--max-cycles", type=int, default=0, help="stop after this many (0: by time)")
    parser.add_argument("--chats", type=int, default=8, help="chats kept in use on the graph")
    parser.add_argument("--chat-max-turns", type=int, default=24, help="a chat is deleted after this many turns")
    parser.add_argument("--turn-rate", type=float, default=0.7, help="share of the chats that take a turn a cycle")
    parser.add_argument("--pending-rate", type=float, default=0.2,
                        help="share of tool turns left waiting at their approval until the next cycle")
    parser.add_argument("--mission-iterations", type=int, default=2000)
    parser.add_argument("--mission-chunk-min", type=int, default=150, help="bytes of each mission tool result, least")
    parser.add_argument("--mission-chunk-max", type=int, default=600, help="... most")
    parser.add_argument("--mission-every", type=int, default=3, help="a mission turn every N cycles")
    parser.add_argument("--large-bytes", type=int, default=3 * _MiB, help="the large chats' tool result")
    parser.add_argument("--large-every", type=int, default=4)
    parser.add_argument("--aged-every", type=int, default=5)
    parser.add_argument("--retention-days", type=int, default=30,
                        help="the prune's retention (time_travel.retention_days; the product's default)")
    parser.add_argument("--maintain-every", type=int, default=4, help="maintenance every N cycles")
    parser.add_argument("--ops", type=int, default=8, help="user operations a cycle")
    parser.add_argument("--lock-rate", type=float, default=0.1, help="chance a cycle holds the write lock")
    parser.add_argument("--io-error-rate", type=float, default=0.1)
    parser.add_argument("--unreadable-rate", type=float, default=0.05)
    parser.add_argument("--refusal-rate", type=float, default=0.2,
                        help="chance of each refusal fault (running turn, cut step, waiting approval)")
    parser.add_argument("--kill-rate", type=float, default=0.3, help="chance a mission cycle is killed")
    parser.add_argument("--rss-ceiling-mib", type=float, default=384.0,
                        help="a cycle child's peak resident memory (its start is ~100 MiB of imports)")
    parser.add_argument("--rss-growth-mib", type=float, default=128.0,
                        help="a cycle child's peak over its own start: the states in flight that "
                             "tests/test_time_travel_bounds.py allows (6 steps of 2 MiB and 32 MiB), a large "
                             "state three times over, SQLite's page caches, twice over for the allocator")
    parser.add_argument("--cycle-timeout", type=float, default=1800.0)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None)
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        return _child(args.child)

    data_dir = Path(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="kazma-tt-soak-")) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    if (data_dir / "snapshots.db").exists():
        print(f"{data_dir} already holds a snapshot store: a soak starts from an empty one", file=sys.stderr)
        return 2
    run = _Run(args, data_dir)
    rng = random.Random(args.seed)
    cycle = 0
    while (time.monotonic() - run.started < args.duration
           and (not args.max_cycles or cycle < args.max_cycles)):
        cycle += 1
        problems = run.cycle(_plan(cycle, rng, args, run.state, run.soak_dir))
        run.violations += problems
        _write_report(args.report, run.report("running"))
        if problems:
            break
        time.sleep(max(0.0, args.cycle_seconds))
    if not run.violations:
        # One last cycle: a start, the checkpoints read back, a killed turn
        # continued, nothing new -- so a run that ended on a kill is checked
        # with no step in flight.
        run.violations += run.cycle(_plan(cycle + 1, rng, args, run.state, run.soak_dir, final=True))
    report = finished(run.report("running"), not run.violations)
    _write_report(args.report, report)
    print(json.dumps(report, indent=2, ensure_ascii=False, default=str))
    return 0 if report["healthy"] else 1


if __name__ == "__main__":
    sys.exit(main())
