"""Chroma's local store, kept honest on disk and in its index pool (KB-Q01).

The Knowledge Library's meaning vectors live in a local Chroma (chromadb 1.x,
its Rust "local" mode) under one directory: ``chroma.sqlite3`` (the catalog,
metadata and write log) and one folder per vector segment, named by the
segment's id. ``scripts/knowledge_soak.py`` found two defects of that mode on
2026-10-15, both still in chromadb 1.5.9 (the newest release that day: no
upgrade fixes them); this module is what Kazma does around them, with the
id that ties a coverage record to the vector store it measured.

**A dropped collection's vectors stay on disk.** ``delete_collection``
removes the catalog rows and nothing else: the segment folder (about 40 MB a
10,000 chunks at 1,024 dimensions) stays for good, on every platform, after
every library delete and every model switch that changes the vector size.
:func:`remove_orphan_segment_folders` removes the folders no segment in the
catalog owns -- read from the catalog, never guessed, and never a folder the
catalog names. On Windows a folder whose index this process loaded cannot
be removed until the process lets go of it (the files are held open); the
caller then closes its client and removes them (``vector_store_global``).

**An evicted index loses what it held in memory.** Chroma keeps each loaded
index in a pool split over :data:`POOL_SHARDS` (64) shards, sized by default
as ``max_file_handles // 5`` (102 on Windows: one or two indexes a shard), and
writes an index to disk only every ``sync_threshold`` (1,000) records. Two
collections that hash to one full shard evict each other, and the process
has already replayed each collection's log once:

* an evicted index of fewer than 1,000 records was never written, so every
  later read fails ("Error creating hnsw segment reader: Nothing found on
  disk") until a restart;
* an evicted index of 1,000 or more records reloads from its last write
  WITHOUT the records added since -- no error, the search just misses them
  -- until that collection's next write replays its log.

Kazma sizes the pool itself (:func:`start_system`, before the client
starts): :data:`POOL_SHARDS` times the most collections one process can
load, :func:`library_ceiling` plus :data:`SPARE_COLLECTIONS`. A shard can
then hold every collection there is, whichever shard each hashes to, so no
index is ever evicted. The Knowledge store refuses a library past the
ceiling. The ceiling is :data:`LIBRARY_CEILING`, lowered when the process
cannot open enough files: a loaded index holds :data:`FILES_PER_INDEX`
files open (C runtime streams on Windows, whose limit
:func:`raise_stream_limits` raises). ``vector_store_global`` still rebuilds
an index that was lost from Chroma's log and asks again.

**Deleted text stays in the catalog's free space.** Chroma keeps a record's
document in ``embedding_metadata``, its full-text index and its write log,
and SQLite leaves deleted rows' bytes in free pages and in the free space
of live pages (``secure_delete`` is off, and Chroma owns the connection).
Kazma stores no documents and only the metadata it reads
(``vector_store_global``); a deletion marks the catalog
(:func:`mark_compaction_due`) and the repair pass rewrites it
(:func:`compact_catalog`: the full-text index merged, then ``VACUUM``, as
Chroma's own ``chroma utils vacuum`` does), so no deleted row stays
readable in ``chroma.sqlite3``. :func:`holds_text` finds a collection an
older build wrote text into.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from kazma_core.db.fts5_purge import purge_deleted_rows

__all__ = [
    "CATALOG",
    "FILES_PER_INDEX",
    "LIBRARY_CEILING",
    "POOL_SHARDS",
    "SPARE_COLLECTIONS",
    "STORE_ID_FILE",
    "catalog_text_rows",
    "compact_catalog",
    "compaction_due",
    "holds_text",
    "index_pool_size",
    "library_ceiling",
    "mark_compaction_due",
    "raise_stream_limits",
    "remove_orphan_segment_folders",
    "start_system",
    "vector_store_id",
]

logger = logging.getLogger(__name__)

#: Chroma's catalog in a persist directory.
CATALOG = "chroma.sqlite3"
#: The vector store's own id: written once when Kazma first opens a
#: directory, recorded with every coverage measurement, and compared before
#: a recorded coverage is shown (KB-Q01: a knowledge.db restored onto a
#: machine with no vectors reported every library fully covered).
STORE_ID_FILE = "kazma_vector_store_id"
#: Present while the catalog may hold the bytes of deleted rows: written by
#: every deletion, removed by :func:`compact_catalog` once it rewrote the
#: file. A file, so a deletion before a restart is still compacted.
COMPACTION_MARKER = "kazma_compaction_due"

#: The shards Chroma's index pool is split into (chromadb 1.5.9: its cache
#: has 64, each holding the pool's size / 64 indexes). Measured, not read:
#: Chroma exposes neither number.
POOL_SHARDS = 64
#: The most Knowledge libraries an install holds (one collection each): a
#: library past it is refused. Each library's index stays loaded once used,
#: so the ceiling is also what the open-file budget below must carry.
LIBRARY_CEILING = 500
#: Collections a process may load beside the libraries': a graph rebuild's
#: temporary copy, the store's default collection, and slack.
SPARE_COLLECTIONS = 4
#: Files a loaded, persisted HNSW index holds open (header, data, length
#: and link lists -- measured on chromadb 1.5.9).
FILES_PER_INDEX = 4
#: Streams or file descriptors kept for everything else the process opens.
_FILE_RESERVE = 128

#: What the index files' streams are limited by, raised before the first
#: client starts. Windows: the Universal CRT's stream limit (ucrtbase, whose
#: ``FILE`` streams the index files are opened with; 8,192 is its maximum;
#: lowered to 40, a process loads 9 indexes and fails on the tenth -- measured).
#: POSIX: the soft open-file limit, never above the hard one.
_UCRT_STREAMS = 8192
_POSIX_OPEN_FILES = 10240

_LIMITS_LOCK = threading.Lock()
_LIMITS: dict[str, Any] | None = None


def _is_segment_name(name: str) -> bool:
    """Chroma names a segment folder by the segment's UUID."""
    try:
        return str(uuid.UUID(name)) == name.lower()
    except ValueError:
        return False


