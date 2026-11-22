"""Passages: meaning vectors for what a memory's own vector misses.

The memory plan's W5 + W1b (2026-11-22, the main session's decisions). A
memory's vector is made from the first ``limits.EMBED_TEXT_CHARS`` characters
of its first side that is not blank -- the summary, else the question, else
the answer (``episode_text.embed_text``) -- so meaning search never saw what
the assistant answered, nor a long question past its start. R3 measured
mixing the answer into that vector, and precision fell; a passage is a
vector of its own.

A passage is a stretch of about :data:`limits.PASSAGE_TOKENS` tokens, cut at
sentence breaks with :data:`limits.PASSAGE_OVERLAP_TOKENS` overlapping, of
what the memory's vector misses (the main session's choice (a)): every side
that is not the vector's own (the answer, as a rule), and the vector's own
side past its first ``EMBED_TEXT_CHARS`` characters. A passage names its
memory and the memory's space, the side and where it stands in it; it holds
no text, only its vector and the model that made it.

* **Written** after the memory, never under a write lock
  (:func:`write_passages`: every vector made first, then one short
  transaction that writes only while the memory still holds the text the
  vectors were made from): by the memory writer (``dual_write.mirror_episode``,
  every turn), by the benchmark's and LongMemEval's seeding, and by the
  vector repair sweep for memories written before, or whose model changed
  (:func:`fill_passages`).
* **Taken by a forget**: triggers on ``episodes`` delete a memory's passages
  when its row goes, when it becomes ``forgotten``, when its text changes
  (the repair writes them again) or when it moves to another space
  (:func:`ensure_passages_schema`).
* **Searched for candidates** (``VectorEngine.best_passages``): the memories
  whose best passage is among the question's nearest are candidates the
  reranker reads beside that passage (W3, ``memory/reranker.py``), and the
  block shows the stretch a memory was kept by (W1b, ``recall``).

Off (:func:`in_use`). Counted as a memory's meaning -- the better of its
vector and its best passage -- they lost an answer at four times the store
and one in the speakers' space, and lowered precision, and no threshold the
plan's rule allows recovered it (measured 22 Nov,
``docs/plans/MEMORY_TOP_SCORES_PLAN.md``, W5): they never lift a memory's
meaning. The main session's decision: they bring the reranker's candidates.
With the switch off, the triggers on ``episodes`` are all that runs: an
insert fires none; a change of a memory's text, tier or space fires
``kazma_passages_au`` (two deletes by key on empty tables when the text
changed or the memory was forgotten, nothing otherwise), a delete fires
``kazma_passages_ad`` (the same two deletes).
"""

from __future__ import annotations

import contextlib
import logging
import re
import sqlite3
import time
from collections.abc import Callable, Iterator
from typing import Any

from kazma_core.memory.limits import EMBED_TEXT_CHARS, PASSAGE_OVERLAP_TOKENS, PASSAGE_TOKENS

logger = logging.getLogger(__name__)

__all__ = ["ensure_passages_schema", "fill_passages", "in_use", "missed_spans", "used", "write_passages"]

#: Whether passages are written and searched. Off: not accepted (the memory
#: plan's W5, module docstring); a measuring script turns it on.
_IN_USE = False

#: What the repair sweep does in one pass: memories, and about how long.
FILL_PER_PASS = 200
FILL_SECONDS = 20.0

#: Where a sentence ends: a stop, a question or an exclamation mark (Arabic's
#: too) before a space, or a line break. A run of marks is read once, from its
#: start (the lookbehind) and whole (possessive): linear on a long run.
_SENTENCE_END = re.compile(r"(?<![.!?؟؛])[.!?؟؛]++(?=\s)|\n+")


def in_use() -> bool:
    """Whether passages are written and searched (W5)."""
    return _IN_USE


@contextlib.contextmanager
def used(on: bool = True) -> Iterator[None]:
    """Passages on (or off) for the block: a measurement's own switch."""
    global _IN_USE
    before, _IN_USE = _IN_USE, bool(on)
    try:
        yield
    finally:
        _IN_USE = before


