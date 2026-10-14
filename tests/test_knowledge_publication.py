"""The Knowledge Library's store: one transaction per page, every position
kept, the keyword index by rowid, its own file and quota, reopen and close
(Knowledge audit 2026-10-13: KB-E02, KB-E05, KB-E06, KB-E10, KB-E11, KB-E13).

Each fix has its regression and, beside it, the old behaviour as the negative
control (AGENTS.md §28). The witnesses of the audit asserted the defects; they
are inverted here, never kept.
"""

from __future__ import annotations

import ast
import hashlib
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from kazma_core.stores import knowledge
from kazma_core.stores.knowledge import (
    KnowledgeLibraryGone,
    KnowledgeStorageFull,
    KnowledgeStore,
)

REPO = Path(__file__).resolve().parents[1]
CORE = str(REPO / "kazma-core")


class _NoVector:
    """A vector store that is down: keyword-only, like a Chroma outage."""

    available = False


def _store(tmp_path: Path, name: str = "knowledge.db") -> KnowledgeStore:
    return KnowledgeStore(db_path=str(tmp_path / name))


def _page(library: str, url: str, texts: list[str]) -> list[dict]:
    return [
        {"library_id": library, "source_url": url, "chunk_index": i, "content": t,
         "content_hash": hashlib.sha256(t.encode()).hexdigest(), "document_title": "Docs",
         "section_header": f"S{i}"}
        for i, t in enumerate(texts)
    ]


def _index(store: KnowledgeStore):
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    index = KnowledgeIndex(store)
    index._vector_store_for = lambda _lib: _NoVector()  # type: ignore[method-assign]
    return index


def _page_text(store: KnowledgeStore, library: str, url: str) -> list[str]:
    rows = store._get_conn().execute(
        "SELECT content FROM knowledge_chunks WHERE library_id = ? AND source_url = ? ORDER BY chunk_index",
        (library, url),
    ).fetchall()
    return [r[0] for r in rows]


def _fts_ids(store: KnowledgeStore, library: str) -> set[str]:
    return {r[0] for r in store._get_conn().execute(
        "SELECT chunk_id FROM knowledge_chunks_fts WHERE library_id = ?", (library,))}


# ── KB-E05: every position of a page is a chunk ─────────────────────────────

_REPEATED = (
    "## Send a message\nPOST the payload below.\n\n"
    "```json\n{\"success\": true}\n```\n\n"
    "## Mark as read\nPOST the same acknowledgement.\n\n"
    "```json\n{\"success\": true}\n```\n"
)


def test_identical_sections_keep_every_position_and_the_page_settles(tmp_path):
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict

    url = "https://docs.example.test/messages"
    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        index = _index(store)
        chunks = [chunk_to_dict(c) for c in chunk_markdown_doc(_REPEATED, source_url=url, library_id="api")]
        assert len(chunks) == 4 and len({c["id"] for c in chunks}) == 4
        assert index.index("api", chunks) == (4, 0)
        assert store.count_chunks("api") == 4
        assert store.list_source_content_hashes("api", url) == [c["content_hash"] for c in chunks]
        # The second pass: the page is unchanged and nothing is written.
        assert index.index("api", chunks) == (0, 4)
        assert store.count_chunks("api") == 4
    finally:
        store.close()


def test_the_position_free_id_collapses_identical_sections():
    """Negative control: the old id named the page and the content only."""
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, make_chunk_id

    url = "https://docs.example.test/messages"
    chunks = chunk_markdown_doc(_REPEATED, source_url=url, library_id="api")
    assert len({make_chunk_id("api", url, c.content_hash) for c in chunks}) == 3


def test_a_page_stored_under_old_ids_takes_the_positional_ones_when_ingested_again(tmp_path):
    """A library ingested before positional ids holds position-free ones; the
    next ingest of the page republishes it (its ids differ), once."""
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc, chunk_to_dict, make_chunk_id

    url = "https://docs.example.test/messages"
    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        chunks = [chunk_to_dict(c) for c in chunk_markdown_doc(_REPEATED, source_url=url, library_id="api")]
        held: set[str] = set()
        for chunk in chunks:  # the old writer: position-free ids, one row per distinct section
            legacy_id = make_chunk_id("api", url, chunk["content_hash"])
            if legacy_id not in held:
                held.add(legacy_id)
                store.upsert_chunk({**chunk, "id": legacy_id})
        assert store.count_chunks("api") == 3
        index = _index(store)
        assert index.index("api", chunks) == (4, 0)
        assert store.count_chunks("api") == 4
        assert _fts_ids(store, "api") == {c["id"] for c in chunks}
        assert index.index("api", chunks) == (0, 4)  # settled
    finally:
        store.close()


