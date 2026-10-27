"""The migration export counts a table whatever its name holds.

Audit 2026-10-26 AUD-095, the sibling of AUD-028 (the importer's quoting).
``_count_tables`` wrote ``SELECT COUNT(*) FROM "{name}"``: a table name with
a ``"`` in it closed the quoting early, the count failed and was recorded as
``-1``, which the bundle's verify report then shows as a broken table. The
negative control is the old code: the count is ``-1``.
"""

from __future__ import annotations

import sqlite3

from kazma_core.migration.exporter import _count_tables


def test_a_table_name_with_a_quote_is_counted(tmp_path):
    db = tmp_path / "store.db"
    conn = sqlite3.connect(db)
    try:
        conn.execute('CREATE TABLE "odd""name" (x INTEGER)')
        conn.executemany('INSERT INTO "odd""name" VALUES (?)', [(1,), (2,), (3,)])
        conn.execute("CREATE TABLE plain (x INTEGER)")
        conn.execute("INSERT INTO plain VALUES (1)")
        conn.commit()
    finally:
        conn.close()

    counts = _count_tables(db)

    assert counts == {'odd"name': 3, "plain": 1}
