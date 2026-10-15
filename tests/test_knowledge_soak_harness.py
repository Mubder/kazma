"""The Knowledge soak harness works, and sees what it is there to see (KB-Q01).

``scripts/knowledge_soak.py`` is the harness for the Knowledge Library's
scale and recovery measurement on named hardware and for its multi-day mixed
workload; a session cannot hold those runs, so this keeps the harness honest.
A tiny ``scale`` run (EN, AR and mixed) and a short ``soak`` end healthy,
having done the work they report: ingest, searches, the idle sweep, a
backup, a restore and its re-embed, deletes that leave nothing behind,
crawls and refreshes through the crawl journal, document publications,
crashes and their resumes, a restart every cycle and a model switch with
re-embedding. They run strict (KB-Q01's fixes, 2026-10-15): no vector folder
left by a dropped collection, no deleted library's words in the keyword
index, no coverage record claiming vectors a restore lost, no collection a
process cannot read. Their reports are on disk while they run. And the checks
see planted violations: a chunk deleted behind the store's back, a keyword row
left for a retired chunk, a deleted library's fetched pages left on disk, its
words left in the keyword index, a vector folder no collection owns, an
over-full crawl journal, ``unified_items`` back, searches that miss, and a
collection that cannot be read in two processes in a row (negative controls).

The runs need ``chromadb`` (the ``rag`` extra, which CI does not install);
the negative controls run everywhere.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import knowledge_soak  # noqa: E402

pytestmark = pytest.mark.timeout(300)

#: Each status a reader of the report file saw, in order, during each run.
_WRITTEN: dict[str, list[str]] = {"scale": [], "soak": []}


def _run(mode: str, argv: list[str], tmp_path_factory) -> tuple[int, dict, Path]:
    pytest.importorskip("chromadb")
    data = tmp_path_factory.mktemp(f"kb-{mode}") / "data"
    report = tmp_path_factory.mktemp(f"kb-{mode}-report") / "report.json"
    real_write = knowledge_soak._write_report

    def record(path, payload):
        real_write(path, payload)
        # What a reader of the file sees at that moment, not what was passed.
        _WRITTEN[mode].append(json.loads(Path(path).read_text(encoding="utf-8"))["status"])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(knowledge_soak, "_write_report", record)
        # Strict (KB-Q01, 2026-10-15): a folder a dropped collection leaves
        # and a collection a process cannot read are violations, as they are
        # for an operator's run; until the fixes of that day they were
        # counted here with --tolerate-orphan-vector-folders and
        # --tolerate-vector-read-failures.
        code = knowledge_soak.main([mode, *argv, "--data-dir", str(data), "--report", str(report),
                                    "--hardware-name", "test"])
    return code, json.loads(report.read_text(encoding="utf-8")), data


@pytest.fixture(scope="module")
def scale_run(tmp_path_factory):
    return _run("scale", ["--chunks", "240", "--languages", "en,ar,mixed", "--questions", "6",
                          "--search-repeats", "2", "--page-chunks", "12", "--batch-pages", "5"], tmp_path_factory)


@pytest.fixture(scope="module")
def soak_run(tmp_path_factory):
    return _run("soak", ["--duration", "240", "--max-cycles", "6", "--cycle-seconds", "0", "--switch-every", "3",
                         "--crash-rate", "0.25", "--libraries", "3", "--create-rate", "1", "--delete-rate", "0.3",
                         "--pages", "5", "--questions", "8"], tmp_path_factory)


# ── the runs ───────────────────────────────────────────────────────────────


def test_a_tiny_scale_run_measures_every_step(scale_run):
    code, report, _data = scale_run
    assert code == 0 and report["healthy"] and report["status"] == "passed", report["violations"]
    hardware = report["hardware"]
    assert hardware["name"] == "test" and hardware["cpu"] and hardware["cores_logical"] and hardware["ram_gib"]
    assert hardware["os"] and hardware["python"] and hardware["sqlite"] and "disk" in hardware
    assert [(s["size"], s["language"]) for s in report["steps"]] == [(240, "en"), (240, "ar"), (240, "mixed")]
    for step in report["steps"]:
        assert step["ingest"]["chunks"] == 240 and step["ingest"]["chunks_per_s"] > 0, step["ingest"]
        assert step["ingest"]["capped"] is None
        search = step["search"]
        dropped = sum(not m["hybrid"] for m in search["meaning_index_misses"])
        assert search["found"]["hybrid"][0] + dropped == 6 and search["found"]["keyword"] == [6, 6], search
        assert all(search[f"{layer}_ms"]["p99"] is not None for layer in ("hybrid", "keyword", "meaning"))
        rows = step["sweep_idle"]["rows_listed"]
        assert rows["active_chunks"] == 240 and rows["vector_ids"] == 240, step["sweep_idle"]
        assert step["backup"]["bytes"] > 0
        restore = step["restore"]
        assert restore["keyword_before_sweep"]["found"] == [6, 6]
        assert sum(p["embedded"] for p in restore["sweep"]["passes"]) == 240, restore["sweep"]
        assert restore["vectors_after"]["missing"] == 0 and restore["reembed_rto_seconds"] > 0
        assert restore["search_after"]["found"]["keyword"] == [6, 6]
        delete = step["delete"]
        assert delete["result"]["deleted"] and delete["after"]["problems"] == [], delete
        assert delete["vector_folders"]["orphans"] == 0 and delete["after"]["keyword_segment_residue"][0] == 0
        assert restore["coverage_record_before_sweep"]["state"] == "unknown", restore
        assert step["ceilings"]["after_ingest"]["knowledge_db"]["rows"]["knowledge_chunks"] == 240
        assert step["ceilings"]["after_delete"]["knowledge_db"]["rows"]["knowledge_chunks"] == 0
    # The Arabic questions were asked: the corpus has Arabic planted answers.
    assert report["steps"][1]["search"]["questions"] == 6


def test_a_soak_run_leaves_this_process_environment_as_it_was(soak_run):
    """The run points its own process at its private stores; afterwards the
    process reads the stores it read before (the Knowledge benchmark, run
    after it, found KAZMA_VECTOR_PATH still naming the soak's folder)."""
    _code, _report, data = soak_run
    leaked = {k: v for k, v in os.environ.items() if k.startswith("KAZMA_") and str(data) in v}
    assert not leaked, leaked


def test_negative_control_the_run_without_its_restore_leaks_its_stores(tmp_path, monkeypatch):
    """What a run did before: its environment stayed in the process."""
    data = tmp_path / "data"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(knowledge_soak, "_run_soak", lambda args: os.environ.update(knowledge_soak._env(data)) or 0)
        knowledge_soak.run_soak(None)
        assert "KAZMA_VECTOR_PATH" not in os.environ or str(data) not in os.environ["KAZMA_VECTOR_PATH"]
        # The same body called without the wrapper leaves it behind.
        knowledge_soak._run_soak(None)
        assert str(data) in os.environ["KAZMA_VECTOR_PATH"]


def test_a_short_soak_does_every_operation_and_stays_healthy(soak_run):
    code, report, data = soak_run
    assert code == 0 and report["healthy"] and report["status"] == "passed", report["violations"]
    # Six cycles and the final start; no process failed to read a library
    # (the final start would have been repeated for it).
    assert report["restarts"] == report["cycles"] == 7, report
    assert not report.get("vector_read_failures"), report.get("vector_read_failures")
    ops = report["operations"]
    for name in ("crawls", "refreshes", "resumes", "documents", "deletes", "model_switches"):
        assert ops[name] >= 1, (name, ops)
    assert report["crashes"] >= 1 and ops["pages_failed_as_planned"] >= 1, report
    assert len(report["identities"]) >= 2 and all(s["from"] != s["to"] for s in report["switches"]), report
    assert any(s["reembedded"] > 0 for s in report["switches"]), report["switches"]
    recall = report["recall"]
    assert recall["hybrid"][0] + report["hybrid_rank_drops"] == recall["hybrid"][1] > 0, report
    assert report["recall"]["keyword"][0] == report["recall"]["keyword"][1]
    last = report["growth_last"]
    assert last["active_chunks"] > 0 and last["journal_jobs"] <= 24 and last["provenance_files"] > 0, last
    # Dropped collections (deletes, model switches) left nothing on disk; a
    # deleted library's words left the keyword index.
    assert all(g["orphan_vector_folders"] == 0 for g in report["growth"]), report["growth"]
    assert all(g["deleted_words_in_keyword_segments"] == 0 for g in report["growth"]), report["growth"]
    # Every operation's effect is in the stores at the end.
    held = report["held"]
    assert held["live_libraries"] >= 1 and held["deleted_libraries"] >= 1 and held["pending_resumes"] == 0
    assert (data / "knowledge.db").is_file() and (data / "vector_memory").is_dir()


@pytest.mark.parametrize("mode", ["scale", "soak"])
def test_the_report_is_on_disk_while_the_run_goes(scale_run, soak_run, mode):
    """A multi-day run a reboot ends early still leaves its evidence: the
    report is rewritten whole as it goes, then marked finished once."""
    written = _WRITTEN[mode]
    assert written.count("running") >= 3, written
    assert written[-1] == "passed" and "passed" not in written[:-1], written


# ── negative controls: the checks see what they are for ────────────────────


def _stores(tmp_path, monkeypatch):
    """Two libraries (English, Arabic) of three pages each, published and
    kept the way a crawl does it, and their expected state."""
    from kazma_core.stores import knowledge_ingest as ki
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("KAZMA_DATA_DIR", str(data))
    seed, state = 7, {"libs": {}, "deleted": {}}
    store = KnowledgeStore(db_path=str(data / "knowledge.db"))
    try:
        for lib, lang in (("kept-en", "en"), ("kept-ar", "ar")):
            seed_url = knowledge_soak.seed_url_for(lib)
            store.create_library(lib, lib, seed_url=seed_url)
            spec = {"lang": lang, "seed_url": seed_url, "stored": {}, "docs": {}}
            for page_no in (1, 2, 3):
                text, _fact = knowledge_soak.page_doc(seed, lib, lang, page_no, 1)
                url = knowledge_soak.page_url(seed_url, page_no)
                chunks = chunk_markdown_doc(text, source_url=url, library_id=lib,
                                            document_title=ki._derive_title(text, url))
                store.publish_page(lib, url, [chunk_to_dict(c) for c in chunks])
                ki._save_provenance(lib, url, text)
                spec["stored"][str(page_no)] = 1
            state["libs"][lib] = spec
    finally:
        store.close()
    return data, state, seed


def _db(data: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(data / "knowledge.db"))


def test_the_check_passes_untouched_stores(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    problems, sample = knowledge_soak.check_stores(data, state, seed)
    assert problems == [], problems
    assert sample["libraries"] == 2 and sample["active_chunks"] == sample["keyword_rows"] > 0
    assert sample["provenance_files"] == 6


def test_the_check_sees_a_chunk_deleted_behind_the_stores_back(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    conn = _db(data)
    try:
        conn.execute("DELETE FROM knowledge_chunks WHERE rowid = (SELECT MIN(rowid) FROM knowledge_chunks "
                     "WHERE library_id = 'kept-ar')")
        conn.commit()
    finally:
        conn.close()
    problems, _ = knowledge_soak.check_stores(data, state, seed)
    assert any("published and not active (lost)" in p and "kept-ar" in p for p in problems), problems


def test_the_check_sees_a_keyword_row_left_for_a_retired_chunk(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    conn = _db(data)
    try:
        conn.execute("INSERT INTO knowledge_chunks_fts (rowid, content, folded, library_id, source_url, chunk_id) "
                     "VALUES (999999, 'a retired page', 'a retired page', 'kept-en', "
                     "'https://kept-en.soak.example.test/docs/page-0009', 'kept-en:retired:0')")
        conn.commit()
    finally:
        conn.close()
    problems, _ = knowledge_soak.check_stores(data, state, seed)
    assert any("1 row(s) without an active chunk" in p for p in problems), problems


def test_the_check_sees_a_deleted_librarys_fetched_pages_left_on_disk(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    state["deleted"]["gone-en"] = {"lang": "en", "seed_url": knowledge_soak.seed_url_for("gone-en"),
                                   "stored": {}, "docs": {}}
    folder = data / "knowledge_provenance" / "gone-en"
    folder.mkdir(parents=True)
    (folder / "page-0001.md").write_text("# Source: https://gone-en.soak.example.test/docs/page-0001\n\nold text",
                                         encoding="utf-8")
    problems, _ = knowledge_soak.check_stores(data, state, seed)
    assert any("gone-en" in p and "left on disk" in p for p in problems), problems


def test_the_check_sees_a_deleted_librarys_words_left_in_the_keyword_index(tmp_path, monkeypatch):
    from kazma_core.stores.knowledge import KnowledgeStore
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    data, state, seed = _stores(tmp_path, monkeypatch)
    lib, lang = "gone-en", "en"
    seed_url = knowledge_soak.seed_url_for(lib)
    store = KnowledgeStore(db_path=str(data / "knowledge.db"))
    try:
        store.create_library(lib, lib, seed_url=seed_url)
        text, _fact = knowledge_soak.page_doc(seed, lib, lang, 1, 1)
        url = knowledge_soak.page_url(seed_url, 1)
        store.publish_page(lib, url, [chunk_to_dict(c) for c in chunk_markdown_doc(
            text, source_url=url, library_id=lib, document_title="t")])
        # The delete as it was before 2026-10-15: rows and markers, no purge.
        monkeypatch.setattr(KnowledgeStore, "purge_search_index", lambda self: {})
        store.delete_library(lib)
    finally:
        store.close()
    state["deleted"][lib] = {"lang": lang, "seed_url": seed_url, "stored": {"1": 1}, "docs": {}}
    problems, sample = knowledge_soak.check_stores(data, state, seed)
    assert sample["deleted_words_in_keyword_segments"] == 1
    assert any(p.startswith("keyword index: its segments still hold at least 1") for p in problems), problems


def test_the_check_sees_a_vector_folder_no_collection_owns(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    vectors = data / "vector_memory"
    vectors.mkdir()
    conn = sqlite3.connect(str(vectors / "chroma.sqlite3"))
    try:
        conn.execute("CREATE TABLE segments (id TEXT PRIMARY KEY, type TEXT, scope TEXT, collection TEXT)")
        conn.execute("INSERT INTO segments VALUES ('owned-segment', 'hnsw', 'VECTOR', 'c1')")
        conn.commit()
    finally:
        conn.close()
    (vectors / "owned-segment").mkdir()
    orphan = vectors / "dropped-segment"
    orphan.mkdir()
    (orphan / "data_level0.bin").write_bytes(b"\0" * 64)
    problems, sample = knowledge_soak.check_stores(data, state, seed)
    assert any("1 vector folder(s) no collection owns" in p for p in problems), problems
    assert sample["orphan_vector_folders"] == 1 and sample["orphan_vector_bytes"] == 64
    # Tolerated, it is a counted finding and no violation.
    report = {"findings": []}
    problems, _ = knowledge_soak.check_stores(data, state, seed, tolerate_orphans=True, report=report)
    assert problems == [] and report["findings"] and "vector store:" in report["findings"][0], report


def test_the_check_sees_a_crawl_journal_past_its_bound(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(data / "settings.db"))
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL, "
                     "category TEXT NOT NULL, updated_at TEXT NOT NULL)")
        conn.executemany("INSERT INTO settings VALUES (?, ?, 'acquisition_crawls', '')",
                         [(f"acquisition.crawl.{i:032x}", json.dumps({"job_id": str(i), "status": "completed",
                                                                      "library_id": "kept-en"}))
                          for i in range(25)])
        conn.commit()
    finally:
        conn.close()
    problems, sample = knowledge_soak.check_stores(data, state, seed)
    assert sample["journal_jobs"] == 25
    assert any(p.startswith("crawl journal: 25 jobs") for p in problems), problems


def test_the_check_sees_unified_items_back(tmp_path, monkeypatch):
    data, state, seed = _stores(tmp_path, monkeypatch)
    conn = sqlite3.connect(str(data / "memory_ops.db"))
    try:
        conn.execute("CREATE TABLE unified_items (id TEXT)")
        conn.commit()
    finally:
        conn.close()
    problems, _ = knowledge_soak.check_stores(data, state, seed)
    assert any("unified_items exists" in p for p in problems), problems


_OK = {"token": "zqaaaaaaaa", "lang": "en", "library": "kept-en", "hybrid": True, "keyword": True, "meaning": True}


def test_the_search_checks_see_a_miss_and_a_returned_deleted_answer():
    plan = {"live": ["kept-en"]}
    assert knowledge_soak.check_facts(plan, {"asked": [_OK], "fts_before_sweep": {"ok": True}}) == ([], {})
    # The approximate index passing over a present vector is a measurement, not a violation.
    passed_over = {**_OK, "meaning": False, "meaning_miss": {"kind": "index", "similarity": 0.7}}
    assert knowledge_soak.check_facts(plan, {"asked": [passed_over], "fts_before_sweep": {"ok": True}}) == ([], {})
    # ... and the hybrid top-k dropping a keyword-only answer then is a ranking finding;
    rank_dropped = {**passed_over, "hybrid": False}
    assert knowledge_soak.check_facts(plan, {"asked": [rank_dropped], "fts_before_sweep": {"ok": True}}) == ([], {})
    # a hybrid miss with the vector missing, or the keyword index missing too, is a violation.
    for bad in ({**rank_dropped, "meaning_miss": {"kind": "no_vector", "detail": "no vector"}},
                {**rank_dropped, "keyword": False}):
        problems, _ = knowledge_soak.check_facts(plan, {"asked": [bad], "fts_before_sweep": {"ok": True}})
        assert any("not found by hybrid" in p for p in problems), problems
    missed = {**_OK, "keyword": False, "meaning": False,
              "meaning_miss": {"kind": "no_vector", "detail": "the answer's chunk has no vector"}}
    gone = {"token": "zqbbbbbbbb", "library": "deleted-lib", "found_by": ["hybrid across the live libraries"],
            "problem": None}
    problems, _ = knowledge_soak.check_facts(plan, {"asked": [missed], "absent": [gone],
                                                    "fts_before_sweep": {"ok": True},
                                                    "collections": ["kazma_kb_kept-en", "kazma_kb_deleted-lib"]})
    assert any("not found by keyword" in p for p in problems), problems
    assert any("has no vector" in p for p in problems), problems
    assert any("still found by hybrid" in p for p in problems), problems
    assert any("kazma_kb_deleted-lib belongs to no live library" in p for p in problems), problems


def test_a_collection_one_process_cannot_read_is_a_finding_until_it_survives_a_restart():
    """Chroma failing to read a collection in one process (seen on Windows:
    "Error creating hnsw segment reader: Nothing found on disk") is counted
    and asked again by the next start; the same failure there is a violation."""
    plan = {"live": ["kept-en"]}
    error = "InternalError: Error executing plan: Internal error: Error creating hnsw segment reader"
    facts = {"asked": [{**_OK, "meaning": False,
                        "meaning_miss": {"kind": "query_failed", "detail": f"failed twice: {error}"}}],
             "fts_before_sweep": {"ok": True},
             "sweep": {"error": f"the repair sweep failed: failed kept-en: {error}", "failed": {"kept-en": error}},
             "vectors": {"kept-en": {"available": True, "identity": "h", "missing": 3, "orphans": 0, "foreign": 0,
                                     "health_state": "failed", "health_vectors": 0, "health_chunks": 3}},
             "vector_read_failures": {"kept-en": error}}
    problems, confined = knowledge_soak.check_facts(plan, facts)
    assert problems == [] and len(confined["kept-en"]) == 4, (problems, confined)
    problems, confined = knowledge_soak.check_facts(plan, facts, failed_before={"kept-en"})
    assert confined == {} and len(problems) == 4 and all("survived a restart" in p for p in problems), problems
    # Tolerated (--tolerate-vector-read-failures), the survivor is counted instead.
    problems, confined = knowledge_soak.check_facts(plan, facts, failed_before={"kept-en"}, tolerate_unreadable=True)
    assert problems == [] and len(confined["kept-en"]) == 4, (problems, confined)
    # Without the read failure the same facts are violations (negative control).
    problems, confined = knowledge_soak.check_facts(plan, {**facts, "vector_read_failures": {}})
    assert confined == {} and len(problems) == 4, problems
