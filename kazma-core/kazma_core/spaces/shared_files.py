"""Who shared each file into a shared space, the files taken out of one, and
the files a space's own folder holds (Spaces S2-12, piece 7).

A shared folder records no maker, so a move records one
(``spaces.file_moves``): the space, the file's path in its folder, who shared
it, its SHA-256 and the move that put it there. Only that person takes the
file out, and only while its bytes are the ones they shared. A file taken out
leaves the shared space a line -- who, when, the path it had -- which is no
file and which only the move writes.

**The store** (:data:`STORE`) is made by the first file that is shared, never
before: an install where nobody shares a file has none. Its commits are synced
to disk (``synchronous=FULL``): the file's other copy goes right after, and a
maker lost to a power cut could not be had again. Every row carries its
space's id and the id of its move, so writing a move's rows twice, or taking
them back, never touches another's.

:func:`list_files` lists a page of a plain space's own folder, for the Files
panel of the Documents page: bounded, names as the disk spells them, never
through a link, never a hidden name.

Blocking (SQLite, the file system, the identity store): call it off the event
loop.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import stat
import time
from collections.abc import Iterator, Mapping
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kazma_core.spaces.scope import OWNER_SPACE_ID, spaces_enforced

__all__ = [
    "PAGE_MAX",
    "SCAN_CAP",
    "STORE",
    "SharedFilesUnreadable",
    "ensure_store",
    "forget_gone",
    "list_files",
    "maker_of",
    "name_moves",
    "record_move",
    "undo_move",
    "was_recorded",
]

logger = logging.getLogger(__name__)

#: ``<data dir>/shared_files.db``.
STORE = "shared_files.db"
#: At most this many files of a folder are listed (a page is a slice of them;
#: past it the listing says some are not shown), from this many folders.
SCAN_CAP = 5000
_FOLDER_CAP = 2000
#: The most files one page lists.
PAGE_MAX = 100
#: A shared space keeps this many lines of files taken out, the newest.
_KEPT_PER_SPACE = 500
#: How long a maker's row outlives its file before the sweep forgets it: a
#: file being moved or written is not gone.
_GONE_AFTER_S = 3600.0

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS shared_files (
    space_id  TEXT NOT NULL,
    path      TEXT NOT NULL,
    maker     TEXT NOT NULL,
    sha256    TEXT NOT NULL,
    size      INTEGER NOT NULL,
    shared_at REAL NOT NULL,
    attempt   TEXT NOT NULL,
    PRIMARY KEY (space_id, path)
);
CREATE TABLE IF NOT EXISTS files_taken_out (
    attempt  TEXT PRIMARY KEY,
    space_id TEXT NOT NULL,
    path     TEXT NOT NULL,
    taken_by TEXT NOT NULL,
    taken_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_taken_out_space ON files_taken_out(space_id, taken_at);
"""


class SharedFilesUnreadable(Exception):  # noqa: N818 -- named for the state, as the stores' own are
    """The space's folder could not be read: never answered as "no files"."""


# ── the store ────────────────────────────────────────────────────────────


def _path() -> Path:
    from kazma_core.paths import data_dir

    return data_dir() / STORE


def _connection() -> sqlite3.Connection:
    from kazma_core.config_store import apply_sqlite_pragmas

    conn = sqlite3.connect(str(_path()), timeout=5.0, isolation_level=None)
    apply_sqlite_pragmas(conn)
    conn.execute("PRAGMA synchronous=FULL")
    conn.executescript(_SCHEMA)
    return conn


def ensure_store() -> None:
    """Make the store (a world of every row store)."""
    _connection().close()


@contextmanager
def _reading() -> Iterator[sqlite3.Connection | None]:
    """A connection, or None while nobody shared a file (no store is made)."""
    if not _path().is_file():
        yield None
        return
    with closing(_connection()) as conn:
        yield conn


@contextmanager
def _writing() -> Iterator[sqlite3.Connection]:
    """One transaction: committed at its end, or nothing of it."""
    with closing(_connection()) as conn:
        conn.execute("BEGIN IMMEDIATE")
        committed = False
        try:
            yield conn
            conn.execute("COMMIT")
            committed = True
        finally:
            if not committed:
                conn.execute("ROLLBACK")


