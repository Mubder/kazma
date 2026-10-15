"""Tests for the KnowledgeStore SQLite layer.

Covers: library CRUD, chunk upsert with dedup, FTS5 lexical search,
cascade delete.  Uses a temp DB so the real settings.db is untouched.
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))

from kazma_core.stores.knowledge import KnowledgeStore, slugify_library_id

from tests._fts5_segments import held_only_by_deleted_rows, segment_terms

# ── Slugification ───────────────────────────────────────────────────────────


def test_slugify_normalizes_user_input():
    """Library IDs become ChromaDB collection names + URL segments, so they
    must be lowercase [a-z0-9_-].  Without slugification, a user entering
    'Meta WhatsApp Documentations 2' gets a fragile ID that breaks the
    collection name and URL-encodes to %20."""
    assert slugify_library_id("Meta WhatsApp Documentations 2") == "meta_whatsapp_documentations_2"
    assert slugify_library_id("ShipX-API") == "shipx-api"
    assert slugify_library_id("  foo / bar!!") == "foo_bar"
    assert slugify_library_id("already_ok-id") == "already_ok-id"
    assert slugify_library_id("") == "library"
    assert slugify_library_id("   ") == "library"


def test_create_library_slugifies_id():
    """The store must slugify on insert so downstream code never sees a
    raw user string with spaces/uppercase."""
    s = _fresh_store()
    lib = s.create_library("Meta WhatsApp Docs", "Meta WhatsApp Docs")
    assert lib["id"] == "meta_whatsapp_docs"
    # And the slug-form ID is what's retrievable.
    assert s.get_library("meta_whatsapp_docs") is not None
    assert s.get_library("Meta WhatsApp Docs") is None  # raw form not stored


def test_create_or_use_pattern_does_not_race_on_slug_mismatch():
    """Regression: the 'create-or-use' pattern in kb_api.py / commands.py
    used to call get_library(raw_id) then create_library(raw_id). When the
    raw ID had spaces, get_library() returned None (no row with raw id)
    but create_library() slugified → INSERT hit the UNIQUE constraint on
    the already-existing slug.  The fix is to slugify ONCE at the boundary
    and use the slug for BOTH the existence check and the insert."""
    from kazma_core.stores.knowledge import slugify_library_id

    s = _fresh_store()
    # First insert via raw name — slugifies to "shipx_whatsapp_api".
    s.create_library("ShipX WhatsApp API", "ShipX WhatsApp API")
    assert s.get_library("shipx_whatsapp_api") is not None

    # Now simulate the create-or-use pattern done CORRECTLY: slugify first,
    # then check, then maybe-create. Must NOT raise.
    raw = "ShipX WhatsApp API"
    slug = slugify_library_id(raw)
    existing = s.get_library(slug)
    assert existing is not None  # already there
    # Correct pattern: only create if not existing — no insert attempted.
    if not existing:
        s.create_library(slug, name=raw)
    # No exception, no duplicate row.
    assert s.count_chunks("shipx_whatsapp_api") == 0
    libs = [l["id"] for l in s.list_libraries()]
    assert libs.count("shipx_whatsapp_api") == 1  # exactly one, not two


def _fresh_store() -> KnowledgeStore:
    tmp = tempfile.mkdtemp(prefix="kazma_kb_test_")
    return KnowledgeStore(db_path=os.path.join(tmp, "settings.db"))


def _chunk_dict(library_id: str, source_url: str, idx: int, content: str) -> dict:
    import hashlib

    from kazma_core.stores.knowledge_chunker import make_chunk_id

    content_hash = hashlib.sha256(content.encode()).hexdigest()
    return {
        "id": make_chunk_id(library_id, source_url, content_hash),
        "library_id": library_id,
        "source_url": source_url,
        "document_title": "T",
        "section_header": f"Section {idx}",
        "chunk_index": idx,
        "content_hash": content_hash,
        "has_code": False,
        "char_count": len(content),
        "content": content,
    }


# ── Library CRUD ────────────────────────────────────────────────────────────


def test_create_and_get_library():
    s = _fresh_store()
    lib = s.create_library("lib_a", "Library A", description="desc", seed_url="https://x/a")
    assert lib["id"] == "lib_a"
    assert lib["name"] == "Library A"
    assert lib["chunk_count"] == 0
    assert lib["auto_inject"] is False
    fetched = s.get_library("lib_a")
    assert fetched and fetched["id"] == "lib_a"


def test_list_libraries_ordered():
    s = _fresh_store()
    s.create_library("b", "B")
    s.create_library("a", "A")
    ids = [l["id"] for l in s.list_libraries()]
    assert ids == ["b", "a"]  # by created_at, insertion order


def test_update_library_auto_inject_toggle():
    s = _fresh_store()
    s.create_library("lib", "L")
    updated = s.update_library("lib", auto_inject=True)
    assert updated["auto_inject"] is True
    assert s.get_library("lib")["auto_inject"] is True
    s.update_library("lib", auto_inject=False)
    assert s.get_library("lib")["auto_inject"] is False


def test_update_nonexistent_returns_none():
    s = _fresh_store()
    assert s.update_library("nope", name="x") is None


# ── Chunk upsert + dedup ────────────────────────────────────────────────────


def test_upsert_chunk_then_dedup_on_same_hash():
    s = _fresh_store()
    s.create_library("lib", "L")
    chunk = _chunk_dict("lib", "https://x/p", 0, "hello world")
    assert s.upsert_chunk(chunk) is True        # first insert
    assert s.upsert_chunk(chunk) is False        # same hash → no-op
    assert s.count_chunks("lib") == 1


def test_identical_content_on_different_pages_does_not_collide():
    """Regression: Meta pages share nav/footer chrome. Content-only chunk
    IDs made the second page's INSERT hit PRIMARY KEY UNIQUE and the whole
    page was counted as failed despite a successful fetch."""
    s = _fresh_store()
    s.create_library("lib", "L")
    shared = "WhatsApp Business Platform\n\nWas this helpful?\n"
    a = _chunk_dict("lib", "https://x/docs/a", 0, shared)
    b = _chunk_dict("lib", "https://x/docs/b", 0, shared)
    assert a["id"] != b["id"], "IDs must include source_url so shared chrome is unique per page"
    assert a["content_hash"] == b["content_hash"]
    assert s.upsert_chunk(a) is True
    assert s.upsert_chunk(b) is True  # must NOT raise / collide
    assert s.count_chunks("lib") == 2
    urls = {c["source_url"] for c in s.list_chunks("lib")}
    assert urls == {"https://x/docs/a", "https://x/docs/b"}


def test_upsert_replaces_when_content_changed():
    s = _fresh_store()
    s.create_library("lib", "L")
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 0, "old content"))
    # Same (library, source_url, chunk_index) but different content_hash.
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 0, "new content"))
    assert s.count_chunks("lib") == 1
    chunks = s.list_chunks("lib")
    assert chunks[0]["content"] == "new content"


def test_list_chunks_pagination():
    s = _fresh_store()
    s.create_library("lib", "L")
    for i in range(5):
        s.upsert_chunk(_chunk_dict("lib", "https://x/p", i, f"chunk {i} body {i}"))
    page1 = s.list_chunks("lib", limit=2, offset=0)
    page2 = s.list_chunks("lib", limit=2, offset=2)
    assert len(page1) == 2 and len(page2) == 2
    assert page1[0]["chunk_index"] == 0
    assert page2[0]["chunk_index"] == 2


def test_get_chunks_by_ids():
    s = _fresh_store()
    s.create_library("lib", "L")
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 0, "alpha"))
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 1, "beta"))
    all_chunks = s.list_chunks("lib")
    ids = [c["id"] for c in all_chunks]
    fetched = s.get_chunks_by_ids(ids)
    assert set(fetched.keys()) == set(ids)
    assert fetched[ids[0]]["content"] == "alpha"


def test_existing_hashes_map():
    s = _fresh_store()
    s.create_library("lib", "L")
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 0, "alpha"))
    h = s.existing_hashes("lib")
    assert len(h) == 1
    # The map is content_hash -> chunk_id.
    assert list(h.values())[0].startswith("lib:")


# ── FTS5 lexical search ─────────────────────────────────────────────────────


def test_fts_search_finds_keyword():
    s = _fresh_store()
    s.create_library("lib", "L")
    s.upsert_chunk(_chunk_dict(
        "lib", "https://x/p", 0,
        "Use a Bearer token in the Authorization header or you get 401.",
    ))
    s.upsert_chunk(_chunk_dict(
        "lib", "https://x/p2", 0,
        "Completely unrelated Instagram marketing content.",
    ))
    hits = s.fts_search("401 authentication token", "lib", limit=5)
    assert hits, "expected at least one FTS hit"
    # The auth chunk should be among the results.
    ids = [hid for hid, _score in hits]
    auth_chunk = s.list_chunks("lib", limit=100)
    auth_ids = [c["id"] for c in auth_chunk if "Bearer" in c["content"]]
    assert any(aid in ids for aid in auth_ids)


def test_fts_search_library_scoped():
    s = _fresh_store()
    s.create_library("lib_a", "A")
    s.create_library("lib_b", "B")
    s.upsert_chunk(_chunk_dict("lib_a", "https://x/a", 0, "whatsapp message endpoint"))
    s.upsert_chunk(_chunk_dict("lib_b", "https://x/b", 0, "whatsapp message endpoint"))
    # Searching lib_a must return only lib_a's chunk.
    hits_a = s.fts_search("whatsapp", "lib_a")
    all_chunks_a = {c["id"]: c for c in s.list_chunks("lib_a", limit=100)}
    for hid, _ in hits_a:
        assert hid in all_chunks_a


# ── Cascade delete ──────────────────────────────────────────────────────────


def test_delete_library_cascades_to_chunks_and_fts():
    s = _fresh_store()
    s.create_library("lib", "L")
    s.upsert_chunk(_chunk_dict("lib", "https://x/p", 0, "alpha beta gamma"))
    assert s.count_chunks("lib") == 1
    assert s.delete_library("lib") is True
    assert s.get_library("lib") is None
    assert s.count_chunks("lib") == 0
    # FTS5 row gone too — search returns nothing.
    assert s.fts_search("alpha", "lib") == []


# ── A deleted library's words leave the keyword index (KB-Q01) ──────────────
#
# FTS5 records a deleted row as a delete marker; its words stay in the index's
# older segments until a merge reaches them. The soak harness found every
# planted word of a deleted 10,000-chunk library still in
# ``knowledge_chunks_fts_data`` after the delete -- unsearchable, but on disk.

_GONE = ("qsecretterm", "ثخذضظغق")  # English, Arabic
_KEPT = ("zkeptterm", "بتثجحخد")


def _two_libraries(pages: int = 30) -> KnowledgeStore:
    s = _fresh_store()
    for lib, words in (("gone", _GONE), ("kept", _KEPT)):
        s.create_library(lib, lib)
        for page in range(pages):
            s.publish_page(lib, f"https://{lib}.example.test/p{page}", [
                {"content": f"Section {i} mentions {words[i % 2]}{page} and other words alpha beta",
                 "chunk_index": i} for i in range(10)])
    return s


def _held_in_index(s: KnowledgeStore, word: str) -> bool:
    """The word's bytes in the index's segments. A term is stored after the
    prefix it shares with the term before it, which for these words is at
    most their first letter: the rest is always there in full."""
    conn = s._get_conn()
    blob = b"".join(bytes(r[0]) for r in conn.execute("SELECT block FROM knowledge_chunks_fts_data") if r[0])
    return word[1:].encode() in blob


def test_a_deleted_librarys_words_leave_the_keyword_index():
    s = _two_libraries()
    try:
        assert all(_held_in_index(s, w) for w in _GONE)
        assert s.delete_library("gone") is True
        assert not any(_held_in_index(s, w) for w in _GONE)
        # The other library is whole and searchable, in both languages.
        assert all(_held_in_index(s, w) for w in _KEPT)
        assert s.fts_search("zkeptterm3", "kept") and s.fts_search(f"{_KEPT[1]}3", "kept")
        assert s.fts_coverage()["ok"] is True
    finally:
        s.close()


def test_the_purge_runs_in_bounded_steps(monkeypatch):
    """Each step is its own transaction; the store lock is free between them."""
    from kazma_core.stores import knowledge as km

    monkeypatch.setattr(km, "_PURGE_STEP_PAGES", 2)
    s = _two_libraries(pages=60)
    try:
        steps: list[dict] = []
        real = s.purge_search_index
        monkeypatch.setattr(s, "purge_search_index", lambda: steps.append(real()) or steps[-1])
        s.delete_library("gone")
        assert steps and steps[0]["steps"] > 2, steps
        assert not any(_held_in_index(s, w) for w in _GONE)
        assert s.fts_search("zkeptterm7", "kept")
    finally:
        s.close()


def test_without_the_purge_a_deleted_librarys_words_stay(monkeypatch):
    """Negative control: the delete as it was -- rows and markers only."""
    monkeypatch.setattr(KnowledgeStore, "purge_search_index", lambda self: {"steps": 0, "seconds": 0.0})
    s = _two_libraries()
    try:
        s.delete_library("gone")
        assert s.fts_search("qsecretterm3", "gone") == []  # unsearchable ...
        assert all(_held_in_index(s, w) for w in _GONE)  # ... and still on disk
    finally:
        s.close()


# ── The purge is checked, and outlives the process (KB-Q01, 2026-10-15) ─────
#
# The multi-day soak stopped at cycle 76 of its first run: 8 planted words of
# the library deleted in that cycle were held by delete markers in the
# index's oldest segment after the purge. FTS5 drops a marker only when a
# merge writes the oldest segment, judged by its place in the structure;
# with every segment on one level below the top it merges them one level up
# and keeps every marker (``tests/test_fts5_purge.py``). A store gets there
# by itself: each full merge adds two levels, and a library larger than the
# rest of the index pulls the older segment down beside its own.


def _words(tag: str, n: int) -> list[str]:
    rng = random.Random(tag)
    return ["zq" + "".join(rng.choice("bcdfghjklmnpqrstvwx") for _ in range(8)) for _ in range(n)]


def _publish(s: KnowledgeStore, lib: str, words: list[str]) -> None:
    s.create_library(lib, lib)
    s.publish_page(lib, f"https://{lib}.example.test/p0", [
        {"content": f"Section {i} mentions {w} and other words alpha beta", "chunk_index": i}
        for i, w in enumerate(words)])


def _store_at_one_level(db: str) -> tuple[KnowledgeStore, list[str], list[str]]:
    """A store whose next delete -- of library ``gone`` -- leaves every
    keyword segment on one level below the top: (store, gone's words, the
    kept library's words). Three small libraries deleted (three full merges)
    under a 400-chunk library, then a 1,200-chunk one."""
    from kazma_core.db.fts5_purge import _read_structure

    s = KnowledgeStore(db_path=db)
    kept = _words("kept", 400)
    _publish(s, "kept", kept)
    for n in range(3):
        _publish(s, f"small{n}", _words(f"small{n}", 2))
        s.delete_library(f"small{n}")
    gone = _words("gone", 1200)
    _publish(s, "gone", gone)
    structure = _read_structure(s._get_conn(), "knowledge_chunks_fts")
    populated = [n for n, level in enumerate(structure.levels) if level]
    assert len(populated) == 1 and populated[0] < len(structure.levels) - 2, structure
    return s, gone, kept


def _left_on_disk(db: str) -> set[str]:
    """Terms the keyword index's segments hold that no live row holds."""
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return held_only_by_deleted_rows(conn, "knowledge_chunks_fts")
    finally:
        conn.close()


def _in_segments(db: str) -> set[str]:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        return segment_terms(conn, "knowledge_chunks_fts")
    finally:
        conn.close()


def _purge_mark(db: str) -> str | None:
    conn = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT value FROM knowledge_meta WHERE key = 'keyword_purge_due'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def test_a_delete_leaves_no_word_when_every_segment_sits_on_one_level(tmp_path):
    db = str(tmp_path / "knowledge.db")
    s, gone, kept = _store_at_one_level(db)
    try:
        assert s.delete_library("gone") is True
        assert _left_on_disk(db) == set()
        assert not set(gone) & _in_segments(db)
        assert s.fts_search(kept[11], "kept") and s.fts_coverage()["ok"] is True
        assert _purge_mark(db) is None
    finally:
        s.close()


def test_the_merge_alone_left_the_deleted_librarys_words_on_one_level(tmp_path, monkeypatch):
    """Negative control: the purge of 01cba7cb -- the stepped merge with no
    check of the shape it left -- on the same store."""
    from kazma_core.stores import knowledge

    monkeypatch.setattr(knowledge, "drop_deleted_rows", lambda conn, table: "clean")
    db = str(tmp_path / "knowledge.db")
    s, gone, _kept = _store_at_one_level(db)
    try:
        s.delete_library("gone")
        assert s.fts_search(gone[5], "gone") == []  # unsearchable ...
        assert set(gone) <= _left_on_disk(db)  # ... and every word still on disk
    finally:
        s.close()


def test_a_purge_keeps_the_mark_of_a_delete_made_while_it_ran(tmp_path, monkeypatch):
    """A delete marked between two steps of the purge (another process) is
    not cleared by it: its own purge, or the next open, still runs."""
    from kazma_core.stores import knowledge

    db = str(tmp_path / "knowledge.db")
    s = KnowledgeStore(db_path=db)
    try:
        _publish(s, "a", _words("a", 50))
        _publish(s, "b", _words("b", 50))

        def another_delete(point: str) -> None:
            if point == "purge_step":
                other = sqlite3.connect(db, isolation_level=None)
                try:
                    KnowledgeStore._mark_purge_due(other)
                finally:
                    other.close()

        monkeypatch.setattr(knowledge, "_FAULT", another_delete)
        s.delete_library("a")
        assert _purge_mark(db) is not None  # the later mark survived this purge
        monkeypatch.setattr(knowledge, "_FAULT", None)
        assert s.finish_pending_purge() is not None
        assert _purge_mark(db) is None and _left_on_disk(db) == set()
    finally:
        s.close()


_KILL_CHILD = r"""
import os, sys
sys.path.insert(0, {core!r})
from kazma_core.stores import knowledge
knowledge._DELETE_BATCH = 300
knowledge._PURGE_STEP_PAGES = 2
if {without_mark!r}:  # the store as it was: no record of the purge it owes
    knowledge.KnowledgeStore._mark_purge_due = staticmethod(lambda conn: None)
store = knowledge.KnowledgeStore(db_path={db!r})
def fault(at):
    if at == {point!r}:
        os._exit(17)
knowledge._FAULT = fault
store.delete_library("gone")
os._exit(0)
"""


def _kill_during_delete(db: str, point: str, *, without_mark: bool = False) -> None:
    """A real process deleting library ``gone``, killed at *point*."""
    from kazma_core.security.process_budget import run_bounded

    core = str(Path(__file__).resolve().parents[1] / "kazma-core")
    allowed = {"systemroot", "windir", "temp", "tmp", "path", "kazma_data_dir"}
    result = run_bounded(
        [sys.executable, "-I", "-c", _KILL_CHILD.format(core=core, db=db, point=point, without_mark=without_mark)],
        env={k: v for k, v in os.environ.items() if k.lower() in allowed},
        timeout=120, output_bytes=32768, memory_bytes=1024**3)
    assert result.returncode == 17, result.stderr


@pytest.mark.parametrize("point", ["delete_batch", "library_row_deleted", "purge_step", "purge_checked"])
def test_a_process_killed_before_its_purge_ended_leaves_it_to_the_next_open(tmp_path, point):
    """Killed after a batch of the delete, after the library row, between
    two merge steps, and inside the last transaction (the rebuild rolled
    back): the next open finishes the purge, and no deleted word stays."""
    db = str(tmp_path / "knowledge.db")
    s, gone, kept = _store_at_one_level(db)
    s.close()
    _kill_during_delete(db, point)
    assert _left_on_disk(db)  # the kill left words of deleted rows in the segments
    assert _purge_mark(db) is not None
    reopened = KnowledgeStore(db_path=db)  # the next open
    try:
        assert _left_on_disk(db) == set()
        assert _purge_mark(db) is None
        if point == "delete_batch":  # the library is still there: the operator deletes it again
            assert reopened.library_exists("gone")
            assert reopened.delete_library("gone") is True
        assert not set(gone) & _in_segments(db)
        assert reopened.fts_search(kept[3], "kept") and reopened.fts_coverage()["ok"] is True
    finally:
        reopened.close()


def test_without_the_mark_a_killed_delete_left_its_words_for_good(tmp_path):
    """Negative control: the store as it was -- nothing recorded the purge
    a delete owed, so a process killed before it left the words on disk
    through every later open."""
    db = str(tmp_path / "knowledge.db")
    s, gone, _kept = _store_at_one_level(db)
    s.close()
    _kill_during_delete(db, "library_row_deleted", without_mark=True)
    reopened = KnowledgeStore(db_path=db)
    try:
        assert not reopened.library_exists("gone")
        assert set(gone) <= _left_on_disk(db)
    finally:
        reopened.close()


def test_the_maintenance_pass_finishes_a_purge_a_killed_delete_left(tmp_path, monkeypatch):
    """The other way back: a store opened while the purge could not run,
    then the maintenance pass (``run_repair_sweep``) finishes it."""
    from kazma_core.stores import knowledge_index as ki

    db = str(tmp_path / "knowledge.db")
    s, gone, _kept = _store_at_one_level(db)
    s.close()
    _kill_during_delete(db, "library_row_deleted")
    real = KnowledgeStore.finish_pending_purge
    monkeypatch.setattr(KnowledgeStore, "finish_pending_purge", lambda self: None)  # the open could not run it
    reopened = KnowledgeStore(db_path=db)
    monkeypatch.setattr(KnowledgeStore, "finish_pending_purge", real)
    try:
        assert set(gone) <= _left_on_disk(db)
        index = types.SimpleNamespace(
            store=reopened, _persist_dir=None,
            backfill_vectors=lambda time_budget_s: {"unavailable": False, "failed": {}, "embedded": 0,
                                                    "removed": 0, "missing": 0, "complete": True})
        monkeypatch.setattr(ki, "get_knowledge_index", lambda: index)
        monkeypatch.setattr(ki, "_SWEEP", {"report": None, "unavailable": False, "bad_passes": 0, "at": None})
        report = ki.run_repair_sweep(time_budget_s=1)
        assert report["keyword_purge"]["rebuilt"] is True
        assert _left_on_disk(db) == set() and _purge_mark(db) is None
        assert ki.run_repair_sweep(time_budget_s=1)["keyword_purge"] is None  # nothing owed now
    finally:
        reopened.close()
