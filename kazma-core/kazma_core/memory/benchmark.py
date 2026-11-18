"""Memory retrieval benchmark (Stage 2, R7): the real write path, the real recall().

A persona's life across dated chat sessions inside a large pool of unrelated
chat (``benchmark_data/dataset.json``, built by ``scripts/memory_bench.py``)
is written into a PRIVATE database the way the product writes it: chat turns
through ``dual_write.episode_row`` (the row, tier and embedded text the live
writer computes), and the facts extraction stores from them through
``mutate_belief`` (linked to their turn, superseding in the order they were
said, and kept private: nothing reaches a mirror or a shared index), and
(dataset v3) weekly topic summaries as ``topic_summaries`` stores them. Every
question then goes through ``recall()`` and is scored against the memories
that answer it -- a turn, a fact, or a weekly summary standing for one, in
the order recall shows them (facts, history, then weekly summaries).

Scores per category (single, paraphrase, assistant, multi, update, keyword,
noise, arabic, fact, overview, abstain):

* ``hit_rate`` -- every piece of the answer injected (for ``abstain``:
  nothing injected at all);
* ``mrr`` -- reciprocal rank of the first injected answer;
* ``precision`` -- the share of what recall injected that answers the question
  (what the model has to read past otherwise);
* ``order`` -- the answer ranks above the stale or near-miss memories named
  for the question.

It never touches live memory: its own temp database, its own tenant id, and
``recall(local_only=True)`` so no state mirror or remote index is consulted.

Held out (dataset v5, Memory audit M24): ``heldout`` questions are scored the
same way and reported apart (``report["heldout"]``); recall's thresholds were
never swept on them. The extraction benchmark
(:func:`run_extraction_benchmark`) scores what the heuristic pass -- and,
from recorded replies, the model's deep pass -- keeps from single turns in
English, Arabic and mixed.
"""

from __future__ import annotations

import json
import os
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = [
    "DATASET_PATH",
    "EXTRACTION_PATH",
    "EXTRACTION_SCORES",
    "RECORD_AGAIN",
    "RecordingEmbedder",
    "ReplayEmbedder",
    "StaleRecordingError",
    "check_recording",
    "extraction_prompt_sha256",
    "extraction_scores",
    "gated_scores",
    "load_dataset",
    "record_llm_extraction",
    "recording_identity",
    "replayed_extraction",
    "run_benchmark",
    "run_extraction_benchmark",
]

DATASET_PATH = Path(__file__).with_name("benchmark_data") / "dataset.json"
EXTRACTION_PATH = Path(__file__).with_name("benchmark_data") / "extraction.json"
BENCH_TENANT = "bench"
#: A fixed clock, so every run seeds the same created_at values.
BASE_TIME = 1780000000.0


def load_dataset(path: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    return json.loads(Path(path or DATASET_PATH).read_text(encoding="utf-8"))


def _seed(conn: sqlite3.Connection, dataset: dict[str, Any]) -> dict[str, str]:
    """Write every turn and fact as the product would; return ``ref -> row id``."""
    from kazma_core.memory.belief_mutation import mutate_belief
    from kazma_core.memory.dual_write import episode_row
    from kazma_core.memory.embedder import encode_text_to_blob

    refs: dict[str, str] = {}
    for session in dataset["sessions"]:
        for i, turn in enumerate(session["turns"], 1):
            row = episode_row(
                session_id=f"bench-{session['id']}",
                turn_number=i,
                user_text=turn["user"],
                assistant_text=turn["assistant"],
                tenant_id=BENCH_TENANT,
                source="benchmark",
                created_at=BASE_TIME + session["day"] * 86400 + i * 60,
            )
            blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
            conn.execute(
                "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, "
                "user_text, assistant_text, summary_text, tier, structural_importance, "
                "created_at, metadata_json, embedding_model_version, embedding) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row["id"], row["tenant_id"], row["session_id"], row["turn_number"],
                    row["user_text"], row["assistant_text"], row["summary_text"],
                    row["tier"], row["importance"], row["created_at"],
                    json.dumps(row["meta"], ensure_ascii=False),
                    row["embedding_model_version"], blob,
                ),
            )
            refs[f"{session['id']}#{i}"] = row["id"]
    conn.commit()
    for fact in dataset.get("facts", []):
        stated = BASE_TIME + fact["day"] * 86400 + (fact["turn"] or 0) * 60 + 30
        result = mutate_belief(
            conn,
            fact["subject"],
            fact["predicate"],
            fact["object"],
            predicate_type=fact["predicate_type"],
            confidence=0.9,
            importance=fact["importance"],
            extraction_method=fact["extraction_method"],
            tenant_id=BENCH_TENANT,
            source_session=f"bench-{fact['session']}" if fact["session"] else None,
            source_turn=fact["turn"],
            now=stated,
            private=True,
        )
        if not result.get("belief_id"):
            raise RuntimeError(f"fact {fact['id']} was not stored: {result}")
        refs[fact["id"]] = str(result["belief_id"])
    _seed_summaries(conn, dataset, refs)
    return refs


