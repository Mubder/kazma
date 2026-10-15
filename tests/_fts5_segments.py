"""What an FTS5 index's segments hold, read from its leaf pages.

``fts5vocab`` lists the terms of rows that are live; a term held only by
delete markers -- a deleted row's word FTS5 has not merged away -- is not in
it, and is still on disk in ``<table>_data``. These helpers read every term
of every leaf page (the page footer gives each term's offset; the first term
of a page is stored whole, the others after the prefix they share with the
term before), so a test can say exactly which words are held only by
deleted rows. Used by ``tests/test_fts5_purge.py`` and
``tests/test_knowledge_store.py`` (KB-Q01).
"""

from __future__ import annotations

import sqlite3


def _varint(blob: bytes, i: int) -> tuple[int, int]:
    value = 0
    for k in range(8):
        byte = blob[i + k]
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i + k + 1
    return (value << 8) | blob[i + 8], i + 9


def _leaf_terms(page: bytes) -> list[bytes]:
    footer = int.from_bytes(page[2:4], "big")
    offsets, i, at = [], footer, 0
    while i < len(page):
        delta, i = _varint(page, i)
        at += delta
        offsets.append(at)
    terms: list[bytes] = []
    previous = b""
    for n, offset in enumerate(offsets):
        if n == 0:
            size, j = _varint(page, offset)
            term = page[j:j + size]
        else:
            shared, j = _varint(page, offset)
            size, j = _varint(page, j)
            term = previous[:shared] + page[j:j + size]
        terms.append(term)
        previous = term
    return terms


def segment_terms(conn: sqlite3.Connection, table: str) -> set[str]:
    """Every term the leaf pages of *table*'s index hold (main index only)."""
    out: set[str] = set()
    for rowid, block in conn.execute(f'SELECT id, block FROM "{table}_data" WHERE id > 10'):
        segment, dlidx, height = rowid >> 37, (rowid >> 36) & 1, (rowid >> 31) & 0x1F
        if dlidx or height or segment >= 1 << 16 or not block:  # doclist index, b-tree node, tombstone
            continue
        for term in _leaf_terms(bytes(block)):
            if term[:1] == b"0":
                out.add(term[1:].decode("utf-8", "replace"))
    return out


def live_terms(conn: sqlite3.Connection, table: str) -> set[str]:
    """The terms of live rows, as FTS5 itself reports them."""
    conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS temp.vocab_{table} USING fts5vocab(main, {table}, 'row')")
    try:
        return {str(r[0]) for r in conn.execute(f"SELECT term FROM temp.vocab_{table}")}
    finally:
        conn.execute(f"DROP TABLE temp.vocab_{table}")


def held_only_by_deleted_rows(conn: sqlite3.Connection, table: str) -> set[str]:
    """Terms on disk in the segments that no live row holds."""
    return segment_terms(conn, table) - live_terms(conn, table)
