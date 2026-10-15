"""Swarm semantic routing, fixed (Swarm audit SW-E10, decision D8).

The witness ``test_sw_e11c_semantic_router_mixes_embedding_spaces``
(``session-artifacts/audits-2026-10-13/test_swarm_engine_witnesses.py``)
found worker profiles stored in a Chroma collection with no embedding
function -- embedded by Chroma's default model (384-d MiniLM) -- and queries
embedded by Kazma's encoder (1,024-d bge-m3). On the dev install the routing
tests logged "Collection expecting embedding with dimension of 384, got
1024" on every route, after loading both models. And the stored task named
up to five routed workers while a DISPATCH ran only the first.

Inverted here:

* one embedding space: the task and every profile go through the one
  embedder, as explicit vectors, compared exactly; no swarm or routing module
  reaches a vector store or another model (a gate, the old source as its
  negative control);
* what counts as "about" a worker is measured with bge-m3's own vectors
  (``tests/fixtures/swarm_routing/``, recorded by
  ``scripts/swarm_routing_bench.py vectors``): the nearest worker with no
  floor -- the rule the old router meant -- is the negative control;
* the stored task names only the worker that ran, every candidate is in the
  routing diagnostics;
* an embedder that is missing, failing, slow or busy routes by keywords,
  said once at WARNING (and counted), its recovery at INFO;
* the embedding runs in a thread (also ``tests/test_swarm_live_and_off_loop.py``).
"""

from __future__ import annotations

import ast
import asyncio
import logging
import sys
import threading
from pathlib import Path

import pytest
from kazma_core.swarm.config import SwarmConfig, WorkerConfig
from kazma_core.swarm.engine import SwarmEngine
from kazma_core.swarm.task import SwarmTask, TaskType, WorkerCapabilities

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import swarm_routing_bench as bench  # noqa: E402


@pytest.fixture(autouse=True)
def _inert(monkeypatch):
    import kazma_core.swarm.patterns as patterns
    import kazma_core.swarm.semantic_router as sr
    import kazma_core.swarm.worker_dispatch as wd

    async def _nothing(*_a, **_k):
        return None

    monkeypatch.setattr(patterns, "_run_self_improvement", _nothing)
    monkeypatch.setattr(wd, "_index_worker_l4_memory", _nothing)
    monkeypatch.setattr(sr, "_kazma_embedder", lambda: None)  # never the real 2 GB model
    monkeypatch.setattr(sr, "_semantic_up", None)  # each test starts from "not yet known"
    monkeypatch.setenv("KAZMA_SHARED_BREAKERS", "0")
    monkeypatch.setenv("KAZMA_TEMPORAL", "0")
    monkeypatch.setenv("KAZMA_SELF_IMPROVEMENT", "0")


class _Embedder:
    """A text naming one of *axes*' words gets that vector (the first word
    found); any other text points along an axis no worker has. Records each
    text it was asked for."""

    dim = 4

    def __init__(self, axes: dict[str, list[float]] | None = None) -> None:
        self.axes = axes or {"alpha": [1.0, 0.0, 0.0, 0.0], "beta": [0.9, 0.1, 0.0, 0.0]}
        self.texts: list[str] = []

    def encode(self, text: str) -> list[float]:
        self.texts.append(text)
        for word, vec in self.axes.items():
            if word in text:
                return list(vec)
        return [0.0, 0.0, 0.0, 1.0]


def _engine(*workers: tuple[str, list[str]]) -> SwarmEngine:
    engine = SwarmEngine(SwarmConfig(enabled=True, workers=[]))
    for name, expertise in workers:
        engine.add_worker(WorkerConfig(name=name, type="in_process",
                                       capabilities=WorkerCapabilities(expertise=expertise)))
    return engine


def _use(engine: SwarmEngine, embedder) -> None:
    from kazma_core.swarm.semantic_router import SemanticRouter

    engine._routing_engine._semantic_router = SemanticRouter(embedder_getter=lambda: embedder)