def ensure_passages_schema(conn: Any) -> None:
    """The passage table, the record of what the repair has filled, and the
    triggers that take a memory's passages with its row, its forget, a
    change of its text and a move to another space."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episode_passages ("
        " episode_id TEXT NOT NULL, tenant_id TEXT NOT NULL, side TEXT NOT NULL,"
        " start INTEGER NOT NULL, stop INTEGER NOT NULL,"
        " embedding BLOB NOT NULL, embedding_model_version TEXT NOT NULL DEFAULT '',"
        " created_at REAL NOT NULL,"
        " PRIMARY KEY (episode_id, side, start))")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_episode_passages_tenant ON episode_passages(tenant_id)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS episode_passage_state ("
        " episode_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,"
        " model TEXT NOT NULL DEFAULT '', passages INTEGER NOT NULL, written_at REAL NOT NULL)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_episode_passage_state_tenant ON episode_passage_state(tenant_id)")
    gone = ("DELETE FROM episode_passages WHERE episode_id = old.id; "
            "DELETE FROM episode_passage_state WHERE episode_id = old.id;")
    conn.execute(f"CREATE TRIGGER IF NOT EXISTS kazma_passages_ad AFTER DELETE ON episodes BEGIN {gone} END")
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS kazma_passages_au "
        "AFTER UPDATE OF user_text, assistant_text, summary_text, tier, tenant_id ON episodes "
        "WHEN new.tier = 'forgotten' OR old.user_text IS NOT new.user_text "
        "OR old.assistant_text IS NOT new.assistant_text OR old.summary_text IS NOT new.summary_text "
        f"OR old.tenant_id IS NOT new.tenant_id BEGIN {gone} END")


def _sentences(text: str) -> list[tuple[int, int]]:
    """*text*'s sentences as ``(start, stop)``, every character in one."""
    out: list[tuple[int, int]] = []
    at = 0
    for end in _SENTENCE_END.finditer(text):
        if end.end() > at:
            out.append((at, end.end()))
            at = end.end()
    if at < len(text):
        out.append((at, len(text)))
    return out


