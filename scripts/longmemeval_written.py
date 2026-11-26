#!/usr/bin/env python3
"""LongMemEval's memories written as Kazma writes them (the memory plan's W6,
step 5): the facts, then the weekly summaries and their roll-ups, then the
nightly merge, each at the question's time.

A chat turn's recall reads a memory the product wrote over weeks: every
round's episode and facts (the quick pass and the deep pass), a summary of
each topic once its week has ended, a roll-up of a month or a project once
its weekly summaries are there, and the merge the nightly sweep runs. The
harness wrote the episodes alone; ``longmemeval_facts_pilot.py extract`` (or
``longmemeval_replay.py``, from its kept replies) adds the facts. This script
adds the rest to a copy of each question's facts store, with the product's own
code and as of the question's time:

* the weeks due then (``topic_summaries._due_periods``: ended a day before
  the question, enough turns) are written by ``summarize_period``, the
  summaries task's own function, on the model named;
* the roll-ups the maintenance sweep plans then (``plan_rollups``) are
  written by ``rebuild_summary``, as the queue's task writes them;
* the nightly merge (``run_global_reconsolidation``, the reconsolidation
  cadence's numbers) runs once for each night the history spans, until a
  night merges less than its most.

Weeks and months are read in UTC, the benchmark's zone for the operator's
(recall's is too). The model's replies are kept by message
(``replies-<model>.jsonl``): a run that stops goes on and asks nothing twice,
and ``--stop-usd`` stops it at a spend. A question's ``done`` file says what
wrote each step (``written_with``); a question whose summaries could not all be
written has none, and ``longmemeval_bench.py answer --written`` refuses a run
with a store lacking a step::

    DASHSCOPE_API_KEY=... python scripts/longmemeval_written.py --data DIR --facts RUN --label LABEL \\
        --model qwen3.8-flash --base-url URL --prices HIT MISS OUT --stop-usd 2

Everything holding the dataset's text stays in ``DIR/cache/written/LABEL``,
never in the repository; the key is read from the variable named, never
printed.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import datetime as dt
import json
import math
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import longmemeval_facts_pilot as pilot  # noqa: E402

bench = pilot.bench

#: The steps a written store holds, in the order they are written.
STEPS = ("facts", "summaries", "merge")
#: The reconsolidation cadence's numbers (``worker_bootstrap``'s nightly
#: ``global_reconsolidation`` task).
NIGHTLY = {"max_merges": 50, "reembed_limit": 100}
#: Tries of one queued task before the question is left unwritten (the
#: product's queue retries it later; a run here goes on the next time).
TASK_TRIES = 3
#: Sweeps a question may take (each queues at most two weeks and a few
#: roll-ups, as the product's does).
SWEEPS = 60


def week_of_utc(ts: float) -> tuple[str, float, float]:
    """``topic_summaries._week_of`` with the week read in UTC."""
    year, week, _ = dt.datetime.fromtimestamp(float(ts), dt.UTC).date().isocalendar()
    start = dt.datetime.combine(dt.date.fromisocalendar(year, week, 1), dt.time(), tzinfo=dt.UTC)
    return f"{year}-W{week:02d}", start.timestamp(), (start + dt.timedelta(days=7)).timestamp()


def month_of_utc(ts: float) -> tuple[str, float, float]:
    """``topic_rollups._month_of`` with the month read in UTC."""
    day = dt.datetime.fromtimestamp(float(ts), dt.UTC).date()
    start = dt.datetime(day.year, day.month, 1, tzinfo=dt.UTC)
    end = dt.datetime(day.year + (day.month == 12), day.month % 12 + 1, 1, tzinfo=dt.UTC)
    return f"{day.year}-{day.month:02d}", start.timestamp(), end.timestamp()


def in_utc() -> None:
    """The product's weeks and months, read in UTC for this process."""
    from kazma_core.memory import topic_rollups, topic_summaries

    topic_summaries._week_of = week_of_utc  # type: ignore[assignment]
    topic_rollups._month_of = month_of_utc  # type: ignore[assignment]


