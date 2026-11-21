#!/usr/bin/env python3
"""LongMemEval through Kazma's memory (the memory framework audit's part E,
the owner's decision 7).

LongMemEval (Wu et al., 2024; code and dataset card MIT,
https://github.com/xiaowu0162/LongMemEval) asks 500 questions, each of a
user's own chat history: about 48 sessions, the one or few that hold the
answer among unrelated ones, on dated days. Kazma is measured on a fixed,
stratified sample of it, through its own write path and its own
``recall()``, the way a chat turn reaches memory and a question is answered.

The data is downloaded once, into a folder of its own outside the
repository (``--data``: ``longmemeval_s_cleaned.json`` and
``longmemeval_oracle.json`` from the Hugging Face dataset
``xiaowu0162/longmemeval-cleaned``). It is read as untrusted data and never
committed: the repository holds this script, the sample's question ids
(``tests/fixtures/longmemeval/sample.json``) and result files of ids and
scores only (``tests/fixtures/longmemeval/*.json``).

    python scripts/longmemeval_bench.py sample --data DIR    # the sample's ids
    python scripts/longmemeval_bench.py split --data DIR     # dev 200 / report 300
    python scripts/longmemeval_bench.py retrieve --data DIR  # recall on each history: is the evidence shown?
    DEEPSEEK_API_KEY=... python scripts/longmemeval_bench.py answer --data DIR
        # end to end: one reader answers from Kazma's recall, from the whole
        # history and from the answer sessions alone (the two baselines);
        # LongMemEval's own judge prompts score each answer
    DEEPSEEK_API_KEY=... python scripts/longmemeval_bench.py translate --data DIR
    python scripts/longmemeval_bench.py parallel --data DIR
        # the parallel English and Arabic set: 40 of the sample's questions,
        # their answer sessions translated (cached in DIR, never committed),
        # each question asked in both languages of one shared store

``answer`` asks a model (``--model``, ``deepseek-flash`` by default, on
DeepSeek's API; the key from ``DEEPSEEK_API_KEY``, never written): 3 reader
and 3 judge calls a question. The replies are kept in ``DIR`` (a run that
stops goes on where it stopped); the result file holds the scores and the
tokens each call used.

``retrieve`` loads the configured embedder (bge-m3) and embeds every turn
of the sample's histories once, into a cache in ``DIR``; it makes no other
model call. Each question gets a private database holding its own history,
one memory per round (the user's message and the assistant's reply), dated
as the dataset dates the session; no facts are extracted (the quick pass
and the deep pass are left out, said in the result). It writes nothing of
an install: the benchmark's data folder is a temporary one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "longmemeval"
SAMPLE = FIXTURES / "sample.json"
RETRIEVAL = FIXTURES / "retrieval.json"
ANSWERS = FIXTURES / "answers.json"
PARALLEL = FIXTURES / "parallel.json"
#: The dev and report splits (the memory plan's W0, 2026-11-22): dev is the
#: sample and 100 more questions, used to choose; report is the other 300,
#: never used to choose anything, every look at it logged in LOOKS.
SPLIT = FIXTURES / "split.json"
LOOKS = FIXTURES / "report_looks.json"
#: Answers read by a model (Claude) beside the two judges' verdicts: ids,
#: verdicts and the reading's label only, never the text read.
MODEL_READ = FIXTURES / "model_read.json"
MODEL_READ_SIZE = 50
DEV_SIZE = 200
SPLITS = ("sample", "dev", "report")
#: The parallel set: the first answerable questions of the sample (no
#: preference questions: their answer is a style, not a memory).
PARALLEL_SIZE = 40
S_FILE = "longmemeval_s_cleaned.json"
ORACLE_FILE = "longmemeval_oracle.json"
#: The files the sample was drawn from (Hugging Face revision and SHA-256).
SOURCE = {
    "dataset": "xiaowu0162/longmemeval-cleaned",
    "revision": "98d7416c24c778c2fee6e6f3006e7a073259d48f",
    "sha256": {
        S_FILE: "d6f21ea9d60a0d56f34a05b609c79c88a451d2ae03597821ea3d5a9678c3a442",
        ORACLE_FILE: "821a2034d219ab45846873dd14c14f12cfe7776e73527a483f9dac095d38620c",
    },
}
#: The sample's size and seed. Stratified: each question type in proportion,
#: its abstention questions in proportion within it.
SAMPLE_SIZE = 100
SAMPLE_SEED = "kazma-longmemeval-2026-11-21"
TENANT = "lme"
#: Memories shown a question, as a chat turn's recall shows them.
LIMIT = 5


# ── Reading the dataset ─────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def load(data: Path, name: str = S_FILE, *, check: bool = True) -> list[dict[str, Any]]:
    """The dataset file *name* in *data*, checked against the hash the sample
    was drawn from."""
    path = data / name
    if check and _sha256(path) != SOURCE["sha256"][name]:
        raise SystemExit(f"{path}: not the file the sample was drawn from ({SOURCE['revision']})")
    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise SystemExit(f"{path}: not a list of questions")
    return items


def is_abstention(item: dict[str, Any]) -> bool:
    """LongMemEval marks a question whose answer was never said with ``_abs``."""
    return str(item.get("question_id", "")).endswith("_abs")


def draw_sample(items: list[dict[str, Any]], size: int = SAMPLE_SIZE) -> list[str]:
    """*size* question ids, each type in proportion (largest remainder), and
    within a type its abstention questions in proportion; the order is a
    hash of the id with the seed, so the draw is the same on every machine."""
    def rank(qid: str) -> str:
        return hashlib.sha256(f"{SAMPLE_SEED}:{qid}".encode()).hexdigest()

    groups: dict[tuple[str, bool], list[str]] = {}
    for item in items:
        groups.setdefault((str(item["question_type"]), is_abstention(item)), []).append(str(item["question_id"]))
    total = sum(len(v) for v in groups.values())
    quotas = {k: size * len(v) / total for k, v in groups.items()}
    taken = {k: int(q) for k, q in quotas.items()}
    for k in sorted(quotas, key=lambda k: (quotas[k] - taken[k], k), reverse=True)[: size - sum(taken.values())]:
        taken[k] += 1
    out: list[str] = []
    for k, ids in sorted(groups.items()):
        out += sorted(ids, key=rank)[: taken[k]]
    return sorted(out, key=rank)


def draw_split(items: list[dict[str, Any]], sample: list[str], size: int = DEV_SIZE) -> dict[str, list[str]]:
    """Dev: the sample and ``size - len(sample)`` more answerable questions,
    drawn as the sample was (each type in proportion, ordered by the seed's
    hash); report: every other question. The abstention questions the sample
    did not take stay in report, so its "no answer" measure has its n (24)."""
    taken = set(sample)
    rest = [i for i in items if str(i["question_id"]) not in taken and not is_abstention(i)]
    dev = list(sample) + draw_sample(rest, size - len(sample))
    chosen = set(dev)
    report = [str(i["question_id"]) for i in items if str(i["question_id"]) not in chosen]
    return {"dev": dev,
            "report": sorted(report, key=lambda q: hashlib.sha256(f"{SAMPLE_SEED}:{q}".encode()).hexdigest())}


def question_ids(split: str) -> list[str]:
    """The question ids of *split* (``sample``, ``dev`` or ``report``)."""
    if split == "sample":
        return list(json.loads(SAMPLE.read_text(encoding="utf-8"))["ids"])
    if split not in SPLITS:
        raise SystemExit(f"unknown split {split!r}: one of {', '.join(SPLITS)}")
    return list(json.loads(SPLIT.read_text(encoding="utf-8"))[split])


def result_path(base: Path, split: str) -> Path:
    """Where a run on *split* writes: the sample's file as before, else
    ``<name>_<split>.json`` beside it."""
    return base if split == "sample" else base.with_name(f"{base.stem}_{split}{base.suffix}")


def log_look(split: str, reason: str | None, command: str) -> None:
    """A run on the report split is logged before it runs (date, reason,
    commit, command): the report split chooses nothing, and every look at it
    is on record. Refused without a reason."""
    if split != "report":
        return
    if not (reason or "").strip():
        raise SystemExit("the report split is looked at only with --look \"why\" (it is logged)")
    import subprocess

    try:
        commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=30, check=False).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        commit = ""
    held = json.loads(LOOKS.read_text(encoding="utf-8")) if LOOKS.is_file() else {"looks": []}
    held["looks"].append({"date": datetime.now(UTC).date().isoformat(), "reason": reason.strip(),
                          "commit": commit, "command": command})
    LOOKS.write_text(json.dumps(held, indent=1) + "\n", encoding="utf-8", newline="\n")


# ── A history as Kazma's memories ───────────────────────────────────────────


def parse_date(text: str) -> float:
    """LongMemEval's ``2023/05/20 (Sat) 02:21`` as a Unix time (read in UTC)."""
    day, _weekday, clock = str(text).split(" ", 2)
    return datetime.strptime(f"{day} {clock}", "%Y/%m/%d %H:%M").replace(tzinfo=UTC).timestamp()