# ── KB-E02: a page is published in one transaction ──────────────────────────

_FAULT_POINTS = ["begin", "library_checked", "fts_deleted", "rows_deleted", "row_inserted:0",
                 "fts_inserted:0", "row_inserted:1", "count_updated"]


@pytest.mark.parametrize("point", _FAULT_POINTS)
def test_a_failure_at_any_step_keeps_the_old_page_whole(tmp_path, monkeypatch, point):
    from kazma_core.stores.knowledge_index import KnowledgePublishError

    url = "https://docs.example.test/auth"
    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        index = _index(store)
        v1 = [f"version one paragraph {i} about tokens" for i in range(3)]
        index.index("api", _page("api", url, v1))
        before_fts = _fts_ids(store, "api")

        def fault(at: str) -> None:
            if at == point:
                raise sqlite3.OperationalError("database or disk is I/O failing")

        monkeypatch.setattr(knowledge, "_FAULT", fault)
        v2 = [f"version two paragraph {i} about scopes" for i in range(3)]
        with pytest.raises(KnowledgePublishError) as failure:
            index.index("api", _page("api", url, v2))
        assert failure.value.new == 0 and failure.value.unchanged == 0  # never "skipped"
        assert _page_text(store, "api", url) == v1
        assert _fts_ids(store, "api") == before_fts
        assert [cid for cid, _s in store.fts_search("tokens", "api")]
        assert store.fts_search("scopes", "api") == []
    finally:
        store.close()


def test_the_old_two_transaction_publication_left_a_partial_page(tmp_path):
    """Negative control: purge in one transaction, then one per chunk -- a
    failure on the second chunk left a page that was neither version."""
    url = "https://docs.example.test/auth"
    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        for c in _page("api", url, [f"version one paragraph {i}" for i in range(3)]):
            store.upsert_chunk(c)
        store.delete_chunks_for_source("api", url)
        v2 = _page("api", url, [f"version two paragraph {i}" for i in range(3)])
        store.upsert_chunk(v2[0])
        # (the second write failed here, and was counted as a skip)
        left = _page_text(store, "api", url)
        assert left == ["version two paragraph 0"]
    finally:
        store.close()


_CHILD = r"""
import os, sys, hashlib
sys.path.insert(0, {core!r})
from kazma_core.stores import knowledge
store = knowledge.KnowledgeStore(db_path={db!r})
point = {point!r}
def fault(at):
    if at == point:
        os._exit(17)
knowledge._FAULT = fault
url = "https://docs.example.test/auth"
texts = ["version two paragraph %d about scopes" % i for i in range(3)]
chunks = [dict(library_id="api", source_url=url, chunk_index=i, content=t,
               content_hash=hashlib.sha256(t.encode()).hexdigest()) for i, t in enumerate(texts)]
store.publish_page("api", url, chunks)
os._exit(17 if point == "committed" else 0)
"""


@pytest.mark.parametrize("point", ["library_checked", "fts_deleted", "rows_deleted",
                                   "row_inserted:1", "count_updated", "committed"])
def test_a_killed_process_leaves_the_old_page_or_the_new_one(tmp_path, point):
    """A real process killed at each step of a page publication."""
    from kazma_core.security.process_budget import run_bounded

    url = "https://docs.example.test/auth"
    db = str(tmp_path / "knowledge.db")
    store = KnowledgeStore(db_path=db)
    store.create_library("api", "API")
    v1 = [f"version one paragraph {i} about tokens" for i in range(3)]
    store.publish_page("api", url, _page("api", url, v1))
    store.close()
    allowed = {"systemroot", "windir", "temp", "tmp", "path", "kazma_data_dir"}
    result = run_bounded([sys.executable, "-I", "-c", _CHILD.format(core=CORE, db=db, point=point)],
                         env={k: v for k, v in os.environ.items() if k.lower() in allowed},
                         timeout=60, output_bytes=32768, memory_bytes=1024**3)
    assert result.returncode == 17, result.stderr
    reopened = KnowledgeStore(db_path=db)
    try:
        texts = _page_text(reopened, "api", url)
        v2 = [f"version two paragraph {i} about scopes" for i in range(3)]
        assert texts == (v2 if point == "committed" else v1)
        assert reopened.fts_coverage()["ok"]
        word = "scopes" if point == "committed" else "tokens"
        assert len(reopened.fts_search(word, "api")) == 3
    finally:
        reopened.close()


