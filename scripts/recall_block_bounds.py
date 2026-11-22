#!/usr/bin/env python3
"""Measure the recall block's bounds (2026-11-21): how much of each memory a
turn's model is shown, against what the block's budget allows.

A recalled conversation is shown bounded (``limits.RECALL_SHOW_QUESTION``,
``RECALL_SHOW_ANSWER``, ``RECALL_SHOW_ONE``; a weekly summary at
``recall._SUMMARY_SHOW_CHARS``), and the whole block at
``format_recall_block(max_tokens=1500)``: past it, whole lines are left out.
On LongMemEval, 44 of the 51 questions Kazma's block missed had the answer's
round shown, cut by these bounds. This script asks the same recalls with
other bounds and says, for each:

- ``longmemeval``: of the questions whose answer (a word or a phrase, not a
  bare number) is written word for word in the rounds recall showed, how
  many still hold it in the block; the block's
  size; the lines the budget left out. Needs LongMemEval's data folder and the
  vectors ``longmemeval_bench.py retrieve`` cached there.
  With ``--answer MODEL``, LongMemEval's reader answers from each variant's
  block and its judge scores it (DeepSeek, ``DEEPSEEK_API_KEY``).
- ``main``: on the retrieval benchmark (recorded vectors), the block's size,
  the lines the budget left out, and the answering memories shown in part.

    python scripts/recall_block_bounds.py main
    python scripts/recall_block_bounds.py longmemeval --data DIR [--answer deepseek-flash]

No embedding model is loaded (a text with no recorded vector stops the
run); nothing is written but ``--out`` and, with ``--answer``, the answers'
cache in the data folder.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
VECTORS = ROOT / "tests" / "fixtures" / "memory_bench" / "vectors.npz"

#: Each variant: the question's and the answer's bound, a one-sided memory's,
#: a weekly summary's, the block's budget in tokens, and how a long side is
#: cut ("start": its first characters; "focus": the stretch holding most of
#: the question's words; "top": the best memory at ``memory_search``'s
#: bounds, the rest as today).
VARIANTS: dict[str, dict[str, Any]] = {
    "today": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "start"},
    "x1.5": {"q": 300, "a": 450, "one": 750, "summary": 900, "budget": 1500, "cut": "start"},
    "x2": {"q": 400, "a": 600, "one": 1000, "summary": 900, "budget": 1500, "cut": "start"},
    "x3": {"q": 600, "a": 900, "one": 1500, "summary": 900, "budget": 1500, "cut": "start"},
    "whole": {"q": 10**9, "a": 10**9, "one": 10**9, "summary": 900, "budget": 10**6, "cut": "start"},
    "focus": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "focus"},
    "focus x1.5": {"q": 300, "a": 450, "one": 750, "summary": 900, "budget": 1500, "cut": "focus"},
    "top": {"q": 600, "a": 900, "one": 1500, "summary": 900, "budget": 1500, "cut": "top"},
    "top focus": {"q": 600, "a": 900, "one": 1500, "summary": 900, "budget": 1500, "cut": "top focus"},
    "fit": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "fit",
            "share_min": 500, "share_max": 1500},
    "fit focus": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "fit focus",
                  "share_min": 500, "share_max": 1500},
    "fit 2500": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 2500, "cut": "fit",
                 "share_min": 500, "share_max": 4000},
    "fit 4000": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 4000, "cut": "fit",
                 "share_min": 500, "share_max": 6000},
    "whole oldest": {"q": 10**9, "a": 10**9, "one": 10**9, "summary": 900, "budget": 10**6, "cut": "start",
                     "order": "oldest"},
    "fit oldest": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "fit",
                   "share_min": 500, "share_max": 1500, "order": "oldest"},
    "lead": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "lead",
             "lead": 1500, "rest_min": 250},
    "lead focus": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "lead focus",
                   "lead": 1500, "rest_min": 250},
    "lead focus 1000": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "lead focus",
                        "lead": 1000, "rest_min": 250},
    "list focus": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "start",
                   "list_cut": "focus"},
    "lead focus lists": {"q": 200, "a": 300, "one": 500, "summary": 900, "budget": 1500, "cut": "lead focus",
                         "lead": 1500, "rest_min": 250, "lists": True},
    "summary 600": {"q": 200, "a": 300, "one": 500, "summary": 600, "budget": 1500, "cut": "start"},
    "summary 1200": {"q": 200, "a": 300, "one": 500, "summary": 1200, "budget": 1500, "cut": "start"},
    "summary 1500": {"q": 200, "a": 300, "one": 500, "summary": 1500, "budget": 1500, "cut": "start"},
}
#: A variant cut "top": the memories after the best one, at today's bounds.
TODAY = VARIANTS["today"]

_SECTION = re.compile(r"^## (Known Facts|Relevant History|Weekly Summaries|Summaries)$")


def _words(text: str) -> list[str]:
    """The question's content words, as recall's word coverage reads them."""
    from kazma_core.memory.query_terms import content_terms

    return content_terms(text)


def _word_of(raw: str) -> str:
    """One word of a memory, read as :func:`_words` reads the question's."""
    from kazma_core.documents.arabic import fold_for_search
    from kazma_core.memory.query_terms import _normalise

    return _normalise(fold_for_search(raw))