def rounds(session: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A session's rounds: each user message with the assistant's reply that
    follows it (a reply with no message before it is a round of its own),
    and whether either message holds the answer (``has_answer``)."""
    out: list[dict[str, Any]] = []
    for message in session:
        role, text = message.get("role"), str(message.get("content") or "")
        if role == "user" or not out or out[-1]["assistant"]:
            out.append({"user": text if role == "user" else "", "assistant": "" if role == "user" else text,
                        "evidence": bool(message.get("has_answer"))})
        else:
            out[-1]["assistant"] = text
            out[-1]["evidence"] = out[-1]["evidence"] or bool(message.get("has_answer"))
    return out


def history_rows(item: dict[str, Any]) -> list[dict[str, Any]]:
    """Every round of *item*'s history as the episode row the writer stores
    (``dual_write.episode_row``), with where it came from."""
    from kazma_core.memory.dual_write import episode_row

    rows = []
    for sid, date, session in zip(item["haystack_session_ids"], item["haystack_dates"],
                                  item["haystack_sessions"], strict=True):
        start = parse_date(date)
        for i, r in enumerate(rounds(session), 1):
            row = episode_row(session_id=f"lme-{sid}", turn_number=i, user_text=r["user"],
                              assistant_text=r["assistant"], tenant_id=TENANT, source="benchmark",
                              created_at=start + i * 60)
            rows.append({"row": row, "session": str(sid), "evidence": r["evidence"]})
    return rows


class CachedEmbedder:
    """Answers from vectors embedded once (``fill``), else asks the model.
    The cache is a file in the data folder, never in the repository."""

    def __init__(self, inner: Any, path: Path, model: str) -> None:
        import numpy as np

        self.inner, self.path, self.model = inner, path, model
        self.dim = getattr(inner, "dim", None)
        self.vectors: dict[str, Any] = {}
        if path.is_file():
            held = np.load(path)
            if str(held["model"]) == model:
                self.vectors = dict(zip((str(k) for k in held["keys"]), held["vecs"], strict=True))

    @staticmethod
    def key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]

    def fill(self, texts: list[str], *, batch: int = 32) -> int:
        # Shortest first: a batch is padded to its longest text, so texts of
        # like length together cost the model least.
        todo = sorted({t for t in texts if t and self.key(t) not in self.vectors}, key=lambda t: (len(t), t))
        for at in range(0, len(todo), batch):
            part = todo[at:at + batch]
            for text, vec in zip(part, self.inner.encode_batch(part), strict=True):
                if vec:
                    self.vectors[self.key(text)] = vec
            if at // batch % 20 == 0:
                print(f"  embedded {min(at + batch, len(todo))} of {len(todo)}", flush=True)
                self.save()
        # Nothing new, nothing written: other runs may be reading the file.
        if todo:
            self.save()
        return len(todo)

    def save(self) -> None:
        import numpy as np

        keys = sorted(self.vectors)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp.npz")
        np.savez(tmp, keys=np.asarray(keys), vecs=np.asarray([self.vectors[k] for k in keys], dtype=np.float16),
                 model=np.asarray(self.model))
        os.replace(tmp, self.path)

    def encode(self, text: str) -> list[float]:
        vec = self.vectors.get(self.key(text))
        if vec is not None:
            return [float(x) for x in vec]
        vec = self.inner.encode(text)
        if vec:
            self.vectors[self.key(text)] = vec
        return vec


def _seed_history(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Write *rows* as the product's episodes; episode id -> where it came from."""
    from kazma_core.memory.embedder import encode_text_to_blob

    back: dict[str, dict[str, Any]] = {}
    for entry in rows:
        row = entry["row"]
        blob = encode_text_to_blob(row["embed_text"]) if row["embed_text"] else None
        conn.execute(
            "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, user_text, assistant_text, "
            "summary_text, tier, structural_importance, created_at, metadata_json, embedding_model_version, "
            "embedding) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
             row["assistant_text"], row["summary_text"], row["tier"], row["importance"], row["created_at"],
             json.dumps(row["meta"], ensure_ascii=False), row["embedding_model_version"], blob),
        )
        back[row["id"]] = entry
    conn.commit()
    return back


def _words(text: Any) -> str:
    return " ".join(re.findall(r"\w+", str(text).casefold()))


def answer_words(item: dict[str, Any]) -> str:
    """*item*'s answer as words, for the free proxy "the answer's text is in
    the block": a word or a phrase only (a bare number, a count, is written
    in most rounds and proves nothing), none for an abstention question."""
    words = _words(item.get("answer", ""))
    return words if words and not is_abstention(item) and re.search(r"[^\W\d_]", words) else ""


def holds_answer(text: str, words: str) -> bool:
    """Whether *text* holds *words* (``answer_words``) whole, as words."""
    return bool(words) and f" {words} " in f" {_words(text)} "