def _ran(engine: SwarmEngine, seen: list[str]) -> None:
    for name in list(engine._workers):
        async def dispatch(task, context=None, _n=name):
            seen.append(_n)
            return {"worker": _n, "task_id": "", "status": "success", "output": f"by {_n}", "error": None}
        engine.get_worker(name).dispatch = dispatch


# ── One embedding space ─────────────────────────────────────────────────────


def test_the_task_and_every_profile_are_embedded_by_the_one_embedder() -> None:
    """The SW-E10 witness inverted: no collection embeds a profile with its own
    function -- every vector compared comes from the embedder routing was
    given (Kazma's), explicitly."""
    from kazma_core.swarm.semantic_router import SemanticRouter, SemanticScores, _query_text, profile_text

    embedder = _Embedder()
    profiles = [{"name": "alpha", "expertise": ["alpha work"]}, {"name": "gamma", "expertise": ["gamma"]},
                {"name": "delta", "expertise": ["delta"]}]
    outcome = SemanticRouter(embedder_getter=lambda: embedder).semantic_scores("an alpha task", profiles)
    assert isinstance(outcome, SemanticScores), outcome
    assert _query_text("an alpha task") in embedder.texts
    assert {profile_text(p) for p in profiles} <= set(embedder.texts)
    assert outcome.matches == ["alpha"]
    assert outcome.similarities["alpha"] == pytest.approx(1.0)


def test_a_profile_vector_is_kept_per_embedder_instance() -> None:
    """A model switch re-embeds the profiles (vectors of two models are never
    compared); the same embedder embeds each profile once."""
    from kazma_core.swarm.semantic_router import SemanticRouter

    first, second = _Embedder(), _Embedder()
    second._model_name = "another-model"  # another identity (embedder_identity)
    current = [first]
    router = SemanticRouter(embedder_getter=lambda: current[0])
    profiles = [{"name": "alpha", "expertise": ["alpha work"]}]
    router.semantic_scores("alpha", profiles)
    router.semantic_scores("alpha again", profiles)
    assert sum("Worker: alpha" in t for t in first.texts) == 1
    current[0] = second
    router.semantic_scores("alpha", profiles)
    assert sum("Worker: alpha" in t for t in second.texts) == 1


def test_instances_of_one_template_share_a_profile_and_do_not_hide_each_other() -> None:
    """Profiles are compared distinct: three instances of one template are one
    profile, so each does not count the others as its background."""
    from kazma_core.swarm.semantic_router import SemanticRouter, profile_text

    profiles = [{"name": f"coder-pool-ab12-{i}", "template": "coder", "expertise": ["alpha"]} for i in (1, 2, 3)]
    assert len({profile_text(p) for p in profiles}) == 1
    out = SemanticRouter(embedder_getter=lambda: _Embedder()).semantic_scores("alpha", profiles)
    assert out.matches == sorted(p["name"] for p in profiles)


_ROUTING_MODULES = [*sorted((REPO / "kazma-core" / "kazma_core" / "swarm").rglob("*.py")),
                    REPO / "kazma-core" / "kazma_core" / "routing_engine.py"]
#: What would put a second embedding space next to Kazma's: a vector store
#: (its collections embed with a default function) or a model loaded here.
_FORBIDDEN_IMPORTS = {"chromadb", "sentence_transformers", "fastembed", "onnxruntime"}


def _second_space(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name.split(".")[0] for a in node.names} & _FORBIDDEN_IMPORTS
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in _FORBIDDEN_IMPORTS:
                found.add(node.module.split(".")[0])
            if node.module == "kazma_core.memory.vector_store_global":
                found |= {a.name for a in node.names} & {"get_encoder", "VectorStore"}
        elif isinstance(node, ast.Call) and getattr(node.func, "attr", "") in ("add", "upsert") \
                and any(k.arg == "documents" for k in node.keywords) \
                and not any(k.arg == "embeddings" for k in node.keywords):
            found.add("documents without embeddings")
    return found


