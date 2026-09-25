"""A shadowed `settings` table must carry its warning inside itself.

Switching an install to Postgres leaves `kazma-data/settings.db` behind. The
`settings` table in it is dead, but `knowledge_chunks` in the SAME file is live
data, so the file cannot be deleted. Measured on the reference install: 90 dead
settings rows beside 6,598 live knowledge chunks, and every provider disagreeing
with the real store — disabled with empty keys here, enabled with keys there.

Debugging a credential failure against that file gives a confident wrong
answer, and it has, twice in this repo's history.

ConfigStore already warns at boot, to the log and to stderr. That is delivered
on the day of boot; the person this protects opens the file three days later in
a SQLite browser, mid-incident. A log line on Tuesday does not reach them on
Friday, so the warning also goes in a row of the table itself, where any
`SELECT * FROM settings` puts it on screen beside the rows it is about.

The clearing half matters just as much. If SQLite becomes the live backend
again, those rows mean something, and a leftover "this table is not read"
notice would be the lie — believed, because it is written down.
"""

from __future__ import annotations

import sqlite3

import pytest

from kazma_core.config_store import (
    _STALE_NOTICE_KEY,
    _mark_stale_settings_table,
)

# Verified against a real Postgres; the CI Postgres job runs every test
# carrying this marker (scripts/postgres_suite.py).
pytestmark = pytest.mark.postgres

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'general',
    updated_at TEXT NOT NULL
);
"""


@pytest.fixture
def db(tmp_path):
    p = tmp_path / "settings.db"
    conn = sqlite3.connect(p)
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO settings (key, value, category, updated_at) "
        "VALUES ('providers.list', '[]', 'providers', '2026-01-01')"
    )
    conn.commit()
    conn.close()
    return p


def _rows(p):
    conn = sqlite3.connect(p)
    try:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())
    finally:
        conn.close()


def test_the_notice_lands_in_the_table(db):
    _mark_stale_settings_table(db, live=False)

    rows = _rows(db)
    assert _STALE_NOTICE_KEY in rows, (
        "nothing warns the person who opens this file in a SQLite browser"
    )
    note = rows[_STALE_NOTICE_KEY]
    assert "NOT read" in note
    assert "DO NOT DELETE" in note, (
        "the notice must repeat the do-not-delete caveat — the same file holds "
        "the live Knowledge Library, and an operator tidying up after the "
        "cutover would otherwise delete their ingested corpus"
    )


def test_the_real_rows_are_left_alone(db):
    """A diagnostic that writes must not disturb what it is describing."""
    before = _rows(db)
    _mark_stale_settings_table(db, live=False)
    after = _rows(db)

    del after[_STALE_NOTICE_KEY]
    assert after == before, "the notice altered the rows it was describing"


def test_the_notice_is_removed_when_sqlite_is_live_again(db):
    """A stale warning about staleness is worse than none."""
    _mark_stale_settings_table(db, live=False)
    assert _STALE_NOTICE_KEY in _rows(db)

    _mark_stale_settings_table(db, live=True)
    assert _STALE_NOTICE_KEY not in _rows(db), (
        "the notice outlived the condition it describes, so a live settings "
        "table now claims it is not read"
    )


def test_writing_it_twice_does_not_duplicate(db):
    _mark_stale_settings_table(db, live=False)
    _mark_stale_settings_table(db, live=False)

    conn = sqlite3.connect(db)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM settings WHERE key = ?", (_STALE_NOTICE_KEY,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1


@pytest.mark.parametrize(
    "broken",
    ["missing-file", "not-a-database", "directory"],
    ids=["missing", "garbage", "directory"],
)
def test_it_never_raises(tmp_path, broken):
    """Boot must not fail over a hint. It is not load-bearing."""
    if broken == "missing-file":
        target = tmp_path / "nope.db"
    elif broken == "not-a-database":
        target = tmp_path / "garbage.db"
        target.write_bytes(b"definitely not sqlite")
    else:
        target = tmp_path / "adir"
        target.mkdir()

    _mark_stale_settings_table(target, live=False)   # must not raise
    _mark_stale_settings_table(target, live=True)    # nor must this