def ask(item: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, Any]:
    """*item*'s question through ``recall()`` on a private database holding
    its history: what was shown, whether it holds the evidence, and the free
    proxies (the memory plan's W0): every answer session shown, and the
    answer's text in the shown rounds and in the block the model is shown."""
    from unittest import mock

    from kazma_core.memory.recall import format_recall_block, recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    fd, path = tempfile.mkstemp(prefix="kazma-lme-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        back = _seed_history(conn, rows)
        started = time.perf_counter()
        with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
            result = recall(str(item["question"]), conn=conn, tenant_id=TENANT, limit=LIMIT,
                            local_only=True, now=parse_date(item["question_date"]))
            latency = time.perf_counter() - started
            block = format_recall_block(result, explain=False) if not result.empty else ""
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
    shown = [back[h.id] for h in result.episodes if h.id in back]
    answer_sessions = {str(s) for s in item.get("answer_session_ids") or []}
    shown_sessions = {e["session"] for e in shown}
    words = answer_words(item)
    in_shown = holds_answer(" ".join(f"{e['row']['user_text']} {e['row']['assistant_text']}" for e in shown), words)
    return {
        "id": str(item["question_id"]),
        "type": str(item["question_type"]),
        "abstention": is_abstention(item),
        "shown": len(shown),
        "facts_shown": len(result.beliefs),
        "evidence_turn": any(e["evidence"] for e in shown),
        "answer_session": bool(shown_sessions & answer_sessions),
        "every_answer_session": bool(answer_sessions) and answer_sessions <= shown_sessions,
        "precision": round(sum(1 for e in shown if e["session"] in answer_sessions) / len(shown), 4) if shown else None,
        "answer_in_shown": in_shown if words else None,
        "answer_in_block": holds_answer(block, words) if words else None,
        "block_chars": len(block),
        "degraded": bool(result.degraded),
        "latency_ms": round(latency * 1000, 1),
        "memories": len(rows),
    }


def summarise(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Shares per question type and overall. An answerable question counts
    as found when a shown memory holds its evidence (``evidence_turn``) or
    comes from an answer session (``answer_session``); an abstention
    question counts as quiet when nothing at all was shown."""
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [r for r in rows if not r["abstention"]]
        quiet = [r for r in rows if r["abstention"]]
        precisions = [r["precision"] for r in answerable if r["precision"] is not None]
        out: dict[str, Any] = {"n": len(rows)}
        if answerable:
            out.update({
                "evidence_turn": round(sum(r["evidence_turn"] for r in answerable) / len(answerable), 4),
                "answer_session": round(sum(r["answer_session"] for r in answerable) / len(answerable), 4),
                "every_answer_session": round(sum(r["every_answer_session"] for r in answerable) / len(answerable), 4),
                "precision": round(statistics.fmean(precisions), 4) if precisions else 0.0,
            })
            for key in ("plain_bge_evidence", "plain_bge_every_session", "plain_bm25_evidence",
                        "plain_bm25_every_session"):
                if all(key in r for r in answerable):
                    out[key] = round(sum(r[key] for r in answerable) / len(answerable), 4)
            worded = [r for r in answerable if r.get("answer_in_block") is not None]
            if worded:
                # The free proxies: of the questions whose answer is a word or
                # a phrase, how often its text is in the rounds shown and in
                # the block itself (the difference is what the block cut).
                out.update({
                    "worded": len(worded),
                    "answer_in_shown": round(sum(r["answer_in_shown"] for r in worded) / len(worded), 4),
                    "answer_in_block": round(sum(r["answer_in_block"] for r in worded) / len(worded), 4),
                })
        if quiet:
            out["abstention_quiet"] = round(sum(1 for r in quiet if r["shown"] == 0) / len(quiet), 4)
        out["shown_mean"] = round(statistics.fmean(r["shown"] for r in rows), 2) if rows else 0.0
        chars = sorted(r["block_chars"] for r in rows if "block_chars" in r)
        if chars:
            out["block_chars_p50"] = chars[len(chars) // 2]
        return out

    types = sorted({r["type"] for r in results})
    lat = sorted(r["latency_ms"] for r in results)
    return {
        "overall": {**part(results),
                    "latency_p50_ms": lat[len(lat) // 2] if lat else 0.0,
                    "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))] if lat else 0.0},
        "types": {t: part([r for r in results if r["type"] == t]) for t in types},
    }


# ── The plain retrieval baselines ──────────────────────────────────────────
#
# What Kazma's recall is measured against (the memory plan's W0): the rounds
# nearest the question by bge-m3 alone (cosine of the same vectors Kazma
# stores, no background, no word coverage, no gap) and by BM25 over each
# round's words, PLAIN_K of each, read whole like any recalled round.

PLAIN_K = 10
#: BM25's usual constants (Robertson and Zaragoza).
BM25_K1, BM25_B = 1.5, 0.75


def _round_words(entry: dict[str, Any]) -> list[str]:
    row = entry["row"]
    return re.findall(r"\w+", f"{row['user_text']} {row['assistant_text']}".casefold())


def plain_top(question: str, rows: list[dict[str, Any]], how: str, *, embedder: Any = None,
              k: int = PLAIN_K) -> list[dict[str, Any]]:
    """The *k* rounds of *rows* nearest *question*: ``how`` is ``bge`` (the
    cosine of the stored vectors' texts, by *embedder*) or ``bm25``; best
    first, ties by the history's order."""
    import math

    if how == "bge":
        import numpy as np

        def unit(vec: Any) -> Any:
            v = np.asarray(vec, dtype=np.float32)
            return v / (np.linalg.norm(v) or 1.0)

        q = unit(embedder.encode(question))
        scored = [(float(unit(embedder.encode(e["row"]["embed_text"])) @ q), i)
                  for i, e in enumerate(rows) if e["row"]["embed_text"]]
    elif how == "bm25":
        docs = [_round_words(e) for e in rows]
        n = len(docs) or 1
        avg = sum(len(d) for d in docs) / n
        df: dict[str, int] = {}
        for d in docs:
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        terms = set(re.findall(r"\w+", question.casefold()))
        scored = []
        for i, d in enumerate(docs):
            tf: dict[str, int] = {}
            for w in d:
                if w in terms:
                    tf[w] = tf.get(w, 0) + 1
            s = sum(math.log(1 + (n - df[w] + 0.5) / (df[w] + 0.5))
                    * f * (BM25_K1 + 1) / (f + BM25_K1 * (1 - BM25_B + BM25_B * len(d) / (avg or 1)))
                    for w, f in tf.items())
            scored.append((s, i))
    else:
        raise ValueError(f"unknown baseline {how!r}")
    best = sorted(scored, key=lambda pair: (-pair[0], pair[1]))[:k]
    return [rows[i] for _s, i in best]


def read_whole(entries: list[dict[str, Any]]) -> str:
    """Rounds as a reader reads them whole: dated, oldest first."""
    shown = sorted((e["row"] for e in entries), key=lambda r: r["created_at"])
    return "\n\n".join(
        f"[{datetime.fromtimestamp(r['created_at'], UTC):%Y/%m/%d %H:%M}]\nUser: {r['user_text']}\n"
        f"Assistant: {r['assistant_text']}" for r in shown)


def plain_scores(item: dict[str, Any], rows: list[dict[str, Any]], embedder: Any) -> dict[str, Any]:
    """Each baseline's retrieval measures for *item*: the evidence round among
    its rounds, and every answer session among them."""
    answer_sessions = {str(s) for s in item.get("answer_session_ids") or []}
    out: dict[str, Any] = {}
    for how in ("bge", "bm25"):
        top = plain_top(str(item["question"]), rows, how, embedder=embedder)
        sessions = {e["session"] for e in top}
        out[f"plain_{how}_evidence"] = any(e["evidence"] for e in top)
        out[f"plain_{how}_every_session"] = bool(answer_sessions) and answer_sessions <= sessions
    return out


# ── End to end: one reader, three histories, LongMemEval's judge ────────────
#
# The reader and judge prompts are LongMemEval's own (src/generation/
# run_generation.py, the plain answer prompt; src/evaluation/evaluate_qa.py,
# get_anscheck_prompt; MIT, (c) 2024 Di Wu). The reader is the same model for
# the three histories: Kazma's recall block as a chat turn's model sees it
# (``recall.format_recall_block``), the whole history (the full-context
# baseline) and the sessions holding the answer (the oracle).

READER = ("I will give you several history chats between you and a user. Please answer the question based on "
          "the relevant chat history.\n\n\nHistory Chats:\n\n{}\n\nCurrent Date: {}\nQuestion: {}\nAnswer:")
_JUDGE_HEAD = ("I will give you a question, a correct answer, and a response from a model. Please answer yes if the "
               "response contains the correct answer. Otherwise, answer no. ")
_JUDGE_TAIL = "\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only."
_JUDGE_STEPS = ("If the response is equivalent to the correct answer or contains all the intermediate steps to get "
                "the correct answer, you should also answer yes. If the response only contains a subset of the "
                "information required by the answer, answer no. ")
JUDGE = {
    "plain": _JUDGE_HEAD + _JUDGE_STEPS + _JUDGE_TAIL,
    "temporal-reasoning": _JUDGE_HEAD + _JUDGE_STEPS + (
        "In addition, do not penalize off-by-one errors for the number of days. If the question asks for the "
        "number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when "
        "the answer is 18), the model's response is still correct. ") + _JUDGE_TAIL,
    "knowledge-update": _JUDGE_HEAD + (
        "If the response contains some previous information along with an updated answer, the response should be "
        "considered as correct as long as the updated answer is the required answer.") + _JUDGE_TAIL,
    "single-session-preference": (
        "I will give you a question, a rubric for desired personalized response, and a response from a model. "
        "Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does "
        "not need to reflect all the points in the rubric. The response is correct as long as it recalls and "
        "utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: "
        "{}\n\nIs the model response correct? Answer yes or no only."),
    "abstention": (
        "I will give you an unanswerable question, an explanation, and a response from a model. Please answer "
        "yes if the model correctly identifies the question as unanswerable. The model could say that the "
        "information is incomplete, or some other information is given but the asked information is not."
        "\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the "
        "question as unanswerable? Answer yes or no only."),
}
#: Kazma's recall block as the model sees it; the same recalled rounds
#: read whole (what the model gets when it opens them with ``memory_read``,
#: which a one-shot reader cannot call); the whole history; the oracle.
CONDITIONS = ("kazma", "kazma_whole", "full", "oracle", "bge_top10", "bm25_top10")
#: The whole history, uncut (None), or cut from its oldest session to this
#: many characters. Uncut since 22 Nov: the longest of the 500 histories
#: (540,508 characters, 115,818 tokens) was served whole by deepseek-flash
#: and deepseek-v4-pro; cut at 440,000 every dev history lost sessions, and
#: 33 of the first 100 an answer session. Said in the result.
FULL_MAX_CHARS: int | None = None


def judge_prompt(item: dict[str, Any], response: str) -> str:
    """LongMemEval's ``get_anscheck_prompt`` for *item*."""
    if is_abstention(item):
        template = JUDGE["abstention"]
    else:
        template = JUDGE.get(str(item["question_type"]), JUDGE["plain"])
    return template.format(item["question"], item["answer"], response)


def history_string(item: dict[str, Any], *, only: set[str] | None = None,
                   max_chars: int | None = None) -> tuple[str, int]:
    """LongMemEval's history format (each session dated, its messages as
    JSON), oldest first; with *only*, those sessions alone. With
    *max_chars*, the oldest sessions are left out until it fits; returns
    the text and how many were left out."""
    parts = []
    for sid, date, session in zip(item["haystack_session_ids"], item["haystack_dates"],
                                  item["haystack_sessions"], strict=True):
        if only is not None and str(sid) not in only:
            continue
        messages = [{"role": m.get("role"), "content": m.get("content")} for m in session]
        parts.append((date, json.dumps(messages)))
    dropped = 0
    def render(rows: list[tuple[str, str]]) -> str:
        return "".join(f"\n### Session {i}:\nSession Date: {d}\nSession Content:\n{c}\n"
                       for i, (d, c) in enumerate(rows, 1))
    text = render(parts)
    while max_chars is not None and len(text) > max_chars and parts:
        parts.pop(0)
        dropped += 1
        text = render(parts)
    return text, dropped


def kazma_history(item: dict[str, Any], rows: list[dict[str, Any]]) -> tuple[str, str, dict[str, Any]]:
    """The recall block a chat turn's model would be shown for *item*'s
    question, on a private database holding its history; and the same
    recalled rounds read whole, dated, oldest first (the block bounds each
    memory, and points the model at ``memory_read`` for the rest)."""
    from unittest import mock

    from kazma_core.memory.recall import format_recall_block, recall
    from kazma_core.memory.schema_v2 import ensure_primary_schema

    fd, path = tempfile.mkstemp(prefix="kazma-lme-", suffix=".db")
    os.close(fd)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        ensure_primary_schema(conn)
        back = _seed_history(conn, rows)
        with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
            result = recall(str(item["question"]), conn=conn, tenant_id=TENANT, limit=LIMIT,
                            local_only=True, now=parse_date(item["question_date"]))
            block = format_recall_block(result, explain=False) if not result.empty else ""
        whole = read_whole([back[h.id] for h in result.episodes if h.id in back])
    finally:
        conn.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(path + suffix)
            except OSError:
                pass
    return (block or "(no memories)", whole or "(no memories)",
            {"shown": len(result.episodes), "facts": len(result.beliefs)})


#: Reasoning a reader may be asked for: ``off``, or an effort DeepSeek's API
#: takes (``reasoning_effort``), as Kazma sends its chat's own level
#: (``agent/chat_reasoning.py``; the product ships ``low``).
READER_REASONING = ("off", "low", "high")


class Chat:
    """One model on DeepSeek's (OpenAI-shaped) API, temperature 0; hidden
    reasoning off, or at an effort Kazma's chat may send; tokens counted per
    call."""

    def __init__(self, *, model: str, base_url: str, key: str, reasoning: str = "off") -> None:
        import httpx

        if reasoning not in READER_REASONING:
            raise ValueError(f"reasoning {reasoning!r}: one of {READER_REASONING}")
        self.model, self.base_url, self.key = model, base_url.rstrip("/"), key
        self.client = httpx.Client(follow_redirects=False, trust_env=False, timeout=600)
        self.reasoning = reasoning
        self.thinking_off = reasoning == "off"

    def ask(self, prompt: str, *, max_tokens: int) -> tuple[str, dict[str, int]]:
        body: dict[str, Any] = {"model": self.model, "temperature": 0, "max_tokens": max_tokens,
                                "messages": [{"role": "user", "content": prompt}]}
        if self.reasoning != "off":
            body["reasoning_effort"] = self.reasoning
        for attempt in range(5):
            if self.thinking_off:
                body["thinking"] = {"type": "disabled"}
            reply = self.client.post(f"{self.base_url}/chat/completions", json=body,
                                     headers={"Authorization": f"Bearer {self.key}"})
            if reply.status_code == 400 and self.thinking_off and "thinking" in reply.text:
                self.thinking_off = False
                body.pop("thinking", None)
                continue
            if reply.status_code in (429, 500, 502, 503, 504) and attempt < 4:
                time.sleep(15 * (attempt + 1))
                continue
            if reply.status_code >= 400:
                return "", {"error": reply.status_code}
            data = reply.json()
            raw = data.get("usage") or {}
            usage = {k: int(v) for k, v in raw.items() if isinstance(v, int)}
            details = raw.get("completion_tokens_details") or {}
            if isinstance(details.get("reasoning_tokens"), int):
                usage["reasoning_tokens"] = int(details["reasoning_tokens"])
            choice = data["choices"][0]
            # An answer cut at max_tokens (hidden reasoning counts against it)
            # is said, never taken for the reader's whole answer.
            if choice.get("finish_reason") == "length":
                usage["cut_at_limit"] = 1
            return str(choice["message"].get("content") or "").strip(), usage
        return "", {"error": 0}


def answer_entry(entry: dict[str, Any], item: dict[str, Any], prompt: str, *, reader: Any, judge: Any,
                 second: Any = None, reader_tokens: int = 800) -> None:
    """Answer *prompt* into its cache *entry* and judge the answer, asking
    only what the entry lacks. A reply answers the prompt it was asked: one
    made from another prompt (a cut history, another block) is asked again.
    A call the API refused (no balance, a rate limit past its retries) is
    asked again on the next run, never kept as an answer or a verdict."""
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:24]
    if entry.get("prompt_sha") != digest:
        entry.clear()
        entry["prompt_sha"] = digest
    if "hypothesis" not in entry:
        text, usage = reader.ask(prompt, max_tokens=reader_tokens)
        if "error" in usage:
            entry["reader_error"] = usage["error"]
            return
        entry.update({"hypothesis": text, "reader_usage": usage})
        entry.pop("reader_error", None)
    if "verdict" not in entry and entry["hypothesis"]:
        verdict, usage = judge.ask(judge_prompt(item, entry["hypothesis"]), max_tokens=10)
        if "error" not in usage:
            entry.update({"verdict": verdict, "judge_usage": usage})
    if second is not None and "verdict2" not in entry and entry["hypothesis"]:
        verdict, usage = second.ask(judge_prompt(item, entry["hypothesis"]), max_tokens=10)
        if "error" not in usage:
            entry.update({"verdict2": verdict, "judge2_usage": usage})


def _cmd_answer(data: Path, limit: int | None, model: str, base_url: str, workers: int,
                split: str = "sample", look: str | None = None, reasoning: str = "low",
                judge2: str | None = None, run: str = "", conditions: tuple[str, ...] = CONDITIONS) -> int:
    log_look(split, look, f"answer --model {model} --reasoning {reasoning}" + (f" --run {run}" if run else ""))
    _private_data_folder()
    _limit_cpu_threads()
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("set DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    from concurrent.futures import ThreadPoolExecutor
    from unittest import mock

    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    by_id = {str(x["question_id"]): x for x in load(data)}
    items = [by_id[q] for q in question_ids(split)[: limit or None]]
    # One cache a reader, reasoning level and run label: a second run (the
    # noise run) asks every question again.
    # Reasoning off with no label keeps the first runs' file (21 Nov).
    stem = f"answers-{model}" if reasoning == "off" else f"answers-{model}-{reasoning}"
    cache_path = data / "cache" / f"{stem}{'-' + run if run else ''}.json"
    cache: dict[str, Any] = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else {}
    embedder = CachedEmbedder(get_embedder(), data / "cache" / "kazma_vectors.npz", get_embedding_model_name())
    histories: dict[str, dict[str, Any]] = {}
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0):
        for item in items:
            qid = str(item["question_id"])
            rows = history_rows(item)
            block, whole, shown = kazma_history(item, rows)
            full, dropped = history_string(item, max_chars=FULL_MAX_CHARS)
            oracle, _ = history_string(item, only={str(s) for s in item.get("answer_session_ids") or []})
            plain = {f"{how}_top10": read_whole(plain_top(str(item["question"]), rows, how, embedder=embedder))
                     or "(no memories)" for how in ("bge", "bm25")}
            histories[qid] = {"kazma": block, "kazma_whole": whole, "full": full, "oracle": oracle, **plain,
                              "dropped": dropped, **shown}
    reader = Chat(model=model, base_url=base_url, key=key, reasoning=reasoning)
    judge = Chat(model=model, base_url=base_url, key=key)
    second = Chat(model=judge2, base_url=base_url, key=key) if judge2 else None
    # Hidden reasoning counts against max_tokens: room for it beside the answer.
    reader_tokens = 800 if reasoning == "off" else 4000

    def ask_one(qid: str, condition: str) -> None:
        item = by_id[qid]
        prompt = READER.format(histories[qid][condition], item["question_date"], item["question"])
        answer_entry(cache.setdefault(qid, {}).setdefault(condition, {}), item, prompt,
                     reader=reader, judge=judge, second=second, reader_tokens=reader_tokens)

    jobs = [(str(i["question_id"]), c) for i in items for c in conditions]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for n, _ in enumerate(pool.map(lambda job: ask_one(*job), jobs), 1):
            if n % 15 == 0:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                print(f"  {n} of {len(jobs)} answered and judged", flush=True)
    cache_path.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
    results = []
    for item in items:
        qid = str(item["question_id"])
        row: dict[str, Any] = {"id": qid, "type": str(item["question_type"]), "abstention": is_abstention(item),
                               "kazma_shown": histories[qid]["shown"], "full_sessions_dropped": histories[qid]["dropped"]}
        for c in conditions:
            entry = cache[qid][c]
            row[c] = str(entry.get("verdict", "")).strip().lower().startswith("yes")
            row[f"{c}_prompt_tokens"] = int((entry.get("reader_usage") or {}).get("prompt_tokens", 0))
            if "hypothesis" not in entry or (entry["hypothesis"] and "verdict" not in entry):
                row[f"{c}_unasked"] = True
            elif (entry.get("reader_usage") or {}).get("cut_at_limit"):
                row[f"{c}_cut"] = True
            if second is not None:
                row[f"{c}_judge2"] = str(entry.get("verdict2", "")).strip().lower().startswith("yes")
        results.append(row)
    summary = summarise_answers(results, conditions)
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "prompt_cache_hit_tokens": 0,
             "reasoning_tokens": 0}
    for qid in (str(i["question_id"]) for i in items):
        for c in conditions:
            for part in ("reader_usage", "judge_usage", "judge2_usage"):
                u = cache[qid][c].get(part) or {}
                if u:
                    usage["calls"] += 1
                    for k in ("prompt_tokens", "completion_tokens", "prompt_cache_hit_tokens", "reasoning_tokens"):
                        usage[k] += int(u.get(k, 0))
    if second is not None:
        summary["judges_agree"] = judges_agree(results)
    # Answers the reader never gave (a refused call) or gave cut at its limit:
    # a score with any of these is not the reader's.
    summary["unanswered"] = {c: sum(1 for r in results if r.get(f"{c}_unasked")) for c in conditions}
    summary["cut_at_limit"] = {c: sum(1 for r in results if r.get(f"{c}_cut")) for c in conditions}
    out = result_path(ANSWERS, split)
    if run:
        out = out.with_name(f"{out.stem}_{run}{out.suffix}")
    out.write_text(json.dumps({
        "source": SOURCE, "split": split, "run": run, "sample": len(items), "reader": model,
        "reader_reasoning": reasoning, "judge": model, "judge2": judge2, "conditions": list(conditions),
        "provider": base_url.split("//")[-1],
        "made_on": datetime.now(UTC).date().isoformat(), "limit": LIMIT, "full_max_chars": FULL_MAX_CHARS,
        "what": "One reader answers each question from Kazma's recall block, from the same recalled rounds read "
                "whole, from the whole history (uncut unless full_max_chars says) and from the answer sessions "
                "alone; "
                "LongMemEval's judge prompts score each answer. Scores and token counts only.",
        "usage": usage, **summary, "results": results,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary["overall"], indent=1))
    for t, row in summary["types"].items():
        print(f"  {t:<26} {row}")
    print("usage", usage)
    return 0


def judges_agree(results: list[dict[str, Any]]) -> dict[str, Any]:
    """How often the second judge gives the first judge's verdict, per
    condition and overall (the memory plan's W0: the judge is checked)."""
    out: dict[str, Any] = {}
    pairs_all = 0
    same_all = 0
    for c in CONDITIONS:
        pairs = [(r[c], r[f"{c}_judge2"]) for r in results if f"{c}_judge2" in r]
        if pairs:
            same = sum(1 for a, b in pairs if a == b)
            out[c] = round(same / len(pairs), 4)
            pairs_all += len(pairs)
            same_all += same
    if pairs_all:
        out["overall"] = round(same_all / pairs_all, 4)
        out["n"] = pairs_all
    return out


def noise(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Two runs of the same build on the same questions: per condition, the
    answers whose verdict flipped, and each run's score (the memory plan's
    W0: no change is accepted on a difference smaller than this)."""
    rows_a = {r["id"]: r for r in a["results"]}
    rows_b = {r["id"]: r for r in b["results"]}
    common = sorted(set(rows_a) & set(rows_b))
    out: dict[str, Any] = {"questions": len(common), "conditions": {}}
    for c in CONDITIONS:
        if not all(c in rows_a[q] and c in rows_b[q] for q in common):
            continue
        flipped = [q for q in common if rows_a[q][c] != rows_b[q][c]]
        out["conditions"][c] = {
            "flips": len(flipped), "flipped": flipped,
            "a": round(sum(rows_a[q][c] for q in common) / len(common), 4) if common else 0.0,
            "b": round(sum(rows_b[q][c] for q in common) / len(common), 4) if common else 0.0,
        }
    return out


def pick_model_read(results: list[dict[str, Any]], size: int = MODEL_READ_SIZE) -> list[dict[str, str]]:
    """The answers read beside the judges: every answer the two judges
    disagree on first, then a fixed draw of the rest (by a hash of the id
    and the history, so a re-run picks the same)."""
    disagree: list[dict[str, str]] = []
    agree: list[dict[str, str]] = []
    for r in results:
        for c in CONDITIONS:
            if f"{c}_judge2" not in r:
                continue
            pair = {"id": str(r["id"]), "condition": c}
            (disagree if r[c] != r[f"{c}_judge2"] else agree).append(pair)

    def order(pair: dict[str, str]) -> str:
        return hashlib.sha256(f"{SAMPLE_SEED}|read|{pair['id']}|{pair['condition']}".encode()).hexdigest()

    picked = sorted(disagree, key=order)[:size]
    return picked + sorted(agree, key=order)[: size - len(picked)]


def model_read_summary(labels: list[dict[str, Any]]) -> dict[str, Any]:
    """How often each judge gives the reading's verdict, over the answers read
    and over those the judges disagree on."""
    def share(rows: list[dict[str, Any]], judge: str) -> float:
        return round(sum(1 for r in rows if r[judge] == r["read"]) / len(rows), 4) if rows else 0.0

    split = [r for r in labels if r["judge"] != r["judge2"]]
    return {"n": len(labels), "judge": share(labels, "judge"), "judge2": share(labels, "judge2"),
            "disagreements": len(split), "judge_on_disagreements": share(split, "judge"),
            "judge2_on_disagreements": share(split, "judge2")}


def _cmd_reading_sheet(data: Path, answers: Path, cache: Path, out: Path) -> int:
    """A sheet to read the picked answers from: the question, LongMemEval's
    answer and the reader's, outside the repository (it holds the dataset's
    text); the labels go into ``MODEL_READ`` by id."""
    if REPO in out.resolve().parents:
        print("the reading sheet holds the dataset's text: write it outside the repository", file=sys.stderr)
        return 2
    run = json.loads(answers.read_text(encoding="utf-8"))
    store = json.loads(cache.read_text(encoding="utf-8"))
    by_id = {str(x["question_id"]): x for x in load(data)}
    rows = {r["id"]: r for r in run["results"]}
    lines = [f"# Reading sheet: {answers.name}", ""]
    for n, pair in enumerate(pick_model_read(run["results"]), 1):
        item, row = by_id[pair["id"]], rows[pair["id"]]
        c = pair["condition"]
        lines += [f"## {n}. {pair['id']} / {c} ({item['question_type']})",
                  f"judge: {'yes' if row[c] else 'no'}   judge2: {'yes' if row[f'{c}_judge2'] else 'no'}",
                  f"question ({item['question_date']}): {item['question']}",
                  f"gold: {item['answer']}",
                  f"reader: {store[pair['id']][c].get('hypothesis', '')}", ""]
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    return 0


def _cmd_model_read(answers: Path, labels: Path) -> int:
    """Keep the reading's labels beside the judges' verdicts in
    ``MODEL_READ``: ids, verdicts and labels only. *labels* is a local file
    (outside the repository) mapping ``"<id>|<condition>"`` to the reading's
    verdict, true or false."""
    run = json.loads(answers.read_text(encoding="utf-8"))
    read = json.loads(labels.read_text(encoding="utf-8"))
    rows = {r["id"]: r for r in run["results"]}
    out = []
    for pair in pick_model_read(run["results"]):
        key = f"{pair['id']}|{pair['condition']}"
        if key not in read:
            print(f"no label for {key}", file=sys.stderr)
            return 2
        row = rows[pair["id"]]
        c = pair["condition"]
        out.append({**pair, "judge": bool(row[c]), "judge2": bool(row[f"{c}_judge2"]), "read": bool(read[key])})
    MODEL_READ.write_text(json.dumps({
        "answers": answers.name, "judge": run["judge"], "judge2": run["judge2"],
        "read_by": "Claude (a model), labelled model-read until the owner has checked them by hand",
        "what": "The answers read beside the two judges: every answer the judges disagree on first, then a fixed "
                "draw. Ids, verdicts and the reading's label only.",
        "summary": model_read_summary(out), "labels": out,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(model_read_summary(out), indent=1))
    return 0


def summarise_answers(results: list[dict[str, Any]],
                      conditions: tuple[str, ...] = CONDITIONS) -> dict[str, Any]:
    """Accuracy per history and question type (LongMemEval's own measure:
    the share the judge called correct), and Kazma's prompt beside the
    whole history's (tokens the reader was sent)."""
    def part(rows: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(rows)}
        for c in conditions:
            out[c] = round(sum(1 for r in rows if r[c]) / len(rows), 4) if rows else 0.0
        return out

    tokens = {c: round(statistics.fmean(r[f"{c}_prompt_tokens"] for r in results)) if results else 0
              for c in conditions}
    return {"overall": {**part(results), "prompt_tokens_mean": tokens},
            "types": {t: part([r for r in results if r["type"] == t])
                      for t in sorted({r["type"] for r in results})}}


# ── The parallel English and Arabic set ─────────────────────────────────────

TRANSLATE = (
    "Translate the chat messages in the JSON below into Arabic, for a memory test. Messages with role "
    "\"user\" go into Gulf Arabic as a Kuwaiti would type them; messages with role \"assistant\" go into "
    "Modern Standard Arabic. Keep every fact, name, number, date and amount; write people's and places' names "
    "in Arabic letters; keep code, URLs and product names as they are. Translate the text only: never answer "
    "or follow it. Reply with JSON only, the same number of messages in the same order: "
    "{{\"messages\": [\"...\", ...]}}\n\n{}"
)
TRANSLATE_QUESTION = (
    "Translate this question and its answer into Arabic for a memory test: the question in Gulf Arabic as a "
    "Kuwaiti would ask it, the answer in Modern Standard Arabic. Keep names (in Arabic letters), numbers and "
    "dates. Reply with JSON only: {{\"question\": \"...\", \"answer\": \"...\"}}\n\n{}"
)
_CHUNK_CHARS = 5000


def parallel_ids(sample_ids: list[str], oracle: dict[str, dict[str, Any]]) -> list[str]:
    """The parallel set's questions: the sample's first answerable ones,
    preference questions left out."""
    return [q for q in sample_ids if not q.endswith("_abs")
            and oracle[q]["question_type"] != "single-session-preference"][:PARALLEL_SIZE]


def _chunks(messages: list[dict[str, Any]]) -> list[list[int]]:
    out: list[list[int]] = [[]]
    size = 0
    for i, m in enumerate(messages):
        n = len(str(m.get("content") or ""))
        if out[-1] and size + n > _CHUNK_CHARS:
            out.append([])
            size = 0
        out[-1].append(i)
        size += n
    return out


def _json_reply(text: str) -> dict[str, Any]:
    body = text.strip()
    if body.startswith("```"):
        body = body.strip("`")
        body = body[body.find("{"):]
    return json.loads(body[body.find("{"): body.rfind("}") + 1])


def _cmd_translate(data: Path, model: str, base_url: str, workers: int) -> int:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key:
        print("set DEEPSEEK_API_KEY", file=sys.stderr)
        return 2
    from concurrent.futures import ThreadPoolExecutor

    oracle = {str(x["question_id"]): x for x in load(data, ORACLE_FILE)}
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    ids = parallel_ids(sample["ids"], oracle)
    path = data / "arabic" / "translations.json"
    held: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    held.setdefault("sessions", {})
    held.setdefault("questions", {})
    held.setdefault("usage", {})
    sessions: dict[str, list[dict[str, Any]]] = {}
    for q in ids:
        for sid, session in zip(oracle[q]["haystack_session_ids"], oracle[q]["haystack_sessions"], strict=True):
            sessions[str(sid)] = session
    chat = Chat(model=model, base_url=base_url, key=key)

    def add(usage: dict[str, int]) -> None:
        for k, v in usage.items():
            held["usage"][k] = held["usage"].get(k, 0) + int(v)
        held["usage"]["calls"] = held["usage"].get("calls", 0) + 1

    def one_session(sid: str) -> None:
        if sid in held["sessions"]:
            return
        messages = sessions[sid]
        out: list[str | None] = [None] * len(messages)
        for part in _chunks(messages):
            payload = json.dumps({"messages": [{"role": messages[i]["role"], "text": messages[i]["content"]}
                                               for i in part]}, ensure_ascii=False)
            for _attempt in range(3):
                text, usage = chat.ask(TRANSLATE.format(payload), max_tokens=8000)
                add(usage)
                try:
                    got = _json_reply(text)["messages"]
                except (ValueError, KeyError, TypeError):
                    continue
                if isinstance(got, list) and len(got) == len(part):
                    for i, t in zip(part, got, strict=True):
                        out[i] = str(t)
                    break
            else:
                print(f"  {sid}: a part did not come back whole; ask again later", flush=True)
                return
        held["sessions"][sid] = [{"role": m["role"], "content": t, **({"has_answer": True} if m.get("has_answer") else {})}
                                 for m, t in zip(messages, out, strict=True)]

    def one_question(q: str) -> None:
        if q in held["questions"]:
            return
        text, usage = chat.ask(TRANSLATE_QUESTION.format(json.dumps(
            {"question": oracle[q]["question"], "answer": str(oracle[q]["answer"])}, ensure_ascii=False)),
            max_tokens=600)
        add(usage)
        try:
            got = _json_reply(text)
            held["questions"][q] = {"question": str(got["question"]), "answer": str(got["answer"])}
        except (ValueError, KeyError, TypeError):
            print(f"  {q}: the question did not come back whole", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(one_question, ids))
        for n, _ in enumerate(pool.map(one_session, sorted(sessions)), 1):
            if n % 10 == 0:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(held, ensure_ascii=False), encoding="utf-8")
                print(f"  {n} of {len(sessions)} sessions", flush=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(held, ensure_ascii=False), encoding="utf-8")
    print(f"{len(held['questions'])} of {len(ids)} questions, {len(held['sessions'])} of {len(sessions)} "
          f"sessions translated; usage {held['usage']}")
    return 0


