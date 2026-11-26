#!/usr/bin/env python3
"""LongMemEval with the facts Kazma's deep pass writes (the memory plan's W4
pilot of dated event lines, 2026-11-23).

The harness (``longmemeval_bench.py``) writes each history as the product
writes a turn's memory but extracts no facts: the deep pass is one model call
a round, 45,611 rounds on dev. This pilot does it for a quarter of dev, the
product's way, before anyone pays for the rest:

  pick     50 dev questions, by question type, multi-session and temporal
           reasoning weighted in (ids into tests/fixtures/longmemeval/facts_pilot.json)
  extract  each question's history into a memory store of its own, round by
           round in time order as a chat would write it: the episode, the
           quick pass (``extract_and_apply_beliefs_sync``, as the post-turn
           path calls it), then the product's own deep-pass task
           (``worker_bootstrap._handle_micro_consolidation``, unchanged) on the
           model named -- its day, the turn before, the facts held, the names
           in use, its writes and vectors. Every round of the history is
           written, so the unrelated chat stays real. Replies are cached by
           message in the data folder: a run that stops goes on, and asks
           nothing twice. Each question's map (``map/<id>.json``: a round's
           reply and the facts its pass was shown) lets
           ``longmemeval_replay.py`` write the store again through another
           checkout's code, with no model call.
  blocks   each question's recall block from its store (``facts_events``) and
           from a copy without the dated event facts (``facts_no_events``),
           for ``longmemeval_bench.py answer --ids ... --blocks NAME=FILE``.

Everything holding the dataset's text (stores, replies, blocks) stays in the
data folder, never in the repository; the key is read from the environment
variable named, never printed. Stats hold ids and counts only.
"""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import longmemeval_bench as bench  # noqa: E402

PILOT = bench.FIXTURES / "facts_pilot.json"
#: Questions a type, out of dev's 53 multi-session, 53 temporal, 31
#: knowledge-update, 28 single-session-user, 23 single-session-assistant and
#: 12 preference: a quarter of dev, the two kinds events serve weighted in.
QUOTAS = {"multi-session": 16, "temporal-reasoning": 16, "knowledge-update": 7, "single-session-user": 5,
          "single-session-assistant": 4, "single-session-preference": 2}
SEED = "kazma-facts-pilot-2026-11-23"


def pick(items: dict[str, dict[str, Any]]) -> list[str]:
    """The pilot's ids: each type's quota of dev, ordered by a hash of the
    id with :data:`SEED` (the same draw on every machine)."""
    def rank(qid: str) -> str:
        return hashlib.sha256(f"{SEED}:{qid}".encode()).hexdigest()

    dev = bench.question_ids("dev")
    out: list[str] = []
    for kind, n in QUOTAS.items():
        out += sorted((q for q in dev if str(items[q]["question_type"]) == kind), key=rank)[:n]
    return sorted(out, key=rank)


def pilot_ids(questions: str = "pilot") -> list[str]:
    """The pilot's 50 ids, or (``dev``) every dev question, the pilot's first:
    their stores and replies are the pilot's, so the full run asks only the
    rest."""
    ids = list(json.loads(PILOT.read_text(encoding="utf-8"))["ids"])
    if questions == "dev":
        ids += [q for q in bench.question_ids("dev") if q not in set(ids)]
    return ids


# ── The stores, one a question ───────────────────────────────────────────────

#: The question whose store the running task writes: Kazma's memory code asks
#: ``kazma_core.paths`` for its databases, so each task's calls -- and the
#: threads they run in, which copy the context -- find their own.
_STORE: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar("pilot_store", default=None)


#: The round the running task's deep pass is for: the facts it was shown and
#: the model's reply, for the question's map (``longmemeval_replay.py``).
_ROUND: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar("pilot_round", default=None)


def _record_shown() -> None:
    """Keep the facts each deep pass is shown in its round's map entry."""
    import kazma_core.memory.held_facts as where

    if getattr(where.held_facts, "records_rounds", False):
        return
    held = where.held_facts

    def held_facts(conn: Any, **kw: Any) -> list[dict[str, Any]]:
        shown = held(conn, **kw)
        entry = _ROUND.get()
        if entry is not None:
            entry["held"] = [{k: h.get(k) for k in ("ref", "subject", "predicate", "object")} for h in shown]
        return shown

    held_facts.records_rounds = True  # type: ignore[attr-defined]
    where.held_facts = held_facts  # type: ignore[assignment]