def _segment_folders(persist_dir: Path) -> list[Path]:
    try:
        return sorted(p for p in persist_dir.iterdir() if p.is_dir() and _is_segment_name(p.name))
    except OSError:
        return []


def _owned_segment_ids(persist_dir: Path) -> set[str] | None:
    """Every segment id the catalog names; an empty set when there is no
    catalog (a fresh one owns nothing), None when it cannot be read."""
    catalog = persist_dir / CATALOG
    if not catalog.is_file():
        return set()
    try:
        conn = sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        return {str(row[0]).lower() for row in conn.execute("SELECT id FROM segments")}
    except sqlite3.Error as exc:
        logger.warning("[chroma] the vector catalog %s cannot be read (%s): no segment folder is removed",
                       catalog, exc)
        return None
    finally:
        conn.close()


def _orphan_segment_folders(persist_dir: str | Path) -> list[Path] | None:
    """Segment folders no segment in the catalog owns; None when the catalog
    cannot be read (then nothing is an orphan to remove).

    The folders are listed BEFORE the catalog is read: Chroma creates a
    segment's folder only after its catalog row is committed, so a folder
    listed first whose row is not in the read that follows was dropped --
    never one being created.
    """
    root = Path(persist_dir)
    folders = _segment_folders(root)
    if not folders:
        return []
    owned = _owned_segment_ids(root)
    if owned is None:
        return None
    return [p for p in folders if p.name.lower() not in owned]


def _bytes(folder: Path) -> int:
    total = 0
    for path in folder.rglob("*"):
        try:
            if path.is_file():
                total += path.stat().st_size
        except OSError:
            pass
    return total


def remove_orphan_segment_folders(persist_dir: str | Path) -> dict[str, Any]:
    """Remove every segment folder no collection owns.

    Returns ``removed`` (folders), ``removed_bytes``, ``held`` (folder names
    still there: a process holds their files open -- on Windows, this one
    until it closes its client) and ``unreadable`` (the catalog could not
    be read, so nothing was removed). Never raises.
    """
    out: dict[str, Any] = {"removed": 0, "removed_bytes": 0, "held": [], "unreadable": False}
    orphans = _orphan_segment_folders(persist_dir)
    if orphans is None:
        out["unreadable"] = True
        return out
    for folder in orphans:
        size = _bytes(folder)
        # Past a held file: the rest of an orphan is no one's.
        shutil.rmtree(folder, ignore_errors=True)
        if folder.exists():
            out["held"].append(folder.name)
        else:
            out["removed"] += 1
            out["removed_bytes"] += size
    if out["removed"]:
        logger.info("[chroma] removed %d vector folder(s) no collection owns (%d bytes) in %s",
                    out["removed"], out["removed_bytes"], persist_dir)
    return out


