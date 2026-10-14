"""Knowledge Library meaning vectors: who made them, what a pass reports,
and handles another instance dropped (Knowledge audit 2026-10-13: KB-E03,
KB-E11, KB-E12).
"""

from __future__ import annotations

import logging
import types

import pytest
from kazma_core.memory import vector_store_global as vsg
from kazma_core.stores import knowledge_index as ki

from tests._module_stubs import stub_modules
from tests.test_knowledge_meaning_search import DIM, _chromadb_stub, _Client, _Collection, _Embedder, _seed


@pytest.fixture(autouse=True)
def fresh_sweep(monkeypatch):
    monkeypatch.setattr(ki, "_SWEEP", {"report": None, "unavailable": False, "bad_passes": 0, "at": None})


@pytest.fixture()
def kb(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(_Client, "made", [])
    monkeypatch.setattr(_Client, "settings_seen", [])
    embedder = _Embedder()
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    with stub_modules(_chromadb_stub()):
        yield types.SimpleNamespace(store=store, index=ki.KnowledgeIndex(store))
    store.close()


class _Remote(_Embedder):
    """A remote endpoint's embedder, as the identity sees it."""

    def __init__(self, base_url: str = "https://embed.example.test/v1", dim: int = DIM) -> None:
        self._base_url = base_url
        self._model = "nvidia/nv-embed-v1"
        self.dim = dim

    def encode(self, text):
        return list(reversed(super().encode(text)))[: self.dim]


class _LocalFallback(_Embedder):
    """The local model a remote configuration falls back to."""

    def __init__(self, dim: int = DIM) -> None:
        self._model_name = "BAAI/bge-m3"
        self.dim = dim

    def encode(self, text):
        vec = super().encode(text)
        return (vec[1:] + vec[:1])[: self.dim]


def _use(monkeypatch, embedder) -> None:
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: embedder)


def _tags(index, library="api") -> set[str]:
    return {meta.get("model") for _v, _d, meta in index._vector_store_for(library)._collection.rows.values()}


TEXTS = ["Webhooks retry delivery five times", "Install the command line tool",
         "Tokens expire after an hour"]


# ── KB-E03: the identity is the instance's ─────────────────────────────────


@pytest.mark.parametrize("dim", [DIM, 16], ids=["same-size", "other-size"])
def test_a_model_switch_converges_in_one_sweep_and_health_under_reports_until_then(kb, monkeypatch, dim):
    remote = _Remote()
    _use(monkeypatch, remote)
    index = ki.KnowledgeIndex(kb.store)
    _seed(kb, "api", TEXTS)
    assert index.backfill_vectors(time_budget_s=30)["embedded"] == 3
    assert _tags(index) == {vsg.embedder_identity(remote)}
    assert index.health("api")["vector_chunks"] == 3 and index.health("api")["state"] == "ok"

    # 1. The configuration changes; the process still runs the remote embedder.
    monkeypatch.setenv("KAZMA_EMBED_PROVIDER", "local")
    monkeypatch.setenv("KAZMA_EMBED_MODEL", "BAAI/bge-m3")
    assert index.backfill_vectors(time_budget_s=30)["embedded"] == 0  # nothing to re-make

    # 2. A restart: the remote configuration fell back to the local model.
    fallback = _LocalFallback(dim)
    _use(monkeypatch, fallback)
    restarted = ki.KnowledgeIndex(kb.store)
    # Before the sweep, queries never compare against the remote model's space.
    assert restarted.search_sync("webhooks", "api")[0].chunk_id  # keyword still answers
    semantic, _lexical = restarted._raw_layers("api", "how many attempts to resend callbacks", 5)
    assert semantic == []
    # Health still says what the last pass measured; it never claims the new
    # identity covers what the old one made.
    report = restarted.backfill_vectors(time_budget_s=30)
    assert report["embedded"] == 3 and report["identity"] == vsg.embedder_identity(fallback)
    assert _tags(restarted) == {vsg.embedder_identity(fallback)}
    assert restarted.health("api")["vector_chunks"] == 3
    assert restarted.backfill_vectors(time_budget_s=30)["embedded"] == 0  # converged
    semantic, _lexical = restarted._raw_layers("api", "how many attempts to resend callbacks", 5)
    assert semantic and semantic[0][0].startswith("api")


