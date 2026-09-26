"""Memory's search index is rewritten only when indexed text changes (Stage 2, S5).

The update triggers that keep ``episodes_fts`` / ``beliefs_fts`` /
``entities_fts`` in step with their tables fired on ANY column: every recall's
access bump, every archive move, every re-embed and every entity count
refresh deleted and re-inserted the index rows of what it touched -- on
recall's own hot path. They now fire on the indexed columns only, and an
install carrying the old triggers is upgraded by ``ensure_primary_schema``.

``Connection.total_changes`` counts the rows trigger programs write too, so an
update that leaves the index alone changes exactly one row.
"""

from __future__ import annotations

import sqlite3

import pytest
from kazma_core.memory.schema_v2 import ensure_primary_schema

#: The any-column triggers every install had until 2026-09-26.
OLD_TRIGGERS = {
    "episodes_fts_au": """
        CREATE TRIGGER episodes_fts_au AFTER UPDATE ON episodes BEGIN
          INSERT INTO episodes_fts(episodes_fts, rowid, user_text, assistant_text, summary_text)
          VALUES ('delete', old.rowid, old.user_text, old.assistant_text, old.summary_text);
          INSERT INTO episodes_fts(rowid, user_text, assistant_text, summary_text)
          VALUES (new.rowid, new.user_text, new.assistant_text, new.summary_text);
        END""",
    "beliefs_fts_au": """
        CREATE TRIGGER beliefs_fts_au AFTER UPDATE ON beliefs BEGIN
          INSERT INTO beliefs_fts(beliefs_fts, rowid, subject, predicate, object)
          VALUES ('delete', old.rowid, old.subject, old.predicate, old.object);
          INSERT INTO beliefs_fts(rowid, subject, predicate, object)
          VALUES (new.rowid, new.subject, new.predicate, new.object);
        END""",
    "entities_fts_au": """
        CREATE TRIGGER entities_fts_au AFTER UPDATE ON entities BEGIN
          INSERT INTO entities_fts(entities_fts, rowid, name, type, aliases_json)
          VALUES ('delete', old.rowid, old.name, old.type, old.aliases_json);
          INSERT INTO entities_fts(rowid, name, type, aliases_json)
          VALUES (new.rowid, new.name, new.type, new.aliases_json);
        END""",
}

#: An update of a column no index holds, per table.
BOOKKEEPING = {
    "episodes": "UPDATE episodes SET access_count = access_count + 1, tier = 'archived' WHERE id = 'e1'",
    "beliefs": "UPDATE beliefs SET valid_until = 1, invalidated_at = 1 WHERE id = 'b1'",
    "entities": "UPDATE entities SET metadata_json = '{\"n\": 1}' WHERE id = 'ent1'",
}


@pytest.fixture()
def conn(tmp_path):
    db = sqlite3.connect(str(tmp_path / "memory_state.db"))
    ensure_primary_schema(db)
    db.execute(
        "INSERT INTO episodes (id, tenant_id, session_id, turn_number, user_text, created_at) "
        "VALUES ('e1', 'default', 's', 1, 'the heron nests by the river', 1.0)"
    )
    db.execute(
        "INSERT INTO beliefs (id, tenant_id, subject, predicate, predicate_type, object, "
        "valid_from, ingested_at) VALUES ('b1', 'default', 'user', 'likes', 'set', 'herons', 1, 1)"
    )
    db.execute("INSERT INTO entities (id, tenant_id, type, name) VALUES ('ent1', 'default', 'concept', 'heron')")
    db.commit()
    yield db
    db.close()


def _rows_written(conn: sqlite3.Connection, sql: str) -> int:
    before = conn.total_changes
    conn.execute(sql)
    return conn.total_changes - before


def _integrity(conn: sqlite3.Connection) -> None:
    for fts in ("episodes_fts", "beliefs_fts", "entities_fts"):
        conn.execute(f"INSERT INTO {fts}({fts}) VALUES('integrity-check')")


@pytest.mark.parametrize("table", sorted(BOOKKEEPING))
def test_bookkeeping_leaves_the_index_alone(conn, table):
    assert _rows_written(conn, BOOKKEEPING[table]) == 1
    _integrity(conn)


def test_a_text_edit_is_still_indexed(conn):
    conn.execute("UPDATE episodes SET user_text = 'the kingfisher fishes' WHERE id = 'e1'")
    conn.execute("UPDATE beliefs SET object = 'kingfishers' WHERE id = 'b1'")
    conn.execute("UPDATE entities SET name = 'kingfisher' WHERE id = 'ent1'")
    for fts in ("episodes_fts", "beliefs_fts", "entities_fts"):
        assert conn.execute(f"SELECT count(*) FROM {fts} WHERE {fts} MATCH 'kingfisher*'").fetchone()[0] == 1
        assert conn.execute(f"SELECT count(*) FROM {fts} WHERE {fts} MATCH 'heron*'").fetchone()[0] == 0
    _integrity(conn)


def test_an_install_with_the_old_triggers_is_upgraded(conn):
    for name, sql in OLD_TRIGGERS.items():
        conn.execute(f"DROP TRIGGER {name}")
        conn.execute(sql)
    # Negative control: the old trigger rewrites the index row on a bump
    # (its delete and insert, and FTS5's own shadow-table writes).
    assert _rows_written(conn, BOOKKEEPING["episodes"]) > 1

    ensure_primary_schema(conn)

    for name in OLD_TRIGGERS:
        sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()[0]
        assert "UPDATE OF" in " ".join(sql.upper().split())
    for table in sorted(BOOKKEEPING):
        assert _rows_written(conn, BOOKKEEPING[table]) == 1
    _integrity(conn)
