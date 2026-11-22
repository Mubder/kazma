"""What memory costs an ordinary turn, measured: the product measures every
memory change is gated on (``docs/plans/MEMORY_TOP_SCORES_PLAN.md``, section
2.1 and W0). A change that raises an outside score while worsening one of
these is not adopted.

Run with the repository's packages on the path (``PYTHONPATH`` as the test
suite has it)::

    python scripts/memory_product_measures.py measure              # print
    python scripts/memory_product_measures.py measure --write      # record it
    python scripts/memory_product_measures.py measure --real-embedder
    python scripts/memory_product_measures.py check                # tokens against the record
    python scripts/memory_product_measures.py live-lengths BACKUP  # live's episode lengths (W1a)

Measured, on the retrieval benchmark's store (dataset v8, both sets) with its
recorded vectors (no model is loaded unless ``--real-embedder``):

* **Kazma's own benchmark, English and Arabic apart**: answerable questions
  whose answer was shown, their MRR and precision, and unanswerable questions
  with nothing shown. (The benchmark's lock holds the same run by category.)
* **The tokens the recall block adds to a turn**: the block the supervisor
  adds (``format_recall_block``, rules and fence included), counted by Kazma's
  own estimate (``summarizer.estimate_tokens``, the trim's), per question; on
  the store and on the store with four times its unrelated chat. p50, p95,
  the largest, and the share of turns it adds nothing to. Deterministic: the
  ``check`` command and ``tests/test_memory_product_measures.py`` hold it to
  the record, so a change to the block's size records its new numbers in the
  same commit (its before and after).
* **The same block where memories are as long as live's** (``long``): the
  store, each conversation lengthened to a length drawn from live's episode
  lengths (``tests/fixtures/memory_bench/live_lengths.json``: percentiles
  only, read by ``live-lengths`` from a native backup of live's memory
  database opened immutable, never the live file; no text leaves it). The
  padding is stopwords only, after the conversation's own text: no word a
  question asks about, the seeded vectors kept, so recall chooses as on the
  store and the tokens measure the lengths alone. Since memories are kept
  whole (2026-11-19) a live memory is mostly longer than the benchmark's.
* **Recall's time per question** (the search and ranking, recorded vectors),
  and with ``--real-embedder`` the embedding model's time for each question on
  this machine: memory's share of the time to the first token. Kazma's whole
  share is ``tests/e2e/test_first_token_latency.py`` (run it with ``-s``; its
  line is recorded here by hand, ``first_token``). Times are this PC's, never
  gated.
* **A new install's small store** (``run_small_store_benchmark``).

The harness differs from a live turn in what the benchmarks page lists: a
private store, a fixed "now", recorded vectors.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import random
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "memory_bench"
VECTORS = FIXTURES / "vectors.npz"
RECORD = FIXTURES / "product_measures.json"
LIVE_LENGTHS = FIXTURES / "live_lengths.json"
STORES = ("store", "scale", "long")
#: The tiers recall reads (``vector_engine.RECALLABLE_TIERS``), counted in live's lengths.
_LIVE_TIERS = ("working", "recall", "episodic", "archived")
LANGS = ("en", "ar")


def _arabic(text: str) -> bool:
    return bool(re.search("[؀-ۿ]", text))


def _questions(data: dict[str, Any]) -> list[dict[str, Any]]:
    return list(data["questions"]) + list(data.get("second") or [])


def _replayed(embedder: Any) -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder))
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: embedder.model))
    return stack


def _pct(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))] if ordered else 0.0


def block_tokens(conn: Any, questions: list[dict[str, Any]]) -> tuple[dict[str, list[int]], list[float]]:
    """Each question's recall block as the supervisor adds it, in tokens, by
    language; and recall's time per question."""
    from kazma_core.memory import benchmark
    from kazma_core.memory.question_shape import standalone_query
    from kazma_core.memory.recall import format_recall_block, recall
    from kazma_core.summarizer import estimate_tokens

    tokens: dict[str, list[int]] = {lang: [] for lang in LANGS}
    seconds: list[float] = []
    for q in questions:
        query = standalone_query(q["text"], q.get("context"))
        now = benchmark.BASE_TIME + float(q.get("now_day", benchmark._ASKED_ON_DAY)) * 86400
        with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
            started = time.perf_counter()
            res = recall(query, conn=conn, tenant_id=benchmark.BENCH_TENANT, limit=5, local_only=True, now=now)
            seconds.append(time.perf_counter() - started)
        block = "" if res.empty else format_recall_block(res, explain=False)
        n = estimate_tokens([{"role": "system", "content": block}]) if block else 0
        tokens["ar" if _arabic(q["text"]) else "en"].append(n)
    return tokens, seconds