def _route_memory_paths() -> None:
    import kazma_core.paths as paths

    primary, ops = paths.primary_memory_db, paths.memory_ops_db
    paths.primary_memory_db = lambda: (_STORE.get() or (primary(), ""))[0]  # type: ignore[assignment]
    paths.memory_ops_db = lambda: (_STORE.get() or ("", ops()))[1]  # type: ignore[assignment]


def prompt_tag() -> str:
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM

    return hashlib.sha256(_EXTRACT_SYSTEM.encode("utf-8")).hexdigest()[:8]


def run_tag(label: str = "") -> str:
    """The names a run's folder and blocks carry: the prompt's, and the
    extractor's *label* when it is not the first pilot's."""
    return f"{prompt_tag()}-{label}" if label else prompt_tag()


def work_folder(data: Path, label: str = "") -> Path:
    return data / "cache" / "facts-pilot" / run_tag(label)


class PilotEmbedder(bench.CachedEmbedder):
    """The harness's vectors (rounds and questions, embedded once), and the
    real model for what they lack (facts, entity names), kept in a file of
    the pilot's own: the harness's 300 MB file is never written. The model
    is asked from many threads at once, as the product asks it."""

    def __init__(self, inner: Any, path: Path, model: str, extra: Path) -> None:
        super().__init__(inner, path, model)
        self.extra, self.added = extra, {}
        if extra.is_file():
            import numpy as np

            held = np.load(extra)
            if str(held["model"]) == model:
                self.added = dict(zip((str(k) for k in held["keys"]), held["vecs"], strict=True))
                self.vectors.update(self.added)

    def encode(self, text: str) -> list[float]:
        vec = self.vectors.get(self.key(text))
        if vec is not None:
            return [float(x) for x in vec]
        vec = self.inner.encode(text)
        if vec:
            self.vectors[self.key(text)] = self.added[self.key(text)] = vec
        return vec

    def save(self) -> None:
        import numpy as np

        if not self.added:
            return
        keys = sorted(self.added)
        tmp = self.extra.with_name(self.extra.name + ".tmp.npz")
        np.savez(tmp, keys=np.asarray(keys), vecs=np.asarray([self.added[k] for k in keys], dtype=np.float16),
                 model=np.asarray(self.model))
        os.replace(tmp, self.extra)


class SpendLimitError(RuntimeError):
    """The run's replies have cost the most it may spend."""


class Spend:
    """What a run's replies cost at *prices* (USD per million tokens: cache
    hit, cache miss, output), and the most it may spend. With no prices it
    counts nothing and stops nothing."""

    def __init__(self, prices: tuple[float, float, float] | None = None, stop: float | None = None) -> None:
        self.prices, self.stop, self.usd = prices, stop, 0.0

    def add(self, usage: Any) -> None:
        if self.prices is None:
            return
        from kazma_core.token_usage import token_counts

        n, (hit, miss, out) = token_counts(usage), self.prices
        self.usd += (n.cached * hit + (n.prompt - n.cached) * miss + n.completion * out) / 1_000_000

    @property
    def reached(self) -> bool:
        return self.stop is not None and self.usd >= self.stop


