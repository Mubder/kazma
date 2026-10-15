"""Take deleted rows' words out of an FTS5 index, for certain.

FTS5 deletes a row by writing a *delete marker* into a new segment; the
row's words stay in the older segments, and every marker carries the word it
cancels. A merge drops a marker and the entry it cancels only when the
segment it writes is the oldest in the index, and FTS5 decides that from the
segment's place: the only segment on the structure's last level
(``bOldest`` in ``fts5_index.c``).

``optimize`` and ``merge`` with a negative argument move every segment onto
a new top level first -- but only when the segments sit on more than one
level. Each full merge adds two levels to the structure (up to 64), and a
new segment at least as large as an older one pulls it down beside itself.
When every segment sits on one level below the top, both commands merge that
level one level up, into a segment that is not the oldest, and keep every
delete marker, word included. The KB-Q01 soak found it on 2026-10-15: eight
planted words of a library deleted at cycle 76 were held by delete markers
in the store's oldest segment after ``purge_search_index`` had run.

So a purge is checked by the shape it leaves: no segment, or one segment on
the last level, is a shape in which FTS5 has dropped every marker (that
segment was written as the oldest; :func:`_holds_no_deleted_rows`). Any
other shape -- or a structure
record this code cannot read -- is rebuilt from the table's own content
(FTS5 ``rebuild``: a new index with no deleted row in it). The record is
FTS5's own (row 10 of ``<table>_data``), read with the layout
``fts5StructureDecode`` reads.

Every FTS5 ``optimize`` and ``merge`` in product code goes through this
module or finishes with :func:`drop_deleted_rows`
(``tests/test_fts5_purge.py``).
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass

__all__ = [
    "Fts5Structure",
    "drop_deleted_rows",
    "purge_deleted_rows",
]

logger = logging.getLogger(__name__)

#: The row of ``<table>_data`` that holds the index's structure record.
_STRUCTURE_ROWID = 10

#: What FTS5 writes after the cookie of a version 2 structure record
#: (SQLite 3.43+, tables with tombstones).
_STRUCTURE_V2 = b"\xff\x00\x00\x01"

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class Fts5Structure:
    """An FTS5 index's segments, level by level: the page count of each."""

    levels: tuple[tuple[int, ...], ...]
    merging: bool  # a merge is in progress on some level
    tombstone_pages: int  # version 2 records only

    @property
    def segments(self) -> int:
        return sum(len(level) for level in self.levels)


def _table(name: str) -> str:
    if not _IDENTIFIER.fullmatch(name):
        raise ValueError(f"not an FTS5 table name: {name!r}")
    return name


def _varint(blob: bytes, i: int) -> tuple[int, int]:
    """SQLite's variable-length integer, as FTS5 writes it."""
    value = 0
    for k in range(8):
        byte = blob[i + k]
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, i + k + 1
    return (value << 8) | blob[i + 8], i + 9


def _read_structure(conn: sqlite3.Connection, table: str) -> Fts5Structure | None:
    """The structure of *table*'s index, or None when there is no record or
    it does not read as FTS5 writes one (every byte accounted for)."""
    table = _table(table)
    try:
        row = conn.execute(f'SELECT block FROM "{table}_data" WHERE id = ?', (_STRUCTURE_ROWID,)).fetchone()
    except sqlite3.OperationalError:
        return None
    if row is None or row[0] is None:
        return None
    blob = bytes(row[0])
    try:
        i = 4  # the configuration cookie
        v2 = blob[4:8] == _STRUCTURE_V2
        if v2:
            i += 4
        n_levels, i = _varint(blob, i)
        n_segments, i = _varint(blob, i)
        _write_counter, i = _varint(blob, i)
        levels: list[tuple[int, ...]] = []
        merging = False
        tombstone_pages = 0
        for _ in range(n_levels):
            n_merge, i = _varint(blob, i)
            n_total, i = _varint(blob, i)
            merging = merging or n_merge > 0
            sizes = []
            for _ in range(n_total):
                _segment_id, i = _varint(blob, i)
                first, i = _varint(blob, i)
                last, i = _varint(blob, i)
                if v2:
                    _origin1, i = _varint(blob, i)
                    _origin2, i = _varint(blob, i)
                    pages, i = _varint(blob, i)
                    _entries_tombstoned, i = _varint(blob, i)
                    _entries, i = _varint(blob, i)
                    tombstone_pages += pages
                sizes.append(last - first + 1)
            levels.append(tuple(sizes))
    except IndexError:
        return None
    structure = Fts5Structure(tuple(levels), merging, tombstone_pages)
    if i != len(blob) or structure.segments != n_segments:
        return None
    return structure


def _holds_no_deleted_rows(structure: Fts5Structure | None) -> bool:
    """True when FTS5 has dropped every delete marker of the index: it holds
    no segment, or one segment on its last level and nothing in a merge or a
    tombstone. False for any other shape, and for a record not read."""
    if structure is None or structure.merging or structure.tombstone_pages:
        return False
    if structure.segments == 0:
        return True
    return structure.segments == 1 and bool(structure.levels[-1])


def drop_deleted_rows(conn: sqlite3.Connection, table: str) -> str:
    """Make sure *table*'s index holds no deleted row's words, in the
    caller's transaction: ``"clean"`` when the shape says so, else the index
    is rebuilt from the table's content and ``"rebuilt"`` returned."""
    table = _table(table)
    if _holds_no_deleted_rows(_read_structure(conn, table)):
        return "clean"
    conn.execute(f"INSERT INTO \"{table}\"(\"{table}\") VALUES ('rebuild')")
    logger.info("[fts5] %s: a merge could not drop the deleted rows' words (the segments sat on one "
                "level below the top); rebuilt the index from its content", table)
    return "rebuilt"


def purge_deleted_rows(conn: sqlite3.Connection, table: str) -> str:
    """Merge *table*'s index into one segment and make sure no deleted row's
    words are left (:func:`drop_deleted_rows`), in the caller's transaction.
    Returns ``"merged"`` or ``"rebuilt"``."""
    table = _table(table)
    conn.execute(f"INSERT INTO \"{table}\"(\"{table}\") VALUES ('optimize')")
    return "merged" if drop_deleted_rows(conn, table) == "clean" else "rebuilt"