# ── a move's rows ────────────────────────────────────────────────────────


def maker_of(space: str, path: str) -> dict[str, Any] | None:
    """Who shared the file at *path* of *space*: ``maker``, ``sha256``,
    ``size``, ``shared_at`` and the move's ``attempt``; None when nobody is
    recorded."""
    with _reading() as conn:
        row = None if conn is None else conn.execute(
            "SELECT maker, sha256, size, shared_at, attempt FROM shared_files WHERE space_id = ? AND path = ?",
            (space, path)).fetchone()
    if row is None:
        return None
    return {"maker": str(row[0]), "sha256": str(row[1]), "size": int(row[2]),
            "shared_at": float(row[3]), "attempt": str(row[4])}


def record_move(move: Mapping[str, Any]) -> None:
    """The rows of a move whose file is in its target, in one transaction: a
    shared file's maker, or the line of a file taken out (its maker's row
    goes). Writing them again changes nothing."""
    with _writing() as conn:
        if not move["taken_out"]:
            conn.execute(
                "INSERT INTO shared_files (space_id, path, maker, sha256, size, shared_at, attempt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(space_id, path) DO UPDATE SET maker = excluded.maker, "
                "sha256 = excluded.sha256, size = excluded.size, shared_at = excluded.shared_at, "
                "attempt = excluded.attempt WHERE shared_files.attempt <> excluded.attempt",
                (move["target"], move["target_path"], move["by"], move["sha256"], int(move["size"]),
                 time.time(), move["attempt"]))
            return
        conn.execute("DELETE FROM shared_files WHERE attempt = ?", (move["shared_by_attempt"],))
        conn.execute("INSERT OR IGNORE INTO files_taken_out (attempt, space_id, path, taken_by, taken_at) "
                     "VALUES (?, ?, ?, ?, ?)",
                     (move["attempt"], move["source"], move["path"], move["by"], time.time()))
        conn.execute(
            "DELETE FROM files_taken_out WHERE space_id = ? AND rowid NOT IN (SELECT rowid FROM files_taken_out "
            "WHERE space_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?)",
            (move["source"], move["source"], _KEPT_PER_SPACE))


def undo_move(move: Mapping[str, Any]) -> None:
    """Take back what :func:`record_move` wrote for this very move: its file
    is not in the target. Nothing to do when it wrote nothing."""
    if not _path().is_file():
        return
    with _writing() as conn:
        if not move["taken_out"]:
            conn.execute("DELETE FROM shared_files WHERE attempt = ?", (move["attempt"],))
            return
        undone = conn.execute("DELETE FROM files_taken_out WHERE attempt = ?", (move["attempt"],)).rowcount
        if undone:
            conn.execute(
                "INSERT OR IGNORE INTO shared_files (space_id, path, maker, sha256, size, shared_at, attempt) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (move["source"], move["path"], move["by"], move["sha256"], int(move["size"]),
                 float(move["shared_at"]), move["shared_by_attempt"]))


def was_recorded(move: Mapping[str, Any]) -> bool:
    """Whether :func:`record_move` wrote this very move's rows: its file was
    in its target."""
    table = "files_taken_out" if move["taken_out"] else "shared_files"
    with _reading() as conn:
        return conn is not None and conn.execute(
            f"SELECT 1 FROM {table} WHERE attempt = ?", (move["attempt"],)).fetchone() is not None


def _line_id(attempt: str) -> str:
    """A line's id on the page: its own, never the move's."""
    return hashlib.sha256(f"taken out:{attempt}".encode()).hexdigest()[:32]


def _taken_out_files(space: str, *, limit: int = 50) -> list[dict[str, str]]:
    """The files taken out of *space*, newest first: who, when and the path
    each had. Never a word of the file."""
    with _reading() as conn:
        rows = [] if conn is None else conn.execute(
            "SELECT attempt, path, taken_by, taken_at FROM files_taken_out WHERE space_id = ? "
            "ORDER BY taken_at DESC, rowid DESC LIMIT ?", (space, max(1, min(int(limit), 200)))).fetchall()
    return [{"id": _line_id(str(r[0])), "path": str(r[1]), "by": str(r[2]), "at": _iso(float(r[3]))} for r in rows]