def vector_store_id(persist_dir: str | Path | None, *, create: bool = False) -> str:
    """The id of the vector store in *persist_dir*: written once by the
    first process that opens the directory (``create``), read by health
    without opening Chroma. Empty when there is none (no directory, never
    opened, or lost with the vectors)."""
    if not persist_dir:
        return ""
    path = Path(persist_dir) / STORE_ID_FILE
    try:
        found = path.read_text(encoding="utf-8").strip()
    except OSError:
        found = ""
    if found or not create:
        return found
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        new = uuid.uuid4().hex
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(new, encoding="utf-8")
        try:
            # First writer wins: a concurrent creator's id is kept.
            os.link(tmp, path)
        except FileExistsError:
            pass
        except OSError:
            if not path.exists():
                os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        logger.warning("[chroma] could not record the vector store's id in %s", path, exc_info=True)
        return ""


def raise_stream_limits() -> dict[str, Any]:
    """Raise the limit on the files loaded indexes hold open, once per
    process, before the first client starts (never lowers one). Returns
    what was found and set (``[before, after]``); never raises."""
    global _LIMITS
    with _LIMITS_LOCK:
        if _LIMITS is not None:
            return _LIMITS
        result: dict[str, Any] = {}
        try:
            if os.name == "nt":
                import ctypes

                dll = ctypes.CDLL("ucrtbase")
                before = int(dll._getmaxstdio())
                if before < _UCRT_STREAMS and dll._setmaxstdio(_UCRT_STREAMS) == -1:
                    logger.warning("[chroma] the stream limit stays %d", before)
                result["ucrt"] = [before, int(dll._getmaxstdio())]
            else:
                import resource

                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                target = _POSIX_OPEN_FILES if hard == resource.RLIM_INFINITY else min(hard, _POSIX_OPEN_FILES)
                if soft != resource.RLIM_INFINITY and soft < target:
                    try:
                        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                    except (OSError, ValueError) as exc:
                        logger.warning("[chroma] the open-file limit stays %d (%s)", soft, exc)
                after = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
                result["nofile"] = [soft, _POSIX_OPEN_FILES if after == resource.RLIM_INFINITY else after]
        except (AttributeError, OSError, ValueError):  # the ceiling is then sized from the default
            logger.warning("[chroma] could not raise the open-file limit loaded vector indexes use",
                           exc_info=True)
        _LIMITS = result
        logger.info("[chroma] open-file limits for loaded vector indexes: %s", result)
        return result


def _file_budget() -> int:
    """Files (Windows: C runtime streams) this process may open, after
    :func:`raise_stream_limits`; 512 when it cannot be read (the default
    on both platforms)."""
    limits = raise_stream_limits()
    found = limits.get("ucrt") or limits.get("nofile")
    return int(found[1]) if found else 512


def library_ceiling() -> int:
    """The most Knowledge libraries this install may hold:
    :data:`LIBRARY_CEILING`, lowered when the process cannot keep every
    library's index open (:data:`FILES_PER_INDEX` files each, beside
    :data:`SPARE_COLLECTIONS` more and a reserve). At least one."""
    by_files = (_file_budget() - _FILE_RESERVE) // FILES_PER_INDEX - SPARE_COLLECTIONS
    return max(1, min(LIBRARY_CEILING, by_files))


def index_pool_size() -> int:
    """The index pool Kazma gives Chroma: every shard holds as many indexes
    as one process can load (:func:`library_ceiling` plus the spares), so
    no index is ever evicted, whichever shards the collections hash to."""
    return POOL_SHARDS * (library_ceiling() + SPARE_COLLECTIONS)


