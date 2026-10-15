"""The Knowledge Library guide says what the code does (Knowledge audit
2026-10-13, KB-S14).

The guide said pages are pruned (the code never pruned), the library is
"completely isolated from chat memory" (a switch copied hits into memory),
jobs live in ``kb_jobs`` (the crawl journal holds them), the fetch order
starts with Jina (acquisition is local only by default), the tool lives in
``tool_registry.py``, the TUI has a library panel, three sites inject, and
archived libraries stay searchable everywhere. Each row below pairs the
guide's claim with the code fact it stands on; the old text is each row's
negative control.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GUIDE = REPO / "docs/docs/guide/knowledge-library.md"


def _src(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _product_files() -> list[str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard", "kazma-*/**.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return [rel for rel in listed if "/tests/" not in rel and (REPO / rel).exists()]


def _never_pruned() -> bool:
    from kazma_core.stores import knowledge_ingest

    progress = {f.name for f in __import__("dataclasses").fields(knowledge_ingest.ProgressUpdate)}
    return "pruned_urls" not in progress and "_prune_gone_urls" not in _src(
        "kazma-core/kazma_core/stores/knowledge_ingest.py")


def _never_in_memory() -> bool:
    return not any("promote_kb_hits_to_episodes" in _src(rel) for rel in _product_files())


def _journal_holds_jobs() -> bool:
    return (not (REPO / "kazma-core/kazma_core/stores/kb_jobs.py").exists()
            and "def start(" in _src("kazma-core/kazma_core/web_acquire/crawl_jobs.py"))


def _local_only_default() -> bool:
    from kazma_core.web_acquire.policy import AcquisitionPolicy

    return AcquisitionPolicy().mode == "local_only"


def _tool_home() -> bool:
    tree = ast.parse(_src("kazma-core/kazma_core/agent/tool_builtins/knowledge.py"))
    return any(isinstance(n, ast.AsyncFunctionDef) and n.name == "knowledge_search" for n in ast.walk(tree))


def _no_tui_panel() -> bool:
    tui = [rel for rel in _product_files() if rel.startswith("kazma-tui/")]
    return not any("/api/kb" in _src(rel) or "knowledge_search" in _src(rel) for rel in tui)


def _one_injection_site() -> bool:
    return all("get_knowledge_auto_inject_block" not in _src(rel) for rel in (
        "kazma-ui/kazma_ui/sse_chat/__init__.py", "kazma-ui/kazma_ui/voice_turn.py",
        "kazma-gateway/kazma_gateway/agent_handler/graph.py", "kazma-core/kazma_core/agent_runner.py")) \
        and "_knowledge_inject_for_turn" in _src("kazma-core/kazma_core/agent/graph_supervisor.py")


def _archive_hides_from_all_library_search() -> bool:
    from kazma_core.stores.knowledge import KnowledgeStore

    default = inspect.signature(KnowledgeStore.list_libraries).parameters["include_archived"].default
    return default is False


def _route_count() -> bool:
    from kazma_ui.kb_api import create_kb_router

    pairs = [(m, r.path) for r in create_kb_router().routes for m in r.methods]
    return len(pairs) == 16 and "16 routes" in _src("kazma-ui/kazma_ui/kb_api.py")


def _kb_admin_only() -> bool:
    from kazma_gateway.slash_commands import changes_global_config

    return all(changes_global_config(f"/kb {s} docs https://e.test/") for s in ("add", "crawl", "refresh", "delete"))


def _searches_are_judged() -> bool:
    """KQ01: every library search goes through the relevance rule."""
    from kazma_core.stores import knowledge_index

    return knowledge_index._EVIDENCE_FLOOR > 0 and "_judge(" in inspect.getsource(
        knowledge_index.KnowledgeIndex._search_libraries)


def _benchmark_report_version() -> bool:
    """The report the guide gives is of the dataset the ratchet measures."""
    import json

    from kazma_core.stores.knowledge_benchmark import load_dataset

    locked = json.loads((REPO / "tests/fixtures/knowledge_bench/thresholds.json").read_text(encoding="utf-8"))
    return locked["dataset_version"] == load_dataset()["version"] == 1


def _private_seed_refused_at_admission() -> bool:
    from kazma_core.stores import knowledge_ingest

    return "_admit_public_seed(" in inspect.getsource(knowledge_ingest._prepare_ingest_site)
def _dropped_folders_removed() -> bool:
    source = _src("kazma-core/kazma_core/memory/vector_store_global.py")
    return ("remove_orphan_segment_folders" in source and "def release_dropped_vectors" in source
            and "release_dropped_vectors(" in _src("kazma-core/kazma_core/stores/knowledge_index.py"))


def _coverage_names_its_store() -> bool:
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    return "_measured_here" in inspect.getsource(KnowledgeIndex.health)


def _hnsw_settings() -> bool:
    from kazma_core.memory.vector_store_global import _HNSW

    return (_HNSW["max_neighbors"], _HNSW["ef_construction"], _HNSW["ef_search"]) == (48, 400, 256)


def _breadth_steps_text() -> str:
    """The guide's words for the search-breadth table, built from the table
    itself: a step changed without the guide fails the row."""
    from kazma_core.memory.vector_store_global import _BREADTH_STEPS, _HNSW

    steps = [f"{breadth:,} from {size:,}" for size, breadth in _BREADTH_STEPS]
    return f"a search breadth of {_HNSW['ef_search']:,} below {_BREADTH_STEPS[0][0]:,} vectors, " + ", ".join(steps)


def _breadth_follows_the_size() -> bool:
    from kazma_core.memory import vector_store_global as vsg
    from kazma_core.stores import knowledge_index

    return (vsg.search_breadth(0) == vsg._HNSW["ef_search"] < vsg.search_breadth(10**7)
            and "self._fit_breadth()" in inspect.getsource(vsg.VectorStore._open)
            and "chroma_local.collection_size(" in inspect.getsource(vsg.VectorStore._size)
            and "fit_search_breadth()" in inspect.getsource(knowledge_index.KnowledgeIndex.backfill_vectors)
            and "reload_vector_indexes(" in inspect.getsource(knowledge_index.run_repair_sweep))


def _delete_purges_words() -> bool:
    from kazma_core.stores.knowledge import KnowledgeStore

    return "self.purge_search_index()" in inspect.getsource(KnowledgeStore.delete_library)


def _purge_checks_its_shape() -> bool:
    from kazma_core.memory import chroma_local, forget
    from kazma_core.stores.knowledge import KnowledgeStore

    return ("drop_deleted_rows(" in inspect.getsource(KnowledgeStore.purge_search_index)
            and "purge_deleted_rows(" in inspect.getsource(forget.purge_search_index)
            and "purge_deleted_rows(" in inspect.getsource(chroma_local.compact_catalog))


def _purge_outlives_the_process() -> bool:
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_index import run_repair_sweep

    return ("_mark_purge_due(" in inspect.getsource(KnowledgeStore.delete_library)
            and "finish_pending_purge(" in inspect.getsource(KnowledgeStore.__init__)
            and "finish_pending_purge(" in inspect.getsource(run_repair_sweep))


def _no_text_beside_vectors() -> bool:
    from kazma_core.memory.vector_store_global import KEPT_METADATA, VectorStore
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    meta = KnowledgeIndex._chunk_vector_metadata("lib", {"document_title": "t", "section_header": "h",
                                                         "source_url": "https://u", "document_id": "d"}, "m")
    return set(meta) <= KEPT_METADATA and all(
        "documents=" not in inspect.getsource(f)
        for f in (VectorStore.index, VectorStore.index_many, VectorStore.rebuild_collection))


def _deleted_text_compacted() -> bool:
    from kazma_core.memory import chroma_local
    from kazma_core.stores.knowledge_index import run_repair_sweep

    return ("compact_vectors(" in inspect.getsource(run_repair_sweep)
            and "VACUUM" in inspect.getsource(chroma_local.compact_catalog))


def _library_ceiling() -> bool:
    from kazma_core.memory import chroma_local
    from kazma_core.stores.knowledge import KnowledgeStore

    return (chroma_local.LIBRARY_CEILING == 500
            and "KnowledgeLibraryLimit" in inspect.getsource(KnowledgeStore.create_library)
            and (512 - chroma_local._FILE_RESERVE) // chroma_local.FILES_PER_INDEX
            - chroma_local.SPARE_COLLECTIONS == 92)


def _no_eviction() -> bool:
    from kazma_core.memory import chroma_local
    from kazma_core.memory import vector_store_global as vsg

    per_shard = chroma_local.index_pool_size() // chroma_local.POOL_SHARDS
    return ("start_system(" in inspect.getsource(vsg._shared_client)
            and per_shard >= chroma_local.library_ceiling() + chroma_local.SPARE_COLLECTIONS)


def _filter_only_when_mixed() -> bool:
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    return "only_model(" in inspect.getsource(KnowledgeIndex._model_filter)


@dataclass(frozen=True)
class Claim:
    topic: str
    stale: str | None  # what the guide said, contradicting the code (None: it said nothing)
    current: str  # what it must say
    fact: Callable[[], bool]


CLAIMS = [
    Claim("pruning", "Pruned on full-site crawl/refresh",
          "A sparse, capped or failed discovery never deletes indexed pages", _never_pruned),
    Claim("isolation from chat memory", "completely isolated from chat memory",
          "it is never written into chat memory", _never_in_memory),
    Claim("job durability", "Durable (ConfigStore `kb_jobs`, same as crawl)",
          "the one source of crawl state", _journal_holds_jobs),
    Claim("fetch order", "Jina → Firecrawl → httpx+trafilatura → Playwright",
          "By default acquisition is **local only**", _local_only_default),
    Claim("tool location", "`kazma-core/kazma_core/agent/tool_registry.py` `knowledge_search`",
          "`kazma-core/kazma_core/agent/tool_builtins/knowledge.py`", _tool_home),
    Claim("TUI", "Same library works from Web, Telegram, Discord, Slack, TUI",
          "There is no Knowledge panel in the TUI.", _no_tui_panel),
    Claim("injection sites", "Three injection points",
          "The supervisor is the one place that builds this block", _one_injection_site),
    Claim("archived-library search", "Archived libraries stay **searchable** — their chunks remain in the index.",
          "An archived library leaves the Active list, auto-inject and every search across libraries",
          _archive_hides_from_all_library_search),
    Claim("/kb commands", "| `/kb add <id> <url>` | Create-or-use library, ingest ONE page (sync) |",
          "Commands that change a library are for the gateway's admins only.", _kb_admin_only),
    Claim("kb_api routes", None,
          "`/api/kb/*` (`kazma-ui/kazma_ui/kb_api.py`) has 16 routes.", _route_count),
    Claim("relevance", "and retrieval quality on a versioned EN/AR benchmark, are open work",
          "A search shows only what is about the question.", _searches_are_judged),
    Claim("benchmark report", None, "### Benchmark report (dataset v1, 2026-10-15)", _benchmark_report_version),
    Claim("private crawl seed", None,
          "A crawl seed on a private network is refused before anything is created",
          _private_seed_refused_at_admission),
    # KB-Q01 (2026-10-15): what the soak harness found and the store now does.
    Claim("dropped vectors", None, "**A dropped collection's vectors leave the disk.**", _dropped_folders_removed),
    Claim("coverage after a restore", None,
          "never the coverage of vectors it lost", _coverage_names_its_store),
    Claim("HNSW settings", None,
          "48 neighbours a vector, a construction breadth of 400 and a search breadth of 256", _hnsw_settings),
    Claim("deleted words", None,
          "which leaves none of the library's words in the file", _delete_purges_words),
    # KB-Q01's follow-up (2026-10-15): the vector catalog's text, the pool, the filter.
    Claim("vector text", None, "**No text is stored beside a vector.**", _no_text_beside_vectors),
    Claim("deleted vector text", None, "**Deleted text leaves the vector catalog.**", _deleted_text_compacted),
    Claim("library ceiling", None, "An install holds at most 500 libraries", _library_ceiling),
    Claim("index pool", "Kazma raises the limits Chroma sizes the pool from before the first client starts",
          "**No index is evicted.**", _no_eviction),
    Claim("search filter", None, "**A search asks no metadata filter when it needs none.**", _filter_only_when_mixed),
    # KB-Q01's multi-day soak (2026-10-15): the purge checked, and finished after a kill.
    Claim("purge checked", None, "**rebuilds the index from its own rows**", _purge_checks_its_shape),
    Claim("purge after a kill", None,
          "**a process stopped before the purge ends leaves it to the next open of the store or the next "
          "maintenance pass**", _purge_outlives_the_process),
    # KB-Q01's follow-up (2026-10-15): the search breadth follows the library's size.
    Claim("search breadth", "which is not set yet", "**The search widens as a library grows.**",
          _breadth_follows_the_size),
    Claim("search breadth table", None, _breadth_steps_text(), _breadth_follows_the_size),
]


def claim_problems(guide: str) -> list[str]:
    problems = []
    for claim in CLAIMS:
        if claim.stale and claim.stale in guide:
            problems.append(f"{claim.topic}: the guide still says {claim.stale!r}")
        if claim.current not in guide:
            problems.append(f"{claim.topic}: the guide does not say {claim.current!r}")
    return problems


def test_the_guide_says_what_the_code_does():
    assert claim_problems(GUIDE.read_text(encoding="utf-8")) == []


def test_every_claim_stands_on_its_code_fact():
    assert [c.topic for c in CLAIMS if not c.fact()] == []


def test_the_old_guide_fails_every_row_it_held():
    """Negative control: the guide before the rewrite."""
    old = subprocess.run(["git", "show", "c575d4e4:docs/docs/guide/knowledge-library.md"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode != 0:  # a shallow clone without that commit
        import pytest

        pytest.skip("the pre-rewrite guide is not in this clone")
    problems = claim_problems(old.stdout)
    for topic in ("pruning", "isolation from chat memory", "job durability", "fetch order", "tool location",
                  "injection sites", "archived-library search", "/kb commands"):
        assert any(p.startswith(topic + ":") for p in problems), topic


def test_the_guide_before_kq01_fails_the_new_rows():
    """Negative control: the guide as merged on 2026-10-14 (718389b5), when
    retrieval quality was not measured and a private seed was admitted."""
    old = subprocess.run(["git", "show", "718389b5:docs/docs/guide/knowledge-library.md"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode != 0:  # a shallow clone without that commit
        import pytest

        pytest.skip("the 2026-10-14 guide is not in this clone")
    problems = claim_problems(old.stdout)
    for topic in ("relevance", "benchmark report", "private crawl seed"):
        assert any(p.startswith(topic + ":") for p in problems), topic


def test_the_guide_before_the_kb_q01_follow_up_fails_its_rows():
    """Negative control: the guide as committed in 01cba7cb, when every
    vector kept a preview of its chunk and the index pool was Chroma's."""
    old = subprocess.run(["git", "show", "01cba7cb:docs/docs/guide/knowledge-library.md"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode != 0:  # a shallow clone without that commit
        import pytest

        pytest.skip("the guide of 01cba7cb is not in this clone")
    problems = claim_problems(old.stdout)
    for topic in ("vector text", "deleted vector text", "library ceiling", "index pool", "search filter"):
        assert any(p.startswith(topic + ":") for p in problems), topic
    assert any(p.startswith("index pool: the guide still says") for p in problems)


def test_the_guide_before_the_multi_day_soak_fails_its_rows():
    """Negative control: the guide as committed in ff282522, when a merge
    was taken for proof and a killed delete's purge was never finished."""
    old = subprocess.run(["git", "show", "ff282522:docs/docs/guide/knowledge-library.md"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode != 0:  # a shallow clone without that commit
        import pytest

        pytest.skip("the guide of ff282522 is not in this clone")
    problems = claim_problems(old.stdout)
    for topic in ("purge checked", "purge after a kill"):
        assert any(p.startswith(topic + ":") for p in problems), topic


def test_the_guide_before_the_growing_search_breadth_fails_its_rows():
    """Negative control: the guide as committed in 0ad31815, when the search
    breadth was 256 at every size and the guide said a wider one was not set."""
    old = subprocess.run(["git", "show", "0ad31815:docs/docs/guide/knowledge-library.md"], cwd=REPO,
                         capture_output=True, text=True, encoding="utf-8")
    if old.returncode != 0:  # a shallow clone without that commit
        import pytest

        pytest.skip("the guide of 0ad31815 is not in this clone")
    problems = claim_problems(old.stdout)
    for topic in ("search breadth", "search breadth table"):
        assert any(p.startswith(topic + ":") for p in problems), topic
    assert "search breadth: the guide still says 'which is not set yet'" in problems


def test_a_changed_breadth_step_fails_the_table_row(monkeypatch):
    """Negative control without git: the code's table moved, the guide not."""
    from kazma_core.memory import vector_store_global as vsg

    guide = GUIDE.read_text(encoding="utf-8")
    monkeypatch.setattr(vsg, "_BREADTH_STEPS", ((vsg._BREADTH_STEPS[0][0] + 1, vsg._BREADTH_STEPS[0][1]),
                                                *vsg._BREADTH_STEPS[1:]))
    assert _breadth_steps_text() not in guide


def test_a_stale_claim_is_caught():
    """Negative control without git: one stale line planted in the guide."""
    guide = GUIDE.read_text(encoding="utf-8") + "\n| URL gone | Pruned on full-site crawl/refresh |\n"
    assert claim_problems(guide) == ["pruning: the guide still says 'Pruned on full-site crawl/refresh'"]
