"""Semantic capability routing for the swarm: one embedding space, compared exactly.

Swarm audit SW-E10 (decision D8: fixed, not removed). The router kept worker
profiles in a Chroma collection created with no embedding function, so Chroma
embedded them with its own default model (a 384-dimension MiniLM fetched on
first use), while the task was embedded with Kazma's encoder (bge-m3, 1,024
dimensions). Every semantic query failed on the dimensions and fell back to
keywords with a WARNING, after loading both models (the routing tests did it
too, reaching the HF Hub).

Now the task and every profile are embedded by Kazma's own embedder
(:func:`kazma_core.memory.embedder.get_embedder`, the one memory uses), the
vectors are explicit, and they are compared here by cosine, exactly, as
memory compares by meaning (AGENTS §15F/G). There is no vector store: a swarm
has tens of workers, and a store's default embedding function is how the two
spaces were mixed. A profile's vector is kept per embedder INSTANCE
(``embedder_identity``) and text, so a model switch re-embeds it.

What counts as "about" a worker follows memory's lesson (AGENTS §15G): an
absolute cosine cannot tell "about this" from "near this" (bge-m3 puts a task
0.25-0.55 from every profile). A profile matches when it stands out: its
similarity minus the mean of the OTHER distinct profiles' -- blended with
:data:`_BACKGROUND_PRIOR` when there are fewer than :data:`_BACKGROUND_MIN`
of them -- is at least :data:`MATCH_FLOOR`. Instances of one template share
one profile, so they never hide each other. Measured with bge-m3 on
``tests/fixtures/swarm_routing/`` (``scripts/swarm_routing_bench.py``; CI
replays the recorded vectors): see ``tests/test_swarm_semantic_routing.py``.
The nearest worker with no floor -- what the router meant to do -- sent every
unrelated task to some specialist, so none reached the autoscaler.

When the embedder is missing, failing, slow or busy, :meth:`semantic_scores`
says why (:class:`SemanticUnavailable`) and the caller routes by keywords;
the change of state is a WARNING (and ``kazma_swarm_failures_total{site=
"semantic_routing"}``) once, its recovery an INFO line.

Blocking (an embedding, a settings read): callers run :meth:`semantic_scores`
in a thread; it is in ``_LOOP_STALL_HELPERS``.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "MATCH_FLOOR",
    "SemanticRouter",
    "SemanticScores",
    "SemanticUnavailable",
    "evidence",
    "get_semantic_router",
    "note_outcome",
    "profile_text",
]

logger = logging.getLogger(__name__)

#: How far a profile must stand out from the others (bge-m3, measured).
MATCH_FLOOR = 0.045
#: What an unrelated profile reaches for a task with bge-m3; stands in for
#: missing others below :data:`_BACKGROUND_MIN` (one instance registered).
_BACKGROUND_PRIOR = 0.40
_BACKGROUND_MIN = 3

#: The task text compared: its start and its end (memory's rule for a long
#: paste, ``recall._bounded_question``).
_QUERY_MAX_CHARS = 2000
#: How much of a worker's system prompt describes it.
_PROFILE_PROMPT_CHARS = 200
#: Profile vectors kept (embedder identity and text): far more than a swarm
#: has workers.
_PROFILE_CACHE = 512
#: Embedding calls at once; past this a route answers by keywords.
_EMBED_IN_FLIGHT = 2


def profile_text(profile: dict[str, Any]) -> str:
    """The text a worker is embedded as: its name, expertise and the start of
    its system prompt (else its role). An autoscaler instance is named by its
    template (``template``), so every instance of one shares one profile.
    Empty when the worker declares neither expertise nor a description."""
    name = str(profile.get("template") or profile.get("name") or "").strip()
    expertise = ", ".join(str(e).strip() for e in (profile.get("expertise") or []) if str(e).strip())
    description = str(profile.get("system_prompt") or "").strip()
    if not description:
        description = ", ".join(str(r).strip() for r in (profile.get("roles") or []) if str(r).strip())
    if not expertise and not description:
        return ""
    text = f"Worker: {name}\nExpertise: {expertise}\n"
    if description:
        text += f"Description: {description[:_PROFILE_PROMPT_CHARS]}\n"
    return text


def _query_text(prompt: str) -> str:
    """The task as it is embedded: at most ``_QUERY_MAX_CHARS``, its start
    and its end."""
    q = str(prompt or "")
    if len(q) <= _QUERY_MAX_CHARS:
        return q
    head = _QUERY_MAX_CHARS // 4
    return q[:head] + " " + q[-(_QUERY_MAX_CHARS - head):]


def _cosine(a: list[float], b: list[float]) -> float | None:
    if not a or not b or len(a) != len(b):
        return None
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if not na or not nb:
        return None
    return dot / (na * nb)


def evidence(similarities: dict[str, float]) -> dict[str, float]:
    """For each distinct profile (text -> similarity), how far it stands out:
    its similarity minus the mean of the others', blended with the prior when
    there are fewer than ``_BACKGROUND_MIN`` others. Pure."""
    out: dict[str, float] = {}
    total = sum(similarities.values())
    n = len(similarities)
    for text, sim in similarities.items():
        others = n - 1
        missing = max(0, _BACKGROUND_MIN - others)
        background = (total - sim + _BACKGROUND_PRIOR * missing) / (others + missing)
        out[text] = sim - background
    return out


@dataclass(frozen=True)
class SemanticScores:
    """What the embedder said about one task."""

    #: worker -> cosine with its profile.
    similarities: dict[str, float]
    #: worker -> how far its profile stands out (see :func:`evidence`).
    evidence: dict[str, float]
    #: The workers the task is about, best first (empty: none is).
    matches: list[str]
    model: str
    #: Workers with no profile to compare (no expertise, prompt or role).
    unprofiled: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SemanticUnavailable:
    """Why this task is routed by keywords."""

    reason: str  # no_embedder | embed_failed | busy | timeout | no_profiles | error
    detail: str = ""


def _kazma_embedder() -> Any:
    """Kazma's shared embedder (memory's), or None."""
    from kazma_core.memory.embedder import get_embedder

    return get_embedder()


class SemanticRouter:
    """Scores a task against worker profiles with Kazma's embedder.

    ``embedder_getter`` defaults to Kazma's shared embedder, looked up on
    every call (a configuration change or a test replaces it).
    """

    def __init__(self, embedder_getter: Callable[[], Any] | None = None) -> None:
        self._embedder_getter = embedder_getter
        self._lock = threading.Lock()
        self._vectors: OrderedDict[tuple[str, str], list[float]] = OrderedDict()
        self._in_flight = threading.BoundedSemaphore(_EMBED_IN_FLIGHT)

    def _embedder(self) -> Any:
        getter = self._embedder_getter or _kazma_embedder
        return getter()

    def _vector(self, embedder: Any, identity: str, text: str) -> list[float]:
        key = (identity, text)
        with self._lock:
            hit = self._vectors.get(key)
            if hit is not None:
                self._vectors.move_to_end(key)
                return hit
        vec = [float(x) for x in (embedder.encode(text) or [])]
        if vec:
            with self._lock:
                self._vectors[key] = vec
                while len(self._vectors) > _PROFILE_CACHE:
                    self._vectors.popitem(last=False)
        return vec

    def semantic_scores(self, prompt: str, profiles: list[dict[str, Any]]) -> SemanticScores | SemanticUnavailable:
        """Score *prompt* against every profile. Blocking: run it in a thread.

        Never raises: a reason to route by keywords instead is returned (and
        :func:`note_outcome` says it)."""
        if not self._in_flight.acquire(blocking=False):
            return SemanticUnavailable("busy", f"{_EMBED_IN_FLIGHT} embeddings already running")
        try:
            return self._scores(prompt, profiles)
        except Exception as exc:  # any embedder's failure: the answer, said by note_outcome
            return SemanticUnavailable("error", f"{type(exc).__name__}: {exc}"[:300])
        finally:
            self._in_flight.release()

    def _scores(self, prompt: str, profiles: list[dict[str, Any]]) -> SemanticScores | SemanticUnavailable:
        texts = {str(p.get("name", "")): profile_text(p) for p in profiles if p.get("name")}
        unprofiled = sorted(name for name, text in texts.items() if not text)
        texts = {name: text for name, text in texts.items() if text}
        if not texts:
            return SemanticUnavailable("no_profiles", "no worker declares expertise or a description")
        embedder = self._embedder()
        if embedder is None:
            return SemanticUnavailable("no_embedder", "no embedder is configured")
        from kazma_core.memory.vector_store_global import embedder_identity

        identity = embedder_identity(embedder)
        query = [float(x) for x in (embedder.encode(_query_text(prompt)) or [])]
        if not query:
            return SemanticUnavailable("embed_failed", _embedder_problem(embedder))
        by_text: dict[str, float] = {}
        for text in sorted(set(texts.values())):
            sim = _cosine(query, self._vector(embedder, identity, text))
            if sim is None:
                return SemanticUnavailable("embed_failed", _embedder_problem(embedder))
            by_text[text] = sim
        stands_out = evidence(by_text)
        similarities = {name: by_text[text] for name, text in texts.items()}
        worker_evidence = {name: stands_out[text] for name, text in texts.items()}
        matches = sorted((name for name, ev in worker_evidence.items() if ev >= MATCH_FLOOR),
                         key=lambda name: (-similarities[name], name))
        return SemanticScores(similarities=similarities, evidence=worker_evidence, matches=matches,
                              model=identity, unprofiled=unprofiled)


def _embedder_problem(embedder: Any) -> str:
    """What the embedder's own breaker says (Memory audit M16), if anything."""
    breaker = getattr(embedder, "breaker", None)
    state = breaker.state() if breaker is not None and hasattr(breaker, "state") else {}
    return str(state.get("last_error") or "the embedder returned no vector")[:300]