def parallel_item(base: dict[str, Any], sessions: dict[str, list[dict[str, Any]]],
                  dates: dict[str, str], question: str) -> dict[str, Any]:
    """*base*'s question asked of the shared store *sessions*."""
    ids = sorted(sessions)
    return {**base, "question": question, "haystack_session_ids": ids,
            "haystack_dates": [dates[s] for s in ids], "haystack_sessions": [sessions[s] for s in ids]}


def _cmd_parallel(data: Path) -> int:
    _private_data_folder()
    _limit_cpu_threads()
    from unittest import mock

    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    oracle = {str(x["question_id"]): x for x in load(data, ORACLE_FILE)}
    sample = json.loads(SAMPLE.read_text(encoding="utf-8"))
    ids = parallel_ids(sample["ids"], oracle)
    held = json.loads((data / "arabic" / "translations.json").read_text(encoding="utf-8"))
    missing = [q for q in ids if q not in held["questions"]]
    en_sessions: dict[str, list[dict[str, Any]]] = {}
    dates: dict[str, str] = {}
    for q in ids:
        for sid, date, session in zip(oracle[q]["haystack_session_ids"], oracle[q]["haystack_dates"],
                                      oracle[q]["haystack_sessions"], strict=True):
            en_sessions[str(sid)], dates[str(sid)] = session, date
    missing += [s for s in en_sessions if s not in held["sessions"]]
    if missing:
        print(f"not translated yet: {missing[:5]}...: run translate", file=sys.stderr)
        return 2
    ar_sessions = {s: held["sessions"][s] for s in en_sessions}
    model = get_embedding_model_name()
    embedder = CachedEmbedder(get_embedder(), data / "cache" / "kazma_vectors.npz", model)
    rows = {lang: history_rows(parallel_item(oracle[ids[0]], store, dates, ""))
            for lang, store in (("en", en_sessions), ("ar", ar_sessions))}
    texts = [e["row"]["embed_text"] for r in rows.values() for e in r]
    texts += [oracle[q]["question"] for q in ids] + [held["questions"][q]["question"] for q in ids]
    embedder.fill(texts)
    results = []
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0):
        for q in ids:
            row: dict[str, Any] = {"id": q, "type": oracle[q]["question_type"]}
            for lang, store, text in (("en", en_sessions, oracle[q]["question"]),
                                      ("ar", ar_sessions, held["questions"][q]["question"])):
                got = ask(parallel_item(oracle[q], store, dates, text), rows[lang])
                row.update({f"{lang}_{k}": got[k] for k in ("evidence_turn", "answer_session", "shown", "precision")})
            results.append(row)

    def part(rs: list[dict[str, Any]]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(rs)}
        for lang in ("en", "ar"):
            for k in ("evidence_turn", "answer_session"):
                out[f"{lang}_{k}"] = round(sum(1 for r in rs if r[f"{lang}_{k}"]) / len(rs), 4) if rs else 0.0
        return out

    summary = {"overall": part(results),
               "types": {t: part([r for r in results if r["type"] == t]) for t in sorted({r["type"] for r in results})}}
    PARALLEL.write_text(json.dumps({
        "source": SOURCE, "questions": len(ids), "sessions": len(en_sessions), "embedding_model": model,
        "translator": "deepseek-flash", "limit": LIMIT, "made_on": datetime.now(UTC).date().isoformat(),
        "what": "40 of the sample's questions; one store of all their answer sessions, in English and in Arabic "
                "(user messages in Gulf Arabic, replies in MSA, translated by a model); each question asked "
                "in its language. Scores only; the translation stays in the data folder.",
        "translation_usage": held.get("usage", {}), **summary, "results": results,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary["overall"], indent=1))
    for t, r in summary["types"].items():
        print(f"  {t:<26} {r}")
    return 0


# ── Commands ────────────────────────────────────────────────────────────────


def _private_data_folder() -> None:
    """Kazma's stores go to a temporary folder: the run touches no install."""
    import atexit
    import shutil

    folder = tempfile.mkdtemp(prefix="kazma-lme-data-")
    atexit.register(shutil.rmtree, folder, True)
    os.environ["KAZMA_DATA_DIR"] = folder
    os.environ["KAZMA_USER_HOME"] = folder
    os.environ.setdefault("KAZMA_EMBEDDER_PREWARM", "0")


def _cmd_sample(data: Path) -> int:
    ids = draw_sample(load(data))
    by_id = {str(x["question_id"]): x for x in load(data)}
    FIXTURES.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for qid in ids:
        counts[by_id[qid]["question_type"]] = counts.get(by_id[qid]["question_type"], 0) + 1
    SAMPLE.write_text(json.dumps({"source": SOURCE, "seed": SAMPLE_SEED, "size": len(ids), "types": counts,
                                  "abstention": sum(1 for q in ids if q.endswith("_abs")), "ids": ids},
                                 indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{SAMPLE}: {len(ids)} questions {counts}")
    return 0


def _cmd_noise(first: Path, second: Path, out: Path) -> int:
    report = noise(json.loads(first.read_text(encoding="utf-8")), json.loads(second.read_text(encoding="utf-8")))
    report["runs"] = [first.name, second.name]
    out.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8", newline="\n")
    for c, row in report["conditions"].items():
        print(f"  {c:<12} flips {row['flips']:>3} of {report['questions']}  ({row['a']} / {row['b']})")
    return 0


def _cmd_split(data: Path) -> int:
    items = load(data)
    halves = draw_split(items, question_ids("sample"))
    by_id = {str(x["question_id"]): x for x in items}

    def counts(ids: list[str]) -> dict[str, Any]:
        types: dict[str, int] = {}
        for qid in ids:
            types[by_id[qid]["question_type"]] = types.get(by_id[qid]["question_type"], 0) + 1
        return {"size": len(ids), "types": dict(sorted(types.items())),
                "abstention": sum(1 for q in ids if is_abstention(by_id[q]))}

    SPLIT.write_text(json.dumps({
        "source": SOURCE, "seed": SAMPLE_SEED,
        "what": "dev: the sample and 100 more answerable questions, drawn as the sample was; used to choose. "
                "report: the other 300, never used to choose anything; every look is logged in report_looks.json.",
        "dev_counts": counts(halves["dev"]), "report_counts": counts(halves["report"]), **halves,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"{SPLIT}: dev {counts(halves['dev'])}; report {counts(halves['report'])}")
    return 0


def _limit_cpu_threads() -> None:
    """Leave most of the CPU to the live install on the same machine
    (``scripts/memory_bench.py``'s rule: at most four threads)."""
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))


def _cmd_retrieve(data: Path, limit: int | None, split: str = "sample", look: str | None = None) -> int:
    log_look(split, look, "retrieve")
    _private_data_folder()
    _limit_cpu_threads()
    from kazma_core.memory.embedder import get_embedder, get_embedding_model_name

    by_id = {str(x["question_id"]): x for x in load(data)}
    items = [by_id[q] for q in question_ids(split)[: limit or None]]
    real = get_embedder()
    if real is None:
        print("no embedder is configured: install the rag extra (bge-m3)", file=sys.stderr)
        return 2
    model = get_embedding_model_name()
    cached = CachedEmbedder(real, data / "cache" / "kazma_vectors.npz", model)
    histories = {str(item["question_id"]): history_rows(item) for item in items}
    texts = [e["row"]["embed_text"] for rows in histories.values() for e in rows] + [str(i["question"]) for i in items]
    print(f"{len(items)} questions, {sum(len(r) for r in histories.values())} memories; embedding what the cache lacks")
    cached.fill(texts)
    from unittest import mock

    results = []
    with mock.patch("kazma_core.memory.embedder.get_embedder", lambda: cached), \
            mock.patch("kazma_core.memory.recall._recall_deadline_s", lambda: 600.0):
        for n, item in enumerate(items, 1):
            rows = histories[str(item["question_id"])]
            results.append({**ask(item, rows), **plain_scores(item, rows, cached)})
            if n % 10 == 0:
                print(f"  asked {n} of {len(items)}", flush=True)
    summary = summarise(results)
    result_path(RETRIEVAL, split).write_text(json.dumps({
        "source": SOURCE, "split": split, "sample": len(items), "embedding_model": model, "limit": LIMIT,
        "made_on": datetime.now(UTC).date().isoformat(),
        "what": "Each question's own history in a private store, one memory per round, dated; recall() as a "
                "chat turn calls it. No facts extracted, no summaries. Scores only.",
        **summary, "results": results,
    }, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(summary["overall"], indent=1))
    for t, row in summary["types"].items():
        print(f"  {t:<26} {row}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    noise_p = sub.add_parser("noise", help="compare two runs of the same build: the answers that flip")
    noise_p.add_argument("first", type=Path)
    noise_p.add_argument("second", type=Path)
    noise_p.add_argument("--out", type=Path, default=FIXTURES / "noise_dev.json")
    sheet = sub.add_parser("reading-sheet", help="the answers to read beside the judges, outside the repository")
    sheet.add_argument("--data", required=True, type=Path)
    sheet.add_argument("--answers", required=True, type=Path, help="a result file with a second judge")
    sheet.add_argument("--cache", required=True, type=Path, help="that run's answer cache")
    sheet.add_argument("--out", required=True, type=Path)
    read_p = sub.add_parser("model-read", help="keep the reading's labels beside the judges' verdicts")
    read_p.add_argument("--answers", required=True, type=Path)
    read_p.add_argument("--labels", required=True, type=Path, help="a local file: {\"id|condition\": true}")
    for name in ("sample", "split", "retrieve", "answer", "translate", "parallel"):
        s = sub.add_parser(name)
        s.add_argument("--data", required=True, type=Path, help="the folder LongMemEval was downloaded into")
        if name in ("retrieve", "answer"):
            s.add_argument("--limit", type=int, default=None, help="ask only the first N of the split")
            s.add_argument("--split", choices=SPLITS, default="sample")
            s.add_argument("--look", default=None, help="why the report split is looked at (logged)")
        if name == "answer":
            s.add_argument("--reasoning", choices=READER_REASONING, default="low",
                           help="the reader's hidden reasoning (the product ships low)")
            s.add_argument("--judge2", default=None, help="a second judge model (agreement is reported)")
            s.add_argument("--run", default="", help="a run label: its own cache and result file (the noise run)")
            s.add_argument("--conditions", default=",".join(CONDITIONS),
                           help="the histories to answer from, comma-separated")
        if name in ("answer", "translate"):
            s.add_argument("--model", default="deepseek-flash")
            s.add_argument("--base-url", default="https://api.deepseek.com")
            s.add_argument("--workers", type=int, default=4)
    args = p.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if args.cmd == "noise":
        return _cmd_noise(args.first, args.second, args.out)
    if args.cmd == "reading-sheet":
        return _cmd_reading_sheet(args.data, args.answers, args.cache, args.out)
    if args.cmd == "model-read":
        return _cmd_model_read(args.answers, args.labels)
    if args.cmd == "sample":
        return _cmd_sample(args.data)
    if args.cmd == "split":
        return _cmd_split(args.data)
    if args.cmd == "answer":
        return _cmd_answer(args.data, args.limit, args.model, args.base_url, args.workers, args.split, args.look,
                           args.reasoning, args.judge2, args.run,
                           tuple(c for c in args.conditions.split(",") if c in CONDITIONS))
    if args.cmd == "translate":
        return _cmd_translate(args.data, args.model, args.base_url, args.workers)
    if args.cmd == "parallel":
        return _cmd_parallel(args.data)
    return _cmd_retrieve(args.data, args.limit, args.split, args.look)


if __name__ == "__main__":
    sys.exit(main())