def start_system(path: str, settings: Any, *, pool: int | None = None) -> dict[str, Any]:
    """Start Chroma's system for *path* with Kazma's index pool, before the
    client is made (``chromadb.PersistentClient(path, settings)`` then
    finds it running and uses it).

    Chroma sizes its pool when it builds its Rust API (``max_file_handles
    // 5``) and reads it only when the system starts; Kazma sets
    ``hnsw_cache_size`` in between -- the system ``PersistentClient`` would
    build, started the way it starts one. *settings* must be the object
    later passed to ``PersistentClient``. Returns ``pool`` (what the system
    runs with) and ``started`` (False: a system for *path* already ran, its
    pool unchanged, logged). Raises what Chroma raises.
    """
    from chromadb.api import ServerAPI
    from chromadb.api.shared_system_client import SharedSystemClient
    from chromadb.config import System
    from chromadb.telemetry.product import ProductTelemetryClient

    settings.persist_directory = path
    settings.is_persistent = True
    identifier = SharedSystemClient._get_identifier_from_settings(settings)
    running = SharedSystemClient._identifier_to_system.get(identifier)
    if running is not None:
        size = getattr(running.instance(ServerAPI), "hnsw_cache_size", None)
        logger.warning("[chroma] a vector client for %s was started outside Kazma; its index pool "
                       "stays %s", path, size)
        return {"pool": size, "started": False}
    system = System(settings)
    system.instance(ProductTelemetryClient)
    api = system.instance(ServerAPI)
    api.hnsw_cache_size = int(pool if pool is not None else index_pool_size())
    SharedSystemClient._identifier_to_system[identifier] = system
    try:
        system.start()
    except BaseException:
        SharedSystemClient._identifier_to_system.pop(identifier, None)
        raise
    logger.info("[chroma] vector index pool for %s: %d (%d a shard)", path, api.hnsw_cache_size,
                api.hnsw_cache_size // POOL_SHARDS)
    return {"pool": api.hnsw_cache_size, "started": True}


# ── Text in the catalog ────────────────────────────────────────────────────


def _catalog_ro(persist_dir: str | Path) -> sqlite3.Connection | None:
    catalog = Path(persist_dir) / CATALOG
    if not catalog.is_file():
        return None
    return sqlite3.connect(f"file:{catalog.as_posix()}?mode=ro", uri=True, timeout=5)


def holds_text(persist_dir: str | Path, collection: str, kept_keys: Iterable[str]) -> bool | None:
    """Whether *collection* still holds anything but its vectors, ids and
    the metadata keys in *kept_keys*: a document, or another metadata key
    (an older build stored a 2,000-character preview and the chunk's title,
    heading and address), in the metadata segment or in the write log.
    False for a collection or catalog that is not there; None when the
    catalog cannot be read. Read only."""
    kept = sorted(set(kept_keys))
    try:
        conn = _catalog_ro(persist_dir)
    except sqlite3.Error:
        return None
    if conn is None:
        return False
    try:
        row = conn.execute("SELECT id FROM collections WHERE name = ?", (collection,)).fetchone()
        if row is None:
            return False
        collection_id = str(row[0])
        marks = ",".join("?" * len(kept)) or "''"
        segments = [r[0] for r in conn.execute(
            "SELECT id FROM segments WHERE collection = ? AND scope = 'METADATA'", (collection_id,))]
        for segment in segments:
            if conn.execute(
                    "SELECT 1 FROM embeddings e JOIN embedding_metadata m ON m.id = e.id "
                    f"WHERE e.segment_id = ? AND m.key NOT IN ({marks}) LIMIT 1",
                    (segment, *kept)).fetchone():
                return True
        # The write log keeps each record's metadata, document included, as
        # JSON until Chroma writes the index to disk (every 1,000 records).
        return conn.execute(
            "SELECT 1 FROM embeddings_queue q, json_each(q.metadata) j "
            f"WHERE q.topic LIKE ? AND q.metadata IS NOT NULL AND j.key NOT IN ({marks}) LIMIT 1",
            (f"%/{collection_id}", *kept)).fetchone() is not None
    except sqlite3.Error as exc:
        logger.warning("[chroma] could not read what %s holds in %s: %s", collection, persist_dir, exc)
        return None
    finally:
        conn.close()


def mark_compaction_due(persist_dir: str | Path | None) -> None:
    """Record that rows were deleted from the catalog: their bytes stay in
    the file until :func:`compact_catalog` rewrites it. Never raises."""
    if not persist_dir:
        return
    marker = Path(persist_dir) / COMPACTION_MARKER
    if marker.exists():
        return
    try:
        marker.write_text(str(int(time.time())), encoding="utf-8")
    except OSError:
        logger.warning("[chroma] could not record a pending compaction in %s", persist_dir, exc_info=True)


def compaction_due(persist_dir: str | Path | None) -> bool:
    return bool(persist_dir) and (Path(persist_dir) / COMPACTION_MARKER).exists()  # type: ignore[arg-type]


def compact_catalog(persist_dir: str | Path, *, busy_timeout_s: float = 10.0) -> dict[str, Any]:
    """Rewrite ``chroma.sqlite3`` so no deleted row stays readable in it.

    The full-text index is merged first (``optimize``: FTS5 keeps a deleted
    row's terms in its older segments until a merge reaches them; rebuilt
    when the merge could not drop them,
    :func:`kazma_core.db.fts5_purge.purge_deleted_rows`), then the
    file is rebuilt from its live rows (``VACUUM``: free pages and the free
    space inside pages go, and the file shrinks). The caller makes sure no
    Chroma call runs meanwhile; another process's write waits up to
    *busy_timeout_s*. Clears the marker on success. Returns ``compacted``,
    ``ms``, ``bytes_before`` and ``bytes_after``; a failure is logged and
    returned as ``error`` (the marker stays: the next pass tries again).
    """
    catalog = Path(persist_dir) / CATALOG
    out: dict[str, Any] = {"compacted": False, "ms": 0.0, "bytes_before": 0, "bytes_after": 0}
    if not catalog.is_file():
        (Path(persist_dir) / COMPACTION_MARKER).unlink(missing_ok=True)
        return out
    out["bytes_before"] = catalog.stat().st_size
    started = time.perf_counter()
    try:
        conn = sqlite3.connect(str(catalog), timeout=busy_timeout_s, isolation_level=None)
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("[chroma] could not open the vector catalog %s to compact it: %s", catalog, exc)
        return out
    try:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'embedding_fulltext_search'").fetchone():
            try:
                # A merge into one segment, rebuilt when FTS5 could not drop
                # every delete marker (KB-Q01: segments on one level below the top).
                purge_deleted_rows(conn, "embedding_fulltext_search")
            except sqlite3.OperationalError as exc:  # no FTS5 or trigram here: the VACUUM still runs
                logger.warning("[chroma] could not merge the vector catalog's full-text index: %s", exc)
        conn.execute("VACUUM")
    except sqlite3.Error as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        logger.warning("[chroma] could not compact the vector catalog %s (the next pass tries again): %s",
                       catalog, exc)
        return out
    finally:
        conn.close()
    out.update(compacted=True, ms=round((time.perf_counter() - started) * 1000, 1),
               bytes_after=catalog.stat().st_size)
    (Path(persist_dir) / COMPACTION_MARKER).unlink(missing_ok=True)
    logger.info("[chroma] compacted the vector catalog %s in %.0f ms (%d -> %d bytes)", catalog, out["ms"],
                out["bytes_before"], out["bytes_after"])
    return out


def catalog_text_rows(persist_dir: str | Path, kept_keys: Iterable[str]) -> dict[str, int]:
    """How much text the whole catalog holds outside *kept_keys*: metadata
    rows, write-log records and full-text rows (the soak and the tests read
    it); a table the catalog lacks holds none. Read only; raises what SQLite
    raises."""
    kept = sorted(set(kept_keys))
    marks = ",".join("?" * len(kept)) or "''"
    out = {"metadata": 0, "log": 0, "fulltext": 0}
    conn = _catalog_ro(persist_dir)
    if conn is None:
        return out
    try:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "embedding_metadata" in tables:
            out["metadata"] = int(conn.execute(
                f"SELECT COUNT(*) FROM embedding_metadata WHERE key NOT IN ({marks})", kept).fetchone()[0])
        if "embeddings_queue" in tables:
            out["log"] = int(conn.execute(
                "SELECT COUNT(DISTINCT q.seq_id) FROM embeddings_queue q, json_each(q.metadata) j "
                f"WHERE q.metadata IS NOT NULL AND j.key NOT IN ({marks})", kept).fetchone()[0])
        if "embedding_fulltext_search_content" in tables:
            out["fulltext"] = int(conn.execute(
                "SELECT COUNT(*) FROM embedding_fulltext_search_content").fetchone()[0])
        return out
    finally:
        conn.close()