def test_routing_reaches_no_second_embedding_space() -> None:
    offenders = {f"{p.relative_to(REPO).as_posix()}: {sorted(found)}"
                 for p in _ROUTING_MODULES if (found := _second_space(ast.parse(p.read_text(encoding="utf-8"))))}
    assert not offenders, ("swarm routing embeds through kazma_core.memory.embedder.get_embedder only "
                           f"(SW-E10): {sorted(offenders)}")


def test_the_space_gate_sees_the_old_router() -> None:
    """Negative control: the old SemanticRouter's lines are found."""
    old = ast.parse(
        "def _ensure_model(self):\n"
        "    from kazma_core.memory.vector_store_global import get_encoder\n"
        "    self._model = get_encoder(self._model_name)\n"
        "def _ensure_collection(self):\n"
        "    import chromadb\n"
        "def build_profiles(self, ids, documents, metadatas):\n"
        "    self._collection.add(ids=ids, documents=documents, metadatas=metadatas)\n")
    assert _second_space(old) == {"get_encoder", "chromadb", "documents without embeddings"}


# ── Measured with bge-m3 ───────────────────────────────────────────────────


@pytest.fixture(scope="module")
def measured():
    pytest.importorskip("numpy")
    from kazma_core.memory.benchmark import ReplayEmbedder

    replay = ReplayEmbedder(bench.VECTORS)
    dataset = bench.load_dataset()
    return replay, bench.score(dataset, replay), bench.score(dataset, replay, nearest_only=True)


def test_the_recording_is_bge_m3_and_holds_every_text(measured) -> None:
    replay, _report, _nearest = measured
    assert replay.model == "BAAI/bge-m3" and replay.dim == 1024


def test_a_task_about_a_specialist_reaches_it(measured) -> None:
    """Measured 2026-10-15 (35 tasks, 7 workers, English and Arabic): the
    keyword fallback alone routes 5 of the 24 specialist tasks (it reads no
    Arabic); semantic routing 19, and 21 with the keyword fallback behind it."""
    _replay, report, _nearest = measured
    for scenario in ("all", "specialists"):
        row = report[scenario]
        assert row["specialist_correct"] >= 19, (scenario, row["wrong"])
        assert row["router_specialist_correct"] >= 21, (scenario, row["wrong"])
    keyword = sum(bench._keyword_top(t["prompt"], bench.load_dataset()["workers"]) in t["accept"]
                  for t in bench.load_dataset()["tasks"] if "none" not in t["accept"])
    assert keyword <= 5, "the dataset must still need meaning to route"


def test_a_task_about_no_specialist_reaches_none(measured) -> None:
    """Unrelated tasks go to the autoscaler (no specialist matches): at most
    one of eleven is taken, and a lone instance takes no task about another
    worker. Negative control: the nearest worker with no floor sends 7 of 11
    (11 of 11 without a catch-all) to a specialist, and a lone instance takes
    every task."""
    _replay, report, nearest = measured
    assert report["all"]["misrouted_unrelated"] <= 1 and report["specialists"]["misrouted_unrelated"] <= 1
    assert report["alone"]["taken_wrongly"] == 0 and report["alone"]["found"] >= 7
    assert nearest["all"]["misrouted_unrelated"] >= 7
    assert nearest["specialists"]["misrouted_unrelated"] == nearest["specialists"]["unrelated_tasks"]
    assert nearest["alone"]["taken_wrongly"] == nearest["alone"]["not_about_it"]


def test_arabic_tasks_route_by_meaning(measured) -> None:
    _replay, report, _nearest = measured
    arabic = report["all"]["by_language"]["ar"]
    assert arabic["correct"] >= arabic["tasks"] - 1, report["all"]["wrong"]