# ── Reporting: a change of state is said once ─────────────────────────────

#: Not a change of state: another route is embedding, or no worker has a
#: profile to compare.
_TRANSIENT = frozenset({"busy", "no_profiles"})
_state_lock = threading.Lock()
_semantic_up: bool | None = None


def note_outcome(outcome: SemanticScores | SemanticUnavailable) -> None:
    """Say when semantic routing stops (WARNING, counted) and when it is back
    (INFO). A route that found it busy, or had no profile, changes nothing."""
    global _semantic_up
    if isinstance(outcome, SemanticUnavailable) and outcome.reason in _TRANSIENT:
        return
    up = isinstance(outcome, SemanticScores)
    with _state_lock:
        before, _semantic_up = _semantic_up, up
    if up and before is False:
        logger.info("[SemanticRouter] semantic routing is back (%s)", outcome.model)
    elif not up and before is not False:
        logger.warning("[SemanticRouter] semantic routing unavailable (%s: %s): routing by keywords",
                       outcome.reason, outcome.detail)
        from kazma_core.metrics import record_swarm_failure

        record_swarm_failure("semantic_routing")


# ── Module-level singleton ────────────────────────────────────────────────

_semantic_router: SemanticRouter | None = None


def get_semantic_router() -> SemanticRouter:
    """The process's router (its profile vectors are shared)."""
    global _semantic_router
    if _semantic_router is None:
        _semantic_router = SemanticRouter()
    return _semantic_router