def _seed_summaries(conn: sqlite3.Connection, dataset: dict[str, Any], refs: dict[str, str]) -> None:
    """Write the weekly topic summaries (dataset v3) as ``topic_summaries``
    stores them: the same text embedded, the turns they came from linked."""
    from kazma_core.memory.embedder import encode_text_to_blob, get_embedding_model_name

    model = get_embedding_model_name()
    for s in dataset.get("summaries", []):
        start = BASE_TIME + s["day"] * 86400
        blob = encode_text_to_blob(f"{s['title']}\n{s['text']}")
        row_id = f"bench-{s['id']}"
        conn.execute(
            "INSERT INTO memory_summaries (id, tenant_id, period_key, period_start, period_end, "
            "title, summary_text, status, turn_count, chat_count, created_at, updated_at, "
            "embedding, embedding_model_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, ?)",
            (row_id, BENCH_TENANT, f"bench-week-{s['day'] // 7}", start, start + 7 * 86400,
             s["title"], s["text"], len(s["sources"]), start + 8 * 86400, start + 8 * 86400,
             blob, model if blob else None),
        )
        conn.executemany(
            "INSERT INTO memory_summary_sources (summary_id, episode_id) VALUES (?, ?)",
            [(row_id, refs[r]) for r in s["sources"]],
        )
        refs[s["id"]] = row_id
    conn.commit()


def _covers(dataset: dict[str, Any], refs: dict[str, str]) -> dict[str, set[str]]:
    """Summary row id -> the memories it stands for: its turns, and the facts
    extracted from them."""
    by_turn: dict[str, set[str]] = {}
    for f in dataset.get("facts", []):
        if f["session"]:
            by_turn.setdefault(f"{f['session']}#{f['turn']}", set()).add(refs[f["id"]])
    return {
        refs[s["id"]]: {refs[t] for t in s["sources"]} | set().union(*(by_turn.get(t, set()) for t in s["sources"]))
        for s in dataset.get("summaries", [])
    }


def _score(
    question: dict[str, Any], refs: dict[str, str], injected: list[str],
    covers: dict[str, set[str]] | None = None,
) -> dict[str, Any]:
    """Score one question against what recall injected, in the order it is shown.

    Each gold entry is one piece of the answer, "a|b" when either memory
    gives it (the turn, the fact extracted from it, or a weekly summary). A
    weekly summary that stands for an answering turn or fact (*covers*)
    answers too: the model reads the answer in it.
    """
    covers = covers or {}
    pieces = [[refs[a] for a in piece.split("|")] for piece in question["gold"]]
    below = [refs[r] for r in question.get("below", [])]
    rank: dict[str, int] = {}
    for i, item in enumerate(injected, 1):
        rank.setdefault(item, i)
    # Where each memory's content is shown: itself, or a summary standing for it.
    shown = dict(rank)
    for i, item in enumerate(injected, 1):
        for covered in covers.get(item, ()):
            shown.setdefault(covered, i)
    out: dict[str, Any] = {
        "id": question["id"],
        "category": question["category"],
        "injected": len(injected),
    }
    if not pieces:
        out["hit"] = not injected
        return out
    answers = {a for piece in pieces for a in piece}
    ranks = [shown[a] for a in answers if a in shown]
    out["hit"] = all(any(a in shown for a in piece) for piece in pieces)
    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
    out["precision"] = (
        sum(1 for i in injected if i in answers or covers.get(i, set()) & answers) / len(injected)
        if injected else 0.0
    )
    if below:
        best = min(ranks) if ranks else None
        out["order"] = best is not None and all(best < rank[b] for b in below if b in rank)
    return out


