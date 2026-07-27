"""Tests for the KnowledgeStore SQLite layer.

Covers: library CRUD, chunk upsert with dedup, FTS5 lexical search,
cascade delete.  Uses a temp DB so the real settings.db is untouched.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))

from kazma_core.stores.knowledge import KnowledgeStore, slugify_library_id


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