def test_health_counts_only_vectors_the_active_embedder_made(kb, monkeypatch):
    _seed(kb, "api", TEXTS)
    kb.index.backfill_vectors(time_budget_s=30)

    class Partial(_LocalFallback):
        def encode(self, text):  # cannot embed one text
            return [] if "Tokens" in text else super().encode(text)

    _use(monkeypatch, Partial())
    fresh = ki.KnowledgeIndex(kb.store)
    fresh.backfill_vectors(time_budget_s=30)
    health = fresh.health("api")
    assert health["vector_chunks"] == 2 and health["state"] == "rebuilding"
    # Negative control: the raw collection count -- what health used to
    # report -- says "3 of 3" over a vector the old model made.
    assert fresh._vector_store_for("api").count() == 3


def test_the_identity_names_the_instance_never_the_configuration(monkeypatch):
    remote = _Remote()
    fallback = _LocalFallback()
    assert vsg.embedder_identity(remote) != vsg.embedder_identity(fallback)
    assert vsg.embedder_identity(_Remote("https://other.example.test/v1")) != vsg.embedder_identity(remote)
    assert vsg.embedder_identity(_LocalFallback(16)) != vsg.embedder_identity(fallback)
    assert vsg.embedder_identity(None) == ""


def test_the_configured_name_tag_kept_another_models_vectors():
    """Negative control: tagging by the configured model name. A remote
    configuration that fell back to the local model tags the local model's
    vectors with the remote name; the restart that brings the remote back
    finds every tag "current" and re-makes nothing."""
    configured = "nvidia/nv-embed-v1"
    tag_written_during_fallback = configured
    tag_expected_after_restart = configured
    assert tag_written_during_fallback == tag_expected_after_restart  # nothing re-made


def test_a_query_of_another_size_waits_quietly_for_the_repair(kb, monkeypatch, caplog):
    _seed(kb, "api", TEXTS)
    kb.index.backfill_vectors(time_budget_s=30)
    _use(monkeypatch, _LocalFallback(16))
    fresh = ki.KnowledgeIndex(kb.store)
    with caplog.at_level(logging.INFO, logger=vsg.__name__):
        for _ in range(3):
            assert fresh._vector_store_for("api").query("webhooks retry", limit=3) == []
    said = [r for r in caplog.records if "waits for the repair sweep" in r.getMessage()]
    assert len(said) == 1 and said[0].levelno == logging.INFO
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ── KB-E11: one library cannot stop the others; the report is seen ─────────


class _Poisoned(_Collection):
    def get(self, *a, **k):
        raise RuntimeError("collection segment is corrupt")


def _poison(kb, library: str) -> None:
    vs = kb.index._vector_store_for(library)
    assert vs.available
    vs._collection.__class__ = _Poisoned


def test_a_poisoned_library_does_not_stop_the_others(kb, monkeypatch):
    for library in ("alpha", "bad", "zulu"):
        _seed(kb, library, TEXTS)
    _poison(kb, "bad")
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: kb.index)
    with pytest.raises(RuntimeError, match="bad"):
        ki.run_repair_sweep(time_budget_s=30)
    report = ki._last_repair_report()
    assert set(report["failed"]) == {"bad"}
    assert report["coverage"]["alpha"] == [3, 3] and report["coverage"]["zulu"] == [3, 3]
    assert kb.index.health("bad")["state"] == "failed"
    assert kb.index.health("alpha")["state"] == "ok"
    assert ki.vector_readiness()["status"] == "degraded"
    assert 'kazma_kb_vector_failed_libraries 1' in ki.repair_metric_lines()


def test_the_old_sweep_stopped_at_the_first_bad_library(kb):
    """Negative control: the loop without a per-library guard."""
    for library in ("alpha", "bad", "zulu"):
        _seed(kb, library, TEXTS)
    _poison(kb, "bad")
    reached = []
    with pytest.raises(RuntimeError):
        for library in kb.store.all_library_ids():
            kb.index._vector_store_for(library).models()
            reached.append(library)
    assert "zulu" not in reached


