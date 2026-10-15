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

**An evicted, never-persisted HNSW index is lost for the rest of the
process.** Chroma keeps each loaded index in a pool of ``max_file_handles //
5`` entries split over 64 shards -- on Windows 512 // 5 = 102, so one or two
indexes a shard -- and writes an index to disk only every ``sync_threshold``
(1,000) records. Two collections that hash to one shard evict each other; an
evicted index of fewer than 1,000 records existed only in memory, and the
process has already replayed that collection's log, so every later read of
it fails ("Error creating hnsw segment reader: Nothing found on disk") until
a restart. :func:`raise_stream_limits` raises the limits Chroma sizes its
pool from, before the first client starts (pool 409 on Windows, 2,048 on
POSIX); ``vector_store_global`` rebuilds an index that was lost anyway from
Chroma's own log and asks again.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

__all__ = [
    "CATALOG",
    "STORE_ID_FILE",
    "raise_stream_limits",
    "remove_orphan_segment_folders",
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

#: What Chroma's pool is sized from, raised before the first client
#: starts. Windows: the legacy CRT's stream limit (msvcrt.dll, the value
#: Chroma reads; 2,048 is its maximum) and the Universal CRT's (ucrtbase,
#: whose ``FILE`` streams the index files are opened with; 8,192 is its
#: maximum). POSIX: the soft open-file limit, never above the hard one.
_MSVCRT_STREAMS = 2048
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
    """Raise the limits Chroma sizes its HNSW index pool from, once per
    process, before the first client starts (never lowers one). Returns
    what was found and set; never raises."""
    global _LIMITS
    with _LIMITS_LOCK:
        if _LIMITS is not None:
            return _LIMITS
        result: dict[str, Any] = {}
        try:
            if os.name == "nt":
                import ctypes

                for name, dll, target in (("msvcrt", ctypes.windll.msvcrt, _MSVCRT_STREAMS),
                                          ("ucrt", ctypes.CDLL("ucrtbase"), _UCRT_STREAMS)):
                    before = int(dll._getmaxstdio())
                    if before < target and dll._setmaxstdio(target) == -1:
                        logger.warning("[chroma] the %s stream limit stays %d", name, before)
                    result[name] = [before, int(dll._getmaxstdio())]
            else:
                import resource

                soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
                target = _POSIX_OPEN_FILES if hard == resource.RLIM_INFINITY else min(hard, _POSIX_OPEN_FILES)
                if soft != resource.RLIM_INFINITY and soft < target:
                    try:
                        resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                    except (OSError, ValueError) as exc:
                        logger.warning("[chroma] the open-file limit stays %d (%s)", soft, exc)
                result["nofile"] = [soft, resource.getrlimit(resource.RLIMIT_NOFILE)[0]]
        except (AttributeError, OSError, ValueError):  # Chroma then sizes its pool as before
            logger.warning("[chroma] could not raise the stream limits Chroma sizes its index pool from",
                           exc_info=True)
        _LIMITS = result
        logger.info("[chroma] stream limits for the vector index pool: %s", result)
        return result
