"""Relevance: a local cross-encoder reads the question beside each memory.

The memory plan's W3 + W2 (2026-11-22, the main session's decisions).
Recall's evidence (``recall._rank_by_evidence``) is meaning above the
question's background plus the share of its words a memory holds; it ranks
by how near a memory is, not by whether it answers. A cross-encoder reads
the question and the memory together and says how well one answers the
other. Here it rescores the memories today's evidence ranks first, and the
memories a passage brought (``memory/passages.py``: what a memory's own
vector misses, its answer above all), and its calibrated score decides what
is shown (W2): below the floor, nothing is "about the question".

* **The model**: :data:`MODEL` (multilingual, 118M, Apache-2.0) at a pinned
  revision, loaded from the local Hugging Face cache only, never with remote
  code; :data:`REFERENCE_MODEL` is the quality reference, measured and not
  shipped.
* **Anytime** (:meth:`Reranker.score`): the memories are read in today's
  order, a few at a time, until the recall's budget ends
  (:data:`BUDGET_S`); what was not read keeps today's rule, and the recall
  says it was partial (``RecallResult.rerank``).
* **Calibrated** (:func:`calibrated`): the model's score becomes the
  probability that a memory answers the question, fitted per kind of memory
  on the benchmark's first set; the floor and the gap are probabilities.

Off (:func:`in_use`); a measuring script turns it on. Not accepted (22-23
Nov, ``docs/plans/MEMORY_TOP_SCORES_PLAN.md`` W3): under the plan's rule no
floor kept every answer on Kazma's benchmark's four stores with either
model, and the reference model reads several times slower than the latency
budget on this PC's CPU.
"""

from __future__ import annotations

import contextlib
import logging
import math
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from typing import Any, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "BUDGET_S",
    "MODEL",
    "MODEL_REVISION",
    "REFERENCE_MODEL",
    "REFERENCE_REVISION",
    "Reranker",
    "calibrated",
    "get_reranker",
    "in_use",
    "used",
]

#: The shipping candidate and the quality reference, each at the revision
#: downloaded and measured (21 Nov; both Apache-2.0).
MODEL = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
REFERENCE_MODEL = "BAAI/bge-reranker-v2-m3"
REFERENCE_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
_PINS = {MODEL: MODEL_REVISION, REFERENCE_MODEL: REFERENCE_REVISION}

#: Whether recall reranks. Off: not accepted (module docstring).
_IN_USE = False

#: The time a recall may spend reading memories with the model, all kinds
#: together; past it the rest keep today's rule (the plan's latency budget:
#: at most 150 ms p50 and 400 ms p95 added to a recall on this PC).
BUDGET_S = 0.15

#: Tokens of a question and a memory read together: a memory's start and
#: its passage's stretch are read as two pairs (a passage is about 256
#: tokens, ``limits.PASSAGE_TOKENS``).
MAX_TOKENS = 256

#: Pairs read between two looks at the clock.
BATCH = 4

#: Probability = sigmoid(c0 + c_evidence * evidence + c_score * score), per
#: (model, kind of memory): a logistic fit on the benchmark's first set
#: (``scripts/memory_rerank_calibrate.py``) of whether a memory answers,
#: from recall's evidence and the model's score together. Measured 22 Nov on
#: the benchmark's store: the model's score alone separates answers from
#: the rest no better than the evidence (turns AUC 0.915 against 0.926,
#: facts 0.845 against 0.945); the two together better (turns 0.944, the
#: second set 0.953 against 0.918). A pair with no entry reads the model's
#: score as a logit. Fitted 22 Nov on the recorded scores
#: (``tests/fixtures/memory_bench/rerank_scores.npz``), the first set's
#: candidates in the benchmark's store; AUC on the first set and the second
#: (never fitted on): turns 0.944 / 0.949 (evidence 0.935 / 0.918), weekly
#: summaries 0.956 / 0.971 (0.937 / 0.964), facts 0.936 / 0.946 (0.938 /
#: 0.945: no better than the evidence, so facts are not reranked).
CALIBRATION: dict[tuple[str, str], tuple[float, float, float]] = {
    (MODEL, "episode"): (-2.6177, 13.2456, 0.2978),
    (MODEL, "belief"): (-2.7618, 13.9291, 0.2418),
    (MODEL, "summary"): (-4.4445, 13.5682, 0.253),
    # The quality reference's, measured the same way (turns 0.967 / 0.977,
    # its score alone 0.963 / 0.976; summaries 0.964 / 0.964): not shipped.
    (REFERENCE_MODEL, "episode"): (-0.6673, 6.8479, 0.4761),
    (REFERENCE_MODEL, "summary"): (-1.6861, 5.806, 0.483),
}