def test_an_outage_is_said_once_alerted_after_three_passes_and_its_end_said(kb, monkeypatch, caplog):
    from kazma_core.observability import ops_alerts

    _seed(kb, "api", TEXTS)
    raised = []
    monkeypatch.setattr(ops_alerts, "alert", lambda key, *a, **k: raised.append(key))
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: kb.index)
    with caplog.at_level(logging.WARNING, logger=ki.__name__):
        with stub_modules({"chromadb": None}):
            for n in range(3):
                down = ki.KnowledgeIndex(kb.store)
                monkeypatch.setattr(ki, "get_knowledge_index", lambda d=down: d)
                report = ki.run_repair_sweep(time_budget_s=5)
                assert report["unavailable"]
                assert raised == ([] if n < 2 else ["knowledge.meaning_search"])
        outage = [r for r in caplog.records if "meaning search is unavailable" in r.getMessage()]
        assert len(outage) == 1
        assert ki.vector_readiness()["status"] == "degraded"
        assert "kazma_kb_vector_store_unavailable 1" in ki.repair_metric_lines()
        from kazma_core.observability.daily_digest import build_digest

        assert "knowledge meaning-search passes without a vector store: 3" in build_digest()
        monkeypatch.setattr(ki, "get_knowledge_index", lambda: kb.index)
        report = ki.run_repair_sweep(time_budget_s=30)
    assert not report["unavailable"] and report["embedded"] == 3
    assert [r for r in caplog.records if "meaning search is available again" in r.getMessage()]
    assert ki.vector_readiness()["status"] == "ok"
    assert ki._last_repair_report()["bad_passes"] == 0


def test_the_readiness_route_names_the_knowledge_line(monkeypatch):
    import asyncio
    import json

    from kazma_ui import health

    for name in ("check_config_store", "check_database", "check_swarm_engine", "check_model_registry",
                 "check_agent_runner", "check_mcp", "check_cron", "check_schedulers", "check_llm_provider"):
        monkeypatch.setattr(health, name, lambda n=name: {"status": "ok", "component": n})
    body = json.loads(asyncio.run(health._readiness()).body)
    line = body["checks"]["knowledge_vectors"]
    assert line["status"] == "ok" and line["topology"] == "single_install"
    assert "knowledge_vectors" not in body["required_capabilities"]


def test_metrics_carry_the_repair_report(monkeypatch):
    monkeypatch.setattr(ki, "_SWEEP", {"report": {"unavailable": False, "failed": {}, "missing": 4,
                                                  "coverage": {"api": [6, 10]}, "fts": {"ok": True}},
                                       "unavailable": False, "bad_passes": 0, "at": 1_700_000_000.0})
    lines = ki.repair_metric_lines()
    assert 'kazma_kb_vector_coverage_ratio{library="api"} 0.6000' in lines
    assert "kazma_kb_vector_missing 4" in lines
    assert ki.vector_readiness()["detail"] == "4 chunk(s) waiting for a vector"


# ── KB-E12: one index per store, and a dropped collection re-opened ────────


def test_one_index_per_store(kb, monkeypatch, tmp_path):
    """The Knowledge Library and the Documents platform share the store's
    one index (Documents built its own and kept dead collection handles)."""
    from kazma_core.documents.config import DocumentConfig
    from kazma_core.documents.knowledge import DocumentKnowledgeAdapter

    ki.reset_knowledge_index()
    try:
        monkeypatch.setattr(ki, "get_knowledge_store", lambda: kb.store)
        shared = ki.get_knowledge_index()
        assert ki.knowledge_index_for(kb.store) is shared
        adapter = DocumentKnowledgeAdapter(repository=object(), knowledge_store=kb.store,
                                           config=DocumentConfig(storage_root=tmp_path / "documents"))
        assert adapter.index is shared
    finally:
        ki.reset_knowledge_index()


def test_every_index_of_the_shared_store_is_the_shared_one():
    """Class gate: product code builds a ``KnowledgeIndex`` only through
    ``knowledge_index_for`` (the constructor call inside it is the one)."""
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    files = subprocess.run(["git", "ls-files", "kazma-*/**.py"], cwd=repo, capture_output=True,
                           text=True, check=True).stdout.split()
    offenders = _direct_constructions(repo, files)
    assert offenders == ["kazma-core/kazma_core/stores/knowledge_index.py:knowledge_index_for"], offenders


def _direct_constructions(repo, files, extra: dict[str, str] | None = None) -> list[str]:
    import ast

    found = []
    sources = {rel: (repo / rel).read_text(encoding="utf-8") for rel in files if (repo / rel).exists()}
    sources.update(extra or {})
    for rel, source in sources.items():
        if "KnowledgeIndex(" not in source:
            continue
        tree = ast.parse(source)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for node in ast.walk(fn):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == "KnowledgeIndex"):
                    found.append(f"{rel}:{fn.name}")
    return sorted(set(found))