def test_a_deleted_library_refuses_publication_and_makes_no_collection(tmp_path, monkeypatch):
    from kazma_core.memory import vector_store_global as vsg
    from kazma_core.stores.knowledge_index import KnowledgeIndex

    from tests._module_stubs import stub_modules
    from tests.test_knowledge_meaning_search import _chromadb_stub, _Embedder

    monkeypatch.setenv("KAZMA_VECTOR_PATH", str(tmp_path / "vector_memory"))
    monkeypatch.setattr(vsg, "_CLIENTS", {})
    monkeypatch.setattr(vsg, "get_encoder", lambda model_name=None: _Embedder())
    store = _store(tmp_path)
    try:
        with stub_modules(_chromadb_stub()):
            index = KnowledgeIndex(store)
            store.create_library("gone", "Gone")
            assert index.delete_library("gone") is True
            with pytest.raises(KnowledgeLibraryGone):
                index.index("gone", _page("gone", "https://docs.example.test/p", ["text one", "text two"]))
            client = next(iter(vsg._CLIENTS.values()))
            assert "kazma_kb_gone" not in client.collections
    finally:
        store.close()


@pytest.fixture
def journal(tmp_path, monkeypatch):
    from kazma_core import config_store

    settings = config_store.ConfigStore(str(tmp_path / "settings.db"))
    monkeypatch.setattr(config_store, "get_config_store", lambda: settings)
    yield settings
    settings.close()


def _crawl_env(monkeypatch, store, index, pages, fetch):
    import contextlib

    import kazma_core.security.ssrf as ssrf
    from kazma_core.stores import knowledge_ingest as ki

    async def discover(*a, **k):
        return list(pages)

    monkeypatch.setattr(ssrf, "validate_url", lambda *a, **k: ())
    monkeypatch.setattr(ki, "kb_discover_pages", discover)
    monkeypatch.setattr(ki, "_extract_page", fetch)
    monkeypatch.setattr(ki, "_kb_delay_ms", lambda: 0)
    monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
    monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
    monkeypatch.setattr(ki, "_pw_browser_scope", contextlib.nullcontext)
    monkeypatch.setattr(ki, "_save_provenance", lambda *a: None)
    return ki


@pytest.mark.asyncio
async def test_a_page_that_cannot_be_stored_fails_and_the_crawl_ends_partial(journal, tmp_path, monkeypatch):
    from kazma_core.web_acquire import crawl_jobs

    store = _store(tmp_path)
    try:
        store.create_library("docs", "Docs")
        index = _index(store)
        pages = [f"https://docs.example.test/p{i}" for i in range(3)]

        async def fetch(url, **kwargs):
            return f"# Page {url}\n\n" + ("useful body text " * 30), "ok", ""

        ki = _crawl_env(monkeypatch, store, index, pages, fetch)
        real = store.publish_page

        def full_on_second(library_id, url, chunks, **kw):
            if url.endswith("p1"):
                raise sqlite3.OperationalError("database or disk is full")
            return real(library_id, url, chunks, **kw)

        monkeypatch.setattr(store, "publish_page", full_on_second)
        updates = [u async for u in ki.ingest_site("docs", "https://docs.example.test/", job_id="partial")]
        final = updates[-1]
        assert final.phase == "done" and final.failed == 1 and final.fetched == 2
        assert final.pages_unchanged == 0  # a failed write is never "unchanged"
        row = crawl_jobs.inspect_jobs("partial")
        assert row["status"] == "partial"
        assert [p["status"] for p in row["pages"]] == ["completed", "failed", "completed"]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_a_crawl_stops_when_its_library_is_deleted(journal, tmp_path, monkeypatch):
    from kazma_core.web_acquire import crawl_jobs

    store = _store(tmp_path)
    try:
        store.create_library("docs", "Docs")
        index = _index(store)
        pages = [f"https://docs.example.test/p{i}" for i in range(3)]
        fetched: list[str] = []

        async def fetch(url, **kwargs):
            fetched.append(url)
            if len(fetched) == 1:
                store.delete_library("docs")  # deleted while the crawl runs
            return f"# Page {url}\n\n" + ("useful body text " * 30), "ok", ""

        ki = _crawl_env(monkeypatch, store, index, pages, fetch)
        updates = [u async for u in ki.ingest_site("docs", "https://docs.example.test/", job_id="stop")]
        assert fetched == pages[:1]  # no egress for the pages after the delete
        assert updates[-1].phase == "error" and "deleted" in updates[-1].message
        assert crawl_jobs.inspect_jobs("stop")["status"] == "failed"
    finally:
        store.close()


