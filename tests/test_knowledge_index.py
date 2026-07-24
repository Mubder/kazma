"""Tests for the KnowledgeIndex retrieval engine (RRF + library isolation).

These tests exercise the SQLite + FTS5 path deterministically.  ChromaDB
is optional: when present the semantic layer also runs; when absent the
tests still pass on the lexical-only path (graceful degradation is part
of the contract).
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))

from kazma_core.stores.knowledge import (
    KnowledgeStore,
    get_knowledge_store,
    reset_knowledge_store,
)
from kazma_core.stores.knowledge_chunker import chunk_to_dict
from kazma_core.stores.knowledge_index import KnowledgeIndex, reset_knowledge_index


# The auto-inject getter reaches into the store singleton, so the temp DB
# must be wired into BOTH the local store and the singleton.  We do that by
# importing the singleton module globals and pointing them at our temp store.
import kazma_core.stores.knowledge as _kb_module


def _setup():
    """Fresh store + index against a temp DB."""
    tmp = tempfile.mkdtemp(prefix="kazma_kbi_test_")
    os.environ["KAZMA_PROJECT_ROOT"] = tmp
    # Reset BOTH singletons — the auto-inject getter uses the store singleton.
    reset_knowledge_index()
    reset_knowledge_store()
    store = KnowledgeStore(db_path=os.path.join(tmp, "settings.db"))
    # Make this temp store the process singleton so the getter sees it.
    _kb_module._knowledge_store = store
    store.create_library("lib_a", "A")
    store.create_library("lib_b", "B")
    index = KnowledgeIndex(store=store)
    return store, index


WHATSAPP_DOC = """# WhatsApp Cloud API — Messages

## Send a text message

To send a text message, POST to /v18.0/{phone_number_id}/messages with
`messaging_product` set to `whatsapp` and a `text` object containing the
message body. The recipient phone number goes in the `to` field in
E.164 format.

```json
{
  "messaging_product": "whatsapp",
  "to": "15551234567",
  "type": "text",
  "text": {"body": "Hello from ShipX"}
}
```

### Authentication

Every request must include a Bearer token in the `Authorization` header.
A missing or expired token returns HTTP 401 with error code 401 and a
message indicating the access token is invalid.
"""

INSTAGRAM_DOC = """# Instagram Graph API

## Post a photo