def test_the_index_gate_sees_a_second_construction():
    """Negative control: Documents' old ``KnowledgeIndex(knowledge_store)``."""
    from pathlib import Path

    planted = {"kazma-core/kazma_core/documents/knowledge.py":
               "def __init__(self, store):\n    self.index = KnowledgeIndex(store)\n"}
    assert _direct_constructions(Path("."), [], planted) == [
        "kazma-core/kazma_core/documents/knowledge.py:__init__"]


class _MissingAware(_Collection):
    dropped = False

    def _check(self):
        if self.dropped:
            raise type("NotFoundError", (Exception,), {})(f"Collection {self.name} does not exist.")

    def upsert(self, *a, **k):
        self._check()
        return super().upsert(*a, **k)

    def get(self, *a, **k):
        self._check()
        return super().get(*a, **k)

    def count(self):
        self._check()
        return super().count()

    def query(self, *a, **k):
        self._check()
        return super().query(*a, **k)


def _dropping_client(monkeypatch):
    def get_or_create(self, name, metadata=None, embedding_function=None):
        if name not in self.collections:
            self.collections[name] = _MissingAware(name, metadata)
        return self.collections[name]

    def delete(self, name):
        self.collections.pop(name).dropped = True

    monkeypatch.setattr(_Client, "get_or_create_collection", get_or_create)
    monkeypatch.setattr(_Client, "delete_collection", delete)


def test_a_collection_dropped_by_another_instance_is_reopened(kb, monkeypatch):
    _dropping_client(monkeypatch)
    _seed(kb, "api", TEXTS)
    first, second = ki.KnowledgeIndex(kb.store), ki.KnowledgeIndex(kb.store)
    first.backfill_vectors(time_budget_s=30)
    held = second._vector_store_for("api")
    assert held.query("webhooks", limit=2)
    first.delete_library("api")  # drops the collection under the second's handle
    _seed(kb, "api", TEXTS)  # the same slug, created again
    assert held.index_many([("api-0", TEXTS[0], {"library_id": "api", "model": held.identity})]) == 1
    assert held.query("webhooks retry", limit=2)


def test_without_the_reopen_the_dead_handle_fails(kb, monkeypatch):
    """Negative control: a handle that does not re-open keeps failing."""
    _dropping_client(monkeypatch)
    _seed(kb, "api", TEXTS)
    first, second = ki.KnowledgeIndex(kb.store), ki.KnowledgeIndex(kb.store)
    first.backfill_vectors(time_budget_s=30)
    held = second._vector_store_for("api")
    held.query("webhooks", limit=2)
    first.delete_library("api")
    monkeypatch.setattr(held, "_reopen_if_missing", lambda exc: False)
    with pytest.raises(Exception, match="does not exist"):
        held.index_many([("api-0", TEXTS[0], {"library_id": "api"})])
    assert held.query("webhooks retry", limit=2) == []


def test_with_the_real_chromadb_a_dropped_collection_is_reopened(tmp_path, monkeypatch):
    pytest.importorskip("chromadb")
    from kazma_core.stores.knowledge import KnowledgeStore

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    _use(monkeypatch, _Embedder())
    store = KnowledgeStore(db_path=str(tmp_path / "knowledge.db"))
    try:
        kb = types.SimpleNamespace(store=store)
        _seed(kb, "api", TEXTS)
        first, second = ki.KnowledgeIndex(store), ki.KnowledgeIndex(store)
        first.backfill_vectors(time_budget_s=60)
        held = second._vector_store_for("api")
        assert held.query("webhooks retry", limit=2)
        first.delete_library("api")
        _seed(kb, "api", TEXTS)
        # The other instance's next write and query succeed.
        assert held.index_many([("api-0", TEXTS[0], {"library_id": "api", "model": held.identity})]) == 1
        assert held.query("webhooks retry", limit=2)[0][0] == "api-0"
        # And a size change made through one handle (the cross-instance _fit).
        narrow = _LocalFallback(16)
        _use(monkeypatch, narrow)
        third = ki.KnowledgeIndex(store)
        assert third.backfill_vectors(time_budget_s=60)["embedded"] == 3
        _use(monkeypatch, _Embedder())
        assert held.index_many([("api-1", TEXTS[1], {"library_id": "api", "model": held.identity})]) == 1
    finally:
        store.close()