#: Product code allowed to write knowledge_chunks rows: the store's own
#: publication paths. Everything else goes through publish_page,
#: publish_document_version or the explicit purge.
_CHUNK_WRITERS = frozenset({"kazma-core/kazma_core/stores/knowledge.py"})
_LEGACY_WRITE_CALLS = frozenset({"upsert_chunk", "delete_chunks_for_source"})


def _chunk_write_offenders(path: str, source: str) -> list[str]:
    found = []
    if path not in _CHUNK_WRITERS and "INSERT INTO knowledge_chunks" in source:
        found.append(f"{path}: writes knowledge_chunks rows")
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return found
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in _LEGACY_WRITE_CALLS
                and path not in _CHUNK_WRITERS | {"kazma-core/kazma_core/stores/knowledge_index.py"}):
            found.append(f"{path}:{node.lineno} {node.func.attr}()")
    return found


def test_every_knowledge_writer_goes_through_one_publication_path():
    listed = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True,
                            check=True).stdout.split()
    offenders = []
    for rel in listed:
        if not rel.startswith("kazma-") or "/tests/" in rel or "_tests/" in rel:
            continue
        path = REPO / rel
        if path.exists():
            offenders += _chunk_write_offenders(rel, path.read_text(encoding="utf-8", errors="replace"))
    assert offenders == [], offenders


def test_the_writer_gate_sees_a_second_path():
    """Negative control: the old index() wrote chunk by chunk."""
    planted = "def index(store, chunks):\n    for c in chunks:\n        store.upsert_chunk(c)\n"
    assert _chunk_write_offenders("kazma-core/kazma_core/stores/elsewhere.py", planted)
    assert _chunk_write_offenders("kazma-ui/kazma_ui/x.py", "SQL = 'INSERT INTO knowledge_chunks (id) VALUES (?)'")


# ── KB-E06: the keyword index is keyed by rowid ─────────────────────────────


def _fill(store: KnowledgeStore, n: int) -> None:
    """*n* chunks of another library, their index rows keyed by rowid."""
    conn = store._get_conn()
    conn.execute("BEGIN")
    now = "2026-10-14T00:00:00+00:00"
    conn.executemany(
        "INSERT INTO knowledge_chunks (id, library_id, source_url, chunk_index, content_hash, content, created_at) "
        "VALUES (?, 'big', ?, 0, ?, ?, ?)",
        [(f"big:{i}", f"https://big.test/{i}", f"h{i}", f"filler chunk {i} lorem ipsum", now) for i in range(n)],
    )
    conn.execute(
        "INSERT INTO knowledge_chunks_fts (rowid, content, folded, library_id, source_url, chunk_id) "
        "SELECT rowid, content, content, library_id, source_url, id FROM knowledge_chunks WHERE library_id = 'big'"
    )
    conn.execute("COMMIT")


def _steps(conn: sqlite3.Connection, action) -> int:
    counter = {"n": 0}

    def tick() -> int:
        counter["n"] += 1
        return 0

    conn.set_progress_handler(tick, 100)
    try:
        action()
    finally:
        conn.set_progress_handler(None, 0)
    return counter["n"] * 100


@pytest.mark.parametrize("big", [10_000, 100_000])
def test_a_page_write_costs_the_same_whatever_the_index_holds(tmp_path, big):
    url = "https://small.test/page"
    costs = []
    for n in (1_000, big):
        store = _store(tmp_path, f"k{n}.db")
        try:
            store.create_library("big", "Big")
            store.create_library("small", "Small")
            _fill(store, n)
            store.publish_page("small", url, _page("small", url, ["one", "two"]))
            conn = store._get_conn()
            costs.append(_steps(conn, lambda: store.publish_page(
                "small", url, _page("small", url, ["three", "four"]))))
        finally:
            store.close()
    small, large = costs
    assert large < 1.5 * small + 2_000, costs