POST to /{ig-user-id}/media with image_url and caption to create a
container, then POST to /{ig-user-id}/media_publish to publish it.
"""


def _ingest(index: KnowledgeIndex, library_id: str, md: str, url: str):
    from kazma_core.stores.knowledge_chunker import chunk_markdown_doc
    chunks = chunk_markdown_doc(md, source_url=url, library_id=library_id)
    index.index(library_id, [chunk_to_dict(c) for c in chunks])


# ── Round-trip + isolation ──────────────────────────────────────────────────


def test_search_returns_relevant_chunk_with_citation():
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa/messages")
    hits = asyncio.run(index.search("401 expired token authentication", "lib_a", top_k=3))
    assert hits, "expected hits"
    top = hits[0]
    # The hit carries provenance for citation.
    assert top.source_url == "https://x/wa/messages"
    assert "Authentication" in top.section_header or "Authentication" in top.content
    assert top.library_id == "lib_a"


def test_library_isolation_query_never_returns_other_library():
    """The headline isolation contract: a query against lib_a must not return
    chunks from lib_b, even when lib_b contains matching terms."""
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    _ingest(index, "lib_b", INSTAGRAM_DOC, "https://x/ig")
    # Search lib_b for something only lib_a knows about.
    hits = asyncio.run(index.search("whatsapp bearer token 401", "lib_b", top_k=5))
    for h in hits:
        assert h.library_id == "lib_b"
    # And the inverse.
    hits_a = asyncio.run(index.search("instagram photo publish", "lib_a", top_k=5))
    for h in hits_a:
        assert h.library_id == "lib_a"


def test_reingest_same_doc_dedups():
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    first_count = store.count_chunks("lib_a")
    # Ingest identical content again.
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    assert store.count_chunks("lib_a") == first_count  # no growth


def test_cross_library_search_returns_per_library():
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    _ingest(index, "lib_b", INSTAGRAM_DOC, "https://x/ig")
    out = asyncio.run(index.search_across("endpoint", ["lib_a", "lib_b"], top_k=3))
    assert set(out.keys()) == {"lib_a", "lib_b"}
    assert isinstance(out["lib_a"], list)


def test_search_all_fuses_libraries_into_one_ranking():
    """The Phase 2 cross-library RRF path: a single fused ranking, not a
    dict of per-library lists.  Hits keep their library_id provenance."""
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    _ingest(index, "lib_b", INSTAGRAM_DOC, "https://x/ig")
    hits = asyncio.run(index.search_all("api endpoint post", ["lib_a", "lib_b"], top_k=5))
    assert isinstance(hits, list)
    assert len(hits) > 0
    # Provenance preserved.
    lib_ids = {h.library_id for h in hits}
    assert lib_ids.issubset({"lib_a", "lib_b"})
    # Sorted best-first by fused RRF score.
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True)


def test_delete_library_removes_chunks_and_isolates():
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    _ingest(index, "lib_b", INSTAGRAM_DOC, "https://x/ig")
    assert index.delete_library("lib_a") is True
    assert store.get_library("lib_a") is None
    assert store.count_chunks("lib_a") == 0
    # lib_b unaffected.
    assert store.get_library("lib_b") is not None
    assert store.count_chunks("lib_b") > 0


def test_search_empty_query_returns_empty():
    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    assert asyncio.run(index.search("", "lib_a")) == []
    assert asyncio.run(index.search("   ", "lib_a")) == []


# ── Auto-inject getter (Phase 2) ────────────────────────────────────────────


def test_auto_inject_off_by_default_no_opt_in():
    """With no library opted in, the getter returns "" even when the kill
    switch is on — behaviour is strictly opt-in per library."""
    from kazma_core.stores.knowledge_index import (
        get_knowledge_auto_inject_block,
        kb_auto_inject_enabled,
    )

    assert kb_auto_inject_enabled() is True  # default ON
    _setup()  # fresh libs, none have auto_inject=1
    assert asyncio.run(get_knowledge_auto_inject_block("whatsapp 401")) == ""


def test_auto_inject_returns_block_when_library_opted_in():
    from kazma_core.stores.knowledge_index import get_knowledge_auto_inject_block

    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    store.update_library("lib_a", auto_inject=True)
    block = asyncio.run(get_knowledge_auto_inject_block("whatsapp 401 token"))
    assert block, "expected non-empty auto-inject block"
    # Provenance is mandatory in the injected block (for citation).
    assert "https://x/wa" in block


def test_auto_inject_kill_switch_disables_everything():
    """KAZMA_KB_AUTO_INJECT=0 must short-circuit even when a library is
    opted in — checked live, per call."""
    import os

    from kazma_core.stores.knowledge_index import (
        get_knowledge_auto_inject_block,
        kb_auto_inject_enabled,
    )

    store, index = _setup()
    _ingest(index, "lib_a", WHATSAPP_DOC, "https://x/wa")
    store.update_library("lib_a", auto_inject=True)

    old = os.environ.get("KAZMA_KB_AUTO_INJECT")
    try:
        os.environ["KAZMA_KB_AUTO_INJECT"] = "0"
        assert kb_auto_inject_enabled() is False
        assert asyncio.run(get_knowledge_auto_inject_block("whatsapp 401")) == ""
    finally:
        if old is None:
            os.environ.pop("KAZMA_KB_AUTO_INJECT", None)
        else:
            os.environ["KAZMA_KB_AUTO_INJECT"] = old


def test_auto_inject_empty_message_returns_empty():
    from kazma_core.stores.knowledge_index import get_knowledge_auto_inject_block

    _setup()
    assert asyncio.run(get_knowledge_auto_inject_block("")) == ""
    assert asyncio.run(get_knowledge_auto_inject_block("   ")) == ""
