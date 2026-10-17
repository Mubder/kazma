#!/usr/bin/env python3
"""Soak of the memory lifecycle, with restarts, crashes and restores.

Memory audit Stage 4 asks for a multi-day soak with restarts; this is the
harness for it, not the result. It runs the product's own memory code on
PRIVATE stores (a temporary data directory unless ``--data-dir`` names one;
never an install's), one cycle per child process -- every cycle is a
restart -- and kills some children partway (a crash). A cycle:

* starts as Kazma does: forget convergence, then turn reconcile;
* saves new chat turns in the chat store (English and Arabic) and writes
  most of them as the live path does (``dual_write.write_episode`` and the
  quick fact pass); the rest are left to turn reconcile, like a turn whose
  live write was lost;
* forgets a few memories, and now and then keeps a whole chat out;
* runs the maintenance passes: forget convergence, turn reconcile, macro
  sleep (archival: an archived memory must still be found);
* takes the native memory backup every ``--backup-every`` cycles, and
  restores an older one every ``--restore-every`` cycles
  (``memory.backup.restore_memory_backup``);
* asks recall for a few known memories and times it.

After every cycle that was not killed, the parent checks, from the stores:
every settled chat turn is a memory unless it was forgotten or its chat
kept out; every forgotten turn has no text and no vector and recall does
not find it; both memory databases pass ``PRAGMA integrity_check``; and
recall finds what it was asked for. The run FAILS (exit 1) on any
violation. The report (``--report``, JSON) holds the cycles, crashes,
restores, every violation, recall latency p50/p95/max, database sizes and
the child's peak memory.

The model's deep pass is not part of it (it needs a model): the queue is
not drained. Meaning search is off (no embedder): recall is by words.

Usage:
  python scripts/memory_soak.py --duration 259200 --report soak.json
  python scripts/memory_soak.py --duration 300 --cycle-seconds 2
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))  # the shared report helpers sit beside this file
from soak_report import percentile as _percentile  # noqa: E402
from soak_report import utc_stamp  # noqa: E402
from soak_report import write_report as _write_report  # noqa: E402

_ROOT = _HERE.parent
for _pkg in ("kazma-core", "kazma-ui"):
    _p = str(_ROOT / _pkg)
    if _p not in sys.path:
        sys.path.insert(0, _p)

_KILLED = 137
#: Arabic and English turn texts; each turn gets its own token so recall can
#: be asked for exactly one memory.
_TOPICS = [
    ("en", "Where did I park the {t} bike?", "Level two, bay {n}."),
    ("en", "What did we decide about the {t} budget?", "To keep it at {n} thousand."),
    ("ar", "وين حطيت مفاتيح {t}؟", "في الدرج رقم {n}."),
    ("ar", "شنو قررنا عن مشروع {t}؟", "نكمل بعد {n} أيام."),
    # Statements the quick fact pass keeps (a single-valued fact, superseded
    # each time: the facts and their forgets are exercised too).
    ("en", "I live in {T} now.", "Noted, {T}."),
    ("ar", "ساكن في {t} حاليا", "تمام."),
]


def _token(rng: random.Random) -> str:
    return "zq" + "".join(rng.choice("bcdfghjklmnpqrstvwx") for _ in range(8))


def _env(data_dir: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("KAZMA_") and k not in ("DATABASE_URL",)}
    env.update({
        "KAZMA_DATA_DIR": str(data_dir),
        "KAZMA_MEMORY_STATE_DB": str(data_dir / "memory_state.db"),
        "KAZMA_MEMORY_OPS_DB": str(data_dir / "memory_ops.db"),
        "KAZMA_BACKUPS_DIR": str(data_dir / "backups"),
        # Nothing of the user's: the home folders a Kazma process reads.
        "KAZMA_USER_HOME": str(data_dir / "home"),
        "KAZMA_SKILLS_HOME": str(data_dir / "home"),
        "KAZMA_DB_BACKEND": "sqlite",
        "KAZMA_PROCESS_PRIORITY": "keep",
        "PYTHONIOENCODING": "utf-8",
        # A native crash in a child still names the Python stack that ran it.
        "PYTHONFAULTHANDLER": "1",
    })
    return env


#: How long a child keeps rebuilding after a restore before the soak calls it
#: unfinished (a violation of its own, never "turns lost").
_REBUILD_PASSES = 40
_REBUILD_DEADLINE_S = 900.0


def _finish_rebuild(first: dict[str, Any], run_pass: Any, *, max_passes: int = _REBUILD_PASSES,
                    deadline_s: float = _REBUILD_DEADLINE_S) -> dict[str, Any]:
    """Run turn reconcile until a restore's rebuild is done.

    ``restore_memory_backup`` rebuilds within its own budget and says
    ``more`` when turns are left for the next pass -- in the product the
    15-minute cadence takes them. The soak checks right after the cycle,
    so it finishes the rebuild first and records how long it took (the
    restore's recovery time): on 2026-10-16 a loaded machine left 799
    turns for the next pass and the check called them lost.
    """
    t0 = time.monotonic()
    passes, written, more = 0, 0, bool(first.get("more"))
    while more and passes < max_passes and time.monotonic() - t0 < deadline_s:
        res = run_pass() or {}
        passes += 1
        written += int(res.get("turns_written") or 0)
        more = not res.get("done", True)
    return {"passes": passes, "turns_written": written, "seconds": round(time.monotonic() - t0, 2),
            "finished": not more}


# ── the child: one cycle ─────────────────────────────────────────────────


def _child(plan: dict[str, Any]) -> dict[str, Any]:
    """One cycle, in its own process. Prints its result as one JSON line."""
    from kazma_core.memory import dual_write, embedder, forget, task_queue, turn_reconcile
    from kazma_core.memory.backup import perform_native_backups, restore_memory_backup
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
    from kazma_core.memory.macro_sleep import run_macro_sleep
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema
    from kazma_core.paths import memory_ops_db, primary_memory_db
    from kazma_ui.session_manager import ChatSession, SessionManager

    embedder.get_embedder = lambda: None  # words only: no model in a soak
    embedder.encode_text_to_blob = lambda text: None
    task_queue.enqueue_task = lambda *a, **k: "soak"  # the deep pass is a model's
    turn_reconcile._SETTLE_S = 0.0  # a soak's turns are settled when saved
    rng = random.Random(plan["seed"])
    crash_at = plan.get("crash_at")
    out: dict[str, Any] = {"cycle": plan["cycle"], "steps": [], "recall_ms": []}

    def step(name: str) -> None:
        out["steps"].append(name)
        if crash_at == name:
            sys.stdout.flush()
            os._exit(_KILLED)

    for path, ensure in ((primary_memory_db(), ensure_primary_schema), (memory_ops_db(), ensure_ops_schema)):
        conn = sqlite3.connect(path)
        try:
            ensure(conn)
        finally:
            conn.close()
    step("boot")
    forget.converge()
    turn_reconcile.run_turn_reconcile_pass(time_budget_s=120)

    store = SessionManager(db_path=str(Path(os.environ["KAZMA_DATA_DIR"]) / "chat_sessions.db"))
    try:
        for chat in plan["chats"]:
            session = store.get(chat["id"])
            messages = list(getattr(session, "messages", None) or [])
            # Numbered from the chat as stored: a killed cycle saved nothing.
            base = sum(1 for m in messages if m.get("role") == "user")
            for k, t in enumerate(chat["turns"], 1):
                t["turn"] = base + k
                messages += [{"role": "user", "content": t["q"], "ts": t["ts"]},
                             {"role": "assistant", "content": t["a"]}]
            store.put(ChatSession(session_id=chat["id"], tenant_id="default", thread_id="",
                                  messages=messages))
            step("saved")
            for t in chat["turns"]:
                if not t["live"]:
                    continue  # its live write was lost: turn reconcile's
                dual_write.write_episode(session_id=chat["id"], turn_number=t["turn"], user_text=t["q"],
                                         assistant_text=t["a"], tenant_id="default",
                                         created_at=t["at"])
                primary = sqlite3.connect(primary_memory_db(), check_same_thread=False)
                ops = sqlite3.connect(memory_ops_db(), check_same_thread=False)
                try:
                    extract_and_apply_beliefs_sync(primary, ops, t["q"], t["a"], session_id=chat["id"],
                                                   turn=t["turn"], now=t["at"])
                finally:
                    primary.close()
                    ops.close()
                step("live")
    finally:
        store.close()

    conn = sqlite3.connect(primary_memory_db())
    conn.row_factory = sqlite3.Row
    try:
        live = [r["id"] for r in conn.execute(
            "SELECT id FROM episodes WHERE tier != 'forgotten' AND session_id LIKE 'soak-%'")]
    finally:
        conn.close()
    for eid in rng.sample(live, min(len(live), plan["forgets"])):
        forget.forget_episode(eid, tenant_id="default")
        step("forget")
    if plan.get("keep_out"):
        forget.set_chat_remembered(plan["keep_out"], False, tenant_id="default")
        step("keep_out")

    step("maintenance")
    forget.converge()
    turn_reconcile.run_turn_reconcile_pass(time_budget_s=120)
    conn = sqlite3.connect(primary_memory_db())
    try:
        run_macro_sleep(conn, now=time.time())
    finally:
        conn.close()
    if plan.get("backup"):
        out["backup"] = [p.name for p in perform_native_backups(retention=50)]
        step("backup")
    if plan.get("restore"):
        out["restore"] = restore_memory_backup(Path(plan["restore"]))
        out["restore"]["rebuild"] = _finish_rebuild(
            out["restore"], lambda: turn_reconcile.run_turn_reconcile_pass(time_budget_s=120))
        step("restore")

    for q in plan["ask"]:
        t0 = time.perf_counter()
        found = recall(q["token"], tenant_id="default", local_only=True)
        out["recall_ms"].append(round((time.perf_counter() - t0) * 1000, 2))
        out.setdefault("found", {})[q["token"]] = any(q["token"] in e.content.casefold() for e in found.episodes)
    step("done")
    try:
        import resource  # POSIX

        out["peak_rss_mb"] = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1)
    except ImportError:
        out["peak_rss_mb"] = None
    return out


# ── the parent: the plan, the checks ─────────────────────────────────────


def _settled_turns(data_dir: Path) -> list[tuple[str, int, str]]:
    """(chat, turn number, question) of every user turn the chat store holds."""
    db = data_dir / "chat_sessions.db"
    if not db.is_file():
        return []
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = []
        for sid, messages in conn.execute("SELECT session_id, messages FROM sessions"):
            turn = 0
            for m in json.loads(messages or "[]"):
                if m.get("role") == "user":
                    turn += 1
                    out.append((str(sid), turn, str(m.get("content") or "")))
        return out
    finally:
        conn.close()


def _check(data_dir: Path, asked: dict[str, bool]) -> list[str]:
    """The invariants, read from the stores. Returns the violations."""
    from kazma_core.memory.forget import question_sha

    problems: list[str] = []
    state = data_dir / "memory_state.db"
    for path in (state, data_dir / "memory_ops.db"):
        conn = sqlite3.connect(str(path))
        try:
            verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        if verdict != "ok":
            problems.append(f"integrity: {path.name}: {verdict}")
    conn = sqlite3.connect(str(state))
    conn.row_factory = sqlite3.Row
    try:
        held = {}
        for r in conn.execute("SELECT id, session_id, user_text, tier, embedding FROM episodes"):
            held.setdefault((r["session_id"], r["user_text"]), []).append(r)
        ledger = {(r["session_key"], int(r["turn_number"]), r["question_sha"])
                  for r in conn.execute("SELECT session_key, turn_number, question_sha FROM memory_forgotten")}
        tombstones = conn.execute(
            "SELECT id, user_text, assistant_text, summary_text, embedding FROM episodes WHERE tier = 'forgotten'"
        ).fetchall()
    finally:
        conn.close()
    kept_out = {key for key, turn, sha in ledger if turn == 0 and not sha}
    for sid, turn, question in _settled_turns(data_dir):
        if sid in kept_out or (sid, turn, question_sha(question)) in ledger:
            continue
        rows = [r for r in held.get((sid, question), []) if r["tier"] != "forgotten"]
        if not rows:
            problems.append(f"lost: {sid} turn {turn} is in the chat store and not a memory")
    for r in tombstones:
        if r["user_text"] or r["assistant_text"] or r["summary_text"] or r["embedding"]:
            problems.append(f"forget incomplete: {r['id']} keeps text or a vector")
    for token, ok in asked.items():
        if not ok:
            problems.append(f"recall: {token} not found")
    return problems


def _size_mb(path: Path) -> float:
    total = sum(p.stat().st_size for p in path.parent.glob(path.name + "*") if p.is_file())
    return round(total / 1048576, 2)


def _plan(cycle: int, rng: random.Random, args: argparse.Namespace, chats: dict[str, int],
          memory: list[dict[str, Any]], backups: list[Path]) -> dict[str, Any]:
    now = time.time()
    plan: dict[str, Any] = {"cycle": cycle, "seed": rng.randrange(1 << 30), "chats": [],
                            "forgets": rng.randint(0, 2), "ask": []}
    for _ in range(rng.randint(1, 2)):
        if chats and rng.random() < 0.6:
            cid = rng.choice(sorted(chats))
        else:
            cid = f"soak-{cycle}-{rng.randrange(1 << 16):x}"
            chats[cid] = 0
        turns = []
        for _ in range(rng.randint(1, args.turns_per_chat)):
            chats[cid] += 1
            lang, q, a = rng.choice(_TOPICS)
            token = _token(rng)
            at = now - rng.uniform(0, 90) * 86400  # some old enough to archive
            fill = {"t": token, "T": token.capitalize(), "n": rng.randint(1, 99)}
            turn = {"q": q.format(**fill), "a": a.format(**fill),
                    "at": at, "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(at)),
                    "live": rng.random() < 0.8, "token": token, "chat": cid}
            turns.append(turn)
            memory.append(turn)
        plan["chats"].append({"id": cid, "turns": turns})
    if rng.random() < args.keep_out_rate and len(chats) > 2:
        plan["keep_out"] = rng.choice(sorted(chats))
    plan["backup"] = cycle % args.backup_every == 0
    if backups and cycle % args.restore_every == 0:
        plan["restore"] = str(rng.choice(backups))
    if rng.random() < args.crash_rate:
        plan["crash_at"] = rng.choice(["boot", "saved", "live", "forget", "maintenance", "backup"])
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--duration", type=float, default=600.0, help="seconds to run")
    parser.add_argument("--cycle-seconds", type=float, default=30.0, help="pause between cycles")
    parser.add_argument("--max-cycles", type=int, default=0, help="stop after this many (0: by time)")
    parser.add_argument("--turns-per-chat", type=int, default=4)
    parser.add_argument("--backup-every", type=int, default=6)
    parser.add_argument("--restore-every", type=int, default=15)
    parser.add_argument("--crash-rate", type=float, default=0.15)
    parser.add_argument("--keep-out-rate", type=float, default=0.03)
    parser.add_argument("--seed", type=int, default=20261014)
    parser.add_argument("--data-dir", default=None, help="default: a private temporary directory")
    parser.add_argument("--report", default=None)
    parser.add_argument("--child", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.child:
        result = _child(json.loads(Path(args.child).read_text(encoding="utf-8")))
        print("SOAK-RESULT " + json.dumps(result, ensure_ascii=False))
        return 0

    work = Path(tempfile.mkdtemp(prefix="kazma-memory-soak-"))
    data_dir = Path(args.data_dir) if args.data_dir else work / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.update(_env(data_dir))  # the parent's own reads (question_sha) are pure
    rng = random.Random(args.seed)
    chats: dict[str, int] = {}
    memory: list[dict[str, Any]] = []
    backups: list[Path] = []
    report: dict[str, Any] = {"cycles": 0, "crashes": 0, "restores": 0, "backups": 0,
                              "violations": [], "recall_ms": [], "child_seconds": [],
                              "peak_rss_mb": [], "rebuild_seconds": [], "data_dir": str(data_dir)}
    started = time.monotonic()
    cycle = 0
    while time.monotonic() - started < args.duration and (not args.max_cycles or cycle < args.max_cycles):
        cycle += 1
        plan = _plan(cycle, rng, args, chats, memory, backups)
        survivors = [m for m in memory if m["chat"] != plan.get("keep_out")]
        plan["ask"] = [{"token": m["token"]} for m in rng.sample(survivors, min(3, len(survivors)))]
        plan_file = work / f"plan-{cycle}.json"
        plan_file.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        t0 = time.monotonic()
        proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(plan_file)],
                              env=_env(data_dir), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=900 + _REBUILD_DEADLINE_S, check=False)
        plan_file.unlink(missing_ok=True)  # thousands of cycles in a multi-day run
        report["child_seconds"].append(round(time.monotonic() - t0, 3))
        report["cycles"] = cycle
        if proc.returncode == _KILLED:
            report["crashes"] += 1
            _write_report(args.report, _summary(report, started, memory, data_dir, "running"))
            continue
        line = next((x for x in proc.stdout.splitlines() if x.startswith("SOAK-RESULT ")), None)
        if proc.returncode != 0 or line is None:
            report["violations"].append(f"cycle {cycle}: the child failed ({proc.returncode}): "
                                        f"{(proc.stderr or '')[-1500:]}")
            break
        result = json.loads(line[len("SOAK-RESULT "):])
        backups += [data_dir / "backups" / name for name in result.get("backup", [])
                    if name.startswith("memory_state_")]
        report["backups"] += bool(result.get("backup"))
        report["restores"] += bool(result.get("restore"))
        rebuild = (result.get("restore") or {}).get("rebuild")
        if rebuild:
            report["rebuild_seconds"].append(rebuild["seconds"])
            if not rebuild["finished"]:
                report["violations"].append(
                    f"cycle {cycle}: the rebuild after a restore did not finish in {rebuild['passes']} "
                    f"passes ({rebuild['seconds']} s)")
                _write_report(args.report, _summary(report, started, memory, data_dir, "running"))
                break
        report["recall_ms"] += result["recall_ms"]
        if result.get("peak_rss_mb"):
            report["peak_rss_mb"].append(result["peak_rss_mb"])
        # A memory forgotten in this cycle is not asked for afterwards.
        problems = _check(data_dir, {
            t: ok for t, ok in (result.get("found") or {}).items() if not _forgotten(data_dir, t)})
        report["violations"] += [f"cycle {cycle}: {p}" for p in problems]
        _write_report(args.report, _summary(report, started, memory, data_dir, "running"))
        if problems:
            break
        time.sleep(max(0.0, args.cycle_seconds))
    # A last clean cycle (a start, nothing new), then the checks: a run that
    # ended on a killed cycle is checked too.
    plan_file = work / "plan-final.json"
    plan_file.write_text(json.dumps({"cycle": cycle + 1, "seed": 0, "chats": [], "forgets": 0, "ask": []}),
                         encoding="utf-8")
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", str(plan_file)],
                          env=_env(data_dir), capture_output=True, text=True, encoding="utf-8",
                          errors="replace", timeout=900, check=False)
    if proc.returncode != 0:
        report["violations"].append(f"final cycle failed ({proc.returncode}): {(proc.stderr or '')[-1500:]}")
    else:
        report["violations"] += [f"final: {p}" for p in _check(data_dir, {})]
    report["held"] = _held(data_dir)
    summary = _summary(report, started, memory, data_dir, "passed" if not report["violations"] else "failed")
    _write_report(args.report, summary)
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if summary["healthy"] else 1


def _summary(report: dict[str, Any], started: float, memory: list[dict[str, Any]], data_dir: Path,
             status: str) -> dict[str, Any]:
    """The report as it stands: ``running`` while the soak goes on, then
    ``passed`` or ``failed``. Written after every cycle, so a run that a
    reboot or a kill ends early still leaves its evidence."""
    lat, secs, rss = report["recall_ms"], report["child_seconds"], report["peak_rss_mb"]
    rebuilt = report.get("rebuild_seconds") or []
    out = {k: v for k, v in report.items()
           if k not in ("recall_ms", "child_seconds", "peak_rss_mb", "rebuild_seconds")}
    out.update({
        "status": status,
        "updated_at": utc_stamp(),
        "seconds": round(time.monotonic() - started, 1),
        "turns": len(memory),
        "recall_ms": {"p50": _percentile(lat, 0.5), "p95": _percentile(lat, 0.95),
                      "max": max(lat) if lat else None, "n": len(lat)},
        "cycle_seconds": {"p50": statistics.median(secs) if secs else None, "max": max(secs) if secs else None},
        "peak_rss_mb": max(rss) if rss else None,
        "restore_rebuild_seconds": {"p50": statistics.median(rebuilt) if rebuilt else None,
                                    "max": max(rebuilt) if rebuilt else None, "n": len(rebuilt)},
        "memory_state_mb": _size_mb(data_dir / "memory_state.db"),
        "memory_ops_mb": _size_mb(data_dir / "memory_ops.db"),
        "healthy": not report["violations"],
    })
    return out


def _held(data_dir: Path) -> dict[str, Any]:
    """What the stores hold at the end: a run that did nothing passes nothing."""
    conn = sqlite3.connect(str(data_dir / "memory_state.db"))
    try:
        tiers = dict(conn.execute("SELECT tier, COUNT(*) FROM episodes GROUP BY tier").fetchall())
        facts = conn.execute("SELECT COUNT(*), SUM(valid_until IS NULL AND invalidated_at IS NULL) "
                             "FROM beliefs").fetchone()
        ledger = conn.execute("SELECT COUNT(*), COALESCE(SUM(turn_number = 0), 0) FROM memory_forgotten").fetchone()
    finally:
        conn.close()
    return {"episodes_by_tier": tiers, "facts": facts[0], "current_facts": facts[1] or 0,
            "forgotten_turns": ledger[0] - ledger[1], "chats_kept_out": ledger[1],
            "chat_turns": len(_settled_turns(data_dir))}


def _forgotten(data_dir: Path, token: str) -> bool:
    """Whether the memory holding *token* was forgotten (its chat kept out,
    or the turn itself): then recall must NOT find it."""
    conn = sqlite3.connect(str(data_dir / "memory_state.db"))
    try:
        row = conn.execute("SELECT COUNT(*) FROM episodes WHERE user_text LIKE ? AND tier != 'forgotten'",
                           (f"%{token}%",)).fetchone()
        return row[0] == 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
