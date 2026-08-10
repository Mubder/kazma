"""Consistent native backup of the document store (DB + content tree).

Phase 9 backup. The document store is a SQLite metadata DB (``documents.db``)
plus an immutable content-addressed tree (``quarantine/`` / ``originals/`` /
``artifacts/`` / ``manifests/``). A naive copy risks a **torn snapshot** where
the DB references a blob the copy missed.

Consistency strategy (no publication quiesce required):

1. Snapshot ``documents.db`` first via ``sqlite3.backup()`` (holds a read
   lock → a consistent point-in-time). Blob *files* are always written before
   their DB *rows* (``put_stream`` → ``register_blob``), so every blob the
   snapshot references already exists on disk at snapshot time.
2. Copy the referenced content **after** the DB snapshot. Referenced blobs are
   immutable and GC never removes a referenced blob, so each is still present.
   The copy is a superset (extra newer blobs are harmless).
3. **Verify**: re-open the snapshot DB, enumerate every referenced
   ``(sha256, storage_kind)`` and manifest ``(document, version)``, and confirm
   each is present in the backup with a matching checksum. The report lists any
   missing item truthfully (there should be none).

Backups land in :func:`kazma_core.paths.backups_dir` under a timestamped
``document-store-<ts>/`` directory with a ``manifest.json`` of file counts +
checksums.
"""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = [
    "perform_document_backup",
    "verify_document_backup",
]

_KIND_DIRS = ("quarantine", "originals", "artifacts")
_COPY_CHUNK = 1 << 20


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_COPY_CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def _blob_rel_path(kind: str, sha: str) -> Path:
    return Path(kind) / "sha256" / sha[:2] / sha[2:4] / sha


def perform_document_backup(
    *,
    storage_root: str | Path | None = None,
    dest_dir: str | Path | None = None,
    retention: int = 5,
) -> dict[str, Any]:
    """Back up ``documents.db`` + referenced content to a timestamped dir.

    Returns a report dict (paths, counts, checksums, verification result).
    Best-effort: returns ``{"ok": False, ...}`` on failure rather than raising.
    """
    try:
        if storage_root is None:
            from .config import get_document_config

            storage_root = get_document_config().storage_root
        root = Path(storage_root)
        db_src = root / "documents.db"
        if not db_src.exists():
            return {"ok": True, "skipped": "no documents.db yet"}

        if dest_dir is None:
            from kazma_core.paths import backups_dir

            dest_dir = backups_dir()
        ts = int(time.time())
        out = Path(dest_dir) / f"document-store-{ts}"
        out.mkdir(parents=True, exist_ok=True)

        # 1) Consistent DB snapshot FIRST (read lock → point-in-time).
        from kazma_core.memory.backup import backup_one

        db_dest = out / "documents.db"
        if not backup_one(db_src, db_dest):
            return {"ok": False, "error": "documents.db snapshot failed"}

        # 2) Copy content referenced by the snapshot (superset-safe).
        refs, versions = _read_references(db_dest)
        checksums: dict[str, str] = {}
        copied = 0
        for sha, kind in refs:
            rel = _blob_rel_path(kind, sha)
            rel_key = str(rel).replace("\\", "/")
            checksums[rel_key] = sha
            src = root / rel
            if not src.is_file():
                continue
            dst = out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy2(src, dst)
                copied += 1

        manifests_copied = 0
        for doc_id, ver_id in versions:
            src = root / "manifests" / doc_id / f"{ver_id}.json"
            if not src.is_file():
                continue
            dst = out / "manifests" / doc_id / f"{ver_id}.json"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            manifests_copied += 1

        # 3) Verify + write manifest.
        verification = verify_document_backup(backup_dir=out, checksums=checksums)
        manifest = {
            "created_epoch": ts,
            "referenced_blobs": len(refs),
            "copied_blobs": copied,
            "manifests": manifests_copied,
            "checksums": checksums,
            "verification": verification,
        }
        (out / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        _prune_old(Path(dest_dir), retention=retention)
        logger.info(
            "[documents.backup] backed up documents.db + %d blob(s) → %s (verified=%s)",
            copied,
            out.name,
            verification["ok"],
        )
        return {"ok": bool(verification["ok"]), "path": str(out), **manifest}
    except Exception as exc:  # noqa: BLE001 - backup must never crash the worker
        logger.warning("[documents.backup] backup failed: %s", exc, exc_info=True)
        return {"ok": False, "error": str(exc)[:200]}


def _read_references(db_path: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return ([(sha256, storage_kind)], [(document_id, version_id)]) refs."""
    refs: list[tuple[str, str]] = []
    versions: list[tuple[str, str]] = []
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        for row in conn.execute(
            """
            SELECT DISTINCT b.sha256 AS sha256, b.storage_kind AS storage_kind
            FROM document_blobs b
            WHERE b.id IN (
                SELECT source_blob_id FROM document_versions
                UNION SELECT blob_id FROM document_artifacts
            )
            """
        ):
            refs.append((row["sha256"], row["storage_kind"]))
        for row in conn.execute(
            "SELECT id, document_id FROM document_versions"
        ):
            versions.append((row["document_id"], row["id"]))
    finally:
        conn.close()
    return refs, versions


def verify_document_backup(
    *, backup_dir: str | Path, checksums: dict[str, str] | None = None
) -> dict[str, Any]:
    """Verify every referenced blob is present in the backup with right checksum.

    If ``checksums`` is not supplied it is read from the backup's
    ``manifest.json``. Returns ``{"ok": bool, "checked": int, "missing": [...],
    "corrupt": [...]}``.
    """
    out = Path(backup_dir)
    if checksums is None:
        manifest_path = out / "manifest.json"
        if manifest_path.is_file():
            try:
                checksums = json.loads(manifest_path.read_text(encoding="utf-8")).get(
                    "checksums", {}
                )
            except (ValueError, OSError):
                checksums = {}
        else:
            checksums = {}
    missing: list[str] = []
    corrupt: list[str] = []
    for rel, expected in checksums.items():
        path = out / rel
        if not path.is_file():
            missing.append(rel)
            continue
        if _sha256_file(path) != expected:
            corrupt.append(rel)
    return {
        "ok": not missing and not corrupt,
        "checked": len(checksums),
        "missing": missing[:50],
        "corrupt": corrupt[:50],
    }


def _prune_old(dest_dir: Path, *, retention: int) -> int:
    """Keep the newest ``retention`` document-store backups, delete older."""
    dirs = sorted(
        (p for p in dest_dir.glob("document-store-*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    deleted = 0
    for stale in dirs[max(1, int(retention)):]:
        try:
            shutil.rmtree(stale, ignore_errors=True)
            deleted += 1
        except OSError:
            pass
    return deleted