# ── The stored task names the worker that ran ───────────────────────────────


async def test_a_dispatch_stores_only_the_worker_that_ran() -> None:
    """Two workers match; the DISPATCH runs one. The task names that one, the
    diagnostics both. (Before: task.workers held every candidate.)"""
    engine = _engine(("alpha", ["alpha work"]), ("beta", ["beta work"]), ("gamma", ["gamma"]), ("delta", ["delta"]))
    _use(engine, _Embedder())
    seen: list[str] = []
    _ran(engine, seen)
    result = await engine.dispatch(SwarmTask(prompt="alpha beta task", workers=["auto"], timeout=0))
    assert result.status == "success"
    stored = engine.get_task(result.task_id)
    diagnostics = stored.metadata["routing_diagnostics"]
    assert diagnostics["strategy_used"] == "semantic"
    # Control: the router's own answer names both, which the old code stored.
    assert diagnostics["candidates"][:2] == ["alpha", "beta"]
    assert stored.workers == seen == ["alpha"]
    assert stored.metadata["routed_workers"] == ["alpha"]
    assert diagnostics["scores"]["alpha"]["semantic_evidence"] > 0.045


async def test_a_fan_out_to_auto_stores_every_worker_it_ran() -> None:
    engine = _engine(("alpha", ["alpha work"]), ("beta", ["beta work"]), ("gamma", ["gamma"]), ("delta", ["delta"]))
    _use(engine, _Embedder())
    seen: list[str] = []
    _ran(engine, seen)
    result = await engine.dispatch(SwarmTask(prompt="alpha beta task", workers=["auto"], timeout=0,
                                             type=TaskType.FAN_OUT))
    assert result.status == "success"
    assert sorted(engine.get_task(result.task_id).workers) == sorted(seen) == ["alpha", "beta"]


# ── No embedder, a failing, slow or busy one: keywords, said once ──────────


class _Broken:
    dim = 4

    def encode(self, text):
        return []


async def test_without_an_embedder_routing_is_by_keywords_and_said_once(caplog) -> None:
    from kazma_core import metrics

    engine = _engine(("coder", ["python"]), ("writer", ["essay"]))
    _use(engine, _Broken())
    seen: list[str] = []
    _ran(engine, seen)
    counter = metrics.SWARM_FAILURES_TOTAL
    before = counter.labels(site="semantic_routing")._value.get() if counter is not None else 0.0
    caplog.set_level(logging.INFO, logger="kazma_core.swarm.semantic_router")
    for _ in range(3):
        result = await engine.dispatch(SwarmTask(prompt="fix this python bug", workers=["auto"], timeout=0))
        assert result.status == "success"
        diagnostics = engine.get_task(result.task_id).metadata["routing_diagnostics"]
        assert diagnostics["strategy_used"] == "keyword"
        assert diagnostics["semantic"] == {"available": False, "reason": "embed_failed",
                                           "detail": "the embedder returned no vector"}
    assert seen == ["coder", "coder", "coder"]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "unavailable" in r.getMessage()]
    assert len(warnings) == 1, [r.getMessage() for r in caplog.records]
    if counter is not None:
        assert counter.labels(site="semantic_routing")._value.get() == before + 1
    # Back: one INFO line.
    _use(engine, _Embedder({"python": [1.0, 0.0, 0.0, 0.0]}))
    await engine.dispatch(SwarmTask(prompt="fix this python bug", workers=["auto"], timeout=0))
    assert [r for r in caplog.records if r.levelno == logging.INFO and "is back" in r.getMessage()]


async def test_the_said_once_check_sees_a_silent_fallback(monkeypatch, caplog) -> None:
    """Negative control: with the report switched off nothing is said."""
    import kazma_core.swarm.semantic_router as sr

    monkeypatch.setattr(sr, "note_outcome", lambda outcome: None)
    engine = _engine(("coder", ["python"]))
    _use(engine, _Broken())
    _ran(engine, [])
    caplog.set_level(logging.INFO, logger="kazma_core.swarm.semantic_router")
    await engine.dispatch(SwarmTask(prompt="fix this python bug", workers=["auto"], timeout=0))
    assert not [r for r in caplog.records if "unavailable" in r.getMessage()]