def focused(text: str, limit: int, terms: set[str]) -> str:
    """*text* cut to *limit* characters around the stretch that holds the most
    of *terms* (folded words); its start when none is there. Whole words; an
    ellipsis where text was left out."""
    if len(text) <= limit or not terms:
        return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
    spans = [(m.start(), m.end(), _word_of(m.group())) for m in re.finditer(r"\w+", text)]
    room = limit - 2
    # Each stretch starts at a question word: the one holding the most
    # distinct words wins (the first, on a tie); it runs to its last word.
    best, best_at, best_end = 0, 0, 0
    for i, (first, _end, w) in enumerate(spans):
        if w not in terms:
            continue
        seen: set[str] = set()
        last = first
        for _s, e, word in spans[i:]:
            if e - first > room:
                break
            if word in terms:
                seen.add(word)
                last = e
        if len(seen) > best:
            best, best_at, best_end = len(seen), first, last
    if best == 0:
        return text[: limit - 1].rstrip() + "…"
    # Start a little before the stretch, so its sentence reads, as far as the
    # room after its last word allows.
    start = max(0, best_at - min(limit // 5, room - (best_end - best_at)))
    start = text.rfind(" ", 0, start) + 1 if start > 0 else 0
    piece = text[start:start + limit - 2]
    if start + limit - 2 < len(text):
        piece = piece[: piece.rfind(" ")] if " " in piece else piece
        piece += "…"
    return ("…" if start > 0 else "") + piece.strip()


def variant_display(variant: dict[str, Any], query: str) -> Callable[..., tuple[str, int]]:
    """A stand-in for ``episode_text.display_parts`` under *variant*: a call
    with bounds of its own (a list's compact line) is passed through."""
    from kazma_core.memory import episode_text

    real = episode_text.display_parts
    terms = set(_words(query))

    def display(user_text: Any, assistant_text: Any, summary_text: Any = "", **bounds: Any) -> tuple[str, int]:
        if bounds:  # a list's compact line, or a caller with its own bounds
            if variant.get("list_cut") != "focus":
                return real(user_text, assistant_text, summary_text, **bounds)
            # The list's own bounds, cut around the question's words.
            q, a, one = bounds["question_max"], bounds["answer_max"], bounds["one_max"]
            question, answer = episode_text._flat(user_text), episode_text._flat(assistant_text)
            if question and answer:
                return (f"User: {focused(question, q, terms)} / Assistant: {focused(answer, a, terms)}",
                        episode_text._hidden(question, q) + episode_text._hidden(answer, a))
            whole = question or answer or episode_text._flat(summary_text)
            return focused(whole, one, terms), episode_text._hidden(whole, one)
        q, a, one = variant["q"], variant["a"], variant["one"]
        if variant["cut"] == "start":
            return real(user_text, assistant_text, summary_text, question_max=q, answer_max=a, one_max=one)
        question, answer = episode_text._flat(user_text), episode_text._flat(assistant_text)
        hidden = episode_text._hidden
        if question and answer:
            text = f"User: {focused(question, q, terms)} / Assistant: {focused(answer, a, terms)}"
            return text, hidden(question, q) + hidden(answer, a)
        whole = question or answer or episode_text._flat(summary_text)
        return focused(whole, one, terms), hidden(whole, one)

    return display


def _row_texts(conn: sqlite3.Connection, episode_id: str) -> Any:
    """A recalled memory's question, answer and summary from the benchmark's
    private store (one tenant: every tenant is named, ``None``, in the
    statement, as the id-only statement gate asks)."""
    return conn.execute("SELECT user_text, assistant_text, summary_text FROM episodes "
                        "WHERE id = ? AND (? IS NULL OR tenant_id = ?)", (episode_id, None, None)).fetchone()


def render(result: Any, variant: dict[str, Any], query: str, conn: sqlite3.Connection) -> str:
    """The recall block for *result* under *variant*."""
    from kazma_core.memory import recall as rm

    if variant["cut"].startswith("top") and result.episodes:
        # The best memory at the wider bounds (focused or not), the rest as today.
        top_cut = "focus" if "focus" in variant["cut"] else "start"
        for i, hit in enumerate(result.episodes):
            row = _row_texts(conn, hit.id)
            if row is None or (hit.metadata or {}).get("joined"):
                continue
            v = {**variant, "cut": top_cut} if i == 0 else {**TODAY, "cut": top_cut if "focus" in variant["cut"] else "start"}
            display = variant_display(v, query)
            text, more = display(row[0], row[1], row[2])
            hit.metadata["display"] = text
            hit.metadata.pop("more", None)
            if more:
                hit.metadata["more"] = more
    if variant["cut"].startswith("fit") and result.episodes and not result.listing:
        _fit(result, variant, query, conn)
    if variant["cut"].startswith("lead") and result.episodes and (variant.get("lists") or not result.listing):
        _lead(result, variant, query, conn)
    if variant.get("order") == "oldest" and result.episodes:
        # A diagnostic, not a bound: the same lines in the order they were said.
        when = {h.id: (conn.execute("SELECT created_at FROM episodes WHERE id = ? AND (? IS NULL OR tenant_id = ?)",
                                  (h.id, None, None)).fetchone() or [0])[0]
                for h in result.episodes}
        result.episodes.sort(key=lambda h: float(when[h.id] or 0))
    return rm.format_recall_block(result, explain=False, max_tokens=variant["budget"]) if not result.empty else ""


def _shown_at(hit: Any, row: Any, share: int, cut: str, query: str) -> None:
    """*hit*'s display at *share* characters: a short side gives its room to
    the other (the question at least two fifths when both are long)."""
    from kazma_core.memory import episode_text

    q, a = episode_text._flat(row[0]), episode_text._flat(row[1])
    q_room = min(len(q), max(share * 2 // 5, share - len(a)))
    display = variant_display({"q": max(q_room, 1), "a": max(share - q_room, 1), "one": share, "cut": cut}, query)
    text, more = display(row[0], row[1], row[2])
    hit.metadata["display"] = text
    hit.metadata.pop("more", None)
    if more:
        hit.metadata["more"] = more


def _lead(result: Any, variant: dict[str, Any], query: str, conn: sqlite3.Connection) -> None:
    """The best recalled turn at ``lead`` characters, the others at today's
    500; when the block's budget would leave a line out, the others give
    way first (down to ``rest_min``), then the best one (down to 500)."""
    from kazma_core.memory import recall as rm

    turns = [h for h in result.episodes if not (h.metadata or {}).get("joined")]
    rows = {h.id: _row_texts(conn, h.id) for h in turns}
    cut = "focus" if "focus" in variant["cut"] else "start"
    every = len(result.beliefs) + len(result.episodes) + len(result.summaries)
    shares = [(variant["lead"], rest) for rest in range(500, variant["rest_min"] - 1, -50)]
    shares += [(lead, variant["rest_min"]) for lead in range(variant["lead"] - 100, 499, -100)]
    for lead, rest in shares:
        for i, hit in enumerate(turns):
            if rows[hit.id] is not None:
                _shown_at(hit, rows[hit.id], lead if i == 0 else rest, cut, query)
        if lines_shown(rm.format_recall_block(result, explain=False, max_tokens=variant["budget"])) == every:
            return


def _fit(result: Any, variant: dict[str, Any], query: str, conn: sqlite3.Connection) -> None:
    """Each recalled turn at the widest share of the block that still leaves
    no line out (from ``memory_search``'s bounds down to today's, in steps
    of 50 characters, rendered each time); a short side gives its room to
    the other."""
    from kazma_core.memory import episode_text
    from kazma_core.memory import recall as rm

    turns = [h for h in result.episodes if not (h.metadata or {}).get("joined")]
    rows = {h.id: _row_texts(conn, h.id) for h in turns}
    cut = "focus" if "focus" in variant["cut"] else "start"
    every = len(result.beliefs) + len(result.episodes) + len(result.summaries)
    for share in range(variant["share_max"], variant["share_min"] - 1, -50):
        for hit in turns:
            row = rows[hit.id]
            if row is None:
                continue
            q, a = episode_text._flat(row[0]), episode_text._flat(row[1])
            q_room = min(len(q), max(share * 2 // 5, share - len(a)))
            display = variant_display({"q": max(q_room, 1), "a": max(share - q_room, 1), "one": share,
                                       "cut": cut}, query)
            text, more = display(row[0], row[1], row[2])
            hit.metadata["display"] = text
            hit.metadata.pop("more", None)
            if more:
                hit.metadata["more"] = more
        if lines_shown(rm.format_recall_block(result, explain=False, max_tokens=variant["budget"])) == every:
            return


@contextmanager
def bounded(variant: dict[str, Any], query: str) -> Iterator[None]:
    """Recall's displays and the summary cut under *variant* (a "top" or
    "fit" variant is re-rendered by :func:`render`)."""
    from kazma_core.memory import recall as rm

    if variant["cut"].startswith(("top", "fit", "lead")):
        yield
        return
    display = variant_display(variant, query)
    with mock.patch.object(rm, "display_parts", display), \
            mock.patch.object(rm, "_SUMMARY_SHOW_CHARS", variant["summary"]):
        yield


def lines_shown(block: str) -> int:
    n, inside = 0, False
    for line in block.splitlines():
        if _SECTION.match(line.strip()):
            inside = True
        elif not line.strip():
            inside = False
        elif inside and line.startswith("- "):
            n += 1
    return n


def _sizes(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chars = sorted(r["chars"] for r in rows)
    if not chars:
        return {}
    return {"chars_p50": chars[len(chars) // 2], "chars_p95": chars[min(len(chars) - 1, int(len(chars) * 0.95))],
            "chars_max": chars[-1], "tokens_p95": round(chars[min(len(chars) - 1, int(len(chars) * 0.95))] / 4),
            "budget_left_out": sum(r["left_out"] for r in rows),
            "questions_with_lines_left_out": sum(1 for r in rows if r["left_out"])}


class _NoModel:
    """Recorded vectors only: a text with none stops the run, no model loads."""

    def __init__(self, vectors: dict[str, Any], key: Callable[[str], str], model: str) -> None:
        self.vectors, self.key, self.model = vectors, key, model
        self.dim = len(next(iter(vectors.values()))) if vectors else None

    def encode(self, text: str) -> list[float]:
        vec = self.vectors.get(self.key(text))
        if vec is None:
            raise SystemExit(f"no recorded vector for a text of {len(text)} characters: run "
                             "longmemeval_bench.py retrieve first")
        return [float(x) for x in vec]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]


def _load_lme() -> Any:
    spec = importlib.util.spec_from_file_location("longmemeval_bench", ROOT / "scripts" / "longmemeval_bench.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["longmemeval_bench"] = module
    spec.loader.exec_module(module)
    return module


def _norm(text: str) -> str:
    return " ".join(re.findall(r"\w+", str(text).casefold()))


def _cmd_longmemeval(data: Path, variants: list[str], *, answer_with: str | None = None) -> dict[str, Any]:
    import numpy as np

    lme = _load_lme()
    lme._private_data_folder()
    from datetime import UTC

    from kazma_core.memory import recall as rm
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    held = np.load(data / "cache" / "kazma_vectors.npz")
    model = str(held["model"])
    embedder = _NoModel(dict(zip((str(k) for k in held["keys"]), held["vecs"], strict=True)),
                        lme.CachedEmbedder.key, model)
    sample = json.loads(lme.SAMPLE.read_text(encoding="utf-8"))
    by_id = {str(x["question_id"]): x for x in lme.load(data)}
    rows_out: dict[str, list[dict[str, Any]]] = {v: [] for v in variants}
    blocks: dict[str, dict[str, str]] = {v: {} for v in variants}
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: model), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0), \
            mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
        for qid in sample["ids"]:
            item = by_id[qid]
            history = lme.history_rows(item)
            fd, path = tempfile.mkstemp(prefix="kazma-bounds-", suffix=".db")
            os.close(fd)
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                ensure_primary_schema(conn)
                back = lme._seed_history(conn, history)
                question, now = str(item["question"]), lme.parse_date(item["question_date"])
                # An answer that is a word or a phrase: a bare number ("3", a
                # count) is written somewhere in most rounds and proves nothing.
                answer = _norm(item.get("answer", ""))
                if not re.search(r"[^\W\d_]", answer):
                    answer = ""
                for name in variants:
                    variant = VARIANTS[name]
                    with bounded(variant, question):
                        result = rm.recall(question, conn=conn, tenant_id=lme.TENANT, limit=lme.LIMIT,
                                           local_only=True, now=now)
                        block = render(result, variant, question, conn)
                    blocks[name][qid] = block or "(no memories)"
                    shown_rows = [back[h.id]["row"] for h in result.episodes if h.id in back]
                    whole = _norm(" ".join(f"{r['user_text']} {r['assistant_text']}" for r in shown_rows))
                    found = bool(answer) and not lme.is_abstention(item) and f" {answer} " in f" {whole} "
                    rows_out[name].append({
                        "id": qid, "type": item["question_type"], "chars": len(block),
                        "left_out": len(result.beliefs) + len(result.episodes) + len(result.summaries)
                        - lines_shown(block),
                        "literal": found, "kept": found and f" {answer} " in f" {_norm(block)} ",
                    })
            finally:
                conn.close()
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.remove(path + suffix)
                    except OSError:
                        pass
    report = {}
    for name in variants:
        rows = rows_out[name]
        lit = [r for r in rows if r["literal"]]
        report[name] = {**VARIANTS[name], **_sizes(rows), "answer_in_shown_rounds": len(lit),
                        "answer_kept_in_block": sum(1 for r in lit if r["kept"]),
                        "answer_not_kept": sorted(r["id"] for r in lit if not r["kept"])}
    out: dict[str, Any] = {"questions": len(sample["ids"]), "variants": report}
    if answer_with:
        out["answers"] = _answer(lme, data, by_id, blocks, answer_with)
        for name, row in out["answers"]["variants"].items():
            report[name]["answered"] = row["answered"]
    return out


#: DeepSeek's API, as ``longmemeval_bench.py answer`` asks it.
DEEPSEEK = "https://api.deepseek.com"


def _answer(lme: Any, data: Path, by_id: dict[str, Any], blocks: dict[str, dict[str, str]],
            model: str) -> dict[str, Any]:
    """LongMemEval's reader answers from each variant's block and its judge
    scores the answer (``longmemeval_bench.py``'s prompts and client). A
    block already answered is not asked again: the cache, in the data folder,
    is keyed by the question and the block's text."""
    import hashlib
    from concurrent.futures import ThreadPoolExecutor

    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        raise SystemExit("set DEEPSEEK_API_KEY to answer")
    cache_path = data / "cache" / f"bounds-answers-{model}.json"
    cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    chat = lme.Chat(model=model, base_url=DEEPSEEK, key=key)
    def job_key(qid: str, block: str) -> str:
        return hashlib.sha256(f"{qid}\n{block}".encode()).hexdigest()

    jobs = {job_key(qid, block): (qid, block) for per_q in blocks.values() for qid, block in per_q.items()}
    fresh = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    def run(job_key: str) -> None:
        qid, block = jobs[job_key]
        item = by_id[qid]
        entry = cache.setdefault(job_key, {})
        if "hypothesis" not in entry:
            text, usage = chat.ask(lme.READER.format(block, item["question_date"], item["question"]), max_tokens=800)
            entry.update({"hypothesis": text, "reader_usage": usage})
        if "verdict" not in entry and entry["hypothesis"]:
            verdict, usage = chat.ask(lme.judge_prompt(item, entry["hypothesis"]), max_tokens=10)
            entry.update({"verdict": verdict, "judge_usage": usage})

    todo = [k for k in jobs if "verdict" not in cache.get(k, {})]
    with ThreadPoolExecutor(max_workers=8) as pool:
        for n, _ in enumerate(pool.map(run, todo), 1):
            if n % 25 == 0:
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                print(f"  {n} of {len(todo)} answered and judged", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    for k in todo:
        for part in ("reader_usage", "judge_usage"):
            usage = cache[k].get(part) or {}
            if usage and "error" not in usage:
                fresh["calls"] += 1
                fresh["prompt_tokens"] += int(usage.get("prompt_tokens", 0))
                fresh["completion_tokens"] += int(usage.get("completion_tokens", 0))
    result: dict[str, Any] = {"model": model, "new_calls": fresh, "variants": {}}
    for name, per_q in blocks.items():
        right = {qid: str(cache[job_key(qid, block)].get("verdict", "")).strip().lower().startswith("yes")
                 for qid, block in per_q.items()}
        result["variants"][name] = {"answered": round(sum(right.values()) / len(right), 4),
                                    "right": sorted(q for q, ok in right.items() if ok)}
    return result


def _cmd_main(variants: list[str]) -> dict[str, Any]:
    from kazma_core.memory import benchmark
    from kazma_core.memory import recall as rm

    replay = benchmark.ReplayEmbedder(VECTORS)
    data = benchmark.load_dataset()
    questions = list(data["questions"]) + list(data.get("second") or [])
    from datetime import UTC

    from kazma_core.memory.question_shape import standalone_query

    report: dict[str, Any] = {}
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: replay), \
            mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: replay.model), \
            mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC), \
            benchmark._seeded_store(data) as (conn, refs, _covers):
        gold_of = {q["id"]: {refs.get(a, a) for piece in q["gold"] for a in piece.split("|")} for q in questions}
        for name in variants:
            variant = VARIANTS[name]
            rows = []
            for q in questions:
                query = standalone_query(q["text"], q.get("context"))
                now = benchmark.BASE_TIME + float(q.get("now_day", benchmark._ASKED_ON_DAY)) * 86400
                with bounded(variant, query):
                    result = rm.recall(query, conn=conn, tenant_id=benchmark.BENCH_TENANT, limit=5,
                                       local_only=True, now=now)
                    block = render(result, variant, query, conn)
                gold = gold_of[q["id"]]
                rows.append({
                    "chars": len(block),
                    "left_out": len(result.beliefs) + len(result.episodes) + len(result.summaries)
                    - lines_shown(block),
                    "gold_cut": sum(1 for h in result.episodes if h.id in gold and (h.metadata or {}).get("more"))
                    + sum(1 for h in result.summaries
                          if h.id in gold and str((h.metadata or {}).get("display") or "").endswith("…")),
                })
            report[name] = {**variant, **_sizes(rows), "answering_memories_shown_in_part":
                            sum(r["gold_cut"] for r in rows)}
    return {"questions": len(questions), "variants": report}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("main")
    m.add_argument("--out", type=Path)
    m.add_argument("--variants", default=",".join(VARIANTS))
    lme = sub.add_parser("longmemeval")
    lme.add_argument("--data", type=Path, required=True)
    lme.add_argument("--out", type=Path)
    lme.add_argument("--variants", default=",".join(VARIANTS))
    lme.add_argument("--answer", metavar="MODEL", help="also answer each block with this DeepSeek model and judge it")
    args = parser.parse_args(argv)
    names = [v.strip() for v in args.variants.split(",") if v.strip()]
    unknown = [v for v in names if v not in VARIANTS]
    if unknown:
        parser.error(f"unknown variants: {unknown}")
    report = (_cmd_main(names) if args.cmd == "main"
              else _cmd_longmemeval(args.data, names, answer_with=args.answer))
    text = json.dumps(report, indent=1, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    for name, row in report["variants"].items():
        print(f"{name:<11} " + "  ".join(f"{k} {v}" for k, v in row.items()
                                         if k not in ("q", "a", "one", "summary", "budget", "cut", "answer_not_kept",
                                                      "share_min", "share_max", "order", "lead", "rest_min",
                                                      "list_cut", "lists")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
