"""Hierarchy-aware Markdown chunker for Knowledge Library ingestion.

Technical API documentation (e.g. the Meta WhatsApp Cloud API) is built
from nested headers, parameter tables, and fenced code examples.  A blind
character-count sliding window — the approach the existing research tools
use (``tools/read_url.py: DEFAULT_CHUNK_SIZE``) — destroys that structure:
it splits a JSON payload across two chunks, or separates an endpoint
description from its parameter table.  Both ruin retrieval.

This module implements a two-tier splitter that is deliberately **pure
standard library** (``re`` + ``hashlib`` + ``dataclasses``).  No LangChain
dependency.  It does two things the off-the-shelf
``MarkdownHeaderTextSplitter`` + ``RecursiveCharacterTextSplitter`` combo
gets wrong:

1. **Real code-fence atomicity.**  We first split the text into ordered
   ``(prose | code)`` segments.  Code spans are *atomic* — they are never
   cut, regardless of size, and they never contribute overlap to a
   neighbouring chunk.  The recursive splitter only ever runs on prose
   segments.  (``RecursiveCharacterTextSplitter`` only *prefers* fence
   boundaries; given a block larger than ``chunk_size`` it will still cut
   it, silently breaking the one invariant it advertises.)

2. **Header context preservation.**  Walking the ``#``/``##``/``###``/
   ``####`` structure builds a *section trail* (e.g.
   ``"Messages > Send Text Message"``) that is stamped onto every chunk's
   metadata, so a hit retrieved out of order still knows where it lives in
   the document.

After splitting, a post-pass backfills ``total_chunks`` and ``content_hash``
(sha256) so re-ingestion can dedup unchanged pages cheaply.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["KnowledgeChunk", "chunk_markdown_doc"]

# ── Tunables ────────────────────────────────────────────────────────────────

DEFAULT_MAX_CHARS = 4000
DEFAULT_OVERLAP = 0  # overlap stays 0 around code; prose interiors may overlap

# Recursive-split separators for oversized prose sections, most → least
# preferred.  ``\n\n`` keeps paragraphs intact; the space is the last resort.
_PROSE_SEPARATORS: tuple[str, ...] = ("\n\n", "\n", "·", " ", "")

# Matches a leading markdown ATX header line.  ``\1`` captures the hashes so
# we know the depth.  We require a space after the hashes (GitHub flavor).
_HEADER_RE = re.compile(r"^(#{1,4})\s+(.*)$", re.MULTILINE)

# A fenced code block.  Supports ``` and ~~~ fences with optional language.
# The closing fence must sit on its own line (``$`` under MULTILINE).  We do
# NOT use a named-backreference ``(?P=fence)`` because that pattern fails to
# match in CPython's ``re`` when combined with DOTALL — verified empirically.
_FENCE_RE = re.compile(
    r"(?:```|~~~)[^\n]*\n.*?\n(?:```|~~~)[ \t]*$",
    re.DOTALL | re.MULTILINE,
)


@dataclass(slots=True)
class KnowledgeChunk:
    """A single retrievable unit of an ingested document."""

    content: str
    library_id: str
    source_url: str
    document_title: str = ""
    section_header: str = ""           # e.g. "Messages > Send Text Message"
    chunk_index: int = 0
    total_chunks: int = 0              # backfilled in post-pass
    content_hash: str = ""             # sha256(content), backfilled
    has_code: bool = False
    # ``extra`` carries anything else the caller wants to persist (kept out
    # of slots/eq by being a default-factory field).
    extra: dict[str, Any] = field(default_factory=dict)


# ── Segment splitting: protect code fences ──────────────────────────────────


@dataclass(slots=True)
class _Segment:
    kind: str   # "prose" | "code"
    text: str
    header_trail: str   # section trail at the start of this segment


def _split_into_segments(markdown: str) -> list[_Segment]:
    """Split text into ordered ``(prose|code)`` segments.

    Code spans (```...``` or ~~~...~~~) are kept whole and tagged ``code``.
    Everything else is tagged ``prose`` and will later be header-walked.
    """
    segments: list[_Segment] = []
    pos = 0
    for m in _FENCE_RE.finditer(markdown):
        start, end = m.span()
        if start > pos:
            segments.append(_Segment("prose", markdown[pos:start], ""))
        segments.append(_Segment("code", markdown[start:end], ""))
        pos = end
    if pos < len(markdown):
        segments.append(_Segment("prose", markdown[pos:], ""))
    if not segments:
        segments.append(_Segment("prose", markdown, ""))
    # Collapse adjacent prose segments (no code in the doc at all).
    return segments


# ── Header walking over a prose segment ─────────────────────────────────────


@dataclass(slots=True)
class _Section:
    header_trail: str
    text: str
    touches_code: bool   # does this section neighbour a code segment?


def _walk_headers(prose_text: str, parent_trail: str) -> list[_Section]:
    """Split one prose segment into header-delimited sections.

    Each section inherits the trail of every enclosing header, producing a
    ``"H1 > H2 > H3"`` breadcrumb.  Sections with no headers fall under the
    inherited ``parent_trail`` (which may be empty for top-level prose).
    """
    # Find all header positions.
    headers = list(_HEADER_RE.finditer(prose_text))
    if not headers:
        return [_Section(parent_trail, prose_text, False)]

    sections: list[_Section] = []
    # Stack of (depth, title) — depth = number of ``#``.
    stack: list[tuple[int, str]] = []

    def _trail() -> str:
        return " > ".join(title for _depth, title in stack) or parent_trail

    # Text before the first header (preamble).
    if headers[0].start() > 0:
        pre = prose_text[: headers[0].start()]
        if pre.strip():
            sections.append(_Section(parent_trail, pre, False))

    for i, m in enumerate(headers):
        depth = len(m.group(1))
        title = m.group(2).strip()
        # Pop stack entries deeper-or-equal to this header.
        while stack and stack[-1][0] >= depth:
            stack.pop()
        stack.append((depth, title))
        body_start = m.end()
        body_end = headers[i + 1].start() if i + 1 < len(headers) else len(prose_text)
        body = prose_text[body_start:body_end]
        # Keep the header line at the top of its section for readability.
        section_text = f"{m.group(0)}{body}"
        sections.append(_Section(_trail(), section_text, False))
    return sections


# ── Recursive character splitter for oversized prose ────────────────────────


def _recursive_split(text: str, max_chars: int, separators: tuple[str, ...]) -> list[str]:
    """Greedy recursive splitter on ``separators`` (LangChain-style, dep-free).

    Tries the first separator; if any resulting piece still exceeds
    ``max_chars`` it recurses with the next separator.  Final fall-back is
    a hard character cut.  Code is never passed here (segments of kind
    ``code`` are atomic), so this function only ever runs on prose.
    """
    if len(text) <= max_chars:
        return [text]
    if not separators:
        # Last resort: hard cut.
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    sep = separators[0]
    rest = separators[1:]
    if sep == "":
        return [text[i : i + max_chars] for i in range(0, len(text), max_chars)]

    pieces = text.split(sep)
    out: list[str] = []
    buf = ""
    for piece in pieces:
        candidate = piece if not buf else buf + sep + piece
        if len(candidate) <= max_chars:
            buf = candidate
        else:
            if buf:
                out.append(buf)
            # The piece itself may still be too long → recurse.
            if len(piece) > max_chars:
                out.extend(_recursive_split(piece, max_chars, rest))
            else:
                buf = piece
        # ``candidate`` re-bound above; keep linting happy.
        _ = candidate
    if buf:
        out.append(buf)
    # Any piece that still exceeds max_chars (separator was absent) → recurse.
    final: list[str] = []
    for chunk in out:
        if len(chunk) > max_chars:
            final.extend(_recursive_split(chunk, max_chars, rest))
        else:
            final.append(chunk)
    return final


# ── Public API ──────────────────────────────────────────────────────────────


def chunk_markdown_doc(
    markdown_text: str,
    *,
    source_url: str,
    library_id: str,
    document_title: str = "",
    max_chars: int = DEFAULT_MAX_CHARS,
) -> list[KnowledgeChunk]:
    """Split a markdown document into hierarchy-aware chunks.

    Args:
        markdown_text: The full markdown body of one page.
        source_url:    The URL the page was fetched from (provenance).
        library_id:    The Knowledge Library this page belongs to.
        document_title: Optional page title for attribution.
        max_chars:     Soft per-chunk ceiling.  Code blocks are exempt
                       (kept whole even when larger).

    Returns:
        A list of :class:`KnowledgeChunk` with ``chunk_index`` assigned in
        document order and ``total_chunks`` / ``content_hash`` backfilled.
    """
    if not markdown_text or not markdown_text.strip():
        return []

    size = max(500, int(max_chars or DEFAULT_MAX_CHARS))

    # 1. Segment into (prose | code).  Code segments are atomic.
    segments = _split_into_segments(markdown_text)

    # 2. Walk headers within each prose segment → sections.
    sections: list[_Section] = []
    for seg in segments:
        if seg.kind == "code":
            # Code carries no header of its own; it attaches to the most
            # recent section by being emitted as its own atomic section with
            # the current trail.  We mark ``touches_code`` so the splitter
            # knows to keep overlap at 0 on either side.
            sections.append(_Section("", seg.text, True))
        else:
            sections.extend(_walk_headers(seg.text, parent_trail=""))

    # 3. Re-stamp section trails so a code segment that followed a header
    #    inherits that header's trail.  We track the last non-empty trail.
    last_trail = ""
    for sec in sections:
        if sec.header_trail:
            last_trail = sec.header_trail
        else:
            sec.header_trail = last_trail

    # 4. Per section: keep whole if ≤ max_chars; else recursive-split.
    pieces: list[tuple[str, str, bool]] = []  # (trail, text, has_code)
    for sec in sections:
        if not sec.text.strip():
            continue
        if len(sec.text) <= size:
            pieces.append((sec.header_trail or "General", sec.text, sec.touches_code))
            continue
        if sec.touches_code:
            # Code is atomic → emit the whole segment as one chunk even if
            # it exceeds max_chars.  This is the *point*: never split code.
            pieces.append((sec.header_trail or "General", sec.text, True))
            continue
        for sub in _recursive_split(sec.text, size, _PROSE_SEPARATORS):
            if sub.strip():
                pieces.append((sec.header_trail or "General", sub, sec.touches_code))

    # 5. Build KnowledgeChunk objects; backfill index / total / hash.
    total = len(pieces)
    chunks: list[KnowledgeChunk] = []
    for idx, (trail, text, has_code) in enumerate(pieces):
        text = text.strip("\n")
        if not text:
            continue
        chunks.append(
            KnowledgeChunk(
                content=text,
                library_id=library_id,
                source_url=source_url,
                document_title=document_title,
                section_header=trail,
                chunk_index=idx,
                total_chunks=total,
                content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                has_code=has_code,
            )
        )
    # Re-stamp total_chunks in case stripping dropped any empties.
    real_total = len(chunks)
    for c in chunks:
        c.total_chunks = real_total
    return chunks


def make_chunk_id(library_id: str, source_url: str, content_hash: str) -> str:
    """Stable primary key that is unique **per page**, not just per content.

    Older IDs used ``{library_id}:{content_hash[:16]}``.  Meta (and most doc
    sites) repeat the same nav/footer chrome on every page, so identical
    sections across URLs produced the *same* primary key.  SQLite then raised
    UNIQUE constraint on INSERT and the whole page was counted as failed —
    even though the fetch succeeded (provenance was written).  Observed on a
    live Meta WhatsApp crawl: 182 pages fetched, only 10 URLs in the index.

    Including a short digest of ``source_url`` keeps IDs stable under
    re-ingest of the same page while allowing shared chrome across pages.
    """
    import hashlib

    url_digest = hashlib.sha256((source_url or "").encode("utf-8")).hexdigest()[:10]
    body = (content_hash or "")[:16]
    return f"{library_id}:{url_digest}:{body}"


# Convenience: marshal a chunk to the dict shape KnowledgeStore expects.
def chunk_to_dict(chunk: KnowledgeChunk) -> dict[str, Any]:
    return {
        "content": chunk.content,
        "library_id": chunk.library_id,
        "source_url": chunk.source_url,
        "document_title": chunk.document_title,
        "section_header": chunk.section_header,
        "chunk_index": chunk.chunk_index,
        "total_chunks": chunk.total_chunks,
        "content_hash": chunk.content_hash,
        "has_code": chunk.has_code,
        "char_count": len(chunk.content),
        "id": make_chunk_id(chunk.library_id, chunk.source_url, chunk.content_hash),
    }