def forget_gone(spaces_being_moved: set[str]) -> int:
    """The maker's row of a file that is no longer there goes (renamed,
    deleted): nobody can take it out, and its path is a person's words. Never
    in a space a move is under way in (*spaces_being_moved*), nor within the
    hour, nor when the folder cannot be read. Returns how many."""
    from kazma_core.spaces.files import FILES, space_dir

    with _reading() as conn:
        rows = [] if conn is None else conn.execute(
            "SELECT space_id, path, attempt FROM shared_files WHERE shared_at < ?",
            (time.time() - _GONE_AFTER_S,)).fetchall()
    gone = []
    for space, path, attempt in rows:
        root = space_dir(str(space)) / FILES
        if str(space) in spaces_being_moved or not root.is_dir():
            continue
        try:
            os.lstat(root / str(path))
        except (FileNotFoundError, NotADirectoryError):
            gone.append(str(attempt))
        except OSError:
            continue
    if gone:
        with _writing() as conn:
            conn.executemany("DELETE FROM shared_files WHERE attempt = ?", [(a,) for a in gone])
        logger.info("[spaces] %d shared files that are gone are no longer recorded", len(gone))
    return len(gone)


# ── the files a space holds ──────────────────────────────────────────────


def _iso(when: float) -> str:
    return datetime.fromtimestamp(when, UTC).isoformat(timespec="seconds")


def name_moves(path: str) -> bool:
    """Whether a file of this path can move: its names are written the same
    everywhere a space goes. A name another system cannot hold (``:``, ``\\``,
    a control character, or bytes that are not text) is listed and stays."""
    return not any(ch in "\\:" or ord(ch) < 32 or ord(ch) == 127 or 0xD800 <= ord(ch) <= 0xDFFF for ch in path)


def _walk(root: Path) -> tuple[list[tuple[str, os.stat_result]], bool]:
    """The files under *root* by their path in it, and whether some are not
    among them (past the caps, or in a folder that could not be read)."""
    from kazma_core.spaces.files import own_files

    if not root.exists():
        return [], False
    unread: list[Path] = []
    found: list[tuple[str, os.stat_result]] = []
    for path in own_files(root, unreadable=unread, hidden=False, most_folders=_FOLDER_CAP):
        if len(found) >= SCAN_CAP:
            unread.append(path)
            break
        try:
            info = os.lstat(path)
        except OSError:
            continue  # gone since the walk saw it
        if stat.S_ISREG(info.st_mode):
            found.append((path.relative_to(root).as_posix(), info))
    if root in unread:
        raise SharedFilesUnreadable("the space's folder could not be read")
    found.sort(key=lambda item: (item[0].casefold(), item[0]))
    return found, bool(unread)


def list_files(space: str, *, by: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    """A page of the files in *space*'s own folder: each file's path, size,
    time, whether it can move, and whether *by* shared it and it is the size
    they shared (``mine``: theirs to take out). ``taken_out``: the lines of
    the files taken out. Only a plain space has this panel (``available``):
    the owner's space and one with a project folder answer False. Blocking."""
    from kazma_core.spaces import files

    if not spaces_enforced() or not space or space == OWNER_SPACE_ID or files.space_kind(space) != "sandboxed":
        return {"available": False, "files": [], "total": 0, "offset": 0, "limit": 0, "cut": False, "taken_out": []}
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or 50), PAGE_MAX))
    found, cut = _walk(files.space_dir(space) / files.FILES)
    page = found[offset:offset + limit]
    with _reading() as conn:
        makers = {} if conn is None else {
            str(r[0]): (str(r[1]), int(r[2])) for r in conn.execute(
                "SELECT path, maker, size FROM shared_files WHERE space_id = ?", (space,))}
    return {
        "available": True,
        "files": [{"path": rel.encode("utf-8", "replace").decode("utf-8"), "size": int(info.st_size),
                   "modified": _iso(info.st_mtime), "movable": name_moves(rel),
                   "mine": makers.get(rel) == (by, int(info.st_size))} for rel, info in page],
        "total": len(found), "offset": offset, "limit": limit, "cut": cut,
        "taken_out": _taken_out_files(space),
    }
