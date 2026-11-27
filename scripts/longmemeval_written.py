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


def in_utc() -> None:
    """Memory's days, for this process, in UTC: the benchmark's zone for the
    operator's. The product reads weeks and months in the operator's zone
    (``topic_summaries.memory_zone``), as recall reads days."""
    import kazma_core.memory.recall as recall

    recall._memory_zone = lambda: dt.UTC  # type: ignore[assignment]


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


async def sweep(now: float, model: str, sweeps: int = SWEEPS) -> dict[str, Any] | str:
    """The summaries sweep at *now* (``topic_summaries.queue_due_work``: the
    weeks due, the roll-ups it plans) and the queue's tasks for what it
    queued, until a sweep queues nothing; why not, when a task failed past
    its tries or *sweeps* sweeps did not settle it (the step 5 review, L5:
    the two were one message, and a rerun from a fresh copy can only settle
    the second with more sweeps)."""
    from kazma_core.memory.topic_summaries import queue_due_work

    handlers = _handlers()
    counts = {"model": model, "weeks": 0, "rollups": 0, "tasks": 0}
    for passes in range(1, sweeps + 1):
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
                return f"a {task_type} task's model call failed {TASK_TRIES} times; written again on the next run"
            counts["tasks"] += 1
    return f"{sweeps} sweep{'s' if sweeps != 1 else ''} did not settle its summaries; run again with a larger --sweeps"


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
                         facts_with: dict[str, Any], sweeps: int = SWEEPS) -> dict[str, Any] | str:
    """*item*'s facts store, copied, with its summaries, roll-ups and nightly
    merge as of the question's time; what wrote each step, or why it was not
    written (:func:`sweep`)."""
    qid = str(item["question_id"])
    db, ops = await asyncio.to_thread(_copy, facts, folder, qid)
    pilot._STORE.set((str(db), str(ops)))
    now = bench.parse_date(item["question_date"])
    summaries = await sweep(now, model, sweeps)
    if isinstance(summaries, str):
        return summaries
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


async def _write(items: list[dict[str, Any]], facts: Path, folder: Path, model: Any, workers: int,
                 sweeps: int = SWEEPS) -> dict[str, int]:
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
            stats = await write_question(item, facts, folder, model.config.model, facts_with, sweeps)
        if isinstance(stats, str):
            out["unwritten"] += 1
            print(f"  {qid}: {stats}", flush=True)
            return
        done.write_text(json.dumps(stats, ensure_ascii=False) + "\n", encoding="utf-8")
        out["written"] += 1
        print(f"  {qid}: {({k: v for k, v in stats.items() if k != 'written_with'})}", flush=True)

    with as_the_active_model(model):
        await asyncio.gather(*(one(item) for item in items))
    return out


def _lines_under(block: str, header: str) -> int:
    """How many lines the block shows under *header* ("## Known Facts")."""
    if header not in block:
        return 0
    body = block.split(header, 1)[1].split("\n\n", 1)[0]
    return sum(1 for line in body.splitlines() if line.startswith("- "))


def compare_blocks(data: Path, facts: Path, folder: Path, ids: list[str]) -> list[dict[str, Any]]:
    """Each question's block on its written store, on its facts store alone and
    on its episodes alone: what each shows (memories, the lines under each
    header, tokens) and whether the answer's words are in it. Ids and counts
    only."""
    from unittest import mock

    items = {str(x["question_id"]): x for x in bench.load(data)}
    embedder = pilot.PilotEmbedder(*_embedder_parts(data), folder / "vectors-extra.npz")
    embedder.vectors.update(pilot.PilotEmbedder(None, data / "cache" / "none.npz", embedder.model,
                                                facts / "vectors-extra.npz").added)
    rows = []
    with (mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder),
          mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0)):
        for qid in ids:
            item = items[qid]
            history, words = bench.history_rows(item), bench.answer_words(item)
            row: dict[str, Any] = {"id": qid, "type": str(item["question_type"])}
            blocks = {}
            for name, store in (("written", folder / "stores" / f"{qid}.db"),
                                ("facts", facts / "stores" / f"{qid}.db"), ("episodes", None)):
                block, _whole, shown = bench.kazma_history(item, history, store)
                blocks[name] = block
                row[name] = {"tokens": bench.shown_tokens(block), **shown,
                             "fact_lines": _lines_under(block, "## Known Facts"),
                             "summary_lines": _lines_under(block, "## Weekly Summaries")
                             + _lines_under(block, "## Summaries\n"),
                             "answer_in_block": bench.holds_answer(block, words) if words else None}
            row["changed"] = blocks["written"].strip() != blocks["facts"].strip()
            row["fact_lines_changed"] = (_fact_lines(blocks["written"]) != _fact_lines(blocks["facts"]))
            rows.append(row)
    embedder.save()
    return rows


def _fact_lines(block: str) -> list[str]:
    if "## Known Facts" not in block:
        return []
    return [line for line in block.split("## Known Facts", 1)[1].split("\n\n", 1)[0].splitlines()
            if line.startswith("- ")]