#: The tasks the running question's sweep queued, run here in its order.
_QUEUED: contextvars.ContextVar[list[tuple[str, dict[str, Any]]] | None] = contextvars.ContextVar(
    "written_queue", default=None)


def route_queue() -> None:
    """The memory queue, for this process: a task the running question's
    sweep queues is kept for :func:`sweep` to run, never written to a queue."""
    from kazma_core.memory import task_queue

    if getattr(task_queue.enqueue_task, "keeps_for_the_bench", False):
        return
    real = task_queue.enqueue_task

    def enqueue_task(task_type: str, payload: dict[str, Any], **kw: Any) -> str | None:
        held = _QUEUED.get()
        if held is None:
            return real(task_type, payload, **kw)
        held.append((task_type, dict(payload)))
        return f"bench-{len(held)}"

    enqueue_task.keeps_for_the_bench = True  # type: ignore[attr-defined]
    task_queue.enqueue_task = enqueue_task  # type: ignore[assignment]


@contextlib.contextmanager
def as_the_active_model(model: Any) -> Any:
    """*model* as the install's active model, for the summaries' own calls
    (``topic_summaries._default_chat``, through the per-call ledger)."""
    from unittest import mock

    registry = type("Registry", (), {"active_model": model.config.model,
                                     "get_client": lambda self, *_a, **_k: model})()
    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        yield


def _handlers() -> dict[str, Any]:
    """The queue's own tasks for what the summaries sweep queues."""
    from kazma_core.memory import worker_bootstrap

    return {"topic_summaries": worker_bootstrap._handle_topic_summaries,
            "topic_summary_rebuild": worker_bootstrap._handle_topic_summary_rebuild}


def _open() -> sqlite3.Connection:
    from kazma_core.memory import topic_summaries

    conn = topic_summaries._open()
    if conn is None:
        raise RuntimeError("the question's store is missing")
    return conn


def due_weeks(now: float) -> list[tuple[str, float, float, str]]:
    """The weeks the summaries sweep would queue at *now*."""
    from kazma_core.memory import topic_summaries

    conn = _open()
    try:
        return topic_summaries._due_periods(conn, now, topic_summaries._config()["min_turns"])
    finally:
        conn.close()


def summary_counts() -> dict[str, int]:
    conn = _open()
    try:
        out = {"weekly": 0, "month": 0, "project": 0}
        for level, n in conn.execute("SELECT COALESCE(level, 'week'), COUNT(*) FROM memory_summaries "
                                     "WHERE status = 'active' GROUP BY 1"):
            out["weekly" if level == "week" else level] = int(n)
        return out
    finally:
        conn.close()


async def sweep(now: float, model: str) -> dict[str, Any] | None:
    """The summaries sweep at *now* (``topic_summaries.queue_due_work``: the
    weeks due, the roll-ups it plans) and the queue's tasks for what it
    queued, until a sweep queues nothing; None when a task failed past its
    tries or the sweeps never settled."""
    from kazma_core.memory.topic_summaries import queue_due_work

    handlers = _handlers()
    counts = {"model": model, "weeks": 0, "rollups": 0, "tasks": 0}
    for passes in range(1, SWEEPS + 1):
        queued: list[tuple[str, dict[str, Any]]] = []
        token = _QUEUED.set(queued)
        try:
            planned = await asyncio.to_thread(queue_due_work, now=now)
        finally:
            _QUEUED.reset(token)
        counts["weeks"] += int(planned.get("weeks", 0))
        counts["rollups"] += int(planned.get("rollups", 0))
        if not queued and not planned.get("rollups"):
            return {**counts, "sweeps": passes, "weeks_left": len(await asyncio.to_thread(due_weeks, now))}
        for task_type, payload in queued:
            for _ in range(TASK_TRIES):
                if await handlers[task_type](payload):
                    break
            else:
                return None
            counts["tasks"] += 1
    return None