class CachedModel:
    """Kazma's own provider for the deep pass, its replies kept by message
    (``replies.jsonl``) and its usage counted (prompt, cache, completion,
    reasoning). *spend* holds what every kept reply cost, earlier runs' too:
    past its stop no new message is asked."""

    def __init__(self, provider: Any, path: Path, spend: Spend | None = None) -> None:
        self.provider, self.path, self.spend = provider, path, spend or Spend()
        self.provider_name = getattr(provider, "provider_name", "qwen")
        self.config = provider.config
        self.lock = threading.Lock()
        self.replies: dict[str, str] = {}
        self.usage = {"calls": 0, "cached_replies": 0, "prompt": 0, "cached": 0, "completion": 0, "reasoning": 0}
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    row = json.loads(line)
                    self.replies[row["key"]] = row["content"]
                    self.spend.add(row.get("usage"))

    def key(self, messages: Any, kw: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps({"model": self.config.model, "messages": messages, "kw": kw},
                                         ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()

    async def chat(self, messages: Any, **kw: Any) -> Any:
        from kazma_core.llm_provider import LLMResponse
        from kazma_core.token_usage import token_counts

        key = self.key(messages, kw)
        entry = _ROUND.get()
        if key in self.replies:
            self.usage["cached_replies"] += 1
            if entry is not None:
                entry.update(content=self.replies[key], key=key)
            return LLMResponse(content=self.replies[key], model=self.config.model)
        if self.spend.reached:
            raise SpendLimitError(f"${self.spend.usd:.2f} of ${self.spend.stop:.2f} spent")
        response = await self.provider.chat(messages, **kw)
        usage = response.usage or {}
        counts = token_counts(usage)
        with self.lock:
            self.spend.add(usage)
            self.usage["calls"] += 1
            self.usage["prompt"] += counts.prompt
            self.usage["cached"] += counts.cached
            self.usage["completion"] += counts.completion
            self.usage["reasoning"] += int((usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
                                           or 0)
            self.replies[key] = response.content or ""
            if entry is not None:
                entry.update(content=response.content or "", key=key)
            with self.path.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"key": key, "content": response.content or "", "usage": usage},
                                     ensure_ascii=False) + "\n")
        return response


def _connect(path: str) -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas

    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
    return conn


def _seed(item: dict[str, Any], db: Path, ops: Path) -> list[dict[str, Any]]:
    """The question's history as the harness writes it (its episodes and
    their vectors); its rows, oldest first."""
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    rows = bench.history_rows(item)
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        bench._seed_history(conn, rows)
    finally:
        conn.close()
    o = sqlite3.connect(str(ops))
    try:
        ensure_ops_schema(o)
    finally:
        o.close()
    return sorted((e["row"] for e in rows), key=lambda r: (r["created_at"], r["session_id"], r["turn_number"]))


def _quick_pass(db: str, ops: str, row: dict[str, Any]) -> None:
    """The post-turn path's heuristic pass (``consolidator._v2_extract_sync``)."""
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
    from kazma_core.memory.config import read_memory_cfg

    primary, o = _connect(db), _connect(ops)
    try:
        extract_and_apply_beliefs_sync(primary, o, row["user_text"] or "", row["assistant_text"] or "",
                                       session_id=row["session_id"], turn=row["turn_number"],
                                       tenant_id=row["tenant_id"], cfg=read_memory_cfg(), now=row["created_at"])
    finally:
        primary.close()
        o.close()


def store_counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db))
    try:
        one = lambda sql: int(conn.execute(sql).fetchone()[0] or 0)  # noqa: E731
        return {"facts": one("SELECT COUNT(*) FROM beliefs WHERE predicate != 'related_to'"),
                "current": one("SELECT COUNT(*) FROM beliefs WHERE predicate != 'related_to' "
                               "AND valid_until IS NULL AND invalidated_at IS NULL"),
                "deep_pass": one("SELECT COUNT(*) FROM beliefs WHERE extraction_method = 'llm_inferred'"),
                "dated_events": one("SELECT COUNT(*) FROM beliefs WHERE json_extract(metadata_json, '$.happened') "
                                    "IS NOT NULL"),
                "planned_events": one("SELECT COUNT(*) FROM beliefs WHERE json_extract(metadata_json, '$.planned') "
                                      "IS NOT NULL"),
                "dated_starts": one("SELECT COUNT(*) FROM beliefs WHERE json_extract(metadata_json, '$.started') "
                                    "IS NOT NULL AND json_extract(metadata_json, '$.happened') IS NULL")}
    finally:
        conn.close()


