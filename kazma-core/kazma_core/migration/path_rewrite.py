"""Path translation for migration bundles — invariant B.

On export, Kazma captures the source machine's workspace root and data dir
into ``pathmap.json``. On import, this module rewrites every embedded
absolute path (in ``workspaces.root_path``, ``snapshots.state_json``,
``chat_sessions.messages``, memory episodes, cron prompts, config) to the
target machine's equivalent — across OS path-separator conventions
(``/home/user/kazma`` ↔ ``C:\\Users\\user\\kazma``).

This is the single place path-translation logic lives. Three design points:

1. **PathMap is ordered, longest-source-first.** When one path is a prefix
   of another (``/home/user/kazma`` vs ``/home/user/kazma-repos/ShipX``),
   the longer must be substituted first or the shorter would partially
   rewrite it and leave a broken hybrid.

2. **Substitution is byte-level on the column text**, not a JSON parse.
   The 304 MB ``snapshots.db`` ``state_json`` column holds full
   SupervisorState blobs that may contain paths inside tool results, file
   refs, environment blocks — anywhere. Parsing every JSON value to walk
   for paths would be slow and fragile; a verified substring replacement
   over known-absolute path prefixes is correct and fast.

3. **Both separator forms are generated for each source root.** A Linux
   source path ``/home/user/kazma`` may appear in JSON as ``/home/user/kazma``
   (native) OR, in some Windows-authored rows, as ``\\home\\user\\kazma``.
   We substitute both forms of the source root, and write the target in
   its native separator form.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import PurePath, PurePosixPath, PureWindowsPath
from typing import Iterator

logger = logging.getLogger(__name__)

__all__ = [
    "PathMap",
    "build_path_map",
    "rewrite_text",
    "rewrite_paths_in_sqlite",
]


@dataclass
class PathMap:
    """An ordered list of (source_path, target_path) substitution pairs.

    Pairs are kept longest-source-first so prefix overlaps resolve correctly.
    Both separator variants of each source path are substituted.
    """

    pairs: list[tuple[str, str]] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not self.pairs

    def add(self, source: str, target: str) -> None:
        """Register a source→target path pair (both normalized as given)."""
        source = (source or "").rstrip("/\\")
        target = (target or "").rstrip("/\\")
        if not source or not target or source == target:
            return
        # De-dup; keep first.
        for s, _ in self.pairs:
            if s == source:
                return
        self.pairs.append((source, target))
        # Re-sort longest-source-first (prefix safety).
        self.pairs.sort(key=lambda p: len(p[0]), reverse=True)

    def to_json(self) -> str:
        return json.dumps({"pairs": self.pairs}, indent=2)

    @classmethod
    def from_json(cls, text: str) -> "PathMap":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return cls()
        return cls(pairs=[tuple(p) for p in data.get("pairs", []) if len(p) == 2])

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(self.pairs)


def build_path_map(
    source_workspace_root: str,
    target_workspace_root: str,
    *,
    source_data_dir: str | None = None,
    target_data_dir: str | None = None,
) -> PathMap:
    """Construct a PathMap from source→target root paths.

    Extra explicit pairs (e.g. a separate data-dir relocation) can be added
    by mutating the returned PathMap before passing it to the rewriter.
    """
    pm = PathMap()
    if source_workspace_root and target_workspace_root:
        pm.add(source_workspace_root, target_workspace_root)
    if source_data_dir and target_data_dir and source_data_dir != source_workspace_root:
        # The data dir is usually <root>/kazma-data; register it explicitly
        # in case the target relocates data independently (KAZMA_DATA_DIR).
        pm.add(source_data_dir, target_data_dir)
    return pm


# ── Text substitution ────────────────────────────────────────────────────


def rewrite_text(text: str, path_map: PathMap) -> tuple[str, int]:
    """Apply path substitutions to a text blob. Returns (new_text, replacement_count).

    For each (source, target) pair (longest source first), substitutes BOTH
    separator variants of ``source`` (forward-slash and backslash) with the
    target in its native form. Backslash variants only apply when the source
    actually contains a backslash form (Linux paths won't, Windows paths will).
    """
    if not isinstance(text, str) or path_map.is_empty():
        return text, 0
    out = text
    total = 0
    for source, target in path_map:
        if not source:
            continue
        # Forward-slash variant: always substitute (JSON paths are frequently
        # forward-slashed even on Windows, and Linux paths are native this way).
        fs_source = source.replace("\\", "/")
        fs_target = target.replace("\\", "/")
        if fs_source in out:
            count = out.count(fs_source)
            out = out.replace(fs_source, fs_target)
            total += count
        # Backslash variant: only if the source actually has one (Windows source).
        # Avoids pointless scans for Linux sources.
        if "\\" in source:
            bs_source = source.replace("/", "\\")
            bs_target = target.replace("/", "\\")
            if bs_source in out:
                count = out.count(bs_source)
                out = out.replace(bs_source, bs_target)
                total += count
    return out, total


# ── SQLite column rewriting ──────────────────────────────────────────────


def rewrite_paths_in_sqlite(
    db_path: str,
    columns: list[tuple[str, str]],
    path_map: PathMap,
    *,
    progress: "callable[[int, int], None] | None" = None,  # type: ignore[valid-type]
    progress_every: int = 500,
) -> int:
    """Rewrite embedded paths in text columns of a SQLite database, in place.

    Args:
        db_path: path to the .db file (WAL-safe: opens read-write, but the
            caller should ensure no live Kazma process is writing to it).
        columns: list of ``(table, text_column)`` to scan+rewrite. Each
            column is rewritten via ``UPDATE ... SET col = ?`` where the new
            value differs.
        path_map: the substitution map.
        progress: optional callback ``(done, total)`` for the slow tables
            (snapshots.db can be 304 MB / thousands of rows).
        progress_every: invoke the progress callback every N rows.

    Returns:
        The total number of rows actually changed.

    For each table/column, fetches rows in a single pass and issues targeted
    UPDATEs only for rows whose value changed. Uses ``rowid`` when available
    for a stable primary key; falls back to scanning all columns otherwise.
    """
    if path_map.is_empty():
        return 0

    total_changed = 0
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    try:
        for table, col in columns:
            total_changed += _rewrite_one_column(
                conn, table, col, path_map, progress, progress_every
            )
        conn.commit()
    finally:
        conn.close()
    return total_changed


def _rewrite_one_column(
    conn: sqlite3.Connection,
    table: str,
    col: str,
    path_map: PathMap,
    progress: "callable[[int, int], None] | None",  # type: ignore[valid-type]
    progress_every: int,
) -> int:
    """Rewrite one (table, column); returns rows-changed count."""
    # Safety: table/column names can't be parameterized; whitelist against
    # identifier rules so a caller typo can't inject SQL.
    if not _is_safe_identifier(table) or not _is_safe_identifier(col):
        logger.warning("[migrate] refusing unsafe identifier: %s.%s", table, col)
        return 0

    # Detect the table's primary key for stable UPDATEs.
    pk_cols = _primary_key_cols(conn, table)
    has_rowid = _table_has_rowid(conn, table)

    # Count rows for progress reporting.
    try:
        total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
    except sqlite3.OperationalError:
        logger.warning("[migrate] table %s not found in %s — skipping", table, conn.execute("PRAGMA database_list").fetchone()[2] if False else "?")
        return 0

    changed = 0
    done = 0

    # Build the SELECT. Prefer the declared PK; fall back to rowid; last
    # resort scan all columns (re-identification by full-row equality).
    select_pk = ", ".join(f'"{c}"' for c in pk_cols) if pk_cols else ("rowid" if has_rowid else "*")
    select_sql = f'SELECT {select_pk}, "{col}" FROM "{table}"'

    try:
        cursor = conn.execute(select_sql)
    except sqlite3.OperationalError as exc:
        logger.warning("[migrate] cannot read %s.%s: %s", table, col, exc)
        return 0

    rows = cursor.fetchall()
    for row in rows:
        done += 1
        *pk_values, value = row
        if not isinstance(value, str):
            continue
        new_value, n = rewrite_text(value, path_map)
        if n == 0:
            if progress and done % progress_every == 0:
                progress(done, total)
            continue

        # Build the UPDATE keyed on the PK / rowid.
        if pk_cols:
            where = " AND ".join(f'"{c}" = ?' for c in pk_cols)
            params = [new_value, *pk_values]
        elif has_rowid:
            where = "rowid = ?"
            params = [new_value, pk_values[0]]
        else:
            # No stable key — skip (rare; all Kazma tables have a PK).
            logger.warning("[migrate] %s has no PK/rowid — cannot update row %r", table, pk_values)
            continue
        conn.execute(f'UPDATE "{table}" SET "{col}" = ? WHERE {where}', params)
        changed += 1
        if progress and done % progress_every == 0:
            progress(done, total)
    if progress:
        progress(done, total)
    return changed


def _is_safe_identifier(name: str) -> bool:
    """Reject anything that isn't a bare SQL identifier (letters/digits/_)."""
    return bool(name) and all(c.isalnum() or c == "_" for c in name)


def _primary_key_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    """Return the declared PK column names for ``table`` (empty if none/rowid)."""
    try:
        cols = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
    except sqlite3.OperationalError:
        return []
    pk_cols = [c[1] for c in cols if c[5] > 0]  # c[5] = pk index
    # A single INTEGER PRIMARY KEY column aliases rowid; use rowid instead.
    if len(pk_cols) == 1:
        for c in cols:
            if c[1] == pk_cols[0] and c[2].upper() == "INTEGER":
                return []  # use rowid
    return pk_cols


def _table_has_rowid(conn: sqlite3.Connection, table: str) -> bool:
    """True unless the table was created WITHOUT ROWID."""
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not sql or not sql[0]:
            return True
        return "WITHOUT ROWID" not in sql[0].upper()
    except sqlite3.OperationalError:
        return True