def nightly_merge(db: Path, first: float, now: float) -> dict[str, Any]:
    """The nightly merge, once for each night from the history's first day
    to *now*, until a night merges less than its most."""
    from kazma_core.memory.global_reconsolidation import run_global_reconsolidation
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    nights = max(1, math.ceil((now - first) / 86400))
    out = {"nights_run": 0, "merged": 0, "embedded": 0, "errors": 0}
    conn = sqlite3.connect(str(db), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        for _ in range(nights):
            stats = run_global_reconsolidation(conn, tenant_id=bench.TENANT, **NIGHTLY)
            while stats.get("has_more") and stats.get("next_partition_index") is not None:
                more = run_global_reconsolidation(
                    conn, tenant_id=bench.TENANT, **NIGHTLY, auto_partition=False,
                    partition_index=int(stats["next_partition_index"]),
                    partition_count=int(stats.get("partition_count") or 1))
                stats = {**more, "duplicate_beliefs_merged": int(stats["duplicate_beliefs_merged"])
                         + int(more["duplicate_beliefs_merged"])}
            out["nights_run"] += 1
            out["merged"] += int(stats["duplicate_beliefs_merged"])
            out["embedded"] += int(stats["episodes_embedded"]) + int(stats["beliefs_embedded"])
            out["errors"] += int(stats["errors"])
            if int(stats["duplicate_beliefs_merged"]) < NIGHTLY["max_merges"]:
                break
    finally:
        conn.close()
    return out


def _copy(facts: Path, folder: Path, qid: str) -> tuple[Path, Path]:
    from kazma_core.db.sqlite_snapshot import snapshot_copy

    paths = []
    for name in (f"{qid}.db", f"{qid}.ops.db"):
        target = folder / "stores" / name
        for suffix in ("", "-wal", "-shm"):
            Path(str(target) + suffix).unlink(missing_ok=True)
        snapshot_copy(facts / "stores" / name, target, read_only=True)
        paths.append(target)
    return paths[0], paths[1]


def _first_day(db: Path) -> float:
    conn = sqlite3.connect(str(db))
    try:
        return float(conn.execute("SELECT MIN(created_at) FROM episodes").fetchone()[0] or 0)
    finally:
        conn.close()


async def write_question(item: dict[str, Any], facts: Path, folder: Path, model: str,
                         facts_with: dict[str, Any]) -> dict[str, Any] | None:
    """*item*'s facts store, copied, with its summaries, roll-ups and nightly
    merge as of the question's time; what wrote each step, or None when a
    model call failed past its tries (the question is written again on the
    next run)."""
    qid = str(item["question_id"])
    db, ops = await asyncio.to_thread(_copy, facts, folder, qid)
    pilot._STORE.set((str(db), str(ops)))
    now = bench.parse_date(item["question_date"])
    summaries = await sweep(now, model)
    if summaries is None:
        return None
    merge = await asyncio.to_thread(nightly_merge, db, await asyncio.to_thread(_first_day, db), now)
    counts = await asyncio.to_thread(summary_counts)
    return {**counts, "merged": merge["merged"],
            "written_with": {"episodes": "the harness's rounds", "facts": facts_with,
                             "summaries": summaries, "merge": merge,
                             "at": str(item["question_date"])}}


def facts_written_with(facts: Path, qid: str) -> dict[str, Any] | None:
    """What wrote a question's facts (its ``done`` file), or None when the
    facts run did not finish it."""
    done = facts / "done" / f"{qid}.json"
    if not done.is_file():
        return None
    held = json.loads(done.read_text(encoding="utf-8"))
    return {"run": facts.name, **(held.get("written_with") or {"facts": "extracted"}),
            "deep": int(held.get("deep", 0)), "deep_failed": int(held.get("deep_failed", 0)),
            "no_reply": int(held.get("no_reply", 0))}


def written_folder(data: Path, label: str) -> Path:
    return data / "cache" / "written" / label


async def _write(items: list[dict[str, Any]], facts: Path, folder: Path, model: Any, workers: int) -> dict[str, int]:
    """Every question of *items* written (a store already done is kept), on
    *model* as the active model."""
    pilot._route_memory_paths()
    route_queue()
    in_utc()
    gate = asyncio.Semaphore(workers)
    out = {"written": 0, "held": 0, "unwritten": 0}

    async def one(item: dict[str, Any]) -> None:
        qid = str(item["question_id"])
        done = folder / "done" / f"{qid}.json"
        if done.is_file():
            out["held"] += 1
            return
        facts_with = facts_written_with(facts, qid)
        if facts_with is None:
            out["unwritten"] += 1
            print(f"  {qid}: its facts were not written ({facts.name})", flush=True)
            return
        async with gate:
            stats = await write_question(item, facts, folder, model.config.model, facts_with)
        if stats is None:
            out["unwritten"] += 1
            print(f"  {qid}: a summary's model call failed; written again on the next run", flush=True)
            return
        done.write_text(json.dumps(stats, ensure_ascii=False) + "\n", encoding="utf-8")
        out["written"] += 1
        print(f"  {qid}: {({k: v for k, v in stats.items() if k != 'written_with'})}", flush=True)

    with as_the_active_model(model):
        await asyncio.gather(*(one(item) for item in items))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, type=Path, help="the folder LongMemEval was downloaded into")
    p.add_argument("--facts", required=True, help="the facts run its stores are copied from (facts-pilot/RUN)")
    p.add_argument("--label", required=True, help="the written run's name (cache/written/LABEL)")
    p.add_argument("--questions", choices=("pilot", "dev"), default="pilot")
    p.add_argument("--model", default="qwen3.8-flash")
    p.add_argument("--base-url", required=True)
    p.add_argument("--key-env", default="DASHSCOPE_API_KEY")
    p.add_argument("--workers", type=int, default=8, help="questions written at once")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--prices", type=float, nargs=3, metavar=("HIT", "MISS", "OUT"),
                   help="USD per million tokens: cache hit, cache miss, output")
    p.add_argument("--stop-usd", type=float, help="stop once the replies have cost this (needs --prices)")
    args = p.parse_args(argv)
    if args.stop_usd is not None and not args.prices:
        p.error("--stop-usd needs --prices")
    key = os.environ.get(args.key_env, "")
    if not key:
        print(f"set {args.key_env}", file=sys.stderr)
        return 2
    facts = args.data / "cache" / "facts-pilot" / args.facts
    if not (facts / "stores").is_dir():
        print(f"{facts}: no stores", file=sys.stderr)
        return 2
    bench._private_data_folder()
    bench._limit_cpu_threads()
    from unittest import mock

    from kazma_core.llm_provider import LLMConfig, LLMProvider

    folder = written_folder(args.data, args.label)
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    by_id = {str(x["question_id"]): x for x in bench.load(args.data)}
    items = [by_id[q] for q in pilot.pilot_ids(args.questions)[: args.limit or None]]
    spend = pilot.Spend(tuple(args.prices) if args.prices else None, args.stop_usd)
    model = pilot.CachedModel(LLMProvider(LLMConfig(base_url=args.base_url, api_key=key, model=args.model)),
                              folder / f"replies-{args.model}.jsonl", spend)
    embedder = pilot.PilotEmbedder(*_embedder_parts(args.data), folder / "vectors-extra.npz")
    print(f"{len(items)} questions from {facts.name}, summaries on {args.model}; {len(model.replies)} replies held, "
          f"${spend.usd:.2f} spent", flush=True)
    try:
        with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder):
            counts = asyncio.run(_write(items, facts, folder, model, args.workers))
    except pilot.SpendLimitError as exc:
        print(f"stopped at the spend limit: {exc}; usage this run {model.usage}", file=sys.stderr)
        return 3
    finally:
        embedder.save()
    (folder / "usage.json").write_text(json.dumps({"model": args.model, "this_run": model.usage, **counts,
                                                   "usd_all_runs": round(spend.usd, 4)}, indent=1) + "\n",
                                       encoding="utf-8")
    print("questions", counts, "usage this run", model.usage, f"${spend.usd:.4f} all runs")
    return 0 if not counts["unwritten"] else 1


def _embedder_parts(data: Path) -> tuple[Any, Path, str]:
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    return get_embedder(), data / "cache" / "kazma_vectors.npz", get_embedding_model_name()


if __name__ == "__main__":
    sys.exit(main())