def _filler(arabic: bool) -> str:
    """Text holding no content word (``query_terms``' own stopwords): what a
    lengthened memory is padded with, in its language."""
    from kazma_core.memory import query_terms

    words = sorted(query_terms._AR_STOPWORDS_RAW.split() if arabic else
                   (w for w in query_terms._EN_STOPWORDS if w.isalpha()))
    return " ".join(words) + " "


def _to_length(text: str, length: int) -> str:
    """*text*, then filler in its language, to *length* characters (whole
    words); a text already that long is kept as it is."""
    if len(text) >= length:
        return text
    filler = _filler(_arabic(text))
    pad = (filler * (length // len(filler) + 2))[: max(0, length - len(text) - 1)]
    return (text + " " + pad[: pad.rfind(" ")]).rstrip() if " " in pad else text


def lengthen(conn: Any, lengths: dict[str, Any], *, tenant_id: str) -> int:
    """Lengthen every conversation of the seeded store to a length drawn from
    *lengths* (``live_lengths.json``), the draw seeded by the row's id: a
    two-sided conversation's question and answer from their percentiles, a
    one-sided one from the one-sided percentiles. Returns the rows changed."""
    rows = conn.execute("SELECT id, user_text, assistant_text FROM episodes WHERE tenant_id = ?",
                        (tenant_id,)).fetchall()
    changed = 0
    for row in rows:
        draw = random.Random(f"live-lengths:{row[0]}")
        question, answer = str(row[1] or ""), str(row[2] or "")
        if question and answer:
            question = _to_length(question, lengths["question_percentiles"][draw.randint(0, 100)])
            answer = _to_length(answer, lengths["answer_percentiles"][draw.randint(0, 100)])
        elif question or answer:
            n = lengths["one_side_percentiles"][draw.randint(0, 100)]
            question, answer = (_to_length(question, n), answer) if question else (question, _to_length(answer, n))
        else:
            continue
        conn.execute("UPDATE episodes SET user_text = ?, assistant_text = ? WHERE id = ? "
                     "AND (? IS NULL OR tenant_id = ?)", (question, answer, row[0], tenant_id, tenant_id))
        changed += 1
    conn.commit()
    return changed


def _percentiles(values: list[int]) -> list[int]:
    ordered = sorted(values)
    return [ordered[min(len(ordered) - 1, round(p / 100 * (len(ordered) - 1)))] for p in range(101)] if ordered else []


def live_lengths(backup: Path) -> dict[str, Any]:
    """Episode lengths from *backup*, a native backup of live's memory
    database (``memory_state_<ts>.db``), opened immutable: nothing written,
    no lock taken, lengths only (SQLite's ``length``), as percentiles. The
    live file itself is refused."""
    import sqlite3

    # A native backup's own name (``memory/backup.py``: ``<stem>_<epoch>.db``
    # of live's ``memory_state.db``), never the live file's.
    if not re.fullmatch(r"memory_state_\d+\.db", backup.name):
        raise SystemExit(f"{backup.name} is not a native backup of the memory database (memory_state_<ts>.db); "
                         "the live file is never read")
    conn = sqlite3.connect("file:" + backup.resolve().as_posix() + "?mode=ro&immutable=1", uri=True)
    try:
        rows = conn.execute(
            "SELECT length(COALESCE(user_text,'')), length(COALESCE(assistant_text,'')) FROM episodes "
            f"WHERE tier IN ({','.join('?' * len(_LIVE_TIERS))})", _LIVE_TIERS).fetchall()
    finally:
        conn.close()
    both = [r for r in rows if r[0] and r[1]]
    one = [max(r) for r in rows if bool(r[0]) != bool(r[1])]
    return {
        "what": "Live's episode lengths in characters, as percentiles 0..100, from a native backup of its "
                "memory database opened immutable; lengths only, no text. Written by "
                "scripts/memory_product_measures.py live-lengths.",
        "backup_taken": int(backup.stem.rpartition("_")[2]),
        "tiers": list(_LIVE_TIERS),
        "episodes": len(rows),
        "two_sided": len(both),
        "one_sided": len(one),
        "question_percentiles": _percentiles([r[0] for r in both]),
        "answer_percentiles": _percentiles([r[1] for r in both]),
        "one_side_percentiles": _percentiles(one),
    }


def token_summary(tokens: dict[str, list[int]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for lang, values in [*tokens.items(), ("all", [v for vs in tokens.values() for v in vs])]:
        out[lang] = {"n": len(values), "p50": _pct(values, 0.5), "p95": _pct(values, 0.95),
                     "max": max(values, default=0),
                     "none": round(sum(1 for v in values if v == 0) / len(values), 4) if values else 0.0}
    return out


def scores_by_language(conn: Any, refs: dict[str, str], covers: dict[str, set[str]],
                       questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Kazma's benchmark, English and Arabic apart."""
    from kazma_core.memory import benchmark

    results, _ = benchmark._ask_questions(conn, refs, covers, questions)
    by_id = {q["id"]: q for q in questions}
    out: dict[str, Any] = {}
    for lang in LANGS:
        rows = [r for r in results if ("ar" if _arabic(by_id[r["id"]]["text"]) else "en") == lang]
        answerable = [r for r in rows if r["category"] != "abstain"]
        unanswerable = [r for r in rows if r["category"] == "abstain"]
        scored = [r for r in answerable if "rr" in r]
        out[lang] = {
            "questions": len(rows),
            "answered": round(sum(1 for r in answerable if r["hit"]) / len(answerable), 4) if answerable else 0.0,
            "mrr": round(statistics.fmean(r["rr"] for r in scored), 4) if scored else 0.0,
            "precision": round(statistics.fmean(r["precision"] for r in scored), 4) if scored else 0.0,
            "abstained": round(sum(1 for r in unanswerable if r["hit"]) / len(unanswerable), 4)
            if unanswerable else 0.0,
            "unanswerable": len(unanswerable),
        }
    return out


def embedding_seconds(questions: list[dict[str, Any]]) -> list[float]:
    """The embedding model's time for each question, on this machine (the
    model loaded once first)."""
    from kazma_core.memory.embedder import get_embedder

    embedder = get_embedder()
    if embedder is None:
        raise SystemExit("no embedder is configured: install the rag extra")
    embedder.encode("warm up")
    out = []
    for q in questions:
        started = time.perf_counter()
        embedder.encode(q["text"])
        out.append(time.perf_counter() - started)
    return out


def measure(*, real_embedder: bool = False, small: bool = True, scale: bool = True,
            long: bool = True) -> dict[str, Any]:
    from kazma_core.memory import benchmark

    data = benchmark.load_dataset()
    questions = _questions(data)
    replay = benchmark.ReplayEmbedder(VECTORS)
    report: dict[str, Any] = {"dataset_version": data.get("version"), "questions": len(questions)}
    with _replayed(replay):
        with benchmark._seeded_store(data) as (conn, refs, covers):
            report["benchmark"] = scores_by_language(conn, refs, covers, questions)
            tokens, seconds = block_tokens(conn, questions)
            report["block_tokens"] = {"store": token_summary(tokens)}
            report["recall_ms"] = {"p50": round(_pct(seconds, 0.5) * 1000, 1),
                                   "p95": round(_pct(seconds, 0.95) * 1000, 1)}
        scaled = benchmark._scaled_dataset(data) if scale else None
        if scaled:
            with benchmark._seeded_store(scaled) as (conn, _refs, _covers):
                tokens, seconds = block_tokens(conn, questions)
                report["block_tokens"]["scale"] = token_summary(tokens)
                report["recall_ms_scale"] = {"p50": round(_pct(seconds, 0.5) * 1000, 1),
                                             "p95": round(_pct(seconds, 0.95) * 1000, 1)}
        if long:
            with benchmark._seeded_store(data) as (conn, _refs, _covers):
                lengthen(conn, json.loads(LIVE_LENGTHS.read_text(encoding="utf-8")), tenant_id=benchmark.BENCH_TENANT)
                tokens, _seconds = block_tokens(conn, questions)
                report["block_tokens"]["long"] = token_summary(tokens)
        if small:
            report["small_store"] = benchmark.run_small_store_benchmark(data)["overall"]
    if real_embedder:
        seconds = embedding_seconds(questions)
        report["embed_ms"] = {"p50": round(_pct(seconds, 0.5) * 1000, 1),
                              "p95": round(_pct(seconds, 0.95) * 1000, 1)}
    return report


def changed_tokens(record: dict[str, Any], today: dict[str, Any]) -> list[str]:
    """Where today's block tokens differ from the record (deterministic: any
    difference is a change to the block, recorded with its commit)."""
    problems = []
    for store in STORES:
        for lang, row in (record.get("block_tokens", {}).get(store) or {}).items():
            now = (today.get("block_tokens", {}).get(store) or {}).get(lang)
            if now != row:
                problems.append(f"{store}/{lang}: recorded {row}, today {now}")
    return problems


def _cmd_measure(write: bool, real_embedder: bool) -> int:
    report = measure(real_embedder=real_embedder)
    old = json.loads(RECORD.read_text(encoding="utf-8")) if RECORD.is_file() else {}
    print(json.dumps(report, indent=1))
    if write:
        kept = {k: old[k] for k in ("first_token", "embed_ms") if k in old and k not in report}
        RECORD.write_text(json.dumps({
            "what": "The product measures every memory change is gated on: Kazma's benchmark in English and "
                    "Arabic, the tokens the recall block adds to a turn, recall's time, the small store. "
                    "Written by scripts/memory_product_measures.py; times are this PC's.",
            "made_on": datetime.now(UTC).date().isoformat(), **kept, **report,
        }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        print(f"wrote {RECORD.relative_to(REPO)}")
    return 0


def _cmd_check() -> int:
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    problems = changed_tokens(record, measure(small=False))
    for p in problems:
        print(p)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--write", action="store_true", help="record it in tests/fixtures/memory_bench")
    m.add_argument("--real-embedder", action="store_true", help="also time the embedding model on this machine")
    sub.add_parser("check")
    live = sub.add_parser("live-lengths", help="record live's episode lengths from a native backup")
    live.add_argument("backup", type=Path, help="a memory_state_<ts>.db native backup (opened immutable)")
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    random.seed(0)
    if args.cmd == "measure":
        return _cmd_measure(args.write, args.real_embedder)
    if args.cmd == "live-lengths":
        report = live_lengths(args.backup)
        LIVE_LENGTHS.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
        q, a = report["question_percentiles"], report["answer_percentiles"]
        print(f"{report['episodes']} episodes; question p50/p95 {q[50]}/{q[95]}, answer p50/p95 {a[50]}/{a[95]}")
        print(f"wrote {LIVE_LENGTHS.relative_to(REPO)}")
        return 0
    return _cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