async def _one_question(qid: str, item: dict[str, Any], folder: Path, gate: asyncio.Semaphore,
                        progress: dict[str, int], spend: Spend) -> dict[str, Any] | None:
    from kazma_core.memory import worker_bootstrap
    from kazma_core.memory.belief_extractor import is_filler_turn

    done = folder / "done" / f"{qid}.json"
    if done.is_file():
        return json.loads(done.read_text(encoding="utf-8"))
    async with gate:
        db, ops = folder / "stores" / f"{qid}.db", folder / "stores" / f"{qid}.ops.db"
        for path in (db, ops):
            for suffix in ("", "-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)  # a store a stop cut off: written again
        _STORE.set((str(db), str(ops)))
        rows = await asyncio.to_thread(_seed, item, db, ops)
        stats = {"rounds": len(rows), "quick": 0, "deep": 0, "deep_failed": 0}
        mapped: list[dict[str, Any]] = []
        started = time.monotonic()
        for row in rows:
            user = (row["user_text"] or "").strip()
            if not user or user.startswith("/"):
                continue  # the product writes no turn memory for these
            await asyncio.to_thread(_quick_pass, str(db), str(ops), row)
            stats["quick"] += 1
            if is_filler_turn(user):
                continue  # small talk queues no deep pass
            entry: dict[str, Any] = {"episode": row["id"], "held": [], "content": None, "key": None}
            mapped.append(entry)
            for attempt in range(4):
                token = _ROUND.set(entry)
                try:
                    ok = await worker_bootstrap._handle_micro_consolidation({"episode_id": row["id"]})
                finally:
                    _ROUND.reset(token)
                if ok:
                    stats["deep"] += 1
                    break
                if spend.reached:
                    # The product's task reads the refusal as an outage: the
                    # question stays unwritten, never counted as failed.
                    raise SpendLimitError(f"{qid}: ${spend.usd:.2f} of ${spend.stop:.2f} spent")
                await asyncio.sleep(20 * (attempt + 1))
            else:
                stats["deep_failed"] += 1
            progress["deep"] += 1
        stats.update(await asyncio.to_thread(store_counts, db))
        stats["seconds"] = round(time.monotonic() - started)
        (folder / "map").mkdir(exist_ok=True)
        (folder / "map" / f"{qid}.json").write_text(json.dumps({"stats": stats, "rounds": mapped},
                                                               ensure_ascii=False) + "\n", encoding="utf-8")
        done.write_text(json.dumps(stats) + "\n", encoding="utf-8")
        progress["questions"] += 1
        print(f"  {qid}: {stats}", flush=True)
        return stats


def _where_threads_wait() -> str:
    """The innermost Kazma or library function each thread is in, counted:
    what holds the run up, printed with the progress."""
    counts: dict[str, int] = {}
    for frame in sys._current_frames().values():
        names = []
        while frame is not None:
            names.append(f"{Path(frame.f_code.co_filename).stem}.{frame.f_code.co_name}")
            frame = frame.f_back
        top = next((n for n in names if not n.startswith(("threading.", "thread.", "queue.", "selectors."))),
                   names[0] if names else "?")
        counts[top] = counts.get(top, 0) + 1
    return ", ".join(f"{n} x{c}" for n, c in sorted(counts.items(), key=lambda kv: -kv[1])[:8])


async def _extract(items: list[dict[str, Any]], folder: Path, model: Any, workers: int) -> dict[str, Any]:
    from concurrent.futures import ThreadPoolExecutor

    # Every question's SQLite work and vectors run in threads: room for all.
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=workers * 2 + 8))
    gate = asyncio.Semaphore(workers)
    progress = {"deep": 0, "questions": 0}

    async def report() -> None:
        while True:
            await asyncio.sleep(300)
            print(f"  [{datetime.now().strftime('%H:%M')}] {progress['questions']} questions done, "
                  f"{progress['deep']} deep passes this run; usage {model.usage}; ${model.spend.usd:.2f}", flush=True)
            print(f"    threads: {_where_threads_wait()}", flush=True)

    reporter = asyncio.create_task(report())  # noqa: RUF006 - cancelled below
    try:
        results = await asyncio.gather(*(_one_question(str(i["question_id"]), i, folder, gate, progress,
                                                       model.spend) for i in items))
    finally:
        reporter.cancel()
    return {str(i["question_id"]): r for i, r in zip(items, results, strict=True)}


def _embedder(data: Path, folder: Path) -> PilotEmbedder:
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    return PilotEmbedder(get_embedder(), data / "cache" / "kazma_vectors.npz", get_embedding_model_name(),
                         folder / "vectors-extra.npz")


