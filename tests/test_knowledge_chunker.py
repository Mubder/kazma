"""Tests for the hierarchy-aware markdown chunker.

The headline guarantee is **code-fence atomicity**: an oversized fenced
code block must never be split, regardless of ``max_chars``.  This is the
single invariant the off-the-shelf ``RecursiveCharacterTextSplitter``-
based spec (rejected) silently failed to deliver.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make kazma_core importable when running from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "kazma-core"))

from kazma_core.stores.knowledge_chunker import (
    KnowledgeChunk,
    chunk_markdown_doc,
    chunk_to_dict,
)


# ── Code-fence atomicity ────────────────────────────────────────────────────


def test_oversized_code_block_stays_atomic():
    """A code block larger than max_chars must be emitted as ONE chunk."""
    big_payload = "\n".join(
        f'    "key_{i}": "value_{i} padded to enlarge the block",' for i in range(400)
    )
    md = f"""# Messages API

## Send Text Message

Intro prose.

```json
{{
{big_payload}
}}
```

### After

Tail.
"""
    chunks = chunk_markdown_doc(
        md, source_url="https://x.com/p", library_id="lib", max_chars=500
    )
    code_chunks = [c for c in chunks if c.has_code]
    assert len(code_chunks) == 1, f"expected 1 atomic code chunk, got {len(code_chunks)}"
    # The single code chunk should be far larger than max_chars (kept whole).
    assert len(code_chunks[0].content) > 500
    # … and it must contain both fences intact.
    assert code_chunks[0].content.count("```") == 2


def test_multiple_code_blocks_each_atomic():
    md = """# Sample

```python
%s
```

Then:

```js
%s
```
""" % ("\n".join(f"# line {i}" for i in range(300)),
       "\n".join(f"// line {i}" for i in range(300)))
    chunks = chunk_markdown_doc(md, source_url="u", library_id="lib", max_chars=400)
    code_chunks = [c for c in chunks if c.has_code]
    assert len(code_chunks) == 2
    for c in code_chunks:
        assert c.content.count("```") == 2


# ── Backfilled fields ───────────────────────────────────────────────────────


def test_total_chunks_backfilled_consistently():
    md = "# H1\n\npara1\n\n## H2\n\npara2\n\n## H3\n\npara3\n"
    chunks = chunk_markdown_doc(md, source_url="u", library_id="lib")
    totals = {c.total_chunks for c in chunks}
    assert len(totals) == 1
    assert next(iter(totals)) == len(chunks)


def test_content_hash_stable_and_unique():
    md = "# H1\n\nA\n\n## H2\n\nB\n"
    chunks = chunk_markdown_doc(md, source_url="u", library_id="lib")
    assert all(len(c.content_hash) == 64 for c in chunks)  # sha256 hex
    assert len({c.content_hash for c in chunks}) == len(chunks)
    # Re-chunking the same doc is deterministic.
    again = chunk_markdown_doc(md, source_url="u", library_id="lib")
    assert [c.content_hash for c in chunks] == [c.content_hash for c in again]


def test_chunk_to_dict_shape():
    md = "# H\n\nbody\n"
    chunks = chunk_markdown_doc(md, source_url="https://x/y", library_id="lib")
    d = chunk_to_dict(chunks[0])
    assert d["library_id"] == "lib"
    assert d["source_url"] == "https://x/y"
    assert d["id"].startswith("lib:")
    assert d["char_count"] == len(chunks[0].content)
    assert "content_hash" in d and d["content_hash"]


def test_chunk_ids_differ_across_pages_with_same_body():
    """Shared nav/footer must not produce the same PRIMARY KEY for two URLs."""
    from kazma_core.stores.knowledge_chunker import make_chunk_id

    md = "# Title\n\nSame body on every page.\n"
    a = chunk_to_dict(chunk_markdown_doc(md, source_url="https://x/a", library_id="lib")[0])
    b = chunk_to_dict(chunk_markdown_doc(md, source_url="https://x/b", library_id="lib")[0])
    assert a["content_hash"] == b["content_hash"]
    assert a["id"] != b["id"]
    # Explicit helper contract.
    assert make_chunk_id("lib", "https://x/a", a["content_hash"]) == a["id"]
    assert make_chunk_id("lib", "https://x/b", b["content_hash"]) == b["id"]


# ── Header walking ──────────────────────────────────────────────────────────


def test_section_header_trail_is_breadcrumb():
    md = """# Messages

## Send

### Errors

Content here.
"""
    chunks = chunk_markdown_doc(md, source_url="u", library_id="lib")
    # The deepest section's chunk should carry the full breadcrumb.
    deepest = [c for c in chunks if "Errors" in c.section_header]
    assert deepest, f"no Errors trail found: {[c.section_header for c in chunks]}"
    trail = deepest[0].section_header
    assert "Messages" in trail and "Send" in trail and "Errors" in trail
    assert ">" in trail  # breadcrumb separator


def test_no_headers_falls_under_general():
    chunks = chunk_markdown_doc("just prose, no headers at all", source_url="u", library_id="lib")
    assert len(chunks) >= 1
    assert chunks[0].section_header  # non-empty (defaults to "General")


def test_empty_input_returns_empty():
    assert chunk_markdown_doc("", source_url="u", library_id="lib") == []
    assert chunk_markdown_doc("   \n\n  ", source_url="u", library_id="lib") == []