def in_use() -> bool:
    """Whether recall reranks (W3)."""
    return _IN_USE


@contextlib.contextmanager
def used(on: bool = True) -> Iterator[None]:
    """Reranking on (or off) for the block: a measurement's own switch."""
    global _IN_USE
    before, _IN_USE = _IN_USE, bool(on)
    try:
        yield
    finally:
        _IN_USE = before


def calibrated(model: str, kind: str, score: float, evidence: float = 0.0) -> float:
    """The probability that a memory of *kind* answers, from *model*'s score
    and recall's evidence for it (:data:`CALIBRATION`)."""
    c0, c_evidence, c_score = CALIBRATION.get((model, kind), (0.0, 0.0, 1.0))
    z = c0 + c_evidence * float(evidence) + c_score * float(score)
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


class _Scorer(Protocol):
    """What recall asks: the model's score of each text for the question, in
    order, None for those not read before *deadline* (``time.monotonic``)."""

    model: str

    def score(self, query: str, texts: Sequence[str], *, deadline: float) -> list[float | None]: ...


class Reranker:
    """The cross-encoder, loaded once, read by one recall at a time."""

    def __init__(self, model: str = MODEL, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.model = model
        self._clock = clock
        self._lock = threading.Lock()
        self._ce: Any = None

    def _load(self) -> Any:
        if self._ce is None:
            from sentence_transformers import CrossEncoder

            self._ce = CrossEncoder(self.model, revision=_PINS.get(self.model), local_files_only=True,
                                    trust_remote_code=False, max_length=MAX_TOKENS)
        return self._ce

    def score(self, query: str, texts: Sequence[str], *, deadline: float) -> list[float | None]:
        out: list[float | None] = [None] * len(texts)
        left = deadline - self._clock()
        # A read with no budget (a measurement's) waits for the model.
        held = self._lock.acquire() if math.isinf(left) else self._lock.acquire(timeout=max(0.0, left))
        if not texts or not held:
            if held:
                self._lock.release()
            return out  # another recall holds the model past this one's budget
        try:
            import torch

            ce = self._load()
            for start in range(0, len(texts), BATCH):
                if self._clock() >= deadline:
                    break
                chunk = [(query, t) for t in texts[start:start + BATCH]]
                logits = ce.predict(chunk, batch_size=BATCH, show_progress_bar=False,
                                    activation_fn=torch.nn.Identity())
                for i, value in enumerate(logits):
                    out[start + i] = float(value)
        finally:
            self._lock.release()
        return out


_LOADED: Reranker | None = None
_LOAD_LOCK = threading.Lock()
#: When a load failed (``time.monotonic``): not tried again before
#: :data:`_RETRY_S` has passed, so a missing model costs no recall a load.
_FAILED_AT: float | None = None
_RETRY_S = 600.0


def get_reranker() -> _Scorer | None:
    """The shipping model, or None when it is not installed or not on disk
    (recall then keeps today's rule; said once a failure, tried again after
    :data:`_RETRY_S`)."""
    global _LOADED, _FAILED_AT
    with _LOAD_LOCK:
        if _LOADED is None:
            if _FAILED_AT is not None and time.monotonic() - _FAILED_AT < _RETRY_S:
                return None
            try:
                rr = Reranker(MODEL)
                rr._load()
            except (ImportError, OSError, RuntimeError, ValueError):
                # Not installed (the rag extra), not in the cache at the pin,
                # or a file the library cannot read.
                if _FAILED_AT is None:
                    logger.warning("[reranker] %s could not be loaded; recall keeps its evidence rule",
                                   MODEL, exc_info=True)
                _FAILED_AT = time.monotonic()
                return None
            _LOADED, _FAILED_AT = rr, None
        return _LOADED
