"""SQLite WAL store for per-workspace symbol rows."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

from kazma_core.code_index.symbols import Symbol
from kazma_core.code_index.walk import lang_for_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  mtime REAL NOT NULL,
  size INTEGER NOT NULL,
  lang TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS symbols (
  path TEXT NOT NULL,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  line INTEGER NOT NULL,
  signature TEXT NOT NULL DEFAULT '',
  FOREIGN KEY(path) REFERENCES files(path) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
"""


def db_path_for(root: Path) -> Path:
    from kazma_core.paths import data_dir

    digest = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:16]
    folder = data_dir() / "code-index"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{digest}.db"


def connect(root: Path) -> sqlite3.Connection:
    path = db_path_for(root)
    conn = sqlite3.connect(str(path), timeout=5.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


def upsert_file(
    conn: sqlite3.Connection,
    rel: str,
    *,
    mtime: float,
    size: int,
    symbols: list[Symbol],
    lang: str,
) -> None:
    conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
    conn.execute(
        "INSERT INTO files(path, mtime, size, lang) VALUES (?,?,?,?) "
        "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size, lang=excluded.lang",
        (rel, mtime, size, lang or lang_for_path(Path(rel))),
    )
    conn.executemany(
        "INSERT INTO symbols(path, name, kind, line, signature) VALUES (?,?,?,?,?)",
        [(rel, s.name, s.kind, int(s.line), s.signature or "") for s in symbols],
    )


def drop_file(conn: sqlite3.Connection, rel: str) -> None:
    conn.execute("DELETE FROM symbols WHERE path = ?", (rel,))
    conn.execute("DELETE FROM files WHERE path = ?", (rel,))


def listed_files(conn: sqlite3.Connection) -> dict[str, tuple[float, int]]:
    rows = conn.execute("SELECT path, mtime, size FROM files").fetchall()
    return {str(r["path"]): (float(r["mtime"]), int(r["size"])) for r in rows}


def search_symbols(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
) -> list[sqlite3.Row]:
    q = (query or "").strip()
    if not q:
        return []
    like = f"%{q}%"
    return list(
        conn.execute(
            """
            SELECT path, name, kind, line, signature
            FROM symbols
            WHERE name = ? COLLATE NOCASE OR name LIKE ? COLLATE NOCASE
            ORDER BY CASE WHEN name = ? COLLATE NOCASE THEN 0 ELSE 1 END,
                     length(name), path, line
            LIMIT ?
            """,
            (q, like, q, int(limit)),
        ).fetchall()
    )


def stats(conn: sqlite3.Connection) -> dict[str, int]:
    n_files = int(conn.execute("SELECT COUNT(*) FROM files").fetchone()[0])
    n_syms = int(conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0])
    return {"files": n_files, "symbols": n_syms}
