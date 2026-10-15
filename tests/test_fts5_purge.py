"""A purge of an FTS5 index leaves no deleted row's word in its segments
(KB-Q01, 2026-10-15).

The Knowledge soak's first multi-day run stopped at cycle 76: eight planted
words of the library deleted in that cycle were still in
``knowledge_chunks_fts_data``, held by delete markers in the index's oldest
segment, after ``purge_search_index`` had merged the index. FTS5 drops a
marker (and the word it carries) only when the merge writes the oldest
segment, judged by its place in the structure; when every segment sits on
one level below the top, ``optimize`` and ``merge -N`` merge that level one
level up and keep every marker. Every full merge adds levels and a large new
segment pulls the older ones down beside it, so a store reaches that shape
by itself -- here in a few statements.

The fix (:mod:`kazma_core.db.fts5_purge`) checks the shape a merge left and
rebuilds the index when it is not one segment on the last level. Each case
has its negative control: the plain ``optimize`` -- memory's purge and the
vector catalog's until this fix -- on the same index.
"""

from __future__ import annotations

import ast
import random
import re
import sqlite3
import subprocess
from pathlib import Path

import pytest
from kazma_core.db import fts5_purge
from kazma_core.db.fts5_purge import (
    Fts5Structure,
    _holds_no_deleted_rows,
    _read_structure,
    drop_deleted_rows,
    purge_deleted_rows,
)

from tests._fts5_segments import held_only_by_deleted_rows, live_terms, segment_terms

REPO = Path(__file__).resolve().parents[1]


def _words(tag: str, n: int) -> list[str]:
    rng = random.Random(tag)
    return ["zq" + "".join(rng.choice("bcdfghjklmnpqrstvwx") for _ in range(8)) for _ in range(n)]


_EXTERNAL = """
CREATE TABLE docs(body TEXT);
CREATE VIRTUAL TABLE docs_fts USING fts5(body, content='docs', content_rowid='rowid');
CREATE TRIGGER docs_ai AFTER INSERT ON docs BEGIN
  INSERT INTO docs_fts(rowid, body) VALUES (new.rowid, new.body); END;
CREATE TRIGGER docs_ad AFTER DELETE ON docs BEGIN
  INSERT INTO docs_fts(docs_fts, rowid, body) VALUES ('delete', old.rowid, old.body); END;
"""


def _one_level_below_the_top(conn: sqlite3.Connection, *, external: bool = False, table: str = "docs_fts",
                             column: str = "body") -> tuple[list[str], list[str]]:
    """An index whose segments all sit on one level below the top, holding
    the delete markers of 1,200 rows: (the deleted words, the kept ones).

    A base merged into one segment; two small rows written, deleted and the
    index merged (each full merge across two levels adds two levels); then a
    batch larger than the base, whose new segment pulls the base down beside
    it, deleted. With *external* the index is external-content with
    triggers, as memory's are."""
    if external:
        conn.executescript(_EXTERNAL)
        insert, delete = "INSERT INTO docs(rowid, body) VALUES (?, ?)", "DELETE FROM docs WHERE rowid BETWEEN ? AND ?"
    else:
        conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5({column})")
        insert = f"INSERT INTO {table}(rowid, {column}) VALUES (?, ?)"
        delete = f"DELETE FROM {table} WHERE rowid BETWEEN ? AND ?"

    def put(first: int, words: list[str]) -> None:
        conn.execute("BEGIN")
        conn.executemany(insert, [(first + i, f"{w} alpha beta") for i, w in enumerate(words)])
        conn.execute("COMMIT")

    def drop(first: int, last: int) -> None:
        conn.execute("BEGIN")
        conn.execute(delete, (first, last))
        conn.execute("COMMIT")

    merge = f"INSERT INTO {table}({table}) VALUES ('optimize')"
    kept = _words("kept", 400)
    put(1, kept)
    conn.execute(merge)
    for n in range(2):
        put(10_000 + n, _words(f"small{n}", 1))
        drop(10_000 + n, 10_000 + n)
        conn.execute(merge)
    gone = _words("gone", 1200)
    put(20_000, gone)
    drop(20_000, 20_000 + len(gone) - 1)
    structure = _read_structure(conn, table)
    populated = [n for n, level in enumerate(structure.levels) if level]
    assert len(populated) == 1 and populated[0] < len(structure.levels) - 2, structure
    return gone, kept