def test_the_unindexed_column_delete_scans_every_row(tmp_path):
    """Negative control: the old statement, a delete by an UNINDEXED column."""
    costs = []
    for n in (1_000, 10_000):
        store = _store(tmp_path, f"old{n}.db")
        try:
            store.create_library("big", "Big")
            _fill(store, n)
            conn = store._get_conn()
            costs.append(_steps(conn, lambda: conn.execute(
                "DELETE FROM knowledge_chunks_fts WHERE chunk_id = ?", ("small:x",))))
        finally:
            store.close()
    assert costs[1] > 5 * costs[0], costs


def test_a_library_is_deleted_in_bounded_batches(tmp_path):
    store = _store(tmp_path)
    try:
        store.create_library("big", "Big")
        _fill(store, 1_200)
        statements: list[str] = []
        store._get_conn().set_trace_callback(statements.append)
        assert store.delete_library("big") is True
        store._get_conn().set_trace_callback(None)
        assert sum(s.startswith("BEGIN IMMEDIATE") for s in statements) >= 3
        assert store._get_conn().execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0] == 0
        assert store._get_conn().execute("SELECT COUNT(*) FROM knowledge_chunks_fts").fetchone()[0] == 0
    finally:
        store.close()


def test_search_stays_quick_while_a_large_ingest_runs(tmp_path):
    store = _store(tmp_path)
    try:
        store.create_library("docs", "Docs")
        store.create_library("bulk", "Bulk")
        store.publish_page("docs", "https://docs.test/a", _page("docs", "https://docs.test/a", ["webhook retry"]))
        done = threading.Event()

        def ingest() -> None:
            for i in range(300):
                url = f"https://bulk.test/{i}"
                store.publish_page("bulk", url, _page("bulk", url, [f"bulk page {i} part {j}" for j in range(5)]))
            done.set()

        writer = threading.Thread(target=ingest)
        writer.start()
        latencies = []
        while not done.is_set():
            started = time.perf_counter()
            assert store.fts_search("webhook", "docs")
            latencies.append(time.perf_counter() - started)
        writer.join()
        latencies.sort()
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
        assert p95 < 1.0, p95
    finally:
        store.close()


# ── KB-E13: a reopened connection keeps every pragma; close holds the lock ──


def test_a_reopened_store_deletes_a_library_with_its_chunks(tmp_path):
    store = _store(tmp_path)
    try:
        store.create_library("lib", "Lib")
        store.publish_page("lib", "https://docs.test/a", _page("lib", "https://docs.test/a", ["secret runbook"]))
        store.close()  # _get_conn() reopens lazily
        assert store._get_conn().execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert store.delete_library("lib") is True
        assert store._get_conn().execute(
            "SELECT COUNT(*) FROM knowledge_chunks WHERE library_id = 'lib'").fetchone()[0] == 0
        store.create_library("lib", "Same slug, new library")
        assert store.count_chunks("lib") == 0
    finally:
        store.close()


def test_a_connection_without_the_pragma_leaves_orphans(tmp_path):
    """Negative control: the reopen of before -- no foreign keys."""
    store = _store(tmp_path)
    store.create_library("lib", "Lib")
    store.publish_page("lib", "https://docs.test/a", _page("lib", "https://docs.test/a", ["secret runbook"]))
    store.close()
    raw = sqlite3.connect(str(tmp_path / "knowledge.db"))
    raw.execute("DELETE FROM knowledge_libraries WHERE id = 'lib'")
    raw.commit()
    assert raw.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE library_id = 'lib'").fetchone()[0] == 1
    raw.close()


def test_close_waits_for_a_statement_holding_the_lock(tmp_path):
    store = _store(tmp_path)
    store._get_conn()
    released = threading.Event()

    def hold() -> None:
        with store._lock:
            released.wait(0.4)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.05)
    started = time.monotonic()
    store.close()
    waited = time.monotonic() - started
    holder.join()
    assert waited >= 0.3, waited