def _replies(folder: Path, model: str | None) -> int | None:
    """The model replies the run holds (one a line), resumed runs included."""
    path = folder / f"replies-{model}.jsonl"
    if not model or not path.is_file():
        return None
    with path.open(encoding="utf-8") as f:
        return sum(1 for line in f if line.strip())


def write_evidence(data: Path, facts: Path, label: str, out: Path, questions: str = "pilot",
                   answers: list[str] | None = None, answers_note: str = "") -> dict[str, Any]:
    """The written run's evidence file (``docs/audits/evidence/memory_ingestion``):
    per question the stores' counts, the sweep's, and each block's
    (:func:`compare_blocks`). It holds ids, counts and booleans only."""
    folder = written_folder(data, label)
    ids = pilot.pilot_ids(questions)
    blocks = {r["id"]: r for r in compare_blocks(data, facts, folder, ids)}
    usage = json.loads((folder / "usage.json").read_text(encoding="utf-8")) if (folder / "usage.json").is_file() else {}
    rows = []
    for qid in ids:
        done = json.loads((folder / "done" / f"{qid}.json").read_text(encoding="utf-8"))
        held = json.loads((facts / "done" / f"{qid}.json").read_text(encoding="utf-8"))
        steps = done["written_with"]
        rows.append({"id": qid, "type": blocks[qid]["type"], "changed": blocks[qid]["changed"],
                     "fact_lines_changed": blocks[qid]["fact_lines_changed"],
                     # null where the facts run did not count it (the replay
                     # counts cross-name matches since the step 5 review's L1)
                     "facts_store": {k: held.get(k) for k in ("deep", "no_reply", "deep_failed", "facts", "current",
                                                              "dated_events", "refs_added", "refs_lost",
                                                              "refs_cross_name")},
                     "written_store": {k: done.get(k, 0) for k in ("weekly", "month", "project", "merged")},
                     "sweep": {k: steps["summaries"].get(k, 0) for k in ("weeks", "rollups", "tasks", "weeks_left")},
                     "blocks": {n: blocks[qid][n] for n in ("written", "facts", "episodes")}})
    evidence = {
        "what": "The memory plan's W6 step 5: each question's memory as Kazma writes it (written), the facts "
                "store alone (facts) and the episodes alone (episodes); per question the stores' counts, the "
                "summaries sweep's, and each block's memories, lines, tokens and whether the answer's words are "
                "in it. Ids and counts only. Made by scripts/longmemeval_written.py --evidence.",
        "facts": f"facts run {facts.name}", "written": label,
        # every reply the summaries were written from (resumed runs
        # included), and what they cost, all runs (the delta review's D)
        "summaries": {"model": usage.get("model"), "calls": _replies(folder, usage.get("model")),
                      "usd_all_runs": usage.get("usd_all_runs")},
        **({"answers": list(answers), "answers_note": answers_note} if answers else {}),
        "questions": rows,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, indent=1) + "\n", encoding="utf-8", newline="\n")
    return evidence


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--data", required=True, type=Path, help="the folder LongMemEval was downloaded into")
    p.add_argument("--facts", required=True, help="the facts run its stores are copied from (facts-pilot/RUN)")
    p.add_argument("--label", required=True, help="the written run's name (cache/written/LABEL)")
    p.add_argument("--questions", choices=("pilot", "dev"), default="pilot")
    p.add_argument("--model", default="qwen3.8-flash")
    p.add_argument("--base-url", default="", help="the model's OpenAI-shaped address (not for --evidence)")
    p.add_argument("--key-env", default="DASHSCOPE_API_KEY")
    p.add_argument("--workers", type=int, default=8, help="questions written at once")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--prices", type=float, nargs=3, metavar=("HIT", "MISS", "OUT"),
                   help="USD per million tokens: cache hit, cache miss, output")
    p.add_argument("--stop-usd", type=float, help="stop once the replies have cost this (needs --prices)")
    p.add_argument("--sweeps", type=int, default=SWEEPS, help="the most summaries sweeps a question may take")
    p.add_argument("--evidence", type=Path, default=None,
                   help="write the run's evidence file here (ids and counts; no model call) instead of writing")
    p.add_argument("--answers", nargs="*", default=[], help="with --evidence: the answer runs' result files")
    p.add_argument("--answers-note", default="", help="with --evidence: what blocks the answers were asked on")
    args = p.parse_args(argv)
    if args.evidence is not None:
        bench._private_data_folder()
        bench._limit_cpu_threads()
        facts = args.data / "cache" / "facts-pilot" / args.facts
        made = write_evidence(args.data, facts, args.label, args.evidence, args.questions, args.answers,
                              args.answers_note)
        print(f"wrote {args.evidence}: {len(made['questions'])} questions, "
              f"{sum(r['changed'] for r in made['questions'])} blocks changed")
        return 0
    if not args.base_url:
        p.error("--base-url is needed to write")
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
            counts = asyncio.run(_write(items, facts, folder, model, args.workers, args.sweeps))
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