def _cmd_extract(data: Path, model_name: str, base_url: str, key_env: str, workers: int,
                 limit: int | None, questions: str = "pilot", label: str = "",
                 spend: Spend | None = None) -> int:
    key = os.environ.get(key_env, "")
    if not key:
        print(f"set {key_env}", file=sys.stderr)
        return 2
    bench._private_data_folder()
    bench._limit_cpu_threads()
    from unittest import mock

    from kazma_core.llm_provider import LLMConfig, LLMProvider

    folder = work_folder(data, label)
    for sub in ("stores", "done"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    by_id = {str(x["question_id"]): x for x in bench.load(data)}
    items = [by_id[q] for q in pilot_ids(questions)[: limit or None]]
    embedder = _embedder(data, folder)
    model = CachedModel(LLMProvider(LLMConfig(base_url=base_url, api_key=key, model=model_name)),
                        folder / f"replies-{model_name}.jsonl", spend)
    registry = type("Registry", (), {"get_client": lambda self, *_a, **_k: model})()
    _route_memory_paths()
    _record_shown()
    print(f"{len(items)} questions, prompt {prompt_tag()}, {model_name}; {len(model.replies)} replies held, "
          f"${model.spend.usd:.2f} spent", flush=True)
    try:
        with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
                mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry), \
                mock.patch("kazma_core.memory.belief_extractor._turn_zone", lambda: UTC):
            stats = asyncio.run(_extract(items, folder, model, workers))
    except SpendLimitError as exc:
        print(f"stopped at the spend limit: {exc}; usage this run {model.usage}", file=sys.stderr)
        return 3
    finally:
        embedder.save()
    total = {k: sum(int((s or {}).get(k, 0)) for s in stats.values())
             for k in ("rounds", "quick", "deep", "deep_failed", "facts", "deep_pass", "dated_events")}
    (folder / "usage.json").write_text(json.dumps({"model": model_name, "this_run": model.usage, "total": total,
                                                   "usd_all_runs": round(model.spend.usd, 2)},
                                                  indent=1) + "\n", encoding="utf-8")
    print("totals", total)
    print("usage this run", model.usage)
    return 0


# ── The blocks ───────────────────────────────────────────────────────────────


def _recall_block(item: dict[str, Any], db: Path, *, without_events: bool) -> tuple[str, dict[str, int]]:
    """*item*'s recall block from a copy of its store (recall writes its
    access counts), optionally without the dated event facts
    (a day it happened or is planned for)."""
    from unittest import mock

    from kazma_core.db.sqlite_snapshot import snapshot_copy
    from kazma_core.memory.recall import format_recall_block, recall

    tmp = Path(tempfile.mkdtemp(prefix="kazma-pilot-"))
    try:
        copy = tmp / "store.db"
        snapshot_copy(db, copy)
        conn = sqlite3.connect(str(copy))
        conn.row_factory = sqlite3.Row
        try:
            if without_events:
                conn.execute("PRAGMA foreign_keys=OFF")
                conn.execute("DELETE FROM beliefs WHERE COALESCE(json_extract(metadata_json, '$.happened'), "
                             "json_extract(metadata_json, '$.planned')) IS NOT NULL")
                conn.commit()
            with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
                result = recall(str(item["question"]), conn=conn, tenant_id=bench.TENANT, limit=bench.LIMIT,
                                local_only=True, now=bench.parse_date(item["question_date"]))
                block = format_recall_block(result) if not result.empty else ""
            shown = {"turns": len(result.episodes), "facts": len(result.beliefs),
                     "dated_event_facts": sum(1 for b in result.beliefs if (b.metadata or {}).get("on"))}
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return block or bench.NO_MEMORIES, shown