@pytest.mark.parametrize("external", [False, True], ids=["regular", "external-content"])
def test_a_plain_optimize_keeps_every_deleted_word_when_the_segments_sit_on_one_level(external):
    """Negative control: the purge memory and the vector catalog ran."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    gone, _kept = _one_level_below_the_top(conn, external=external)
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES ('optimize')")
    assert held_only_by_deleted_rows(conn, "docs_fts") >= set(gone)
    assert not _holds_no_deleted_rows(_read_structure(conn, "docs_fts"))  # the check sees it
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES ('optimize')")  # one segment: a second merge does nothing
    assert held_only_by_deleted_rows(conn, "docs_fts") >= set(gone)
    assert purge_deleted_rows(conn, "docs_fts") == "rebuilt"  # the check rebuilds it
    assert held_only_by_deleted_rows(conn, "docs_fts") == set()


@pytest.mark.parametrize("external", [False, True], ids=["regular", "external-content"])
def test_the_purge_leaves_no_deleted_word_in_that_shape(external):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    gone, kept = _one_level_below_the_top(conn, external=external)
    assert purge_deleted_rows(conn, "docs_fts") == "rebuilt"
    assert held_only_by_deleted_rows(conn, "docs_fts") == set()
    assert not set(gone) & segment_terms(conn, "docs_fts")
    assert set(kept) <= live_terms(conn, "docs_fts")
    assert conn.execute("SELECT COUNT(*) FROM docs_fts WHERE docs_fts MATCH ?", (kept[7],)).fetchone()[0] == 1
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES ('integrity-check')")


def test_a_merge_across_levels_needs_no_rebuild():
    """The common shape: the merge writes the oldest segment and FTS5 drops
    the markers itself, so the check passes and nothing is rebuilt."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(body)")
    words = _words("w", 60)
    for i, w in enumerate(words):
        conn.execute("INSERT INTO docs_fts(rowid, body) VALUES (?, ?)", (i, w))
    conn.execute("INSERT INTO docs_fts(docs_fts) VALUES ('optimize')")
    conn.execute("DELETE FROM docs_fts WHERE rowid < 30")
    assert purge_deleted_rows(conn, "docs_fts") == "merged"
    assert held_only_by_deleted_rows(conn, "docs_fts") == set()
    assert drop_deleted_rows(conn, "docs_fts") == "clean"


# ── reading the structure ───────────────────────────────────────────────────


def test_the_shapes_that_hold_no_deleted_row():
    assert _holds_no_deleted_rows(Fts5Structure((), False, 0))  # an empty index
    assert _holds_no_deleted_rows(Fts5Structure(((), ()), False, 0))
    assert _holds_no_deleted_rows(Fts5Structure(((), (), (7,)), False, 0))  # one segment, last level
    assert not _holds_no_deleted_rows(Fts5Structure(((7,), (), ()), False, 0))  # one, below the top
    assert not _holds_no_deleted_rows(Fts5Structure(((1,), (7,)), False, 0))  # two
    assert not _holds_no_deleted_rows(Fts5Structure(((), (7, 3)), True, 0))  # a merge in progress
    assert not _holds_no_deleted_rows(Fts5Structure(((), (7,)), False, 2))  # tombstones
    assert not _holds_no_deleted_rows(None)  # a record not read: rebuild


def test_a_record_that_does_not_read_as_fts5_wrote_it_is_rebuilt():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(body)")
    conn.execute("INSERT INTO docs_fts(rowid, body) VALUES (1, 'zqkept alpha')")
    good = bytes(conn.execute("SELECT block FROM docs_fts_data WHERE id = 10").fetchone()[0])
    assert _read_structure(conn, "docs_fts") is not None
    probe = sqlite3.connect(":memory:")
    probe.execute("CREATE TABLE docs_fts_data(id INTEGER PRIMARY KEY, block BLOB)")
    for bad in (good[:-1], good + b"\x00"):  # cut short, or bytes left over
        probe.execute("INSERT OR REPLACE INTO docs_fts_data VALUES (10, ?)", (bad,))
        assert _read_structure(probe, "docs_fts") is None
    assert _read_structure(probe, "missing_fts") is None


def test_a_version_2_record_is_read():
    """SQLite 3.43+ writes a version 2 record for tables with tombstones."""
    if sqlite3.sqlite_version_info < (3, 43, 0):
        pytest.skip("contentless_delete needs SQLite 3.43")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.execute("CREATE VIRTUAL TABLE docs_fts USING fts5(body, content='', contentless_delete=1)")
    for i in range(20):
        conn.execute("INSERT INTO docs_fts(rowid, body) VALUES (?, ?)", (i, f"zqword{i}"))
    conn.execute("DELETE FROM docs_fts WHERE rowid = 3")
    structure = _read_structure(conn, "docs_fts")
    assert structure is not None and structure.segments >= 1
    assert structure.tombstone_pages >= 1 and not _holds_no_deleted_rows(structure)


def test_the_table_name_is_checked():
    conn = sqlite3.connect(":memory:")
    with pytest.raises(ValueError):
        drop_deleted_rows(conn, "docs; DROP TABLE x")


# ── memory's purge (the sibling) ────────────────────────────────────────────