def _summarise(results: list[dict[str, Any]], latencies: list[float]) -> dict[str, Any]:
    cats: dict[str, dict[str, Any]] = {}
    for r in results:
        c = cats.setdefault(r["category"], {"n": 0, "hits": 0, "rr": [], "precision": [], "order": []})
        c["n"] += 1
        c["hits"] += int(bool(r["hit"]))
        if "rr" in r:
            c["rr"].append(r["rr"])
            c["precision"].append(r["precision"])
        if "order" in r:
            c["order"].append(bool(r["order"]))
    summary: dict[str, Any] = {}
    for name, c in sorted(cats.items()):
        row: dict[str, Any] = {"n": c["n"], "hit_rate": round(c["hits"] / c["n"], 4)}
        if c["rr"]:
            row["mrr"] = round(statistics.fmean(c["rr"]), 4)
            row["precision"] = round(statistics.fmean(c["precision"]), 4)
        if c["order"]:
            row["order"] = round(sum(c["order"]) / len(c["order"]), 4)
        summary[name] = row
    answerable = [r for r in results if "rr" in r]
    lat = sorted(latencies)
    return {
        "categories": summary,
        "overall": {
            "questions": len(results),
            "hit_rate": round(statistics.fmean([s["hit_rate"] for s in summary.values()]), 4),
            "mrr": round(statistics.fmean([r["rr"] for r in answerable]), 4) if answerable else 0.0,
            "precision": round(statistics.fmean([r["precision"] for r in answerable]), 4)
            if answerable else 0.0,
            "latency_p50_ms": round(lat[len(lat) // 2] * 1000, 1) if lat else 0.0,
            "latency_p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))] * 1000, 1)
            if lat else 0.0,
        },
    }


def gated_scores(report: dict[str, Any]) -> dict[str, float]:
    """The scores the ratchet holds (``tests/fixtures/memory_bench/thresholds.json``).

    Overall hit rate, MRR and precision, and each category's hit rate, MRR,
    precision and order. Latency is not gated: it is the machine's, not the
    code's.
    """
    out: dict[str, float] = {}
    for prefix, part in (("", report), ("heldout.", report.get("heldout"))):
        if not part:
            continue
        out.update({f"{prefix}overall.{k}": float(v) for k, v in part["overall"].items()
                    if k in ("hit_rate", "mrr", "precision")})
        for cat, row in part["categories"].items():
            for k in ("hit_rate", "mrr", "precision", "order"):
                if k in row:
                    out[f"{prefix}{cat}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


def run_benchmark(
    dataset: dict[str, Any] | None = None,
    *,
    limit: int = 5,
    keep_results: bool = False,
) -> dict[str, Any]:
    """Seed a private database, ask every question, return the scores."""
    from kazma_core.memory.recall import recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    data = dataset or load_dataset()
    fd, path = tempfile.mkstemp(prefix="kazma-bench-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        refs = _seed(conn, data)
        covers = _covers(data, refs)
        def ask(questions: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[float]]:
            results, latencies = [], []
            for q in questions:
                started = time.perf_counter()
                res = recall(q["text"], conn=conn, tenant_id=BENCH_TENANT, limit=limit,
                             local_only=True)
                latencies.append(time.perf_counter() - started)
                # Facts, history, then weekly summaries: the order format_recall_block shows.
                injected = (
                    [h.id for h in res.beliefs[:limit]] + [h.id for h in res.episodes[:limit]]
                    + [h.id for h in res.summaries]
                )
                results.append(_score(q, refs, injected, covers))
            return results, latencies

        results, latencies = ask(data["questions"])
        report = _summarise(results, latencies)
        report["memories"] = len(refs)
        if data.get("heldout"):
            held, held_latencies = ask(data["heldout"])
            report["heldout"] = _summarise(held, held_latencies)
            if keep_results:
                report["heldout"]["results"] = held
        if keep_results:
            report["results"] = results
        return report
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass


# ── Recorded vectors: the real model's answers, replayed without the model ──
#
# CI cannot load bge-m3 (torch). ``scripts/memory_bench.py vectors`` runs the
# benchmark once with the real embedder inside a RecordingEmbedder and saves
# every text it was asked for; ReplayEmbedder answers the same texts from that
# file (int8 per vector, with its scale). A text the file does not hold means
# the dataset or the embedded text changed: regenerate, do not guess.


def _text_key(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


class RecordingEmbedder:
    """Wraps a real embedder and keeps every vector it returns."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.dim = getattr(inner, "dim", None)
        self.seen: dict[str, list[float]] = {}

    def encode(self, text: str) -> list[float]:
        vec = self.inner.encode(text)
        if vec:
            self.seen[text] = [float(x) for x in vec]
        return vec

    def save(self, path: str | os.PathLike[str], *, model: str) -> int:
        import numpy as np

        texts = sorted(self.seen)
        vecs = np.asarray([self.seen[t] for t in texts], dtype=np.float32)
        scale = np.maximum(np.abs(vecs).max(axis=1), 1e-12) / 127.0
        quant = np.clip(np.rint(vecs / scale[:, None]), -127, 127).astype(np.int8)
        np.savez_compressed(
            path,
            keys=np.asarray([_text_key(t) for t in texts]),
            vecs=quant,
            scale=scale.astype(np.float32),
            model=np.asarray(model),
        )
        return len(texts)


class ReplayEmbedder:
    """Answers the recorded texts with the recorded vectors."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        import numpy as np

        data = np.load(path)
        self._vecs = data["vecs"]
        self._scale = data["scale"]
        self._index = {str(k): i for i, k in enumerate(data["keys"])}
        self.model = str(data["model"])
        self.dim = int(self._vecs.shape[1])

    def encode(self, text: str) -> list[float]:
        i = self._index.get(_text_key(text))
        if i is None:
            raise KeyError(
                f"no recorded vector for {text[:60]!r}: the benchmark's texts changed; "
                "run: python scripts/memory_bench.py vectors"
            )
        return (self._vecs[i].astype("float32") * self._scale[i]).tolist()


# ── Extraction (Memory audit M24; scored by value since 2026-11) ───────────
#
# What the two extraction passes keep from one chat turn, scored against the
# facts a reader would keep (``benchmark_data/extraction.json``, built by
# ``scripts/memory_bench.py build``). A gold fact lists the acceptable
# (subject, predicate names, value); a turn with no gold fact should give
# nothing. Each fact a pass keeps is one of:
#
# * *found*: a gold fact's value under its subject -- or the same relation
#   stated from the other side ("lulwa child_of user" for "user daughter
#   Lulwa") -- under any predicate name. A reader calls that fact kept.
# * *an extra*: true, and not asked for (``extras``: "moved from Ooredoo").
# * *unwanted*: anything else, and anything the turn says must not be kept
#   (``avoid``: a sister's job filed as the user's own).
#
# Scores per pass and language: ``found`` (the gold facts kept, under any
# name), ``named`` (of those, the share kept under a listed name, by the
# same-words rule ``predicates._predicate_key``: the name is what varies
# between askings, and two names for one fact keep both current), ``right``
# (the kept facts that are not unwanted) and ``quiet`` (the turns with
# nothing to keep that kept nothing). Until 2026-11 a fact counted only under
# a listed name, so a fact kept under another name was "not found" and
# "wrong" at once (benchmark audit F1); each row still carries that reading
# as ``recall`` and ``precision``, ungated, for comparison.
#
# The model's pass replays recorded replies
# (``tests/fixtures/memory_bench/extraction_llm.json``, recorded with
# ``scripts/memory_extraction_record.py`` on the configured model). A
# recording says what it is: the hash of the system prompt it answered, the
# model, the provider, the date and the askings a turn
# (:func:`recording_identity`). A recording of another prompt is refused
# (:class:`StaleRecordingError`), and a turn it does not hold is an error, never a
# guess: either way, record again.

#: What to run when a recording does not answer what the code asks.
RECORD_AGAIN = "python scripts/memory_extraction_record.py --out <fixture>"


class StaleRecordingError(ValueError):
    """The recorded replies do not answer what the extractor asks today."""


def _sha256(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def extraction_prompt_sha256() -> str:
    """The hash of the system prompt the deep pass sends."""
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM

    return _sha256(_EXTRACT_SYSTEM)


def recording_identity(llm_replies: dict[str, Any]) -> dict[str, Any]:
    """What a recording is. ``sha256`` covers its replies alone, so the
    description beside them can be completed without making it another
    recording."""
    replies = llm_replies.get("replies") or {}
    return {
        "sha256": _sha256(json.dumps(replies, ensure_ascii=False, sort_keys=True)),
        "turns": len(replies),
        "askings": int(llm_replies.get("askings") or 1),
        "model": llm_replies.get("model"),
        "provider": llm_replies.get("provider"),
        "recorded_on": llm_replies.get("recorded_on"),
        "prompt_sha256": llm_replies.get("prompt_sha256"),
    }


def check_recording(llm_replies: dict[str, Any]) -> None:
    """Refuse a recording that does not say which prompt it answered, or
    answered another one (benchmark audit F4: with the prompt replaced by
    "Keep nothing", every deep-pass score stayed the same)."""
    recorded = llm_replies.get("prompt_sha256")
    if not recorded:
        raise StaleRecordingError(f"the recording does not say which extraction prompt it answered: {RECORD_AGAIN}")
    if recorded != extraction_prompt_sha256():
        raise StaleRecordingError(f"the extraction prompt changed since the recording: {RECORD_AGAIN}")


def _slug(text: str) -> str:
    return "_".join(str(text or "").strip().lower().replace("-", " ").split())


def _folded(text: str) -> str:
    """A value as search reads it: folded, and an underscore separates words
    (``memory/query_terms``, FTS5) -- the extraction prompt slugs values, so
    ``kuwait_city`` is the user's "Kuwait City"."""
    from kazma_core.documents.arabic import fold_for_search

    return " ".join(fold_for_search(str(text or "").replace("_", " ")).lower().split())


def _holds(gold: str, value: str) -> bool:
    return bool(gold and value) and (gold in value or value in gold)


def _value_matches(alt: dict[str, Any], got: dict[str, Any]) -> bool:
    """*got* keeps *alt*'s value under its subject, under any name -- or
    states the same relation from the other side (subject and value
    swapped)."""
    gold = _folded(alt["object"])
    subject = _slug(alt["subject"])
    if _slug(got.get("subject")) == subject and _holds(gold, _folded(got.get("object"))):
        return True
    return _slug(got.get("object")) == subject and _holds(gold, _folded(got.get("subject")))


def _fact_matches(alternatives: list[dict[str, Any]], got: dict[str, Any]) -> bool:
    """*got* keeps one of the gold fact's alternatives under its subject AND a
    listed name (the reading before 2026-11, now the ``named`` score)."""
    from kazma_core.memory.predicates import _predicate_key

    subject, predicate, value = _slug(got.get("subject")), got.get("predicate") or "", _folded(got.get("object"))
    if not value:
        return False
    for alt in alternatives:
        if _slug(alt["subject"]) != subject:
            continue
        if _predicate_key(predicate) not in {_predicate_key(p) for p in alt["predicates"]}:
            continue
        if _holds(_folded(alt["object"]), value):
            return True
    return False


def _avoided(entry: dict[str, Any], got: dict[str, Any]) -> bool:
    """*got* is a fact the turn says must not be kept: the entry's subject and
    value, under one of its names (any name when it lists none)."""
    from kazma_core.memory.predicates import _predicate_key

    if _slug(got.get("subject")) != _slug(entry["subject"]):
        return False
    names = entry.get("predicates")
    if names and _predicate_key(got.get("predicate") or "") not in {_predicate_key(p) for p in names}:
        return False
    return _holds(_folded(entry["object"]), _folded(got.get("object")))


def _score_extraction(turns: list[dict[str, Any]], extracted: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    by_lang: dict[str, dict[str, Any]] = {}
    for turn in turns:
        row = by_lang.setdefault(turn["lang"], {
            "turns": 0, "gold": 0, "gold_found": 0, "gold_named": 0, "kept": 0, "kept_unwanted": 0,
            "kept_listed": 0, "quiet_turns": 0, "quiet_kept_nothing": 0, "unwanted": []})
        got = extracted.get(turn["id"], [])
        gold = turn["facts"]
        allowed = gold + (turn.get("extras") or [])
        avoid = turn.get("avoid") or []
        # A fact the turn says must not be kept finds nothing, whatever value
        # it holds: "user studies_at Bayan School" holds the value of "user
        # daughter_school Bayan School", and says something false.
        good = [g for g in got if not any(_avoided(e, g) for e in avoid)]
        row["turns"] += 1
        row["gold"] += len(gold)
        row["kept"] += len(got)
        row["gold_found"] += sum(1 for alts in gold if any(_value_matches(a, g) for a in alts for g in good))
        row["gold_named"] += sum(1 for alts in gold if any(_fact_matches(alts, g) for g in good))
        row["kept_listed"] += sum(1 for g in got if any(_fact_matches(alts, g) for alts in gold))
        for g in got:
            if g not in good or not any(_value_matches(a, g) for alts in allowed for a in alts):
                row["kept_unwanted"] += 1
                row["unwanted"].append(f"{turn['id']}: {g.get('subject')} {g.get('predicate')} {g.get('object')}")
        if not gold:
            row["quiet_turns"] += 1
            row["quiet_kept_nothing"] += int(not got)
    out = {}
    for lang, row in sorted(by_lang.items()):
        found, kept = row["gold_found"], row["kept"]
        out[lang] = {
            **row,
            "found": round(found / row["gold"], 4) if row["gold"] else 0.0,
            "named": round(row["gold_named"] / found, 4) if found else 1.0,
            "right": round((kept - row["kept_unwanted"]) / kept, 4) if kept else 1.0,
            "quiet": round(row["quiet_kept_nothing"] / row["quiet_turns"], 4) if row["quiet_turns"] else 1.0,
            # The reading before 2026-11 (a fact counts only under a listed
            # name), kept for comparison; not gated.
            "recall": round(row["gold_named"] / row["gold"], 4) if row["gold"] else 0.0,
            "precision": round(row["kept_listed"] / kept, 4) if kept else 1.0,
        }
    return out


def _llm_blob(user_text: str) -> str:
    """The user message ``belief_extractor._llm_extract`` sends for a turn
    with no vocabulary (the benchmark asks with none)."""
    return f"User: {user_text[:1500]}\nAssistant: "


class _ReplayClient:
    """Answers the extraction prompt from recorded replies; a prompt nobody
    recorded is an error (the dataset changed: record again). It also checks
    the system message it is sent against the recording's: a prompt built
    another way at run time is a recording of something else."""

    def __init__(self, replies: dict[str, str], prompt_sha256: str) -> None:
        self._replies = replies
        self._prompt = prompt_sha256
        self.stale = False

    async def chat(self, messages: list[dict[str, Any]], **_kw: Any) -> str:
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        if _sha256(system) != self._prompt:
            # Raised after the run: the extractor reads any failed call as
            # "the model is down".
            self.stale = True
            return ""
        key = _text_key(messages[-1]["content"])
        if key not in self._replies:
            raise KeyError(f"no recorded reply for this turn: {RECORD_AGAIN}")
        return self._replies[key]


def replayed_extraction(turns: list[dict[str, Any]], llm_replies: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """What the deep pass keeps from each turn, from the recorded replies,
    read by the product's own parser and sanitizer."""
    import asyncio
    from unittest import mock

    from kazma_core.memory.belief_extractor import _llm_extract, _sanitize_belief

    check_recording(llm_replies)
    missing = [t["id"] for t in turns if _text_key(_llm_blob(t["user"])) not in llm_replies["replies"]]
    if missing:
        # Checked here: the extractor reads any failed call as "the model is
        # down", which would score a stale recording as an outage.
        raise KeyError(f"no recorded reply for {missing}: {RECORD_AGAIN}")
    client = _ReplayClient(llm_replies["replies"], llm_replies["prompt_sha256"])
    registry = type("_Registry", (), {"get_client": lambda self: client})()

    async def ask_all() -> dict[str, list[dict[str, Any]]]:
        got = {}
        for t in turns:
            beliefs, _down = await _llm_extract(t["user"], "")
            got[t["id"]] = [b for b in (_sanitize_belief(x) for x in beliefs or []) if b]
        return got

    with mock.patch("kazma_core.model_registry.get_model_registry", lambda: registry):
        extracted = asyncio.run(ask_all())
    if client.stale:
        raise StaleRecordingError(f"the extractor sent another system prompt than the recording answered: {RECORD_AGAIN}")
    return extracted


def run_extraction_benchmark(
    dataset: dict[str, Any] | None = None, *, llm_replies: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score the heuristic pass, and the model's pass when *llm_replies* (the
    recorded file) is given."""
    from kazma_core.memory.belief_extractor import extract_beliefs_heuristic

    data = dataset or json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))
    turns = data["turns"]
    report: dict[str, Any] = {
        "version": data["version"],
        "heuristic": _score_extraction(turns, {t["id"]: extract_beliefs_heuristic(t["user"]) for t in turns}),
    }
    if not llm_replies:
        report["llm"] = {"recorded": False}
        return report
    report["llm"] = _score_extraction(turns, replayed_extraction(turns, llm_replies))
    report["llm_recording"] = recording_identity(llm_replies)
    return report


#: The extraction scores the lock holds, per pass and language.
EXTRACTION_SCORES = ("found", "named", "right", "quiet")


def extraction_scores(report: dict[str, Any]) -> dict[str, float]:
    """The extraction scores the lock holds: per pass and language, ``found``,
    ``named``, ``right`` and ``quiet``."""
    out = {}
    for pass_name in ("heuristic", "llm"):
        part = report.get(pass_name) or {}
        for lang, row in part.items():
            if isinstance(row, dict):
                for k in EXTRACTION_SCORES:
                    out[f"{pass_name}.{lang}.{k}"] = float(row[k])
    return dict(sorted(out.items()))


async def record_llm_extraction(dataset: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ask the configured model the extraction prompt for every turn and
    return its raw replies (``scripts/memory_extraction_record.py``;
    costs one call a turn)."""
    from kazma_core.model_registry import get_model_registry

    data = dataset or json.loads(EXTRACTION_PATH.read_text(encoding="utf-8"))
    registry = get_model_registry()
    client = registry.get_client()
    replies: dict[str, str] = {}
    from kazma_core.memory.belief_extractor import _EXTRACT_SYSTEM

    for t in data["turns"]:
        blob = _llm_blob(t["user"])
        raw = await client.chat([{"role": "system", "content": _EXTRACT_SYSTEM}, {"role": "user", "content": blob}])
        replies[_text_key(blob)] = raw.content if hasattr(raw, "content") else str(raw or "")
    # The provider client keeps its model on its config (``LLMProvider.config``).
    model = (getattr(getattr(client, "config", None), "model", None) or getattr(client, "model", None)
             or getattr(registry, "active_model", None) or "unknown")
    import datetime as _dt

    return {"model": str(model), "provider": str(getattr(registry, "_active_provider", "") or "unknown"),
            "recorded_on": _dt.date.today().isoformat(), "askings": 1,
            "prompt_sha256": _sha256(_EXTRACT_SYSTEM), "extraction_version": data["version"],
            "replies": replies}