def _cmd_blocks(data: Path, stats_out: Path | None, questions: str = "pilot", label: str = "") -> int:
    bench._private_data_folder()
    bench._limit_cpu_threads()
    from unittest import mock

    folder = work_folder(data, label)
    by_id = {str(x["question_id"]): x for x in bench.load(data)}
    ids = pilot_ids(questions)
    missing = [q for q in ids if not (folder / "done" / f"{q}.json").is_file()]
    if missing:
        print(f"{len(missing)} questions not extracted yet (e.g. {missing[0]})", file=sys.stderr)
        return 2
    embedder = _embedder(data, folder)
    blocks: dict[str, dict[str, str]] = {"facts_events": {}, "facts_no_events": {}}
    rows = []
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0):
        for qid in ids:
            item, db = by_id[qid], folder / "stores" / f"{qid}.db"
            today, _whole, today_shown = bench.kazma_history(item, bench.history_rows(item))
            row: dict[str, Any] = {"id": qid, "type": str(item["question_type"]),
                                   **json.loads((folder / "done" / f"{qid}.json").read_text(encoding="utf-8")),
                                   "kazma_tokens": bench.shown_tokens(today), "kazma_shown": today_shown}
            for name, without in (("facts_events", False), ("facts_no_events", True)):
                block, shown = _recall_block(item, db, without_events=without)
                blocks[name][qid] = block
                row[f"{name}_tokens"] = bench.shown_tokens(block)
                row[f"{name}_shown"] = shown
            rows.append(row)
    embedder.save()
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO, capture_output=True, text=True,
                            check=False).stdout.strip()
    for name, held in blocks.items():
        out = data / "cache" / f"blocks-{questions}-{name}-{run_tag(label)}.json"
        out.write_text(json.dumps({"commit": commit, "split": "dev", "made_on": datetime.now(UTC).isoformat(),
                                   "prompt": prompt_tag(), "label": label, "blocks": held}, ensure_ascii=False),
                       encoding="utf-8")
        print(f"wrote {out}")
    if stats_out:
        stats_out.parent.mkdir(parents=True, exist_ok=True)
        stats_out.write_text(json.dumps({"what": "The pilot's stores and blocks: counts only (no dataset text).",
                                         "commit": commit, "prompt": prompt_tag(), "label": label,
                                         "questions": rows},
                                        indent=1) + "\n", encoding="utf-8", newline="\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("pick", "extract", "blocks"):
        s = sub.add_parser(name)
        s.add_argument("--data", required=True, type=Path)
        if name in ("extract", "blocks"):
            s.add_argument("--questions", choices=("pilot", "dev"), default="pilot",
                           help="the pilot's 50, or every dev question (the pilot's stores kept)")
            s.add_argument("--label", default="", help="a run of its own (another extractor): its stores, replies "
                                                       "and blocks are kept apart under this name")
        if name == "extract":
            s.add_argument("--model", default="qwen3.8-flash")
            s.add_argument("--base-url", required=True)
            s.add_argument("--key-env", default="DASHSCOPE_API_KEY")
            s.add_argument("--workers", type=int, default=16, help="questions written at once")
            s.add_argument("--limit", type=int, default=None)
            s.add_argument("--prices", type=float, nargs=3, metavar=("HIT", "MISS", "OUT"),
                           help="USD per million tokens: cache hit, cache miss, output")
            s.add_argument("--stop-usd", type=float, help="stop once the replies have cost this (needs --prices)")
        if name == "blocks":
            s.add_argument("--stats", type=Path, default=None, help="the counts, for the evidence folder")
    args = p.parse_args(argv)
    if args.cmd == "pick":
        items = {str(x["question_id"]): x for x in bench.load(args.data)}
        ids = pick(items)
        PILOT.write_text(json.dumps({"seed": SEED, "split": "dev", "quotas": QUOTAS, "size": len(ids),
                                     "what": "The memory plan's W4 pilot of dated event lines: a quarter of dev, "
                                             "multi-session and temporal reasoning weighted in.",
                                     "ids": ids}, indent=1) + "\n", encoding="utf-8", newline="\n")
        rounds = sum(len(bench.rounds(s)) for q in ids for s in items[q]["haystack_sessions"])
        print(f"{PILOT}: {len(ids)} questions, {rounds} rounds")
        return 0
    if args.cmd == "extract":
        if args.stop_usd is not None and not args.prices:
            p.error("--stop-usd needs --prices")
        return _cmd_extract(args.data, args.model, args.base_url, args.key_env, args.workers, args.limit,
                            args.questions, args.label, Spend(tuple(args.prices) if args.prices else None,
                                                              args.stop_usd))
    return _cmd_blocks(args.data, args.stats, args.questions, args.label)


if __name__ == "__main__":
    sys.exit(main())