def test_the_old_close_did_not_wait(tmp_path):
    """Negative control: close() without the lock returns at once."""
    store = _store(tmp_path)
    conn = store._get_conn()
    gate = threading.Event()

    def hold() -> None:
        with store._lock:
            gate.wait(0.4)

    holder = threading.Thread(target=hold)
    holder.start()
    time.sleep(0.05)
    started = time.monotonic()
    conn.close()  # what close() did: no lock
    store._conn = None
    assert time.monotonic() - started < 0.2
    gate.set()
    holder.join()


# ── KB-E10: its own file, a quota at admission, reads never refused ────────


def test_the_knowledge_library_has_its_own_file(tmp_path, monkeypatch):
    from kazma_core import paths

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    store = KnowledgeStore()
    try:
        assert store.path == Path(paths.knowledge_db())
        assert store.path.name == "knowledge.db" and store.path != Path(paths.settings_db())
    finally:
        store.close()


def test_at_the_quota_ingest_is_refused_before_fetch_and_reads_work(tmp_path, monkeypatch):
    from kazma_core.stores import knowledge_ingest as ki

    monkeypatch.setattr(knowledge, "_MAX_KNOWLEDGE_BYTES", 512 * 1024)
    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        filled = 0
        with pytest.raises(KnowledgeStorageFull):
            for i in range(200):
                url = f"https://docs.test/{i}"
                store.publish_page("api", url, _page("api", url, [f"page {i} " + "lorem ipsum " * 300]))
                filled += 1
        assert filled > 0
        with pytest.raises(KnowledgeStorageFull):
            store.ensure_capacity()
        # Reads work at the quota, and a reopen opens.
        assert store.count_chunks("api") == filled
        assert store.fts_search("lorem", "api")
        store.close()
        store = _store(tmp_path)
        assert store.list_libraries()

        async def fetch_forbidden(*a, **k):
            pytest.fail("nothing is fetched for a store at its quota")

        monkeypatch.setattr(ki, "get_knowledge_store", lambda: store)
        monkeypatch.setattr(ki, "_extract_page", fetch_forbidden)
        import asyncio

        result = asyncio.run(ki.ingest_url("api", "https://docs.test/new"))
        assert result.pages_failed == 1 and "quota" in result.errors[0]
        # Deleting frees room (and compaction returns the pages).
        store.delete_library("api")
        store.compact()
        store.ensure_capacity()
    finally:
        store.close()


def test_the_old_ceiling_measured_the_whole_shared_file(tmp_path):
    """Negative control: the size the old check refused to open at -- the
    page count of a file another writer grew, the KB itself empty."""
    path = tmp_path / "shared-settings.db"
    other = sqlite3.connect(str(path))
    other.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    other.execute("INSERT INTO settings VALUES ('acquisition.crawls', ?)", ("x" * 400_000,))
    other.commit()
    page_size = other.execute("PRAGMA page_size").fetchone()[0]
    pages = other.execute("PRAGMA page_count").fetchone()[0]
    other.close()
    assert pages * page_size > 256 * 1024  # past a 256 KiB ceiling: the old store raised here