def _cut_long(text: str, start: int, stop: int, budget: int) -> list[tuple[int, int]]:
    """A sentence longer than *budget* tokens, cut at spaces into pieces of
    about *budget* tokens each."""
    from kazma_core.summarizer import estimate_text_tokens

    tokens = max(1, estimate_text_tokens(text[start:stop]))
    room = max(1, (stop - start) * budget // tokens)  # characters per piece
    out = []
    at = start
    while at < stop:
        end = min(stop, at + room)
        if end < stop:
            space = text.rfind(" ", at + room // 2, end)
            end = space if space > at else end
        out.append((at, end))
        at = end
    return out


def _passage_spans(text: str) -> list[tuple[int, int]]:
    """*text* cut into passages of about :data:`PASSAGE_TOKENS` tokens at
    sentence breaks, each but the first starting about
    :data:`PASSAGE_OVERLAP_TOKENS` tokens before the one before it ended
    (whole sentences; none when its last sentence is longer). Tokens are
    counted as the context trim counts them (``summarizer``): Arabic at
    about 2.5 characters a token."""
    from kazma_core.summarizer import estimate_text_tokens

    pieces: list[tuple[int, int, int]] = []
    for start, stop in _sentences(text):
        if not text[start:stop].strip():
            continue
        tokens = estimate_text_tokens(text[start:stop])
        if tokens > PASSAGE_TOKENS:
            pieces.extend((a, b, estimate_text_tokens(text[a:b])) for a, b in _cut_long(text, start, stop,
                                                                                        PASSAGE_TOKENS))
        else:
            pieces.append((start, stop, tokens))
    spans: list[tuple[int, int]] = []
    first = 0
    while first < len(pieces):
        # Counted on the joined text, as the trim counts it (a sum of the
        # sentences' own counts runs short by their spaces).
        last = first
        while (last + 1 < len(pieces)
               and estimate_text_tokens(text[pieces[first][0]:pieces[last + 1][1]]) <= PASSAGE_TOKENS):
            last += 1
        spans.append((pieces[first][0], pieces[last][1]))
        if last + 1 >= len(pieces):
            break
        # The next passage starts a few sentences back: those that fit in
        # the overlap, never the whole passage again.
        back = last + 1
        while (back - 1 > first
               and estimate_text_tokens(text[pieces[back - 1][0]:pieces[last][1]]) <= PASSAGE_OVERLAP_TOKENS):
            back -= 1
        first = back
    return spans


def missed_spans(user_text: str | None, assistant_text: str | None,
                 summary_text: str | None = "") -> list[tuple[str, int, int]]:
    """The passages of what a memory's own vector misses, ``(side, start,
    stop)``: every side that is not the vector's (``episode_text.embed_text``
    takes the summary, else the question, else the answer), and the
    vector's own side past its first ``EMBED_TEXT_CHARS`` characters.
    Offsets are into the side's text as stored."""
    sides = (("question", user_text or ""), ("answer", assistant_text or ""))
    head = "summary" if (summary_text or "").strip() else next(
        (name for name, text in sides if text.strip()), "")
    out: list[tuple[str, int, int]] = []
    for name, text in sides:
        if not text.strip():
            continue
        for start, stop in _passage_spans(text):
            if name == head and stop <= EMBED_TEXT_CHARS:
                continue  # the memory's own vector covers it
            out.append((name, start, stop))
    return out


def _current_model() -> str:
    from kazma_core.memory.embedder import get_embedding_model_name

    return get_embedding_model_name() or ""


def write_passages(conn: sqlite3.Connection, episode_id: str, *, lock: Any = None,
                   embedder: Any = None) -> int | None:
    """Make and store *episode_id*'s passages: how many, or None when nothing
    was written (passages off, the memory gone or forgotten, the embedder
    unavailable, or the memory's text changed while its passages were made:
    the repair sweep writes them later). Every vector is made first, with no
    transaction open and no lock held; then one short transaction (under
    *lock*, the writer's, when given) replaces the memory's passages, only
    while the memory still holds the text they were cut from. Commits only a
    transaction this call began."""
    if not in_use() or not episode_id:
        return None
    from kazma_core.memory.embedder import serialize_f32_embedding
    from kazma_core.memory.vector_write import encode_vector

    hold = lock if lock is not None else contextlib.nullcontext()
    with hold:
        row = conn.execute(
            "SELECT tenant_id, tier, user_text, assistant_text, summary_text FROM episodes WHERE id = ?",
            (episode_id,)).fetchone()
    if row is None or row[1] == "forgotten":
        return None
    tenant, _tier, question, answer, summary = (row[0], row[1], row[2] or "", row[3] or "", row[4] or "")
    texts = {"question": question, "answer": answer}
    blobs: list[tuple[str, int, int, bytes]] = []
    for side, start, stop in missed_spans(question, answer, summary):
        vec = encode_vector(texts[side][start:stop], embedder=embedder)
        if not vec:
            return None  # the embedder is down: the repair sweep tries again
        blobs.append((side, start, stop, serialize_f32_embedding(vec)))
    model, now = _current_model(), time.time()
    with hold:
        began = not conn.in_transaction
        try:
            still = conn.execute(
                "SELECT 1 FROM episodes WHERE id = ? AND tenant_id = ? AND tier != 'forgotten' "
                "AND user_text IS ? AND assistant_text IS ? AND summary_text IS ?",
                (episode_id, tenant, row[2], row[3], row[4])).fetchone()
            if still is None:
                return None
            conn.execute("DELETE FROM episode_passages WHERE episode_id = ?", (episode_id,))
            conn.executemany(
                "INSERT INTO episode_passages (episode_id, tenant_id, side, start, stop, embedding, "
                "embedding_model_version, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [(episode_id, tenant, side, start, stop, blob, model, now) for side, start, stop, blob in blobs])
            conn.execute(
                "INSERT OR REPLACE INTO episode_passage_state (episode_id, tenant_id, model, passages, written_at) "
                "VALUES (?, ?, ?, ?, ?)", (episode_id, tenant, model, len(blobs), now))
            if began and conn.in_transaction:
                conn.commit()
        except sqlite3.Error:
            if began and conn.in_transaction:
                conn.rollback()
            raise
    return len(blobs)


def fill_passages(conn: sqlite3.Connection, *, budget: int = FILL_PER_PASS, seconds: float = FILL_SECONDS,
                  embedder: Any = None, clock: Callable[[], float] = time.monotonic) -> dict[str, int]:
    """The repair sweep's part (``reembed.run_vector_repair_pass``): passages
    for recallable memories that have none of the current model's -- written
    before passages, or before a model switch -- newest first, at most
    *budget* memories and about *seconds* a pass."""
    if not in_use():
        return {"filled": 0, "passages": 0}
    from kazma_core.memory.vector_engine import RECALLABLE_TIERS

    tiers = ",".join("?" for _ in RECALLABLE_TIERS)
    rows = conn.execute(
        "SELECT e.id FROM episodes e LEFT JOIN episode_passage_state s "
        "ON s.episode_id = e.id AND s.model = ? "
        f"WHERE s.episode_id IS NULL AND e.tier IN ({tiers}) ORDER BY e.created_at DESC LIMIT ?",  # nosec B608 - '?' placeholders only
        (_current_model(), *RECALLABLE_TIERS, int(budget))).fetchall()
    deadline = clock() + seconds
    filled = written = 0
    for (eid,) in rows:
        if clock() > deadline:
            break
        made = write_passages(conn, str(eid), embedder=embedder)
        if made is None:
            continue
        filled += 1
        written += made
    return {"filled": filled, "passages": written}