def test_memorys_purge_leaves_no_forgotten_word_in_that_shape(monkeypatch):
    from kazma_core.memory import forget

    conn = sqlite3.connect(":memory:", isolation_level=None)
    gone, kept = _one_level_below_the_top(conn, external=True)
    monkeypatch.setattr(forget, "_FTS_INDEXES", ("docs_fts", "absent_fts"))  # a missing index is skipped
    conn.execute("BEGIN")
    forget.purge_search_index(conn)
    conn.execute("COMMIT")
    assert held_only_by_deleted_rows(conn, "docs_fts") == set()
    assert set(kept) <= live_terms(conn, "docs_fts")


def test_the_vector_catalogs_compaction_leaves_no_deleted_word_in_that_shape(tmp_path):
    from kazma_core.memory import chroma_local

    db = tmp_path / chroma_local.CATALOG
    conn = sqlite3.connect(str(db), isolation_level=None)
    gone, _kept = _one_level_below_the_top(conn, table="embedding_fulltext_search", column="string_value")
    conn.close()
    assert chroma_local.compact_catalog(tmp_path)["compacted"] is True
    conn = sqlite3.connect(str(db))
    try:
        assert held_only_by_deleted_rows(conn, "embedding_fulltext_search") == set()
        assert not set(gone) & segment_terms(conn, "embedding_fulltext_search")
    finally:
        conn.close()


# ── the class gate: every FTS5 merge ends with the check ────────────────────

_COMMAND = re.compile(r"VALUES\s*\(\s*'(optimize|merge)'", re.IGNORECASE)

_HELPER = "kazma-core/kazma_core/db/fts5_purge.py"

#: Functions outside the helper that may send an FTS5 merge, because they
#: finish with ``drop_deleted_rows``: the Knowledge store's stepped purge.
_ALLOWED = {"kazma-core/kazma_core/stores/knowledge.py": "purge_search_index"}


def fts5_merges_outside_the_check(sources: dict[str, str]) -> list[str]:
    """Each FTS5 ``optimize``/``merge`` sent outside :mod:`fts5_purge`, or by
    a function that does not then call ``drop_deleted_rows``."""
    found = []
    for rel, text in sorted(sources.items()):
        if rel == _HELPER:
            continue
        lines = [text.count("\n", 0, m.start()) + 1 for m in _COMMAND.finditer(text)]
        if not lines:
            continue
        functions = [n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for line in lines:
            around = [f for f in functions if f.lineno <= line <= (f.end_lineno or f.lineno)]
            if not around:
                found.append(f"{rel}:{line} (outside a function)")
                continue
            inner = max(around, key=lambda f: f.lineno)
            if _ALLOWED.get(rel) == inner.name and "drop_deleted_rows(" in (ast.get_source_segment(text, inner) or ""):
                continue
            found.append(f"{rel}:{line} {inner.name}")
    return found


def _product_sources() -> dict[str, str]:
    listed = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard",
                             "kazma-*/**.py", "scripts/**.py"],
                            cwd=REPO, capture_output=True, text=True, check=True).stdout.split()
    return {rel: (REPO / rel).read_text(encoding="utf-8", errors="replace") for rel in listed
            if "_tests/" not in rel and "/tests/" not in rel and (REPO / rel).is_file()}


def test_every_fts5_merge_in_product_code_ends_with_the_check():
    assert fts5_merges_outside_the_check(_product_sources()) == []


def test_the_gate_sees_a_plain_merge():
    """Negative control: memory's purge as it was, and a merge in a module."""
    planted = {
        "kazma-core/kazma_core/memory/forget.py": (
            "def purge_search_index(conn):\n"
            "    for name in ('episodes_fts',):\n"
            "        conn.execute(f\"INSERT INTO {name}({name}) VALUES('optimize')\")\n"),
        "kazma-core/kazma_core/stores/knowledge.py": (
            "def purge_search_index(self):\n"
            "    conn.execute(\"INSERT INTO t (t, rank) VALUES ('merge', ?)\", (-1,))\n"),
        "kazma-core/kazma_core/x.py": "conn.execute(\"INSERT INTO t(t) VALUES ('optimize')\")\n",
    }
    assert fts5_merges_outside_the_check(planted) == [
        "kazma-core/kazma_core/memory/forget.py:3 purge_search_index",
        "kazma-core/kazma_core/stores/knowledge.py:2 purge_search_index",
        "kazma-core/kazma_core/x.py:1 (outside a function)",
    ]


def test_the_helper_is_the_one_the_store_and_memory_use():
    from kazma_core.memory import chroma_local, forget
    from kazma_core.stores import knowledge

    assert knowledge.drop_deleted_rows is fts5_purge.drop_deleted_rows
    assert forget.purge_deleted_rows is chroma_local.purge_deleted_rows is fts5_purge.purge_deleted_rows
