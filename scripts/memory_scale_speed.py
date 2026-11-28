#!/usr/bin/env python3
"""Recall at the size of a heavy user's memory (the memory plan's W8).

No paid model call. The deep pass, weekly summaries and the reranker stay
out: this is the part of scale and speed that needs no model. A private
database is filled and ``recall()`` is timed. The insert is this bench's
own (``episode_row`` written with its vector inline), not ``write_episode``.

1x is the episode count in ``tests/fixtures/memory_bench/live_lengths.json``
(1,723 on the backup that file was taken from). That file is lengths only,
read by the product measures from a native backup; this bench does not open
an install, and none of its text comes from one. The text is the
measured-limits generator (a 20,000-word Zipf vocabulary, Arabic words at
the head, so a small vocabulary cannot make the full-text index match every
row) cut to that file's length percentiles. Turns per chat were not in the
file; 6 is the measured-limits bench's grouping. Facts were not counted in
the backup, so they are not assumed one per episode: every 25th turn is a
short preference, English and Arabic alternating, and the quick pass stores
what it stores.

What is written, and what is not. The row is ``episode_row`` with
``source="benchmark"`` (the tier a history written after the fact has, which
is what recall reads). The vector is the product embedder's, on the text
``episode_row`` names. The quick pass is
``extract_and_apply_beliefs_sync(private=True)``: nothing leaves the
database, no state mirror and no remote index. The deep pass is not queued.
Passages stay off, as shipped. Recall is the product's, ``local_only``,
limit 5, times read in UTC.

Recall makes no paid call (``local_only``). Each size gets a new embedder
wrapper, so the question's local embedding is counted for that size: recall
caches a question vector by the embedder's identity, and one wrapper would
make every later size embed nothing. Token counts stay 0; the paid-call
guard patches httpx only. The latency budget in the plan (150 ms p50, 400 ms
p95, decision 4) is time a reranker may add. This bench times recall itself,
reranker off, and says when that alone is already over those numbers. One
untimed recall per size runs first and is reported as ``warmup_s``.

    python scripts/memory_scale_speed.py size
    python scripts/memory_scale_speed.py run --factors 1,10 --out REPORT.json

``size`` fills a sample through the same insert and extrapolates. ``run``
refuses 100x when that extrapolation is two hours or more (recall at 100x is
not in the sample; it is small beside the fill on the measured-limits
bench). The gate is this bench's write cost. Text is synthetic. Nothing of
an install is read or written.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import random
import sqlite3
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[1]
LENGTHS_PATH = REPO / "tests" / "fixtures" / "memory_bench" / "live_lengths.json"
FACTORS = (1, 10, 100)
FACT_EVERY = 25
TURNS_PER_CHAT = 6
TWO_HOURS_S = 2 * 60 * 60
#: Decision 4: time a reranker may add to a recall. Compared here with recall
#: itself, so a recall already over it cannot grow a reranker and stay inside.
BUDGET_P50_S = 0.150
BUDGET_P95_S = 0.400
#: One recall before the timed set. Not one of QUESTIONS: those stay the sample.
WARMUP_TEXT = "What is on the hall table?"
FILL_WHAT = (
    "the bench's own INSERT of episode_row with the vector inline, batch-encoded; "
    "not write_episode (no forget-ledger check, no BEGIN IMMEDIATE). "
    "fill_s and the 100x gate measure this write cost"
)
TOKENS_WHAT = (
    "prompt_tokens and completion_tokens are 0 because recall runs local_only. "
    "model_calls counts an httpx request to a paid host, which the guard refuses"
)
TENANT = "scale"
FIXED_NOW = datetime(2026, 11, 1, tzinfo=UTC).timestamp()
_VOCAB = 20_000
_SYLLABLES = ("ka", "zu", "mi", "ro", "ta", "ne", "lo", "sa", "di", "fu", "ba", "qe", "wi", "ho", "ja", "pe")
_ARABIC = ("موعد", "الميزانية", "رحلة", "النادي", "خزانة", "تقرير", "اجتماع", "مشروع", "قهوة", "مدرسة",
           "طبيب", "فندق", "رحلات", "جدول", "تذكير", "نسخة", "خادم", "تغريدة", "مسودة", "مبيعات")
_EN_PREF = ("amber", "bread", "coffee", "linen", "cedar", "honey", "olive", "maple", "birch", "coral",
            "daisy", "fennel", "garlic", "hazel", "ivory", "jasper", "kiln", "lemon", "melon", "nutmeg",
            "orchid", "pepper", "quartz", "raisin", "saffron", "thyme", "violet", "walnut", "yarrow", "ginger",
            "basil", "clover", "dill", "barley", "cocoa", "cumin")
_AR_PREF = ("الشاي", "القهوة", "الخبز", "العسل", "الزيتون", "الليمون", "النعناع", "الزعتر", "الارز", "العدس",
            "السمك", "الدجاج", "التمر", "الحليب", "الجبن", "الزبدة", "التفاح", "الموز", "العنب", "الرمان",
            "الخيار", "الجزر", "البصل", "الثوم", "الكمون", "الكركم", "القرفة", "الهيل", "الزنجبيل", "الشوفان",
            "الذرة", "الشعير", "الحمص", "الفول", "اللوز", "الجوز")
#: A fixed set, both languages, ordinary shapes (a fact, a time, a list, a
#: question memory cannot answer). Not taken from a benchmark dataset.
QUESTIONS: tuple[dict[str, str], ...] = (
    {"lang": "en", "text": "Where do I live?"},
    {"lang": "en", "text": "What is my name?"},
    {"lang": "en", "text": "Where do I work?"},
    {"lang": "en", "text": "What do I prefer to drink?"},
    {"lang": "en", "text": "What did I say about the budget last week?"},
    {"lang": "en", "text": "How many times did I mention the trip?"},
    {"lang": "en", "text": "Who looks after my teeth?"},
    {"lang": "en", "text": "What changed about my city?"},
    {"lang": "en", "text": "List every restaurant I liked."},
    {"lang": "en", "text": "What did I tell you in August?"},
    {"lang": "en", "text": "Do I have a sister named Maya?"},
    {"lang": "en", "text": "What is my favourite coffee?"},
    {"lang": "ar", "text": "وين أسكن؟"},
    {"lang": "ar", "text": "وش اسمي؟"},
    {"lang": "ar", "text": "وين أشتغل؟"},
    {"lang": "ar", "text": "وش أفضل أشرب؟"},
    {"lang": "ar", "text": "وش قلت عن الميزانية الأسبوع الماضي؟"},
    {"lang": "ar", "text": "كم مرة ذكرت الرحلة؟"},
    {"lang": "ar", "text": "من يعتني بأسناني؟"},
    {"lang": "ar", "text": "وش تغير في مدينتي؟"},
    {"lang": "ar", "text": "عدد لي كل المطاعم اللي أحبها."},
    {"lang": "ar", "text": "وش قلت لك في أغسطس؟"},
    {"lang": "ar", "text": "عندي أخت اسمها مايا؟"},
    {"lang": "ar", "text": "وش قهوتي المفضلة؟"},
)


def _lengths(path: Path = LENGTHS_PATH) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    for key in ("question_percentiles", "answer_percentiles", "one_side_percentiles"):
        if len(data[key]) != 101:
            raise RuntimeError(f"{path} {key} is not percentiles 0..100")
    if int(data["episodes"]) <= 0:
        raise RuntimeError(f"{path} has no episodes")
    return data


LENGTHS = _lengths()
ONE_X_EPISODES = int(LENGTHS["episodes"])
ONE_SIDED_SHARE = int(LENGTHS["one_sided"]) / ONE_X_EPISODES


def question_set_problems(questions: list[dict[str, str]] | tuple[dict[str, str], ...]) -> list[str]:
    """English and Arabic both present. A set of one language is the control."""
    langs = {q["lang"] for q in questions}
    problems = []
    if "en" not in langs or "ar" not in langs:
        problems.append("the question set needs English and Arabic")
    if any(q["lang"] not in ("en", "ar") for q in questions):
        problems.append("a question is neither English nor Arabic")
    return problems


def store_counts(factor: int, *, episodes: int | None = None, fact_every: int = FACT_EVERY) -> dict[str, int]:
    """How many episodes and fact turns *factor* times the 1x store holds.

    *episodes* replaces the 1x count (the tests build a small one). A fact
    turn is every ``fact_every``th episode, starting at 0; ``fact_every`` of
    0 is a store the quick pass is still run on, with no preference planted.
    """
    if factor not in FACTORS:
        raise ValueError(f"factor {factor} is not one of {FACTORS}")
    base = ONE_X_EPISODES if episodes is None else episodes
    n = base * factor
    fact_turns = 0 if fact_every <= 0 else sum(1 for i in range(n) if i % fact_every == 0)
    return {"episodes": n, "fact_turns": fact_turns, "factor": factor}


def percentile(values: list[float], share: float) -> float:
    """The product measures' percentile (``memory_product_measures._pct``):
    nearest rank, ``round`` of the index."""
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, round(share * (len(ordered) - 1)))]


def within_two_hours(estimate_s: float, *, factor: int, limit_s: float = TWO_HOURS_S) -> bool:
    """100x runs only under *limit_s*. 1x and 10x are not gated on it."""
    if factor != 100:
        return True
    return estimate_s < limit_s


def _vocabulary() -> list[str]:
    words = list(_ARABIC)
    i = 0
    while len(words) < _VOCAB:
        n, w = i, ""
        for _ in range(3):
            w += _SYLLABLES[n % len(_SYLLABLES)]
            n //= len(_SYLLABLES)
        words.append(w + str(i // 4096) if i >= 4096 else w)
        i += 1
    return words


def _pair(words: tuple[str, ...], k: int) -> tuple[str, str]:
    n = len(words)
    a = words[k % n]
    b = words[(k // n) % n]
    if a == b:
        b = words[(k // n + 1) % n]
    return a, b


def fact_text(ordinal: int) -> tuple[str, str]:
    """The preference turn number *ordinal* (0-based among fact turns), and
    its language. English and Arabic alternate. Each phrase is unique until
    the word lists' pairs run out, so a set-fact stays stored."""
    if ordinal % 2 == 0:
        a, b = _pair(_EN_PREF, ordinal // 2)
        return f"I prefer {a} {b}.", "en"
    a, b = _pair(_AR_PREF, ordinal // 2)
    return f"أحب {a} {b}.", "ar"


def _draw_len(rng: random.Random, percentiles: list[int]) -> int:
    return int(percentiles[rng.randint(0, 100)])


def _zipf_text(nrng: Any, vocab: list[str], n_chars: int) -> str:
    if n_chars <= 0:
        return ""
    parts: list[str] = []
    total = 0
    while total < n_chars:
        need = max(4, (n_chars - total) // 4)
        ranks = nrng.zipf(1.15, size=need)
        for rank in ranks.tolist():
            word = vocab[int(rank) % len(vocab)]
            parts.append(word)
            total += len(word) + 1
            if total >= n_chars:
                break
    text = " ".join(parts)
    if len(text) <= n_chars:
        return text
    cut = text[:n_chars]
    return cut[: cut.rfind(" ")] if " " in cut else cut


def _turn(i: int, n: int, vocab: list[str], nrng: Any, rng: random.Random, *,
          fact_every: int) -> dict[str, Any]:
    """Episode *i* of *n*: a preference when it is a fact turn, otherwise
    Zipf text at a length drawn from the published percentiles."""
    session = i // TURNS_PER_CHAT
    number = (i % TURNS_PER_CHAT) + 1
    created = FIXED_NOW - (n - i) * (400 * 86400 / max(1, n))
    fact_lang = ""
    if fact_every > 0 and i % fact_every == 0:
        user, fact_lang = fact_text(i // fact_every)
        assistant = f"Noted {i}."
    else:
        one_sided = rng.random() < ONE_SIDED_SHARE
        if one_sided and rng.random() < 0.5:
            user, assistant = _zipf_text(nrng, vocab, _draw_len(rng, LENGTHS["one_side_percentiles"])), ""
        elif one_sided:
            user, assistant = "", _zipf_text(nrng, vocab, _draw_len(rng, LENGTHS["one_side_percentiles"]))
        else:
            user = _zipf_text(nrng, vocab, _draw_len(rng, LENGTHS["question_percentiles"]))
            assistant = _zipf_text(nrng, vocab, _draw_len(rng, LENGTHS["answer_percentiles"]))
        assistant = (assistant + f" n{i}").strip()
    return {"session": f"scale-{session}", "turn": number, "user": user, "assistant": assistant,
            "created": created, "fact_lang": fact_lang}


class CountingEmbedder:
    """The embedder recall and the quick pass call, with its encodes counted.

    A paid model is not this. ``encodes`` and ``seconds`` are the local
    embedding only."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.dim = getattr(inner, "dim", None)
        self.model = getattr(inner, "model", "counting")
        self.encodes = 0
        self.seconds = 0.0
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.encodes = 0
            self.seconds = 0.0

    def _note(self, n: int, started: float) -> None:
        with self._lock:
            self.encodes += n
            self.seconds += time.perf_counter() - started

    def encode(self, text: str) -> list[float]:
        started = time.perf_counter()
        try:
            return self.inner.encode(text)
        finally:
            self._note(1, started)

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        started = time.perf_counter()
        try:
            batch = getattr(self.inner, "encode_batch", None)
            if batch is None:
                return [self.inner.encode(text) for text in texts]
            return batch(texts)
        finally:
            self._note(len(texts), started)


class StubEmbedder:
    """A stand-in embedder for tests. Never the real model."""

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim
        self.model = "stub"
        self.encodes = 0

    def encode(self, text: str) -> list[float]:
        self.encodes += 1
        # A different vector per text, so a search still ranks.
        start = (sum(ord(c) for c in text) % 997) / 997
        return [start + i / self.dim for i in range(self.dim)]

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(text) for text in texts]


def _patched(embedder: Any) -> contextlib.ExitStack:
    from unittest import mock

    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedder", lambda: embedder))
    name = str(getattr(embedder, "model", "scale"))
    stack.enter_context(mock.patch("kazma_core.memory.embedder.get_embedding_model_name", lambda: name))
    return stack


def _open(path: Path) -> tuple[sqlite3.Connection, sqlite3.Connection]:
    from kazma_core.config_store import apply_sqlite_pragmas
    from kazma_core.memory.schema_v2 import ensure_ops_schema, ensure_primary_schema

    path.parent.mkdir(parents=True, exist_ok=True)
    primary = sqlite3.connect(path)
    primary.row_factory = sqlite3.Row
    apply_sqlite_pragmas(primary)
    ensure_primary_schema(primary)
    ops = sqlite3.connect(path.with_name(path.stem + "-ops.db"))
    ops.row_factory = sqlite3.Row
    apply_sqlite_pragmas(ops)
    ensure_ops_schema(ops)
    return primary, ops


def _blob(vec: Any) -> bytes | None:
    from kazma_core.memory.embedder import serialize_f32_embedding

    if vec is None or len(vec) == 0:
        return None
    return serialize_f32_embedding([float(x) for x in vec]) or None


def build_store(path: Path, embedder: Any, *, factor: int = 1, episodes: int | None = None,
                fact_every: int = FACT_EVERY, batch: int = 32) -> dict[str, int]:
    """Fill *path* and return the rows the database holds.

    *episodes* is the 1x count (default the published one) before *factor*.
    The quick pass runs on every turn. Progress goes to stderr past 500
    episodes, so a test stays quiet.
    """
    from kazma_core.memory.belief_extractor import extract_and_apply_beliefs_sync
    from kazma_core.memory.dual_write import episode_row

    counts = store_counts(factor, episodes=episodes, fact_every=fact_every)
    n = counts["episodes"]
    vocab = _vocabulary()
    nrng = __import__("numpy").random.default_rng(factor * 1_000_003 + n)
    rng = random.Random(f"kazma-scale-speed-{factor}-{n}-{fact_every}")
    primary, ops = _open(path)
    try:
        with _patched(embedder):
            for start in range(0, n, 256):
                chunk = [_turn(i, n, vocab, nrng, rng, fact_every=fact_every)
                         for i in range(start, min(n, start + 256))]
                rows = [episode_row(session_id=t["session"], turn_number=t["turn"], user_text=t["user"],
                                    assistant_text=t["assistant"], tenant_id=TENANT, source="benchmark",
                                    created_at=t["created"]) for t in chunk]
                vectors: list[Any] = []
                texts = [row["embed_text"] for row in rows]
                for at in range(0, len(texts), batch):
                    vectors.extend(embedder.encode_batch(texts[at:at + batch]))
                primary.executemany(
                    "INSERT OR IGNORE INTO episodes (id, tenant_id, session_id, turn_number, "
                    "user_text, assistant_text, summary_text, tier, structural_importance, "
                    "created_at, metadata_json, embedding_model_version, embedding) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    [(row["id"], row["tenant_id"], row["session_id"], row["turn_number"], row["user_text"],
                      row["assistant_text"], row["summary_text"], row["tier"], row["importance"],
                      row["created_at"], json.dumps(row["meta"], ensure_ascii=False),
                      row["embedding_model_version"], _blob(vec) if row["embed_text"] else None)
                     for row, vec in zip(rows, vectors, strict=True)],
                )
                primary.commit()
                for turn in chunk:
                    if not (turn["user"] or "").strip():
                        continue
                    extract_and_apply_beliefs_sync(
                        primary, ops, turn["user"], turn["assistant"], session_id=turn["session"],
                        turn=turn["turn"], tenant_id=TENANT, now=turn["created"], private=True)
                if n >= 500 and (start // 256) % 4 == 0:
                    print(f"  wrote {min(n, start + 256)} of {n}", file=sys.stderr, flush=True)
        primary.commit()
        ops.commit()
        held = {
            "episodes": int(primary.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]),
            "facts": int(primary.execute(
                "SELECT COUNT(*) FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL").fetchone()[0]),
            "fact_turns": counts["fact_turns"],
        }
        langs = {"en": 0, "ar": 0}
        for (obj,) in primary.execute(
                "SELECT object FROM beliefs WHERE valid_until IS NULL AND invalidated_at IS NULL"):
            langs["ar" if any("\u0600" <= ch <= "\u06ff" for ch in str(obj)) else "en"] += 1
        held["facts_en"] = langs["en"]
        held["facts_ar"] = langs["ar"]
        return held
    finally:
        primary.close()
        ops.close()


_PAID_HOST = ("deepseek", "dashscope", "qwen", "openai", "anthropic", "googleapis")


@contextlib.contextmanager
def _paid_guard() -> Any:
    """Count an HTTP call to a paid model host during a recall, and refuse it.

    A loopback call is left alone. Tokens stay 0: a refused call has no usage
    to read, and a recall that makes none spent none."""
    import httpx

    box: dict[str, Any] = {"calls": 0, "hosts": [], "prompt_tokens": 0, "completion_tokens": 0}
    orig = httpx.Client.send
    orig_async = httpx.AsyncClient.send

    def _watch(host: str) -> None:
        if host and any(mark in host for mark in _PAID_HOST):
            box["calls"] += 1
            box["hosts"].append(host)
            raise RuntimeError(f"paid model call refused: {host}")

    def send(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        url = getattr(request, "url", None)
        _watch(str(getattr(url, "host", "") or ""))
        return orig(self, request, *args, **kwargs)

    async def asend(self: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
        url = getattr(request, "url", None)
        _watch(str(getattr(url, "host", "") or ""))
        return await orig_async(self, request, *args, **kwargs)

    httpx.Client.send = send  # type: ignore[method-assign]
    httpx.AsyncClient.send = asend  # type: ignore[method-assign]
    try:
        yield box
    finally:
        httpx.Client.send = orig  # type: ignore[method-assign]
        httpx.AsyncClient.send = orig_async  # type: ignore[method-assign]


def _product_recall(text: str, conn: sqlite3.Connection) -> Any:
    from unittest import mock

    from kazma_core.memory.recall import recall

    with mock.patch("kazma_core.memory.recall._memory_zone", lambda: UTC):
        return recall(text, conn=conn, tenant_id=TENANT, limit=5, local_only=True, now=FIXED_NOW)


def time_recalls(conn: sqlite3.Connection, questions: list[dict[str, str]] | tuple[dict[str, str], ...], *,
                 recall_fn: Callable[[str, sqlite3.Connection], Any],
                 clock: Callable[[], float] | None = None,
                 embedder: CountingEmbedder | None = None) -> dict[str, Any]:
    """Time each question. *clock* defaults to ``perf_counter``; a test clock
    that a slow ``recall_fn`` advances makes that question the p95."""
    now = clock or time.perf_counter
    walls: list[float] = []
    embeds: list[float] = []
    searches: list[float] = []
    by_lang: dict[str, list[float]] = {}
    local_embeds = 0
    degraded = 0
    slowest = ""
    slowest_wall = -1.0
    # Recall asks get_embedder() itself. The wrapper has to be that embedder
    # for the whole question, or the count is of a model the recall never used.
    patch = _patched(embedder) if embedder is not None else contextlib.nullcontext()
    with patch, _paid_guard() as paid:
        # Untimed. The first recall pays for lazy imports, the first FTS and
        # vector reads, and an analyser. That cost is warmup_s, not p95.
        warm_started = now()
        recall_fn(WARMUP_TEXT, conn)
        warmup_s = now() - warm_started
        for question in questions:
            before_s = embedder.seconds if embedder is not None else 0.0
            before_n = embedder.encodes if embedder is not None else 0
            started = now()
            result = recall_fn(question["text"], conn)
            wall = now() - started
            embed_s = (embedder.seconds - before_s) if embedder is not None else 0.0
            local_embeds += (embedder.encodes - before_n) if embedder is not None else 0
            walls.append(wall)
            embeds.append(embed_s)
            searches.append(max(0.0, wall - embed_s))
            by_lang.setdefault(question["lang"], []).append(wall)
            if wall >= slowest_wall:
                slowest_wall = wall
                slowest = question["text"]
            if getattr(result, "degraded", None):
                degraded += 1

    def pack(values: list[float]) -> dict[str, float]:
        return {"p50": percentile(values, 0.50), "p95": percentile(values, 0.95), "n": float(len(values)),
                "max": max(values) if values else 0.0}

    return {
        "recall_s": pack(walls),
        "embed_s": pack(embeds),
        "search_s": pack(searches),
        "by_lang": {lang: pack(values) for lang, values in sorted(by_lang.items())},
        "local_embeds": local_embeds,
        "model_calls": int(paid["calls"]),
        "prompt_tokens": int(paid["prompt_tokens"]),
        "completion_tokens": int(paid["completion_tokens"]),
        "degraded": degraded,
        "slowest_question": slowest,
        "over_budget": percentile(walls, 0.50) > BUDGET_P50_S or percentile(walls, 0.95) > BUDGET_P95_S,
        "warmup_s": warmup_s,
        "warmup_what": "one untimed recall of a question outside the set, before the timed questions",
        "tokens_what": TOKENS_WHAT,
    }


def profile_slowest(conn: sqlite3.Connection, question: str, embedder: Any | None = None) -> str:
    """Where one recall spends its time, when the size is over the budget.
    Recall's code is not changed; this only reads a profile."""
    import cProfile
    import io
    import pstats

    pr = cProfile.Profile()
    patch = _patched(embedder) if embedder is not None else contextlib.nullcontext()
    pr.enable()
    try:
        with patch:
            _product_recall(question, conn)
    finally:
        pr.disable()
    buf = io.StringIO()
    pstats.Stats(pr, stream=buf).sort_stats("cumulative").print_stats(12)
    return buf.getvalue()


def report_problems(report: dict[str, Any], questions: list[dict[str, str]] | tuple[dict[str, str], ...], *,
                    factors: tuple[int, ...], episodes: int | None, fact_every: int) -> list[str]:
    """Every asked size, both languages, the declared episode count, and the
    call and token counts. A report with Arabic removed is the control."""
    problems = list(question_set_problems(questions))
    langs = {q["lang"] for q in questions}
    sizes = report.get("sizes") or {}
    for factor in factors:
        row = sizes.get(str(factor))
        declared = store_counts(factor, episodes=episodes, fact_every=fact_every)
        if not isinstance(row, dict):
            problems.append(f"{factor}x missing")
            continue
        if row.get("refused"):
            continue
        if row.get("episodes") != declared["episodes"]:
            problems.append(f"{factor}x built {row.get('episodes')} episodes, declared {declared['episodes']}")
        recall = row.get("recall_s") or {}
        if "p50" not in recall or "p95" not in recall:
            problems.append(f"{factor}x has no p50 and p95")
        if not isinstance(row.get("model_calls"), int) or not isinstance(row.get("prompt_tokens"), int):
            problems.append(f"{factor}x has no model-call and token counts")
        by_lang = row.get("by_lang") or {}
        for lang in sorted(langs):
            if lang not in by_lang or "p50" not in (by_lang.get(lang) or {}):
                problems.append(f"{factor}x missing {lang}")
    return problems


def _remove(path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(path) + suffix)
        except OSError:
            pass
    ops = path.with_name(path.stem + "-ops.db")
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(str(ops) + suffix)
        except OSError:
            pass


def estimate_fill(embedder: Any, *, sample: int = 48, episodes: int | None = None,
                  fact_every: int = FACT_EVERY) -> dict[str, Any]:
    """Time *sample* episodes through the write path and extrapolate each factor.

    The sample is the fill only. Recall of the question set is not in it.
    """
    base = ONE_X_EPISODES if episodes is None else episodes
    sample = max(1, min(sample, base))
    folder = Path(tempfile.mkdtemp(prefix="kazma-scale-size-"))
    path = folder / "sample.db"
    try:
        started = time.perf_counter()
        built = build_store(path, embedder, factor=1, episodes=sample, fact_every=fact_every)
        elapsed = time.perf_counter() - started
    finally:
        _remove(path)
        try:
            folder.rmdir()
        except OSError:
            pass
    per = elapsed / sample
    sizes = {}
    for factor in FACTORS:
        n = base * factor
        fill_s = per * n
        sizes[str(factor)] = {"episodes": n, "fill_s": fill_s,
                               "under_two_hours": within_two_hours(fill_s, factor=factor)}
    return {"sample_n": sample, "sample_s": elapsed, "per_episode_s": per, "one_x_episodes": base,
            "recall_included": False, "fill_what": FILL_WHAT, "sizes": sizes, "sample_facts": built["facts"]}


def _machine(lowered: bool) -> dict[str, Any]:
    import platform

    info: dict[str, Any] = {
        "cpu": platform.processor() or platform.machine(),
        "logical_cpus": os.cpu_count(),
        "os": platform.platform(),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "priority": "below normal" if lowered else "not lowered",
    }
    try:
        import psutil

        info["ram_gb"] = round(psutil.virtual_memory().total / 2 ** 30, 1)
    except Exception:
        logger.warning("[scale] ram not read", exc_info=True)
    try:
        import sqlite_vec

        info["sqlite_vec"] = getattr(sqlite_vec, "__version__", "?")
    except Exception:
        info["sqlite_vec"] = None
    return info


def _size_embedder(embedder: Any) -> CountingEmbedder:
    """A new wrapper for one size.

    Recall caches a question vector by ``id(embedder)`` (64 entries, and the
    question set fits). One wrapper kept across sizes makes every size after
    the first embed nothing, and its ``local_embeds`` is 0.
    """
    inner = embedder.inner if isinstance(embedder, CountingEmbedder) else embedder
    return CountingEmbedder(inner)


def measure(factors: tuple[int, ...], embedder: Any, *, episodes: int | None = None,
            fact_every: int = FACT_EVERY,
            questions: tuple[dict[str, str], ...] | list[dict[str, str]] | None = None,
            recall_fn: Callable[[str, sqlite3.Connection], Any] | None = None,
            clock: Callable[[], float] | None = None, profile: bool = False) -> dict[str, Any]:
    """Build each factor and time the question set. The caller decided 100x."""
    asked = list(questions or QUESTIONS)
    call = recall_fn or _product_recall
    report: dict[str, Any] = {
        "what": "recall on a private store. The insert is the bench's own, not write_episode. No paid model",
        "fill_what": FILL_WHAT,
        "tokens_what": TOKENS_WHAT,
        "one_x_episodes": ONE_X_EPISODES if episodes is None else episodes,
        "one_x_why": "tests/fixtures/memory_bench/live_lengths.json episodes, the backup that file records; "
                     "text is synthetic, lengths are that file's percentiles; facts are the quick pass on "
                     f"every {fact_every}th turn" if fact_every else
                     "tests/fixtures/memory_bench/live_lengths.json episodes; no preference turns planted",
        "backup_taken": LENGTHS.get("backup_taken"),
        "fact_every": fact_every,
        "turns_per_chat": TURNS_PER_CHAT,
        "budget_s": {"p50": BUDGET_P50_S, "p95": BUDGET_P95_S,
                     "what": "decision 4, time a reranker may add; recall itself is what is timed"},
        "questions": {"en": sum(1 for q in asked if q["lang"] == "en"),
                      "ar": sum(1 for q in asked if q["lang"] == "ar")},
        "made_on": datetime.now(UTC).date().isoformat(),
        "sizes": {},
    }
    for factor in factors:
        counting = _size_embedder(embedder)
        folder = Path(tempfile.mkdtemp(prefix=f"kazma-scale-{factor}-"))
        path = folder / "memory.db"
        try:
            started = time.perf_counter()
            held = build_store(path, counting, factor=factor, episodes=episodes, fact_every=fact_every)
            fill_s = time.perf_counter() - started
            conn = sqlite3.connect(path)
            conn.row_factory = sqlite3.Row
            try:
                counting.reset()
                timed = time_recalls(conn, asked, recall_fn=call, clock=clock, embedder=counting)
            finally:
                conn.close()
            row = {**held, "fill_s": fill_s, "db_mb": round(path.stat().st_size / 2 ** 20, 2), **timed}
            if profile and row["over_budget"] and row.get("slowest_question"):
                conn = sqlite3.connect(path)
                conn.row_factory = sqlite3.Row
                try:
                    # The question already timed as the slowest. Profiling it
                    # again asks recall once; the others are not asked again.
                    row["profile"] = profile_slowest(conn, row["slowest_question"], _size_embedder(embedder))
                finally:
                    conn.close()
            report["sizes"][str(factor)] = row
        finally:
            _remove(path)
            try:
                folder.rmdir()
            except OSError:
                pass
    return report


def hundred_x_block(estimate: dict[str, Any]) -> str | None:
    """Why 100x must not start, or None when its fill is under two hours.

    The estimate is the fill only (``estimate_fill``). 1x and 10x are not
    this decision."""
    fill_s = float(estimate["sizes"]["100"]["fill_s"])
    if within_two_hours(fill_s, factor=100):
        return None
    return f"100x fill estimates {fill_s:.0f} s, which is not under two hours; not started"


def _private_data_folder() -> None:
    """Kazma's own files go to a temporary folder. Nothing of an install is read.

    The embedder is forced local: a shell variable must not send this bench
    to a paid embedding API. Called from ``main`` only, never at import.
    """
    import atexit
    import shutil

    folder = tempfile.mkdtemp(prefix="kazma-scale-data-")
    atexit.register(shutil.rmtree, folder, True)
    os.environ["KAZMA_DATA_DIR"] = folder
    os.environ["KAZMA_USER_HOME"] = folder
    os.environ["KAZMA_EMBEDDER_PREWARM"] = "0"
    os.environ["KAZMA_OPS_ALERTS"] = "0"
    os.environ["KAZMA_EMBED_PROVIDER"] = "local"
    os.environ.pop("KAZMA_EMBED_BASE_URL", None)
    from kazma_core.paths import pin_project_root

    pin_project_root(REPO)


def _priority_lowered() -> bool:
    """Ask for below-normal priority. False when the OS refuses. An OSError
    propagates so the caller can say the failure."""
    if os.name != "nt":
        os.nice(10)
        return True
    import ctypes

    kernel = ctypes.windll.kernel32
    return bool(kernel.SetPriorityClass(kernel.GetCurrentProcess(), 0x00004000))


def _below_normal() -> bool:
    """Leave the machine responsive. The live server shares it.

    False, and a warning, when the priority was not lowered. A report must
    not say "below normal" in that case.
    """
    try:
        lowered = _priority_lowered()
    except OSError:
        logger.warning("[scale] could not lower priority", exc_info=True)
        return False
    if not lowered:
        logger.warning("[scale] could not lower priority")
        return False
    return True


def _limit_threads() -> None:
    try:
        import torch
    except ImportError:
        return
    torch.set_num_threads(max(1, min(4, (os.cpu_count() or 2) // 4)))


def _real_embedder() -> Any:
    from kazma_core.memory.embedder import get_embedder

    embedder = get_embedder()
    if embedder is None:
        raise SystemExit("no embedder is configured: install the rag extra (bge-m3)")
    return CountingEmbedder(embedder)


def _warm(embedder: Any) -> None:
    """Load the model before a timed sample. A load inside the sample would be
    counted once per episode of the extrapolation. An empty vector means the
    model did not load: the bench stops rather than time keyword search alone."""
    vectors = embedder.encode_batch(["warm one", "warm two", "warm three", "warm four"])
    if not vectors or any(not vec for vec in vectors):
        raise SystemExit("the local embedder returned no vector; the bench was not run")


def _round(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 4)
    if isinstance(value, dict):
        return {k: _round(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_round(v) for v in value]
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    size_p = sub.add_parser("size", help="extrapolate the fill from a measured sample; recall is not run")
    size_p.add_argument("--sample", type=int, default=48)
    run_p = sub.add_parser("run", help="fill and time recall")
    run_p.add_argument("--factors", default="1,10", help="1, 10 and 100, comma-separated")
    run_p.add_argument("--out", type=Path, default=None)
    run_p.add_argument("--sample", type=int, default=48, help="the fill sample used to gate 100x")
    args = parser.parse_args(argv)
    lowered = _below_normal()
    _private_data_folder()
    _limit_threads()
    embedder = _real_embedder()
    _warm(embedder)
    if args.cmd == "size":
        print(json.dumps(_round(estimate_fill(embedder, sample=args.sample)), indent=1))
        return 0
    factors = tuple(int(part) for part in args.factors.split(",") if part.strip())
    for factor in factors:
        if factor not in FACTORS:
            raise SystemExit(f"factor {factor} is not one of {FACTORS}")
    if 100 in factors:
        estimate = estimate_fill(embedder, sample=args.sample)
        fill_s = float(estimate["sizes"]["100"]["fill_s"])
        print(f"100x fill estimate {fill_s:.0f} s from {estimate['sample_n']} episodes "
              f"in {estimate['sample_s']:.1f} s (the bench's own insert, not write_episode; "
              "recall not included)", file=sys.stderr)
        blocked = hundred_x_block(estimate)
        if blocked:
            raise SystemExit(blocked)
    report = measure(factors, embedder, profile=True)
    report["machine"] = _machine(lowered)
    text = json.dumps(_round(report), indent=1, ensure_ascii=False)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8", newline="\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