def test_an_older_install_moves_its_tables_out_of_settings_db(tmp_path, monkeypatch):
    from kazma_core import paths

    monkeypatch.setenv("KAZMA_DATA_DIR", str(tmp_path))
    legacy = KnowledgeStore(db_path=paths.settings_db())  # the old layout: in settings.db
    legacy.create_library("api", "API")
    legacy.publish_page("api", "https://docs.test/a", _page("api", "https://docs.test/a", ["الاشتراك الشهري"]))
    legacy._get_conn().execute("UPDATE knowledge_libraries SET seed_url = 'javascript:alert(1)'")
    legacy.close()
    store = KnowledgeStore()
    try:
        assert store.count_chunks("api") == 1
        assert store.fts_search("الاشتراك", "api")
        assert store.get_library("api")["seed_url"] == ""  # a non-http seed does not travel
    finally:
        store.close()
    tables = {r[0] for r in sqlite3.connect(paths.settings_db()).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert not {"knowledge_libraries", "knowledge_chunks"} & tables


# ── KB-E11: the fold version, the index's health and its repair ────────────

_FOLD_SAMPLE = (
    "Webhooks Retry Delivery FIVE times",
    "المُسْتَخْدِم",
    "أحمد إبراهيم آخر",
    "مدرسة مستشفى",
    "١٢٣ ۴۵۶ 2026",
    "كــتاب",
    "ﻻ ﺍ",
)
#: KB_FOLD_VERSION -> the fold's output on the sample. A change to
#: ``fold_for_search`` fails here until KB_FOLD_VERSION is raised (which
#: rebuilds every index's folded column) and the new fingerprint recorded.
_FOLD_FINGERPRINTS = {1: "3437d99abc2540105e1d138f7c4ed55b9cdfcd225c9ea0ed6bb8743f4035f038"}


def _fingerprint(fold) -> str:
    return hashlib.sha256("\n".join(fold(s) for s in _FOLD_SAMPLE).encode("utf-8")).hexdigest()


def test_the_fold_version_names_the_fold():
    from kazma_core.documents.arabic import fold_for_search

    assert _fingerprint(fold_for_search) == _FOLD_FINGERPRINTS[knowledge.KB_FOLD_VERSION]


def test_a_changed_fold_changes_the_fingerprint():
    """Negative control: a fold that also lowercases differently is caught."""
    from kazma_core.documents.arabic import fold_for_search

    assert _fingerprint(lambda s: fold_for_search(s).upper()) != _FOLD_FINGERPRINTS[1]


def test_a_new_fold_version_rebuilds_the_folded_column(tmp_path):
    store = _store(tmp_path)
    store.create_library("api", "API")
    store.publish_page("api", "https://docs.test/a", _page("api", "https://docs.test/a", ["المستخدم"]))
    conn = store._get_conn()
    conn.execute("UPDATE knowledge_chunks_fts SET folded = 'stale'")
    conn.execute("UPDATE knowledge_meta SET value = '0' WHERE key = 'fold_version'")
    store.close()
    reopened = _store(tmp_path)
    try:
        folded = reopened._get_conn().execute("SELECT folded FROM knowledge_chunks_fts").fetchone()[0]
        assert folded != "stale"
        assert reopened._get_conn().execute(
            "SELECT value FROM knowledge_meta WHERE key = 'fold_version'").fetchone()[0] == str(knowledge.KB_FOLD_VERSION)
    finally:
        reopened.close()


def test_an_unchanged_marker_leaves_the_index_alone(tmp_path):
    """Negative control: without a version change the index is not rebuilt."""
    store = _store(tmp_path)
    store.create_library("api", "API")
    store.publish_page("api", "https://docs.test/a", _page("api", "https://docs.test/a", ["text"]))
    store._get_conn().execute("UPDATE knowledge_chunks_fts SET folded = 'stale'")
    store.close()
    reopened = _store(tmp_path)
    try:
        assert reopened._get_conn().execute("SELECT folded FROM knowledge_chunks_fts").fetchone()[0] == "stale"
    finally:
        reopened.close()


def test_a_broken_keyword_index_is_a_warning_and_a_health_failure(tmp_path, caplog):
    import logging

    from kazma_core.stores.knowledge_index import KnowledgeIndex

    store = _store(tmp_path)
    try:
        store.create_library("api", "API")
        store.publish_page("api", "https://docs.test/a", _page("api", "https://docs.test/a", ["webhook signature"]))
        assert store.fts_search("webhook", "api")
        store._get_conn().execute("ALTER TABLE knowledge_chunks_fts RENAME TO fts_gone")
        with caplog.at_level(logging.INFO, logger="kazma_core.stores.knowledge"):
            assert store.fts_search("webhook", "api") == []
        assert any(r.levelno == logging.WARNING for r in caplog.records)
        assert store.fts_problem
        assert KnowledgeIndex(store).health("api")["keyword_index_ok"] is False
        assert store.fts_coverage()["ok"] is False
        store._get_conn().execute("DROP TABLE fts_gone")
        assert store.rebuild_fts(reason="test") == 1
        assert store.fts_search("webhook", "api") and store.fts_problem is None
        assert store.fts_coverage()["ok"]
    finally:
        store.close()


def test_a_query_fts_cannot_parse_stays_quiet():
    """The other side of the split: a syntax error is the query's, not the index's."""
    assert knowledge._is_fts_syntax_error(sqlite3.OperationalError("fts5: syntax error near \"x\""))
    assert not knowledge._is_fts_syntax_error(sqlite3.OperationalError("no such table: knowledge_chunks_fts"))
    assert not knowledge._is_fts_syntax_error(sqlite3.OperationalError("database is locked"))