async def test_a_slow_embedder_routes_by_keywords_within_the_deadline(monkeypatch) -> None:
    import kazma_core.routing_engine as routing_engine

    release = threading.Event()

    class _Slow(_Embedder):
        def encode(self, text):
            release.wait(10)
            return super().encode(text)

    monkeypatch.setattr(routing_engine, "SEMANTIC_DEADLINE_S", 0.2)
    engine = _engine(("coder", ["python"]))
    _use(engine, _Slow())
    seen: list[str] = []
    _ran(engine, seen)
    try:
        result = await asyncio.wait_for(
            engine.dispatch(SwarmTask(prompt="fix this python bug", workers=["auto"], timeout=0)), 5)
        diagnostics = engine.get_task(result.task_id).metadata["routing_diagnostics"]
        assert diagnostics["semantic"]["reason"] == "timeout" and seen == ["coder"]
    finally:
        release.set()


def test_a_busy_embedder_is_not_queued_behind() -> None:
    """Two embeddings hang: a third route gets its answer at once (keywords),
    not a place in a queue of hung calls."""
    from kazma_core.swarm.semantic_router import SemanticRouter

    release, entered = threading.Event(), threading.Semaphore(0)

    class _Hung(_Embedder):
        def encode(self, text):
            entered.release()
            release.wait(10)
            return super().encode(text)

    router = SemanticRouter(embedder_getter=lambda: _Hung())
    profiles = [{"name": "coder", "expertise": ["python"]}]
    hung = [threading.Thread(target=router.semantic_scores, args=("x", profiles)) for _ in range(2)]
    try:
        for t in hung:
            t.start()
        assert entered.acquire(timeout=5) and entered.acquire(timeout=5)
        assert router.semantic_scores("x", profiles).reason == "busy"
    finally:
        release.set()
        for t in hung:
            t.join(5)


async def test_routing_without_a_match_says_why_and_reaches_the_autoscaler(tmp_path) -> None:
    """No profile stands out and no keyword matches: the diagnostics say so
    before the refusal, and the autoscaler takes the task."""
    from kazma_core.swarm.autoscaler import AutoScaler, WorkerTemplate

    engine = _engine(("coder", ["python"]), ("writer", ["essay"]), ("analyst", ["sql"]))
    _use(engine, _Embedder({"python": [1.0, 0.0, 0.0, 0.0], "essay": [0.0, 1.0, 0.0, 0.0],
                            "sql": [0.0, 0.0, 1.0, 0.0]}))
    scaler = AutoScaler(engine, templates_path=tmp_path / "t.json")
    scaler.register_template(WorkerTemplate(name="gen", catch_all=True))
    engine._autoscaler = scaler
    seen: list[str] = []

    from kazma_core.swarm.worker import InProcessWorker

    async def by_instance(self, task, context=None):
        seen.append(self.name)
        return {"worker": self.name, "task_id": "", "status": "success", "output": "ok", "error": None}

    import unittest.mock as mock

    with mock.patch.object(InProcessWorker, "dispatch", by_instance):
        result = await engine.dispatch(SwarmTask(prompt="what is the weather tomorrow", workers=["auto"],
                                                 timeout=0))
    stored = engine.get_task(result.task_id)
    assert result.status == "success" and seen == stored.workers and seen[0].startswith("gen-pool-")
    diagnostics = stored.metadata["routing_diagnostics"]
    assert diagnostics["candidates"] == [] and diagnostics["semantic"]["matches"] == []
    assert diagnostics["autoscaled"] == {"template": "gen", "worker": seen[0], "reused": False}
